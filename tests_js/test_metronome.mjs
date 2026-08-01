import assert from "node:assert/strict";
import test from "node:test";

import {
    isMetronomeDownbeat,
    metronomeBeatIndexAtOrAfter,
    metronomeBeatTime,
    metronomeVolumeGain,
} from "../js/metronome.js";
import { timingGrid } from "../js/musical_grid.js";

const close = (actual, expected, epsilon = 1e-12) => {
    assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} != ${expected}`);
};

test("exact zero-time beat boundary is retained", () => {
    const timing = timingGrid({ bpm: 120, downbeatOffset: 0 });
    assert.equal(metronomeBeatIndexAtOrAfter(0, timing), 0);
});

test("exact nonzero beat boundary is retained", () => {
    const timing = timingGrid({ bpm: 120, downbeatOffset: 0 });
    assert.equal(metronomeBeatIndexAtOrAfter(1.5, timing), 3);
});

test("a position just after a boundary selects the following beat", () => {
    const timing = timingGrid({ bpm: 120, downbeatOffset: 0 });
    assert.equal(metronomeBeatIndexAtOrAfter(1.5 + 1e-8, timing), 4);
});

test("positive downbeat offsets produce signed pre-downbeat indexes", () => {
    const timing = timingGrid({ bpm: 120, downbeatOffset: 1 });
    assert.equal(metronomeBeatIndexAtOrAfter(0, timing), -2);
    close(metronomeBeatTime(-2, timing), 0);
});

test("negative downbeat offsets align later beat indexes", () => {
    const timing = timingGrid({ bpm: 120, downbeatOffset: -0.25 });
    assert.equal(metronomeBeatIndexAtOrAfter(0, timing), 1);
    close(metronomeBeatTime(1, timing), 0.25);
});

test("beat indexes convert to absolute media time", () => {
    const timing = timingGrid({ bpm: 120, downbeatOffset: 0.125 });
    close(metronomeBeatTime(5, timing), 2.625);
});

test("4/4 accents every fourth beat", () => {
    assert.deepEqual(
        Array.from({ length: 8 }, (_, index) => isMetronomeDownbeat(index, 4)),
        [true, false, false, false, true, false, false, false],
    );
});

test("3/4 accents every third beat", () => {
    assert.deepEqual(
        Array.from({ length: 6 }, (_, index) => isMetronomeDownbeat(index, 3)),
        [true, false, false, true, false, false],
    );
});

test("6/8 accents every sixth meter beat", () => {
    assert.deepEqual(
        Array.from({ length: 12 }, (_, index) => isMetronomeDownbeat(index, 6)),
        [true, false, false, false, false, false, true, false, false, false, false, false],
    );
});

test("negative indexes use normalized modulo for downbeats", () => {
    assert.equal(isMetronomeDownbeat(-8, 4), true);
    assert.equal(isMetronomeDownbeat(-4, 4), true);
    assert.equal(isMetronomeDownbeat(-3, 4), false);
    assert.equal(isMetronomeDownbeat(-1, 4), false);
});

test("dotted-quarter timing from timingGrid controls meter-beat spacing", () => {
    const timing = timingGrid({
        bpm: 90,
        tempoUnit: "Dotted Quarter",
        beatsPerBar: 6,
        beatUnit: 8,
    });
    close(metronomeBeatTime(6, timing) - metronomeBeatTime(0, timing), 4 / 3);
});

test("eighth-note tempo unit from timingGrid controls meter-beat spacing", () => {
    const timing = timingGrid({ bpm: 120, tempoUnit: "Eighth", beatUnit: 4 });
    close(metronomeBeatTime(1, timing), 1);
});

test("FPS changes do not affect metronome beat times", () => {
    const lowFps = timingGrid({ bpm: 137, fps: 23.976, downbeatOffset: 0.1 });
    const highFps = timingGrid({ bpm: 137, fps: 120, downbeatOffset: 0.1 });
    close(metronomeBeatTime(17, lowFps), metronomeBeatTime(17, highFps));
});

test("subdivisions per beat do not affect metronome click positions", () => {
    const coarse = timingGrid({ bpm: 137, subdivisionsPerBeat: 1, downbeatOffset: -0.2 });
    const fine = timingGrid({ bpm: 137, subdivisionsPerBeat: 16, downbeatOffset: -0.2 });
    close(metronomeBeatTime(17, coarse), metronomeBeatTime(17, fine));
    assert.equal(
        metronomeBeatIndexAtOrAfter(3.2, coarse),
        metronomeBeatIndexAtOrAfter(3.2, fine),
    );
});

test("finite valid inputs produce finite outputs", () => {
    const timing = { secondsPerBeat: 0.125, downbeatOffset: -12.5 };
    const beatIndex = metronomeBeatIndexAtOrAfter(123.456, timing);
    assert.equal(Number.isFinite(beatIndex), true);
    assert.equal(Number.isFinite(metronomeBeatTime(beatIndex, timing)), true);
});

test("invalid secondsPerBeat values fail deterministically", () => {
    for (const secondsPerBeat of [0, -1, Number.NaN, Number.POSITIVE_INFINITY, "0.5"]) {
        assert.throws(
            () => metronomeBeatIndexAtOrAfter(0, { secondsPerBeat, downbeatOffset: 0 }),
            secondsPerBeat === 0 || secondsPerBeat === -1 ? RangeError : TypeError,
        );
    }
});

test("invalid beatsPerBar values fail deterministically", () => {
    for (const beatsPerBar of [0, -1]) {
        assert.throws(() => isMetronomeDownbeat(0, beatsPerBar), RangeError);
    }
    for (const beatsPerBar of [1.5, Number.NaN, "4"]) {
        assert.throws(() => isMetronomeDownbeat(0, beatsPerBar), TypeError);
    }
});

test("0% metronome volume produces gain 0", () => {
    assert.equal(metronomeVolumeGain(0), 0);
});

test("50% metronome volume produces gain 0.5", () => {
    assert.equal(metronomeVolumeGain(50), 0.5);
});

test("100% metronome volume produces gain 1", () => {
    assert.equal(metronomeVolumeGain(100), 1);
});

test("200% metronome volume produces gain 2", () => {
    assert.equal(metronomeVolumeGain(200), 2);
});

test("metronome volume above 200% clamps to gain 2", () => {
    assert.equal(metronomeVolumeGain(500), 2);
});

test("negative metronome volume clamps to gain 0", () => {
    assert.equal(metronomeVolumeGain(-10), 0);
});

test("missing metronome volume defaults to gain 1", () => {
    assert.equal(metronomeVolumeGain(undefined), 1);
});

test("NaN metronome volume defaults to gain 1", () => {
    assert.equal(metronomeVolumeGain(Number.NaN), 1);
});

test("numeric strings are not interpreted as metronome percentages", () => {
    assert.equal(metronomeVolumeGain("50"), 1);
});

test("volume gain does not alter metronome beat calculations", () => {
    const timing = timingGrid({ bpm: 120, downbeatOffset: 0.25 });
    const beatIndexBefore = metronomeBeatIndexAtOrAfter(1.25, timing);
    const beatTimeBefore = metronomeBeatTime(beatIndexBefore, timing);
    for (const percentage of [0, 50, 100, 200]) {
        metronomeVolumeGain(percentage);
        assert.equal(metronomeBeatIndexAtOrAfter(1.25, timing), beatIndexBefore);
        assert.equal(metronomeBeatTime(beatIndexBefore, timing), beatTimeBefore);
    }
});
