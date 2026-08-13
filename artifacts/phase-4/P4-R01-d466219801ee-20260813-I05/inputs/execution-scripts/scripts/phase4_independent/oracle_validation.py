from __future__ import annotations

import itertools
from decimal import Decimal, InvalidOperation, localcontext
from math import comb
from typing import Any, Mapping, Sequence


PRECISION = 80
PROBABILITY_ABS_TOLERANCE = Decimal("1e-45")
METRIC_ABS_TOLERANCE = Decimal("1e-40")
FULL_RULE_ERROR_LIMIT = Decimal("1e-60")
EXPECTED_TOP_K = [10, 100, 200, 1000]
EXPECTED_GAMES = {
    "ssq": {"rule_id": "ssq-ns-33c6-16c1-v1", "front_n": 33, "front_k": 6, "back_n": 16, "back_k": 1, "space_size": 17721088},
    "dlt": {"rule_id": "dlt-ns-35c5-12c2-v1", "front_n": 35, "front_k": 5, "back_n": 12, "back_k": 2, "space_size": 21425712},
}


def _require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def _finite_decimal(value: Any, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a Decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{label} is not numeric") from error
    if not result.is_finite():
        raise ValueError(f"{label} is non-finite")
    return result


def validate_probability_contract(value: Mapping[str, Any]) -> None:
    _require_equal(value.get("games"), EXPECTED_GAMES, "probability.games")
    _require_equal(value.get("top_k"), EXPECTED_TOP_K, "probability.top_k")
    _require_equal(value.get("forecast_size"), 1000, "probability.forecast_size")
    _require_equal(value.get("joint_score_bounds"), [-28672, 28672], "probability.joint_score_bounds")
    _require_equal(value.get("probability_order_key"), "P4Q1024-<score_plus_28672_as_5_decimal_digits>", "probability.probability_order_key")
    _require_equal(value.get("tie_equivalence"), "exact_probability_order_key", "probability.tie_equivalence")
    _require_equal(value.get("m0_semantics"), "one_full_space_tie_group", "probability.m0_semantics")
    _require_equal(value.get("approximate_tie_grouping_allowed"), False, "probability.approximate_tie_grouping_allowed")
    _require_equal(value.get("normalization_tolerance"), {"absolute": "1e-45", "relative": "1e-40"}, "probability.normalization_tolerance")
    _require_equal(value.get("probability_decimal_places"), 50, "probability.probability_decimal_places")
    _require_equal(value.get("minimum_probability_lower_bound"), "1e-32", "probability.minimum_probability_lower_bound")


def validate_metric_contract(value: Mapping[str, Any]) -> None:
    _require_equal(value.get("metric_contract_id"), "phase4-metric-v1", "metric.metric_contract_id")
    _require_equal(value.get("minimum_observations"), 30, "metric.minimum_observations")
    _require_equal(value.get("reliability_bins"), {"count": 10, "kind": "equal_width_left_closed_right_open_last_includes_one"}, "metric.reliability_bins")
    _require_equal(value.get("numeric_tolerance"), {"absolute": "1e-40", "relative": "1e-35"}, "metric.numeric_tolerance")
    _require_equal(value.get("insufficient_state"), "insufficient_observation", "metric.insufficient_state")
    _require_equal(value.get("zero_probability_allowed"), False, "metric.zero_probability_allowed")


def validate_full_rule_spec(value: Mapping[str, Any]) -> None:
    expected = {
        "spec_id": "P4E1-full-rule-known-answer-v1",
        "result_blind": True,
        "probability_family": "P4E1",
        "decimal_precision": 80,
        "scale": 1024,
        "games": EXPECTED_GAMES,
        "top_k": EXPECTED_TOP_K,
        "raw_tick_rule": {"positions_1_to_4": [32, 24, 16, 8], "positions_n_minus_3_to_n": [-8, -16, -24, -32], "all_other_positions": 0},
        "normalization": "subtract_raw_tick_at_number_1",
        "normalized_tick_range": [-64, 0],
        "candidate_source": "mathematical_spec_only_not_product_output",
        "m0_coverage": "K/full_space_size",
        "candidate_coverage": "sum_exact_candidate_probability_over_deterministic_top_K",
        "strict_gate": "candidate_coverage>M0_coverage_for_all_eight_cells",
        "absolute_error_bound": "1e-60",
        "t01_probability_contract_sha256": "3d122a337a8c13840963e578b0c670169983c893409b2b299112575e2d228d2c",
    }
    for key, expected_value in expected.items():
        _require_equal(value.get(key), expected_value, f"full_rule_spec.{key}")


def validate_full_rule_result(value: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, str]:
    validate_full_rule_spec(spec)
    results = value.get("results")
    cells = value.get("eight_cells")
    if not isinstance(results, list) or len(results) != 2 or not isinstance(cells, list) or len(cells) != 8:
        raise ValueError("full-rule result/count mismatch")
    residuals: dict[str, str] = {}
    expected_cells: list[tuple[str, int]] = []
    for result in results:
        game = result.get("game")
        if game not in EXPECTED_GAMES:
            raise ValueError("unexpected full-rule game")
        rule = EXPECTED_GAMES[game]
        _require_equal(result.get("rule_id"), rule["rule_id"], f"{game}.rule_id")
        _require_equal(result.get("space_size"), rule["space_size"], f"{game}.space_size")
        _require_equal(result.get("front_combination_count"), comb(rule["front_n"], rule["front_k"]), f"{game}.front_count")
        _require_equal(result.get("back_combination_count"), comb(rule["back_n"], rule["back_k"]), f"{game}.back_count")
        _require_equal(result.get("histogram_total"), rule["space_size"], f"{game}.histogram_total")
        if not isinstance(result.get("top1000"), list) or len(result["top1000"]) != 1000:
            raise ValueError(f"{game}.top1000 count mismatch")
        error = _finite_decimal(result.get("partition_absolute_difference"), f"{game}.partition_absolute_difference")
        if error > FULL_RULE_ERROR_LIMIT:
            raise ValueError(f"{game}.partition error exceeds 1e-60")
        _require_equal(result.get("normalization_absolute_error_bound"), "1e-60", f"{game}.normalization_absolute_error_bound")
        expected_cells.extend((game, k) for k in EXPECTED_TOP_K)
    actual_cells: list[tuple[str, int]] = []
    for cell in cells:
        game = cell.get("game")
        k = cell.get("K")
        actual_cells.append((game, k))
        if game not in EXPECTED_GAMES or k not in EXPECTED_TOP_K:
            raise ValueError("unexpected game/K cell")
        m0 = _finite_decimal(cell.get("m0_coverage"), f"{game}.{k}.m0")
        candidate = _finite_decimal(cell.get("candidate_coverage"), f"{game}.{k}.candidate")
        difference = _finite_decimal(cell.get("difference"), f"{game}.{k}.difference")
        error = _finite_decimal(cell.get("absolute_error_bound"), f"{game}.{k}.absolute_error_bound")
        with localcontext() as context:
            context.prec = PRECISION
            expected_m0 = Decimal(k) / Decimal(EXPECTED_GAMES[game]["space_size"])
            arithmetic_residual = abs((candidate - m0) - difference)
            m0_residual = abs(m0 - expected_m0)
        if error > FULL_RULE_ERROR_LIMIT or arithmetic_residual > error or m0_residual > METRIC_ABS_TOLERANCE:
            raise ValueError(f"{game}.{k} numeric/error validation failed")
        if not candidate > m0 or cell.get("strictly_better") is not True:
            raise ValueError(f"{game}.{k} is not numerically strictly better")
        residuals[f"{game}.{k}.difference"] = str(arithmetic_residual)
        residuals[f"{game}.{k}.m0"] = str(m0_residual)
    _require_equal(actual_cells, expected_cells, "full-rule cell ordering")
    _require_equal(value.get("all_eight_strictly_better"), True, "full-rule aggregate gate")
    return residuals


def validate_m0_results(value: Mapping[str, Any]) -> dict[str, str]:
    games = value.get("games")
    if not isinstance(games, list) or len(games) != 2:
        raise ValueError("M0 game count mismatch")
    residuals: dict[str, str] = {}
    for row in games:
        game = row.get("game")
        if game not in EXPECTED_GAMES:
            raise ValueError("unexpected M0 game")
        space = EXPECTED_GAMES[game]["space_size"]
        probability = _finite_decimal(row.get("joint_probability"), f"M0.{game}.joint_probability")
        with localcontext() as context:
            context.prec = PRECISION
            normalization_residual = abs(probability * Decimal(space) - Decimal(1))
            expected = Decimal(1) / Decimal(space)
            probability_residual = abs(probability - expected)
        if normalization_residual > PROBABILITY_ABS_TOLERANCE or probability_residual > PROBABILITY_ABS_TOLERANCE:
            raise ValueError(f"M0.{game} Decimal80 tolerance failed")
        _require_equal(row.get("normalization_tolerance"), "1e-45", f"M0.{game}.normalization_tolerance")
        if len(row.get("top1000", [])) != 1000 or row.get("histogram") != [[0, space]]:
            raise ValueError(f"M0.{game} count/group mismatch")
        residuals[game] = str(normalization_residual)
    return residuals


def validate_metric_vectors_independent(value: Mapping[str, Any]) -> dict[str, str]:
    fixture = value.get("fixture", {})
    _require_equal(fixture.get("space_size"), 40, "metric fixture space_size")
    rows = value.get("per_forecast")
    if not isinstance(rows, list) or len(rows) != 30:
        raise ValueError("metric forecast count mismatch")
    front_ticks = (0, 1024, 0, -1024, 512)
    back_ticks = (0, -512, 512, 0)
    with localcontext() as context:
        context.prec = PRECISION
        front_weighted = [((Decimal(sum(front_ticks[i - 1] for i in ticket)) / Decimal(1024)).exp(), ticket) for ticket in itertools.combinations(range(1, 6), 2)]
        back_weighted = [((Decimal(sum(back_ticks[i - 1] for i in ticket)) / Decimal(1024)).exp(), ticket) for ticket in itertools.combinations(range(1, 5), 1)]
        front_z = sum(weight for weight, _ in front_weighted)
        back_z = sum(weight for weight, _ in back_weighted)
        maximum_probability_error = Decimal(0)
        maximum_log_error = Decimal(0)
        for row in rows:
            front = tuple(row["front"])
            back = tuple(row["back"])
            score = sum(front_ticks[i - 1] for i in front) + sum(back_ticks[i - 1] for i in back)
            expected_probability = (Decimal(score) / Decimal(1024)).exp() / (front_z * back_z)
            recorded_probability = _finite_decimal(row.get("joint_probability"), "metric.joint_probability")
            recorded_log = _finite_decimal(row.get("joint_log_score"), "metric.joint_log_score")
            maximum_probability_error = max(maximum_probability_error, abs(recorded_probability - expected_probability))
            maximum_log_error = max(maximum_log_error, abs(recorded_log + expected_probability.ln()))
    if maximum_probability_error > METRIC_ABS_TOLERANCE or maximum_log_error > METRIC_ABS_TOLERANCE:
        raise ValueError("independent metric recomputation exceeds 1e-40")
    return {"maximum_probability_absolute_error": str(maximum_probability_error), "maximum_log_absolute_error": str(maximum_log_error)}
