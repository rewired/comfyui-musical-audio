import importlib
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import audio_duration_probe
from audio_duration_probe import AudioDurationProbeError, probe_audio_duration


class Container:
    def __init__(self, *, rate=48000, frames=(100, 200), streams=True, failure=None):
        stream = SimpleNamespace(codec_context=SimpleNamespace(sample_rate=rate))
        self.stream = stream
        self.streams = SimpleNamespace(audio=[stream] if streams else [])
        self.frames = frames
        self.failure = failure
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def decode(self, stream):
        if stream is not self.stream: raise AssertionError("first stream not selected")
        if self.failure: raise self.failure
        for samples in self.frames:
            yield SimpleNamespace(samples=samples)


def fake_av(container):
    module = ModuleType("av")
    module.open = lambda _filename, mode="r": container
    return module


class ProbeTests(unittest.TestCase):
    def run_probe(self, container):
        with patch.dict(sys.modules, {"av": fake_av(container)}):
            return probe_audio_duration("private.wav")

    def test_counts_streamed_frames_exactly(self):
        result = self.run_probe(Container(frames=(100, 200, 300)))
        self.assertEqual((result.sample_rate, result.sample_count), (48000, 600))
        self.assertEqual(result.duration_seconds, 600 / 48000)

    def test_failures_are_safe_and_bounded(self):
        cases = (
            Container(streams=False), Container(rate=0), Container(frames=()),
            Container(frames=(0,)), Container(failure=OSError("private/path")),
            Container(failure=EOFError("private/path")), Container(failure=RuntimeError("private/path")),
        )
        for container in cases:
            with self.subTest(container=container), self.assertRaises(AudioDurationProbeError) as captured:
                self.run_probe(container)
            self.assertNotIn("private/path", str(captured.exception))
            self.assertLessEqual(len(str(captured.exception)), 100)

    def test_process_exceptions_propagate(self):
        for error in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.run_probe(Container(failure=error))

    def test_import_has_no_pyav_or_torch_requirement(self):
        with patch.dict(sys.modules, {"av": None, "torch": None}):
            importlib.reload(audio_duration_probe)
        self.assertNotIn("torch", audio_duration_probe.__dict__)


if importlib.util.find_spec("av") is not None:
    class RealPyAVParityTests(unittest.TestCase):
        def test_real_wav_duration_parity(self):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "probe.wav"
                with wave.open(str(path), "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(8000)
                    output.writeframes(b"\0" * 1600)
                result = probe_audio_duration(str(path))
            self.assertEqual((result.sample_rate, result.sample_count, result.duration_seconds), (8000, 800, 0.1))

        def test_real_flac_duration_parity(self):
            import av
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "probe.flac"
                with av.open(str(path), mode="w") as output:
                    stream = output.add_stream("flac", rate=8000)
                    frame = av.AudioFrame(format="s16", layout="mono", samples=800)
                    frame.sample_rate = 8000
                    frame.planes[0].update(b"\0" * 1600)
                    for packet in stream.encode(frame):
                        output.mux(packet)
                    for packet in stream.encode():
                        output.mux(packet)
                result = probe_audio_duration(str(path))
            self.assertEqual((result.sample_rate, result.sample_count, result.duration_seconds), (8000, 800, 0.1))


if __name__ == "__main__":
    unittest.main()
