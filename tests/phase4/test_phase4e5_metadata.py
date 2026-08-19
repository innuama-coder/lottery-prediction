from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from lottery_system.phase4e5.metadata import canonical, parse_dlt_notice, read_captured_body


ROOT = Path(__file__).resolve().parents[2]


class Phase4E5MetadataTests(unittest.TestCase):
    def test_all_raw_request_response_provenance_replays(self) -> None:
        inventory = json.loads((ROOT / "artifacts/phase-4e5/acquisition/raw-inventory.json").read_text())
        self.assertEqual(inventory["request_count"], 489)
        for item in inventory["requests"]:
            directory = ROOT / "artifacts/phase-4e5/acquisition/raw" / item["source_id"]
            body, receipt = read_captured_body(directory)
            self.assertEqual(hashlib.sha256(body).hexdigest(), receipt["response"]["body_sha256"])
            request_body = (directory / "request-body.bin").read_bytes()
            self.assertEqual(hashlib.sha256(request_body).hexdigest(), receipt["request"]["body_sha256"])
            self.assertIn("request_started_at_utc", receipt)
            self.assertIn("response_finished_at_utc", receipt)
            self.assertIsInstance(receipt["response"]["headers"], dict)

    def test_dlt_parser_deterministic_replay(self) -> None:
        directory = ROOT / "artifacts/phase-4e5/acquisition/raw/dlt_official_notice_2024006"
        first = parse_dlt_notice(directory, "2024006")
        second = parse_dlt_notice(directory, "2024006")
        self.assertEqual(canonical(first), canonical(second))
        self.assertEqual(first["sales"], 317_993_801.0)
        self.assertEqual(first["first_prize_count"], 6)
        self.assertEqual(first["second_prize_count"], 90)
        self.assertTrue(first["province_first_prize_distribution"])

    def test_provenance_corruption_is_rejected(self) -> None:
        source = ROOT / "artifacts/phase-4e5/acquisition/raw/dlt_official_notice_2024006"
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            for name in ("receipt.json", "response-body.bin.gz"):
                (target / name).write_bytes((source / name).read_bytes())
            damaged = bytearray((target / "response-body.bin.gz").read_bytes())
            damaged[-1] ^= 1
            (target / "response-body.bin.gz").write_bytes(damaged)
            with self.assertRaises(ValueError):
                read_captured_body(target)

    def test_coverage_audit_fails_closed_without_ssq_substitute(self) -> None:
        audit = json.loads((ROOT / "artifacts/phase-4e5/metadata-audit/coverage-audit.json").read_text())
        self.assertEqual(audit["unofficial_substitution_count"], 0)
        self.assertFalse(audit["games"]["ssq"]["promotion_authority"])
        self.assertEqual(audit["games"]["ssq"]["official_operational_rows"], 0)
        self.assertEqual(audit["games"]["dlt"]["required_operational_coverage_minimum"], 1.0)
        self.assertGreaterEqual(audit["games"]["dlt"]["field_coverage"]["province_first_prize_distribution"], 0.95)


if __name__ == "__main__":
    unittest.main()
