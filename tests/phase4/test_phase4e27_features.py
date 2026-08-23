import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FEATURES_PATH = ROOT / "artifacts" / "phase4e27" / "features.json"


class Phase4E27FeaturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with FEATURES_PATH.open(encoding="utf-8") as stream:
            cls.features = json.load(stream)

    def test_catalog_is_nonempty(self):
        self.assertIsInstance(self.features, list)
        self.assertGreater(len(self.features), 0)

    def test_required_fields_are_present(self):
        required = {
            "feature_id",
            "category",
            "name_zh",
            "name_en",
            "definition",
            "calculation",
            "zone",
            "lotteries",
            "observability",
            "evidence",
            "source",
        }
        for index, feature in enumerate(self.features):
            with self.subTest(index=index, feature_id=feature.get("feature_id")):
                self.assertTrue(required.issubset(feature), required - set(feature))
                for field in required - {"lotteries", "source"}:
                    self.assertIsInstance(feature[field], str)
                    self.assertTrue(feature[field].strip())
                self.assertIsInstance(feature["lotteries"], list)
                self.assertTrue(feature["lotteries"])
                self.assertIsInstance(feature["source"], list)
                self.assertTrue(feature["source"])

    def test_evidence_vocabulary_and_proven_is_forbidden(self):
        allowed = {"documented", "heuristic", "hypothetical"}
        for feature in self.features:
            self.assertIn(feature["evidence"], allowed)
            self.assertNotEqual(feature["evidence"], "proven")

    def test_feature_ids_are_unique(self):
        ids = [feature["feature_id"] for feature in self.features]
        self.assertEqual(len(ids), len(set(ids)))

    def test_at_least_six_categories_are_covered(self):
        categories = {feature["category"] for feature in self.features}
        self.assertGreaterEqual(len(categories), 6)


if __name__ == "__main__":
    unittest.main()
