"""Tests for streaming waveform reduction and binary format version 1."""

from dataclasses import replace
import math
from pathlib import Path
import struct
import tempfile
import unittest
import wave

import numpy as np

from waveform_peaks import (
    EmptyAudioStreamError,
    MAXIMUM_PEAKS_PER_SECOND,
    MINIMUM_FINAL_PEAKS_PER_SECOND,
    NoAudioStreamError,
    StreamingPeakAccumulator,
    WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE,
    WAVEFORM_PEAK_FLAGS,
    WAVEFORM_PEAK_FORMAT_VERSION,
    WAVEFORM_PEAK_HEADER_SIZE,
    WAVEFORM_PEAK_MAGIC,
    WaveformDecodeError,
    WaveformPeakLevel,
    WaveformPeakPyramid,
    base_samples_per_peak,
    build_waveform_peak_pyramid,
    encode_waveform_peak_pyramid,
    extract_waveform_peak_pyramid,
)


HEADER = struct.Struct("<8sHHIIHHQdQQQ")
DIRECTORY = struct.Struct("<IIQQ")
REPO_ROOT = Path(__file__).resolve().parents[1]


def accumulate(samples: np.ndarray, bucket_size: int) -> np.ndarray:
    accumulator = StreamingPeakAccumulator(bucket_size)
    accumulator.add(samples)
    return accumulator.finalize()


def fixture_pyramid() -> WaveformPeakPyramid:
    base = np.asarray(
        [[-32768, 32767], [-1200, 2500], [-300, 800]], dtype=np.int16
    )
    return build_waveform_peak_pyramid(
        base,
        sample_rate=16,
        sample_count=5,
        source_channel_count=2,
        samples_per_peak=2,
    )


class StreamingPeakAccumulatorTests(unittest.TestCase):
    def test_mono_data(self) -> None:
        peaks = accumulate(np.asarray([[-1.0, 0.5, 0.25, 1.0]]), 2)
        self.assertEqual(peaks.tolist(), [[-32768, 16384], [8192, 32767]])

    def test_stereo_combines_channel_extrema_without_averaging(self) -> None:
        samples = np.asarray([[-1.0, -0.5], [0.75, 1.0]])
        self.assertEqual(accumulate(samples, 2).tolist(), [[-32768, 32767]])

    def test_multichannel_data(self) -> None:
        samples = np.asarray([[0.1, 0.2], [-0.8, -0.2], [0.3, 0.9]])
        self.assertEqual(accumulate(samples, 2).tolist(), [[-26214, 29490]])

    def test_bucket_spans_multiple_chunks(self) -> None:
        accumulator = StreamingPeakAccumulator(4)
        accumulator.add(np.asarray([[0.25, -0.5]]))
        accumulator.add(np.asarray([[1.0]]))
        accumulator.add(np.asarray([[-1.0]]))
        self.assertEqual(accumulator.finalize().tolist(), [[-32768, 32767]])

    def test_multiple_buckets_in_one_chunk(self) -> None:
        samples = np.asarray([[-1.0, 0.0, 0.25, 0.5, -0.5, 1.0]])
        self.assertEqual(
            accumulate(samples, 2).tolist(),
            [[-32768, 0], [8192, 16384], [-16384, 32767]],
        )

    def test_partial_final_bucket_is_retained(self) -> None:
        self.assertEqual(
            accumulate(np.asarray([[0.0, 0.5, -0.25]]), 2).tolist(),
            [[0, 16384], [-8192, -8192]],
        )

    def test_silence_remains_zero(self) -> None:
        self.assertEqual(accumulate(np.zeros((2, 9)), 4).tolist(), [[0, 0]] * 3)

    def test_full_scale_negative_maps_to_signed_minimum(self) -> None:
        self.assertEqual(accumulate(np.asarray([[-1.0]]), 1).tolist(), [[-32768, -32768]])

    def test_full_scale_positive_maps_to_signed_maximum(self) -> None:
        self.assertEqual(accumulate(np.asarray([[1.0]]), 1).tolist(), [[32767, 32767]])

    def test_values_outside_range_clamp(self) -> None:
        self.assertEqual(accumulate(np.asarray([[-9.0, 3.0]]), 2).tolist(), [[-32768, 32767]])

    def test_nan_becomes_zero(self) -> None:
        self.assertEqual(accumulate(np.asarray([[math.nan]]), 1).tolist(), [[0, 0]])

    def test_positive_infinity_clamps_positive(self) -> None:
        self.assertEqual(accumulate(np.asarray([[math.inf]]), 1).tolist(), [[32767, 32767]])

    def test_negative_infinity_clamps_negative(self) -> None:
        self.assertEqual(accumulate(np.asarray([[-math.inf]]), 1).tolist(), [[-32768, -32768]])

    def test_input_arrays_are_not_mutated(self) -> None:
        samples = np.asarray([[math.nan, math.inf, -math.inf, 2.0]])
        original = samples.copy()
        accumulate(samples, 2)
        np.testing.assert_equal(samples, original)


class ResolutionAndPyramidTests(unittest.TestCase):
    def test_48000_hz_density_is_at_most_1024(self) -> None:
        size = base_samples_per_peak(48_000)
        self.assertEqual(size, 47)
        self.assertLessEqual(48_000 / size, MAXIMUM_PEAKS_PER_SECOND)

    def test_44100_hz_density_is_at_most_1024(self) -> None:
        size = base_samples_per_peak(44_100)
        self.assertEqual(size, 44)
        self.assertLessEqual(44_100 / size, MAXIMUM_PEAKS_PER_SECOND)

    def test_low_sample_rates_use_at_least_one_sample(self) -> None:
        self.assertEqual(base_samples_per_peak(1), 1)
        self.assertEqual(base_samples_per_peak(1000), 1)

    def test_peak_count_uses_ceiling_behavior(self) -> None:
        accumulator = StreamingPeakAccumulator(4)
        accumulator.add(np.zeros((1, 9)))
        self.assertEqual(accumulator.finalize().shape, (3, 2))

    def test_samples_per_peak_doubles_each_level(self) -> None:
        pyramid = fixture_pyramid()
        self.assertEqual([level.samples_per_peak for level in pyramid.levels], [2, 4])

    def test_pair_reduction_preserves_extrema(self) -> None:
        pyramid = fixture_pyramid()
        self.assertEqual(pyramid.levels[1].peaks[0].tolist(), [-32768, 32767])

    def test_odd_final_peak_is_preserved(self) -> None:
        pyramid = fixture_pyramid()
        self.assertEqual(pyramid.levels[1].peaks[-1].tolist(), [-300, 800])

    def test_final_level_reaches_four_peaks_per_second(self) -> None:
        sample_rate = 48_000
        sample_count = sample_rate * 10
        bucket_size = base_samples_per_peak(sample_rate)
        count = math.ceil(sample_count / bucket_size)
        base = np.zeros((count, 2), dtype=np.int16)
        pyramid = build_waveform_peak_pyramid(
            base,
            sample_rate=sample_rate,
            sample_count=sample_count,
            source_channel_count=1,
            samples_per_peak=bucket_size,
        )
        self.assertLessEqual(
            pyramid.levels[-1].peaks_per_second,
            MINIMUM_FINAL_PEAKS_PER_SECOND,
        )

    def test_one_peak_input_terminates_safely(self) -> None:
        pyramid = build_waveform_peak_pyramid(
            np.asarray([[-1, 1]], dtype=np.int16),
            sample_rate=48_000,
            sample_count=10,
            source_channel_count=1,
            samples_per_peak=47,
        )
        self.assertEqual(len(pyramid.levels), 1)

    def test_every_level_count_matches_decoded_sample_count(self) -> None:
        pyramid = fixture_pyramid()
        for level in pyramid.levels:
            self.assertEqual(
                level.peak_count,
                math.ceil(pyramid.sample_count / level.samples_per_peak),
            )


class BinaryEncodingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pyramid = fixture_pyramid()
        self.payload = encode_waveform_peak_pyramid(self.pyramid)
        self.header = HEADER.unpack_from(self.payload)

    def test_magic_version_and_exact_header(self) -> None:
        self.assertEqual(self.header[0], WAVEFORM_PEAK_MAGIC)
        self.assertEqual(self.header[1], WAVEFORM_PEAK_FORMAT_VERSION)
        self.assertEqual(self.header[2], WAVEFORM_PEAK_HEADER_SIZE)
        self.assertEqual(HEADER.size, 64)

    def test_directory_entries_are_exactly_24_bytes(self) -> None:
        self.assertEqual(DIRECTORY.size, WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE)
        self.assertEqual(DIRECTORY.size, 24)

    def test_flags_equal_three(self) -> None:
        self.assertEqual(self.header[3], WAVEFORM_PEAK_FLAGS)
        self.assertEqual(self.header[3], 3)

    def test_directory_and_first_data_offsets_are_exact(self) -> None:
        self.assertEqual(self.header[9], 64)
        self.assertEqual(self.header[10], 64 + len(self.pyramid.levels) * 24)

    def test_data_blocks_are_contiguous_with_exact_lengths(self) -> None:
        cursor = self.header[10]
        for index, level in enumerate(self.pyramid.levels):
            entry = DIRECTORY.unpack_from(self.payload, 64 + index * 24)
            self.assertEqual(entry[2], cursor)
            self.assertEqual(entry[3], level.peak_count * 4)
            cursor += entry[3]
        self.assertEqual(cursor, len(self.payload))

    def test_signed_little_endian_values_round_trip(self) -> None:
        first_entry = DIRECTORY.unpack_from(self.payload, 64)
        self.assertEqual(
            struct.unpack_from("<hh", self.payload, first_entry[2]),
            (-32768, 32767),
        )

    def test_duration_and_metadata_are_correct(self) -> None:
        self.assertEqual(self.header[4], 16)
        self.assertEqual(self.header[5], 2)
        self.assertEqual(self.header[7], 5)
        self.assertEqual(self.header[8], 5 / 16)
        self.assertEqual(self.header[11], 0)

    def test_identical_input_produces_identical_bytes(self) -> None:
        self.assertEqual(self.payload, encode_waveform_peak_pyramid(self.pyramid))
        self.assertIsInstance(self.payload, bytes)

    def test_invalid_pyramid_invariants_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_waveform_peak_pyramid(replace(self.pyramid, sample_count=7))
        with self.assertRaises(ValueError):
            encode_waveform_peak_pyramid(replace(self.pyramid, levels=()))

    def test_integer_range_overflow_is_rejected(self) -> None:
        with self.assertRaises(OverflowError):
            encode_waveform_peak_pyramid(
                replace(self.pyramid, sample_rate=0x1_0000_0000)
            )


class PyAVStreamingDecodeTests(unittest.TestCase):
    def _write_stereo_wav(self, path: Path, sample_count: int = 1003) -> None:
        frames = np.zeros((sample_count, 2), dtype="<i2")
        frames[:, 0] = 1000
        frames[:, 1] = -2000
        frames[2, 0] = -32768
        frames[3, 1] = 30_000
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48_000)
            output.writeframes(frames.tobytes())

    def test_temporary_wav_streams_into_valid_metadata_and_extrema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deterministic.wav"
            self._write_stereo_wav(path)
            pyramid = extract_waveform_peak_pyramid(str(path))

        self.assertEqual(pyramid.sample_rate, 48_000)
        self.assertEqual(pyramid.sample_count, 1003)
        self.assertEqual(pyramid.source_channel_count, 2)
        self.assertEqual(pyramid.duration_seconds, 1003 / 48_000)
        self.assertEqual(pyramid.levels[0].samples_per_peak, 47)
        self.assertEqual(pyramid.levels[0].peak_count, math.ceil(1003 / 47))
        self.assertEqual(pyramid.levels[0].peaks[0, 0], -32768)
        self.assertGreater(pyramid.levels[0].peaks[0, 1], 29_000)

    def test_empty_audio_is_rejected_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(44_100)
            with self.assertRaises(EmptyAudioStreamError):
                extract_waveform_peak_pyramid(str(path))

    def test_invalid_media_is_a_known_decode_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-audio.bin"
            path.write_bytes(b"not media")
            with self.assertRaises(WaveformDecodeError):
                extract_waveform_peak_pyramid(str(path))

    def test_video_without_audio_has_no_audio_stream(self) -> None:
        import av

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silent-video.mkv"
            with av.open(str(path), mode="w") as container:
                stream = container.add_stream("ffv1", rate=1)
                stream.width = 16
                stream.height = 16
                stream.pix_fmt = "yuv420p"
                frame = av.VideoFrame.from_ndarray(
                    np.zeros((16, 16, 3), dtype=np.uint8), format="rgb24"
                )
                for packet in stream.encode(frame):
                    container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            with self.assertRaises(NoAudioStreamError):
                extract_waveform_peak_pyramid(str(path))

    def test_new_module_has_no_full_track_pcm_accumulation_or_node_execution(self) -> None:
        source = (REPO_ROOT / "waveform_peaks.py").read_text(encoding="utf-8")
        for forbidden in ("torch.cat", "load_audio_file", "load_audio(", "execute("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("MAX_DECODED_CHUNK_SAMPLES", source)
        self.assertIn("_iter_converted_frames(resampler, None)", source)


if __name__ == "__main__":
    unittest.main()
