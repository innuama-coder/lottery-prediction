from __future__ import annotations

import sys
import unittest
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "phase4_independent"
sys.path.insert(0, str(SCRIPTS))

from check_qualification_feasibility import binomial_cdf_le, binomial_tail_ge, validate_analytic_spec  # noqa: E402
from oracle_mutation_audit import ANALYTIC_FIELDS, _parent_and_key, _tampered  # noqa: E402


class FeasibilityPrimitiveTests(unittest.TestCase):
    def test_binomial_boundaries(self) -> None:
        self.assertEqual(binomial_cdf_le(10, Decimal(0), 0), Decimal(1))
        self.assertEqual(binomial_tail_ge(10, Decimal(1), 10), Decimal(1))
        self.assertEqual(binomial_tail_ge(10, Decimal(0), 1), Decimal(0))

    def test_binomial_complement(self) -> None:
        p = Decimal("0.37")
        left = binomial_cdf_le(20, p, 7)
        right = binomial_tail_ge(20, p, 8)
        self.assertLess(abs(left + right - Decimal(1)), Decimal("1e-70"))

    def test_frozen_uniform_aggregate_bound(self) -> None:
        self.assertGreater(binomial_cdf_le(1000, Decimal("0.018"), 50), Decimal("0.9999999999"))

    def test_all_62_frozen_analytic_delete_tamper_cases_fail_closed(self) -> None:
        spec_path = Path(__file__).resolve().parents[2] / "qualification-design/analytic-feasibility-spec.json"
        spec = json.loads(spec_path.read_text())
        self.assertEqual(len(ANALYTIC_FIELDS), 31)
        rejected = 0
        for field in ANALYTIC_FIELDS:
            for operation in ("delete", "tamper"):
                mutation = deepcopy(spec)
                parent, key = _parent_and_key(mutation, field)
                if operation == "delete":
                    del parent[key]
                else:
                    parent[key] = _tampered(parent[key])
                with self.subTest(field=field, operation=operation), self.assertRaises(ValueError):
                    validate_analytic_spec(mutation)
                rejected += 1
        self.assertEqual(rejected, 62)
        root_extra = deepcopy(spec)
        root_extra["unexpected"] = True
        nested_extra = deepcopy(spec)
        nested_extra["small_space"]["unexpected"] = 1
        for mutation in (root_extra, nested_extra):
            with self.assertRaises(ValueError):
                validate_analytic_spec(mutation)


if __name__ == "__main__":
    unittest.main()
