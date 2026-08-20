#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4_independent"))
import p4e2_oracle as oracle

SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
OUT = ROOT / "artifacts/phase4e9"
WINDOW = 120
CALIBRATION_DRAWS = 60
SPACES = (1000, 2000, 5000, 10000, 50000, 100000)
CALIBRATION_TARGETS = (0.5, 0.8, 0.9)
RELIABLE_CALIBRATION_TARGET = 0.9
RELIABLE_EVALUATION_RATE = 0.8
RELIABLE_WILSON_LOWER = 0.75
L2 = {"ssq": 8.0, "dlt": 24.0}
MASK_NAME = {"ssq": "history_structure", "dlt": "all14"}
MASK = {"ssq": set(oracle.FEATURE_IDS[:12]), "dlt": set(oracle.FEATURE_IDS)}
COMBOS: dict[tuple[int, int], np.ndarray] = {}


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def load(game: str) -> list[oracle.Draw]:
    rows = [json.loads(line) for line in (SOURCE / f"{game}.jsonl").read_text().splitlines()]
    return [
        oracle.Draw(
            str(row["issue"]), tuple(row["front"]), tuple(row["back"]), row.get("source_record_sha256", "")
        )
        for row in rows
    ]


def combo_array(n: int, k: int) -> np.ndarray:
    key = (n, k)
    if key not in COMBOS:
        flat = np.fromiter(
            (value for combo in itertools.combinations(range(1, n + 1), k) for value in combo),
            dtype=np.int16,
            count=math.comb(n, k) * k,
        )
        COMBOS[key] = flat.reshape((-1, k))
    return COMBOS[key]


def vector_scores(context: dict[str, object], coefficients: dict[str, float]) -> np.ndarray:
    n, k = int(context["n"]), int(context["k"])
    combos = combo_array(n, k)
    number_scores = np.zeros(n)
    for key in oracle.NUMBER_IDS:
        number_scores += float(coefficients[key]) * np.asarray(context["number_features"][key])
    scores = number_scores[combos - 1].sum(axis=1)
    pair_matrix = np.asarray(context["pair_matrix"])
    pair_sum = np.zeros(len(combos))
    for left in range(k - 1):
        for right in range(left + 1, k):
            pair_sum += pair_matrix[combos[:, left], combos[:, right]]
    if k > 1:
        scores += float(coefficients["F06"]) * pair_sum / (k * (k - 1) / 2)
    spec = context["normalization"]["F07"]
    overlap = np.isin(combos, np.asarray(context["last_numbers"])).sum(axis=1) / k
    scores += float(coefficients["F07"]) * (overlap - float(spec["mean"])) / float(spec["scale"])
    structure = [
        combos.sum(axis=1),
        combos[:, -1] - combos[:, 0],
        np.abs((combos % 2).sum(axis=1) - k / 2),
    ]
    width = math.ceil(n / 3)
    structure.append(sum(np.any(((combos - 1) // width) == bucket, axis=1) for bucket in range(3)) / 3)
    structure.append((np.diff(combos, axis=1) == 1).sum(axis=1) if k > 1 else np.zeros(len(combos)))
    structure.append(sum(np.any((combos % 10) == digit, axis=1) for digit in range(10)) / k)
    if k > 1:
        gaps = np.diff(combos, axis=1)
        mean = (combos[:, -1] - combos[:, 0]) / (k - 1)
        structure.append(np.sqrt(np.maximum(0, (gaps * gaps).mean(axis=1) - mean * mean)))
    else:
        structure.append(np.zeros(len(combos)))
    for key, raw in zip(oracle.STRUCTURE_IDS, structure):
        spec = context["normalization"][key]
        scores += float(coefficients[key]) * (raw - float(spec["mean"])) / float(spec["scale"])
    return scores


def approximate_score_ticks(scores: np.ndarray) -> np.ndarray:
    return np.rint(scores * oracle.SCORE_ORDER_SCALE).astype(np.int64)


def rank_at(game: str, data: list[oracle.Draw], target: int) -> dict[str, int]:
    old_grid = oracle.L2_GRID
    oracle.L2_GRID = (L2[game],)
    try:
        coefficients = oracle.fit_coefficients(game, data, target, L2[game])
    finally:
        oracle.L2_GRID = old_grid
    zone_scores = []
    contexts = []
    masked_coefficients = []
    actual_indices = []
    actual_scores = []
    actual_numbers = (data[target].front, data[target].back)
    for zone, (n, k) in enumerate(oracle.RULES[game]):
        context = oracle.feature_context(game, data[:target], zone)
        masked = {key: value if key in MASK[game] else 0.0 for key, value in coefficients[zone].items()}
        scores = vector_scores(context, masked)
        combos = combo_array(n, k)
        matches = np.flatnonzero(np.all(combos == np.asarray(actual_numbers[zone]), axis=1))
        if len(matches) != 1:
            raise ValueError("FAIL_ACTUAL_TICKET_IDENTITY: expected one legal combination")
        actual_index = int(matches[0])
        actual_scores.append(oracle._score(actual_numbers[zone], context, masked))
        contexts.append(context)
        masked_coefficients.append(masked)
        zone_scores.append(scores)
        actual_indices.append(actual_index)
    actual_tick = oracle.score_order_tick(actual_scores[0] + actual_scores[1])
    greater = 0
    equal = 0
    tie_before = 0
    exact_boundary_rechecks = 0
    tick_mismatches_corrected = 0
    exact_front_cache: dict[int, float] = {}
    for back_index, back_score in enumerate(zone_scores[1]):
        ticks = approximate_score_ticks(zone_scores[0] + float(back_score))
        boundary_indices = np.flatnonzero(np.abs(ticks - actual_tick) <= 1)
        if len(boundary_indices):
            back_combo = tuple(combo_array(*oracle.RULES[game][1])[back_index])
            exact_back = oracle._score(back_combo, contexts[1], masked_coefficients[1])
            for front_index in boundary_indices:
                index = int(front_index)
                if index not in exact_front_cache:
                    front_combo = tuple(combo_array(*oracle.RULES[game][0])[index])
                    exact_front_cache[index] = oracle._score(front_combo, contexts[0], masked_coefficients[0])
                exact_tick = oracle.score_order_tick(exact_front_cache[index] + exact_back)
                tick_mismatches_corrected += int(ticks[index] != exact_tick)
                ticks[index] = exact_tick
            exact_boundary_rechecks += len(boundary_indices)
        if back_index == actual_indices[1] and int(ticks[actual_indices[0]]) != actual_tick:
            raise ValueError("FAIL_SCORE_TICK_EQUIVALENCE: boundary guard did not preserve the actual ticket tick")
        greater += int(np.count_nonzero(ticks > actual_tick))
        same = ticks == actual_tick
        equal += int(np.count_nonzero(same))
        tie_before += int(np.count_nonzero(same[: actual_indices[0]]))
        if back_index < actual_indices[1] and bool(same[actual_indices[0]]):
            tie_before += 1
    canonical_rank = greater + tie_before + 1
    return {
        "canonical_rank": canonical_rank,
        "tie_rank_lower": greater + 1,
        "tie_rank_upper": greater + equal,
        "actual_score_tick": actual_tick,
        "tie_group_size": equal,
        "exact_boundary_rechecks": exact_boundary_rechecks,
        "tick_mismatches_corrected": tick_mismatches_corrected,
    }


def wilson(hits: int, draws: int) -> list[float]:
    z = 1.959963984540054
    rate = hits / draws
    denominator = 1 + z * z / draws
    center = (rate + z * z / (2 * draws)) / denominator
    margin = z * math.sqrt((rate * (1 - rate) + z * z / (4 * draws)) / draws) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def coverage(rows: list[dict[str, object]], k: int, space: int) -> dict[str, object]:
    hits = sum(int(row["canonical_rank"]) <= k for row in rows)
    rate = hits / len(rows)
    return {
        "hits": hits,
        "draws": len(rows),
        "rate": rate,
        "wilson95": wilson(hits, len(rows)),
        "uniform_expected_rate": k / space,
        "lift_vs_uniform": rate / (k / space) if hits else 0.0,
    }


def quantile_k(rows: list[dict[str, object]], target: float, conformal: bool) -> int:
    ranks = sorted(int(row["canonical_rank"]) for row in rows)
    multiplier = len(ranks) + 1 if conformal else len(ranks)
    return ranks[min(len(ranks), math.ceil(multiplier * target)) - 1]


def run(game: str) -> dict[str, object]:
    data = load(game)
    start = len(data) - WINDOW
    space = math.prod(math.comb(n, k) for n, k in oracle.RULES[game])
    rows = []
    for target in range(start, len(data)):
        ranking = rank_at(game, data, target)
        rows.append(
            {
                "issue": data[target].issue,
                "target_position": target,
                "maximum_training_position": target - 1,
                "strict_lag": True,
                **ranking,
                "rank_percentile": ranking["canonical_rank"] / space,
                "covered": {str(k): ranking["canonical_rank"] <= k for k in SPACES},
            }
        )
    calibration_rows = rows[:CALIBRATION_DRAWS]
    evaluation_rows = rows[CALIBRATION_DRAWS:]
    fixed_coverage = {
        str(k): {
            "all_120": coverage(rows, k, space),
            "calibration_60": coverage(calibration_rows, k, space),
            "evaluation_60": coverage(evaluation_rows, k, space),
        }
        for k in SPACES
    }
    calibrated_spaces = {}
    for target in CALIBRATION_TARGETS:
        k = quantile_k(calibration_rows, target, conformal=True)
        calibrated_spaces[str(target)] = {
            "selection_method": "split_conformal_rank_quantile_ceil_n_plus_1_p_v1",
            "selected_k_from_calibration_only": k,
            "calibration": coverage(calibration_rows, k, space),
            "evaluation": coverage(evaluation_rows, k, space),
        }
    reliable = calibrated_spaces[str(RELIABLE_CALIBRATION_TARGET)]
    evaluation = reliable["evaluation"]
    reliable_pass = (
        float(evaluation["rate"]) >= RELIABLE_EVALUATION_RATE
        and float(evaluation["wilson95"][0]) >= RELIABLE_WILSON_LOWER
    )
    sorted_ranks = sorted(int(row["canonical_rank"]) for row in rows)
    monotonic = all(
        fixed_coverage[str(left)]["all_120"]["hits"] <= fixed_coverage[str(right)]["all_120"]["hits"]
        for left, right in zip(SPACES, SPACES[1:])
    )
    report = {
        "artifact_type": "phase4e9_nested_probability_space_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "model_configuration": {"source": "phase4e8_inner_selection", "mask": MASK_NAME[game], "l2": L2[game]},
        "window_size": WINDOW,
        "calibration_draws": CALIBRATION_DRAWS,
        "evaluation_draws": WINDOW - CALIBRATION_DRAWS,
        "full_space_size": space,
        "ranking_contract": oracle.RANKING_ALGORITHM_ID,
        "score_order_key_id": oracle.SCORE_ORDER_KEY_ID,
        "score_tick_boundary_guard": {
            "method": "exact_oracle_recheck_within_one_approximate_tick_of_actual_v1",
            "exact_rechecks": sum(int(row["exact_boundary_rechecks"]) for row in rows),
            "tick_mismatches_corrected": sum(int(row["tick_mismatches_corrected"]) for row in rows),
        },
        "nested_spaces": list(SPACES),
        "nested_coverage_monotonic": monotonic,
        "fixed_space_coverage": fixed_coverage,
        "calibrated_spaces": calibrated_spaces,
        "first_reliable_space": {
            "calibration_target": RELIABLE_CALIBRATION_TARGET,
            "selected_k": reliable["selected_k_from_calibration_only"],
            "acceptance": {
                "minimum_evaluation_rate": RELIABLE_EVALUATION_RATE,
                "minimum_wilson95_lower": RELIABLE_WILSON_LOWER,
                "actual_evaluation_rate": evaluation["rate"],
                "actual_wilson95_lower": evaluation["wilson95"][0],
                "pass": reliable_pass,
            },
        },
        "required_k_for_empirical_coverage_all_120": {
            str(target): quantile_k(rows, target, conformal=False) for target in CALIBRATION_TARGETS
        },
        "median_rank": (sorted_ranks[WINDOW // 2 - 1] + sorted_ranks[WINDOW // 2]) / 2,
        "mean_rank": sum(sorted_ranks) / WINDOW,
        "compression_to_100000_accepted": fixed_coverage["100000"]["evaluation_60"]["rate"] >= RELIABLE_EVALUATION_RATE,
        "probability_claim": "ranking coverage only; no ticket-level probability guarantee",
        "probability_spread_adjustment": "none",
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
    }
    path = OUT / game
    path.mkdir(parents=True, exist_ok=True)
    (path / "rolling-report.jsonl").write_bytes(b"".join(canonical(row) for row in rows))
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    OUT.mkdir(exist_ok=True)
    reports = {game: run(game) for game in ("ssq", "dlt")}
    summary = {
        "artifact_type": "phase4e9_nested_probability_space_summary",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "games": reports,
        "all_first_reliable_spaces_pass": all(report["first_reliable_space"]["acceptance"]["pass"] for report in reports.values()),
        "all_compression_to_100000_accepted": all(report["compression_to_100000_accepted"] for report in reports.values()),
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
    }
    (OUT / "summary.json").write_bytes(canonical(summary))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
