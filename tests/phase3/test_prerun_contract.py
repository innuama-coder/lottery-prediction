from __future__ import annotations

import unittest
import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lottery_research.phase3.prerun_contract import validate_prerun_contract


ROOT = Path(__file__).resolve().parents[2]


class PreRunContractTests(unittest.TestCase):
    def contract_root(self, raw: str) -> Path:
        root = Path(raw)
        for name in ("artifacts", "schemas", "tasks"):
            os.symlink(ROOT / name, root / name, target_is_directory=True)
        shutil.copytree(ROOT / "config/phase3", root / "config/phase3")
        return root

    def test_frozen_baseline_is_held_when_availability_is_unproven(self) -> None:
        receipt = validate_prerun_contract(ROOT)

        self.assertEqual(receipt["status"], "HOLD")
        self.assertFalse(receipt["formal_run_authorized"])
        self.assertEqual(receipt["metrics"]["input_identity_coverage"], 1.0)
        self.assertEqual(receipt["metrics"]["eligible_feature_coverage"], 0.0)
        self.assertIn("PIT_AVAILABILITY_UNPROVEN", receipt["hold_reasons"])

    def test_missing_required_forbidden_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = self.contract_root(raw)
            path = root / "config/phase3/data-time-contract.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["forbidden_fields"].remove("future_draw_result")
            path.write_text(json.dumps(payload), encoding="utf-8")
            prereg_path = root / "config/phase3/preregistration.json"
            prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
            prereg["data_time_contract_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            prereg_path.write_text(json.dumps(prereg), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "forbidden field coverage"):
                validate_prerun_contract(root)

    def test_validator_emits_json_and_nonzero_exit_for_hold(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/phase3/validate_prerun_contract.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["status"], "HOLD")
        self.assertFalse(receipt["formal_run_authorized"])


if __name__ == "__main__":
    unittest.main()
