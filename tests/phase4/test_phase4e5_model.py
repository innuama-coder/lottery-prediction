from __future__ import annotations

import itertools
import math
import unittest

import numpy as np

from lottery_system.phase4e5.model import elementary, inclusion_probabilities, top_zone


class Phase4E5ModelTests(unittest.TestCase):
    def test_exact_fixed_cardinality_normalization(self) -> None:
        weights = np.asarray([0.5, 1.0, 1.5, 2.0, 2.5])
        k = 3
        normalizer = elementary(weights, k)
        mass = math.fsum(math.prod(weights[value - 1] for value in combo) / normalizer for combo in itertools.combinations(range(1, 6), k))
        self.assertLessEqual(abs(mass - 1.0), 1e-15)
        inclusion = inclusion_probabilities(weights, k)
        self.assertLessEqual(abs(float(np.sum(inclusion)) - k), 1e-14)

    def test_top_zone_is_deterministic_and_unadjusted(self) -> None:
        weights = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0])
        first, norm1 = top_zone(weights, 2, 5)
        second, norm2 = top_zone(weights, 2, 5)
        self.assertEqual(first, second)
        self.assertEqual(norm1, norm2)
        self.assertEqual(first[0][0], (4, 5))
        self.assertGreaterEqual(first[0][1], first[-1][1])


if __name__ == "__main__":
    unittest.main()
