import test from "node:test";
import assert from "node:assert/strict";

import {
    SCORE_ENDPOINT,
    SCORE_LOAD_STATUS,
    SCORE_ROUTE_SCHEMA_VERSION,
    createScoreLoader,
    scoreRequestPath,
} from "../js/score_loader.js";

const constantTiming = Object.freeze({ bpm: 120, tempoUnit: "Quarter", beatsPerBar: 4, beatUnit: 4 });

function request(overrides = {}) {
    return { audio: "music & voice.flac", scoreFile: "", downbeatOffset: 0, constantTiming, ...overrides };
}

function diagnostics() { return []; }

function constantPayload(alignment = 0) {
    return {
        schema_version: 1, status: "constant", audio_seconds_at_tick_zero: alignment,
        audio_duration_seconds: 8, source: "constant", provider: "constant", diagnostics: diagnostics(),
    };
}

function analyzingPayload(delay = 100) {
    return {
        schema_version: 1, status: "analyzing", poll_after_ms: delay,
        source: "analyzed", provider: "analysis", diagnostics: diagnostics(),
    };
}

function errorPayload(message = "bad") {
    return { schema_version: 1, status: "error", diagnostics: [{ code: "provider_error", severity: "error", message }] };
}

function readyPayload() {
    return {
        schema_version: 1, status: "ready", ticks_per_quarter: 480,
        audio_seconds_at_tick_zero: 0.25, audio_duration_seconds: 8,
        bar_starts: [0, 1920, 3840, 5760, 7680], tempos: [{ tick: 0, us_per_quarter: 500000 }],
        meters: [{ tick: 0, numerator: 4, denominator: 4 }], markers: [], sections: [],
        source: "json", provider: "explicit", meter_estimated: false,
        has_variable_meter: false, has_midbar_meter_change: false, diagnostics: [],
    };
}

function response(status, body, headers = {}) {
    const lower = new Map(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]));
    return {
        status,
        ok: status >= 200 && status < 300,
        headers: { get(name) { return lower.get(name.toLowerCase()) ?? null; } },
        async json() { return body; },
    };
}

function deferred() {
    let resolve; let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function fakeTimers() {
    let next = 1;
    const entries = new Map();
    return {
        set(fn, delay) { const id = next++; entries.set(id, { fn, delay }); return id; },
        clear(id) { entries.delete(id); },
        get size() { return entries.size; },
        delays() { return [...entries.values()].map((entry) => entry.delay); },
        runNext() { const [id, entry] = entries.entries().next().value; entries.delete(id); entry.fn(); },
    };
}

async function turn() { await new Promise((resolve) => setImmediate(resolve)); }

test("endpoint, schema version, six statuses, and URL encoding are exact", () => {
    assert.equal(SCORE_ENDPOINT, "/comfyui-musical-audio/score");
    assert.equal(SCORE_ROUTE_SCHEMA_VERSION, 1);
    assert.deepEqual(SCORE_LOAD_STATUS, {
        IDLE: "idle", LOADING: "loading", READY: "ready", CONSTANT: "constant", ANALYZING: "analyzing", ERROR: "error",
    });
    const path = scoreRequestPath({ audio: "a & b.flac", scoreFile: "side #1.json", downbeatOffset: -0.25 });
    assert.equal(path, `${SCORE_ENDPOINT}?audio=a+%26+b.flac&score_file=side+%231.json&downbeat_offset=-0.25`);
    assert.equal(scoreRequestPath({ audio: "none", scoreFile: "  ", downbeatOffset: 0 }), `${SCORE_ENDPOINT}?audio=none&downbeat_offset=0`);
    assert.match(scoreRequestPath({ audio: "x.wav", scoreFile: "none" }), /score_file=none/);
    assert.throws(() => scoreRequestPath({ audio: "x.wav", downbeatOffset: Infinity }), TypeError);
});

test("ready and constant states are deeply frozen and serializable", async () => {
    for (const [body, status] of [[readyPayload(), "ready"], [constantPayload(-0.5), "constant"]]) {
        const publications = [];
        const loader = createScoreLoader({ fetchResponse: async () => response(200, body), onStateChange: (state) => publications.push(state) });
        const state = await loader.load(request());
        assert.equal(state.status, status);
        assert.equal(Object.keys(state).join(","), "status,request,payload,timeAxis,diagnostics,error");
        assert.ok(Object.isFrozen(state)); assert.ok(Object.isFrozen(state.request.constantTiming));
        assert.ok(Object.isFrozen(state.payload)); assert.ok(Object.isFrozen(state.timeAxis));
        assert.doesNotThrow(() => JSON.stringify({ ...state, timeAxis: null }));
        assert.deepEqual(publications.map((value) => value.status), ["loading", status]);
    }
});

test("status/payload agreement, exact keys, and schema mismatch become errors without fallback", async () => {
    const bodies = [
        [200, analyzingPayload()],
        [202, { ...analyzingPayload(), extra: true }],
        [200, { ...constantPayload(), schema_version: 2 }],
        [200, { ...constantPayload(), provider: "analysis" }],
    ];
    for (const [status, body] of bodies) {
        const loader = createScoreLoader({ fetchResponse: async () => response(status, body) });
        const state = await loader.load(request());
        assert.equal(state.status, "error");
        assert.ok(["protocol", "schema"].includes(state.error.kind));
        assert.equal(state.timeAxis, null);
    }
});

test("valid HTTP error retains diagnostics; malformed HTTP error is protocol", async () => {
    let loader = createScoreLoader({ fetchResponse: async () => response(422, errorPayload("invalid")) });
    let state = await loader.load(request());
    assert.equal(state.error.kind, "http"); assert.equal(state.error.httpStatus, 422);
    assert.equal(state.diagnostics[0].message, "invalid");
    loader = createScoreLoader({ fetchResponse: async () => response(500, { status: "error" }) });
    state = await loader.load(request());
    assert.equal(state.error.kind, "protocol");
});

test("ETag is retained for one identity and valid 304 reconstructs current local axis", async () => {
    const calls = [];
    const responses = [response(200, constantPayload(0.1), { ETag: '"one"' }), response(304, null)];
    const loader = createScoreLoader({ fetchResponse: async (path, options) => { calls.push({ path, options }); return responses.shift(); } });
    await loader.load(request());
    const state = await loader.load(request({ constantTiming: { ...constantTiming, bpm: 90 } }));
    assert.equal(calls[1].options.headers["If-None-Match"], '"one"');
    assert.equal(state.status, "constant");
    assert.equal(state.timeAxis.tickToScoreSeconds(960), 2 / 3);
});

test("orphan 304 retries unconditionally once and a second 304 is protocol error", async () => {
    let calls = 0;
    let loader = createScoreLoader({ fetchResponse: async () => { calls += 1; return calls === 1 ? response(304, null) : response(200, readyPayload()); } });
    assert.equal((await loader.load(request())).status, "ready");
    assert.equal(calls, 2);
    calls = 0;
    loader = createScoreLoader({ fetchResponse: async () => { calls += 1; return response(304, null); } });
    const state = await loader.load(request());
    assert.equal(calls, 2); assert.equal(state.error.kind, "protocol"); assert.equal(state.error.httpStatus, 304);
});

test("new generations abort and suppress stale fetch and body results", async () => {
    const first = deferred(); const body = deferred(); const signals = [];
    let count = 0;
    const loader = createScoreLoader({ fetchResponse: async (_path, options) => {
        signals.push(options.signal); count += 1;
        if (count === 1) return first.promise;
        if (count === 2) return { ...response(200, readyPayload()), json: () => body.promise };
        return response(200, constantPayload());
    } });
    const old = loader.load(request({ audio: "old.wav" }));
    const middle = loader.load(request({ audio: "middle.wav" }));
    assert.equal(signals[0].aborted, true);
    first.resolve(response(200, readyPayload()));
    const newest = loader.load(request({ audio: "new.flac" }));
    assert.equal(signals[1].aborted, true);
    body.resolve(readyPayload());
    await Promise.all([old, middle, newest]);
    assert.equal(loader.getState().request.audio, "new.flac");
    assert.equal(loader.getState().status, "constant");
});

test("AbortError is suppressed, network errors publish only for current generation, callbacks cannot break loads", async () => {
    let loader = createScoreLoader({ fetchResponse: async () => { const error = new Error("abort"); error.name = "AbortError"; throw error; } });
    await loader.load(request());
    assert.equal(loader.getState().status, "loading");
    loader = createScoreLoader({ fetchResponse: async () => { throw new Error("offline"); }, onStateChange: () => { throw new Error("callback"); } });
    const state = await loader.load(request());
    assert.equal(state.status, "error"); assert.equal(state.error.kind, "network");
});

test("analyzing polls only with leases, backs off, caps, and never overlaps", async () => {
    const timers = fakeTimers(); const pending = deferred(); let calls = 0;
    const loader = createScoreLoader({
        setTimeoutFn: (fn, delay) => timers.set(fn, delay), clearTimeoutFn: (id) => timers.clear(id),
        fetchResponse: async () => {
            calls += 1;
            if (calls === 1) return response(202, analyzingPayload(100));
            if (calls === 2) return pending.promise;
            return response(202, analyzingPayload(1));
        },
    });
    await loader.load(request());
    assert.equal(loader.getState().status, "analyzing"); assert.equal(timers.size, 0);
    const releaseA = loader.acquirePollingLease(); const releaseB = loader.acquirePollingLease();
    assert.deepEqual(timers.delays(), [100]);
    timers.runNext(); await turn();
    assert.equal(calls, 2); assert.equal(timers.size, 0);
    pending.resolve(response(202, analyzingPayload(1))); await turn(); await turn();
    assert.deepEqual(timers.delays(), [200]);
    releaseA(); assert.equal(timers.size, 1); releaseB(); assert.equal(timers.size, 0); releaseB();

    const delays = [];
    const fastTimers = fakeTimers(); calls = 0;
    const capped = createScoreLoader({
        setTimeoutFn: (fn, delay) => { delays.push(delay); return fastTimers.set(fn, delay); },
        clearTimeoutFn: (id) => fastTimers.clear(id),
        fetchResponse: async () => { calls += 1; return response(202, analyzingPayload(4000)); },
    });
    capped.acquirePollingLease(); await capped.load(request());
    for (let index = 0; index < 3; index += 1) { fastTimers.runNext(); await turn(); await turn(); }
    assert.deepEqual(delays, [4000, 8000, 10000, 10000]);
});

test("identity changes, clear, retry, and dispose own all lifecycle state", async () => {
    const timers = fakeTimers(); let calls = 0;
    const loader = createScoreLoader({
        setTimeoutFn: (fn, delay) => timers.set(fn, delay), clearTimeoutFn: (id) => timers.clear(id),
        fetchResponse: async () => { calls += 1; return calls === 1 ? response(202, analyzingPayload()) : response(200, constantPayload()); },
    });
    const release = loader.acquirePollingLease(); await loader.load(request()); assert.equal(timers.size, 1);
    await loader.load(request({ audio: "replacement.flac" })); assert.equal(timers.size, 0); assert.equal(loader.getState().status, "constant");
    loader.clear(); assert.equal(loader.getState().status, "idle"); assert.equal(loader.getState().request, null);
    assert.equal((await loader.retry()).status, "idle");
    await loader.load(request()); const beforeRetry = calls; await loader.retry(); assert.equal(calls, beforeRetry + 1);
    release(); const disposed = loader.dispose(); assert.equal(disposed.status, "idle"); assert.equal(loader.dispose(), disposed);
    const afterDispose = calls; await loader.load(request()); await loader.retry(); assert.equal(calls, afterDispose);
    const lateRelease = loader.acquirePollingLease(); assert.doesNotThrow(lateRelease);
});
