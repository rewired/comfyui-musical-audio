import assert from "node:assert/strict";
import test from "node:test";

import {
    WAVEFORM_LOAD_STATUS,
    WAVEFORM_PEAK_CONTENT_TYPE,
    WAVEFORM_PEAK_ENDPOINT,
    createWaveformPeakLoader,
    waveformPeakRequestPath,
} from "../js/waveform_loader.js";


const MAGIC = new TextEncoder().encode("MAUPK001");
const HEADER_SIZE = 64;
const DIRECTORY_SIZE = 24;

function buildFixture() {
    const sampleRate = 8;
    const sampleCount = 3;
    const peaks = [-5, 10, -3, 4];
    const firstDataOffset = HEADER_SIZE + DIRECTORY_SIZE;
    const buffer = new ArrayBuffer(firstDataOffset + peaks.length * 2);
    const view = new DataView(buffer);
    MAGIC.forEach((value, index) => view.setUint8(index, value));
    view.setUint16(8, 1, true);
    view.setUint16(10, HEADER_SIZE, true);
    view.setUint32(12, 3, true);
    view.setUint32(16, sampleRate, true);
    view.setUint16(20, 2, true);
    view.setUint16(22, 1, true);
    view.setBigUint64(24, BigInt(sampleCount), true);
    view.setFloat64(32, sampleCount / sampleRate, true);
    view.setBigUint64(40, BigInt(HEADER_SIZE), true);
    view.setBigUint64(48, BigInt(firstDataOffset), true);
    view.setBigUint64(56, 0n, true);
    view.setUint32(HEADER_SIZE, 2, true);
    view.setUint32(HEADER_SIZE + 4, peaks.length / 2, true);
    view.setBigUint64(HEADER_SIZE + 8, BigInt(firstDataOffset), true);
    view.setBigUint64(HEADER_SIZE + 16, BigInt(peaks.length * 2), true);
    peaks.forEach((value, index) => view.setInt16(firstDataOffset + index * 2, value, true));
    return buffer;
}

function makeResponse({
    status = 200,
    ok = status >= 200 && status < 300,
    contentType = WAVEFORM_PEAK_CONTENT_TYPE,
    version = "1",
    buffer = buildFixture(),
    bodyError,
} = {}) {
    const headerValues = new Map([
        ["content-type", contentType],
        ["x-musical-audio-waveform-version", version],
    ]);
    const response = {
        status,
        ok,
        bodyReads: 0,
        headers: {
            get(name) {
                return headerValues.get(String(name).toLowerCase()) ?? null;
            },
        },
        async arrayBuffer() {
            response.bodyReads += 1;
            if (bodyError !== undefined) throw bodyError;
            return buffer;
        },
    };
    return response;
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function loaderFor(fetchResponse = async () => makeResponse(), onStateChange) {
    return createWaveformPeakLoader({ fetchResponse, onStateChange });
}

function decodedFilename(path) {
    return new URL(path, "http://localhost").searchParams.get("filename");
}


test("1. root filename is encoded correctly", () => {
    assert.equal(
        waveformPeakRequestPath("track.wav"),
        `${WAVEFORM_PEAK_ENDPOINT}?filename=track.wav`,
    );
});

test("2. nested forward-slash filename round-trips exactly", () => {
    const filename = "whatdreamscost/track.mp3";
    const path = waveformPeakRequestPath(filename);
    assert.match(path, /%2F/);
    assert.equal(decodedFilename(path), filename);
});

test("3. backslash filename is encoded", () => {
    const filename = "folder\\track.wav";
    const path = waveformPeakRequestPath(filename);
    assert.match(path, /%5C/);
    assert.equal(decodedFilename(path), filename);
});

test("4. spaces are encoded", () => {
    const filename = "my track.wav";
    const path = waveformPeakRequestPath(filename);
    assert.equal(path.includes(" "), false);
    assert.equal(decodedFilename(path), filename);
});

test("5. Unicode is encoded", () => {
    const filename = "música/音.wav";
    const path = waveformPeakRequestPath(filename);
    assert.match(path, /%/);
    assert.equal(decodedFilename(path), filename);
});

test("6. query punctuation is encoded", () => {
    const filename = "mix#one?left&right=value.wav";
    const path = waveformPeakRequestPath(filename);
    assert.equal(path.includes("#"), false);
    assert.equal(path.split("?").length, 2);
    assert.equal(decodedFilename(path), filename);
});

test("7. empty filename is rejected by the path helper", () => {
    assert.throws(() => waveformPeakRequestPath(""), TypeError);
});

test("8. whitespace-only filename is rejected", () => {
    assert.throws(() => waveformPeakRequestPath(" \t\n"), TypeError);
});

test("9. none is rejected case-insensitively", () => {
    for (const filename of ["none", "NONE", "NoNe"]) {
        assert.throws(() => waveformPeakRequestPath(filename), TypeError);
    }
});


test("10. new loader starts idle", () => {
    assert.deepEqual(loaderFor().getState(), {
        status: WAVEFORM_LOAD_STATUS.IDLE,
        filename: null,
        pyramid: null,
        error: null,
    });
});

test("11. initial state is immutable", () => {
    assert.equal(Object.isFrozen(loaderFor().getState()), true);
});

test("12. no fetch occurs during construction", () => {
    let fetchCount = 0;
    loaderFor(async () => {
        fetchCount += 1;
        return makeResponse();
    });
    assert.equal(fetchCount, 0);
});


test("13. load enters loading synchronously", () => {
    const pending = deferred();
    const loader = loaderFor(() => pending.promise);
    loader.load("track.wav");
    assert.equal(loader.getState().status, WAVEFORM_LOAD_STATUS.LOADING);
    pending.resolve(makeResponse());
});

test("14. valid 200 response becomes ready", async () => {
    const state = await loaderFor().load("track.wav");
    assert.equal(state.status, WAVEFORM_LOAD_STATUS.READY);
});

test("15. exact filename is retained", async () => {
    const filename = "nested/Track 音.wav";
    const state = await loaderFor().load(filename);
    assert.equal(state.filename, filename);
});

test("16. pyramid metadata is decoded correctly", async () => {
    const { pyramid } = await loaderFor().load("track.wav");
    assert.deepEqual({
        version: pyramid.version,
        sampleRate: pyramid.sampleRate,
        sourceChannelCount: pyramid.sourceChannelCount,
        sampleCount: pyramid.sampleCount,
        durationSeconds: pyramid.durationSeconds,
        levelCount: pyramid.levels.length,
    }, {
        version: 1,
        sampleRate: 8,
        sourceChannelCount: 2,
        sampleCount: 3,
        durationSeconds: 3 / 8,
        levelCount: 1,
    });
});

test("17. ready state is immutable", async () => {
    const state = await loaderFor().load("track.wav");
    assert.equal(Object.isFrozen(state), true);
});

test("18. Accept header is exact", async () => {
    let options;
    await loaderFor(async (_path, requestOptions) => {
        options = requestOptions;
        return makeResponse();
    }).load("track.wav");
    assert.deepEqual(options.headers, { Accept: WAVEFORM_PEAK_CONTENT_TYPE });
});

test("19. request method is GET", async () => {
    let options;
    await loaderFor(async (_path, requestOptions) => {
        options = requestOptions;
        return makeResponse();
    }).load("track.wav");
    assert.equal(options.method, "GET");
});

test("20. cache mode is no-cache", async () => {
    let options;
    await loaderFor(async (_path, requestOptions) => {
        options = requestOptions;
        return makeResponse();
    }).load("track.wav");
    assert.equal(options.cache, "no-cache");
});

test("21. AbortSignal is supplied", async () => {
    let options;
    await loaderFor(async (_path, requestOptions) => {
        options = requestOptions;
        return makeResponse();
    }).load("track.wav");
    assert.equal(options.signal instanceof AbortSignal, true);
});


test("22. wrong content type becomes protocol error", async () => {
    const loader = loaderFor(async () => makeResponse({ contentType: "application/octet-stream" }));
    const state = await loader.load("track.wav");
    assert.deepEqual(state.error, { kind: "protocol", httpStatus: 200 });
});

test("23. content type parameters are accepted after base-type parsing", async () => {
    const loader = loaderFor(async () => makeResponse({
        contentType: `${WAVEFORM_PEAK_CONTENT_TYPE}; charset=binary`,
    }));
    assert.equal((await loader.load("track.wav")).status, WAVEFORM_LOAD_STATUS.READY);
});

test("24. missing version header becomes protocol error", async () => {
    const state = await loaderFor(async () => makeResponse({ version: null })).load("track.wav");
    assert.deepEqual(state.error, { kind: "protocol", httpStatus: 200 });
});

test("25. wrong version header becomes protocol error", async () => {
    const state = await loaderFor(async () => makeResponse({ version: "2" })).load("track.wav");
    assert.deepEqual(state.error, { kind: "protocol", httpStatus: 200 });
});

test("26. invalid binary becomes decode error", async () => {
    const buffer = buildFixture();
    new DataView(buffer).setUint8(0, 0);
    const state = await loaderFor(async () => makeResponse({ buffer })).load("track.wav");
    assert.deepEqual(state.error, { kind: "decode", httpStatus: 200 });
});

test("27. empty binary becomes decode error", async () => {
    const state = await loaderFor(async () => makeResponse({
        buffer: new ArrayBuffer(0),
    })).load("track.wav");
    assert.deepEqual(state.error, { kind: "decode", httpStatus: 200 });
});


for (const [number, status] of [[28, 400], [29, 404], [30, 422], [31, 500]]) {
    test(`${number}. HTTP ${status} becomes an HTTP error`, async () => {
        const state = await loaderFor(async () => makeResponse({ status })).load("track.wav");
        assert.deepEqual(state.error, { kind: "http", httpStatus: status });
    });
}

test("32. network rejection becomes network error", async () => {
    const state = await loaderFor(async () => {
        throw new Error("offline");
    }).load("track.wav");
    assert.deepEqual(state.error, { kind: "network", httpStatus: null });
});

test("33. server body text is not stored in the error object", async () => {
    const response = makeResponse({ status: 404 });
    response.text = async () => "absolute path and backend details";
    const state = await loaderFor(async () => response).load("track.wav");
    assert.deepEqual(Object.keys(state.error).sort(), ["httpStatus", "kind"]);
    assert.equal(JSON.stringify(state).includes("backend details"), false);
});

test("34. operational failures resolve rather than reject", async () => {
    const promise = loaderFor(async () => {
        throw new Error("offline");
    }).load("track.wav");
    await assert.doesNotReject(promise);
});


test("35. same ready filename does not refetch", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return makeResponse();
    });
    await loader.load("track.wav");
    await loader.load("track.wav");
    assert.equal(fetchCount, 1);
});

test("36. same error filename does not refetch", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return makeResponse({ status: 404 });
    });
    await loader.load("track.wav");
    await loader.load("track.wav");
    assert.equal(fetchCount, 1);
});

test("37. same loading filename returns the same in-flight promise", async () => {
    const pending = deferred();
    const loader = loaderFor(() => pending.promise);
    const first = loader.load("track.wav");
    const second = loader.load("track.wav");
    assert.equal(second, first);
    pending.resolve(makeResponse());
    await first;
});

test("38. force=true refetches a ready filename", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return makeResponse();
    });
    await loader.load("track.wav");
    await loader.load("track.wav", { force: true });
    assert.equal(fetchCount, 2);
});

test("39. force=true refetches an error filename", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return fetchCount === 1 ? makeResponse({ status: 404 }) : makeResponse();
    });
    await loader.load("track.wav");
    const state = await loader.load("track.wav", { force: true });
    assert.equal(fetchCount, 2);
    assert.equal(state.status, WAVEFORM_LOAD_STATUS.READY);
});

test("40. force=true aborts a same-file loading request", async () => {
    const pending = deferred();
    const calls = [];
    const loader = loaderFor((_path, options) => {
        calls.push(options);
        return calls.length === 1 ? pending.promise : makeResponse();
    });
    const first = loader.load("track.wav");
    const second = loader.load("track.wav", { force: true });
    assert.equal(calls[0].signal.aborted, true);
    pending.resolve(makeResponse());
    await Promise.all([first, second]);
});


test("41. changing filename aborts the prior controller", async () => {
    const firstResponse = deferred();
    const calls = [];
    const loader = loaderFor((_path, options) => {
        calls.push(options);
        return calls.length === 1 ? firstResponse.promise : makeResponse();
    });
    const first = loader.load("old.wav");
    await loader.load("new.wav");
    assert.equal(calls[0].signal.aborted, true);
    firstResponse.resolve(makeResponse());
    await first;
});

test("42. new filename enters loading", () => {
    const requests = [deferred(), deferred()];
    const loader = loaderFor(() => requests.shift().promise);
    loader.load("old.wav");
    loader.load("new.wav");
    assert.equal(loader.getState().status, WAVEFORM_LOAD_STATUS.LOADING);
    assert.equal(loader.getState().filename, "new.wav");
});

test("43. old successful response cannot overwrite the new state", async () => {
    const oldResponse = deferred();
    const newResponse = deferred();
    const loader = loaderFor((path) => (
        decodedFilename(path) === "old.wav" ? oldResponse.promise : newResponse.promise
    ));
    const oldLoad = loader.load("old.wav");
    const newLoad = loader.load("new.wav");
    newResponse.resolve(makeResponse());
    await newLoad;
    oldResponse.resolve(makeResponse());
    await oldLoad;
    assert.equal(loader.getState().filename, "new.wav");
    assert.equal(loader.getState().status, WAVEFORM_LOAD_STATUS.READY);
});

test("44. old failed response cannot overwrite the new state", async () => {
    const oldResponse = deferred();
    const loader = loaderFor((path) => (
        decodedFilename(path) === "old.wav" ? oldResponse.promise : makeResponse()
    ));
    const oldLoad = loader.load("old.wav");
    await loader.load("new.wav");
    oldResponse.reject(new Error("late failure"));
    await oldLoad;
    assert.equal(loader.getState().filename, "new.wav");
    assert.equal(loader.getState().status, WAVEFORM_LOAD_STATUS.READY);
});

test("45. stale response body is not decoded where avoidable", async () => {
    const oldResponse = deferred();
    const staleResponse = makeResponse();
    const loader = loaderFor((path) => (
        decodedFilename(path) === "old.wav" ? oldResponse.promise : makeResponse()
    ));
    const oldLoad = loader.load("old.wav");
    await loader.load("new.wav");
    oldResponse.resolve(staleResponse);
    await oldLoad;
    assert.equal(staleResponse.bodyReads, 0);
});

test("46. two loaders remain fully isolated", async () => {
    const first = loaderFor();
    const second = loaderFor(async () => makeResponse({ status: 404 }));
    await Promise.all([first.load("first.wav"), second.load("second.wav")]);
    assert.equal(first.getState().status, WAVEFORM_LOAD_STATUS.READY);
    assert.equal(first.getState().filename, "first.wav");
    assert.equal(second.getState().status, WAVEFORM_LOAD_STATUS.ERROR);
    assert.equal(second.getState().filename, "second.wav");
});


test("47. clear aborts an active request", async () => {
    const pending = deferred();
    let signal;
    const loader = loaderFor((_path, options) => {
        signal = options.signal;
        return pending.promise;
    });
    const load = loader.load("track.wav");
    loader.clear();
    assert.equal(signal.aborted, true);
    pending.resolve(makeResponse());
    await load;
});

test("48. clear returns idle", () => {
    const loader = loaderFor();
    loader.load("track.wav");
    assert.equal(loader.clear().status, WAVEFORM_LOAD_STATUS.IDLE);
});

test("49. clear releases the pyramid", async () => {
    const loader = loaderFor();
    await loader.load("track.wav");
    loader.clear();
    assert.equal(loader.getState().pyramid, null);
});

for (const [number, label, value] of [
    [50, "empty selection", ""],
    [51, "null", null],
    [52, "undefined", undefined],
    [53, "none", "NoNe"],
]) {
    test(`${number}. ${label} behaves as clear`, async () => {
        let fetchCount = 0;
        const loader = loaderFor(async () => {
            fetchCount += 1;
            return makeResponse();
        });
        await loader.load("track.wav");
        const state = await loader.load(value);
        assert.equal(fetchCount, 1);
        assert.deepEqual(state, {
            status: WAVEFORM_LOAD_STATUS.IDLE,
            filename: null,
            pyramid: null,
            error: null,
        });
    });
}

test("54. clear remains reusable", async () => {
    const loader = loaderFor();
    await loader.load("first.wav");
    loader.clear();
    const state = await loader.load("second.wav");
    assert.equal(state.status, WAVEFORM_LOAD_STATUS.READY);
    assert.equal(state.filename, "second.wav");
});


test("55. script-visible 304 with prior same-file pyramid reuses it", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return fetchCount === 1 ? makeResponse() : makeResponse({ status: 304 });
    });
    await loader.load("track.wav");
    const state = await loader.load("track.wav", { force: true });
    assert.equal(state.status, WAVEFORM_LOAD_STATUS.READY);
    assert.equal(fetchCount, 2);
});

test("56. reused 304 pyramid is the exact same object", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return fetchCount === 1 ? makeResponse() : makeResponse({ status: 304 });
    });
    const first = await loader.load("track.wav");
    const second = await loader.load("track.wav", { force: true });
    assert.equal(second.pyramid, first.pyramid);
});

test("57. 304 without prior pyramid retries once using cache=reload", async () => {
    const caches = [];
    const loader = loaderFor(async (_path, options) => {
        caches.push(options.cache);
        return caches.length === 1 ? makeResponse({ status: 304 }) : makeResponse();
    });
    await loader.load("track.wav");
    assert.deepEqual(caches, ["no-cache", "reload"]);
});

test("58. successful reload fallback becomes ready", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return fetchCount === 1 ? makeResponse({ status: 304 }) : makeResponse();
    });
    assert.equal((await loader.load("track.wav")).status, WAVEFORM_LOAD_STATUS.READY);
});

test("59. second 304 does not loop", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return makeResponse({ status: 304 });
    });
    const state = await loader.load("track.wav");
    assert.equal(fetchCount, 2);
    assert.deepEqual(state.error, { kind: "http", httpStatus: 304 });
});

test("60. failed reload fallback becomes error", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return fetchCount === 1
            ? makeResponse({ status: 304 })
            : makeResponse({ status: 500 });
    });
    const state = await loader.load("track.wav");
    assert.deepEqual(state.error, { kind: "http", httpStatus: 500 });
});


test("61. dispose aborts an active request", async () => {
    const pending = deferred();
    let signal;
    const loader = loaderFor((_path, options) => {
        signal = options.signal;
        return pending.promise;
    });
    const load = loader.load("track.wav");
    loader.dispose();
    assert.equal(signal.aborted, true);
    pending.resolve(makeResponse());
    await load;
});

test("62. dispose releases the pyramid", async () => {
    const loader = loaderFor();
    await loader.load("track.wav");
    loader.dispose();
    assert.equal(loader.getState().pyramid, null);
});

test("63. dispose is idempotent", () => {
    const loader = loaderFor();
    const first = loader.dispose();
    const second = loader.dispose();
    assert.equal(second, first);
});

test("64. load after dispose performs no fetch", async () => {
    let fetchCount = 0;
    const loader = loaderFor(async () => {
        fetchCount += 1;
        return makeResponse();
    });
    loader.dispose();
    const state = await loader.load("track.wav");
    assert.equal(fetchCount, 0);
    assert.equal(state.status, WAVEFORM_LOAD_STATUS.IDLE);
});

test("65. no state callback occurs after dispose", async () => {
    const pending = deferred();
    const notifications = [];
    const loader = loaderFor(() => pending.promise, (state) => notifications.push(state));
    const load = loader.load("track.wav");
    assert.equal(notifications.length, 1);
    loader.dispose();
    pending.resolve(makeResponse());
    await load;
    assert.equal(notifications.length, 1);
});

test("66. late success after dispose is ignored", async () => {
    const pending = deferred();
    const loader = loaderFor(() => pending.promise);
    const load = loader.load("track.wav");
    loader.dispose();
    pending.resolve(makeResponse());
    await load;
    assert.equal(loader.getState().status, WAVEFORM_LOAD_STATUS.IDLE);
});

test("67. late error after dispose is ignored", async () => {
    const pending = deferred();
    const loader = loaderFor(() => pending.promise);
    const load = loader.load("track.wav");
    loader.dispose();
    pending.reject(new Error("late failure"));
    await load;
    assert.equal(loader.getState().status, WAVEFORM_LOAD_STATUS.IDLE);
});


test("68. meaningful transitions notify once each", async () => {
    const notifications = [];
    const loader = loaderFor(undefined, (state) => notifications.push(state.status));
    await loader.load("track.wav");
    loader.clear();
    assert.deepEqual(notifications, [
        WAVEFORM_LOAD_STATUS.LOADING,
        WAVEFORM_LOAD_STATUS.READY,
        WAVEFORM_LOAD_STATUS.IDLE,
    ]);
});

test("69. unchanged ready dedupe does not notify", async () => {
    const notifications = [];
    const loader = loaderFor(undefined, (state) => notifications.push(state));
    await loader.load("track.wav");
    const count = notifications.length;
    await loader.load("track.wav");
    assert.equal(notifications.length, count);
});

test("70. stale work does not notify", async () => {
    const oldResponse = deferred();
    const notifications = [];
    const loader = loaderFor((path) => (
        decodedFilename(path) === "old.wav" ? oldResponse.promise : makeResponse()
    ), (state) => notifications.push(`${state.status}:${state.filename}`));
    const oldLoad = loader.load("old.wav");
    await loader.load("new.wav");
    const beforeLateResponse = notifications.slice();
    oldResponse.resolve(makeResponse());
    await oldLoad;
    assert.deepEqual(notifications, beforeLateResponse);
});

test("71. callback exceptions do not corrupt state", async () => {
    const loader = loaderFor(undefined, () => {
        throw new Error("callback failure");
    });
    const state = await loader.load("track.wav");
    assert.equal(state.status, WAVEFORM_LOAD_STATUS.READY);
    assert.equal(loader.getState(), state);
});

test("72. callback exceptions do not reject load", async () => {
    const loader = loaderFor(undefined, () => {
        throw new Error("callback failure");
    });
    await assert.doesNotReject(loader.load("track.wav"));
});


test("73. state does not contain Response", async () => {
    const response = makeResponse();
    const state = await loaderFor(async () => response).load("track.wav");
    assert.equal(Object.values(state).includes(response), false);
});

test("74. state does not contain Promise", async () => {
    const state = await loaderFor().load("track.wav");
    assert.equal(Object.values(state).some((value) => value instanceof Promise), false);
});

test("75. state does not contain AbortController", async () => {
    const state = await loaderFor().load("track.wav");
    assert.equal(Object.values(state).some((value) => value instanceof AbortController), false);
});

test("76. error state contains no exception object", async () => {
    const exception = new Error("offline");
    const state = await loaderFor(async () => {
        throw exception;
    }).load("track.wav");
    assert.equal(Object.values(state).includes(exception), false);
    assert.equal(Object.isFrozen(state.error), true);
});

test("77. replacing a filename drops the previous pyramid reference", async () => {
    const nextResponse = deferred();
    const loader = loaderFor((path) => (
        decodedFilename(path) === "first.wav" ? makeResponse() : nextResponse.promise
    ));
    const first = await loader.load("first.wav");
    loader.load("second.wav");
    assert.equal(loader.getState().pyramid, null);
    assert.notEqual(loader.getState().pyramid, first.pyramid);
    nextResponse.resolve(makeResponse());
});
