import assert from "node:assert/strict";
import test from "node:test";

import {
    isIntermediateDecimalText,
    parseLocalizedDecimal,
} from "../js/numeric_input.js";

test("integer strings parse", () => {
    assert.equal(parseLocalizedDecimal("12"), 12);
});

test("negative integer strings parse", () => {
    assert.equal(parseLocalizedDecimal("-12"), -12);
});

test("dot decimals parse", () => {
    assert.equal(parseLocalizedDecimal("0.125"), 0.125);
});

test("comma decimals parse", () => {
    assert.equal(parseLocalizedDecimal("0,125"), 0.125);
});

test("negative dot decimals parse", () => {
    assert.equal(parseLocalizedDecimal("-0.125"), -0.125);
});

test("negative comma decimals parse", () => {
    assert.equal(parseLocalizedDecimal("-0,125"), -0.125);
});

test("leading dot decimals parse", () => {
    assert.equal(parseLocalizedDecimal(".5"), 0.5);
    assert.equal(parseLocalizedDecimal("-.5"), -0.5);
});

test("leading comma decimals parse", () => {
    assert.equal(parseLocalizedDecimal(",5"), 0.5);
    assert.equal(parseLocalizedDecimal("-,5"), -0.5);
});

test("trailing dot decimals parse", () => {
    assert.equal(parseLocalizedDecimal("12."), 12);
});

test("trailing comma decimals parse", () => {
    assert.equal(parseLocalizedDecimal("12,"), 12);
});

test("surrounding whitespace is trimmed", () => {
    assert.equal(parseLocalizedDecimal("  -1,25  "), -1.25);
});

test("empty strings are rejected as committed values", () => {
    assert.equal(parseLocalizedDecimal(""), null);
    assert.equal(parseLocalizedDecimal("   "), null);
});

test("a lone minus is recognized as intermediate", () => {
    assert.equal(isIntermediateDecimalText("-"), true);
    assert.equal(parseLocalizedDecimal("-"), null);
});

test("a lone comma is recognized as intermediate", () => {
    assert.equal(isIntermediateDecimalText(","), true);
    assert.equal(parseLocalizedDecimal(","), null);
});

test("a negative comma prefix is recognized as intermediate", () => {
    assert.equal(isIntermediateDecimalText("-,"), true);
    assert.equal(parseLocalizedDecimal("-,"), null);
});

test("all documented intermediate decimal states are recognized", () => {
    for (const text of ["", "-", "+", ".", ",", "-.", "-,", "+.", "+,"]) {
        assert.equal(isIntermediateDecimalText(text), true, text);
    }
    assert.equal(isIntermediateDecimalText("0"), false);
});

test("multiple commas are rejected", () => {
    assert.equal(parseLocalizedDecimal("1,2,3"), null);
});

test("multiple dots are rejected", () => {
    assert.equal(parseLocalizedDecimal("1.2.3"), null);
});

test("mixed comma and dot separators are rejected", () => {
    assert.equal(parseLocalizedDecimal("1,2.3"), null);
    assert.equal(parseLocalizedDecimal("1.2,3"), null);
});

test("arbitrary numeric suffixes are rejected", () => {
    assert.equal(parseLocalizedDecimal("1abc"), null);
});

test("Infinity is rejected", () => {
    assert.equal(parseLocalizedDecimal(Number.POSITIVE_INFINITY), null);
    assert.equal(parseLocalizedDecimal("Infinity"), null);
});

test("NaN is rejected", () => {
    assert.equal(parseLocalizedDecimal(Number.NaN), null);
    assert.equal(parseLocalizedDecimal("NaN"), null);
});

test("finite numeric input is accepted", () => {
    assert.equal(parseLocalizedDecimal(-12.5), -12.5);
});

test("all non-finite numeric inputs are rejected", () => {
    assert.equal(parseLocalizedDecimal(Number.NEGATIVE_INFINITY), null);
    assert.equal(parseLocalizedDecimal(Number.POSITIVE_INFINITY), null);
    assert.equal(parseLocalizedDecimal(Number.NaN), null);
});

test("negative zero is canonicalized deterministically", () => {
    const parsed = parseLocalizedDecimal("-0");
    assert.equal(parsed, 0);
    assert.equal(Object.is(parsed, -0), false);
});

test("optional leading plus signs are accepted", () => {
    assert.equal(parseLocalizedDecimal("+12.5"), 12.5);
    assert.equal(parseLocalizedDecimal("+,5"), 0.5);
});

test("embedded spaces are rejected", () => {
    assert.equal(parseLocalizedDecimal("1 2"), null);
    assert.equal(parseLocalizedDecimal("- 1"), null);
});

test("hexadecimal and exponent notation are rejected", () => {
    assert.equal(parseLocalizedDecimal("0x10"), null);
    assert.equal(parseLocalizedDecimal("1e3"), null);
});

test("thousands-separator forms are rejected", () => {
    assert.equal(parseLocalizedDecimal("1,234,567"), null);
    assert.equal(parseLocalizedDecimal("1.234.567"), null);
});
