from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
import math

from .bars import (
    BarGrid,
    absolute_boundary,
    build_bar_grid,
    round_half_away_from_zero_ratio,
)
from .model import Marker, MeterEvent, Score, Section, TempoEvent


_AUDIO_BEFORE_TICK_ZERO_DIAGNOSTIC = (
    "audio ends before score tick zero; bar grid starts at tick 0"
)


@dataclass(frozen=True)
class _TempoSegment:
    start_tick: int
    start_seconds: float
    us_per_quarter: int


def _require_float(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a built-in int or float")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be finite") from None
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _require_query_real(value: object, name: str) -> int | float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a built-in int or float")
    try:
        converted = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be finite and float-representable") from None
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite and float-representable")
    return value


def _require_int(value: object, name: str, minimum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be a built-in int")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _validate_score(score: object) -> Score:
    if type(score) is not Score:
        raise TypeError("score must be a Score")
    if type(score.ticks_per_quarter) is not int or score.ticks_per_quarter <= 0:
        raise ValueError("score ticks_per_quarter must be a positive built-in int")

    if type(score.tempos) is not tuple:
        raise TypeError("score tempos must be a tuple")
    if not score.tempos:
        raise ValueError("score tempos must not be empty")
    previous_tick = -1
    previous_value: int | None = None
    for tempo in score.tempos:
        if type(tempo) is not TempoEvent:
            raise TypeError("score tempos must contain only TempoEvent values")
        if type(tempo.tick) is not int or tempo.tick < 0:
            raise ValueError("tempo ticks must be nonnegative built-in ints")
        if tempo.tick <= previous_tick:
            raise ValueError("score tempos must be strictly sorted by tick")
        if type(tempo.us_per_quarter) is not int or tempo.us_per_quarter <= 0:
            raise ValueError("tempo values must be positive built-in ints")
        if tempo.us_per_quarter == previous_value:
            raise ValueError("score tempos must not contain consecutive equal values")
        previous_tick = tempo.tick
        previous_value = tempo.us_per_quarter
    if score.tempos[0].tick != 0:
        raise ValueError("the first score tempo must begin at tick 0")

    if type(score.meters) is not tuple:
        raise TypeError("score meters must be a tuple")
    if not score.meters:
        raise ValueError("score meters must not be empty")
    previous_tick = -1
    previous_signature: tuple[int, int] | None = None
    for meter in score.meters:
        if type(meter) is not MeterEvent:
            raise TypeError("score meters must contain only MeterEvent values")
        if type(meter.tick) is not int or meter.tick < 0:
            raise ValueError("meter ticks must be nonnegative built-in ints")
        if meter.tick <= previous_tick:
            raise ValueError("score meters must be strictly sorted by tick")
        if type(meter.numerator) is not int or meter.numerator <= 0:
            raise ValueError("meter numerators must be positive built-in ints")
        if (
            type(meter.denominator) is not int
            or meter.denominator <= 0
            or meter.denominator > 64
            or meter.denominator & (meter.denominator - 1)
        ):
            raise ValueError("meter denominators must be powers of two through 64")
        signature = (meter.numerator, meter.denominator)
        if signature == previous_signature:
            raise ValueError("score meters must not contain consecutive equal signatures")
        previous_tick = meter.tick
        previous_signature = signature
    if score.meters[0].tick != 0:
        raise ValueError("the first score meter must begin at tick 0")

    if type(score.markers) is not tuple:
        raise TypeError("score markers must be a tuple")
    for marker in score.markers:
        if type(marker) is not Marker:
            raise TypeError("score markers must contain only Marker values")
        if type(marker.tick) is not int or marker.tick < 0:
            raise ValueError("marker ticks must be nonnegative built-in ints")
        if type(marker.name) is not str:
            raise TypeError("marker names must be built-in strings")
    if score.markers != tuple(sorted(score.markers, key=lambda item: (item.tick, item.name))):
        raise ValueError("score markers must be canonically ordered")

    if type(score.sections) is not tuple:
        raise TypeError("score sections must be a tuple")
    for section in score.sections:
        if type(section) is not Section:
            raise TypeError("score sections must contain only Section values")
        if type(section.name) is not str:
            raise TypeError("Section names must be built-in strings")
        if type(section.start_tick) is not int or section.start_tick < 0:
            raise ValueError("Section starts must be nonnegative built-in ints")
        if type(section.end_tick_exclusive) is not int or section.end_tick_exclusive < 0:
            raise ValueError("Section ends must be nonnegative built-in ints")
        if section.end_tick_exclusive < section.start_tick:
            raise ValueError("Section end must not precede its start")
        if type(section.bar_aligned) is not bool:
            raise TypeError("Section bar_aligned must be a built-in bool")
        if section.confidence is not None:
            _require_float(section.confidence, "Section confidence")
    if score.sections != tuple(
        sorted(
            score.sections,
            key=lambda item: (item.start_tick, item.end_tick_exclusive, item.name),
        )
    ):
        raise ValueError("score sections must be canonically ordered")

    if type(score.source) is not str:
        raise TypeError("score source must be a built-in string")
    if score.source not in ("json", "midi", "analyzed", "constant"):
        raise ValueError("score source is invalid")
    if type(score.meter_estimated) is not bool:
        raise TypeError("score meter_estimated must be a built-in bool")
    if type(score.has_variable_meter) is not bool:
        raise TypeError("score has_variable_meter must be a built-in bool")
    if type(score.has_midbar_meter_change) is not bool:
        raise TypeError("score has_midbar_meter_change must be a built-in bool")
    return score


def _build_tempo_segments(score: Score) -> tuple[_TempoSegment, ...]:
    segments: list[_TempoSegment] = []
    start_seconds = 0.0
    previous: TempoEvent | None = None
    for tempo in score.tempos:
        if previous is not None:
            try:
                start_seconds += (
                    (tempo.tick - previous.tick)
                    * previous.us_per_quarter
                    / (score.ticks_per_quarter * 1_000_000)
                )
            except OverflowError:
                raise ValueError("tempo segment seconds must be finite") from None
            if not math.isfinite(start_seconds):
                raise ValueError("tempo segment seconds must be finite")
            if start_seconds <= segments[-1].start_seconds:
                raise ValueError("tempo segment seconds must be strictly increasing")
        segments.append(
            _TempoSegment(
                start_tick=tempo.tick,
                start_seconds=float(start_seconds),
                us_per_quarter=tempo.us_per_quarter,
            )
        )
        previous = tempo
    return tuple(segments)


def _finite_result(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} result must be finite")
    return float(value)


@dataclass(frozen=True)
class ScoreResolver:
    score: Score
    audio_duration_seconds: float
    audio_seconds_at_tick_zero: float
    _tempo_segments: tuple[_TempoSegment, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _tempo_start_ticks: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _tempo_start_seconds: tuple[float, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _bar_grid: BarGrid = field(init=False, repr=False, compare=False)
    _diagnostics: tuple[str, ...] = field(init=False, repr=False, compare=False)
    _final_meter: MeterEvent = field(init=False, repr=False, compare=False)
    _final_meter_tick: int = field(init=False, repr=False, compare=False)
    _final_meter_anchor_bar: int = field(init=False, repr=False, compare=False)
    _cached_bar_count: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.score) is not Score:
            raise TypeError("score must be a Score")
        duration = _require_float(self.audio_duration_seconds, "audio_duration_seconds")
        if duration < 0.0:
            raise ValueError("audio_duration_seconds must be nonnegative")
        alignment = _require_float(
            self.audio_seconds_at_tick_zero,
            "audio_seconds_at_tick_zero",
        )
        object.__setattr__(self, "audio_duration_seconds", duration)
        object.__setattr__(self, "audio_seconds_at_tick_zero", alignment)
        score = _validate_score(self.score)

        segments = _build_tempo_segments(score)
        start_ticks = tuple(segment.start_tick for segment in segments)
        start_seconds = tuple(segment.start_seconds for segment in segments)
        object.__setattr__(self, "_tempo_segments", segments)
        object.__setattr__(self, "_tempo_start_ticks", start_ticks)
        object.__setattr__(self, "_tempo_start_seconds", start_seconds)

        audio_end_score_seconds = duration - alignment
        if not math.isfinite(audio_end_score_seconds):
            raise ValueError("audio end in Score seconds must be finite")
        raw_audio_end_tick = self.seconds_to_tick(audio_end_score_seconds)
        audio_through_tick = math.ceil(raw_audio_end_tick)
        through_tick = max(0, audio_through_tick, score.meters[-1].tick)
        bar_grid = build_bar_grid(
            ticks_per_quarter=score.ticks_per_quarter,
            meters=score.meters,
            through_tick=through_tick,
        )
        if score.has_variable_meter != bar_grid.has_variable_meter:
            raise ValueError("score has_variable_meter does not match the BarGrid")
        if score.has_midbar_meter_change != bar_grid.has_midbar_meter_change:
            raise ValueError("score has_midbar_meter_change does not match the BarGrid")
        if bar_grid.boundaries[0] != 0 or bar_grid.boundaries[-1] <= through_tick:
            raise ValueError("constructed BarGrid does not cover through_tick")
        if len(bar_grid.meters_by_bar) != len(bar_grid.boundaries) - 1:
            raise ValueError("constructed BarGrid has inconsistent table lengths")

        final_meter = score.meters[-1]
        anchor_index = bisect_left(bar_grid.boundaries, final_meter.tick)
        if (
            anchor_index >= len(bar_grid.boundaries)
            or bar_grid.boundaries[anchor_index] != final_meter.tick
            or anchor_index >= len(bar_grid.meters_by_bar)
        ):
            raise ValueError("final meter event is not a cached bar boundary")

        diagnostics = []
        if audio_end_score_seconds < 0.0:
            diagnostics.append(_AUDIO_BEFORE_TICK_ZERO_DIAGNOSTIC)
        diagnostics.extend(bar_grid.diagnostics)
        object.__setattr__(self, "_bar_grid", bar_grid)
        object.__setattr__(self, "_diagnostics", tuple(diagnostics))
        object.__setattr__(self, "_final_meter", final_meter)
        object.__setattr__(self, "_final_meter_tick", final_meter.tick)
        object.__setattr__(self, "_final_meter_anchor_bar", anchor_index + 1)
        object.__setattr__(self, "_cached_bar_count", len(bar_grid.meters_by_bar))

    @property
    def bar_grid(self) -> BarGrid:
        return self._bar_grid

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self._diagnostics

    def tick_to_seconds(self, tick: int | float) -> float:
        value = _require_query_real(tick, "tick")
        index = max(0, bisect_right(self._tempo_start_ticks, value) - 1)
        segment = self._tempo_segments[index]
        try:
            result = segment.start_seconds + (
                (value - segment.start_tick)
                * segment.us_per_quarter
                / (self.score.ticks_per_quarter * 1_000_000)
            )
        except OverflowError:
            raise ValueError("tick_to_seconds result must be finite") from None
        return _finite_result(result, "tick_to_seconds")

    def seconds_to_tick(self, seconds: int | float) -> float:
        value = _require_query_real(seconds, "seconds")
        index = max(0, bisect_right(self._tempo_start_seconds, value) - 1)
        segment = self._tempo_segments[index]
        try:
            result = segment.start_tick + (
                (value - segment.start_seconds)
                * self.score.ticks_per_quarter
                * 1_000_000
                / segment.us_per_quarter
            )
        except OverflowError:
            raise ValueError("seconds_to_tick result must be finite") from None
        return _finite_result(result, "seconds_to_tick")

    def tick_to_audio_seconds(self, tick: int | float) -> float:
        return _finite_result(
            self.tick_to_seconds(tick) + self.audio_seconds_at_tick_zero,
            "tick_to_audio_seconds",
        )

    def audio_seconds_to_tick(self, audio_seconds: int | float) -> float:
        value = _require_query_real(audio_seconds, "audio_seconds")
        score_seconds = value - self.audio_seconds_at_tick_zero
        if not math.isfinite(score_seconds):
            raise ValueError("audio_seconds relative to alignment must be finite")
        return self.seconds_to_tick(score_seconds)

    def bar_to_tick(self, bar: int) -> int:
        value = _require_int(bar, "bar", 1)
        if value <= self._cached_bar_count:
            return self._bar_grid.boundaries[value - 1]
        return absolute_boundary(
            anchor_tick=self._final_meter_tick,
            bar_index=value - self._final_meter_anchor_bar,
            meter=self._final_meter,
            ticks_per_quarter=self.score.ticks_per_quarter,
        )

    def bar_length_ticks(self, bar: int) -> int:
        value = _require_int(bar, "bar", 1)
        return self.bar_to_tick(value + 1) - self.bar_to_tick(value)

    def meter_at_bar(self, bar: int) -> tuple[int, int]:
        value = _require_int(bar, "bar", 1)
        meter = (
            self._bar_grid.meters_by_bar[value - 1]
            if value <= self._cached_bar_count
            else self._final_meter
        )
        return meter.numerator, meter.denominator

    def position_to_tick(
        self,
        bar: int,
        beat: int,
        subdivision: int,
        subdivisions_per_beat: int,
    ) -> int:
        bar_value = _require_int(bar, "bar", 1)
        beat_value = _require_int(beat, "beat", 1)
        subdivision_value = _require_int(subdivision, "subdivision", 0)
        subdivisions_value = _require_int(
            subdivisions_per_beat,
            "subdivisions_per_beat",
            1,
        )
        numerator, denominator = self.meter_at_bar(bar_value)
        if beat_value > numerator:
            raise ValueError("beat exceeds the meter numerator")
        if subdivision_value >= subdivisions_value:
            raise ValueError("subdivision must be below subdivisions_per_beat")
        if subdivisions_value * denominator > 4 * self.score.ticks_per_quarter:
            raise ValueError("subdivision grid is finer than integer tick resolution")

        bar_start = self.bar_to_tick(bar_value)
        bar_end = self.bar_to_tick(bar_value + 1)
        beat_start = bar_start + round_half_away_from_zero_ratio(
            (beat_value - 1) * 4 * self.score.ticks_per_quarter,
            denominator,
        )
        subdivision_offset = round_half_away_from_zero_ratio(
            subdivision_value * 4 * self.score.ticks_per_quarter,
            denominator * subdivisions_value,
        )
        position_tick = beat_start + subdivision_offset
        if position_tick < bar_start or position_tick >= bar_end:
            raise ValueError("position does not lie within the actual bar boundaries")
        return position_tick

    def _bar_containing_tick(self, tick: int | float) -> int:
        if tick < self._bar_grid.boundaries[-1]:
            return bisect_right(self._bar_grid.boundaries, tick)

        delta = math.floor(tick) - self._final_meter_tick
        width_numerator = (
            self._final_meter.numerator * 4 * self.score.ticks_per_quarter
        )
        denominator = self._final_meter.denominator
        offset = (((2 * delta + 1) * denominator) - 1) // (2 * width_numerator)
        containing_bar = self._final_meter_anchor_bar + offset
        if tick < self.bar_to_tick(containing_bar):
            containing_bar -= 1
        elif tick >= self.bar_to_tick(containing_bar + 1):
            containing_bar += 1
        if not (
            self.bar_to_tick(containing_bar)
            <= tick
            < self.bar_to_tick(containing_bar + 1)
        ):
            raise ValueError("analytical tick-to-bar lookup was inconsistent")
        return containing_bar

    def _estimate_index(self, value: int | float, numerator: int, denominator: int) -> int:
        if type(value) is int:
            return value * numerator // denominator
        return math.floor(value * numerator / denominator)

    def tick_to_position(
        self,
        tick: int | float,
        subdivisions_per_beat: int,
    ) -> tuple[int, int, int]:
        value = _require_query_real(tick, "tick")
        if value < 0:
            raise ValueError("tick must be nonnegative for musical positions")
        subdivisions = _require_int(
            subdivisions_per_beat,
            "subdivisions_per_beat",
            1,
        )
        bar = self._bar_containing_tick(value)
        numerator, denominator = self.meter_at_bar(bar)
        if subdivisions * denominator > 4 * self.score.ticks_per_quarter:
            raise ValueError("subdivision grid is finer than integer tick resolution")
        bar_start = self.bar_to_tick(bar)
        beat_floor = self._estimate_index(
            value - bar_start,
            denominator,
            4 * self.score.ticks_per_quarter,
        )

        candidates: dict[tuple[int, int, int], int] = {}
        beat_indexes = {0, numerator - 1}
        beat_indexes.update(range(beat_floor - 2, beat_floor + 3))
        for beat_index in beat_indexes:
            beat = beat_index + 1
            if beat < 1 or beat > numerator:
                continue
            try:
                beat_start = self.position_to_tick(bar, beat, 0, subdivisions)
            except ValueError:
                continue
            subdivision_floor = self._estimate_index(
                value - beat_start,
                denominator * subdivisions,
                4 * self.score.ticks_per_quarter,
            )
            subdivision_indexes = {0, subdivisions - 1}
            subdivision_indexes.update(
                range(subdivision_floor - 2, subdivision_floor + 3)
            )
            for subdivision in subdivision_indexes:
                if subdivision < 0 or subdivision >= subdivisions:
                    continue
                position = (bar, beat, subdivision)
                try:
                    candidates[position] = self.position_to_tick(
                        bar,
                        beat,
                        subdivision,
                        subdivisions,
                    )
                except ValueError:
                    continue

        following = (bar + 1, 1, 0)
        candidates[following] = self.position_to_tick(
            following[0],
            following[1],
            following[2],
            subdivisions,
        )
        if not candidates:
            raise ValueError("no valid canonical position exists for this grid")
        return min(
            candidates,
            key=lambda position: (
                abs(candidates[position] - value),
                -candidates[position],
            ),
        )

    def sections(self) -> tuple[Section, ...]:
        return self.score.sections
