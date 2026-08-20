#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4e9"))
import run_nested_spaces as e9

OUT = ROOT / "artifacts/phase4e11"
INNER_DRAWS = 120
OUTER_DRAWS = 120
CALIBRATION_DRAWS = 60
SPACES = (1000, 2000, 5000, 10000, 50000, 100000)
MASKS = {
    "e8_selected": None,
    "all14": set(e9.oracle.FEATURE_IDS),
    "history_only": set(e9.oracle.FEATURE_IDS[:5]),
    "history_structure": set(e9.oracle.FEATURE_IDS[:12]),
}


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def quantile(values: list[int], probability: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered), math.ceil((len(ordered) + 1) * probability)) - 1]


def wilson(hits: int, draws: int) -> list[float]:
    return e9.wilson(hits, draws)


def coverage(rows: list[dict[str, object]], k: int, space: int) -> dict[str, object]:
    hits = sum(int(row["canonical_rank"]) <= k for row in rows)
    rate = hits / len(rows)
    return {"hits": hits, "draws": len(rows), "rate": rate, "wilson95": wilson(hits, len(rows)),
            "uniform_expected_rate": k / space, "lift_vs_uniform": rate / (k / space) if hits else 0.0}


def rank_with_mask(game: str, data: list[e9.oracle.Draw], target: int, mask_name: str) -> dict[str, int]:
    old_mask = e9.MASK[game]
    old_l2 = e9.L2[game]
    if mask_name == "e8_selected":
        mask = old_mask
    else:
        mask = MASKS[mask_name]
    try:
        e9.MASK[game] = mask
        return e9.rank_at(game, data, target)
    finally:
        e9.MASK[game] = old_mask
        e9.L2[game] = old_l2


def run(game: str) -> dict[str, object]:
    data = e9.load(game)
    inner_targets = range(len(data) - INNER_DRAWS - OUTER_DRAWS, len(data) - OUTER_DRAWS)
    outer_targets = range(len(data) - OUTER_DRAWS, len(data))
    space = math.prod(math.comb(n, k) for n, k in e9.oracle.RULES[game])
    candidate_rows: dict[str, list[dict[str, object]]] = {}
    metrics: dict[str, dict[str, object]] = {}
    for mask_name in MASKS:
        rows = []
        for target in inner_targets:
            ranking = rank_with_mask(game, data, target, mask_name)
            rows.append({"issue": data[target].issue, "target_position": target,
                         "maximum_training_position": target - 1, "strict_lag": True, **ranking})
        candidate_rows[mask_name] = rows
        ranks = [int(row["canonical_rank"]) for row in rows]
        metrics[mask_name] = {"inner_k90": quantile(ranks, 0.9), "inner_k80": quantile(ranks, 0.8),
                              "inner_k50": quantile(ranks, 0.5), "mean_rank": sum(ranks) / len(ranks)}
    selected = min(MASKS, key=lambda name: (metrics[name]["inner_k90"], metrics[name]["inner_k80"],
                                             metrics[name]["inner_k50"], tuple(MASKS).index(name)))
    outer_rows = []
    for target in outer_targets:
        ranking = rank_with_mask(game, data, target, selected)
        outer_rows.append({"issue": data[target].issue, "target_position": target,
                           "maximum_training_position": target - 1, "strict_lag": True, **ranking,
                           "rank_percentile": ranking["canonical_rank"] / space,
                           "covered": {str(k): ranking["canonical_rank"] <= k for k in SPACES}})
    calibration = outer_rows[:CALIBRATION_DRAWS]
    evaluation = outer_rows[CALIBRATION_DRAWS:]
    selected_k90 = quantile([int(row["canonical_rank"]) for row in calibration], 0.9)
    eval_cov = coverage(evaluation, selected_k90, space)
    report = {
        "artifact_type": "phase4e11_mask_selection_report", "status": "RETROSPECTIVE_BACKTEST_ONLY", "game": game,
        "candidate_selection_window": {"draws": INNER_DRAWS, "first_issue": data[next(iter(inner_targets))].issue,
                                        "last_issue": data[next(reversed(inner_targets))].issue, "strictly_before_outer": True},
        "candidate_metrics": metrics, "selected_mask": selected,
        "selection_rule": "minimum_inner_split_conformal_k90_then_k80_k50_v1",
        "outer_window_draws": OUTER_DRAWS, "outer_calibration_draws": CALIBRATION_DRAWS,
        "outer_selected_k90": selected_k90, "outer_evaluation": eval_cov,
        "compression": {str(k): coverage(evaluation, k, space) for k in SPACES},
        "reliability_gate": {"evaluation_rate_min": 0.8, "wilson_lower_min": 0.75,
                             "pass": eval_cov["rate"] >= 0.8 and eval_cov["wilson95"][0] >= 0.75},
        "compression_to_100000_accepted": (
            coverage(evaluation, 100000, space)["rate"] >= 0.8
            and coverage(evaluation, 100000, space)["wilson95"][0] >= 0.75
        ),
        "p4e6_serving_unchanged": True, "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY", "promotion_eligible": False,
    }
    path = OUT / game
    path.mkdir(parents=True, exist_ok=True)
    (path / "inner-rolling-report.jsonl").write_bytes(b"".join(canonical({"mask": name, "rows": rows}) for name, rows in candidate_rows.items()))
    (path / "outer-rolling-report.jsonl").write_bytes(b"".join(canonical(row) for row in outer_rows))
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    OUT.mkdir(exist_ok=True)
    reports = {game: run(game) for game in ("ssq", "dlt")}
    summary = {"artifact_type": "phase4e11_mask_selection_summary", "status": "RETROSPECTIVE_BACKTEST_ONLY",
               "games": reports, "all_compression_to_100000_accepted": all(r["compression_to_100000_accepted"] for r in reports.values()),
               "p4e6_serving_unchanged": True, "p4e6_serving_release": "P4-P4E2-20260815-r12",
               "p4e6_terminal_status": "PROSPECTIVE_ONLY"}
    (OUT / "summary.json").write_bytes(canonical(summary))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
