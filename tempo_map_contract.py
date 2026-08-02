"""Shared immutable values and structural contracts for timing adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


TempoUnit = Literal["Quarter", "Eighth", "Dotted Quarter"]


@dataclass(frozen=True)
class LegacyTimingConfiguration:
    """The exact legacy timing configuration bound to a constant map."""

    bpm: float
    tempo_unit: TempoUnit
    beats_per_bar: int
    beat_unit: int


@dataclass(frozen=True)
class TempoMapBinding:
    """Identify exactly one source of timing-map runtime authority."""

    legacy_configuration: LegacyTimingConfiguration | None
    alignment_seconds: float | None

    def __post_init__(self) -> None:
        has_legacy = self.legacy_configuration is not None
        has_alignment = self.alignment_seconds is not None
        if has_legacy == has_alignment:
            raise ValueError(
                "TempoMapBinding internal contract violation: exactly one binding "
                "kind must be active"
            )
        if has_legacy and type(self.legacy_configuration) is not LegacyTimingConfiguration:
            raise TypeError(
                "TempoMapBinding internal contract violation: legacy_configuration "
                "must be a LegacyTimingConfiguration"
            )
        if has_alignment:
            alignment = self.alignment_seconds
            if type(alignment) not in (int, float):
                raise TypeError(
                    "TempoMapBinding internal contract violation: alignment_seconds "
                    "must be a built-in int or float"
                )
            if type(alignment) is float and (
                alignment != alignment
                or alignment in (float("inf"), float("-inf"))
            ):
                raise ValueError(
                    "TempoMapBinding internal contract violation: alignment_seconds "
                    "must be finite"
                )


@dataclass(frozen=True)
class UniformTimingMetrics:
    """Globally constant timing metrics required by the legacy result shape."""

    seconds_per_tempo_pulse: float
    seconds_per_quarter: float
    seconds_per_beat: float
    seconds_per_bar: float
    beats_per_bar: int
    beat_unit: int


@dataclass(frozen=True)
class ScoreRelativeSpan:
    """A requested musical span expressed relative to Score tick zero."""

    start_beats: float
    start_seconds: float
    duration_beats_total: float
    duration_seconds: float


@dataclass(frozen=True)
class NearestPosition:
    """A canonical position or a positive distance before Bar 1."""

    bar: int | None
    beat: int | None
    subdivision: int | None
    subdivisions_before_bar_one: int

    def __post_init__(self) -> None:
        before = self.subdivisions_before_bar_one
        if type(before) is not int:
            raise TypeError(
                "subdivisions_before_bar_one must be a built-in int"
            )
        all_none = self.bar is None and self.beat is None and self.subdivision is None
        any_none = self.bar is None or self.beat is None or self.subdivision is None
        if all_none:
            if before <= 0:
                raise ValueError(
                    "a Pre-Bar-1 position requires a positive subdivision count"
                )
            return
        if any_none:
            raise ValueError("NearestPosition variants must not be mixed")
        if before != 0:
            raise ValueError(
                "a canonical position requires subdivisions_before_bar_one to be zero"
            )
        if type(self.bar) is not int or self.bar <= 0:
            raise ValueError("bar must be a positive built-in int")
        if type(self.beat) is not int or self.beat <= 0:
            raise ValueError("beat must be a positive built-in int")
        if type(self.subdivision) is not int or self.subdivision < 0:
            raise ValueError("subdivision must be a nonnegative built-in int")


class TempoMap(Protocol):
    """Complete source-independent timing profile contract."""

    def tick_to_seconds(self, tick: int | float) -> float: ...

    def seconds_to_tick(self, seconds: int | float) -> float: ...

    def bar_to_tick(self, bar: int) -> int: ...

    def position_to_tick(
        self,
        bar: int,
        beat: int,
        subdivision: int,
        subdivisions_per_beat: int,
    ) -> int: ...

    def tick_to_position(
        self,
        tick: int | float,
        subdivisions_per_beat: int,
    ) -> tuple[int, int, int]: ...

    def meter_at_bar(self, bar: int) -> tuple[int, int]: ...

    def sections(self) -> tuple[object, ...]: ...


class UniformTimingBridge(Protocol):
    """Constant-shaped bridge used by the pure runtime timing functions."""

    @property
    def supports_uniform_timing(self) -> bool: ...

    @property
    def binding(self) -> TempoMapBinding: ...

    def uniform_timing_metrics(self) -> UniformTimingMetrics: ...

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
    ) -> ScoreRelativeSpan: ...

    def describe_nearest_position(
        self,
        score_seconds: float,
        subdivisions_per_beat: int,
    ) -> NearestPosition: ...


__all__ = [
    "LegacyTimingConfiguration",
    "NearestPosition",
    "ScoreRelativeSpan",
    "TempoMap",
    "TempoMapBinding",
    "TempoUnit",
    "UniformTimingBridge",
    "UniformTimingMetrics",
]
