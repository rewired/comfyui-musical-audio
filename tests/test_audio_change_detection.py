"""Focused cache-fingerprint tests without a live ComfyUI installation."""

import builtins
import importlib.util
import math
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_SOURCE = REPO_ROOT / "musical_audio_ui.py"
SELECTED_AUDIO = "exports/song.wav"
RESOLVED_AUDIO = REPO_ROOT / "input" / ".." / "audio" / "song.wav"


class FakeTensor:
    pass


def _load_node_module() -> ModuleType:
    folder_paths = ModuleType("folder_paths")
    folder_paths.get_annotated_filepath = lambda _name: str(RESOLVED_AUDIO)  # type: ignore[attr-defined]

    torch = ModuleType("torch")
    torch.Tensor = FakeTensor  # type: ignore[attr-defined]
    torch.int16 = object()  # type: ignore[attr-defined]
    torch.int32 = object()  # type: ignore[attr-defined]

    av = ModuleType("av")
    audio_clip_plan = ModuleType("audio_clip_plan")
    audio_clip_plan.create_audio_clip_plan = object()  # type: ignore[attr-defined]

    spec = importlib.util.spec_from_file_location(
        "_musical_audio_ui_change_detection",
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


def _stat(*, size: int = 1024, mtime_ns: int = 2_000_000_003) -> SimpleNamespace:
    return SimpleNamespace(st_size=size, st_mtime_ns=mtime_ns)


class AudioChangeDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_node_module()
        self.node_class = self.module.MusicalLoadAudioUI

    def _existing_fingerprint(self, **metadata: int) -> tuple[object, ...]:
        with patch.object(self.module.os, "stat", return_value=_stat(**metadata)):
            return self.node_class.IS_CHANGED(SELECTED_AUDIO)

    def test_is_changed_exists_as_a_classmethod(self) -> None:
        source = (REPO_ROOT / "musical_audio_ui.py").read_text(encoding="utf-8")
        self.assertIn("prepare_score_repository_request", source)
        self.assertNotIn("build_score_fingerprint", source)
        descriptor = vars(self.node_class)["IS_CHANGED"]

        self.assertIsInstance(descriptor, classmethod)
        self.assertEqual(descriptor.__func__.__name__, "IS_CHANGED")

    def test_is_changed_accepts_unrelated_keyword_inputs(self) -> None:
        with patch.object(self.module.os, "stat", return_value=_stat()):
            fingerprint = self.node_class.IS_CHANGED(
                SELECTED_AUDIO,
                bpm=120.0,
                start_bar=7,
                unrelated="ignored",
            )

        self.assertEqual(fingerprint[0], "file")

    def test_none_returns_a_stable_deterministic_fingerprint(self) -> None:
        first = self.node_class.IS_CHANGED("none")
        second = self.node_class.IS_CHANGED("none", bpm=999)

        self.assertEqual(first[:2], ("none", "none"))
        self.assertIsInstance(first[-1], tuple)
        self.assertEqual(first, second)

    def test_same_existing_file_metadata_returns_equal_fingerprints(self) -> None:
        first = self._existing_fingerprint()
        second = self._existing_fingerprint()

        self.assertEqual(first, second)

    def test_changed_mtime_ns_changes_the_fingerprint(self) -> None:
        first = self._existing_fingerprint(mtime_ns=100)
        second = self._existing_fingerprint(mtime_ns=101)

        self.assertNotEqual(first, second)

    def test_changed_size_changes_the_fingerprint(self) -> None:
        first = self._existing_fingerprint(size=100)
        second = self._existing_fingerprint(size=101)

        self.assertNotEqual(first, second)

    def test_selected_filename_remains_in_the_fingerprint(self) -> None:
        fingerprint = self._existing_fingerprint()

        self.assertIn(SELECTED_AUDIO, fingerprint)
        self.assertEqual(fingerprint[1], SELECTED_AUDIO)

    def test_normalized_resolved_path_remains_in_the_fingerprint(self) -> None:
        fingerprint = self._existing_fingerprint()
        expected_path = os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(RESOLVED_AUDIO)))
        )

        self.assertEqual(fingerprint[2], expected_path)

    def test_path_resolution_exception_returns_a_stable_unresolved_fingerprint(self) -> None:
        with patch.object(
            self.module.folder_paths,
            "get_annotated_filepath",
            side_effect=RuntimeError("resolver unavailable"),
        ):
            first = self.node_class.IS_CHANGED(SELECTED_AUDIO)
            second = self.node_class.IS_CHANGED(SELECTED_AUDIO)

        self.assertEqual(first[:2], ("unresolved", SELECTED_AUDIO))
        self.assertIsInstance(first[-1], tuple)
        self.assertEqual(first, second)

    def test_missing_file_returns_a_stable_missing_fingerprint(self) -> None:
        with patch.object(self.module.os, "stat", side_effect=FileNotFoundError):
            first = self.node_class.IS_CHANGED(SELECTED_AUDIO)
            second = self.node_class.IS_CHANGED(SELECTED_AUDIO)

        self.assertEqual(first[0], "missing")
        self.assertEqual(first, second)
        self.assertEqual(first[1], SELECTED_AUDIO)

    def test_missing_to_existing_changes_the_fingerprint(self) -> None:
        audio_states = iter((FileNotFoundError(), _stat()))
        normalized_audio = os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(RESOLVED_AUDIO)))
        )

        def changing_stat(path: object) -> SimpleNamespace:
            if os.fspath(path) == normalized_audio:
                value = next(audio_states)
                if isinstance(value, Exception):
                    raise value
                return value
            raise FileNotFoundError

        with patch.object(self.module.os, "stat", side_effect=changing_stat):
            missing = self.node_class.IS_CHANGED(SELECTED_AUDIO)
            existing = self.node_class.IS_CHANGED(SELECTED_AUDIO)

        self.assertEqual(missing[0], "missing")
        self.assertEqual(existing[0], "file")
        self.assertNotEqual(missing, existing)

    def test_existing_to_missing_changes_the_fingerprint(self) -> None:
        audio_states = iter((_stat(), FileNotFoundError()))
        normalized_audio = os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(RESOLVED_AUDIO)))
        )

        def changing_stat(path: object) -> SimpleNamespace:
            if os.fspath(path) == normalized_audio:
                value = next(audio_states)
                if isinstance(value, Exception):
                    raise value
                return value
            raise FileNotFoundError

        with patch.object(self.module.os, "stat", side_effect=changing_stat):
            existing = self.node_class.IS_CHANGED(SELECTED_AUDIO)
            missing = self.node_class.IS_CHANGED(SELECTED_AUDIO)

        self.assertEqual(existing[0], "file")
        self.assertEqual(missing[0], "missing")
        self.assertNotEqual(existing, missing)

    def test_file_contents_are_never_opened_or_read(self) -> None:
        with (
            patch.object(self.module.os, "stat", return_value=_stat()),
            patch.object(builtins, "open", side_effect=AssertionError("file opened")),
        ):
            fingerprint = self.node_class.IS_CHANGED(SELECTED_AUDIO)

        self.assertEqual(fingerprint[0], "file")

    def test_fingerprint_is_not_a_boolean(self) -> None:
        fingerprints = (
            self.node_class.IS_CHANGED("none"),
            self._existing_fingerprint(),
        )

        for fingerprint in fingerprints:
            self.assertNotIsInstance(fingerprint, bool)
            self.assertIsInstance(fingerprint, tuple)

    def test_fingerprint_contains_no_nan(self) -> None:
        fingerprints = (
            self.node_class.IS_CHANGED("none"),
            self._existing_fingerprint(),
        )

        for fingerprint in fingerprints:
            self.assertFalse(
                any(isinstance(value, float) and math.isnan(value) for value in fingerprint)
            )

    def test_keyboard_interrupt_and_system_exit_are_not_swallowed(self) -> None:
        for signal in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(source="resolution", signal=type(signal).__name__):
                with patch.object(
                    self.module.folder_paths,
                    "get_annotated_filepath",
                    side_effect=signal,
                ):
                    with self.assertRaises(type(signal)):
                        self.node_class.IS_CHANGED(SELECTED_AUDIO)

            with self.subTest(source="stat", signal=type(signal).__name__):
                with patch.object(self.module.os, "stat", side_effect=signal):
                    with self.assertRaises(type(signal)):
                        self.node_class.IS_CHANGED(SELECTED_AUDIO)

    def test_is_changed_does_not_mutate_inputs(self) -> None:
        audio = SELECTED_AUDIO
        unrelated = {
            "score_file": "scores/song.score.json",
            "start_time": [1.25],
            "mode": {"value": "Musical"},
        }
        expected = {
            "score_file": "scores/song.score.json",
            "start_time": [1.25],
            "mode": {"value": "Musical"},
        }

        with patch.object(self.module.os, "stat", return_value=_stat()):
            self.node_class.IS_CHANGED(audio, **unrelated)

        self.assertEqual(audio, SELECTED_AUDIO)
        self.assertEqual(unrelated, expected)

    def test_repeated_calls_create_no_global_cache_state(self) -> None:
        global_names_before = set(vars(self.module))
        cache_names_before = {
            name for name in global_names_before if "cache" in name.lower()
        }

        with patch.object(self.module.os, "stat", return_value=_stat()):
            for _ in range(5):
                self.node_class.IS_CHANGED(SELECTED_AUDIO)

        self.assertEqual(set(vars(self.module)), global_names_before)
        self.assertEqual(
            {name for name in vars(self.module) if "cache" in name.lower()},
            cache_names_before,
        )


if __name__ == "__main__":
    unittest.main()
