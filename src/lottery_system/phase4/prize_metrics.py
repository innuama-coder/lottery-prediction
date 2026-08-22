"""Frozen group-prize metrics for SSQ and DLT.

This module is the single reusable entry for prediction backtests and group
prize summaries. Every per-ticket decision is delegated to
`bonus.fixed_bonus`; no tier table or amount is re-implemented here. Each game
is evaluated only against its own frozen fixed rule and its own canonical
full-space dimensions, so SSQ and DLT remain isolated.

Contract (`docs/phase4/prize-calculation-contract.md`) definitions:

    分组奖金总额 = Σ 单注固定奖金
    分组平均奖金 = 分组奖金总额 / 完整注数
    中奖率       = 获得任一固定奖级的完整注数 / 完整注数

The input is always per-ticket `(front_hits, back_hits)`; no floating-prize
field is accepted. Unregistered rule versions, unknown games, empty partitions
and malformed hit states fail closed. There is exactly one fixed policy per
game, so no "new rule / old rule" switch exists for DLT.
"""

from __future__ import annotations

from math import comb
from typing import Final, Iterable, Mapping

from .bonus import (
    BONUS_CONTRACT_FINGERPRINT,
    DLT_FIXED_RULE,
    SSQ_FIXED_RULE,
    fixed_bonus,
)

# Canonical full-space dimensions per game (frozen alongside the prize tables).
# These describe the lottery combination space consumed by the independent
# full-space oracle -- SSQ is 33-choose-6 front x 16-choose-1 back, DLT is
# 35-choose-5 front x 12-choose-2 back. Hit-count bounds are enforced by
# `bonus.fixed_bonus`; these values are used only to weight each hit state by
# its combinatorial multiplicity.
_GAME_SPACE: Final[Mapping[str, tuple[int, int, int, int]]] = {
    "ssq": (33, 6, 16, 1),
    "dlt": (35, 5, 12, 2),
}

_GAME_FIXED_RULE: Final[Mapping[str, str]] = {
    "ssq": SSQ_FIXED_RULE,
    "dlt": DLT_FIXED_RULE,
}

# Frozen independent full-space oracle acceptance values from the contract.
FROZEN_ORACLE: Final[Mapping[str, Mapping[str, int]]] = {
    "ssq": {
        "ticket_count": 17_721_088,
        "winning_ticket_count": 1_188_988,
        "prize_total_yuan": 15_117_950,
    },
    "dlt": {
        "ticket_count": 21_425_712,
        "winning_ticket_count": 1_429_197,
        "prize_total_yuan": 18_890_405,
    },
}


class PrizeMetricsViolation(ValueError):
    """Raised when group-prize inputs fail closed."""


def _fixed_rule(game: str) -> str:
    try:
        return _GAME_FIXED_RULE[game]
    except KeyError as exc:
        raise PrizeMetricsViolation(f"unsupported game: {game}") from exc


def _validate_hit_state(state: object) -> tuple[int, int]:
    if type(state) not in {tuple, list} or len(state) != 2:
        raise PrizeMetricsViolation(f"invalid hit state: {state!r}")
    front_hits, back_hits = state
    if type(front_hits) is not int or type(back_hits) is not int:
        raise PrizeMetricsViolation(f"hit counts must be integers: {state!r}")
    return front_hits, back_hits


def group_prize_metrics(
    game: str,
    rule_version: str,
    hit_states: Iterable[object],
) -> dict[str, object]:
    """Compute frozen group-prize metrics for an explicit ticket partition.

    `hit_states` is an iterable of per-ticket `(front_hits, back_hits)` pairs.
    Each pair is evaluated through `bonus.fixed_bonus`; a ticket contributes
    its fixed prize only when its hit state is a registered prize state,
    otherwise it contributes 0 yuan.
    """
    expected_rule = _fixed_rule(game)
    if rule_version != expected_rule:
        raise PrizeMetricsViolation(f"unregistered prize rule: {game}/{rule_version}")
    states = list(hit_states)
    if not states:
        raise PrizeMetricsViolation("hit states must not be empty")

    prize_total = 0
    winning = 0
    tier_counts: dict[int, int] = {}
    for raw_state in states:
        front_hits, back_hits = _validate_hit_state(raw_state)
        # `fixed_bonus` enforces hit-count bounds and performs the single
        # authoritative tier lookup; its ValueError propagates unchanged.
        result = fixed_bonus(game, rule_version, front_hits, back_hits)
        prize_total += int(result["fixed_prize_yuan"])
        tier = result["prize_tier"]
        if tier is not None:
            winning += 1
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

    ticket_count = len(states)
    return {
        "game": game,
        "rule_version": rule_version,
        "ticket_count": ticket_count,
        "winning_ticket_count": winning,
        "prize_total_yuan": prize_total,
        "average_prize_yuan": prize_total / ticket_count,
        "win_rate": winning / ticket_count,
        "prize_tier_ticket_counts": {
            str(tier): count for tier, count in sorted(tier_counts.items())
        },
        "bonus_contract_fingerprint": BONUS_CONTRACT_FINGERPRINT,
    }


def full_space_oracle(game: str) -> dict[str, object]:
    """Independent full-space enumeration of the fixed prize over every ticket.

    Sums `bonus.fixed_bonus` over each hit-state combinatorial multiplicity so
    the totals depend only on the frozen tables and the canonical combination
    space. The computed values must equal `FROZEN_ORACLE[game]`.
    """
    rule_version = _fixed_rule(game)
    front_n, front_k, back_n, back_k = _GAME_SPACE[game]
    total = comb(front_n, front_k) * comb(back_n, back_k)

    prize_total = 0
    winning = 0
    tier_counts: dict[int, int] = {}
    for front_hits in range(front_k + 1):
        front_mult = comb(front_k, front_hits) * comb(front_n - front_k, front_k - front_hits)
        for back_hits in range(back_k + 1):
            back_mult = comb(back_k, back_hits) * comb(back_n - back_k, back_k - back_hits)
            multiplicity = front_mult * back_mult
            result = fixed_bonus(game, rule_version, front_hits, back_hits)
            prize_total += multiplicity * int(result["fixed_prize_yuan"])
            tier = result["prize_tier"]
            if tier is not None:
                winning += multiplicity
                tier_counts[tier] = tier_counts.get(tier, 0) + multiplicity

    expected = dict(FROZEN_ORACLE[game])
    return {
        "game": game,
        "rule_version": rule_version,
        "total_ticket_count": total,
        "winning_ticket_count": winning,
        "fixed_prize_total_yuan": prize_total,
        "tier_winning_counts": {
            str(tier): count for tier, count in sorted(tier_counts.items())
        },
        "expected": expected,
        "matches_frozen_oracle": (
            total == expected["ticket_count"]
            and winning == expected["winning_ticket_count"]
            and prize_total == expected["prize_total_yuan"]
        ),
        "bonus_contract_fingerprint": BONUS_CONTRACT_FINGERPRINT,
    }
