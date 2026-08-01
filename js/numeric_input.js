const INTERMEDIATE_DECIMAL_TEXT = new Set([
    "",
    "-",
    "+",
    ".",
    ",",
    "-.",
    "-,",
    "+.",
    "+,",
]);

const DECIMAL_TEXT_PATTERN = /^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)$/;

/** Return whether text is a valid but incomplete decimal editing state. */
export function isIntermediateDecimalText(value) {
    return typeof value === "string" && INTERMEDIATE_DECIMAL_TEXT.has(value);
}

/** Parse a locale-tolerant decimal without accepting partial numeric suffixes. */
export function parseLocalizedDecimal(value) {
    if (typeof value === "number") {
        if (!Number.isFinite(value)) return null;
        return Object.is(value, -0) ? 0 : value;
    }
    if (typeof value !== "string") return null;

    const trimmed = value.trim();
    if (!trimmed || !DECIMAL_TEXT_PATTERN.test(trimmed)) return null;
    const parsed = Number(trimmed.replace(",", "."));
    if (!Number.isFinite(parsed)) return null;
    return Object.is(parsed, -0) ? 0 : parsed;
}
