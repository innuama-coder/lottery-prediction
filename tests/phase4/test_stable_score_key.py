from __future__ import annotations

import json
import math
import sys
import unittest
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from lottery_system.phase4.p4e2_model import (
    SCORE_ORDER_QUANTUM,
    score_identity,
    score_order_key,
    score_order_tick,
    tie_group_id_for_score,
    tie_key_for_score,
)
from lottery_system.phase4 import p4e2_model


ROOT = Path(__file__).resolve().parents[2]
R10 = ROOT / "artifacts/phase-4/P4-P4E2-20260815-r10"
sys.path.insert(0, str(ROOT / "scripts/phase4_independent"))
import p4e2_oracle as oracle  # noqa: E402


def ticket(row: dict[str, object]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return tuple(row["front_numbers"]), tuple(row["back_numbers"])


class StableScoreKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scopes: list[tuple[str, Path]] = []
        for game in ("ssq", "dlt"):
            cls.scopes.extend([
                (f"formal/{game}", next((R10 / f"forecasts/{game}").glob("*/top1000.jsonl"))),
                (f"historical/{game}", R10 / f"runtime/lifecycle/{game}/historical-cycle-v1/top1000.jsonl"),
                (f"shadow/{game}", R10 / f"research/{game}/shadow-top1000.jsonl"),
            ])

    def test_every_preserved_r10_row_is_one_ulp_stable_and_order_preserving(self) -> None:
        global_minimum = math.inf
        for scope, path in self.scopes:
            with self.subTest(scope=scope):
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                scores = [float(row["log_joint_score"]) for row in rows]
                keys = [score_order_key(score) for score in scores]
                self.assertEqual(len(rows), 1000)
                self.assertEqual(len(set(keys)), 1000)
                for score, key in zip(scores, keys):
                    self.assertEqual(score_order_key(math.nextafter(score, -math.inf)), key)
                    self.assertEqual(score_order_key(math.nextafter(score, math.inf)), key)
                    self.assertEqual(oracle.score_order_key(score), key)
                    self.assertEqual(oracle.score_identity(score), score_identity(score))
                for left, right in zip(scores, scores[1:]):
                    if left != right:
                        global_minimum = min(global_minimum, abs(left - right))
                        self.assertNotEqual(score_order_key(left), score_order_key(right))
                stable_order = sorted(
                    range(len(rows)),
                    key=lambda index: (-score_order_tick(scores[index]), ticket(rows[index])),
                )
                self.assertEqual(stable_order, list(range(1000)))
                self.assertEqual([row["rank"] for row in rows], list(range(1, 1001)))
                self.assertEqual(len({ticket(row) for row in rows}), 1000)
        self.assertEqual(format(global_minimum, ".18e"), "4.326295779955025012e-10")

    def test_controller_one_ulp_fixtures_match_product_and_independent_keys(self) -> None:
        fixture = json.loads((ROOT / "tests/phase4/fixtures/stable-score-key-macos-31211.json").read_text())
        self.assertEqual(fixture["quantum"], format(SCORE_ORDER_QUANTUM, "f"))
        for row in fixture["examples"]:
            with self.subTest(game=row["game"], rank=row["rank_index"]):
                release_score = float(row["release_score"])
                controller_score = float(row["controller_score"])
                self.assertEqual(release_score.hex(), row["release_hex"])
                self.assertEqual(controller_score.hex(), row["controller_hex"])
                self.assertEqual(score_order_key(release_score), score_order_key(controller_score))
                self.assertEqual(score_identity(release_score), score_identity(controller_score))
                self.assertEqual(oracle.score_order_key(release_score), score_order_key(release_score))
                self.assertEqual(oracle.score_order_key(controller_score), score_order_key(controller_score))
                self.assertEqual(oracle.score_identity(release_score), score_identity(release_score))
                self.assertEqual(oracle.score_identity(controller_score), score_identity(controller_score))

    def test_first_key_boundary_changes_every_derived_identity(self) -> None:
        tick = score_order_tick(1.0)
        boundary = float((Decimal(tick) + Decimal("0.5")) * SCORE_ORDER_QUANTUM)
        below = math.nextafter(boundary, -math.inf)
        above = math.nextafter(boundary, math.inf)
        self.assertEqual(score_order_tick(below), tick)
        self.assertEqual(score_order_tick(above), tick + 1)
        self.assertNotEqual(score_order_key(below), score_order_key(above))
        self.assertNotEqual(score_identity(below), score_identity(above))
        self.assertNotEqual(tie_key_for_score(below), tie_key_for_score(above))
        self.assertNotEqual(tie_group_id_for_score(below), tie_group_id_for_score(above))
        self.assertEqual(oracle.score_order_key(below), score_order_key(below))
        self.assertEqual(oracle.score_order_key(above), score_order_key(above))

    def test_integer_rational_rounding_matches_decimal_half_even_reference(self) -> None:
        values = (-100.0, -1.00000000015, -0.0, 0.0, 0.00000000005, 1.00000000005, 99.99999999995)
        for value in values:
            with self.subTest(value=value):
                expected = int((Decimal.from_float(value) / SCORE_ORDER_QUANTUM).to_integral_value(rounding=ROUND_HALF_EVEN))
                self.assertEqual(score_order_tick(value), expected)
                self.assertEqual(oracle.score_order_tick(value), expected)

    def test_same_key_shares_tie_identity_and_different_keys_do_not(self) -> None:
        score = 0.13767089985115685
        neighbor = math.nextafter(score, -math.inf)
        self.assertEqual(tie_key_for_score(score), tie_key_for_score(neighbor))
        self.assertEqual(tie_group_id_for_score(score), tie_group_id_for_score(neighbor))
        separated = score + float(SCORE_ORDER_QUANTUM) * 2
        self.assertNotEqual(score_order_key(score), score_order_key(separated))
        self.assertNotEqual(tie_key_for_score(score), tie_key_for_score(separated))
        self.assertNotEqual(tie_group_id_for_score(score), tie_group_id_for_score(separated))

    def test_ranking_uses_stable_key_then_canonical_ticket_not_raw_score(self) -> None:
        zones = [
            {"rows": [(1.00000000002, (2,)), (1.00000000001, (1,))]},
            {"rows": [(0.0, (1,))]},
        ]
        product_rows = p4e2_model._top(zones, 2)
        independent_rows = oracle._top(zones, 2)
        self.assertEqual([row[1] for row in product_rows], [(1,), (2,)])
        self.assertEqual(product_rows, independent_rows)

    def test_non_finite_scores_fail_closed_in_both_implementations(self) -> None:
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    score_order_key(value)
                with self.assertRaises(ValueError):
                    oracle.score_order_key(value)


if __name__ == "__main__":
    unittest.main()
