import ast
import dataclasses
import pathlib
import subprocess
import sys
import typing
import unittest

from score.model import (
    Marker,
    MeterEvent,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    ResolvedScore,
    Score,
    ScoreFormat,
    Section,
    TempoEvent,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ScoreModelTests(unittest.TestCase):
    def test_public_literal_values(self):
        self.assertEqual(
            typing.get_args(ScoreFormat),
            ("json", "midi", "analyzed", "constant"),
        )
        self.assertEqual(
            typing.get_args(ProviderKind),
            ("explicit", "json_sidecar", "midi_sidecar", "analysis", "constant"),
        )
        self.assertEqual(
            typing.get_args(ProviderStatus),
            ("not_applicable", "found", "invalid"),
        )

    def test_exact_field_order_and_defaults(self):
        expected = {
            TempoEvent: [("tick", dataclasses.MISSING), ("us_per_quarter", dataclasses.MISSING)],
            MeterEvent: [
                ("tick", dataclasses.MISSING),
                ("numerator", dataclasses.MISSING),
                ("denominator", dataclasses.MISSING),
            ],
            Marker: [("tick", dataclasses.MISSING), ("name", dataclasses.MISSING)],
            Section: [
                ("name", dataclasses.MISSING),
                ("start_tick", dataclasses.MISSING),
                ("end_tick_exclusive", dataclasses.MISSING),
                ("bar_aligned", dataclasses.MISSING),
                ("confidence", None),
            ],
            Score: [
                ("ticks_per_quarter", dataclasses.MISSING),
                ("tempos", dataclasses.MISSING),
                ("meters", dataclasses.MISSING),
                ("markers", dataclasses.MISSING),
                ("sections", dataclasses.MISSING),
                ("source", dataclasses.MISSING),
                ("meter_estimated", False),
                ("has_variable_meter", False),
                ("has_midbar_meter_change", False),
            ],
            ResolvedScore: [
                ("score", dataclasses.MISSING),
                ("audio_seconds_at_tick_zero", dataclasses.MISSING),
                ("provider", dataclasses.MISSING),
            ],
            ProviderResult: [
                ("status", dataclasses.MISSING),
                ("score", dataclasses.MISSING),
                ("diagnostics", dataclasses.MISSING),
                ("provenance", dataclasses.MISSING),
            ],
        }
        for cls, fields in expected.items():
            with self.subTest(cls=cls.__name__):
                self.assertEqual(
                    [(field.name, field.default) for field in dataclasses.fields(cls)],
                    fields,
                )

    def test_all_public_dataclasses_are_frozen(self):
        values = [
            TempoEvent(0, 500_000),
            MeterEvent(0, 4, 4),
            Marker(0, "A"),
            Section("A", 0, 1, True),
            Score(480, (), (), (), (), "midi"),
            ResolvedScore(Score(480, (), (), (), (), "midi"), 0.0, "explicit"),
            ProviderResult("not_applicable", None, (), {}),
        ]
        for value in values:
            with self.subTest(cls=type(value).__name__):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(value, dataclasses.fields(value)[0].name, None)

    def test_value_contracts_have_no_behavior_methods(self):
        for cls in (ResolvedScore, ProviderResult):
            public = {name for name in cls.__dict__ if not name.startswith("_")}
            self.assertEqual(public, set())

    def test_model_imports_no_score_sibling(self):
        tree = ast.parse((ROOT / "score" / "model.py").read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        self.assertFalse(any(name == "score" or name.startswith("score.") for name in imports))
        self.assertEqual(set(imports), {"dataclasses", "typing"})

    def test_model_contains_no_parser_resolver_serialization_or_io(self):
        tree = ast.parse((ROOT / "score" / "model.py").read_text(encoding="utf-8"))
        functions = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        calls = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertEqual(functions, [])
        self.assertFalse({"open", "print", "parse_midi", "resolve", "serialize"} & set(calls))

    def test_package_initializer_is_exactly_zero_bytes(self):
        self.assertEqual((ROOT / "score" / "__init__.py").read_bytes(), b"")

    def test_explicit_import_has_no_repository_or_comfy_side_effect(self):
        code = """
import sys
import score.model
forbidden = [name for name in sys.modules if name == '__init__' or name.startswith(('comfy', 'server', 'waveform_routes'))]
assert forbidden == [], forbidden
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
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
