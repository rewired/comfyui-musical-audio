import folder_paths
import math
import os
import torch
import av

try:
    from .audio_clip_plan import (
        ClipTimingMetadata,
        RequestedAudioRange,
        apply_sample_range,
        create_audio_clip_plan,
        finalize_audio_clip_plan,
    )
    from .score.providers import select_section
    from .score.resolver import ScoreResolver
    from .score.routes import _resolve_comfy_path
    from .score.runtime import (
        ResolvedPathState,
        ScoreRepositoryError,
        append_resolver_diagnostics,
        diagnostic,
        prepare_score_repository_request,
        resolve_score_repository,
        serialize_diagnostics,
    )
    from .score.selection import (
        ScoreSelectionError,
        resolve_score_selection,
        selection_start_score_tick,
    )
except ImportError:  # Support direct module loading outside the package.
    import audio_clip_plan as _audio_clip_plan

    create_audio_clip_plan = _audio_clip_plan.create_audio_clip_plan
    ClipTimingMetadata = getattr(_audio_clip_plan, "ClipTimingMetadata", None)
    RequestedAudioRange = getattr(_audio_clip_plan, "RequestedAudioRange", None)
    apply_sample_range = getattr(_audio_clip_plan, "apply_sample_range", None)
    finalize_audio_clip_plan = getattr(_audio_clip_plan, "finalize_audio_clip_plan", None)
    from score.providers import select_section
    from score.resolver import ScoreResolver
    from score.routes import _resolve_comfy_path
    from score.runtime import (
        ResolvedPathState,
        ScoreRepositoryError,
        append_resolver_diagnostics,
        diagnostic,
        prepare_score_repository_request,
        resolve_score_repository,
        serialize_diagnostics,
    )
    from score.selection import (
        ScoreSelectionError,
        resolve_score_selection,
        selection_start_score_tick,
    )


_EXTERNAL_INPUT_MISSING = object()
MAX_LOCAL_BEATS_PER_BAR = 64


def external_or_local(external_value, local_value):
    """Use an explicitly supplied external value, including falsey values."""
    return local_value if external_value is _EXTERNAL_INPUT_MISSING else external_value


def _resolve_audio_path(audio):
    """Resolve a selected audio value without letting ComfyUI path errors escape."""
    if audio == "none":
        return None, None

    try:
        audio_path = _resolve_comfy_path(
            audio,
            require_file=False,
            folder_paths_module=folder_paths,
        )
    except Exception as error:
        return None, error
    return audio_path or None, None


def _resolve_score_path(score_file):
    """Resolve a nonblank explicit Score selection at the ComfyUI boundary."""
    if type(score_file) is not str:
        raise TypeError("score_file must be a built-in string")
    if not score_file.strip():
        return None, None

    try:
        score_path = _resolve_comfy_path(
            score_file,
            require_file=False,
            folder_paths_module=folder_paths,
        )
    except Exception as error:
        return None, error
    if not score_path:
        return None, ValueError("score_file resolved to no path")
    return score_path, None


def _audio_dependency_fingerprint(audio):
    """Return the historical audio identity together with its resolved path."""
    if audio == "none":
        return ("none", "none"), None

    audio_path, resolution_error = _resolve_audio_path(audio)
    if resolution_error is not None or audio_path is None:
        return ("unresolved", audio), None

    try:
        normalized_path = os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(audio_path)))
        )
    except (OSError, TypeError, ValueError):
        return ("unresolved", audio), None

    try:
        file_stat = os.stat(normalized_path)
    except (OSError, ValueError):
        return ("missing", audio, normalized_path), audio_path

    return (
        "file",
        audio,
        normalized_path,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    ), audio_path


def _prepare_score_dependencies(score_file, audio_path):
    """Resolve boundary state and prepare the shared repository request."""
    explicit_path, explicit_error = _resolve_score_path(score_file)
    return prepare_score_repository_request(
        score_file=score_file,
        audio_path=audio_path,
        explicit=ResolvedPathState(
            selection=score_file,
            resolved_path=explicit_path,
            resolution_error=(
                None if explicit_error is None else type(explicit_error).__name__
            ),
        ),
    )


def _concise_exception_message(error, limit=200):
    """Format bounded exception text for a single-line node output."""
    message = " ".join(str(error).split()) or "no exception message"
    if len(message) > limit:
        return f"{message[:limit - 3]}..."
    return message


def _finite_float(name, value):
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be an int or float")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _validate_score_planning_inputs(
    *,
    edit_mode,
    bpm,
    tempo_unit,
    beats_per_bar,
    beat_unit,
    fps,
    subdivisions_per_beat,
):
    if type(edit_mode) is not str:
        raise TypeError("edit_mode must be a str")
    if edit_mode not in ("Seconds", "Musical"):
        raise ValueError("edit_mode must be 'Seconds' or 'Musical'")
    for name, value in (("bpm", bpm), ("fps", fps)):
        numeric = _finite_float(name, value)
        if numeric <= 0:
            raise ValueError(f"{name} must be greater than zero")
    if type(tempo_unit) is not str:
        raise TypeError("tempo_unit must be a str")
    if tempo_unit not in ("Quarter", "Eighth", "Dotted Quarter"):
        raise ValueError("tempo_unit is unsupported")
    integer_values = (
        ("beats_per_bar", beats_per_bar, 1),
        ("beat_unit", beat_unit, 1),
    )
    for name, value, minimum in integer_values:
        if type(value) is not int:
            raise TypeError(f"{name} must be an int")
        if value < minimum:
            raise ValueError(f"{name} must be at least {minimum}")
    if edit_mode == "Seconds":
        if type(subdivisions_per_beat) is not int:
            raise TypeError("subdivisions_per_beat must be an int")
        if subdivisions_per_beat < 1:
            raise ValueError("subdivisions_per_beat must be at least 1")


def _duration_label(duration_bars, duration_beats, duration_subdivisions):
    parts = []
    for value, singular, plural in (
        (duration_bars, "Bar", "Bars"),
        (duration_beats, "Beat", "Beats"),
        (duration_subdivisions, "Subdivision", "Subdivisions"),
    ):
        if value:
            parts.append(f"{value} {singular if value == 1 else plural}")
    return " + ".join(parts) if parts else "0 Beats"


def _round_half_away(value):
    fractional, integral = math.modf(value)
    if fractional >= 0.5:
        integral += 1
    elif fractional <= -0.5:
        integral -= 1
    return int(integral)


def _score_nearest_text(resolver, anchor_tick, subdivisions_per_beat):
    if anchor_tick >= 0:
        bar, beat, subdivision = resolver.tick_to_position(
            anchor_tick,
            subdivisions_per_beat,
        )
        return f"Bar {bar} · Beat {beat} · Subdivision {subdivision}"
    _numerator, denominator = resolver.meter_at_bar(1)
    if subdivisions_per_beat * denominator > 4 * resolver.score.ticks_per_quarter:
        raise ValueError("subdivision grid is finer than integer tick resolution")
    signed = _round_half_away(
        anchor_tick
        * denominator
        * subdivisions_per_beat
        / (4 * resolver.score.ticks_per_quarter)
    )
    if signed == 0:
        return "Bar 1 · Beat 1 · Subdivision 0"
    return f"{-signed} subdivisions before Bar 1 · Beat 1"


def _score_musical_position(
    *,
    edit_mode,
    selection,
    resolver,
    sample_plan,
    subdivisions_per_beat,
):
    frame_end = sample_plan.start_frame + sample_plan.frame_count
    clamp_suffix = " | clamped to audio" if sample_plan.clamped else ""
    time_and_frames = (
        f"Time: {sample_plan.start_seconds:.3f}–{sample_plan.end_seconds:.3f} s | "
        f"Frames: {sample_plan.start_frame}–{frame_end}{clamp_suffix}"
    )
    if edit_mode == "Musical":
        start = selection.start_position
        if selection.mode == "exact":
            end = selection.exact_end_position
            return (
                f"Bar {start.bar} · Beat {start.beat} · Subdivision {start.subdivision} | "
                f"End (exclusive): Bar {end.bar} · Beat {end.beat} · "
                f"Subdivision {end.subdivision} | {time_and_frames}"
            )
        length = _duration_label(
            selection.duration_bars,
            selection.duration_beats,
            selection.duration_subdivisions,
        )
        return (
            f"Bar {start.bar} · Beat {start.beat} · Subdivision {start.subdivision} | "
            f"Length: {length} | {time_and_frames}"
        )
    nearest_tick = resolver.audio_seconds_to_tick(sample_plan.start_seconds)
    nearest = _score_nearest_text(resolver, nearest_tick, subdivisions_per_beat)
    return f"Seconds mode | Nearest: {nearest} | {time_and_frames}"


def f32_pcm(wav: torch.Tensor) -> torch.Tensor:
    """Convert audio to float 32 bits PCM format."""
    if wav.dtype.is_floating_point:
        return wav
    elif wav.dtype == torch.int16:
        return wav.float() / (2 ** 15)
    elif wav.dtype == torch.int32:
        return wav.float() / (2 ** 31)
    raise ValueError(f"Unsupported wav dtype: {wav.dtype}")

def load_audio_file(filepath: str) -> tuple[torch.Tensor, int]:
    """Uses the latest ComfyUI av-based decoding for maximum compatibility."""
    with av.open(filepath) as af:
        if not af.streams.audio:
            raise ValueError("No audio stream found in the file.")

        stream = af.streams.audio[0]
        sr = stream.codec_context.sample_rate
        n_channels = stream.channels

        frames = []
        for frame in af.decode(streams=stream.index):
            buf = torch.from_numpy(frame.to_ndarray())
            if buf.shape[0] != n_channels:
                buf = buf.view(-1, n_channels).t()

            frames.append(buf)

        if not frames:
            raise ValueError("No audio frames decoded.")

        wav = torch.cat(frames, dim=1)
        if wav.shape[-1] == 0:
            raise ValueError("No audio samples decoded.")
        wav = f32_pcm(wav)
        return wav, sr


class MusicalLoadAudioUI:
    @classmethod
    def INPUT_TYPES(s):
        try:
            files = folder_paths.get_filename_list("audio")
        except Exception:
            files = []
        
        if not files:
            input_dir = folder_paths.get_input_directory()
            if os.path.exists(input_dir):
                all_files = [f for f in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, f))]
                wdc_dir = os.path.join(input_dir, "whatdreamscost")
                if os.path.exists(wdc_dir):
                    wdc_files = [f"whatdreamscost/{f}" for f in os.listdir(wdc_dir) if os.path.isfile(os.path.join(wdc_dir, f))]
                    all_files.extend(wdc_files)
                try:
                    files = sorted(folder_paths.filter_files_content_types(all_files, ["audio", "video"]))
                except Exception:
                    files = sorted(all_files)
        
        if not files or len(files) == 0:
            files = ["none"]

        return {
            "required": {
                "audio": (files, {"audio_upload": True, "socketless": True}), # Moved to the top so it appears first
                "start_time": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 100000.0, "step": 0.01, "socketless": True}),
                "end_time": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 100000.0, "step": 0.01, "socketless": True}),
                "duration": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 100000.0, "step": 0.01, "socketless": True}),
                "edit_mode": (["Seconds", "Musical"], {"default": "Seconds", "socketless": True}),
                "bpm": ("FLOAT", {"default": 120.0, "min": 0.01, "step": 0.01, "socketless": True}),
                "tempo_unit": (["Quarter", "Eighth", "Dotted Quarter"], {"default": "Quarter", "socketless": True}),
                "beats_per_bar": ("INT", {"default": 4, "min": 1, "max": MAX_LOCAL_BEATS_PER_BAR, "socketless": True}),
                "beat_unit": ("INT", {"default": 4, "min": 1, "socketless": True}),
                "downbeat_offset": ("FLOAT", {"default": 0.0, "min": -100000.0, "max": 100000.0, "step": 0.001, "socketless": True}),
                "fps": ("FLOAT", {"default": 24.0, "min": 0.001, "step": 0.001, "socketless": True}),
                "start_bar": ("INT", {"default": 1, "min": 1, "socketless": True}),
                "start_beat": ("INT", {"default": 1, "min": 1, "max": MAX_LOCAL_BEATS_PER_BAR, "socketless": True}),
                "start_subdivision": ("INT", {"default": 0, "min": 0, "socketless": True}),
                "duration_bars": ("INT", {"default": 4, "min": 0, "socketless": True}),
                "duration_beats": ("INT", {"default": 0, "min": 0, "socketless": True}),
                "duration_subdivisions": ("INT", {"default": 0, "min": 0, "socketless": True}),
                "subdivisions_per_beat": ("INT", {"default": 4, "min": 1, "socketless": True}),
                "snap_mode": (["Off", "Bar", "Beat", "Subdivision", "Video Frame"], {"default": "Off", "socketless": True}),
                "score_file": (
                    "STRING",
                    {"default": "", "socketless": True},
                ),
                "score_end_bar": (
                    "INT",
                    {"default": 0, "min": 0, "socketless": True},
                ),
                "score_end_beat": (
                    "INT",
                    {"default": 0, "min": 0, "socketless": True},
                ),
                "score_end_subdivision": (
                    "INT",
                    {"default": 0, "min": 0, "socketless": True},
                ),
            },
            "optional": {
                "audioUI": ("AUDIO_UI", {"socketless": True}),
                "bpm_input": ("FLOAT", {"forceInput": True}),
                "tempo_unit_input": ("STRING", {"forceInput": True}),
                "fps_input": ("FLOAT", {"forceInput": True}),
                "beats_per_bar_input": ("INT", {"forceInput": True}),
                "beat_unit_input": ("INT", {"forceInput": True}),
                "subdivisions_per_beat_input": ("INT", {"forceInput": True}),
                "downbeat_offset_input": ("FLOAT", {"forceInput": True}),
            }
        }

    CATEGORY = "Musical Audio"
    RETURN_TYPES = (
        "AUDIO",
        "FLOAT",
        "STRING",
        "FLOAT",
        "FLOAT",
        "INT",
        "INT",
        "FLOAT",
        "FLOAT",
        "FLOAT",
        "FLOAT",
        "STRING",
        "FLOAT",
        "FLOAT",
        "INT",
        "STRING",
        "INT",
        "STRING",
        "STRING",
        "STRING",
    )
    RETURN_NAMES = (
        "audio",
        "duration",
        "filename",
        "start_seconds",
        "end_seconds",
        "start_frame",
        "frame_count",
        "seconds_per_beat",
        "frames_per_beat",
        "seconds_per_bar",
        "frames_per_bar",
        "musical_position",
        "bpm",
        "fps",
        "end_frame_exclusive",
        "section_name",
        "sample_rate",
        "score_format",
        "score_provider",
        "diagnostics",
    )
    FUNCTION = "load_audio"

    @classmethod
    def IS_CHANGED(cls, audio, **_kwargs):
        score_file = _kwargs.get("score_file", "")
        audio_fingerprint, audio_path = _audio_dependency_fingerprint(audio)
        score_request = _prepare_score_dependencies(
            score_file,
            audio_path,
        )
        return (*audio_fingerprint, score_request.dependency_fingerprint)

    @classmethod
    def VALIDATE_INPUTS(cls, audio):
        # Only audio bypasses default validation so stale or missing saved filenames
        # can reach the silence fallback. Every other constant keeps ComfyUI's normal
        # min/max, type, and combo validation because it is not accepted here.
        return True
    
    def load_audio(
        self,
        audio,
        start_time,
        end_time,
        duration,
        edit_mode,
        bpm,
        tempo_unit,
        beats_per_bar,
        beat_unit,
        downbeat_offset,
        fps,
        start_bar,
        start_beat,
        start_subdivision,
        duration_bars,
        duration_beats,
        duration_subdivisions,
        subdivisions_per_beat,
        snap_mode,
        score_file,
        score_end_bar=0,
        score_end_beat=0,
        score_end_subdivision=0,
        audioUI=None,
        bpm_input=_EXTERNAL_INPUT_MISSING,
        tempo_unit_input=_EXTERNAL_INPUT_MISSING,
        fps_input=_EXTERNAL_INPUT_MISSING,
        beats_per_bar_input=_EXTERNAL_INPUT_MISSING,
        beat_unit_input=_EXTERNAL_INPUT_MISSING,
        subdivisions_per_beat_input=_EXTERNAL_INPUT_MISSING,
        downbeat_offset_input=_EXTERNAL_INPUT_MISSING,
    ):
        effective_bpm = external_or_local(bpm_input, bpm)
        effective_tempo_unit = external_or_local(tempo_unit_input, tempo_unit)
        effective_fps = external_or_local(fps_input, fps)
        effective_beats_per_bar = external_or_local(beats_per_bar_input, beats_per_bar)
        effective_beat_unit = external_or_local(beat_unit_input, beat_unit)
        effective_subdivisions_per_beat = external_or_local(
            subdivisions_per_beat_input,
            subdivisions_per_beat,
        )
        effective_downbeat_offset = external_or_local(
            downbeat_offset_input,
            downbeat_offset,
        )
        effective_alignment = _finite_float(
            "downbeat_offset",
            effective_downbeat_offset,
        )

        # Determine the annotated file path if a file is actually selected
        # We wrap this in a try/except because get_annotated_filepath can fail if 
        # the input string is malformed or doesn't follow expected paths.
        audio_path, resolution_error = _resolve_audio_path(audio)
        path_exists = False
        if audio_path is not None:
            try:
                path_exists = os.path.isfile(audio_path)
            except (OSError, ValueError):
                path_exists = False

        fallback_warning = None
        
        # --- FALLBACK LOGIC ---
        # If the file is 'none' or doesn't exist on disk, provide 1 second of silence
        if audio == "none" or resolution_error is not None or audio_path is None or not path_exists:
            missing_info = audio if audio != "none" else "None selected"
            print(f"!!! [MusicalLoadAudioUI] Warning: Audio file '{missing_info}' not found. Outputting 1 second of silence.")

            if audio == "none":
                fallback_warning = "no file selected"
            elif resolution_error is not None or audio_path is None:
                fallback_warning = f"selected audio '{audio}' path could not be resolved"
            else:
                fallback_warning = f"selected audio '{audio}' file not found"
            
            sample_rate = 44100
            # 1 second of silence (stereo) -> shape [channels, time]
            waveform = torch.zeros((2, 44100))
        else:
            try:
                waveform, sample_rate = load_audio_file(audio_path)
            except Exception as e:
                # If decoding fails for any reason, fallback to silence rather than crashing the workflow
                print(f"!!! [MusicalLoadAudioUI] Error decoding {audio}: {e}. Falling back to silence.")
                fallback_warning = (
                    f"selected audio '{audio}' decode failed "
                    f"({type(e).__name__}: {_concise_exception_message(e)})"
                )
                sample_rate = 44100
                waveform = torch.zeros((2, 44100))

        # duration remains a positional compatibility widget, while snap_mode is
        # reserved for the later frontend timeline implementation.
        _ = duration, snap_mode, audioUI

        diagnostics_values = []
        if fallback_warning is not None:
            diagnostics_values.append(
                diagnostic(
                    "score_provider_error",
                    "warning",
                    f"{fallback_warning}; using 1 second of silence",
                )
            )

        score_request = _prepare_score_dependencies(score_file, audio_path)
        try:
            repository_result = resolve_score_repository(
                score_request,
                audio_seconds_at_tick_zero=effective_alignment,
            )
        except ScoreRepositoryError as error:
            raise ValueError(serialize_diagnostics((error.diagnostic,))) from None

        score_resolution = repository_result.resolution
        diagnostics_values.extend(repository_result.diagnostics)

        resolver = None
        anchor = None
        selection = None
        if score_resolution.resolved_score is not None:
            resolved_score = score_resolution.resolved_score
            resolver = ScoreResolver(
                score=resolved_score.score,
                audio_duration_seconds=waveform.shape[-1] / sample_rate,
                audio_seconds_at_tick_zero=effective_alignment,
            )
            repository_result = append_resolver_diagnostics(
                repository_result,
                resolver_diagnostics=resolver.diagnostics,
                effective_alignment=effective_alignment,
            )
            diagnostics_values = diagnostics_values[:1] if fallback_warning is not None else []
            diagnostics_values.extend(repository_result.diagnostics)

        if resolver is None:
            planner_start_beat = start_beat
            if (
                type(start_beat) is int
                and type(effective_beats_per_bar) is int
                and effective_beats_per_bar >= 1
            ):
                planner_start_beat = min(start_beat, effective_beats_per_bar)
            plan = create_audio_clip_plan(
                edit_mode=edit_mode,
                sample_rate=sample_rate,
                sample_count=waveform.shape[-1],
                start_time=start_time,
                end_time=end_time,
                bpm=effective_bpm,
                tempo_unit=effective_tempo_unit,
                beats_per_bar=effective_beats_per_bar,
                beat_unit=effective_beat_unit,
                downbeat_offset=effective_alignment,
                fps=effective_fps,
                start_bar=start_bar,
                start_beat=planner_start_beat,
                start_subdivision=start_subdivision,
                duration_bars=duration_bars,
                duration_beats=duration_beats,
                duration_subdivisions=duration_subdivisions,
                subdivisions_per_beat=effective_subdivisions_per_beat,
            )
        else:
            _validate_score_planning_inputs(
                edit_mode=edit_mode,
                bpm=effective_bpm,
                tempo_unit=effective_tempo_unit,
                beats_per_bar=effective_beats_per_bar,
                beat_unit=effective_beat_unit,
                fps=effective_fps,
                subdivisions_per_beat=effective_subdivisions_per_beat,
            )
            if edit_mode == "Musical":
                try:
                    selection = resolve_score_selection(
                        resolver,
                        start_bar=start_bar,
                        start_beat=start_beat,
                        start_subdivision=start_subdivision,
                        score_end_bar=score_end_bar,
                        score_end_beat=score_end_beat,
                        score_end_subdivision=score_end_subdivision,
                        duration_bars=duration_bars,
                        duration_beats=duration_beats,
                        duration_subdivisions=duration_subdivisions,
                        subdivisions_per_beat=effective_subdivisions_per_beat,
                    )
                except ScoreSelectionError as error:
                    raise ValueError(
                        serialize_diagnostics(
                            (diagnostic(error.code, "error", error.message),)
                        )
                    ) from None
                requested_range = RequestedAudioRange(
                    selection.requested_start_seconds,
                    selection.requested_end_seconds,
                )
                musical_start_tick = selection.start_tick
            else:
                requested_start = _finite_float("start_time", start_time)
                requested_end_value = _finite_float("end_time", end_time)
                requested_end = (
                    waveform.shape[-1] / sample_rate
                    if requested_end_value <= 0
                    else requested_end_value
                )
                requested_range = RequestedAudioRange(
                    requested_start,
                    requested_end,
                )
                musical_start_tick = None

            sample_plan = apply_sample_range(
                requested_range=requested_range,
                sample_rate=sample_rate,
                sample_count=waveform.shape[-1],
                fps=effective_fps,
            )
            anchor = selection_start_score_tick(
                resolver,
                edit_mode=edit_mode,
                clamped=sample_plan.clamped,
                requested_start_seconds=sample_plan.requested_start_seconds,
                returned_start_seconds=sample_plan.start_seconds,
                musical_start_tick=musical_start_tick,
            )
            bar_start_tick, bar_end_tick = resolver.containing_bar_ticks(anchor)
            beat_start_tick, beat_end_tick = resolver.containing_beat_ticks(anchor)
            seconds_per_bar = (
                resolver.tick_to_seconds(bar_end_tick)
                - resolver.tick_to_seconds(bar_start_tick)
            )
            seconds_per_beat = (
                resolver.tick_to_seconds(beat_end_tick)
                - resolver.tick_to_seconds(beat_start_tick)
            )
            musical_position = _score_musical_position(
                edit_mode=edit_mode,
                selection=selection,
                resolver=resolver,
                sample_plan=sample_plan,
                subdivisions_per_beat=effective_subdivisions_per_beat,
            )
            plan = finalize_audio_clip_plan(
                sample_plan,
                ClipTimingMetadata(
                    seconds_per_beat=seconds_per_beat,
                    frames_per_beat=seconds_per_beat * effective_fps,
                    seconds_per_bar=seconds_per_bar,
                    frames_per_bar=seconds_per_bar * effective_fps,
                    musical_position=musical_position,
                ),
            )

        # Trim the waveform tensor -> shape: [channels, time]
        trimmed_waveform = waveform[:, plan.start_sample:plan.end_sample]
        
        # Format for ComfyUI's standard AUDIO type: [batch, channels, time]
        audio_output = {"waveform": trimmed_waveform.unsqueeze(0), "sample_rate": sample_rate}

        out_filename = "" if audio == "none" or not path_exists else os.path.basename(audio)
        section_name = ""
        if resolver is not None:
            section = select_section(resolver.sections(), anchor)
            if section is not None:
                section_name = section.name

        score_format = repository_result.score_format
        return (
            audio_output,
            plan.duration_seconds,
            out_filename,
            plan.start_seconds,
            plan.end_seconds,
            plan.start_frame,
            plan.frame_count,
            plan.seconds_per_beat,
            plan.frames_per_beat,
            plan.seconds_per_bar,
            plan.frames_per_bar,
            plan.musical_position,
            float(effective_bpm),
            float(effective_fps),
            plan.start_frame + plan.frame_count,
            section_name,
            sample_rate,
            score_format,
            repository_result.score_provider,
            serialize_diagnostics(diagnostics_values),
        )
