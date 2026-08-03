"""Pure shared Score repository and diagnostic service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
import math
from typing import Literal

from .model import ProviderKind, ScoreFormat
from .providers import (
    ANALYSIS_PROVIDER_CONTRACT,
    BLANK_SCORE_FILE,
    SCORE_FINGERPRINT_VERSION,
    AnalysisProvider,
    ConstantProvider,
    ExplicitFileProvider,
    MidiSidecarProvider,
    ProviderChainError,
    ScoreResolution,
    SidecarJsonProvider,
    derive_automatic_candidate_paths,
    fingerprint_candidate,
    resolve_provider_chain,
)


_AUTOMATIC_SCORE_UNAVAILABLE = ("automatic_score_candidate", "unavailable")
_ALIGNMENT_DIVERGENCE_MESSAGE = (
    "stored Score alignment differs from the effective node alignment; "
    "the node value remains authoritative"
)


@dataclass(frozen=True)
class ResolvedPathState:
    selection: str
    resolved_path: str | None
    resolution_error: str | None


@dataclass(frozen=True)
class ScoreDiagnostic:
    code: str
    severity: Literal["warning", "error"]
    message: str


@dataclass(frozen=True)
class ScoreRepositoryRequest:
    score_file: str
    audio_path: str | None
    explicit: ResolvedPathState
    json_sidecar_path: str | None
    midi_sidecar_path: str | None
    dependency_fingerprint: tuple[object, ...]


@dataclass(frozen=True)
class ScoreRepositoryResult:
    resolution: ScoreResolution
    diagnostics: tuple[ScoreDiagnostic, ...]
    score_format: ScoreFormat
    score_provider: ProviderKind


class ScoreRepositoryError(ValueError):
    provider: ProviderKind
    category: Literal["not_found", "unprocessable"]
    diagnostic: ScoreDiagnostic

    def __init__(
        self,
        provider: ProviderKind,
        category: Literal["not_found", "unprocessable"],
        value: ScoreDiagnostic,
    ) -> None:
        self.provider = provider
        self.category = category
        self.diagnostic = value
        super().__init__(value.message)


def diagnostic(code: str, severity: str, message: object) -> ScoreDiagnostic:
    if type(code) is not str or not code:
        raise TypeError("code must be a nonempty built-in string")
    if severity not in ("warning", "error"):
        raise ValueError("severity must be warning or error")
    normalized = " ".join(str(message).split()) or "no diagnostic message"
    if len(normalized) > 200:
        normalized = f"{normalized[:197]}..."
    return ScoreDiagnostic(code=code, severity=severity, message=normalized)


def serialize_diagnostics(values: Sequence[ScoreDiagnostic]) -> str:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("values must be a sequence of ScoreDiagnostic values")
    encoded = []
    for value in values:
        if type(value) is not ScoreDiagnostic:
            raise TypeError("values must contain only ScoreDiagnostic values")
        encoded.append(
            {
                "code": value.code,
                "severity": value.severity,
                "message": value.message,
            }
        )
    return json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))


def _validate_path_state(value: ResolvedPathState, score_file: str) -> None:
    if type(value) is not ResolvedPathState:
        raise TypeError("explicit must be a ResolvedPathState")
    if type(value.selection) is not str or value.selection != score_file:
        raise ValueError("explicit selection must equal score_file")
    if value.resolved_path is not None and type(value.resolved_path) is not str:
        raise TypeError("resolved_path must be a built-in string or None")
    if value.resolution_error is not None and type(value.resolution_error) is not str:
        raise TypeError("resolution_error must be a built-in string or None")
    if value.resolved_path is not None and value.resolution_error is not None:
        raise ValueError("a resolved path cannot also contain a resolution error")


def _score_fingerprint(
    *,
    score_file: str,
    explicit: ResolvedPathState,
    json_sidecar_path: str | None,
    midi_sidecar_path: str | None,
) -> tuple[object, ...]:
    supplied = bool(score_file.strip())
    values: list[object] = [
        SCORE_FINGERPRINT_VERSION,
        score_file if supplied else BLANK_SCORE_FILE,
    ]
    if supplied:
        if explicit.resolved_path is None:
            values.append(("explicit", score_file, "unresolved"))
        else:
            values.append(fingerprint_candidate("explicit", explicit.resolved_path))
    if json_sidecar_path is None:
        values.append(_AUTOMATIC_SCORE_UNAVAILABLE)
    else:
        values.append(fingerprint_candidate("json_sidecar", json_sidecar_path))
    if midi_sidecar_path is None:
        values.append(_AUTOMATIC_SCORE_UNAVAILABLE)
    else:
        values.append(fingerprint_candidate("midi_sidecar", midi_sidecar_path))
    values.append(ANALYSIS_PROVIDER_CONTRACT)
    return tuple(values)


def prepare_score_repository_request(
    *,
    score_file: str,
    audio_path: str | None,
    explicit: ResolvedPathState,
) -> ScoreRepositoryRequest:
    if type(score_file) is not str:
        raise TypeError("score_file must be a built-in string")
    if audio_path is not None and type(audio_path) is not str:
        raise TypeError("audio_path must be a built-in string or None")
    _validate_path_state(explicit, score_file)

    if audio_path is None:
        json_sidecar_path = None
        midi_sidecar_path = None
    else:
        json_sidecar_path, midi_sidecar_path = derive_automatic_candidate_paths(
            audio_path
        )
    fingerprint = _score_fingerprint(
        score_file=score_file,
        explicit=explicit,
        json_sidecar_path=json_sidecar_path,
        midi_sidecar_path=midi_sidecar_path,
    )
    return ScoreRepositoryRequest(
        score_file=score_file,
        audio_path=audio_path,
        explicit=explicit,
        json_sidecar_path=json_sidecar_path,
        midi_sidecar_path=midi_sidecar_path,
        dependency_fingerprint=fingerprint,
    )


def _provider_error_category(error: ProviderChainError) -> Literal[
    "not_found", "unprocessable"
]:
    if error.provider == "explicit" and error.diagnostics:
        prefix = error.diagnostics[0].split(":", 1)[0]
        if prefix in ("explicit_path_unresolved", "explicit_file_missing"):
            return "not_found"
    return "unprocessable"


def resolve_score_repository(
    request: ScoreRepositoryRequest,
    *,
    audio_seconds_at_tick_zero: float,
) -> ScoreRepositoryResult:
    if type(request) is not ScoreRepositoryRequest:
        raise TypeError("request must be a ScoreRepositoryRequest")
    if type(audio_seconds_at_tick_zero) is not float or not math.isfinite(
        audio_seconds_at_tick_zero
    ):
        raise TypeError("audio_seconds_at_tick_zero must be a finite built-in float")
    providers = (
        ExplicitFileProvider(
            score_file=request.score_file,
            resolved_path=request.explicit.resolved_path,
            audio_path=request.audio_path,
        ),
        SidecarJsonProvider(
            candidate_path=request.json_sidecar_path,
            audio_path=request.audio_path,
        ),
        MidiSidecarProvider(candidate_path=request.midi_sidecar_path),
        AnalysisProvider(),
        ConstantProvider(),
    )
    try:
        resolution = resolve_provider_chain(
            providers,
            audio_seconds_at_tick_zero=audio_seconds_at_tick_zero,
            dependency_fingerprint=request.dependency_fingerprint,
        )
    except ProviderChainError as error:
        message = "; ".join(error.diagnostics) or "invalid provider result"
        value = diagnostic("score_provider_error", "error", message)
        raise ScoreRepositoryError(
            error.provider,
            _provider_error_category(error),
            value,
        ) from None

    stale = []
    ordinary = []
    for message in resolution.diagnostics:
        if message.startswith("score_source_identity_mismatch:"):
            stale.append(diagnostic("score_provider_error", "warning", message))
        else:
            ordinary.append(diagnostic("score_resolver_diagnostic", "warning", message))
    score_format: ScoreFormat = (
        "constant"
        if resolution.resolved_score is None
        else resolution.resolved_score.score.source
    )
    return ScoreRepositoryResult(
        resolution=resolution,
        diagnostics=tuple(stale + ordinary),
        score_format=score_format,
        score_provider=resolution.provider,
    )


def append_resolver_diagnostics(
    result: ScoreRepositoryResult,
    *,
    resolver_diagnostics: tuple[str, ...],
    effective_alignment: float,
) -> ScoreRepositoryResult:
    if type(result) is not ScoreRepositoryResult:
        raise TypeError("result must be a ScoreRepositoryResult")
    if type(resolver_diagnostics) is not tuple or any(
        type(value) is not str for value in resolver_diagnostics
    ):
        raise TypeError("resolver_diagnostics must be a tuple of strings")
    if type(effective_alignment) is not float or not math.isfinite(effective_alignment):
        raise TypeError("effective_alignment must be a finite built-in float")
    values = list(result.diagnostics)
    values.extend(
        diagnostic("score_resolver_diagnostic", "warning", message)
        for message in resolver_diagnostics
    )
    provider_alignment = result.resolution.provider_alignment_seconds
    if provider_alignment is not None and provider_alignment != effective_alignment:
        values.append(
            diagnostic(
                "score_alignment_divergence",
                "warning",
                _ALIGNMENT_DIVERGENCE_MESSAGE,
            )
        )
    return ScoreRepositoryResult(
        resolution=result.resolution,
        diagnostics=tuple(values),
        score_format=result.score_format,
        score_provider=result.score_provider,
    )


__all__ = (
    "ResolvedPathState",
    "ScoreDiagnostic",
    "ScoreRepositoryError",
    "ScoreRepositoryRequest",
    "ScoreRepositoryResult",
    "append_resolver_diagnostics",
    "diagnostic",
    "prepare_score_repository_request",
    "resolve_score_repository",
    "serialize_diagnostics",
)
