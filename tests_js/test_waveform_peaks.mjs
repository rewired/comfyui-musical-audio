import assert from "node:assert/strict";
import test from "node:test";

import {
    MAX_WAVEFORM_PEAK_LEVELS,
    decodeWaveformPeaks,
    selectWaveformPeakLevel,
} from "../js/waveform_peaks.js";


const MAGIC = new TextEncoder().encode("MAUPK001");
const HEADER_SIZE = 64;
const DIRECTORY_SIZE = 24;

function buildFixture({
    sampleRate = 8,
    sourceChannelCount = 2,
    sampleCount = 5,
    durationSeconds = sampleCount / sampleRate,
    levels = [
        { samplesPerPeak: 2, peaks: [-32768, 32767, -1200, 2500, -300, 800] },
        { samplesPerPeak: 4, peaks: [-32768, 32767, -300, 800] },
        { samplesPerPeak: 8, peaks: [-32768, 32767] },
    ],
} = {}) {
    const firstDataOffset = HEADER_SIZE + levels.length * DIRECTORY_SIZE;
    const byteLength = firstDataOffset
        + levels.reduce((total, level) => total + level.peaks.length * 2, 0);
    const buffer = new ArrayBuffer(byteLength);
    const view = new DataView(buffer);
    MAGIC.forEach((value, index) => view.setUint8(index, value));
    view.setUint16(8, 1, true);
    view.setUint16(10, HEADER_SIZE, true);
    view.setUint32(12, 3, true);
    view.setUint32(16, sampleRate, true);
    view.setUint16(20, sourceChannelCount, true);
    view.setUint16(22, levels.length, true);
    view.setBigUint64(24, BigInt(sampleCount), true);
    view.setFloat64(32, durationSeconds, true);
    view.setBigUint64(40, 64n, true);
    view.setBigUint64(48, BigInt(firstDataOffset), true);
    view.setBigUint64(56, 0n, true);

    let dataOffset = firstDataOffset;
    levels.forEach((level, levelIndex) => {
        const directoryOffset = HEADER_SIZE + levelIndex * DIRECTORY_SIZE;
        const peakCount = level.peaks.length / 2;
        view.setUint32(directoryOffset, level.samplesPerPeak, true);
        view.setUint32(directoryOffset + 4, peakCount, true);
        view.setBigUint64(directoryOffset + 8, BigInt(dataOffset), true);
        view.setBigUint64(directoryOffset + 16, BigInt(peakCount * 4), true);
        level.peaks.forEach((value, valueIndex) => {
            view.setInt16(dataOffset + valueIndex * 2, value, true);
        });
        dataOffset += peakCount * 4;
    });
    return buffer;
}

function clone(buffer) {
    return buffer.slice(0);
}

function expectInvalid(buffer) {
    assert.throws(() => decodeWaveformPeaks(buffer), /Invalid waveform peak payload/);
}


test("decodes a valid single-level payload", () => {
    const decoded = decodeWaveformPeaks(buildFixture({
        sampleCount: 3,
        durationSeconds: 3 / 8,
        levels: [{ samplesPerPeak: 2, peaks: [-5, 10, -3, 4] }],
    }));
    assert.equal(decoded.levels.length, 1);
    assert.equal(decoded.levels[0].peakCount, 2);
});

test("decodes valid multilevel metadata and signed min/max values", () => {
    const decoded = decodeWaveformPeaks(buildFixture());
    assert.deepEqual(
        {
            version: decoded.version,
            flags: decoded.flags,
            sampleRate: decoded.sampleRate,
            sourceChannelCount: decoded.sourceChannelCount,
            sampleCount: decoded.sampleCount,
            durationSeconds: decoded.durationSeconds,
        },
        {
            version: 1,
            flags: 3,
            sampleRate: 8,
            sourceChannelCount: 2,
            sampleCount: 5,
            durationSeconds: 5 / 8,
        },
    );
    assert.deepEqual(Array.from(decoded.levels[0].peaks), [
        -32768, 32767, -1200, 2500, -300, 800,
    ]);
    assert.deepEqual(decoded.levels.map((level) => level.peaksPerSecond), [4, 2, 1]);
    assert.equal("dataOffset" in decoded.levels[0], false);
});

test("converts safe uint64 sample counts without truncating to uint32", () => {
    const sampleCount = 0x1_0000_0001;
    const decoded = decodeWaveformPeaks(buildFixture({
        sampleCount,
        durationSeconds: sampleCount / 8,
        levels: [{ samplesPerPeak: 0xffffffff, peaks: [-1, 1, -2, 2] }],
    }));
    assert.equal(decoded.sampleCount, sampleCount);
});

test("accepts an ArrayBuffer view including an unaligned copying fallback", () => {
    const fixture = new Uint8Array(buildFixture());
    const backing = new Uint8Array(fixture.byteLength + 3);
    backing.set(fixture, 1);
    const decoded = decodeWaveformPeaks(
        new Uint8Array(backing.buffer, 1, fixture.byteLength),
    );
    assert.deepEqual(Array.from(decoded.levels[0].peaks.slice(0, 2)), [-32768, 32767]);
});

test("does not mutate the source buffer", () => {
    const buffer = buildFixture();
    const before = new Uint8Array(buffer).slice();
    decodeWaveformPeaks(buffer);
    assert.deepEqual(new Uint8Array(buffer), before);
});


test("selects exact finest, intermediate, and coarsest boundaries", () => {
    const pyramid = decodeWaveformPeaks(buildFixture());
    assert.equal(selectWaveformPeakLevel(pyramid, 4), pyramid.levels[0]);
    assert.equal(selectWaveformPeakLevel(pyramid, 2), pyramid.levels[1]);
    assert.equal(selectWaveformPeakLevel(pyramid, 1), pyramid.levels[2]);
});

test("zoom above finest returns finest and below coarsest returns coarsest", () => {
    const pyramid = decodeWaveformPeaks(buildFixture());
    assert.equal(selectWaveformPeakLevel(pyramid, 10), pyramid.levels[0]);
    assert.equal(selectWaveformPeakLevel(pyramid, 0.25), pyramid.levels[2]);
});

test("intermediate zoom chooses the coarsest qualifying level", () => {
    const pyramid = decodeWaveformPeaks(buildFixture());
    assert.equal(selectWaveformPeakLevel(pyramid, 1.5), pyramid.levels[1]);
});

test("invalid pixels-per-second values are rejected", () => {
    const pyramid = decodeWaveformPeaks(buildFixture());
    for (const value of [0, -1, Number.NaN, Infinity, -Infinity]) {
        assert.throws(() => selectWaveformPeakLevel(pyramid, value), RangeError);
    }
});


test("rejects wrong input types", () => {
    for (const value of [null, undefined, "buffer", 3, {}]) {
        assert.throws(() => decodeWaveformPeaks(value), /expected an ArrayBuffer/);
    }
});

test("rejects a truncated header", () => {
    expectInvalid(new ArrayBuffer(63));
});

test("rejects the wrong magic", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint8(0, 0);
    expectInvalid(buffer);
});

test("rejects an unsupported version", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint16(8, 2, true);
    expectInvalid(buffer);
});

test("rejects the wrong header size", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint16(10, 63, true);
    expectInvalid(buffer);
});

test("rejects unsupported flags", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint32(12, 7, true);
    expectInvalid(buffer);
});

test("rejects a zero sample rate", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint32(16, 0, true);
    expectInvalid(buffer);
});

test("rejects zero source channels", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint16(20, 0, true);
    expectInvalid(buffer);
});

test("rejects zero and excessive level counts", () => {
    for (const count of [0, MAX_WAVEFORM_PEAK_LEVELS + 1]) {
        const buffer = clone(buildFixture());
        new DataView(buffer).setUint16(22, count, true);
        expectInvalid(buffer);
    }
});

test("rejects an unsafe uint64 sample count", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setBigUint64(24, BigInt(Number.MAX_SAFE_INTEGER) + 1n, true);
    expectInvalid(buffer);
});

test("rejects NaN and infinite durations", () => {
    for (const duration of [Number.NaN, Infinity, -Infinity]) {
        const buffer = clone(buildFixture());
        new DataView(buffer).setFloat64(32, duration, true);
        expectInvalid(buffer);
    }
});

test("rejects a duration inconsistent beyond one sample", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setFloat64(32, 10, true);
    expectInvalid(buffer);
});

test("rejects the wrong directory offset", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setBigUint64(40, 63n, true);
    expectInvalid(buffer);
});

test("rejects the wrong first data offset", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setBigUint64(48, 65n, true);
    expectInvalid(buffer);
});

test("rejects a truncated directory", () => {
    const buffer = clone(buildFixture({
        sampleCount: 3,
        durationSeconds: 3 / 8,
        levels: [{ samplesPerPeak: 2, peaks: [-1, 1, -2, 2] }],
    }));
    const view = new DataView(buffer);
    view.setUint16(22, 2, true);
    view.setBigUint64(48, 112n, true);
    expectInvalid(buffer);
});

test("rejects a data offset outside the payload", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setBigUint64(72, BigInt(buffer.byteLength + 4), true);
    expectInvalid(buffer);
});

test("rejects overlapping data blocks", () => {
    const buffer = clone(buildFixture());
    const view = new DataView(buffer);
    const firstOffset = view.getBigUint64(72, true);
    view.setBigUint64(96, firstOffset + 4n, true);
    expectInvalid(buffer);
});

test("rejects gaps between data blocks", () => {
    const buffer = clone(buildFixture());
    const view = new DataView(buffer);
    view.setBigUint64(96, view.getBigUint64(96, true) + 4n, true);
    expectInvalid(buffer);
});

test("rejects a data length other than peak count times four", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setBigUint64(80, 8n, true);
    expectInvalid(buffer);
});

test("rejects zero samples per peak", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint32(64, 0, true);
    expectInvalid(buffer);
});

test("rejects zero peak count", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint32(68, 0, true);
    expectInvalid(buffer);
});

test("rejects a peak count inconsistent with sample count", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint32(68, 2, true);
    expectInvalid(buffer);
});

test("rejects non-doubling samples per peak", () => {
    const buffer = clone(buildFixture());
    new DataView(buffer).setUint32(88, 5, true);
    expectInvalid(buffer);
});

test("rejects missing data bytes", () => {
    const buffer = buildFixture();
    expectInvalid(buffer.slice(0, buffer.byteLength - 1));
});

test("rejects undeclared trailing bytes", () => {
    const fixture = new Uint8Array(buildFixture());
    const buffer = new Uint8Array(fixture.byteLength + 1);
    buffer.set(fixture);
    expectInvalid(buffer.buffer);
});
