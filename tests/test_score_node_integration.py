"""Phase 4b integration tests for Score-aware MusicalLoadAudioUI execution."""

from __future__ import annotations

import builtins
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from audio_clip_plan import (
    ClipTimingMetadata,
    RequestedAudioRange,
    SampleRangePlan,
    apply_sample_range,
    create_audio_clip_plan as real_create_audio_clip_plan,
    finalize_audio_clip_plan,
)
from score.model import MeterEvent, ResolvedScore, Score, Section, TempoEvent
from score.providers import ScoreResolution


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_SOURCE = REPO_ROOT / "musical_audio_ui.py"
AUDIO_SELECTION = "music/track.wav"
AUDIO_PATH = REPO_ROOT / "media" / "track.wav"


class FakeTensor:
    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape

    def __getitem__(self, key: object) -> "FakeTensor":
        if isinstance(key, tuple) and len(key) == 2 and isinstance(key[1], slice):
            start = 0 if key[1].start is None else key[1].start
            stop = self.shape[-1] if key[1].stop is None else key[1].stop
            return FakeTensor((self.shape[0], max(0, stop - start)))
        return FakeTensor(self.shape)

    def unsqueeze(self, dimension: int) -> "FakeTensor":
        shape = list(self.shape)
        shape.insert(dimension, 1)
        return FakeTensor(tuple(shape))


def _plan(**overrides: object) -> SimpleNamespace:
    values = {
        "requested_start_seconds": 0.25,
        "requested_end_seconds": 1.0,
        "start_sample": 12_000,
        "end_sample": 48_000,
        "start_seconds": 0.25,
        "end_seconds": 1.0,
        "duration_seconds": 0.75,
        "start_frame": 6,
        "frame_count": 18,
        "seconds_per_beat": 0.5,
        "frames_per_beat": 12.0,
        "seconds_per_bar": 2.0,
        "frames_per_bar": 48.0,
        "musical_position": "protected musical position",
        "clamped": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _score(
    *,
    numerator: int = 4,
    denominator: int = 4,
    tempos: tuple[TempoEvent, ...] | None = None,
    meters: tuple[MeterEvent, ...] | None = None,
    sections: tuple[Section, ...] = (),
    variable_meter: bool = False,
    midbar_meter: bool = False,
    source: str = "json",
    ticks_per_quarter: int = 480,
) -> Score:
    return Score(
        ticks_per_quarter=ticks_per_quarter,
        tempos=(TempoEvent(0, 500_000),) if tempos is None else tempos,
        meters=(MeterEvent(0, numerator, denominator),) if meters is None else meters,
        markers=(),
        sections=sections,
        source=source,
        has_variable_meter=variable_meter,
        has_midbar_meter_change=midbar_meter,
    )


def _resolution(
    score: Score | None = None,
    *,
    provider: str = "constant",
    provider_alignment: float | None = None,
    diagnostics: tuple[str, ...] = (),
    provenance: dict[str, str] | None = None,
    fingerprint: tuple[object, ...] = ("test",),
) -> ScoreResolution:
    resolved = None
    if score is not None:
        resolved = ResolvedScore(
            score=score,
            audio_seconds_at_tick_zero=0.0,
            provider=provider,
        )
    return ScoreResolution(
        resolved_score=resolved,
        provider=provider,
        provider_alignment_seconds=provider_alignment,
        diagnostics=diagnostics,
        provenance={} if provenance is None else provenance,
        dependency_fingerprint=fingerprint,
    )


def _load_node_module(
    planner: object,
    *,
    annotated_resolver: object | None = None,
) -> ModuleType:
    folder_paths = ModuleType("folder_paths")
    folder_paths.get_filename_list = lambda _kind: [AUDIO_SELECTION]  # type: ignore[attr-defined]
    folder_paths.get_input_directory = lambda: str(REPO_ROOT)  # type: ignore[attr-defined]
    resolver = (
        (lambda _name: str(AUDIO_PATH))
        if annotated_resolver is None
        else annotated_resolver
    )
    folder_paths.get_annotated_filepath = resolver  # type: ignore[attr-defined]
    folder_paths.filter_files_content_types = lambda files, _types: files  # type: ignore[attr-defined]

    torch = ModuleType("torch")
    torch.Tensor = FakeTensor  # type: ignore[attr-defined]
    torch.zeros = Mock(side_effect=lambda shape: FakeTensor(shape))  # type: ignore[attr-defined]
    torch.int16 = object()  # type: ignore[attr-defined]
    torch.int32 = object()  # type: ignore[attr-defined]

    av = ModuleType("av")
    audio_clip_plan = ModuleType("audio_clip_plan")
    audio_clip_plan.ClipTimingMetadata = ClipTimingMetadata  # type: ignore[attr-defined]
    audio_clip_plan.RequestedAudioRange = RequestedAudioRange  # type: ignore[attr-defined]
    audio_clip_plan.SampleRangePlan = SampleRangePlan  # type: ignore[attr-defined]
    audio_clip_plan.apply_sample_range = apply_sample_range  # type: ignore[attr-defined]
    audio_clip_plan.create_audio_clip_plan = planner  # type: ignore[attr-defined]
    audio_clip_plan.finalize_audio_clip_plan = finalize_audio_clip_plan  # type: ignore[attr-defined]

    name = f"_phase4b_node_{id(planner)}_{id(folder_paths)}"
    spec = importlib.util.spec_from_file_location(name, NODE_SOURCE)
    if spec is None or spec.loader is None:
        raise AssertionError("could not create node module spec")
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


def _inputs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "audio": AUDIO_SELECTION,
        "start_time": 0.25,
        "end_time": 1.0,
        "duration": 0.75,
        "edit_mode": "Seconds",
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
        "duration_beats": 1,
        "duration_subdivisions": 0,
        "subdivisions_per_beat": 4,
        "snap_mode": "Off",
        "score_file": "",
        "score_end_bar": 0,
        "score_end_beat": 0,
        "score_end_subdivision": 0,
    }
    values.update(overrides)
    return values


def _run(
    module: ModuleType,
    *,
    resolution: ScoreResolution | None = None,
    path_exists: bool = True,
    plan: SimpleNamespace | None = None,
    **overrides: object,
) -> SimpleNamespace:
    planner = module.create_audio_clip_plan
    if isinstance(planner, Mock) and plan is not None:
        planner.return_value = plan
    chain = None
    chain_mock = None
    if resolution is not None:
        chain_mock = Mock(return_value=resolution)
        chain = patch.dict(
            module.resolve_score_repository.__globals__,
            {"resolve_provider_chain": chain_mock},
        )
    decoder = Mock(return_value=(FakeTensor((2, 960_000)), 48_000))
    stdout = io.StringIO()
    contexts = [
        patch.object(module.os.path, "isfile", return_value=path_exists),
        patch.object(module, "load_audio_file", decoder),
    ]
    if chain is not None:
        contexts.append(chain)
    with contexts[0], contexts[1], redirect_stdout(stdout):
        if chain is None:
            result = module.MusicalLoadAudioUI().load_audio(**_inputs(**overrides))
        else:
            with contexts[2]:
                result = module.MusicalLoadAudioUI().load_audio(**_inputs(**overrides))
    return SimpleNamespace(
        result=result,
        stdout=stdout.getvalue(),
        decoder=decoder,
        chain=chain_mock,
    )


class ContractAndPathTests(unittest.TestCase):
    def test_dynamic_input_and_output_contract_is_exact(self) -> None:
        module = _load_node_module(Mock(return_value=_plan()))
        input_types = module.MusicalLoadAudioUI.INPUT_TYPES()
        required = tuple(input_types["required"])

        self.assertEqual(
            required[-4:],
            (
                "score_file",
                "score_end_bar",
                "score_end_beat",
                "score_end_subdivision",
            ),
        )
        self.assertEqual(
            input_types["required"]["score_file"],
            ("STRING", {"default": "", "socketless": True}),
        )
        self.assertEqual(len(module.MusicalLoadAudioUI.RETURN_TYPES), 20)
        self.assertEqual(
            module.MusicalLoadAudioUI.RETURN_NAMES[14:],
            (
                "end_frame_exclusive",
                "section_name",
                "sample_rate",
                "score_format",
                "score_provider",
                "diagnostics",
            ),
        )

    def test_blank_and_whitespace_do_not_resolve_explicit_score(self) -> None:
        calls: list[str] = []

        def resolve(name: str) -> str:
            calls.append(name)
            return str(AUDIO_PATH)

        for score_file in ("", "  \t"):
            with self.subTest(score_file=score_file):
                calls.clear()
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner, annotated_resolver=resolve)
                _run(module, score_file=score_file)
                self.assertEqual(calls, [AUDIO_SELECTION])

    def test_literal_none_is_resolved_as_an_explicit_filename(self) -> None:
        calls: list[str] = []

        def resolve(name: str) -> str:
            calls.append(name)
            return str(AUDIO_PATH if name == AUDIO_SELECTION else REPO_ROOT / "none")

        module = _load_node_module(Mock(return_value=_plan()), annotated_resolver=resolve)
        with self.assertRaises(ValueError):
            _run(module, score_file="none")
        self.assertEqual(calls, [AUDIO_SELECTION, "none"])

    def test_explicit_resolution_error_and_missing_path_are_fatal(self) -> None:
        for label, resolver in (
            (
                "unresolved",
                lambda name: (
                    str(AUDIO_PATH)
                    if name == AUDIO_SELECTION
                    else (_ for _ in ()).throw(RuntimeError("unresolved"))
                ),
            ),
            (
                "missing",
                lambda name: str(AUDIO_PATH if name == AUDIO_SELECTION else REPO_ROOT / "missing.json"),
            ),
        ):
            with self.subTest(label=label):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner, annotated_resolver=resolver)
                with self.assertRaises(ValueError) as captured:
                    _run(module, score_file="selected.json")
                value = json.loads(str(captured.exception))
                self.assertEqual(value[0]["code"], "score_provider_error")
                self.assertEqual(value[0]["severity"], "error")
                planner.assert_not_called()

    def test_explicit_resolution_does_not_swallow_process_control_signals(self) -> None:
        for signal in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(signal=type(signal).__name__):
                def resolve(name: str, signal: BaseException = signal) -> str:
                    if name == AUDIO_SELECTION:
                        return str(AUDIO_PATH)
                    raise signal

                planner = Mock(return_value=_plan())
                module = _load_node_module(planner, annotated_resolver=resolve)
                with self.assertRaises(type(signal)):
                    _run(module, score_file="selected.json")
                planner.assert_not_called()

    def test_explicit_annotated_path_reaches_the_first_provider(self) -> None:
        explicit_path = REPO_ROOT / "scores" / "selected.score.json"

        def resolve(name: str) -> str:
            return str(AUDIO_PATH if name == AUDIO_SELECTION else explicit_path)

        module = _load_node_module(
            Mock(return_value=_plan()),
            annotated_resolver=resolve,
        )
        run = _run(module, resolution=_resolution(), score_file="selected.score.json")
        provider = run.chain.call_args.args[0][0]
        self.assertEqual(provider.score_file, "selected.score.json")
        self.assertEqual(provider.resolved_path, str(explicit_path))

    def test_malformed_explicit_file_stops_before_planner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "broken.json"
            malformed.write_text("{not json", encoding="utf-8")

            def resolve(name: str) -> str:
                return str(AUDIO_PATH if name == AUDIO_SELECTION else malformed)

            planner = Mock(return_value=_plan())
            module = _load_node_module(planner, annotated_resolver=resolve)
            with self.assertRaises(ValueError) as captured:
                _run(module, score_file="broken.json")

        self.assertEqual(json.loads(str(captured.exception))[0]["severity"], "error")
        planner.assert_not_called()

    def test_automatic_candidates_replace_wav_and_flac_suffixes(self) -> None:
        for suffix in (".wav", ".flac"):
            with self.subTest(suffix=suffix):
                audio_path = REPO_ROOT / "media" / f"track{suffix}"
                module = _load_node_module(
                    Mock(return_value=_plan()),
                    annotated_resolver=lambda _name, path=audio_path: str(path),
                )
                resolution = _resolution()
                run = _run(module, resolution=resolution)
                providers = run.chain.call_args.args[0]
                self.assertEqual(
                    Path(providers[1].candidate_path).name,
                    "track.score.json",
                )
                self.assertEqual(Path(providers[2].candidate_path).name, "track.mid")

    def test_provider_order_is_exact_and_chain_runs_once(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        run = _run(module, resolution=_resolution())
        providers = run.chain.call_args.args[0]

        self.assertEqual(
            tuple(provider.kind for provider in providers),
            ("explicit", "json_sidecar", "midi_sidecar", "analysis", "constant"),
        )
        run.chain.assert_called_once()

    def test_constant_fallback_appends_exact_neutral_outputs(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        result = _run(module).result

        self.assertEqual(len(result), 20)
        self.assertEqual(result[14:], (24, "", 48_000, "constant", "constant", "[]"))
        planner.assert_called_once()


class FingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_node_module(Mock(return_value=_plan()))
        self.node = self.module.MusicalLoadAudioUI

    def test_audio_identity_is_an_unchanged_prefix_for_all_states(self) -> None:
        none_value = self.node.IS_CHANGED("none")
        self.assertEqual(none_value[:2], ("none", "none"))

        with patch.object(self.module.os, "stat", side_effect=FileNotFoundError):
            missing = self.node.IS_CHANGED(AUDIO_SELECTION)
        self.assertEqual(missing[:2], ("missing", AUDIO_SELECTION))

        with patch.object(
            self.module.folder_paths,
            "get_annotated_filepath",
            side_effect=RuntimeError("no resolver"),
        ):
            unresolved = self.node.IS_CHANGED(AUDIO_SELECTION)
        self.assertEqual(unresolved[:2], ("unresolved", AUDIO_SELECTION))

    def test_score_fingerprint_exists_when_audio_is_none_or_unresolved(self) -> None:
        none_value = self.node.IS_CHANGED("none")[-1]
        with patch.object(
            self.module.folder_paths,
            "get_annotated_filepath",
            side_effect=RuntimeError("no resolver"),
        ):
            unresolved = self.node.IS_CHANGED(AUDIO_SELECTION)[-1]

        self.assertIsInstance(none_value, tuple)
        self.assertEqual(none_value[-3], none_value[-2])
        self.assertEqual(unresolved[-3], unresolved[-2])
        self.assertIn("unavailable", none_value[-3])

    def test_missing_automatic_json_and_midi_are_both_present(self) -> None:
        with patch.object(self.module.os, "stat", side_effect=FileNotFoundError):
            fingerprint = self.node.IS_CHANGED(AUDIO_SELECTION)[-1]

        roles = tuple(value[0] for value in fingerprint if isinstance(value, tuple))
        self.assertIn("json_sidecar", roles)
        self.assertIn("midi_sidecar", roles)

    def test_candidate_appearance_mutation_and_removal_change_identity(self) -> None:
        json_path = os.path.normcase(
            os.path.abspath(os.path.normpath(str(AUDIO_PATH.with_suffix(".score.json"))))
        )
        state = {"present": False, "size": 10, "mtime": 100}

        def stat(path: object) -> SimpleNamespace:
            normalized = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))
            if normalized == json_path and not state["present"]:
                raise FileNotFoundError
            if normalized.endswith(".mid"):
                raise FileNotFoundError
            if normalized.endswith("track.wav"):
                return SimpleNamespace(st_size=10, st_mtime_ns=100)
            return SimpleNamespace(st_size=state["size"], st_mtime_ns=state["mtime"])

        with patch.object(self.module.os, "stat", side_effect=stat):
            absent = self.node.IS_CHANGED(AUDIO_SELECTION)
            state["present"] = True
            appeared = self.node.IS_CHANGED(AUDIO_SELECTION)
            state["mtime"] = 101
            mutated = self.node.IS_CHANGED(AUDIO_SELECTION)
            state["present"] = False
            removed = self.node.IS_CHANGED(AUDIO_SELECTION)

        self.assertNotEqual(absent, appeared)
        self.assertNotEqual(appeared, mutated)
        self.assertNotEqual(mutated, removed)
        self.assertEqual(absent, removed)

    def test_explicit_score_metadata_changes_invalidate_the_fingerprint(self) -> None:
        explicit_path = REPO_ROOT / "scores" / "selected.json"

        def resolve(name: str) -> str:
            return str(AUDIO_PATH if name == AUDIO_SELECTION else explicit_path)

        module = _load_node_module(
            Mock(return_value=_plan()),
            annotated_resolver=resolve,
        )
        state = {"mtime": 1}

        def stat(path: object) -> SimpleNamespace:
            normalized = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))
            if normalized.endswith((".score.json", ".mid")):
                raise FileNotFoundError
            if normalized.endswith("track.wav"):
                return SimpleNamespace(st_size=10, st_mtime_ns=1)
            return SimpleNamespace(st_size=10, st_mtime_ns=state["mtime"])

        with patch.object(module.os, "stat", side_effect=stat):
            first = module.MusicalLoadAudioUI.IS_CHANGED(
                AUDIO_SELECTION,
                score_file="selected.json",
            )
            state["mtime"] = 2
            second = module.MusicalLoadAudioUI.IS_CHANGED(
                AUDIO_SELECTION,
                score_file="selected.json",
            )
        self.assertNotEqual(first, second)

    def test_unresolved_audio_never_derives_automatic_paths_from_raw_widget_text(self) -> None:
        with (
            patch.object(
                self.module.folder_paths,
                "get_annotated_filepath",
                side_effect=RuntimeError("unresolved"),
            ),
            patch(
                "score.runtime.derive_automatic_candidate_paths",
                side_effect=AssertionError("automatic path derived"),
            ),
        ):
            value = self.node.IS_CHANGED("raw/selection.flac")
        self.assertEqual(value[:2], ("unresolved", "raw/selection.flac"))

    def test_is_changed_is_stat_only_and_does_not_mutate_kwargs(self) -> None:
        kwargs = {"score_file": "score.json", "nested": {"value": [1]}}
        expected = {"score_file": "score.json", "nested": {"value": [1]}}
        with (
            patch.object(self.module.os, "stat", side_effect=FileNotFoundError),
            patch.object(builtins, "open", side_effect=AssertionError("content read")),
        ):
            self.node.IS_CHANGED(AUDIO_SELECTION, **kwargs)
        self.assertEqual(kwargs, expected)

    def test_execution_uses_the_same_score_fingerprint_policy(self) -> None:
        with patch.object(self.module.os, "stat", side_effect=FileNotFoundError):
            expected = self.node.IS_CHANGED(AUDIO_SELECTION)[-1]
            run = _run(self.module, resolution=_resolution())
        self.assertEqual(
            run.chain.call_args.kwargs["dependency_fingerprint"],
            expected,
        )


class AlignmentAndActivationTests(unittest.TestCase):
    def test_external_alignment_reaches_complete_score_planning_once(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        run = _run(
            module,
            resolution=_resolution(_score(), provider="explicit", provider_alignment=-0.5),
            downbeat_offset=7.0,
            downbeat_offset_input=-0.5,
        )
        self.assertEqual(run.chain.call_args.kwargs["audio_seconds_at_tick_zero"], -0.5)
        planner.assert_not_called()
        self.assertEqual(json.loads(run.result[19]), [])

    def test_divergent_typed_alignment_warns_without_addition_or_refusal(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        result = _run(
            module,
            resolution=_resolution(
                _score(),
                provider="explicit",
                provider_alignment=2.0,
                provenance={"audio_seconds_at_tick_zero": "999.0"},
            ),
            downbeat_offset=-0.25,
        ).result
        values = json.loads(result[19])

        planner.assert_not_called()
        self.assertEqual([value["code"] for value in values], ["score_alignment_divergence"])

    def test_seconds_ignores_unused_musical_fields_without_fake_start(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        result = _run(
            module,
            resolution=_resolution(_score(numerator=3), provider="explicit"),
            edit_mode="Seconds",
            start_bar="unused",
            start_beat=99,
            start_subdivision=object(),
            duration_bars="unused",
            duration_beats=-1,
            duration_subdivisions=object(),
        ).result
        planner.assert_not_called()
        self.assertEqual(len(result), 20)

    def test_uniform_musical_uses_original_score_canonical_start(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        _run(
            module,
            resolution=_resolution(_score(), provider="explicit"),
            edit_mode="Musical",
            beats_per_bar=3,
            start_beat=4,
        )
        planner.assert_not_called()

    def test_noncanonical_musical_start_is_a_fatal_structural_error(self) -> None:
        cases = (
            {"start_bar": 0},
            {"start_bar": True},
            {"start_beat": 4},
            {"start_subdivision": 4},
            {"subdivisions_per_beat": True},
            {"subdivisions_per_beat": 1_000},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)
                with self.assertRaises(ValueError) as caught:
                    _run(
                        module,
                        resolution=_resolution(_score(numerator=3), provider="explicit"),
                        edit_mode="Musical",
                        **overrides,
                    )
                self.assertEqual(
                    [value["code"] for value in json.loads(str(caught.exception))],
                    ["score_selection_range_invalid"],
                )
                planner.assert_not_called()

    def test_duration_overflow_does_not_refuse_uniform_score(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        _run(
            module,
            resolution=_resolution(_score(), provider="explicit"),
            edit_mode="Musical",
            duration_bars=12,
            duration_beats=99,
            duration_subdivisions=101,
        )
        planner.assert_not_called()

    def test_every_nonuniform_shape_has_complete_score_authority(self) -> None:
        scores = (
            _score(tempos=(TempoEvent(0, 500_000), TempoEvent(480, 600_000))),
            _score(
                meters=(MeterEvent(0, 4, 4), MeterEvent(1920, 3, 4)),
                variable_meter=True,
            ),
            _score(
                meters=(MeterEvent(0, 4, 4), MeterEvent(480, 3, 4)),
                variable_meter=True,
                midbar_meter=True,
            ),
        )
        for score in scores:
            with self.subTest(score=score):
                planner = Mock(return_value=_plan())
                module = _load_node_module(planner)
                result = _run(
                    module,
                    resolution=_resolution(score, provider="explicit"),
                ).result
                planner.assert_not_called()
                self.assertEqual(result[17:19], (score.source, "explicit"))
                self.assertNotIn(
                    "score_activation_refused",
                    [value["code"] for value in json.loads(result[19])],
                )

    def test_constant_editing_applies_legacy_clamp_after_activation_decision(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        _run(module, resolution=_resolution(), beats_per_bar=3, start_beat=7)
        self.assertEqual(planner.call_args.kwargs["start_beat"], 3)

    def test_unexpected_position_failure_after_preflight_propagates(self) -> None:
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        with patch.object(module.ScoreResolver, "position_to_tick", side_effect=RuntimeError("unexpected")):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                _run(
                    module,
                    resolution=_resolution(_score(), provider="explicit"),
                    edit_mode="Musical",
                )
        planner.assert_not_called()


class SectionAndDiagnosticsTests(unittest.TestCase):
    def test_active_unclamped_musical_uses_exact_tick_at_section_boundary(self) -> None:
        sections = (
            Section("Before", 0, 1920, True),
            Section("After", 1920, 3840, True),
        )
        planner = Mock(return_value=_plan(requested_start_seconds=2.0, start_seconds=2.0))
        module = _load_node_module(planner)
        with patch.object(
            module.ScoreResolver,
            "audio_seconds_to_tick",
            side_effect=AssertionError("seconds roundtrip"),
        ):
            result = _run(
                module,
                resolution=_resolution(_score(sections=sections), provider="explicit"),
                edit_mode="Musical",
                start_bar=2,
            ).result
        self.assertEqual(result[15], "After")

    def test_seconds_unclamped_uses_requested_time_not_returned_sample_time(self) -> None:
        sections = (
            Section("Before", 0, 480, True),
            Section("After", 480, 960, True),
        )
        planner = Mock(
            return_value=_plan(
                requested_start_seconds=0.5,
                start_seconds=0.499,
                musical_position="Nearest: Bar 1 · Beat 2 · Subdivision 0",
            )
        )
        module = _load_node_module(planner)
        result = _run(
            module,
            resolution=_resolution(_score(sections=sections), provider="explicit"),
            start_time=0.5,
        ).result
        self.assertEqual(result[15], "After")
        self.assertIn("Nearest: Bar 1 · Beat 2 · Subdivision 0", result[11])

    def test_clamped_active_and_display_only_queries_use_returned_time(self) -> None:
        sections = (
            Section("Returned", 0, 480, True),
            Section("Requested", 480, 1920, True),
        )
        for score in (
            _score(sections=sections),
            _score(
                sections=sections,
                tempos=(TempoEvent(0, 500_000), TempoEvent(960, 600_000)),
            ),
        ):
            with self.subTest(score=score):
                planner = Mock(
                    return_value=_plan(
                        requested_start_seconds=1.0,
                        start_seconds=0.25,
                        clamped=True,
                    )
                )
                module = _load_node_module(planner)
                result = _run(
                    module,
                    resolution=_resolution(score, provider="explicit"),
                ).result
                self.assertEqual(result[15], "Returned")

    def test_clamped_active_musical_query_uses_returned_time(self) -> None:
        sections = (
            Section("Requested", 0, 480, True),
            Section("Returned", 960, 1440, True),
        )
        planner = Mock(
            return_value=_plan(
                requested_start_seconds=2.0,
                start_seconds=0.25,
                clamped=True,
            )
        )
        module = _load_node_module(planner)
        result = _run(
            module,
            resolution=_resolution(_score(sections=sections), provider="explicit"),
            edit_mode="Musical",
            start_bar=1,
            downbeat_offset=-1.0,
        ).result
        self.assertEqual(result[15], "Returned")

    def test_overlap_selection_delegates_to_phase4a_helper(self) -> None:
        sections = (
            Section("Z", 0, 1000, False),
            Section("A", 200, 900, False),
            Section("B", 200, 900, False),
        )
        planner = Mock(return_value=_plan(requested_start_seconds=0.25))
        module = _load_node_module(planner)
        result = _run(
            module,
            resolution=_resolution(_score(sections=sections), provider="explicit"),
        ).result
        self.assertEqual(result[15], "A")

    def test_no_section_match_and_constant_timing_return_empty_name(self) -> None:
        planner = Mock(return_value=_plan(requested_start_seconds=5.0))
        module = _load_node_module(planner)
        score_result = _run(
            module,
            resolution=_resolution(
                _score(sections=(Section("Early", 0, 100, False),)),
                provider="explicit",
            ),
        ).result
        constant_result = _run(module, resolution=_resolution()).result
        self.assertEqual(score_result[15], "")
        self.assertEqual(constant_result[15], "")

    def test_diagnostics_are_compact_ordered_bounded_and_closed(self) -> None:
        long_message = "parser\n" + "x" * 400
        planner = Mock(return_value=_plan())
        module = _load_node_module(planner)
        result = _run(
            module,
            resolution=_resolution(
                _score(
                    tempos=(TempoEvent(0, 500_000), TempoEvent(480, 600_000)),
                ),
                provider="explicit",
                provider_alignment=1.0,
                diagnostics=(
                    "score_source_identity_mismatch: stale",
                    long_message,
                    long_message,
                ),
            ),
            path_exists=False,
        ).result
        raw = result[19]
        values = json.loads(raw)
        codes = [value["code"] for value in values]

        self.assertEqual(
            raw,
            json.dumps(values, ensure_ascii=False, separators=(",", ":")),
        )
        self.assertEqual(
            codes,
            [
                "score_provider_error",
                "score_provider_error",
                "score_resolver_diagnostic",
                "score_resolver_diagnostic",
                "score_alignment_divergence",
            ],
        )
        self.assertEqual(values[2]["message"], values[3]["message"])
        self.assertLessEqual(len(values[2]["message"]), 200)
        self.assertTrue(values[2]["message"].endswith("..."))
        allowed = {
            "score_alignment_divergence",
            "score_provider_error",
            "score_start_position_not_canonical",
            "score_resolver_diagnostic",
        }
        self.assertLessEqual(set(codes), allowed)
        self.assertTrue(all(tuple(value) == ("code", "severity", "message") for value in values))

    def test_audio_fallback_warning_moves_out_of_musical_position(self) -> None:
        planner = Mock(return_value=_plan(musical_position="protected nearest"))
        module = _load_node_module(planner)
        result = _run(module, path_exists=False).result
        self.assertEqual(result[11], "protected nearest")
        self.assertEqual(json.loads(result[19])[0]["code"], "score_provider_error")

    def test_appended_outputs_use_plan_and_actual_resolution_values(self) -> None:
        planner = Mock(return_value=_plan(start_frame=7, frame_count=13))
        module = _load_node_module(planner)
        result = _run(
            module,
            resolution=_resolution(_score(source="midi"), provider="midi_sidecar"),
        ).result
        self.assertEqual(result[14], 24)
        self.assertEqual(result[16], 48_000)
        self.assertEqual(result[17:19], ("midi", "midi_sidecar"))

    def test_real_planner_preserves_both_protected_nearest_strings(self) -> None:
        module = _load_node_module(real_create_audio_clip_plan)
        canonical = _run(
            module,
            resolution=_resolution(_score(), provider="explicit"),
            start_time=2.75,
            end_time=3.0,
        ).result
        before = _run(
            module,
            resolution=_resolution(_score(), provider="explicit"),
            start_time=0.5,
            end_time=0.75,
            downbeat_offset=1.0,
        ).result

        self.assertIn("Nearest: Bar 2 · Beat 2 · Subdivision 2", canonical[11])
        self.assertIn("Nearest: 4 subdivisions before Bar 1 · Beat 1", before[11])


class ArchitectureTests(unittest.TestCase):
    def test_folder_paths_remains_at_the_phase5a_route_and_node_boundaries(self) -> None:
        for name in ("providers.py", "runtime.py"):
            source = (REPO_ROOT / "score" / name).read_text(encoding="utf-8")
            self.assertNotIn("folder_paths", source)
        route_source = (REPO_ROOT / "score" / "routes.py").read_text(encoding="utf-8")
        self.assertIn("import folder_paths", route_source)

    def test_node_has_no_mutable_score_cache_and_provider_core_is_unchanged(self) -> None:
        source = NODE_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("score_cache", source.lower())
        self.assertNotIn("lru_cache", source)
        self.assertTrue((REPO_ROOT / "score" / "providers.py").is_file())


if __name__ == "__main__":
    unittest.main()
