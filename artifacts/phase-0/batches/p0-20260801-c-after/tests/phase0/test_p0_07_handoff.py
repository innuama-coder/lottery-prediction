from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
ARTIFACTS = REPO / "artifacts" / "phase-0"
sys.path.insert(0, str(SCRIPTS))

from p0_07_decision import GATE_IDS, derive_per_game_outcome  # noqa: E402
from p0_07_handoff import (  # noqa: E402
    DECISION_EVIDENCE_REF, PREVIOUS_REFS, build_handoff_fixture, consume_stage1_fixture,
    finalize_handoff_fixed_point, project_handoff_pass, validate_fixed_point, validate_handoff_fixture,
)
from phase0lib import ValidationError, load_json  # noqa: E402


class P007HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_json(REPO / "docs" / "roadmap" / "phase-0-acceptance-contract.json")
        cls.fixture_schema = load_json(ARTIFACTS / "schemas" / "stage1-handoff-fixture.schema.json")
        cls.receipt_schema = load_json(ARTIFACTS / "schemas" / "p0-07-stage1-consumer-receipt.schema.json")
        cls.evidence = [{"evidence_id": "ev-dlt", "game": "dlt"}, {"evidence_id": "ev-ssq", "game": "ssq"}]

    @staticmethod
    def base_result(game: str, tier: str, *, stop: bool = False):
        gates = []
        for gate_id in GATE_IDS:
            passed = gate_id not in {"G-HANDOFF"} and not (gate_id == "G-COVERAGE" and tier == "none")
            remediation = "not_applicable" if passed else (
                "alternatives_exhausted_no_evidentiary_path" if stop and gate_id == "G-COVERAGE"
                else "concrete_compliant_action_available"
            )
            gates.append({
                "gate_id": gate_id, "outcome": "PASS" if passed else "FAIL",
                "remediation_status": remediation, "reason_code": "test_fact",
                "evidence_refs": ["fact:test"], "reason": "Deterministic test gate fact.",
            })
        return {
            "game": game, "coverage_tier": tier, "gate_results": gates,
            "per_game_outcome": derive_per_game_outcome(gates, tier),
        }

    @classmethod
    def refs(cls):
        return {
            PREVIOUS_REFS[0]: None, PREVIOUS_REFS[1]: None, PREVIOUS_REFS[2]: None,
            DECISION_EVIDENCE_REF: None, "ev-dlt": {"dlt"}, "ev-ssq": {"ssq"},
        }

    def finalize(self, base, reconciliation=None, refs=None):
        return finalize_handoff_fixed_point(
            base, reconciliation=[] if reconciliation is None else reconciliation, evidence=self.evidence,
            contract=self.contract, fixture_schema=self.fixture_schema, receipt_schema=self.receipt_schema,
            available_refs=self.refs() if refs is None else refs,
        )

    def test_active_empty_is_explicit_and_zero_records_use_none_tier(self) -> None:
        base = [self.base_result("dlt", "none"), self.base_result("ssq", "none")]
        results, fixture, receipt = self.finalize(base)
        self.assertEqual(fixture["active_games"], [])
        self.assertEqual(fixture["excluded_games"], ["dlt", "ssq"])
        self.assertEqual([item["corroboration_tier"] for item in fixture["game_results"]], ["none", "none"])
        self.assertTrue(all(sum(item["count"] for item in game["corroboration_counts"]) == 0 for game in fixture["game_results"]))
        self.assertEqual(receipt["active_games"], [])
        self.assertEqual(receipt, consume_stage1_fixture(
            fixture, fixture_schema=self.fixture_schema, receipt_schema=self.receipt_schema, available_refs=self.refs(),
        ))
        self.assertEqual([item["per_game_outcome"] for item in results], ["HOLD", "HOLD"])

    def test_mixed_pass_handoff_and_accepted_tier_are_mechanical(self) -> None:
        base = [self.base_result("dlt", "target"), self.base_result("ssq", "none")]
        reconciliation = [{
            "game": "dlt", "primary_evidence_ref": "ev-dlt", "corroborating_evidence_refs": [],
            "corroboration_tier": "primary_only", "core_fact_match": True,
            "resolution_status": "primary_only", "resolved_record_ref": "derived/normalized/dlt.json",
        }]
        results, fixture, _receipt = self.finalize(base, reconciliation)
        self.assertEqual(fixture["project_decision"], "LIMITED_GO")
        self.assertEqual(fixture["active_games"], ["dlt"])
        self.assertEqual(fixture["excluded_games"], ["ssq"])
        dlt = fixture["game_results"][0]
        self.assertEqual(dlt["corroboration_tier"], "primary_only")
        self.assertEqual(dlt["evidence_ref"], [DECISION_EVIDENCE_REF, "ev-dlt"])
        self.assertEqual(results[0]["per_game_outcome"], "PASS_FULL")

    def test_handoff_semantic_tampering_is_rejected(self) -> None:
        base = [self.base_result("dlt", "none"), self.base_result("ssq", "none")]
        projected = project_handoff_pass(base, contract=self.contract)
        fixture = build_handoff_fixture(
            projected, reconciliation=[], evidence=self.evidence, contract=self.contract, schema=self.fixture_schema,
        )

        def clone():
            return json.loads(json.dumps(fixture))

        tier = clone(); tier["game_results"][0]["corroboration_tier"] = "primary_only"
        count = clone(); count["game_results"][0]["corroboration_counts"][0]["count"] = 1
        partition = clone(); partition["active_games"] = ["dlt"]
        outcome = clone(); outcome["game_results"][0]["per_game_outcome"] = "PASS_FULL"
        decision = clone(); decision["project_decision"] = "GO"
        for value, expected in (
            (tier, "tier/count/ref/outcome"), (count, "tier/count/ref/outcome"),
            (partition, "active/excluded"), (outcome, "tier/count/ref/outcome"),
            (decision, "project decision"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValidationError, expected):
                    validate_handoff_fixture(
                        value, projected_game_results=projected, reconciliation=[], evidence=self.evidence,
                        contract=self.contract, schema=self.fixture_schema,
                    )

    def test_reconciliation_cross_game_and_dangling_refs_fail(self) -> None:
        projected = project_handoff_pass(
            [self.base_result("dlt", "target"), self.base_result("ssq", "none")], contract=self.contract,
        )
        template = {
            "game": "dlt", "corroborating_evidence_refs": [], "corroboration_tier": "primary_only",
            "core_fact_match": True, "resolution_status": "primary_only", "resolved_record_ref": "derived/a.json",
        }
        for reference, expected in (("ev-ssq", "cross-game"), ("missing", "dangling")):
            row = {**template, "primary_evidence_ref": reference}
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValidationError, expected):
                    build_handoff_fixture(
                        projected, reconciliation=[row], evidence=self.evidence,
                        contract=self.contract, schema=self.fixture_schema,
                    )

    def test_consumer_rejects_extra_fields_dangling_cross_game_and_future_refs(self) -> None:
        projected = project_handoff_pass(
            [self.base_result("dlt", "none"), self.base_result("ssq", "none")], contract=self.contract,
        )
        fixture = build_handoff_fixture(
            projected, reconciliation=[], evidence=self.evidence, contract=self.contract, schema=self.fixture_schema,
        )
        extra = json.loads(json.dumps(fixture)); extra["hidden_transform"] = True
        with self.assertRaisesRegex(ValidationError, "unknown property"):
            consume_stage1_fixture(extra, fixture_schema=self.fixture_schema, receipt_schema=self.receipt_schema, available_refs=self.refs())
        dangling_refs = self.refs(); del dangling_refs[DECISION_EVIDENCE_REF]
        with self.assertRaisesRegex(ValidationError, "cannot resolve"):
            consume_stage1_fixture(fixture, fixture_schema=self.fixture_schema, receipt_schema=self.receipt_schema, available_refs=dangling_refs)
        future = json.loads(json.dumps(fixture)); future["game_results"][0]["evidence_ref"].append("artifacts/phase-0/replay-report.json")
        future_refs = self.refs(); future_refs["artifacts/phase-0/replay-report.json"] = None
        with self.assertRaisesRegex(ValidationError, "future/terminal dependency"):
            consume_stage1_fixture(future, fixture_schema=self.fixture_schema, receipt_schema=self.receipt_schema, available_refs=future_refs)
        cross = json.loads(json.dumps(fixture)); cross["game_results"][0]["evidence_ref"].append("ev-ssq")
        with self.assertRaisesRegex(ValidationError, "cross-game"):
            consume_stage1_fixture(cross, fixture_schema=self.fixture_schema, receipt_schema=self.receipt_schema, available_refs=self.refs())

    def test_fixed_point_drift_fails_closed(self) -> None:
        base = [self.base_result("dlt", "target"), self.base_result("ssq", "none")]
        projected = project_handoff_pass(base, contract=self.contract)
        fixture = build_handoff_fixture(
            projected, reconciliation=[], evidence=self.evidence, contract=self.contract, schema=self.fixture_schema,
        )
        drifted_fixture = json.loads(json.dumps(fixture)); drifted_fixture["excluded_games"] = []
        with self.assertRaisesRegex(ValidationError, "changed the handoff fixture"):
            validate_fixed_point(fixture, drifted_fixture, projected, projected)
        drifted_results = json.loads(json.dumps(projected)); drifted_results[0]["per_game_outcome"] = "HOLD"
        with self.assertRaisesRegex(ValidationError, "changed per-game"):
            validate_fixed_point(fixture, fixture, projected, drifted_results)


if __name__ == "__main__":
    unittest.main()
