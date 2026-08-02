const MAGIC_BYTES = Object.freeze([77, 65, 85, 80, 75, 48, 48, 49]);

export const WAVEFORM_PEAK_FORMAT_VERSION = 1;
export const WAVEFORM_PEAK_FLAGS = 3;
export const WAVEFORM_PEAK_HEADER_SIZE = 64;
export const WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE = 24;
export const MAX_WAVEFORM_PEAK_LEVELS = 64;

const MAX_SAFE_UINT64 = BigInt(Number.MAX_SAFE_INTEGER);
const endianProbe = new Uint16Array([0x0102]);
const HOST_IS_LITTLE_ENDIAN = new Uint8Array(endianProbe.buffer)[0] === 0x02;

function fail(message) {
    throw new TypeError(`Invalid waveform peak payload: ${message}`);
}

function bufferWindow(input) {
    if (input instanceof ArrayBuffer) {
        return { buffer: input, byteOffset: 0, byteLength: input.byteLength };
    }
    if (ArrayBuffer.isView(input) && input.buffer instanceof ArrayBuffer) {
        return {
            buffer: input.buffer,
            byteOffset: input.byteOffset,
            byteLength: input.byteLength,
        };
    }
    fail("expected an ArrayBuffer or ArrayBuffer view");
}

function readSafeUint64(view, offset, label) {
    const value = view.getBigUint64(offset, true);
    if (value > MAX_SAFE_UINT64) {
        fail(`${label} exceeds Number.MAX_SAFE_INTEGER`);
    }
    return Number(value);
}

function readPeaks(window, view, dataOffset, peakCount) {
    const valueCount = peakCount * 2;
    const absoluteOffset = window.byteOffset + dataOffset;
    if (HOST_IS_LITTLE_ENDIAN && absoluteOffset % Int16Array.BYTES_PER_ELEMENT === 0) {
        return new Int16Array(window.buffer, absoluteOffset, valueCount);
    }
    const peaks = new Int16Array(valueCount);
    for (let index = 0; index < valueCount; index += 1) {
        peaks[index] = view.getInt16(dataOffset + index * 2, true);
    }
    return peaks;
}

/**
 * Decode and rigorously validate the version-1 little-endian peak format.
 * The input bytes are never modified.
 */
export function decodeWaveformPeaks(input) {
    const window = bufferWindow(input);
    if (window.byteLength < WAVEFORM_PEAK_HEADER_SIZE) {
        fail("payload is shorter than the fixed header");
    }
    const view = new DataView(window.buffer, window.byteOffset, window.byteLength);

    for (let index = 0; index < MAGIC_BYTES.length; index += 1) {
        if (view.getUint8(index) !== MAGIC_BYTES[index]) {
            fail("magic does not match MAUPK001");
        }
    }

    const version = view.getUint16(8, true);
    const headerSize = view.getUint16(10, true);
    const flags = view.getUint32(12, true);
    const sampleRate = view.getUint32(16, true);
    const sourceChannelCount = view.getUint16(20, true);
    const levelCount = view.getUint16(22, true);
    const sampleCount = readSafeUint64(view, 24, "sample count");
    const durationSeconds = view.getFloat64(32, true);
    const directoryOffset = readSafeUint64(view, 40, "directory offset");
    const firstDataOffset = readSafeUint64(view, 48, "first data offset");
    const reserved = readSafeUint64(view, 56, "reserved value");

    if (version !== WAVEFORM_PEAK_FORMAT_VERSION) {
        fail("unsupported format version");
    }
    if (headerSize !== WAVEFORM_PEAK_HEADER_SIZE) {
        fail("fixed header size is not 64 bytes");
    }
    if (flags !== WAVEFORM_PEAK_FLAGS) {
        fail("unsupported flags");
    }
    if (sampleRate === 0) {
        fail("sample rate is zero");
    }
    if (sourceChannelCount === 0) {
        fail("source channel count is zero");
    }
    if (levelCount === 0 || levelCount > MAX_WAVEFORM_PEAK_LEVELS) {
        fail("level count is zero or unreasonable");
    }
    if (sampleCount === 0) {
        fail("sample count is zero");
    }
    if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
        fail("duration is not finite and positive");
    }
    const expectedDuration = sampleCount / sampleRate;
    const durationTolerance = 1 / sampleRate + Number.EPSILON * expectedDuration;
    if (Math.abs(durationSeconds - expectedDuration) > durationTolerance) {
        fail("duration is inconsistent with sample count and sample rate");
    }
    if (directoryOffset !== WAVEFORM_PEAK_HEADER_SIZE) {
        fail("directory offset is not 64");
    }
    const expectedFirstDataOffset =
        WAVEFORM_PEAK_HEADER_SIZE
        + levelCount * WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE;
    if (firstDataOffset !== expectedFirstDataOffset) {
        fail("first data offset is inconsistent with the level directory");
    }
    if (expectedFirstDataOffset > window.byteLength) {
        fail("level directory extends outside the payload");
    }
    if (reserved !== 0) {
        fail("reserved header field is not zero");
    }

    const directories = [];
    let previousSamplesPerPeak = null;
    let expectedDataOffset = firstDataOffset;
    for (let levelIndex = 0; levelIndex < levelCount; levelIndex += 1) {
        const entryOffset =
            directoryOffset + levelIndex * WAVEFORM_PEAK_DIRECTORY_ENTRY_SIZE;
        const samplesPerPeak = view.getUint32(entryOffset, true);
        const peakCount = view.getUint32(entryOffset + 4, true);
        const dataOffset = readSafeUint64(view, entryOffset + 8, "level data offset");
        const dataByteLength = readSafeUint64(
            view,
            entryOffset + 16,
            "level data byte length",
        );

        if (samplesPerPeak === 0) {
            fail("a level has zero samples per peak");
        }
        if (peakCount === 0) {
            fail("a level has zero peaks");
        }
        if (
            previousSamplesPerPeak !== null
            && samplesPerPeak !== previousSamplesPerPeak * 2
        ) {
            fail("samples per peak does not double between levels");
        }
        previousSamplesPerPeak = samplesPerPeak;
        if (peakCount !== Math.ceil(sampleCount / samplesPerPeak)) {
            fail("peak count is inconsistent with decoded sample count");
        }
        const expectedByteLength = peakCount * 4;
        if (dataByteLength !== expectedByteLength) {
            fail("level data length is not peak count times four");
        }
        if (dataOffset !== expectedDataOffset) {
            fail("level data blocks overlap or are not contiguous");
        }
        const dataEnd = dataOffset + dataByteLength;
        if (!Number.isSafeInteger(dataEnd) || dataEnd > window.byteLength) {
            fail("level data extends outside the payload");
        }
        directories.push({ samplesPerPeak, peakCount, dataOffset });
        expectedDataOffset = dataEnd;
    }
    if (expectedDataOffset !== window.byteLength) {
        fail("payload has missing or undeclared trailing data bytes");
    }

    const levels = directories.map((entry) => ({
        samplesPerPeak: entry.samplesPerPeak,
        peakCount: entry.peakCount,
        peaksPerSecond: sampleRate / entry.samplesPerPeak,
        peaks: readPeaks(window, view, entry.dataOffset, entry.peakCount),
    }));

    return {
        version,
        flags,
        sampleRate,
        sourceChannelCount,
        sampleCount,
        durationSeconds,
        levels,
    };
}

/**
 * Select the coarsest level that still supplies at least one peak per pixel.
 */
export function selectWaveformPeakLevel(pyramid, pixelsPerSecond) {
    if (!Number.isFinite(pixelsPerSecond) || pixelsPerSecond <= 0) {
        throw new RangeError("pixelsPerSecond must be finite and greater than zero");
    }
    if (!pyramid || !Array.isArray(pyramid.levels) || pyramid.levels.length === 0) {
        throw new TypeError("pyramid must contain at least one peak level");
    }
    for (let index = pyramid.levels.length - 1; index >= 0; index -= 1) {
        if (pyramid.levels[index].peaksPerSecond >= pixelsPerSecond) {
            return pyramid.levels[index];
        }
    }
    return pyramid.levels[0];
}
