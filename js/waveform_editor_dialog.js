export const WAVEFORM_EDITOR_DIALOG_KEY_PREFIX =
    "extension-comfyui-musical-audio-waveform-editor-";


function normalizeNodeId(nodeId) {
    if (typeof nodeId === "number") {
        if (!Number.isFinite(nodeId) || !Number.isInteger(nodeId) || nodeId < 0) {
            throw new TypeError("Waveform editor dialog requires a valid node ID");
        }
        return String(nodeId);
    }

    if (typeof nodeId === "string") {
        const normalized = nodeId.trim();
        if (normalized) return normalized;
    }

    throw new TypeError("Waveform editor dialog requires a valid node ID");
}


function displaySourceLabel(sourceLabel) {
    const normalized = sourceLabel == null ? "" : String(sourceLabel).trim();
    return normalized && normalized.toLowerCase() !== "none"
        ? normalized
        : "No audio selected";
}


export const WAVEFORM_EDITOR_SPIKE_COMPONENT = Object.freeze({
    name: "MusicalAudioWaveformEditorSpike",
    props: {
        nodeId: {
            type: [Number, String],
            required: true,
        },
        sourceLabel: {
            type: String,
            default: "",
        },
    },
    render() {
        return [
            "Waveform editor mounting spike",
            `Node ${String(this.nodeId)}`,
            `Source: ${displaySourceLabel(this.sourceLabel)}`,
        ].join(" · ");
    },
});


export function waveformEditorDialogKey(nodeId) {
    return `${WAVEFORM_EDITOR_DIALOG_KEY_PREFIX}${encodeURIComponent(normalizeNodeId(nodeId))}`;
}


export function isExtensionDialogAvailable(dialogService) {
    return typeof dialogService?.showExtensionDialog === "function";
}


export function openWaveformEditorDialog({
    dialogService,
    nodeId,
    sourceLabel,
    onClose,
} = {}) {
    if (!isExtensionDialogAvailable(dialogService)) return null;

    const key = waveformEditorDialogKey(nodeId);
    let closeRequested = false;
    let closeNotified = false;
    const notifyClose = () => {
        closeRequested = true;
        if (closeNotified) return;
        closeNotified = true;
        if (typeof onClose !== "function") return;
        try {
            onClose();
        } catch {
            // A consumer callback must not interrupt the dialog service cleanup.
        }
    };

    const rawResult = dialogService.showExtensionDialog({
        key,
        title: "Waveform Editor",
        component: WAVEFORM_EDITOR_SPIKE_COMPONENT,
        props: {
            nodeId,
            sourceLabel,
        },
        dialogComponentProps: {
            modal: true,
            closable: true,
            closeOnEscape: true,
            onClose: notifyClose,
        },
    });

    if (typeof rawResult?.closeDialog !== "function") {
        throw new TypeError(
            "ComfyUI showExtensionDialog did not return a public close function",
        );
    }

    return {
        key,
        rawResult,
        close() {
            if (closeRequested) return;
            closeRequested = true;
            rawResult.closeDialog();
        },
    };
}
