from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from score.providers import ProviderChainError, ScoreResolution
from score.runtime import (
    ResolvedPathState, ScoreRepositoryError, ScoreRepositoryResult,
    append_resolver_diagnostics, diagnostic, prepare_score_repository_request,
    resolve_score_repository, serialize_diagnostics,
)


class RuntimeTests(unittest.TestCase):
    def test_request_preserves_blank_literal_none_and_unavailable_candidates(self):
        for raw in ("", "  ", "none"):
            request = prepare_score_repository_request(
                score_file=raw, audio_path=None,
                explicit=ResolvedPathState(raw, None, None),
            )
            self.assertEqual(request.score_file, raw)
            self.assertIsNone(request.json_sidecar_path)
            self.assertEqual(request.dependency_fingerprint[-3], request.dependency_fingerprint[-2])
        with self.assertRaises(FrozenInstanceError):
            request.score_file = "changed"

    def test_candidates_follow_resolved_wav_and_flac_audio(self):
        for suffix in (".wav", ".flac"):
            request = prepare_score_repository_request(
                score_file="", audio_path=f"music/track{suffix}",
                explicit=ResolvedPathState("", None, None),
            )
            self.assertTrue(request.json_sidecar_path.endswith("track.score.json"))
            self.assertTrue(request.midi_sidecar_path.endswith("track.mid"))

    def test_provider_order_and_constant_identity(self):
        request = prepare_score_repository_request(
            score_file="", audio_path=None, explicit=ResolvedPathState("", None, None)
        )
        captured = {}
        resolution = ScoreResolution(None, "constant", None, (), {}, request.dependency_fingerprint)
        def chain(providers, **kwargs):
            captured["kinds"] = tuple(provider.kind for provider in providers)
            return resolution
        with patch("score.runtime.resolve_provider_chain", side_effect=chain):
            result = resolve_score_repository(request, audio_seconds_at_tick_zero=0.0)
        self.assertEqual(captured["kinds"], ("explicit", "json_sidecar", "midi_sidecar", "analysis", "constant"))
        self.assertEqual((result.score_format, result.score_provider), ("constant", "constant"))

    def test_error_category_and_single_safe_diagnostic(self):
        request = prepare_score_repository_request(
            score_file="x", audio_path=None, explicit=ResolvedPathState("x", None, "bad")
        )
        error = ProviderChainError("explicit", ("explicit_path_unresolved: cannot resolve",), {"path": "secret"})
        with patch("score.runtime.resolve_provider_chain", side_effect=error), self.assertRaises(ScoreRepositoryError) as captured:
            resolve_score_repository(request, audio_seconds_at_tick_zero=0.0)
        self.assertEqual(captured.exception.category, "not_found")
        self.assertEqual(captured.exception.diagnostic.code, "score_provider_error")
        self.assertNotIn("secret", captured.exception.diagnostic.message)

    def test_warning_and_resolver_order_and_typed_alignment(self):
        resolution = ScoreResolution(None, "constant", 1.0, (
            "score_source_identity_mismatch: edited", "ordinary warning"
        ), {}, ())
        request = prepare_score_repository_request(
            score_file="", audio_path=None, explicit=ResolvedPathState("", None, None)
        )
        with patch("score.runtime.resolve_provider_chain", return_value=resolution):
            result = resolve_score_repository(request, audio_seconds_at_tick_zero=0.0)
        final = append_resolver_diagnostics(result, resolver_diagnostics=("resolver",), effective_alignment=0.0)
        self.assertEqual([item.code for item in final.diagnostics], [
            "score_provider_error", "score_resolver_diagnostic",
            "score_resolver_diagnostic", "score_alignment_divergence",
        ])

    def test_diagnostic_normalization_bound_and_json_order(self):
        value = diagnostic("code", "warning", "  one\n" + "x" * 300)
        self.assertEqual(len(value.message), 200)
        encoded = serialize_diagnostics((value,))
        self.assertEqual(tuple(json.loads(encoded)[0]), ("code", "severity", "message"))
        self.assertNotIn(" ", encoded.split('"message":', 1)[0])
        self.assertEqual(serialize_diagnostics(()), "[]")


if __name__ == "__main__":
    unittest.main()
