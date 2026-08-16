from __future__ import annotations

import copy
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4_independent"))
import replay_real_model_release as verifier  # noqa: E402


class LocalVerifierNumericContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = verifier.local_contract()

    def test_observed_macos_python_31211_four_ulp_fixture_passes(self) -> None:
        fixtures = (
            (-0.3853463719541539, -0.38534637195415367),
            (0.02098171210825526, 0.020981712108255272),
        )
        for linux_value, macos_value in fixtures:
            result = verifier.numeric_comparison(linux_value, macos_value, contract=self.contract)
            self.assertTrue(result["passed"])
            self.assertEqual(result["ulp_distance"], 4)

    def test_exact_eight_ulp_boundary_passes_and_nine_ulp_fails(self) -> None:
        base = 1.0
        eight, nine = base, base
        for _ in range(8):
            eight = math.nextafter(eight, math.inf)
        for _ in range(9):
            nine = math.nextafter(nine, math.inf)
        self.assertTrue(verifier.numeric_comparison(base, eight, contract=self.contract)["passed"])
        self.assertFalse(verifier.numeric_comparison(base, nine, contract=self.contract)["passed"])
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(base, nine, "model.zones.0.coefficients.F04", contract=self.contract)

    def test_non_finite_and_unlisted_paths_fail_closed(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ValueError, "FAIL_NON_FINITE"):
                verifier.numeric_comparison(value, 1.0, contract=self.contract)
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_MISMATCH"):
            verifier.compare_value(1.0, math.nextafter(1.0, 2.0), "model.training_count", contract=self.contract)

    def test_contract_release_copy_uses_canonical_json_not_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pretty = Path(raw) / "pretty.json"
            compact = Path(raw) / "compact.json"
            pretty.write_text(json.dumps(self.contract, indent=2) + "\n", encoding="utf-8")
            compact.write_bytes(verifier.canon(self.contract))
            self.assertNotEqual(pretty.read_bytes(), compact.read_bytes())
            self.assertTrue(verifier.same_json_document(pretty, compact))
            changed = copy.deepcopy(self.contract)
            changed["numeric_bounds"]["max_ulps"] += 1
            compact.write_bytes(verifier.canon(changed))
            self.assertFalse(verifier.same_json_document(pretty, compact))


class LocalVerifierIntegrityTests(unittest.TestCase):
    release = ROOT / "artifacts/phase-4/P4-P4E2-20260815-r04"

    def test_missing_and_tampered_final_closure_fail(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / self.release.name
            shutil.copytree(self.release, copied)
            verifier._validate_final_closure(copied)
            closure = copied / "acceptance/final-closure.json"
            original = closure.read_bytes()
            closure.unlink()
            with self.assertRaises((FileNotFoundError, ValueError)):
                verifier._validate_final_closure(copied)
            closure.write_bytes(original)
            value = json.loads(closure.read_text())
            value["manifest_sha256"] = "0" * 64
            closure.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
            with self.assertRaisesRegex(ValueError, "HOLD_FINAL_CLOSURE_MISMATCH"):
                verifier._validate_final_closure(copied)

    def test_top1000_order_tie_identity_and_lineage_are_exact(self) -> None:
        top_path = next((self.release / "forecasts/ssq").glob("*/top1000.jsonl"))
        rows = [json.loads(line) for line in top_path.read_text().splitlines()]
        verifier._compare_top(rows, copy.deepcopy(rows), "top1000")
        reordered = copy.deepcopy(rows)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        with self.assertRaisesRegex(ValueError, "HOLD_TIE_IDENTITY|HOLD_TOP1000_ORDER|HOLD_REPLAY_MISMATCH"):
            verifier._compare_top(reordered, rows, "top1000")
        changed_tie = copy.deepcopy(rows)
        changed_tie[0]["score_identity"] = changed_tie[1]["score_identity"]
        with self.assertRaisesRegex(ValueError, "HOLD_TIE_IDENTITY"):
            verifier._compare_top(changed_tie, rows, "top1000")
        changed_lineage = copy.deepcopy(rows)
        changed_lineage[0]["lineage"]["model_release_id"] += "-mutated"
        with self.assertRaisesRegex(ValueError, "lineage"):
            verifier._compare_top(changed_lineage, rows, "top1000")

    def test_local_entry_point_contains_no_vps_only_path(self) -> None:
        for relative in ("scripts/phase4/local-accept-release", "scripts/phase4/local_accept_release.py"):
            text = (ROOT / relative).read_text()
            self.assertNotIn("/home/", text)
            self.assertNotIn("/usr/bin/python", text)
            self.assertNotIn("acceptance-venv", text)
        finalizer = (ROOT / "scripts/phase4/finalize_real_model_release.py").read_text()
        checklist_template = finalizer[finalizer.index("checklist = f\"\"\""):finalizer.index("checklist_path =", finalizer.index("checklist = f\"\"\""))]
        self.assertNotIn("/home/", checklist_template)
        self.assertNotIn("/usr/bin/", checklist_template)
        self.assertNotIn("acceptance-venv", checklist_template)


if __name__ == "__main__":
    unittest.main()
