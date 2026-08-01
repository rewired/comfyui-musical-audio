"""Static MusicalLoadAudioUI contract checks without ComfyUI dependencies."""

import ast
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_SOURCE = REPO_ROOT / "musical_audio_ui.py"

EXPECTED_WIDGETS = (
    "audio",
    "start_time",
    "end_time",
    "duration",
    "edit_mode",
    "bpm",
    "tempo_unit",
    "beats_per_bar",
    "beat_unit",
    "downbeat_offset",
    "fps",
    "start_bar",
    "start_beat",
    "start_subdivision",
    "duration_bars",
    "duration_beats",
    "duration_subdivisions",
    "subdivisions_per_beat",
    "snap_mode",
)
EXPECTED_RETURN_TYPES = (
    "AUDIO",
    "FLOAT",
    "STRING",
    "FLOAT",
    "FLOAT",
    "INT",
    "INT",
    "FLOAT",
    "FLOAT",
    "FLOAT",
    "FLOAT",
    "STRING",
)
EXPECTED_RETURN_NAMES = (
    "audio",
    "duration",
    "filename",
    "start_seconds",
    "end_seconds",
    "start_frame",
    "frame_count",
    "seconds_per_beat",
    "frames_per_beat",
    "seconds_per_bar",
    "frames_per_bar",
    "musical_position",
)


def _node_class() -> ast.ClassDef:
    tree = ast.parse(NODE_SOURCE.read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.ClassDef) and statement.name == "MusicalLoadAudioUI":
            return statement
    raise AssertionError("MusicalLoadAudioUI class was not found")


def _class_literal(name: str) -> object:
    for statement in _node_class().body:
        if isinstance(statement, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in statement.targets
            ):
                return ast.literal_eval(statement.value)
    raise AssertionError(f"{name} assignment was not found")


def _required_widget_names() -> tuple[str, ...]:
    input_types = next(
        statement
        for statement in _node_class().body
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and statement.name == "INPUT_TYPES"
    )
    return_node = next(
        statement
        for statement in ast.walk(input_types)
        if isinstance(statement, ast.Return)
    )
    if not isinstance(return_node.value, ast.Dict):
        raise AssertionError("INPUT_TYPES must return a dictionary literal")

    for key, value in zip(return_node.value.keys, return_node.value.values):
        if isinstance(key, ast.Constant) and key.value == "required":
            if not isinstance(value, ast.Dict):
                raise AssertionError("required inputs must be a dictionary literal")
            return tuple(ast.literal_eval(widget) for widget in value.keys)
    raise AssertionError("INPUT_TYPES has no required inputs dictionary")


class StaticNodeContractTests(unittest.TestCase):
    def test_required_widget_order_preserves_compatibility_prefix(self) -> None:
        widget_names = _required_widget_names()

        self.assertEqual(widget_names[:4], EXPECTED_WIDGETS[:4])
        self.assertEqual(widget_names, EXPECTED_WIDGETS)

    def test_all_output_types_are_in_required_order(self) -> None:
        self.assertEqual(_class_literal("RETURN_TYPES"), EXPECTED_RETURN_TYPES)

    def test_all_output_names_are_in_required_order(self) -> None:
        return_names = _class_literal("RETURN_NAMES")

        self.assertEqual(return_names[:3], ("audio", "duration", "filename"))
        self.assertEqual(return_names, EXPECTED_RETURN_NAMES)


if __name__ == "__main__":
    unittest.main()
