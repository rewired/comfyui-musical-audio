"""Pure Phase 5c Score-selection contract tests."""

from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
import unittest

import score.selection as selection_module
from score.model import MeterEvent, Score, TempoEvent
from score.resolver import ScoreResolver
from score.selection import (
    ScorePosition,
    ScoreSelection,
    ScoreSelectionError,
    resolve_score_selection,
    selection_start_score_tick,
)
from score.serialize import score_from_dict


ROOT = Path(__file__).resolve().parents[1]


def score(**changes: object) -> Score:
    values = {
        "ticks_per_quarter": 480,
        "tempos": (TempoEvent(0, 500_000),),
        "meters": (MeterEvent(0, 4, 4),),
        "markers": (),
        "sections": (),
        "source": "json",
        "meter_estimated": False,
        "has_variable_meter": False,
        "has_midbar_meter_change": False,
    }
    values.update(changes)
    return Score(**values)


def resolver(value: Score | None = None, alignment: float = 0.0) -> ScoreResolver:
    return ScoreResolver(value or score(), 30.0, alignment)


def selection(value: ScoreResolver | None = None, **changes: object) -> ScoreSelection:
    arguments = {
        "start_bar": 1,
        "start_beat": 1,
        "start_subdivision": 0,
        "score_end_bar": 0,
        "score_end_beat": 0,
        "score_end_subdivision": 0,
        "duration_bars": 1,
        "duration_beats": 0,
        "duration_subdivisions": 0,
        "subdivisions_per_beat": 4,
    }
    arguments.update(changes)
    return resolve_score_selection(value or resolver(), **arguments)  # type: ignore[arg-type]


class ScoreSelectionValueTests(unittest.TestCase):
    def test_public_surface_is_exact(self):
        self.assertEqual(
            selection_module.__all__,
            (
                "ScorePosition",
                "ScoreSelection",
                "ScoreSelectionError",
                "ScoreSelectionMode",
                "resolve_score_selection",
                "selection_start_score_tick",
            ),
        )

    def test_values_are_frozen_and_fields_are_exact(self):
        result = selection()
        self.assertIsInstance(result.start_position, ScorePosition)
        self.assertEqual(
            tuple(result.__dataclass_fields__),
            (
                "start_tick", "end_tick_exclusive",
                "requested_start_score_seconds", "requested_end_score_seconds",
                "requested_start_seconds", "requested_end_seconds", "mode",
                "start_position", "exact_end_position", "duration_bars",
                "duration_beats", "duration_subdivisions", "subdivisions_per_beat",
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            result.start_tick = 1  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            result.start_position.bar = 2  # type: ignore[misc]

    def test_exact_end_has_priority_and_is_exclusive(self):
        result = selection(
            score_end_bar=2,
            score_end_beat=1,
            score_end_subdivision=0,
            duration_bars=99,
            duration_beats=99,
            duration_subdivisions=99,
        )
        self.assertEqual(result.mode, "exact")
        self.assertEqual(result.end_tick_exclusive, 1920)
        self.assertEqual(result.exact_end_position, ScorePosition(2, 1, 0))
        self.assertEqual((result.duration_bars, result.duration_beats), (99, 99))

    def test_exact_end_equal_to_start_is_valid(self):
        result = selection(
            score_end_bar=1,
            score_end_beat=1,
            score_end_subdivision=0,
        )
        self.assertEqual(result.start_tick, result.end_tick_exclusive)

    def test_exact_end_before_start_is_invalid(self):
        with self.assertRaises(ScoreSelectionError) as caught:
            selection(
                start_bar=2,
                score_end_bar=1,
                score_end_beat=1,
                score_end_subdivision=0,
            )
        self.assertEqual(caught.exception.code, "score_selection_range_invalid")

    def test_only_all_zero_is_the_unset_end(self):
        for values in ((0, 1, 0), (1, 0, 0), (0, 0, 1)):
            with self.subTest(values=values), self.assertRaises(ScoreSelectionError) as caught:
                selection(
                    score_end_bar=values[0],
                    score_end_beat=values[1],
                    score_end_subdivision=values[2],
                )
            self.assertEqual(caught.exception.code, "score_selection_range_invalid")

    def test_noncanonical_start_and_end_are_invalid(self):
        for changes in (
            {"start_beat": 5},
            {"start_subdivision": 4},
            {"score_end_bar": 1, "score_end_beat": 5, "score_end_subdivision": 0},
            {"score_end_bar": 1, "score_end_beat": 1, "score_end_subdivision": 4},
        ):
            with self.subTest(changes=changes), self.assertRaises(ScoreSelectionError) as caught:
                selection(**changes)
            self.assertEqual(caught.exception.code, "score_selection_range_invalid")

    def test_invalid_built_in_domains_and_bools_are_rejected(self):
        for field, value in (
            ("start_bar", True),
            ("start_beat", 1.0),
            ("duration_bars", -1),
            ("duration_beats", False),
            ("score_end_bar", -1),
            ("subdivisions_per_beat", 0),
        ):
            with self.subTest(field=field), self.assertRaises(ScoreSelectionError) as caught:
                selection(**{field: value})
            self.assertEqual(caught.exception.code, "score_selection_range_invalid")

    def test_over_fine_grid_is_invalid(self):
        with self.assertRaises(ScoreSelectionError) as caught:
            selection(subdivisions_per_beat=481)
        self.assertEqual(caught.exception.code, "score_selection_range_invalid")


class DurationFallbackTests(unittest.TestCase):
    def test_zero_duration_is_valid(self):
        result = selection(duration_bars=0)
        self.assertEqual(result.mode, "duration")
        self.assertEqual(result.start_tick, result.end_tick_exclusive)

    def test_overflow_is_one_absolute_quantity_without_drift(self):
        result = selection(
            start_beat=2,
            start_subdivision=1,
            duration_bars=0,
            duration_beats=5,
            duration_subdivisions=7,
        )
        self.assertEqual(result.end_tick_exclusive, 480 + 120 + (5 * 4 + 7) * 120)

    def test_uneven_subdivision_grid_does_not_accumulate_rounding(self):
        # subdivisions_per_beat=7 does not evenly divide ticks_per_beat (480),
        # so start and end are each individually rounded. The end must be
        # computed by combining the start offset and duration into one
        # subdivision count before splitting and rounding, not by adding an
        # independently rounded duration delta to the already-rounded start
        # tick (which can produce a tick that fails to round-trip).
        result = selection(
            start_subdivision=3,
            duration_bars=0,
            duration_subdivisions=3,
            subdivisions_per_beat=7,
        )
        self.assertEqual(result.start_tick, 206)
        self.assertEqual(result.end_tick_exclusive, 411)

    def test_multiple_tempo_events_are_allowed_and_seconds_are_piecewise(self):
        value = resolver(
            score(tempos=(TempoEvent(0, 500_000), TempoEvent(960, 250_000)))
        )
        result = selection(value)
        self.assertEqual(result.end_tick_exclusive, 1920)
        self.assertEqual(result.requested_end_score_seconds, 1.5)

    def test_meter_event_at_end_is_allowed(self):
        value = resolver(
            score(
                meters=(MeterEvent(0, 4, 4), MeterEvent(1920, 3, 4)),
                has_variable_meter=True,
            )
        )
        self.assertEqual(selection(value).end_tick_exclusive, 1920)

    def test_meter_event_strictly_inside_requires_exact_end(self):
        value = resolver(
            score(
                meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
                has_variable_meter=True,
                has_midbar_meter_change=True,
            )
        )
        with self.assertRaises(ScoreSelectionError) as caught:
            selection(value)
        self.assertEqual(caught.exception.code, "score_end_position_required")
        self.assertEqual(
            caught.exception.message,
            "an exact Score end is required because the duration crosses a meter change",
        )

    def test_exact_end_crosses_midbar_meter(self):
        value = resolver(
            score(
                meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
                has_variable_meter=True,
                has_midbar_meter_change=True,
            )
        )
        result = selection(
            value,
            score_end_bar=3,
            score_end_beat=1,
            score_end_subdivision=0,
        )
        self.assertEqual(result.end_tick_exclusive, 2440)

    def test_odd_31_32_uses_exact_ticks(self):
        raw = json.loads(
            (ROOT / "tests/fixtures/scores/odd_meter_31_32.json").read_text(
                encoding="utf-8"
            )
        )
        result = selection(
            resolver(score_from_dict(raw)),
            subdivisions_per_beat=2,
        )
        self.assertEqual(result.end_tick_exclusive, 1860)

    def test_score_and_audio_seconds_include_alignment_once(self):
        result = selection(resolver(alignment=1.25))
        self.assertEqual(result.requested_start_score_seconds, 0.0)
        self.assertEqual(result.requested_start_seconds, 1.25)
        self.assertEqual(result.requested_end_score_seconds, 2.0)
        self.assertEqual(result.requested_end_seconds, 3.25)


class SelectionAnchorTests(unittest.TestCase):
    def test_unclamped_musical_preserves_exact_tick(self):
        value = resolver()
        self.assertEqual(
            selection_start_score_tick(
                value,
                edit_mode="Musical",
                clamped=False,
                requested_start_seconds=0.1,
                returned_start_seconds=0.2,
                musical_start_tick=123,
            ),
            123,
        )

    def test_other_paths_use_requested_or_returned_audio_seconds(self):
        value = resolver(alignment=1.0)
        cases = (
            ("Seconds", False, 1.5, 1.6, None, 480.0),
            ("Seconds", True, 1.5, 1.25, None, 240.0),
            ("Musical", True, -1.0, 0.5, 0, -480.0),
        )
        for mode, clamped, requested, returned, tick, expected in cases:
            with self.subTest(mode=mode, clamped=clamped):
                self.assertEqual(
                    selection_start_score_tick(
                        value,
                        edit_mode=mode,
                        clamped=clamped,
                        requested_start_seconds=requested,
                        returned_start_seconds=returned,
                        musical_start_tick=tick,
                    ),
                    expected,
                )

    def test_invalid_anchor_domains_are_rejected(self):
        value = resolver()
        common = dict(
            resolver=value,
            edit_mode="Musical",
            clamped=False,
            requested_start_seconds=0.0,
            returned_start_seconds=0.0,
            musical_start_tick=None,
        )
        with self.assertRaises(ValueError):
            selection_start_score_tick(**common)
        for field, bad in (("clamped", 0), ("requested_start_seconds", math.inf)):
            values = {**common, "musical_start_tick": 0, field: bad}
            with self.subTest(field=field), self.assertRaises((TypeError, ValueError)):
                selection_start_score_tick(**values)


if __name__ == "__main__":
    unittest.main()
