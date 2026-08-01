"""Tests for the standalone musical timing calculations."""

from dataclasses import FrozenInstanceError, is_dataclass
import math
import unittest

from musical_timing import MusicalTimingResult, calculate_musical_timing


BASE_INPUTS = {
    "bpm": 120,
    "tempo_unit": "Quarter",
    "beats_per_bar": 4,
    "beat_unit": 4,
    "fps": 24,
    "subdivisions_per_beat": 4,
}


def calculate(**overrides: object) -> MusicalTimingResult:
    """Calculate using valid baseline inputs with selected overrides."""
    inputs = {**BASE_INPUTS, **overrides}
    return calculate_musical_timing(**inputs)  # type: ignore[arg-type]


class MusicalTimingCalculationTests(unittest.TestCase):
    def test_golden_timing(self) -> None:
        result = calculate(
            bpm=180,
            fps=24,
            subdivisions_per_beat=1,
            duration_bars=4,
        )

        self.assertAlmostEqual(result.seconds_per_tempo_pulse, 1 / 3)
        self.assertAlmostEqual(result.seconds_per_quarter, 1 / 3)
        self.assertAlmostEqual(result.seconds_per_beat, 1 / 3)
        self.assertAlmostEqual(result.seconds_per_bar, 4 / 3)
        self.assertAlmostEqual(result.start_beats, 0.0)
        self.assertAlmostEqual(result.start_seconds, 0.0)
        self.assertAlmostEqual(result.duration_beats_total, 16.0)
        self.assertAlmostEqual(result.duration_seconds, 16 / 3)
        self.assertAlmostEqual(result.end_seconds, 16 / 3)
        self.assertAlmostEqual(result.frames_per_beat, 8.0)
        self.assertAlmostEqual(result.frames_per_bar, 32.0)
        self.assertEqual(result.start_frame, 0)
        self.assertEqual(result.frame_count, 128)

    def test_fractional_frames_per_beat_are_not_truncated(self) -> None:
        result = calculate(bpm=174, fps=24)
        expected_seconds_per_beat = 60 / 174
        expected_frames_per_beat = 24 * 60 / 174

        self.assertAlmostEqual(
            result.seconds_per_beat, expected_seconds_per_beat
        )
        self.assertAlmostEqual(result.frames_per_beat, expected_frames_per_beat)
        self.assertIsInstance(result.frames_per_beat, float)
        self.assertFalse(result.frames_per_beat.is_integer())

    def test_start_frame_half_ties_round_away_from_zero(self) -> None:
        cases = (
            (0.5, 1),
            (1.5, 2),
            (2.5, 3),
            (-0.5, -1),
            (-1.5, -2),
        )

        for downbeat_offset, expected_frame in cases:
            with self.subTest(downbeat_offset=downbeat_offset):
                result = calculate(fps=1, downbeat_offset=downbeat_offset)
                self.assertEqual(result.start_frame, expected_frame)

    def test_frame_count_half_ties_round_away_from_zero(self) -> None:
        cases = (
            (1, 1),
            (3, 2),
            (5, 3),
        )

        for duration_beats, expected_frame_count in cases:
            with self.subTest(duration_beats=duration_beats):
                result = calculate(
                    bpm=120,
                    fps=1,
                    duration_beats=duration_beats,
                )
                self.assertEqual(result.frame_count, expected_frame_count)

    def test_compound_meter_with_dotted_quarter_tempo(self) -> None:
        result = calculate(
            bpm=90,
            tempo_unit="Dotted Quarter",
            beats_per_bar=6,
            beat_unit=8,
        )

        self.assertAlmostEqual(result.seconds_per_tempo_pulse, 2 / 3)
        self.assertAlmostEqual(result.seconds_per_quarter, 4 / 9)
        self.assertAlmostEqual(result.seconds_per_beat, 2 / 9)
        self.assertAlmostEqual(result.seconds_per_bar, 4 / 3)

    def test_eighth_note_tempo_unit(self) -> None:
        result = calculate(bpm=120, tempo_unit="Eighth")

        self.assertAlmostEqual(result.seconds_per_tempo_pulse, 0.5)
        self.assertAlmostEqual(result.seconds_per_quarter, 1.0)
        self.assertAlmostEqual(result.seconds_per_beat, 1.0)

    def test_position_with_downbeat_offset_and_subdivision(self) -> None:
        result = calculate(
            bpm=120,
            fps=30,
            downbeat_offset=0.25,
            start_bar=2,
            start_beat=3,
            start_subdivision=2,
        )

        self.assertAlmostEqual(result.start_beats, 6.5)
        self.assertAlmostEqual(result.start_seconds, 3.5)
        self.assertEqual(result.start_frame, 105)

    def test_unbounded_quantities_and_negative_offset_are_calculated(self) -> None:
        result = calculate(
            downbeat_offset=-1.0,
            start_subdivision=5,
            duration_beats=5,
            duration_subdivisions=6,
        )

        self.assertAlmostEqual(result.start_beats, 1.25)
        self.assertAlmostEqual(result.start_seconds, -0.375)
        self.assertEqual(result.start_frame, -9)
        self.assertAlmostEqual(result.duration_beats_total, 6.5)
        self.assertAlmostEqual(result.duration_seconds, 3.25)

    def test_valid_durations_and_frame_counts_are_non_negative(self) -> None:
        cases = (
            {},
            {"duration_subdivisions": 1},
            {
                "duration_bars": 2,
                "duration_beats": 5,
                "duration_subdivisions": 6,
            },
        )

        for overrides in cases:
            with self.subTest(overrides=overrides):
                result = calculate(**overrides)
                self.assertGreaterEqual(result.duration_beats_total, 0.0)
                self.assertGreaterEqual(result.duration_seconds, 0.0)
                self.assertGreaterEqual(result.frame_count, 0)

    def test_invalid_ranges_raise_value_error(self) -> None:
        invalid_inputs = {
            "zero bpm": {"bpm": 0},
            "zero fps": {"fps": 0},
            "zero beats per bar": {"beats_per_bar": 0},
            "zero beat unit": {"beat_unit": 0},
            "zero subdivisions per beat": {"subdivisions_per_beat": 0},
            "zero start bar": {"start_bar": 0},
            "zero start beat": {"start_beat": 0},
            "start beat above bar": {"start_beat": 5},
            "negative start subdivision": {"start_subdivision": -1},
            "negative duration bars": {"duration_bars": -1},
            "negative duration beats": {"duration_beats": -1},
            "negative duration subdivisions": {"duration_subdivisions": -1},
        }

        for label, overrides in invalid_inputs.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    calculate(**overrides)

    def test_unsupported_tempo_unit_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            calculate(tempo_unit="Half")

    def test_bpm_rejects_nan_and_infinity(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate(bpm=value)

    def test_fps_rejects_nan_and_infinity(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate(fps=value)

    def test_downbeat_offset_rejects_nan_and_infinities(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate(downbeat_offset=value)

    def test_real_number_inputs_reject_booleans(self) -> None:
        for field in ("bpm", "fps", "downbeat_offset"):
            with self.subTest(field=field):
                with self.assertRaises(TypeError):
                    calculate(**{field: True})

    def test_invalid_numeric_types_raise_type_error(self) -> None:
        invalid_inputs = {
            "string bpm": {"bpm": "120"},
            "string fps": {"fps": "24"},
            "string offset": {"downbeat_offset": "0"},
            "none offset": {"downbeat_offset": None},
            "non-string tempo": {"tempo_unit": 1},
        }

        for label, overrides in invalid_inputs.items():
            with self.subTest(label=label):
                with self.assertRaises(TypeError):
                    calculate(**overrides)

    def test_count_fields_require_built_in_integers(self) -> None:
        count_fields = (
            "beats_per_bar",
            "beat_unit",
            "subdivisions_per_beat",
            "start_bar",
            "start_beat",
            "start_subdivision",
            "duration_bars",
            "duration_beats",
            "duration_subdivisions",
        )

        for field in count_fields:
            for value in (True, 1.0, 1.5):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(TypeError):
                        calculate(**{field: value})

    def test_result_is_a_frozen_dataclass(self) -> None:
        result = calculate()

        self.assertTrue(is_dataclass(result))
        self.assertIsInstance(result, MusicalTimingResult)
        with self.assertRaises(FrozenInstanceError):
            result.start_seconds = 1.0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
