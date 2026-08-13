from __future__ import annotations

import copy
import unittest
from decimal import Decimal
from pathlib import Path

from lottery_system.phase4.commands.score import _validate_metric_oracle
from lottery_system.phase4.metrics import MetricViolation, derive_score_id, inclusion_probabilities, score_zones
from lottery_system.phase4.probability import ZoneDistribution, zone_distribution
from lottery_system.phase4.ranking import rank_bands, zone_histogram
from lottery_system.phase4.windows import (
    WindowViolation,
    _build_window_metric,
    build_oracle_fixture_window_metric,
    canonical_window_anchor,
    reliability_summary,
    wilson_95,
)


ROOT = Path(__file__).resolve().parents[2]
ORACLE = ROOT / "artifacts/phase-4-prep/p4-prep-phase4-mvp-20260813-r01-i01/work-items/T10/attempts/T10-I01/known-answers"


def _hist(front: ZoneDistribution, back: ZoneDistribution) -> dict[int, int]:
    result: dict[int, int] = {}
    for left, left_count in zone_histogram(front.ticks, front.k).items():
        for right, right_count in zone_histogram(back.ticks, back.k).items():
            result[left + right] = result.get(left + right, 0) + left_count * right_count
    return result


def _package(ordinal: int, *, config: str = "config-a", front_ticks=(0, 1024, 0, -1024, 512)):
    front = zone_distribution(front_ticks, 2)
    back = zone_distribution((0, -512, 512, 0), 1)
    champion_front = zone_distribution((0, 0, 0, 0, 0), 2)
    champion_back = zone_distribution((0, 0, 0, 0), 1)
    tickets = []
    for a in range(1, 6):
        for b in range(a + 1, 6):
            for c in range(1, 5):
                tickets.append((front.score((a, b)) + back.score((c,)), (a, b), (c,)))
    tickets.sort(key=lambda row: (-row[0], row[1] + row[2]))
    rows = [{"front": list(a), "back": list(b), "display_position": i}
            for i, (_, a, b) in enumerate(tickets, 1)]
    result = rows[(ordinal - 1) % len(rows)]
    return score_zones(
        model_front=front, model_back=back, champion_front=champion_front, champion_back=champion_back,
        result_front=result["front"], result_back=result["back"], histogram=_hist(front, back), ordered_tickets=rows,
        forecast_id=f"forecast-{ordinal:02d}", result_revision_id=f"revision-{ordinal:02d}",
        metric_contract_id="phase4-metric-v1", comparator_forecast_id=f"champion-{ordinal:02d}",
        context={"game": "ssq", "issue_id": f"issue-{ordinal:02d}", "model_id": "P4E1",
                 "model_release_id": "release-a", "config_id": config, "comparator_champion_id": "M0"},
    )


def _anchor(packages, window_id):
    return canonical_window_anchor(
        packages=packages,
        window_id=window_id,
        anchor_source_sha256="8073aa54c1d1fa8d06ff1fc56e9c2fa1c625744cd3d81e17b9575906f8157803",
    )


def _window(packages, window_id):
    return build_oracle_fixture_window_metric(
        packages=packages, game="ssq", model_id="P4E1", comparator_champion_id="M0",
        model_release_id="release-a", window_id=window_id, metric_contract_id="phase4-metric-v1",
    )


class MetricsTest(unittest.TestCase):
    def test_decimal80_known_answers(self):
        summary = _validate_metric_oracle(ORACLE)
        self.assertEqual(summary["observations"], 30)

    def test_fixed_cardinality_inclusion_dp(self):
        zone = zone_distribution((0, 1024, 0, -1024, 512), 2)
        values = inclusion_probabilities(zone)
        expected = __import__("json").loads((ORACLE / "small-space-metrics.json").read_text())["inclusion_probabilities"]["front"]
        self.assertEqual([str(value) for value in values], expected)
        self.assertLess(abs(sum(values) - Decimal(2)), Decimal("1e-25"))

    def test_exact_ties_and_score_identity(self):
        package = _package(1)
        score = package["score"]
        self.assertEqual(score["score_id"], derive_score_id(score["forecast_id"], score["result_revision_id"], score["metric_contract_id"]))
        self.assertLessEqual(score["tie_rank_lower"], Decimal(score["tie_midrank"]))
        self.assertLessEqual(Decimal(score["tie_midrank"]), score["tie_rank_upper"])

    def test_wrong_comparator_rule_rejected(self):
        front = zone_distribution((0, 0, 0), 1)
        back = zone_distribution((0, 0), 1)
        with self.assertRaisesRegex(MetricViolation, "Champion comparator rule"):
            score_zones(model_front=front, model_back=back,
                        champion_front=zone_distribution((0, 0, 0, 0), 1), champion_back=back,
                        result_front=[1], result_back=[1], histogram={0: 6},
                        ordered_tickets=[{"front": [1], "back": [1], "display_position": 1}],
                        forecast_id="f", result_revision_id="r", metric_contract_id="phase4-metric-v1",
                        comparator_forecast_id="c", context={"game": "ssq", "issue_id": "i", "model_id": "m",
                        "model_release_id": "mr", "config_id": "cfg", "comparator_champion_id": "M0"})

    def test_non_m0_same_rule_comparator_rejected(self):
        model = zone_distribution((0, 1), 1)
        with self.assertRaisesRegex(MetricViolation, "permanent M0"):
            score_zones(model_front=model, model_back=model, champion_front=model, champion_back=model,
                        result_front=[2], result_back=[2], histogram={0: 1, 1: 2, 2: 1},
                        ordered_tickets=[{"front": [2], "back": [2], "display_position": 1}],
                        forecast_id="f", result_revision_id="r", metric_contract_id="phase4-metric-v1", comparator_forecast_id="c",
                        context={"game": "ssq", "issue_id": "i", "model_id": "m", "model_release_id": "mr", "config_id": "cfg", "comparator_champion_id": "M0"})

    def test_zero_probability_distribution_rejected(self):
        invalid = ZoneDistribution((0, 0), 1, (Decimal(0), Decimal(0)), Decimal(0))
        champion = zone_distribution((0, 0), 1)
        with self.assertRaisesRegex(MetricViolation, "zero"):
            score_zones(model_front=invalid, model_back=champion, champion_front=champion, champion_back=champion,
                        result_front=[1], result_back=[1], histogram={0: 4},
                        ordered_tickets=[{"front": [1], "back": [1], "display_position": 1}], forecast_id="f",
                        result_revision_id="r", metric_contract_id="phase4-metric-v1", comparator_forecast_id="c",
                        context={"game": "ssq", "issue_id": "i", "model_id": "m", "model_release_id": "mr", "config_id": "cfg", "comparator_champion_id": "M0"})

    def test_duplicate_ticket_and_bad_histogram_rejected(self):
        package = _package(1)
        with self.assertRaisesRegex(MetricViolation, "duplicate"):
            model = zone_distribution((0, 0), 1)
            score_zones(model_front=model, model_back=model, champion_front=model, champion_back=model,
                        result_front=[1], result_back=[1], histogram={0: 4},
                        ordered_tickets=[{"front": [1], "back": [1], "display_position": 1}, {"front": [1], "back": [1], "display_position": 2}],
                        forecast_id="f", result_revision_id="r", metric_contract_id="phase4-metric-v1", comparator_forecast_id="c",
                        context={"game": "ssq", "issue_id": "i", "model_id": "m", "model_release_id": "mr", "config_id": "cfg", "comparator_champion_id": "M0"})

    def test_window_n29_has_no_numeric_values(self):
        packages = [_package(i) for i in range(1, 30)]
        window = _window(packages, "w29")
        self.assertEqual(window["aggregate_state"], "insufficient_observation")
        self.assertIsNone(window["values"])

    def test_window_n30_bins_wilson_and_stability(self):
        packages = [_package(i) for i in range(1, 31)]
        window = _window(packages, "w30")
        self.assertEqual(window["aggregate_state"], "available")
        self.assertEqual(len(window["values"]["reliability"]), 10)
        self.assertEqual(window["values"]["reliability"][-1]["upper"], "1")
        low, high = wilson_95(0, 30)
        self.assertLessEqual(abs(low), Decimal("1e-79"))
        self.assertGreater(high, 0)

    def test_window_duplicate_issue_and_tamper_rejected(self):
        rows = [_package(i) for i in range(1, 3)]
        rows[1]["detail"]["issue_id"] = rows[0]["detail"]["issue_id"]
        with self.assertRaises(WindowViolation):
            _window(rows, "bad")
        bad = _package(1)
        bad["score"]["score_id"] = "tampered"
        with self.assertRaises(WindowViolation):
            _window([bad], "bad2")
        nonfinite = _package(1)
        nonfinite["score"]["joint_log_score"] = "NaN"
        with self.assertRaises(WindowViolation):
            _window([nonfinite], "bad3")

    def test_config_change_breaks_stability_adjacency(self):
        rows = [_package(i, config="a" if i < 16 else "b") for i in range(1, 31)]
        window = _window(rows, "stable")
        self.assertGreaterEqual(Decimal(window["values"]["stability"]), 0)

    def test_reliability_bin_boundaries_are_left_closed_and_one_is_last(self):
        bins, _ = reliability_summary([(Decimal(index) / Decimal(10), index % 2) for index in range(11)])
        self.assertEqual([cell["count"] for cell in bins], [1] * 9 + [2])
        self.assertEqual(bins[1]["mean_predicted_probability"], "0.1")
        self.assertEqual(bins[9]["upper"], "1")
        with self.assertRaises(WindowViolation):
            reliability_summary([(Decimal("1.01"), 1)])

    def test_independent_23_case_score_window_matrix_rejects(self):
        def rejected(mutator, *, argument_mutator=None):
            rows = [_package(1), _package(2)]
            anchor = _anchor(rows, "matrix")
            mutator(rows, anchor)
            arguments = {"packages": rows, "game": "ssq", "model_id": "P4E1", "comparator_champion_id": "M0",
                         "model_release_id": "release-a", "window_id": "matrix", "metric_contract_id": "phase4-metric-v1",
                         "canonical_anchor": anchor, "allow_oracle_fixture": True}
            if argument_mutator:
                argument_mutator(arguments)
            with self.assertRaises((WindowViolation, ValueError, TypeError)):
                _build_window_metric(**arguments)

        mutations = {
            "negative-joint-log-score": lambda r, c: r[0]["score"].__setitem__("joint_log_score", "-1"),
            "finite-joint-log-score-tamper": lambda r, c: r[0]["score"].__setitem__("joint_log_score", "1"),
            "finite-skill-tamper": lambda r, c: r[0]["score"].__setitem__("skill_vs_champion", "1"),
            "brier-above-one": lambda r, c: r[0]["score"].__setitem__("inclusion_brier", "1.1"),
            "rank-band-and-midrank-coordinated-tamper": lambda r, c: (r[0]["score"].__setitem__("tie_rank_lower", 2), r[0]["score"].__setitem__("tie_rank_upper", 2), r[0]["score"].__setitem__("tie_midrank", "2")),
            "rank-upper-exceeds-known-space": lambda r, c: (r[0]["score"].__setitem__("tie_rank_upper", 999), r[0]["score"].__setitem__("tie_midrank", "500")),
            "midrank-percentile-inconsistent": lambda r, c: r[0]["score"].__setitem__("midrank_percentile", "0.5"),
            "comparator-forecast-id-tamper": lambda r, c: (r[0]["score"].__setitem__("comparator_forecast_id", "tampered-m0"), r[0]["detail"]["recomputation"].__setitem__("comparator_forecast_id", "tampered-m0")),
            "forecast-id-and-score-id-coordinated-tamper": lambda r, c: (r[0]["score"].__setitem__("forecast_id", "tampered-forecast"), r[0]["score"].__setitem__("score_id", derive_score_id("tampered-forecast", r[0]["score"]["result_revision_id"], "phase4-metric-v1")), r[0]["detail"].__setitem__("score_id", r[0]["score"]["score_id"]), r[0]["detail"]["recomputation"].__setitem__("forecast_id", "tampered-forecast")),
            "revision-id-and-score-id-coordinated-tamper": lambda r, c: (r[0]["score"].__setitem__("result_revision_id", "tampered-revision"), r[0]["score"].__setitem__("score_id", derive_score_id(r[0]["score"]["forecast_id"], "tampered-revision", "phase4-metric-v1")), r[0]["detail"].__setitem__("score_id", r[0]["score"]["score_id"]), r[0]["detail"]["recomputation"].__setitem__("result_revision_id", "tampered-revision")),
            "nonmonotone-hit-at-k": lambda r, c: r[0]["score"].__setitem__("hit_at_k", {"10": 1, "100": 0, "200": 1, "1000": 1}),
            "front-inclusion-wrong-length-with-adjacency-skipped": lambda r, c: r[0]["detail"].__setitem__("front_inclusion", r[0]["detail"]["front_inclusion"][:-1]),
            "front-inclusion-wrong-cardinality-sum": lambda r, c: r[0]["detail"]["front_inclusion"].__setitem__(0, "0"),
            "front-observed-wrong-length": lambda r, c: r[0]["detail"].__setitem__("observed_front", r[0]["detail"]["observed_front"][:-1]),
            "front-observed-out-of-rule-range": lambda r, c: r[0]["detail"].__setitem__("observed_front", [1, 99]),
            "front-observed-noncanonical-order": lambda r, c: r[0]["detail"].__setitem__("observed_front", list(reversed(r[0]["detail"]["observed_front"]))),
            "package-order-reversal": lambda r, c: r.reverse(),
            "hit-at-k-wrong-key-set-control": lambda r, c: r[0]["score"]["hit_at_k"].pop("10"),
            "duplicate-issue-control": lambda r, c: r[1]["detail"].__setitem__("issue_id", r[0]["detail"]["issue_id"]),
            "nonfinite-metric-control": lambda r, c: r[0]["score"].__setitem__("joint_log_score", "NaN"),
            "rank-midpoint-control": lambda r, c: r[0]["score"].__setitem__("tie_midrank", "99"),
            "inclusion-range-control": lambda r, c: r[0]["detail"]["front_inclusion"].__setitem__(0, "2"),
            "window-key-model-mismatch-control": lambda r, c: r[0]["detail"].__setitem__("model_id", "other-model"),
        }
        for case_id, mutator in mutations.items():
            with self.subTest(case_id=case_id):
                rejected(mutator)

    def test_canonical_anchor_rejects_five_coordinated_substitutions(self):
        original = [_package(1)]
        anchor = _anchor(original, "coordinated")
        variants = {
            "model": [_package(1, front_ticks=(0, 0, 1024, -1024, 512))],
            "result": [_package(2)],
            "comparator": copy.deepcopy(original),
            "forecast-score-id": copy.deepcopy(original),
            "revision-score-id": copy.deepcopy(original),
        }
        variants["comparator"][0]["score"]["comparator_forecast_id"] = "substitute-m0"
        variants["comparator"][0]["detail"]["recomputation"]["comparator_forecast_id"] = "substitute-m0"
        variants["forecast-score-id"][0]["score"]["forecast_id"] = "substitute-forecast"
        variants["forecast-score-id"][0]["detail"]["recomputation"]["forecast_id"] = "substitute-forecast"
        variants["forecast-score-id"][0]["score"]["score_id"] = derive_score_id(
            "substitute-forecast", variants["forecast-score-id"][0]["score"]["result_revision_id"], "phase4-metric-v1")
        variants["forecast-score-id"][0]["detail"]["score_id"] = variants["forecast-score-id"][0]["score"]["score_id"]
        variants["revision-score-id"][0]["score"]["result_revision_id"] = "substitute-revision"
        variants["revision-score-id"][0]["detail"]["recomputation"]["result_revision_id"] = "substitute-revision"
        variants["revision-score-id"][0]["score"]["score_id"] = derive_score_id(
            variants["revision-score-id"][0]["score"]["forecast_id"], "substitute-revision", "phase4-metric-v1")
        variants["revision-score-id"][0]["detail"]["score_id"] = variants["revision-score-id"][0]["score"]["score_id"]
        for case_id, rows in variants.items():
            with self.subTest(case_id=case_id), self.assertRaises(WindowViolation):
                _build_window_metric(
                    packages=rows, game="ssq", model_id="P4E1", comparator_champion_id="M0",
                    model_release_id="release-a", window_id="coordinated",
                    metric_contract_id="phase4-metric-v1", canonical_anchor=anchor,
                    allow_oracle_fixture=True,
                )


if __name__ == "__main__":
    unittest.main()
