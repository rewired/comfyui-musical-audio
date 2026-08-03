"""Pure canonical Score selection resolution."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

from .bars import round_half_away_from_zero_ratio
from .resolver import ScoreResolver


ScoreSelectionMode = Literal["exact", "duration"]


@dataclass(frozen=True)
class ScorePosition:
    bar: int
    beat: int
    subdivision: int


@dataclass(frozen=True)
class ScoreSelection:
    start_tick: int
    end_tick_exclusive: int
    requested_start_score_seconds: float
    requested_end_score_seconds: float
    requested_start_seconds: float
    requested_end_seconds: float
    mode: ScoreSelectionMode
    start_position: ScorePosition
    exact_end_position: ScorePosition | None
    duration_bars: int
    duration_beats: int
    duration_subdivisions: int
    subdivisions_per_beat: int


class ScoreSelectionError(ValueError):
    code: Literal[
        "score_end_position_required",
        "score_selection_range_invalid",
    ]
    message: str

    def __init__(
        self,
        code: Literal[
            "score_end_position_required",
            "score_selection_range_invalid",
        ],
        message: str,
    ) -> None:
        if code not in (
            "score_end_position_required",
            "score_selection_range_invalid",
        ):
            raise ValueError("unsupported Score selection error code")
        if type(message) is not str or not message:
            raise TypeError("Score selection error message must be a nonempty string")
        self.code = code
        self.message = message
        super().__init__(message)


def _invalid(message: str) -> ScoreSelectionError:
    return ScoreSelectionError("score_selection_range_invalid", message)


def _require_int(name: str, value: object, minimum: int) -> int:
    if type(value) is not int:
        raise _invalid(f"{name} must be a built-in int")
    if value < minimum:
        raise _invalid(f"{name} must be at least {minimum}")
    return value


def _position_tick(
    resolver: ScoreResolver,
    position: ScorePosition,
    subdivisions_per_beat: int,
    label: str,
) -> int:
    try:
        return resolver.position_to_tick(
            position.bar,
            position.beat,
            position.subdivision,
            subdivisions_per_beat,
        )
    except (TypeError, ValueError):
        raise _invalid(f"{label} is not canonical for the resolved Score") from None


def resolve_score_selection(
    resolver: ScoreResolver,
    *,
    start_bar: int,
    start_beat: int,
    start_subdivision: int,
    score_end_bar: int,
    score_end_beat: int,
    score_end_subdivision: int,
    duration_bars: int,
    duration_beats: int,
    duration_subdivisions: int,
    subdivisions_per_beat: int,
) -> ScoreSelection:
    if type(resolver) is not ScoreResolver:
        raise TypeError("resolver must be a ScoreResolver")

    start = ScorePosition(
        _require_int("start_bar", start_bar, 1),
        _require_int("start_beat", start_beat, 1),
        _require_int("start_subdivision", start_subdivision, 0),
    )
    end_values = (
        _require_int("score_end_bar", score_end_bar, 0),
        _require_int("score_end_beat", score_end_beat, 0),
        _require_int("score_end_subdivision", score_end_subdivision, 0),
    )
    duration_values = (
        _require_int("duration_bars", duration_bars, 0),
        _require_int("duration_beats", duration_beats, 0),
        _require_int("duration_subdivisions", duration_subdivisions, 0),
    )
    subdivisions = _require_int(
        "subdivisions_per_beat",
        subdivisions_per_beat,
        1,
    )
    if start.subdivision >= subdivisions:
        raise _invalid("start_subdivision must be below subdivisions_per_beat")

    start_tick = _position_tick(resolver, start, subdivisions, "start position")
    exact_end: ScorePosition | None = None

    if end_values == (0, 0, 0):
        mode: ScoreSelectionMode = "duration"
        try:
            numerator, denominator = resolver.meter_at_bar(start.bar)
            start_bar_tick = resolver.bar_to_tick(start.bar)
            duration_subdivision_count = (
                (duration_values[0] * numerator + duration_values[1])
                * subdivisions
                + duration_values[2]
            )
            provisional_end_subdivision_index = (
                (start.beat - 1) * subdivisions + start.subdivision
            ) + duration_subdivision_count
            beat_index, subdivision_index = divmod(
                provisional_end_subdivision_index, subdivisions
            )
            end_tick = (
                start_bar_tick
                + round_half_away_from_zero_ratio(
                    beat_index * 4 * resolver.score.ticks_per_quarter,
                    denominator,
                )
                + round_half_away_from_zero_ratio(
                    subdivision_index * 4 * resolver.score.ticks_per_quarter,
                    denominator * subdivisions,
                )
            )
            if any(
                start_tick < meter.tick < end_tick
                for meter in resolver.score.meters
            ):
                raise ScoreSelectionError(
                    "score_end_position_required",
                    "an exact Score end is required because the duration crosses a meter change",
                )
            end_position = resolver.tick_to_position(end_tick, subdivisions)
            if resolver.position_to_tick(*end_position, subdivisions) != end_tick:
                raise ValueError("duration end did not round-trip")
        except ScoreSelectionError:
            raise
        except (TypeError, ValueError, OverflowError):
            raise _invalid(
                "duration end is not an exact canonical Score position"
            ) from None

    else:
        if end_values[0] < 1 or end_values[1] < 1:
            raise _invalid("exact Score end fields must form one complete position")
        if end_values[2] >= subdivisions:
            raise _invalid("score_end_subdivision must be below subdivisions_per_beat")
        mode = "exact"
        exact_end = ScorePosition(*end_values)
        end_tick = _position_tick(
            resolver,
            exact_end,
            subdivisions,
            "exact end position",
        )

    if end_tick < start_tick:
        raise _invalid("exclusive Score end must not precede the start")

    try:
        start_score_seconds = resolver.tick_to_seconds(start_tick)
        end_score_seconds = resolver.tick_to_seconds(end_tick)
        start_seconds = resolver.tick_to_audio_seconds(start_tick)
        end_seconds = resolver.tick_to_audio_seconds(end_tick)
    except (TypeError, ValueError, OverflowError):
        raise _invalid("Score selection seconds could not be resolved") from None

    return ScoreSelection(
        start_tick=start_tick,
        end_tick_exclusive=end_tick,
        requested_start_score_seconds=start_score_seconds,
        requested_end_score_seconds=end_score_seconds,
        requested_start_seconds=start_seconds,
        requested_end_seconds=end_seconds,
        mode=mode,
        start_position=start,
        exact_end_position=exact_end,
        duration_bars=duration_values[0],
        duration_beats=duration_values[1],
        duration_subdivisions=duration_values[2],
        subdivisions_per_beat=subdivisions,
    )


def selection_start_score_tick(
    resolver: ScoreResolver,
    *,
    edit_mode: Literal["Seconds", "Musical"],
    clamped: bool,
    requested_start_seconds: float,
    returned_start_seconds: float,
    musical_start_tick: int | None,
) -> int | float:
    if type(resolver) is not ScoreResolver:
        raise TypeError("resolver must be a ScoreResolver")
    if type(edit_mode) is not str:
        raise TypeError("edit_mode must be a built-in str")
    if edit_mode not in ("Seconds", "Musical"):
        raise ValueError("edit_mode must be Seconds or Musical")
    if type(clamped) is not bool:
        raise TypeError("clamped must be a built-in bool")
    for name, value in (
        ("requested_start_seconds", requested_start_seconds),
        ("returned_start_seconds", returned_start_seconds),
    ):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite built-in int or float")
    if musical_start_tick is not None and type(musical_start_tick) is not int:
        raise TypeError("musical_start_tick must be a built-in int or None")

    if edit_mode == "Musical" and not clamped:
        if musical_start_tick is None:
            raise ValueError("unclamped Musical Score selection requires an exact tick")
        return musical_start_tick
    query_seconds = returned_start_seconds if clamped else requested_start_seconds
    return resolver.audio_seconds_to_tick(query_seconds)


__all__ = (
    "ScorePosition",
    "ScoreSelection",
    "ScoreSelectionError",
    "ScoreSelectionMode",
    "resolve_score_selection",
    "selection_start_score_tick",
)
