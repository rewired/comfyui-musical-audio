import {
    decodeWaveformPeaks,
    WAVEFORM_PEAK_FORMAT_VERSION,
} from "./waveform_peaks.js";


export const WAVEFORM_PEAK_ENDPOINT = "/comfyui-musical-audio/waveform-peaks";
export const WAVEFORM_PEAK_CONTENT_TYPE = "application/vnd.comfyui-musical-audio.waveform-peaks";
export const WAVEFORM_LOAD_STATUS = Object.freeze({
    IDLE: "idle",
    LOADING: "loading",
    READY: "ready",
    ERROR: "error",
});


function idleState() {
    return Object.freeze({
        status: WAVEFORM_LOAD_STATUS.IDLE,
        filename: null,
        pyramid: null,
        error: null,
    });
}

function loadState(status, filename, pyramid = null, error = null) {
    return Object.freeze({ status, filename, pyramid, error });
}

function loadError(kind, httpStatus) {
    return Object.freeze({ kind, httpStatus });
}

function sameState(left, right) {
    return (
        left.status === right.status
        && left.filename === right.filename
        && left.pyramid === right.pyramid
        && left.error === right.error
    );
}

function isSelectableFilename(filename) {
    return (
        typeof filename === "string"
        && filename.trim() !== ""
        && filename.toLowerCase() !== "none"
    );
}

export function waveformPeakRequestPath(filename) {
    if (!isSelectableFilename(filename)) {
        throw new TypeError("filename must be a non-empty selected filename");
    }
    const query = new URLSearchParams({ filename });
    return `${WAVEFORM_PEAK_ENDPOINT}?${query.toString()}`;
}

export function createWaveformPeakLoader({ fetchResponse, onStateChange } = {}) {
    if (typeof fetchResponse !== "function") {
        throw new TypeError("fetchResponse must be a function");
    }
    if (onStateChange != null && typeof onStateChange !== "function") {
        throw new TypeError("onStateChange must be a function when provided");
    }

    let state = idleState();
    let stateChangeCallback = onStateChange ?? null;
    let activeController = null;
    let inFlightPromise = null;
    let generation = 0;
    let disposed = false;

    const publish = (nextState) => {
        if (sameState(state, nextState)) return state;
        state = nextState;
        if (!disposed && stateChangeCallback) {
            try {
                stateChangeCallback(state);
            } catch {}
        }
        return state;
    };

    const isCurrent = (requestGeneration, filename) => (
        !disposed
        && generation === requestGeneration
        && state.filename === filename
    );

    const publishError = (requestGeneration, filename, kind, httpStatus) => {
        if (!isCurrent(requestGeneration, filename)) return state;
        return publish(loadState(
            WAVEFORM_LOAD_STATUS.ERROR,
            filename,
            null,
            loadError(kind, httpStatus),
        ));
    };

    const requestOptions = (signal, cache) => ({
        method: "GET",
        signal,
        cache,
        headers: {
            Accept: WAVEFORM_PEAK_CONTENT_TYPE,
        },
    });

    const fetchOnce = async (path, signal, cache) => {
        try {
            return {
                failed: false,
                response: await fetchResponse(path, requestOptions(signal, cache)),
            };
        } catch (error) {
            return { failed: true, error };
        }
    };

    const runRequest = async (
        path,
        filename,
        requestGeneration,
        signal,
    ) => {
        try {
            let result = await fetchOnce(path, signal, "no-cache");
            if (!isCurrent(requestGeneration, filename)) return state;
            if (result.failed) {
                if (result.error?.name === "AbortError") return state;
                return publishError(requestGeneration, filename, "network", null);
            }

            let response = result.response;
            if (response?.status === 304) {
                if (state.pyramid) {
                    return publish(loadState(
                        WAVEFORM_LOAD_STATUS.READY,
                        filename,
                        state.pyramid,
                    ));
                }
                result = await fetchOnce(path, signal, "reload");
                if (!isCurrent(requestGeneration, filename)) return state;
                if (result.failed) {
                    if (result.error?.name === "AbortError") return state;
                    return publishError(requestGeneration, filename, "network", null);
                }
                response = result.response;
            }

            if (!isCurrent(requestGeneration, filename)) return state;
            const httpStatus = Number.isInteger(response?.status) ? response.status : null;
            if (response?.status !== 200 || response.ok !== true) {
                return publishError(requestGeneration, filename, "http", httpStatus);
            }

            let contentType;
            let versionHeader;
            try {
                contentType = response.headers.get("Content-Type");
                versionHeader = response.headers.get("X-Musical-Audio-Waveform-Version");
            } catch {
                return publishError(requestGeneration, filename, "protocol", 200);
            }
            const baseContentType = typeof contentType === "string"
                ? contentType.split(";", 1)[0].trim()
                : "";
            if (
                baseContentType !== WAVEFORM_PEAK_CONTENT_TYPE
                || versionHeader !== String(WAVEFORM_PEAK_FORMAT_VERSION)
            ) {
                return publishError(requestGeneration, filename, "protocol", 200);
            }

            if (!isCurrent(requestGeneration, filename)) return state;
            let buffer;
            try {
                buffer = await response.arrayBuffer();
            } catch {
                if (!isCurrent(requestGeneration, filename)) return state;
                return publishError(requestGeneration, filename, "network", null);
            }
            if (!isCurrent(requestGeneration, filename)) return state;

            let pyramid;
            try {
                pyramid = decodeWaveformPeaks(buffer);
            } catch {
                return publishError(requestGeneration, filename, "decode", 200);
            }
            if (!isCurrent(requestGeneration, filename)) return state;
            if (pyramid.version !== WAVEFORM_PEAK_FORMAT_VERSION) {
                return publishError(requestGeneration, filename, "decode", 200);
            }
            return publish(loadState(WAVEFORM_LOAD_STATUS.READY, filename, pyramid));
        } finally {
            if (isCurrent(requestGeneration, filename)) {
                activeController = null;
                inFlightPromise = null;
            }
        }
    };

    const clear = () => {
        if (disposed) return state;
        activeController?.abort();
        activeController = null;
        inFlightPromise = null;
        generation += 1;
        return publish(idleState());
    };

    const dispose = () => {
        if (disposed) return state;
        disposed = true;
        activeController?.abort();
        activeController = null;
        inFlightPromise = null;
        generation += 1;
        stateChangeCallback = null;
        state = idleState();
        return state;
    };

    const load = (filename, options = {}) => {
        if (disposed) return Promise.resolve(state);
        if (!isSelectableFilename(filename)) {
            clear();
            return Promise.resolve(state);
        }

        const force = options?.force === true;
        if (!force && state.filename === filename) {
            if (state.status === WAVEFORM_LOAD_STATUS.LOADING && inFlightPromise) {
                return inFlightPromise;
            }
            if (
                state.status === WAVEFORM_LOAD_STATUS.READY
                || state.status === WAVEFORM_LOAD_STATUS.ERROR
            ) {
                return Promise.resolve(state);
            }
        }

        const retainedPyramid = state.filename === filename ? state.pyramid : null;
        activeController?.abort();
        generation += 1;
        const requestGeneration = generation;
        activeController = new AbortController();
        const signal = activeController.signal;
        publish(loadState(
            WAVEFORM_LOAD_STATUS.LOADING,
            filename,
            retainedPyramid,
        ));

        const path = waveformPeakRequestPath(filename);
        inFlightPromise = runRequest(
            path,
            filename,
            requestGeneration,
            signal,
        );
        return inFlightPromise;
    };

    return Object.freeze({
        getState: () => state,
        load,
        clear,
        dispose,
    });
}
