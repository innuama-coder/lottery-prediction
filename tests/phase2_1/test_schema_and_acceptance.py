from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase2_1.schema import validate
from lottery_research.phase2_1.serialization import canonical_json_bytes, sha256
from lottery_research.phase2_1.workflow import (
    _scientific_classification,
    project_root,
    verify_evidence_manifest,
)


ROOT = project_root()


class SchemaAndAcceptanceTests(unittest.TestCase):
    def test_contract_schema_rejects_a_resource_threshold(self) -> None:
        contract = json.loads((ROOT / "docs/roadmap/phase-2.1-acceptance-contract.json").read_text(encoding="utf-8"))
        contract["resource_policy"]["generic_thresholds"] = ["memory"]
        with self.assertRaisesRegex(ValueError, "generic_thresholds"):
            validate("contract", contract)

    def test_dedicated_result_schema_rejects_missing_status(self) -> None:
        fixture = {
            "schema_version": "2.1.0", "artifact_type": "phase2_1_qualification", "release_id": "x", "gate": "G2",
            "generator_known_answers": [], "strong_positive_results": [], "metrics": {}, "input_identities": [],
        }
        with self.assertRaises(ValueError):
            validate("qualification", fixture)

    def test_recursive_manifest_detects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            evidence = root / "evidence.json"
            evidence.write_bytes(b"original")
            rows = [{"path": "evidence.json", "sha256": sha256(evidence)}]
            manifest = {"artifact_type": "phase2_1_recursive_evidence_manifest", "release_id": __import__("lottery_research.phase2_1", fromlist=["RELEASE_ID"]).RELEASE_ID, "file_count": 1, "files": rows, "inventory_sha256": __import__("hashlib").sha256(canonical_json_bytes(rows)).hexdigest()}
            self.assertEqual(verify_evidence_manifest(root, manifest), 1.0)
            evidence.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "closure"):
                verify_evidence_manifest(root, manifest)

    def test_recursive_manifest_rejects_unregistered_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            evidence = root / "evidence.json"
            evidence.write_bytes(b"original")
            rows = [{"path": "evidence.json", "sha256": sha256(evidence)}]
            manifest = {"artifact_type": "phase2_1_recursive_evidence_manifest", "release_id": __import__("lottery_research.phase2_1", fromlist=["RELEASE_ID"]).RELEASE_ID, "file_count": 1, "files": rows, "inventory_sha256": __import__("hashlib").sha256(canonical_json_bytes(rows)).hexdigest()}
            (root / "unregistered.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inventory mismatch"):
                verify_evidence_manifest(root, manifest)

    def test_acceptance_schema_allows_consistent_no_go_but_not_false_go(self) -> None:
        fixture = {
            "schema_version": "2.1.0", "artifact_type": "phase2_1_acceptance", "release_id": __import__("lottery_research.phase2_1", fromlist=["RELEASE_ID"]).RELEASE_ID,
            "status": "FAIL", "delivery_status": "NO-GO", "scientific_classification": "indeterminate", "accepted_at_utc": "2026-08-05T00:00:00Z",
            "gate_verdicts": {**{f"G{i}": "PASS" for i in range(6)}, "G6": "FAIL"},
            "recomputed_metrics": {"evidence_hash_closure": 0.9, "historical_result_coverage": 1.0, "power_grid_coverage": 1.0, "independent_replay_consistency": 1.0, "e2e_expected_terminal_coverage": 1.0, "blocking_findings": 0},
            "blocking_findings": 0, "evidence_inventory": [str(i) for i in range(15)], "recursive_manifest_identity": {}, "limitations": ["indeterminate is not proof of randomness"],
            "input_identity": {"release_id": "x", "baseline_sha": "x", "phase1_frozen": [], "phase2_frozen": [], "task_inputs": {}, "task_input_aggregate_sha256": "x"},
        }
        validate("acceptance", fixture)
        fixture["delivery_status"] = "GO"
        with self.assertRaises(ValueError):
            validate("acceptance", fixture)

    def test_classification_is_recomputed_and_indeterminate_is_not_randomness(self) -> None:
        families = ["marginal_inclusion", "set_structure", "pair_dependence", "slow_drift", "cross_zone_dependence"]
        boundaries = {name: index + 1.0 for index, name in enumerate(families)}
        audit = {"primary_results": []}
        grid = []
        for game in ("dlt", "ssq"):
            for family in families:
                audit["primary_results"].append({"game": game, "family": family, "candidate_eligible": True, "holm_adjusted_p_value": 1.0, "confidence_set": {"hull": [0.0, boundaries[family]]}, "practical_boundary": boundaries[family], "sensitivity_direction_consistency": True})
                grid.append({"game": game, "family": family, "effect": boundaries[family], "sample_size": 200, "simultaneous_95_lower": 0.2})
        prereg = {"practical_boundaries": boundaries, "target_power": 0.8}
        self.assertEqual(_scientific_classification(audit, {"grid": grid}, prereg), "indeterminate")


if __name__ == "__main__":
    unittest.main()
