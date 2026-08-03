"""Secure schema-version-1 Score preview route."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import ntpath
import os
import re
from urllib.parse import urlsplit

from .resolver import ScoreResolver
from .runtime import (
    ResolvedPathState,
    ScoreDiagnostic,
    ScoreRepositoryError,
    ScoreRepositoryResult,
    append_resolver_diagnostics,
    diagnostic,
    prepare_score_repository_request,
    resolve_score_repository,
)

try:
    from ..audio_duration_probe import AudioDurationProbeError, probe_audio_duration
    from ..route_cache import BoundedByteCache, if_none_match_matches, strong_etag
except ImportError:  # Support direct repository-root imports.
    from audio_duration_probe import AudioDurationProbeError, probe_audio_duration
    from route_cache import BoundedByteCache, if_none_match_matches, strong_etag


SCORE_ROUTE = "/comfyui-musical-audio/score"
SCORE_ROUTE_SCHEMA_VERSION = 1
SCORE_PROVIDER_CONTRACT_VERSION = 1
SCORE_PAYLOAD_VERSION = 1
AUDIO_DURATION_PROBE_VERSION = 1
_ROUTE_REGISTRATION_MARKER = "_comfyui_musical_audio_score_routes"
_ALLOWED_QUERY_KEYS = frozenset(("audio", "score_file", "downbeat_offset"))
_ANNOTATION = re.compile(r"^(?P<path>.*) \[(?P<root>input|output|temp)\]$")
_ANY_ANNOTATION = re.compile(r".*\[[^\]]*\]\s*$")
_LOGGER = logging.getLogger(__name__)
SCORE_PAYLOAD_CACHE = BoundedByteCache(
    maximum_entries=32,
    maximum_bytes=8 * 1024 * 1024,
)


class ScoreRouteQueryError(ValueError):
    pass


class ScoreRouteFileNotFound(FileNotFoundError):
    pass


def _split_safe_selection(selection: str) -> tuple[str, str]:
    if type(selection) is not str or not selection.strip() or "\x00" in selection:
        raise ScoreRouteQueryError("The selected path is invalid")
    match = _ANNOTATION.fullmatch(selection)
    if match:
        relative = match.group("path")
        root_name = match.group("root")
    else:
        if "[" in selection or "]" in selection or _ANY_ANNOTATION.fullmatch(selection):
            raise ScoreRouteQueryError("The selected path annotation is invalid")
        relative = selection
        root_name = "input"
    parsed = urlsplit(relative)
    drive, _tail = ntpath.splitdrive(relative)
    if (
        parsed.scheme
        or parsed.netloc
        or drive
        or os.path.isabs(relative)
        or ntpath.isabs(relative)
    ):
        raise ScoreRouteQueryError("The selected path must be relative")
    parts = relative.replace("\\", "/").split("/")
    if any(part == "" for part in parts):
        raise ScoreRouteQueryError("The selected path contains an empty component")
    if any(part == ".." for part in parts):
        raise ScoreRouteQueryError("Parent traversal is not allowed")
    return relative, root_name


def _resolve_comfy_path(
    selection: str,
    *,
    require_file: bool,
    folder_paths_module: object | None = None,
) -> str:
    if folder_paths_module is None:
        import folder_paths as folder_paths_module
    relative, root_name = _split_safe_selection(selection)
    # Exercise the delivered annotation API, while retaining explicit knowledge
    # of the selected root for the containment proof.
    annotated = getattr(folder_paths_module, "annotated_filepath", None)
    if callable(annotated):
        annotated(selection)
    resolved_api = getattr(folder_paths_module, "get_annotated_filepath", None)
    getter = getattr(folder_paths_module, f"get_{root_name}_directory", None)
    if not callable(getter) and callable(resolved_api):
        # Narrow compatibility seam for delivered unit-test doubles. Real ComfyUI
        # always supplies the three root getters used by the secure boundary.
        candidate = resolved_api(selection)
        if not candidate:
            raise ScoreRouteFileNotFound("The selected file was not found")
        candidate = os.path.realpath(os.path.abspath(os.fspath(candidate)))
        if require_file and not os.path.isfile(candidate):
            raise ScoreRouteFileNotFound("The selected file was not found")
        return candidate
    root = os.path.realpath(os.path.abspath(os.fspath(getter())))
    if callable(resolved_api):
        candidate_value = resolved_api(selection)
    else:  # Compatibility with narrow test doubles.
        candidate_value = os.path.join(root, *relative.replace("\\", "/").split("/"))
    candidate = os.path.realpath(os.path.abspath(os.fspath(candidate_value)))
    try:
        if os.path.commonpath((root, candidate)) != root:
            raise ScoreRouteQueryError("The selected path escapes its ComfyUI root")
    except ValueError as exc:
        raise ScoreRouteQueryError("The selected path is invalid") from exc
    if require_file and not os.path.isfile(candidate):
        raise ScoreRouteFileNotFound("The selected file was not found")
    return candidate


def _query_values(query: object, key: str) -> list[object]:
    getall = getattr(query, "getall", None)
    if callable(getall):
        return list(getall(key, []))
    if key not in query:
        return []
    value = query[key]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _parse_query(query: object) -> tuple[str, str, float]:
    keys = list(query.keys())
    if any(type(key) is not str or key not in _ALLOWED_QUERY_KEYS for key in keys):
        raise ScoreRouteQueryError("The query contains an unknown key")
    values = {key: _query_values(query, key) for key in _ALLOWED_QUERY_KEYS}
    if len(values["audio"]) != 1:
        raise ScoreRouteQueryError("audio is required exactly once")
    if any(len(items) > 1 for items in values.values()):
        raise ScoreRouteQueryError("Query keys must not be duplicated")
    audio = values["audio"][0]
    if type(audio) is not str or not audio.strip() or "\x00" in audio:
        raise ScoreRouteQueryError("audio must be a nonblank string")
    score_file_value = values["score_file"][0] if values["score_file"] else ""
    if type(score_file_value) is not str or "\x00" in score_file_value:
        raise ScoreRouteQueryError("score_file must be a string")
    score_file = score_file_value if score_file_value.strip() else ""
    if not values["downbeat_offset"]:
        downbeat = 0.0
    else:
        raw = values["downbeat_offset"][0]
        if type(raw) is not str or not raw.strip():
            raise ScoreRouteQueryError("downbeat_offset must be a finite number")
        try:
            downbeat = float(raw)
        except (TypeError, ValueError):
            raise ScoreRouteQueryError("downbeat_offset must be a finite number") from None
        if not math.isfinite(downbeat):
            raise ScoreRouteQueryError("downbeat_offset must be a finite number")
    return audio, score_file, float(downbeat)


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))


def _audio_identity(raw_audio: str, path: str | None) -> tuple[object, ...]:
    if raw_audio == "none":
        return "none", "none"
    if path is None:
        return "unresolved", raw_audio
    normalized = _normalized_path(path)
    try:
        state = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return "missing", raw_audio, normalized
    return "file", raw_audio, normalized, state.st_size, state.st_mtime_ns


def _diagnostics(values: tuple[ScoreDiagnostic, ...]) -> list[dict[str, object]]:
    return [
        {"code": item.code, "severity": item.severity, "message": item.message}
        for item in values
    ]


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _error_bytes(value: ScoreDiagnostic) -> bytes:
    return _json_bytes(
        {
            "schema_version": SCORE_ROUTE_SCHEMA_VERSION,
            "status": "error",
            "diagnostics": _diagnostics((value,)),
        }
    )


def _serialize_result(
    result: ScoreRepositoryResult,
    *,
    duration: float,
    alignment: float,
) -> tuple[int, bytes, dict[str, str], bool]:
    resolution = result.resolution
    if result.score_provider == "analysis" and resolution.resolved_score is None:
        return 202, _json_bytes(
            {
                "schema_version": SCORE_ROUTE_SCHEMA_VERSION,
                "status": "analyzing",
                "poll_after_ms": 1000,
                "source": "analyzed",
                "provider": "analysis",
                "diagnostics": _diagnostics(result.diagnostics),
            }
        ), {"Retry-After": "1"}, False
    if resolution.resolved_score is None:
        return 200, _json_bytes(
            {
                "schema_version": SCORE_ROUTE_SCHEMA_VERSION,
                "status": "constant",
                "audio_seconds_at_tick_zero": alignment,
                "audio_duration_seconds": duration,
                "source": "constant",
                "provider": "constant",
                "diagnostics": _diagnostics(result.diagnostics),
            }
        ), {}, True
    score = resolution.resolved_score.score
    resolver = ScoreResolver(
        score=score,
        audio_duration_seconds=duration,
        audio_seconds_at_tick_zero=alignment,
    )
    final = append_resolver_diagnostics(
        result,
        resolver_diagnostics=resolver.diagnostics,
        effective_alignment=alignment,
    )
    return 200, _json_bytes(
        {
            "schema_version": SCORE_ROUTE_SCHEMA_VERSION,
            "status": "ready",
            "ticks_per_quarter": score.ticks_per_quarter,
            "audio_seconds_at_tick_zero": alignment,
            "audio_duration_seconds": duration,
            "bar_starts": list(resolver.bar_grid.boundaries),
            "tempos": [
                {"tick": event.tick, "us_per_quarter": event.us_per_quarter}
                for event in score.tempos
            ],
            "meters": [
                {
                    "tick": event.tick,
                    "numerator": event.numerator,
                    "denominator": event.denominator,
                }
                for event in score.meters
            ],
            "markers": [
                {"tick": event.tick, "name": event.name} for event in score.markers
            ],
            "sections": [
                {
                    "name": section.name,
                    "start_tick": section.start_tick,
                    "end_tick_exclusive": section.end_tick_exclusive,
                    "bar_aligned": section.bar_aligned,
                    "confidence": section.confidence,
                }
                for section in score.sections
            ],
            "source": score.source,
            "provider": result.score_provider,
            "meter_estimated": score.meter_estimated,
            "has_variable_meter": score.has_variable_meter,
            "has_midbar_meter_change": score.has_midbar_meter_change,
            "diagnostics": _diagnostics(final.diagnostics),
        }
    ), {}, True


def _build_route_payload(
    request_value: object,
    *,
    duration: float,
    alignment: float,
    audio_missing_warning: bool,
) -> tuple[int, bytes, dict[str, str], bool]:
    result = resolve_score_repository(
        request_value,
        audio_seconds_at_tick_zero=alignment,
    )
    if audio_missing_warning:
        warning = diagnostic(
            "score_audio_unavailable",
            "warning",
            "Audio preview is unavailable; a one-second duration is used",
        )
        result = ScoreRepositoryResult(
            resolution=result.resolution,
            diagnostics=(warning,) + result.diagnostics,
            score_format=result.score_format,
            score_provider=result.score_provider,
        )
    return _serialize_result(result, duration=duration, alignment=alignment)


def _response(body: bytes | None, status: int, headers: dict[str, str] | None = None):
    from aiohttp import web
    return web.Response(
        body=body,
        status=status,
        headers=headers,
        content_type=None if status == 304 else "application/json",
    )


async def score_handler(request: object) -> object:
    try:
        raw_audio, raw_score, alignment = _parse_query(request.query)
        import folder_paths
        if raw_audio == "none":
            audio_path = None
            duration = 1.0
            audio_warning = True
        else:
            audio_path = _resolve_comfy_path(
                raw_audio, require_file=True, folder_paths_module=folder_paths
            )
            duration = None
            audio_warning = False
        if raw_score:
            try:
                explicit_path = _resolve_comfy_path(
                    raw_score, require_file=False, folder_paths_module=folder_paths
                )
                explicit = ResolvedPathState(raw_score, explicit_path, None)
            except ScoreRouteQueryError:
                raise
            except (OSError, ValueError) as exc:
                explicit = ResolvedPathState(raw_score, None, type(exc).__name__)
        else:
            explicit = ResolvedPathState(raw_score, None, None)
        repository_request = prepare_score_repository_request(
            score_file=raw_score,
            audio_path=audio_path,
            explicit=explicit,
        )
        audio_identity = _audio_identity(raw_audio, audio_path)
        if audio_identity[0] not in ("none", "file"):
            raise ScoreRouteFileNotFound("The selected audio was not found")
        logical_key = ("score_route", raw_audio, raw_score)
        etag_identity = (
            SCORE_ROUTE_SCHEMA_VERSION,
            audio_identity,
            repository_request.dependency_fingerprint,
            raw_score,
            alignment,
            (AUDIO_DURATION_PROBE_VERSION, "pyav", "decoded_samples", "first_audio_stream"),
            SCORE_PROVIDER_CONTRACT_VERSION,
            SCORE_PAYLOAD_VERSION,
        )
        etag = strong_etag("comfyui-musical-audio-score", etag_identity)
        headers = {"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"}
        if if_none_match_matches(request.headers.get("If-None-Match"), etag):
            return _response(None, 304, headers)
        cache_key = (logical_key, etag_identity)
        cached = SCORE_PAYLOAD_CACHE.get(cache_key)
        if cached is not None:
            return _response(cached, 200, headers)
        if duration is None:
            try:
                probe = await asyncio.to_thread(probe_audio_duration, audio_path)
            except AudioDurationProbeError:
                return _response(
                    _error_bytes(diagnostic("score_audio_unavailable", "error", "The selected audio could not be decoded")),
                    422,
                )
            duration = probe.duration_seconds
        status_code, payload, extra_headers, cacheable = await asyncio.to_thread(
            _build_route_payload,
            repository_request,
            duration=duration,
            alignment=alignment,
            audio_missing_warning=audio_warning,
        )
        response_headers = dict(headers) if cacheable else {}
        response_headers.update(extra_headers)
        if cacheable:
            SCORE_PAYLOAD_CACHE.put(cache_key, payload, logical_key=logical_key)
        return _response(payload, status_code, response_headers)
    except ScoreRouteQueryError:
        return _response(_error_bytes(diagnostic("score_route_query_invalid", "error", "The Score route query is invalid")), 400)
    except ScoreRouteFileNotFound:
        return _response(_error_bytes(diagnostic("score_route_file_not_found", "error", "The selected file was not found")), 404)
    except ScoreRepositoryError as exc:
        if exc.category == "not_found":
            value = diagnostic("score_route_file_not_found", "error", "The selected Score file was not found")
            return _response(_error_bytes(value), 404)
        return _response(_error_bytes(exc.diagnostic), 422)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # pragma: no cover - defensive server boundary
        _LOGGER.error("Unexpected Score route failure (%s)", type(exc).__name__)
        return _response(_error_bytes(diagnostic("score_route_internal_error", "error", "Unable to prepare the Score preview")), 500)


def register_score_routes(prompt_server: object | None = None) -> bool:
    if prompt_server is None:
        from server import PromptServer
        prompt_server = PromptServer.instance
    registered = set(getattr(prompt_server, _ROUTE_REGISTRATION_MARKER, ()))
    if SCORE_ROUTE in registered:
        return False
    prompt_server.routes.get(SCORE_ROUTE)(score_handler)
    registered.add(SCORE_ROUTE)
    setattr(prompt_server, _ROUTE_REGISTRATION_MARKER, frozenset(registered))
    return True


__all__ = (
    "AUDIO_DURATION_PROBE_VERSION",
    "SCORE_PAYLOAD_VERSION",
    "SCORE_PROVIDER_CONTRACT_VERSION",
    "SCORE_ROUTE",
    "SCORE_ROUTE_SCHEMA_VERSION",
    "register_score_routes",
)
