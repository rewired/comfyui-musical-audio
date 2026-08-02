import ast
import dataclasses
from decimal import Decimal
from fractions import Fraction
import inspect
import json
import math
from pathlib import Path
import random
from types import MappingProxyType
import unittest

from score.bars import (
    absolute_boundary,
    build_bar_grid,
    round_half_away_from_zero_ratio,
)
from score.model import Marker, MeterEvent, Score, Section, TempoEvent
from score.resolver import ScoreResolver
from score.serialize import score_from_dict


ROOT = Path(__file__).resolve().parents[1]
SCORE_FIXTURES = ROOT / "tests" / "fixtures" / "scores"


@dataclasses.dataclass(frozen=True)
class ResolverFixtureCase:
    audio_duration_seconds: float
    audio_seconds_at_tick_zero: float
    meaningful_query_ticks: tuple[int, ...]


RESOLVER_FIXTURE_CASES = MappingProxyType(
    {
        "constant_4_4.json": ResolverFixtureCase(8.0, 0.0, (0, 480, 1920)),
        "changing_meter.json": ResolverFixtureCase(5.0, 1.0, (0, 1920, 3360)),
        "midbar_tempo.json": ResolverFixtureCase(4.0, -0.5, (0, 960, 1920)),
        "midbar_meter.json": ResolverFixtureCase(0.25, 1.0, (0, 1000, 2440)),
        "odd_meter_31_32.json": ResolverFixtureCase(10.0, 0.0, (0, 60, 1860)),
        "unaligned_markers.json": ResolverFixtureCase(0.5, 0.25, (0, 481, 1920)),
        "truncated_tempo_track.json": ResolverFixtureCase(30.0, 0.0, (0, 960, 6000)),
    }
)


def load_fixture(name: str) -> Score:
    value = json.loads((SCORE_FIXTURES / name).read_text(encoding="utf-8"))
    return score_from_dict(value)


def basic_score(**changes: object) -> Score:
    values = {
        "ticks_per_quarter": 480,
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


def resolver_for(score: Score | None = None, duration: float = 8.0, alignment: float = 0.0) -> ScoreResolver:
    return ScoreResolver(score or basic_score(), duration, alignment)


class ScoreResolverTests(unittest.TestCase):
    def test_fixture_parameter_table_is_complete_and_immutable(self):
        self.assertEqual(
            tuple(RESOLVER_FIXTURE_CASES),
            (
                "constant_4_4.json",
                "changing_meter.json",
                "midbar_tempo.json",
                "midbar_meter.json",
                "odd_meter_31_32.json",
                "unaligned_markers.json",
                "truncated_tempo_track.json",
            ),
        )
        with self.assertRaises(TypeError):
            RESOLVER_FIXTURE_CASES["new.json"] = ResolverFixtureCase(1.0, 0.0, ())
        for name, case in RESOLVER_FIXTURE_CASES.items():
            score = load_fixture(name)
            resolver = ScoreResolver(
                score,
                case.audio_duration_seconds,
                case.audio_seconds_at_tick_zero,
            )
            self.assertIs(resolver.score, score)
            for tick in case.meaningful_query_ticks:
                self.assertAlmostEqual(
                    resolver.audio_seconds_to_tick(resolver.tick_to_audio_seconds(tick)),
                    tick,
                    places=9,
                )

    def test_fixture_table_covers_required_audio_contexts(self):
        cases = RESOLVER_FIXTURE_CASES
        self.assertGreater(cases["changing_meter.json"].audio_seconds_at_tick_zero, 0)
        self.assertLess(cases["midbar_tempo.json"].audio_seconds_at_tick_zero, 0)
        before_zero = cases["midbar_meter.json"]
        self.assertGreater(before_zero.audio_seconds_at_tick_zero, before_zero.audio_duration_seconds)
        truncated = load_fixture("truncated_tempo_track.json")
        resolver = ScoreResolver(truncated, 30.0, 0.0)
        self.assertGreater(resolver.bar_grid.boundaries[-1], 20_000)
        unaligned = load_fixture("unaligned_markers.json")
        short = ScoreResolver(unaligned, 0.5, 0.25)
        self.assertTrue(
            any(
                section.end_tick_exclusive > short.bar_grid.boundaries[-1]
                for section in unaligned.sections
            )
        )

    def test_rounding_helper_examples_and_large_integers(self):
        cases = {
            (0, 7): 0,
            (1, 2): 1,
            (3, 2): 2,
            (-1, 2): -1,
            (-3, 2): -2,
            (4, 10): 0,
            (6, 10): 1,
            (-4, 10): 0,
            (-6, 10): -1,
            (10**200 + 5, 10): 10**199 + 1,
        }
        for arguments, expected in cases.items():
            with self.subTest(arguments=arguments):
                self.assertEqual(round_half_away_from_zero_ratio(*arguments), expected)

    def test_rounding_helper_rejects_invalid_inputs(self):
        for numerator, denominator, error in (
            (True, 2, TypeError),
            (1, True, TypeError),
            (1.0, 2, TypeError),
            (1, 0, ValueError),
            (1, -2, ValueError),
        ):
            with self.subTest(numerator=numerator, denominator=denominator):
                with self.assertRaises(error):
                    round_half_away_from_zero_ratio(numerator, denominator)

    def test_absolute_boundary_examples(self):
        self.assertEqual(
            absolute_boundary(anchor_tick=37, bar_index=0, meter=MeterEvent(0, 4, 4), ticks_per_quarter=480),
            37,
        )
        self.assertEqual(
            absolute_boundary(anchor_tick=0, bar_index=1, meter=MeterEvent(0, 4, 4), ticks_per_quarter=480),
            1920,
        )
        self.assertEqual(
            absolute_boundary(anchor_tick=0, bar_index=1, meter=MeterEvent(0, 31, 32), ticks_per_quarter=480),
            1860,
        )
        self.assertEqual(
            absolute_boundary(anchor_tick=0, bar_index=1, meter=MeterEvent(0, 63, 64), ticks_per_quarter=480),
            1890,
        )
        self.assertEqual(
            [absolute_boundary(anchor_tick=0, bar_index=i, meter=MeterEvent(0, 1, 32), ticks_per_quarter=100) for i in range(5)],
            [0, 13, 25, 38, 50],
        )

    def test_absolute_boundary_agrees_with_grid_without_drift(self):
        meter = MeterEvent(0, 1, 32)
        grid = build_bar_grid(ticks_per_quarter=100, meters=(meter,), through_tick=40)
        for index, boundary in enumerate(grid.boundaries):
            self.assertEqual(
                boundary,
                absolute_boundary(anchor_tick=0, bar_index=index, meter=meter, ticks_per_quarter=100),
            )
        large = absolute_boundary(anchor_tick=19, bar_index=1_000_000, meter=meter, ticks_per_quarter=100)
        self.assertEqual(large, 19 + 12_500_000)

    def test_shared_helpers_contain_no_float_division(self):
        for function in (round_half_away_from_zero_ratio, absolute_boundary):
            tree = ast.parse(inspect.getsource(function))
            divisions = [node for node in ast.walk(tree) if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)]
            self.assertEqual(divisions, [])
        self.assertIn("round_half_away_from_zero_ratio", inspect.getsource(absolute_boundary))
        self.assertIn("absolute_boundary", inspect.getsource(build_bar_grid))

    def test_constructor_converts_audio_numbers_to_float(self):
        resolver = ScoreResolver(basic_score(), 8, -1)
        self.assertIs(type(resolver.audio_duration_seconds), float)
        self.assertIs(type(resolver.audio_seconds_at_tick_zero), float)
        self.assertEqual(resolver.audio_duration_seconds, 8.0)
        self.assertEqual(resolver.audio_seconds_at_tick_zero, -1.0)

    def test_constructor_rejects_wrong_root_and_empty_events_cleanly(self):
        with self.assertRaisesRegex(TypeError, "score must be a Score"):
            ScoreResolver(object(), 1.0, 0.0)
        for score in (basic_score(tempos=()), basic_score(meters=())):
            with self.subTest(score=score):
                with self.assertRaises((TypeError, ValueError)) as caught:
                    ScoreResolver(score, 1.0, 0.0)
                self.assertNotIsInstance(caught.exception, IndexError)

    def test_constructor_rejects_malformed_tempo_data(self):
        cases = (
            basic_score(tempos=[TempoEvent(0, 500_000)]),
            basic_score(tempos=(object(),)),
            basic_score(tempos=(TempoEvent(1, 500_000),)),
            basic_score(tempos=(TempoEvent(0, 500_000), TempoEvent(0, 400_000))),
            basic_score(tempos=(TempoEvent(0, 500_000), TempoEvent(10, 400_000), TempoEvent(5, 300_000))),
            basic_score(tempos=(TempoEvent(0, 0),)),
            basic_score(tempos=(TempoEvent(0, 500_000), TempoEvent(10, 500_000))),
            basic_score(tempos=(TempoEvent(True, 500_000),)),
        )
        for score in cases:
            with self.subTest(score=score), self.assertRaises((TypeError, ValueError)):
                ScoreResolver(score, 1.0, 0.0)

    def test_constructor_rejects_malformed_meter_data(self):
        cases = (
            basic_score(meters=[MeterEvent(0, 4, 4)]),
            basic_score(meters=(object(),)),
            basic_score(meters=(MeterEvent(1, 4, 4),)),
            basic_score(meters=(MeterEvent(0, 4, 4), MeterEvent(0, 3, 4)), has_variable_meter=True),
            basic_score(meters=(MeterEvent(0, 4, 4), MeterEvent(10, 3, 4), MeterEvent(5, 2, 4)), has_variable_meter=True),
            basic_score(meters=(MeterEvent(0, 0, 4),)),
            basic_score(meters=(MeterEvent(0, 4, 3),)),
            basic_score(meters=(MeterEvent(0, 4, 128),)),
            basic_score(meters=(MeterEvent(0, 4, 4), MeterEvent(10, 4, 4))),
        )
        for score in cases:
            with self.subTest(score=score), self.assertRaises((TypeError, ValueError)):
                ScoreResolver(score, 1.0, 0.0)

    def test_constructor_rejects_bad_tpq_source_and_flags(self):
        cases = (
            basic_score(ticks_per_quarter=0),
            basic_score(ticks_per_quarter=True),
            basic_score(source="unknown"),
            basic_score(meter_estimated=1),
            basic_score(has_variable_meter=0),
            basic_score(has_midbar_meter_change=None),
        )
        for score in cases:
            with self.subTest(score=score), self.assertRaises((TypeError, ValueError)):
                ScoreResolver(score, 1.0, 0.0)

    def test_constructor_rejects_malformed_markers(self):
        cases = (
            basic_score(markers=[Marker(0, "A")]),
            basic_score(markers=(object(),)),
            basic_score(markers=(Marker(-1, "A"),)),
            basic_score(markers=(Marker(0, 1),)),
            basic_score(markers=(Marker(10, "B"), Marker(0, "A"))),
        )
        for score in cases:
            with self.subTest(score=score), self.assertRaises((TypeError, ValueError)):
                ScoreResolver(score, 1.0, 0.0)

    def test_constructor_accepts_duplicate_markers(self):
        markers = (Marker(0, "A"), Marker(0, "A"))
        resolver = resolver_for(basic_score(markers=markers))
        self.assertEqual(resolver.score.markers, markers)

    def test_constructor_rejects_malformed_sections(self):
        valid = Section("A", 0, 1, False, 0.5)
        cases = (
            basic_score(sections=[valid]),
            basic_score(sections=(object(),)),
            basic_score(sections=(Section(1, 0, 1, False),)),
            basic_score(sections=(Section("A", -1, 1, False),)),
            basic_score(sections=(Section("A", 2, 1, False),)),
            basic_score(sections=(Section("A", 0, 1, 0),)),
            basic_score(sections=(Section("A", 0, 1, False, True),)),
            basic_score(sections=(Section("A", 0, 1, False, math.inf),)),
            basic_score(sections=(Section("B", 2, 3, False), Section("A", 0, 1, False))),
        )
        for score in cases:
            with self.subTest(score=score), self.assertRaises((TypeError, ValueError)):
                ScoreResolver(score, 1.0, 0.0)

    def test_constructor_accepts_duplicate_and_zero_length_sections(self):
        section = Section("  Café 🎵  ", 12, 12, False, 0.75)
        score = basic_score(sections=(section, section))
        resolver = resolver_for(score)
        self.assertIs(resolver.sections(), score.sections)
        self.assertEqual(resolver.sections(), (section, section))

    def test_constructor_rejects_invalid_audio_values(self):
        duration_cases = (True, "1", Decimal("1"), Fraction(1, 1), -1, math.inf, -math.inf, math.nan)
        for value in duration_cases:
            with self.subTest(duration=value), self.assertRaises((TypeError, ValueError)):
                ScoreResolver(basic_score(), value, 0.0)
        alignment_cases = (False, "0", Decimal("0"), Fraction(0, 1), math.inf, -math.inf, math.nan)
        for value in alignment_cases:
            with self.subTest(alignment=value), self.assertRaises((TypeError, ValueError)):
                ScoreResolver(basic_score(), 1.0, value)

    def test_constructor_rejects_inconsistent_meter_flags(self):
        variable = basic_score(
            meters=(MeterEvent(0, 4, 4), MeterEvent(1920, 3, 4)),
            has_variable_meter=False,
        )
        midbar = basic_score(
            meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
            has_variable_meter=True,
            has_midbar_meter_change=False,
        )
        for score in (variable, midbar):
            with self.subTest(score=score), self.assertRaisesRegex(ValueError, "does not match"):
                ScoreResolver(score, 2.0, 0.0)

    def test_constant_tempo_conversions_and_negative_time(self):
        resolver = resolver_for()
        self.assertEqual(resolver.tick_to_seconds(0), 0.0)
        self.assertEqual(resolver.tick_to_seconds(480), 0.5)
        self.assertEqual(resolver.seconds_to_tick(0.5), 480.0)
        self.assertEqual(resolver.tick_to_seconds(-480), -0.5)
        self.assertEqual(resolver.seconds_to_tick(-0.5), -480.0)

    def test_piecewise_tempo_boundaries_and_continuation(self):
        resolver = resolver_for(load_fixture("midbar_tempo.json"))
        self.assertEqual(resolver.tick_to_seconds(960), 1.0)
        self.assertEqual(resolver.seconds_to_tick(1.0), 960.0)
        epsilon_tick = 1e-6
        self.assertLess(resolver.tick_to_seconds(960 - epsilon_tick), 1.0)
        self.assertGreater(resolver.tick_to_seconds(960 + epsilon_tick), 1.0)
        self.assertAlmostEqual(resolver.tick_to_seconds(1440), 1.4, places=12)
        self.assertAlmostEqual(resolver.seconds_to_tick(2.2), 2400.0, places=9)

    def test_tempo_maps_are_monotonic_and_approximately_inverse(self):
        resolver = resolver_for(load_fixture("midbar_tempo.json"))
        ticks = [-960, -1, 0, 959.5, 960, 961, 10_000, 1_000_000]
        seconds = [resolver.tick_to_seconds(tick) for tick in ticks]
        self.assertEqual(seconds, sorted(seconds))
        for tick in ticks:
            self.assertAlmostEqual(resolver.seconds_to_tick(resolver.tick_to_seconds(tick)), tick, places=8)
        values = [-2.0, -0.1, 0.0, 0.999, 1.0, 3.0, 100.0]
        for value in values:
            self.assertAlmostEqual(resolver.tick_to_seconds(resolver.seconds_to_tick(value)), value, places=10)

    def test_large_tempo_queries_remain_finite(self):
        resolver = resolver_for()
        self.assertTrue(math.isfinite(resolver.tick_to_seconds(10**100)))
        self.assertTrue(math.isfinite(resolver.seconds_to_tick(1e100)))

    def test_query_numeric_hardening(self):
        resolver = resolver_for()
        invalid = (True, "1", Decimal("1"), Fraction(1, 1), math.nan, math.inf, -math.inf, 10**10_000)
        methods = (resolver.tick_to_seconds, resolver.seconds_to_tick, resolver.tick_to_audio_seconds, resolver.audio_seconds_to_tick)
        for method in methods:
            for value in invalid:
                with self.subTest(method=method.__name__, value=type(value).__name__), self.assertRaises((TypeError, ValueError)):
                    method(value)

    def test_audio_alignment_equations_and_round_trips(self):
        for alignment in (0.0, 1.25, -0.75):
            resolver = resolver_for(alignment=alignment)
            for tick in (-480, 0, 1234.5):
                audio = resolver.tick_to_audio_seconds(tick)
                self.assertEqual(audio, resolver.tick_to_seconds(tick) + alignment)
                self.assertAlmostEqual(resolver.audio_seconds_to_tick(audio), tick, places=9)
            for audio in (-2.0, 0.0, 3.5):
                self.assertEqual(
                    resolver.audio_seconds_to_tick(audio),
                    resolver.seconds_to_tick(audio - alignment),
                )

    def test_audio_end_before_tick_zero_diagnostic_and_cache_clamp(self):
        resolver = resolver_for(duration=0.25, alignment=1.0)
        self.assertEqual(resolver.audio_seconds_at_tick_zero, 1.0)
        self.assertEqual(
            resolver.diagnostics,
            ("audio ends before score tick zero; bar grid starts at tick 0",),
        )
        self.assertEqual(resolver.bar_grid.boundaries[0], 0)
        self.assertGreater(resolver.bar_grid.boundaries[-1], 0)
        self.assertLess(resolver.audio_seconds_to_tick(0.25), 0)

    def test_bar_grid_is_frozen_and_has_sentinel_invariants(self):
        resolver = resolver_for(duration=3.0)
        through_tick = math.ceil(resolver.seconds_to_tick(3.0))
        self.assertEqual(resolver.bar_grid.boundaries[0], 0)
        self.assertGreater(resolver.bar_grid.boundaries[-1], through_tick)
        self.assertEqual(len(resolver.bar_grid.meters_by_bar), len(resolver.bar_grid.boundaries) - 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            resolver.bar_grid.boundaries = ()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            resolver.audio_duration_seconds = 4.0

    def test_cache_reaches_final_meter_even_when_audio_is_short(self):
        score = load_fixture("midbar_meter.json")
        resolver = ScoreResolver(score, 0.0, 0.0)
        self.assertIn(score.meters[-1].tick, resolver.bar_grid.boundaries)
        self.assertGreater(resolver.bar_grid.boundaries[-1], score.meters[-1].tick)

    def test_cached_and_first_extrapolated_bar_boundaries_agree(self):
        resolver = resolver_for(duration=1.1)
        count = len(resolver.bar_grid.meters_by_bar)
        self.assertEqual(resolver.bar_to_tick(count), resolver.bar_grid.boundaries[count - 1])
        self.assertEqual(resolver.bar_to_tick(count + 1), resolver.bar_grid.boundaries[-1])

    def test_far_bar_queries_are_absolute_and_do_not_grow_cache(self):
        score = basic_score(ticks_per_quarter=100, meters=(MeterEvent(0, 1, 32),))
        resolver = resolver_for(score, duration=0.0)
        grid = resolver.bar_grid
        diagnostics = resolver.diagnostics
        self.assertEqual(resolver.bar_to_tick(1_000_000), absolute_boundary(anchor_tick=0, bar_index=999_999, meter=score.meters[-1], ticks_per_quarter=100))
        self.assertIs(resolver.bar_grid, grid)
        self.assertEqual(resolver.diagnostics, diagnostics)
        self.assertEqual(resolver.bar_grid, grid)
        self.assertNotIn("for ", inspect.getsource(ScoreResolver.bar_to_tick))

    def test_extrapolation_uses_final_meter_event_anchor(self):
        score = basic_score(
            meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
            has_variable_meter=True,
            has_midbar_meter_change=True,
        )
        resolver = resolver_for(score, duration=1.1)
        anchor_bar = resolver.bar_grid.boundaries.index(1000) + 1
        target_bar = anchor_bar + 10_000
        self.assertEqual(
            resolver.bar_to_tick(target_bar),
            absolute_boundary(anchor_tick=1000, bar_index=10_000, meter=score.meters[-1], ticks_per_quarter=480),
        )

    def test_meter_and_bar_length_semantics(self):
        resolver = resolver_for(load_fixture("midbar_meter.json"), duration=1.1)
        self.assertEqual(resolver.meter_at_bar(1), (4, 4))
        self.assertEqual(resolver.bar_length_ticks(1), 1000)
        self.assertEqual(resolver.meter_at_bar(2), (3, 4))
        self.assertEqual(resolver.bar_length_ticks(2), 1440)
        self.assertEqual(resolver.meter_at_bar(1_000_000), (3, 4))

    def test_fractional_bar_lengths_vary_without_cumulative_drift(self):
        score = basic_score(ticks_per_quarter=100, meters=(MeterEvent(0, 1, 32),))
        resolver = resolver_for(score, duration=0.0)
        self.assertEqual([resolver.bar_length_ticks(bar) for bar in range(1, 5)], [13, 12, 13, 12])
        self.assertEqual(resolver.bar_to_tick(1_000_001), 12_500_000)

    def test_bar_diagnostics_are_preserved_and_extrapolation_is_silent(self):
        resolver = resolver_for(load_fixture("midbar_meter.json"), duration=0.0)
        expected = ("meter change at tick 1000 shortened a bar",)
        self.assertEqual(resolver.diagnostics, expected)
        resolver.bar_to_tick(1_000_000)
        resolver.tick_to_position(10_000_000, 4)
        self.assertEqual(resolver.diagnostics, expected)

    def test_bar_queries_reject_noncanonical_indices(self):
        resolver = resolver_for()
        for method in (resolver.bar_to_tick, resolver.bar_length_ticks, resolver.meter_at_bar):
            for value in (0, -1, True, 1.0):
                with self.subTest(method=method.__name__, value=value), self.assertRaises((TypeError, ValueError)):
                    method(value)

    def test_position_input_validation(self):
        resolver = resolver_for()
        cases = (
            (0, 1, 0, 1),
            (1, 0, 0, 1),
            (1, 1, -1, 1),
            (1, 1, 0, 0),
            (True, 1, 0, 1),
            (1, True, 0, 1),
            (1, 1, False, 1),
            (1, 1, 0, True),
            (1.0, 1, 0, 1),
            (1, 5, 0, 1),
            (1, 1, 4, 4),
            (1, 1, 0, 481),
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises((TypeError, ValueError)):
                resolver.position_to_tick(*case)

    def test_normal_position_mapping_for_common_denominators(self):
        four = resolver_for()
        self.assertEqual(four.position_to_tick(1, 1, 0, 4), 0)
        self.assertEqual(four.position_to_tick(1, 2, 2, 4), 720)
        eight = resolver_for(basic_score(meters=(MeterEvent(0, 6, 8),)))
        self.assertEqual(eight.position_to_tick(1, 2, 0, 1), 240)
        two = resolver_for(basic_score(meters=(MeterEvent(0, 2, 2),)))
        self.assertEqual(two.position_to_tick(1, 2, 0, 1), 960)

    def test_odd_meter_absolute_position_rounding(self):
        odd = resolver_for(load_fixture("odd_meter_31_32.json"))
        self.assertEqual(odd.position_to_tick(1, 2, 0, 2), 60)
        self.assertEqual(odd.position_to_tick(1, 1, 1, 2), 30)
        self.assertEqual(odd.position_to_tick(1, 31, 1, 2), 1830)
        thirds = resolver_for(basic_score(ticks_per_quarter=100, meters=(MeterEvent(0, 1, 8),)))
        self.assertEqual([thirds.position_to_tick(1, 1, sub, 3) for sub in range(3)], [0, 17, 33])

    def test_positions_work_in_cached_and_extrapolated_bars(self):
        resolver = resolver_for(duration=0.0)
        self.assertEqual(resolver.position_to_tick(1, 1, 0, 4), 0)
        bar = 1_000_000
        self.assertEqual(resolver.position_to_tick(bar, 3, 0, 4), resolver.bar_to_tick(bar) + 960)

    def test_shortened_bar_rejects_nominal_tail_positions(self):
        resolver = resolver_for(load_fixture("midbar_meter.json"), duration=0.0)
        self.assertEqual(resolver.meter_at_bar(1), (4, 4))
        self.assertEqual(resolver.position_to_tick(1, 3, 0, 1), 960)
        with self.assertRaisesRegex(ValueError, "actual bar boundaries"):
            resolver.position_to_tick(1, 4, 0, 1)
        self.assertEqual(resolver.position_to_tick(2, 1, 0, 1), 1000)
        self.assertEqual(resolver.tick_to_position(1000, 1), (2, 1, 0))

    def test_tick_quantization_nearest_and_later_tie(self):
        resolver = resolver_for()
        self.assertEqual(resolver.tick_to_position(0, 1), (1, 1, 0))
        self.assertEqual(resolver.tick_to_position(240, 1), (1, 2, 0))
        self.assertEqual(resolver.tick_to_position(239.5, 1), (1, 1, 0))
        self.assertEqual(resolver.tick_to_position(1910, 1), (2, 1, 0))
        self.assertEqual(resolver.tick_to_position(1920, 1), (2, 1, 0))

    def test_tick_position_rejects_invalid_queries_and_overfine_grid(self):
        resolver = resolver_for()
        for tick in (-1, True, math.nan, math.inf, "1"):
            with self.subTest(tick=tick), self.assertRaises((TypeError, ValueError)):
                resolver.tick_to_position(tick, 4)
        for subdivisions in (0, True, 1.0, 481):
            with self.subTest(subdivisions=subdivisions), self.assertRaises((TypeError, ValueError)):
                resolver.tick_to_position(0, subdivisions)

    def test_tick_position_round_trips_canonical_grid(self):
        resolver = resolver_for(load_fixture("changing_meter.json"), duration=0.0)
        for bar in (1, 2, 3, 1000):
            numerator, _ = resolver.meter_at_bar(bar)
            for beat in range(1, numerator + 1):
                for subdivision in range(4):
                    position = (bar, beat, subdivision)
                    tick = resolver.position_to_tick(*position, 4)
                    self.assertEqual(resolver.tick_to_position(tick, 4), position)

    def test_far_tick_lookup_is_analytical_and_cache_stays_fixed(self):
        resolver = resolver_for(duration=0.0)
        before = resolver.bar_grid
        bar = 1_000_000
        tick = resolver.bar_to_tick(bar) + 960
        self.assertEqual(resolver.tick_to_position(tick, 1), (bar, 3, 0))
        self.assertIs(resolver.bar_grid, before)
        source = inspect.getsource(ScoreResolver._bar_containing_tick)
        self.assertNotIn("while ", source)
        self.assertNotIn("for ", source)

    def test_generated_position_and_order_invariants(self):
        randomizer = random.Random(20260802)
        scores = (
            basic_score(),
            load_fixture("changing_meter.json"),
            load_fixture("odd_meter_31_32.json"),
        )
        for score in scores:
            resolver = resolver_for(score, duration=1.0)
            for _ in range(100):
                bar = randomizer.randint(1, 20_000)
                numerator, denominator = resolver.meter_at_bar(bar)
                maximum_subdivisions = (4 * score.ticks_per_quarter) // denominator
                subdivisions = randomizer.randint(1, min(16, maximum_subdivisions))
                beat = randomizer.randint(1, numerator)
                subdivision = randomizer.randrange(subdivisions)
                position = (bar, beat, subdivision)
                tick = resolver.position_to_tick(*position, subdivisions)
                self.assertEqual(resolver.tick_to_position(tick, subdivisions), position)
                if subdivision + 1 < subdivisions:
                    following = (bar, beat, subdivision + 1)
                    self.assertLess(tick, resolver.position_to_tick(*following, subdivisions))

    def test_generated_tempo_invariants(self):
        randomizer = random.Random(8675309)
        score = basic_score(
            tempos=(TempoEvent(0, 500_000), TempoEvent(700, 300_000), TempoEvent(3000, 800_000)),
        )
        resolver = resolver_for(score)
        ticks = sorted(randomizer.uniform(-10_000, 1_000_000) for _ in range(500))
        seconds = [resolver.tick_to_seconds(tick) for tick in ticks]
        self.assertTrue(all(left < right for left, right in zip(seconds, seconds[1:])))
        for tick, second in zip(ticks, seconds):
            self.assertAlmostEqual(resolver.seconds_to_tick(second), tick, places=7)

    def test_generated_analytical_boundaries_match_helper(self):
        randomizer = random.Random(314159)
        score = basic_score(ticks_per_quarter=100, meters=(MeterEvent(0, 7, 32),))
        resolver = resolver_for(score, duration=0.0)
        for _ in range(100):
            bar = randomizer.randint(100_000, 10_000_000)
            self.assertEqual(
                resolver.bar_to_tick(bar),
                absolute_boundary(anchor_tick=0, bar_index=bar - 1, meter=score.meters[-1], ticks_per_quarter=100),
            )

    def test_far_queries_preserve_all_source_values(self):
        section = Section(" Διάστημα 🎵 ", 0, 0, False, 0.625)
        score = basic_score(markers=(Marker(999_999, " End "),), sections=(section, section))
        resolver = resolver_for(score, duration=0.0)
        original = dataclasses.asdict(score)
        grid = resolver.bar_grid
        diagnostics = resolver.diagnostics
        resolver.bar_to_tick(10_000_000)
        resolver.tick_to_position(10_000_000_000, 8)
        resolver.tick_to_seconds(10_000_000_000)
        self.assertEqual(dataclasses.asdict(score), original)
        self.assertIs(resolver.bar_grid, grid)
        self.assertIs(resolver.score.tempos, score.tempos)
        self.assertIs(resolver.score.meters, score.meters)
        self.assertIs(resolver.sections(), score.sections)
        self.assertEqual(resolver.diagnostics, diagnostics)

    def test_sections_are_exact_passthrough_for_all_fixtures(self):
        for name, case in RESOLVER_FIXTURE_CASES.items():
            score = load_fixture(name)
            resolver = ScoreResolver(score, case.audio_duration_seconds, case.audio_seconds_at_tick_zero)
            self.assertIs(resolver.sections(), score.sections)
            self.assertEqual(resolver.sections(), score.sections)

    def test_public_query_surface_is_exact(self):
        expected = {
            "tick_to_seconds",
            "seconds_to_tick",
            "tick_to_audio_seconds",
            "audio_seconds_to_tick",
            "bar_to_tick",
            "bar_length_ticks",
            "meter_at_bar",
            "position_to_tick",
            "tick_to_position",
            "sections",
        }
        public_methods = {
            name
            for name, value in ScoreResolver.__dict__.items()
            if callable(value) and not name.startswith("_")
        }
        self.assertEqual(public_methods, expected)


if __name__ == "__main__":
    unittest.main()
