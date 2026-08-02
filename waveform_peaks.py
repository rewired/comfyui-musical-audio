"""Streaming waveform peak extraction and version-1 binary encoding."""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Iterable

import numpy as np


WAVEFORM_PEAK_MAGIC = b"MAUPK001"
WAVEFORM_PEAK_FORMAT_VERSION = 1
WAVEFORM_PEAK_CONTENT_TYPE = (
    "application/vnd.comfyui-musical-audio.waveform-peaks"
)
WAVEFORM_PEAK_FLAG_COMBINED_CHANNELS = 1 << 0
WAVEFORM_PEAK_FLAG_SIGNED_INT16_PAIRS = 1 << 1
WAVEFORM_PEAK_FLAGS = (
    WAVEFORM_PEAK_FLAG_COMBINED_CHANNELS
    | WAVEFORM_PEAK_FLAG_SIGNED_INT16_PAIRS
)
WAVEFORM_PEAK_HEADER_SIZE = 64
WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE = 24
MAXIMUM_PEAKS_PER_SECOND = 1024
MINIMUM_FINAL_PEAKS_PER_SECOND = 4
MAX_DECODED_CHUNK_SAMPLES = 65_536

_HEADER_STRUCT = struct.Struct("<8sHHIIHHQdQQQ")
_DIRECTORY_ENTRY_STRUCT = struct.Struct("<IIQQ")


class WaveformPeakError(ValueError):
    """Base class for deterministic waveform peak failures."""


class WaveformDecodeError(WaveformPeakError):
    """The selected file could not be decoded into usable audio."""


class NoAudioStreamError(WaveformDecodeError):
    """The selected file contains no audio stream."""


class EmptyAudioStreamError(WaveformDecodeError):
    """The selected audio stream contains no decoded timeline samples."""


def base_samples_per_peak(sample_rate: int) -> int:
    """Return a bucket size whose density never exceeds 1024 peaks/second."""

    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool):
        raise TypeError("sample_rate must be an integer")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than zero")
    return max(1, math.ceil(sample_rate / MAXIMUM_PEAKS_PER_SECOND))


def _readonly_int16_pairs(peaks: np.ndarray) -> np.ndarray:
    array = np.asarray(peaks)
    if array.ndim != 2 or array.shape[1] != 2:
        raise ValueError("peaks must have shape (peak_count, 2)")
    if array.dtype.kind != "i" or array.dtype.itemsize != 2:
        raise TypeError("peaks must use signed Int16 values")
    result = np.array(array, dtype="<i2", order="C", copy=True)
    result.flags.writeable = False
    return result


@dataclass(frozen=True)
class WaveformPeakLevel:
    """One immutable resolution level of interleaved minimum/maximum pairs."""

    samples_per_peak: int
    peaks: np.ndarray
    peaks_per_second: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "peaks", _readonly_int16_pairs(self.peaks))

    @property
    def peak_count(self) -> int:
        return int(self.peaks.shape[0])


@dataclass(frozen=True)
class WaveformPeakPyramid:
    """Read-only waveform metadata and its finest-to-coarsest peak levels."""

    sample_rate: int
    sample_count: int
    duration_seconds: float
    source_channel_count: int
    levels: tuple[WaveformPeakLevel, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "levels", tuple(self.levels))


def _quantize(values: np.ndarray) -> np.ndarray:
    """Quantize normalized floats with asymmetric signed Int16 endpoints."""

    clipped = np.clip(values, -1.0, 1.0)
    scaled = np.where(clipped < 0.0, clipped * 32768.0, clipped * 32767.0)
    return np.rint(scaled).astype("<i2")


class StreamingPeakAccumulator:
    """Accumulate combined-channel extrema without retaining decoded PCM."""

    def __init__(self, samples_per_peak: int) -> None:
        if not isinstance(samples_per_peak, int) or isinstance(samples_per_peak, bool):
            raise TypeError("samples_per_peak must be an integer")
        if samples_per_peak <= 0:
            raise ValueError("samples_per_peak must be greater than zero")
        self.samples_per_peak = samples_per_peak
        self.sample_count = 0
        self.source_channel_count: int | None = None
        self._partial_count = 0
        self._partial_min = 0.0
        self._partial_max = 0.0
        self._peaks = np.empty((0, 2), dtype="<i2")
        self._peak_count = 0
        self._finalized = False

    def _reserve(self, additional: int) -> None:
        required = self._peak_count + additional
        if required <= self._peaks.shape[0]:
            return
        capacity = max(required, max(16, self._peaks.shape[0] * 2))
        replacement = np.empty((capacity, 2), dtype="<i2")
        if self._peak_count:
            replacement[: self._peak_count] = self._peaks[: self._peak_count]
        self._peaks = replacement

    def _append_float_extrema(self, minima: np.ndarray, maxima: np.ndarray) -> None:
        count = int(minima.size)
        if count == 0:
            return
        self._reserve(count)
        target = self._peaks[self._peak_count : self._peak_count + count]
        target[:, 0] = _quantize(minima)
        target[:, 1] = _quantize(maxima)
        self._peak_count += count

    def add(self, planar_samples: np.ndarray) -> None:
        """Add a bounded planar chunk with shape ``channels x samples``."""

        if self._finalized:
            raise RuntimeError("cannot add samples after finalize")
        source = np.asarray(planar_samples)
        if source.ndim != 2 or source.shape[0] == 0:
            raise ValueError("planar_samples must have shape (channels, samples)")
        channels, chunk_samples = source.shape
        if self.source_channel_count is None:
            self.source_channel_count = int(channels)
        elif channels != self.source_channel_count:
            raise ValueError("source channel count changed during decoding")
        if chunk_samples == 0:
            return

        # copy=True is intentional: sanitizing and clipping must not mutate a frame
        # ndarray supplied by PyAV or a caller.
        samples = np.nan_to_num(
            source,
            copy=True,
            nan=0.0,
            posinf=1.0,
            neginf=-1.0,
        )
        np.clip(samples, -1.0, 1.0, out=samples)
        self.sample_count += int(chunk_samples)
        cursor = 0

        if self._partial_count:
            take = min(self.samples_per_peak - self._partial_count, chunk_samples)
            segment = samples[:, :take]
            self._partial_min = min(self._partial_min, float(np.min(segment)))
            self._partial_max = max(self._partial_max, float(np.max(segment)))
            self._partial_count += take
            cursor += take
            if self._partial_count == self.samples_per_peak:
                self._append_float_extrema(
                    np.asarray([self._partial_min]),
                    np.asarray([self._partial_max]),
                )
                self._partial_count = 0

        remaining = chunk_samples - cursor
        complete_count = remaining // self.samples_per_peak
        if complete_count:
            complete_samples = complete_count * self.samples_per_peak
            complete = samples[:, cursor : cursor + complete_samples]
            buckets = complete.reshape(
                channels, complete_count, self.samples_per_peak
            )
            self._append_float_extrema(
                np.min(buckets, axis=(0, 2)),
                np.max(buckets, axis=(0, 2)),
            )
            cursor += complete_samples

        if cursor < chunk_samples:
            partial = samples[:, cursor:]
            self._partial_count = chunk_samples - cursor
            self._partial_min = float(np.min(partial))
            self._partial_max = float(np.max(partial))

    def finalize(self) -> np.ndarray:
        """Retain a final partial bucket and return an exact read-only array."""

        if not self._finalized:
            if self._partial_count:
                self._append_float_extrema(
                    np.asarray([self._partial_min]),
                    np.asarray([self._partial_max]),
                )
                self._partial_count = 0
            self._finalized = True
        result = self._peaks[: self._peak_count].copy()
        result.flags.writeable = False
        return result


def build_waveform_peak_pyramid(
    base_peaks: np.ndarray,
    *,
    sample_rate: int,
    sample_count: int,
    source_channel_count: int,
    samples_per_peak: int | None = None,
) -> WaveformPeakPyramid:
    """Build coarser levels from adjacent extrema in the preceding level."""

    if samples_per_peak is None:
        samples_per_peak = base_samples_per_peak(sample_rate)
    current = _readonly_int16_pairs(base_peaks)
    levels: list[WaveformPeakLevel] = []
    current_samples_per_peak = samples_per_peak

    while True:
        levels.append(
            WaveformPeakLevel(
                samples_per_peak=current_samples_per_peak,
                peaks=current,
                peaks_per_second=sample_rate / current_samples_per_peak,
            )
        )
        if (
            current.shape[0] == 1
            or sample_rate / current_samples_per_peak
            <= MINIMUM_FINAL_PEAKS_PER_SECOND
        ):
            break

        left = current[0::2]
        right = current[1::2]
        reduced = np.array(left, dtype="<i2", order="C", copy=True)
        paired_count = right.shape[0]
        if paired_count:
            reduced[:paired_count, 0] = np.minimum(
                reduced[:paired_count, 0], right[:, 0]
            )
            reduced[:paired_count, 1] = np.maximum(
                reduced[:paired_count, 1], right[:, 1]
            )
        current = reduced
        current_samples_per_peak *= 2

    pyramid = WaveformPeakPyramid(
        sample_rate=sample_rate,
        sample_count=sample_count,
        duration_seconds=sample_count / sample_rate if sample_rate else math.nan,
        source_channel_count=source_channel_count,
        levels=tuple(levels),
    )
    validate_waveform_peak_pyramid(pyramid)
    return pyramid


def _iter_converted_frames(resampler: object, frame: object) -> Iterable[object]:
    converted = resampler.resample(frame)
    if converted is None:
        return ()
    if isinstance(converted, (list, tuple)):
        return converted
    return (converted,)


def extract_waveform_peak_pyramid(filename: str) -> WaveformPeakPyramid:
    """Stream the first audio stream through PyAV into a peak pyramid."""

    try:
        import av
    except ImportError as exc:  # pragma: no cover - runtime dependency contract
        raise WaveformDecodeError("PyAV is not available") from exc

    try:
        with av.open(filename, mode="r") as container:
            stream = next(iter(container.streams.audio), None)
            if stream is None:
                raise NoAudioStreamError("The selected file has no audio stream")

            stream_rate = int(
                getattr(stream, "sample_rate", 0)
                or getattr(stream, "rate", 0)
                or getattr(stream.codec_context, "sample_rate", 0)
                or 0
            )
            resampler = None
            accumulator = None
            source_channel_count = 0

            def consume(converted_frame: object) -> None:
                nonlocal accumulator, source_channel_count
                planar = converted_frame.to_ndarray()
                if planar.ndim != 2:
                    raise WaveformDecodeError("Decoded audio is not planar")
                if source_channel_count == 0:
                    source_channel_count = int(planar.shape[0])
                elif planar.shape[0] != source_channel_count:
                    raise WaveformDecodeError("Audio channel layout changed")
                for start in range(0, planar.shape[1], MAX_DECODED_CHUNK_SAMPLES):
                    accumulator.add(planar[:, start : start + MAX_DECODED_CHUNK_SAMPLES])

            for frame in container.decode(stream):
                if resampler is None:
                    sample_rate = stream_rate or int(frame.sample_rate or 0)
                    if sample_rate <= 0:
                        raise WaveformDecodeError("Audio sample rate is unavailable")
                    source_channel_count = len(frame.layout.channels)
                    if source_channel_count <= 0:
                        raise WaveformDecodeError("Audio channel layout is unavailable")
                    resampler = av.AudioResampler(
                        format="fltp",
                        layout=frame.layout,
                        rate=sample_rate,
                    )
                    accumulator = StreamingPeakAccumulator(
                        base_samples_per_peak(sample_rate)
                    )
                for converted_frame in _iter_converted_frames(resampler, frame):
                    consume(converted_frame)

            if resampler is None or accumulator is None:
                raise EmptyAudioStreamError("The selected audio stream is empty")
            for converted_frame in _iter_converted_frames(resampler, None):
                consume(converted_frame)

            base_peaks = accumulator.finalize()
            if accumulator.sample_count <= 0 or base_peaks.shape[0] == 0:
                raise EmptyAudioStreamError("The selected audio stream is empty")
            return build_waveform_peak_pyramid(
                base_peaks,
                sample_rate=sample_rate,
                sample_count=accumulator.sample_count,
                source_channel_count=source_channel_count,
                samples_per_peak=accumulator.samples_per_peak,
            )
    except (NoAudioStreamError, EmptyAudioStreamError, WaveformDecodeError):
        raise
    except (OSError, EOFError, ValueError) as exc:
        raise WaveformDecodeError("The selected file could not be decoded") from exc
    except getattr(av, "FFmpegError", ()) as exc:
        raise WaveformDecodeError("The selected file could not be decoded") from exc


def _require_integer_range(name: str, value: int, maximum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or value > maximum:
        raise OverflowError(f"{name} is outside its binary field range")


def validate_waveform_peak_pyramid(pyramid: WaveformPeakPyramid) -> None:
    """Validate all model and cross-level invariants before serialization."""

    if not isinstance(pyramid, WaveformPeakPyramid):
        raise TypeError("pyramid must be a WaveformPeakPyramid")
    _require_integer_range("sample_rate", pyramid.sample_rate, 0xFFFFFFFF)
    _require_integer_range("sample_count", pyramid.sample_count, 0xFFFFFFFFFFFFFFFF)
    _require_integer_range(
        "source_channel_count", pyramid.source_channel_count, 0xFFFF
    )
    if pyramid.sample_rate == 0:
        raise ValueError("sample_rate must be greater than zero")
    if pyramid.sample_count == 0:
        raise ValueError("sample_count must be greater than zero")
    if pyramid.source_channel_count == 0:
        raise ValueError("source_channel_count must be greater than zero")
    if not pyramid.levels:
        raise ValueError("at least one peak level is required")
    if len(pyramid.levels) > 0xFFFF:
        raise OverflowError("level_count is outside its binary field range")
    if not math.isfinite(pyramid.duration_seconds) or pyramid.duration_seconds <= 0:
        raise ValueError("duration_seconds must be finite and greater than zero")
    expected_duration = pyramid.sample_count / pyramid.sample_rate
    if abs(pyramid.duration_seconds - expected_duration) > 1 / pyramid.sample_rate:
        raise ValueError("duration_seconds is inconsistent with decoded samples")

    previous_samples_per_peak = None
    for level in pyramid.levels:
        _require_integer_range(
            "samples_per_peak", level.samples_per_peak, 0xFFFFFFFF
        )
        if level.samples_per_peak == 0:
            raise ValueError("samples_per_peak must be greater than zero")
        if previous_samples_per_peak is not None and (
            level.samples_per_peak != previous_samples_per_peak * 2
        ):
            raise ValueError("samples_per_peak must double between levels")
        previous_samples_per_peak = level.samples_per_peak
        if level.peaks.ndim != 2 or level.peaks.shape[1] != 2:
            raise ValueError("each peak array must have two columns")
        if level.peaks.dtype.kind != "i" or level.peaks.dtype.itemsize != 2:
            raise TypeError("each peak array must use signed Int16 values")
        expected_peak_count = math.ceil(
            pyramid.sample_count / level.samples_per_peak
        )
        if level.peak_count == 0 or level.peak_count != expected_peak_count:
            raise ValueError("peak_count is inconsistent with decoded samples")
        _require_integer_range("peak_count", level.peak_count, 0xFFFFFFFF)
        expected_density = pyramid.sample_rate / level.samples_per_peak
        if (
            not math.isfinite(level.peaks_per_second)
            or level.peaks_per_second != expected_density
        ):
            raise ValueError("peaks_per_second is inconsistent with sample_rate")
        if np.any(level.peaks[:, 0] > level.peaks[:, 1]):
            raise ValueError("a peak minimum exceeds its maximum")


def encode_waveform_peak_pyramid(pyramid: WaveformPeakPyramid) -> bytes:
    """Encode a validated pyramid into deterministic little-endian format 1."""

    validate_waveform_peak_pyramid(pyramid)
    level_count = len(pyramid.levels)
    directory_offset = WAVEFORM_PEAK_HEADER_SIZE
    first_data_offset = (
        WAVEFORM_PEAK_HEADER_SIZE
        + level_count * WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE
    )
    _require_integer_range("first_data_offset", first_data_offset, 0xFFFFFFFFFFFFFFFF)

    directories: list[tuple[int, int, int, int]] = []
    data_offset = first_data_offset
    for level in pyramid.levels:
        byte_length = level.peak_count * 4
        _require_integer_range("data_byte_length", byte_length, 0xFFFFFFFFFFFFFFFF)
        directories.append(
            (
                level.samples_per_peak,
                level.peak_count,
                data_offset,
                byte_length,
            )
        )
        data_offset += byte_length
        _require_integer_range("data_offset", data_offset, 0xFFFFFFFFFFFFFFFF)

    payload = bytearray(data_offset)
    _HEADER_STRUCT.pack_into(
        payload,
        0,
        WAVEFORM_PEAK_MAGIC,
        WAVEFORM_PEAK_FORMAT_VERSION,
        WAVEFORM_PEAK_HEADER_SIZE,
        WAVEFORM_PEAK_FLAGS,
        pyramid.sample_rate,
        pyramid.source_channel_count,
        level_count,
        pyramid.sample_count,
        pyramid.duration_seconds,
        directory_offset,
        first_data_offset,
        0,
    )
    for index, directory in enumerate(directories):
        _DIRECTORY_ENTRY_STRUCT.pack_into(
            payload,
            directory_offset + index * WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE,
            *directory,
        )
    for level, (_, _, offset, byte_length) in zip(pyramid.levels, directories):
        little_endian = np.asarray(level.peaks, dtype="<i2", order="C")
        payload[offset : offset + byte_length] = little_endian.tobytes(order="C")
    return bytes(payload)


def generate_waveform_peak_payload(filename: str) -> bytes:
    """Decode, reduce, and encode one file on the calling worker thread."""

    return encode_waveform_peak_pyramid(extract_waveform_peak_pyramid(filename))


__all__ = [
    "EmptyAudioStreamError",
    "MAXIMUM_PEAKS_PER_SECOND",
    "MINIMUM_FINAL_PEAKS_PER_SECOND",
    "NoAudioStreamError",
    "StreamingPeakAccumulator",
    "WAVEFORM_PEAK_CONTENT_TYPE",
    "WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE",
    "WAVEFORM_PEAK_FLAGS",
    "WAVEFORM_PEAK_FLAG_COMBINED_CHANNELS",
    "WAVEFORM_PEAK_FLAG_SIGNED_INT16_PAIRS",
    "WAVEFORM_PEAK_FORMAT_VERSION",
    "WAVEFORM_PEAK_HEADER_SIZE",
    "WAVEFORM_PEAK_MAGIC",
    "WaveformDecodeError",
    "WaveformPeakError",
    "WaveformPeakLevel",
    "WaveformPeakPyramid",
    "base_samples_per_peak",
    "build_waveform_peak_pyramid",
    "encode_waveform_peak_pyramid",
    "extract_waveform_peak_pyramid",
    "generate_waveform_peak_payload",
    "validate_waveform_peak_pyramid",
]
