import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from audio_duration_probe import AudioDurationProbeError
from score.routes import (
    SCORE_PAYLOAD_CACHE, SCORE_ROUTE, ScoreRouteQueryError, _audio_identity,
    _parse_query, _resolve_comfy_path, register_score_routes, score_handler,
)
from score.model import MeterEvent, Score, Section, TempoEvent
from score.serialize import score_to_dict


class MultiQuery(dict):
    def __init__(self, pairs=()):
        self.pairs = list(pairs)
        super().__init__(self.pairs)
    def keys(self): return [key for key, _ in self.pairs]
    def getall(self, key, default):
        values = [value for current, value in self.pairs if current == key]
        return values or default


class FolderPaths(ModuleType):
    def __init__(self, base):
        super().__init__("folder_paths")
        self.roots = {name: str(Path(base) / name) for name in ("input", "output", "temp")}
        for root in self.roots.values(): Path(root).mkdir()
    def annotated_filepath(self, name):
        for root in self.roots:
            suffix = f" [{root}]"
            if name.endswith(suffix): return name[:-len(suffix)], self.roots[root]
        return name, None
    def get_annotated_filepath(self, name, default_dir=None):
        relative, root = self.annotated_filepath(name)
        return str(Path(root or default_dir or self.roots["input"]) / relative)
    def get_input_directory(self): return self.roots["input"]
    def get_output_directory(self): return self.roots["output"]
    def get_temp_directory(self): return self.roots["temp"]


class QueryAndPathTests(unittest.TestCase):
    def test_query_contract(self):
        self.assertEqual(_parse_query(MultiQuery((("audio", "none"),))), ("none", "", 0.0))
        self.assertEqual(_parse_query(MultiQuery((("audio", "none"), ("score_file", "none"), ("downbeat_offset", "-0.5")))), ("none", "none", -0.5))
        invalid = ((), (("audio", ""),), (("audio", "x"), ("audio", "y")), (("audio", "x"), ("bad", "y")), (("audio", "x"), ("downbeat_offset", "nan")))
        for pairs in invalid:
            with self.subTest(pairs=pairs), self.assertRaises(ScoreRouteQueryError):
                _parse_query(MultiQuery(pairs))

    def test_annotations_containment_and_regular_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = FolderPaths(directory)
            for root in folder.roots:
                path = Path(folder.roots[root]) / "track.flac"
                path.write_bytes(b"audio")
                selection = "track.flac" if root == "input" else f"track.flac [{root}]"
                self.assertEqual(_resolve_comfy_path(selection, require_file=True, folder_paths_module=folder), os.path.realpath(path))
            for unsafe in ("../x", "a//b", r"C:\x", "https://x/y", "x [unknown]", "x\x00y"):
                with self.subTest(unsafe=unsafe), self.assertRaises(ScoreRouteQueryError):
                    _resolve_comfy_path(unsafe, require_file=False, folder_paths_module=folder)

    def test_audio_identity_order(self):
        self.assertEqual(_audio_identity("none", None), ("none", "none"))
        self.assertEqual(_audio_identity("raw", None), ("unresolved", "raw"))


class FakeRoutes:
    def __init__(self): self.values = []
    def get(self, path):
        def decorate(handler): self.values.append((path, handler)); return handler
        return decorate


class FakeResponse:
    def __init__(self, *, body=None, status=200, headers=None, content_type=None):
        self.body = body
        self.status = status
        self.headers = {} if headers is None else dict(headers)
        if content_type is not None:
            self.headers.setdefault("Content-Type", content_type)


def fake_aiohttp():
    module = ModuleType("aiohttp")
    module.web = SimpleNamespace(Response=FakeResponse)
    return module


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        SCORE_PAYLOAD_CACHE.clear()
        self.aiohttp_patch = patch.dict(sys.modules, {"aiohttp": fake_aiohttp()})
        self.aiohttp_patch.start()
        self.addAsyncCleanup(self.aiohttp_patch.stop)

    async def test_none_audio_constant_payload_cache_and_304(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = FolderPaths(directory)
            request = SimpleNamespace(query=MultiQuery((("audio", "none"),)), headers={})
            with patch.dict(sys.modules, {"folder_paths": folder}):
                response = await score_handler(request)
                body = json.loads(response.body)
                self.assertEqual((response.status, tuple(body)), (200, ("schema_version", "status", "audio_seconds_at_tick_zero", "audio_duration_seconds", "source", "provider", "diagnostics")))
                self.assertEqual(body["status"], "constant")
                self.assertEqual(body["diagnostics"][0]["code"], "score_audio_unavailable")
                etag = response.headers["ETag"]
                with patch("score.routes.SCORE_PAYLOAD_CACHE.get", side_effect=AssertionError("cache accessed")):
                    second = await score_handler(SimpleNamespace(query=request.query, headers={"If-None-Match": etag}))
                self.assertEqual(second.status, 304)

    async def test_none_audio_explicit_score_returns_exact_ready_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = FolderPaths(directory)
            score = Score(
                ticks_per_quarter=480,
                tempos=(TempoEvent(0, 500000),),
                meters=(MeterEvent(0, 4, 4),),
                markers=(),
                sections=(Section("Verse", 0, 1920, True, 1.0),),
                source="json",
            )
            path = Path(folder.roots["input"]) / "song.score.json"
            path.write_text(json.dumps(score_to_dict(score)), encoding="utf-8")
            query = MultiQuery((("audio", "none"), ("score_file", path.name)))
            with patch.dict(sys.modules, {"folder_paths": folder}):
                response = await score_handler(SimpleNamespace(query=query, headers={}))
            body = json.loads(response.body)
            self.assertEqual(response.status, 200)
            self.assertEqual(body["status"], "ready")
            self.assertEqual(body["provider"], "explicit")
            self.assertGreaterEqual(len(body["bar_starts"]), 2)
            self.assertEqual(tuple(body["sections"][0]), ("name", "start_tick", "end_tick_exclusive", "bar_aligned", "confidence"))

    async def test_errors_are_compact_and_registration_is_idempotent(self):
        response = await score_handler(SimpleNamespace(query=MultiQuery(), headers={}))
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.body)["diagnostics"][0]["code"], "score_route_query_invalid")
        server = SimpleNamespace(routes=FakeRoutes())
        self.assertTrue(register_score_routes(server)); self.assertFalse(register_score_routes(server))
        self.assertEqual(server.routes.values[0][0], SCORE_ROUTE)

    async def test_missing_malformed_probe_and_internal_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = FolderPaths(directory)
            with patch.dict(sys.modules, {"folder_paths": folder}):
                missing = await score_handler(SimpleNamespace(
                    query=MultiQuery((("audio", "missing.wav"),)), headers={}
                ))
                self.assertEqual(missing.status, 404)

                malformed_path = Path(folder.roots["input"]) / "broken.json"
                malformed_path.write_text("{broken", encoding="utf-8")
                malformed = await score_handler(SimpleNamespace(
                    query=MultiQuery((("audio", "none"), ("score_file", "broken.json"))), headers={}
                ))
                self.assertEqual(malformed.status, 422)
                self.assertEqual(json.loads(malformed.body)["diagnostics"][0]["code"], "score_provider_error")

                audio_path = Path(folder.roots["input"]) / "audio.flac"
                audio_path.write_bytes(b"not decoded in this test")
                with patch("score.routes.probe_audio_duration", side_effect=AudioDurationProbeError("safe")):
                    unavailable = await score_handler(SimpleNamespace(
                        query=MultiQuery((("audio", "audio.flac"),)), headers={}
                    ))
                self.assertEqual(unavailable.status, 422)
                self.assertEqual(json.loads(unavailable.body)["diagnostics"][0]["code"], "score_audio_unavailable")

        with self.assertLogs("score.routes", level="ERROR") as captured, patch(
            "score.routes._parse_query", side_effect=RuntimeError("private/path defect")
        ):
            internal = await score_handler(SimpleNamespace(query=MultiQuery(), headers={}))
        self.assertEqual(internal.status, 500)
        self.assertEqual(json.loads(internal.body)["diagnostics"][0]["code"], "score_route_internal_error")
        self.assertIn("RuntimeError", captured.output[0])
        self.assertNotIn("private/path", captured.output[0])


if __name__ == "__main__":
    unittest.main()
