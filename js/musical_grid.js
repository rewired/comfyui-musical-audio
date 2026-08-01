const TEMPO_UNIT_IN_QUARTERS = Object.freeze({
    Quarter: 1.0,
    Eighth: 0.5,
    "Dotted Quarter": 1.5,
});

function requireFinite(name, value) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
        throw new TypeError(`${name} must be a finite number`);
    }
    return value;
}

function requirePositive(name, value) {
    requireFinite(name, value);
    if (value <= 0) throw new RangeError(`${name} must be greater than zero`);
    return value;
}

function requireInteger(name, value) {
    if (!Number.isSafeInteger(value)) {
        throw new TypeError(`${name} must be a safe integer`);
    }
    return value;
}

function requirePositiveInteger(name, value) {
    requireInteger(name, value);
    if (value < 1) throw new RangeError(`${name} must be at least one`);
    return value;
}

function requireNonnegativeInteger(name, value) {
    requireInteger(name, value);
    if (value < 0) throw new RangeError(`${name} must be nonnegative`);
    return value;
}

/** Round to nearest, resolving exact half ties away from zero. */
export function roundHalfAwayFromZero(value) {
    requireFinite("value", value);
    const rounded = value >= 0 ? Math.floor(value + 0.5) : Math.ceil(value - 0.5);
    return Object.is(rounded, -0) ? 0 : rounded;
}

export function tempoUnitInQuarters(tempoUnit) {
    if (!Object.prototype.hasOwnProperty.call(TEMPO_UNIT_IN_QUARTERS, tempoUnit)) {
        throw new RangeError(`unsupported tempo unit: ${tempoUnit}`);
    }
    return TEMPO_UNIT_IN_QUARTERS[tempoUnit];
}

export function secondsPerQuarter(bpm, tempoUnit = "Quarter") {
    return (60.0 / requirePositive("bpm", bpm)) / tempoUnitInQuarters(tempoUnit);
}

export function secondsPerBeat(bpm, tempoUnit = "Quarter", beatUnit = 4) {
    return secondsPerQuarter(bpm, tempoUnit) * (4.0 / requirePositiveInteger("beatUnit", beatUnit));
}

export function secondsPerBar(bpm, tempoUnit = "Quarter", beatUnit = 4, beatsPerBar = 4) {
    return secondsPerBeat(bpm, tempoUnit, beatUnit) * requirePositiveInteger("beatsPerBar", beatsPerBar);
}

function gridShape(beatsPerBar, subdivisionsPerBeat) {
    return {
        beatsPerBar: requirePositiveInteger("beatsPerBar", beatsPerBar),
        subdivisionsPerBeat: requirePositiveInteger("subdivisionsPerBeat", subdivisionsPerBeat),
    };
}

/** Convert 1-based Bar/Beat and 0-based Subdivision fields to a signed grid index. */
export function musicalPositionToSubdivisionIndex(
    { bar = 1, beat = 1, subdivision = 0 },
    { beatsPerBar = 4, subdivisionsPerBeat = 4 } = {},
) {
    const shape = gridShape(beatsPerBar, subdivisionsPerBeat);
    requireInteger("bar", bar);
    requireInteger("beat", beat);
    requireInteger("subdivision", subdivision);
    return (
        ((bar - 1) * shape.beatsPerBar + (beat - 1)) * shape.subdivisionsPerBeat
        + subdivision
    );
}

/** Convert a signed grid index to canonical Bar/Beat/Subdivision fields. */
export function subdivisionIndexToMusicalPosition(
    subdivisionIndex,
    { beatsPerBar = 4, subdivisionsPerBeat = 4 } = {},
) {
    requireInteger("subdivisionIndex", subdivisionIndex);
    const shape = gridShape(beatsPerBar, subdivisionsPerBeat);
    const subdivisionsPerBar = shape.beatsPerBar * shape.subdivisionsPerBeat;
    const barIndex = Math.floor(subdivisionIndex / subdivisionsPerBar);
    const withinBar = subdivisionIndex - barIndex * subdivisionsPerBar;
    const beatIndex = Math.floor(withinBar / shape.subdivisionsPerBeat);
    return {
        bar: barIndex + 1,
        beat: beatIndex + 1,
        subdivision: withinBar - beatIndex * shape.subdivisionsPerBeat,
    };
}

/** Convert possibly overflowing duration fields into a signed subdivision count. */
export function durationFieldsToSubdivisionCount(
    { bars = 0, beats = 0, subdivisions = 0 },
    { beatsPerBar = 4, subdivisionsPerBeat = 4 } = {},
) {
    requireInteger("bars", bars);
    requireInteger("beats", beats);
    requireInteger("subdivisions", subdivisions);
    const shape = gridShape(beatsPerBar, subdivisionsPerBeat);
    return (
        (bars * shape.beatsPerBar + beats) * shape.subdivisionsPerBeat
        + subdivisions
    );
}

/** Convert a nonnegative subdivision count to canonical duration fields. */
export function subdivisionCountToDurationFields(
    subdivisionCount,
    { beatsPerBar = 4, subdivisionsPerBeat = 4 } = {},
) {
    requireInteger("subdivisionCount", subdivisionCount);
    if (subdivisionCount < 0) {
        throw new RangeError("subdivisionCount must be nonnegative");
    }
    const shape = gridShape(beatsPerBar, subdivisionsPerBeat);
    const subdivisionsPerBar = shape.beatsPerBar * shape.subdivisionsPerBeat;
    const bars = Math.floor(subdivisionCount / subdivisionsPerBar);
    const withinBar = subdivisionCount - bars * subdivisionsPerBar;
    const beats = Math.floor(withinBar / shape.subdivisionsPerBeat);
    return {
        bars,
        beats,
        subdivisions: withinBar - beats * shape.subdivisionsPerBeat,
    };
}

export function timingGrid({
    bpm = 120,
    tempoUnit = "Quarter",
    beatsPerBar = 4,
    beatUnit = 4,
    downbeatOffset = 0,
    fps = 24,
    subdivisionsPerBeat = 4,
} = {}) {
    requireFinite("downbeatOffset", downbeatOffset);
    requirePositive("fps", fps);
    const shape = gridShape(beatsPerBar, subdivisionsPerBeat);
    const perQuarter = secondsPerQuarter(bpm, tempoUnit);
    const perBeat = perQuarter * (4.0 / requirePositiveInteger("beatUnit", beatUnit));
    const perBar = perBeat * shape.beatsPerBar;
    const perSubdivision = perBeat / shape.subdivisionsPerBeat;
    const framesPerBeat = fps * perBeat;
    const framesPerBar = fps * perBar;
    for (const [name, value] of [
        ["secondsPerQuarter", perQuarter],
        ["secondsPerBeat", perBeat],
        ["secondsPerBar", perBar],
        ["secondsPerSubdivision", perSubdivision],
        ["framesPerBeat", framesPerBeat],
        ["framesPerBar", framesPerBar],
    ]) {
        if (!Number.isFinite(value) || value <= 0) {
            throw new RangeError(`${name} must be finite and greater than zero`);
        }
    }
    return Object.freeze({
        bpm,
        tempoUnit,
        beatsPerBar: shape.beatsPerBar,
        beatUnit,
        downbeatOffset,
        fps,
        subdivisionsPerBeat: shape.subdivisionsPerBeat,
        secondsPerTempoPulse: 60.0 / bpm,
        secondsPerQuarter: perQuarter,
        secondsPerBeat: perBeat,
        secondsPerBar: perBar,
        secondsPerSubdivision: perSubdivision,
        framesPerBeat,
        framesPerBar,
    });
}

function asGrid(config) {
    return config && Number.isFinite(config.secondsPerSubdivision)
        ? config
        : timingGrid(config);
}

export function subdivisionIndexToSeconds(subdivisionIndex, config = {}) {
    requireInteger("subdivisionIndex", subdivisionIndex);
    const grid = asGrid(config);
    return grid.downbeatOffset + subdivisionIndex * grid.secondsPerSubdivision;
}

export function musicalPositionToSeconds(position, config = {}) {
    const grid = asGrid(config);
    const index = musicalPositionToSubdivisionIndex(position, grid);
    return subdivisionIndexToSeconds(index, grid);
}

/** Return a signed index so pre-downbeat audio remains representable to callers. */
export function secondsToNearestSubdivision(seconds, config = {}) {
    requireFinite("seconds", seconds);
    const grid = asGrid(config);
    return roundHalfAwayFromZero(
        (seconds - grid.downbeatOffset) / grid.secondsPerSubdivision,
    );
}

/** Quantize a Seconds range to the finest subdivision in the supplied grid. */
export function secondsRangeToMusicalSelection(
    { startSeconds = 0, endSeconds = 0 } = {},
    config = {},
) {
    requireFinite("startSeconds", startSeconds);
    requireFinite("endSeconds", endSeconds);
    const grid = asGrid(config);
    let startIndex = Math.max(0, secondsToNearestSubdivision(startSeconds, grid));
    let endIndex = Math.max(0, secondsToNearestSubdivision(endSeconds, grid));
    requireNonnegativeInteger("startIndex", startIndex);
    requireNonnegativeInteger("endIndex", endIndex);
    endIndex = Math.max(startIndex, endIndex);

    const expandedToOneSubdivision = endSeconds > startSeconds && endIndex === startIndex;
    if (expandedToOneSubdivision) {
        endIndex += 1;
        requireNonnegativeInteger("endIndex", endIndex);
    }

    return Object.freeze({
        startIndex,
        endIndex,
        subdivisionCount: endIndex - startIndex,
        quantizedStartSeconds: subdivisionIndexToSeconds(startIndex, grid),
        quantizedEndSeconds: subdivisionIndexToSeconds(endIndex, grid),
        expandedToOneSubdivision,
    });
}

/** Convert an exact Musical grid start and length to seconds. */
export function musicalSelectionToSecondsRange(
    { startIndex = 0, subdivisionCount = 0 } = {},
    config = {},
) {
    requireInteger("startIndex", startIndex);
    requireNonnegativeInteger("subdivisionCount", subdivisionCount);
    const endIndex = startIndex + subdivisionCount;
    requireInteger("endIndex", endIndex);
    const grid = asGrid(config);
    const startSeconds = subdivisionIndexToSeconds(startIndex, grid);
    const endSeconds = subdivisionIndexToSeconds(endIndex, grid);
    const durationSeconds = Math.max(0, endSeconds - startSeconds);
    requireFinite("startSeconds", startSeconds);
    requireFinite("endSeconds", endSeconds);
    requireFinite("durationSeconds", durationSeconds);
    return Object.freeze({ startSeconds, endSeconds, durationSeconds });
}

export function snapToVideoFrame(seconds, fps) {
    requireFinite("seconds", seconds);
    requirePositive("fps", fps);
    return roundHalfAwayFromZero(seconds * fps) / fps;
}

function snapRelative(seconds, origin, interval) {
    requireFinite("seconds", seconds);
    requireFinite("origin", origin);
    requirePositive("interval", interval);
    return origin + roundHalfAwayFromZero((seconds - origin) / interval) * interval;
}

export function snapToBar(seconds, config = {}) {
    const grid = asGrid(config);
    return snapRelative(seconds, grid.downbeatOffset, grid.secondsPerBar);
}

export function snapToBeat(seconds, config = {}) {
    const grid = asGrid(config);
    return snapRelative(seconds, grid.downbeatOffset, grid.secondsPerBeat);
}

export function snapToSubdivision(seconds, config = {}) {
    const grid = asGrid(config);
    return snapRelative(seconds, grid.downbeatOffset, grid.secondsPerSubdivision);
}

/** Frame snap followed by the nearest representable musical subdivision. */
export function frameToNearestSubdivision(seconds, config = {}) {
    const grid = asGrid(config);
    const frameSeconds = snapToVideoFrame(seconds, grid.fps);
    const subdivisionIndex = secondsToNearestSubdivision(frameSeconds, grid);
    return Object.freeze({
        frameSeconds,
        subdivisionIndex,
        seconds: subdivisionIndexToSeconds(subdivisionIndex, grid),
    });
}

export { TEMPO_UNIT_IN_QUARTERS };
