import ast
import copy
import dataclasses
import hashlib
import json
import math
from pathlib import Path
import unittest

from score.midi_parse import parse_midi
from score.model import Marker, MeterEvent, Score, Section, TempoEvent
from score.serialize import (
    DerivedFrom,
    GeneratorInfo,
    SCORE_SCHEMA_VERSION,
    SIDECAR_SCHEMA_VERSION,
    ScoreSerializationError,
    ScoreSidecar,
    score_from_dict,
    score_to_dict,
    sidecar_from_dict,
    sidecar_to_dict,
)


ROOT = Path(__file__).resolve().parents[1]
SCORE_FIXTURES = ROOT / "tests" / "fixtures" / "scores"
PURE_FIXTURE_NAMES = (
    "constant_4_4.json",
    "changing_meter.json",
    "midbar_tempo.json",
    "midbar_meter.json",
    "odd_meter_31_32.json",
    "unaligned_markers.json",
    "truncated_tempo_track.json",
)


def load_value(name: str) -> object:
    return json.loads((SCORE_FIXTURES / name).read_text(encoding="utf-8"))


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def value_at(root: object, tokens: tuple[object, ...]) -> object:
    current = root
    for token in tokens:
        current = current[token]
    return current


def replace_at(root: object, tokens: tuple[object, ...], value: object) -> object:
    result = copy.deepcopy(root)
    parent = value_at(result, tokens[:-1])
    parent[tokens[-1]] = value
    return result


def assert_serialization_error(call, code: str, path: str) -> ScoreSerializationError:
    assertions = unittest.TestCase()
    with assertions.assertRaises(ScoreSerializationError) as first:
        call()
    with assertions.assertRaises(ScoreSerializationError) as second:
        call()
    error = first.exception
    assert error.code == code
    assert error.path == path
    assert error.message
    assert (error.code, error.path, error.message, str(error)) == (
        second.exception.code,
        second.exception.path,
        second.exception.message,
        str(second.exception),
    )
    assert str(error) == f"{code} at {path}: {error.message}"
    return error


class DictSubclass(dict):
    pass


class ListSubclass(list):
    pass


def test_serialization_value_contracts_are_exact_and_frozen():
    assert SCORE_SCHEMA_VERSION == 1
    assert SIDECAR_SCHEMA_VERSION == 1
    expected = {
        GeneratorInfo: ("name", "version", "algorithm_version", "config_fingerprint"),
        DerivedFrom: ("filename", "size", "mtime_ns"),
        ScoreSidecar: (
            "score",
            "end_tick_exclusive",
            "audio_seconds_at_tick_zero",
            "generator",
            "derived_from",
            "edited",
        ),
    }
    score = score_from_dict(load_value("constant_4_4.json"))
    values = (
        GeneratorInfo("", "", 0, ""),
        DerivedFrom("", 0, 0),
        ScoreSidecar(score, 0, 0.0, GeneratorInfo("", "", 0, ""), DerivedFrom("", 0, 0), False),
    )
    for value in values:
        assert tuple(field.name for field in dataclasses.fields(value)) == expected[type(value)]
        with unittest.TestCase().assertRaises(dataclasses.FrozenInstanceError):
            setattr(value, dataclasses.fields(value)[0].name, None)


def test_pure_score_exact_shape_and_key_order():
    value = score_to_dict(score_from_dict(load_value("odd_meter_31_32.json")))
    assert tuple(value) == (
        "schema_version",
        "ticks_per_quarter",
        "tempos",
        "meters",
        "markers",
        "sections",
        "source",
        "meter_estimated",
    )
    assert tuple(value["tempos"][0]) == ("tick", "us_per_quarter")
    assert tuple(value["meters"][0]) == ("tick", "numerator", "denominator")
    assert tuple(value["markers"][0]) == ("tick", "name")
    assert tuple(value["sections"][0]) == (
        "name",
        "start_tick",
        "end_tick_exclusive",
        "bar_aligned",
        "confidence",
    )
    assert "has_variable_meter" not in value
    assert "has_midbar_meter_change" not in value
    assert "bar_starts" not in value
    assert "provider" not in value


def test_sidecar_exact_shape_and_independent_nested_version():
    value = sidecar_to_dict(sidecar_from_dict(load_value("sidecar_v1.json")))
    assert tuple(value) == (
        "schema_version",
        "score",
        "end_tick_exclusive",
        "audio_seconds_at_tick_zero",
        "generator",
        "derived_from",
        "edited",
    )
    assert tuple(value["generator"]) == (
        "name",
        "version",
        "algorithm_version",
        "config_fingerprint",
    )
    assert tuple(value["derived_from"]) == ("filename", "size", "mtime_ns")
    assert value["schema_version"] == SIDECAR_SCHEMA_VERSION
    assert value["score"]["schema_version"] == SCORE_SCHEMA_VERSION
    assert "provider" not in value


def test_real_velvet_lies_score_round_trip():
    data = (ROOT / "tests" / "fixtures" / "midi" / "velvet-lies.mid").read_bytes()
    parsed = parse_midi(data)
    assert score_from_dict(score_to_dict(parsed.score)) == parsed.score


def test_all_pure_fixtures_round_trip_to_exact_canonical_bytes(name):
    path = SCORE_FIXTURES / name
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    score = score_from_dict(value)
    canonical = score_to_dict(score)
    assert score_from_dict(canonical) == score
    assert canonical_bytes(canonical) == raw


def test_sidecar_fixture_round_trip_to_exact_canonical_bytes():
    path = SCORE_FIXTURES / "sidecar_v1.json"
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    sidecar = sidecar_from_dict(value)
    canonical = sidecar_to_dict(sidecar)
    assert sidecar_from_dict(canonical) == sidecar
    assert canonical_bytes(canonical) == raw


def test_source_is_preserved_exactly(source):
    value = load_value("constant_4_4.json")
    value["source"] = source
    score = score_from_dict(value)
    assert score.source == source
    assert score_to_dict(score)["source"] == source


def test_meter_estimated_is_preserved(meter_estimated):
    value = load_value("constant_4_4.json")
    value["meter_estimated"] = meter_estimated
    score = score_from_dict(value)
    assert score.meter_estimated is meter_estimated
    assert score_to_dict(score)["meter_estimated"] is meter_estimated


def test_derived_meter_flags_are_recomputed_not_serialized():
    changing = score_from_dict(load_value("changing_meter.json"))
    midbar = score_from_dict(load_value("midbar_meter.json"))
    assert changing.has_variable_meter is True
    assert changing.has_midbar_meter_change is False
    assert midbar.has_variable_meter is True
    assert midbar.has_midbar_meter_change is True
    assert "has_variable_meter" not in score_to_dict(changing)
    assert "has_midbar_meter_change" not in score_to_dict(midbar)


def test_exact_marker_and_section_names_survive_without_unicode_normalization():
    value = load_value("unaligned_markers.json")
    score = score_from_dict(value)
    marker_names = tuple(marker.name for marker in score.markers)
    section_names = tuple(section.name for section in score.sections)
    expected_markers = ("", "  Chorus  ", "Café", "Café", "🎵")
    expected_sections = ("", "  Chorus  ", "Café", "Café", "🎵")
    assert marker_names == expected_markers
    assert section_names == expected_sections
    assert marker_names[2] != marker_names[3]
    assert score_to_dict(score)["markers"] == value["markers"]


def test_duplicate_markers_and_sections_are_preserved():
    value = load_value("constant_4_4.json")
    marker = {"tick": 100, "name": "A"}
    section = {
        "name": "A",
        "start_tick": 100,
        "end_tick_exclusive": 100,
        "bar_aligned": False,
        "confidence": None,
    }
    value["markers"] = [marker, copy.deepcopy(marker)]
    value["sections"] = [section, copy.deepcopy(section)]
    score = score_from_dict(value)
    assert score.markers == (Marker(100, "A"), Marker(100, "A"))
    assert score.sections == (
        Section("A", 100, 100, False, None),
        Section("A", 100, 100, False, None),
    )


def test_same_tick_events_and_defaults_reuse_phase2a_normalization():
    value = load_value("constant_4_4.json")
    value["tempos"] = [
        {"tick": 0, "us_per_quarter": 500000},
        {"tick": 0, "us_per_quarter": 400000},
        {"tick": 100, "us_per_quarter": 400000},
    ]
    value["meters"] = [
        {"tick": 0, "numerator": 4, "denominator": 4},
        {"tick": 0, "numerator": 3, "denominator": 4},
        {"tick": 100, "numerator": 3, "denominator": 4},
    ]
    score = score_from_dict(value)
    assert score.tempos == (TempoEvent(0, 400000),)
    assert score.meters == (MeterEvent(0, 3, 4),)

    value["tempos"] = []
    value["meters"] = []
    defaulted = score_from_dict(value)
    assert defaulted.tempos == (TempoEvent(0, 500000),)
    assert defaulted.meters == (MeterEvent(0, 4, 4),)


def test_zero_length_and_unaligned_sections_are_valid():
    score = score_from_dict(load_value("unaligned_markers.json"))
    assert any(section.start_tick == section.end_tick_exclusive for section in score.sections)
    assert any(not section.bar_aligned for section in score.sections)


def test_pure_score_has_no_global_end_validation():
    score = score_from_dict(load_value("truncated_tempo_track.json"))
    assert score.tempos[-1].tick == 960
    assert score.markers[-1].tick == 5000
    assert score.sections[-1].end_tick_exclusive == 6000
    assert "end_tick_exclusive" not in score.__dataclass_fields__


def test_inconsistent_section_alignment_is_rejected_not_repaired():
    value = load_value("unaligned_markers.json")
    value["sections"][1]["bar_aligned"] = True
    assert_serialization_error(
        lambda: score_from_dict(value),
        "invalid_value",
        "$.sections[1].bar_aligned",
    )


def test_complete_sidecar_round_trip_and_metadata_preservation():
    value = load_value("sidecar_v1.json")
    sidecar = sidecar_from_dict(value)
    assert sidecar_from_dict(sidecar_to_dict(sidecar)) == sidecar
    assert sidecar.generator == GeneratorInfo("score-core", "0.2.0", 1, "phase2b-fixture")
    assert sidecar.derived_from == DerivedFrom("velvet-lies.mid", 129, 0)
    assert sidecar.edited is True
    assert type(sidecar.audio_seconds_at_tick_zero) is float


def test_finite_positive_zero_and_negative_audio_alignment(alignment):
    value = load_value("sidecar_v1.json")
    value["audio_seconds_at_tick_zero"] = alignment
    sidecar = sidecar_from_dict(value)
    assert sidecar.audio_seconds_at_tick_zero == float(alignment)
    assert type(sidecar.audio_seconds_at_tick_zero) is float


def test_edited_boolean_round_trip(edited):
    value = load_value("sidecar_v1.json")
    value["edited"] = edited
    assert sidecar_to_dict(sidecar_from_dict(value))["edited"] is edited


def test_marker_and_zero_length_section_exactly_at_sidecar_end_are_valid():
    value = load_value("sidecar_v1.json")
    value["end_tick_exclusive"] = 5000
    value["score"]["sections"] = [
        {
            "name": "End",
            "start_tick": 5000,
            "end_tick_exclusive": 5000,
            "bar_aligned": False,
            "confidence": None,
        }
    ]
    sidecar = sidecar_from_dict(value)
    assert sidecar.score.markers[-1].tick == sidecar.end_tick_exclusive
    assert sidecar.score.sections[-1].start_tick == sidecar.end_tick_exclusive
    assert sidecar.score.sections[-1].end_tick_exclusive == sidecar.end_tick_exclusive


def test_sidecar_rejects_marker_past_end():
    value = load_value("sidecar_v1.json")
    value["score"]["markers"][0]["tick"] = 8001
    assert_serialization_error(
        lambda: sidecar_from_dict(value),
        "invalid_value",
        "$.score.markers[0].tick",
    )


def test_sidecar_rejects_section_start_past_end():
    value = load_value("sidecar_v1.json")
    value["score"]["sections"] = [
        {
            "name": "Past",
            "start_tick": 9600,
            "end_tick_exclusive": 9600,
            "bar_aligned": True,
            "confidence": None,
        }
    ]
    assert_serialization_error(
        lambda: sidecar_from_dict(value),
        "invalid_value",
        "$.score.sections[0].start_tick",
    )


def test_sidecar_rejects_section_end_past_end():
    value = load_value("sidecar_v1.json")
    value["end_tick_exclusive"] = 5999
    assert_serialization_error(
        lambda: sidecar_from_dict(value),
        "invalid_value",
        "$.score.sections[0].end_tick_exclusive",
    )


def test_wrong_root_object_types(value, path):
    assert_serialization_error(lambda: score_from_dict(value), "invalid_type", path)


def test_missing_required_score_fields(field):
    value = load_value("constant_4_4.json")
    del value[field]
    assert_serialization_error(
        lambda: score_from_dict(value),
        "missing_field",
        f"$.{field}",
    )


def test_unknown_fields_at_every_schema_level(fixture, tokens, path):
    value = load_value(fixture)
    value_at(value, tokens)["unexpected"] = 1
    loader = sidecar_from_dict if fixture == "sidecar_v1.json" else score_from_dict
    assert_serialization_error(lambda: loader(value), "unknown_field", path)


def test_unsupported_outer_and_nested_schema_versions():
    score_value = replace_at(load_value("constant_4_4.json"), ("schema_version",), 2)
    assert_serialization_error(
        lambda: score_from_dict(score_value),
        "unsupported_schema_version",
        "$.schema_version",
    )
    sidecar_value = replace_at(load_value("sidecar_v1.json"), ("schema_version",), 2)
    assert_serialization_error(
        lambda: sidecar_from_dict(sidecar_value),
        "unsupported_schema_version",
        "$.schema_version",
    )
    nested_value = replace_at(load_value("sidecar_v1.json"), ("score", "schema_version"), 2)
    assert_serialization_error(
        lambda: sidecar_from_dict(nested_value),
        "unsupported_schema_version",
        "$.score.schema_version",
    )


def test_bool_is_rejected_as_integer_or_real(fixture, tokens, loader, path):
    value = replace_at(load_value(fixture), tokens, True)
    assert_serialization_error(lambda: loader(value), "invalid_type", path)


def test_nonfinite_audio_alignment_is_rejected(number):
    value = replace_at(load_value("sidecar_v1.json"), ("audio_seconds_at_tick_zero",), number)
    assert_serialization_error(
        lambda: sidecar_from_dict(value),
        "invalid_value",
        "$.audio_seconds_at_tick_zero",
    )


def test_real_too_large_to_canonicalize_is_rejected_deterministically():
    value = replace_at(
        load_value("sidecar_v1.json"),
        ("audio_seconds_at_tick_zero",),
        10**10000,
    )
    assert_serialization_error(
        lambda: sidecar_from_dict(value),
        "invalid_value",
        "$.audio_seconds_at_tick_zero",
    )


def test_nonfinite_confidence_is_rejected(number):
    value = replace_at(load_value("unaligned_markers.json"), ("sections", 0, "confidence"), number)
    assert_serialization_error(
        lambda: score_from_dict(value),
        "invalid_value",
        "$.sections[0].confidence",
    )


def test_confidence_accepts_any_finite_real_and_canonicalizes_to_float():
    value = replace_at(load_value("unaligned_markers.json"), ("sections", 0, "confidence"), 7)
    score = score_from_dict(value)
    assert score.sections[0].confidence == 7.0
    assert type(score.sections[0].confidence) is float


def test_invalid_score_values_have_stable_paths(tokens, bad_value, code, path):
    fixture = "odd_meter_31_32.json"
    value = replace_at(load_value(fixture), tokens, bad_value)
    assert_serialization_error(lambda: score_from_dict(value), code, path)


def test_reversed_section_interval_is_rejected():
    value = load_value("odd_meter_31_32.json")
    value["sections"][0]["start_tick"] = 100
    value["sections"][0]["end_tick_exclusive"] = 99
    assert_serialization_error(
        lambda: score_from_dict(value),
        "invalid_value",
        "$.sections[0].end_tick_exclusive",
    )


def test_invalid_sidecar_metadata_has_stable_paths(tokens, bad_value, code, path):
    value = replace_at(load_value("sidecar_v1.json"), tokens, bad_value)
    assert_serialization_error(lambda: sidecar_from_dict(value), code, path)


def test_non_builtin_arrays_and_nested_objects_are_rejected():
    value = load_value("constant_4_4.json")
    value["tempos"] = ListSubclass(value["tempos"])
    assert_serialization_error(lambda: score_from_dict(value), "invalid_type", "$.tempos")

    value = load_value("constant_4_4.json")
    value["meters"][0] = DictSubclass(value["meters"][0])
    assert_serialization_error(lambda: score_from_dict(value), "invalid_type", "$.meters[0]")

    value = load_value("sidecar_v1.json")
    value["generator"] = DictSubclass(value["generator"])
    assert_serialization_error(lambda: sidecar_from_dict(value), "invalid_type", "$.generator")


def test_serializer_imports_are_acyclic_and_production_is_io_free():
    path = ROOT / "score" / "serialize.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    direct_imports = set()
    sibling_imports = {}
    absolute_score_imports = []
    calls = []
    score_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            direct_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                sibling_imports[(node.level, node.module)] = tuple(
                    alias.name for alias in node.names
                )
            else:
                direct_imports.add(node.module or "")
                if node.module is not None and node.module.startswith("score."):
                    absolute_score_imports.append(node.module)
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            calls.append(name)
            if name == "Score":
                score_calls.append(node.lineno)
    assert direct_imports == {"dataclasses", "math"}
    assert sibling_imports == {
        (1, "bars"): ("build_bar_grid",),
        (1, "model"): ("Marker", "MeterEvent", "Score", "Section", "TempoEvent"),
        (1, "normalize"): ("finalize_score", "normalize_events"),
    }
    assert absolute_score_imports == []
    assert not {"open", "print", "read_bytes", "write_bytes", "getenv", "putenv"} & set(calls)
    assert score_calls == []


def test_all_fixture_identities_and_text_format_are_stable():
    identities = {}
    for path in sorted(SCORE_FIXTURES.glob("*.json")):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xEF\xBB\xBF")
        assert b"\r" not in raw
        assert raw.endswith(b"\n")
        assert not raw.endswith(b"\n\n")
        assert all(not line.endswith((b" ", b"\t")) for line in raw.splitlines())
        raw.decode("utf-8", errors="strict")
        identities[path.name] = hashlib.sha256(raw).hexdigest().upper()
    assert tuple(identities) == tuple(sorted(PURE_FIXTURE_NAMES + ("sidecar_v1.json",)))
    assert len(identities) == 8


class ScoreSerializeTests(unittest.TestCase):
    pass


def _add_test(name, function, *args):
    def test(self):
        function(*args)

    test.__name__ = name
    test.__qualname__ = f"ScoreSerializeTests.{name}"
    setattr(ScoreSerializeTests, name, test)


_add_test(
    "test_serialization_value_contracts_are_exact_and_frozen",
    test_serialization_value_contracts_are_exact_and_frozen,
)
_add_test("test_pure_score_exact_shape_and_key_order", test_pure_score_exact_shape_and_key_order)
_add_test(
    "test_sidecar_exact_shape_and_independent_nested_version",
    test_sidecar_exact_shape_and_independent_nested_version,
)
_add_test("test_real_velvet_lies_score_round_trip", test_real_velvet_lies_score_round_trip)

for fixture_name in PURE_FIXTURE_NAMES:
    _add_test(
        f"test_pure_fixture_round_trip_{fixture_name.removesuffix('.json')}",
        test_all_pure_fixtures_round_trip_to_exact_canonical_bytes,
        fixture_name,
    )

_add_test(
    "test_sidecar_fixture_round_trip_to_exact_canonical_bytes",
    test_sidecar_fixture_round_trip_to_exact_canonical_bytes,
)

for source_name in ("json", "midi", "analyzed", "constant"):
    _add_test(
        f"test_source_is_preserved_exactly_{source_name}",
        test_source_is_preserved_exactly,
        source_name,
    )

for estimated in (False, True):
    _add_test(
        f"test_meter_estimated_is_preserved_{str(estimated).lower()}",
        test_meter_estimated_is_preserved,
        estimated,
    )

_add_test(
    "test_derived_meter_flags_are_recomputed_not_serialized",
    test_derived_meter_flags_are_recomputed_not_serialized,
)
_add_test(
    "test_exact_marker_and_section_names_survive_without_unicode_normalization",
    test_exact_marker_and_section_names_survive_without_unicode_normalization,
)
_add_test(
    "test_duplicate_markers_and_sections_are_preserved",
    test_duplicate_markers_and_sections_are_preserved,
)
_add_test(
    "test_same_tick_events_and_defaults_reuse_phase2a_normalization",
    test_same_tick_events_and_defaults_reuse_phase2a_normalization,
)
_add_test(
    "test_zero_length_and_unaligned_sections_are_valid",
    test_zero_length_and_unaligned_sections_are_valid,
)
_add_test(
    "test_pure_score_has_no_global_end_validation",
    test_pure_score_has_no_global_end_validation,
)
_add_test(
    "test_inconsistent_section_alignment_is_rejected_not_repaired",
    test_inconsistent_section_alignment_is_rejected_not_repaired,
)
_add_test(
    "test_complete_sidecar_round_trip_and_metadata_preservation",
    test_complete_sidecar_round_trip_and_metadata_preservation,
)

for name, alignment in (("positive", 1.25), ("zero", 0), ("negative", -2.5)):
    _add_test(
        f"test_finite_audio_alignment_{name}",
        test_finite_positive_zero_and_negative_audio_alignment,
        alignment,
    )

for edited_value in (False, True):
    _add_test(
        f"test_edited_boolean_round_trip_{str(edited_value).lower()}",
        test_edited_boolean_round_trip,
        edited_value,
    )

_add_test(
    "test_marker_and_zero_length_section_exactly_at_sidecar_end_are_valid",
    test_marker_and_zero_length_section_exactly_at_sidecar_end_are_valid,
)
_add_test("test_sidecar_rejects_marker_past_end", test_sidecar_rejects_marker_past_end)
_add_test(
    "test_sidecar_rejects_section_start_past_end",
    test_sidecar_rejects_section_start_past_end,
)
_add_test(
    "test_sidecar_rejects_section_end_past_end",
    test_sidecar_rejects_section_end_past_end,
)

_add_test("test_wrong_root_type_list", test_wrong_root_object_types, [], "$")
_add_test(
    "test_wrong_root_type_dict_subclass",
    test_wrong_root_object_types,
    DictSubclass(load_value("constant_4_4.json")),
    "$",
)

for field_name in ("schema_version", "ticks_per_quarter", "source"):
    _add_test(
        f"test_missing_required_score_field_{field_name}",
        test_missing_required_score_fields,
        field_name,
    )

for name, fixture_name, tokens, path in (
    ("score", "constant_4_4.json", (), "$.unexpected"),
    ("tempo", "constant_4_4.json", ("tempos", 0), "$.tempos[0].unexpected"),
    ("meter", "constant_4_4.json", ("meters", 0), "$.meters[0].unexpected"),
    ("marker", "odd_meter_31_32.json", ("markers", 0), "$.markers[0].unexpected"),
    ("section", "odd_meter_31_32.json", ("sections", 0), "$.sections[0].unexpected"),
    ("sidecar", "sidecar_v1.json", (), "$.unexpected"),
    ("generator", "sidecar_v1.json", ("generator",), "$.generator.unexpected"),
    ("derived_from", "sidecar_v1.json", ("derived_from",), "$.derived_from.unexpected"),
    ("nested_score", "sidecar_v1.json", ("score",), "$.score.unexpected"),
):
    _add_test(
        f"test_unknown_field_at_{name}_level",
        test_unknown_fields_at_every_schema_level,
        fixture_name,
        tokens,
        path,
    )

_add_test(
    "test_unsupported_outer_and_nested_schema_versions",
    test_unsupported_outer_and_nested_schema_versions,
)

for name, fixture_name, tokens, loader, path in (
    ("ticks_per_quarter", "constant_4_4.json", ("ticks_per_quarter",), score_from_dict, "$.ticks_per_quarter"),
    ("tempo_tick", "constant_4_4.json", ("tempos", 0, "tick"), score_from_dict, "$.tempos[0].tick"),
    ("sidecar_extent", "sidecar_v1.json", ("end_tick_exclusive",), sidecar_from_dict, "$.end_tick_exclusive"),
    ("algorithm_version", "sidecar_v1.json", ("generator", "algorithm_version"), sidecar_from_dict, "$.generator.algorithm_version"),
    ("audio_alignment", "sidecar_v1.json", ("audio_seconds_at_tick_zero",), sidecar_from_dict, "$.audio_seconds_at_tick_zero"),
):
    _add_test(
        f"test_bool_is_rejected_as_{name}",
        test_bool_is_rejected_as_integer_or_real,
        fixture_name,
        tokens,
        loader,
        path,
    )

for name, number in (("nan", math.nan), ("positive_infinity", math.inf), ("negative_infinity", -math.inf)):
    _add_test(
        f"test_nonfinite_audio_alignment_{name}",
        test_nonfinite_audio_alignment_is_rejected,
        number,
    )

_add_test(
    "test_real_too_large_to_canonicalize_is_rejected_deterministically",
    test_real_too_large_to_canonicalize_is_rejected_deterministically,
)

for name, number in (("nan", math.nan), ("positive_infinity", math.inf), ("negative_infinity", -math.inf)):
    _add_test(
        f"test_nonfinite_confidence_{name}",
        test_nonfinite_confidence_is_rejected,
        number,
    )

_add_test(
    "test_confidence_accepts_any_finite_real_and_canonicalizes_to_float",
    test_confidence_accepts_any_finite_real_and_canonicalizes_to_float,
)

for name, tokens, bad_value, code, path in (
    ("source", ("source",), "other", "invalid_value", "$.source"),
    ("ticks_per_quarter", ("ticks_per_quarter",), 0, "invalid_value", "$.ticks_per_quarter"),
    ("tempo_value", ("tempos", 0, "us_per_quarter"), 0, "invalid_value", "$.tempos[0].us_per_quarter"),
    ("tempo_tick", ("tempos", 0, "tick"), -1, "invalid_value", "$.tempos[0].tick"),
    ("meter_tick", ("meters", 0, "tick"), -1, "invalid_value", "$.meters[0].tick"),
    ("meter_numerator", ("meters", 0, "numerator"), 0, "invalid_value", "$.meters[0].numerator"),
    ("meter_denominator_non_power", ("meters", 0, "denominator"), 3, "invalid_value", "$.meters[0].denominator"),
    ("meter_denominator_too_large", ("meters", 0, "denominator"), 128, "invalid_value", "$.meters[0].denominator"),
    ("marker_tick", ("markers", 0, "tick"), -1, "invalid_value", "$.markers[0].tick"),
    ("section_start", ("sections", 0, "start_tick"), -1, "invalid_value", "$.sections[0].start_tick"),
    ("section_name_type", ("sections", 0, "name"), 1, "invalid_type", "$.sections[0].name"),
    ("section_alignment_type", ("sections", 0, "bar_aligned"), 1, "invalid_type", "$.sections[0].bar_aligned"),
):
    _add_test(
        f"test_invalid_score_{name}",
        test_invalid_score_values_have_stable_paths,
        tokens,
        bad_value,
        code,
        path,
    )

_add_test("test_reversed_section_interval_is_rejected", test_reversed_section_interval_is_rejected)

for name, tokens, bad_value, code, path in (
    ("extent", ("end_tick_exclusive",), -1, "invalid_value", "$.end_tick_exclusive"),
    ("generator_name", ("generator", "name"), 1, "invalid_type", "$.generator.name"),
    ("algorithm_version", ("generator", "algorithm_version"), -1, "invalid_value", "$.generator.algorithm_version"),
    ("filename", ("derived_from", "filename"), 1, "invalid_type", "$.derived_from.filename"),
    ("size", ("derived_from", "size"), -1, "invalid_value", "$.derived_from.size"),
    ("mtime_ns", ("derived_from", "mtime_ns"), -1, "invalid_value", "$.derived_from.mtime_ns"),
    ("edited", ("edited",), 0, "invalid_type", "$.edited"),
):
    _add_test(
        f"test_invalid_sidecar_metadata_{name}",
        test_invalid_sidecar_metadata_has_stable_paths,
        tokens,
        bad_value,
        code,
        path,
    )

_add_test(
    "test_non_builtin_arrays_and_nested_objects_are_rejected",
    test_non_builtin_arrays_and_nested_objects_are_rejected,
)
_add_test(
    "test_serializer_imports_are_acyclic_and_production_is_io_free",
    test_serializer_imports_are_acyclic_and_production_is_io_free,
)
_add_test(
    "test_all_fixture_identities_and_text_format_are_stable",
    test_all_fixture_identities_and_text_format_are_stable,
)

assert len(unittest.defaultTestLoader.getTestCaseNames(ScoreSerializeTests)) == 86
