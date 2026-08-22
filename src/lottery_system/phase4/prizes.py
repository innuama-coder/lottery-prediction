"""Compatibility API backed by :mod:`lottery_system.phase4.bonus`.

There is intentionally no second prize table here. All callers resolve the
same immutable rule registry used by the Phase-4 production/replay path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Mapping

from .bonus import (
    DLT_FIXED_PRIZES,
    DLT_FIXED_RULE,
    SSQ_FIXED_PRIZES,
    SSQ_FIXED_RULE,
    fixed_bonus,
)


class PrizeRuleViolation(ValueError):
    """Raised when a hit vector or rule version is not registered."""


@dataclass(frozen=True)
class PrizeAward:
    game: str
    rule_version: str
    tier: int | None
    amount_yuan: int

    @property
    def won(self) -> bool:
        return self.tier is not None


SSQ_PRIZES: Final[Mapping[int, int]] = SSQ_FIXED_PRIZES
DLT_PRIZES: Final[Mapping[int, int]] = DLT_FIXED_PRIZES


def _calculate(game: str, rule_version: str, front_hits: int, back_hits: int) -> PrizeAward:
    try:
        result = fixed_bonus(game, rule_version, front_hits, back_hits)
    except ValueError as exc:
        raise PrizeRuleViolation(str(exc)) from exc
    return PrizeAward(
        game,
        rule_version,
        result["prize_tier"],
        result["fixed_prize_yuan"],
    )


def calculate_ssq_prize(red_hits: int, blue_hits: int) -> PrizeAward:
    return _calculate("ssq", SSQ_FIXED_RULE, red_hits, blue_hits)


def calculate_dlt_prize(
    front_hits: int,
    back_hits: int,
    *,
    rule_version: str = DLT_FIXED_RULE,
) -> PrizeAward:
    return _calculate("dlt", rule_version, front_hits, back_hits)


def calculate_prize(
    game: str,
    front_hits: int,
    back_hits: int,
    *,
    rule_version: str | None = None,
) -> PrizeAward:
    if game == "ssq":
        return _calculate("ssq", rule_version or SSQ_FIXED_RULE, front_hits, back_hits)
    if game == "dlt":
        return _calculate("dlt", rule_version or DLT_FIXED_RULE, front_hits, back_hits)
    raise PrizeRuleViolation(f"unsupported game: {game}")
