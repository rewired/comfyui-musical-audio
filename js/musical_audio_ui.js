import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
    durationFieldsToSubdivisionCount,
    frameToNearestSubdivision,
    musicalPositionToSubdivisionIndex,
    roundHalfAwayFromZero,
    secondsToNearestSubdivision,
    snapToBar,
    snapToBeat,
    snapToSubdivision,
    snapToVideoFrame,
    subdivisionCountToDurationFields,
    subdivisionIndexToMusicalPosition,
    subdivisionIndexToSeconds,
    timingGrid,
} from "./musical_grid.js";

const HIDDEN_WIDGETS = [
    "audioUI",
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
];

const TEMPO_UNITS = ["Quarter", "Eighth", "Dotted Quarter"];
const SNAP_MODES = ["Off", "Bar", "Beat", "Subdivision", "Video Frame"];
const STORAGE_PRECISION = 1_000_000;
const STYLESHEET_ID = "comfyui-musical-audio-styles";

function ensureStylesheet() {
    if (document.getElementById(STYLESHEET_ID)) return;

    const link = document.createElement("link");
    link.id = STYLESHEET_ID;
    link.rel = "stylesheet";
    link.href = new URL("./musical_audio_ui.css", import.meta.url).href;
    document.head.appendChild(link);
}

ensureStylesheet();

function hideWidget(widget) {
    if (!widget) return;
    widget.hidden = true;
    if (!widget.options) widget.options = {};
    widget.options.hidden = true;

    if (!window.LiteGraph || !window.LiteGraph.vueNodesMode) {
        widget.computeSize = () => [0, -4];
        if (!widget._hiddenDrawHooked) {
            widget._origDraw = Object.prototype.hasOwnProperty.call(widget, "draw")
                ? widget.draw
                : undefined;
            widget._hiddenDrawHooked = true;
        }
        widget.draw = () => {};
    }
    if (widget.element) widget.element.style.display = "none";
}

function clamp(value, minimum, maximum) {
    return Math.min(Math.max(value, minimum), maximum);
}

function finiteNumber(value, fallback = 0) {
    const parsed = typeof value === "number" ? value : Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function integerValue(value, fallback = 0) {
    const parsed = finiteNumber(value, fallback);
    const integer = Math.trunc(parsed);
    return Number.isSafeInteger(integer) ? integer : fallback;
}

function storedNumber(value) {
    if (!Number.isFinite(value)) return null;
    const scaled = value * STORAGE_PRECISION;
    if (!Number.isFinite(scaled)) return null;
    const rounded = roundHalfAwayFromZero(scaled) / STORAGE_PRECISION;
    return Object.is(rounded, -0) ? 0 : rounded;
}

function makeElement(tag, className = "", text = "") {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
}

function compactInput(type = "number") {
    const input = makeElement("input", "musical-audio-ui__input");
    input.type = type;
    return input;
}

function compactSelect(values) {
    const select = makeElement("select", "musical-audio-ui__select");
    for (const value of values) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
    }
    return select;
}

function makeField(labelText, control) {
    const field = makeElement("label", "musical-audio-ui__field");
    field.appendChild(makeElement("span", "musical-audio-ui__field-label", labelText));
    field.appendChild(control);
    return field;
}

function formatInputValue(value) {
    if (!Number.isFinite(value)) return "0";
    const stored = storedNumber(value);
    return stored === null ? String(value) : String(stored);
}

function plural(value, singular, pluralForm = `${singular}s`) {
    return `${value} ${value === 1 ? singular : pluralForm}`;
}

function durationLabel(fields) {
    const parts = [];
    if (fields.bars) parts.push(plural(fields.bars, "Bar"));
    if (fields.beats) parts.push(plural(fields.beats, "Beat"));
    if (fields.subdivisions) parts.push(plural(fields.subdivisions, "Subdivision"));
    return parts.length ? parts.join(" + ") : "0 Beats";
}

app.registerExtension({
    name: "comfyui-musical-audio.MusicalLoadAudioUI",
    async beforeRegisterNodeDef(nodeType, nodeData, appInstance) {
        if (nodeData.name !== "MusicalLoadAudioUI") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        const onDrawBackground = nodeType.prototype.onDrawBackground;
        const onConfigure = nodeType.prototype.onConfigure;
        const onResize = nodeType.prototype.onResize;

        nodeType.prototype.onDrawBackground = function () {
            if (onDrawBackground) onDrawBackground.apply(this, arguments);
        };

        nodeType.prototype.onResize = function () {
            const result = onResize ? onResize.apply(this, arguments) : undefined;
            // Legacy DOM widgets re-enter onResize after setSize, so resize only follows width.
            if (this.syncMusicalAudioWidth) this.syncMusicalAudioWidth();
            return result;
        };

        nodeType.prototype.onConfigure = function () {
            this._configuringMusicalAudio = true;
            const result = onConfigure ? onConfigure.apply(this, arguments) : undefined;
            setTimeout(() => {
                this._configuringMusicalAudio = false;
                if (this.syncMusicalAudioWidth) this.syncMusicalAudioWidth();
                if (this.refreshMusicalAudioUI) {
                    this.refreshMusicalAudioUI(true);
                }
            }, 0);
            return result;
        };

        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            node._initializingMusicalAudio = true;
            node._shouldResetSecondsTrim = false;

            // Prevent ComfyUI V1/V2 image-preview paths from replacing the audio UI.
            Object.defineProperty(node, "imgs", {
                get() { return undefined; },
                set() {},
                configurable: true,
            });

            const handleFileUpload = async (file) => {
                if (!file || (!file.type.startsWith("audio/") && !file.type.startsWith("video/"))) {
                    return false;
                }
                try {
                    const body = new FormData();
                    body.append("image", file);
                    body.append("type", "input");
                    const response = await api.fetchApi("/upload/image", { method: "POST", body });
                    if (response.status === 200) {
                        const data = await response.json();
                        const audioWidget = node.widgets?.find((candidate) => candidate.name === "audio");
                        if (audioWidget) {
                            node._shouldResetSecondsTrim = true;
                            audioWidget.value = data.name;
                            const values = audioWidget.options?.values;
                            if (Array.isArray(values) && !values.includes(data.name)) values.push(data.name);
                            if (audioWidget.callback) audioWidget.callback(data.name);
                            appInstance.graph?.setDirtyCanvas(true, false);
                        }
                    }
                } catch (error) {
                    console.error("Error uploading dragged audio file:", error);
                }
                return true;
            };

            node.onDragOver = function (event) {
                if (event.dataTransfer?.types?.includes("Files")) {
                    event.preventDefault();
                    return true;
                }
                return false;
            };

            node.onDragDrop = function (event) {
                const file = event.dataTransfer?.files?.[0];
                if (file && (file.type.startsWith("audio/") || file.type.startsWith("video/"))) {
                    handleFileUpload(file);
                    return true;
                }
                return false;
            };

            const initialEditMode = node.widgets?.find(
                (candidate) => candidate.name === "edit_mode",
            )?.value;
            const container = makeElement("div", "musical-audio-ui");
            container.dataset.mode = initialEditMode === "Musical" ? "musical" : "seconds";

            // 1. Filename and selection summary.
            const playerTop = makeElement("div", "musical-audio-ui__header");
            const playerTitle = makeElement(
                "span",
                "musical-audio-ui__title",
                "No audio selected",
            );
            const trimLength = makeElement(
                "span",
                "musical-audio-ui__summary",
                "Trimmed: 0.000 s",
            );
            playerTop.append(playerTitle, trimLength);
            container.appendChild(playerTop);

            // 2. HTML audio player. The fixed wrapper keeps native controls out of flex shrink.
            const playerWrapper = makeElement("div", "musical-audio-ui__player");
            const audioEl = document.createElement("audio");
            audioEl.controls = true;
            audioEl.className = "musical-audio-ui__player-element";
            playerWrapper.appendChild(audioEl);
            container.appendChild(playerWrapper);

            // 3. Mode and snap toolbar.
            const toolbar = makeElement("div", "musical-audio-ui__toolbar");
            const modeGroup = makeElement("div", "musical-audio-ui__mode-group");
            const modeButtons = new Map();
            for (const mode of ["Seconds", "Musical"]) {
                const button = makeElement("button", "musical-audio-ui__mode-button", mode);
                button.type = "button";
                modeButtons.set(mode, button);
                modeGroup.appendChild(button);
            }
            const snapWrap = makeElement("label", "musical-audio-ui__snap");
            snapWrap.appendChild(document.createTextNode("Snap"));
            const snapSelect = compactSelect(SNAP_MODES);
            snapWrap.appendChild(snapSelect);
            toolbar.append(modeGroup, snapWrap);
            container.appendChild(toolbar);

            // 4a. Seconds-mode controls.
            const secondsPanel = makeElement(
                "div",
                "musical-audio-ui__panel musical-audio-ui__panel--seconds",
            );
            const secondsStartInput = compactInput();
            const secondsEndInput = compactInput();
            const secondsDurationInput = compactInput();
            for (const input of [secondsStartInput, secondsEndInput, secondsDurationInput]) {
                input.step = "0.000001";
                input.min = "0";
            }
            secondsPanel.append(
                makeField("Start (s)", secondsStartInput),
                makeField("End (s, 0 = file)", secondsEndInput),
                makeField("Duration (s)", secondsDurationInput),
            );
            container.appendChild(secondsPanel);

            // 4b. Musical grid and selection controls.
            const musicalPanel = makeElement(
                "div",
                "musical-audio-ui__panel musical-audio-ui__panel--musical",
            );
            const gridControls = makeElement("div", "musical-audio-ui__grid-controls");
            const controlByWidget = new Map();

            const addNumericControl = (parent, widgetName, label, options = {}) => {
                const input = compactInput();
                input.step = String(options.step ?? 1);
                if (options.min !== undefined) input.min = String(options.min);
                controlByWidget.set(widgetName, input);
                parent.appendChild(makeField(label, input));
                return input;
            };
            const addSelectControl = (parent, widgetName, label, values) => {
                const select = compactSelect(values);
                controlByWidget.set(widgetName, select);
                parent.appendChild(makeField(label, select));
                return select;
            };

            addNumericControl(gridControls, "bpm", "BPM", { min: 0.000001, step: 0.01 });
            addSelectControl(gridControls, "tempo_unit", "Tempo unit", TEMPO_UNITS);
            addNumericControl(gridControls, "beats_per_bar", "Meter numerator", { min: 1 });
            addNumericControl(gridControls, "beat_unit", "Meter denominator", { min: 1 });
            addNumericControl(gridControls, "downbeat_offset", "Downbeat offset", { step: 0.001 });
            addNumericControl(gridControls, "fps", "FPS", { min: 0.000001, step: 0.001 });
            addNumericControl(gridControls, "subdivisions_per_beat", "Subdivisions / beat", { min: 1 });
            musicalPanel.appendChild(gridControls);

            const selectionHeading = makeElement(
                "div",
                "musical-audio-ui__section-heading",
                "Selection",
            );
            musicalPanel.appendChild(selectionHeading);

            const musicalSelection = makeElement(
                "div",
                "musical-audio-ui__selection-controls",
            );
            addNumericControl(musicalSelection, "start_bar", "Start · Bar", { min: 1 });
            addNumericControl(musicalSelection, "start_beat", "Start · Beat", { min: 1 });
            addNumericControl(musicalSelection, "start_subdivision", "Start · Sub", { min: 0 });
            addNumericControl(musicalSelection, "duration_bars", "Length · Bars", { min: 0 });
            addNumericControl(musicalSelection, "duration_beats", "Length · Beats", { min: 0 });
            addNumericControl(musicalSelection, "duration_subdivisions", "Length · Subs", { min: 0 });
            musicalPanel.appendChild(musicalSelection);

            const frameFallbackNote = makeElement(
                "div",
                "musical-audio-ui__frame-note is-hidden",
                "Frame snap resolves to the nearest musical subdivision",
            );
            musicalPanel.appendChild(frameFallbackNote);
            container.appendChild(musicalPanel);

            // 5 and 6. Ruler and selection timeline.
            const trimArea = makeElement("div", "musical-audio-ui__trim-area");
            const timeRuler = makeElement("div", "musical-audio-ui__ruler");
            trimArea.appendChild(timeRuler);

            const sliderBox = makeElement("div", "musical-audio-ui__timeline");
            const fill = makeElement("div", "musical-audio-ui__selection");
            sliderBox.appendChild(fill);

            const startHandle = makeElement(
                "div",
                "musical-audio-ui__handle musical-audio-ui__handle--start",
            );
            const endHandle = makeElement(
                "div",
                "musical-audio-ui__handle musical-audio-ui__handle--end",
            );
            sliderBox.append(startHandle, endHandle);
            trimArea.appendChild(sliderBox);
            container.appendChild(trimArea);

            // 7. Compact position/status line.
            const statusLine = makeElement(
                "div",
                "musical-audio-ui__status",
                "Seconds · 0.000–0.000 s · Frames 0–0",
            );
            container.appendChild(statusLine);

            const domWidget = node.addDOMWidget("audio_ui", "audio_ui", container);
            domWidget._contentHeight = 250;
            domWidget.computeSize = function (width) {
                const nodeWidth = node.size?.[0] ?? width ?? 475;
                return [Math.max(10, nodeWidth - 30), Math.max(180, domWidget._contentHeight)];
            };

            let heightSyncQueued = false;
            let heightSyncUpdating = false;
            node.syncMusicalAudioWidth = function () {
                const nodeWidth = this.size?.[0] ?? 475;
                const targetWidth = Math.max(10, nodeWidth - 30);
                container.style.width = `${targetWidth}px`;
                container.style.maxWidth = `${targetWidth}px`;
            };
            node.scheduleMusicalAudioHeightSync = function () {
                if (heightSyncQueued || heightSyncUpdating) return;
                heightSyncQueued = true;
                requestAnimationFrame(() => {
                    heightSyncQueued = false;
                    if (heightSyncUpdating) return;

                    const previousHeight = container.style.height;
                    const previousMinHeight = container.style.minHeight;
                    const previousMaxHeight = container.style.maxHeight;
                    container.style.height = "max-content";
                    container.style.minHeight = "0";
                    container.style.maxHeight = "none";
                    const measured = Math.max(180, Math.ceil(container.scrollHeight) + 10);
                    container.style.height = previousHeight;
                    container.style.minHeight = previousMinHeight;
                    container.style.maxHeight = previousMaxHeight;

                    const heightChanged = Math.abs(measured - domWidget._contentHeight) > 1;
                    domWidget._contentHeight = measured;
                    heightSyncUpdating = true;
                    const widthNow = node.size?.[0] ?? 475;
                    try {
                        const recommendedHeight = node.computeSize()[1];
                        const nodeHeightChanged = Math.abs((node.size?.[1] ?? 0) - recommendedHeight) > 1;
                        if (nodeHeightChanged) {
                            node.setSize([widthNow, recommendedHeight]);
                        }
                        if (heightChanged || nodeHeightChanged) {
                            appInstance.graph?.setDirtyCanvas(true, true);
                        }
                    } finally {
                        heightSyncUpdating = false;
                    }
                });
            };

            node.syncMusicalAudioWidth();

            setTimeout(() => {
                const widgets = new Map((node.widgets || []).map((candidate) => [candidate.name, candidate]));
                const audioWidget = widgets.get("audio");
                const originalCallbacks = new Map();
                let internalUpdate = false;
                let audioDuration = 0;
                let dragging = null;
                let lastRulerKey = "";

                for (const name of HIDDEN_WIDGETS) hideWidget(widgets.get(name));

                const originalCallbackFor = (widget) => originalCallbacks.get(widget);
                for (const widget of widgets.values()) originalCallbacks.set(widget, widget.callback);

                const dirtyGraph = (background = false) => {
                    appInstance.graph?.setDirtyCanvas(true, background);
                };

                const widgetValue = (name, fallback) => {
                    const widget = widgets.get(name);
                    return widget ? widget.value : fallback;
                };

                const setWidgetValue = (name, value) => {
                    const widget = widgets.get(name);
                    if (!widget) return false;
                    if (typeof value === "number" && !Number.isFinite(value)) return false;
                    if (Object.is(widget.value, value) || widget.value === value) return true;
                    internalUpdate = true;
                    try {
                        widget.value = value;
                        const original = originalCallbackFor(widget);
                        if (original) original.call(widget, value);
                    } finally {
                        internalUpdate = false;
                    }
                    return true;
                };

                const currentMode = () => widgetValue("edit_mode", "Seconds") === "Musical"
                    ? "Musical"
                    : "Seconds";

                const currentSnap = () => SNAP_MODES.includes(widgetValue("snap_mode", "Off"))
                    ? widgetValue("snap_mode", "Off")
                    : "Off";

                const visibleStructureChanged = () => {
                    const musical = currentMode() === "Musical";
                    const mode = musical ? "musical" : "seconds";
                    const frameNoteHidden = !(musical && currentSnap() === "Video Frame");
                    return container.dataset.mode !== mode
                        || frameFallbackNote.classList.contains("is-hidden") !== frameNoteHidden;
                };

                const readTiming = () => {
                    const values = {
                        bpm: Math.max(finiteNumber(widgetValue("bpm", 120), 120), 0.000001),
                        tempoUnit: TEMPO_UNITS.includes(widgetValue("tempo_unit", "Quarter"))
                            ? widgetValue("tempo_unit", "Quarter")
                            : "Quarter",
                        beatsPerBar: Math.max(1, integerValue(widgetValue("beats_per_bar", 4), 4)),
                        beatUnit: Math.max(1, integerValue(widgetValue("beat_unit", 4), 4)),
                        downbeatOffset: finiteNumber(widgetValue("downbeat_offset", 0), 0),
                        fps: Math.max(finiteNumber(widgetValue("fps", 24), 24), 0.000001),
                        subdivisionsPerBeat: Math.max(
                            1,
                            integerValue(widgetValue("subdivisions_per_beat", 4), 4),
                        ),
                    };
                    try {
                        return timingGrid(values);
                    } catch {
                        // Malformed legacy workflow values are previewed safely and left untouched.
                        return timingGrid();
                    }
                };

                const readMusicalFields = () => ({
                    position: {
                        bar: integerValue(widgetValue("start_bar", 1), 1),
                        beat: integerValue(widgetValue("start_beat", 1), 1),
                        subdivision: integerValue(widgetValue("start_subdivision", 0), 0),
                    },
                    duration: {
                        bars: integerValue(widgetValue("duration_bars", 0), 0),
                        beats: integerValue(widgetValue("duration_beats", 0), 0),
                        subdivisions: integerValue(widgetValue("duration_subdivisions", 0), 0),
                    },
                });

                const resolveSelection = () => {
                    const mode = currentMode();
                    const timing = readTiming();
                    let requestedStart;
                    let requestedEnd;
                    let startIndex = null;
                    let subdivisionCount = null;
                    let musicalFields = null;

                    if (mode === "Musical") {
                        musicalFields = readMusicalFields();
                        startIndex = musicalPositionToSubdivisionIndex(musicalFields.position, timing);
                        subdivisionCount = Math.max(
                            0,
                            durationFieldsToSubdivisionCount(musicalFields.duration, timing),
                        );
                        requestedStart = subdivisionIndexToSeconds(startIndex, timing);
                        requestedEnd = subdivisionIndexToSeconds(startIndex + subdivisionCount, timing);
                    } else {
                        requestedStart = finiteNumber(widgetValue("start_time", 0), 0);
                        const storedEnd = finiteNumber(widgetValue("end_time", 0), 0);
                        requestedEnd = storedEnd <= 0 ? audioDuration : storedEnd;
                    }

                    const hasDuration = Number.isFinite(audioDuration) && audioDuration > 0;
                    let start = hasDuration ? clamp(requestedStart, 0, audioDuration) : 0;
                    let end = hasDuration ? clamp(requestedEnd, 0, audioDuration) : 0;
                    let clamped = hasDuration && (
                        requestedStart < 0
                        || requestedStart > audioDuration
                        || requestedEnd < 0
                        || requestedEnd > audioDuration
                        || requestedEnd < requestedStart
                    );
                    if (end < start) {
                        end = start;
                        clamped = hasDuration;
                    }

                    const selectionDuration = Math.max(0, end - start);
                    const startFrame = roundHalfAwayFromZero(start * timing.fps);
                    const frameCount = roundHalfAwayFromZero(selectionDuration * timing.fps);
                    return {
                        mode,
                        timing,
                        requestedStart,
                        requestedEnd,
                        start,
                        end,
                        selectionDuration,
                        startFrame,
                        frameCount,
                        frameEnd: startFrame + frameCount,
                        clamped,
                        startIndex,
                        subdivisionCount,
                        musicalFields,
                    };
                };

                const setControlValue = (control, value, force = false) => {
                    if (!control || (!force && document.activeElement === control)) return;
                    const next = typeof value === "number" ? formatInputValue(value) : String(value);
                    if (control.value !== next) control.value = next;
                };

                const syncControls = (state, force = false) => {
                    for (const [mode, button] of modeButtons) {
                        const active = mode === state.mode;
                        button.classList.toggle("is-active", active);
                        button.setAttribute("aria-pressed", active ? "true" : "false");
                    }
                    setControlValue(snapSelect, currentSnap(), force);
                    container.dataset.mode = state.mode.toLowerCase();
                    frameFallbackNote.classList.toggle(
                        "is-hidden",
                        !(state.mode === "Musical" && currentSnap() === "Video Frame"),
                    );

                    setControlValue(
                        secondsStartInput,
                        finiteNumber(widgetValue("start_time", 0), 0),
                        force,
                    );
                    setControlValue(
                        secondsEndInput,
                        finiteNumber(widgetValue("end_time", 0), 0),
                        force,
                    );
                    setControlValue(secondsDurationInput, state.mode === "Seconds"
                        ? state.selectionDuration
                        : finiteNumber(widgetValue("duration", 0), 0), force);

                    for (const [name, control] of controlByWidget) {
                        setControlValue(control, widgetValue(name, 0), force);
                    }
                };

                const formatTime = (seconds) => {
                    if (seconds < 60) return `${seconds.toFixed(1)}s`;
                    const minutes = Math.floor(seconds / 60);
                    const remainder = (seconds % 60).toFixed(1);
                    return `${minutes}:${remainder.padStart(4, "0")}`;
                };

                const addRulerTick = (percentage, strength, label = "", highlight = false) => {
                    const classes = [
                        "musical-audio-ui__tick",
                        `musical-audio-ui__tick--${strength}`,
                    ];
                    if (highlight) classes.push("is-active");
                    const wrapper = makeElement("div", classes.join(" "));
                    wrapper.style.setProperty("--mau-tick-position", `${percentage}%`);
                    wrapper.style.setProperty(
                        "--mau-tick-align",
                        percentage <= 0 ? "flex-start" : percentage >= 100 ? "flex-end" : "center",
                    );
                    wrapper.style.setProperty(
                        "--mau-tick-transform",
                        percentage <= 0
                            ? "none"
                            : percentage >= 100 ? "translateX(-100%)" : "translateX(-50%)",
                    );
                    if (label) {
                        wrapper.appendChild(makeElement(
                            "div",
                            "musical-audio-ui__tick-label",
                            label,
                        ));
                    }
                    timeRuler.appendChild(wrapper);
                };

                const renderSecondsRuler = () => {
                    const majorTicks = 5;
                    const subdivisions = 4;
                    const totalTicks = (majorTicks - 1) * subdivisions;
                    for (let index = 0; index <= totalTicks; index += 1) {
                        const fraction = index / totalTicks;
                        const major = index % subdivisions === 0;
                        addRulerTick(
                            fraction * 100,
                            major ? "bar" : "subdivision",
                            major ? formatTime(audioDuration * fraction) : "",
                        );
                    }
                };

                const renderMusicalRuler = (timing) => {
                    if (timing.downbeatOffset > 0) {
                        const mutedWidth = clamp((timing.downbeatOffset / audioDuration) * 100, 0, 100);
                        const preroll = makeElement("div", "musical-audio-ui__preroll");
                        preroll.style.setProperty("--mau-preroll-width", `${mutedWidth}%`);
                        preroll.classList.toggle("is-complete", mutedWidth >= 100);
                        timeRuler.appendChild(preroll);
                    }

                    const secondsPerSubdivision = timing.secondsPerSubdivision;
                    const firstIndex = Math.ceil((0 - timing.downbeatOffset) / secondsPerSubdivision - 1e-10);
                    const lastIndex = Math.floor((audioDuration - timing.downbeatOffset) / secondsPerSubdivision + 1e-10);
                    if (!Number.isFinite(firstIndex) || !Number.isFinite(lastIndex) || lastIndex < firstIndex) return;

                    const subdivisionsPerBeat = timing.subdivisionsPerBeat;
                    const subdivisionsPerBar = timing.beatsPerBar * subdivisionsPerBeat;
                    const range = Math.max(0, lastIndex - firstIndex);
                    let step = 1;
                    if (range > 160) step = subdivisionsPerBeat;
                    if (range / step > 160) step = subdivisionsPerBar;
                    if (range / step > 120) {
                        step = subdivisionsPerBar * Math.ceil(range / subdivisionsPerBar / 100);
                    }
                    const visibleBars = Math.max(1, Math.ceil(range / subdivisionsPerBar));
                    const labelStride = Math.max(1, Math.ceil(visibleBars / 35));
                    const firstTick = Math.ceil(firstIndex / step) * step;

                    let rendered = 0;
                    for (let index = firstTick; index <= lastIndex && rendered < 180; index += step) {
                        const seconds = subdivisionIndexToSeconds(index, timing);
                        const percentage = clamp((seconds / audioDuration) * 100, 0, 100);
                        const withinBar = ((index % subdivisionsPerBar) + subdivisionsPerBar) % subdivisionsPerBar;
                        const withinBeat = ((index % subdivisionsPerBeat) + subdivisionsPerBeat) % subdivisionsPerBeat;
                        const isBar = withinBar === 0;
                        const isBeat = withinBeat === 0;
                        let label = "";
                        let isBarOne = false;
                        if (isBar && index >= 0) {
                            const bar = Math.floor(index / subdivisionsPerBar) + 1;
                            isBarOne = bar === 1;
                            if (isBarOne || (bar - 1) % labelStride === 0) label = `B${bar}`;
                        }
                        addRulerTick(
                            percentage,
                            isBar ? "bar" : isBeat ? "beat" : "subdivision",
                            label,
                            isBarOne,
                        );
                        rendered += 1;
                    }
                };

                const renderRuler = (state) => {
                    const rulerKey = state.mode === "Seconds"
                        ? `Seconds:${audioDuration}`
                        : [
                            "Musical",
                            audioDuration,
                            state.timing.secondsPerSubdivision,
                            state.timing.beatsPerBar,
                            state.timing.subdivisionsPerBeat,
                            state.timing.downbeatOffset,
                        ].join(":");
                    if (rulerKey === lastRulerKey) return;
                    lastRulerKey = rulerKey;
                    timeRuler.replaceChildren();
                    if (!(audioDuration > 0)) return;
                    if (state.mode === "Musical") renderMusicalRuler(state.timing);
                    else renderSecondsRuler();
                };

                const renderSelection = (state) => {
                    const startPercentage = audioDuration > 0 ? (state.start / audioDuration) * 100 : 0;
                    const endPercentage = audioDuration > 0 ? (state.end / audioDuration) * 100 : 0;
                    startHandle.style.setProperty("--mau-handle-position", `${startPercentage}%`);
                    endHandle.style.setProperty("--mau-handle-position", `${endPercentage}%`);
                    fill.style.setProperty("--mau-selection-start", `${startPercentage}%`);
                    const width = Math.max(0, endPercentage - startPercentage);
                    fill.style.setProperty(
                        "--mau-selection-width",
                        width > 0 ? `${width}%` : "2px",
                    );
                    fill.classList.toggle(
                        "is-collapsed-at-end",
                        width === 0 && startPercentage >= 100,
                    );

                    if (state.mode === "Musical") {
                        const canonicalLength = subdivisionCountToDurationFields(
                            state.subdivisionCount,
                            state.timing,
                        );
                        const lengthText = durationLabel(canonicalLength);
                        trimLength.textContent = `${lengthText} · ${state.selectionDuration.toFixed(3)} s · ${state.frameCount} frames`;
                        const position = subdivisionIndexToMusicalPosition(
                            Math.max(0, state.startIndex),
                            state.timing,
                        );
                        statusLine.textContent = [
                            `B${position.bar} · Beat ${position.beat} · Sub ${position.subdivision}`,
                            lengthText,
                            `${state.start.toFixed(3)}–${state.end.toFixed(3)} s`,
                            `Frames ${state.startFrame}–${state.frameEnd}`,
                            state.clamped ? "clamped to audio" : "",
                        ].filter(Boolean).join(" | ");
                    } else {
                        trimLength.textContent = `Trimmed: ${state.selectionDuration.toFixed(3)} s · ${state.frameCount} frames`;
                        statusLine.textContent = [
                            "Seconds",
                            `${state.start.toFixed(3)}–${state.end.toFixed(3)} s`,
                            `Frames ${state.startFrame}–${state.frameEnd}`,
                            state.clamped ? "clamped to audio" : "",
                        ].filter(Boolean).join(" · ");
                    }
                };

                const seekToSelectionStart = (state) => {
                    if (audioEl.readyState >= 1 && audioDuration > 0) {
                        audioEl.currentTime = clamp(state.start, 0, audioDuration);
                    }
                };

                const refreshUI = (seek = false, forceControls = false, recomputeLayout = false) => {
                    const state = resolveSelection();
                    syncControls(state, forceControls);
                    renderRuler(state);
                    renderSelection(state);
                    if (seek) seekToSelectionStart(state);
                    if (recomputeLayout) node.scheduleMusicalAudioHeightSync();
                    return state;
                };

                const updateAudioSource = () => {
                    if (!audioWidget?.value || audioWidget.value === "none") {
                        playerTitle.textContent = "No audio selected";
                        audioDuration = 0;
                        audioEl.removeAttribute("src");
                        audioEl.load();
                        lastRulerKey = "";
                        refreshUI(false, true);
                        return;
                    }
                    let filename = String(audioWidget.value);
                    let subfolder = "";
                    if (filename.includes("/") || filename.includes("\\")) {
                        const separator = filename.includes("/") ? "/" : "\\";
                        const parts = filename.split(separator);
                        filename = parts.pop();
                        subfolder = parts.join("/");
                    }
                    playerTitle.textContent = filename;
                    const source = api.apiURL(
                        `/view?filename=${encodeURIComponent(filename)}&type=input&subfolder=${encodeURIComponent(subfolder)}`,
                    );
                    if (audioEl.src !== source) audioEl.src = source;
                };

                const writeSecondsRange = (start, storedEnd, resolvedEnd) => {
                    const safeStart = storedNumber(start);
                    const safeEnd = storedNumber(storedEnd);
                    const safeDuration = storedNumber(Math.max(0, resolvedEnd - start));
                    if (safeStart === null || safeEnd === null || safeDuration === null) return false;
                    setWidgetValue("start_time", safeStart);
                    setWidgetValue("end_time", safeEnd);
                    setWidgetValue("duration", safeDuration);
                    return true;
                };

                const writeMusicalSelection = (startIndex, subdivisionCount) => {
                    const canonicalStart = subdivisionIndexToMusicalPosition(
                        Math.max(0, integerValue(startIndex, 0)),
                        readTiming(),
                    );
                    const canonicalDuration = subdivisionCountToDurationFields(
                        Math.max(0, integerValue(subdivisionCount, 0)),
                        readTiming(),
                    );
                    setWidgetValue("start_bar", canonicalStart.bar);
                    setWidgetValue("start_beat", canonicalStart.beat);
                    setWidgetValue("start_subdivision", canonicalStart.subdivision);
                    setWidgetValue("duration_bars", canonicalDuration.bars);
                    setWidgetValue("duration_beats", canonicalDuration.beats);
                    setWidgetValue("duration_subdivisions", canonicalDuration.subdivisions);
                };

                const canonicalizeMusicalSelection = () => {
                    const timing = readTiming();
                    const fields = readMusicalFields();
                    const startIndex = Math.max(
                        0,
                        musicalPositionToSubdivisionIndex(fields.position, timing),
                    );
                    const count = Math.max(
                        0,
                        durationFieldsToSubdivisionCount(fields.duration, timing),
                    );
                    writeMusicalSelection(startIndex, count);
                };

                const activeChangeComplete = (seek = true, recomputeLayout = false) => {
                    lastRulerKey = "";
                    refreshUI(seek, true, recomputeLayout);
                    dirtyGraph(false);
                };

                for (const [mode, button] of modeButtons) {
                    button.addEventListener("click", () => {
                        setWidgetValue("edit_mode", mode);
                        activeChangeComplete(true, visibleStructureChanged());
                    });
                }
                snapSelect.addEventListener("change", () => {
                    if (!SNAP_MODES.includes(snapSelect.value)) return;
                    setWidgetValue("snap_mode", snapSelect.value);
                    activeChangeComplete(false, visibleStructureChanged());
                });

                secondsStartInput.addEventListener("input", () => {
                    const parsed = Number.parseFloat(secondsStartInput.value);
                    if (!Number.isFinite(parsed)) return;
                    const state = resolveSelection();
                    const maximum = audioDuration > 0 ? audioDuration : Math.max(0, parsed);
                    const resolvedEnd = audioDuration > 0
                        ? state.end
                        : Math.max(parsed, finiteNumber(widgetValue("end_time", 0), 0));
                    const start = clamp(parsed, 0, Math.max(0, Math.min(maximum, resolvedEnd)));
                    const endStored = finiteNumber(widgetValue("end_time", 0), 0) <= 0
                        ? 0
                        : resolvedEnd;
                    writeSecondsRange(start, endStored, resolvedEnd);
                    activeChangeComplete(true);
                });

                secondsEndInput.addEventListener("input", () => {
                    const parsed = Number.parseFloat(secondsEndInput.value);
                    if (!Number.isFinite(parsed)) return;
                    const state = resolveSelection();
                    const start = state.start;
                    if (parsed <= 0) {
                        const resolvedEnd = audioDuration > 0 ? audioDuration : start;
                        writeSecondsRange(start, 0, resolvedEnd);
                    } else {
                        const maximum = audioDuration > 0 ? audioDuration : parsed;
                        const resolvedEnd = clamp(parsed, start, Math.max(start, maximum));
                        writeSecondsRange(start, resolvedEnd, resolvedEnd);
                    }
                    activeChangeComplete(true);
                });

                secondsDurationInput.addEventListener("input", () => {
                    const parsed = Number.parseFloat(secondsDurationInput.value);
                    if (!Number.isFinite(parsed)) return;
                    let length = Math.max(0, parsed);
                    let start = Math.max(0, finiteNumber(widgetValue("start_time", 0), 0));
                    if (audioDuration > 0) {
                        length = Math.min(length, audioDuration);
                        start = Math.min(start, audioDuration);
                        if (start + length > audioDuration) start = audioDuration - length;
                    }
                    const end = start + length;
                    writeSecondsRange(start, end, end);
                    activeChangeComplete(true);
                });

                const integerWidgetNames = new Set([
                    "beats_per_bar",
                    "beat_unit",
                    "start_bar",
                    "start_beat",
                    "start_subdivision",
                    "duration_bars",
                    "duration_beats",
                    "duration_subdivisions",
                    "subdivisions_per_beat",
                ]);
                const positiveWidgetNames = new Set([
                    "bpm",
                    "fps",
                    "beats_per_bar",
                    "beat_unit",
                    "subdivisions_per_beat",
                ]);
                const canonicalWidgetNames = new Set([
                    "beats_per_bar",
                    "start_bar",
                    "start_beat",
                    "start_subdivision",
                    "duration_bars",
                    "duration_beats",
                    "duration_subdivisions",
                    "subdivisions_per_beat",
                ]);

                for (const [name, control] of controlByWidget) {
                    const eventName = control.tagName === "SELECT" ? "change" : "input";
                    control.addEventListener(eventName, () => {
                        if (control.tagName === "SELECT") {
                            if (name === "tempo_unit" && !TEMPO_UNITS.includes(control.value)) return;
                            setWidgetValue(name, control.value);
                        } else {
                            let value = Number.parseFloat(control.value);
                            if (!Number.isFinite(value)) return;
                            if (integerWidgetNames.has(name)) value = Math.trunc(value);
                            if (integerWidgetNames.has(name) && !Number.isSafeInteger(value)) return;
                            if (positiveWidgetNames.has(name) && value <= 0) return;
                            const stored = integerWidgetNames.has(name) ? value : storedNumber(value);
                            if (stored === null) return;
                            if (positiveWidgetNames.has(name) && stored <= 0) return;
                            setWidgetValue(name, stored);
                        }
                        if (canonicalWidgetNames.has(name)) canonicalizeMusicalSelection();
                        activeChangeComplete(true);
                    });
                    control.addEventListener("blur", () => refreshUI(false, true));
                }

                const snapSecondsPointer = (seconds, timing, mode) => {
                    let snapped = seconds;
                    if (mode === "Bar") snapped = snapToBar(seconds, timing);
                    else if (mode === "Beat") snapped = snapToBeat(seconds, timing);
                    else if (mode === "Subdivision") snapped = snapToSubdivision(seconds, timing);
                    else if (mode === "Video Frame") snapped = snapToVideoFrame(seconds, timing.fps);
                    return clamp(snapped, 0, audioDuration);
                };

                const musicalPointerIndex = (seconds, timing, mode) => {
                    let index;
                    if (mode === "Video Frame") {
                        index = frameToNearestSubdivision(seconds, timing).subdivisionIndex;
                    } else if (mode === "Bar") {
                        index = secondsToNearestSubdivision(snapToBar(seconds, timing), timing);
                    } else if (mode === "Beat") {
                        index = secondsToNearestSubdivision(snapToBeat(seconds, timing), timing);
                    } else {
                        // Off and Subdivision both use the finest representable musical unit.
                        index = secondsToNearestSubdivision(seconds, timing);
                    }
                    return Math.max(0, index);
                };

                const applyPointer = (pointerSeconds) => {
                    if (!dragging || !(audioDuration > 0)) return;
                    const state = resolveSelection();
                    const snap = currentSnap();
                    if (state.mode === "Seconds") {
                        if (dragging.kind === "start") {
                            const start = Math.min(
                                snapSecondsPointer(pointerSeconds, state.timing, snap),
                                state.end,
                            );
                            writeSecondsRange(start, state.end, state.end);
                        } else if (dragging.kind === "end") {
                            const end = Math.max(
                                snapSecondsPointer(pointerSeconds, state.timing, snap),
                                state.start,
                            );
                            writeSecondsRange(state.start, end, end);
                        } else {
                            const width = dragging.durationSeconds;
                            let start = snapSecondsPointer(
                                pointerSeconds - dragging.offsetSeconds,
                                state.timing,
                                snap,
                            );
                            start = clamp(start, 0, Math.max(0, audioDuration - width));
                            const end = Math.min(audioDuration, start + width);
                            writeSecondsRange(start, end, end);
                        }
                    } else {
                        const pointerIndex = musicalPointerIndex(pointerSeconds, state.timing, snap);
                        const currentEndIndex = state.startIndex + state.subdivisionCount;
                        if (dragging.kind === "start") {
                            const startIndex = Math.min(pointerIndex, currentEndIndex);
                            writeMusicalSelection(startIndex, currentEndIndex - startIndex);
                        } else if (dragging.kind === "end") {
                            const endIndex = Math.max(pointerIndex, state.startIndex);
                            writeMusicalSelection(state.startIndex, endIndex - state.startIndex);
                        } else {
                            let startIndex = Math.max(0, pointerIndex - dragging.offsetSubdivisions);
                            const lastAudioIndex = Math.max(
                                0,
                                secondsToNearestSubdivision(audioDuration, state.timing),
                            );
                            if (state.subdivisionCount <= lastAudioIndex) {
                                startIndex = Math.min(startIndex, lastAudioIndex - state.subdivisionCount);
                            }
                            writeMusicalSelection(startIndex, state.subdivisionCount);
                        }
                    }
                    // Pointer movement updates values and visuals only; it never changes structure.
                    activeChangeComplete(true);
                };

                sliderBox.addEventListener("pointerdown", (event) => {
                    if (!(audioDuration > 0)) return;
                    const rect = sliderBox.getBoundingClientRect();
                    if (!(rect.width > 0)) return;
                    const x = clamp(event.clientX - rect.left, 0, rect.width);
                    const pointerSeconds = (x / rect.width) * audioDuration;
                    const state = resolveSelection();
                    const tolerance = (10 / rect.width) * audioDuration;
                    if (
                        pointerSeconds > state.start + tolerance
                        && pointerSeconds < state.end - tolerance
                    ) {
                        dragging = {
                            kind: "center",
                            offsetSeconds: pointerSeconds - state.start,
                            durationSeconds: state.selectionDuration,
                            offsetSubdivisions: state.mode === "Musical"
                                ? musicalPointerIndex(pointerSeconds, state.timing, currentSnap()) - state.startIndex
                                : 0,
                        };
                    } else if (Math.abs(pointerSeconds - state.start) <= Math.abs(pointerSeconds - state.end)) {
                        dragging = { kind: "start" };
                    } else {
                        dragging = { kind: "end" };
                    }
                    sliderBox.setPointerCapture(event.pointerId);
                    applyPointer(pointerSeconds);
                });

                sliderBox.addEventListener("pointermove", (event) => {
                    if (!dragging || !(audioDuration > 0)) return;
                    const rect = sliderBox.getBoundingClientRect();
                    if (!(rect.width > 0)) return;
                    const x = clamp(event.clientX - rect.left, 0, rect.width);
                    applyPointer((x / rect.width) * audioDuration);
                });

                const finishPointer = (event) => {
                    dragging = null;
                    if (sliderBox.hasPointerCapture(event.pointerId)) {
                        sliderBox.releasePointerCapture(event.pointerId);
                    }
                };
                sliderBox.addEventListener("pointerup", finishPointer);
                sliderBox.addEventListener("pointercancel", finishPointer);

                audioEl.addEventListener("loadedmetadata", () => {
                    audioDuration = Number.isFinite(audioEl.duration) ? Math.max(0, audioEl.duration) : 0;
                    lastRulerKey = "";
                    if (node._shouldResetSecondsTrim && audioDuration > 0) {
                        writeSecondsRange(0, audioDuration, audioDuration);
                        node._shouldResetSecondsTrim = false;
                    }
                    refreshUI(false, true, true);
                    dirtyGraph(false);
                });
                audioEl.addEventListener("durationchange", () => {
                    if (Number.isFinite(audioEl.duration) && audioEl.duration >= 0) {
                        audioDuration = audioEl.duration;
                        lastRulerKey = "";
                        refreshUI(false, false);
                    }
                });
                audioEl.addEventListener("error", () => {
                    audioDuration = 0;
                    lastRulerKey = "";
                    refreshUI(false, false);
                });
                audioEl.addEventListener("timeupdate", () => {
                    if (dragging || !(audioDuration > 0)) return;
                    const state = resolveSelection();
                    if (audioEl.currentTime >= state.end) {
                        audioEl.pause();
                        audioEl.currentTime = state.start;
                    }
                });
                audioEl.addEventListener("play", () => {
                    const state = resolveSelection();
                    if (state.end <= state.start) {
                        audioEl.pause();
                        seekToSelectionStart(state);
                    } else if (audioEl.currentTime < state.start || audioEl.currentTime >= state.end) {
                        audioEl.currentTime = state.start;
                    }
                });

                container.addEventListener("dragover", (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    container.classList.add("is-dragging");
                });
                container.addEventListener("dragleave", (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    container.classList.remove("is-dragging");
                });
                container.addEventListener("drop", (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    container.classList.remove("is-dragging");
                    const file = event.dataTransfer?.files?.[0];
                    if (file) handleFileUpload(file);
                });

                if (audioWidget) {
                    const original = originalCallbackFor(audioWidget);
                    audioWidget.callback = function () {
                        const callbackResult = original ? original.apply(this, arguments) : undefined;
                        if (
                            !internalUpdate
                            && !node._initializingMusicalAudio
                            && !node._configuringMusicalAudio
                        ) {
                            node._shouldResetSecondsTrim = true;
                        }
                        updateAudioSource();
                        dirtyGraph(false);
                        return callbackResult;
                    };
                }

                for (const [name, widget] of widgets) {
                    if (name === "audio" || name === "audio_ui") continue;
                    const original = originalCallbackFor(widget);
                    widget.callback = function () {
                        const callbackResult = original ? original.apply(this, arguments) : undefined;
                        if (!internalUpdate) {
                            const activeNativeChange = (
                                !node._initializingMusicalAudio
                                && !node._configuringMusicalAudio
                            );
                            if (activeNativeChange && currentMode() === "Seconds") {
                                if (name === "duration") {
                                    let length = Math.max(0, finiteNumber(widget.value, 0));
                                    let start = Math.max(
                                        0,
                                        finiteNumber(widgetValue("start_time", 0), 0),
                                    );
                                    if (audioDuration > 0) {
                                        length = Math.min(length, audioDuration);
                                        start = Math.min(start, audioDuration);
                                        if (start + length > audioDuration) {
                                            start = audioDuration - length;
                                        }
                                    }
                                    writeSecondsRange(start, start + length, start + length);
                                } else if (name === "start_time" || name === "end_time") {
                                    const state = resolveSelection();
                                    const synchronizedDuration = storedNumber(state.selectionDuration);
                                    if (synchronizedDuration !== null) {
                                        setWidgetValue("duration", synchronizedDuration);
                                    }
                                }
                            }
                            setTimeout(() => {
                                lastRulerKey = "";
                                refreshUI(true, false, visibleStructureChanged());
                                dirtyGraph(false);
                            }, 0);
                        }
                        return callbackResult;
                    };
                }

                node.refreshMusicalAudioUI = (recomputeLayout = false) => {
                    updateAudioSource();
                    lastRulerKey = "";
                    refreshUI(false, true, recomputeLayout);
                };

                updateAudioSource();
                refreshUI(false, true, true);
                setTimeout(() => {
                    node._initializingMusicalAudio = false;
                }, 500);
            }, 100);

            return result;
        };
    },
});
