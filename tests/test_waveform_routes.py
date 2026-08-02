"""Tests for secure waveform routing, ETags, cache bounds, and registration."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from waveform_routes import (
    InvalidWaveformFilename,
    WAVEFORM_PEAK_ROUTE,
    WaveformCacheKey,
    WaveformInputNotFound,
    WaveformPayloadCache,
    if_none_match_matches,
    make_waveform_etag,
    register_waveform_routes,
    resolve_input_file,
    waveform_peaks_handler,
)


def cache_key(
    path: str,
    *,
    size: int = 1,
    modification_time_ns: int = 1,
    version: int = 1,
) -> WaveformCacheKey:
    return WaveformCacheKey(path, size, modification_time_ns, version, (1024, 4))


class SecurePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.input_root = Path(self.temporary_directory.name) / "input"
        self.input_root.mkdir()
        (self.input_root / "track.wav").write_bytes(b"wave")
        (self.input_root / "nested").mkdir()
        (self.input_root / "nested" / "clip.mp3").write_bytes(b"audio")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_root_and_nested_input_filenames_are_accepted(self) -> None:
        self.assertEqual(
            resolve_input_file(str(self.input_root), "track.wav"),
            os.path.realpath(self.input_root / "track.wav"),
        )
        self.assertEqual(
            resolve_input_file(str(self.input_root), "nested/clip.mp3"),
            os.path.realpath(self.input_root / "nested" / "clip.mp3"),
        )

    def test_forward_and_windows_separators_are_normalized(self) -> None:
        forward = resolve_input_file(str(self.input_root), "nested/clip.mp3")
        backward = resolve_input_file(str(self.input_root), r"nested\clip.mp3")
        self.assertEqual(forward, backward)

    def test_missing_and_none_filename_are_rejected(self) -> None:
        for filename in (None, "", "   ", "none", "NONE"):
            with self.subTest(filename=filename), self.assertRaises(
                InvalidWaveformFilename
            ):
                resolve_input_file(str(self.input_root), filename)

    def test_parent_traversal_is_rejected(self) -> None:
        for filename in ("../track.wav", r"..\track.wav", "nested/../../x"):
            with self.subTest(filename=filename), self.assertRaises(
                InvalidWaveformFilename
            ):
                resolve_input_file(str(self.input_root), filename)

    def test_absolute_drive_qualified_and_url_paths_are_rejected(self) -> None:
        values = (
            str((self.input_root / "track.wav").resolve()),
            r"C:\input\track.wav",
            r"\\server\share\track.wav",
            "https://example.invalid/track.wav",
            "file:///tmp/track.wav",
        )
        for filename in values:
            with self.subTest(filename=filename), self.assertRaises(
                InvalidWaveformFilename
            ):
                resolve_input_file(str(self.input_root), filename)

    def test_nul_byte_is_rejected(self) -> None:
        with self.assertRaises(InvalidWaveformFilename):
            resolve_input_file(str(self.input_root), "track.wav\x00.mp3")

    def test_symlink_escape_is_rejected_when_supported(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside.wav"
        outside.write_bytes(b"outside")
        link = self.input_root / "escape.wav"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(InvalidWaveformFilename):
            resolve_input_file(str(self.input_root), link.name)

    def test_missing_file_is_distinct_from_invalid_path(self) -> None:
        with self.assertRaises(WaveformInputNotFound):
            resolve_input_file(str(self.input_root), "missing.wav")
        with self.assertRaises(InvalidWaveformFilename):
            resolve_input_file(str(self.input_root), "../missing.wav")

    def test_directory_target_is_not_a_regular_file(self) -> None:
        with self.assertRaises(WaveformInputNotFound):
            resolve_input_file(str(self.input_root), "nested")


class ETagTests(unittest.TestCase):
    def test_same_metadata_creates_same_etag(self) -> None:
        self.assertEqual(make_waveform_etag(100, 200), make_waveform_etag(100, 200))

    def test_size_mtime_and_version_each_affect_etag(self) -> None:
        baseline = make_waveform_etag(100, 200, format_version=1)
        self.assertNotEqual(baseline, make_waveform_etag(101, 200, format_version=1))
        self.assertNotEqual(baseline, make_waveform_etag(100, 201, format_version=1))
        self.assertNotEqual(baseline, make_waveform_etag(100, 200, format_version=2))

    def test_etag_is_quoted_and_does_not_contain_path(self) -> None:
        etag = make_waveform_etag(100, 200)
        self.assertTrue(etag.startswith('"') and etag.endswith('"'))
        self.assertNotIn("input", etag)
        self.assertNotIn("\\", etag)
        self.assertNotIn("/", etag)

    def test_if_none_match_handles_lists_weak_tags_and_wildcard(self) -> None:
        etag = make_waveform_etag(100, 200)
        self.assertTrue(if_none_match_matches(etag, etag))
        self.assertTrue(if_none_match_matches(f'"other", W/{etag}', etag))
        self.assertTrue(if_none_match_matches("*", etag))
        self.assertFalse(if_none_match_matches(None, etag))


class PayloadCacheTests(unittest.TestCase):
    def test_hit_returns_same_bytes_and_updates_lru_order(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=3, maximum_bytes=100)
        first = cache_key("first")
        second = cache_key("second")
        payload = b"payload"
        cache.put(first, payload)
        cache.put(second, b"two")
        self.assertIs(cache.get(first), payload)
        self.assertEqual(cache.snapshot()[0], (second, first))

    def test_entry_count_eviction_removes_least_recently_used(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=2, maximum_bytes=100)
        keys = [cache_key(str(index)) for index in range(3)]
        for key in keys:
            cache.put(key, b"x")
        self.assertIsNone(cache.get(keys[0]))
        self.assertEqual(cache.snapshot()[0], (keys[1], keys[2]))

    def test_byte_budget_eviction_works(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=8, maximum_bytes=5)
        first = cache_key("first")
        second = cache_key("second")
        cache.put(first, b"123")
        cache.put(second, b"456")
        self.assertIsNone(cache.get(first))
        self.assertEqual(cache.snapshot(), ((second,), 3))

    def test_oversized_payload_is_returned_but_not_retained(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=8, maximum_bytes=2)
        key = cache_key("large")
        self.assertEqual(cache.get_or_build(key, lambda: b"large"), b"large")
        self.assertEqual(len(cache), 0)

    def test_oversized_changed_payload_removes_stale_path_entry(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=8, maximum_bytes=3)
        old = cache_key("same", modification_time_ns=1)
        new = cache_key("same", modification_time_ns=2)
        cache.put(old, b"old")
        cache.put(new, b"oversized")
        self.assertEqual(len(cache), 0)

    def test_changed_metadata_invalidates_old_path_entry(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=8, maximum_bytes=100)
        old = cache_key("same", modification_time_ns=1)
        new = cache_key("same", modification_time_ns=2)
        cache.put(old, b"old")
        cache.put(new, b"new")
        self.assertIsNone(cache.get(old))
        self.assertEqual(cache.get(new), b"new")

    def test_builder_exceptions_are_not_cached(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=8, maximum_bytes=100)
        key = cache_key("failure")
        with self.assertRaisesRegex(RuntimeError, "boom"):
            cache.get_or_build(key, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertEqual(len(cache), 0)

    def test_cache_is_thread_safe_under_deterministic_concurrency(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=8, maximum_bytes=1000)
        keys = [cache_key(str(index)) for index in range(8)]

        def exercise(index: int) -> bytes:
            key = keys[index % len(keys)]
            return cache.get_or_build(key, lambda: bytes([index % 256]))

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(exercise, range(64)))
        self.assertEqual(len(results), 64)
        self.assertLessEqual(len(cache), 8)
        self.assertEqual(cache.snapshot()[1], len(cache))

    def test_cache_lock_is_not_held_while_builder_runs(self) -> None:
        cache = WaveformPayloadCache(maximum_entries=8, maximum_bytes=100)
        builder_started = threading.Event()
        release_builder = threading.Event()

        def builder() -> bytes:
            builder_started.set()
            self.assertTrue(release_builder.wait(2))
            return b"built"

        with ThreadPoolExecutor(max_workers=2) as executor:
            build_future = executor.submit(cache.get_or_build, cache_key("slow"), builder)
            self.assertTrue(builder_started.wait(1))
            put_future = executor.submit(cache.put, cache_key("fast"), b"fast")
            put_future.result(timeout=1)
            release_builder.set()
            self.assertEqual(build_future.result(timeout=1), b"built")


class FakeRoutes:
    def __init__(self) -> None:
        self.registrations: list[tuple[str, str, object]] = []

    def get(self, path: str):
        def decorate(handler: object) -> object:
            self.registrations.append(("GET", path, handler))
            return handler

        return decorate


class RouteRegistrationTests(unittest.TestCase):
    def test_registration_is_idempotent_exact_and_get_only(self) -> None:
        server = SimpleNamespace(routes=FakeRoutes())
        self.assertTrue(register_waveform_routes(server))
        self.assertFalse(register_waveform_routes(server))
        self.assertEqual(len(server.routes.registrations), 1)
        method, path, _ = server.routes.registrations[0]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/comfyui-musical-audio/waveform-peaks")
        self.assertEqual(path, WAVEFORM_PEAK_ROUTE)

    def test_registration_does_not_decode_audio(self) -> None:
        server = SimpleNamespace(routes=FakeRoutes())
        with patch("waveform_routes.generate_waveform_peak_payload") as generate:
            register_waveform_routes(server)
        generate.assert_not_called()


class RouteResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_etag_returns_304_before_cache_or_decode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "track.wav"
            path.write_bytes(b"audio")
            stat_result = path.stat()
            etag = make_waveform_etag(stat_result.st_size, stat_result.st_mtime_ns)
            request = SimpleNamespace(
                query={"filename": "track.wav"},
                headers={"If-None-Match": etag},
            )
            folder_paths = ModuleType("folder_paths")
            folder_paths.get_input_directory = lambda: directory  # type: ignore[attr-defined]
            with patch.dict(sys.modules, {"folder_paths": folder_paths}), patch(
                "waveform_routes._build_cached_payload",
                side_effect=AssertionError("304 must bypass payload generation"),
            ):
                response = await waveform_peaks_handler(request)
        self.assertEqual(response.status, 304)
        self.assertIn(response.body, (None, b""))
        self.assertEqual(response.headers["ETag"], etag)

    async def test_missing_invalid_and_missing_file_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder_paths = ModuleType("folder_paths")
            folder_paths.get_input_directory = lambda: directory  # type: ignore[attr-defined]
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                for filename, expected_status in (
                    (None, 400),
                    ("none", 400),
                    ("../escape.wav", 400),
                    ("missing.wav", 404),
                ):
                    with self.subTest(filename=filename):
                        request = SimpleNamespace(query={}, headers={})
                        if filename is not None:
                            request.query["filename"] = filename
                        response = await waveform_peaks_handler(request)
                        self.assertEqual(response.status, expected_status)


if __name__ == "__main__":
    unittest.main()
