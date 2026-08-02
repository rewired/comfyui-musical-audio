"""Pure Score-backed implementations of the shared timing contracts."""

from __future__ import annotations

from dataclasses import dataclass
import math

try:
    from ..tempo_map_contract import (
        NearestPosition,
        ScoreRelativeSpan,
        TempoMapBinding,
        UniformTimingMetrics,
    )
except ImportError:
    from tempo_map_contract import (
        NearestPosition,
        ScoreRelativeSpan,
        TempoMapBinding,
        UniformTimingMetrics,
    )

from .model import Section
from .resolver import ScoreResolver


def _require_finite_number(name: str, value: object) -> int | float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a built-in int or float")
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _require_nonnegative_integer(name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be a built-in int")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _round_half_away_from_zero(value: float) -> int:
    fractional, integral = math.modf(value)
    if fractional >= 0.5:
        integral += 1
    elif fractional <= -0.5:
        integral -= 1
    return int(integral)


@dataclass(frozen=True)
class ScoreTempoMap:
    """Immutable adapter over one canonical ScoreResolver."""

    resolver: ScoreResolver

    def __post_init__(self) -> None:
        if type(self.resolver) is not ScoreResolver:
            raise TypeError("resolver must be a ScoreResolver")

    @property
    def binding(self) -> TempoMapBinding:
        return TempoMapBinding(
            legacy_configuration=None,
            alignment_seconds=self.resolver.audio_seconds_at_tick_zero,
        )

    @property
    def supports_uniform_timing(self) -> bool:
        score = self.resolver.score
        return (
            not score.has_variable_meter
            and not score.has_midbar_meter_change
            and len(score.tempos) == 1
        )

    def _require_uniform_timing(self) -> None:
        if not self.supports_uniform_timing:
            raise ValueError(
                "ScoreTempoMap uniform timing bridge requires one tempo and "
                "constant, bar-aligned meter"
            )

    def tick_to_seconds(self, tick: int | float) -> float:
        return self.resolver.tick_to_seconds(tick)

    def seconds_to_tick(self, seconds: int | float) -> float:
        return self.resolver.seconds_to_tick(seconds)

    def bar_to_tick(self, bar: int) -> int:
        return self.resolver.bar_to_tick(bar)

    def position_to_tick(
        self,
        bar: int,
        beat: int,
        subdivision: int,
        subdivisions_per_beat: int,
    ) -> int:
        return self.resolver.position_to_tick(
            bar,
            beat,
            subdivision,
            subdivisions_per_beat,
        )

    def tick_to_position(
        self,
        tick: int | float,
        subdivisions_per_beat: int,
    ) -> tuple[int, int, int]:
        return self.resolver.tick_to_position(tick, subdivisions_per_beat)

    def meter_at_bar(self, bar: int) -> tuple[int, int]:
        return self.resolver.meter_at_bar(bar)

    def sections(self) -> tuple[Section, ...]:
        return self.resolver.sections()

    def uniform_timing_metrics(self) -> UniformTimingMetrics:
        self._require_uniform_timing()
        score = self.resolver.score
        numerator, denominator = self.resolver.meter_at_bar(1)
        seconds_per_quarter = score.tempos[0].us_per_quarter / 1_000_000
        seconds_per_beat = seconds_per_quarter * (4.0 / denominator)
        seconds_per_bar = seconds_per_beat * numerator
        return UniformTimingMetrics(
            seconds_per_tempo_pulse=seconds_per_quarter,
            seconds_per_quarter=seconds_per_quarter,
            seconds_per_beat=seconds_per_beat,
            seconds_per_bar=seconds_per_bar,
            beats_per_bar=numerator,
            beat_unit=denominator,
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
        self._require_uniform_timing()
        start_tick = self.resolver.position_to_tick(
            start_bar,
            start_beat,
            start_subdivision,
            subdivisions_per_beat,
        )
        start_score_seconds = self.resolver.tick_to_seconds(start_tick)
        for name, value in (
            ("duration_bars", duration_bars),
            ("duration_beats", duration_beats),
            ("duration_subdivisions", duration_subdivisions),
        ):
            _require_nonnegative_integer(name, value)

        numerator, _ = self.resolver.meter_at_bar(start_bar)
        subdivisions_per_bar = numerator * subdivisions_per_beat
        start_linear_subdivision = (
            ((start_bar - 1) * numerator + (start_beat - 1))
            * subdivisions_per_beat
            + start_subdivision
        )
        duration_linear_subdivisions = (
            (duration_bars * numerator + duration_beats)
            * subdivisions_per_beat
            + duration_subdivisions
        )
        end_linear_subdivision = (
            start_linear_subdivision + duration_linear_subdivisions
        )

        # Uniform-meter bridge only. Do not reuse this linear index for a
        # variable-meter ruler or snapping implementation.
        end_bar_index, end_subdivision_in_bar = divmod(
            end_linear_subdivision,
            subdivisions_per_bar,
        )
        end_beat_index, end_subdivision = divmod(
            end_subdivision_in_bar,
            subdivisions_per_beat,
        )
        end_tick = self.resolver.position_to_tick(
            end_bar_index + 1,
            end_beat_index + 1,
            end_subdivision,
            subdivisions_per_beat,
        )
        end_score_seconds = self.resolver.tick_to_seconds(end_tick)
        return ScoreRelativeSpan(
            start_beats=start_linear_subdivision / subdivisions_per_beat,
            start_seconds=start_score_seconds,
            duration_beats_total=(
                duration_linear_subdivisions / subdivisions_per_beat
            ),
            duration_seconds=end_score_seconds - start_score_seconds,
        )

    def describe_nearest_position(
        self,
        score_seconds: float,
        subdivisions_per_beat: int,
    ) -> NearestPosition:
        self._require_uniform_timing()
        value = _require_finite_number("score_seconds", score_seconds)
        if type(subdivisions_per_beat) is not int:
            raise TypeError("subdivisions_per_beat must be a built-in int")
        if subdivisions_per_beat < 1:
            raise ValueError("subdivisions_per_beat must be at least one")

        if value >= 0:
            tick = self.resolver.seconds_to_tick(value)
            bar, beat, subdivision = self.resolver.tick_to_position(
                tick,
                subdivisions_per_beat,
            )
            return NearestPosition(
                bar=bar,
                beat=beat,
                subdivision=subdivision,
                subdivisions_before_bar_one=0,
            )

        self.resolver.position_to_tick(1, 1, 0, subdivisions_per_beat)
        seconds_per_beat = self.uniform_timing_metrics().seconds_per_beat
        signed_relative_subdivisions = _round_half_away_from_zero(
            value / seconds_per_beat * subdivisions_per_beat
        )
        if signed_relative_subdivisions == 0:
            return NearestPosition(
                bar=1,
                beat=1,
                subdivision=0,
                subdivisions_before_bar_one=0,
            )
        return NearestPosition(
            bar=None,
            beat=None,
            subdivision=None,
            subdivisions_before_bar_one=-signed_relative_subdivisions,
        )


__all__ = ["ScoreTempoMap"]
