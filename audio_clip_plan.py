"""Pure sample-boundary planning for Seconds and Musical audio edits."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

try:
    from .musical_timing import (
        TempoUnit,
        calculate_musical_timing,
        round_half_away_from_zero,
    )
except ImportError:  # Support direct imports when running the standalone tests.
    from musical_timing import (  # type: ignore[no-redef]
        TempoUnit,
        calculate_musical_timing,
        round_half_away_from_zero,
    )


EditMode = Literal["Seconds", "Musical"]


@dataclass(frozen=True)
class AudioClipPlan:
    """Resolved source samples and metadata for one non-empty audio clip."""

    requested_start_seconds: float
    requested_end_seconds: float
    start_sample: int
    end_sample: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    start_frame: int
    frame_count: int
    seconds_per_beat: float
    frames_per_beat: float
    seconds_per_bar: float
    frames_per_bar: float
    musical_position: str
    clamped: bool


def _require_positive_integer(name: str, value: object) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _require_finite_number(name: str, value: object) -> None:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be an int or float")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _duration_label(
    duration_bars: int,
    duration_beats: int,
    duration_subdivisions: int,
) -> str:
    parts = []
    for value, singular, plural in (
        (duration_bars, "Bar", "Bars"),
        (duration_beats, "Beat", "Beats"),
        (duration_subdivisions, "Subdivision", "Subdivisions"),
    ):
        if value:
            parts.append(f"{value} {singular if value == 1 else plural}")
    return " + ".join(parts) if parts else "0 Beats"


def _nearest_grid_position(
    *,
    start_seconds: float,
    downbeat_offset: float,
    seconds_per_beat: float,
    beats_per_bar: int,
    subdivisions_per_beat: int,
) -> str:
    relative_subdivisions = round_half_away_from_zero(
        ((start_seconds - downbeat_offset) / seconds_per_beat)
        * subdivisions_per_beat
    )
    if relative_subdivisions < 0:
        return (
            f"{-relative_subdivisions} subdivisions before "
            "Bar 1 · Beat 1"
        )

    subdivisions_per_bar = beats_per_bar * subdivisions_per_beat
    bar_index, subdivision_in_bar = divmod(
        relative_subdivisions, subdivisions_per_bar
    )
    beat_index, subdivision = divmod(
        subdivision_in_bar, subdivisions_per_beat
    )
    return (
        f"Bar {bar_index + 1} · Beat {beat_index + 1} · "
        f"Subdivision {subdivision}"
    )


def create_audio_clip_plan(
    *,
    edit_mode: EditMode,
    sample_rate: int,
    sample_count: int,
    start_time: float = 0.0,
    end_time: float = 0.0,
    bpm: float = 120.0,
    tempo_unit: TempoUnit = "Quarter",
    beats_per_bar: int = 4,
    beat_unit: int = 4,
    downbeat_offset: float = 0.0,
    fps: float = 24.0,
    start_bar: int = 1,
    start_beat: int = 1,
    start_subdivision: int = 0,
    duration_bars: int = 4,
    duration_beats: int = 0,
    duration_subdivisions: int = 0,
    subdivisions_per_beat: int = 4,
) -> AudioClipPlan:
    """Create the complete sample-accurate clip plan for an edit mode."""
    if type(edit_mode) is not str:
        raise TypeError("edit_mode must be a str")
    if edit_mode not in ("Seconds", "Musical"):
        raise ValueError("edit_mode must be 'Seconds' or 'Musical'")
    _require_positive_integer("sample_rate", sample_rate)
    _require_positive_integer("sample_count", sample_count)

    timing = calculate_musical_timing(
        bpm=bpm,
        tempo_unit=tempo_unit,
        beats_per_bar=beats_per_bar,
        beat_unit=beat_unit,
        downbeat_offset=downbeat_offset,
        fps=fps,
        start_bar=start_bar,
        start_beat=start_beat,
        start_subdivision=start_subdivision,
        duration_bars=duration_bars,
        duration_beats=duration_beats,
        duration_subdivisions=duration_subdivisions,
        subdivisions_per_beat=subdivisions_per_beat,
    )
    audio_duration = sample_count / sample_rate

    if edit_mode == "Seconds":
        _require_finite_number("start_time", start_time)
        _require_finite_number("end_time", end_time)
        requested_start_seconds = float(start_time)
        requested_end_seconds = (
            audio_duration if end_time <= 0 else float(end_time)
        )
    else:
        requested_start_seconds = timing.start_seconds
        requested_end_seconds = timing.end_seconds

    start_sample = round_half_away_from_zero(
        requested_start_seconds * sample_rate
    )
    end_sample = round_half_away_from_zero(
        requested_end_seconds * sample_rate
    )

    clamped = (
        requested_start_seconds < 0.0
        or requested_start_seconds > audio_duration
        or requested_end_seconds < 0.0
        or requested_end_seconds > audio_duration
        or requested_end_seconds < requested_start_seconds
    )
    start_sample = min(max(start_sample, 0), sample_count)
    end_sample = min(max(end_sample, 0), sample_count)

    if end_sample < start_sample:
        end_sample = start_sample
        clamped = True

    if end_sample == start_sample:
        clamped = True
        if start_sample == sample_count:
            start_sample = sample_count - 1
            end_sample = sample_count
        else:
            end_sample = start_sample + 1

    start_seconds = start_sample / sample_rate
    end_seconds = end_sample / sample_rate
    duration_seconds = (end_sample - start_sample) / sample_rate
    start_frame = round_half_away_from_zero(start_seconds * fps)
    frame_count = round_half_away_from_zero(duration_seconds * fps)
    frame_end = start_frame + frame_count
    clamp_suffix = " | clamped to audio" if clamped else ""

    if edit_mode == "Musical":
        length = _duration_label(
            duration_bars,
            duration_beats,
            duration_subdivisions,
        )
        musical_position = (
            f"Bar {start_bar} · Beat {start_beat} · "
            f"Subdivision {start_subdivision} | Length: {length} | "
            f"Time: {start_seconds:.3f}–{end_seconds:.3f} s | "
            f"Frames: {start_frame}–{frame_end}{clamp_suffix}"
        )
    else:
        nearest = _nearest_grid_position(
            start_seconds=start_seconds,
            downbeat_offset=downbeat_offset,
            seconds_per_beat=timing.seconds_per_beat,
            beats_per_bar=beats_per_bar,
            subdivisions_per_beat=subdivisions_per_beat,
        )
        musical_position = (
            f"Seconds mode | Nearest: {nearest} | "
            f"Time: {start_seconds:.3f}–{end_seconds:.3f} s | "
            f"Frames: {start_frame}–{frame_end}{clamp_suffix}"
        )

    return AudioClipPlan(
        requested_start_seconds=requested_start_seconds,
        requested_end_seconds=requested_end_seconds,
        start_sample=start_sample,
        end_sample=end_sample,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        duration_seconds=duration_seconds,
        start_frame=start_frame,
        frame_count=frame_count,
        seconds_per_beat=timing.seconds_per_beat,
        frames_per_beat=timing.frames_per_beat,
        seconds_per_bar=timing.seconds_per_bar,
        frames_per_bar=timing.frames_per_bar,
        musical_position=musical_position,
        clamped=clamped,
    )


__all__ = ["AudioClipPlan", "EditMode", "create_audio_clip_plan"]
