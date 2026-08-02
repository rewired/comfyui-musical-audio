import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
    durationFieldsToSubdivisionCount,
    frameToNearestSubdivision,
    musicalSelectionToSecondsRange,
    musicalPositionToSubdivisionIndex,
    roundHalfAwayFromZero,
    secondsRangeToMusicalSelection,
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
import {
    BEAT_BOUNDARY_EPSILON,
    isMetronomeDownbeat,
    metronomeBeatIndexAtOrAfter,
    metronomeBeatTime,
    metronomeVolumeGain,
} from "./metronome.js";
import {
    isIntermediateDecimalText,
    parseLocalizedDecimal,
} from "./numeric_input.js";
import { createAudioTransport } from "./audio_transport.js";
import { createPlayheadController } from "./playhead.js";
import { createWaveformPeakLoader } from "./waveform_loader.js";
import {
    clearWaveformCanvas,
    renderWaveformCanvas,
} from "./waveform_renderer.js";
import {
    isExtensionDialogAvailable,
    openWaveformEditorDialog,
} from "./waveform_editor_dialog.js";

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

const MUSICAL_TIMING_INPUT_MAP = Object.freeze({
    bpm: "bpm_input",
    tempo_unit: "tempo_unit_input",
    fps: "fps_input",
    beats_per_bar: "beats_per_bar_input",
    beat_unit: "beat_unit_input",
    subdivisions_per_beat: "subdivisions_per_beat_input",
    downbeat_offset: "downbeat_offset_input",
});
const MUSICAL_TIMING_INPUTS = Object.freeze(Object.keys(MUSICAL_TIMING_INPUT_MAP));
const EXTERNAL_INPUT_LABELS = Object.freeze({
    bpm_input: "BPM",
    tempo_unit_input: "Tempo unit",
    fps_input: "FPS",
    beats_per_bar_input: "Beats / bar",
    beat_unit_input: "Beat unit",
    subdivisions_per_beat_input: "Grid / beat",
    downbeat_offset_input: "Downbeat offset",
});
const TEMPO_UNITS = ["Quarter", "Eighth", "Dotted Quarter"];
const SNAP_MODES = ["Off", "Bar", "Beat", "Subdivision", "Video Frame"];
const STORAGE_PRECISION = 1_000_000;
const STYLESHEET_ID = "comfyui-musical-audio-styles";
const RESIZE_HEIGHT_SYNC_DELAY_MS = 120;
const SYNC_FEEDBACK_DURATION_MS = 1800;
const METRONOME_SCHEDULER_INTERVAL_MS = 25;
const METRONOME_SCHEDULE_AHEAD_SECONDS = 0.12;
const METRONOME_START_LEAD_SECONDS = 0.003;
const METRONOME_JUMP_TOLERANCE_SECONDS = 0.075;
const METRONOME_VOLUME_SETTING_ID = "ComfyUI.MusicalAudio.MetronomeVolume";
const METRONOME_VOLUME_RAMP_SECONDS = 0.01;
const WIDTH_CHANGE_TOLERANCE_PX = 1;
const EXTERNAL_PREVIEW_NOTICE = "External timing \u00b7 preview uses local values";
const EXTERNAL_CONTROL_TITLE = [
    "Controlled by an external input.",
    "The displayed value is the saved local fallback used for UI preview.",
].join("\n");
const EXTENSION_PATCHED = Symbol.for("comfyui-musical-audio.extension-patched");
let sharedMetronomeAudioContext = null;
const metronomeVolumeTargets = new Set();

function currentMetronomeVolumeGain() {
    return metronomeVolumeGain(
        app.extensionManager.setting.get(METRONOME_VOLUME_SETTING_ID),
    );
}

function updateMetronomeVolumeTargets(percentage) {
    const gainMultiplier = metronomeVolumeGain(percentage);
    for (const updateTarget of [...metronomeVolumeTargets]) {
        try {
            updateTarget(gainMultiplier);
        } catch {
            // A concurrently removed node must not prevent other buses from updating.
        }
    }
}

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

function applyMusicalAudioInputLabels(node) {
    if (!node || node._musicalAudioRemoved) return;
    for (const input of node.inputs || []) {
        const inputName = input?.name;
        if (!Object.prototype.hasOwnProperty.call(EXTERNAL_INPUT_LABELS, inputName)) continue;
        input.label = EXTERNAL_INPUT_LABELS[input.name];
        if (inputName === "downbeat_offset_input") input.label = "Downbeat offset (s)";
    }
}

function matchingMusicalAudioInput(node, widgetName) {
    const inputName = MUSICAL_TIMING_INPUT_MAP[widgetName];
    if (!inputName) return null;
    return (node.inputs || []).find((input) => input?.name === inputName) || null;
}

function graphContainsLink(graph, linkId) {
    if (!graph) return true;
    if (typeof graph.getLink === "function") return Boolean(graph.getLink(linkId));
    if (graph.links instanceof Map) {
        return graph.links.has(linkId) && graph.links.get(linkId) != null;
    }
    if (graph.links && typeof graph.links === "object") {
        return graph.links[linkId] != null;
    }
    return true;
}

function isMusicalAudioInputConnected(node, widgetName) {
    const input = matchingMusicalAudioInput(node, widgetName);
    return input?.link != null && graphContainsLink(node.graph, input.link);
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

function compactDecimalInput() {
    const input = compactInput("text");
    input.inputMode = "decimal";
    input.autocomplete = "off";
    input.spellcheck = false;
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

function wrapCustomControl(control) {
    if (control._musicalAudioControlWrap) return control._musicalAudioControlWrap;
    const wrapper = makeElement("span", "musical-audio-ui__control-wrap");
    const badge = makeElement(
        "span",
        "musical-audio-ui__external-badge",
        "External",
    );
    badge.setAttribute("aria-hidden", "true");
    wrapper.append(control, badge);
    control._musicalAudioControlWrap = wrapper;
    control._musicalAudioExternalBadge = badge;
    return wrapper;
}

function makeField(labelText, control) {
    const field = makeElement("label", "musical-audio-ui__field");
    field.appendChild(makeElement("span", "musical-audio-ui__field-label", labelText));
    field.appendChild(wrapCustomControl(control));
    return field;
}

function makeControlGroup(title, modifier) {
    const group = makeElement(
        "fieldset",
        `musical-audio-ui__control-group musical-audio-ui__control-group--${modifier}`,
    );
    const legend = makeElement(
        "legend",
        "musical-audio-ui__control-group-title",
        title,
    );
    const body = makeElement("div", "musical-audio-ui__control-group-body");
    group.append(legend, body);
    return { group, body };
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

function syncOnSwitchEnabled(node) {
    if (!node.properties || typeof node.properties !== "object" || Array.isArray(node.properties)) {
        node.properties = {};
    }
    if (typeof node.properties.musical_audio_sync_on_switch !== "boolean") {
        node.properties.musical_audio_sync_on_switch = true;
    }
    return node.properties.musical_audio_sync_on_switch;
}

function metronomeEnabled(node) {
    if (!node.properties || typeof node.properties !== "object" || Array.isArray(node.properties)) {
        node.properties = {};
    }
    if (typeof node.properties.musical_audio_metronome_enabled !== "boolean") {
        node.properties.musical_audio_metronome_enabled = false;
    }
    return node.properties.musical_audio_metronome_enabled;
}

async function ensureSharedMetronomeContext(allowCreation) {
    if (sharedMetronomeAudioContext?.state === "closed") {
        sharedMetronomeAudioContext = null;
    }
    if (!sharedMetronomeAudioContext && allowCreation) {
        const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextConstructor) return null;
        try {
            sharedMetronomeAudioContext = new AudioContextConstructor();
        } catch {
            return null;
        }
    }
    const context = sharedMetronomeAudioContext;
    if (!context) return null;
    if (context.state === "suspended") {
        try {
            await context.resume();
        } catch {
            return null;
        }
    }
    return context.state === "running" ? context : null;
}

app.registerExtension({
    name: "comfyui-musical-audio.MusicalLoadAudioUI",
    settings: [
        {
            id: METRONOME_VOLUME_SETTING_ID,
            name: "Metronome volume (%)",
            category: ["Musical Audio", "Metronome"],
            tooltip: [
                "Controls the preview metronome volume for all Musical Audio nodes.",
                "This does not change the loaded audio or node output.",
            ].join("\n"),
            type: "slider",
            attrs: {
                min: 0,
                max: 200,
                step: 5,
            },
            defaultValue: 100,
            onChange: (value) => updateMetronomeVolumeTargets(value),
        },
    ],
    async beforeRegisterNodeDef(nodeType, nodeData, appInstance) {
        if (nodeData.name !== "MusicalLoadAudioUI") return;
        if (nodeType.prototype[EXTENSION_PATCHED]) return;

        // Comfy.UploadAudio injects this frontend-only widget before custom-node
        // definitions are registered. It must remain a button, not an eighth socket.
        const uploadConfig = nodeData.input?.required?.upload;
        if (Array.isArray(uploadConfig)) {
            uploadConfig[1] ??= {};
            uploadConfig[1].socketless = true;
        }

        Object.defineProperty(nodeType.prototype, EXTENSION_PATCHED, {
            value: true,
            configurable: false,
        });

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        const onDrawBackground = nodeType.prototype.onDrawBackground;
        const onConfigure = nodeType.prototype.onConfigure;
        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        const onResize = nodeType.prototype.onResize;
        const onRemoved = nodeType.prototype.onRemoved;

        nodeType.prototype.onDrawBackground = function () {
            if (onDrawBackground) onDrawBackground.apply(this, arguments);
        };

        nodeType.prototype.onResize = function () {
            const result = onResize ? onResize.apply(this, arguments) : undefined;
            // Width follows the drag immediately; responsive height follows once resizing settles.
            const widthChanged = this.syncMusicalAudioWidth
                ? this.syncMusicalAudioWidth()
                : false;
            if (widthChanged && this.scheduleMusicalAudioResizeHeightSync) {
                this.scheduleMusicalAudioResizeHeightSync();
            }
            return result;
        };

        nodeType.prototype.onRemoved = function () {
            this._musicalAudioRemoved = true;
            this._musicalAudioExternalRefreshPending = false;
            if (this.closeMusicalAudioWaveformEditor) {
                this.closeMusicalAudioWaveformEditor();
            }
            if (this.destroyMusicalAudioWaveformRenderer) {
                this.destroyMusicalAudioWaveformRenderer();
            }
            if (this.destroyMusicalAudioWaveformData) {
                this.destroyMusicalAudioWaveformData();
            }
            if (this.destroyMusicalAudioMetronome) {
                this.destroyMusicalAudioMetronome();
            }
            if (this.destroyMusicalAudioTransport) {
                this.destroyMusicalAudioTransport();
            }
            if (this.cancelMusicalAudioResizeHeightSync) {
                this.cancelMusicalAudioResizeHeightSync();
            }
            if (this.clearMusicalAudioSyncFeedback) {
                this.clearMusicalAudioSyncFeedback();
            }
            return onRemoved ? onRemoved.apply(this, arguments) : undefined;
        };

        nodeType.prototype.onConfigure = function () {
            this._configuringMusicalAudio = true;
            const result = onConfigure ? onConfigure.apply(this, arguments) : undefined;
            applyMusicalAudioInputLabels(this);
            syncOnSwitchEnabled(this);
            metronomeEnabled(this);
            if (this.refreshMusicalAudioExternalState) {
                this.refreshMusicalAudioExternalState();
            } else {
                this._musicalAudioExternalRefreshPending = true;
            }
            setTimeout(() => {
                if (this._musicalAudioRemoved) return;
                this._configuringMusicalAudio = false;
                if (this.syncMusicalAudioWidth) this.syncMusicalAudioWidth();
                if (this.refreshMusicalAudioUI) {
                    this.refreshMusicalAudioUI(true);
                }
            }, 0);
            return result;
        };

        nodeType.prototype.onConnectionsChange = function () {
            const result = onConnectionsChange
                ? onConnectionsChange.apply(this, arguments)
                : undefined;
            if (this.refreshMusicalAudioExternalState) {
                this.refreshMusicalAudioExternalState();
            } else {
                this._musicalAudioExternalRefreshPending = true;
            }
            return result;
        };

        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;
            const node = this;
            applyMusicalAudioInputLabels(node);
            syncOnSwitchEnabled(node);
            metronomeEnabled(node);
            node._initializingMusicalAudio = true;
            node._shouldResetSecondsTrim = false;
            node._musicalAudioRemoved = false;
            node._musicalAudioWaveformEditorDialog = null;

            const waveformEditorDialogService = () => app.extensionManager?.dialog ?? null;
            const waveformEditorSourceLabel = () => {
                const value = node.widgets?.find(
                    (candidate) => candidate.name === "audio",
                )?.value;
                const normalized = value == null ? "" : String(value).trim();
                return normalized && normalized.toLowerCase() !== "none"
                    ? normalized
                    : "No audio selected";
            };
            node.openMusicalAudioWaveformEditor = () => {
                if (node._musicalAudioRemoved) return null;
                const existingHandle = node._musicalAudioWaveformEditorDialog;
                let openedHandle = null;
                openedHandle = openWaveformEditorDialog({
                    dialogService: waveformEditorDialogService(),
                    nodeId: node.id,
                    sourceLabel: waveformEditorSourceLabel(),
                    onClose: () => {
                        if (node._musicalAudioWaveformEditorDialog === openedHandle) {
                            node._musicalAudioWaveformEditorDialog = null;
                        }
                    },
                });
                if (!openedHandle) return null;
                if (existingHandle) return existingHandle;
                node._musicalAudioWaveformEditorDialog = openedHandle;
                return openedHandle;
            };
            node.closeMusicalAudioWaveformEditor = () => {
                const activeHandle = node._musicalAudioWaveformEditorDialog;
                if (!activeHandle) return;
                node._musicalAudioWaveformEditorDialog = null;
                activeHandle.close();
            };

            const waveformLoader = createWaveformPeakLoader({
                fetchResponse: (path, requestOptions) => api.fetchApi(path, requestOptions),
                onStateChange: (state) => {
                    node._musicalAudioWaveformState = state;
                    node.scheduleMusicalAudioWaveformRender?.();
                },
            });
            node._musicalAudioWaveformState = waveformLoader.getState();
            node.getMusicalAudioWaveformState = () => waveformLoader.getState();
            node.refreshMusicalAudioWaveformData = (options = {}) => {
                const value = node.widgets?.find(
                    (candidate) => candidate.name === "audio",
                )?.value;
                const filename = value == null ? value : String(value);
                return waveformLoader.load(filename, { force: options?.force === true });
            };
            node.clearMusicalAudioWaveformData = () => waveformLoader.clear();
            node.destroyMusicalAudioWaveformData = () => {
                waveformLoader.dispose();
                node._musicalAudioWaveformState = waveformLoader.getState();
            };

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
            const isLegacyRenderer = !window.LiteGraph || !window.LiteGraph.vueNodesMode;
            if (isLegacyRenderer) container.classList.add("is-legacy-renderer");
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
            const audioTransport = createAudioTransport(audioEl);
            node._musicalAudioTransport = audioTransport;
            node.getMusicalAudioTransport = () => node._musicalAudioTransport ?? null;
            node.destroyMusicalAudioTransport = () => {
                const currentTransport = node._musicalAudioTransport;
                if (!currentTransport) return;
                currentTransport.destroy();
                if (node._musicalAudioTransport === currentTransport) {
                    node._musicalAudioTransport = null;
                }
            };
            playerWrapper.appendChild(audioEl);
            container.appendChild(playerWrapper);

            // 3. Mode, synchronization, metronome, and snap toolbar.
            const toolbar = makeElement("div", "musical-audio-ui__toolbar");
            const modeGroup = makeElement("div", "musical-audio-ui__mode-group");
            const modeButtons = new Map();
            for (const mode of ["Seconds", "Musical"]) {
                const button = makeElement("button", "musical-audio-ui__mode-button", mode);
                button.type = "button";
                modeButtons.set(mode, button);
                modeGroup.appendChild(button);
            }
            const syncWrap = makeElement("label", "musical-audio-ui__sync");
            syncWrap.title = "Copy the active selection into the other time model when switching modes.";
            const syncLabel = makeElement("span", "musical-audio-ui__sync-label", "Sync");
            const syncInput = makeElement("input", "musical-audio-ui__sync-input");
            syncInput.type = "checkbox";
            syncInput.checked = syncOnSwitchEnabled(node);
            syncInput.setAttribute("aria-label", "Sync on switch");
            const syncSwitch = makeElement("span", "musical-audio-ui__sync-switch");
            syncSwitch.setAttribute("aria-hidden", "true");
            syncSwitch.appendChild(makeElement("span", "musical-audio-ui__sync-thumb"));
            syncWrap.append(syncLabel, syncInput, syncSwitch);
            const metronomeWrap = makeElement("label", "musical-audio-ui__metronome");
            metronomeWrap.title = [
                "Play a local metronome while previewing audio.",
                "Clicks follow the saved local timing values.",
            ].join("\n");
            const metronomeLabel = makeElement(
                "span",
                "musical-audio-ui__metronome-label",
                "Metronome",
            );
            const metronomeInput = makeElement(
                "input",
                "musical-audio-ui__metronome-input",
            );
            metronomeInput.type = "checkbox";
            metronomeInput.checked = metronomeEnabled(node);
            metronomeInput.setAttribute("aria-label", "Enable metronome preview");
            const metronomeSwitch = makeElement(
                "span",
                "musical-audio-ui__metronome-switch",
            );
            metronomeSwitch.setAttribute("aria-hidden", "true");
            metronomeSwitch.appendChild(makeElement(
                "span",
                "musical-audio-ui__metronome-thumb",
            ));
            metronomeWrap.append(metronomeLabel, metronomeInput, metronomeSwitch);
            const snapWrap = makeElement("label", "musical-audio-ui__snap");
            snapWrap.appendChild(document.createTextNode("Snap"));
            const snapSelect = compactSelect(SNAP_MODES);
            snapWrap.appendChild(snapSelect);
            toolbar.append(modeGroup, syncWrap, metronomeWrap, snapWrap);
            container.appendChild(toolbar);

            const syncFeedback = makeElement(
                "div",
                "musical-audio-ui__sync-feedback",
            );
            syncFeedback.setAttribute("aria-live", "polite");
            syncFeedback.setAttribute("aria-atomic", "true");
            container.appendChild(syncFeedback);

            let syncFeedbackTimer = null;
            node.clearMusicalAudioSyncFeedback = () => {
                if (syncFeedbackTimer !== null) {
                    clearTimeout(syncFeedbackTimer);
                    syncFeedbackTimer = null;
                }
                syncFeedback.classList.remove("is-visible");
            };
            const showSyncFeedback = (message) => {
                node.clearMusicalAudioSyncFeedback();
                if (node._musicalAudioRemoved) return;
                syncFeedback.textContent = message;
                syncFeedback.classList.add("is-visible");
                syncFeedbackTimer = setTimeout(() => {
                    syncFeedbackTimer = null;
                    if (node._musicalAudioRemoved) return;
                    syncFeedback.classList.remove("is-visible");
                }, SYNC_FEEDBACK_DURATION_MS);
            };

            // 4a. Seconds-mode controls.
            const secondsPanel = makeElement(
                "div",
                "musical-audio-ui__panel musical-audio-ui__panel--seconds",
            );
            const secondsStartInput = compactDecimalInput();
            const secondsEndInput = compactDecimalInput();
            const secondsDurationInput = compactDecimalInput();
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

            // 4b. Musical timing groups and selection controls.
            const musicalPanel = makeElement(
                "div",
                "musical-audio-ui__panel musical-audio-ui__panel--musical",
            );
            const controlGroups = makeElement("div", "musical-audio-ui__control-groups");
            const controlByWidget = new Map();

            const addNumericControl = (parent, widgetName, label, options = {}) => {
                const input = options.decimal ? compactDecimalInput() : compactInput();
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

            const timingGroup = makeControlGroup("Timing", "timing");
            addNumericControl(timingGroup.body, "bpm", "BPM", {
                decimal: true,
                min: 0.000001,
                step: 0.01,
            });
            addSelectControl(timingGroup.body, "tempo_unit", "Tempo unit", TEMPO_UNITS);
            addNumericControl(timingGroup.body, "fps", "FPS", {
                decimal: true,
                min: 0.000001,
                step: 0.001,
            });

            const meterGridGroup = makeControlGroup("Meter & Grid", "meter-grid");
            const meterField = makeElement(
                "div",
                "musical-audio-ui__field musical-audio-ui__meter-field",
            );
            meterField.appendChild(makeElement(
                "span",
                "musical-audio-ui__field-label",
                "Time signature",
            ));
            const meterControl = makeElement("div", "musical-audio-ui__meter");
            const meterNumerator = compactInput();
            meterNumerator.min = "1";
            meterNumerator.step = "1";
            meterNumerator.classList.add("musical-audio-ui__meter-numerator");
            meterNumerator.setAttribute("aria-label", "Meter numerator");
            meterNumerator.title = "Meter numerator (beats per bar)";
            const meterDivider = makeElement("div", "musical-audio-ui__meter-divider");
            meterDivider.setAttribute("aria-hidden", "true");
            const meterDenominator = compactInput();
            meterDenominator.min = "1";
            meterDenominator.step = "1";
            meterDenominator.classList.add("musical-audio-ui__meter-denominator");
            meterDenominator.setAttribute("aria-label", "Meter denominator");
            meterDenominator.title = "Meter denominator (beat unit)";
            controlByWidget.set("beats_per_bar", meterNumerator);
            controlByWidget.set("beat_unit", meterDenominator);
            meterControl.append(
                wrapCustomControl(meterNumerator),
                meterDivider,
                wrapCustomControl(meterDenominator),
            );
            meterField.appendChild(meterControl);
            meterGridGroup.body.appendChild(meterField);
            const gridDivisions = addNumericControl(
                meterGridGroup.body,
                "subdivisions_per_beat",
                "Grid divisions / beat",
                { min: 1 },
            );
            gridDivisions.title = [
                "Number of equal grid steps inside each beat.",
                "In 4/4, a value of 4 creates a sixteenth-note grid.",
            ].join("\n");

            const alignmentGroup = makeControlGroup("Alignment", "alignment");
            const downbeatOffset = addNumericControl(
                alignmentGroup.body,
                "downbeat_offset",
                "Downbeat offset (s)",
                { decimal: true, step: 0.001 },
            );
            downbeatOffset.title = [
                "Time in seconds where Bar 1 \u00b7 Beat 1 occurs.",
                "Positive values move the first downbeat later; negative values place it before audio time 0.",
            ].join("\n");
            controlGroups.append(timingGroup.group, meterGridGroup.group, alignmentGroup.group);
            musicalPanel.appendChild(controlGroups);

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

            // 5 and 6. Ruler and selection timeline in one shared horizontal viewport.
            const trimArea = makeElement("div", "musical-audio-ui__trim-area");
            const trimHeader = makeElement("div", "musical-audio-ui__trim-header");
            const trimLabel = makeElement(
                "span",
                "musical-audio-ui__trim-label",
                "Waveform",
            );
            const openEditorButton = makeElement(
                "button",
                "musical-audio-ui__editor-button",
                "Open",
            );
            openEditorButton.type = "button";
            openEditorButton.setAttribute("aria-label", "Open waveform editor");
            const dialogAvailable = isExtensionDialogAvailable(
                waveformEditorDialogService(),
            );
            openEditorButton.disabled = !dialogAvailable;
            openEditorButton.title = dialogAvailable
                ? "Open waveform editor"
                : "Waveform editor dialog is unavailable in this ComfyUI frontend";
            openEditorButton.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                node.openMusicalAudioWaveformEditor();
            });
            trimHeader.append(trimLabel, openEditorButton);
            const timelineViewport = makeElement(
                "div",
                "musical-audio-ui__timeline-viewport",
            );
            const timelineContent = makeElement(
                "div",
                "musical-audio-ui__timeline-content",
            );
            const timeRuler = makeElement("div", "musical-audio-ui__ruler");
            timelineContent.appendChild(timeRuler);

            const sliderBox = makeElement("div", "musical-audio-ui__timeline");
            const waveformCanvas = makeElement("canvas", "musical-audio-ui__waveform");
            waveformCanvas.setAttribute("aria-hidden", "true");
            const fill = makeElement("div", "musical-audio-ui__selection");
            const playhead = makeElement("div", "musical-audio-ui__playhead");
            playhead.setAttribute("aria-hidden", "true");
            sliderBox.append(waveformCanvas, fill);
            sliderBox.append(playhead);

            const startHandle = makeElement(
                "div",
                "musical-audio-ui__handle musical-audio-ui__handle--start",
            );
            const endHandle = makeElement(
                "div",
                "musical-audio-ui__handle musical-audio-ui__handle--end",
            );
            sliderBox.append(startHandle, endHandle);
            timelineContent.appendChild(sliderBox);
            timelineViewport.appendChild(timelineContent);
            trimArea.append(trimHeader, timelineViewport);
            container.appendChild(trimArea);

            // 7. Compact position/status line.
            const statusLine = makeElement(
                "div",
                "musical-audio-ui__status",
                "Seconds · 0.000–0.000 s · Frames 0–0",
            );
            container.appendChild(statusLine);

            const externalPresentationByWidget = new Map();
            for (const widgetName of MUSICAL_TIMING_INPUTS) {
                const control = controlByWidget.get(widgetName);
                if (!control) continue;
                externalPresentationByWidget.set(widgetName, {
                    control,
                    wrapper: control._musicalAudioControlWrap,
                    originalTitle: control.title,
                });
            }
            let statusBaseText = statusLine.textContent;
            let hasExternalTiming = false;
            const updateStatusText = () => {
                statusLine.textContent = hasExternalTiming
                    ? `${statusBaseText} \u00b7 ${EXTERNAL_PREVIEW_NOTICE}`
                    : statusBaseText;
            };
            const setStatusBaseText = (text) => {
                statusBaseText = text;
                updateStatusText();
            };
            node.refreshMusicalAudioExternalState = () => {
                if (node._musicalAudioRemoved) return;
                let anyConnected = false;
                for (const widgetName of MUSICAL_TIMING_INPUTS) {
                    const presentation = externalPresentationByWidget.get(widgetName);
                    if (!presentation) continue;
                    const connected = isMusicalAudioInputConnected(node, widgetName);
                    anyConnected ||= connected;
                    presentation.control.disabled = connected;
                    presentation.wrapper?.classList.toggle("is-external", connected);
                    if (connected) {
                        presentation.control.setAttribute("aria-disabled", "true");
                        presentation.control.title = EXTERNAL_CONTROL_TITLE;
                    } else {
                        presentation.control.removeAttribute("aria-disabled");
                        presentation.control.title = presentation.originalTitle;
                    }
                }
                hasExternalTiming = anyConnected;
                node._musicalAudioExternalRefreshPending = false;
                updateStatusText();
            };

            const domWidget = node.addDOMWidget("audio_ui", "audio_ui", container);
            domWidget._contentHeight = 250;
            domWidget.computeSize = function (width) {
                const nodeWidth = node.size?.[0] ?? width ?? 475;
                return [Math.max(10, nodeWidth - 30), Math.max(180, domWidget._contentHeight)];
            };

            const waveformRenderer = {
                animationFrameId: null,
                canvas: waveformCanvas,
                destroyed: false,
                playheadAnimationFrameId: null,
                playheadController: null,
                resizeObserver: null,
            };
            node._musicalAudioWaveformRenderer = waveformRenderer;
            waveformRenderer.playheadController = createPlayheadController({
                element: playhead,
                transport: audioTransport,
                runtime: waveformRenderer,
                requestFrame: requestAnimationFrame,
                cancelFrame: cancelAnimationFrame,
            });
            node.refreshMusicalAudioPlayhead = () => (
                node._musicalAudioWaveformRenderer?.playheadController?.render() ?? null
            );
            node.clearMusicalAudioPlayhead = () => {
                node._musicalAudioWaveformRenderer?.playheadController?.clear();
            };
            node.renderMusicalAudioWaveform = () => {
                const runtime = node._musicalAudioWaveformRenderer;
                if (!runtime || runtime.destroyed || !runtime.canvas) return null;
                const waveformState = node.getMusicalAudioWaveformState?.()
                    ?? node._musicalAudioWaveformState;
                if (!waveformState?.pyramid) {
                    return clearWaveformCanvas(runtime.canvas);
                }
                try {
                    return renderWaveformCanvas(runtime.canvas, waveformState.pyramid, {
                        startSeconds: 0,
                        endSeconds: waveformState.pyramid.durationSeconds,
                    });
                } catch {
                    try {
                        clearWaveformCanvas(runtime.canvas);
                    } catch {
                        // A malformed runtime Canvas must not break the remaining node UI.
                    }
                    return null;
                }
            };
            node.scheduleMusicalAudioWaveformRender = () => {
                const runtime = node._musicalAudioWaveformRenderer;
                if (
                    !runtime
                    || runtime.destroyed
                    || node._musicalAudioRemoved
                    || runtime.animationFrameId !== null
                ) return;
                runtime.animationFrameId = requestAnimationFrame(() => {
                    const currentRuntime = node._musicalAudioWaveformRenderer;
                    if (!currentRuntime || currentRuntime !== runtime) return;
                    currentRuntime.animationFrameId = null;
                    if (currentRuntime.destroyed || node._musicalAudioRemoved) return;
                    node.renderMusicalAudioWaveform();
                });
            };
            node.destroyMusicalAudioWaveformRenderer = () => {
                const runtime = node._musicalAudioWaveformRenderer;
                if (!runtime || runtime.destroyed) return;
                runtime.playheadController?.destroy();
                runtime.playheadController = null;
                runtime.destroyed = true;
                if (runtime.animationFrameId !== null) {
                    cancelAnimationFrame(runtime.animationFrameId);
                    runtime.animationFrameId = null;
                }
                runtime.resizeObserver?.disconnect();
                runtime.resizeObserver = null;
                if (runtime.canvas) {
                    runtime.canvas.width = 0;
                    runtime.canvas.height = 0;
                    runtime.canvas = null;
                }
                node._musicalAudioWaveformRenderer = null;
            };
            if (typeof ResizeObserver === "function") {
                waveformRenderer.resizeObserver = new ResizeObserver(() => {
                    node.scheduleMusicalAudioWaveformRender?.();
                });
                waveformRenderer.resizeObserver.observe(sliderBox);
            }
            node.scheduleMusicalAudioWaveformRender();

            let heightSyncQueued = false;
            let heightSyncUpdating = false;
            let resizeHeightSyncTimer = null;
            let lastObservedNodeWidth = Number.isFinite(node.size?.[0])
                ? node.size[0]
                : 475;
            node.syncMusicalAudioWidth = function () {
                const rawNodeWidth = this.size?.[0];
                const nodeWidth = Number.isFinite(rawNodeWidth) ? rawNodeWidth : 475;
                const targetWidth = Math.max(10, nodeWidth - 30);
                const renderedWidthChanged = container.style.width !== `${targetWidth}px`;
                const meaningfulWidthChange = Math.abs(
                    nodeWidth - lastObservedNodeWidth,
                ) >= WIDTH_CHANGE_TOLERANCE_PX;
                container.style.width = `${targetWidth}px`;
                container.style.maxWidth = `${targetWidth}px`;
                if (renderedWidthChanged && this.refreshMusicalAudioTimeline) {
                    this.refreshMusicalAudioTimeline();
                }
                if (meaningfulWidthChange) lastObservedNodeWidth = nodeWidth;
                return meaningfulWidthChange;
            };
            node.cancelMusicalAudioResizeHeightSync = function () {
                if (resizeHeightSyncTimer === null) return;
                clearTimeout(resizeHeightSyncTimer);
                resizeHeightSyncTimer = null;
            };
            node.scheduleMusicalAudioResizeHeightSync = function () {
                if (this._musicalAudioRemoved) return;
                this.cancelMusicalAudioResizeHeightSync();
                resizeHeightSyncTimer = setTimeout(() => {
                    resizeHeightSyncTimer = null;
                    if (node._musicalAudioRemoved) return;
                    node.scheduleMusicalAudioHeightSync();
                }, RESIZE_HEIGHT_SYNC_DELAY_MS);
            };
            node.scheduleMusicalAudioHeightSync = function () {
                this.cancelMusicalAudioResizeHeightSync();
                if (this._musicalAudioRemoved) return;
                if (heightSyncQueued || heightSyncUpdating) return;
                heightSyncQueued = true;
                requestAnimationFrame(() => {
                    heightSyncQueued = false;
                    if (node._musicalAudioRemoved) return;
                    node.cancelMusicalAudioResizeHeightSync();
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
                if (node._musicalAudioRemoved) return;
                const widgets = new Map((node.widgets || []).map((candidate) => [candidate.name, candidate]));
                const audioWidget = widgets.get("audio");
                const originalCallbacks = new Map();
                let internalUpdate = false;
                let audioDuration = 0;
                let dragging = null;
                let lastRulerKey = "";
                let metronomeScheduler = null;
                let metronomeOutputBus = null;
                let metronomeNextBeatIndex = null;
                let metronomeRunSignature = "";
                let metronomeLastMediaTime = null;
                let metronomeLastContextTime = null;
                let metronomeGeneration = 0;
                const metronomeClickResources = new Set();

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

                const storedSnapMode = () => SNAP_MODES.includes(widgetValue("snap_mode", "Off"))
                    ? widgetValue("snap_mode", "Off")
                    : "Off";

                const effectiveSnapMode = () => currentMode() === "Musical"
                    ? storedSnapMode()
                    : "Off";

                const visibleStructureChanged = () => {
                    const musical = currentMode() === "Musical";
                    const mode = musical ? "musical" : "seconds";
                    const frameNoteHidden = effectiveSnapMode() !== "Video Frame";
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

                const metronomeRunState = () => {
                    const playerDuration = audioEl.duration;
                    if (
                        node._musicalAudioRemoved
                        || !metronomeEnabled(node)
                        || currentMode() !== "Musical"
                        || audioEl.paused
                        || audioEl.ended
                        || !(Number.isFinite(audioDuration) && audioDuration > 0)
                        || !(Number.isFinite(playerDuration) && playerDuration > 0)
                    ) {
                        return null;
                    }
                    const playbackRate = audioEl.playbackRate;
                    if (!(Number.isFinite(playbackRate) && playbackRate > 0)) return null;
                    const selection = resolveSelection();
                    if (!(selection.end > selection.start)) return null;
                    return { selection, playbackRate, duration: playerDuration };
                };

                const metronomeSignature = (runState) => [
                    runState.selection.timing.secondsPerBeat,
                    runState.selection.timing.beatsPerBar,
                    runState.selection.timing.downbeatOffset,
                    runState.selection.start,
                    runState.selection.end,
                    runState.duration,
                    runState.playbackRate,
                    audioEl.currentSrc || audioEl.src,
                ].join(":");

                const disposeMetronomeClick = (resource, stopNow = false) => {
                    if (!metronomeClickResources.delete(resource)) return;
                    resource.oscillator.onended = null;
                    if (stopNow) {
                        const contextNow = resource.oscillator.context.currentTime;
                        try {
                            resource.gain.gain.cancelScheduledValues(contextNow);
                            resource.gain.gain.setValueAtTime(0, contextNow);
                        } catch {
                            // The node may already have ended during cleanup.
                        }
                        try {
                            resource.oscillator.stop(contextNow);
                        } catch {
                            // OscillatorNode.stop() throws when it has already stopped.
                        }
                    }
                    try {
                        resource.oscillator.disconnect();
                    } catch {
                        // Already disconnected.
                    }
                    try {
                        resource.gain.disconnect();
                    } catch {
                        // Already disconnected.
                    }
                };

                const cancelPendingMetronomeClicks = () => {
                    for (const resource of [...metronomeClickResources]) {
                        disposeMetronomeClick(resource, true);
                    }
                };

                const updateMetronomeOutputBusGain = (gainMultiplier) => {
                    if (!metronomeOutputBus) return;
                    const gainParam = metronomeOutputBus.gain;
                    const contextNow = metronomeOutputBus.context.currentTime;
                    let heldCurrentValue = false;
                    if (typeof gainParam.cancelAndHoldAtTime === "function") {
                        try {
                            gainParam.cancelAndHoldAtTime(contextNow);
                            heldCurrentValue = true;
                        } catch {
                            // Older Web Audio implementations use the fallback below.
                        }
                    }
                    if (!heldCurrentValue) {
                        const currentValue = gainParam.value;
                        gainParam.cancelScheduledValues(contextNow);
                        gainParam.setValueAtTime(currentValue, contextNow);
                    }
                    gainParam.linearRampToValueAtTime(
                        gainMultiplier,
                        contextNow + METRONOME_VOLUME_RAMP_SECONDS,
                    );
                };

                const stopMusicalAudioMetronome = () => {
                    metronomeGeneration += 1;
                    if (metronomeScheduler !== null) {
                        clearInterval(metronomeScheduler);
                        metronomeScheduler = null;
                    }
                    cancelPendingMetronomeClicks();
                    metronomeNextBeatIndex = null;
                    metronomeRunSignature = "";
                    metronomeLastMediaTime = null;
                    metronomeLastContextTime = null;
                };

                const ensureMetronomeOutputBus = (context) => {
                    if (metronomeOutputBus?.context === context) return metronomeOutputBus;
                    if (metronomeOutputBus) {
                        try {
                            metronomeOutputBus.disconnect();
                        } catch {
                            // Already disconnected.
                        }
                    }
                    metronomeOutputBus = context.createGain();
                    metronomeOutputBus.gain.value = currentMetronomeVolumeGain();
                    metronomeOutputBus.connect(context.destination);
                    metronomeVolumeTargets.add(updateMetronomeOutputBusGain);
                    return metronomeOutputBus;
                };

                const scheduleMetronomeClick = (context, contextTime, downbeat) => {
                    const outputBus = ensureMetronomeOutputBus(context);
                    const oscillator = context.createOscillator();
                    const gain = context.createGain();
                    const frequency = downbeat ? 1500 : 1000;
                    const peakGain = downbeat ? 0.13 : 0.07;
                    const duration = downbeat ? 0.045 : 0.035;
                    const startTime = Math.max(
                        context.currentTime + METRONOME_START_LEAD_SECONDS,
                        contextTime,
                    );
                    const attackEnd = startTime + 0.002;
                    const soundEnd = startTime + duration;

                    oscillator.frequency.setValueAtTime(frequency, startTime);
                    gain.gain.setValueAtTime(0.0001, startTime);
                    gain.gain.linearRampToValueAtTime(peakGain, attackEnd);
                    gain.gain.exponentialRampToValueAtTime(0.0001, soundEnd);
                    oscillator.connect(gain);
                    gain.connect(outputBus);

                    const resource = { oscillator, gain };
                    metronomeClickResources.add(resource);
                    oscillator.onended = () => disposeMetronomeClick(resource);
                    oscillator.start(startTime);
                    oscillator.stop(soundEnd + 0.005);
                };

                const resetMetronomeBeatPosition = (runState) => {
                    const firstMediaTime = Math.max(
                        audioEl.currentTime,
                        runState.selection.start,
                        0,
                    );
                    metronomeNextBeatIndex = metronomeBeatIndexAtOrAfter(
                        firstMediaTime,
                        runState.selection.timing,
                    );
                    metronomeLastMediaTime = null;
                    metronomeLastContextTime = null;
                };

                let reconcileMusicalAudioMetronome;

                const scheduleMetronomeWindow = (generation) => {
                    if (generation !== metronomeGeneration || node._musicalAudioRemoved) return;
                    const context = sharedMetronomeAudioContext;
                    const runState = metronomeRunState();
                    if (!context || context.state !== "running" || !runState) {
                        stopMusicalAudioMetronome();
                        return;
                    }

                    const currentSignature = metronomeSignature(runState);
                    if (currentSignature !== metronomeRunSignature) {
                        void reconcileMusicalAudioMetronome();
                        return;
                    }

                    const contextNow = context.currentTime;
                    const mediaNow = audioEl.currentTime;
                    if (!Number.isFinite(mediaNow)) {
                        stopMusicalAudioMetronome();
                        return;
                    }

                    if (
                        metronomeLastMediaTime !== null
                        && metronomeLastContextTime !== null
                    ) {
                        const expectedMediaTime = metronomeLastMediaTime
                            + (contextNow - metronomeLastContextTime) * runState.playbackRate;
                        if (
                            Math.abs(mediaNow - expectedMediaTime)
                            > METRONOME_JUMP_TOLERANCE_SECONDS
                        ) {
                            cancelPendingMetronomeClicks();
                            resetMetronomeBeatPosition(runState);
                        }
                    }

                    const timing = runState.selection.timing;
                    const scheduleThroughMediaTime = Math.min(
                        runState.selection.end,
                        runState.duration,
                        mediaNow
                            + METRONOME_SCHEDULE_AHEAD_SECONDS * runState.playbackRate,
                    );
                    let safetyCount = 0;
                    while (metronomeNextBeatIndex !== null && safetyCount < 256) {
                        const beatIndex = metronomeNextBeatIndex;
                        const beatTime = metronomeBeatTime(beatIndex, timing);
                        if (
                            beatTime < runState.selection.start - BEAT_BOUNDARY_EPSILON
                            || beatTime < 0 - BEAT_BOUNDARY_EPSILON
                            || beatTime < mediaNow - BEAT_BOUNDARY_EPSILON
                        ) {
                            metronomeNextBeatIndex += 1;
                            safetyCount += 1;
                            continue;
                        }
                        if (
                            beatTime >= runState.selection.end - BEAT_BOUNDARY_EPSILON
                            || beatTime >= runState.duration - BEAT_BOUNDARY_EPSILON
                            || beatTime > scheduleThroughMediaTime + BEAT_BOUNDARY_EPSILON
                        ) {
                            break;
                        }

                        const contextTime = contextNow
                            + (beatTime - mediaNow) / runState.playbackRate;
                        scheduleMetronomeClick(
                            context,
                            contextTime,
                            isMetronomeDownbeat(beatIndex, timing.beatsPerBar),
                        );
                        metronomeNextBeatIndex += 1;
                        safetyCount += 1;
                    }

                    metronomeLastMediaTime = mediaNow;
                    metronomeLastContextTime = contextNow;
                };

                reconcileMusicalAudioMetronome = async ({
                    allowContextCreation = false,
                    ensureContextWhilePaused = false,
                } = {}) => {
                    stopMusicalAudioMetronome();
                    const initialRunState = metronomeRunState();
                    if (!initialRunState && !ensureContextWhilePaused) return;

                    const generation = metronomeGeneration;
                    const context = await ensureSharedMetronomeContext(allowContextCreation);
                    if (
                        generation !== metronomeGeneration
                        || node._musicalAudioRemoved
                        || !context
                    ) {
                        return;
                    }

                    const runState = metronomeRunState();
                    if (!runState) return;
                    ensureMetronomeOutputBus(context);
                    metronomeRunSignature = metronomeSignature(runState);
                    resetMetronomeBeatPosition(runState);
                    scheduleMetronomeWindow(generation);
                    if (generation !== metronomeGeneration || metronomeScheduler !== null) return;
                    metronomeScheduler = setInterval(
                        () => scheduleMetronomeWindow(generation),
                        METRONOME_SCHEDULER_INTERVAL_MS,
                    );
                };

                node.stopMusicalAudioMetronome = stopMusicalAudioMetronome;
                node.destroyMusicalAudioMetronome = () => {
                    stopMusicalAudioMetronome();
                    metronomeVolumeTargets.delete(updateMetronomeOutputBusGain);
                    if (metronomeOutputBus) {
                        try {
                            metronomeOutputBus.disconnect();
                        } catch {
                            // Already disconnected.
                        }
                        metronomeOutputBus = null;
                    }
                };

                const setControlValue = (control, value, force = false) => {
                    if (
                        !control
                        || (
                            document.activeElement === control
                            && (control.type === "text" || !force)
                        )
                    ) {
                        return;
                    }
                    const next = typeof value === "number" ? formatInputValue(value) : String(value);
                    if (control.value !== next) control.value = next;
                };

                const syncControls = (state, force = false) => {
                    for (const [mode, button] of modeButtons) {
                        const active = mode === state.mode;
                        button.classList.toggle("is-active", active);
                        button.setAttribute("aria-pressed", active ? "true" : "false");
                    }
                    syncInput.checked = syncOnSwitchEnabled(node);
                    metronomeInput.checked = metronomeEnabled(node);
                    setControlValue(snapSelect, storedSnapMode(), force);
                    container.dataset.mode = state.mode.toLowerCase();
                    frameFallbackNote.classList.toggle(
                        "is-hidden",
                        effectiveSnapMode() !== "Video Frame",
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

                const renderMusicalRuler = (timing, effectiveWidth) => {
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
                    const maximumLabels = Math.max(1, Math.floor(effectiveWidth / 48));
                    const labelStride = Math.max(1, Math.ceil(visibleBars / maximumLabels));
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
                    const effectiveWidth = Math.max(1, timelineContent.clientWidth);
                    const rulerKey = state.mode === "Seconds"
                        ? `Seconds:${audioDuration}:${effectiveWidth}`
                        : [
                            "Musical",
                            audioDuration,
                            effectiveWidth,
                            state.timing.secondsPerSubdivision,
                            state.timing.beatsPerBar,
                            state.timing.subdivisionsPerBeat,
                            state.timing.downbeatOffset,
                        ].join(":");
                    if (rulerKey === lastRulerKey) return;
                    lastRulerKey = rulerKey;
                    timeRuler.replaceChildren();
                    if (!(audioDuration > 0)) return;
                    if (state.mode === "Musical") {
                        renderMusicalRuler(state.timing, effectiveWidth);
                    }
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
                        setStatusBaseText([
                            `B${position.bar} · Beat ${position.beat} · Sub ${position.subdivision}`,
                            lengthText,
                            `${state.start.toFixed(3)}–${state.end.toFixed(3)} s`,
                            `Frames ${state.startFrame}–${state.frameEnd}`,
                            state.clamped ? "clamped to audio" : "",
                        ].filter(Boolean).join(" | "));
                    } else {
                        trimLength.textContent = `Trimmed: ${state.selectionDuration.toFixed(3)} s · ${state.frameCount} frames`;
                        setStatusBaseText([
                            "Seconds",
                            `${state.start.toFixed(3)}–${state.end.toFixed(3)} s`,
                            `Frames ${state.startFrame}–${state.frameEnd}`,
                            state.clamped ? "clamped to audio" : "",
                        ].filter(Boolean).join(" · "));
                    }
                };

                const seekToSelectionStart = (state) => {
                    if (audioEl.readyState >= 1 && audioDuration > 0) {
                        audioTransport.seek(clamp(state.start, 0, audioDuration));
                    }
                };

                const refreshUI = (seek = false, forceControls = false, recomputeLayout = false) => {
                    const state = resolveSelection();
                    syncControls(state, forceControls);
                    node.refreshMusicalAudioExternalState();
                    renderRuler(state);
                    renderSelection(state);
                    if (seek) seekToSelectionStart(state);
                    if (recomputeLayout) node.scheduleMusicalAudioHeightSync();
                    return state;
                };

                const updateAudioSource = () => {
                    const selectedAudioValue = audioWidget?.value;
                    const selectedAudioFilename = selectedAudioValue == null
                        ? ""
                        : String(selectedAudioValue);
                    if (
                        selectedAudioFilename.trim() === ""
                        || selectedAudioFilename.toLowerCase() === "none"
                    ) {
                        waveformLoader.clear();
                        stopMusicalAudioMetronome();
                        node.clearMusicalAudioPlayhead();
                        playerTitle.textContent = "No audio selected";
                        audioDuration = 0;
                        audioEl.removeAttribute("src");
                        audioEl.load();
                        lastRulerKey = "";
                        refreshUI(false, true);
                        return;
                    }
                    void waveformLoader.load(selectedAudioFilename);
                    let filename = selectedAudioFilename;
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
                    if (audioEl.src !== source) {
                        stopMusicalAudioMetronome();
                        node.clearMusicalAudioPlayhead();
                        audioEl.src = source;
                    }
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

                const writeTransferredSecondsRange = (start, end) => {
                    const safeStart = storedNumber(start);
                    const safeEnd = storedNumber(end);
                    if (safeStart === null || safeEnd === null) return false;
                    const safeDuration = storedNumber(Math.max(0, safeEnd - safeStart));
                    if (safeDuration === null) return false;
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

                const activeChangeComplete = (
                    seek = true,
                    recomputeLayout = false,
                    resyncMetronome = true,
                ) => {
                    if (resyncMetronome) stopMusicalAudioMetronome();
                    lastRulerKey = "";
                    refreshUI(seek, true, recomputeLayout);
                    dirtyGraph(false);
                    if (resyncMetronome) {
                        void reconcileMusicalAudioMetronome({ allowContextCreation: true });
                    }
                };

                const writeAndDetectWidgetChanges = (names, write) => {
                    const before = names.map((name) => widgetValue(name, undefined));
                    if (write() === false) return false;
                    return names.some((name, index) => {
                        const current = widgetValue(name, undefined);
                        return !Object.is(current, before[index]) && current !== before[index];
                    });
                };

                const setCanonicalDecimalControlValue = (control, value) => {
                    const next = typeof value === "number"
                        ? formatInputValue(value)
                        : String(value);
                    if (control.value !== next) control.value = next;
                };

                const bindDecimalControl = (control, {
                    isAllowed = () => true,
                    write,
                    displayValue,
                }) => {
                    const commit = () => {
                        if (isIntermediateDecimalText(control.value)) {
                            return { committed: false, changed: false };
                        }
                        const parsed = parseLocalizedDecimal(control.value);
                        if (parsed === null || !isAllowed(parsed)) {
                            return { committed: false, changed: false };
                        }
                        const stored = storedNumber(parsed);
                        if (stored === null || !isAllowed(stored)) {
                            return { committed: false, changed: false };
                        }
                        return { committed: true, changed: write(stored) };
                    };

                    const commitAndRefresh = () => {
                        const result = commit();
                        if (result.changed) activeChangeComplete(true);
                        return result;
                    };

                    control.addEventListener("input", commitAndRefresh);
                    control.addEventListener("blur", () => {
                        commitAndRefresh();
                        setCanonicalDecimalControlValue(control, displayValue());
                    });
                    control.addEventListener("keydown", (event) => {
                        if (event.key !== "Enter") return;
                        event.preventDefault();
                        const result = commitAndRefresh();
                        if (result.committed) {
                            control.blur();
                        } else {
                            setCanonicalDecimalControlValue(control, displayValue());
                        }
                    });
                };

                const switchEditMode = (targetMode) => {
                    const sourceMode = currentMode();
                    if (targetMode === sourceMode || node._syncingMusicalAudioMode) return;
                    if (targetMode === "Seconds") stopMusicalAudioMetronome();

                    node._syncingMusicalAudioMode = true;
                    try {
                        const sourceState = resolveSelection();
                        let feedbackMessage = "";
                        const shouldSync = syncOnSwitchEnabled(node);
                        if (shouldSync) {
                            if (sourceMode === "Seconds") {
                                const converted = secondsRangeToMusicalSelection({
                                    startSeconds: sourceState.start,
                                    endSeconds: sourceState.end,
                                }, sourceState.timing);
                                writeMusicalSelection(
                                    converted.startIndex,
                                    converted.subdivisionCount,
                                );
                                const divisions = sourceState.timing.subdivisionsPerBeat;
                                feedbackMessage = divisions === 1
                                    ? "Synced \u00b7 beat grid"
                                    : `Synced \u00b7 quantized to 1/${divisions}-beat grid`;
                            } else {
                                const converted = musicalSelectionToSecondsRange({
                                    startIndex: sourceState.startIndex,
                                    subdivisionCount: sourceState.subdivisionCount,
                                }, sourceState.timing);
                                const durationKnown = audioEl.readyState >= 1
                                    && Number.isFinite(audioEl.duration)
                                    && audioEl.duration >= 0;
                                let start;
                                let end;
                                if (durationKnown) {
                                    const availableDuration = Math.max(0, audioEl.duration);
                                    start = clamp(converted.startSeconds, 0, availableDuration);
                                    end = clamp(converted.endSeconds, start, availableDuration);
                                } else {
                                    start = Math.max(0, converted.startSeconds);
                                    end = start + converted.durationSeconds;
                                }
                                writeTransferredSecondsRange(start, end);
                                feedbackMessage = "Synced to seconds";
                            }
                        } else {
                            node.clearMusicalAudioSyncFeedback();
                        }

                        setWidgetValue("edit_mode", targetMode);
                        lastRulerKey = "";
                        refreshUI(true, true, true);
                        dirtyGraph(false);
                        if (feedbackMessage) showSyncFeedback(feedbackMessage);
                        void reconcileMusicalAudioMetronome({
                            allowContextCreation: true,
                        });
                    } finally {
                        node._syncingMusicalAudioMode = false;
                    }
                };

                for (const [mode, button] of modeButtons) {
                    button.addEventListener("click", () => {
                        switchEditMode(mode);
                    });
                }
                syncInput.addEventListener("change", () => {
                    syncOnSwitchEnabled(node);
                    if (node.properties.musical_audio_sync_on_switch === syncInput.checked) return;
                    node.properties.musical_audio_sync_on_switch = syncInput.checked;
                    if (!syncInput.checked) node.clearMusicalAudioSyncFeedback();
                    dirtyGraph(false);
                });
                metronomeInput.addEventListener("change", () => {
                    metronomeEnabled(node);
                    if (
                        node.properties.musical_audio_metronome_enabled
                        === metronomeInput.checked
                    ) {
                        return;
                    }
                    node.properties.musical_audio_metronome_enabled = metronomeInput.checked;
                    if (metronomeInput.checked) {
                        void reconcileMusicalAudioMetronome({
                            allowContextCreation: true,
                            ensureContextWhilePaused: true,
                        });
                    } else {
                        stopMusicalAudioMetronome();
                    }
                    dirtyGraph(false);
                });
                snapSelect.addEventListener("change", () => {
                    if (!SNAP_MODES.includes(snapSelect.value)) return;
                    setWidgetValue("snap_mode", snapSelect.value);
                    activeChangeComplete(false, visibleStructureChanged(), false);
                });

                const secondsWidgetNames = ["start_time", "end_time", "duration"];

                bindDecimalControl(secondsStartInput, {
                    isAllowed: (value) => value >= 0,
                    write: (value) => writeAndDetectWidgetChanges(secondsWidgetNames, () => {
                        const state = resolveSelection();
                        const maximum = audioDuration > 0 ? audioDuration : value;
                        const resolvedEnd = audioDuration > 0
                            ? state.end
                            : Math.max(value, finiteNumber(widgetValue("end_time", 0), 0));
                        const start = clamp(
                            value,
                            0,
                            Math.max(0, Math.min(maximum, resolvedEnd)),
                        );
                        const endStored = finiteNumber(widgetValue("end_time", 0), 0) <= 0
                            ? 0
                            : resolvedEnd;
                        return writeSecondsRange(start, endStored, resolvedEnd);
                    }),
                    displayValue: () => finiteNumber(widgetValue("start_time", 0), 0),
                });

                bindDecimalControl(secondsEndInput, {
                    isAllowed: (value) => value >= 0,
                    write: (value) => writeAndDetectWidgetChanges(secondsWidgetNames, () => {
                        const state = resolveSelection();
                        const start = state.start;
                        if (value <= 0) {
                            const resolvedEnd = audioDuration > 0 ? audioDuration : start;
                            return writeSecondsRange(start, 0, resolvedEnd);
                        }
                        const maximum = audioDuration > 0 ? audioDuration : value;
                        const resolvedEnd = clamp(value, start, Math.max(start, maximum));
                        return writeSecondsRange(start, resolvedEnd, resolvedEnd);
                    }),
                    displayValue: () => finiteNumber(widgetValue("end_time", 0), 0),
                });

                bindDecimalControl(secondsDurationInput, {
                    isAllowed: (value) => value >= 0,
                    write: (value) => writeAndDetectWidgetChanges(secondsWidgetNames, () => {
                        let length = value;
                        let start = Math.max(0, finiteNumber(widgetValue("start_time", 0), 0));
                        if (audioDuration > 0) {
                            length = Math.min(length, audioDuration);
                            start = Math.min(start, audioDuration);
                            if (start + length > audioDuration) start = audioDuration - length;
                        }
                        const end = start + length;
                        return writeSecondsRange(start, end, end);
                    }),
                    displayValue: () => currentMode() === "Seconds"
                        ? resolveSelection().selectionDuration
                        : finiteNumber(widgetValue("duration", 0), 0),
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
                const decimalTimingWidgetNames = new Set(["bpm", "fps", "downbeat_offset"]);

                for (const [name, control] of controlByWidget) {
                    if (decimalTimingWidgetNames.has(name)) {
                        bindDecimalControl(control, {
                            isAllowed: (value) => name === "downbeat_offset" || value > 0,
                            write: (value) => writeAndDetectWidgetChanges(
                                [name],
                                () => setWidgetValue(name, value),
                            ),
                            displayValue: () => finiteNumber(widgetValue(name, 0), 0),
                        });
                        continue;
                    }
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
                    const snap = effectiveSnapMode();
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
                                ? musicalPointerIndex(pointerSeconds, state.timing, effectiveSnapMode()) - state.startIndex
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
                    node.scheduleMusicalAudioWaveformRender?.();
                    dirtyGraph(false);
                    if (!audioEl.paused) void reconcileMusicalAudioMetronome();
                });
                audioEl.addEventListener("durationchange", () => {
                    if (Number.isFinite(audioEl.duration) && audioEl.duration >= 0) {
                        audioDuration = audioEl.duration;
                        lastRulerKey = "";
                        refreshUI(false, false);
                        node.scheduleMusicalAudioWaveformRender?.();
                        if (!audioEl.paused) void reconcileMusicalAudioMetronome();
                    }
                });
                audioEl.addEventListener("error", () => {
                    stopMusicalAudioMetronome();
                    node.clearMusicalAudioPlayhead();
                    audioDuration = 0;
                    lastRulerKey = "";
                    refreshUI(false, false);
                });
                audioEl.addEventListener("timeupdate", () => {
                    if (dragging || !(audioDuration > 0)) return;
                    const state = resolveSelection();
                    if (audioEl.currentTime >= state.end) {
                        audioEl.pause();
                        audioTransport.seek(state.start);
                    }
                });
                audioEl.addEventListener("play", () => {
                    const state = resolveSelection();
                    if (state.end <= state.start) {
                        audioEl.pause();
                        seekToSelectionStart(state);
                    } else if (audioEl.currentTime < state.start || audioEl.currentTime >= state.end) {
                        audioTransport.seek(state.start);
                    }
                    void reconcileMusicalAudioMetronome({ allowContextCreation: true });
                });
                audioEl.addEventListener("pause", stopMusicalAudioMetronome);
                audioEl.addEventListener("ended", stopMusicalAudioMetronome);
                audioEl.addEventListener("emptied", stopMusicalAudioMetronome);
                audioEl.addEventListener("seeking", stopMusicalAudioMetronome);
                audioEl.addEventListener("seeked", () => {
                    if (!audioEl.paused) void reconcileMusicalAudioMetronome();
                });
                audioEl.addEventListener("ratechange", () => {
                    if (!audioEl.paused) {
                        void reconcileMusicalAudioMetronome({ allowContextCreation: true });
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
                            stopMusicalAudioMetronome();
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
                                if (node._musicalAudioRemoved) return;
                                lastRulerKey = "";
                                refreshUI(true, false, visibleStructureChanged());
                                dirtyGraph(false);
                                if (!audioEl.paused) void reconcileMusicalAudioMetronome();
                            }, 0);
                        }
                        return callbackResult;
                    };
                }

                node.refreshMusicalAudioUI = (recomputeLayout = false) => {
                    updateAudioSource();
                    lastRulerKey = "";
                    refreshUI(false, true, recomputeLayout);
                    if (!audioEl.paused) void reconcileMusicalAudioMetronome();
                };
                node.refreshMusicalAudioTimeline = () => {
                    lastRulerKey = "";
                    const state = resolveSelection();
                    renderRuler(state);
                    renderSelection(state);
                    node.scheduleMusicalAudioWaveformRender?.();
                };

                updateAudioSource();
                refreshUI(false, true, true);
                setTimeout(() => {
                    if (node._musicalAudioRemoved) return;
                    node._initializingMusicalAudio = false;
                }, 500);
            }, 100);

            return result;
        };
    },
});
