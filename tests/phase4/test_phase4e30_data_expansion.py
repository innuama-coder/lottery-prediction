from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/phase4e30_data_expansion/run_collect_dlt.py"
SPEC = importlib.util.spec_from_file_location("phase4e30_collect", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
DRAW_PATH = ROOT / "artifacts/phase4e30_data_expansion/dlt-draws-full.jsonl"
SUMMARY_PATH = ROOT / "artifacts/phase4e30_data_expansion/collection-summary.json"

FIXTURE = """<!doctype html><meta charset="UTF-8"><h2>第26070期开奖公告</h2>
<p>开奖日期：2026年6月24日</p><p>本期全国销售金额：312,722,588元</p>
<p>本期使用第1套摇奖球</p><ul><li>本期出球顺序：</li><li>15 04 05 32 21</li><li>11 02</li>
<li>本期开奖号码：</li><li>04 05 15 21 32</li><li>02 11</li></ul>
<h3>本期中奖情况</h3><ul><li>奖级</li><li>中奖注数</li><li>单注奖金</li><li>应派奖金合计</li></ul>
<ul><li><div>一等奖</div><div>基本</div><div>追加</div></li>
<li>1注</li><li>10,000,000元</li><li>10,000,000元</li><li>0注</li><li>---</li><li>0元</li></ul>
<ul><li><div>二等奖</div><div>基本</div><div>追加</div></li>
<li>112注</li><li>142,147元</li><li>15,920,464元</li><li>26注</li><li>113,717元</li><li>2,956,642元</li></ul>
<ul><li>三等奖</li><li>956注</li><li>5,000元</li><li>4,780,000元</li></ul>
<ul><li>合计</li><li>---</li><li>---</li><li>96,306,041元</li></ul>
<p>814,894,461.66元奖金滚入下期奖池。</p>""".encode()


class DataExpansionTests(unittest.TestCase):
    def test_offline_official_fragment(self):
        draw = MODULE.parse_draw_html(FIXTURE, url="https://www.gdlottery.cn/f_html/kjgg/P085_26070.html", fetched_at_utc="2026-06-24T14:00:00Z")
        self.assertEqual(draw["issue_id"], "26070")
        self.assertEqual(draw["draw_date_local"], "2026-06-24")
        self.assertEqual(draw["front_numbers"], [4, 5, 15, 21, 32])
        self.assertEqual(draw["back_numbers"], [2, 11])
        self.assertEqual(draw["front_draw_order"], [15, 4, 5, 32, 21])
        self.assertEqual(draw["back_draw_order"], [11, 2])
        self.assertEqual(draw["ball_set_id"], 1)
        self.assertEqual(draw["national_sales_yuan"], 312_722_588)
        self.assertEqual(draw["pool_rollover_yuan"], 814_894_461)
        self.assertIn({"tier": "一等奖基本", "winners": 1, "prize_per_ticket_yuan": 10_000_000}, draw["tiers"])
        self.assertIn({"tier": "二等奖追加", "winners": 26, "prize_per_ticket_yuan": 113_717}, draw["tiers"])
        self.assertEqual(len(draw["provenance"]["raw_sha256"]), 64)

    def test_full_dataset_contract(self):
        rows = [json.loads(line) for line in DRAW_PATH.read_text(encoding="utf-8").splitlines()]
        issues = [row["issue_id"] for row in rows]
        self.assertEqual(len(issues), len(set(issues)))
        for row in rows:
            self.assertEqual(row["front_numbers"], sorted(row["front_numbers"]))
            self.assertEqual(row["back_numbers"], sorted(row["back_numbers"]))
            self.assertEqual(len(row["front_numbers"]), 5)
            self.assertEqual(len(row["back_numbers"]), 2)
            self.assertTrue(all(1 <= n <= 35 for n in row["front_numbers"]))
            self.assertTrue(all(1 <= n <= 12 for n in row["back_numbers"]))
            if row["front_draw_order"] is not None:
                self.assertEqual(sorted(row["front_draw_order"]), row["front_numbers"])
                self.assertEqual(sorted(row["back_draw_order"]), row["back_numbers"])
            else:
                self.assertIsNone(row["back_draw_order"])
            provenance = row["provenance"]
            self.assertRegex(provenance["url"], r"^https://www\.gdlottery\.cn/.+P085_\d{5}\.html$")
            self.assertRegex(provenance["fetched_at_utc"], r"^\d{4}-\d{2}-\d{2}T")
            self.assertRegex(provenance["raw_sha256"], r"^[0-9a-f]{64}$")

    def test_summary_matches_dataset(self):
        rows = DRAW_PATH.read_text(encoding="utf-8").splitlines()
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(summary["total_issues"], len(rows))
        self.assertEqual(sum(summary["issues_by_year"].values()), len(rows))


if __name__ == "__main__":
    unittest.main()
