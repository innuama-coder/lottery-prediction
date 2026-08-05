from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "phase0_multisource"
sys.path.insert(0, str(SCRIPT))

from phase0_multisource import (  # noqa: E402
    Phase0Error, collect_request, evaluate_corroboration, normalize_issue, parse_eastmoney,
    parse_gdlottery, parse_ydniu, year_contiguous,
)


PROVENANCE = {
    "url": "https://example.test/history", "raw_ref": "raw/example.html",
    "raw_sha256": "0" * 64, "captured_at_utc": "2026-08-02T00:00:00Z",
}


class Phase0MultisourceTests(unittest.TestCase):
    def test_issue_normalization(self) -> None:
        self.assertEqual(normalize_issue("dlt", "26083"), "2026083")
        self.assertEqual(normalize_issue("dlt", "2026083"), "2026083")
        self.assertEqual(normalize_issue("ssq", "2026087"), "2026087")
        with self.assertRaises(Phase0Error):
            normalize_issue("ssq", "26087")

    def test_ydniu_parser_extracts_both_games(self) -> None:
        ssq = b'''<table><tbody><tr><td>2026087</td><td>2026-07-30 Thursday</td><td class="open_number"><i class="hq">04</i><i class="hq">06</i><i class="hq">10</i><i class="hq">18</i><i class="hq">23</i><i class="hq">31</i><i class="lq">11</i></td></tr></tbody></table>'''
        dlt = b'''<table><tbody><tr><td>2026086</td><td>2026-08-01 Saturday</td><td class="open_number"><i class="lq">10</i><i class="lq">11</i><i class="lq">18</i><i class="lq">22</i><i class="lq">35</i><i class="yq">06</i><i class="yq">12</i></td></tr></tbody></table>'''
        self.assertEqual(parse_ydniu(ssq, "ssq", PROVENANCE)[0]["back_numbers"], [11])
        self.assertEqual(parse_ydniu(dlt, "dlt", PROVENANCE)[0]["front_numbers"], [10, 11, 18, 22, 35])

    def test_eastmoney_parser_extracts_and_normalizes_dlt(self) -> None:
        body = b'''<table><tr><td><a href="/Result/Category/dlt?type=dlt&id=26083">26083</a></td><td>2026-07-25(Sat)</td><td><span class="pellet red">14</span><span class="pellet red">15</span><span class="pellet red">16</span><span class="pellet red">23</span><span class="pellet red">26</span><span class="pellet blue">07</span><span class="pellet blue">09</span></td></tr></table>'''
        record = parse_eastmoney(body, "dlt", PROVENANCE)[0]
        self.assertEqual(record["issue_id"], "2026083")
        self.assertEqual(record["back_numbers"], [7, 9])

    def test_invalid_ball_counts_fail_closed(self) -> None:
        body = b'''<tr><td>2026087</td><td>2026-07-30 X</td><td class="open_number"><i class="hq">04</i><i class="lq">11</i></td></tr>'''
        with self.assertRaises(Phase0Error):
            parse_ydniu(body, "ssq", PROVENANCE)

    def test_gdlottery_official_announcement_parser(self) -> None:
        body = '''<h2>第26026期开奖公告</h2><p>开奖日期：2026年3月14日</p><li>本期开奖号码：</li><li>10 11 22 26 32</li><li>01 08</li>'''.encode("utf-8")
        record = parse_gdlottery(body, "dlt", PROVENANCE)[0]
        self.assertEqual(record["issue_id"], "2026026")
        self.assertEqual(record["draw_date"], "2026-03-14")
        self.assertEqual(record["front_numbers"], [10, 11, 22, 26, 32])

    def test_failure_is_durably_recorded(self) -> None:
        def fail(_url: str, _timeout: float):
            raise TimeoutError("injected timeout")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = collect_request(
                snapshot_dir=root, spec={"source_id": "test", "game": "ssq", "page": "1", "url": "https://example.test"},
                event_path=root / "events.jsonl", timeout=1, fetcher=fail,
            )
            events = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual([item["event"] for item in events], ["request_started", "request_failed"])
        self.assertEqual(events[-1]["error_type"], "TimeoutError")

    def test_continuity_detects_missing_issue(self) -> None:
        good = [
            {"issue_id": "2026003", "draw_date": "2026-01-07"},
            {"issue_id": "2026002", "draw_date": "2026-01-05"},
            {"issue_id": "2026001", "draw_date": "2026-01-03"},
            {"issue_id": "2025153", "draw_date": "2025-12-31"},
        ]
        self.assertTrue(year_contiguous(good)[0])
        bad = [good[0], good[2]]
        self.assertFalse(year_contiguous(bad)[0])

    def test_one_match_and_one_dissent_is_a_blocking_conflict(self) -> None:
        primary = {"core_fact_sha256": "a" * 64}
        observations = {
            "matching_portal": {"core_fact_sha256": "a" * 64},
            "dissenting_portal": {"core_fact_sha256": "b" * 64},
        }
        status, matching, dissenting = evaluate_corroboration(primary, observations)
        self.assertEqual(status, "conflict")
        self.assertEqual(set(matching), {"matching_portal"})
        self.assertEqual(set(dissenting), {"dissenting_portal"})


if __name__ == "__main__":
    unittest.main()
