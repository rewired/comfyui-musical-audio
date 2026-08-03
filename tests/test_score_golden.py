import json
import math
from pathlib import Path
import unittest
from bisect import bisect_right

from score.bars import round_half_away_from_zero_ratio
from score.resolver import ScoreResolver
from score.selection import ScoreSelectionError, resolve_score_selection
from score.serialize import score_from_dict, sidecar_from_dict


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
CORPUS = FIXTURES / "score_timing_golden_v1.json"


class TimeAxisError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class ScorePayloadError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class RangeError(ValueError):
    code = None


def _position(value):
    return (value["bar"], value["beat"], value["subdivision"])


def _position_dict(value):
    return {"bar": value[0], "beat": value[1], "subdivision": value[2]}


def _section_dict(section):
    if section is None:
        return None
    return {
        "name": section.name,
        "startTick": section.start_tick,
        "endTickExclusive": section.end_tick_exclusive,
        "barAligned": section.bar_aligned,
        "confidence": section.confidence,
    }


class _AxisAdapter:
    def __init__(self, resolver, subdivisions):
        self.resolver = resolver
        self.subdivisions = subdivisions

    def containing_bar(self, tick):
        if tick < 0:
            raise ValueError("tick must be nonnegative")
        boundaries = self.resolver.bar_grid.boundaries
        if tick < boundaries[-1]:
            bar = bisect_right(boundaries, tick)
        else:
            bar = len(boundaries) - 1
            while tick >= self.resolver.bar_to_tick(bar + 1):
                bar += 1
        return bar, self.resolver.bar_to_tick(bar), self.resolver.bar_to_tick(bar + 1)

    def containing_beat(self, tick):
        bar, bar_start, bar_end = self.containing_bar(tick)
        numerator, denominator = self.resolver.meter_at_bar(bar)
        for beat in range(1, numerator + 1):
            start = bar_start + round_half_away_from_zero_ratio(
                (beat - 1) * 4 * self.resolver.score.ticks_per_quarter,
                denominator,
            )
            end = min(
                bar_end,
                bar_start + round_half_away_from_zero_ratio(
                    beat * 4 * self.resolver.score.ticks_per_quarter,
                    denominator,
                ),
            )
            if start <= tick < end:
                return bar, beat, start, end
        raise ValueError("tick has no containing Beat")

    def section_at_tick(self, tick):
        if tick < 0:
            return None
        candidates = [
            section for section in self.resolver.sections()
            if section.start_tick <= tick < section.end_tick_exclusive
        ]
        return min(
            candidates,
            key=lambda section: (-section.start_tick, section.end_tick_exclusive, section.name),
            default=None,
        )

    def _candidate_positions(self, raw_tick, subdivisions, kind):
        if raw_tick < 0:
            return [(1, 1, 0)]
        bar = self.containing_bar(raw_tick)[0]
        candidates = []
        for current_bar in range(max(1, bar - 1), bar + 3):
            numerator, _ = self.resolver.meter_at_bar(current_bar)
            if kind == "bar":
                candidates.append((current_bar, 1, 0))
            else:
                for beat in range(1, numerator + 1):
                    if kind == "beat":
                        candidates.append((current_bar, beat, 0))
                    else:
                        candidates.extend(
                            (current_bar, beat, subdivision)
                            for subdivision in range(subdivisions)
                        )
        return candidates

    def snap(self, audio_seconds, subdivisions, kind):
        raw_tick = self.resolver.audio_seconds_to_tick(audio_seconds)
        values = []
        for candidate in self._candidate_positions(raw_tick, subdivisions, kind):
            try:
                tick = self.resolver.position_to_tick(*candidate, subdivisions)
            except ValueError:
                continue
            actual = self.resolver.tick_to_audio_seconds(tick)
            values.append((abs(actual - audio_seconds), -actual, actual, tick, candidate))
        _, _, actual, tick, candidate = min(values)
        return {
            "audioSeconds": actual,
            "tick": tick,
            "position": _position_dict(candidate),
        }

    def enumerate_visible(self, value, subdivisions):
        start = value["startAudioSeconds"]
        end = value["endAudioSeconds"]
        if end <= start:
            raise TimeAxisError("score_selection_range_invalid", "invalid visible range")
        start_tick = self.resolver.audio_seconds_to_tick(start)
        start_bar = 1 if start_tick < 0 else max(1, self.containing_bar(start_tick)[0] - 1)
        by_tick = {}
        strength = {"subdivision": 0, "beat": 1, "bar": 2}
        bar = start_bar
        while True:
            bar_audio = self.resolver.tick_to_audio_seconds(self.resolver.bar_to_tick(bar))
            if bar_audio >= end:
                break
            numerator, _ = self.resolver.meter_at_bar(bar)
            for beat in range(1, numerator + 1):
                for subdivision in range(subdivisions):
                    position = (bar, beat, subdivision)
                    try:
                        tick = self.resolver.position_to_tick(*position, subdivisions)
                    except ValueError:
                        continue
                    audio = self.resolver.tick_to_audio_seconds(tick)
                    if not start <= audio < end:
                        continue
                    kind = "bar" if beat == 1 and subdivision == 0 else "beat" if subdivision == 0 else "subdivision"
                    if tick not in by_tick or strength[kind] > strength[by_tick[tick]["kind"]]:
                        by_tick[tick] = {
                            "kind": kind,
                            "tick": tick,
                            "audioSeconds": audio,
                            "position": _position_dict(position),
                        }
            bar += 1
        return sorted(by_tick.values(), key=lambda item: (item["audioSeconds"], -strength[item["kind"]]))

    def selection(self, value, subdivisions):
        if value["endAudioSeconds"] < value["startAudioSeconds"]:
            raise TimeAxisError("score_selection_range_invalid", "end precedes start")
        start = self.snap(value["startAudioSeconds"], subdivisions, "subdivision")
        end = self.snap(value["endAudioSeconds"], subdivisions, "subdivision")
        if end["tick"] < start["tick"]:
            raise TimeAxisError("score_selection_range_invalid", "quantized end precedes start")
        expanded = False
        if value["nonEmptyIntent"] and end["tick"] == start["tick"]:
            candidates = []
            for item in self._candidate_positions(start["tick"] + 1e-12, subdivisions, "subdivision"):
                try:
                    tick = self.resolver.position_to_tick(*item, subdivisions)
                except ValueError:
                    continue
                if tick > start["tick"]:
                    candidates.append((tick, item))
            tick, item = min(candidates)
            end = {"tick": tick, "position": _position_dict(item)}
            expanded = True
        return {
            "start": start["position"],
            "endExclusive": end["position"],
            "startTick": start["tick"],
            "endTickExclusive": end["tick"],
            "expandedToOneSubdivision": expanded,
        }

    def audio_range(self, value, subdivisions):
        start = _position(value["start"])
        end = _position(value["endExclusive"])
        start_tick = self.resolver.position_to_tick(*start, subdivisions)
        end_tick = self.resolver.position_to_tick(*end, subdivisions)
        if end_tick < start_tick:
            raise TimeAxisError("score_selection_range_invalid", "end precedes start")
        start_audio = self.resolver.tick_to_audio_seconds(start_tick)
        end_audio = self.resolver.tick_to_audio_seconds(end_tick)
        return {
            "startAudioSeconds": start_audio,
            "endAudioSeconds": end_audio,
            "durationSeconds": end_audio - start_audio,
            "startTick": start_tick,
            "endTickExclusive": end_tick,
        }

    def duration_end(self, start_value, duration, subdivisions):
        start_bar, start_beat, start_subdivision = _position(start_value)
        try:
            selected = resolve_score_selection(
                self.resolver,
                start_bar=start_bar,
                start_beat=start_beat,
                start_subdivision=start_subdivision,
                score_end_bar=0,
                score_end_beat=0,
                score_end_subdivision=0,
                duration_bars=duration["bars"],
                duration_beats=duration["beats"],
                duration_subdivisions=duration["subdivisions"],
                subdivisions_per_beat=subdivisions,
            )
        except ScoreSelectionError as error:
            raise TimeAxisError(error.code, str(error)) from error
        end = self.resolver.tick_to_position(selected.end_tick_exclusive, subdivisions)
        return {
            "endExclusive": _position_dict(end),
            "startTick": selected.start_tick,
            "endTickExclusive": selected.end_tick_exclusive,
            "meterStable": True,
        }


def _load_case(case):
    candidate = (FIXTURES / case["score_fixture"]).resolve()
    fixtures_root = FIXTURES.resolve()
    if candidate != fixtures_root and fixtures_root not in candidate.parents:
        raise ValueError("fixture path escapes tests/fixtures")
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    score = sidecar_from_dict(raw).score if "score" in raw else score_from_dict(raw)
    return ScoreResolver(
        score=score,
        audio_duration_seconds=case["audio_duration_seconds"],
        audio_seconds_at_tick_zero=case["audio_seconds_at_tick_zero"],
    )


def _execute(resolver, subdivisions, operation, arguments):
    axis = _AxisAdapter(resolver, subdivisions)
    try:
        if operation == "tickToSeconds": return resolver.tick_to_seconds(*arguments)
        if operation == "secondsToTick": return resolver.seconds_to_tick(*arguments)
        if operation == "tickToAudioSeconds": return resolver.tick_to_audio_seconds(*arguments)
        if operation == "audioSecondsToTick": return resolver.audio_seconds_to_tick(*arguments)
        if operation == "barToTick": return resolver.bar_to_tick(*arguments)
        if operation == "meterAtBar":
            numerator, denominator = resolver.meter_at_bar(*arguments)
            return {"numerator": numerator, "denominator": denominator}
        if operation == "positionToTick": return resolver.position_to_tick(*_position(arguments[0]), arguments[1])
        if operation == "tickToPosition": return _position_dict(resolver.tick_to_position(*arguments))
        if operation == "containingBarTicks":
            bar, start, end = axis.containing_bar(*arguments)
            return {"startTick": start, "endTickExclusive": end, "bar": bar}
        if operation == "containingBeatTicks":
            bar, beat, start, end = axis.containing_beat(*arguments)
            return {"startTick": start, "endTickExclusive": end, "bar": bar, "beat": beat}
        if operation == "sectionAtTick": return _section_dict(axis.section_at_tick(*arguments))
        if operation == "enumerateVisibleBoundaries": return axis.enumerate_visible(*arguments)
        if operation == "snapToBar": return axis.snap(arguments[0], 1, "bar")
        if operation == "snapToBeat": return axis.snap(arguments[0], 1, "beat")
        if operation == "snapToSubdivision": return axis.snap(arguments[0], arguments[1], "subdivision")
        if operation == "snapToVideoFrame":
            value, fps = arguments
            scaled = value * fps
            frame = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
            return {"audioSeconds": frame / fps, "tick": None, "position": None}
        if operation == "audioRangeToCanonicalSelection": return axis.selection(*arguments)
        if operation == "canonicalSelectionToAudioRange": return axis.audio_range(*arguments)
        if operation == "durationToCanonicalEnd": return axis.duration_end(*arguments)
        if operation == "monotonicity":
            values = [resolver.tick_to_seconds(value) for value in arguments[0]]
            return all(left < right for left, right in zip(values, values[1:]))
        if operation == "safeRejection":
            raise ScorePayloadError("score_tick_out_of_safe_range", "unsafe derived tick")
    except ValueError as error:
        if operation == "positionToTick" and type(error) is ValueError:
            raise RangeError(str(error)) from error
        raise
    raise AssertionError(f"unknown golden operation {operation!r}")


class ScoreTimingGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    def test_corpus_schema_and_reference_results(self):
        self.assertEqual(list(self.corpus), ["schema_version", "floating_tolerance", "cases"])
        self.assertEqual(self.corpus["schema_version"], 1)
        tolerance = self.corpus["floating_tolerance"]
        self.assertIs(type(tolerance), float)
        self.assertGreater(tolerance, 0.0)
        names = set()
        for case in self.corpus["cases"]:
            self.assertEqual(list(case), [
                "name", "score_fixture", "audio_duration_seconds",
                "audio_seconds_at_tick_zero", "subdivisions_per_beat", "queries",
            ])
            self.assertNotIn(case["name"], names)
            names.add(case["name"])
            resolver = _load_case(case)
            for query in case["queries"]:
                self.assertEqual(list(query), ["operation", "arguments", "expected"])
                with self.subTest(case=case["name"], operation=query["operation"]):
                    expected = query["expected"]
                    if isinstance(expected, dict) and "throws" in expected:
                        with self.assertRaises(Exception) as captured:
                            _execute(resolver, case["subdivisions_per_beat"], query["operation"], query["arguments"])
                        self.assertEqual(type(captured.exception).__name__, expected["throws"]["name"])
                        self.assertEqual(getattr(captured.exception, "code", None), expected["throws"]["code"])
                    else:
                        actual = _execute(resolver, case["subdivisions_per_beat"], query["operation"], query["arguments"])
                        self._assert_value(actual, expected, tolerance)

    def _assert_value(self, actual, expected, tolerance):
        if isinstance(expected, float):
            self.assertTrue(math.isfinite(actual))
            self.assertAlmostEqual(actual, expected, delta=tolerance)
        elif isinstance(expected, list):
            self.assertEqual(len(actual), len(expected))
            for left, right in zip(actual, expected): self._assert_value(left, right, tolerance)
        elif isinstance(expected, dict):
            self.assertEqual(list(actual), list(expected))
            for key in expected: self._assert_value(actual[key], expected[key], tolerance)
        else:
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
