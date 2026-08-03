"""Complete Phase 5c node-planning tests."""

import json
import unittest
from unittest.mock import Mock, patch

from audio_clip_plan import apply_sample_range
from score.model import MeterEvent, Section, TempoEvent
try:
    from tests.test_score_node_integration import (
        _load_node_module,
        _plan,
        _resolution,
        _run,
        _score,
    )
except ModuleNotFoundError:
    from test_score_node_integration import (
        _load_node_module,
        _plan,
        _resolution,
        _run,
        _score,
    )


class PlanningBoundaryTests(unittest.TestCase):
    def test_constant_provider_uses_compatibility_wrapper_once(self):
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        result = _run(module, resolution=_resolution()).result
        planner.assert_called_once()
        self.assertEqual(len(result), 20)

    def test_resolved_score_skips_wrapper_and_uses_one_sample_pass(self):
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        with patch.object(
            module,
            "apply_sample_range",
            wraps=apply_sample_range,
        ) as sample_layer:
            result = _run(
                module,
                resolution=_resolution(_score(), provider="explicit"),
            ).result
        planner.assert_not_called()
        sample_layer.assert_called_once()
        self.assertEqual(result[17:19], ("json", "explicit"))

    def test_fatal_selection_stops_before_sample_pass_and_slice(self):
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        with patch.object(module, "apply_sample_range") as sample_layer:
            with self.assertRaises(ValueError) as caught:
                _run(
                    module,
                    resolution=_resolution(_score(numerator=3), provider="explicit"),
                    edit_mode="Musical",
                    start_beat=4,
                )
        planner.assert_not_called()
        sample_layer.assert_not_called()
        self.assertEqual(
            json.loads(str(caught.exception)),
            [
                {
                    "code": "score_selection_range_invalid",
                    "severity": "error",
                    "message": "start position is not canonical for the resolved Score",
                }
            ],
        )

    def test_duration_crossing_meter_requires_exact_end(self):
        value = _score(
            meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
            variable_meter=True,
            midbar_meter=True,
        )
        module = _load_node_module(Mock(return_value=_plan()))
        with patch.object(module, "apply_sample_range") as sample_layer:
            with self.assertRaises(ValueError) as caught:
                _run(
                    module,
                    resolution=_resolution(value, provider="explicit"),
                    edit_mode="Musical",
                    duration_bars=1,
                )
        sample_layer.assert_not_called()
        self.assertEqual(
            json.loads(str(caught.exception))[0]["code"],
            "score_end_position_required",
        )

    def test_exact_end_crossing_meter_succeeds(self):
        value = _score(
            meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
            variable_meter=True,
            midbar_meter=True,
        )
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        result = _run(
            module,
            resolution=_resolution(value, provider="explicit"),
            edit_mode="Musical",
            score_end_bar=3,
            score_end_beat=1,
            score_end_subdivision=0,
        ).result
        planner.assert_not_called()
        self.assertIn("End (exclusive): Bar 3 · Beat 1 · Subdivision 0", result[11])
        self.assertNotIn("Length:", result[11])

    def test_exact_empty_request_expands_to_one_sample(self):
        module = _load_node_module(Mock(return_value=_plan()))
        result = _run(
            module,
            resolution=_resolution(_score(), provider="explicit"),
            edit_mode="Musical",
            score_end_bar=1,
            score_end_beat=1,
            score_end_subdivision=0,
        ).result
        self.assertEqual(result[0]["waveform"].shape[-1], 1)
        self.assertIn("clamped to audio", result[11])

    def test_seconds_ignores_exact_end_for_trim_authority(self):
        module = _load_node_module(Mock(return_value=_plan()))
        result = _run(
            module,
            resolution=_resolution(_score(), provider="explicit"),
            edit_mode="Seconds",
            start_time=0.25,
            end_time=0.75,
            score_end_bar=999,
            score_end_beat=0,
            score_end_subdivision=999,
        ).result
        self.assertEqual((result[3], result[4]), (0.25, 0.75))


class AuthorityAndMetadataTests(unittest.TestCase):
    def test_variable_tempo_meter_midbar_and_odd_shapes_are_authoritative(self):
        scores = (
            _score(tempos=(TempoEvent(0, 500_000), TempoEvent(480, 250_000))),
            _score(
                meters=(MeterEvent(0, 4, 4), MeterEvent(1920, 3, 4)),
                variable_meter=True,
            ),
            _score(
                meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
                variable_meter=True,
                midbar_meter=True,
            ),
            _score(numerator=31, denominator=32),
        )
        for value in scores:
            with self.subTest(value=value):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)
                result = _run(
                    module,
                    resolution=_resolution(value, provider="explicit"),
                    edit_mode="Seconds",
                ).result
                planner.assert_not_called()
                self.assertNotIn(
                    "score_activation_refused",
                    [item["code"] for item in json.loads(result[19])],
                )

    def test_one_shared_anchor_controls_section_and_local_metrics(self):
        value = _score(
            tempos=(TempoEvent(0, 500_000), TempoEvent(240, 250_000)),
            sections=(
                Section("Before", 0, 480, True),
                Section("After", 480, 1920, False),
            ),
        )
        module = _load_node_module(Mock(return_value=_plan()))
        result = _run(
            module,
            resolution=_resolution(value, provider="explicit"),
            start_time=0.375,
            end_time=1.0,
        ).result
        self.assertEqual(result[15], "After")
        self.assertAlmostEqual(result[7], 0.25)
        self.assertAlmostEqual(result[9], 1.125)
        self.assertAlmostEqual(result[8], result[7] * 24.0)
        self.assertAlmostEqual(result[10], result[9] * 24.0)

    def test_shortened_bar_and_clipped_final_beat_metrics(self):
        value = _score(
            meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
            variable_meter=True,
            midbar_meter=True,
        )
        module = _load_node_module(Mock(return_value=_plan()))
        result = _run(
            module,
            resolution=_resolution(value, provider="explicit"),
            start_time=1.02,
            end_time=1.1,
        ).result
        self.assertAlmostEqual(result[7], 40 / 960)
        self.assertAlmostEqual(result[9], 1000 / 960)

    def test_negative_preroll_uses_initial_local_intervals_and_protected_text(self):
        module = _load_node_module(Mock(return_value=_plan()))
        result = _run(
            module,
            resolution=_resolution(_score(), provider="explicit"),
            start_time=0.5,
            end_time=0.75,
            downbeat_offset=1.0,
        ).result
        self.assertEqual(result[15], "")
        self.assertIn("Nearest: 4 subdivisions before Bar 1 · Beat 1", result[11])
        self.assertEqual(result[7], 0.5)
        self.assertEqual(result[9], 2.0)

    def test_diagnostics_keep_provider_resolver_alignment_order(self):
        value = _score(
            meters=(MeterEvent(0, 4, 4), MeterEvent(1000, 3, 4)),
            variable_meter=True,
            midbar_meter=True,
        )
        module = _load_node_module(Mock(return_value=_plan()))
        result = _run(
            module,
            resolution=_resolution(
                value,
                provider="explicit",
                provider_alignment=1.0,
                diagnostics=("provider note",),
            ),
            downbeat_offset=0.0,
        ).result
        self.assertEqual(
            [item["code"] for item in json.loads(result[19])],
            [
                "score_resolver_diagnostic",
                "score_resolver_diagnostic",
                "score_alignment_divergence",
            ],
        )
        self.assertEqual(len(result), 20)


if __name__ == "__main__":
    unittest.main()
