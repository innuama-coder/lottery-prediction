import importlib.util
import pathlib
import unittest

path = pathlib.Path(__file__).parents[2] / "scripts/phase4e19/ssq_prize_aware.py"
spec = importlib.util.spec_from_file_location("ssq_prize_aware", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SSQPrizeAwareTests(unittest.TestCase):
    def test_prize_tiers(self):
        self.assertEqual(module.prize_tier(6, 1), 1)
        self.assertEqual(module.prize_tier(6, 0), 2)
        self.assertEqual(module.prize_tier(5, 1), 3)
        self.assertEqual(module.prize_tier(3, 0), 6)

    def test_fixed_benchmarks(self):
        self.assertEqual(module.ticket_prize([1,2,3,4,5,6], 7, [1,2,3,4,5,6], 7), 5_000_000.0)
        self.assertEqual(module.ticket_prize([1,2,3,4,5,6], 7, [1,2,3,4,5,6], 8), 100_000.0)

    def test_acceptance_gate(self):
        self.assertTrue(module.acceptance_gate([2.1, 2.5])["passed"])
        self.assertFalse(module.acceptance_gate([2.1, 1.9])["passed"])


if __name__ == "__main__":
    unittest.main()
