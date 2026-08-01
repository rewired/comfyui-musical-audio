import folder_paths
import os
import torch
import av

try:
    from .audio_clip_plan import create_audio_clip_plan
except ImportError:  # Support direct module loading outside the package.
    from audio_clip_plan import create_audio_clip_plan


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
        except:
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
                except:
                    files = sorted(all_files)
        
        if not files or len(files) == 0:
            files = ["none"]

        return {
            "required": {
                "audio": (files, {"audio_upload": True}), # Moved to the top so it appears first
                "start_time": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 100000.0, "step": 0.01}),
                "end_time": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 100000.0, "step": 0.01}),
                "duration": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 100000.0, "step": 0.01}),
                "edit_mode": (["Seconds", "Musical"], {"default": "Seconds"}),
                "bpm": ("FLOAT", {"default": 120.0, "min": 0.01, "step": 0.01}),
                "tempo_unit": (["Quarter", "Eighth", "Dotted Quarter"], {"default": "Quarter"}),
                "beats_per_bar": ("INT", {"default": 4, "min": 1}),
                "beat_unit": ("INT", {"default": 4, "min": 1}),
                "downbeat_offset": ("FLOAT", {"default": 0.0, "step": 0.001}),
                "fps": ("FLOAT", {"default": 24.0, "min": 0.001, "step": 0.001}),
                "start_bar": ("INT", {"default": 1, "min": 1}),
                "start_beat": ("INT", {"default": 1, "min": 1}),
                "start_subdivision": ("INT", {"default": 0, "min": 0}),
                "duration_bars": ("INT", {"default": 4, "min": 0}),
                "duration_beats": ("INT", {"default": 0, "min": 0}),
                "duration_subdivisions": ("INT", {"default": 0, "min": 0}),
                "subdivisions_per_beat": ("INT", {"default": 4, "min": 1}),
                "snap_mode": (["Off", "Bar", "Beat", "Subdivision", "Video Frame"], {"default": "Off"}),
            },
            "optional": {
                "audioUI": ("AUDIO_UI",)
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
    )
    FUNCTION = "load_audio"

    @classmethod
    def VALIDATE_INPUTS(cls, audio, **kwargs):
        # CRITICAL FIX: This bypasses the "Value not in list" error.
        # By returning True, we tell ComfyUI to allow the 'audio' value even if it isn't in 
        # the current dropdown list. This allows the execution to reach load_audio(),
        # where our fallback silence logic can handle the missing file gracefully.
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
        **kwargs,
    ):
        # Determine the annotated file path if a file is actually selected
        # We wrap this in a try/except because get_annotated_filepath can fail if 
        # the input string is malformed or doesn't follow expected paths.
        try:
            audio_path = folder_paths.get_annotated_filepath(audio) if audio != "none" else ""
        except:
            audio_path = ""
        
        # --- FALLBACK LOGIC ---
        # If the file is 'none' or doesn't exist on disk, provide 1 second of silence
        if audio == "none" or not audio_path or not os.path.exists(audio_path):
            missing_info = audio if audio != "none" else "None selected"
            print(f"!!! [MusicalLoadAudioUI] Warning: Audio file '{missing_info}' not found. Outputting 1 second of silence.")
            
            sample_rate = 44100
            # 1 second of silence (stereo) -> shape [channels, time]
            waveform = torch.zeros((2, 44100))
        else:
            try:
                waveform, sample_rate = load_audio_file(audio_path)
            except Exception as e:
                # If decoding fails for any reason, fallback to silence rather than crashing the workflow
                print(f"!!! [MusicalLoadAudioUI] Error decoding {audio}: {e}. Falling back to silence.")
                sample_rate = 44100
                waveform = torch.zeros((2, 44100))

        # duration remains a positional compatibility widget, while snap_mode is
        # reserved for the later frontend timeline implementation.
        _ = duration, snap_mode

        plan = create_audio_clip_plan(
            edit_mode=edit_mode,
            sample_rate=sample_rate,
            sample_count=waveform.shape[-1],
            start_time=start_time,
            end_time=end_time,
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

        # Trim the waveform tensor -> shape: [channels, time]
        trimmed_waveform = waveform[:, plan.start_sample:plan.end_sample]
        
        # Format for ComfyUI's standard AUDIO type: [batch, channels, time]
        audio_output = {"waveform": trimmed_waveform.unsqueeze(0), "sample_rate": sample_rate}

        out_filename = "" if audio == "none" or not audio_path or not os.path.exists(audio_path) else os.path.basename(audio)
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
        )
