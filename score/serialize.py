from dataclasses import dataclass
import math

from .bars import build_bar_grid
from .model import Marker, MeterEvent, Score, Section, TempoEvent
from .normalize import finalize_score, normalize_events


SCORE_SCHEMA_VERSION = 1
SIDECAR_SCHEMA_VERSION = 1

_SCORE_FIELDS = (
    "schema_version",
    "ticks_per_quarter",
    "tempos",
    "meters",
    "markers",
    "sections",
    "source",
    "meter_estimated",
)
_TEMPO_FIELDS = ("tick", "us_per_quarter")
_METER_FIELDS = ("tick", "numerator", "denominator")
_MARKER_FIELDS = ("tick", "name")
_SECTION_FIELDS = (
    "name",
    "start_tick",
    "end_tick_exclusive",
    "bar_aligned",
    "confidence",
)
_SIDECAR_FIELDS = (
    "schema_version",
    "score",
    "end_tick_exclusive",
    "audio_seconds_at_tick_zero",
    "generator",
    "derived_from",
    "edited",
)
_GENERATOR_FIELDS = (
    "name",
    "version",
    "algorithm_version",
    "config_fingerprint",
)
_DERIVED_FROM_FIELDS = ("filename", "size", "mtime_ns")
_SCORE_SOURCES = ("json", "midi", "analyzed", "constant")


@dataclass(frozen=True)
class GeneratorInfo:
    name: str
    version: str
    algorithm_version: int
    config_fingerprint: str


@dataclass(frozen=True)
class DerivedFrom:
    filename: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class ScoreSidecar:
    score: Score
    end_tick_exclusive: int
    audio_seconds_at_tick_zero: float
    generator: GeneratorInfo
    derived_from: DerivedFrom
    edited: bool


class ScoreSerializationError(ValueError):
    code: str
    path: str
    message: str

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _fail(code: str, path: str, message: str) -> None:
    raise ScoreSerializationError(code, path, message)


def _field_path(path: str, field: str) -> str:
    return f"{path}.{field}"


def _item_path(path: str, index: int) -> str:
    return f"{path}[{index}]"


def _require_object(
    value: object,
    path: str,
    fields: tuple[str, ...],
) -> dict[str, object]:
    if type(value) is not dict:
        _fail("invalid_type", path, "expected a built-in object")
    result = value
    unknown = [key for key in result if type(key) is not str or key not in fields]
    if unknown:
        key = sorted(unknown, key=lambda item: (type(item).__name__, repr(item)))[0]
        unknown_path = _field_path(path, key) if type(key) is str else path
        _fail("unknown_field", unknown_path, f"unknown field {key!r}")
    for field in fields:
        if field not in result:
            _fail(
                "missing_field",
                _field_path(path, field),
                f"required field {field!r} is missing",
            )
    return result


def _require_array(value: object, path: str) -> list[object]:
    if type(value) is not list:
        _fail("invalid_type", path, "expected a built-in array")
    return value


def _require_string(value: object, path: str) -> str:
    if type(value) is not str:
        _fail("invalid_type", path, "expected a built-in string")
    return value


def _require_bool(value: object, path: str) -> bool:
    if type(value) is not bool:
        _fail("invalid_type", path, "expected a built-in bool")
    return value


def _require_int(
    value: object,
    path: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> int:
    if type(value) is not int:
        _fail("invalid_type", path, "expected a built-in int")
    if positive and value <= 0:
        _fail("invalid_value", path, "value must be positive")
    if nonnegative and value < 0:
        _fail("invalid_value", path, "value must be nonnegative")
    return value


def _require_real(value: object, path: str) -> float:
    if type(value) not in (int, float):
        _fail("invalid_type", path, "expected a built-in int or float")
    try:
        result = float(value)
    except OverflowError:
        _fail("invalid_value", path, "value must be finite")
    if not math.isfinite(result):
        _fail("invalid_value", path, "value must be finite")
    return result


def _require_confidence(value: object, path: str) -> float | None:
    if value is None:
        return None
    return _require_real(value, path)


def _require_schema_version(value: object, path: str, expected: int) -> None:
    version = _require_int(value, path)
    if version != expected:
        _fail(
            "unsupported_schema_version",
            path,
            f"schema version {version} is unsupported; expected {expected}",
        )


def _require_tuple(value: object, path: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        _fail("invalid_type", path, "expected a canonical tuple")
    return value


def _decode_tempos(value: object, path: str) -> tuple[TempoEvent, ...]:
    tempos = []
    for index, item in enumerate(_require_array(value, path)):
        item_path = _item_path(path, index)
        obj = _require_object(item, item_path, _TEMPO_FIELDS)
        tempos.append(
            TempoEvent(
                tick=_require_int(
                    obj["tick"],
                    _field_path(item_path, "tick"),
                    nonnegative=True,
                ),
                us_per_quarter=_require_int(
                    obj["us_per_quarter"],
                    _field_path(item_path, "us_per_quarter"),
                    positive=True,
                ),
            )
        )
    return tuple(tempos)


def _decode_meters(value: object, path: str) -> tuple[MeterEvent, ...]:
    meters = []
    for index, item in enumerate(_require_array(value, path)):
        item_path = _item_path(path, index)
        obj = _require_object(item, item_path, _METER_FIELDS)
        denominator_path = _field_path(item_path, "denominator")
        denominator = _require_int(
            obj["denominator"],
            denominator_path,
            positive=True,
        )
        if denominator > 64 or denominator & (denominator - 1):
            _fail(
                "invalid_value",
                denominator_path,
                "meter denominator must be a power of two through 64",
            )
        meters.append(
            MeterEvent(
                tick=_require_int(
                    obj["tick"],
                    _field_path(item_path, "tick"),
                    nonnegative=True,
                ),
                numerator=_require_int(
                    obj["numerator"],
                    _field_path(item_path, "numerator"),
                    positive=True,
                ),
                denominator=denominator,
            )
        )
    return tuple(meters)


def _decode_markers(value: object, path: str) -> tuple[Marker, ...]:
    markers = []
    for index, item in enumerate(_require_array(value, path)):
        item_path = _item_path(path, index)
        obj = _require_object(item, item_path, _MARKER_FIELDS)
        markers.append(
            Marker(
                tick=_require_int(
                    obj["tick"],
                    _field_path(item_path, "tick"),
                    nonnegative=True,
                ),
                name=_require_string(
                    obj["name"],
                    _field_path(item_path, "name"),
                ),
            )
        )
    return tuple(markers)


def _decode_sections(value: object, path: str) -> tuple[Section, ...]:
    sections = []
    for index, item in enumerate(_require_array(value, path)):
        item_path = _item_path(path, index)
        obj = _require_object(item, item_path, _SECTION_FIELDS)
        start_tick = _require_int(
            obj["start_tick"],
            _field_path(item_path, "start_tick"),
            nonnegative=True,
        )
        end_path = _field_path(item_path, "end_tick_exclusive")
        end_tick_exclusive = _require_int(
            obj["end_tick_exclusive"],
            end_path,
            nonnegative=True,
        )
        if end_tick_exclusive < start_tick:
            _fail(
                "invalid_value",
                end_path,
                "Section end must not precede its start",
            )
        sections.append(
            Section(
                name=_require_string(
                    obj["name"],
                    _field_path(item_path, "name"),
                ),
                start_tick=start_tick,
                end_tick_exclusive=end_tick_exclusive,
                bar_aligned=_require_bool(
                    obj["bar_aligned"],
                    _field_path(item_path, "bar_aligned"),
                ),
                confidence=_require_confidence(
                    obj["confidence"],
                    _field_path(item_path, "confidence"),
                ),
            )
        )
    return tuple(sections)


def _decode_score(value: object, path: str) -> Score:
    obj = _require_object(value, path, _SCORE_FIELDS)
    _require_schema_version(
        obj["schema_version"],
        _field_path(path, "schema_version"),
        SCORE_SCHEMA_VERSION,
    )
    ticks_per_quarter = _require_int(
        obj["ticks_per_quarter"],
        _field_path(path, "ticks_per_quarter"),
        positive=True,
    )
    tempos = _decode_tempos(obj["tempos"], _field_path(path, "tempos"))
    meters = _decode_meters(obj["meters"], _field_path(path, "meters"))
    markers = _decode_markers(obj["markers"], _field_path(path, "markers"))
    sections = _decode_sections(obj["sections"], _field_path(path, "sections"))
    source_path = _field_path(path, "source")
    source = _require_string(obj["source"], source_path)
    if source not in _SCORE_SOURCES:
        _fail("invalid_value", source_path, f"unsupported Score source {source!r}")
    meter_estimated = _require_bool(
        obj["meter_estimated"],
        _field_path(path, "meter_estimated"),
    )

    events = normalize_events(
        ticks_per_quarter=ticks_per_quarter,
        tempos=tempos,
        meters=meters,
        markers=markers,
    )
    through_tick = max(
        0,
        events.meters[-1].tick,
        *(marker.tick for marker in events.markers),
        *(section.start_tick for section in sections),
        *(section.end_tick_exclusive for section in sections),
    )
    try:
        bar_grid = build_bar_grid(
            ticks_per_quarter=ticks_per_quarter,
            meters=events.meters,
            through_tick=through_tick,
        )
    except (TypeError, ValueError) as exc:
        _fail("invalid_value", _field_path(path, "meters"), str(exc))

    boundaries = set(bar_grid.boundaries)
    for index, section in enumerate(sections):
        expected_alignment = section.start_tick in boundaries
        if section.bar_aligned != expected_alignment:
            _fail(
                "invalid_value",
                _field_path(
                    _item_path(_field_path(path, "sections"), index),
                    "bar_aligned",
                ),
                f"bar_aligned must be {expected_alignment} for this start tick",
            )

    return finalize_score(
        events=events,
        sections=sections,
        source=source,
        meter_estimated=meter_estimated,
        bar_grid=bar_grid,
    )


def _encode_score(score: object, path: str) -> dict[str, object]:
    if type(score) is not Score:
        _fail("invalid_type", path, "expected a Score")

    tempos = []
    for index, tempo in enumerate(_require_tuple(score.tempos, _field_path(path, "tempos"))):
        item_path = _item_path(_field_path(path, "tempos"), index)
        if type(tempo) is not TempoEvent:
            _fail("invalid_type", item_path, "expected a TempoEvent")
        tempos.append(
            {
                "tick": _require_int(tempo.tick, _field_path(item_path, "tick"), nonnegative=True),
                "us_per_quarter": _require_int(
                    tempo.us_per_quarter,
                    _field_path(item_path, "us_per_quarter"),
                    positive=True,
                ),
            }
        )

    meters = []
    for index, meter in enumerate(_require_tuple(score.meters, _field_path(path, "meters"))):
        item_path = _item_path(_field_path(path, "meters"), index)
        if type(meter) is not MeterEvent:
            _fail("invalid_type", item_path, "expected a MeterEvent")
        denominator_path = _field_path(item_path, "denominator")
        denominator = _require_int(meter.denominator, denominator_path, positive=True)
        if denominator > 64 or denominator & (denominator - 1):
            _fail(
                "invalid_value",
                denominator_path,
                "meter denominator must be a power of two through 64",
            )
        meters.append(
            {
                "tick": _require_int(meter.tick, _field_path(item_path, "tick"), nonnegative=True),
                "numerator": _require_int(
                    meter.numerator,
                    _field_path(item_path, "numerator"),
                    positive=True,
                ),
                "denominator": denominator,
            }
        )

    markers = []
    for index, marker in enumerate(_require_tuple(score.markers, _field_path(path, "markers"))):
        item_path = _item_path(_field_path(path, "markers"), index)
        if type(marker) is not Marker:
            _fail("invalid_type", item_path, "expected a Marker")
        markers.append(
            {
                "tick": _require_int(marker.tick, _field_path(item_path, "tick"), nonnegative=True),
                "name": _require_string(marker.name, _field_path(item_path, "name")),
            }
        )

    sections = []
    for index, section in enumerate(_require_tuple(score.sections, _field_path(path, "sections"))):
        item_path = _item_path(_field_path(path, "sections"), index)
        if type(section) is not Section:
            _fail("invalid_type", item_path, "expected a Section")
        start_tick = _require_int(
            section.start_tick,
            _field_path(item_path, "start_tick"),
            nonnegative=True,
        )
        end_path = _field_path(item_path, "end_tick_exclusive")
        end_tick_exclusive = _require_int(
            section.end_tick_exclusive,
            end_path,
            nonnegative=True,
        )
        if end_tick_exclusive < start_tick:
            _fail("invalid_value", end_path, "Section end must not precede its start")
        sections.append(
            {
                "name": _require_string(section.name, _field_path(item_path, "name")),
                "start_tick": start_tick,
                "end_tick_exclusive": end_tick_exclusive,
                "bar_aligned": _require_bool(
                    section.bar_aligned,
                    _field_path(item_path, "bar_aligned"),
                ),
                "confidence": _require_confidence(
                    section.confidence,
                    _field_path(item_path, "confidence"),
                ),
            }
        )

    source_path = _field_path(path, "source")
    source = _require_string(score.source, source_path)
    if source not in _SCORE_SOURCES:
        _fail("invalid_value", source_path, f"unsupported Score source {source!r}")
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "ticks_per_quarter": _require_int(
            score.ticks_per_quarter,
            _field_path(path, "ticks_per_quarter"),
            positive=True,
        ),
        "tempos": tempos,
        "meters": meters,
        "markers": markers,
        "sections": sections,
        "source": source,
        "meter_estimated": _require_bool(
            score.meter_estimated,
            _field_path(path, "meter_estimated"),
        ),
    }


def score_to_dict(score: Score) -> dict[str, object]:
    encoded = _encode_score(score, "$")
    canonical = _decode_score(encoded, "$")
    return _encode_score(canonical, "$")


def score_from_dict(value: object) -> Score:
    return _decode_score(value, "$")


def _decode_generator(value: object, path: str) -> GeneratorInfo:
    obj = _require_object(value, path, _GENERATOR_FIELDS)
    return GeneratorInfo(
        name=_require_string(obj["name"], _field_path(path, "name")),
        version=_require_string(obj["version"], _field_path(path, "version")),
        algorithm_version=_require_int(
            obj["algorithm_version"],
            _field_path(path, "algorithm_version"),
            nonnegative=True,
        ),
        config_fingerprint=_require_string(
            obj["config_fingerprint"],
            _field_path(path, "config_fingerprint"),
        ),
    )


def _decode_derived_from(value: object, path: str) -> DerivedFrom:
    obj = _require_object(value, path, _DERIVED_FROM_FIELDS)
    return DerivedFrom(
        filename=_require_string(obj["filename"], _field_path(path, "filename")),
        size=_require_int(
            obj["size"],
            _field_path(path, "size"),
            nonnegative=True,
        ),
        mtime_ns=_require_int(
            obj["mtime_ns"],
            _field_path(path, "mtime_ns"),
            nonnegative=True,
        ),
    )


def _decode_sidecar(value: object, path: str) -> ScoreSidecar:
    obj = _require_object(value, path, _SIDECAR_FIELDS)
    _require_schema_version(
        obj["schema_version"],
        _field_path(path, "schema_version"),
        SIDECAR_SCHEMA_VERSION,
    )
    score_path = _field_path(path, "score")
    score = _decode_score(obj["score"], score_path)
    end_path = _field_path(path, "end_tick_exclusive")
    end_tick_exclusive = _require_int(
        obj["end_tick_exclusive"],
        end_path,
        nonnegative=True,
    )
    for index, marker in enumerate(score.markers):
        if marker.tick > end_tick_exclusive:
            _fail(
                "invalid_value",
                _field_path(_item_path(_field_path(score_path, "markers"), index), "tick"),
                "marker tick exceeds sidecar end_tick_exclusive",
            )
    for index, section in enumerate(score.sections):
        item_path = _item_path(_field_path(score_path, "sections"), index)
        if section.start_tick > end_tick_exclusive:
            _fail(
                "invalid_value",
                _field_path(item_path, "start_tick"),
                "Section start exceeds sidecar end_tick_exclusive",
            )
        if section.end_tick_exclusive > end_tick_exclusive:
            _fail(
                "invalid_value",
                _field_path(item_path, "end_tick_exclusive"),
                "Section end exceeds sidecar end_tick_exclusive",
            )

    return ScoreSidecar(
        score=score,
        end_tick_exclusive=end_tick_exclusive,
        audio_seconds_at_tick_zero=_require_real(
            obj["audio_seconds_at_tick_zero"],
            _field_path(path, "audio_seconds_at_tick_zero"),
        ),
        generator=_decode_generator(
            obj["generator"],
            _field_path(path, "generator"),
        ),
        derived_from=_decode_derived_from(
            obj["derived_from"],
            _field_path(path, "derived_from"),
        ),
        edited=_require_bool(obj["edited"], _field_path(path, "edited")),
    )


def _encode_generator(value: object, path: str) -> dict[str, object]:
    if type(value) is not GeneratorInfo:
        _fail("invalid_type", path, "expected GeneratorInfo")
    return {
        "name": _require_string(value.name, _field_path(path, "name")),
        "version": _require_string(value.version, _field_path(path, "version")),
        "algorithm_version": _require_int(
            value.algorithm_version,
            _field_path(path, "algorithm_version"),
            nonnegative=True,
        ),
        "config_fingerprint": _require_string(
            value.config_fingerprint,
            _field_path(path, "config_fingerprint"),
        ),
    }


def _encode_derived_from(value: object, path: str) -> dict[str, object]:
    if type(value) is not DerivedFrom:
        _fail("invalid_type", path, "expected DerivedFrom")
    return {
        "filename": _require_string(value.filename, _field_path(path, "filename")),
        "size": _require_int(value.size, _field_path(path, "size"), nonnegative=True),
        "mtime_ns": _require_int(
            value.mtime_ns,
            _field_path(path, "mtime_ns"),
            nonnegative=True,
        ),
    }


def _encode_sidecar(sidecar: object, path: str) -> dict[str, object]:
    if type(sidecar) is not ScoreSidecar:
        _fail("invalid_type", path, "expected a ScoreSidecar")
    return {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "score": _encode_score(sidecar.score, _field_path(path, "score")),
        "end_tick_exclusive": _require_int(
            sidecar.end_tick_exclusive,
            _field_path(path, "end_tick_exclusive"),
            nonnegative=True,
        ),
        "audio_seconds_at_tick_zero": _require_real(
            sidecar.audio_seconds_at_tick_zero,
            _field_path(path, "audio_seconds_at_tick_zero"),
        ),
        "generator": _encode_generator(
            sidecar.generator,
            _field_path(path, "generator"),
        ),
        "derived_from": _encode_derived_from(
            sidecar.derived_from,
            _field_path(path, "derived_from"),
        ),
        "edited": _require_bool(sidecar.edited, _field_path(path, "edited")),
    }


def sidecar_to_dict(sidecar: ScoreSidecar) -> dict[str, object]:
    encoded = _encode_sidecar(sidecar, "$")
    canonical = _decode_sidecar(encoded, "$")
    return _encode_sidecar(canonical, "$")


def sidecar_from_dict(value: object) -> ScoreSidecar:
    return _decode_sidecar(value, "$")
