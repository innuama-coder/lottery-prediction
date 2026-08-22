#!/usr/bin/env python3
"""Evaluate the birthday-popularity proxy against the frozen B1 DLT sample."""

from __future__ import annotations

import json
from pathlib import Path

from lottery_system.phase4.parimutuel import (
    DLT_PRIZE_PARIMUTUEL_v1,
    expected_ticket_value,
    tier_win_probability,
)
from lottery_system.phase4.popularity import ticket_popularity_weight


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "artifacts/phase4e25_b1_dlt_pool_data/dlt-draws.jsonl"
OUTPUT = ROOT / "artifacts/phase4e26_b2_popularity/ev-by-popularity.json"
BIAS = 0.2
TARGET_EV = 2.0


def _pool(draw: dict[str, object], prefix: str) -> int:
    return sum(
        int(tier["prize_per_ticket_yuan"]) * int(tier["winners"])
        for tier in draw["tiers"]
        if str(tier["tier"]).startswith(prefix)
    )


def _tier1_threshold(*, tier2_pool: int, total_bets: int, weight: float) -> float:
    zero_tier1 = expected_ticket_value(
        "dlt",
        DLT_PRIZE_PARIMUTUEL_v1,
        tier1_pool=0,
        tier2_pool=tier2_pool,
        total_bets=total_bets,
        popularity_weight=weight,
    )
    residual = TARGET_EV - zero_tier1["total_ev"]
    if residual <= 0:
        return 0.0
    p1 = tier_win_probability("dlt", 1)
    denominator = (total_bets - 1) * p1 * weight + 1
    return residual * denominator / p1


def main() -> None:
    draws = [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines() if line]
    if len(draws) != 20:
        raise ValueError(f"expected 20 B1 draws, got {len(draws)}")

    tickets = {
        "uniform": {"front_numbers": None, "back_numbers": [1, 2], "popularity_weight": 1.0},
        "birthday": {
            "front_numbers": [1, 2, 3, 4, 5],
            "back_numbers": [1, 2],
            "popularity_weight": ticket_popularity_weight((1, 2, 3, 4, 5), (1, 2), birthday_bias=BIAS),
        },
        "anti_birthday": {
            "front_numbers": [31, 32, 33, 34, 35],
            "back_numbers": [1, 2],
            "popularity_weight": ticket_popularity_weight((31, 32, 33, 34, 35), (1, 2), birthday_bias=BIAS),
        },
    }
    rows = []
    thresholds = []
    for draw in draws:
        tier1_pool = _pool(draw, "一等奖")
        tier2_pool = _pool(draw, "二等奖")
        total_bets = int(draw["national_sales_yuan"]) // 2
        results = {}
        for name, ticket in tickets.items():
            ev = expected_ticket_value(
                "dlt",
                DLT_PRIZE_PARIMUTUEL_v1,
                tier1_pool=tier1_pool,
                tier2_pool=tier2_pool,
                total_bets=total_bets,
                popularity_weight=ticket["popularity_weight"],
            )
            results[name] = {
                "popularity_weight": ticket["popularity_weight"],
                "total_ev": ev["total_ev"],
            }
        threshold = _tier1_threshold(
            tier2_pool=tier2_pool,
            total_bets=total_bets,
            weight=tickets["anti_birthday"]["popularity_weight"],
        )
        thresholds.append(threshold)
        rows.append(
            {
                "issue_id": draw["issue_id"],
                "national_sales_yuan": draw["national_sales_yuan"],
                "pool_rollover_yuan": draw["pool_rollover_yuan"],
                "tier1_pool_yuan": tier1_pool,
                "tier2_pool_yuan": tier2_pool,
                "total_bets": total_bets,
                "tickets": results,
                "anti_birthday_tier1_pool_threshold_for_2_yuan": threshold,
                "additional_tier1_pool_needed_yuan": max(0.0, threshold - tier1_pool),
            }
        )

    summary = {}
    for name in tickets:
        values = [row["tickets"][name]["total_ev"] for row in rows]
        summary[name] = {
            "popularity_weight": tickets[name]["popularity_weight"],
            "mean_total_ev": sum(values) / len(values),
            "min_total_ev": min(values),
            "max_total_ev": max(values),
        }
    summary["ev_differences"] = {
        "birthday_minus_uniform_mean": summary["birthday"]["mean_total_ev"] - summary["uniform"]["mean_total_ev"],
        "anti_birthday_minus_uniform_mean": summary["anti_birthday"]["mean_total_ev"] - summary["uniform"]["mean_total_ev"],
        "anti_birthday_minus_birthday_mean": summary["anti_birthday"]["mean_total_ev"] - summary["birthday"]["mean_total_ev"],
    }
    achieved = any(
        result["total_ev"] >= TARGET_EV
        for row in rows
        for result in row["tickets"].values()
    )
    payload = {
        "model": {
            "game": "dlt",
            "rule_version": DLT_PRIZE_PARIMUTUEL_v1,
            "birthday_bias": BIAS,
            "birthday_bias_is_assumption": True,
            "target_ev_yuan": TARGET_EV,
            "ticket_price_yuan": 2,
            "pool_definition": "sum(prize_per_ticket_yuan * winners) for all matching base/add-on tier labels",
            "limitations": "Popularity is a birthday-effect proxy, not an observed DLT betting distribution or a return guarantee.",
        },
        "representative_tickets": tickets,
        "draws": rows,
        "summary": summary,
        "target_analysis": {
            "any_observed_ticket_draw_total_ev_at_least_2": achieved,
            "minimum_anti_birthday_tier1_pool_threshold_yuan": min(thresholds),
            "maximum_anti_birthday_tier1_pool_threshold_yuan": max(thresholds),
            "interpretation": "Counterfactual tier-1 pool thresholds conditional on each draw's sales and tier-2 pool; not a claim that rollover becomes tier-1 payout or that 2 yuan EV will be reached.",
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(payload["target_analysis"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
