from __future__ import annotations

import json
import unittest
from pathlib import Path

from lottery_system.phase4e6.sources import parse_00038, parse_17500, parse_official_dlt


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase4e6"


class Phase4E6EvidenceTests(unittest.TestCase):
    def test_real_source_parsers_agree_on_known_dlt(self) -> None:
        archive = parse_17500(BASE / "acquisition/raw/17500_dlt_2024006", "dlt", "2024006")
        mirror = parse_00038(BASE / "acquisition/raw/00038_dlt_2024006", "dlt", "2024006")
        official = parse_official_dlt(ROOT / "artifacts/phase-4e5/acquisition/raw/dlt_official_notice_2024006", "2024006")
        for field in ("issue", "draw_date", "front", "back", "first_prize_count", "first_prize_amount"):
            self.assertEqual(archive[field], official[field]); self.assertEqual(mirror[field], official[field])

    def test_coverage_and_quarantine_are_fail_closed(self) -> None:
        report = json.loads((BASE / "consensus/coverage-report.json").read_text())
        self.assertEqual(report["games"]["dlt"]["accepted_fraction"], 1.0)
        self.assertEqual(report["games"]["ssq"]["accepted_fraction"], 0.0)
        self.assertFalse(report["both_games_coverage_gate_pass"])
        self.assertTrue(report["access_probes"]["500"]["bot_challenge_detected"])
        self.assertFalse(report["access_probes"]["500"]["bypassed"])

    def test_no_untouched_window_and_no_report_access(self) -> None:
        audit = json.loads((BASE / "untouched-window-audit.json").read_text())
        self.assertFalse(audit["valid_both_game_window"]); self.assertFalse(audit["report_labels_opened_by_p4e6"])
        self.assertEqual(audit["report_evaluation_count"], 0)
        self.assertTrue(all(row["untouched_rows"] < row["required_contiguous_report_rows"] for row in audit["games"].values()))

    def test_both_game_shadow_outputs_and_normalization(self) -> None:
        for game in ("ssq", "dlt"):
            top1000 = [json.loads(line) for line in (BASE / f"delivery/top1000/{game}-top1000-shadow.jsonl").read_text().splitlines()]
            top10 = (BASE / f"delivery/top10-shadow/{game}-top10-shadow.jsonl").read_text().splitlines()
            self.assertEqual(len(top1000), 1000); self.assertEqual(len(top10), 10)
            self.assertEqual([row["rank"] for row in top1000], list(range(1, 1001)))
            self.assertTrue(all(a["joint_probability"] >= b["joint_probability"] for a, b in zip(top1000, top1000[1:])))
            proof = json.loads((BASE / f"delivery/normalization/{game}-normalization-proof.json").read_text())
            self.assertEqual(proof["probability_spread_adjustment"], "none")

    def test_r12_retained_and_prior_bytes_unchanged(self) -> None:
        decision = json.loads((BASE / "delivery/decision.json").read_text())
        inventory = json.loads((BASE / "delivery/inventory/prior-byte-inventory.json").read_text())
        self.assertEqual(decision["terminal_status"], "PROSPECTIVE_ONLY")
        self.assertEqual(decision["serving_release"], "P4-P4E2-20260815-r12")
        self.assertFalse(decision["serving_release_changed"]); self.assertTrue(inventory["all_bytes_unchanged"])


if __name__ == "__main__": unittest.main()
