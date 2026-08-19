from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class Phase4E5EvidenceTests(unittest.TestCase):
    def test_independent_roles_exclude_p4e4_and_original_200(self) -> None:
        roles = json.loads((ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json").read_text())
        for game in ("ssq", "dlt"):
            role = roles["games"][game]
            self.assertEqual(role["report"]["row_count"], 120)
            self.assertTrue(role["report"]["independent_of_p4e4_report_and_original_200"])
            self.assertLess(int(role["report"]["last_issue"]), 2024128 if game == "ssq" else 2024126)

    def test_selection_was_sealed_without_report_access(self) -> None:
        for game in ("ssq", "dlt"):
            receipt = json.loads((ROOT / f"artifacts/phase-4e5/selection/{game}-selection-receipt.json").read_text())
            self.assertFalse(receipt["report_labels_read"])
            self.assertEqual(receipt["report_row_count_read"], 0)
            self.assertFalse(receipt["original_200_labels_read"])

    def test_shadow_outputs_and_normalization_proofs(self) -> None:
        for game in ("ssq", "dlt"):
            top1000 = (ROOT / f"artifacts/phase-4e5/delivery/top1000/{game}-top1000-shadow.jsonl").read_text().splitlines()
            top10 = (ROOT / f"artifacts/phase-4e5/delivery/top10-shadow/{game}-top10-shadow.jsonl").read_text().splitlines()
            self.assertEqual(len(top1000), 1000)
            self.assertEqual(len(top10), 10)
            rows = [json.loads(line) for line in top1000]
            self.assertEqual([row["rank"] for row in rows], list(range(1, 1001)))
            self.assertTrue(all(left["joint_probability"] >= right["joint_probability"] for left, right in zip(rows, rows[1:])))
            proof = json.loads((ROOT / f"artifacts/phase-4e5/delivery/normalization/{game}-normalization-proof.json").read_text())
            self.assertLessEqual(proof["absolute_normalization_error"], 1e-12)
            self.assertEqual(proof["probability_spread_adjustment"], "none")

    def test_prior_release_bytes_are_unchanged(self) -> None:
        inventory = json.loads((ROOT / "artifacts/phase-4e5/delivery/inventory/prior-release-byte-inventory.json").read_text())
        self.assertEqual(set(inventory), {"r12", "p4e3", "p4e4"})
        self.assertTrue(all(item["unchanged_from_base"] for item in inventory.values()))
        closure = json.loads((ROOT / "artifacts/phase-4e4/acceptance-20260819/final-closure.json").read_text())
        self.assertEqual(closure["terminal_state"], "FEATURE_ENGINEERING_DELIVERED_PROSPECTIVE_ONLY")

    def test_decision_keeps_r12_and_fails_closed(self) -> None:
        decision = json.loads((ROOT / "artifacts/phase-4e5/delivery/decision.json").read_text())
        self.assertEqual(decision["serving_release_unchanged"], "P4-P4E2-20260815-r12")
        self.assertEqual(decision["release_allocation"], "FORBIDDEN")
        self.assertEqual(decision["terminal_state"], "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION")


if __name__ == "__main__":
    unittest.main()
