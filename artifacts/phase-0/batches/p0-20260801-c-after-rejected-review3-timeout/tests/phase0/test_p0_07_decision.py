from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
ARTIFACTS = REPO / "artifacts" / "phase-0"
sys.path.insert(0, str(SCRIPTS))

from p0_07_decision import (  # noqa: E402
    GATE_IDS, build_per_game_gate_results, derive_per_game_outcome,
    derive_project_decision, validate_game_and_project_results, validate_gate_results,
)
from phase0lib import ValidationError, load_json, sha256_file  # noqa: E402


class P007DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_json(REPO / "docs" / "roadmap" / "phase-0-acceptance-contract.json")
        cls.catalog = load_json(ARTIFACTS / "source-catalog.json")
        pre_manifest = ARTIFACTS / "batches" / "p0-20260801-c-pre" / "snapshot-manifest.json"
        after_archive = ARTIFACTS / "batches" / "p0-20260801-c-after"
        archived_draft = after_archive / "artifacts" / "phase-0" / "repair-manifest-p0-20260801-c-draft.json"
        after_manifest = load_json(after_archive / "snapshot-manifest.json")
        draft_record = next(item for item in after_manifest["files"] if item["path"] == "artifacts/phase-0/repair-manifest-p0-20260801-c-draft.json")
        if sha256_file(archived_draft) != draft_record["sha256"]:
            raise AssertionError("C-after archived repair draft hash differs from its immutable manifest")
        cls.repair = load_json(archived_draft)
        if cls.repair["parent_lineage"]["before_snapshot_manifest_sha256"] != sha256_file(pre_manifest):
            raise AssertionError("archived repair draft does not bind the C-pre manifest")

    @staticmethod
    def exact24():
        return [
            {"request_id": f"REQ-{game}-{index:02d}", "game": game}
            for game in ("dlt", "ssq") for index in range(1, 13)
        ]

    @staticmethod
    def candidate(tier: str, *, action: bool, exhausted: bool):
        gate_inputs = {
            "artifact_type": "p0_07_gate_inputs",
            "games": [
                {
                    "game": game, "coverage_tier": tier, "soak_request_count": 12,
                    "unresolved_conflicts": 0,
                    "compliant_corrective_action_available": action,
                    "alternatives_exhausted_no_evidentiary_path": exhausted,
                }
                for game in ("dlt", "ssq")
            ],
        }
        coverage = {
            "artifact_type": "coverage_report",
            "games": [
                {
                    "game": game, "coverage_tier": tier, "rule_boundary": [],
                    "evidence_refs": ["artifacts/phase-0/rule-bundles.json"],
                }
                for game in ("dlt", "ssq")
            ],
        }
        revision = {
            "artifact_type": "revision_report", "append_only_verified": True,
            "history_sha256": "1" * 64, "events": [],
            "synthetic_correction_replay": {
                "reconstructed": True, "before_hash": "2" * 64, "after_hash": "3" * 64,
            },
        }
        return gate_inputs, coverage, revision

    def gates(self, game: str, tier: str, *, action: bool = True, exhausted: bool = False, clean: bool = True, handoff: bool = True):
        gate_inputs, coverage, revision = self.candidate(tier, action=action, exhausted=exhausted)
        return build_per_game_gate_results(
            game, gate_inputs=gate_inputs, coverage=coverage, revision=revision,
            exact24=self.exact24(), source_catalog=self.catalog, contract=self.contract,
            repair_evidence=self.repair, clean_replay_match=clean, handoff_consumer_match=handoff,
        )

    def game_result(self, game: str, tier: str, **kwargs):
        gates = self.gates(game, tier, **kwargs)
        return {
            "game": game, "gate_results": gates, "coverage_tier": tier,
            "per_game_outcome": derive_per_game_outcome(gates, tier),
        }

    def test_four_per_game_outcomes_follow_contract_order(self) -> None:
        cases = (
            ("target", {"action": True}, "PASS_FULL"),
            ("minimum_viable", {"action": True}, "PASS_LIMITED"),
            ("none", {"action": True}, "HOLD"),
            ("none", {"action": False, "exhausted": True}, "STOP"),
        )
        for tier, kwargs, expected in cases:
            with self.subTest(expected=expected):
                gates = self.gates("dlt", tier, **kwargs)
                self.assertEqual(tuple(item["gate_id"] for item in gates), GATE_IDS)
                self.assertEqual(derive_per_game_outcome(gates, tier), expected)
                coverage_gate = next(item for item in gates if item["gate_id"] == "G-COVERAGE")
                self.assertEqual(coverage_gate["outcome"], "PASS" if tier != "none" else "FAIL")

    def test_project_aggregation_covers_all_four_decisions(self) -> None:
        full_dlt = self.game_result("dlt", "target")
        full_ssq = self.game_result("ssq", "target")
        limited_ssq = self.game_result("ssq", "minimum_viable")
        hold_dlt = self.game_result("dlt", "none")
        hold_ssq = self.game_result("ssq", "none")
        stop_dlt = self.game_result("dlt", "none", action=False, exhausted=True)
        stop_ssq = self.game_result("ssq", "none", action=False, exhausted=True)
        self.assertEqual(derive_project_decision([full_dlt, full_ssq]), "GO")
        self.assertEqual(derive_project_decision([full_dlt, limited_ssq]), "LIMITED_GO")
        self.assertEqual(derive_project_decision([hold_dlt, stop_ssq]), "HOLD")
        self.assertEqual(derive_project_decision([stop_dlt, stop_ssq]), "STOP")
        validate_game_and_project_results([hold_dlt, hold_ssq], "HOLD", contract=self.contract)

    def test_reproducibility_and_handoff_require_explicit_facts(self) -> None:
        for field in ("clean", "handoff"):
            kwargs = {field: False}
            gates = self.gates("dlt", "target", **kwargs)
            failed = [item for item in gates if item["outcome"] == "FAIL"]
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0]["remediation_status"], "concrete_compliant_action_available")
            self.assertEqual(derive_per_game_outcome(gates, "target"), "HOLD")

    def test_gate_and_decision_tampering_is_rejected(self) -> None:
        gates = self.gates("dlt", "target")
        with self.assertRaisesRegex(ValidationError, "fourteen contract gates"):
            validate_gate_results(gates[:-1], contract=self.contract)
        duplicated = json.loads(json.dumps(gates)); duplicated[-1] = json.loads(json.dumps(duplicated[0]))
        with self.assertRaisesRegex(ValidationError, "fourteen contract gates"):
            validate_gate_results(duplicated, contract=self.contract)
        invalid_remediation = json.loads(json.dumps(gates))
        invalid_remediation[0]["outcome"] = "FAIL"
        invalid_remediation[0]["remediation_status"] = "not_applicable"
        with self.assertRaisesRegex(ValidationError, "controlled remediation"):
            validate_gate_results(invalid_remediation, contract=self.contract)
        results = [self.game_result("dlt", "target"), self.game_result("ssq", "target")]
        results[0]["per_game_outcome"] = "HOLD"
        with self.assertRaisesRegex(ValidationError, "per-game outcome differs"):
            validate_game_and_project_results(results, "GO", contract=self.contract)
        results[0]["per_game_outcome"] = "PASS_FULL"
        with self.assertRaisesRegex(ValidationError, "project decision differs"):
            validate_game_and_project_results(results, "HOLD", contract=self.contract)

    def test_failed_gate_without_controlled_remediation_is_rejected_at_build(self) -> None:
        with self.assertRaisesRegex(ValidationError, "no controlled remediation classification"):
            self.gates("dlt", "none", action=False, exhausted=False)


if __name__ == "__main__":
    unittest.main()
