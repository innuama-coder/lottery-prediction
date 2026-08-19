from __future__ import annotations

import json
import unittest
from pathlib import Path

from lottery_system.phase4e4.data import canonical, sha256_bytes, sha256_file
from lottery_system.phase4e4.model import FAMILIES


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "artifacts/phase-4e4/report-20260819"
DELIVERY = ROOT / "artifacts/phase-4e4/delivery-20260819"


class Phase4E4EvidenceTests(unittest.TestCase):
    def test_report_receipts_are_complete_self_hashed_and_fail_closed(self) -> None:
        hypotheses = []
        for game in ("ssq", "dlt"):
            receipt = json.loads((REPORT / f"{game}-report-receipt.json").read_text(encoding="utf-8"))
            recorded = receipt.pop("receipt_sha256")
            self.assertEqual(recorded, sha256_bytes(canonical(receipt)))
            self.assertEqual(receipt["report_draw_count"], 60)
            self.assertEqual(receipt["hypothesis_family_size"], 84)
            self.assertFalse(receipt["report_used_for_selection_or_tuning"])
            self.assertFalse(receipt["post_report_refit_or_reselection"])
            self.assertEqual(receipt["terminal_state"], "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION")
            self.assertEqual(receipt["promoted_candidates"], [])
            self.assertEqual(set(receipt["candidate_gates"]), set(FAMILIES))
            hypotheses.extend(receipt["game_hypotheses"])
        self.assertEqual(len(hypotheses), 84)
        self.assertEqual(len({row["hypothesis_id"] for row in hypotheses}), 84)
        self.assertTrue(all(row["bootstrap"]["iterations"] == 8192 for row in hypotheses))
        self.assertTrue(all(row["bootstrap"]["block_length"] == 6 for row in hypotheses))
        ssq = json.loads((REPORT / "ssq-report-receipt.json").read_text(encoding="utf-8"))
        self.assertFalse(ssq["promotion_authority"])

    def test_delivery_manifest_top1000_and_prior_inventories(self) -> None:
        manifest = json.loads((DELIVERY / "core-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_sha256"], sha256_bytes(canonical(manifest["files"])))
        self.assertEqual(manifest["file_count"], len(manifest["files"]))
        for row in manifest["files"]:
            path = DELIVERY / row["path"]
            self.assertEqual(path.stat().st_size, row["bytes"])
            self.assertEqual(sha256_file(path), row["sha256"])
        decision = json.loads((DELIVERY / "decision.json").read_text(encoding="utf-8"))
        self.assertEqual(decision["terminal_state"], "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION")
        self.assertEqual(decision["serving_release_unchanged"], "P4-P4E2-20260815-r12")
        self.assertFalse(decision["ssq_promotion_authority"])
        for game in ("ssq", "dlt"):
            summary = json.loads((DELIVERY / f"top1000/{game}-summary.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in (DELIVERY / f"top1000/{game}-{summary['candidate_id']}.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1000)
            self.assertEqual([row["rank"] for row in rows], list(range(1, 1001)))
            self.assertEqual(rows, sorted(rows, key=lambda row: (-row["joint_probability"], row["front"], row["back"])))
            self.assertEqual(summary["top1000_sha256"], sha256_file(DELIVERY / f"top1000/{game}-{summary['candidate_id']}.jsonl"))


if __name__ == "__main__":
    unittest.main()
