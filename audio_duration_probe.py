"""Decoded-sample audio-duration probing with a lazy PyAV boundary."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AudioDurationProbe:
    sample_rate: int
    sample_count: int
    duration_seconds: float


class AudioDurationProbeError(ValueError):
    pass


def _probe_error(message: str) -> AudioDurationProbeError:
    return AudioDurationProbeError(message)


def probe_audio_duration(filename: str) -> AudioDurationProbe:
    if type(filename) is not str:
        raise TypeError("filename must be a built-in string")
    if not filename:
        raise ValueError("filename must be nonempty")

    try:
        import av
    except (ImportError, ModuleNotFoundError) as exc:
        raise _probe_error("PyAV is not available") from exc

    try:
        with av.open(filename, mode="r") as container:
            audio_streams = container.streams.audio
            if not audio_streams:
                raise _probe_error("The selected media has no audio stream")
            stream = audio_streams[0]
            sample_rate = stream.codec_context.sample_rate
            if type(sample_rate) is not int or sample_rate <= 0:
                raise _probe_error("The decoded audio sample rate is invalid")

            sample_count = 0
            for frame in container.decode(stream):
                frame_samples = getattr(frame, "samples", None)
                if type(frame_samples) is not int or frame_samples <= 0:
                    raise _probe_error("A decoded audio frame is malformed")
                sample_count += frame_samples
            if type(sample_count) is not int or sample_count <= 0:
                raise _probe_error("The decoded audio stream is empty")
            duration_seconds = sample_count / sample_rate
            if not math.isfinite(duration_seconds) or duration_seconds <= 0.0:
                raise _probe_error("The decoded audio duration is invalid")
            return AudioDurationProbe(
                sample_rate=sample_rate,
                sample_count=sample_count,
                duration_seconds=duration_seconds,
            )
    except AudioDurationProbeError:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise _probe_error("The selected audio could not be decoded") from exc


__all__ = ["AudioDurationProbe", "AudioDurationProbeError", "probe_audio_duration"]
