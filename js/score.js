const READY_KEYS = Object.freeze([
    "schema_version", "status", "ticks_per_quarter",
    "audio_seconds_at_tick_zero", "audio_duration_seconds", "bar_starts",
    "tempos", "meters", "markers", "sections", "source", "provider",
    "meter_estimated", "has_variable_meter", "has_midbar_meter_change",
    "diagnostics",
]);
const TEMPO_KEYS = Object.freeze(["tick", "us_per_quarter"]);
const METER_KEYS = Object.freeze(["tick", "numerator", "denominator"]);
const MARKER_KEYS = Object.freeze(["tick", "name"]);
const SECTION_KEYS = Object.freeze([
    "name", "start_tick", "end_tick_exclusive", "bar_aligned", "confidence",
]);
const DIAGNOSTIC_KEYS = Object.freeze(["code", "severity", "message"]);
const NORMALIZED = Symbol("normalizedScorePayload");

export class ScorePayloadError extends Error {
    constructor(code, message) {
        super(message);
        this.name = "ScorePayloadError";
        this.code = code;
    }
}

function fail(message, code = "score_route_schema_invalid") {
    throw new ScorePayloadError(code, message);
}

function ownKeys(value, keys, name) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
        fail(`${name} must be an object`);
    }
    const actual = Object.keys(value);
    if (actual.length !== keys.length || actual.some((key, index) => key !== keys[index])) {
        fail(`${name} has an invalid key set or order`);
    }
}

function finite(value, name) {
    if (typeof value !== "number" || !Number.isFinite(value)) fail(`${name} must be finite`);
    return value;
}

function safeInteger(value, name, minimum = null) {
    if (typeof value !== "number" || !Number.isInteger(value)) fail(`${name} must be an integer`);
    if (!Number.isSafeInteger(value)) fail(`${name} is outside the safe integer range`, "score_tick_out_of_safe_range");
    if (minimum !== null && value < minimum) fail(`${name} must be at least ${minimum}`);
    return value;
}

function safeDerived(value, name) {
    if (!Number.isSafeInteger(value)) fail(`${name} is outside the safe integer range`, "score_tick_out_of_safe_range");
    return value;
}

function string(value, name) {
    if (typeof value !== "string") fail(`${name} must be a string`);
    return value;
}

function bool(value, name) {
    if (typeof value !== "boolean") fail(`${name} must be a boolean`);
    return value;
}

function array(value, name) {
    if (!Array.isArray(value)) fail(`${name} must be an array`);
    return value;
}

function compareText(left, right) {
    const a = Array.from(left, (character) => character.codePointAt(0));
    const b = Array.from(right, (character) => character.codePointAt(0));
    for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
        if (a[index] !== b[index]) return a[index] - b[index];
    }
    return a.length - b.length;
}

function deepFreeze(value) {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
        for (const child of Object.values(value)) deepFreeze(child);
        Object.freeze(value);
    }
    return value;
}

function roundRatio(numerator, denominator) {
    safeDerived(numerator, "rounding numerator");
    safeDerived(denominator, "rounding denominator");
    if (denominator <= 0) fail("rounding denominator must be positive");
    const sign = numerator < 0 ? -1 : 1;
    const absolute = Math.abs(numerator);
    let quotient = Math.floor(absolute / denominator);
    const remainder = absolute - quotient * denominator;
    if (remainder * 2 >= denominator) quotient += 1;
    return safeDerived(sign * quotient, "rounded tick");
}

function absoluteBoundary(anchorTick, barIndex, meter, ticksPerQuarter) {
    const numerator = safeDerived(
        safeDerived(barIndex * meter.numerator, "bar width") * 4 * ticksPerQuarter,
        "bar width",
    );
    return safeDerived(anchorTick + roundRatio(numerator, meter.denominator), "bar boundary");
}

function buildBarGrid(ticksPerQuarter, meters, throughTick) {
    const boundaries = [0];
    const metersByBar = [];
    const midbarChangeTicks = [];
    let meterIndex = 0;
    let currentMeter = meters[0];
    let anchorTick = currentMeter.tick;
    let barIndex = 1;
    while (boundaries.at(-1) <= throughTick) {
        const nextBoundary = absoluteBoundary(anchorTick, barIndex, currentMeter, ticksPerQuarter);
        if (nextBoundary <= boundaries.at(-1)) fail("meter resolution is not increasing");
        const nextMeter = meters[meterIndex + 1] ?? null;
        if (nextMeter !== null && nextMeter.tick <= nextBoundary) {
            metersByBar.push(currentMeter);
            boundaries.push(nextMeter.tick);
            if (nextMeter.tick < nextBoundary) midbarChangeTicks.push(nextMeter.tick);
            meterIndex += 1;
            currentMeter = nextMeter;
            anchorTick = nextMeter.tick;
            barIndex = 1;
        } else {
            metersByBar.push(currentMeter);
            boundaries.push(nextBoundary);
            barIndex += 1;
        }
    }
    return { boundaries, metersByBar, midbarChangeTicks };
}

export function normalizeScorePayload(payload) {
    ownKeys(payload, READY_KEYS, "ready payload");
    if (safeInteger(payload.schema_version, "schema_version") !== 1) fail("schema_version must be 1");
    if (payload.status !== "ready") fail("status must be ready");
    const ticksPerQuarter = safeInteger(payload.ticks_per_quarter, "ticks_per_quarter", 1);
    const alignment = finite(payload.audio_seconds_at_tick_zero, "audio_seconds_at_tick_zero");
    const duration = finite(payload.audio_duration_seconds, "audio_duration_seconds");
    if (duration < 0) fail("audio_duration_seconds must be nonnegative");

    const tempos = array(payload.tempos, "tempos").map((event, index) => {
        ownKeys(event, TEMPO_KEYS, `tempos[${index}]`);
        return {
            tick: safeInteger(event.tick, `tempos[${index}].tick`, 0),
            usPerQuarter: safeInteger(event.us_per_quarter, `tempos[${index}].us_per_quarter`, 1),
        };
    });
    if (!tempos.length || tempos[0].tick !== 0) fail("the first tempo must begin at tick zero");
    tempos.forEach((event, index) => {
        if (index && event.tick <= tempos[index - 1].tick) fail("tempo ticks must increase");
        if (index && event.usPerQuarter === tempos[index - 1].usPerQuarter) fail("consecutive tempo values must differ");
    });

    const meters = array(payload.meters, "meters").map((event, index) => {
        ownKeys(event, METER_KEYS, `meters[${index}]`);
        const denominator = safeInteger(event.denominator, `meters[${index}].denominator`, 1);
        if (denominator > 64 || (denominator & (denominator - 1)) !== 0) fail("meter denominator must be a power of two through 64");
        return {
            tick: safeInteger(event.tick, `meters[${index}].tick`, 0),
            numerator: safeInteger(event.numerator, `meters[${index}].numerator`, 1),
            denominator,
        };
    });
    if (!meters.length || meters[0].tick !== 0) fail("the first meter must begin at tick zero");
    meters.forEach((event, index) => {
        if (index && event.tick <= meters[index - 1].tick) fail("meter ticks must increase");
        if (index && event.numerator === meters[index - 1].numerator && event.denominator === meters[index - 1].denominator) fail("consecutive meters must differ");
    });

    const markers = array(payload.markers, "markers").map((event, index) => {
        ownKeys(event, MARKER_KEYS, `markers[${index}]`);
        return { tick: safeInteger(event.tick, `markers[${index}].tick`, 0), name: string(event.name, `markers[${index}].name`) };
    });
    for (let index = 1; index < markers.length; index += 1) {
        const previous = markers[index - 1];
        const current = markers[index];
        if (current.tick < previous.tick || (current.tick === previous.tick && compareText(current.name, previous.name) < 0)) fail("markers are not canonically ordered");
    }

    const sections = array(payload.sections, "sections").map((event, index) => {
        ownKeys(event, SECTION_KEYS, `sections[${index}]`);
        const startTick = safeInteger(event.start_tick, `sections[${index}].start_tick`, 0);
        const endTickExclusive = safeInteger(event.end_tick_exclusive, `sections[${index}].end_tick_exclusive`, 0);
        if (endTickExclusive < startTick) fail("Section end precedes start");
        const confidence = event.confidence === null ? null : finite(event.confidence, `sections[${index}].confidence`);
        return {
            name: string(event.name, `sections[${index}].name`),
            startTick,
            endTickExclusive,
            barAligned: bool(event.bar_aligned, `sections[${index}].bar_aligned`),
            confidence,
        };
    });
    for (let index = 1; index < sections.length; index += 1) {
        const a = sections[index - 1];
        const b = sections[index];
        if (b.startTick < a.startTick || (b.startTick === a.startTick && (b.endTickExclusive < a.endTickExclusive || (b.endTickExclusive === a.endTickExclusive && compareText(b.name, a.name) < 0)))) fail("Sections are not canonically ordered");
    }

    const barStarts = array(payload.bar_starts, "bar_starts").map((tick, index) => safeInteger(tick, `bar_starts[${index}]`, 0));
    if (barStarts.length < 2 || barStarts[0] !== 0) fail("bar_starts must contain zero and an exclusive boundary");
    for (let index = 1; index < barStarts.length; index += 1) if (barStarts[index] <= barStarts[index - 1]) fail("bar_starts must increase");
    const computed = buildBarGrid(ticksPerQuarter, meters, barStarts.at(-2));
    if (computed.boundaries.length !== barStarts.length || computed.boundaries.some((tick, index) => tick !== barStarts[index])) fail("bar_starts are incompatible with the Score");
    let segmentSeconds = 0;
    const tempoSegments = tempos.map((tempo, index) => {
        if (index) {
            const previous = tempos[index - 1];
            segmentSeconds += (tempo.tick - previous.tick) * previous.usPerQuarter / (ticksPerQuarter * 1_000_000);
        }
        return { tick: tempo.tick, seconds: segmentSeconds, usPerQuarter: tempo.usPerQuarter };
    });
    const audioEndScoreSeconds = duration - alignment;
    let endSegment = tempoSegments[0];
    for (const segment of tempoSegments) {
        if (segment.seconds <= audioEndScoreSeconds) endSegment = segment;
        else break;
    }
    const rawEndTick = endSegment.tick
        + (audioEndScoreSeconds - endSegment.seconds) * ticksPerQuarter * 1_000_000 / endSegment.usPerQuarter;
    if (!Number.isFinite(rawEndTick)) fail("audio end tick is not finite", "score_tick_out_of_safe_range");
    const throughTick = Math.max(0, Math.ceil(rawEndTick), meters.at(-1).tick);
    safeDerived(throughTick, "audio through tick");
    if (barStarts.at(-1) <= throughTick) fail("bar_starts do not cover the audio target");

    const diagnostics = array(payload.diagnostics, "diagnostics").map((value, index) => {
        ownKeys(value, DIAGNOSTIC_KEYS, `diagnostics[${index}]`);
        const severity = string(value.severity, `diagnostics[${index}].severity`);
        if (severity !== "warning" && severity !== "error") fail("diagnostic severity is invalid");
        return { code: string(value.code, `diagnostics[${index}].code`), severity, message: string(value.message, `diagnostics[${index}].message`) };
    });
    if (!["json", "midi", "analyzed"].includes(payload.source)) fail("source is invalid");
    if (!["explicit", "json_sidecar", "midi_sidecar", "analysis"].includes(payload.provider)) fail("provider is invalid");

    const score = {
        ticksPerQuarter,
        tempos,
        meters,
        markers,
        sections,
        source: payload.source,
        meterEstimated: bool(payload.meter_estimated, "meter_estimated"),
        hasVariableMeter: bool(payload.has_variable_meter, "has_variable_meter"),
        hasMidbarMeterChange: bool(payload.has_midbar_meter_change, "has_midbar_meter_change"),
    };
    const signatures = new Set(meters.map((meter) => `${meter.numerator}/${meter.denominator}`));
    const midbar = computed.midbarChangeTicks.length > 0;
    if ((signatures.size > 1) !== score.hasVariableMeter || midbar !== score.hasMidbarMeterChange) fail("meter flags are inconsistent");
    const normalized = {
        schemaVersion: 1,
        status: "ready",
        ticksPerQuarter,
        audioSecondsAtTickZero: alignment,
        audioDurationSeconds: duration,
        barStarts,
        score,
        source: payload.source,
        provider: payload.provider,
        diagnostics,
    };
    Object.defineProperty(normalized, NORMALIZED, { value: true });
    return deepFreeze(normalized);
}

function binaryRight(values, query, key = (value) => value) {
    let low = 0;
    let high = values.length;
    while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (key(values[middle]) <= query) low = middle + 1;
        else high = middle;
    }
    return low;
}

function requireFiniteQuery(value, name) {
    if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
    return value;
}

function requireSafe(value, name, minimum) {
    if (!Number.isSafeInteger(value)) throw new TypeError(`${name} must be a safe integer`);
    if (value < minimum) throw new RangeError(`${name} must be at least ${minimum}`);
    return value;
}

function frozenPosition(bar, beat, subdivision) {
    return Object.freeze({ bar, beat, subdivision });
}

export function createScoreResolver(normalizedReadyPayload) {
    if (!normalizedReadyPayload || normalizedReadyPayload[NORMALIZED] !== true || !Object.isFrozen(normalizedReadyPayload)) {
        throw new TypeError("normalizedReadyPayload must come from normalizeScorePayload");
    }
    const score = normalizedReadyPayload.score;
    const tpq = score.ticksPerQuarter;
    const segments = [];
    let startSeconds = 0;
    score.tempos.forEach((tempo, index) => {
        if (index) {
            const previous = score.tempos[index - 1];
            startSeconds += (tempo.tick - previous.tick) * previous.usPerQuarter / (tpq * 1_000_000);
            if (!Number.isFinite(startSeconds)) fail("tempo segment is not finite");
        }
        segments.push(Object.freeze({ startTick: tempo.tick, startSeconds, usPerQuarter: tempo.usPerQuarter }));
    });
    const barStarts = normalizedReadyPayload.barStarts;
    const grid = buildBarGrid(tpq, score.meters, barStarts.at(-2));
    const finalMeter = score.meters.at(-1);
    const finalMeterIndex = barStarts.indexOf(finalMeter.tick);
    const cachedBarCount = barStarts.length - 1;

    function tickToSeconds(tick) {
        const value = requireFiniteQuery(tick, "tick");
        const index = Math.max(0, binaryRight(segments, value, (item) => item.startTick) - 1);
        const segment = segments[index];
        const result = segment.startSeconds + (value - segment.startTick) * segment.usPerQuarter / (tpq * 1_000_000);
        if (!Number.isFinite(result)) throw new RangeError("tickToSeconds result must be finite");
        return result;
    }

    function secondsToTick(seconds) {
        const value = requireFiniteQuery(seconds, "seconds");
        const index = Math.max(0, binaryRight(segments, value, (item) => item.startSeconds) - 1);
        const segment = segments[index];
        const result = segment.startTick + (value - segment.startSeconds) * tpq * 1_000_000 / segment.usPerQuarter;
        if (!Number.isFinite(result)) throw new RangeError("secondsToTick result must be finite");
        return result;
    }

    function barToTick(bar) {
        const value = requireSafe(bar, "bar", 1);
        if (value <= cachedBarCount) return barStarts[value - 1];
        return absoluteBoundary(finalMeter.tick, value - (finalMeterIndex + 1), finalMeter, tpq);
    }

    function meterAtBar(bar) {
        const value = requireSafe(bar, "bar", 1);
        const meter = value <= cachedBarCount ? grid.metersByBar[value - 1] : finalMeter;
        return Object.freeze({ numerator: meter.numerator, denominator: meter.denominator });
    }

    function positionToTick(position, subdivisionsPerBeat) {
        ownKeys(position, ["bar", "beat", "subdivision"], "position");
        const bar = requireSafe(position.bar, "bar", 1);
        const beat = requireSafe(position.beat, "beat", 1);
        const subdivision = requireSafe(position.subdivision, "subdivision", 0);
        const subdivisions = requireSafe(subdivisionsPerBeat, "subdivisionsPerBeat", 1);
        const meter = meterAtBar(bar);
        if (beat > meter.numerator) throw new RangeError("beat exceeds meter numerator");
        if (subdivision >= subdivisions) throw new RangeError("subdivision exceeds grid");
        if (subdivisions * meter.denominator > 4 * tpq) throw new RangeError("subdivision grid is finer than integer ticks");
        const barStart = barToTick(bar);
        const barEnd = barToTick(bar + 1);
        const beatStart = safeDerived(barStart + roundRatio((beat - 1) * 4 * tpq, meter.denominator), "beat tick");
        const tick = safeDerived(beatStart + roundRatio(subdivision * 4 * tpq, meter.denominator * subdivisions), "position tick");
        if (tick < barStart || tick >= barEnd) throw new RangeError("position is outside the actual bar");
        return tick;
    }

    function containingBarNumber(tick) {
        const value = requireFiniteQuery(tick, "tick");
        if (value < 0) throw new RangeError("tick must be nonnegative");
        if (value < barStarts.at(-1)) return binaryRight(barStarts, value);
        let bar = finalMeterIndex + 1;
        const approximateWidth = finalMeter.numerator * 4 * tpq / finalMeter.denominator;
        bar += Math.max(0, Math.floor((value - finalMeter.tick) / approximateWidth));
        while (value < barToTick(bar)) bar -= 1;
        while (value >= barToTick(bar + 1)) bar += 1;
        return bar;
    }

    function tickToPosition(tick, subdivisionsPerBeat) {
        const value = requireFiniteQuery(tick, "tick");
        if (value < 0) throw new RangeError("tick must be nonnegative");
        const subdivisions = requireSafe(subdivisionsPerBeat, "subdivisionsPerBeat", 1);
        const bar = containingBarNumber(value);
        const meter = meterAtBar(bar);
        if (subdivisions * meter.denominator > 4 * tpq) throw new RangeError("subdivision grid is finer than integer ticks");
        const candidates = [];
        const barStart = barToTick(bar);
        const beatFloor = Math.floor((value - barStart) * meter.denominator / (4 * tpq));
        const beatIndexes = new Set([0, meter.numerator - 1]);
        for (let index = beatFloor - 2; index <= beatFloor + 2; index += 1) beatIndexes.add(index);
        for (const beatIndex of beatIndexes) {
            const beat = beatIndex + 1;
            if (beat < 1 || beat > meter.numerator) continue;
            let beatStart;
            try { beatStart = positionToTick(frozenPosition(bar, beat, 0), subdivisions); }
            catch (error) { if (!(error instanceof RangeError)) throw error; else continue; }
            const subdivisionFloor = Math.floor(
                (value - beatStart) * meter.denominator * subdivisions / (4 * tpq),
            );
            const subdivisionIndexes = new Set([0, subdivisions - 1]);
            for (let index = subdivisionFloor - 2; index <= subdivisionFloor + 2; index += 1) subdivisionIndexes.add(index);
            for (const subdivision of subdivisionIndexes) {
                if (subdivision < 0 || subdivision >= subdivisions) continue;
                try {
                    const candidate = frozenPosition(bar, beat, subdivision);
                    candidates.push([candidate, positionToTick(candidate, subdivisions)]);
                } catch (error) {
                    if (!(error instanceof RangeError)) throw error;
                }
            }
        }
        const following = frozenPosition(bar + 1, 1, 0);
        candidates.push([following, positionToTick(following, subdivisions)]);
        candidates.sort((left, right) => Math.abs(left[1] - value) - Math.abs(right[1] - value) || right[1] - left[1]);
        return candidates[0][0];
    }

    function containingBarTicks(tick) {
        const bar = containingBarNumber(tick);
        return Object.freeze({ startTick: barToTick(bar), endTickExclusive: barToTick(bar + 1), bar });
    }

    function containingBeatTicks(tick) {
        const barInfo = containingBarTicks(tick);
        const meter = meterAtBar(barInfo.bar);
        const value = requireFiniteQuery(tick, "tick");
        let winner = null;
        for (let beat = 1; beat <= meter.numerator; beat += 1) {
            const position = frozenPosition(barInfo.bar, beat, 0);
            let start;
            try { start = positionToTick(position, 1); } catch { continue; }
            const next = beat < meter.numerator
                ? (() => { try { return positionToTick(frozenPosition(barInfo.bar, beat + 1, 0), 1); } catch { return barInfo.endTickExclusive; } })()
                : barInfo.endTickExclusive;
            const end = Math.min(next, barInfo.endTickExclusive);
            if (start <= value && value < end) winner = Object.freeze({ startTick: start, endTickExclusive: end, bar: barInfo.bar, beat });
        }
        if (winner === null) throw new RangeError("tick has no containing Beat");
        return winner;
    }

    function sectionAtTick(tick) {
        const value = requireFiniteQuery(tick, "tick");
        if (value < 0) return null;
        const candidates = score.sections.filter((section) => section.startTick <= value && value < section.endTickExclusive);
        candidates.sort((a, b) => b.startTick - a.startTick || a.endTickExclusive - b.endTickExclusive || compareText(a.name, b.name));
        return candidates[0] ?? null;
    }

    const resolver = {
        ticksPerQuarter: tpq,
        audioSecondsAtTickZero: normalizedReadyPayload.audioSecondsAtTickZero,
        audioDurationSeconds: normalizedReadyPayload.audioDurationSeconds,
        score,
        _metadata: deepFreeze({ barStarts: [...barStarts], metersByBar: grid.metersByBar.map((meter) => ({ ...meter })), finalMeter: { ...finalMeter } }),
        tickToSeconds,
        secondsToTick,
        tickToAudioSeconds(tick) { return tickToSeconds(tick) + normalizedReadyPayload.audioSecondsAtTickZero; },
        audioSecondsToTick(seconds) { return secondsToTick(requireFiniteQuery(seconds, "audioSeconds") - normalizedReadyPayload.audioSecondsAtTickZero); },
        barToTick,
        meterAtBar,
        positionToTick,
        tickToPosition,
        containingBarTicks,
        containingBeatTicks,
        sections() { return score.sections; },
        sectionAtTick,
    };
    return Object.freeze(resolver);
}
