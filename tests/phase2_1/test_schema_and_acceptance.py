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
            manifest = {"file_count": 1, "files": rows, "inventory_sha256": __import__("hashlib").sha256(canonical_json_bytes(rows)).hexdigest()}
            self.assertEqual(verify_evidence_manifest(root, manifest), 1.0)
            evidence.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "closure"):
                verify_evidence_manifest(root, manifest)

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
