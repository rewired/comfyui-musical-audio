import assert from "node:assert/strict";
import test from "node:test";

import {
    durationFieldsToSubdivisionCount,
    frameToNearestSubdivision,
    musicalPositionToSeconds,
    musicalPositionToSubdivisionIndex,
    roundHalfAwayFromZero,
    secondsPerBar,
    secondsPerBeat,
    secondsPerQuarter,
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
