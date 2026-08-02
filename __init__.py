from .musical_audio_ui import MusicalLoadAudioUI
from .waveform_routes import register_waveform_routes


register_waveform_routes()


NODE_CLASS_MAPPINGS = {
    "MusicalLoadAudioUI": MusicalLoadAudioUI,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MusicalLoadAudioUI": "Load Audio UI — Musical Grid",
}

WEB_DIRECTORY = "./js"


__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
