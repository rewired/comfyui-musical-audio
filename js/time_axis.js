import { createScoreResolver } from "./score.js";
import {
    durationFieldsToSubdivisionCount,
    musicalPositionToSubdivisionIndex,
    musicalSelectionToSecondsRange,
    roundHalfAwayFromZero,
    secondsRangeToMusicalSelection,
    snapToBar as constantSnapToBar,
    snapToBeat as constantSnapToBeat,
    snapToSubdivision as constantSnapToSubdivision,
    snapToVideoFrame as gridSnapToVideoFrame,
    subdivisionIndexToMusicalPosition,
    timingGrid,
} from "./musical_grid.js";

export const CONSTANT_SYNTHETIC_TICKS_PER_QUARTER = 960;

const POSITION_KEYS = Object.freeze(["bar", "beat", "subdivision"]);
const RANGE_KEYS = Object.freeze(["startAudioSeconds", "endAudioSeconds"]);
const SELECTION_KEYS = Object.freeze(["startAudioSeconds", "endAudioSeconds", "nonEmptyIntent"]);
const DURATION_KEYS = Object.freeze(["bars", "beats", "subdivisions"]);
const CANONICAL_KEYS = Object.freeze([
    "start", "endExclusive", "startTick", "endTickExclusive", "expandedToOneSubdivision",
]);

class TimeAxisError extends Error {
    constructor(code, message) {
        super(message);
        this.name = "TimeAxisError";
        this.code = code;
    }
}

function axisError(code, message) { throw new TimeAxisError(code, message); }

function exactObject(value, keys, name) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) axisError("score_selection_range_invalid", `${name} must be an object`);
    const actual = Object.keys(value);
    if (actual.length !== keys.length || actual.some((key, index) => key !== keys[index])) axisError("score_selection_range_invalid", `${name} has an invalid key set or order`);
}

function finite(value, name) {
    if (typeof value !== "number" || !Number.isFinite(value)) axisError("score_selection_range_invalid", `${name} must be finite`);
    return value;
}

function safe(value, name, minimum = null) {
    if (!Number.isSafeInteger(value)) axisError("score_selection_range_invalid", `${name} must be a safe integer`);
    if (minimum !== null && value < minimum) axisError("score_selection_range_invalid", `${name} must be at least ${minimum}`);
    return value;
}

function safeTick(value, name) {
    if (!Number.isSafeInteger(value)) axisError("score_tick_out_of_safe_range", `${name} is outside the safe integer range`);
    return value;
}

function immutable(value) {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
        for (const child of Object.values(value)) immutable(child);
        Object.freeze(value);
    }
    return value;
}

function position(value) {
    exactObject(value, POSITION_KEYS, "position");
    return Object.freeze({
        bar: safe(value.bar, "bar", 1),
        beat: safe(value.beat, "beat", 1),
        subdivision: safe(value.subdivision, "subdivision", 0),
    });
}

function duration(value) {
    exactObject(value, DURATION_KEYS, "duration");
    return Object.freeze({
        bars: safe(value.bars, "bars", 0),
        beats: safe(value.beats, "beats", 0),
        subdivisions: safe(value.subdivisions, "subdivisions", 0),
    });
}

function snapResult(axis, audioSeconds, tick, subdivisionsPerBeat) {
    return immutable({
        audioSeconds,
        tick,
        position: tick === null ? null : axis.tickToPosition(tick, subdivisionsPerBeat),
    });
}

function commonVideoSnap(audioSeconds, fps) {
    finite(audioSeconds, "audioSeconds");
    finite(fps, "fps");
    if (fps <= 0) axisError("score_selection_range_invalid", "fps must be positive");
    return Object.freeze({ audioSeconds: gridSnapToVideoFrame(audioSeconds, fps), tick: null, position: null });
}

function constantTick(positionValue, subdivisionsPerBeat, meter) {
    const index = musicalPositionToSubdivisionIndex(positionValue, {
        beatsPerBar: meter.beatsPerBar,
        subdivisionsPerBeat,
    });
    const numerator = index * 4 * CONSTANT_SYNTHETIC_TICKS_PER_QUARTER;
    const denominator = meter.beatUnit * subdivisionsPerBeat;
    return safeTick(roundHalfAwayFromZero(numerator / denominator), "synthetic tick");
}

export function createConstantTimeAxis(configuration) {
    exactObject(configuration, ["bpm", "tempoUnit", "beatsPerBar", "beatUnit", "downbeatOffset"], "configuration");
    const base = timingGrid({ ...configuration, subdivisionsPerBeat: 1, fps: 1 });
    const meter = Object.freeze({ numerator: base.beatsPerBar, denominator: base.beatUnit });

    function grid(subdivisionsPerBeat) {
        safe(subdivisionsPerBeat, "subdivisionsPerBeat", 1);
        return timingGrid({ ...configuration, subdivisionsPerBeat, fps: 1 });
    }

    function constantPosition(value) {
        exactObject(value, POSITION_KEYS, "position");
        const result = Object.freeze({
            bar: safe(value.bar, "bar"),
            beat: safe(value.beat, "beat", 1),
            subdivision: safe(value.subdivision, "subdivision", 0),
        });
        if (result.beat > base.beatsPerBar) axisError("score_selection_range_invalid", "beat is outside the Constant meter");
        return result;
    }

    function positionToTick(value, subdivisionsPerBeat) {
        const canonical = constantPosition(value);
        if (canonical.beat > base.beatsPerBar || canonical.subdivision >= subdivisionsPerBeat) axisError("score_selection_range_invalid", "position is outside the Constant grid");
        return constantTick(canonical, subdivisionsPerBeat, base);
    }

    function tickToPosition(tick, subdivisionsPerBeat) {
        safeTick(tick, "tick");
        const ticksPerSubdivision = 4 * CONSTANT_SYNTHETIC_TICKS_PER_QUARTER / (base.beatUnit * safe(subdivisionsPerBeat, "subdivisionsPerBeat", 1));
        const index = roundHalfAwayFromZero(tick / ticksPerSubdivision);
        return immutable(subdivisionIndexToMusicalPosition(index, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat }));
    }

    function tickToScoreSeconds(tick) {
        safeTick(tick, "tick");
        return tick * base.secondsPerQuarter / CONSTANT_SYNTHETIC_TICKS_PER_QUARTER;
    }

    function scoreSecondsToTick(seconds) {
        return safeTick(roundHalfAwayFromZero(finite(seconds, "seconds") * CONSTANT_SYNTHETIC_TICKS_PER_QUARTER / base.secondsPerQuarter), "tick");
    }

    function makeSnap(audioSeconds, subdivisionsPerBeat, snapper) {
        const snapped = snapper(finite(audioSeconds, "audioSeconds"), grid(subdivisionsPerBeat));
        const index = roundHalfAwayFromZero((snapped - base.downbeatOffset) / grid(subdivisionsPerBeat).secondsPerSubdivision);
        const tick = constantTick(subdivisionIndexToMusicalPosition(index, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat }), subdivisionsPerBeat, base);
        return snapResult(axis, snapped, tick, subdivisionsPerBeat);
    }

    function enumerateVisibleBoundaries(visibleRange, subdivisionsPerBeat) {
        exactObject(visibleRange, RANGE_KEYS, "visibleRange");
        const start = finite(visibleRange.startAudioSeconds, "startAudioSeconds");
        const end = finite(visibleRange.endAudioSeconds, "endAudioSeconds");
        if (end <= start) axisError("score_selection_range_invalid", "visible range must be increasing");
        const currentGrid = grid(subdivisionsPerBeat);
        const first = Math.ceil((start - currentGrid.downbeatOffset) / currentGrid.secondsPerSubdivision);
        const last = Math.ceil((end - currentGrid.downbeatOffset) / currentGrid.secondsPerSubdivision);
        const boundaries = [];
        for (let index = first; index < last; index += 1) {
            const audioSeconds = currentGrid.downbeatOffset + index * currentGrid.secondsPerSubdivision;
            if (audioSeconds < start || audioSeconds >= end) continue;
            const canonical = immutable(subdivisionIndexToMusicalPosition(index, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat }));
            const withinBar = ((index % (base.beatsPerBar * subdivisionsPerBeat)) + base.beatsPerBar * subdivisionsPerBeat) % (base.beatsPerBar * subdivisionsPerBeat);
            const kind = withinBar === 0 ? "bar" : withinBar % subdivisionsPerBeat === 0 ? "beat" : "subdivision";
            boundaries.push(immutable({ kind, tick: constantTick(canonical, subdivisionsPerBeat, base), audioSeconds, position: canonical }));
        }
        return Object.freeze(boundaries);
    }

    function audioRangeToCanonicalSelection(selectionRange, subdivisionsPerBeat) {
        exactObject(selectionRange, SELECTION_KEYS, "selectionRange");
        const start = finite(selectionRange.startAudioSeconds, "startAudioSeconds");
        const end = finite(selectionRange.endAudioSeconds, "endAudioSeconds");
        if (typeof selectionRange.nonEmptyIntent !== "boolean" || end < start) axisError("score_selection_range_invalid", "selection range is invalid");
        const selected = secondsRangeToMusicalSelection({ startSeconds: start, endSeconds: end }, grid(subdivisionsPerBeat));
        let endIndex = selected.endIndex;
        let expanded = selected.expandedToOneSubdivision;
        if (!selectionRange.nonEmptyIntent && selected.startIndex === endIndex) expanded = false;
        if (selectionRange.nonEmptyIntent && selected.startIndex === endIndex) { endIndex += 1; expanded = true; }
        const startPosition = immutable(subdivisionIndexToMusicalPosition(selected.startIndex, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat }));
        const endPosition = immutable(subdivisionIndexToMusicalPosition(endIndex, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat }));
        return immutable({ start: startPosition, endExclusive: endPosition, startTick: positionToTick(startPosition, subdivisionsPerBeat), endTickExclusive: positionToTick(endPosition, subdivisionsPerBeat), expandedToOneSubdivision: expanded });
    }

    function canonicalSelectionToAudioRange(value, subdivisionsPerBeat) {
        exactObject(value, CANONICAL_KEYS, "canonicalSelection");
        const start = constantPosition(value.start);
        const end = constantPosition(value.endExclusive);
        safeTick(value.startTick, "startTick");
        safeTick(value.endTickExclusive, "endTickExclusive");
        if (typeof value.expandedToOneSubdivision !== "boolean") axisError("score_selection_range_invalid", "expandedToOneSubdivision must be boolean");
        const startIndex = musicalPositionToSubdivisionIndex(start, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat });
        const endIndex = musicalPositionToSubdivisionIndex(end, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat });
        if (endIndex < startIndex) axisError("score_selection_range_invalid", "selection end precedes start");
        const range = musicalSelectionToSecondsRange({ startIndex, subdivisionCount: endIndex - startIndex }, grid(subdivisionsPerBeat));
        const startTick = positionToTick(start, subdivisionsPerBeat);
        const endTickExclusive = positionToTick(end, subdivisionsPerBeat);
        if (value.startTick !== startTick || value.endTickExclusive !== endTickExclusive) axisError("score_selection_range_invalid", "canonical selection ticks are inconsistent");
        return immutable({ startAudioSeconds: range.startSeconds, endAudioSeconds: range.endSeconds, durationSeconds: range.durationSeconds, startTick, endTickExclusive });
    }

    function durationToCanonicalEnd(startValue, durationValue, subdivisionsPerBeat) {
        const start = constantPosition(startValue);
        const fields = duration(durationValue);
        const startIndex = musicalPositionToSubdivisionIndex(start, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat });
        const count = durationFieldsToSubdivisionCount(fields, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat });
        if (count < 0) axisError("score_selection_range_invalid", "duration must be nonnegative");
        const end = immutable(subdivisionIndexToMusicalPosition(startIndex + count, { beatsPerBar: base.beatsPerBar, subdivisionsPerBeat }));
        return immutable({ endExclusive: end, startTick: positionToTick(start, subdivisionsPerBeat), endTickExclusive: positionToTick(end, subdivisionsPerBeat), meterStable: true });
    }

    const axis = Object.freeze({
        kind: "constant",
        positionToTick,
        tickToPosition,
        tickToScoreSeconds,
        scoreSecondsToTick,
        tickToAudioSeconds(tick) { return tickToScoreSeconds(tick) + base.downbeatOffset; },
        audioSecondsToTick(seconds) { return scoreSecondsToTick(finite(seconds, "audioSeconds") - base.downbeatOffset); },
        barToTick(bar) { return positionToTick(Object.freeze({ bar: safe(bar, "bar", 1), beat: 1, subdivision: 0 }), 1); },
        meterAtBar(bar) { safe(bar, "bar", 1); return meter; },
        enumerateVisibleBoundaries,
        sectionAtTick(tick) { finite(tick, "tick"); return null; },
        durationToCanonicalEnd,
        snapToBar(audioSeconds) { return makeSnap(audioSeconds, 1, constantSnapToBar); },
        snapToBeat(audioSeconds) { return makeSnap(audioSeconds, 1, constantSnapToBeat); },
        snapToSubdivision(audioSeconds, subdivisionsPerBeat) { return makeSnap(audioSeconds, subdivisionsPerBeat, constantSnapToSubdivision); },
        snapToVideoFrame: commonVideoSnap,
        audioRangeToCanonicalSelection,
        canonicalSelectionToAudioRange,
    });
    return axis;
}

function scoreCandidatePositions(resolver, rawTick, subdivisionsPerBeat, kind) {
    if (rawTick < 0) return [Object.freeze({ bar: 1, beat: 1, subdivision: 0 })];
    const info = resolver.containingBarTicks(rawTick);
    const positions = [];
    for (let bar = Math.max(1, info.bar - 1); bar <= info.bar + 2; bar += 1) {
        const meter = resolver.meterAtBar(bar);
        if (kind === "bar") positions.push(Object.freeze({ bar, beat: 1, subdivision: 0 }));
        else {
            for (let beat = 1; beat <= meter.numerator; beat += 1) {
                if (kind === "beat") positions.push(Object.freeze({ bar, beat, subdivision: 0 }));
                else for (let subdivision = 0; subdivision < subdivisionsPerBeat; subdivision += 1) positions.push(Object.freeze({ bar, beat, subdivision }));
            }
        }
    }
    return positions;
}

export function createScoreTimeAxis(normalizedReadyPayload) {
    const resolver = createScoreResolver(normalizedReadyPayload);

    function nearest(audioSeconds, subdivisionsPerBeat, kind) {
        const audio = finite(audioSeconds, "audioSeconds");
        const rawTick = resolver.audioSecondsToTick(audio);
        const candidates = scoreCandidatePositions(resolver, rawTick, subdivisionsPerBeat, kind)
            .map((candidate) => {
                try {
                    const tick = resolver.positionToTick(candidate, subdivisionsPerBeat);
                    return { candidate, tick, audioSeconds: resolver.tickToAudioSeconds(tick) };
                } catch { return null; }
            })
            .filter(Boolean);
        candidates.sort((a, b) => Math.abs(a.audioSeconds - audio) - Math.abs(b.audioSeconds - audio) || b.audioSeconds - a.audioSeconds);
        const winner = candidates[0];
        return immutable({ audioSeconds: winner.audioSeconds, tick: winner.tick, position: winner.candidate });
    }

    function enumerateVisibleBoundaries(visibleRange, subdivisionsPerBeat) {
        exactObject(visibleRange, RANGE_KEYS, "visibleRange");
        const start = finite(visibleRange.startAudioSeconds, "startAudioSeconds");
        const end = finite(visibleRange.endAudioSeconds, "endAudioSeconds");
        if (end <= start) axisError("score_selection_range_invalid", "visible range must be increasing");
        safe(subdivisionsPerBeat, "subdivisionsPerBeat", 1);
        const startTick = resolver.audioSecondsToTick(start);
        const endTick = resolver.audioSecondsToTick(end);
        const startingBar = startTick < 0 ? 1 : Math.max(1, resolver.containingBarTicks(startTick).bar - 1);
        const byTick = new Map();
        const strength = { subdivision: 0, beat: 1, bar: 2 };
        for (let bar = startingBar; ; bar += 1) {
            const barTick = resolver.barToTick(bar);
            const barAudio = resolver.tickToAudioSeconds(barTick);
            if (barAudio >= end && barTick > endTick) break;
            const meter = resolver.meterAtBar(bar);
            for (let beat = 1; beat <= meter.numerator; beat += 1) {
                for (let subdivision = 0; subdivision < subdivisionsPerBeat; subdivision += 1) {
                    const candidate = Object.freeze({ bar, beat, subdivision });
                    let tick;
                    try { tick = resolver.positionToTick(candidate, subdivisionsPerBeat); } catch { continue; }
                    const audioSeconds = resolver.tickToAudioSeconds(tick);
                    if (audioSeconds < start || audioSeconds >= end) continue;
                    const kind = beat === 1 && subdivision === 0 ? "bar" : subdivision === 0 ? "beat" : "subdivision";
                    const existing = byTick.get(tick);
                    if (!existing || strength[kind] > strength[existing.kind]) byTick.set(tick, immutable({ kind, tick, audioSeconds, position: candidate }));
                }
            }
            if (bar > startingBar + 1_000_000) axisError("score_tick_out_of_safe_range", "visible boundary enumeration is too large");
        }
        return Object.freeze([...byTick.values()].sort((a, b) => a.audioSeconds - b.audioSeconds || strength[b.kind] - strength[a.kind]));
    }

    function audioRangeToCanonicalSelection(selectionRange, subdivisionsPerBeat) {
        exactObject(selectionRange, SELECTION_KEYS, "selectionRange");
        const startAudio = finite(selectionRange.startAudioSeconds, "startAudioSeconds");
        const endAudio = finite(selectionRange.endAudioSeconds, "endAudioSeconds");
        if (typeof selectionRange.nonEmptyIntent !== "boolean" || endAudio < startAudio) axisError("score_selection_range_invalid", "selection range is invalid");
        const startSnap = nearest(startAudio, subdivisionsPerBeat, "subdivision");
        let endSnap = nearest(endAudio, subdivisionsPerBeat, "subdivision");
        let expanded = false;
        if (endSnap.tick < startSnap.tick) axisError("score_selection_range_invalid", "quantized end precedes start");
        if (selectionRange.nonEmptyIntent && endSnap.tick === startSnap.tick) {
            const current = startSnap.position;
            const nextProbe = resolver.tickToAudioSeconds(startSnap.tick) + 1e-12;
            const candidates = scoreCandidatePositions(resolver, resolver.audioSecondsToTick(nextProbe), subdivisionsPerBeat, "subdivision")
                .map((candidate) => { try { return [candidate, resolver.positionToTick(candidate, subdivisionsPerBeat)]; } catch { return null; } })
                .filter((item) => item && item[1] > startSnap.tick)
                .sort((a, b) => a[1] - b[1]);
            const [candidate, tick] = candidates[0];
            endSnap = immutable({ audioSeconds: resolver.tickToAudioSeconds(tick), tick, position: candidate });
            expanded = true;
            void current;
        }
        return immutable({ start: startSnap.position, endExclusive: endSnap.position, startTick: startSnap.tick, endTickExclusive: endSnap.tick, expandedToOneSubdivision: expanded });
    }

    function canonicalSelectionToAudioRange(value, subdivisionsPerBeat) {
        exactObject(value, CANONICAL_KEYS, "canonicalSelection");
        const start = position(value.start);
        const end = position(value.endExclusive);
        safeTick(value.startTick, "startTick");
        safeTick(value.endTickExclusive, "endTickExclusive");
        if (typeof value.expandedToOneSubdivision !== "boolean") axisError("score_selection_range_invalid", "expandedToOneSubdivision must be boolean");
        const startTick = resolver.positionToTick(start, subdivisionsPerBeat);
        const endTick = resolver.positionToTick(end, subdivisionsPerBeat);
        if (endTick < startTick) axisError("score_selection_range_invalid", "selection end precedes start");
        if (value.startTick !== startTick || value.endTickExclusive !== endTick) axisError("score_selection_range_invalid", "canonical selection ticks are inconsistent");
        const startAudio = resolver.tickToAudioSeconds(startTick);
        const endAudio = resolver.tickToAudioSeconds(endTick);
        return immutable({ startAudioSeconds: startAudio, endAudioSeconds: endAudio, durationSeconds: endAudio - startAudio, startTick, endTickExclusive: endTick });
    }

    function durationToCanonicalEnd(startValue, durationValue, subdivisionsPerBeat) {
        const start = position(startValue);
        const fields = duration(durationValue);
        const startTick = resolver.positionToTick(start, subdivisionsPerBeat);
        const meter = resolver.meterAtBar(start.bar);
        const startBarTick = resolver.barToTick(start.bar);
        const durationSubdivisionCount = (fields.bars * meter.numerator + fields.beats) * subdivisionsPerBeat + fields.subdivisions;
        const provisionalEndSubdivisionIndex = ((start.beat - 1) * subdivisionsPerBeat + start.subdivision) + durationSubdivisionCount;
        safeTick(provisionalEndSubdivisionIndex, "duration units");
        const beatIndex = Math.floor(provisionalEndSubdivisionIndex / subdivisionsPerBeat);
        const residualSubdivisionIndex = provisionalEndSubdivisionIndex - beatIndex * subdivisionsPerBeat;
        const beatOffset = roundHalfAwayFromZero((beatIndex * 4 * resolver.ticksPerQuarter) / meter.denominator);
        const subdivisionOffset = roundHalfAwayFromZero(
            (residualSubdivisionIndex * 4 * resolver.ticksPerQuarter) / (meter.denominator * subdivisionsPerBeat),
        );
        const endTick = safeTick(startBarTick + beatOffset + subdivisionOffset, "end tick");
        const internalChange = resolver.score.meters.some((event) => startTick < event.tick && event.tick < endTick);
        if (internalChange) axisError("score_end_position_required", "meter changes inside the requested duration");
        let endPosition;
        try { endPosition = resolver.tickToPosition(endTick, subdivisionsPerBeat); }
        catch { axisError("score_selection_range_invalid", "duration end is not canonical"); }
        if (resolver.positionToTick(endPosition, subdivisionsPerBeat) !== endTick) axisError("score_selection_range_invalid", "duration end is not an exact canonical boundary");
        return immutable({ endExclusive: endPosition, startTick, endTickExclusive: endTick, meterStable: true });
    }

    const axis = Object.freeze({
        kind: "score",
        positionToTick: resolver.positionToTick,
        tickToPosition: resolver.tickToPosition,
        tickToScoreSeconds: resolver.tickToSeconds,
        scoreSecondsToTick: resolver.secondsToTick,
        tickToAudioSeconds: resolver.tickToAudioSeconds,
        audioSecondsToTick: resolver.audioSecondsToTick,
        barToTick: resolver.barToTick,
        meterAtBar: resolver.meterAtBar,
        enumerateVisibleBoundaries,
        sectionAtTick: resolver.sectionAtTick,
        durationToCanonicalEnd,
        snapToBar(audioSeconds) { return nearest(audioSeconds, 1, "bar"); },
        snapToBeat(audioSeconds) { return nearest(audioSeconds, 1, "beat"); },
        snapToSubdivision(audioSeconds, subdivisionsPerBeat) { safe(subdivisionsPerBeat, "subdivisionsPerBeat", 1); return nearest(audioSeconds, subdivisionsPerBeat, "subdivision"); },
        snapToVideoFrame: commonVideoSnap,
        audioRangeToCanonicalSelection,
        canonicalSelectionToAudioRange,
    });
    return axis;
}
