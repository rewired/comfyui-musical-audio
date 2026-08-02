"""Phase 3b tests for AudioClipPlan timing-bridge routing."""

import ast
import inspect
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from audio_clip_plan import (
    AudioClipPlan,
    _format_nearest_position,
    create_audio_clip_plan,
)
from musical_timing import ConstantTempoMap, NearestPosition
from score.model import MeterEvent, Score, TempoEvent
from score.resolver import ScoreResolver
from score.tempo_map import ScoreTempoMap


ROOT = Path(__file__).resolve().parents[1]

BASE_INPUTS = {
    "edit_mode": "Seconds",
    "sample_rate": 1_000,
    "sample_count": 20_000,
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


def score_map(
    score: Score | None = None,
    *,
    alignment: float = 0.0,
    duration: float = 60.0,
) -> ScoreTempoMap:
    return ScoreTempoMap(ScoreResolver(score or score_with(), duration, alignment))


class RecordingBridge:
    """Structural bridge that records which adapter operations were used."""

    def __init__(self, inner: ConstantTempoMap) -> None:
        self.inner = inner
        self.span_calls = 0
        self.nearest_calls: list[tuple[float, int]] = []

    @property
    def supports_uniform_timing(self) -> bool:
        return self.inner.supports_uniform_timing

    @property
    def binding(self):
        return self.inner.binding

    def uniform_timing_metrics(self):
        return self.inner.uniform_timing_metrics()

    def resolve_score_relative_span(self, **arguments: int):
        self.span_calls += 1
        return self.inner.resolve_score_relative_span(**arguments)

    def describe_nearest_position(
        self,
        score_seconds: float,
        subdivisions_per_beat: int,
    ) -> NearestPosition:
        self.nearest_calls.append((score_seconds, subdivisions_per_beat))
        return self.inner.describe_nearest_position(
            score_seconds,
            subdivisions_per_beat,
        )


class ActiveBridgeAndLegacyParityTests(unittest.TestCase):
    def test_optional_tempo_map_is_the_final_keyword_parameter(self) -> None:
        signature = inspect.signature(create_audio_clip_plan)
        parameters = list(signature.parameters.values())
        self.assertEqual(parameters[-1].name, "tempo_map")
        self.assertIsNone(parameters[-1].default)
        self.assertTrue(
            all(
                parameter.kind is parameter.KEYWORD_ONLY
                for parameter in parameters
            )
        )

    def test_one_supplied_bridge_drives_timing_and_seconds_nearest(self) -> None:
        bridge = RecordingBridge(ConstantTempoMap(120.0, "Quarter", 4, 4))
        result = plan(start_time=2.0, end_time=2.5, tempo_map=bridge)
        self.assertEqual(bridge.span_calls, 1)
        self.assertEqual(bridge.nearest_calls, [(result.start_seconds, 4)])

    def test_musical_mode_does_not_describe_nearest_position(self) -> None:
        bridge = RecordingBridge(ConstantTempoMap(120.0, "Quarter", 4, 4))
        plan(
            edit_mode="Musical",
            duration_beats=1,
            tempo_map=bridge,
        )
        self.assertEqual(bridge.span_calls, 1)
        self.assertEqual(bridge.nearest_calls, [])

    def test_omitted_map_constructs_only_one_constant_authority(self) -> None:
        with (
            patch(
                "audio_clip_plan.ConstantTempoMap",
                wraps=ConstantTempoMap,
            ) as clip_constructor,
            patch(
                "musical_timing.ConstantTempoMap",
                side_effect=AssertionError("timing created a second map"),
            ),
        ):
            plan()
        clip_constructor.assert_called_once_with(
            bpm=120.0,
            tempo_unit="Quarter",
            beats_per_bar=4,
            beat_unit=4,
        )

    def test_omitted_and_explicit_constant_maps_match_in_seconds_mode(self) -> None:
        omitted = plan(start_time=2.75, end_time=3.125)
        explicit = plan(
            start_time=2.75,
            end_time=3.125,
            tempo_map=ConstantTempoMap(120.0, "Quarter", 4, 4),
        )
        self.assertEqual(explicit, omitted)

    def test_omitted_and_explicit_constant_maps_match_in_musical_mode(self) -> None:
        arguments = {
            "edit_mode": "Musical",
            "downbeat_offset": 0.125,
            "start_bar": 2,
            "start_beat": 3,
            "start_subdivision": 1,
            "duration_bars": 1,
            "duration_beats": 2,
            "duration_subdivisions": 3,
        }
        omitted = plan(**arguments)
        explicit = plan(
            **arguments,
            tempo_map=ConstantTempoMap(120.0, "Quarter", 4, 4),
        )
        self.assertEqual(explicit, omitted)

    def test_conflicting_constant_map_is_rejected_by_timing_core(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicts"):
            plan(tempo_map=ConstantTempoMap(121.0, "Quarter", 4, 4))


class NearestFormattingTests(unittest.TestCase):
    def test_canonical_variant_has_exact_format(self) -> None:
        self.assertEqual(
            _format_nearest_position(NearestPosition(2, 3, 4, 0)),
            "Bar 2 · Beat 3 · Subdivision 4",
        )

    def test_pre_bar_one_variant_has_exact_plural_format(self) -> None:
        self.assertEqual(
            _format_nearest_position(NearestPosition(None, None, None, 1)),
            "1 subdivisions before Bar 1 · Beat 1",
        )

    def test_formatter_rejects_a_corrupted_variant(self) -> None:
        nearest = NearestPosition(1, 1, 0, 0)
        object.__setattr__(nearest, "bar", None)
        with self.assertRaisesRegex(ValueError, "invalid canonical"):
            _format_nearest_position(nearest)

    def test_protected_canonical_output_is_byte_for_byte_unchanged(self) -> None:
        result = plan(start_time=2.75, end_time=3.0)
        self.assertIn(
            "Nearest: Bar 2 · Beat 2 · Subdivision 2",
            result.musical_position,
        )
        self.assertNotIn("Bar 0", result.musical_position)
        self.assertNotIn("None", result.musical_position)

    def test_protected_pre_bar_one_output_is_byte_for_byte_unchanged(self) -> None:
        result = plan(start_time=0.5, end_time=0.75, downbeat_offset=1.0)
        self.assertIn(
            "Nearest: 4 subdivisions before Bar 1 · Beat 1",
            result.musical_position,
        )
        self.assertNotIn("Subdivision None", result.musical_position)


class ScoreMusicalModeTests(unittest.TestCase):
    def test_uniform_three_four_score_drives_requested_times_and_metrics(self) -> None:
        timing_map = score_map(
            score_with(meters=(MeterEvent(0, 3, 4),)),
            alignment=0.25,
        )
        result = plan(
            edit_mode="Musical",
            downbeat_offset=0.25,
            beats_per_bar=4,
            start_bar=2,
            duration_bars=1,
            tempo_map=timing_map,
        )
        self.assertEqual(result.requested_start_seconds, 1.75)
        self.assertEqual(result.requested_end_seconds, 3.25)
        self.assertEqual(result.seconds_per_beat, 0.5)
        self.assertEqual(result.frames_per_beat, 12.0)
        self.assertEqual(result.seconds_per_bar, 1.5)
        self.assertEqual(result.frames_per_bar, 36.0)
        self.assertIn(
            "Bar 2 · Beat 1 · Subdivision 0 | Length: 1 Bar",
            result.musical_position,
        )

    def test_score_four_four_accepts_beat_four_with_widget_three_four(self) -> None:
        result = plan(
            edit_mode="Musical",
            beats_per_bar=3,
            start_beat=4,
            duration_beats=1,
            tempo_map=score_map(),
        )
        self.assertEqual(result.requested_start_seconds, 1.5)
        self.assertIn("Bar 1 · Beat 4 · Subdivision 0", result.musical_position)

    def test_score_three_four_rejects_beat_four_with_widget_four_four(self) -> None:
        timing_map = score_map(score_with(meters=(MeterEvent(0, 3, 4),)))
        with self.assertRaisesRegex(ValueError, "beat exceeds"):
            plan(
                edit_mode="Musical",
                beats_per_bar=4,
                start_beat=4,
                tempo_map=timing_map,
            )

    def test_meter_independent_legacy_domains_remain_required_for_score(self) -> None:
        for overrides in ({"bpm": 0}, {"tempo_unit": "Half"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                plan(tempo_map=score_map(), **overrides)

    def test_requested_times_shift_with_alignment_without_changing_duration(self) -> None:
        score = score_with(meters=(MeterEvent(0, 3, 4),))
        first = plan(
            edit_mode="Musical",
            downbeat_offset=0.1,
            start_bar=2,
            duration_bars=1,
            tempo_map=score_map(score, alignment=0.1),
        )
        second = plan(
            edit_mode="Musical",
            downbeat_offset=0.4,
            start_bar=2,
            duration_bars=1,
            tempo_map=score_map(score, alignment=0.4),
        )
        self.assertAlmostEqual(
            second.requested_start_seconds - first.requested_start_seconds,
            0.3,
        )
        self.assertAlmostEqual(
            second.requested_end_seconds - first.requested_end_seconds,
            0.3,
        )
        self.assertAlmostEqual(
            second.requested_end_seconds - second.requested_start_seconds,
            first.requested_end_seconds - first.requested_start_seconds,
        )
        self.assertEqual(second.seconds_per_beat, first.seconds_per_beat)
        self.assertEqual(second.seconds_per_bar, first.seconds_per_bar)
        self.assertEqual(second.frame_count, first.frame_count)
        self.assertEqual(
            second.musical_position.split(" | Time:")[0],
            first.musical_position.split(" | Time:")[0],
        )

    def test_negative_score_alignment_is_supported(self) -> None:
        result = plan(
            edit_mode="Musical",
            downbeat_offset=-0.25,
            start_bar=2,
            duration_beats=1,
            tempo_map=score_map(alignment=-0.25),
        )
        self.assertEqual(result.requested_start_seconds, 1.75)


class ScoreSecondsModeTests(unittest.TestCase):
    def test_score_meter_overrides_widget_meter_for_nearest_position(self) -> None:
        timing_map = score_map(score_with(meters=(MeterEvent(0, 3, 4),)))
        result = plan(
            start_time=2.0,
            end_time=2.5,
            beats_per_bar=4,
            tempo_map=timing_map,
        )
        self.assertIn(
            "Seconds mode | Nearest: Bar 2 · Beat 2 · Subdivision 0 |",
            result.musical_position,
        )

    def test_offset_changes_interpretation_without_changing_samples(self) -> None:
        score = score_with(meters=(MeterEvent(0, 3, 4),))
        zero = plan(
            start_time=2.0,
            end_time=2.5,
            tempo_map=score_map(score),
        )
        shifted = plan(
            start_time=2.0,
            end_time=2.5,
            downbeat_offset=0.5,
            tempo_map=score_map(score, alignment=0.5),
        )
        self.assertEqual(
            (shifted.start_sample, shifted.end_sample),
            (zero.start_sample, zero.end_sample),
        )
        self.assertIn(
            "Nearest: Bar 2 · Beat 2 · Subdivision 0",
            zero.musical_position,
        )
        self.assertIn(
            "Nearest: Bar 2 · Beat 1 · Subdivision 0",
            shifted.musical_position,
        )

    def test_request_before_file_uses_clamped_returned_start(self) -> None:
        result = plan(
            start_time=-2.0,
            end_time=0.5,
            downbeat_offset=0.5,
            tempo_map=score_map(alignment=0.5),
        )
        self.assertEqual(result.requested_start_seconds, -2.0)
        self.assertEqual(result.start_sample, 0)
        self.assertEqual(result.start_seconds, 0.0)
        self.assertIn(
            "Nearest: 4 subdivisions before Bar 1 · Beat 1",
            result.musical_position,
        )

    def test_request_after_file_uses_selected_final_sample(self) -> None:
        timing_map = score_map(score_with(meters=(MeterEvent(0, 3, 4),)))
        result = plan(
            sample_rate=100,
            sample_count=100,
            start_time=2.0,
            end_time=3.0,
            tempo_map=timing_map,
        )
        self.assertEqual((result.start_sample, result.end_sample), (99, 100))
        self.assertEqual(result.start_seconds, 0.99)
        self.assertIn(
            "Nearest: Bar 1 · Beat 3 · Subdivision 0",
            result.musical_position,
        )
        self.assertNotIn("Nearest: Bar 2 · Beat 2", result.musical_position)

    def test_alignment_mismatch_is_rejected_in_both_modes(self) -> None:
        for edit_mode in ("Seconds", "Musical"):
            with self.subTest(edit_mode=edit_mode), self.assertRaisesRegex(
                ValueError,
                "stale or inconsistent",
            ):
                plan(
                    edit_mode=edit_mode,
                    downbeat_offset=0.0,
                    tempo_map=score_map(alignment=0.5),
                )

    def test_negative_nearest_boundaries_match_both_adapters(self) -> None:
        width = 0.25
        cases = (
            (
                1.0 - 0.49 * width,
                "Nearest: Bar 1 · Beat 1 · Subdivision 0",
            ),
            (
                1.0 - 0.5 * width,
                "Nearest: 1 subdivisions before Bar 1 · Beat 1",
            ),
            (
                1.0 - 4.0 * width,
                "Nearest: 4 subdivisions before Bar 1 · Beat 1",
            ),
        )
        adapters = (
            ConstantTempoMap(60.0, "Quarter", 4, 4),
            score_map(
                score_with(tempos=(TempoEvent(0, 1_000_000),)),
                alignment=1.0,
            ),
        )
        for timing_map in adapters:
            for start_time, expected in cases:
                with self.subTest(
                    timing_map=type(timing_map).__name__,
                    start_time=start_time,
                ):
                    result = plan(
                        sample_rate=800,
                        sample_count=3_200,
                        start_time=start_time,
                        end_time=start_time + 0.05,
                        bpm=60.0,
                        downbeat_offset=1.0,
                        tempo_map=timing_map,
                    )
                    self.assertIn(expected, result.musical_position)

    def test_negative_score_time_never_reaches_tick_to_position(self) -> None:
        timing_map = score_map(alignment=1.0)
        with patch.object(
            ScoreResolver,
            "tick_to_position",
            side_effect=AssertionError("negative time reached tick_to_position"),
        ):
            result = plan(
                start_time=0.5,
                end_time=0.75,
                downbeat_offset=1.0,
                tempo_map=timing_map,
            )
        self.assertIn("subdivisions before Bar 1", result.musical_position)


class UniformGateTests(unittest.TestCase):
    def test_nonuniform_scores_are_rejected_in_both_edit_modes(self) -> None:
        scores = (
            score_with(
                tempos=(TempoEvent(0, 500_000), TempoEvent(960, 400_000)),
            ),
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
            for edit_mode in ("Seconds", "Musical"):
                with (
                    self.subTest(score=score, edit_mode=edit_mode),
                    self.assertRaisesRegex(ValueError, "uniform timing"),
                ):
                    plan(edit_mode=edit_mode, tempo_map=score_map(score))


class ArchitectureAndImportTests(unittest.TestCase):
    def test_audio_clip_plan_has_only_the_allowed_dependency_boundary(self) -> None:
        tree = ast.parse((ROOT / "audio_clip_plan.py").read_text(encoding="utf-8"))
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertEqual(
            imports,
            {"__future__", "dataclasses", "math", "typing", "musical_timing"},
        )
        source = (ROOT / "audio_clip_plan.py").read_text(encoding="utf-8")
        self.assertNotIn("tempo_map_contract", source)
        self.assertNotIn("ScoreResolver", source)
        self.assertNotIn("ScoreTempoMap", source)
        self.assertNotIn("isinstance", source)
        self.assertNotIn("tick_to_audio_seconds", source)
        self.assertNotIn("audio_seconds_to_tick", source)

    def test_duplicate_nearest_timing_mathematics_was_removed(self) -> None:
        source = (ROOT / "audio_clip_plan.py").read_text(encoding="utf-8")
        self.assertNotIn("_nearest_grid_position", source)
        self.assertNotIn("relative_subdivisions", source)
        self.assertNotIn("subdivisions_per_bar", source)
        self.assertNotIn("divmod", source)
        self.assertEqual(source.count("describe_nearest_position("), 1)
        self.assertEqual(source.count("active_map ="), 1)
        self.assertIn("tempo_map=active_map", source)

    def test_flat_import_is_silent_and_side_effect_free(self) -> None:
        self._assert_silent_import(
            """
import os, sys
before = dict(os.environ)
import audio_clip_plan
assert os.environ == before
assert 'score' not in sys.modules
forbidden = [
    name for name in sys.modules
    if name.startswith(('av', 'torch', 'comfy', 'server', 'waveform_routes'))
]
assert forbidden == [], forbidden
"""
        )

    def test_package_context_import_is_silent_and_side_effect_free(self) -> None:
        self._assert_silent_import(
            """
import importlib, os, pathlib, sys, types
before = dict(os.environ)
package = types.ModuleType('phase3b_package')
package.__path__ = [str(pathlib.Path.cwd())]
sys.modules['phase3b_package'] = package
importlib.import_module('phase3b_package.audio_clip_plan')
assert os.environ == before
assert 'score' not in sys.modules
"""
        )

    def test_arbitrary_parent_import_is_silent_and_does_not_leak_score(self) -> None:
        self._assert_silent_import(
            """
import importlib, os, pathlib, sys, types
before = dict(os.environ)
outer = types.ModuleType('phase3b_outer')
outer.__path__ = []
inner = types.ModuleType('phase3b_outer.child')
inner.__path__ = [str(pathlib.Path.cwd())]
sys.modules['phase3b_outer'] = outer
sys.modules['phase3b_outer.child'] = inner
importlib.import_module('phase3b_outer.child.audio_clip_plan')
assert os.environ == before
assert 'score' not in sys.modules
"""
        )

    def _assert_silent_import(self, code: str) -> None:
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
