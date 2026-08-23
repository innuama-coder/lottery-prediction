import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FEATURES_PATH = ROOT / "artifacts" / "phase4e27" / "features.json"
FEASIBILITY_PATH = ROOT / "artifacts" / "phase4e28" / "feature-feasibility.json"


class Phase4E28FeasibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with FEATURES_PATH.open(encoding="utf-8") as stream:
            cls.features = json.load(stream)
        with FEASIBILITY_PATH.open(encoding="utf-8") as stream:
            cls.assessments = json.load(stream)

    def test_all_40_features_are_covered_exactly_once(self):
        expected = {item["feature_id"] for item in self.features}
        actual = [item["feature_id"] for item in self.assessments]
        self.assertEqual(len(self.features), 40)
        self.assertEqual(len(actual), 40)
        self.assertEqual(len(actual), len(set(actual)))
        self.assertEqual(set(actual), expected)

    def test_feasibility_values_are_legal(self):
        allowed = {
            "computable_from_current",
            "collectable_feasible",
            "not_feasible",
        }
        for item in self.assessments:
            with self.subTest(feature_id=item.get("feature_id")):
                self.assertIn(item.get("feasibility"), allowed)

    def test_every_assessment_has_a_reason(self):
        for item in self.assessments:
            with self.subTest(feature_id=item.get("feature_id")):
                self.assertIsInstance(item.get("reason"), str)
                self.assertTrue(item["reason"].strip())

    def test_collectable_items_have_source_and_method(self):
        for item in self.assessments:
            if item["feasibility"] == "collectable_feasible":
                with self.subTest(feature_id=item["feature_id"]):
                    self.assertIsInstance(item.get("source"), str)
                    self.assertTrue(item["source"].strip())
                    self.assertIsInstance(item.get("collection_method"), str)
                    self.assertTrue(item["collection_method"].strip())

    def test_not_feasible_items_have_a_hard_reason(self):
        for item in self.assessments:
            if item["feasibility"] == "not_feasible":
                with self.subTest(feature_id=item["feature_id"]):
                    self.assertIsInstance(item.get("reason"), str)
                    self.assertTrue(item["reason"].strip())


if __name__ == "__main__":
    unittest.main()
