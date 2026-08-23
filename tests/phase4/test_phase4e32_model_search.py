from __future__ import annotations
import json
from pathlib import Path
import unittest
from lottery_system.phase4.model_selection import CandidateConfig,candidate_space,evaluate_candidate,top_k_hits

ROOT=Path(__file__).resolve().parents[2]
class Phase4E32Tests(unittest.TestCase):
    def setUp(self):
        self.draws=[]
        for t in range(36):
            self.draws.append({"draw_date_local":f"2024-01-{1+t%28:02d}","front_numbers":[1,2,3,4,5],
             "back_numbers":[1,2],"national_sales_yuan":100+t,"pool_rollover_yuan":200+t,
             "ball_set_id":None,"front_draw_order":None,"back_draw_order":None})
    def test_candidate_space_exhausted(self):
        self.assertEqual(48,len(candidate_space())); self.assertEqual(48,len(set(candidate_space())))
    def test_prefix_only(self):
        seen=[]; c=CandidateConfig("A","freq-only(3)",1e-3,30)
        evaluate_candidate(self.draws,c,evaluation_window=2,audit_hook=lambda t,h:seen.append((t,h)))
        self.assertTrue(seen); self.assertTrue(all(len(h)==t for t,h in seen))
    def test_top_k_hand_example(self):
        self.assertEqual(2,top_k_hits([.1,.9,.8,.2],[2,4],3))
    def test_deterministic(self):
        c=CandidateConfig("A","freq-only(3)",1e-3,30)
        self.assertEqual(evaluate_candidate(self.draws,c,2),evaluate_candidate(self.draws,c,2))
    def test_summary_warns_best_of_n(self):
        s=json.loads((ROOT/"artifacts/phase4e32_model_search/summary.json").read_text())
        self.assertTrue(s["selection_bias_warning"]["best_of_n_upward_bias"])
        self.assertIn("best-of-N 上偏",s["selection_bias_warning"]["warning"])
if __name__=="__main__": unittest.main()
