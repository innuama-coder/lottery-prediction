"""Versioned parimutuel prize and expected-value models for SSQ and DLT.

The frozen fixed-prize contract remains authoritative for tier assignment and
all low-tier amounts.  This module replaces only tier-one and tier-two amounts
with a deliberately small, versioned parimutuel abstraction.

The parimutuel split is an assumption-bearing model abstraction, not an
assertion about the exact rules, deductions, caps, carryovers, or settlement
of either official lottery.  Likewise, the EV calculation is conditional on
its pool, bet-count, and popularity assumptions; it is not a return guarantee.
"""

from __future__ import annotations

import math
from typing import Final, Mapping

from .bonus import (
    DLT_FIXED_RULE,
    DLT_TIER_STATES,
    SSQ_FIXED_RULE,
    SSQ_TIER_STATES,
    fixed_bonus,
)
from .prize_metrics import full_space_oracle


SSQ_PRIZE_PARIMUTUEL_v1: Final = "SSQ_PRIZE_PARIMUTUEL_v1"
DLT_PRIZE_PARIMUTUEL_v1: Final = "DLT_PRIZE_PARIMUTUEL_v1"
PARIMUTUEL_TIERS: Final = {"ssq": {1, 2}, "dlt": {1, 2}}

_RULES: Final[Mapping[str, str]] = {
    "ssq": SSQ_PRIZE_PARIMUTUEL_v1,
    "dlt": DLT_PRIZE_PARIMUTUEL_v1,
}
_FIXED_RULES: Final[Mapping[str, str]] = {
    "ssq": SSQ_FIXED_RULE,
    "dlt": DLT_FIXED_RULE,
}
_TIER_STATES = {"ssq": SSQ_TIER_STATES, "dlt": DLT_TIER_STATES}
_SPACES: Final[Mapping[str, tuple[int, int, int, int]]] = {
    "ssq": (33, 6, 16, 1),
    "dlt": (35, 5, 12, 2),
}


def _validate_rule(game: str, rule_version: str) -> None:
    if game not in _RULES or rule_version != _RULES[game]:
        raise ValueError(f"unregistered parimutuel prize rule: {game}/{rule_version}")


def _positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_number(value: object, name: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def parimutuel_bonus(
    game: str,
    rule_version: str,
    front_hits: int,
    back_hits: int,
    *,
    tier1_pool: float,
    tier1_winners: int,
    tier2_pool: float,
    tier2_winners: int,
) -> dict[str, object]:
    """Return the modeled payout for one hit state, in whole yuan."""
    _validate_rule(game, rule_version)
    winners1 = _positive_integer(tier1_winners, "tier1_winners")
    winners2 = _positive_integer(tier2_winners, "tier2_winners")
    pool1 = _nonnegative_number(tier1_pool, "tier1_pool")
    pool2 = _nonnegative_number(tier2_pool, "tier2_pool")

    fixed = fixed_bonus(game, _FIXED_RULES[game], front_hits, back_hits)
    tier = fixed["prize_tier"]
    if tier == 1:
        prize = math.floor(pool1 / winners1)
    elif tier == 2:
        prize = math.floor(pool2 / winners2)
    else:
        prize = int(fixed["fixed_prize_yuan"])
    return {
        "prize_tier": tier,
        "prize_yuan": prize,
        "is_parimutuel": tier in PARIMUTUEL_TIERS[game],
    }


def tier_win_probability(game: str, tier: int) -> float:
    """Return a tier probability from the frozen full-space oracle."""
    if type(tier) is not int or tier <= 0:
        raise ValueError("tier must be a positive integer")
    oracle = full_space_oracle(game)
    try:
        count = oracle["tier_winning_counts"][str(tier)]
    except KeyError as exc:
        raise ValueError(f"unsupported prize tier: {game}/{tier}") from exc
    return count / oracle["total_ticket_count"]


def _hit_probability(game: str, front_hits: int, back_hits: int) -> float:
    front_n, front_k, back_n, back_k = _SPACES[game]
    front = (
        math.comb(front_k, front_hits)
        * math.comb(front_n - front_k, front_k - front_hits)
        / math.comb(front_n, front_k)
    )
    back = (
        math.comb(back_k, back_hits)
        * math.comb(back_n - back_k, back_k - back_hits)
        / math.comb(back_n, back_k)
    )
    return front * back


def expected_ticket_value(
    game: str,
    rule_version: str,
    *,
    tier1_pool: float,
    tier2_pool: float,
    total_bets: int,
    popularity_weight: float = 1.0,
) -> dict[str, float]:
    """Calculate conditional per-ticket EV under the documented model.

    ``popularity_weight`` describes the ticket's relative popularity versus a
    uniform pick: values below one model fewer co-winners and values above one
    model more co-winners.
    """
    _validate_rule(game, rule_version)
    bets = _positive_integer(total_bets, "total_bets")
    pool1 = _nonnegative_number(tier1_pool, "tier1_pool")
    pool2 = _nonnegative_number(tier2_pool, "tier2_pool")
    weight = _nonnegative_number(popularity_weight, "popularity_weight")

    low_ev = 0.0
    fixed_rule = _FIXED_RULES[game]
    for tier, states in _TIER_STATES[game].items():
        if tier in PARIMUTUEL_TIERS[game]:
            continue
        for front_hits, back_hits in states:
            amount = fixed_bonus(game, fixed_rule, front_hits, back_hits)[
                "fixed_prize_yuan"
            ]
            low_ev += _hit_probability(game, front_hits, back_hits) * amount

    p1 = tier_win_probability(game, 1)
    p2 = tier_win_probability(game, 2)
    ev1 = p1 * pool1 / ((bets - 1) * p1 * weight + 1)
    ev2 = p2 * pool2 / ((bets - 1) * p2 * weight + 1)
    return {"low_ev": low_ev, "ev1": ev1, "ev2": ev2, "total_ev": low_ev + ev1 + ev2}
