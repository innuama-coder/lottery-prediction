from __future__ import annotations

import unittest

from lottery_research.phase2_1.workflow import project_root
from lottery_research.phase2_1.serialization import sha256


ROOT = project_root()


class HistoricalProtectionTests(unittest.TestCase):
    def test_phase1_frozen_input_identity_is_unchanged(self) -> None:
        self.assertEqual(sha256(ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"), "f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1")

    def test_historical_phase2_results_are_unchanged(self) -> None:
        self.assertEqual(sha256(ROOT / "artifacts/phase-2/results/historical-audit.json"), "952213369d1236b8c8298de7f42fed513f2e3abc94b34660d1f0832c877af075")
        self.assertEqual(sha256(ROOT / "artifacts/phase-2/results/power-envelope.json"), "f53b1bd17341cf3e9ded0d664ef328d7422dd6148cb14478e21ba30026ead3d4")


if __name__ == "__main__":
    unittest.main()
