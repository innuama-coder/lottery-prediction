#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4e9"))
import run_nested_spaces as e9

OUT = ROOT / "artifacts/phase4e10"
INNER_DRAWS = 120
OUTER_DRAWS = 120
CALIBRATION_DRAWS = 60
CANDIDATES = ("positive", "negative", "zero_uniform")


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def transform_rank(row: dict[str, object], space: int, candidate: str, uniform_rank: int) -> int:
    if candidate == "positive":
        return int(row["canonical_rank"])
    if candidate == "negative":
        return (
            space
            - int(row["tie_rank_upper"])
            + (int(row["canonical_rank"]) - int(row["tie_rank_lower"]))
            + 1
        )
    if candidate == "zero_uniform":
        return uniform_rank
    raise ValueError(candidate)


def uniform_ranks(game: str, data: list[e9.oracle.Draw], targets: range) -> list[int]:
    zone_maps = []
    for n, k in e9.oracle.RULES[game]:
        zone_maps.append({combo: index for index, combo in enumerate(itertools.combinations(range(1, n + 1), k))})
    back_count = math.comb(*e9.oracle.RULES[game][1])
    return [
        zone_maps[0][data[target].front] * back_count + zone_maps[1][data[target].back] + 1
        for target in targets
    ]


def quantile(values: list[int], probability: float, conformal: bool) -> int:
    ordered = sorted(values)
    multiplier = len(ordered) + 1 if conformal else len(ordered)
    return ordered[min(len(ordered), math.ceil(multiplier * probability)) - 1]


def wilson(hits: int, draws: int) -> list[float]:
    return e9.wilson(hits, draws)


def run(game: str) -> dict[str, object]:
    data = e9.load(game)
    inner_targets = range(len(data) - INNER_DRAWS - OUTER_DRAWS, len(data) - OUTER_DRAWS)
    outer_targets = range(len(data) - OUTER_DRAWS, len(data))
    space = math.prod(math.comb(n, k) for n, k in e9.oracle.RULES[game])
    inner_uniform = uniform_ranks(game, data, inner_targets)
    inner_rows = []
    for offset, target in enumerate(inner_targets):
        ranking = e9.rank_at(game, data, target)
        inner_rows.append(
            {
                "issue": data[target].issue,
                "target_position": target,
                "maximum_training_position": target - 1,
                "strict_lag": True,
                **ranking,
                "candidate_ranks": {
                    candidate: transform_rank(ranking, space, candidate, inner_uniform[offset])
                    for candidate in CANDIDATES
                },
            }
        )
    candidate_metrics = {}
    for candidate in CANDIDATES:
        ranks = [int(row["candidate_ranks"][candidate]) for row in inner_rows]
        candidate_metrics[candidate] = {
            "inner_k90": quantile(ranks, 0.9, conformal=True),
            "inner_k80": quantile(ranks, 0.8, conformal=True),
            "inner_k50": quantile(ranks, 0.5, conformal=True),
            "mean_rank": sum(ranks) / len(ranks),
        }
    selected = min(
        CANDIDATES,
        key=lambda candidate: (
            candidate_metrics[candidate]["inner_k90"],
            candidate_metrics[candidate]["inner_k80"],
            candidate_metrics[candidate]["inner_k50"],
            CANDIDATES.index(candidate),
        ),
    )
    e9_rows = [
        json.loads(line)
        for line in (ROOT / f"artifacts/phase4e9/{game}/rolling-report.jsonl").read_text().splitlines()
    ]
    if [row["issue"] for row in e9_rows] != [data[target].issue for target in outer_targets]:
        raise ValueError("FAIL_OUTER_IDENTITY: Phase4E9 rows do not match frozen outer window")
    outer_uniform = uniform_ranks(game, data, outer_targets)
    outer_ranks = [
        transform_rank(row, space, selected, outer_uniform[index]) for index, row in enumerate(e9_rows)
    ]
    calibration = outer_ranks[:CALIBRATION_DRAWS]
    evaluation = outer_ranks[CALIBRATION_DRAWS:]
    selected_k = quantile(calibration, 0.9, conformal=True)
    evaluation_hits = sum(rank <= selected_k for rank in evaluation)
    interval = wilson(evaluation_hits, len(evaluation))
    report = {
        "artifact_type": "phase4e10_orientation_selection_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "candidate_selection_window": {
            "draws": INNER_DRAWS,
            "first_issue": inner_rows[0]["issue"],
            "last_issue": inner_rows[-1]["issue"],
            "strictly_before_outer": True,
        },
        "candidate_metrics": candidate_metrics,
        "selected_candidate": selected,
        "selection_rule": "minimum_inner_split_conformal_k90_then_k80_k50_v1",
        "outer_window_draws": OUTER_DRAWS,
        "outer_calibration_draws": CALIBRATION_DRAWS,
        "outer_evaluation_draws": OUTER_DRAWS - CALIBRATION_DRAWS,
        "outer_selected_k90": selected_k,
        "outer_evaluation": {
            "hits": evaluation_hits,
            "draws": len(evaluation),
            "rate": evaluation_hits / len(evaluation),
            "wilson95": interval,
            "reliability_gate_pass": evaluation_hits / len(evaluation) >= 0.8 and interval[0] >= 0.75,
        },
        "compression_to_100000_accepted": sum(rank <= 100000 for rank in evaluation) / len(evaluation) >= 0.8,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
        "promotion_eligible": False,
    }
    path = OUT / game
    path.mkdir(parents=True, exist_ok=True)
    (path / "inner-rolling-report.jsonl").write_bytes(b"".join(canonical(row) for row in inner_rows))
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    OUT.mkdir(exist_ok=True)
    reports = {game: run(game) for game in ("ssq", "dlt")}
    summary = {
        "artifact_type": "phase4e10_orientation_selection_summary",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "games": reports,
        "all_compression_to_100000_accepted": all(report["compression_to_100000_accepted"] for report in reports.values()),
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
    }
    (OUT / "summary.json").write_bytes(canonical(summary))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
