from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import os
from typing import ClassVar, Protocol

from .midi_parse import MidiParseError, parse_midi
from .model import ProviderKind, ProviderResult, ResolvedScore, Score, Section
from .serialize import ScoreSerializationError, ScoreSidecar, score_from_dict, sidecar_from_dict


PathValue = str | os.PathLike[str]
CandidateFingerprint = tuple[object, ...]

SCORE_FINGERPRINT_VERSION = ("score_fingerprint", 1)
BLANK_SCORE_FILE = ("score_file", "blank")
ANALYSIS_PROVIDER_CONTRACT = ("provider_contract", 1, "analysis", "stub")

_PROVIDER_KINDS = (
    "explicit",
    "json_sidecar",
    "midi_sidecar",
    "analysis",
    "constant",
)
_PROVIDER_STATUSES = ("not_applicable", "found", "invalid")


class ScoreProvider(Protocol):
    kind: ProviderKind

    def resolve(self) -> "ProviderSelection": ...


@dataclass(frozen=True)
class ProviderSelection:
    provider: ProviderKind
    result: ProviderResult
    provider_alignment_seconds: float | None


@dataclass(frozen=True)
class ScoreResolution:
    resolved_score: ResolvedScore | None
    provider: ProviderKind
    provider_alignment_seconds: float | None
    diagnostics: tuple[str, ...]
    provenance: Mapping[str, str]
    dependency_fingerprint: tuple[object, ...]


class ProviderChainError(ValueError):
    provider: ProviderKind
    diagnostics: tuple[str, ...]
    provenance: Mapping[str, str]

    def __init__(
        self,
        provider: ProviderKind,
        diagnostics: tuple[str, ...],
        provenance: Mapping[str, str],
    ) -> None:
        self.provider = provider
        self.diagnostics = diagnostics
        self.provenance = provenance
        detail = "; ".join(diagnostics) if diagnostics else "invalid provider result"
        super().__init__(f"{provider} provider is invalid: {detail}")


def normalize_candidate_path(path: PathValue) -> str:
    value = os.fspath(path)
    if type(value) is not str:
        raise TypeError("path must resolve to a built-in string")
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def derive_automatic_candidate_paths(audio_path: PathValue) -> tuple[str, str]:
    normalized = normalize_candidate_path(audio_path)
    stem, _suffix = os.path.splitext(normalized)
    return f"{stem}.score.json", f"{stem}.mid"


def fingerprint_candidate(role: str, path: PathValue) -> CandidateFingerprint:
    if type(role) is not str or not role:
        raise TypeError("role must be a nonempty built-in string")
    normalized = normalize_candidate_path(path)
    try:
        state = os.stat(normalized)
    except (FileNotFoundError, NotADirectoryError):
        return role, normalized, "missing"
    except OSError:
        return role, normalized, "unreadable"
    return role, normalized, "file", state.st_size, state.st_mtime_ns


def build_score_fingerprint(
    *,
    score_file: str,
    explicit_path: PathValue | None,
    json_sidecar_path: PathValue,
    midi_sidecar_path: PathValue,
) -> tuple[object, ...]:
    if type(score_file) is not str:
        raise TypeError("score_file must be a built-in string")
    supplied = bool(score_file.strip())
    values: list[object] = [
        SCORE_FINGERPRINT_VERSION,
        score_file if supplied else BLANK_SCORE_FILE,
    ]
    if supplied:
        candidate_path = score_file if explicit_path is None else explicit_path
        values.append(fingerprint_candidate("explicit", candidate_path))
    values.extend(
        (
            fingerprint_candidate("json_sidecar", json_sidecar_path),
            fingerprint_candidate("midi_sidecar", midi_sidecar_path),
            ANALYSIS_PROVIDER_CONTRACT,
        )
    )
    return tuple(values)


def select_section(
    sections: Sequence[Section],
    query_tick: int | float,
) -> Section | None:
    if isinstance(sections, (str, bytes)) or not isinstance(sections, Sequence):
        raise TypeError("sections must be a sequence of Section values")
    if type(query_tick) is int:
        tick = query_tick
    elif type(query_tick) is float:
        if not math.isfinite(query_tick):
            raise ValueError("query_tick must be finite")
        tick = query_tick
    else:
        raise TypeError("query_tick must be a built-in int or float")

    candidates = []
    for section in sections:
        if type(section) is not Section:
            raise TypeError("sections must contain only Section values")
        if section.start_tick <= tick < section.end_tick_exclusive:
            candidates.append(section)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda section: (
            -section.start_tick,
            section.end_tick_exclusive,
            section.name,
        ),
    )


def _selection(
    provider: ProviderKind,
    status: str,
    *,
    score: Score | None = None,
    diagnostics: tuple[str, ...] = (),
    provenance: Mapping[str, str] | None = None,
    provider_alignment_seconds: float | None = None,
) -> ProviderSelection:
    return ProviderSelection(
        provider=provider,
        result=ProviderResult(
            status=status,
            score=score,
            diagnostics=diagnostics,
            provenance={} if provenance is None else provenance,
        ),
        provider_alignment_seconds=provider_alignment_seconds,
    )


def _path_state(path: str) -> str:
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return "missing"
    except OSError:
        return "unreadable"
    return "file"


def _file_provenance(provider: ProviderKind, path: str, format_name: str) -> dict[str, str]:
    return {
        "provider": provider,
        "path": path,
        "format": format_name,
    }


def _source_identity(audio_path: PathValue | None) -> tuple[str, int, int] | None:
    if audio_path is None:
        return None
    original = os.fspath(audio_path)
    if type(original) is not str:
        raise TypeError("audio_path must resolve to a built-in string")
    normalized = normalize_candidate_path(audio_path)
    try:
        state = os.stat(normalized)
    except OSError:
        return None
    filename = os.path.basename(os.path.abspath(os.path.normpath(original)))
    return filename, state.st_size, state.st_mtime_ns


def _source_identity_matches(
    sidecar: ScoreSidecar,
    audio_identity: tuple[str, int, int] | None,
) -> bool | None:
    if audio_identity is None:
        return None
    actual = sidecar.derived_from
    return (
        actual.filename,
        actual.size,
        actual.mtime_ns,
    ) == audio_identity


def _add_sidecar_provenance(
    provenance: dict[str, str],
    sidecar: ScoreSidecar,
) -> None:
    provenance.update(
        {
            "schema": "sidecar",
            "end_tick_exclusive": str(sidecar.end_tick_exclusive),
            "audio_seconds_at_tick_zero": repr(sidecar.audio_seconds_at_tick_zero),
            "generator_name": sidecar.generator.name,
            "generator_version": sidecar.generator.version,
            "generator_algorithm_version": str(sidecar.generator.algorithm_version),
            "generator_config_fingerprint": sidecar.generator.config_fingerprint,
            "derived_from_filename": sidecar.derived_from.filename,
            "derived_from_size": str(sidecar.derived_from.size),
            "derived_from_mtime_ns": str(sidecar.derived_from.mtime_ns),
            "edited": "true" if sidecar.edited else "false",
        }
    )


def _json_error_selection(
    provider: ProviderKind,
    path: str,
    diagnostic: str,
) -> ProviderSelection:
    return _selection(
        provider,
        "invalid",
        diagnostics=(diagnostic,),
        provenance=_file_provenance(provider, path, "json"),
    )


def _resolve_json(
    provider: ProviderKind,
    path: str,
    *,
    audio_path: PathValue | None,
    automatic: bool,
) -> ProviderSelection:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            root = json.load(handle)
    except UnicodeDecodeError as error:
        return _json_error_selection(
            provider,
            path,
            f"json_unicode_error:start={error.start};end={error.end};reason={error.reason}",
        )
    except json.JSONDecodeError as error:
        return _json_error_selection(
            provider,
            path,
            f"json_decode_error:line={error.lineno};column={error.colno};message={error.msg}",
        )
    except OSError:
        return _json_error_selection(provider, path, "file_unreadable:json")

    provenance = _file_provenance(provider, path, "json")
    if type(root) is not dict:
        return _json_error_selection(
            provider,
            path,
            "json_root_invalid:expected a built-in object",
        )
    wrapper = "score" in root
    bare = "ticks_per_quarter" in root
    if wrapper and bare:
        return _json_error_selection(
            provider,
            path,
            "json_schema_ambiguous:root contains both score and ticks_per_quarter",
        )
    if not wrapper and not bare:
        return _json_error_selection(
            provider,
            path,
            "json_schema_unknown:root contains neither score nor ticks_per_quarter",
        )

    try:
        if wrapper:
            sidecar = sidecar_from_dict(root)
            score = sidecar.score
            alignment = sidecar.audio_seconds_at_tick_zero
            _add_sidecar_provenance(provenance, sidecar)
            identity_matches = _source_identity_matches(
                sidecar,
                _source_identity(audio_path),
            )
            if identity_matches is False and automatic and not sidecar.edited:
                return _selection(provider, "not_applicable")
            diagnostics = (
                ("score_source_identity_mismatch:sidecar does not match the audio file",)
                if identity_matches is False
                else ()
            )
        else:
            score = score_from_dict(root)
            alignment = None
            diagnostics = ()
            provenance["schema"] = "score"
    except ScoreSerializationError as error:
        provenance.update(
            {
                "serialization_code": error.code,
                "serialization_path": error.path,
            }
        )
        return _selection(
            provider,
            "invalid",
            diagnostics=(
                f"score_serialization_error:code={error.code};path={error.path};message={error.message}",
            ),
            provenance=provenance,
        )

    return _selection(
        provider,
        "found",
        score=score,
        diagnostics=diagnostics,
        provenance=provenance,
        provider_alignment_seconds=alignment,
    )


def _midi_error_diagnostic(error: MidiParseError) -> str:
    track = "none" if error.track_index is None else str(error.track_index)
    event = "none" if error.event_index is None else str(error.event_index)
    return (
        f"midi_parse_error:code={error.code};byte_offset={error.byte_offset};"
        f"track_index={track};event_index={event};message={error.message}"
    )


def _resolve_midi(provider: ProviderKind, path: str) -> ProviderSelection:
    provenance = _file_provenance(provider, path, "midi")
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return _selection(
            provider,
            "invalid",
            diagnostics=("file_unreadable:midi",),
            provenance=provenance,
        )
    try:
        parsed = parse_midi(data)
    except MidiParseError as error:
        provenance.update(
            {
                "parser_code": error.code,
                "parser_byte_offset": str(error.byte_offset),
                "parser_track_index": (
                    "none" if error.track_index is None else str(error.track_index)
                ),
                "parser_event_index": (
                    "none" if error.event_index is None else str(error.event_index)
                ),
            }
        )
        return _selection(
            provider,
            "invalid",
            diagnostics=(_midi_error_diagnostic(error),),
            provenance=provenance,
        )
    provenance["end_tick_exclusive"] = str(parsed.end_tick_exclusive)
    return _selection(
        provider,
        "found",
        score=parsed.score,
        diagnostics=parsed.diagnostics,
        provenance=provenance,
    )


@dataclass(frozen=True)
class ExplicitFileProvider:
    score_file: str
    resolved_path: PathValue | None
    audio_path: PathValue | None = None
    kind: ClassVar[ProviderKind] = "explicit"

    def resolve(self) -> ProviderSelection:
        if type(self.score_file) is not str:
            raise TypeError("score_file must be a built-in string")
        if not self.score_file.strip():
            return _selection(self.kind, "not_applicable")
        if self.resolved_path is None:
            return _selection(
                self.kind,
                "invalid",
                diagnostics=("explicit_path_unresolved:score_file could not be resolved",),
                provenance={"provider": self.kind, "selection": self.score_file},
            )
        path = normalize_candidate_path(self.resolved_path)
        state = _path_state(path)
        if state != "file":
            return _selection(
                self.kind,
                "invalid",
                diagnostics=(f"explicit_file_{state}:selected Score file is {state}",),
                provenance={"provider": self.kind, "path": path},
            )
        suffix = os.path.splitext(path)[1].lower()
        if suffix == ".json":
            return _resolve_json(
                self.kind,
                path,
                audio_path=self.audio_path,
                automatic=False,
            )
        if suffix in (".mid", ".midi"):
            return _resolve_midi(self.kind, path)
        return _selection(
            self.kind,
            "invalid",
            diagnostics=(f"explicit_suffix_unsupported:{suffix or '<none>'}",),
            provenance={"provider": self.kind, "path": path},
        )


@dataclass(frozen=True)
class SidecarJsonProvider:
    candidate_path: PathValue | None
    audio_path: PathValue | None = None
    kind: ClassVar[ProviderKind] = "json_sidecar"

    def resolve(self) -> ProviderSelection:
        if self.candidate_path is None:
            return _selection(self.kind, "not_applicable")
        path = normalize_candidate_path(self.candidate_path)
        state = _path_state(path)
        if state == "missing":
            return _selection(self.kind, "not_applicable")
        if state == "unreadable":
            return _selection(
                self.kind,
                "invalid",
                diagnostics=("automatic_json_unreadable:candidate cannot be accessed",),
                provenance={"provider": self.kind, "path": path, "format": "json"},
            )
        return _resolve_json(
            self.kind,
            path,
            audio_path=self.audio_path,
            automatic=True,
        )


@dataclass(frozen=True)
class MidiSidecarProvider:
    candidate_path: PathValue | None
    kind: ClassVar[ProviderKind] = "midi_sidecar"

    def resolve(self) -> ProviderSelection:
        if self.candidate_path is None:
            return _selection(self.kind, "not_applicable")
        path = normalize_candidate_path(self.candidate_path)
        state = _path_state(path)
        if state == "missing":
            return _selection(self.kind, "not_applicable")
        if state == "unreadable":
            return _selection(
                self.kind,
                "invalid",
                diagnostics=("automatic_midi_unreadable:candidate cannot be accessed",),
                provenance={"provider": self.kind, "path": path, "format": "midi"},
            )
        return _resolve_midi(self.kind, path)


@dataclass(frozen=True)
class AnalysisProvider:
    kind: ClassVar[ProviderKind] = "analysis"

    def resolve(self) -> ProviderSelection:
        return _selection(self.kind, "not_applicable")


@dataclass(frozen=True)
class ConstantProvider:
    kind: ClassVar[ProviderKind] = "constant"

    def resolve(self) -> ProviderSelection:
        return _selection(
            self.kind,
            "found",
            provenance={"format": "constant", "provider": "constant"},
        )


def _validate_immutable_fingerprint(value: object, path: str = "fingerprint") -> None:
    if type(value) is tuple:
        for index, item in enumerate(value):
            _validate_immutable_fingerprint(item, f"{path}[{index}]")
        return
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    raise TypeError(f"{path} must contain only immutable finite built-in values")


def _validate_selection(
    provider: ScoreProvider,
    selection: object,
) -> ProviderSelection:
    kind = getattr(provider, "kind", None)
    if kind not in _PROVIDER_KINDS:
        raise ValueError("provider kind violates the provider contract")
    if type(selection) is not ProviderSelection:
        raise TypeError("provider resolve() must return a ProviderSelection")
    if selection.provider != kind:
        raise ValueError("ProviderSelection provider does not match provider kind")
    if type(selection.result) is not ProviderResult:
        raise TypeError("ProviderSelection result must be a ProviderResult")
    result = selection.result
    if result.status not in _PROVIDER_STATUSES:
        raise ValueError("ProviderResult status violates the provider contract")
    if result.score is not None and type(result.score) is not Score:
        raise TypeError("ProviderResult score must be a Score or None")
    if type(result.diagnostics) is not tuple or any(
        type(item) is not str for item in result.diagnostics
    ):
        raise TypeError("ProviderResult diagnostics must be a tuple of strings")
    if not isinstance(result.provenance, Mapping) or any(
        type(key) is not str or type(value) is not str
        for key, value in result.provenance.items()
    ):
        raise TypeError("ProviderResult provenance must map strings to strings")
    alignment = selection.provider_alignment_seconds
    if alignment is not None and (
        type(alignment) is not float or not math.isfinite(alignment)
    ):
        raise TypeError("provider_alignment_seconds must be a finite float or None")

    if result.status == "not_applicable":
        if result.score is not None or alignment is not None:
            raise ValueError("not_applicable provider result violates its invariant")
    elif result.status == "invalid":
        if result.score is not None or alignment is not None:
            raise ValueError("invalid provider result violates its invariant")
        if not result.diagnostics:
            raise ValueError("invalid provider result must include diagnostics")
    elif kind == "constant":
        if result.score is not None or alignment is not None:
            raise ValueError("constant found result violates its invariant")
    elif result.score is None:
        raise ValueError("nonconstant found result must contain a Score")
    return selection


def resolve_provider_chain(
    providers: Sequence[ScoreProvider],
    *,
    audio_seconds_at_tick_zero: float,
    dependency_fingerprint: tuple[object, ...],
) -> ScoreResolution:
    if isinstance(providers, (str, bytes)) or not isinstance(providers, Sequence):
        raise TypeError("providers must be a sequence")
    if type(audio_seconds_at_tick_zero) is not float or not math.isfinite(
        audio_seconds_at_tick_zero
    ):
        raise TypeError("audio_seconds_at_tick_zero must be a finite built-in float")
    if type(dependency_fingerprint) is not tuple:
        raise TypeError("dependency_fingerprint must be a tuple")
    _validate_immutable_fingerprint(dependency_fingerprint)

    for provider in providers:
        selection = _validate_selection(provider, provider.resolve())
        result = selection.result
        if result.status == "not_applicable":
            continue
        if result.status == "invalid":
            raise ProviderChainError(
                selection.provider,
                result.diagnostics,
                result.provenance,
            )
        resolved_score = (
            None
            if selection.provider == "constant"
            else ResolvedScore(
                score=result.score,
                audio_seconds_at_tick_zero=audio_seconds_at_tick_zero,
                provider=selection.provider,
            )
        )
        return ScoreResolution(
            resolved_score=resolved_score,
            provider=selection.provider,
            provider_alignment_seconds=selection.provider_alignment_seconds,
            diagnostics=result.diagnostics,
            provenance=result.provenance,
            dependency_fingerprint=dependency_fingerprint,
        )
    raise RuntimeError("provider chain ended without a found result")


__all__ = (
    "ANALYSIS_PROVIDER_CONTRACT",
    "BLANK_SCORE_FILE",
    "SCORE_FINGERPRINT_VERSION",
    "AnalysisProvider",
    "ConstantProvider",
    "ExplicitFileProvider",
    "MidiSidecarProvider",
    "ProviderChainError",
    "ProviderSelection",
    "ScoreProvider",
    "ScoreResolution",
    "SidecarJsonProvider",
    "build_score_fingerprint",
    "derive_automatic_candidate_paths",
    "fingerprint_candidate",
    "normalize_candidate_path",
    "resolve_provider_chain",
    "select_section",
)
