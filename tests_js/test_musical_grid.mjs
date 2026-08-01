import assert from "node:assert/strict";
import test from "node:test";

import {
    durationFieldsToSubdivisionCount,
    frameToNearestSubdivision,
    musicalSelectionToSecondsRange,
    musicalPositionToSeconds,
    musicalPositionToSubdivisionIndex,
    roundHalfAwayFromZero,
    secondsPerBar,
    secondsPerBeat,
    secondsPerQuarter,
    secondsRangeToMusicalSelection,
    secondsToNearestSubdivision,
    snapToBar,
    snapToBeat,
    snapToSubdivision,
    snapToVideoFrame,
    subdivisionCountToDurationFields,
    subdivisionIndexToMusicalPosition,
    subdivisionIndexToSeconds,
    tempoUnitInQuarters,
    timingGrid,
} from "../js/musical_grid.js";

const close = (actual, expected, epsilon = 1e-12) => {
    assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} != ${expected}`);
};

test("half-away-from-zero rounding", () => {
    for (const [value, expected] of [[0.49, 0], [0.5, 1], [1.5, 2], [2.5, 3], [-0.49, 0], [-0.5, -1], [-1.5, -2]]) {
        assert.equal(roundHalfAwayFromZero(value), expected);
    }
});

test("180 BPM golden case", () => {
    const grid = timingGrid({ bpm: 180, tempoUnit: "Quarter", beatsPerBar: 4, beatUnit: 4, fps: 24, subdivisionsPerBeat: 1 });
    close(grid.secondsPerTempoPulse, 1 / 3);
    close(grid.secondsPerQuarter, 1 / 3);
    close(grid.secondsPerBeat, 1 / 3);
    close(grid.secondsPerBar, 4 / 3);
    assert.equal(roundHalfAwayFromZero(4 * grid.secondsPerBar * grid.fps), 128);
});

test("174 BPM preserves fractional frames", () => {
    const grid = timingGrid({ bpm: 174, fps: 24 });
    close(grid.secondsPerBeat, 60 / 174);
    close(grid.framesPerBeat, 24 * 60 / 174);
    assert.notEqual(grid.framesPerBeat, Math.trunc(grid.framesPerBeat));
});

test("90 BPM dotted-quarter 6/8", () => {
    const grid = timingGrid({ bpm: 90, tempoUnit: "Dotted Quarter", beatsPerBar: 6, beatUnit: 8 });
    close(tempoUnitInQuarters("Dotted Quarter"), 1.5);
    close(secondsPerQuarter(90, "Dotted Quarter"), 4 / 9);
    close(secondsPerBeat(90, "Dotted Quarter", 8), 2 / 9);
    close(secondsPerBar(90, "Dotted Quarter", 8, 6), 4 / 3);
    close(grid.secondsPerBar, 4 / 3);
});

test("position-to-subdivision conversion", () => {
    assert.equal(musicalPositionToSubdivisionIndex(
        { bar: 2, beat: 3, subdivision: 2 },
        { beatsPerBar: 4, subdivisionsPerBeat: 4 },
    ), 26);
});

test("subdivision-to-position conversion", () => {
    assert.deepEqual(subdivisionIndexToMusicalPosition(
        26,
        { beatsPerBar: 4, subdivisionsPerBeat: 4 },
    ), { bar: 2, beat: 3, subdivision: 2 });
});

test("duration canonicalization carries overflow", () => {
    const count = durationFieldsToSubdivisionCount(
        { bars: 1, beats: 5, subdivisions: 6 },
        { beatsPerBar: 4, subdivisionsPerBeat: 4 },
    );
    assert.equal(count, 42);
    assert.deepEqual(subdivisionCountToDurationFields(
        count,
        { beatsPerBar: 4, subdivisionsPerBeat: 4 },
    ), { bars: 2, beats: 2, subdivisions: 2 });
});

test("nonzero downbeat offset", () => {
    const grid = timingGrid({ bpm: 120, downbeatOffset: 0.25, subdivisionsPerBeat: 4 });
    close(musicalPositionToSeconds({ bar: 2, beat: 3, subdivision: 2 }, grid), 3.5);
    close(subdivisionIndexToSeconds(26, grid), 3.5);
});

test("negative pre-downbeat position remains signed", () => {
    const grid = timingGrid({ bpm: 120, downbeatOffset: 1, subdivisionsPerBeat: 4 });
    assert.equal(secondsToNearestSubdivision(0.5, grid), -4);
    assert.deepEqual(subdivisionIndexToMusicalPosition(-1, grid), { bar: 0, beat: 4, subdivision: 3 });
});

test("bar snapping", () => {
    const grid = timingGrid({ bpm: 120, downbeatOffset: 0.25 });
    close(snapToBar(2.1, grid), 2.25);
});

test("beat snapping", () => {
    const grid = timingGrid({ bpm: 120, downbeatOffset: 0.25 });
    close(snapToBeat(1.31, grid), 1.25);
});

test("subdivision snapping", () => {
    const grid = timingGrid({ bpm: 120, downbeatOffset: 0.25, subdivisionsPerBeat: 4 });
    close(snapToSubdivision(1.34, grid), 1.375);
});

test("video-frame snapping", () => {
    close(snapToVideoFrame(1.02, 24), 1.0);
    close(snapToVideoFrame(1.03, 24), 25 / 24);
});

test("frame-to-subdivision fallback resolves in documented order", () => {
    const grid = timingGrid({ bpm: 120, fps: 24, subdivisionsPerBeat: 4 });
    const resolved = frameToNearestSubdivision(0.19, grid);
    close(resolved.frameSeconds, 5 / 24);
    assert.equal(resolved.subdivisionIndex, 2);
    close(resolved.seconds, 0.25);
});

test("valid helper inputs never produce NaN", () => {
    const grid = timingGrid({ bpm: 123.45, tempoUnit: "Eighth", beatsPerBar: 7, beatUnit: 8, downbeatOffset: -0.2, fps: 29.97, subdivisionsPerBeat: 5 });
    const values = [
        grid.secondsPerQuarter,
        grid.secondsPerBeat,
        grid.secondsPerBar,
        musicalPositionToSeconds({ bar: 3, beat: 2, subdivision: 4 }, grid),
        snapToBar(2.3, grid),
        snapToBeat(2.3, grid),
        snapToSubdivision(2.3, grid),
        snapToVideoFrame(2.3, grid.fps),
        frameToNearestSubdivision(2.3, grid).seconds,
    ];
    assert.ok(values.every(Number.isFinite));
});

test("Seconds-to-Musical conversion has a 180 BPM four-subdivision golden case", () => {
    const grid = timingGrid({
        bpm: 180,
        tempoUnit: "Quarter",
        beatsPerBar: 4,
        beatUnit: 4,
        subdivisionsPerBeat: 4,
    });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0.5, endSeconds: 1.25 },
        grid,
    );
    assert.equal(selection.startIndex, 6);
    assert.equal(selection.endIndex, 15);
    assert.equal(selection.subdivisionCount, 9);
    close(selection.quantizedStartSeconds, 0.5);
    close(selection.quantizedEndSeconds, 1.25);
});

test("exact Seconds subdivision boundaries remain unchanged", () => {
    const grid = timingGrid({ bpm: 120, subdivisionsPerBeat: 4 });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0.25, endSeconds: 0.875 },
        grid,
    );
    assert.deepEqual(
        [selection.startIndex, selection.endIndex, selection.subdivisionCount],
        [2, 7, 5],
    );
    assert.equal(selection.expandedToOneSubdivision, false);
});

test("Seconds boundaries quantize independently to the nearest subdivision", () => {
    const grid = timingGrid({ bpm: 120, subdivisionsPerBeat: 4 });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0.14, endSeconds: 0.39 },
        grid,
    );
    assert.deepEqual(
        [selection.startIndex, selection.endIndex, selection.subdivisionCount],
        [1, 3, 2],
    );
});

test("Seconds conversion resolves exact half ties away from zero", () => {
    const grid = timingGrid({ bpm: 120, subdivisionsPerBeat: 4 });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0.0625, endSeconds: 0.1875 },
        grid,
    );
    assert.deepEqual([selection.startIndex, selection.endIndex], [1, 2]);
});

test("positive Seconds ranges collapsed by quantization expand to one subdivision", () => {
    const grid = timingGrid({ bpm: 120, subdivisionsPerBeat: 4 });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0.01, endSeconds: 0.02 },
        grid,
    );
    assert.equal(selection.subdivisionCount, 1);
    assert.equal(selection.endIndex, selection.startIndex + 1);
    assert.equal(selection.expandedToOneSubdivision, true);
});

test("zero-length Seconds ranges remain zero subdivisions", () => {
    const grid = timingGrid({ bpm: 120, subdivisionsPerBeat: 4 });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0.02, endSeconds: 0.02 },
        grid,
    );
    assert.equal(selection.subdivisionCount, 0);
    assert.equal(selection.expandedToOneSubdivision, false);
});

test("Seconds conversion honors a negative downbeat offset", () => {
    const grid = timingGrid({
        bpm: 120,
        downbeatOffset: -0.25,
        subdivisionsPerBeat: 4,
    });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0, endSeconds: 0.25 },
        grid,
    );
    assert.deepEqual([selection.startIndex, selection.endIndex], [2, 4]);
});

test("Seconds conversion honors a positive downbeat offset", () => {
    const grid = timingGrid({
        bpm: 120,
        downbeatOffset: 0.25,
        subdivisionsPerBeat: 4,
    });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0.25, endSeconds: 0.5 },
        grid,
    );
    assert.deepEqual([selection.startIndex, selection.endIndex], [0, 2]);
});

test("Seconds conversion clamps subdivision indexes before Bar 1", () => {
    const grid = timingGrid({
        bpm: 120,
        downbeatOffset: 0.5,
        subdivisionsPerBeat: 4,
    });
    const selection = secondsRangeToMusicalSelection(
        { startSeconds: 0, endSeconds: 0 },
        grid,
    );
    assert.deepEqual([selection.startIndex, selection.endIndex], [0, 0]);
});

test("Musical-to-Seconds conversion is exact on the grid", () => {
    const grid = timingGrid({ bpm: 120, subdivisionsPerBeat: 4 });
    const range = musicalSelectionToSecondsRange(
        { startIndex: 4, subdivisionCount: 8 },
        grid,
    );
    close(range.startSeconds, 0.5);
    close(range.endSeconds, 1.5);
    close(range.durationSeconds, 1);
});

test("Musical-to-Seconds conversion supports dotted-quarter tempo in 6/8", () => {
    const grid = timingGrid({
        bpm: 90,
        tempoUnit: "Dotted Quarter",
        beatsPerBar: 6,
        beatUnit: 8,
        subdivisionsPerBeat: 4,
    });
    const range = musicalSelectionToSecondsRange(
        { startIndex: 24, subdivisionCount: 6 },
        grid,
    );
    close(range.startSeconds, 4 / 3);
    close(range.endSeconds, 5 / 3);
    close(range.durationSeconds, 1 / 3);
});

test("fractional FPS does not influence selection conversion", () => {
    const lowFps = timingGrid({ bpm: 123, fps: 23.976, subdivisionsPerBeat: 4 });
    const highFps = timingGrid({ bpm: 123, fps: 59.94, subdivisionsPerBeat: 4 });
    const secondsRange = { startSeconds: 0.37, endSeconds: 1.42 };
    assert.deepEqual(
        secondsRangeToMusicalSelection(secondsRange, lowFps),
        secondsRangeToMusicalSelection(secondsRange, highFps),
    );
    assert.deepEqual(
        musicalSelectionToSecondsRange({ startIndex: 3, subdivisionCount: 9 }, lowFps),
        musicalSelectionToSecondsRange({ startIndex: 3, subdivisionCount: 9 }, highFps),
    );
});

test("selection conversion helpers return finite numeric outputs", () => {
    const grid = timingGrid({
        bpm: 123.45,
        tempoUnit: "Eighth",
        beatsPerBar: 7,
        beatUnit: 8,
        downbeatOffset: -0.2,
        fps: 29.97,
        subdivisionsPerBeat: 5,
    });
    const musical = secondsRangeToMusicalSelection(
        { startSeconds: 0.35, endSeconds: 2.7 },
        grid,
    );
    const seconds = musicalSelectionToSecondsRange(musical, grid);
    const numericValues = [...Object.values(musical), ...Object.values(seconds)]
        .filter((value) => typeof value === "number");
    assert.ok(numericValues.every(Number.isFinite));
});

test("exact-grid Seconds ranges round trip through Musical selection", () => {
    const grid = timingGrid({
        bpm: 180,
        tempoUnit: "Quarter",
        beatsPerBar: 4,
        beatUnit: 4,
        downbeatOffset: -0.125,
        subdivisionsPerBeat: 4,
    });
    const original = {
        startSeconds: subdivisionIndexToSeconds(5, grid),
        endSeconds: subdivisionIndexToSeconds(17, grid),
    };
    const musical = secondsRangeToMusicalSelection(original, grid);
    const roundTrip = musicalSelectionToSecondsRange(musical, grid);
    close(roundTrip.startSeconds, original.startSeconds);
    close(roundTrip.endSeconds, original.endSeconds);
    close(roundTrip.durationSeconds, original.endSeconds - original.startSeconds);
});
