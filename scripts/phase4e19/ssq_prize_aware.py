#!/usr/bin/env python3
"""SSQ-only prize-aware scoring primitives for Phase4E19.

This module deliberately contains no DLT imports or serving mutations. Model fitting
and walk-forward orchestration will be added on top of these deterministic pieces.
"""
from __future__ import annotations

import itertools
import math
from typing import Iterable, Sequence

SSQ_PARTITION_SIZES = (1000, 5000, 10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000, 90000, 100000)
SSQ_FIXED_PRIZES = {1: 5_000_000.0, 2: 100_000.0, 3: 3_000.0, 4: 200.0, 5: 10.0, 6: 5.0}


def prize_tier(red_hits: int, blue_hits: int) -> int | None:
    pattern = (int(red_hits), int(blue_hits))
    patterns = {
        1: {(6, 1)},
        2: {(6, 0)},
        3: {(5, 1)},
        4: {(5, 0), (4, 1)},
        5: {(4, 0), (3, 1)},
        6: {(3, 0), (2, 1), (1, 1), (0, 1)},
    }
    return next((tier for tier, values in patterns.items() if pattern in values), None)


def ticket_prize(red: Sequence[int], blue: int, actual_red: Iterable[int], actual_blue: int) -> float:
    tier = prize_tier(len(set(red) & set(actual_red)), int(blue == actual_blue))
    return SSQ_FIXED_PRIZES.get(tier, 0.0)


def ranked_ticket_partitions(
    red_scores: Sequence[float], blue_scores: Sequence[float], actual_red: Iterable[int], actual_blue: int,
    partition_sizes: Sequence[int] = SSQ_PARTITION_SIZES,
) -> dict[int, dict[str, float | int]]:
    """Rank complete SSQ tickets by additive expected-score proxy and aggregate prize."""
    red = sorted(
        (math.fsum(float(red_scores[n - 1]) for n in combo), tuple(combo))
        for combo in itertools.combinations(range(1, 34), 6)
    )
    red.sort(key=lambda item: (-item[0], item[1]))
    blue = sorted(((float(blue_scores[n - 1]), n) for n in range(1, 17)), key=lambda item: (-item[0], item[1]))
    ranked = sorted(
        (rscore + bscore, rcombo, bnumber)
        for rscore, rcombo in red[: max(partition_sizes)]
        for bscore, bnumber in blue
    )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    result = {}
    total = 0.0
    winners = 0
    for rank, (_, rcombo, bnumber) in enumerate(ranked[: max(partition_sizes)], start=1):
        prize = ticket_prize(rcombo, bnumber, actual_red, actual_blue)
        total += prize
        winners += int(prize > 0)
        if rank in partition_sizes:
            result[rank] = {
                "partition_size": rank,
                "known_prize_total_yuan": total,
                "average_prize_yuan": total / rank,
                "winning_ticket_count": winners,
            }
    return result


def acceptance_gate(averages: Sequence[float], threshold: float = 2.0) -> dict[str, object]:
    values = [float(value) for value in averages]
    return {
        "metric": "average_prize_yuan",
        "threshold_yuan_per_ticket": float(threshold),
        "comparison": "strictly_greater_than",
        "passed": bool(values) and all(value > threshold for value in values),
        "minimum_observed_average_prize_yuan": min(values) if values else None,
    }
