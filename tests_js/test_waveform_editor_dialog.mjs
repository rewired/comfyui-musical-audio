import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
    WAVEFORM_EDITOR_DIALOG_KEY_PREFIX,
    WAVEFORM_EDITOR_SPIKE_COMPONENT,
    isExtensionDialogAvailable,
    openWaveformEditorDialog,
    waveformEditorDialogKey,
} from "../js/waveform_editor_dialog.js";


const MODULE_SOURCE = readFileSync(
    new URL("../js/waveform_editor_dialog.js", import.meta.url),
    "utf8",
);


function createDialogService() {
    const state = {
        calls: [],
        closeCalls: new Map(),
    };
    const service = {
        showExtensionDialog(options) {
            state.calls.push(options);
            return {
                dialog: { key: options.key },
                closeDialog() {
                    state.closeCalls.set(
                        options.key,
                        (state.closeCalls.get(options.key) || 0) + 1,
                    );
                    options.dialogComponentProps.onClose?.();
                },
            };
        },
    };
    return { service, state };
}


test("dialog key prefix is stable", () => {
    assert.equal(
        WAVEFORM_EDITOR_DIALOG_KEY_PREFIX,
        "extension-comfyui-musical-audio-waveform-editor-",
    );
});

test("same node ID produces the same key", () => {
    assert.equal(waveformEditorDialogKey(42), waveformEditorDialogKey(42));
});

test("different node IDs produce different keys", () => {
    assert.notEqual(waveformEditorDialogKey(42), waveformEditorDialogKey(43));
});

test("invalid node IDs are rejected deterministically", () => {
    const invalidIds = [null, undefined, "", "  ", NaN, Infinity, -1, 1.5, {}, []];
    for (const value of invalidIds) {
        assert.throws(
            () => waveformEditorDialogKey(value),
            {
                name: "TypeError",
                message: "Waveform editor dialog requires a valid node ID",
            },
        );
    }
});

test("availability is true for a valid service", () => {
    assert.equal(isExtensionDialogAvailable(createDialogService().service), true);
});

test("availability is false for a missing service", () => {
    assert.equal(isExtensionDialogAvailable(null), false);
});

test("availability is false without showExtensionDialog", () => {
    assert.equal(isExtensionDialogAvailable({}), false);
});

test("spike component has the expected name", () => {
    assert.equal(WAVEFORM_EDITOR_SPIKE_COMPONENT.name, "MusicalAudioWaveformEditorSpike");
});

test("component render includes nodeId", () => {
    const text = WAVEFORM_EDITOR_SPIKE_COMPONENT.render.call({
        nodeId: 42,
        sourceLabel: "song.wav",
    });
    assert.match(text, /Node 42/);
});

test("component render includes sourceLabel", () => {
    const text = WAVEFORM_EDITOR_SPIKE_COMPONENT.render.call({
        nodeId: 42,
        sourceLabel: "song.wav",
    });
    assert.match(text, /Source: song\.wav/);
});

test("empty source becomes No audio selected", () => {
    for (const sourceLabel of ["", "   ", "none", null, undefined]) {
        const text = WAVEFORM_EDITOR_SPIKE_COMPONENT.render.call({
            nodeId: 42,
            sourceLabel,
        });
        assert.match(text, /Source: No audio selected$/);
    }
});

test("adapter calls showExtensionDialog exactly once", () => {
    const { service, state } = createDialogService();
    openWaveformEditorDialog({ dialogService: service, nodeId: 42, sourceLabel: "a.wav" });
    assert.equal(state.calls.length, 1);
});

test("adapter supplies the expected key", () => {
    const { service, state } = createDialogService();
    openWaveformEditorDialog({ dialogService: service, nodeId: 42, sourceLabel: "a.wav" });
    assert.equal(state.calls[0].key, waveformEditorDialogKey(42));
});

test("adapter supplies title Waveform Editor", () => {
    const { service, state } = createDialogService();
    openWaveformEditorDialog({ dialogService: service, nodeId: 42, sourceLabel: "a.wav" });
    assert.equal(state.calls[0].title, "Waveform Editor");
});

test("adapter supplies the spike component", () => {
    const { service, state } = createDialogService();
    openWaveformEditorDialog({ dialogService: service, nodeId: 42, sourceLabel: "a.wav" });
    assert.equal(state.calls[0].component, WAVEFORM_EDITOR_SPIKE_COMPONENT);
});

test("adapter supplies nodeId and sourceLabel props", () => {
    const { service, state } = createDialogService();
    openWaveformEditorDialog({ dialogService: service, nodeId: 42, sourceLabel: "a.wav" });
    assert.deepEqual(state.calls[0].props, { nodeId: 42, sourceLabel: "a.wav" });
});

test("adapter supplies supported modal and closing options", () => {
    const { service, state } = createDialogService();
    openWaveformEditorDialog({ dialogService: service, nodeId: 42, sourceLabel: "a.wav" });
    const props = state.calls[0].dialogComponentProps;
    assert.equal(props.modal, true);
    assert.equal(props.closable, true);
    assert.equal(props.closeOnEscape, true);
    assert.equal("renderer" in props, false);
    assert.equal("maximizable" in props, false);
});

test("unsupported service returns null", () => {
    assert.equal(openWaveformEditorDialog({ dialogService: null, nodeId: 42 }), null);
});

test("returned close delegates to the public close function", () => {
    const { service, state } = createDialogService();
    const handle = openWaveformEditorDialog({
        dialogService: service,
        nodeId: 42,
        sourceLabel: "a.wav",
    });
    handle.close();
    assert.equal(state.closeCalls.get(handle.key), 1);
});

test("close is idempotent", () => {
    const { service, state } = createDialogService();
    const handle = openWaveformEditorDialog({ dialogService: service, nodeId: 42 });
    handle.close();
    handle.close();
    assert.equal(state.closeCalls.get(handle.key), 1);
});

test("onClose is forwarded", () => {
    const { service, state } = createDialogService();
    let calls = 0;
    const handle = openWaveformEditorDialog({
        dialogService: service,
        nodeId: 42,
        onClose: () => { calls += 1; },
    });
    state.calls[0].dialogComponentProps.onClose();
    state.calls[0].dialogComponentProps.onClose();
    handle.close();
    assert.equal(calls, 1);
    assert.equal(state.closeCalls.get(handle.key), undefined);
});

test("subscriber failure does not recurse or interrupt public close", () => {
    const { service, state } = createDialogService();
    const handle = openWaveformEditorDialog({
        dialogService: service,
        nodeId: 42,
        onClose: () => { throw new Error("consumer failure"); },
    });
    assert.doesNotThrow(() => handle.close());
    handle.close();
    assert.equal(state.closeCalls.get(handle.key), 1);
});

test("two node handles remain independent", () => {
    const { service, state } = createDialogService();
    const first = openWaveformEditorDialog({ dialogService: service, nodeId: 1 });
    const second = openWaveformEditorDialog({ dialogService: service, nodeId: 2 });
    first.close();
    assert.equal(state.closeCalls.get(first.key), 1);
    assert.equal(state.closeCalls.get(second.key), undefined);
    second.close();
    assert.equal(state.closeCalls.get(second.key), 1);
});

test("adapter does not expose or require audioEl", () => {
    assert.doesNotMatch(MODULE_SOURCE, /audioEl/);
});

test("adapter imports no Vue or internal ComfyUI module", () => {
    assert.doesNotMatch(MODULE_SOURCE, /\bfrom\s+["'][^"']*(?:vue|stores|components\/dialog)/i);
    assert.doesNotMatch(MODULE_SOURCE, /useDialogStore|dialogStack|GlobalDialog/);
});

test("adapter contains no graph-dirty behavior", () => {
    assert.doesNotMatch(MODULE_SOURCE, /setDirtyCanvas|graph\.change|graph\.setDirty/);
});

test("adapter contains no transport or waveform data access", () => {
    assert.doesNotMatch(
        MODULE_SOURCE,
        /audioTransport|waveformRenderer|waveformPeaks|waveformData|fetch\(/,
    );
});

test("adapter contains no compiler or innerHTML usage", () => {
    assert.doesNotMatch(MODULE_SOURCE, /createApp|innerHTML|\btemplate\s*:/);
});

test("adapter rejects an installed result without public closeDialog", () => {
    const dialogService = {
        showExtensionDialog: () => ({ dialog: { key: "extension-test" } }),
    };
    assert.throws(
        () => openWaveformEditorDialog({ dialogService, nodeId: 42 }),
        /did not return a public close function/,
    );
});

test("same-node adapter calls retain the same public key", () => {
    const { service, state } = createDialogService();
    openWaveformEditorDialog({ dialogService: service, nodeId: 42 });
    openWaveformEditorDialog({ dialogService: service, nodeId: 42 });
    assert.equal(state.calls.length, 2);
    assert.equal(state.calls[0].key, state.calls[1].key);
});
