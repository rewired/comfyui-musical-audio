"""Execution-contract tests for audio fallbacks and timing inputs."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
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
    """Small tensor stand-in covering silence, slicing, and batching."""

    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape

    def __getitem__(self, key: object) -> "FakeTensor":
        if (
            isinstance(key, tuple)
            and len(key) == 2
            and isinstance(key[1], slice)
        ):
            start = 0 if key[1].start is None else key[1].start
            stop = self.shape[-1] if key[1].stop is None else key[1].stop
            return FakeTensor((self.shape[0], max(0, stop - start)))
        return FakeTensor(self.shape)

    def unsqueeze(self, dimension: int) -> "FakeTensor":
        shape = list(self.shape)
        shape.insert(dimension, 1)
        return FakeTensor(tuple(shape))


def _load_node_module(planner: object) -> ModuleType:
    folder_paths = ModuleType("folder_paths")
    folder_paths.get_filename_list = lambda _kind: ["fixture.wav"]  # type: ignore[attr-defined]
    folder_paths.get_input_directory = lambda: str(REPO_ROOT)  # type: ignore[attr-defined]
    folder_paths.get_annotated_filepath = lambda _name: str(  # type: ignore[attr-defined]
        REPO_ROOT / "fixture.wav"
    )
    folder_paths.filter_files_content_types = lambda files, _types: files  # type: ignore[attr-defined]

    torch = ModuleType("torch")
    torch.Tensor = FakeTensor  # type: ignore[attr-defined]
    torch.zeros = Mock(side_effect=lambda shape: FakeTensor(shape))  # type: ignore[attr-defined]
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


def _plan(**overrides: object) -> SimpleNamespace:
    values = {
        "start_sample": 11,
        "end_sample": 29,
        "duration_seconds": 0.75,
        "start_seconds": 0.25,
        "end_seconds": 1.0,
        "start_frame": 6,
        "frame_count": 18,
        "seconds_per_beat": 0.3463203463203463,
        "frames_per_beat": 8.303030303030303,
        "seconds_per_bar": 1.3852813852813852,
        "frames_per_bar": 33.21212121212121,
        "musical_position": "Bar 1 · Beat 1 · Subdivision 0",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _run_load_audio(
    module: ModuleType,
    *,
    path_exists: bool = True,
    decode_error: Exception | None = None,
    **overrides: object,
) -> SimpleNamespace:
    inputs = {
        "audio": "fixture.wav",
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
        "score_file": "",
    }
    inputs.update(overrides)
    decoder = Mock(
        return_value=(FakeTensor((2, 96_000)), 48_000),
        side_effect=decode_error,
    )
    stdout = io.StringIO()
    module.torch.zeros.reset_mock()
    with (
        patch.object(module.os.path, "isfile", return_value=path_exists),
        patch.object(module, "load_audio_file", decoder),
        redirect_stdout(stdout),
    ):
        result = module.MusicalLoadAudioUI().load_audio(**inputs)
    return SimpleNamespace(result=result, stdout=stdout.getvalue(), decoder=decoder)


def _load_audio(module: ModuleType, **overrides: object) -> tuple[object, ...]:
    return _run_load_audio(module, **overrides).result


class LoadAudioOutputContractTests(unittest.TestCase):
    def test_local_values_are_used_when_external_inputs_are_absent(self) -> None:
        source = NODE_SOURCE.read_text(encoding="utf-8")
        self.assertIn("serialize_diagnostics", source)
        self.assertNotIn("def _serialize_diagnostics", source)
        plan = _plan()
        planner = Mock(return_value=plan)
        module = _load_node_module(planner)
        result = _load_audio(module)

        self.assertEqual(len(result), 20)
        self.assertEqual(result[0]["sample_rate"], 48_000)
        self.assertIsInstance(result[0]["waveform"], FakeTensor)
        self.assertEqual(result[0]["waveform"].shape, (1, 2, 18))
        self.assertEqual(result[1:12], (
            plan.duration_seconds,
            "fixture.wav",
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
        self.assertEqual(
            result[14:],
            (24, "", 48_000, "constant", "constant", "[]"),
        )

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

    def test_successful_decode_preserves_musical_position_and_has_no_warning(self) -> None:
        plan = _plan(musical_position="Bar 12 · Beat 3 · Subdivision 2")
        planner = Mock(return_value=plan)
        module = _load_node_module(planner)

        run = _run_load_audio(module)

        self.assertEqual(run.result[11], plan.musical_position)
        self.assertNotIn("WARNING", run.result[11])
        self.assertEqual(run.result[2], "fixture.wav")
        self.assertEqual(run.stdout, "")
        run.decoder.assert_called_once_with(str(REPO_ROOT / "fixture.wav"))
        module.torch.zeros.assert_not_called()

    def test_no_selection_reports_warning_and_one_second_stereo_silence(self) -> None:
        plan = _plan(start_sample=0, end_sample=44_100, duration_seconds=1.0)
        planner = Mock(return_value=plan)
        module = _load_node_module(planner)

        run = _run_load_audio(module, audio="none")

        self.assertEqual(len(run.result), 20)
        self.assertNotIn("WARNING", run.result[11])
        warning = json.loads(run.result[19])
        self.assertEqual(warning[0]["code"], "score_provider_error")
        self.assertIn("no file selected", warning[0]["message"])
        self.assertIn("using 1 second of silence", warning[0]["message"])
        self.assertIn("Outputting 1 second of silence", run.stdout)
        self.assertEqual(run.result[0]["sample_rate"], 44_100)
        self.assertEqual(run.result[16], 44_100)
        self.assertEqual(run.result[0]["waveform"].shape, (1, 2, 44_100))
        self.assertEqual(run.result[2], "")
        module.torch.zeros.assert_called_once_with((2, 44_100))
        planner.assert_called_once()

    def test_unresolved_selection_reports_the_exact_audio_value(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        selected_audio = "stale/DAW export.wav"

        with patch.object(
            module.folder_paths,
            "get_annotated_filepath",
            side_effect=RuntimeError("cannot resolve"),
        ):
            run = _run_load_audio(module, audio=selected_audio)

        self.assertNotIn("WARNING", run.result[11])
        warning = json.loads(run.result[19])[0]
        self.assertIn(selected_audio, warning["message"])
        self.assertIn("path could not be resolved", warning["message"])
        self.assertIn("using 1 second of silence", warning["message"])
        self.assertIn(selected_audio, run.stdout)
        self.assertEqual(run.result[2], "")
        run.decoder.assert_not_called()
        planner.assert_called_once()

    def test_missing_file_reports_the_exact_audio_value(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        selected_audio = "missing/song.wav"

        run = _run_load_audio(
            module,
            audio=selected_audio,
            path_exists=False,
        )

        self.assertNotIn("WARNING", run.result[11])
        warning = json.loads(run.result[19])[0]
        self.assertIn(selected_audio, warning["message"])
        self.assertIn("file not found", warning["message"])
        self.assertIn("using 1 second of silence", warning["message"])
        self.assertIn(selected_audio, run.stdout)
        self.assertEqual(run.result[2], "")
        run.decoder.assert_not_called()
        planner.assert_called_once()

    def test_decode_failure_reports_class_message_and_remains_single_line(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        decode_error = RuntimeError("decoder exploded\nsecond line")

        run = _run_load_audio(module, decode_error=decode_error)

        self.assertNotIn("WARNING", run.result[11])
        warning = json.loads(run.result[19])[0]
        self.assertIn("fixture.wav", warning["message"])
        self.assertIn("decode failed", warning["message"])
        self.assertIn("RuntimeError", warning["message"])
        self.assertIn("decoder exploded second line", warning["message"])
        self.assertIn("using 1 second of silence", warning["message"])
        self.assertNotIn("\n", warning["message"])
        self.assertNotIn("\r", warning["message"])
        self.assertIn("Error decoding fixture.wav", run.stdout)
        self.assertEqual(run.result[0]["sample_rate"], 44_100)
        self.assertEqual(run.result[16], 44_100)
        self.assertEqual(run.result[2], "fixture.wav")
        module.torch.zeros.assert_called_once_with((2, 44_100))
        planner.assert_called_once()

    def test_decode_failure_exception_text_is_bounded(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)

        run = _run_load_audio(
            module,
            decode_error=ValueError("x" * 1_000),
        )

        warning = json.loads(run.result[19])[0]
        self.assertLessEqual(len(warning["message"]), 200)
        self.assertIn("ValueError", warning["message"])
        self.assertIn("...", warning["message"])

    def test_start_beat_is_clamped_only_at_the_upper_bar_boundary(self) -> None:
        cases = (
            ("below", 3, 4, 3),
            ("equal", 4, 4, 4),
            ("above", 7, 4, 4),
        )

        for label, start_beat, beats_per_bar, expected in cases:
            with self.subTest(label=label):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)

                _load_audio(
                    module,
                    start_beat=start_beat,
                    beats_per_bar=beats_per_bar,
                )

                self.assertEqual(planner.call_args.kwargs["start_beat"], expected)
                self.assertEqual(
                    planner.call_args.kwargs["beats_per_bar"],
                    beats_per_bar,
                )

    def test_external_beats_per_bar_controls_start_beat_clamping(self) -> None:
        cases = (
            (3, 3),
            (80, 7),
        )

        for external_beats_per_bar, expected_start_beat in cases:
            with self.subTest(external_beats_per_bar=external_beats_per_bar):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)

                _load_audio(
                    module,
                    start_beat=7,
                    beats_per_bar=4,
                    beats_per_bar_input=external_beats_per_bar,
                )

                planning_inputs = planner.call_args.kwargs
                self.assertEqual(
                    planning_inputs["beats_per_bar"],
                    external_beats_per_bar,
                )
                self.assertEqual(
                    planning_inputs["start_beat"],
                    expected_start_beat,
                )

    def test_invalid_external_beats_per_bar_reaches_timing_validation(self) -> None:
        module = _load_node_module(real_create_audio_clip_plan)

        with self.assertRaises(ValueError):
            _load_audio(module, start_beat=7, beats_per_bar_input=0)

    def test_invalid_start_beat_or_meter_types_are_not_coerced(self) -> None:
        cases = (
            ("7", 4),
            (True, 4),
            (7, "4"),
        )

        for start_beat, beats_per_bar in cases:
            with self.subTest(start_beat=start_beat, beats_per_bar=beats_per_bar):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)

                _load_audio(
                    module,
                    start_beat=start_beat,
                    beats_per_bar_input=beats_per_bar,
                )

                self.assertIs(planner.call_args.kwargs["start_beat"], start_beat)
                self.assertIs(
                    planner.call_args.kwargs["beats_per_bar"],
                    beats_per_bar,
                )

    def test_start_beat_argument_is_not_mutated(self) -> None:
        start_beat = 7
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)

        _load_audio(module, start_beat=start_beat, beats_per_bar=4)

        self.assertEqual(start_beat, 7)
        self.assertEqual(planner.call_args.kwargs["start_beat"], 4)


if __name__ == "__main__":
    unittest.main()
