"""Execution-contract tests for local and externally supplied timing values."""

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from audio_clip_plan import create_audio_clip_plan as real_create_audio_clip_plan


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_SOURCE = REPO_ROOT / "musical_audio_ui.py"
LOCAL_TIMING_VALUES = {
    "bpm": 123.25,
    "tempo_unit": "Quarter",
    "fps": 23.976,
    "beats_per_bar": 5,
    "beat_unit": 8,
    "subdivisions_per_beat": 6,
    "downbeat_offset": 0.125,
}
EXTERNAL_TIMING_VALUES = {
    "bpm_input": ("bpm", 173.25),
    "tempo_unit_input": ("tempo_unit", "Dotted Quarter"),
    "fps_input": ("fps", 24_000 / 1_001),
    "beats_per_bar_input": ("beats_per_bar", 7),
    "beat_unit_input": ("beat_unit", 16),
    "subdivisions_per_beat_input": ("subdivisions_per_beat", 12),
    "downbeat_offset_input": ("downbeat_offset", -0.375),
}


class FakeTensor:
    """Small tensor stand-in covering the silence and slice return path."""

    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape

    def __getitem__(self, _key: object) -> "FakeTensor":
        return self

    def unsqueeze(self, _dimension: int) -> "FakeTensor":
        return self


def _load_node_module(planner: object) -> ModuleType:
    folder_paths = ModuleType("folder_paths")
    folder_paths.get_filename_list = lambda _kind: ["fixture.wav"]  # type: ignore[attr-defined]
    folder_paths.get_input_directory = lambda: str(REPO_ROOT)  # type: ignore[attr-defined]
    folder_paths.get_annotated_filepath = lambda _name: ""  # type: ignore[attr-defined]
    folder_paths.filter_files_content_types = lambda files, _types: files  # type: ignore[attr-defined]

    torch = ModuleType("torch")
    torch.Tensor = FakeTensor  # type: ignore[attr-defined]
    torch.zeros = lambda shape: FakeTensor(shape)  # type: ignore[attr-defined]
    torch.int16 = object()  # type: ignore[attr-defined]
    torch.int32 = object()  # type: ignore[attr-defined]

    av = ModuleType("av")
    audio_clip_plan = ModuleType("audio_clip_plan")
    audio_clip_plan.create_audio_clip_plan = planner  # type: ignore[attr-defined]

    spec = importlib.util.spec_from_file_location(
        "_musical_audio_ui_output_contract",
        NODE_SOURCE,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("Could not create a module spec for musical_audio_ui.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "folder_paths": folder_paths,
            "torch": torch,
            "av": av,
            "audio_clip_plan": audio_clip_plan,
        },
    ):
        spec.loader.exec_module(module)
    return module


def _plan() -> SimpleNamespace:
    return SimpleNamespace(
        start_sample=11,
        end_sample=29,
        duration_seconds=0.75,
        start_seconds=0.25,
        end_seconds=1.0,
        start_frame=6,
        frame_count=18,
        seconds_per_beat=0.3463203463203463,
        frames_per_beat=8.303030303030303,
        seconds_per_bar=1.3852813852813852,
        frames_per_bar=33.21212121212121,
        musical_position="Bar 1 · Beat 1 · Subdivision 0",
    )


def _load_audio(module: ModuleType, **overrides: object) -> tuple[object, ...]:
    inputs = {
        "audio": "none",
        "start_time": 0.0,
        "end_time": 1.0,
        "duration": 1.0,
        "edit_mode": "Musical",
        **LOCAL_TIMING_VALUES,
        "start_bar": 1,
        "start_beat": 1,
        "start_subdivision": 0,
        "duration_bars": 0,
        "duration_beats": 1,
        "duration_subdivisions": 0,
        "snap_mode": "Off",
    }
    inputs.update(overrides)
    with redirect_stdout(io.StringIO()):
        return module.MusicalLoadAudioUI().load_audio(**inputs)


class LoadAudioOutputContractTests(unittest.TestCase):
    def test_local_values_are_used_when_external_inputs_are_absent(self) -> None:
        plan = _plan()
        planner = Mock(return_value=plan)
        module = _load_node_module(planner)
        result = _load_audio(module)

        self.assertEqual(len(result), 14)
        self.assertEqual(result[0]["sample_rate"], 44_100)
        self.assertIsInstance(result[0]["waveform"], FakeTensor)
        self.assertEqual(result[1:12], (
            plan.duration_seconds,
            "",
            plan.start_seconds,
            plan.end_seconds,
            plan.start_frame,
            plan.frame_count,
            plan.seconds_per_beat,
            plan.frames_per_beat,
            plan.seconds_per_bar,
            plan.frames_per_bar,
            plan.musical_position,
        ))
        self.assertEqual(result[12], float(LOCAL_TIMING_VALUES["bpm"]))
        self.assertEqual(result[13], float(LOCAL_TIMING_VALUES["fps"]))
        self.assertIsInstance(result[12], float)
        self.assertIsInstance(result[13], float)

        planning_inputs = planner.call_args.kwargs
        for planner_name, local_value in LOCAL_TIMING_VALUES.items():
            with self.subTest(planner_name=planner_name):
                self.assertEqual(planning_inputs[planner_name], local_value)

    def test_each_external_input_independently_overrides_its_local_fallback(self) -> None:
        for external_name, (planner_name, external_value) in EXTERNAL_TIMING_VALUES.items():
            with self.subTest(external_name=external_name):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)
                result = _load_audio(module, **{external_name: external_value})
                planning_inputs = planner.call_args.kwargs

                self.assertEqual(planning_inputs[planner_name], external_value)
                for other_name, local_value in LOCAL_TIMING_VALUES.items():
                    if other_name != planner_name:
                        self.assertEqual(planning_inputs[other_name], local_value)
                self.assertEqual(
                    result[12],
                    float(external_value if planner_name == "bpm" else LOCAL_TIMING_VALUES["bpm"]),
                )
                self.assertEqual(
                    result[13],
                    float(external_value if planner_name == "fps" else LOCAL_TIMING_VALUES["fps"]),
                )

    def test_each_missing_external_input_independently_falls_back_locally(self) -> None:
        all_external_values = {
            external_name: external_value
            for external_name, (_planner_name, external_value) in EXTERNAL_TIMING_VALUES.items()
        }
        for missing_external_name, (missing_planner_name, _value) in EXTERNAL_TIMING_VALUES.items():
            with self.subTest(missing_external_name=missing_external_name):
                supplied_values = {
                    name: value
                    for name, value in all_external_values.items()
                    if name != missing_external_name
                }
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)
                _load_audio(module, **supplied_values)
                planning_inputs = planner.call_args.kwargs

                self.assertEqual(
                    planning_inputs[missing_planner_name],
                    LOCAL_TIMING_VALUES[missing_planner_name],
                )
                for external_name, (planner_name, external_value) in EXTERNAL_TIMING_VALUES.items():
                    if external_name != missing_external_name:
                        self.assertEqual(planning_inputs[planner_name], external_value)

    def test_negative_downbeat_offsets_reach_the_planner_without_clamping(self) -> None:
        cases = (
            ("local fractional", {"downbeat_offset": -0.125}, -0.125),
            ("local extended", {"downbeat_offset": -1.25}, -1.25),
            (
                "external override",
                {"downbeat_offset": -1.25, "downbeat_offset_input": -0.5},
                -0.5,
            ),
        )

        for label, overrides, expected in cases:
            with self.subTest(label=label):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)
                _load_audio(module, **overrides)

                self.assertEqual(
                    planner.call_args.kwargs["downbeat_offset"],
                    expected,
                )

    def test_invalid_external_values_reach_normal_timing_validation(self) -> None:
        invalid_values = {
            "bpm_input": 0,
            "tempo_unit_input": "Half",
            "fps_input": 0,
            "beats_per_bar_input": 0,
            "beat_unit_input": 0,
            "subdivisions_per_beat_input": 0,
            "downbeat_offset_input": float("inf"),
        }
        module = _load_node_module(real_create_audio_clip_plan)

        for external_name, invalid_value in invalid_values.items():
            with self.subTest(external_name=external_name):
                with self.assertRaises((TypeError, ValueError)):
                    _load_audio(module, **{external_name: invalid_value})


if __name__ == "__main__":
    unittest.main()
