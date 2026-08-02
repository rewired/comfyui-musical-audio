import ast
import dataclasses
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from score.midi_parse import parse_midi
from score.model import MeterEvent, ProviderResult, Score, Section, TempoEvent
from score.providers import (
    ANALYSIS_PROVIDER_CONTRACT,
    BLANK_SCORE_FILE,
    SCORE_FINGERPRINT_VERSION,
    AnalysisProvider,
    ConstantProvider,
    ExplicitFileProvider,
    MidiSidecarProvider,
    ProviderChainError,
    ProviderSelection,
    ScoreResolution,
    SidecarJsonProvider,
    build_score_fingerprint,
    derive_automatic_candidate_paths,
    fingerprint_candidate,
    normalize_candidate_path,
    resolve_provider_chain,
    select_section,
)
from score.serialize import score_from_dict
try:
    from tests.score_smf_test_utils import meta_event, smf, track_chunk
except ModuleNotFoundError:
    from score_smf_test_utils import meta_event, smf, track_chunk


ROOT = Path(__file__).resolve().parents[1]
SCORE_FIXTURES = ROOT / "tests" / "fixtures" / "scores"


def load_json_fixture(name: str) -> dict[str, object]:
    return json.loads((SCORE_FIXTURES / name).read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def basic_score() -> Score:
    return Score(
        ticks_per_quarter=480,
        tempos=(TempoEvent(0, 500_000),),
        meters=(MeterEvent(0, 4, 4),),
        markers=(),
        sections=(),
        source="json",
    )


def minimal_midi(*, diagnostic: bool = False) -> bytes:
    track = track_chunk(
        (meta_event(0, 0x51, (500_000).to_bytes(3, "big")),),
        include_eot=not diagnostic,
    )
    return smf((track,))


def sidecar_value(
    audio_path: Path,
    *,
    edited: bool = False,
    stale: bool = False,
    alignment: float = 1.25,
) -> dict[str, object]:
    value = load_json_fixture("sidecar_v1.json")
    state = audio_path.stat()
    value["audio_seconds_at_tick_zero"] = alignment
    value["derived_from"] = {
        "filename": audio_path.name,
        "size": state.st_size + (1 if stale else 0),
        "mtime_ns": state.st_mtime_ns,
    }
    value["edited"] = edited
    return value


class StubProvider:
    def __init__(self, kind: str, selection: object) -> None:
        self.kind = kind
        self.selection = selection
        self.calls = 0

    def resolve(self):
        self.calls += 1
        return self.selection


def selection(
    provider: str,
    status: str,
    *,
    score: Score | None = None,
    diagnostics: tuple[str, ...] = (),
    provenance: object = None,
    alignment: float | None = None,
) -> ProviderSelection:
    return ProviderSelection(
        provider,
        ProviderResult(
            status,
            score,
            diagnostics,
            {} if provenance is None else provenance,
        ),
        alignment,
    )


class ProviderValueContractTests(unittest.TestCase):
    def test_provider_value_types_are_frozen_with_exact_fields(self):
        expected = {
            ProviderSelection: (
                "provider",
                "result",
                "provider_alignment_seconds",
            ),
            ScoreResolution: (
                "resolved_score",
                "provider",
                "provider_alignment_seconds",
                "diagnostics",
                "provenance",
                "dependency_fingerprint",
            ),
        }
        for value_type, fields in expected.items():
            with self.subTest(value_type=value_type.__name__):
                self.assertEqual(
                    tuple(field.name for field in dataclasses.fields(value_type)),
                    fields,
                )
                self.assertTrue(value_type.__dataclass_params__.frozen)

    def test_provider_classes_have_exact_kinds(self):
        self.assertEqual(ExplicitFileProvider("", None).kind, "explicit")
        self.assertEqual(SidecarJsonProvider(None).kind, "json_sidecar")
        self.assertEqual(MidiSidecarProvider(None).kind, "midi_sidecar")
        self.assertEqual(AnalysisProvider().kind, "analysis")
        self.assertEqual(ConstantProvider().kind, "constant")

    def test_analysis_provider_returns_exact_empty_result(self):
        with mock.patch("builtins.open", side_effect=AssertionError("unexpected read")), mock.patch(
            "score.providers.os.stat", side_effect=AssertionError("unexpected stat")
        ):
            result = AnalysisProvider().resolve()
        self.assertEqual(
            result,
            selection("analysis", "not_applicable"),
        )
        self.assertIsNone(result.provider_alignment_seconds)

    def test_constant_provider_returns_exact_terminal_result(self):
        result = ConstantProvider().resolve()
        self.assertEqual(
            result,
            selection(
                "constant",
                "found",
                provenance={"format": "constant", "provider": "constant"},
            ),
        )
        self.assertIsNone(result.result.score)

    def test_constant_chain_terminates_without_synthetic_score(self):
        fingerprint = (SCORE_FINGERPRINT_VERSION, ANALYSIS_PROVIDER_CONTRACT)
        result = resolve_provider_chain(
            (AnalysisProvider(), ConstantProvider()),
            audio_seconds_at_tick_zero=0.0,
            dependency_fingerprint=fingerprint,
        )
        self.assertIsNone(result.resolved_score)
        self.assertEqual(result.provider, "constant")
        self.assertIs(result.dependency_fingerprint, fingerprint)

    def test_first_found_wins_and_later_provider_is_not_called(self):
        first = StubProvider(
            "explicit",
            selection("explicit", "found", score=basic_score()),
        )
        later = StubProvider("constant", ConstantProvider().resolve())
        result = resolve_provider_chain(
            (first, later),
            audio_seconds_at_tick_zero=2.5,
            dependency_fingerprint=("test",),
        )
        self.assertEqual((first.calls, later.calls), (1, 0))
        self.assertEqual(result.provider, "explicit")
        self.assertEqual(result.resolved_score.audio_seconds_at_tick_zero, 2.5)

    def test_invalid_stops_and_raises_stable_chain_error(self):
        invalid = StubProvider(
            "json_sidecar",
            selection(
                "json_sidecar",
                "invalid",
                diagnostics=("broken",),
                provenance={"provider": "json_sidecar"},
            ),
        )
        later = StubProvider("constant", ConstantProvider().resolve())
        with self.assertRaises(ProviderChainError) as caught:
            resolve_provider_chain(
                (invalid, later),
                audio_seconds_at_tick_zero=0.0,
                dependency_fingerprint=("test",),
            )
        self.assertEqual(later.calls, 0)
        self.assertEqual(caught.exception.provider, "json_sidecar")
        self.assertEqual(caught.exception.diagnostics, ("broken",))
        self.assertEqual(str(caught.exception), "json_sidecar provider is invalid: broken")

    def test_winning_diagnostics_provenance_and_alignment_are_preserved(self):
        diagnostics = ("first", "second")
        provenance = {"provider": "explicit", "alignment": "999"}
        provider = StubProvider(
            "explicit",
            selection(
                "explicit",
                "found",
                score=basic_score(),
                diagnostics=diagnostics,
                provenance=provenance,
                alignment=1.75,
            ),
        )
        result = resolve_provider_chain(
            (provider,),
            audio_seconds_at_tick_zero=-0.5,
            dependency_fingerprint=("test",),
        )
        self.assertIs(result.diagnostics, diagnostics)
        self.assertIs(result.provenance, provenance)
        self.assertEqual(result.provider_alignment_seconds, 1.75)
        self.assertEqual(result.resolved_score.audio_seconds_at_tick_zero, -0.5)

    def test_malformed_status_invariants_are_rejected(self):
        cases = (
            selection("explicit", "not_applicable", score=basic_score()),
            selection("explicit", "found"),
            selection("explicit", "invalid", score=basic_score(), diagnostics=("x",)),
            selection("constant", "found", score=basic_score()),
            selection("explicit", "invalid"),
        )
        for malformed in cases:
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                resolve_provider_chain(
                    (StubProvider(malformed.provider, malformed),),
                    audio_seconds_at_tick_zero=0.0,
                    dependency_fingerprint=("test",),
                )

    def test_malformed_selection_shapes_are_rejected(self):
        cases = (
            StubProvider("explicit", object()),
            StubProvider("explicit", selection("json_sidecar", "not_applicable")),
            StubProvider("unknown", selection("unknown", "not_applicable")),
            StubProvider("explicit", selection("explicit", "unknown")),
            StubProvider(
                "explicit",
                selection("explicit", "not_applicable", provenance={"x": 1}),
            ),
            StubProvider(
                "explicit",
                selection("explicit", "found", score=basic_score(), alignment=1),
            ),
        )
        for provider in cases:
            with self.subTest(provider=provider.kind), self.assertRaises((TypeError, ValueError)):
                resolve_provider_chain(
                    (provider,),
                    audio_seconds_at_tick_zero=0.0,
                    dependency_fingerprint=("test",),
                )

    def test_chain_requires_finite_float_alignment_and_immutable_fingerprint(self):
        for value in (0, True, math.nan, math.inf):
            with self.subTest(alignment=value), self.assertRaises(TypeError):
                resolve_provider_chain(
                    (ConstantProvider(),),
                    audio_seconds_at_tick_zero=value,
                    dependency_fingerprint=("test",),
                )
        for fingerprint in (["mutable"], ("nested", []), ("nan", math.nan)):
            with self.subTest(fingerprint=fingerprint), self.assertRaises(TypeError):
                resolve_provider_chain(
                    (ConstantProvider(),),
                    audio_seconds_at_tick_zero=0.0,
                    dependency_fingerprint=fingerprint,
                )

    def test_chain_without_terminator_fails_deterministically(self):
        with self.assertRaisesRegex(RuntimeError, "ended without a found result"):
            resolve_provider_chain(
                (AnalysisProvider(),),
                audio_seconds_at_tick_zero=0.0,
                dependency_fingerprint=("test",),
            )


class ExplicitProviderTests(unittest.TestCase):
    def test_blank_and_whitespace_are_not_applicable(self):
        for value in ("", " ", "\t\r\n"):
            with self.subTest(value=value):
                self.assertEqual(
                    ExplicitFileProvider(value, None).resolve().result.status,
                    "not_applicable",
                )

    def test_literal_none_is_not_a_sentinel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "none"
            path.write_text("{}", encoding="utf-8")
            result = ExplicitFileProvider("none", path).resolve()
        self.assertEqual(result.result.status, "invalid")
        self.assertEqual(result.result.diagnostics, ("explicit_suffix_unsupported:<none>",))

    def test_unresolved_and_resolved_missing_are_distinct_invalid_results(self):
        unresolved = ExplicitFileProvider("score.json", None).resolve()
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "score.json"
            missing = ExplicitFileProvider("score.json", missing_path).resolve()
        self.assertEqual(unresolved.result.status, "invalid")
        self.assertEqual(missing.result.status, "invalid")
        self.assertNotEqual(unresolved.result.diagnostics, missing.result.diagnostics)

    def test_supported_json_suffixes_are_case_insensitive(self):
        value = load_json_fixture("constant_4_4.json")
        with tempfile.TemporaryDirectory() as directory:
            for name in ("score.json", "score.score.json", "SCORE.JSON"):
                with self.subTest(name=name):
                    path = Path(directory) / name
                    write_json(path, value)
                    result = ExplicitFileProvider(name, path).resolve()
                    self.assertEqual(result.result.status, "found")
                    self.assertEqual(result.result.score.source, "constant")

    def test_supported_midi_suffixes_are_case_insensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("score.mid", "score.midi", "SCORE.MIDI"):
                with self.subTest(name=name):
                    path = Path(directory) / name
                    path.write_bytes(minimal_midi())
                    result = ExplicitFileProvider(name, path).resolve()
                    self.assertEqual(result.result.status, "found")
                    self.assertEqual(result.result.score.source, "midi")

    def test_unsupported_suffix_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.txt"
            path.write_text("{}", encoding="utf-8")
            result = ExplicitFileProvider("score.txt", path).resolve()
        self.assertEqual(result.result.status, "invalid")
        self.assertEqual(result.result.diagnostics, ("explicit_suffix_unsupported:.txt",))

    def test_json_suffix_does_not_sniff_or_fallback_to_midi(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.json"
            path.write_bytes(minimal_midi())
            with mock.patch("score.providers.parse_midi") as parser:
                result = ExplicitFileProvider("score.json", path).resolve()
        self.assertEqual(result.result.status, "invalid")
        parser.assert_not_called()

    def test_midi_suffix_does_not_sniff_or_fallback_to_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.mid"
            write_json(path, load_json_fixture("constant_4_4.json"))
            result = ExplicitFileProvider("score.mid", path).resolve()
        self.assertEqual(result.result.status, "invalid")
        self.assertIn("midi_parse_error", result.result.diagnostics[0])


class JsonProviderTests(unittest.TestCase):
    def test_explicit_bare_score_has_none_typed_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.json"
            write_json(path, load_json_fixture("constant_4_4.json"))
            result = ExplicitFileProvider("score.json", path).resolve()
        self.assertEqual(result.result.status, "found")
        self.assertIsNone(result.provider_alignment_seconds)
        self.assertEqual(result.result.provenance["schema"], "score")

    def test_explicit_wrapper_preserves_typed_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "song.wav"
            audio.write_bytes(b"audio")
            path = Path(directory) / "score.score.json"
            write_json(path, sidecar_value(audio, alignment=-0.75))
            result = ExplicitFileProvider("score.score.json", path, audio).resolve()
        self.assertEqual(result.result.status, "found")
        self.assertEqual(result.provider_alignment_seconds, -0.75)
        self.assertIs(type(result.provider_alignment_seconds), float)

    def test_automatic_json_accepts_bare_and_wrapper_schemas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.flac"
            audio.write_bytes(b"flac-audio")
            path = root / "song.score.json"
            for value, expected_alignment in (
                (load_json_fixture("constant_4_4.json"), None),
                (sidecar_value(audio, alignment=3.5), 3.5),
            ):
                with self.subTest(wrapper="score" in value):
                    write_json(path, value)
                    result = SidecarJsonProvider(path, audio).resolve()
                    self.assertEqual(result.result.status, "found")
                    self.assertEqual(result.provider_alignment_seconds, expected_alignment)

    def test_non_object_json_roots_are_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.json"
            for root in ([], "value", 7, True, None):
                with self.subTest(root=root):
                    write_json(path, root)
                    result = ExplicitFileProvider("score.json", path).resolve()
                    self.assertEqual(result.result.status, "invalid")
                    self.assertIn("json_root_invalid", result.result.diagnostics[0])

    def test_both_schema_discriminators_are_ambiguous(self):
        value = load_json_fixture("constant_4_4.json")
        value["score"] = {}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.json"
            write_json(path, value)
            result = ExplicitFileProvider("score.json", path).resolve()
        self.assertIn("json_schema_ambiguous", result.result.diagnostics[0])

    def test_neither_discriminator_is_unknown_even_with_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.json"
            write_json(path, {"schema_version": 1})
            result = ExplicitFileProvider("score.json", path).resolve()
        self.assertIn("json_schema_unknown", result.result.diagnostics[0])

    def test_strict_serialization_failure_preserves_stable_fields(self):
        value = load_json_fixture("constant_4_4.json")
        value["unexpected"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.json"
            write_json(path, value)
            result = ExplicitFileProvider("score.json", path).resolve()
        self.assertEqual(result.result.status, "invalid")
        self.assertIn("code=unknown_field", result.result.diagnostics[0])
        self.assertIn("path=$.unexpected", result.result.diagnostics[0])
        self.assertEqual(result.result.provenance["serialization_code"], "unknown_field")

    def test_malformed_json_is_invalid_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.json"
            path.write_text("{", encoding="utf-8")
            with mock.patch("score.providers.score_from_dict") as bare_decoder, mock.patch(
                "score.providers.sidecar_from_dict"
            ) as wrapper_decoder:
                result = ExplicitFileProvider("score.json", path).resolve()
        self.assertEqual(result.result.status, "invalid")
        bare_decoder.assert_not_called()
        wrapper_decoder.assert_not_called()

    def test_alignment_is_not_recovered_from_provenance(self):
        provider = StubProvider(
            "explicit",
            selection(
                "explicit",
                "found",
                score=basic_score(),
                provenance={"audio_seconds_at_tick_zero": "99.0"},
            ),
        )
        result = resolve_provider_chain(
            (provider,),
            audio_seconds_at_tick_zero=1.0,
            dependency_fingerprint=("test",),
        )
        self.assertIsNone(result.provider_alignment_seconds)
        self.assertEqual(result.resolved_score.audio_seconds_at_tick_zero, 1.0)


class SourceIdentityTests(unittest.TestCase):
    def test_matching_wav_and_flac_identity_are_found(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for audio_name in ("Song.WAV", "Song.FLAC"):
                with self.subTest(audio_name=audio_name):
                    audio = root / audio_name
                    audio.write_bytes(b"audio")
                    path = root / f"sidecar-{audio.suffix[1:].lower()}.json"
                    write_json(path, sidecar_value(audio))
                    result = SidecarJsonProvider(path, audio).resolve()
                    self.assertEqual(result.result.status, "found")
                    self.assertEqual(result.result.diagnostics, ())

    def test_automatic_unedited_stale_wrapper_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.flac"
            audio.write_bytes(b"audio")
            path = root / "song.score.json"
            write_json(path, sidecar_value(audio, stale=True, edited=False))
            result = SidecarJsonProvider(path, audio).resolve()
        self.assertEqual(result.result.status, "not_applicable")
        self.assertEqual(result.result.diagnostics, ())

    def test_automatic_edited_stale_wrapper_is_found_with_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.flac"
            audio.write_bytes(b"audio")
            path = root / "song.score.json"
            write_json(path, sidecar_value(audio, stale=True, edited=True))
            result = SidecarJsonProvider(path, audio).resolve()
        self.assertEqual(result.result.status, "found")
        self.assertEqual(
            result.result.diagnostics,
            ("score_source_identity_mismatch:sidecar does not match the audio file",),
        )

    def test_explicit_stale_wrapper_is_found_with_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            path = root / "manual.json"
            write_json(path, sidecar_value(audio, stale=True, edited=False))
            result = ExplicitFileProvider("manual.json", path, audio).resolve()
        self.assertEqual(result.result.status, "found")
        self.assertTrue(result.result.diagnostics)

    def test_missing_audio_identity_does_not_invent_a_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            path = root / "song.score.json"
            write_json(path, sidecar_value(audio, stale=True))
            audio.unlink()
            result = SidecarJsonProvider(path, audio).resolve()
        self.assertEqual(result.result.status, "found")
        self.assertEqual(result.result.diagnostics, ())


class MidiProviderTests(unittest.TestCase):
    def test_valid_midi_uses_parsed_score_and_none_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.mid"
            path.write_bytes(minimal_midi())
            result = MidiSidecarProvider(path).resolve()
        self.assertEqual(result.result.status, "found")
        self.assertEqual(result.result.score.source, "midi")
        self.assertIsNone(result.provider_alignment_seconds)

    def test_malformed_midi_preserves_stable_error_location(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.mid"
            path.write_bytes(b"not midi")
            result = MidiSidecarProvider(path).resolve()
        self.assertEqual(result.result.status, "invalid")
        diagnostic = result.result.diagnostics[0]
        self.assertIn("code=invalid_header", diagnostic)
        self.assertIn("byte_offset=0", diagnostic)
        self.assertNotIn("Traceback", diagnostic)

    def test_parser_diagnostics_and_end_tick_provenance_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.mid"
            path.write_bytes(minimal_midi(diagnostic=True))
            result = MidiSidecarProvider(path).resolve()
        expected = parse_midi(minimal_midi(diagnostic=True))
        self.assertEqual(result.result.diagnostics, expected.diagnostics)
        self.assertEqual(
            result.result.provenance["end_tick_exclusive"],
            str(expected.end_tick_exclusive),
        )

    def test_parse_midi_is_called_exactly_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.mid"
            path.write_bytes(minimal_midi())
            with mock.patch("score.providers.parse_midi", wraps=parse_midi) as parser:
                result = MidiSidecarProvider(path).resolve()
        self.assertEqual(result.result.status, "found")
        parser.assert_called_once()
        self.assertIs(type(parser.call_args.args[0]), bytes)

    def test_missing_automatic_candidates_are_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_json = Path(directory) / "song.score.json"
            missing_midi = Path(directory) / "song.mid"
            self.assertEqual(
                SidecarJsonProvider(missing_json).resolve().result.status,
                "not_applicable",
            )
            self.assertEqual(
                MidiSidecarProvider(missing_midi).resolve().result.status,
                "not_applicable",
            )

    def test_unreadable_automatic_candidates_are_invalid(self):
        for provider in (SidecarJsonProvider("score.json"), MidiSidecarProvider("score.mid")):
            with self.subTest(provider=provider.kind), mock.patch(
                "score.providers.os.stat", side_effect=PermissionError
            ):
                result = provider.resolve()
                self.assertEqual(result.result.status, "invalid")
                self.assertTrue(result.result.diagnostics)


class FingerprintTests(unittest.TestCase):
    def test_path_normalization_uses_the_binding_expression(self):
        path = Path("folder") / ".." / "track.flac"
        expected = os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))
        self.assertEqual(normalize_candidate_path(path), expected)

    def test_wav_and_flac_derive_suffix_replacing_candidates(self):
        for audio_name in ("music.wav", "music.flac", "MUSIC.FLAC"):
            with self.subTest(audio_name=audio_name):
                json_path, midi_path = derive_automatic_candidate_paths(audio_name)
                stem = os.path.splitext(normalize_candidate_path(audio_name))[0]
                self.assertEqual(json_path, f"{stem}.score.json")
                self.assertEqual(midi_path, f"{stem}.mid")

    def test_candidate_fingerprint_has_missing_file_and_unreadable_states(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.mid"
            missing = fingerprint_candidate("midi_sidecar", path)
            self.assertEqual(missing[2], "missing")
            path.write_bytes(b"data")
            present = fingerprint_candidate("midi_sidecar", path)
            self.assertEqual(present[2], "file")
            self.assertEqual(present[3], 4)
            with mock.patch("score.providers.os.stat", side_effect=PermissionError):
                unreadable = fingerprint_candidate("midi_sidecar", path)
            self.assertEqual(unreadable[2], "unreadable")

    def test_complete_fingerprint_has_exact_order_and_missing_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "song.score.json"
            midi_path = root / "song.mid"
            fingerprint = build_score_fingerprint(
                score_file="",
                explicit_path=None,
                json_sidecar_path=json_path,
                midi_sidecar_path=midi_path,
            )
        self.assertEqual(fingerprint[0], SCORE_FINGERPRINT_VERSION)
        self.assertEqual(fingerprint[1], BLANK_SCORE_FILE)
        self.assertEqual(fingerprint[2][:3], ("json_sidecar", normalize_candidate_path(json_path), "missing"))
        self.assertEqual(fingerprint[3][:3], ("midi_sidecar", normalize_candidate_path(midi_path), "missing"))
        self.assertEqual(fingerprint[4], ANALYSIS_PROVIDER_CONTRACT)

    def test_explicit_raw_value_and_candidate_are_included(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = root / "manual.json"
            json_path = root / "song.score.json"
            midi_path = root / "song.mid"
            fingerprint = build_score_fingerprint(
                score_file="  manual.json  ",
                explicit_path=explicit,
                json_sidecar_path=json_path,
                midi_sidecar_path=midi_path,
            )
        self.assertEqual(fingerprint[1], "  manual.json  ")
        self.assertEqual(fingerprint[2][0], "explicit")

    def test_appearance_mutation_and_removal_change_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "song.score.json"
            midi_path = root / "song.mid"

            def current():
                return build_score_fingerprint(
                    score_file="",
                    explicit_path=None,
                    json_sidecar_path=json_path,
                    midi_sidecar_path=midi_path,
                )

            missing = current()
            midi_path.write_bytes(b"a")
            appeared = current()
            midi_path.write_bytes(b"longer")
            mutated = current()
            json_path.write_text("{}", encoding="utf-8")
            higher_priority = current()
            json_path.unlink()
            midi_path.unlink()
            removed = current()
        self.assertNotEqual(missing, appeared)
        self.assertNotEqual(appeared, mutated)
        self.assertNotEqual(mutated, higher_priority)
        self.assertNotEqual(higher_priority, removed)
        self.assertEqual(missing, removed)

    def test_fingerprint_is_deterministic_and_never_opens_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "song.score.json"
            midi_path = root / "song.mid"
            json_path.write_text("content is irrelevant", encoding="utf-8")
            midi_path.write_bytes(b"also irrelevant")
            with mock.patch("builtins.open", side_effect=AssertionError("content read")):
                first = build_score_fingerprint(
                    score_file="",
                    explicit_path=None,
                    json_sidecar_path=json_path,
                    midi_sidecar_path=midi_path,
                )
                second = build_score_fingerprint(
                    score_file="",
                    explicit_path=None,
                    json_sidecar_path=json_path,
                    midi_sidecar_path=midi_path,
                )
        self.assertEqual(first, second)
        self.assertIs(type(first), tuple)
        self.assertTrue(all(type(item) is tuple for item in first))


class SectionSelectionTests(unittest.TestCase):
    def test_half_open_boundaries_include_start_and_exclude_end(self):
        section = Section("A", 10, 20, False)
        self.assertIs(select_section((section,), 10), section)
        self.assertIs(select_section((section,), 19.999), section)
        self.assertIsNone(select_section((section,), 20))

    def test_overlap_prefers_greatest_start(self):
        outer = Section("outer", 0, 100, True)
        inner = Section("inner", 50, 90, False)
        self.assertIs(select_section((outer, inner), 60), inner)

    def test_overlap_then_prefers_smallest_end(self):
        long = Section("long", 10, 100, False)
        short = Section("short", 10, 80, False)
        self.assertIs(select_section((long, short), 20), short)

    def test_overlap_final_tie_prefers_lexicographically_smallest_name(self):
        beta = Section("Beta", 10, 80, False)
        alpha = Section("Alpha", 10, 80, False)
        self.assertIs(select_section((beta, alpha), 20), alpha)

    def test_selection_is_independent_of_input_order(self):
        sections = (
            Section("outer", 0, 100, True),
            Section("Beta", 10, 80, False),
            Section("Alpha", 10, 80, False),
        )
        self.assertEqual(
            select_section(sections, 20),
            select_section(tuple(reversed(sections)), 20),
        )

    def test_zero_length_and_track_end_marker_never_win(self):
        marker = Section("End", 100, 100, False)
        self.assertIsNone(select_section((marker,), 100))

    def test_no_match_returns_none_without_mutation(self):
        sections = [Section("A", 10, 20, False)]
        original = list(sections)
        self.assertIsNone(select_section(sections, 0))
        self.assertEqual(sections, original)

    def test_invalid_query_values_are_rejected(self):
        for value in (True, False, "1", None, math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                select_section((), value)

    def test_invalid_section_collection_is_rejected(self):
        with self.assertRaises(TypeError):
            select_section("sections", 0)
        with self.assertRaises(TypeError):
            select_section((object(),), 0)


class ArchitectureTests(unittest.TestCase):
    def test_provider_module_uses_only_stdlib_and_relative_score_imports(self):
        tree = ast.parse((ROOT / "score" / "providers.py").read_text(encoding="utf-8"))
        absolute = set()
        relatives = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                absolute.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 1:
                    relatives.add(node.module)
                else:
                    absolute.add(node.module or "")
        self.assertEqual(
            absolute,
            {"collections.abc", "dataclasses", "json", "math", "os", "typing"},
        )
        self.assertEqual(relatives, {"midi_parse", "model", "serialize"})

    def test_provider_module_contains_no_forbidden_dependencies_or_operations(self):
        text = (ROOT / "score" / "providers.py").read_text(encoding="utf-8")
        for forbidden in (
            "folder_paths",
            "torch",
            "import av",
            "ComfyUI",
            "musical_audio_ui",
            "musical_timing",
            "audio_clip_plan",
            "logging",
            "getenv",
            "putenv",
            "socket",
            "requests",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_no_import_time_filesystem_calls(self):
        tree = ast.parse((ROOT / "score" / "providers.py").read_text(encoding="utf-8"))
        top_level_calls = []
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                top_level_calls.append(node.value)
        self.assertEqual(top_level_calls, [])

    def test_flat_score_package_import_is_silent(self):
        code = """
import os, sys
before = dict(os.environ)
import score.providers
assert os.environ == before
forbidden = [name for name in sys.modules if name.startswith(('av', 'torch', 'comfy', 'server', 'waveform_routes'))]
assert forbidden == [], forbidden
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

    def test_arbitrary_parent_package_import_is_silent_without_score_leakage(self):
        code = """
import importlib, os, pathlib, sys, types
before = dict(os.environ)
outer = types.ModuleType('phase4a_outer')
outer.__path__ = []
inner = types.ModuleType('phase4a_outer.child')
inner.__path__ = [str(pathlib.Path.cwd())]
sys.modules['phase4a_outer'] = outer
sys.modules['phase4a_outer.child'] = inner
importlib.import_module('phase4a_outer.child.score.providers')
assert os.environ == before
assert 'score' not in sys.modules
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
