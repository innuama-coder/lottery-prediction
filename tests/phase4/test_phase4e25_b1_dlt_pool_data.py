from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

from lottery_system.phase4.parimutuel import DLT_PRIZE_PARIMUTUEL_v1, expected_ticket_value


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/phase4e25_b1_dlt_pool_data/run_collect_and_ev.py"
SPEC = importlib.util.spec_from_file_location("phase4e25_b1", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


FIXTURE = """<!doctype html><html><head><meta charset="UTF-8"></head><body>
<h2>第26070期开奖公告</h2><p>开奖日期：2026年6月24日</p>
<p>本期全国销售金额：312,722,588元</p>
<div>本期开奖号码：<br>04 05 15 21 32<br>02 11</div>
<h3>本期中奖情况</h3><table>
<tr><th>奖级</th><th>中奖注数</th><th>单注奖金</th><th>应派奖金合计</th></tr>
<tr><td>一等奖<br>基本<br>追加</td><td>1注<br>0注</td><td>10,000,000元<br>---</td><td>10,000,000元<br>0元</td></tr>
<tr><td>二等奖<br>基本<br>追加</td><td>112注<br>26注</td><td>142,147元<br>113,717元</td><td>15,920,464元<br>2,956,642元</td></tr>
<tr><td>三等奖</td><td>956注</td><td>5,000元</td><td>4,780,000元</td></tr>
<tr><td>四等奖</td><td>15,321注</td><td>300元</td><td>4,596,300元</td></tr>
<tr><td>五等奖</td><td>61,695注</td><td>150元</td><td>9,254,250元</td></tr>
<tr><td>六等奖</td><td>701,558注</td><td>15元</td><td>10,523,370元</td></tr>
<tr><td>七等奖</td><td>7,655,003注</td><td>5元</td><td>38,275,015元</td></tr>
<tr><td>合计</td><td>---</td><td>---</td><td>96,306,041元</td></tr>
</table><p>814,894,461.66元奖金滚入下期奖池。</p></body></html>""".encode()


class DltPoolDataTests(unittest.TestCase):
    def test_offline_fixture_parser(self):
        draw = MODULE.parse_draw_html(
            FIXTURE,
            url="https://www.gdlottery.cn/f_html/kjgg/P085_26070.html",
            fetched_at_utc="2026-06-24T14:00:00Z",
        )
        self.assertEqual(draw["issue_id"], "26070")
        self.assertEqual(draw["draw_date_local"], "2026-06-24")
        self.assertEqual(draw["front_numbers"], [4, 5, 15, 21, 32])
        self.assertEqual(draw["back_numbers"], [2, 11])
        self.assertEqual(draw["national_sales_yuan"], 312_722_588)
        self.assertEqual(draw["pool_rollover_yuan"], 814_894_461)
        first = next(row for row in draw["tiers"] if row["tier"] == "一等奖基本")
        additional = next(row for row in draw["tiers"] if row["tier"] == "二等奖追加")
        self.assertEqual(first, {"tier": "一等奖基本", "winners": 1, "prize_per_ticket_yuan": 10_000_000})
        self.assertEqual(additional["prize_per_ticket_yuan"], 113_717)
        self.assertEqual(len(draw["provenance"]["raw_sha256"]), 64)

    def test_observed_ev_hand_calculation(self):
        draw = {
            "national_sales_yuan": 20,
            "tiers": [
                {"tier": "一等奖基本", "winners": 1, "prize_per_ticket_yuan": 5},
                {"tier": "二等奖追加", "winners": 2, "prize_per_ticket_yuan": 3},
            ],
        }
        # (1*5 + 2*3) / (20/2) = 1.1 yuan per base ticket.
        self.assertAlmostEqual(MODULE.observed_ev(draw), 1.1)

    def test_b0_ev_is_monotonic_in_tier1_pool(self):
        values = []
        for pool in (0, 100_000_000, 200_000_000):
            values.append(expected_ticket_value(
                "dlt", DLT_PRIZE_PARIMUTUEL_v1,
                tier1_pool=pool, tier2_pool=20_000_000,
                total_bets=150_000_000, popularity_weight=1.0,
            )["total_ev"])
        self.assertLess(values[0], values[1])
        self.assertLess(values[1], values[2])


if __name__ == "__main__":
    unittest.main()
