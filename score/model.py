from dataclasses import dataclass
from typing import Literal, Mapping


ScoreFormat = Literal["json", "midi", "analyzed", "constant"]
ProviderKind = Literal[
    "explicit",
    "json_sidecar",
    "midi_sidecar",
    "analysis",
    "constant",
]
ProviderStatus = Literal["not_applicable", "found", "invalid"]


@dataclass(frozen=True)
class TempoEvent:
    tick: int
    us_per_quarter: int


@dataclass(frozen=True)
class MeterEvent:
    tick: int
    numerator: int
    denominator: int


@dataclass(frozen=True)
class Marker:
    tick: int
    name: str


@dataclass(frozen=True)
class Section:
    name: str
    start_tick: int
    end_tick_exclusive: int
    bar_aligned: bool
    confidence: float | None = None


@dataclass(frozen=True)
class Score:
    ticks_per_quarter: int
    tempos: tuple[TempoEvent, ...]
    meters: tuple[MeterEvent, ...]
    markers: tuple[Marker, ...]
    sections: tuple[Section, ...]
    source: ScoreFormat
    meter_estimated: bool = False
    has_variable_meter: bool = False
    has_midbar_meter_change: bool = False


@dataclass(frozen=True)
class ResolvedScore:
    score: Score
    audio_seconds_at_tick_zero: float
    provider: ProviderKind


@dataclass(frozen=True)
class ProviderResult:
    status: ProviderStatus
    score: Score | None
    diagnostics: tuple[str, ...]
    provenance: Mapping[str, str]
