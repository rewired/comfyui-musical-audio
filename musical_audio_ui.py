import folder_paths
import os
import torch
import av

try:
    from .audio_clip_plan import create_audio_clip_plan
except ImportError:  # Support direct module loading outside the package.
    from audio_clip_plan import create_audio_clip_plan


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
        audio_path = folder_paths.get_annotated_filepath(audio)
    except Exception as error:
        return None, error
    return audio_path or None, None


def _concise_exception_message(error, limit=200):
    """Format bounded exception text for a single-line node output."""
    message = " ".join(str(error).split()) or "no exception message"
    if len(message) > limit:
        return f"{message[:limit - 3]}..."
    return message


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
    )
    FUNCTION = "load_audio"

    @classmethod
    def IS_CHANGED(cls, audio, **_kwargs):
        if audio == "none":
            return ("none", "none")

        audio_path, resolution_error = _resolve_audio_path(audio)
        if resolution_error is not None or audio_path is None:
            return ("unresolved", audio)

        try:
            normalized_path = os.path.normcase(
                os.path.abspath(os.path.normpath(os.fspath(audio_path)))
            )
        except (OSError, TypeError, ValueError):
            return ("unresolved", audio)

        try:
            file_stat = os.stat(normalized_path)
        except (OSError, ValueError):
            return ("missing", audio, normalized_path)

        return (
            "file",
            audio,
            normalized_path,
            file_stat.st_size,
            file_stat.st_mtime_ns,
        )

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

        effective_start_beat = start_beat
        if (
            type(start_beat) is int
            and type(effective_beats_per_bar) is int
            and effective_beats_per_bar >= 1
        ):
            effective_start_beat = min(start_beat, effective_beats_per_bar)

        # Determine the annotated file path if a file is actually selected
        # We wrap this in a try/except because get_annotated_filepath can fail if 
        # the input string is malformed or doesn't follow expected paths.
        audio_path, resolution_error = _resolve_audio_path(audio)
        path_exists = False
        if audio_path is not None:
            try:
                path_exists = os.path.exists(audio_path)
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
            downbeat_offset=effective_downbeat_offset,
            fps=effective_fps,
            start_bar=start_bar,
            start_beat=effective_start_beat,
            start_subdivision=start_subdivision,
            duration_bars=duration_bars,
            duration_beats=duration_beats,
            duration_subdivisions=duration_subdivisions,
            subdivisions_per_beat=effective_subdivisions_per_beat,
        )

        # Trim the waveform tensor -> shape: [channels, time]
        trimmed_waveform = waveform[:, plan.start_sample:plan.end_sample]
        
        # Format for ComfyUI's standard AUDIO type: [batch, channels, time]
        audio_output = {"waveform": trimmed_waveform.unsqueeze(0), "sample_rate": sample_rate}

        out_filename = "" if audio == "none" or not path_exists else os.path.basename(audio)
        musical_position = plan.musical_position
        if fallback_warning is not None:
            musical_position = (
                f"{musical_position} · WARNING: {fallback_warning}; "
                "using 1 second of silence"
            )
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
            musical_position,
            float(effective_bpm),
            float(effective_fps),
        )
