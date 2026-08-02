import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
    MAX_WAVEFORM_DEVICE_PIXEL_RATIO,
    clearWaveformCanvas,
    createWaveformRenderPlan,
    normalizeInt16Sample,
    renderWaveformCanvas,
} from "../js/waveform_renderer.js";


function makeLevel(sampleRate, samplesPerPeak, pairs) {
    return {
        samplesPerPeak,
        peakCount: pairs.length,
        peaksPerSecond: sampleRate / samplesPerPeak,
        peaks: new Int16Array(pairs.flat()),
    };
}

function defaultPairs(peakCount, scale = 100) {
    return Array.from({ length: peakCount }, (_, index) => [
        -(index + 1) * scale,
        (index + 1) * scale,
    ]);
}

function makePyramid({
    sampleRate = 8,
    sampleCount = 8,
    levelDefinitions = [
        { samplesPerPeak: 1 },
        { samplesPerPeak: 2 },
        { samplesPerPeak: 4 },
    ],
} = {}) {
    return {
        durationSeconds: sampleCount / sampleRate,
        sampleRate,
        sampleCount,
        levels: levelDefinitions.map((definition, index) => {
            const peakCount = Math.ceil(sampleCount / definition.samplesPerPeak);
            return makeLevel(
                sampleRate,
                definition.samplesPerPeak,
                definition.pairs ?? defaultPairs(peakCount, 100 * (index + 1)),
            );
        }),
    };
}

function columns(plan) {
    return Array.from(plan.columns);
}

class MockContext2D {
    constructor() {
        this.calls = [];
        this.path = [];
        this.lineCap = "";
        this.lineWidth = 1;
        this.strokeStyle = "";
    }

    setTransform(...args) {
        this.calls.push({ name: "setTransform", args });
    }

    clearRect(...args) {
        this.calls.push({ name: "clearRect", args });
    }

    beginPath() {
        this.path = [];
        this.calls.push({ name: "beginPath", args: [] });
    }

    moveTo(...args) {
        this.path.push({ name: "moveTo", args });
        this.calls.push({ name: "moveTo", args });
    }

    lineTo(...args) {
        this.path.push({ name: "lineTo", args });
        this.calls.push({ name: "lineTo", args });
    }

    stroke() {
        this.calls.push({
            name: "stroke",
            args: [],
            lineWidth: this.lineWidth,
            path: this.path.map((entry) => ({ ...entry, args: [...entry.args] })),
            strokeStyle: this.strokeStyle,
        });
    }
}

class MockCanvas {
    constructor(clientWidth = 10, clientHeight = 20, context = new MockContext2D()) {
        this.clientWidth = clientWidth;
        this.clientHeight = clientHeight;
        this.width = 0;
        this.height = 0;
        this.context = context;
        this.contextRequests = [];
    }

    getContext(kind) {
        this.contextRequests.push(kind);
        return this.context;
    }
}

function waveformStroke(context) {
    return context.calls.find(
        (call) => call.name === "stroke" && call.strokeStyle === "#9ca3af",
    );
}

function centerLineStroke(context) {
    return context.calls.find(
        (call) => (
            call.name === "stroke"
            && call.strokeStyle === "rgba(255, 255, 255, 0.07)"
        ),
    );
}

test("rejects a missing pyramid", () => {
    assert.throws(() => createWaveformRenderPlan(null, { pixelWidth: 1 }), /pyramid/);
});

test("rejects an empty level list", () => {
    const pyramid = { ...makePyramid(), levels: [] };
    assert.throws(() => createWaveformRenderPlan(pyramid, { pixelWidth: 1 }), /levels/);
});

test("rejects a non-finite pixel width", () => {
    assert.throws(
        () => createWaveformRenderPlan(makePyramid(), { pixelWidth: Infinity }),
        /pixelWidth/,
    );
});

test("rejects a zero pixel width", () => {
    assert.throws(
        () => createWaveformRenderPlan(makePyramid(), { pixelWidth: 0 }),
        /pixelWidth/,
    );
});

test("rejects a non-integer pixel width", () => {
    assert.throws(
        () => createWaveformRenderPlan(makePyramid(), { pixelWidth: 1.5 }),
        /pixelWidth/,
    );
});

test("rejects an invalid start", () => {
    assert.throws(
        () => createWaveformRenderPlan(makePyramid(), {
            pixelWidth: 1,
            startSeconds: -0.1,
        }),
        /startSeconds/,
    );
});

test("rejects a non-finite end", () => {
    assert.throws(
        () => createWaveformRenderPlan(makePyramid(), {
            pixelWidth: 1,
            endSeconds: Number.NaN,
        }),
        /endSeconds/,
    );
});

test("rejects an end equal to the start", () => {
    assert.throws(
        () => createWaveformRenderPlan(makePyramid(), {
            pixelWidth: 1,
            startSeconds: 0.5,
            endSeconds: 0.5,
        }),
        /greater than startSeconds/,
    );
});

test("rejects an end before the start", () => {
    assert.throws(
        () => createWaveformRenderPlan(makePyramid(), {
            pixelWidth: 1,
            startSeconds: 0.75,
            endSeconds: 0.5,
        }),
        /greater than startSeconds/,
    );
});

test("rejects a range beyond the one-sample endpoint tolerance", () => {
    assert.throws(
        () => createWaveformRenderPlan(makePyramid(), {
            pixelWidth: 1,
            endSeconds: 1.126,
        }),
        /exceeds/,
    );
});

test("accepts and clamps a one-sample endpoint tolerance", () => {
    const plan = createWaveformRenderPlan(makePyramid(), {
        pixelWidth: 1,
        endSeconds: 1.125,
    });
    assert.equal(plan.endSeconds, 1);
});

test("rejects a malformed selected peak envelope", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[100, -100]] }],
    });
    assert.throws(
        () => createWaveformRenderPlan(pyramid, { pixelWidth: 1 }),
        /minimum exceeds/,
    );
});

test("full duration selects the coarsest qualifying level", () => {
    const plan = createWaveformRenderPlan(makePyramid(), { pixelWidth: 3 });
    assert.equal(plan.selectedLevelIndex, 1);
    assert.equal(plan.selectedLevel.samplesPerPeak, 2);
});

test("a dense viewport selects a finer level", () => {
    const plan = createWaveformRenderPlan(makePyramid(), { pixelWidth: 5 });
    assert.equal(plan.selectedLevelIndex, 0);
});

test("the exact level-density boundary is deterministic", () => {
    const plan = createWaveformRenderPlan(makePyramid(), { pixelWidth: 4 });
    assert.equal(plan.selectedLevelIndex, 1);
});

test("density above the finest level chooses the finest level", () => {
    const plan = createWaveformRenderPlan(makePyramid(), { pixelWidth: 9 });
    assert.equal(plan.selectedLevelIndex, 0);
});

test("density below the coarsest level chooses the coarsest level", () => {
    const plan = createWaveformRenderPlan(makePyramid(), { pixelWidth: 1 });
    assert.equal(plan.selectedLevelIndex, 2);
});

test("level selection exposes pixels per second", () => {
    const plan = createWaveformRenderPlan(makePyramid(), {
        pixelWidth: 2,
        startSeconds: 0.25,
        endSeconds: 0.75,
    });
    assert.equal(plan.pixelsPerSecond, 4);
});

test("the source pyramid is not mutated by level selection", () => {
    const pyramid = makePyramid();
    const before = pyramid.levels.map((level) => ({
        samplesPerPeak: level.samplesPerPeak,
        values: Array.from(level.peaks),
    }));
    createWaveformRenderPlan(pyramid, { pixelWidth: 3 });
    assert.deepEqual(
        pyramid.levels.map((level) => ({
            samplesPerPeak: level.samplesPerPeak,
            values: Array.from(level.peaks),
        })),
        before,
    );
});

test("one peak maps to one pixel", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[-12, 34]] }],
    });
    assert.deepEqual(columns(createWaveformRenderPlan(pyramid, { pixelWidth: 1 })), [-12, 34]);
});

test("an exact one-to-one peak mapping copies each pair", () => {
    const pairs = [[-1, 2], [-3, 4], [-5, 6], [-7, 8]];
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 2, pairs }],
    });
    assert.deepEqual(
        columns(createWaveformRenderPlan(pyramid, { pixelWidth: 4 })),
        pairs.flat(),
    );
});

test("multiple peaks aggregate into one pixel", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{
            samplesPerPeak: 2,
            pairs: [[-10, 20], [-30, 15], [-5, 40], [-25, 8]],
        }],
    });
    assert.deepEqual(columns(createWaveformRenderPlan(pyramid, { pixelWidth: 1 })), [-30, 40]);
});

test("aggregation preserves the most negative minimum", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 2, pairs: [[-2, 4], [-300, 5]] }],
    });
    assert.equal(createWaveformRenderPlan(pyramid, { pixelWidth: 1 }).columns[0], -300);
});

test("aggregation preserves the most positive maximum", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 2, pairs: [[-2, 4], [-3, 500]] }],
    });
    assert.equal(createWaveformRenderPlan(pyramid, { pixelWidth: 1 }).columns[1], 500);
});

test("aggregation does not average peaks", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 2, pairs: [[-1000, 100], [-10, 2000]] }],
    });
    assert.deepEqual(
        columns(createWaveformRenderPlan(pyramid, { pixelWidth: 1 })),
        [-1000, 2000],
    );
});

test("the first peak is represented", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{
            samplesPerPeak: 1,
            pairs: [[-3000, 1], [-2, 2], [-3, 3], [-4, 4], [-5, 5], [-6, 6], [-7, 7], [-8, 8]],
        }],
    });
    assert.equal(createWaveformRenderPlan(pyramid, { pixelWidth: 3 }).columns[0], -3000);
});

test("the final peak is represented", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{
            samplesPerPeak: 1,
            pairs: [[-1, 1], [-2, 2], [-3, 3], [-4, 4], [-5, 5], [-6, 6], [-7, 7], [-8, 3000]],
        }],
    });
    const plan = createWaveformRenderPlan(pyramid, { pixelWidth: 3 });
    assert.equal(plan.columns.at(-1), 3000);
});

test("an odd final peak is retained", () => {
    const pyramid = makePyramid({
        sampleRate: 5,
        sampleCount: 5,
        levelDefinitions: [{
            samplesPerPeak: 1,
            pairs: [[-1, 1], [-2, 2], [-3, 3], [-4, 4], [-500, 500]],
        }],
    });
    assert.deepEqual(
        columns(createWaveformRenderPlan(pyramid, { pixelWidth: 2 })),
        [-2, 2, -500, 500],
    );
});

test("downsample partitions contain no gaps", () => {
    const pyramid = makePyramid({
        sampleRate: 6,
        sampleCount: 6,
        levelDefinitions: [{
            samplesPerPeak: 1,
            pairs: [[-1, 1], [-2, 2], [-4, 4], [-8, 8], [-16, 16], [-32, 32]],
        }],
    });
    assert.deepEqual(
        columns(createWaveformRenderPlan(pyramid, { pixelWidth: 3 })),
        [-2, 2, -8, 8, -32, 32],
    );
});

test("upsampling repeats peaks", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 4, pairs: [[-10, 10], [-20, 20]] }],
    });
    assert.deepEqual(
        columns(createWaveformRenderPlan(pyramid, { pixelWidth: 5 })),
        [-10, 10, -10, 10, -10, 10, -20, 20, -20, 20],
    );
});

test("upsampling does not interpolate amplitudes", () => {
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 4, pairs: [[-100, 100], [-900, 900]] }],
    });
    const output = columns(createWaveformRenderPlan(pyramid, { pixelWidth: 7 }));
    assert.equal(output.includes(-500), false);
    assert.equal(output.includes(500), false);
});

test("column array length is pixel width times two", () => {
    assert.equal(createWaveformRenderPlan(makePyramid(), { pixelWidth: 7 }).columns.length, 14);
});

test("column storage is Int16Array", () => {
    assert.ok(createWaveformRenderPlan(makePyramid(), { pixelWidth: 3 }).columns instanceof Int16Array);
});

test("the selected input peak array is not mutated", () => {
    const pyramid = makePyramid();
    const before = Array.from(pyramid.levels[1].peaks);
    createWaveformRenderPlan(pyramid, { pixelWidth: 3 });
    assert.deepEqual(Array.from(pyramid.levels[1].peaks), before);
});

test("all multilevel source arrays remain unchanged", () => {
    const pyramid = makePyramid();
    const before = pyramid.levels.map((level) => Array.from(level.peaks));
    createWaveformRenderPlan(pyramid, { pixelWidth: 5 });
    assert.deepEqual(pyramid.levels.map((level) => Array.from(level.peaks)), before);
});

test("a partial visible range selects the correct peaks", () => {
    const pyramid = makePyramid({
        sampleRate: 4,
        sampleCount: 4,
        levelDefinitions: [{
            samplesPerPeak: 1,
            pairs: [[-1, 1], [-20, 20], [-30, 30], [-4, 4]],
        }],
    });
    const plan = createWaveformRenderPlan(pyramid, {
        pixelWidth: 2,
        startSeconds: 0.25,
        endSeconds: 0.75,
    });
    assert.deepEqual(columns(plan), [-20, 20, -30, 30]);
    assert.equal(plan.firstPeak, 1);
    assert.equal(plan.finalPeakExclusive, 3);
});

test("a tiny visible range remains valid", () => {
    const pyramid = makePyramid({
        sampleRate: 4,
        sampleCount: 4,
        levelDefinitions: [{
            samplesPerPeak: 1,
            pairs: [[-1, 1], [-20, 20], [-30, 30], [-4, 4]],
        }],
    });
    const plan = createWaveformRenderPlan(pyramid, {
        pixelWidth: 4,
        startSeconds: 0.251,
        endSeconds: 0.252,
    });
    assert.deepEqual(columns(plan), [-20, 20, -20, 20, -20, 20, -20, 20]);
});

test("render-plan options are not mutated", () => {
    const options = Object.freeze({ pixelWidth: 3, startSeconds: 0, endSeconds: 1 });
    createWaveformRenderPlan(makePyramid(), options);
    assert.deepEqual(options, { pixelWidth: 3, startSeconds: 0, endSeconds: 1 });
});

test("-32768 normalizes exactly to -1", () => {
    assert.equal(normalizeInt16Sample(-32768), -1);
});

test("zero normalizes exactly to zero", () => {
    assert.equal(normalizeInt16Sample(0), 0);
});

test("32767 normalizes exactly to positive one", () => {
    assert.equal(normalizeInt16Sample(32767), 1);
});

test("negative values use divisor 32768", () => {
    assert.equal(normalizeInt16Sample(-16384), -0.5);
});

test("positive values use divisor 32767", () => {
    assert.equal(normalizeInt16Sample(16384), 16384 / 32767);
});

test("normalization defensively clamps out-of-range values", () => {
    assert.equal(normalizeInt16Sample(-40000), -1);
    assert.equal(normalizeInt16Sample(40000), 1);
});

test("DPR 1 creates an exact CSS-sized backing store", () => {
    const canvas = new MockCanvas(10, 20);
    renderWaveformCanvas(canvas, makePyramid(), { devicePixelRatio: 1 });
    assert.deepEqual([canvas.width, canvas.height], [10, 20]);
});

test("DPR 2 doubles backing dimensions", () => {
    const canvas = new MockCanvas(10, 20);
    renderWaveformCanvas(canvas, makePyramid(), { devicePixelRatio: 2 });
    assert.deepEqual([canvas.width, canvas.height], [20, 40]);
});

test("fractional DPR rounds backing dimensions deterministically", () => {
    const canvas = new MockCanvas(9, 5);
    const result = renderWaveformCanvas(canvas, makePyramid(), { devicePixelRatio: 1.5 });
    assert.deepEqual([canvas.width, canvas.height], [14, 8]);
    assert.equal(result.devicePixelRatio, 1.5);
});

test("invalid DPR falls back to one", () => {
    const canvas = new MockCanvas(9, 5);
    const result = renderWaveformCanvas(canvas, makePyramid(), { devicePixelRatio: Number.NaN });
    assert.deepEqual([canvas.width, canvas.height], [9, 5]);
    assert.equal(result.devicePixelRatio, 1);
});

test("excessive DPR is clamped to the practical maximum", () => {
    const canvas = new MockCanvas(9, 5);
    const result = renderWaveformCanvas(canvas, makePyramid(), { devicePixelRatio: 20 });
    assert.deepEqual([canvas.width, canvas.height], [36, 20]);
    assert.equal(result.devicePixelRatio, MAX_WAVEFORM_DEVICE_PIXEL_RATIO);
});

test("repeated renders reset transforms instead of accumulating them", () => {
    const canvas = new MockCanvas(4, 20);
    renderWaveformCanvas(canvas, makePyramid(), { devicePixelRatio: 2 });
    renderWaveformCanvas(canvas, makePyramid(), { devicePixelRatio: 2 });
    const transforms = canvas.context.calls
        .filter((call) => call.name === "setTransform")
        .map((call) => call.args);
    assert.deepEqual(transforms, [
        [1, 0, 0, 1, 0, 0],
        [2, 0, 0, 2, 0, 0],
        [1, 0, 0, 1, 0, 0],
        [2, 0, 0, 2, 0, 0],
    ]);
});

test("existing pixels are cleared before drawing", () => {
    const canvas = new MockCanvas(4, 20);
    renderWaveformCanvas(canvas, makePyramid());
    const clearIndex = canvas.context.calls.findIndex((call) => call.name === "clearRect");
    const drawIndex = canvas.context.calls.findIndex((call) => call.name === "beginPath");
    assert.ok(clearIndex >= 0 && clearIndex < drawIndex);
});

test("the subtle center line is drawn", () => {
    const canvas = new MockCanvas(4, 20);
    renderWaveformCanvas(canvas, makePyramid());
    const stroke = centerLineStroke(canvas.context);
    assert.ok(stroke);
    assert.deepEqual(stroke.path, [
        { name: "moveTo", args: [0, 10] },
        { name: "lineTo", args: [4, 10] },
    ]);
});

test("a positive peak maps above center", () => {
    const canvas = new MockCanvas(1, 20);
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[0, 32767]] }],
    });
    renderWaveformCanvas(canvas, pyramid);
    const stroke = waveformStroke(canvas.context);
    assert.equal(stroke.path[0].args[1], 4);
    assert.equal(stroke.path[1].args[1], 10);
});

test("a negative peak maps below center", () => {
    const canvas = new MockCanvas(1, 20);
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[-32768, 0]] }],
    });
    renderWaveformCanvas(canvas, pyramid);
    const stroke = waveformStroke(canvas.context);
    assert.equal(stroke.path[0].args[1], 10);
    assert.equal(stroke.path[1].args[1], 16);
});

test("asymmetric min and max remain asymmetric", () => {
    const canvas = new MockCanvas(1, 20);
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[-16384, 32767]] }],
    });
    renderWaveformCanvas(canvas, pyramid);
    const stroke = waveformStroke(canvas.context);
    assert.equal(stroke.path[0].args[1], 4);
    assert.equal(stroke.path[1].args[1], 13);
});

test("a non-zero constant envelope produces a visible segment", () => {
    const canvas = new MockCanvas(1, 20);
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[100, 100]] }],
    });
    renderWaveformCanvas(canvas, pyramid, { devicePixelRatio: 2 });
    const stroke = waveformStroke(canvas.context);
    assert.ok(stroke.path[1].args[1] - stroke.path[0].args[1] >= 0.5);
});

test("digital silence does not invent a signal envelope", () => {
    const canvas = new MockCanvas(3, 20);
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[0, 0]] }],
    });
    const result = renderWaveformCanvas(canvas, pyramid);
    assert.equal(waveformStroke(canvas.context), undefined);
    assert.equal(result.drawnColumnCount, 0);
    assert.ok(centerLineStroke(canvas.context));
});

test("one segment is drawn per visible non-silent pixel", () => {
    const canvas = new MockCanvas(5, 20);
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[-100, 100]] }],
    });
    const result = renderWaveformCanvas(canvas, pyramid);
    const stroke = waveformStroke(canvas.context);
    assert.equal(result.drawnColumnCount, 5);
    assert.equal(stroke.path.filter((entry) => entry.name === "moveTo").length, 5);
});

test("the default four-pixel vertical padding is retained", () => {
    const canvas = new MockCanvas(1, 20);
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[-32768, 32767]] }],
    });
    const result = renderWaveformCanvas(canvas, pyramid);
    assert.equal(result.verticalPadding, 4);
    assert.deepEqual(
        waveformStroke(canvas.context).path.map((entry) => entry.args[1]),
        [4, 16],
    );
});

test("draw coordinates remain inside CSS bounds", () => {
    const canvas = new MockCanvas(7, 23);
    renderWaveformCanvas(canvas, makePyramid());
    const coordinateCalls = canvas.context.calls.filter(
        (call) => call.name === "moveTo" || call.name === "lineTo",
    );
    for (const call of coordinateCalls) {
        assert.ok(call.args[0] >= 0 && call.args[0] <= 7);
        assert.ok(call.args[1] >= 0 && call.args[1] <= 23);
    }
});

test("rendering does not modify any source peak array", () => {
    const pyramid = makePyramid();
    const before = pyramid.levels.map((level) => Array.from(level.peaks));
    renderWaveformCanvas(new MockCanvas(5, 20), pyramid);
    assert.deepEqual(pyramid.levels.map((level) => Array.from(level.peaks)), before);
});

test("render returns selected-level and backing-store metadata", () => {
    const result = renderWaveformCanvas(
        new MockCanvas(3, 20),
        makePyramid(),
        { devicePixelRatio: 2 },
    );
    assert.equal(result.selectedLevelIndex, 1);
    assert.equal(result.pixelWidth, 3);
    assert.equal(result.backingWidth, 6);
    assert.equal(result.backingHeight, 40);
});

test("renderer options can override neutral drawing styles and padding", () => {
    const canvas = new MockCanvas(1, 20);
    const pyramid = makePyramid({
        levelDefinitions: [{ samplesPerPeak: 8, pairs: [[-32768, 32767]] }],
    });
    const result = renderWaveformCanvas(canvas, pyramid, {
        centerLineColor: "center",
        waveformColor: "wave",
        verticalPadding: 3,
    });
    const strokes = canvas.context.calls.filter((call) => call.name === "stroke");
    assert.deepEqual(strokes.map((stroke) => stroke.strokeStyle), ["center", "wave"]);
    assert.equal(result.verticalPadding, 3);
});

test("clear removes all prior backing-store pixels", () => {
    const canvas = new MockCanvas(10, 5);
    clearWaveformCanvas(canvas, { devicePixelRatio: 2 });
    const clear = canvas.context.calls.find((call) => call.name === "clearRect");
    assert.deepEqual(clear.args, [0, 0, 20, 10]);
});

test("clear handles a zero client size with a one-pixel backing store", () => {
    const canvas = new MockCanvas(0, 0);
    const result = clearWaveformCanvas(canvas, { devicePixelRatio: 2 });
    assert.deepEqual([canvas.width, canvas.height], [1, 1]);
    assert.deepEqual([result.cssWidth, result.cssHeight], [0, 0]);
});

test("clear safely handles an unavailable 2D context", () => {
    const canvas = new MockCanvas(10, 5, null);
    assert.doesNotThrow(() => clearWaveformCanvas(canvas));
    assert.equal(clearWaveformCanvas(canvas).contextAvailable, false);
});

test("clear resizes the backing store before clearing", () => {
    const canvas = new MockCanvas(6, 4);
    const result = clearWaveformCanvas(canvas, { devicePixelRatio: 1.5 });
    assert.deepEqual([canvas.width, canvas.height], [9, 6]);
    assert.deepEqual([result.backingWidth, result.backingHeight], [9, 6]);
});

test("render safely returns metadata when the 2D context is unavailable", () => {
    const result = renderWaveformCanvas(new MockCanvas(3, 20, null), makePyramid());
    assert.equal(result.contextAvailable, false);
    assert.equal(result.columns.length, 6);
});

const uiSource = readFileSync(new URL("../js/musical_audio_ui.js", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("../js/musical_audio_ui.css", import.meta.url), "utf8");
const rendererSource = readFileSync(new URL("../js/waveform_renderer.js", import.meta.url), "utf8");

test("integration creates exactly one waveform Canvas", () => {
    assert.equal((uiSource.match(/makeElement\("canvas"/g) ?? []).length, 1);
});

test("integration appends the Canvas before the selection", () => {
    assert.match(uiSource, /sliderBox\.append\(waveformCanvas, fill\)/);
});

test("the decorative Canvas is aria-hidden and has no tab index", () => {
    assert.match(uiSource, /waveformCanvas\.setAttribute\("aria-hidden", "true"\)/);
    assert.doesNotMatch(uiSource, /waveformCanvas\.tabIndex/);
});

test("the Canvas receives no pointer listener", () => {
    assert.doesNotMatch(uiSource, /waveformCanvas\.addEventListener/);
});

test("integration constructs one node-local ResizeObserver", () => {
    assert.equal((uiSource.match(/new ResizeObserver\(/g) ?? []).length, 1);
    assert.match(uiSource, /resizeObserver\.observe\(sliderBox\)/);
});

test("render scheduling uses requestAnimationFrame", () => {
    assert.match(
        uiSource,
        /scheduleMusicalAudioWaveformRender[\s\S]*?requestAnimationFrame\(/,
    );
});

test("the loader state callback schedules a render without dirtying the graph", () => {
    const callback = uiSource.match(/onStateChange: \(state\) => \{([\s\S]*?)\n\s*\},/)[1];
    assert.match(callback, /scheduleMusicalAudioWaveformRender/);
    assert.doesNotMatch(callback, /dirty|setDirty/);
});

test("node removal destroys renderer and loader separately", () => {
    const removal = uiSource.match(/nodeType\.prototype\.onRemoved = function \(\) \{([\s\S]*?)return onRemoved/)[1];
    assert.match(removal, /destroyMusicalAudioWaveformRenderer/);
    assert.match(removal, /destroyMusicalAudioWaveformData/);
});

test("loadedmetadata and durationchange schedule waveform rendering", () => {
    const metadata = uiSource.match(/addEventListener\("loadedmetadata"[\s\S]*?\n\s*\}\);/)[0];
    const duration = uiSource.match(/addEventListener\("durationchange"[\s\S]*?\n\s*\}\);/)[0];
    assert.match(metadata, /scheduleMusicalAudioWaveformRender/);
    assert.match(duration, /scheduleMusicalAudioWaveformRender/);
});

test("timeupdate does not schedule a waveform redraw", () => {
    const timeupdate = uiSource.match(/addEventListener\("timeupdate"[\s\S]*?\n\s*\}\);/)[0];
    assert.doesNotMatch(timeupdate, /WaveformRender/);
});

test("timeline refresh schedules waveform rendering", () => {
    const refresh = uiSource.match(/node\.refreshMusicalAudioTimeline = \(\) => \{([\s\S]*?)\n\s*\};/)[1];
    assert.match(refresh, /scheduleMusicalAudioWaveformRender/);
});

test("CSS fixes the timeline at 80 pixels", () => {
    assert.match(cssSource, /--mau-timeline-height: 80px;/);
});

test("CSS layers waveform, selection, and handles in ascending order", () => {
    const waveformRule = cssSource.match(/\.musical-audio-ui__waveform \{([\s\S]*?)\}/)[1];
    const selectionRule = cssSource.match(/\.musical-audio-ui__selection \{([\s\S]*?)\}/)[1];
    const handleRule = cssSource.match(/\.musical-audio-ui__handle \{([\s\S]*?)\}/)[1];
    assert.match(waveformRule, /z-index: 0/);
    assert.match(selectionRule, /z-index: 1/);
    assert.match(handleRule, /z-index: 2/);
});

test("CSS keeps the Canvas non-interactive and fully sized", () => {
    const rule = cssSource.match(/\.musical-audio-ui__waveform \{([\s\S]*?)\}/)[1];
    assert.match(rule, /position: absolute/);
    assert.match(rule, /inset: 0/);
    assert.match(rule, /width: 100%/);
    assert.match(rule, /height: 100%/);
    assert.match(rule, /pointer-events: none/);
    assert.match(rule, /opacity: 0\.78/);
});

test("renderer module has no application, loader, network, or DOM-query dependency", () => {
    assert.doesNotMatch(rendererSource, /scripts\/app|scripts\/api|waveform_loader/);
    assert.doesNotMatch(rendererSource, /fetch\(|XMLHttpRequest|querySelector|console\./);
});

test("integration introduces no zoom, wheel, playhead, or waveform click feature", () => {
    assert.doesNotMatch(rendererSource, /WebGL|OffscreenCanvas|WaveSurfer/);
    assert.doesNotMatch(uiSource, /waveformZoom|waveformPlayhead|waveformCanvas\.onclick/);
    assert.doesNotMatch(uiSource, /sliderBox\.addEventListener\("wheel"/);
});
