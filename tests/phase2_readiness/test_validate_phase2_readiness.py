from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_phase2_readiness.py"
CONTRACT = ROOT / "docs" / "roadmap" / "phase-2-acceptance-contract.json"
READINESS = ROOT / "artifacts" / "phase-2" / "readiness" / "p2-00a-readiness.json"
START_AUTHORIZATION = ROOT / "artifacts" / "phase-2" / "readiness" / "p2-01-start-authorization.json"
INPUT_DRAFT = ROOT / "artifacts" / "phase-2" / "readiness" / "drafts" / "input-manifest.draft.json"

spec = importlib.util.spec_from_file_location("phase2_readiness_validator", SCRIPT)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class Phase2ReadinessValidatorTests(unittest.TestCase):
    def run_validator(self, readiness: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--contract",
                str(CONTRACT),
                "--readiness",
                str(readiness),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_start_authorization_preserves_pre_p2_01_ready_state(self) -> None:
        authorization = json.loads(START_AUTHORIZATION.read_text(encoding="utf-8"))
        self.assertEqual(authorization["validation_exit_code"], validator.READY)
        self.assertEqual(authorization["terminal"], "P2-00A-READY")
        self.assertEqual(authorization["actual"]["formal_D2_path_occupancy_count"], 0)
        result = self.run_validator(READINESS)
        self.assertEqual(result.returncode, validator.HOLD, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["terminal"], "HOLD")
        self.assertEqual(payload["actual"]["generation_rule_join_count"], 400)
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        occupied, historical = validator.formal_path_counts(ROOT, contract)
        self.assertEqual(payload["actual"]["formal_D2_path_occupancy_count"], occupied)
        self.assertEqual(payload["actual"]["formal_historical_result_count"], historical)
        self.assertGreaterEqual(occupied, 4)

    def test_claimed_draft_hash_tamper_is_evidence_mismatch(self) -> None:
        payload = json.loads(READINESS.read_text(encoding="utf-8"))
        payload["draft_path_sha256_inventory"][0]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "readiness.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.run_validator(path)
        self.assertEqual(result.returncode, validator.EVIDENCE_MISMATCH, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["terminal"], "EVIDENCE_MISMATCH")

    def test_invalid_readiness_schema_returns_code_4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text('{"terminal":"P2-00A-READY"}', encoding="utf-8")
            result = self.run_validator(path)
        self.assertEqual(result.returncode, validator.INVALID_CONTRACT, result.stdout + result.stderr)

    def test_rule_overlap_prevents_full_join(self) -> None:
        draft = json.loads(INPUT_DRAFT.read_text(encoding="utf-8"))
        dlt = next(item for item in draft["game_rule_maps"] if item["game"] == "dlt")
        duplicate = copy.deepcopy(dlt["documented_draw_process_segments"][0])
        duplicate["id"] = "injected-overlap"
        dlt["documented_draw_process_segments"].append(duplicate)
        joined, blockers, failures = validator.validate_input_draft(ROOT, draft)
        self.assertEqual(blockers, 0)
        self.assertLess(joined, 400)
        self.assertTrue(any(item.startswith("rule_join_cardinality:dlt:") for item in failures))

    def test_formal_occupancy_tracks_post_start_progress(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        occupied, historical = validator.formal_path_counts(ROOT, contract)
        self.assertGreaterEqual(occupied, 4)
        self.assertIn(historical, (0, 1, 2))
        self.assertLessEqual(historical, occupied)


if __name__ == "__main__":
    unittest.main()
