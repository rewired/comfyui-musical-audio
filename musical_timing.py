"""Pure calculations for converting musical positions into time and frames."""

from __future__ import annotations

from dataclasses import dataclass
import math

try:
    from .tempo_map_contract import (
        LegacyTimingConfiguration,
        NearestPosition,
        ScoreRelativeSpan,
        TempoMap,
        TempoMapBinding,
        TempoUnit,
        UniformTimingBridge,
        UniformTimingMetrics,
    )
except ImportError:
    from tempo_map_contract import (
        LegacyTimingConfiguration,
        NearestPosition,
        ScoreRelativeSpan,
        TempoMap,
        TempoMapBinding,
        TempoUnit,
        UniformTimingBridge,
        UniformTimingMetrics,
    )

_TEMPO_UNIT_IN_QUARTERS: dict[str, float] = {
    "Quarter": 1.0,
    "Eighth": 0.5,
    "Dotted Quarter": 1.5,
}
_SYNTHETIC_TICKS_PER_QUARTER = 960


def round_half_away_from_zero(value: float) -> int:
    """Round to the nearest integer, with exact half ties away from zero."""
    fractional, integral = math.modf(value)
    if fractional >= 0.5:
        integral += 1
    elif fractional <= -0.5:
        integral -= 1
    return int(integral)


@dataclass(frozen=True)
class MusicalTimingResult:
    """Calculated musical timing values and discrete video frame metadata."""

    seconds_per_tempo_pulse: float
    seconds_per_quarter: float
    seconds_per_beat: float
    seconds_per_bar: float
    start_beats: float
    start_seconds: float
    duration_beats_total: float
    duration_seconds: float
    end_seconds: float
    frames_per_beat: float
    frames_per_bar: float
    start_frame: int
    frame_count: int


def _require_finite_number(name: str, value: object) -> None:
    """Require a finite built-in int or float, excluding booleans."""
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be an int or float")
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _require_integer(name: str, value: object) -> None:
    """Require a built-in integer, excluding booleans and fractional values."""
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")


def _round_half_away_from_zero_ratio(numerator: int, denominator: int) -> int:
    """Round an exact integer ratio with half ties away from zero."""
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient


def _finite_result(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} result must be finite")
    return float(value)


@dataclass(frozen=True)
class ConstantTempoMap:
    """Uniform legacy timing plus a complete synthetic TempoMap surface."""

    bpm: float
    tempo_unit: TempoUnit
    beats_per_bar: int
    beat_unit: int

    def __post_init__(self) -> None:
        if type(self.tempo_unit) is not str:
            raise TypeError("tempo_unit must be a str")
        if self.tempo_unit not in _TEMPO_UNIT_IN_QUARTERS:
            supported = ", ".join(_TEMPO_UNIT_IN_QUARTERS)
            raise ValueError(
                f"Unsupported tempo_unit {self.tempo_unit!r}; expected one of: "
                f"{supported}"
            )
        _require_finite_number("bpm", self.bpm)
        if self.bpm <= 0:
            raise ValueError("bpm must be greater than zero")
        _require_integer("beats_per_bar", self.beats_per_bar)
        _require_integer("beat_unit", self.beat_unit)
        if self.beats_per_bar < 1:
            raise ValueError("beats_per_bar must be at least one")
        if self.beat_unit <= 0:
            raise ValueError("beat_unit must be greater than zero")

    @property
    def binding(self) -> TempoMapBinding:
        return TempoMapBinding(
            legacy_configuration=LegacyTimingConfiguration(
                bpm=self.bpm,
                tempo_unit=self.tempo_unit,
                beats_per_bar=self.beats_per_bar,
                beat_unit=self.beat_unit,
            ),
            alignment_seconds=None,
        )

    @property
    def supports_uniform_timing(self) -> bool:
        return True

    def uniform_timing_metrics(self) -> UniformTimingMetrics:
        seconds_per_tempo_pulse = 60.0 / self.bpm
        tempo_unit_in_quarters = _TEMPO_UNIT_IN_QUARTERS[self.tempo_unit]
        seconds_per_quarter = seconds_per_tempo_pulse / tempo_unit_in_quarters
        seconds_per_beat = seconds_per_quarter * (4.0 / self.beat_unit)
        seconds_per_bar = seconds_per_beat * self.beats_per_bar
        return UniformTimingMetrics(
            seconds_per_tempo_pulse=seconds_per_tempo_pulse,
            seconds_per_quarter=seconds_per_quarter,
            seconds_per_beat=seconds_per_beat,
            seconds_per_bar=seconds_per_bar,
            beats_per_bar=self.beats_per_bar,
            beat_unit=self.beat_unit,
        )

    def resolve_score_relative_span(
        self,
        *,
        start_bar: int,
        start_beat: int,
        start_subdivision: int,
        duration_bars: int,
        duration_beats: int,
        duration_subdivisions: int,
        subdivisions_per_beat: int,
    ) -> ScoreRelativeSpan:
        integer_values = {
            "start_bar": start_bar,
            "start_beat": start_beat,
            "start_subdivision": start_subdivision,
            "duration_bars": duration_bars,
            "duration_beats": duration_beats,
            "duration_subdivisions": duration_subdivisions,
            "subdivisions_per_beat": subdivisions_per_beat,
        }
        for name, value in integer_values.items():
            _require_integer(name, value)
        if start_bar < 1:
            raise ValueError("start_bar must be at least one")
        if not 1 <= start_beat <= self.beats_per_bar:
            raise ValueError("start_beat must be between one and beats_per_bar")
        if start_subdivision < 0:
            raise ValueError("start_subdivision must be non-negative")
        if subdivisions_per_beat < 1:
            raise ValueError("subdivisions_per_beat must be at least one")
        for name, value in (
            ("duration_bars", duration_bars),
            ("duration_beats", duration_beats),
            ("duration_subdivisions", duration_subdivisions),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

        seconds_per_beat = self.uniform_timing_metrics().seconds_per_beat
        start_beats = (
            (start_bar - 1) * self.beats_per_bar
            + (start_beat - 1)
            + start_subdivision / subdivisions_per_beat
        )
        start_seconds = start_beats * seconds_per_beat
        duration_beats_total = (
            duration_bars * self.beats_per_bar
            + duration_beats
            + duration_subdivisions / subdivisions_per_beat
        )
        duration_seconds = duration_beats_total * seconds_per_beat
        return ScoreRelativeSpan(
            start_beats=start_beats,
            start_seconds=start_seconds,
            duration_beats_total=duration_beats_total,
            duration_seconds=duration_seconds,
        )

    def describe_nearest_position(
        self,
        score_seconds: float,
        subdivisions_per_beat: int,
    ) -> NearestPosition:
        _require_finite_number("score_seconds", score_seconds)
        _require_integer("subdivisions_per_beat", subdivisions_per_beat)
        if subdivisions_per_beat < 1:
            raise ValueError("subdivisions_per_beat must be at least one")
        seconds_per_beat = self.uniform_timing_metrics().seconds_per_beat
        signed_relative_subdivisions = round_half_away_from_zero(
            score_seconds / seconds_per_beat * subdivisions_per_beat
        )
        if signed_relative_subdivisions < 0:
            return NearestPosition(
                bar=None,
                beat=None,
                subdivision=None,
                subdivisions_before_bar_one=-signed_relative_subdivisions,
            )
        subdivisions_per_bar = self.beats_per_bar * subdivisions_per_beat
        bar_index, subdivision_in_bar = divmod(
            signed_relative_subdivisions,
            subdivisions_per_bar,
        )
        beat_index, subdivision = divmod(
            subdivision_in_bar,
            subdivisions_per_beat,
        )
        return NearestPosition(
            bar=bar_index + 1,
            beat=beat_index + 1,
            subdivision=subdivision,
            subdivisions_before_bar_one=0,
        )

    def tick_to_seconds(self, tick: int | float) -> float:
        _require_finite_number("tick", tick)
        seconds_per_quarter = self.uniform_timing_metrics().seconds_per_quarter
        try:
            result = tick * seconds_per_quarter / _SYNTHETIC_TICKS_PER_QUARTER
        except OverflowError:
            raise ValueError("tick_to_seconds result must be finite") from None
        return _finite_result(result, "tick_to_seconds")

    def seconds_to_tick(self, seconds: int | float) -> float:
        _require_finite_number("seconds", seconds)
        seconds_per_quarter = self.uniform_timing_metrics().seconds_per_quarter
        try:
            result = seconds / seconds_per_quarter * _SYNTHETIC_TICKS_PER_QUARTER
        except OverflowError:
            raise ValueError("seconds_to_tick result must be finite") from None
        return _finite_result(result, "seconds_to_tick")

    def bar_to_tick(self, bar: int) -> int:
        _require_integer("bar", bar)
        if bar < 1:
            raise ValueError("bar must be at least one")
        return _round_half_away_from_zero_ratio(
            (bar - 1)
            * self.beats_per_bar
            * 4
            * _SYNTHETIC_TICKS_PER_QUARTER,
            self.beat_unit,
        )

    def position_to_tick(
        self,
        bar: int,
        beat: int,
        subdivision: int,
        subdivisions_per_beat: int,
    ) -> int:
        for name, value in (
            ("bar", bar),
            ("beat", beat),
            ("subdivision", subdivision),
            ("subdivisions_per_beat", subdivisions_per_beat),
        ):
            _require_integer(name, value)
        if bar < 1:
            raise ValueError("bar must be at least one")
        if beat < 1:
            raise ValueError("beat must be at least one")
        if beat > self.beats_per_bar:
            raise ValueError("beat exceeds the meter numerator")
        if subdivision < 0:
            raise ValueError("subdivision must be at least 0")
        if subdivisions_per_beat < 1:
            raise ValueError("subdivisions_per_beat must be at least one")
        if subdivision >= subdivisions_per_beat:
            raise ValueError("subdivision must be below subdivisions_per_beat")
        if (
            subdivisions_per_beat * self.beat_unit
            > 4 * _SYNTHETIC_TICKS_PER_QUARTER
        ):
            raise ValueError("subdivision grid is finer than integer tick resolution")

        bar_start = self.bar_to_tick(bar)
        bar_end = self.bar_to_tick(bar + 1)
        beat_start = bar_start + _round_half_away_from_zero_ratio(
            (beat - 1) * 4 * _SYNTHETIC_TICKS_PER_QUARTER,
            self.beat_unit,
        )
        subdivision_offset = _round_half_away_from_zero_ratio(
            subdivision * 4 * _SYNTHETIC_TICKS_PER_QUARTER,
            self.beat_unit * subdivisions_per_beat,
        )
        position_tick = beat_start + subdivision_offset
        if position_tick < bar_start or position_tick >= bar_end:
            raise ValueError("position does not lie within the actual bar boundaries")
        return position_tick

    def _bar_containing_tick(self, tick: int | float) -> int:
        width_numerator = (
            self.beats_per_bar * 4 * _SYNTHETIC_TICKS_PER_QUARTER
        )
        if type(tick) is int:
            bar = tick * self.beat_unit // width_numerator + 1
        else:
            bar = math.floor(tick * self.beat_unit / width_numerator) + 1
        bar = max(1, bar)
        if tick < self.bar_to_tick(bar):
            bar -= 1
        elif tick >= self.bar_to_tick(bar + 1):
            bar += 1
        if bar < 1 or not self.bar_to_tick(bar) <= tick < self.bar_to_tick(bar + 1):
            raise ValueError("analytical tick-to-bar lookup was inconsistent")
        return bar

    def _estimate_index(
        self,
        value: int | float,
        numerator: int,
        denominator: int,
    ) -> int:
        if type(value) is int:
            return value * numerator // denominator
        return math.floor(value * numerator / denominator)

    def tick_to_position(
        self,
        tick: int | float,
        subdivisions_per_beat: int,
    ) -> tuple[int, int, int]:
        _require_finite_number("tick", tick)
        if tick < 0:
            raise ValueError("tick must be nonnegative for musical positions")
        _require_integer("subdivisions_per_beat", subdivisions_per_beat)
        if subdivisions_per_beat < 1:
            raise ValueError("subdivisions_per_beat must be at least one")
        if (
            subdivisions_per_beat * self.beat_unit
            > 4 * _SYNTHETIC_TICKS_PER_QUARTER
        ):
            raise ValueError("subdivision grid is finer than integer tick resolution")

        bar = self._bar_containing_tick(tick)
        bar_start = self.bar_to_tick(bar)
        beat_floor = self._estimate_index(
            tick - bar_start,
            self.beat_unit,
            4 * _SYNTHETIC_TICKS_PER_QUARTER,
        )
        candidates: dict[tuple[int, int, int], int] = {}
        beat_indexes = {0, self.beats_per_bar - 1}
        beat_indexes.update(range(beat_floor - 2, beat_floor + 3))
        for beat_index in beat_indexes:
            beat = beat_index + 1
            if beat < 1 or beat > self.beats_per_bar:
                continue
            beat_start = self.position_to_tick(
                bar,
                beat,
                0,
                subdivisions_per_beat,
            )
            subdivision_floor = self._estimate_index(
                tick - beat_start,
                self.beat_unit * subdivisions_per_beat,
                4 * _SYNTHETIC_TICKS_PER_QUARTER,
            )
            subdivision_indexes = {0, subdivisions_per_beat - 1}
            subdivision_indexes.update(
                range(subdivision_floor - 2, subdivision_floor + 3)
            )
            for subdivision in subdivision_indexes:
                if subdivision < 0 or subdivision >= subdivisions_per_beat:
                    continue
                position = (bar, beat, subdivision)
                candidates[position] = self.position_to_tick(
                    bar,
                    beat,
                    subdivision,
                    subdivisions_per_beat,
                )
        following = (bar + 1, 1, 0)
        candidates[following] = self.position_to_tick(
            following[0],
            following[1],
            following[2],
            subdivisions_per_beat,
        )
        return min(
            candidates,
            key=lambda position: (
                abs(candidates[position] - tick),
                -candidates[position],
            ),
        )

    def meter_at_bar(self, bar: int) -> tuple[int, int]:
        _require_integer("bar", bar)
        if bar < 1:
            raise ValueError("bar must be at least one")
        return self.beats_per_bar, self.beat_unit

    def sections(self) -> tuple[object, ...]:
        return ()


def calculate_musical_timing(
    *,
    bpm: float,
    beats_per_bar: int,
    beat_unit: int,
    fps: float,
    subdivisions_per_beat: int,
    tempo_unit: TempoUnit = "Quarter",
    downbeat_offset: float = 0.0,
    start_bar: int = 1,
    start_beat: int = 1,
    start_subdivision: int = 0,
    duration_bars: int = 0,
    duration_beats: int = 0,
    duration_subdivisions: int = 0,
    tempo_map: UniformTimingBridge | None = None,
) -> MusicalTimingResult:
    """Calculate timing and frame metadata for a musical start and duration."""
    if type(tempo_unit) is not str:
        raise TypeError("tempo_unit must be a str")
    if tempo_unit not in _TEMPO_UNIT_IN_QUARTERS:
        supported = ", ".join(_TEMPO_UNIT_IN_QUARTERS)
        raise ValueError(
            f"Unsupported tempo_unit {tempo_unit!r}; expected one of: {supported}"
        )

    _require_finite_number("bpm", bpm)
    _require_finite_number("fps", fps)
    _require_finite_number("downbeat_offset", downbeat_offset)

    if bpm <= 0:
        raise ValueError("bpm must be greater than zero")
    if fps <= 0:
        raise ValueError("fps must be greater than zero")

    integer_values = {
        "beats_per_bar": beats_per_bar,
        "beat_unit": beat_unit,
        "subdivisions_per_beat": subdivisions_per_beat,
        "start_bar": start_bar,
        "start_beat": start_beat,
        "start_subdivision": start_subdivision,
        "duration_bars": duration_bars,
        "duration_beats": duration_beats,
        "duration_subdivisions": duration_subdivisions,
    }
    for name, value in integer_values.items():
        _require_integer(name, value)

    if beats_per_bar < 1:
        raise ValueError("beats_per_bar must be at least one")
    if beat_unit <= 0:
        raise ValueError("beat_unit must be greater than zero")
    if subdivisions_per_beat < 1:
        raise ValueError("subdivisions_per_beat must be at least one")
    if start_bar < 1:
        raise ValueError("start_bar must be at least one")
    if start_beat < 1:
        raise ValueError("start_beat must be between one and beats_per_bar")
    if start_subdivision < 0:
        raise ValueError("start_subdivision must be non-negative")

    duration_values = {
        "duration_bars": duration_bars,
        "duration_beats": duration_beats,
        "duration_subdivisions": duration_subdivisions,
    }
    for name, value in duration_values.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")

    active_map = (
        ConstantTempoMap(
            bpm=bpm,
            tempo_unit=tempo_unit,
            beats_per_bar=beats_per_bar,
            beat_unit=beat_unit,
        )
        if tempo_map is None
        else tempo_map
    )
    binding = active_map.binding
    if binding.legacy_configuration is not None:
        effective_configuration = LegacyTimingConfiguration(
            bpm=bpm,
            tempo_unit=tempo_unit,
            beats_per_bar=beats_per_bar,
            beat_unit=beat_unit,
        )
        if binding.legacy_configuration != effective_configuration:
            raise ValueError(
                "ConstantTempoMap configuration conflicts with the effective "
                "legacy timing arguments"
            )
        if start_beat > binding.legacy_configuration.beats_per_bar:
            raise ValueError("start_beat must be between one and beats_per_bar")
    elif binding.alignment_seconds != downbeat_offset:
        raise ValueError(
            "ScoreTempoMap resolver alignment is stale or inconsistent with the "
            "effective downbeat_offset"
        )

    if not active_map.supports_uniform_timing:
        raise ValueError(
            "tempo map does not support the uniform timing required by "
            "MusicalTimingResult"
        )

    metrics = active_map.uniform_timing_metrics()
    span = active_map.resolve_score_relative_span(
        start_bar=start_bar,
        start_beat=start_beat,
        start_subdivision=start_subdivision,
        duration_bars=duration_bars,
        duration_beats=duration_beats,
        duration_subdivisions=duration_subdivisions,
        subdivisions_per_beat=subdivisions_per_beat,
    )
    start_seconds = downbeat_offset + span.start_seconds
    duration_seconds = span.duration_seconds
    end_seconds = start_seconds + duration_seconds

    frames_per_beat = fps * metrics.seconds_per_beat
    frames_per_bar = fps * metrics.seconds_per_bar
    start_frame = round_half_away_from_zero(start_seconds * fps)
    frame_count = round_half_away_from_zero(duration_seconds * fps)

    return MusicalTimingResult(
        seconds_per_tempo_pulse=metrics.seconds_per_tempo_pulse,
        seconds_per_quarter=metrics.seconds_per_quarter,
        seconds_per_beat=metrics.seconds_per_beat,
        seconds_per_bar=metrics.seconds_per_bar,
        start_beats=span.start_beats,
        start_seconds=start_seconds,
        duration_beats_total=span.duration_beats_total,
        duration_seconds=duration_seconds,
        end_seconds=end_seconds,
        frames_per_beat=frames_per_beat,
        frames_per_bar=frames_per_bar,
        start_frame=start_frame,
        frame_count=frame_count,
    )


__all__ = [
    "ConstantTempoMap",
    "LegacyTimingConfiguration",
    "MusicalTimingResult",
    "NearestPosition",
    "ScoreRelativeSpan",
    "TempoMap",
    "TempoMapBinding",
    "TempoUnit",
    "UniformTimingBridge",
    "UniformTimingMetrics",
    "calculate_musical_timing",
    "round_half_away_from_zero",
]
