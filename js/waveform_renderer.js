import { selectWaveformPeakLevel } from "./waveform_peaks.js";


export const MAX_WAVEFORM_RENDER_WIDTH = 1_000_000;
export const MAX_WAVEFORM_DEVICE_PIXEL_RATIO = 4;

const DEFAULT_WAVEFORM_COLOR = "#9ca3af";
const DEFAULT_CENTER_LINE_COLOR = "rgba(255, 255, 255, 0.07)";
const DEFAULT_VERTICAL_PADDING = 4;

function requireFinitePositive(value, label) {
    if (!Number.isFinite(value) || value <= 0) {
        throw new RangeError(`${label} must be finite and greater than zero`);
    }
}

function requirePositiveSafeInteger(value, label) {
    if (!Number.isSafeInteger(value) || value <= 0) {
        throw new RangeError(`${label} must be a positive safe integer`);
    }
}

function validatePyramid(pyramid) {
    if (!pyramid || typeof pyramid !== "object") {
        throw new TypeError("pyramid is required");
    }
    requireFinitePositive(pyramid.durationSeconds, "pyramid.durationSeconds");
    requireFinitePositive(pyramid.sampleRate, "pyramid.sampleRate");
    requirePositiveSafeInteger(pyramid.sampleCount, "pyramid.sampleCount");
    if (!Array.isArray(pyramid.levels) || pyramid.levels.length === 0) {
        throw new TypeError("pyramid.levels must contain at least one level");
    }

    for (const [levelIndex, level] of pyramid.levels.entries()) {
        if (!level || typeof level !== "object") {
            throw new TypeError(`pyramid.levels[${levelIndex}] must be an object`);
        }
        requirePositiveSafeInteger(
            level.samplesPerPeak,
            `pyramid.levels[${levelIndex}].samplesPerPeak`,
        );
        requirePositiveSafeInteger(
            level.peakCount,
            `pyramid.levels[${levelIndex}].peakCount`,
        );
        requireFinitePositive(
            level.peaksPerSecond,
            `pyramid.levels[${levelIndex}].peaksPerSecond`,
        );
        if (!(level.peaks instanceof Int16Array)) {
            throw new TypeError(
                `pyramid.levels[${levelIndex}].peaks must be an Int16Array`,
            );
        }
        if (level.peaks.length !== level.peakCount * 2) {
            throw new RangeError(
                `pyramid.levels[${levelIndex}].peaks length does not match peakCount`,
            );
        }
    }
}

function integerBoundary(value, direction) {
    const nearest = Math.round(value);
    const tolerance = Number.EPSILON * Math.max(1, Math.abs(value)) * 8;
    if (Math.abs(value - nearest) <= tolerance) return nearest;
    return direction === "down" ? Math.floor(value) : Math.ceil(value);
}

function clampInteger(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
}

/**
 * Convert a signed Int16 envelope value to its asymmetric unit interval mapping.
 */
export function normalizeInt16Sample(value) {
    if (!Number.isFinite(value)) {
        throw new TypeError("Int16 sample must be finite");
    }
    const normalized = value < 0 ? value / 32768 : value / 32767;
    return Math.min(1, Math.max(-1, normalized));
}

/**
 * Build one raw Int16 min/max pair per visible CSS horizontal pixel.
 * This function never mutates the pyramid, its peak arrays, or options.
 */
export function createWaveformRenderPlan(pyramid, options = {}) {
    validatePyramid(pyramid);
    if (!options || typeof options !== "object") {
        throw new TypeError("options must be an object");
    }

    const pixelWidth = options.pixelWidth;
    if (
        !Number.isSafeInteger(pixelWidth)
        || pixelWidth <= 0
        || pixelWidth > MAX_WAVEFORM_RENDER_WIDTH
    ) {
        throw new RangeError(
            `pixelWidth must be an integer from 1 to ${MAX_WAVEFORM_RENDER_WIDTH}`,
        );
    }

    const requestedStartSeconds = options.startSeconds ?? 0;
    const requestedEndSeconds = options.endSeconds ?? pyramid.durationSeconds;
    if (!Number.isFinite(requestedStartSeconds) || requestedStartSeconds < 0) {
        throw new RangeError("startSeconds must be finite and non-negative");
    }
    if (!Number.isFinite(requestedEndSeconds)) {
        throw new RangeError("endSeconds must be finite");
    }
    if (requestedEndSeconds <= requestedStartSeconds) {
        throw new RangeError("endSeconds must be greater than startSeconds");
    }

    const endpointTolerance = (
        1 / pyramid.sampleRate
        + Number.EPSILON * Math.max(1, pyramid.durationSeconds) * 8
    );
    if (requestedEndSeconds - pyramid.durationSeconds > endpointTolerance) {
        throw new RangeError("endSeconds exceeds the pyramid duration");
    }
    const endSeconds = Math.min(requestedEndSeconds, pyramid.durationSeconds);
    const startSeconds = requestedStartSeconds;
    if (endSeconds <= startSeconds) {
        throw new RangeError("the clamped visible range must have positive duration");
    }

    const visibleDurationSeconds = endSeconds - startSeconds;
    const pixelsPerSecond = pixelWidth / visibleDurationSeconds;
    const selectedLevel = selectWaveformPeakLevel(pyramid, pixelsPerSecond);
    const selectedLevelIndex = pyramid.levels.indexOf(selectedLevel);
    const startSample = Math.min(
        pyramid.sampleCount,
        startSeconds * pyramid.sampleRate,
    );
    const endSample = Math.min(
        pyramid.sampleCount,
        endSeconds * pyramid.sampleRate,
    );
    const firstPeak = clampInteger(
        integerBoundary(startSample / selectedLevel.samplesPerPeak, "down"),
        0,
        selectedLevel.peakCount - 1,
    );
    const finalPeakExclusive = clampInteger(
        integerBoundary(endSample / selectedLevel.samplesPerPeak, "up"),
        firstPeak + 1,
        selectedLevel.peakCount,
    );
    const selectedPeakCount = finalPeakExclusive - firstPeak;
    const columns = new Int16Array(pixelWidth * 2);

    if (selectedPeakCount >= pixelWidth) {
        for (let columnIndex = 0; columnIndex < pixelWidth; columnIndex += 1) {
            const aggregateStart = (
                firstPeak + Math.floor(columnIndex * selectedPeakCount / pixelWidth)
            );
            const aggregateEnd = (
                firstPeak + Math.floor((columnIndex + 1) * selectedPeakCount / pixelWidth)
            );
            let minimum = 32767;
            let maximum = -32768;
            for (let peakIndex = aggregateStart; peakIndex < aggregateEnd; peakIndex += 1) {
                const peakMinimum = selectedLevel.peaks[peakIndex * 2];
                const peakMaximum = selectedLevel.peaks[peakIndex * 2 + 1];
                if (peakMinimum > peakMaximum) {
                    throw new RangeError("a selected peak minimum exceeds its maximum");
                }
                minimum = Math.min(minimum, peakMinimum);
                maximum = Math.max(maximum, peakMaximum);
            }
            columns[columnIndex * 2] = minimum;
            columns[columnIndex * 2 + 1] = maximum;
        }
    } else {
        for (let columnIndex = 0; columnIndex < pixelWidth; columnIndex += 1) {
            const peakOffset = Math.min(
                selectedPeakCount - 1,
                Math.floor(columnIndex * selectedPeakCount / pixelWidth),
            );
            const peakIndex = firstPeak + peakOffset;
            const minimum = selectedLevel.peaks[peakIndex * 2];
            const maximum = selectedLevel.peaks[peakIndex * 2 + 1];
            if (minimum > maximum) {
                throw new RangeError("a selected peak minimum exceeds its maximum");
            }
            columns[columnIndex * 2] = minimum;
            columns[columnIndex * 2 + 1] = maximum;
        }
    }

    return {
        selectedLevel,
        selectedLevelIndex,
        pixelsPerSecond,
        pixelWidth,
        startSeconds,
        endSeconds,
        visibleDurationSeconds,
        firstPeak,
        finalPeakExclusive,
        selectedPeakCount,
        columns,
    };
}

function normalizedDevicePixelRatio(value) {
    const candidate = Number.isFinite(value) && value > 0 ? value : 1;
    return Math.min(MAX_WAVEFORM_DEVICE_PIXEL_RATIO, candidate);
}

function finiteClientDimension(value) {
    return Number.isFinite(value) && value >= 0 ? value : 0;
}

function canvasSizing(canvas, options) {
    if (!canvas || typeof canvas !== "object") {
        throw new TypeError("canvas is required");
    }
    const cssWidth = finiteClientDimension(canvas.clientWidth);
    const cssHeight = finiteClientDimension(canvas.clientHeight);
    const detectedDpr = options.devicePixelRatio ?? globalThis.devicePixelRatio;
    const devicePixelRatio = normalizedDevicePixelRatio(detectedDpr);
    const backingWidth = Math.max(1, Math.round(cssWidth * devicePixelRatio));
    const backingHeight = Math.max(1, Math.round(cssHeight * devicePixelRatio));

    if (canvas.width !== backingWidth) canvas.width = backingWidth;
    if (canvas.height !== backingHeight) canvas.height = backingHeight;

    return {
        cssWidth,
        cssHeight,
        backingWidth,
        backingHeight,
        devicePixelRatio,
    };
}

function prepareCanvas(canvas, options) {
    const sizing = canvasSizing(canvas, options);
    const context = typeof canvas.getContext === "function"
        ? canvas.getContext("2d")
        : null;
    if (!context) return { ...sizing, context: null };

    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, sizing.backingWidth, sizing.backingHeight);
    context.setTransform(
        sizing.devicePixelRatio,
        0,
        0,
        sizing.devicePixelRatio,
        0,
        0,
    );
    return { ...sizing, context };
}

/**
 * Resize and clear a Canvas without drawing fallback or status content.
 */
export function clearWaveformCanvas(canvas, options = {}) {
    if (!options || typeof options !== "object") {
        throw new TypeError("options must be an object");
    }
    const prepared = prepareCanvas(canvas, options);
    return {
        cssWidth: prepared.cssWidth,
        cssHeight: prepared.cssHeight,
        backingWidth: prepared.backingWidth,
        backingHeight: prepared.backingHeight,
        devicePixelRatio: prepared.devicePixelRatio,
        contextAvailable: prepared.context !== null,
    };
}

/**
 * Render a peak pyramid into a supplied Canvas 2D context and return its plan.
 */
export function renderWaveformCanvas(canvas, pyramid, options = {}) {
    if (!options || typeof options !== "object") {
        throw new TypeError("options must be an object");
    }
    const prepared = prepareCanvas(canvas, options);
    const pixelWidth = Math.max(1, Math.round(prepared.cssWidth));
    const plan = createWaveformRenderPlan(pyramid, {
        pixelWidth,
        startSeconds: options.startSeconds,
        endSeconds: options.endSeconds,
    });
    const result = {
        ...plan,
        cssWidth: prepared.cssWidth,
        cssHeight: prepared.cssHeight,
        backingWidth: prepared.backingWidth,
        backingHeight: prepared.backingHeight,
        devicePixelRatio: prepared.devicePixelRatio,
        contextAvailable: prepared.context !== null,
    };
    const context = prepared.context;
    if (!context || prepared.cssWidth <= 0 || prepared.cssHeight <= 0) return result;

    const centerY = prepared.cssHeight / 2;
    context.lineCap = "butt";
    context.lineWidth = 1 / prepared.devicePixelRatio;
    context.strokeStyle = options.centerLineColor ?? DEFAULT_CENTER_LINE_COLOR;
    context.beginPath();
    context.moveTo(0, centerY);
    context.lineTo(prepared.cssWidth, centerY);
    context.stroke();

    const requestedPadding = options.verticalPadding ?? DEFAULT_VERTICAL_PADDING;
    const safePadding = Number.isFinite(requestedPadding) && requestedPadding >= 0
        ? requestedPadding
        : DEFAULT_VERTICAL_PADDING;
    const verticalPadding = Math.min(safePadding, prepared.cssHeight / 2);
    const amplitude = Math.max(0, centerY - verticalPadding);
    if (amplitude <= 0) return { ...result, verticalPadding };

    context.strokeStyle = options.waveformColor ?? DEFAULT_WAVEFORM_COLOR;
    context.beginPath();
    let drawnColumnCount = 0;
    const minimumSegmentHeight = 1 / prepared.devicePixelRatio;
    for (let columnIndex = 0; columnIndex < plan.pixelWidth; columnIndex += 1) {
        const minimum = plan.columns[columnIndex * 2];
        const maximum = plan.columns[columnIndex * 2 + 1];
        if (minimum === 0 && maximum === 0) continue;

        let topY = centerY - normalizeInt16Sample(maximum) * amplitude;
        let bottomY = centerY - normalizeInt16Sample(minimum) * amplitude;
        if (bottomY - topY < minimumSegmentHeight) {
            const midpoint = (topY + bottomY) / 2;
            topY = Math.max(verticalPadding, midpoint - minimumSegmentHeight / 2);
            bottomY = Math.min(
                prepared.cssHeight - verticalPadding,
                topY + minimumSegmentHeight,
            );
            topY = Math.max(verticalPadding, bottomY - minimumSegmentHeight);
        }
        const x = (columnIndex + 0.5) * prepared.cssWidth / plan.pixelWidth;
        context.moveTo(x, topY);
        context.lineTo(x, bottomY);
        drawnColumnCount += 1;
    }
    if (drawnColumnCount > 0) context.stroke();

    return { ...result, verticalPadding, drawnColumnCount };
}
