"""Static MusicalLoadAudioUI contract checks without ComfyUI dependencies."""

import ast
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_SOURCE = REPO_ROOT / "musical_audio_ui.py"
FRONTEND_SOURCE = REPO_ROOT / "js" / "musical_audio_ui.js"
FRONTEND_STYLESHEET = REPO_ROOT / "js" / "musical_audio_ui.css"

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
TARGET_WIDGET_INPUTS = (
    "bpm",
    "tempo_unit",
    "fps",
    "beats_per_bar",
    "beat_unit",
    "subdivisions_per_beat",
    "downbeat_offset",
)
EXTERNAL_TIMING_INPUTS = {
    "bpm": ("bpm_input", "FLOAT"),
    "tempo_unit": ("tempo_unit_input", "STRING"),
    "fps": ("fps_input", "FLOAT"),
    "beats_per_bar": ("beats_per_bar_input", "INT"),
    "beat_unit": ("beat_unit_input", "INT"),
    "subdivisions_per_beat": ("subdivisions_per_beat_input", "INT"),
    "downbeat_offset": ("downbeat_offset_input", "FLOAT"),
}
EXPECTED_TARGET_WIDGET_SPECS = {
    "bpm": ("FLOAT", {"default": 120.0, "min": 0.01, "step": 0.01, "socketless": True}),
    "tempo_unit": (["Quarter", "Eighth", "Dotted Quarter"], {"default": "Quarter", "socketless": True}),
    "fps": ("FLOAT", {"default": 24.0, "min": 0.001, "step": 0.001, "socketless": True}),
    "beats_per_bar": ("INT", {"default": 4, "min": 1, "socketless": True}),
    "beat_unit": ("INT", {"default": 4, "min": 1, "socketless": True}),
    "subdivisions_per_beat": ("INT", {"default": 4, "min": 1, "socketless": True}),
    "downbeat_offset": (
        "FLOAT",
        {
            "default": 0.0,
            "min": -100000.0,
            "max": 100000.0,
            "step": 0.001,
            "socketless": True,
        },
    ),
}
ORIGINAL_RETURN_TYPES = (
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
ORIGINAL_RETURN_NAMES = (
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
EXPECTED_RETURN_TYPES = ORIGINAL_RETURN_TYPES + ("FLOAT", "FLOAT")
EXPECTED_RETURN_NAMES = ORIGINAL_RETURN_NAMES + ("bpm", "fps")


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


def _class_method(name: str) -> ast.FunctionDef:
    for statement in _node_class().body:
        if isinstance(statement, ast.FunctionDef) and statement.name == name:
            return statement
    raise AssertionError(f"{name} method was not found")


def _input_group(group_name: str) -> dict[str, ast.expr]:
    input_types = _class_method("INPUT_TYPES")
    return_node = next(
        statement
        for statement in ast.walk(input_types)
        if isinstance(statement, ast.Return)
    )
    if not isinstance(return_node.value, ast.Dict):
        raise AssertionError("INPUT_TYPES must return a dictionary literal")

    for key, value in zip(return_node.value.keys, return_node.value.values):
        if isinstance(key, ast.Constant) and key.value == group_name:
            if not isinstance(value, ast.Dict):
                raise AssertionError(f"{group_name} inputs must be a dictionary literal")
            return {
                ast.literal_eval(widget): spec
                for widget, spec in zip(value.keys, value.values)
            }
    raise AssertionError(f"INPUT_TYPES has no {group_name} inputs dictionary")


def _required_widget_names() -> tuple[str, ...]:
    return tuple(_input_group("required"))


def _required_input_spec(name: str) -> object:
    return ast.literal_eval(_input_group("required")[name])


def _input_options(group_name: str, name: str) -> dict[str, object]:
    spec = _input_group(group_name)[name]
    if not isinstance(spec, ast.Tuple) or len(spec.elts) < 2:
        return {}
    return ast.literal_eval(spec.elts[1])


class StaticNodeContractTests(unittest.TestCase):
    def test_required_widget_order_preserves_compatibility_prefix(self) -> None:
        widget_names = _required_widget_names()

        self.assertEqual(widget_names[:4], EXPECTED_WIDGETS[:4])
        self.assertEqual(widget_names, EXPECTED_WIDGETS)

    def test_all_output_types_are_in_required_order(self) -> None:
        return_types = _class_literal("RETURN_TYPES")

        self.assertEqual(len(return_types), 14)
        self.assertEqual(return_types[:12], ORIGINAL_RETURN_TYPES)
        self.assertEqual(return_types[12:], ("FLOAT", "FLOAT"))
        self.assertEqual(return_types, EXPECTED_RETURN_TYPES)

    def test_all_output_names_are_in_required_order(self) -> None:
        return_names = _class_literal("RETURN_NAMES")

        self.assertEqual(len(return_names), 14)
        self.assertEqual(return_names[:12], ORIGINAL_RETURN_NAMES)
        self.assertEqual(return_names[12:], ("bpm", "fps"))
        self.assertEqual(return_names, EXPECTED_RETURN_NAMES)

    def test_all_local_widgets_are_socketless(self) -> None:
        for widget_name in EXPECTED_WIDGETS:
            with self.subTest(widget_name=widget_name):
                self.assertIs(
                    _input_options("required", widget_name).get("socketless"),
                    True,
                )

        self.assertEqual(
            ast.literal_eval(_input_group("optional")["audioUI"]),
            ("AUDIO_UI", {"socketless": True}),
        )

    def test_target_widget_specs_are_unchanged_except_for_socketless_metadata(self) -> None:
        for widget_name, expected_spec in EXPECTED_TARGET_WIDGET_SPECS.items():
            with self.subTest(widget_name=widget_name):
                self.assertEqual(_required_input_spec(widget_name), expected_spec)

    def test_downbeat_offset_has_explicit_signed_local_range_only(self) -> None:
        input_type, options = _required_input_spec("downbeat_offset")

        self.assertEqual(input_type, "FLOAT")
        self.assertEqual(
            options,
            {
                "default": 0.0,
                "min": -100000.0,
                "max": 100000.0,
                "step": 0.001,
                "socketless": True,
            },
        )
        self.assertEqual(
            _required_widget_names().index("downbeat_offset"),
            EXPECTED_WIDGETS.index("downbeat_offset"),
        )

        external_spec = ast.literal_eval(
            _input_group("optional")["downbeat_offset_input"],
        )
        self.assertEqual(external_spec, ("FLOAT", {"forceInput": True}))
        self.assertNotIn("min", external_spec[1])
        self.assertNotIn("max", external_spec[1])

    def test_exact_external_timing_inputs_are_optional_force_inputs(self) -> None:
        optional_inputs = _input_group("optional")

        self.assertEqual(
            tuple(optional_inputs),
            ("audioUI", *(external_name for external_name, _ in EXTERNAL_TIMING_INPUTS.values())),
        )
        for external_name, expected_type in EXTERNAL_TIMING_INPUTS.values():
            with self.subTest(external_name=external_name):
                spec = ast.literal_eval(optional_inputs[external_name])
                self.assertEqual(spec, (expected_type, {"forceInput": True}))
                self.assertNotIn("default", spec[1])
                self.assertNotIn("defaultInput", spec[1])

    def test_no_duplicate_override_inputs_were_added(self) -> None:
        input_names = (*_input_group("required"), *_input_group("optional"))

        self.assertEqual(
            input_names,
            (
                *EXPECTED_WIDGETS,
                "audioUI",
                *(external_name for external_name, _ in EXTERNAL_TIMING_INPUTS.values()),
            ),
        )
        self.assertFalse(any(name.endswith(("_override", "_external")) for name in input_names))

    def test_load_audio_uses_explicit_optional_parameters(self) -> None:
        method = _class_method("load_audio")
        positional_names = tuple(argument.arg for argument in method.args.args)

        self.assertIsNone(method.args.kwarg)
        self.assertEqual(
            positional_names[-8:],
            (
                "audioUI",
                *(external_name for external_name, _ in EXTERNAL_TIMING_INPUTS.values()),
            ),
        )

    def test_frontend_maps_local_controls_to_backend_inputs_without_socket_creation(self) -> None:
        source = FRONTEND_SOURCE.read_text(encoding="utf-8")
        mapping_match = re.search(
            r"const MUSICAL_TIMING_INPUT_MAP = Object\.freeze\(\{(?P<body>.*?)\n\}\);",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(mapping_match)
        mapping = dict(
            re.findall(
                r'^\s*([a-z_]+): "([a-z_]+)",$',
                mapping_match.group("body"),
                re.MULTILINE,
            )
        )

        self.assertEqual(
            mapping,
            {
                widget_name: external_name
                for widget_name, (external_name, _type) in EXTERNAL_TIMING_INPUTS.items()
            },
        )

        label_match = re.search(
            r"const EXTERNAL_INPUT_LABELS = Object\.freeze\(\{(?P<body>.*?)\n\}\);",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(label_match)
        labels = dict(
            re.findall(
                r'^\s*([a-z_]+): "([^"]+)",$',
                label_match.group("body"),
                re.MULTILINE,
            )
        )
        self.assertEqual(
            labels,
            {
                "bpm_input": "BPM",
                "tempo_unit_input": "Tempo unit",
                "fps_input": "FPS",
                "beats_per_bar_input": "Beats / bar",
                "beat_unit_input": "Beat unit",
                "subdivisions_per_beat_input": "Grid / beat",
                "downbeat_offset_input": "Downbeat offset",
            },
        )

        label_helper = source.split(
            "function applyMusicalAudioInputLabels(node) {",
            1,
        )[1].split("function matchingMusicalAudioInput", 1)[0]
        self.assertIn("for (const input of node.inputs || [])", label_helper)
        self.assertIn("input.label = EXTERNAL_INPUT_LABELS[input.name]", label_helper)
        self.assertNotIn("input.name =", label_helper)
        self.assertNotIn("localized_name", label_helper)

        configure_wrapper = source.split(
            "nodeType.prototype.onConfigure = function () {",
            1,
        )[1].split("nodeType.prototype.onConnectionsChange", 1)[0]
        created_wrapper = source.split(
            "nodeType.prototype.onNodeCreated = function () {",
            1,
        )[1].split("const handleFileUpload", 1)[0]
        self.assertLess(
            configure_wrapper.index("onConfigure.apply(this, arguments)"),
            configure_wrapper.index("applyMusicalAudioInputLabels(this)"),
        )
        self.assertLess(
            created_wrapper.index("onNodeCreated.apply(this, arguments)"),
            created_wrapper.index("applyMusicalAudioInputLabels(node)"),
        )

        stylesheet = FRONTEND_STYLESHEET.read_text(encoding="utf-8")
        self.assertIn(
            'const isLegacyRenderer = !window.LiteGraph || !window.LiteGraph.vueNodesMode;',
            source,
        )
        self.assertIn(
            'if (isLegacyRenderer) container.classList.add("is-legacy-renderer");',
            source,
        )
        self.assertIn("    padding: 10px;", stylesheet)
        self.assertNotIn("--mau-panel-padding", stylesheet)
        self.assertNotIn("padding-bottom:", stylesheet)
        status_rule = stylesheet.split(
            ".musical-audio-ui .musical-audio-ui__status {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("overflow: hidden", status_rule)
        self.assertIn("line-height: 1.25", status_rule)
        self.assertIn("white-space: nowrap", status_rule)
        self.assertIn("text-overflow: ellipsis", status_rule)
        legacy_status_rule = stylesheet.split(
            ".musical-audio-ui.is-legacy-renderer .musical-audio-ui__status {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("display: block", legacy_status_rule)
        self.assertIn("flex-shrink: 0", legacy_status_rule)
        self.assertIn("min-height: 16px", legacy_status_rule)
        self.assertIn("box-sizing: border-box", legacy_status_rule)
        self.assertIn("line-height: 16px", legacy_status_rule)

        resize_wrapper = source.split(
            "nodeType.prototype.onResize = function () {",
            1,
        )[1].split("nodeType.prototype.onRemoved", 1)[0]
        self.assertIn("this.syncMusicalAudioWidth()", resize_wrapper)
        self.assertIn("this.scheduleMusicalAudioResizeHeightSync()", resize_wrapper)
        self.assertIn("RESIZE_HEIGHT_SYNC_DELAY_MS = 120", source)

        pointer_move = source.split(
            'sliderBox.addEventListener("pointermove", (event) => {',
            1,
        )[1].split("const finishPointer", 1)[0]
        self.assertNotIn("setSize", pointer_move)
        self.assertNotIn("scheduleMusicalAudioHeightSync", pointer_move)

        playback_update = source.split(
            'audioEl.addEventListener("timeupdate", () => {',
            1,
        )[1].split('container.addEventListener("dragover"', 1)[0]
        self.assertNotIn("setSize", playback_update)
        self.assertNotIn("scheduleMusicalAudioHeightSync", playback_update)

        for obsolete_api in (
            ".addInput(",
            "convertWidgetToInput",
            "setWidgetConfig",
            "ensureMusicalAudioInputs",
        ):
            with self.subTest(obsolete_api=obsolete_api):
                self.assertNotIn(obsolete_api, source)

        self.assertIn(
            "input?.link != null && graphContainsLink(node.graph, input.link)",
            source,
        )

    def test_connection_wrapper_only_refreshes_external_presentation(self) -> None:
        source = FRONTEND_SOURCE.read_text(encoding="utf-8")
        wrapper = source.split(
            "nodeType.prototype.onConnectionsChange = function () {",
            1,
        )[1].split("nodeType.prototype.onNodeCreated = function () {", 1)[0]

        self.assertIn("onConnectionsChange.apply(this, arguments)", wrapper)
        self.assertIn("refreshMusicalAudioExternalState", wrapper)
        self.assertNotIn(".value", wrapper)
        self.assertNotIn("refreshMusicalAudioUI", wrapper)
        self.assertNotIn("scheduleMusicalAudioResizeHeightSync", wrapper)


if __name__ == "__main__":
    unittest.main()
