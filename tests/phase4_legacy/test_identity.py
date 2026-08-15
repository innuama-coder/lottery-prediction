from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from lottery_system.phase4.identity import content_id, validate_stable_id, verify_content_id
from lottery_system.phase4.serialization import (
    DuplicateKeyError,
    canonical_json_bytes,
    load_json,
)
from lottery_system.phase4.storage import SecurityBoundaryError, resolve_inside, validate_runtime_root


ROOT = Path(__file__).resolve().parents[2]


class CanonicalIdentityTests(unittest.TestCase):
    def test_p4_cjson_is_compact_sorted_utf8_and_has_no_newline(self) -> None:
        payload = {"z": [Decimal("1.2300"), "\u4e2d\u6587"], "a": 1}
        encoded = canonical_json_bytes(payload)
        self.assertEqual(encoded, '{"a":1,"z":["1.23","\u4e2d\u6587"]}'.encode("utf-8"))
        self.assertFalse(encoded.endswith(b"\n"))

    def test_p4_cjson_rejects_float_nonfinite_and_unsupported_values(self) -> None:
        for value in (0.1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                canonical_json_bytes({"probability": value})
        with self.assertRaises(TypeError):
            canonical_json_bytes({"tuple": (1, 2)})

    def test_loader_rejects_duplicate_keys_and_can_reject_float_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "value.json"
            path.write_text('{"a":1,"a":2}', encoding="utf-8")
            with self.assertRaises(DuplicateKeyError):
                load_json(path)
            path.write_text('{"a":0.1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json(path, reject_floats=True)

    def test_content_identity_excludes_only_registered_derived_fields(self) -> None:
        body = {"game": "ssq", "value": 3}
        identity = content_id("forecast", body)
        stored = {**body, "forecast_id": identity}
        verify_content_id(identity, "forecast", stored, excluded_fields=("forecast_id",))
        with self.assertRaises(ValueError):
            verify_content_id(identity, "forecast", {**stored, "value": 4}, excluded_fields=("forecast_id",))

    def test_explicit_id_and_safe_root_reject_latest_glob_and_escape(self) -> None:
        for value in ("latest", "x/latest", "x*", "../x", "x/y"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_stable_id(value)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaises(SecurityBoundaryError):
                resolve_inside(root, "../escape")
        runtime = ROOT / "artifacts/phase-4-runtime/p4-runtime-test-unit"
        self.assertEqual(validate_runtime_root(ROOT, runtime), runtime.resolve())
        with self.assertRaises(SecurityBoundaryError):
            validate_runtime_root(ROOT, ROOT / "artifacts/phase-1")


if __name__ == "__main__":
    unittest.main()
