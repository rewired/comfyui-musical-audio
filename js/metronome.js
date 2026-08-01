const BEAT_BOUNDARY_EPSILON = 1e-10;

/** Convert a global volume percentage to a clamped gain multiplier. */
export function metronomeVolumeGain(percentage) {
    if (typeof percentage !== "number" || !Number.isFinite(percentage)) return 1;
    return Math.min(Math.max(percentage, 0), 200) / 100;
}

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

function requireSafeInteger(name, value) {
    if (!Number.isSafeInteger(value)) {
        throw new TypeError(`${name} must be a safe integer`);
    }
    return value;
}

function requireTiming(timing) {
    if (!timing || typeof timing !== "object" || Array.isArray(timing)) {
        throw new TypeError("timing must be an object");
    }
    return {
        secondsPerBeat: requirePositive("timing.secondsPerBeat", timing.secondsPerBeat),
        downbeatOffset: requireFinite("timing.downbeatOffset", timing.downbeatOffset),
    };
}

/** Return the signed beat index at, or immediately following, a media time. */
export function metronomeBeatIndexAtOrAfter(seconds, timing) {
    requireFinite("seconds", seconds);
    const validated = requireTiming(timing);
    const relativeBeat = (
        (seconds - validated.downbeatOffset) / validated.secondsPerBeat
    );
    if (!Number.isFinite(relativeBeat)) {
        throw new RangeError("relative beat index must be finite");
    }
    const beatIndex = Math.ceil(relativeBeat - BEAT_BOUNDARY_EPSILON);
    if (!Number.isSafeInteger(beatIndex)) {
        throw new RangeError("beat index must be a safe integer");
    }
    return Object.is(beatIndex, -0) ? 0 : beatIndex;
}

/** Convert a signed beat index to its absolute media time. */
export function metronomeBeatTime(beatIndex, timing) {
    requireSafeInteger("beatIndex", beatIndex);
    const validated = requireTiming(timing);
    const seconds = (
        validated.downbeatOffset + beatIndex * validated.secondsPerBeat
    );
    if (!Number.isFinite(seconds)) {
        throw new RangeError("beat time must be finite");
    }
    return Object.is(seconds, -0) ? 0 : seconds;
}

/** Return whether a signed beat index is the first meter beat of a bar. */
export function isMetronomeDownbeat(beatIndex, beatsPerBar) {
    requireSafeInteger("beatIndex", beatIndex);
    requireSafeInteger("beatsPerBar", beatsPerBar);
    if (beatsPerBar < 1) {
        throw new RangeError("beatsPerBar must be at least one");
    }
    return ((beatIndex % beatsPerBar) + beatsPerBar) % beatsPerBar === 0;
}

export { BEAT_BOUNDARY_EPSILON };
