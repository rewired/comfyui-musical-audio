"""Static MusicalLoadAudioUI contract checks without ComfyUI dependencies."""

import ast
import hashlib
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_SOURCE = REPO_ROOT / "musical_audio_ui.py"
INIT_SOURCE = REPO_ROOT / "__init__.py"
SCORE_SUBSYSTEM = REPO_ROOT / "SCORE_SUBSYSTEM.md"
FRONTEND_SOURCE = REPO_ROOT / "js" / "musical_audio_ui.js"
FRONTEND_STYLESHEET = REPO_ROOT / "js" / "musical_audio_ui.css"
AUDIO_TRANSPORT_SOURCE = REPO_ROOT / "js" / "audio_transport.js"
PLAYHEAD_SOURCE = REPO_ROOT / "js" / "playhead.js"

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
    "beats_per_bar": ("INT", {"default": 4, "min": 1, "max": 64, "socketless": True}),
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


def _module_literal(name: str) -> object:
    tree = ast.parse(NODE_SOURCE.read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in statement.targets
            ):
                return ast.literal_eval(statement.value)
    raise AssertionError(f"{name} module assignment was not found")


def _static_literal(node: ast.expr) -> object:
    if isinstance(node, ast.Name):
        return _module_literal(node.id)
    if isinstance(node, ast.Tuple):
        return tuple(_static_literal(element) for element in node.elts)
    if isinstance(node, ast.List):
        return [_static_literal(element) for element in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _static_literal(key): _static_literal(value)
            for key, value in zip(node.keys, node.values)
        }
    return ast.literal_eval(node)


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
    return _static_literal(_input_group("required")[name])


def _input_options(group_name: str, name: str) -> dict[str, object]:
    spec = _input_group(group_name)[name]
    if not isinstance(spec, ast.Tuple) or len(spec.elts) < 2:
        return {}
    options = _static_literal(spec.elts[1])
    if not isinstance(options, dict):
        raise AssertionError(f"{name} input options must be a dictionary")
    return options


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

    def test_local_beat_widgets_use_the_named_practical_limit(self) -> None:
        self.assertEqual(_module_literal("MAX_LOCAL_BEATS_PER_BAR"), 64)
        self.assertEqual(
            _required_input_spec("beats_per_bar"),
            ("INT", {"default": 4, "min": 1, "max": 64, "socketless": True}),
        )
        self.assertEqual(
            _required_input_spec("start_beat"),
            ("INT", {"default": 1, "min": 1, "max": 64, "socketless": True}),
        )
        self.assertEqual(
            ast.literal_eval(_input_group("optional")["beats_per_bar_input"]),
            ("INT", {"forceInput": True}),
        )

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

    def test_validate_inputs_only_accepts_audio_and_returns_true(self) -> None:
        method = _class_method("VALIDATE_INPUTS")

        self.assertEqual(
            tuple(argument.arg for argument in method.args.args),
            ("cls", "audio"),
        )
        self.assertIsNone(method.args.vararg)
        self.assertIsNone(method.args.kwarg)
        self.assertEqual(method.args.kwonlyargs, [])
        self.assertTrue(
            any(
                isinstance(decorator, ast.Name) and decorator.id == "classmethod"
                for decorator in method.decorator_list
            )
        )
        returns = [node for node in ast.walk(method) if isinstance(node, ast.Return)]
        self.assertEqual(len(returns), 1)
        self.assertIs(ast.literal_eval(returns[0].value), True)

    def test_is_changed_is_a_classmethod_accepting_audio_and_extra_keywords(self) -> None:
        method = _class_method("IS_CHANGED")

        self.assertEqual(
            tuple(argument.arg for argument in method.args.args),
            ("cls", "audio"),
        )
        self.assertIsNotNone(method.args.kwarg)
        self.assertEqual(method.args.kwarg.arg, "_kwargs")
        self.assertTrue(
            any(
                isinstance(decorator, ast.Name) and decorator.id == "classmethod"
                for decorator in method.decorator_list
            )
        )

    def test_node_source_contains_no_bare_except_handler(self) -> None:
        tree = ast.parse(NODE_SOURCE.read_text(encoding="utf-8"))
        bare_handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and node.type is None
        ]

        self.assertEqual(bare_handlers, [])

    def test_duration_and_score_free_node_contract_remain_unchanged(self) -> None:
        all_input_names = (*_input_group("required"), *_input_group("optional"))
        return_names = _class_literal("RETURN_NAMES")

        self.assertIn("duration", _input_group("required"))
        self.assertIn("duration", return_names)
        self.assertNotIn("diagnostics", return_names)
        self.assertFalse(any("score" in name.lower() for name in all_input_names))
        self.assertNotIn("score_file", all_input_names)
        self.assertNotIn("stems_dir", all_input_names)
        self.assertFalse(any("score" in name.lower() for name in return_names))

        node_tree = ast.parse(NODE_SOURCE.read_text(encoding="utf-8"))
        class_names = [
            statement.name
            for statement in node_tree.body
            if isinstance(statement, ast.ClassDef)
        ]
        self.assertEqual(class_names, ["MusicalLoadAudioUI"])

        init_source = INIT_SOURCE.read_text(encoding="utf-8")
        self.assertIn('"MusicalLoadAudioUI": MusicalLoadAudioUI', init_source)
        self.assertIn(
            '"MusicalLoadAudioUI": "Load Audio UI — Musical Grid"',
            init_source,
        )

    def test_frozen_score_document_preserves_core_markers(self) -> None:
        self.assertTrue(SCORE_SUBSYSTEM.is_file())
        source = SCORE_SUBSYSTEM.read_text(encoding="utf-8")
        self.assertEqual(
            hashlib.sha256(SCORE_SUBSYSTEM.read_bytes()).hexdigest().upper(),
            "9539B0E9149D45A806C54765835C4F759E96125C470B094CE72FF0FF8E6A036B",
        )

        for marker in (
            "Status: Architecture frozen for implementation",
            "Revision: 1",
            "604e5ab",
            "ScoreFormat",
            "ProviderKind",
            "ResolvedScore",
            "audio_seconds_at_tick_zero",
            "Gerundet wird absolut, nie kumulativ.",
            "ProviderResult",
            "end_frame_exclusive",
            "/comfyui-musical-audio/score",
            "Feature-Gate für variable Meter",
            "has_variable_meter",
            "has_midbar_meter_change",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

        for number in range(1, 11):
            with self.subTest(semantic_contract=number):
                self.assertRegex(source, rf"(?m)^### S{number} — ")
        for number in range(1, 7):
            with self.subTest(architectural_decision=number):
                self.assertRegex(source, rf"(?m)^### {number}\. ")

        self.assertNotIn("ScoreSource", source)
        self.assertNotIn("score_source", source)

    def test_audio_transport_is_node_local_and_owns_programmatic_seeking(self) -> None:
        source = FRONTEND_SOURCE.read_text(encoding="utf-8")
        transport_source = AUDIO_TRANSPORT_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            'import { createAudioTransport } from "./audio_transport.js";',
            source,
        )
        self.assertEqual(source.count('document.createElement("audio")'), 1)
        self.assertIn("const audioTransport = createAudioTransport(audioEl);", source)
        self.assertIn("node._musicalAudioTransport = audioTransport;", source)
        self.assertIn("node.getMusicalAudioTransport = () =>", source)
        self.assertIn("node.destroyMusicalAudioTransport = () =>", source)
        for raw_property in (
            "node.audioEl",
            "node._audioEl",
            "node.audioElement",
            "node._musicalAudioElement",
        ):
            with self.subTest(raw_property=raw_property):
                self.assertNotIn(raw_property, source)

        self.assertIsNone(re.search(r"audioEl\.currentTime\s*=", source))
        self.assertGreaterEqual(source.count("audioTransport.seek("), 3)
        seek_helper = source.split("const seekToSelectionStart = (state) => {", 1)[1].split(
            "const refreshUI",
            1,
        )[0]
        timeupdate = source.split('audioEl.addEventListener("timeupdate", () => {', 1)[1].split(
            'audioEl.addEventListener("play"',
            1,
        )[0]
        play_handler = source.split('audioEl.addEventListener("play", () => {', 1)[1].split(
            'audioEl.addEventListener("pause"',
            1,
        )[0]
        self.assertIn("audioTransport.seek(", seek_helper)
        self.assertIn("audioTransport.seek(state.start)", timeupdate)
        self.assertIn("audioTransport.seek(state.start)", play_handler)

        on_removed = source.split("nodeType.prototype.onRemoved = function () {", 1)[1].split(
            "nodeType.prototype.onConfigure",
            1,
        )[0]
        cleanup_calls = (
            "destroyMusicalAudioWaveformRenderer",
            "destroyMusicalAudioWaveformData",
            "destroyMusicalAudioMetronome",
            "destroyMusicalAudioTransport",
            "cancelMusicalAudioResizeHeightSync",
            "clearMusicalAudioSyncFeedback",
        )
        self.assertEqual(
            sorted(on_removed.index(call) for call in cleanup_calls),
            [on_removed.index(call) for call in cleanup_calls],
        )

        for forbidden_dependency in (
            "../../scripts/app.js",
            "../../scripts/api.js",
            "waveform_",
            "metronome",
            "Vue",
            "document.",
            "fetch(",
            "localStorage",
            "console.",
        ):
            with self.subTest(forbidden_dependency=forbidden_dependency):
                self.assertNotIn(forbidden_dependency, transport_source)

    def test_inline_playhead_dom_runtime_and_cleanup_are_isolated(self) -> None:
        source = FRONTEND_SOURCE.read_text(encoding="utf-8")
        playhead_source = PLAYHEAD_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            'import { createPlayheadController } from "./playhead.js";',
            source,
        )
        creation = 'const playhead = makeElement("div", "musical-audio-ui__playhead");'
        self.assertEqual(source.count(creation), 1)
        playhead_dom = source.split(creation, 1)[1].split("timelineContent.appendChild", 1)[0]
        self.assertIn('playhead.setAttribute("aria-hidden", "true")', playhead_dom)
        self.assertNotIn("tabIndex", playhead_dom)
        self.assertIn("sliderBox.append(waveformCanvas, fill);", playhead_dom)
        self.assertIn("sliderBox.append(playhead);", playhead_dom)
        self.assertLess(
            playhead_dom.index("sliderBox.append(playhead);"),
            playhead_dom.index("startHandle"),
        )
        self.assertLess(
            playhead_dom.index("sliderBox.append(playhead);"),
            playhead_dom.index("endHandle"),
        )
        self.assertNotIn("playhead.addEventListener", source)

        self.assertIn("playheadAnimationFrameId: null", source)
        self.assertIn("runtime: waveformRenderer", source)
        self.assertIn("node.refreshMusicalAudioPlayhead = () =>", source)
        self.assertIn("node.clearMusicalAudioPlayhead = () =>", source)
        renderer_cleanup = source.split(
            "node.destroyMusicalAudioWaveformRenderer = () => {",
            1,
        )[1].split("if (typeof ResizeObserver", 1)[0]
        self.assertLess(
            renderer_cleanup.index("runtime.playheadController?.destroy()"),
            renderer_cleanup.index("runtime.canvas.width = 0"),
        )
        self.assertIn("node.clearMusicalAudioPlayhead();", source)

        for forbidden_operation in (
            "scheduleMusicalAudioWaveformRender",
            "renderMusicalAudioWaveform",
            "setDirtyCanvas",
            "setInterval",
            "setTimeout",
            "addEventListener",
            "wheel",
            "click",
            "TimeAxis",
            "Vue",
        ):
            with self.subTest(forbidden_operation=forbidden_operation):
                self.assertNotIn(forbidden_operation, playhead_source)

    def test_playhead_css_preserves_timeline_geometry_and_layer_order(self) -> None:
        stylesheet = FRONTEND_STYLESHEET.read_text(encoding="utf-8")

        def rule(selector: str) -> str:
            return stylesheet.split(f"{selector} {{", 1)[1].split("}", 1)[0]

        waveform_rule = rule(".musical-audio-ui .musical-audio-ui__waveform")
        selection_rule = rule(".musical-audio-ui .musical-audio-ui__selection")
        playhead_rule = rule(".musical-audio-ui .musical-audio-ui__playhead")
        visible_rule = rule(
            ".musical-audio-ui .musical-audio-ui__playhead.is-visible",
        )
        handle_rule = rule(".musical-audio-ui .musical-audio-ui__handle")

        self.assertIn("z-index: 0", waveform_rule)
        self.assertIn("z-index: 1", selection_rule)
        self.assertIn("z-index: 2", playhead_rule)
        self.assertIn("z-index: 3", handle_rule)
        for declaration in (
            "position: absolute",
            "top: 0",
            "bottom: 0",
            "left: var(--mau-playhead-position)",
            "width: 2px",
            "background: #f3f4f6",
            "transform: translateX(-50%)",
            "pointer-events: none",
            "user-select: none",
            "visibility: hidden",
            "opacity: 0",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, playhead_rule)
        self.assertNotIn("transition", playhead_rule)
        self.assertIn("visibility: visible", visible_rule)
        self.assertIn("opacity: 0.95", visible_rule)
        self.assertIn("--mau-timeline-height: 80px", stylesheet)
        timeline_rule = rule(".musical-audio-ui .musical-audio-ui__timeline")
        self.assertIn("height: var(--mau-timeline-height)", timeline_rule)

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
