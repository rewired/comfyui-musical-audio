import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { ScorePayloadError, createScoreResolver, normalizeScorePayload } from "../js/score.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

function roundRatio(numerator, denominator) {
    const sign = numerator < 0 ? -1 : 1;
    const absolute = Math.abs(numerator);
    let quotient = Math.floor(absolute / denominator);
    if ((absolute - quotient * denominator) * 2 >= denominator) quotient += 1;
    return sign * quotient;
}

function barStarts(score, throughTick = 20_000) {
    const boundaries = [0];
    let midbar = false;
    let meterIndex = 0;
    let meter = score.meters[0];
    let anchor = meter.tick;
    let index = 1;
    while (boundaries.at(-1) <= throughTick) {
        const next = anchor + roundRatio(index * meter.numerator * 4 * score.ticks_per_quarter, meter.denominator);
        const following = score.meters[meterIndex + 1] ?? null;
        if (following && following.tick <= next) {
            if (following.tick < next) midbar = true;
            boundaries.push(following.tick);
            meterIndex += 1;
            meter = following;
            anchor = following.tick;
            index = 1;
        } else {
            boundaries.push(next);
            index += 1;
        }
    }
    Object.defineProperty(boundaries, "midbar", { value: midbar });
    return boundaries;
}

function readyPayload(scoreValue, { duration = 12, alignment = 0 } = {}) {
    const score = scoreValue.score ?? scoreValue;
    const signatures = new Set(score.meters.map((meter) => `${meter.numerator}/${meter.denominator}`));
    const starts = barStarts(score);
    return {
        schema_version: 1,
        status: "ready",
        ticks_per_quarter: score.ticks_per_quarter,
        audio_seconds_at_tick_zero: alignment,
        audio_duration_seconds: duration,
        bar_starts: starts,
        tempos: structuredClone(score.tempos),
        meters: structuredClone(score.meters),
        markers: structuredClone(score.markers),
        sections: structuredClone(score.sections),
        source: score.source === "constant" ? "json" : score.source,
        provider: "explicit",
        meter_estimated: score.meter_estimated,
        has_variable_meter: signatures.size > 1,
        has_midbar_meter_change: starts.midbar,
        diagnostics: [],
    };
}

async function fixture(name) {
    return JSON.parse(await readFile(join(root, "tests", "fixtures", "scores", name), "utf8"));
}

function assertClose(actual, expected, tolerance = 1e-9) {
    if (typeof expected === "number" && !Number.isInteger(expected)) assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);
    else if (Array.isArray(expected)) { assert.equal(actual.length, expected.length); expected.forEach((item, index) => assertClose(actual[index], item, tolerance)); }
    else if (expected && typeof expected === "object") { assert.deepEqual(Object.keys(actual), Object.keys(expected)); for (const key of Object.keys(expected)) assertClose(actual[key], expected[key], tolerance); }
    else assert.deepEqual(actual, expected);
}

test("normalization enforces exact schema, immutability, and non-mutation", async () => {
    const payload = readyPayload(await fixture("constant_4_4.json"));
    const before = structuredClone(payload);
    const normalized = normalizeScorePayload(payload);
    assert.deepEqual(payload, before);
    assert.ok(Object.isFrozen(normalized));
    assert.ok(Object.isFrozen(normalized.score.tempos));
    assert.ok(Object.isFrozen(normalized.score.tempos[0]));
    assert.throws(() => normalizeScorePayload({ ...payload, extra: true }), ScorePayloadError);
    const missing = structuredClone(payload);
    delete missing.provider;
    assert.throws(() => normalizeScorePayload(missing), { code: "score_route_schema_invalid" });
    const reordered = Object.fromEntries([...Object.entries(payload)].reverse());
    assert.throws(() => normalizeScorePayload(reordered), { code: "score_route_schema_invalid" });
});

test("normalization rejects unsafe ticks, invalid events, ordering, flags, and bar starts", async () => {
    const base = readyPayload(await fixture("constant_4_4.json"));
    const unsafe = structuredClone(base); unsafe.tempos[0].tick = Number.MAX_SAFE_INTEGER + 1;
    assert.throws(() => normalizeScorePayload(unsafe), { code: "score_tick_out_of_safe_range" });
    const tempo = structuredClone(base); tempo.tempos.push({ tick: 480, us_per_quarter: 500000 });
    assert.throws(() => normalizeScorePayload(tempo), { code: "score_route_schema_invalid" });
    const meter = structuredClone(base); meter.meters[0].denominator = 3;
    assert.throws(() => normalizeScorePayload(meter), { code: "score_route_schema_invalid" });
    const marker = structuredClone(base); marker.markers = [{ tick: 1, name: "b" }, { tick: 1, name: "a" }];
    assert.throws(() => normalizeScorePayload(marker), { code: "score_route_schema_invalid" });
    const section = structuredClone(base); section.sections = [
        { name: "z", start_tick: 2, end_tick_exclusive: 3, bar_aligned: false, confidence: null },
        { name: "a", start_tick: 1, end_tick_exclusive: 2, bar_aligned: false, confidence: null },
    ];
    assert.throws(() => normalizeScorePayload(section), { code: "score_route_schema_invalid" });
    const bars = structuredClone(base); bars.bar_starts[1] += 1;
    assert.throws(() => normalizeScorePayload(bars), { code: "score_route_schema_invalid" });
});

test("resolver ports piecewise tempo, inverse, event ownership, and pre-roll", async () => {
    const resolver = createScoreResolver(normalizeScorePayload(readyPayload(
        await fixture("midbar_tempo.json"), { alignment: -0.5 },
    )));
    assert.equal(resolver.tickToSeconds(-480), -0.5);
    assert.equal(resolver.tickToSeconds(960), 1);
    assert.equal(resolver.tickToSeconds(1920), 1.8);
    assert.equal(resolver.secondsToTick(1.8), 1920);
    assert.equal(resolver.tickToAudioSeconds(0), -0.5);
    assert.equal(resolver.audioSecondsToTick(-0.5), 0);
    assert.equal(resolver.secondsToTick(resolver.tickToSeconds(960)), 960);
});

test("resolver handles boundary and inside-bar meter changes, clipping, extrapolation", async () => {
    const boundary = createScoreResolver(normalizeScorePayload(readyPayload(await fixture("changing_meter.json"))));
    assert.deepEqual(boundary.meterAtBar(2), { numerator: 3, denominator: 4 });
    assert.equal(boundary.barToTick(3), 3360);
    assert.deepEqual(boundary.tickToPosition(1920, 4), { bar: 2, beat: 1, subdivision: 0 });
    const shortened = createScoreResolver(normalizeScorePayload(readyPayload(await fixture("midbar_meter.json"))));
    assert.deepEqual(shortened.containingBarTicks(999), { startTick: 0, endTickExclusive: 1000, bar: 1 });
    assert.deepEqual(shortened.containingBeatTicks(999), { startTick: 960, endTickExclusive: 1000, bar: 1, beat: 3 });
    assert.equal(shortened.barToTick(10), 12520);
});

test("position conversion applies half-away tick grids and rejects over-fine grids", async () => {
    const resolver = createScoreResolver(normalizeScorePayload(readyPayload(await fixture("odd_meter_31_32.json"))));
    assert.equal(resolver.positionToTick({ bar: 1, beat: 2, subdivision: 1 }, 2), 90);
    assert.deepEqual(resolver.tickToPosition(90, 2), { bar: 1, beat: 2, subdivision: 1 });
    assert.throws(() => resolver.positionToTick({ bar: 1, beat: 1, subdivision: 0 }, 61), RangeError);
});

test("Sections are half-open and overlapping selection is deterministic", async () => {
    const raw = await fixture("unaligned_markers.json");
    const payload = readyPayload(raw);
    payload.sections = [
        { name: "wide", start_tick: 0, end_tick_exclusive: 300, bar_aligned: false, confidence: null },
        { name: "a", start_tick: 100, end_tick_exclusive: 200, bar_aligned: false, confidence: null },
        { name: "z", start_tick: 100, end_tick_exclusive: 250, bar_aligned: false, confidence: null },
    ];
    const resolver = createScoreResolver(normalizeScorePayload(payload));
    assert.equal(resolver.sectionAtTick(-1), null);
    assert.equal(resolver.sectionAtTick(100).name, "a");
    assert.equal(resolver.sectionAtTick(200).name, "z");
    assert.equal(resolver.sectionAtTick(300), null);
    assert.ok(Object.isFrozen(resolver.sections()));
});

test("derived tick overflow is rejected", async () => {
    const payload = readyPayload(await fixture("constant_4_4.json"));
    const resolver = createScoreResolver(normalizeScorePayload(payload));
    assert.throws(() => resolver.barToTick(Number.MAX_SAFE_INTEGER), { code: "score_tick_out_of_safe_range" });
});

test("resolver operations match the shared golden corpus", async () => {
    const corpus = JSON.parse(await readFile(join(root, "tests", "fixtures", "score_timing_golden_v1.json"), "utf8"));
    const resolverOperations = new Set([
        "tickToSeconds", "secondsToTick", "tickToAudioSeconds", "audioSecondsToTick", "barToTick",
        "meterAtBar", "positionToTick", "tickToPosition", "containingBarTicks", "containingBeatTicks", "sectionAtTick",
    ]);
    for (const item of corpus.cases) {
        const raw = JSON.parse(await readFile(join(root, "tests", "fixtures", item.score_fixture), "utf8"));
        const resolver = createScoreResolver(normalizeScorePayload(readyPayload(raw, {
            duration: item.audio_duration_seconds, alignment: item.audio_seconds_at_tick_zero,
        })));
        for (const query of item.queries.filter((value) => resolverOperations.has(value.operation))) {
            if (query.expected?.throws) continue;
            const args = query.arguments;
            const actual = query.operation === "positionToTick"
                ? resolver.positionToTick(args[0], args[1])
                : resolver[query.operation](...args);
            assertClose(actual, query.expected, corpus.floating_tolerance);
        }
    }
});
