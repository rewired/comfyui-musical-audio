"""Pure calculations for converting musical positions into time and frames."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal


TempoUnit = Literal["Quarter", "Eighth", "Dotted Quarter"]

_TEMPO_UNIT_IN_QUARTERS: dict[str, float] = {
    "Quarter": 1.0,
    "Eighth": 0.5,
    "Dotted Quarter": 1.5,
}


def _round_half_away_from_zero(value: float) -> int:
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
    if not 1 <= start_beat <= beats_per_bar:
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

    seconds_per_tempo_pulse = 60.0 / bpm
    tempo_unit_in_quarters = _TEMPO_UNIT_IN_QUARTERS[tempo_unit]
    seconds_per_quarter = seconds_per_tempo_pulse / tempo_unit_in_quarters
    seconds_per_beat = seconds_per_quarter * (4.0 / beat_unit)
    seconds_per_bar = seconds_per_beat * beats_per_bar

    start_beats = (
        (start_bar - 1) * beats_per_bar
        + (start_beat - 1)
        + start_subdivision / subdivisions_per_beat
    )
    start_seconds = downbeat_offset + start_beats * seconds_per_beat

    duration_beats_total = (
        duration_bars * beats_per_bar
        + duration_beats
        + duration_subdivisions / subdivisions_per_beat
    )
    duration_seconds = duration_beats_total * seconds_per_beat
    end_seconds = start_seconds + duration_seconds

    frames_per_beat = fps * seconds_per_beat
    frames_per_bar = fps * seconds_per_bar
    start_frame = _round_half_away_from_zero(start_seconds * fps)
    frame_count = _round_half_away_from_zero(duration_seconds * fps)

    return MusicalTimingResult(
        seconds_per_tempo_pulse=seconds_per_tempo_pulse,
        seconds_per_quarter=seconds_per_quarter,
        seconds_per_beat=seconds_per_beat,
        seconds_per_bar=seconds_per_bar,
        start_beats=start_beats,
        start_seconds=start_seconds,
        duration_beats_total=duration_beats_total,
        duration_seconds=duration_seconds,
        end_seconds=end_seconds,
        frames_per_beat=frames_per_beat,
        frames_per_bar=frames_per_bar,
        start_frame=start_frame,
        frame_count=frame_count,
    )


__all__ = ["MusicalTimingResult", "TempoUnit", "calculate_musical_timing"]
