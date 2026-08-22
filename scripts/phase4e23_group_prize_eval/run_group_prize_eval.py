#!/usr/bin/env python3
"""Pre-registered, replayable Phase4E23 group-prize evaluation (route C).

Ticket construction is deliberately independent of the draw file.  Draws are
only consumed by ``run_evaluation`` after every candidate group has been built.
All prize decisions are delegated to the frozen Phase 4 prize-metrics entry.
"""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path
from statistics import fmean
from typing import Iterable

from lottery_system.phase4.bonus import DLT_FIXED_RULE, SSQ_FIXED_RULE
from lottery_system.phase4.prize_metrics import full_space_oracle, group_prize_metrics


ROOT = Path(__file__).resolve().parents[2]
DRAW_PATH = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"
OUTPUT_PATH = ROOT / "artifacts/phase4e23_group_prize_eval/summary.json"
GAMES = ("ssq", "dlt")
STRATEGIES = ("m0_uniform", "back_lock_coverage", "front_spread")
GROUP_SIZES = (1_000, 5_000, 10_000)
EVALUATION_DRAWS = 120
BOOTSTRAP_BLOCK_LENGTH = 10
BOOTSTRAP_REPLICATES = 2_000
BOOTSTRAP_SEED = 0

Ticket = tuple[tuple[int, ...], tuple[int, ...]]

GAME_CONFIG = {
    "ssq": {"front_n": 33, "front_k": 6, "back_n": 16, "back_k": 1,
            "rule_version": SSQ_FIXED_RULE},
    "dlt": {"front_n": 35, "front_k": 5, "back_n": 12, "back_k": 2,
            "rule_version": DLT_FIXED_RULE},
}


def _config(game: str) -> dict[str, object]:
    try:
        return GAME_CONFIG[game]
    except KeyError as exc:
        raise ValueError(f"unsupported game: {game}") from exc


def _interleaved_numbers(n: int, width: int) -> tuple[int, ...]:
    """Return a fixed low/middle/high interleaving of 1..n.

    Lexicographic combinations over this permutation expose all regions of the
    number range early.  Sorting each resulting ticket restores canonical
    ticket representation without changing uniqueness.
    """
    columns = (n + width - 1) // width
    return tuple(
        value
        for row in range(columns)
        for value in (row + 1 + column * columns for column in range(width))
        if value <= n
    )


def build_group(game: str, strategy: str, k: int) -> tuple[Ticket, ...]:
    """Build a deterministic pre-registered ticket group from game and size."""
    config = _config(game)
    if strategy not in STRATEGIES:
        raise ValueError(f"unsupported strategy: {strategy}")
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")

    front_n, front_k = int(config["front_n"]), int(config["front_k"])
    back_n, back_k = int(config["back_n"]), int(config["back_k"])
    canonical_fronts: Iterable[tuple[int, ...]] = itertools.combinations(
        range(1, front_n + 1), front_k
    )
    canonical_backs = tuple(itertools.combinations(range(1, back_n + 1), back_k))

    if strategy == "m0_uniform":
        candidates = (
            (front, back) for front in canonical_fronts for back in canonical_backs
        )
    else:
        locked_back = tuple(range(1, back_k + 1))
        if strategy == "front_spread":
            order = _interleaved_numbers(front_n, front_k)
            fronts = (
                tuple(sorted(front)) for front in itertools.combinations(order, front_k)
            )
        else:
            fronts = canonical_fronts
        candidates = ((front, locked_back) for front in fronts)

    group = tuple(itertools.islice(candidates, k))
    if len(group) != k:
        raise ValueError(f"k={k} exceeds the {game}/{strategy} ticket space")
    return group


def ticket_hit_state(ticket: Ticket, draw: dict[str, object]) -> tuple[int, int]:
    """Calculate one ticket's front/back hit counts against a target draw."""
    front, back = ticket
    return (
        len(set(front).intersection(draw["front_numbers"])),
        len(set(back).intersection(draw["back_numbers"])),
    )


def moving_block_bootstrap_ci(
    values: list[float],
    block_length: int = BOOTSTRAP_BLOCK_LENGTH,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Circular moving-block bootstrap percentile interval for a series mean."""
    if not values or block_length <= 0 or replicates <= 0:
        raise ValueError("values, block_length and replicates must be non-empty/positive")
    rng = random.Random(seed)
    n = len(values)
    block_count = (n + block_length - 1) // block_length
    means: list[float] = []
    for _ in range(replicates):
        sample: list[float] = []
        for _block in range(block_count):
            start = rng.randrange(n)
            sample.extend(values[(start + offset) % n] for offset in range(block_length))
        means.append(fmean(sample[:n]))
    means.sort()

    def percentile(probability: float) -> float:
        position = probability * (len(means) - 1)
        lower = int(position)
        fraction = position - lower
        if fraction == 0:
            return means[lower]
        return means[lower] + fraction * (means[lower + 1] - means[lower])

    return percentile(0.025), percentile(0.975)


def _load_evaluation_draws() -> dict[str, list[dict[str, object]]]:
    rows = [json.loads(line) for line in DRAW_PATH.read_text(encoding="utf-8").splitlines()]
    selected: dict[str, list[dict[str, object]]] = {}
    for game in GAMES:
        game_rows = sorted(
            (row for row in rows if row["game"] == game), key=lambda row: row["issue_id"]
        )
        if len(game_rows) < EVALUATION_DRAWS:
            raise RuntimeError(f"insufficient frozen draws for {game}: {len(game_rows)}")
        selected[game] = game_rows[-EVALUATION_DRAWS:]
    return selected


def run_evaluation() -> dict[str, object]:
    draws_by_game = _load_evaluation_draws()
    summary: dict[str, object] = {
        "protocol": {
            "name": "phase4e23-pre-registered-group-prize-evaluation-route-c",
            "draw_source": str(DRAW_PATH.relative_to(ROOT)),
            "evaluation_draws_per_game": EVALUATION_DRAWS,
            "selection": "last 120 issues after ascending issue_id sort",
            "group_sizes": list(GROUP_SIZES),
            "strategies": list(STRATEGIES),
            "bootstrap": {
                "method": "circular_moving_block_percentile",
                "block_length": BOOTSTRAP_BLOCK_LENGTH,
                "replicates": BOOTSTRAP_REPLICATES,
                "seed": BOOTSTRAP_SEED,
                "confidence_level": 0.95,
            },
        },
        "scientific_status": "no_confirmed_lift",
        "target_average_prize_yuan": 2.0,
        "games": {},
    }

    for game in GAMES:
        config = _config(game)
        rule = str(config["rule_version"])
        draws = draws_by_game[game]
        oracle = full_space_oracle(game)
        theoretical = oracle["fixed_prize_total_yuan"] / oracle["total_ticket_count"]
        game_result: dict[str, object] = {
            "evaluation_issue_first": draws[0]["issue_id"],
            "evaluation_issue_last": draws[-1]["issue_id"],
            "evaluation_draw_count": len(draws),
            "rule_version": rule,
            "theoretical_baseline_average_prize_yuan": theoretical,
            "full_space_oracle": oracle,
            "strategies": {},
        }
        series_by_strategy_k: dict[tuple[str, int], tuple[list[float], list[float]]] = {}

        for strategy in STRATEGIES:
            strategy_result: dict[str, object] = {}
            for k in GROUP_SIZES:
                group = build_group(game, strategy, k)
                prize_series: list[float] = []
                win_rate_series: list[float] = []
                for draw in draws:
                    states = (ticket_hit_state(ticket, draw) for ticket in group)
                    metrics = group_prize_metrics(game, rule, states)
                    prize_series.append(float(metrics["average_prize_yuan"]))
                    win_rate_series.append(float(metrics["win_rate"]))
                series_by_strategy_k[(strategy, k)] = (prize_series, win_rate_series)
                ci_low, ci_high = moving_block_bootstrap_ci(prize_series)
                mean_prize = fmean(prize_series)
                strategy_result[str(k)] = {
                    "ticket_count": k,
                    "mean_average_prize_yuan": mean_prize,
                    "bootstrap_95pct_ci_yuan": [ci_low, ci_high],
                    "mean_win_rate": fmean(win_rate_series),
                    "difference_vs_m0_uniform_yuan": 0.0,
                    "target_2_yuan_supported": ci_low <= 2.0 <= ci_high,
                    "scientific_status": "no_confirmed_lift",
                }
            game_result["strategies"][strategy] = strategy_result

        for strategy in STRATEGIES:
            for k in GROUP_SIZES:
                candidate = game_result["strategies"][strategy][str(k)]
                baseline = game_result["strategies"]["m0_uniform"][str(k)]
                candidate["difference_vs_m0_uniform_yuan"] = (
                    candidate["mean_average_prize_yuan"] - baseline["mean_average_prize_yuan"]
                )
        summary["games"][game] = game_result
    return summary


def main() -> None:
    summary = run_evaluation()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for game, game_result in summary["games"].items():
        print(
            f"{game.upper()} theoretical baseline: "
            f"{game_result['theoretical_baseline_average_prize_yuan']:.6f} yuan/ticket"
        )
        for strategy, sizes in game_result["strategies"].items():
            for k, result in sizes.items():
                low, high = result["bootstrap_95pct_ci_yuan"]
                print(
                    f"  {strategy:18s} K={int(k):5d} mean={result['mean_average_prize_yuan']:.6f} "
                    f"CI95=[{low:.6f}, {high:.6f}] "
                    f"delta_m0={result['difference_vs_m0_uniform_yuan']:+.6f} "
                    f"win_rate={result['mean_win_rate']:.6f}"
                )
    print(f"scientific_status={summary['scientific_status']}")
    print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
