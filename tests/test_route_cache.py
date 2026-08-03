from concurrent.futures import ThreadPoolExecutor
import math
import threading
import unittest

from route_cache import BoundedByteCache, if_none_match_matches, strong_etag


class BoundedByteCacheTests(unittest.TestCase):
    def test_limits_payload_types_lru_and_eviction(self):
        for entries, size in ((0, 1), (1, 0), (True, 1)):
            with self.assertRaises(ValueError):
                BoundedByteCache(maximum_entries=entries, maximum_bytes=size)
        cache = BoundedByteCache(maximum_entries=2, maximum_bytes=5)
        cache.put("a", b"12", logical_key="a")
        cache.put("b", b"34", logical_key="b")
        self.assertEqual(cache.get("a"), b"12")
        self.assertEqual(cache.snapshot(), (("b", "a"), 4))
        cache.put("c", b"56", logical_key="c")
        self.assertEqual(cache.snapshot(), (("a", "c"), 4))
        with self.assertRaises(TypeError):
            cache.put("x", bytearray(b"x"), logical_key="x")

    def test_logical_replacement_oversize_and_builder_failure(self):
        cache = BoundedByteCache(maximum_entries=3, maximum_bytes=3)
        cache.put(("x", 1), b"old", logical_key="x")
        cache.put(("x", 2), b"large", logical_key="x")
        self.assertEqual(cache.snapshot(), ((), 0))
        with self.assertRaises(RuntimeError):
            cache.get_or_build("bad", lambda: (_ for _ in ()).throw(RuntimeError()), logical_key="bad")
        self.assertEqual(len(cache), 0)

    def test_builder_is_outside_lock_and_concurrent_misses_are_safe(self):
        cache = BoundedByteCache(maximum_entries=4, maximum_bytes=100)
        started = threading.Event()
        release = threading.Event()
        def slow():
            started.set(); release.wait(2); return b"slow"
        with ThreadPoolExecutor(max_workers=4) as pool:
            future = pool.submit(cache.get_or_build, "slow", slow, logical_key="slow")
            self.assertTrue(started.wait(1))
            pool.submit(cache.put, "fast", b"fast", logical_key="fast").result(1)
            release.set()
            self.assertEqual(future.result(1), b"slow")


class ETagTests(unittest.TestCase):
    def test_nested_type_sensitive_opaque_etags(self):
        identity = ("secret/path", (1, 1.0, True, None))
        tag = strong_etag("score", identity)
        self.assertEqual(tag, strong_etag("score", identity))
        self.assertNotEqual(tag, strong_etag("other", identity))
        self.assertNotEqual(strong_etag("score", (1,)), strong_etag("score", (True,)))
        self.assertNotIn("secret", tag)
        self.assertTrue(tag.startswith('"') and tag.endswith('"'))

    def test_invalid_identity_and_if_none_match(self):
        for value in (([],), ({},), (object(),), (math.inf,), (math.nan,)):
            with self.assertRaises((TypeError, ValueError)):
                strong_etag("score", value)
        for namespace in ("", 1):
            with self.assertRaises(TypeError):
                strong_etag(namespace, ())
        tag = strong_etag("score", ())
        self.assertTrue(if_none_match_matches(f'"other", W/{tag}', tag))
        self.assertTrue(if_none_match_matches("*", tag))
        self.assertFalse(if_none_match_matches(None, tag))


if __name__ == "__main__":
    unittest.main()
