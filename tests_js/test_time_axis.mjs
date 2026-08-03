import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { normalizeScorePayload } from "../js/score.js";
import {
    CONSTANT_SYNTHETIC_TICKS_PER_QUARTER,
    createConstantTimeAxis,
    createScoreTimeAxis,
} from "../js/time_axis.js";
import { snapToBeat, timingGrid } from "../js/musical_grid.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const facade = [
    "kind", "positionToTick", "tickToPosition", "tickToScoreSeconds", "scoreSecondsToTick",
    "tickToAudioSeconds", "audioSecondsToTick", "barToTick", "meterAtBar",
    "enumerateVisibleBoundaries", "sectionAtTick", "durationToCanonicalEnd", "snapToBar",
    "snapToBeat", "snapToSubdivision", "snapToVideoFrame", "audioRangeToCanonicalSelection",
    "canonicalSelectionToAudioRange",
];

function roundRatio(numerator, denominator) {
    const sign = numerator < 0 ? -1 : 1;
    const absolute = Math.abs(numerator);
    let quotient = Math.floor(absolute / denominator);
    if ((absolute - quotient * denominator) * 2 >= denominator) quotient += 1;
    return sign * quotient;
}

function starts(score) {
    const result = [0]; let meterIndex = 0; let meter = score.meters[0]; let anchor = 0; let index = 1; let midbar = false;
    while (result.at(-1) <= 20_000) {
        const next = anchor + roundRatio(index * meter.numerator * 4 * score.ticks_per_quarter, meter.denominator);
        const following = score.meters[meterIndex + 1] ?? null;
        if (following && following.tick <= next) { if (following.tick < next) midbar = true; result.push(following.tick); meter = following; meterIndex += 1; anchor = following.tick; index = 1; }
        else { result.push(next); index += 1; }
    }
    Object.defineProperty(result, "midbar", { value: midbar });
    return result;
}

function payload(raw, duration = 12, alignment = 0) {
    const score = raw.score ?? raw;
    const barStarts = starts(score);
    return {
        schema_version: 1, status: "ready", ticks_per_quarter: score.ticks_per_quarter,
        audio_seconds_at_tick_zero: alignment, audio_duration_seconds: duration, bar_starts: barStarts,
        tempos: structuredClone(score.tempos), meters: structuredClone(score.meters),
        markers: structuredClone(score.markers), sections: structuredClone(score.sections),
        source: score.source === "constant" ? "json" : score.source, provider: "explicit",
        meter_estimated: score.meter_estimated,
        has_variable_meter: new Set(score.meters.map((meter) => `${meter.numerator}/${meter.denominator}`)).size > 1,
        has_midbar_meter_change: barStarts.midbar, diagnostics: [],
    };
}

async function scoreAxis(name, duration = 12, alignment = 0) {
    const raw = JSON.parse(await readFile(join(root, "tests", "fixtures", "scores", name), "utf8"));
    return createScoreTimeAxis(normalizeScorePayload(payload(raw, duration, alignment)));
}

function assertClose(actual, expected, tolerance = 1e-9) {
    if (typeof expected === "number" && !Number.isInteger(expected)) assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);
    else if (Array.isArray(expected)) { assert.equal(actual.length, expected.length); expected.forEach((value, index) => assertClose(actual[index], value, tolerance)); }
    else if (expected && typeof expected === "object") { assert.deepEqual(Object.keys(actual), Object.keys(expected)); for (const key of Object.keys(expected)) assertClose(actual[key], expected[key], tolerance); }
    else assert.deepEqual(actual, expected);
}

test("both axes expose only the exact frozen common facade", async () => {
    const constant = createConstantTimeAxis({ bpm: 120, tempoUnit: "Quarter", beatsPerBar: 4, beatUnit: 4, downbeatOffset: 0 });
    const score = await scoreAxis("constant_4_4.json");
    assert.deepEqual(Object.keys(constant), facade);
    assert.deepEqual(Object.keys(score), facade);
    assert.ok(Object.isFrozen(constant)); assert.ok(Object.isFrozen(score));
    assert.equal(CONSTANT_SYNTHETIC_TICKS_PER_QUARTER, 960);
});

test("Constant axis preserves historical floating behavior and signed ties", () => {
    const config = { bpm: 120, tempoUnit: "Quarter", beatsPerBar: 4, beatUnit: 4, downbeatOffset: 0 };
    const axis = createConstantTimeAxis(config);
    const grid = timingGrid({ ...config, subdivisionsPerBeat: 1, fps: 1 });
    assert.equal(axis.kind, "constant");
    assert.equal(axis.snapToBeat(-0.25).audioSeconds, snapToBeat(-0.25, grid));
    assert.equal(axis.snapToBeat(-0.25).audioSeconds, -0.5);
    const signed = axis.tickToPosition(-960, 4);
    assert.equal(axis.positionToTick(signed, 4), -960);
    assert.equal(axis.positionToTick({ bar: 2, beat: 1, subdivision: 0 }, 4), 3840);
    assert.deepEqual(axis.meterAtBar(99), { numerator: 4, denominator: 4 });
    assert.equal(axis.sectionAtTick(0), null);
    assert.ok(Object.isFrozen(axis.snapToSubdivision(0.1, 4).position));
});

test("Score axis conversions use absolute alignment and piecewise tempo", async () => {
    const axis = await scoreAxis("midbar_tempo.json", 10, -0.5);
    assert.equal(axis.kind, "score");
    assert.equal(axis.tickToScoreSeconds(1920), 1.8);
    assert.equal(axis.tickToAudioSeconds(1920), 1.3);
    assert.equal(axis.audioSecondsToTick(-0.5), 0);
    assert.equal(axis.scoreSecondsToTick(1.8), 1920);
});

test("visible Score boundaries use actual times, deduplicate strength, and retain all candidates", async () => {
    const axis = await scoreAxis("constant_4_4.json", 8, 0.25);
    const values = axis.enumerateVisibleBoundaries({ startAudioSeconds: 0.25, endAudioSeconds: 1.3 }, 4);
    assert.deepEqual(values.slice(0, 3).map((item) => item.kind), ["bar", "subdivision", "subdivision"]);
    assert.equal(values[0].tick, 0);
    assert.equal(new Set(values.map((item) => item.tick)).size, values.length);
    assert.equal(values.length, 9);
    assert.ok(values.every(Object.isFrozen));
    assert.deepEqual(axis.enumerateVisibleBoundaries({ startAudioSeconds: -1, endAudioSeconds: 0.25 }, 4), []);
});

test("Score snapping is nearest in actual seconds, chooses later ties, and clamps before zero", async () => {
    const constant = await scoreAxis("constant_4_4.json", 8, 0.25);
    assert.deepEqual(constant.snapToBeat(0.5), { audioSeconds: 0.75, tick: 480, position: { bar: 1, beat: 2, subdivision: 0 } });
    assert.equal(constant.snapToBar(-100).tick, 0);
    const tempo = await scoreAxis("midbar_tempo.json", 10, -0.5);
    assert.equal(tempo.snapToBeat(0.7).tick, 1440);
    const shortened = await scoreAxis("midbar_meter.json");
    assert.equal(shortened.snapToBar(1.02).tick, 1000);
    assert.equal(shortened.snapToBar(20).position.bar > 2, true);
});

test("frame snap ignores the musical axis", async () => {
    const axis = await scoreAxis("constant_4_4.json");
    assert.deepEqual(axis.snapToVideoFrame(-0.03, 24), { audioSeconds: -1 / 24, tick: null, position: null });
});

test("canonical ranges are half-open, expand only nonempty intent, and round-trip exact ticks", async () => {
    const axis = await scoreAxis("constant_4_4.json", 8, 0.25);
    const expanded = axis.audioRangeToCanonicalSelection({ startAudioSeconds: 0.26, endAudioSeconds: 0.26, nonEmptyIntent: true }, 4);
    assert.deepEqual(expanded, {
        start: { bar: 1, beat: 1, subdivision: 0 }, endExclusive: { bar: 1, beat: 1, subdivision: 1 },
        startTick: 0, endTickExclusive: 120, expandedToOneSubdivision: true,
    });
    const empty = axis.audioRangeToCanonicalSelection({ startAudioSeconds: 0.26, endAudioSeconds: 0.26, nonEmptyIntent: false }, 4);
    assert.equal(empty.startTick, empty.endTickExclusive); assert.equal(empty.expandedToOneSubdivision, false);
    assert.deepEqual(axis.canonicalSelectionToAudioRange(expanded, 4), {
        startAudioSeconds: 0.25, endAudioSeconds: 0.375, durationSeconds: 0.125, startTick: 0, endTickExclusive: 120,
    });
    assert.throws(() => axis.canonicalSelectionToAudioRange({ ...expanded, startTick: 1 }, 4), { code: "score_selection_range_invalid" });
    assert.throws(() => axis.audioRangeToCanonicalSelection({ startAudioSeconds: 2, endAudioSeconds: 1, nonEmptyIntent: true }, 4), { code: "score_selection_range_invalid" });
});

test("duration fallback is meter-stable, permits end changes, and rejects internal changes", async () => {
    const tempo = await scoreAxis("midbar_tempo.json");
    assert.deepEqual(tempo.durationToCanonicalEnd(
        { bar: 1, beat: 1, subdivision: 0 }, { bars: 1, beats: 0, subdivisions: 0 }, 4,
    ), { endExclusive: { bar: 2, beat: 1, subdivision: 0 }, startTick: 0, endTickExclusive: 1920, meterStable: true });
    const boundary = await scoreAxis("changing_meter.json");
    assert.equal(boundary.durationToCanonicalEnd(
        { bar: 1, beat: 1, subdivision: 0 }, { bars: 1, beats: 0, subdivisions: 0 }, 4,
    ).endTickExclusive, 1920);
    const shortened = await scoreAxis("midbar_meter.json");
    assert.throws(() => shortened.durationToCanonicalEnd(
        { bar: 1, beat: 1, subdivision: 0 }, { bars: 1, beats: 0, subdivisions: 0 }, 4,
    ), { name: "TimeAxisError", code: "score_end_position_required" });
});

test("duration fallback combines the start offset and duration before rounding on uneven subdivision grids", async () => {
    const axis = await scoreAxis("constant_4_4.json");
    const cases = [
        {
            start: { bar: 1, beat: 1, subdivision: 3 }, duration: { bars: 0, beats: 0, subdivisions: 3 }, subdivisionsPerBeat: 7,
            expected: { endExclusive: { bar: 1, beat: 1, subdivision: 6 }, startTick: 206, endTickExclusive: 411, meterStable: true },
        },
        {
            start: { bar: 1, beat: 1, subdivision: 6 }, duration: { bars: 0, beats: 0, subdivisions: 2 }, subdivisionsPerBeat: 7,
            expected: { endExclusive: { bar: 1, beat: 2, subdivision: 1 }, startTick: 411, endTickExclusive: 549, meterStable: true },
        },
        {
            start: { bar: 1, beat: 1, subdivision: 4 }, duration: { bars: 0, beats: 0, subdivisions: 4 }, subdivisionsPerBeat: 9,
            expected: { endExclusive: { bar: 1, beat: 1, subdivision: 8 }, startTick: 213, endTickExclusive: 427, meterStable: true },
        },
        {
            start: { bar: 1, beat: 1, subdivision: 1 }, duration: { bars: 0, beats: 0, subdivisions: 1 }, subdivisionsPerBeat: 11,
            expected: { endExclusive: { bar: 1, beat: 1, subdivision: 2 }, startTick: 44, endTickExclusive: 87, meterStable: true },
        },
    ];
    for (const { start, duration, subdivisionsPerBeat, expected } of cases) {
        assert.deepEqual(axis.durationToCanonicalEnd(start, duration, subdivisionsPerBeat), expected);
    }
    const firstCase = cases[0];
    const firstResult = axis.durationToCanonicalEnd(firstCase.start, firstCase.duration, firstCase.subdivisionsPerBeat);
    assert.notEqual(firstResult.endTickExclusive, 412);
});

test("TimeAxis operations match the shared golden corpus", async () => {
    const corpus = JSON.parse(await readFile(join(root, "tests", "fixtures", "score_timing_golden_v1.json"), "utf8"));
    const operations = new Set([
        "enumerateVisibleBoundaries", "snapToBar", "snapToBeat", "snapToSubdivision", "snapToVideoFrame",
        "audioRangeToCanonicalSelection", "canonicalSelectionToAudioRange", "durationToCanonicalEnd",
    ]);
    for (const item of corpus.cases) {
        const raw = JSON.parse(await readFile(join(root, "tests", "fixtures", item.score_fixture), "utf8"));
        const axis = createScoreTimeAxis(normalizeScorePayload(payload(raw, item.audio_duration_seconds, item.audio_seconds_at_tick_zero)));
        for (const query of item.queries.filter((value) => operations.has(value.operation))) {
            if (query.expected?.throws) {
                assert.throws(() => axis[query.operation](...query.arguments), { name: query.expected.throws.name, code: query.expected.throws.code });
            } else assertClose(axis[query.operation](...query.arguments), query.expected, corpus.floating_tolerance);
        }
    }
});
