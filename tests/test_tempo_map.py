"""Contract and regression tests for the pure Phase 3a timing adapters."""

import ast
from dataclasses import FrozenInstanceError, fields, is_dataclass
import inspect
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from musical_timing import (
    ConstantTempoMap,
    LegacyTimingConfiguration,
    MusicalTimingResult,
    NearestPosition,
    ScoreRelativeSpan,
    TempoMap,
    TempoMapBinding,
    TempoUnit,
    UniformTimingBridge,
    UniformTimingMetrics,
    calculate_musical_timing,
)
from score.model import MeterEvent, Score, Section, TempoEvent
from score.resolver import ScoreResolver
from score.tempo_map import ScoreTempoMap


ROOT = Path(__file__).resolve().parents[1]

BASE_INPUTS = {
    "bpm": 120,
    "tempo_unit": "Quarter",
    "beats_per_bar": 4,
    "beat_unit": 4,
    "fps": 24,
    "subdivisions_per_beat": 4,
}


def calculate(**overrides: object) -> MusicalTimingResult:
    inputs = {**BASE_INPUTS, **overrides}
    return calculate_musical_timing(**inputs)  # type: ignore[arg-type]


def score_with(**changes: object) -> Score:
    values = {
        "ticks_per_quarter": 960,
        "tempos": (TempoEvent(0, 500_000),),
        "meters": (MeterEvent(0, 4, 4),),
        "markers": (),
        "sections": (),
        "source": "constant",
        "meter_estimated": False,
        "has_variable_meter": False,
        "has_midbar_meter_change": False,
    }
    values.update(changes)
    return Score(**values)


def resolver_for(
    score: Score | None = None,
    *,
    alignment: float = 0.0,
    duration: float = 12.0,
) -> ScoreResolver:
    return ScoreResolver(score or score_with(), duration, alignment)


def span_arguments(**overrides: int) -> dict[str, int]:
    values = {
        "start_bar": 1,
        "start_beat": 1,
        "start_subdivision": 0,
        "duration_bars": 0,
        "duration_beats": 0,
        "duration_subdivisions": 0,
        "subdivisions_per_beat": 4,
    }
    values.update(overrides)
    return values


class SharedContractTests(unittest.TestCase):
    def test_all_shared_values_are_frozen_dataclasses(self) -> None:
        values = (
            LegacyTimingConfiguration(120, "Quarter", 4, 4),
            TempoMapBinding(LegacyTimingConfiguration(120, "Quarter", 4, 4), None),
            UniformTimingMetrics(0.5, 0.5, 0.5, 2.0, 4, 4),
            ScoreRelativeSpan(0.0, 0.0, 0.0, 0.0),
            NearestPosition(1, 1, 0, 0),
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertTrue(is_dataclass(value))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, fields(value)[0].name, None)

    def test_binding_accepts_legacy_variant(self) -> None:
        configuration = LegacyTimingConfiguration(120, "Quarter", 4, 4)
        binding = TempoMapBinding(configuration, None)
        self.assertIs(binding.legacy_configuration, configuration)
        self.assertIsNone(binding.alignment_seconds)

    def test_binding_accepts_alignment_variant_without_coercion(self) -> None:
        binding = TempoMapBinding(None, -1)
        self.assertEqual(binding.alignment_seconds, -1)
        self.assertIs(type(binding.alignment_seconds), int)

    def test_binding_rejects_both_or_neither_variant(self) -> None:
        configuration = LegacyTimingConfiguration(120, "Quarter", 4, 4)
        for arguments in ((configuration, 0.0), (None, None)):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(
                ValueError,
                "internal contract violation",
            ):
                TempoMapBinding(*arguments)

    def test_binding_rejects_invalid_alignment_values(self) -> None:
        for value in (True, math.nan, math.inf, -math.inf, "0"):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                TempoMapBinding(None, value)  # type: ignore[arg-type]

    def test_binding_rejects_noncanonical_legacy_value(self) -> None:
        with self.assertRaisesRegex(TypeError, "internal contract violation"):
            TempoMapBinding(object(), None)  # type: ignore[arg-type]

    def test_nearest_position_accepts_canonical_variant(self) -> None:
        self.assertEqual(NearestPosition(2, 3, 4, 0), NearestPosition(2, 3, 4, 0))

    def test_nearest_position_accepts_pre_bar_one_variant(self) -> None:
        self.assertEqual(
            NearestPosition(None, None, None, 4).subdivisions_before_bar_one,
            4,
        )

    def test_nearest_position_rejects_all_invalid_variants(self) -> None:
        cases = (
            (None, None, None, 0),
            (None, None, None, -1),
            (1, 1, 0, 1),
            (None, 1, 0, 0),
            (1, None, 0, 0),
            (1, 1, None, 0),
            (0, 1, 0, 0),
            (1, 0, 0, 0),
            (1, 1, -1, 0),
            (True, 1, 0, 0),
            (1, True, 0, 0),
            (1, 1, False, 0),
            (None, None, None, True),
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises((TypeError, ValueError)):
                NearestPosition(*case)

    def test_complete_protocol_has_exact_method_surface(self) -> None:
        public = {name for name in TempoMap.__dict__ if not name.startswith("_")}
        self.assertEqual(
            public,
            {
                "tick_to_seconds",
                "seconds_to_tick",
                "bar_to_tick",
                "position_to_tick",
                "tick_to_position",
                "meter_at_bar",
                "sections",
            },
        )

    def test_uniform_bridge_has_exact_method_surface(self) -> None:
        public = {
            name for name in UniformTimingBridge.__dict__ if not name.startswith("_")
        }
        self.assertEqual(
            public,
            {
                "supports_uniform_timing",
                "binding",
                "uniform_timing_metrics",
                "resolve_score_relative_span",
                "describe_nearest_position",
            },
        )

    def test_tempo_unit_contract_is_publicly_reexported(self) -> None:
        self.assertEqual(
            TempoUnit.__args__,
            ("Quarter", "Eighth", "Dotted Quarter"),
        )


class ConstantTempoMapTests(unittest.TestCase):
    def test_constructor_is_frozen_and_binding_is_exact(self) -> None:
        timing_map = ConstantTempoMap(127, "Quarter", 4, 4)
        self.assertTrue(timing_map.supports_uniform_timing)
        self.assertEqual(
            timing_map.binding,
            TempoMapBinding(
                LegacyTimingConfiguration(127, "Quarter", 4, 4),
                None,
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            timing_map.bpm = 120  # type: ignore[misc]

    def test_constructor_rejects_invalid_values(self) -> None:
        cases = (
            (True, "Quarter", 4, 4),
            (math.nan, "Quarter", 4, 4),
            (0, "Quarter", 4, 4),
            (120, 1, 4, 4),
            (120, "Half", 4, 4),
            (120, "Quarter", True, 4),
            (120, "Quarter", 0, 4),
            (120, "Quarter", 4, True),
            (120, "Quarter", 4, 0),
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises((TypeError, ValueError)):
                ConstantTempoMap(*case)  # type: ignore[arg-type]

    def test_uniform_metrics_use_historical_float_arithmetic(self) -> None:
        metrics = ConstantTempoMap(90, "Dotted Quarter", 6, 8).uniform_timing_metrics()
        self.assertAlmostEqual(metrics.seconds_per_tempo_pulse, 2 / 3)
        self.assertAlmostEqual(metrics.seconds_per_quarter, 4 / 9)
        self.assertAlmostEqual(metrics.seconds_per_beat, 2 / 9)
        self.assertAlmostEqual(metrics.seconds_per_bar, 4 / 3)
        self.assertEqual((metrics.beats_per_bar, metrics.beat_unit), (6, 8))

    def test_span_preserves_start_and_duration_overflow_arithmetically(self) -> None:
        timing_map = ConstantTempoMap(120, "Quarter", 4, 4)
        span = timing_map.resolve_score_relative_span(
            **span_arguments(
                start_subdivision=9,
                duration_bars=1,
                duration_beats=5,
                duration_subdivisions=6,
            )
        )
        self.assertEqual(span.start_beats, 2.25)
        self.assertEqual(span.start_seconds, 1.125)
        self.assertEqual(span.duration_beats_total, 10.5)
        self.assertEqual(span.duration_seconds, 5.25)

    def test_span_does_not_call_synthetic_tick_methods(self) -> None:
        timing_map = ConstantTempoMap(120, "Quarter", 4, 4)
        with (
            patch.object(ConstantTempoMap, "tick_to_seconds", side_effect=AssertionError),
            patch.object(ConstantTempoMap, "position_to_tick", side_effect=AssertionError),
        ):
            span = timing_map.resolve_score_relative_span(**span_arguments())
        self.assertEqual(span, ScoreRelativeSpan(0.0, 0.0, 0.0, 0.0))

    def test_nearest_position_negative_boundaries(self) -> None:
        timing_map = ConstantTempoMap(60, "Quarter", 4, 4)
        width = 1.0 / 4
        cases = (
            (-0.49 * width, NearestPosition(1, 1, 0, 0)),
            (-0.5 * width, NearestPosition(None, None, None, 1)),
            (-4.0 * width, NearestPosition(None, None, None, 4)),
        )
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(timing_map.describe_nearest_position(seconds, 4), expected)

    def test_nearest_position_uses_linear_meter_and_no_tick_method(self) -> None:
        timing_map = ConstantTempoMap(60, "Quarter", 3, 4)
        with (
            patch.object(ConstantTempoMap, "tick_to_position", side_effect=AssertionError),
            patch.object(ConstantTempoMap, "seconds_to_tick", side_effect=AssertionError),
        ):
            nearest = timing_map.describe_nearest_position(3.5, 2)
        self.assertEqual(nearest, NearestPosition(2, 1, 1, 0))

    def test_synthetic_tick_seconds_surface_uses_960_tpq(self) -> None:
        timing_map = ConstantTempoMap(120, "Quarter", 4, 4)
        self.assertEqual(timing_map.tick_to_seconds(960), 0.5)
        self.assertEqual(timing_map.seconds_to_tick(0.5), 960.0)
        self.assertAlmostEqual(
            timing_map.tick_to_seconds(timing_map.seconds_to_tick(12.25)),
            12.25,
        )

    def test_synthetic_bar_meter_and_sections_surface(self) -> None:
        timing_map = ConstantTempoMap(120, "Quarter", 3, 8)
        self.assertEqual(timing_map.bar_to_tick(1), 0)
        self.assertEqual(timing_map.bar_to_tick(2), 1440)
        self.assertEqual(timing_map.meter_at_bar(999), (3, 8))
        self.assertEqual(timing_map.sections(), ())

    def test_synthetic_position_surface_rounds_absolutely(self) -> None:
        timing_map = ConstantTempoMap(120, "Quarter", 1, 8)
        ticks = [timing_map.position_to_tick(1, 1, subdivision, 7) for subdivision in range(7)]
        self.assertEqual(ticks, [0, 69, 137, 206, 274, 343, 411])
        self.assertEqual(timing_map.position_to_tick(2, 1, 0, 7), 480)
        for subdivision, tick in enumerate(ticks):
            self.assertEqual(timing_map.tick_to_position(tick, 7), (1, 1, subdivision))

    def test_tick_to_position_tie_chooses_later_grid_tick(self) -> None:
        timing_map = ConstantTempoMap(120, "Quarter", 4, 4)
        self.assertEqual(timing_map.tick_to_position(480, 1), (1, 2, 0))

    def test_synthetic_surface_rejects_invalid_queries_and_overfine_grid(self) -> None:
        timing_map = ConstantTempoMap(120, "Quarter", 4, 4)
        for tick in (True, math.nan, math.inf, "0"):
            with self.subTest(tick=tick), self.assertRaises((TypeError, ValueError)):
                timing_map.tick_to_seconds(tick)  # type: ignore[arg-type]
        for call in (
            lambda: timing_map.bar_to_tick(0),
            lambda: timing_map.position_to_tick(1, 5, 0, 4),
            lambda: timing_map.position_to_tick(1, 1, 4, 4),
            lambda: timing_map.position_to_tick(1, 1, 0, 961),
            lambda: timing_map.tick_to_position(-1, 4),
            lambda: timing_map.tick_to_position(0, 961),
        ):
            with self.subTest(call=call), self.assertRaises((TypeError, ValueError)):
                call()

    def test_omitted_and_matching_explicit_maps_are_numerically_identical(self) -> None:
        inputs = {
            "bpm": 127,
            "tempo_unit": "Quarter",
            "beats_per_bar": 4,
            "beat_unit": 4,
            "fps": 25,
            "subdivisions_per_beat": 7,
            "downbeat_offset": -0.25,
            "start_bar": 11,
            "start_beat": 1,
            "start_subdivision": 3,
            "duration_bars": 2,
            "duration_beats": 1,
            "duration_subdivisions": 5,
        }
        omitted = calculate_musical_timing(**inputs)  # type: ignore[arg-type]
        explicit = calculate_musical_timing(
            **inputs,
            tempo_map=ConstantTempoMap(127, "Quarter", 4, 4),
        )  # type: ignore[arg-type]
        self.assertEqual(explicit, omitted)

    def test_conflicting_explicit_map_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicts"):
            calculate(tempo_map=ConstantTempoMap(121, "Quarter", 4, 4))


class ScoreTempoMapTests(unittest.TestCase):
    def test_constructor_is_frozen_and_requires_exact_resolver(self) -> None:
        resolver = resolver_for()
        timing_map = ScoreTempoMap(resolver)
        self.assertIs(timing_map.resolver, resolver)
        with self.assertRaises(FrozenInstanceError):
            timing_map.resolver = resolver  # type: ignore[misc]
        with self.assertRaises(TypeError):
            ScoreTempoMap(object())  # type: ignore[arg-type]

    def test_binding_contains_exact_resolver_alignment(self) -> None:
        timing_map = ScoreTempoMap(resolver_for(alignment=-1.25))
        self.assertEqual(timing_map.binding, TempoMapBinding(None, -1.25))

    def test_complete_surface_matches_resolver_and_sections_passthrough(self) -> None:
        section = Section("Verse", 0, 1920, True)
        resolver = resolver_for(score_with(sections=(section,)))
        timing_map = ScoreTempoMap(resolver)
        self.assertEqual(timing_map.tick_to_seconds(960), resolver.tick_to_seconds(960))
        self.assertEqual(timing_map.seconds_to_tick(0.5), resolver.seconds_to_tick(0.5))
        self.assertEqual(timing_map.bar_to_tick(2), resolver.bar_to_tick(2))
        self.assertEqual(timing_map.position_to_tick(1, 2, 2, 4), 1440)
        self.assertEqual(timing_map.tick_to_position(1440, 4), (1, 2, 2))
        self.assertEqual(timing_map.meter_at_bar(1), (4, 4))
        self.assertIs(timing_map.sections(), resolver.score.sections)

    def test_complete_surface_remains_available_for_variable_tempo(self) -> None:
        score = score_with(tempos=(TempoEvent(0, 500_000), TempoEvent(960, 400_000)))
        timing_map = ScoreTempoMap(resolver_for(score))
        self.assertFalse(timing_map.supports_uniform_timing)
        self.assertEqual(timing_map.tick_to_seconds(1920), 0.9)
        self.assertEqual(timing_map.position_to_tick(2, 1, 0, 4), 3840)

    def test_complete_surface_remains_available_for_variable_meter(self) -> None:
        score = score_with(
            meters=(MeterEvent(0, 4, 4), MeterEvent(3840, 3, 4)),
            has_variable_meter=True,
        )
        timing_map = ScoreTempoMap(resolver_for(score))
        self.assertFalse(timing_map.supports_uniform_timing)
        self.assertEqual(timing_map.meter_at_bar(2), (3, 4))
        self.assertEqual(timing_map.position_to_tick(2, 3, 0, 1), 5760)

    def test_complete_surface_remains_available_for_midbar_meter(self) -> None:
        score = score_with(
            meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
            has_variable_meter=True,
            has_midbar_meter_change=True,
        )
        timing_map = ScoreTempoMap(resolver_for(score))
        self.assertFalse(timing_map.supports_uniform_timing)
        self.assertEqual(timing_map.bar_to_tick(2), 1000)
        self.assertEqual(timing_map.tick_to_position(1000, 1), (2, 1, 0))

    def test_uniform_gate_is_exact(self) -> None:
        scores = (
            (score_with(), True),
            (
                score_with(
                    tempos=(TempoEvent(0, 500_000), TempoEvent(960, 400_000))
                ),
                False,
            ),
            (
                score_with(
                    meters=(MeterEvent(0, 4, 4), MeterEvent(3840, 3, 4)),
                    has_variable_meter=True,
                ),
                False,
            ),
            (
                score_with(
                    meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
                    has_variable_meter=True,
                    has_midbar_meter_change=True,
                ),
                False,
            ),
        )
        for score, expected in scores:
            with self.subTest(score=score):
                self.assertIs(ScoreTempoMap(resolver_for(score)).supports_uniform_timing, expected)

    def test_uniform_bridge_methods_reject_every_nonuniform_case(self) -> None:
        scores = (
            score_with(tempos=(TempoEvent(0, 500_000), TempoEvent(960, 400_000))),
            score_with(
                meters=(MeterEvent(0, 4, 4), MeterEvent(3840, 3, 4)),
                has_variable_meter=True,
            ),
            score_with(
                meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
                has_variable_meter=True,
                has_midbar_meter_change=True,
            ),
        )
        for score in scores:
            timing_map = ScoreTempoMap(resolver_for(score))
            with self.subTest(score=score), self.assertRaisesRegex(ValueError, "uniform"):
                timing_map.uniform_timing_metrics()
            with self.subTest(score=score), self.assertRaisesRegex(ValueError, "uniform"):
                timing_map.resolve_score_relative_span(**span_arguments())
            with self.subTest(score=score), self.assertRaisesRegex(ValueError, "uniform"):
                timing_map.describe_nearest_position(0.0, 4)

    def test_uniform_metrics_use_only_score_tempo_and_meter(self) -> None:
        score = score_with(
            tempos=(TempoEvent(0, 600_000),),
            meters=(MeterEvent(0, 3, 8),),
        )
        metrics = ScoreTempoMap(resolver_for(score)).uniform_timing_metrics()
        self.assertEqual(metrics.seconds_per_tempo_pulse, 0.6)
        self.assertEqual(metrics.seconds_per_quarter, 0.6)
        self.assertEqual(metrics.seconds_per_beat, 0.3)
        self.assertAlmostEqual(metrics.seconds_per_bar, 0.9)
        self.assertEqual((metrics.beats_per_bar, metrics.beat_unit), (3, 8))

    def test_uniform_span_uses_canonical_ticks_and_end_position(self) -> None:
        score = score_with(tempos=(TempoEvent(0, 472_441),))
        resolver = resolver_for(score)
        timing_map = ScoreTempoMap(resolver)
        span = timing_map.resolve_score_relative_span(
            **span_arguments(
                start_bar=2,
                start_beat=2,
                start_subdivision=1,
                duration_bars=1,
                duration_beats=5,
                duration_subdivisions=6,
                subdivisions_per_beat=4,
            )
        )
        start_tick = resolver.position_to_tick(2, 2, 1, 4)
        end_tick = resolver.position_to_tick(4, 4, 3, 4)
        self.assertEqual(span.start_seconds, resolver.tick_to_seconds(start_tick))
        self.assertEqual(
            span.duration_seconds,
            resolver.tick_to_seconds(end_tick) - resolver.tick_to_seconds(start_tick),
        )
        self.assertEqual(span.duration_beats_total, 10.5)

    def test_uniform_three_four_bar_eleven_start_beats_is_thirty(self) -> None:
        score = score_with(meters=(MeterEvent(0, 3, 4),))
        span = ScoreTempoMap(resolver_for(score)).resolve_score_relative_span(
            **span_arguments(start_bar=11)
        )
        self.assertEqual(span.start_beats, 30)

    def test_positive_nearest_position_uses_resolver(self) -> None:
        timing_map = ScoreTempoMap(resolver_for())
        seconds = timing_map.tick_to_seconds(1440)
        self.assertEqual(
            timing_map.describe_nearest_position(seconds, 4),
            NearestPosition(1, 2, 2, 0),
        )

    def test_negative_nearest_boundaries_match_constant_behavior(self) -> None:
        resolver = resolver_for()
        timing_map = ScoreTempoMap(resolver)
        width = 0.5 / 4
        cases = (
            (-0.49 * width, NearestPosition(1, 1, 0, 0)),
            (-0.5 * width, NearestPosition(None, None, None, 1)),
            (-4.0 * width, NearestPosition(None, None, None, 4)),
        )
        with patch.object(
            ScoreResolver,
            "tick_to_position",
            side_effect=AssertionError("negative time reached tick_to_position"),
        ):
            for seconds, expected in cases:
                with self.subTest(seconds=seconds):
                    self.assertEqual(
                        timing_map.describe_nearest_position(seconds, 4),
                        expected,
                    )

    def test_score_span_is_alignment_independent(self) -> None:
        first = ScoreTempoMap(resolver_for(alignment=-2.0))
        second = ScoreTempoMap(resolver_for(alignment=3.0))
        arguments = span_arguments(start_bar=2, duration_beats=1)
        self.assertEqual(
            first.resolve_score_relative_span(**arguments),
            second.resolve_score_relative_span(**arguments),
        )


class TimingAuthorityTests(unittest.TestCase):
    def test_constant_three_four_rejects_beat_four(self) -> None:
        with self.assertRaisesRegex(ValueError, "start_beat"):
            calculate(beats_per_bar=3, start_beat=4)

    def test_score_four_four_accepts_beat_four_with_widget_three_four(self) -> None:
        timing_map = ScoreTempoMap(resolver_for())
        result = calculate(
            beats_per_bar=3,
            start_beat=4,
            tempo_map=timing_map,
        )
        self.assertEqual(result.start_beats, 3)
        self.assertEqual(result.start_seconds, 1.5)

    def test_score_three_four_rejects_beat_four_with_widget_four_four(self) -> None:
        score = score_with(meters=(MeterEvent(0, 3, 4),))
        timing_map = ScoreTempoMap(resolver_for(score))
        with self.assertRaisesRegex(ValueError, "beat exceeds"):
            calculate(start_beat=4, tempo_map=timing_map)

    def test_invalid_legacy_domains_still_fail_under_score_authority(self) -> None:
        timing_map = ScoreTempoMap(resolver_for())
        cases = (
            {"bpm": 0},
            {"tempo_unit": "Half"},
            {"beats_per_bar": 0},
            {"beat_unit": 0},
            {"fps": 0},
            {"subdivisions_per_beat": 0},
            {"start_bar": 0},
            {"start_subdivision": -1},
            {"duration_beats": -1},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises((TypeError, ValueError)):
                calculate(tempo_map=timing_map, **overrides)

    def test_matching_alignment_is_added_exactly_once(self) -> None:
        timing_map = ScoreTempoMap(resolver_for(alignment=1.25))
        result = calculate(
            downbeat_offset=1.25,
            start_bar=2,
            tempo_map=timing_map,
        )
        self.assertEqual(result.start_seconds, 3.25)

    def test_negative_alignment_remains_supported(self) -> None:
        timing_map = ScoreTempoMap(resolver_for(alignment=-1.5))
        result = calculate(downbeat_offset=-1.5, tempo_map=timing_map)
        self.assertEqual(result.start_seconds, -1.5)
        self.assertEqual(result.start_frame, -36)

    def test_stale_alignment_is_rejected_deterministically(self) -> None:
        timing_map = ScoreTempoMap(resolver_for(alignment=1.0))
        with self.assertRaisesRegex(ValueError, "stale or inconsistent"):
            calculate(downbeat_offset=0.0, tempo_map=timing_map)

    def test_nonuniform_map_is_rejected_before_result_assembly(self) -> None:
        score = score_with(tempos=(TempoEvent(0, 500_000), TempoEvent(960, 400_000)))
        with self.assertRaisesRegex(ValueError, "uniform timing"):
            calculate(tempo_map=ScoreTempoMap(resolver_for(score)))

    def test_golden_divergence_pair_protects_both_paths(self) -> None:
        # This divergent regression pair uses the contract's synthetic Score
        # tick space of 960 TPQ. ConstantTempoMap's legacy span calculation
        # must not use that tick space. If the synthetic TPQ contract changes,
        # replace this pair with a newly verified divergent case.
        inputs = {
            "bpm": 127,
            "tempo_unit": "Quarter",
            "beats_per_bar": 4,
            "beat_unit": 4,
            "fps": 25,
            "subdivisions_per_beat": 7,
            "start_bar": 11,
            "start_beat": 1,
            "start_subdivision": 3,
        }
        constant = calculate_musical_timing(**inputs)  # type: ignore[arg-type]
        score = score_with(tempos=(TempoEvent(0, 472_441),))
        resolver = resolver_for(score, duration=30.0)
        self.assertEqual(resolver.position_to_tick(11, 1, 3, 7), 38_811)
        canonical = calculate_musical_timing(
            **inputs,
            tempo_map=ScoreTempoMap(resolver),
        )  # type: ignore[arg-type]
        self.assertAlmostEqual(constant.start_seconds, 19.100112485939, places=12)
        self.assertAlmostEqual(canonical.start_seconds, 19.099903803125, places=12)
        self.assertEqual(constant.start_frame, 478)
        self.assertEqual(canonical.start_frame, 477)


class ArchitectureAndImportTests(unittest.TestCase):
    def test_shared_contract_leaf_has_only_allowed_imports(self) -> None:
        tree = ast.parse((ROOT / "tempo_map_contract.py").read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        self.assertEqual(modules, {"__future__", "dataclasses", "typing"})

    def test_production_import_directions_are_acyclic(self) -> None:
        musical_tree = ast.parse((ROOT / "musical_timing.py").read_text(encoding="utf-8"))
        score_tree = ast.parse((ROOT / "score" / "tempo_map.py").read_text(encoding="utf-8"))
        musical_imports = {
            node.module or ""
            for node in ast.walk(musical_tree)
            if isinstance(node, ast.ImportFrom)
        }
        score_imports = {
            node.module or ""
            for node in ast.walk(score_tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertFalse(any(name == "score" or name.startswith("score.") for name in musical_imports))
        self.assertNotIn("musical_timing", score_imports)
        self.assertIn("resolver", score_imports)
        self.assertIn("model", score_imports)

    def test_shared_dataclasses_are_defined_only_in_contract_leaf(self) -> None:
        names = {
            "LegacyTimingConfiguration",
            "TempoMapBinding",
            "UniformTimingMetrics",
            "ScoreRelativeSpan",
            "NearestPosition",
        }
        definitions: dict[str, list[str]] = {name: [] for name in names}
        for path in (
            ROOT / "tempo_map_contract.py",
            ROOT / "musical_timing.py",
            ROOT / "score" / "tempo_map.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in names:
                    definitions[node.name].append(path.name)
        self.assertEqual(
            definitions,
            {name: ["tempo_map_contract.py"] for name in names},
        )

    def test_calculation_uses_no_concrete_isinstance_dispatch(self) -> None:
        source = inspect.getsource(calculate_musical_timing)
        self.assertNotIn("isinstance", source)
        tree = ast.parse(source)
        loaded_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        self.assertNotIn("ScoreTempoMap", loaded_names)

    def test_score_package_initializer_remains_zero_bytes(self) -> None:
        self.assertEqual((ROOT / "score" / "__init__.py").read_bytes(), b"")

    def test_phase3a_production_contains_no_side_effect_capabilities(self) -> None:
        forbidden_imports = {
            "av",
            "json",
            "logging",
            "os",
            "pathlib",
            "requests",
            "socket",
            "torch",
            "urllib",
        }
        forbidden_calls = {"open", "print", "register", "getenv", "putenv"}
        for relative in ("tempo_map_contract.py", "musical_timing.py", "score/tempo_map.py"):
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            imports = set()
            calls = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
            with self.subTest(relative=relative):
                self.assertFalse(forbidden_imports & imports)
                self.assertFalse(forbidden_calls & calls)

    def test_flat_imports_are_silent_and_stdlib_only(self) -> None:
        code = """
import os
before = dict(os.environ)
import tempo_map_contract
import musical_timing
import score.tempo_map
assert os.environ == before
forbidden = [name for name in sys.modules if name.startswith(('av', 'torch', 'comfy', 'server', 'waveform_routes'))]
assert forbidden == [], forbidden
"""
        self._assert_silent_fresh_import(code, include_sys_import=True)

    def test_package_context_imports_are_silent(self) -> None:
        code = """
import importlib, os, pathlib, sys, types
before = dict(os.environ)
package = types.ModuleType('phase3_package')
package.__path__ = [str(pathlib.Path.cwd())]
sys.modules['phase3_package'] = package
importlib.import_module('phase3_package.tempo_map_contract')
importlib.import_module('phase3_package.musical_timing')
importlib.import_module('phase3_package.score.tempo_map')
assert os.environ == before
assert 'score' not in sys.modules
"""
        self._assert_silent_fresh_import(code)

    def test_arbitrary_parent_package_imports_do_not_leak_top_level_score(self) -> None:
        code = """
import importlib, os, pathlib, sys, types
before = dict(os.environ)
outer = types.ModuleType('arbitrary_parent')
outer.__path__ = []
inner = types.ModuleType('arbitrary_parent.child')
inner.__path__ = [str(pathlib.Path.cwd())]
sys.modules['arbitrary_parent'] = outer
sys.modules['arbitrary_parent.child'] = inner
importlib.import_module('arbitrary_parent.child.tempo_map_contract')
importlib.import_module('arbitrary_parent.child.musical_timing')
importlib.import_module('arbitrary_parent.child.score.tempo_map')
assert os.environ == before
assert 'score' not in sys.modules
"""
        self._assert_silent_fresh_import(code)

    def _assert_silent_fresh_import(
        self,
        code: str,
        *,
        include_sys_import: bool = False,
    ) -> None:
        if include_sys_import:
            code = "import sys\n" + code
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
