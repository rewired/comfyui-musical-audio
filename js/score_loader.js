import { normalizeScorePayload, ScorePayloadError } from "./score.js";
import { createConstantTimeAxis, createScoreTimeAxis } from "./time_axis.js";

export const SCORE_ENDPOINT = "/comfyui-musical-audio/score";
export const SCORE_ROUTE_SCHEMA_VERSION = 1;
export const SCORE_LOAD_STATUS = Object.freeze({
    IDLE: "idle",
    LOADING: "loading",
    READY: "ready",
    CONSTANT: "constant",
    ANALYZING: "analyzing",
    ERROR: "error",
});

const REQUEST_KEYS = Object.freeze(["audio", "scoreFile", "downbeatOffset", "constantTiming"]);
const CONSTANT_TIMING_KEYS = Object.freeze(["bpm", "tempoUnit", "beatsPerBar", "beatUnit"]);
const LOAD_OPTIONS_KEYS = Object.freeze(["force"]);
const CONSTANT_KEYS = Object.freeze([
    "schema_version", "status", "audio_seconds_at_tick_zero",
    "audio_duration_seconds", "source", "provider", "diagnostics",
]);
const ANALYZING_KEYS = Object.freeze([
    "schema_version", "status", "poll_after_ms", "source", "provider", "diagnostics",
]);
const ERROR_KEYS = Object.freeze(["schema_version", "status", "diagnostics"]);
const DIAGNOSTIC_KEYS = Object.freeze(["code", "severity", "message"]);

function exactObject(value, keys, name) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
        throw new TypeError(`${name} must be an object`);
    }
    const actual = Object.keys(value);
    if (actual.length !== keys.length || actual.some((key, index) => key !== keys[index])) {
        throw new TypeError(`${name} has an invalid key set or order`);
    }
}

function finite(value, name) {
    if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
    return value;
}

function deepFreeze(value) {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
        for (const child of Object.values(value)) deepFreeze(child);
        Object.freeze(value);
    }
    return value;
}

function diagnostics(value) {
    if (!Array.isArray(value)) throw new TypeError("diagnostics must be an array");
    return Object.freeze(value.map((item, index) => {
        exactObject(item, DIAGNOSTIC_KEYS, `diagnostics[${index}]`);
        if (typeof item.code !== "string" || typeof item.message !== "string") {
            throw new TypeError("diagnostic strings are invalid");
        }
        if (item.severity !== "warning" && item.severity !== "error") {
            throw new TypeError("diagnostic severity is invalid");
        }
        return Object.freeze({ code: item.code, severity: item.severity, message: item.message });
    }));
}

function normalizedScoreFile(value) {
    if (value === undefined || value === null) return "";
    if (typeof value !== "string") throw new TypeError("scoreFile must be a string when provided");
    return value.trim() === "" ? "" : value;
}

function requestIdentity(value) {
    exactObject(value, REQUEST_KEYS, "request");
    if (typeof value.audio !== "string" || value.audio.trim() === "") {
        throw new TypeError("audio must be a nonblank string");
    }
    const scoreFile = normalizedScoreFile(value.scoreFile);
    const downbeatOffset = finite(value.downbeatOffset, "downbeatOffset");
    exactObject(value.constantTiming, CONSTANT_TIMING_KEYS, "constantTiming");
    if (typeof value.constantTiming.tempoUnit !== "string") {
        throw new TypeError("tempoUnit must be a string");
    }
    const constantTiming = Object.freeze({
        bpm: finite(value.constantTiming.bpm, "bpm"),
        tempoUnit: value.constantTiming.tempoUnit,
        beatsPerBar: value.constantTiming.beatsPerBar,
        beatUnit: value.constantTiming.beatUnit,
    });
    if (constantTiming.bpm <= 0 || !Number.isSafeInteger(constantTiming.beatsPerBar)
        || constantTiming.beatsPerBar <= 0 || !Number.isSafeInteger(constantTiming.beatUnit)
        || constantTiming.beatUnit <= 0) {
        throw new TypeError("constantTiming values must be positive");
    }
    return Object.freeze({
        audio: value.audio,
        scoreFile,
        downbeatOffset,
        constantTiming,
    });
}

function requestKey(request) {
    return JSON.stringify([request.audio, request.scoreFile, request.downbeatOffset]);
}

export function scoreRequestPath(request) {
    if (request === null || typeof request !== "object" || Array.isArray(request)) {
        throw new TypeError("request must be an object");
    }
    const keys = Object.keys(request);
    if (!keys.every((key) => ["audio", "scoreFile", "downbeatOffset"].includes(key))) {
        throw new TypeError("request contains an unexpected key");
    }
    if (typeof request.audio !== "string" || request.audio.trim() === "") {
        throw new TypeError("audio must be a nonblank string");
    }
    const scoreFile = normalizedScoreFile(request.scoreFile);
    const downbeatOffset = request.downbeatOffset === undefined
        ? 0
        : finite(request.downbeatOffset, "downbeatOffset");
    const query = new URLSearchParams();
    query.set("audio", request.audio);
    if (scoreFile !== "") query.set("score_file", scoreFile);
    query.set("downbeat_offset", String(downbeatOffset));
    return `${SCORE_ENDPOINT}?${query.toString()}`;
}

function idleState() {
    return Object.freeze({
        status: SCORE_LOAD_STATUS.IDLE,
        request: null,
        payload: null,
        timeAxis: null,
        diagnostics: Object.freeze([]),
        error: null,
    });
}

function publicError(kind, httpStatus = null, diagnosticItems = Object.freeze([])) {
    return Object.freeze({ kind, httpStatus, diagnostics: diagnosticItems });
}

function publicState(status, request, payload = null, timeAxis = null, diagnosticItems = Object.freeze([]), error = null) {
    return Object.freeze({ status, request, payload, timeAxis, diagnostics: diagnosticItems, error });
}

function validateEnvelope(payload, keys, status) {
    exactObject(payload, keys, `${status} payload`);
    if (payload.schema_version !== SCORE_ROUTE_SCHEMA_VERSION || payload.status !== status) {
        throw new TypeError(`${status} payload schema/status mismatch`);
    }
}

function validateConstant(payload) {
    validateEnvelope(payload, CONSTANT_KEYS, "constant");
    const alignment = finite(payload.audio_seconds_at_tick_zero, "audio_seconds_at_tick_zero");
    const audioDuration = finite(payload.audio_duration_seconds, "audio_duration_seconds");
    if (audioDuration < 0 || payload.source !== "constant" || payload.provider !== "constant") {
        throw new TypeError("constant payload values are invalid");
    }
    return deepFreeze({
        schema_version: 1,
        status: "constant",
        audio_seconds_at_tick_zero: alignment,
        audio_duration_seconds: audioDuration,
        source: "constant",
        provider: "constant",
        diagnostics: diagnostics(payload.diagnostics),
    });
}

function validateAnalyzing(payload) {
    validateEnvelope(payload, ANALYZING_KEYS, "analyzing");
    if (!Number.isSafeInteger(payload.poll_after_ms) || payload.poll_after_ms <= 0
        || payload.source !== "analyzed" || payload.provider !== "analysis") {
        throw new TypeError("analyzing payload values are invalid");
    }
    return deepFreeze({
        schema_version: 1,
        status: "analyzing",
        poll_after_ms: payload.poll_after_ms,
        source: "analyzed",
        provider: "analysis",
        diagnostics: diagnostics(payload.diagnostics),
    });
}

function validateError(payload) {
    validateEnvelope(payload, ERROR_KEYS, "error");
    const items = diagnostics(payload.diagnostics);
    if (items.length !== 1 || items[0].severity !== "error") {
        throw new TypeError("error payload must contain exactly one error diagnostic");
    }
    return deepFreeze({ schema_version: 1, status: "error", diagnostics: items });
}

function responseStatus(response) {
    return Number.isInteger(response?.status) ? response.status : null;
}

function responseHeader(response, name) {
    try { return response?.headers?.get?.(name) ?? null; } catch { return null; }
}

export function createScoreLoader(options = {}) {
    if (options === null || typeof options !== "object" || Array.isArray(options)) {
        throw new TypeError("options must be an object");
    }
    const allowed = ["fetchResponse", "onStateChange", "setTimeoutFn", "clearTimeoutFn"];
    if (!Object.keys(options).every((key) => allowed.includes(key))) throw new TypeError("options contains an unexpected key");
    if (typeof options.fetchResponse !== "function") throw new TypeError("fetchResponse must be a function");
    if (options.onStateChange != null && typeof options.onStateChange !== "function") throw new TypeError("onStateChange must be a function");
    const setTimer = options.setTimeoutFn ?? globalThis.setTimeout;
    const clearTimer = options.clearTimeoutFn ?? globalThis.clearTimeout;
    if (typeof setTimer !== "function" || typeof clearTimer !== "function") throw new TypeError("timer functions must be functions");

    let state = idleState();
    let callback = options.onStateChange ?? null;
    let disposed = false;
    let generation = 0;
    let controller = null;
    let inFlight = null;
    let pollTimer = null;
    let pollDelay = null;
    let pollingLeases = 0;
    let retained = null;

    const publish = (next) => {
        if (disposed) return state;
        state = next;
        if (callback) { try { callback(state); } catch {} }
        return state;
    };

    const current = (which, request) => !disposed && generation === which && state.request === request;

    const cancelTimer = () => {
        if (pollTimer !== null) clearTimer(pollTimer);
        pollTimer = null;
    };

    const stopActive = () => {
        controller?.abort();
        controller = null;
        inFlight = null;
        cancelTimer();
    };

    const publishFailure = (which, request, kind, httpStatus = null, items = Object.freeze([])) => {
        if (!current(which, request)) return state;
        cancelTimer();
        return publish(publicState(
            SCORE_LOAD_STATUS.ERROR,
            request,
            null,
            null,
            items,
            publicError(kind, httpStatus, items),
        ));
    };

    const constantAxis = (request, alignment) => createConstantTimeAxis({
        ...request.constantTiming,
        downbeatOffset: alignment,
    });

    let performRequest;

    const schedulePoll = (which, request, delay) => {
        cancelTimer();
        if (!current(which, request) || state.status !== SCORE_LOAD_STATUS.ANALYZING || pollingLeases === 0) return;
        pollDelay = Math.min(delay, 10_000);
        pollTimer = setTimer(() => {
            pollTimer = null;
            if (!current(which, request) || pollingLeases === 0 || inFlight) return;
            inFlight = performRequest(which, request, false, true).finally(() => {
                if (current(which, request)) inFlight = null;
            });
        }, pollDelay);
    };

    const decodeJson = async (response) => {
        if (typeof response?.json !== "function") throw new TypeError("response has no JSON body");
        return response.json();
    };

    const useSuccessfulPayload = (which, request, payload) => {
        if (payload.status === "ready") {
            const normalized = payload.schemaVersion === 1 ? payload : normalizeScorePayload(payload);
            return publish(publicState(
                SCORE_LOAD_STATUS.READY, request, normalized, createScoreTimeAxis(normalized), normalized.diagnostics,
            ));
        }
        const normalized = validateConstant(payload);
        return publish(publicState(
            SCORE_LOAD_STATUS.CONSTANT, request, normalized,
            constantAxis(request, normalized.audio_seconds_at_tick_zero), normalized.diagnostics,
        ));
    };

    performRequest = async (which, request, unconditional = false, isPoll = false) => {
        if (!current(which, request)) return state;
        controller = new AbortController();
        const key = requestKey(request);
        const headers = { Accept: "application/json" };
        if (!unconditional && retained?.key === key && retained.etag) headers["If-None-Match"] = retained.etag;
        let response;
        try {
            response = await options.fetchResponse(scoreRequestPath({
                audio: request.audio,
                scoreFile: request.scoreFile,
                downbeatOffset: request.downbeatOffset,
            }), {
                method: "GET", headers, signal: controller.signal,
            });
        } catch (error) {
            if (!current(which, request) || error?.name === "AbortError") return state;
            return publishFailure(which, request, "network");
        }
        if (!current(which, request)) return state;
        const status = responseStatus(response);
        if (status === 304) {
            if (isPoll) return publishFailure(which, request, "protocol", 304);
            if (retained?.key === key) return useSuccessfulPayload(which, request, retained.payload);
            if (unconditional) return publishFailure(which, request, "protocol", 304);
            return performRequest(which, request, true, false);
        }

        let payload;
        try { payload = await decodeJson(response); }
        catch (error) {
            if (!current(which, request)) return state;
            return publishFailure(which, request, error?.name === "AbortError" ? "network" : "protocol", status);
        }
        if (!current(which, request)) return state;

        if (status >= 400 && status <= 599) {
            try {
                const normalized = validateError(payload);
                return publishFailure(which, request, "http", status, normalized.diagnostics);
            } catch { return publishFailure(which, request, "protocol", status); }
        }
        if (status !== 200 && status !== 202) return publishFailure(which, request, "protocol", status);

        try {
            if (status === 200 && payload?.status === "ready") {
                const normalized = normalizeScorePayload(payload);
                retained = { key, etag: responseHeader(response, "ETag"), payload: normalized };
                cancelTimer();
                return publish(publicState(
                    SCORE_LOAD_STATUS.READY, request, normalized, createScoreTimeAxis(normalized), normalized.diagnostics,
                ));
            }
            if (status === 200 && payload?.status === "constant") {
                const normalized = validateConstant(payload);
                retained = { key, etag: responseHeader(response, "ETag"), payload: normalized };
                cancelTimer();
                return publish(publicState(
                    SCORE_LOAD_STATUS.CONSTANT, request, normalized,
                    constantAxis(request, normalized.audio_seconds_at_tick_zero), normalized.diagnostics,
                ));
            }
            if (status === 202 && payload?.status === "analyzing") {
                const normalized = validateAnalyzing(payload);
                const nextDelay = isPoll && pollDelay !== null
                    ? Math.min(pollDelay * 2, 10_000)
                    : normalized.poll_after_ms;
                publish(publicState(
                    SCORE_LOAD_STATUS.ANALYZING, request, normalized,
                    constantAxis(request, request.downbeatOffset), normalized.diagnostics,
                ));
                schedulePoll(which, request, nextDelay);
                return state;
            }
            return publishFailure(which, request, "protocol", status);
        } catch (error) {
            const kind = error instanceof ScorePayloadError ? "schema" : "schema";
            return publishFailure(which, request, kind, status);
        } finally {
            if (current(which, request)) controller = null;
        }
    };

    const load = (value, loadOptions = {}) => {
        if (disposed) return Promise.resolve(state);
        if (loadOptions === null || typeof loadOptions !== "object" || Array.isArray(loadOptions)
            || !Object.keys(loadOptions).every((key) => LOAD_OPTIONS_KEYS.includes(key))) {
            throw new TypeError("load options has an invalid key set");
        }
        if (loadOptions.force !== undefined && typeof loadOptions.force !== "boolean") throw new TypeError("force must be boolean");
        const request = requestIdentity(value);
        const force = loadOptions.force === true;
        if (!force && state.request && requestKey(state.request) === requestKey(request)
            && JSON.stringify(state.request.constantTiming) === JSON.stringify(request.constantTiming)) {
            if (inFlight) return inFlight;
        }
        stopActive();
        generation += 1;
        const which = generation;
        pollDelay = null;
        publish(publicState(SCORE_LOAD_STATUS.LOADING, request));
        inFlight = performRequest(which, request, force, false).finally(() => {
            if (current(which, request)) inFlight = null;
        });
        return inFlight;
    };

    const retry = () => {
        if (disposed || !state.request) return Promise.resolve(state);
        return load({
            audio: state.request.audio,
            scoreFile: state.request.scoreFile,
            downbeatOffset: state.request.downbeatOffset,
            constantTiming: { ...state.request.constantTiming },
        }, { force: true });
    };

    const clear = () => {
        if (disposed) return state;
        stopActive();
        generation += 1;
        pollDelay = null;
        return publish(idleState());
    };

    const acquirePollingLease = () => {
        if (disposed) return Object.freeze(() => {});
        pollingLeases += 1;
        if (pollingLeases === 1 && state.status === SCORE_LOAD_STATUS.ANALYZING && state.request) {
            schedulePoll(generation, state.request, pollDelay ?? state.payload.poll_after_ms);
        }
        let released = false;
        return Object.freeze(() => {
            if (released) return;
            released = true;
            pollingLeases = Math.max(0, pollingLeases - 1);
            if (pollingLeases === 0) cancelTimer();
        });
    };

    const dispose = () => {
        if (disposed) return state;
        stopActive();
        generation += 1;
        disposed = true;
        callback = null;
        pollingLeases = 0;
        state = idleState();
        return state;
    };

    return Object.freeze({ getState: () => state, load, retry, clear, acquirePollingLease, dispose });
}
