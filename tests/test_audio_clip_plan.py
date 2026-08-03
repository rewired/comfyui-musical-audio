"""Tests for pure sample-based audio clip planning."""

from dataclasses import FrozenInstanceError
import math
import unittest

from audio_clip_plan import (
    AudioClipPlan,
    ClipTimingMetadata,
    RequestedAudioRange,
    SampleRangePlan,
    apply_sample_range,
    create_audio_clip_plan,
    finalize_audio_clip_plan,
)


BASE_INPUTS = {
    "edit_mode": "Seconds",
    "sample_rate": 100,
    "sample_count": 1_000,
    "start_time": 0.0,
    "end_time": 1.0,
    "bpm": 120.0,
    "tempo_unit": "Quarter",
    "beats_per_bar": 4,
    "beat_unit": 4,
    "downbeat_offset": 0.0,
    "fps": 24.0,
    "start_bar": 1,
    "start_beat": 1,
    "start_subdivision": 0,
    "duration_bars": 0,
    "duration_beats": 0,
    "duration_subdivisions": 0,
    "subdivisions_per_beat": 4,
}


def plan(**overrides: object) -> AudioClipPlan:
    inputs = {**BASE_INPUTS, **overrides}
    return create_audio_clip_plan(**inputs)  # type: ignore[arg-type]


class SecondsModeTests(unittest.TestCase):
    def test_start_and_end_times_are_honored(self) -> None:
        result = plan(start_time=1.2, end_time=3.4)

        self.assertEqual(result.start_sample, 120)
        self.assertEqual(result.end_sample, 340)
        self.assertAlmostEqual(result.start_seconds, 1.2)
        self.assertAlmostEqual(result.end_seconds, 3.4)
        self.assertAlmostEqual(result.duration_seconds, 2.2)
        self.assertFalse(result.clamped)

    def test_nonpositive_end_time_means_file_end(self) -> None:
        for end_time in (0.0, -1.0):
            with self.subTest(end_time=end_time):
                result = plan(start_time=8.0, end_time=end_time)
                self.assertEqual(result.end_sample, 1_000)
                self.assertEqual(result.end_seconds, 10.0)
                self.assertFalse(result.clamped)

    def test_end_beyond_file_describes_only_returned_audio(self) -> None:
        result = plan(
            sample_count=100,
            start_time=0.25,
            end_time=2.0,
        )

        self.assertEqual((result.start_sample, result.end_sample), (25, 100))
        self.assertEqual(result.end_seconds, 1.0)
        self.assertEqual(result.duration_seconds, 0.75)
        self.assertEqual(result.frame_count, 18)
        self.assertTrue(result.clamped)

    def test_range_entirely_after_file_selects_final_source_sample(self) -> None:
        result = plan(
            sample_count=100,
            start_time=2.0,
            end_time=3.0,
        )

        self.assertEqual((result.start_sample, result.end_sample), (99, 100))
        self.assertEqual(result.start_seconds, 0.99)
        self.assertEqual(result.end_seconds, 1.0)
        self.assertLessEqual(result.end_seconds, 1.0)
        self.assertTrue(result.clamped)

    def test_zero_duration_selects_one_real_sample(self) -> None:
        result = plan(start_time=0.5, end_time=0.5)

        self.assertEqual((result.start_sample, result.end_sample), (50, 51))
        self.assertEqual(result.duration_seconds, 0.01)
        self.assertTrue(result.clamped)

    def test_reversed_range_collapses_at_requested_start(self) -> None:
        result = plan(start_time=3.0, end_time=2.0)

        self.assertEqual((result.start_sample, result.end_sample), (300, 301))
        self.assertTrue(result.clamped)

    def test_normal_sample_quantization_is_not_file_clamping(self) -> None:
        result = plan(start_time=0.004, end_time=1.004)

        self.assertEqual((result.start_sample, result.end_sample), (0, 100))
        self.assertFalse(result.clamped)

    def test_nearest_grid_position_after_downbeat(self) -> None:
        result = plan(start_time=2.75, end_time=3.0)

        self.assertIn(
            "Nearest: Bar 2 · Beat 2 · Subdivision 2",
            result.musical_position,
        )

    def test_nearest_grid_position_before_first_downbeat(self) -> None:
        result = plan(
            start_time=0.5,
            end_time=0.75,
            downbeat_offset=1.0,
        )

        self.assertIn(
            "Nearest: 4 subdivisions before Bar 1 · Beat 1",
            result.musical_position,
        )


class MusicalModeTests(unittest.TestCase):
    def test_four_bar_golden_case_is_128_frames(self) -> None:
        result = plan(
            edit_mode="Musical",
            sample_rate=48_000,
            sample_count=480_000,
            bpm=180.0,
            fps=24.0,
            subdivisions_per_beat=1,
            duration_bars=4,
        )

        self.assertEqual(result.start_sample, 0)
        self.assertEqual(result.end_sample, 256_000)
        self.assertAlmostEqual(result.duration_seconds, 16 / 3)
        self.assertEqual(result.frame_count, 128)
        self.assertEqual(result.frames_per_beat, 8.0)
        self.assertFalse(result.clamped)

    def test_fractional_frames_per_beat_are_preserved(self) -> None:
        result = plan(edit_mode="Musical", bpm=174.0, duration_beats=1)

        self.assertAlmostEqual(result.frames_per_beat, 24 * 60 / 174)
        self.assertFalse(result.frames_per_beat.is_integer())

    def test_compound_meter_uses_positional_eighth_note_beats(self) -> None:
        result = plan(
            edit_mode="Musical",
            bpm=90.0,
            tempo_unit="Dotted Quarter",
            beats_per_bar=6,
            beat_unit=8,
            duration_bars=1,
        )

        self.assertAlmostEqual(result.seconds_per_beat, 2 / 9)
        self.assertAlmostEqual(result.seconds_per_bar, 4 / 3)

    def test_negative_start_is_clamped_to_zero(self) -> None:
        result = plan(
            edit_mode="Musical",
            downbeat_offset=-0.5,
            duration_beats=2,
        )

        self.assertEqual(result.requested_start_seconds, -0.5)
        self.assertEqual(result.start_sample, 0)
        self.assertEqual(result.start_seconds, 0.0)
        self.assertTrue(result.clamped)
        self.assertTrue(result.musical_position.endswith(" | clamped to audio"))

    def test_zero_musical_duration_uses_zero_beats_label(self) -> None:
        result = plan(edit_mode="Musical")

        self.assertIn("Length: 0 Beats", result.musical_position)
        self.assertEqual(result.end_sample - result.start_sample, 1)

    def test_length_label_pluralizes_nonzero_components(self) -> None:
        result = plan(
            edit_mode="Musical",
            duration_bars=1,
            duration_beats=2,
            duration_subdivisions=1,
        )

        self.assertIn(
            "Length: 1 Bar + 2 Beats + 1 Subdivision",
            result.musical_position,
        )

    def test_legacy_seconds_values_are_ignored(self) -> None:
        result = plan(
            edit_mode="Musical",
            start_time=math.nan,
            end_time=math.inf,
            duration_beats=1,
        )

        self.assertEqual(result.start_seconds, 0.0)
        self.assertAlmostEqual(result.end_seconds, 0.5)


class RoundingAndOutputTests(unittest.TestCase):
    def test_sample_and_frame_half_ties_round_away_from_zero(self) -> None:
        result = plan(
            sample_rate=2,
            sample_count=10,
            start_time=0.25,
            end_time=0.75,
            fps=1.0,
        )

        self.assertEqual((result.start_sample, result.end_sample), (1, 2))
        self.assertEqual(result.start_seconds, 0.5)
        self.assertEqual(result.duration_seconds, 0.5)
        self.assertEqual(result.start_frame, 1)
        self.assertEqual(result.frame_count, 1)

    def test_actual_time_outputs_equal_selected_sample_indices(self) -> None:
        result = plan(
            sample_rate=3,
            sample_count=10,
            start_time=0.5,
            end_time=2.5,
        )

        self.assertEqual(result.start_seconds, result.start_sample / 3)
        self.assertEqual(result.end_seconds, result.end_sample / 3)
        self.assertEqual(
            result.duration_seconds,
            (result.end_sample - result.start_sample) / 3,
        )

    def test_result_is_frozen(self) -> None:
        result = plan()

        with self.assertRaises(FrozenInstanceError):
            result.start_sample = 2  # type: ignore[misc]


class NeutralSampleLayerTests(unittest.TestCase):
    def test_value_types_are_frozen(self) -> None:
        requested = RequestedAudioRange(0.0, 1.0)
        sample = apply_sample_range(
            requested_range=requested,
            sample_rate=100,
            sample_count=100,
            fps=24.0,
        )
        metadata = ClipTimingMetadata(0.5, 12.0, 2.0, 48.0, "position")
        for value, field in (
            (requested, "start_seconds"),
            (sample, "start_sample"),
            (metadata, "seconds_per_beat"),
        ):
            with self.subTest(value=type(value).__name__), self.assertRaises(FrozenInstanceError):
                setattr(value, field, 99)

    def test_apply_sample_range_matches_reversed_equal_and_eof_behavior(self) -> None:
        cases = (
            (RequestedAudioRange(3.0, 2.0), (300, 301)),
            (RequestedAudioRange(0.5, 0.5), (50, 51)),
            (RequestedAudioRange(20.0, 30.0), (999, 1000)),
        )
        for requested, expected in cases:
            with self.subTest(requested=requested):
                result = apply_sample_range(
                    requested_range=requested,
                    sample_rate=100,
                    sample_count=1000,
                    fps=24.0,
                )
                self.assertEqual((result.start_sample, result.end_sample), expected)
                self.assertTrue(result.clamped)

    def test_finalize_copies_without_recalculation(self) -> None:
        sample = SampleRangePlan(1.0, 2.0, 10, 20, 1.1, 1.9, 0.8, 27, 19, True)
        metadata = ClipTimingMetadata(0.3, 7.2, 1.1, 26.4, "exact")
        result = finalize_audio_clip_plan(sample, metadata)
        self.assertEqual(result.start_seconds, 1.1)
        self.assertEqual(result.start_frame, 27)
        self.assertEqual(result.frames_per_bar, 26.4)
        self.assertEqual(result.musical_position, "exact")

    def test_neutral_api_rejects_wrong_dataclass_and_domains(self) -> None:
        with self.assertRaises(TypeError):
            apply_sample_range(
                requested_range=(0.0, 1.0),  # type: ignore[arg-type]
                sample_rate=100,
                sample_count=100,
                fps=24.0,
            )
        for name, value in (("sample_rate", 0), ("sample_count", 0), ("fps", math.inf)):
            arguments = dict(
                requested_range=RequestedAudioRange(0.0, 1.0),
                sample_rate=100,
                sample_count=100,
                fps=24.0,
            )
            arguments[name] = value
            with self.subTest(name=name), self.assertRaises((TypeError, ValueError)):
                apply_sample_range(**arguments)
        with self.assertRaises(TypeError):
            finalize_audio_clip_plan(object(), ClipTimingMetadata(1, 1, 1, 1, "x"))  # type: ignore[arg-type]


class ValidationTests(unittest.TestCase):
    def test_invalid_edit_modes_are_rejected(self) -> None:
        for value, error_type in (("Frames", ValueError), (1, TypeError)):
            with self.subTest(value=value):
                with self.assertRaises(error_type):
                    plan(edit_mode=value)

    def test_invalid_sample_rates_are_rejected(self) -> None:
        for value, error_type in (
            (0, ValueError),
            (-1, ValueError),
            (44_100.0, TypeError),
            (True, TypeError),
        ):
            with self.subTest(value=value):
                with self.assertRaises(error_type):
                    plan(sample_rate=value)

    def test_invalid_sample_counts_are_rejected(self) -> None:
        for value, error_type in (
            (0, ValueError),
            (-1, ValueError),
            (100.0, TypeError),
            (True, TypeError),
        ):
            with self.subTest(value=value):
                with self.assertRaises(error_type):
                    plan(sample_count=value)

    def test_invalid_seconds_mode_times_are_rejected(self) -> None:
        for field in ("start_time", "end_time"):
            for value, error_type in (
                (math.nan, ValueError),
                (math.inf, ValueError),
                (-math.inf, ValueError),
                ("1.0", TypeError),
                (True, TypeError),
            ):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(error_type):
                        plan(**{field: value})


if __name__ == "__main__":
    unittest.main()
