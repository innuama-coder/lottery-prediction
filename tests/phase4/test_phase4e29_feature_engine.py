from __future__ import annotations
import hashlib
import json
from pathlib import Path
import unittest

from lottery_system.phase4 import features
from lottery_system.phase4.features import per_number, ticket

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/"artifacts/phase4e29_feature_engine"

class Phase4E29FeatureEngineTests(unittest.TestCase):
    def test_registry_has_32_callable_ids(self):
        self.assertEqual(32,len(features.FEATURE_IDS))
        self.assertEqual(32,len(set(features.FEATURE_IDS)))
        for feature_id in features.FEATURE_IDS:
            self.assertTrue(callable(getattr(features,feature_id)))

    def test_prefix_invariance_and_determinism(self):
        prefix=[{1,2},{2,3},{1,4}]
        expected=per_number.rolling_rate(prefix,5)
        full=prefix+[{1,5}]
        self.assertEqual(expected,per_number.rolling_rate(full[:3],5))
        self.assertEqual(expected,per_number.rolling_rate(prefix,5))

    def test_waiting_time_censoring(self):
        values,censored=per_number.waiting_time([{1},{2},set()],3)
        self.assertEqual({1:2,2:1,3:3},values)
        self.assertEqual({1:False,2:False,3:True},censored)

    def test_ticket_hand_calculations(self):
        c=[1,2,4,7,31]
        self.assertEqual(2,ticket.previous_overlap(c,[2,7,9]))
        self.assertEqual(3,ticket.odd_count(c)); self.assertEqual(45,ticket.number_sum(c))
        self.assertEqual(30,ticket.number_range(c)); self.assertEqual(1,ticket.adjacent_pairs(c))
        self.assertEqual([1,2,3,24],ticket.gap_vector(c))
        self.assertEqual([4,0,1],ticket.band_counts(c,33))
        self.assertEqual(5,ticket.birthday_count(c))
        self.assertEqual(1,ticket.arithmetic_pattern([1,2,3])["subset_count"])
        self.assertAlmostEqual(1.44,ticket.recent_win_overlap([1,2],[{1},{2}],decay=.8))
        self.assertAlmostEqual(2/(10**.5),ticket.winner_count_residual(12,1000,.01))

    def test_snapshot_and_manifest_hashes(self):
        manifest=json.loads((OUT/"manifest.json").read_text())
        for relative,digest in manifest["inputs"].items():
            self.assertEqual(digest,hashlib.sha256((ROOT/relative).read_bytes()).hexdigest())
        for game in ("ssq","dlt"):
            rows=[json.loads(x) for x in (OUT/f"{game}-feature-snapshot.jsonl").read_text().splitlines()]
            self.assertTrue(rows); self.assertTrue(all(x["game"]==game for x in rows))

if __name__=="__main__": unittest.main()
