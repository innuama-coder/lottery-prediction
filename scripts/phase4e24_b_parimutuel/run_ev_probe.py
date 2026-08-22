#!/usr/bin/env python3
"""Run the offline Phase4E24 B0 EV threshold probe and write JSON output."""

from __future__ import annotations

import json
import math
from pathlib import Path

from lottery_system.phase4.parimutuel import (
    DLT_PRIZE_PARIMUTUEL_v1,
    SSQ_PRIZE_PARIMUTUEL_v1,
    expected_ticket_value,
    tier_win_probability,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/phase4e24_b_parimutuel/ev-probe.json"
TARGET_EV = 2.0
TOTAL_BETS = 100_000_000
TIER2_POOL = 100_000_000
WEIGHTS = (0.5, 1.0, 2.0)


def probe_game(game: str, rule: str) -> dict[str, object]:
    baseline = expected_ticket_value(
        game,
        rule,
        tier1_pool=0,
        tier2_pool=TIER2_POOL,
        total_bets=TOTAL_BETS,
    )
    p1 = tier_win_probability(game, 1)
    rows = []
    for weight in WEIGHTS:
        without_tier1 = expected_ticket_value(
            game,
            rule,
            tier1_pool=0,
            tier2_pool=TIER2_POOL,
            total_bets=TOTAL_BETS,
            popularity_weight=weight,
        )
        denominator = (TOTAL_BETS - 1) * p1 * weight + 1
        required = max(0.0, (TARGET_EV - without_tier1["total_ev"]) * denominator / p1)
        pool = math.ceil(required)
        result = expected_ticket_value(
            game,
            rule,
            tier1_pool=pool,
            tier2_pool=TIER2_POOL,
            total_bets=TOTAL_BETS,
            popularity_weight=weight,
        )
        rows.append(
            {
                "tier1_pool_yuan": pool,
                "popularity_weight": weight,
                "total_ev_yuan": result["total_ev"],
                "meets_target": result["total_ev"] >= TARGET_EV,
            }
        )
    return {
        "rule_version": rule,
        "low_fixed_tier_ev_yuan": baseline["low_ev"],
        "threshold_combinations": rows,
    }


def main() -> None:
    payload = {
        "model": "phase4e24-b0-parimutuel-ev-v1",
        "target_total_ev_yuan": TARGET_EV,
        "probe_assumptions": {
            "total_bets": TOTAL_BETS,
            "tier2_pool_yuan": TIER2_POOL,
            "popularity_weights": list(WEIGHTS),
            "external_data_used": False,
        },
        "games": {
            "ssq": probe_game("ssq", SSQ_PRIZE_PARIMUTUEL_v1),
            "dlt": probe_game("dlt", DLT_PRIZE_PARIMUTUEL_v1),
        },
        "interpretation": (
            "Conditional model thresholds only; they are not claims that such pools "
            "or popularity differences are realistic or that a ticket will return 2 yuan."
        ),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
