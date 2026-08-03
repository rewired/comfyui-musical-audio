"""Thread-safe bounded byte caching and opaque HTTP ETag helpers."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable
import hashlib
import math
import threading


class BoundedByteCache:
    """An LRU of immutable payloads bounded by entry count and total bytes."""

    def __init__(self, *, maximum_entries: int, maximum_bytes: int) -> None:
        if type(maximum_entries) is not int or maximum_entries <= 0:
            raise ValueError("maximum_entries must be a positive built-in int")
        if type(maximum_bytes) is not int or maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be a positive built-in int")
        self.maximum_entries = maximum_entries
        self.maximum_bytes = maximum_bytes
        self._entries: OrderedDict[Hashable, tuple[bytes, Hashable]] = OrderedDict()
        self._total_bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _require_hashable(value: Hashable, name: str) -> None:
        try:
            hash(value)
        except TypeError:
            raise TypeError(f"{name} must be hashable") from None

    def get(self, key: Hashable) -> bytes | None:
        self._require_hashable(key, "key")
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[0]

    def put(
        self,
        key: Hashable,
        payload: bytes,
        *,
        logical_key: Hashable,
    ) -> None:
        self._require_hashable(key, "key")
        self._require_hashable(logical_key, "logical_key")
        if type(payload) is not bytes:
            raise TypeError("cached payloads must be immutable bytes")

        with self._lock:
            obsolete = [
                cached_key
                for cached_key, (_cached_payload, cached_logical_key) in self._entries.items()
                if cached_logical_key == logical_key
            ]
            for cached_key in obsolete:
                cached_payload, _ = self._entries.pop(cached_key)
                self._total_bytes -= len(cached_payload)

            if len(payload) > self.maximum_bytes:
                return

            self._entries[key] = (payload, logical_key)
            self._total_bytes += len(payload)
            while (
                len(self._entries) > self.maximum_entries
                or self._total_bytes > self.maximum_bytes
            ):
                _evicted_key, (evicted_payload, _logical_key) = self._entries.popitem(
                    last=False
                )
                self._total_bytes -= len(evicted_payload)

    def get_or_build(
        self,
        key: Hashable,
        builder: Callable[[], bytes],
        *,
        logical_key: Hashable,
    ) -> bytes:
        if not callable(builder):
            raise TypeError("builder must be callable")
        payload = self.get(key)
        if payload is not None:
            return payload
        payload = builder()
        self.put(key, payload, logical_key=logical_key)
        return payload

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0

    def snapshot(self) -> tuple[tuple[Hashable, ...], int]:
        with self._lock:
            return tuple(self._entries), self._total_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _encode_identity(value: object) -> bytes:
    if value is None:
        return b"n;"
    if type(value) is bool:
        return b"b1;" if value else b"b0;"
    if type(value) is int:
        return b"i" + str(value).encode("ascii") + b";"
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("ETag identity floats must be finite")
        return b"f" + value.hex().encode("ascii") + b";"
    if type(value) is str:
        encoded = value.encode("utf-8")
        return b"s" + str(len(encoded)).encode("ascii") + b":" + encoded + b";"
    if type(value) is tuple:
        return (
            b"t"
            + str(len(value)).encode("ascii")
            + b":"
            + b"".join(_encode_identity(item) for item in value)
            + b";"
        )
    raise TypeError("ETag identity contains an unsupported or mutable value")


def strong_etag(namespace: str, identity: tuple[object, ...]) -> str:
    if type(namespace) is not str or not namespace:
        raise TypeError("namespace must be a nonempty built-in string")
    if type(identity) is not tuple:
        raise TypeError("identity must be a tuple")
    digest = hashlib.sha256(
        _encode_identity(("namespace", namespace, "identity", identity))
    ).hexdigest()
    return f'"{digest}"'


def if_none_match_matches(value: str | None, etag: str) -> bool:
    if value is None:
        return False
    if type(value) is not str or type(etag) is not str:
        return False
    for candidate in value.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == etag:
            return True
    return False


__all__ = ["BoundedByteCache", "if_none_match_matches", "strong_etag"]
