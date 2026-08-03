"""Pure sample-boundary planning for Seconds and Musical audio edits."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

try:
    from .musical_timing import (
        ConstantTempoMap,
        NearestPosition,
        TempoUnit,
        UniformTimingBridge,
        calculate_musical_timing,
        round_half_away_from_zero,
    )
except ImportError:  # Support direct imports when running the standalone tests.
    from musical_timing import (  # type: ignore[no-redef]
        ConstantTempoMap,
        NearestPosition,
        TempoUnit,
        UniformTimingBridge,
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


@dataclass(frozen=True)
class RequestedAudioRange:
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class SampleRangePlan:
    requested_start_seconds: float
    requested_end_seconds: float
    start_sample: int
    end_sample: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    start_frame: int
    frame_count: int
    clamped: bool


@dataclass(frozen=True)
class ClipTimingMetadata:
    seconds_per_beat: float
    frames_per_beat: float
    seconds_per_bar: float
    frames_per_bar: float
    musical_position: str


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


def _format_nearest_position(nearest: NearestPosition) -> str:
    before = nearest.subdivisions_before_bar_one
    if type(before) is not int:
        raise ValueError("invalid NearestPosition before-count")
    if before > 0:
        if not (
            nearest.bar is None
            and nearest.beat is None
            and nearest.subdivision is None
        ):
            raise ValueError("invalid Pre-Bar-1 NearestPosition variant")
        return (
            f"{before} subdivisions before "
            "Bar 1 · Beat 1"
        )
    if (
        before != 0
        or type(nearest.bar) is not int
        or nearest.bar <= 0
        or type(nearest.beat) is not int
        or nearest.beat <= 0
        or type(nearest.subdivision) is not int
        or nearest.subdivision < 0
    ):
        raise ValueError("invalid canonical NearestPosition variant")
    return (
        f"Bar {nearest.bar} · Beat {nearest.beat} · "
        f"Subdivision {nearest.subdivision}"
    )


def apply_sample_range(
    *,
    requested_range: RequestedAudioRange,
    sample_rate: int,
    sample_count: int,
    fps: float,
) -> SampleRangePlan:
    if type(requested_range) is not RequestedAudioRange:
        raise TypeError("requested_range must be a RequestedAudioRange")
    _require_positive_integer("sample_rate", sample_rate)
    _require_positive_integer("sample_count", sample_count)
    _require_finite_number("fps", fps)
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    _require_finite_number("requested start_seconds", requested_range.start_seconds)
    _require_finite_number("requested end_seconds", requested_range.end_seconds)

    requested_start_seconds = float(requested_range.start_seconds)
    requested_end_seconds = float(requested_range.end_seconds)
    audio_duration = sample_count / sample_rate
    start_sample = round_half_away_from_zero(requested_start_seconds * sample_rate)
    end_sample = round_half_away_from_zero(requested_end_seconds * sample_rate)
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
    return SampleRangePlan(
        requested_start_seconds=requested_start_seconds,
        requested_end_seconds=requested_end_seconds,
        start_sample=start_sample,
        end_sample=end_sample,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        duration_seconds=duration_seconds,
        start_frame=round_half_away_from_zero(start_seconds * fps),
        frame_count=round_half_away_from_zero(duration_seconds * fps),
        clamped=clamped,
    )


def finalize_audio_clip_plan(
    sample_plan: SampleRangePlan,
    metadata: ClipTimingMetadata,
) -> AudioClipPlan:
    if type(sample_plan) is not SampleRangePlan:
        raise TypeError("sample_plan must be a SampleRangePlan")
    if type(metadata) is not ClipTimingMetadata:
        raise TypeError("metadata must be a ClipTimingMetadata")
    for name, value in (
        ("seconds_per_beat", metadata.seconds_per_beat),
        ("frames_per_beat", metadata.frames_per_beat),
        ("seconds_per_bar", metadata.seconds_per_bar),
        ("frames_per_bar", metadata.frames_per_bar),
    ):
        _require_finite_number(name, value)
    if type(metadata.musical_position) is not str:
        raise TypeError("musical_position must be a built-in str")
    return AudioClipPlan(
        requested_start_seconds=sample_plan.requested_start_seconds,
        requested_end_seconds=sample_plan.requested_end_seconds,
        start_sample=sample_plan.start_sample,
        end_sample=sample_plan.end_sample,
        start_seconds=sample_plan.start_seconds,
        end_seconds=sample_plan.end_seconds,
        duration_seconds=sample_plan.duration_seconds,
        start_frame=sample_plan.start_frame,
        frame_count=sample_plan.frame_count,
        seconds_per_beat=metadata.seconds_per_beat,
        frames_per_beat=metadata.frames_per_beat,
        seconds_per_bar=metadata.seconds_per_bar,
        frames_per_bar=metadata.frames_per_bar,
        musical_position=metadata.musical_position,
        clamped=sample_plan.clamped,
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
    tempo_map: UniformTimingBridge | None = None,
) -> AudioClipPlan:
    """Create the complete sample-accurate clip plan for an edit mode."""
    if type(edit_mode) is not str:
        raise TypeError("edit_mode must be a str")
    if edit_mode not in ("Seconds", "Musical"):
        raise ValueError("edit_mode must be 'Seconds' or 'Musical'")
    _require_positive_integer("sample_rate", sample_rate)
    _require_positive_integer("sample_count", sample_count)

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
        tempo_map=active_map,
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

    sample_plan = apply_sample_range(
        requested_range=RequestedAudioRange(
            start_seconds=requested_start_seconds,
            end_seconds=requested_end_seconds,
        ),
        sample_rate=sample_rate,
        sample_count=sample_count,
        fps=fps,
    )
    start_seconds = sample_plan.start_seconds
    end_seconds = sample_plan.end_seconds
    start_frame = sample_plan.start_frame
    frame_count = sample_plan.frame_count
    frame_end = start_frame + frame_count
    clamp_suffix = " | clamped to audio" if sample_plan.clamped else ""

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
        nearest = active_map.describe_nearest_position(
            start_seconds - downbeat_offset,
            subdivisions_per_beat,
        )
        musical_position = (
            f"Seconds mode | Nearest: {_format_nearest_position(nearest)} | "
            f"Time: {start_seconds:.3f}–{end_seconds:.3f} s | "
            f"Frames: {start_frame}–{frame_end}{clamp_suffix}"
        )

    return finalize_audio_clip_plan(
        sample_plan,
        ClipTimingMetadata(
            seconds_per_beat=timing.seconds_per_beat,
            frames_per_beat=timing.frames_per_beat,
            seconds_per_bar=timing.seconds_per_bar,
            frames_per_bar=timing.frames_per_bar,
            musical_position=musical_position,
        ),
    )


__all__ = [
    "AudioClipPlan",
    "ClipTimingMetadata",
    "EditMode",
    "RequestedAudioRange",
    "SampleRangePlan",
    "apply_sample_range",
    "create_audio_clip_plan",
    "finalize_audio_clip_plan",
]
