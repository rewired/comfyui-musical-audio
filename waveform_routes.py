"""Secure ComfyUI route and bounded cache for waveform peak payloads."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import ntpath
import os
from typing import Callable, NamedTuple
from urllib.parse import urlsplit

try:
    from .route_cache import BoundedByteCache, if_none_match_matches
    from .waveform_peaks import (
        MAXIMUM_PEAKS_PER_SECOND,
        MINIMUM_FINAL_PEAKS_PER_SECOND,
        WAVEFORM_PEAK_CONTENT_TYPE,
        WAVEFORM_PEAK_FORMAT_VERSION,
        WaveformDecodeError,
        generate_waveform_peak_payload,
    )
except ImportError:  # Support direct test imports from the repository root.
    from route_cache import BoundedByteCache, if_none_match_matches  # type: ignore[no-redef]
    from waveform_peaks import (  # type: ignore[no-redef]
        MAXIMUM_PEAKS_PER_SECOND,
        MINIMUM_FINAL_PEAKS_PER_SECOND,
        WAVEFORM_PEAK_CONTENT_TYPE,
        WAVEFORM_PEAK_FORMAT_VERSION,
        WaveformDecodeError,
        generate_waveform_peak_payload,
    )


WAVEFORM_PEAK_ROUTE = "/comfyui-musical-audio/waveform-peaks"
CACHE_CONTROL_VALUE = "private, max-age=0, must-revalidate"
WAVEFORM_VERSION_HEADER = "X-Musical-Audio-Waveform-Version"
MAX_CACHE_ENTRIES = 8
MAX_CACHE_BYTES = 64 * 1024 * 1024
_ROUTE_REGISTRATION_MARKER = "_comfyui_musical_audio_waveform_routes"
_LOGGER = logging.getLogger(__name__)


class InvalidWaveformFilename(ValueError):
    """A request filename is not a safe relative input path."""


class WaveformInputNotFound(FileNotFoundError):
    """A safe relative input path does not identify a regular file."""


class WaveformCacheKey(NamedTuple):
    canonical_path: str
    file_size: int
    modification_time_ns: int
    format_version: int
    density_configuration: tuple[int, int]


class WaveformPayloadCache:
    """A thread-safe in-memory LRU bounded by entry count and payload bytes."""

    def __init__(
        self,
        *,
        maximum_entries: int = MAX_CACHE_ENTRIES,
        maximum_bytes: int = MAX_CACHE_BYTES,
    ) -> None:
        self.maximum_entries = maximum_entries
        self.maximum_bytes = maximum_bytes
        self._cache = BoundedByteCache(
            maximum_entries=maximum_entries,
            maximum_bytes=maximum_bytes,
        )

    def get(self, key: WaveformCacheKey) -> bytes | None:
        return self._cache.get(key)

    def put(self, key: WaveformCacheKey, payload: bytes) -> None:
        if type(payload) is not bytes:
            raise TypeError("cached waveform payloads must be immutable bytes")
        self._cache.put(key, payload, logical_key=key.canonical_path)

    def get_or_build(
        self,
        key: WaveformCacheKey,
        builder: Callable[[], bytes],
    ) -> bytes:
        def build_bytes() -> bytes:
            payload = builder()
            return payload if type(payload) is bytes else bytes(payload)

        return self._cache.get_or_build(
            key,
            build_bytes,
            logical_key=key.canonical_path,
        )

    def clear(self) -> None:
        self._cache.clear()

    def snapshot(self) -> tuple[tuple[WaveformCacheKey, ...], int]:
        """Return LRU-to-MRU keys and total bytes for deterministic tests."""

        keys, total = self._cache.snapshot()
        return tuple(keys), total

    def __len__(self) -> int:
        return len(self._cache)


WAVEFORM_PAYLOAD_CACHE = WaveformPayloadCache()


def resolve_input_file(input_directory: str, filename: str | None) -> str:
    """Resolve a widget filename to a canonical regular file within input."""

    if filename is None or not isinstance(filename, str) or not filename.strip():
        raise InvalidWaveformFilename("A filename is required")
    if filename.strip().lower() == "none":
        raise InvalidWaveformFilename("A selected input filename is required")
    if "\x00" in filename:
        raise InvalidWaveformFilename("The filename is invalid")

    parsed = urlsplit(filename)
    drive, _ = ntpath.splitdrive(filename)
    if (
        parsed.scheme
        or parsed.netloc
        or drive
        or os.path.isabs(filename)
        or ntpath.isabs(filename)
    ):
        raise InvalidWaveformFilename("The filename must be a relative input path")

    path_parts = filename.replace("\\", "/").split("/")
    if any(part == ".." for part in path_parts):
        raise InvalidWaveformFilename("Parent path traversal is not allowed")
    if any(part == "" for part in path_parts):
        raise InvalidWaveformFilename("The filename contains an empty path segment")

    try:
        input_root = os.path.realpath(os.path.abspath(input_directory))
        candidate = os.path.realpath(os.path.join(input_root, *path_parts))
        if os.path.commonpath((input_root, candidate)) != input_root:
            raise InvalidWaveformFilename("The filename escapes the input folder")
    except (OSError, ValueError) as exc:
        raise InvalidWaveformFilename("The filename is invalid") from exc

    if not os.path.isfile(candidate):
        raise WaveformInputNotFound("The input file was not found")
    return candidate


def make_waveform_etag(
    file_size: int,
    modification_time_ns: int,
    *,
    format_version: int = WAVEFORM_PEAK_FORMAT_VERSION,
) -> str:
    """Build an opaque strong ETag without incorporating or exposing a path."""

    if file_size < 0 or modification_time_ns < 0 or format_version < 0:
        raise ValueError("ETag metadata cannot be negative")
    metadata = f"{format_version}:{file_size}:{modification_time_ns}".encode("ascii")
    return f'"maup-{hashlib.sha256(metadata).hexdigest()[:32]}"'


def make_cache_key(canonical_path: str, stat_result: os.stat_result) -> WaveformCacheKey:
    return WaveformCacheKey(
        canonical_path=canonical_path,
        file_size=stat_result.st_size,
        modification_time_ns=stat_result.st_mtime_ns,
        format_version=WAVEFORM_PEAK_FORMAT_VERSION,
        density_configuration=(
            MAXIMUM_PEAKS_PER_SECOND,
            MINIMUM_FINAL_PEAKS_PER_SECOND,
        ),
    )


def _build_cached_payload(key: WaveformCacheKey) -> bytes:
    return WAVEFORM_PAYLOAD_CACHE.get_or_build(
        key,
        lambda: generate_waveform_peak_payload(key.canonical_path),
    )


def _response_headers(etag: str) -> dict[str, str]:
    return {
        "ETag": etag,
        "Cache-Control": CACHE_CONTROL_VALUE,
        WAVEFORM_VERSION_HEADER: str(WAVEFORM_PEAK_FORMAT_VERSION),
    }


async def waveform_peaks_handler(request: object) -> object:
    """Serve a secure, revalidatable version-1 waveform payload."""

    from aiohttp import web

    try:
        import folder_paths

        filename = request.query.get("filename")
        canonical_path = resolve_input_file(
            folder_paths.get_input_directory(), filename
        )
        stat_result = os.stat(canonical_path)
        etag = make_waveform_etag(stat_result.st_size, stat_result.st_mtime_ns)
        headers = _response_headers(etag)

        # Revalidation deliberately precedes every cache access and decode path.
        if if_none_match_matches(request.headers.get("If-None-Match"), etag):
            return web.Response(status=304, headers=headers)

        key = make_cache_key(canonical_path, stat_result)
        payload = await asyncio.to_thread(_build_cached_payload, key)
        return web.Response(
            body=payload,
            status=200,
            headers=headers,
            content_type=WAVEFORM_PEAK_CONTENT_TYPE,
        )
    except InvalidWaveformFilename as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except WaveformInputNotFound as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except WaveformDecodeError:
        return web.json_response({"error": "The input audio could not be decoded"}, status=422)
    except (OSError, EOFError):
        return web.json_response({"error": "The input audio could not be decoded"}, status=422)
    except Exception as exc:  # pragma: no cover - defensive live-server boundary
        _LOGGER.error("Unexpected waveform peak failure (%s)", type(exc).__name__)
        return web.json_response({"error": "Unable to generate waveform peaks"}, status=500)


def register_waveform_routes(prompt_server: object | None = None) -> bool:
    """Register the GET route once for a particular PromptServer instance."""

    if prompt_server is None:
        from server import PromptServer

        prompt_server = PromptServer.instance
    registered = set(getattr(prompt_server, _ROUTE_REGISTRATION_MARKER, ()))
    if WAVEFORM_PEAK_ROUTE in registered:
        return False
    prompt_server.routes.get(WAVEFORM_PEAK_ROUTE)(waveform_peaks_handler)
    registered.add(WAVEFORM_PEAK_ROUTE)
    setattr(prompt_server, _ROUTE_REGISTRATION_MARKER, frozenset(registered))
    return True


__all__ = [
    "CACHE_CONTROL_VALUE",
    "InvalidWaveformFilename",
    "MAX_CACHE_BYTES",
    "MAX_CACHE_ENTRIES",
    "WAVEFORM_PAYLOAD_CACHE",
    "WAVEFORM_PEAK_ROUTE",
    "WAVEFORM_VERSION_HEADER",
    "WaveformCacheKey",
    "WaveformInputNotFound",
    "WaveformPayloadCache",
    "if_none_match_matches",
    "make_cache_key",
    "make_waveform_etag",
    "register_waveform_routes",
    "resolve_input_file",
    "waveform_peaks_handler",
]
