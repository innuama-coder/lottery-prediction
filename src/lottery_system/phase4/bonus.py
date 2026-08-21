"""Exact, routine-only SSQ and DLT fixed-prize rules.

The core calculation is deliberately closed over four inputs: game, registered
rule version, front-zone hit count, and back-zone hit count.  Arbitrary issue,
payout, promotion, or special-draw metadata is accepted only so callers can
prove that it has no effect; it is never inspected.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


SSQ_OLD_RULE: Final = "SSQ_PRIZE_2018_6TIER"
SSQ_NEW_RULE: Final = "SSQ_PRIZE_2026_BASE"
DLT_OLD_RULE: Final = "DLT_PRIZE_2019_9TIER"
DLT_NEW_RULE: Final = "DLT_PRIZE_2026_7TIER"


SSQ_FIXED_PRIZES: Final[Mapping[int, int]] = MappingProxyType(
    {1: 5_000_000, 2: 100_000, 3: 3_000, 4: 200, 5: 10, 6: 5}
)
DLT_OLD_FIXED_PRIZES: Final[Mapping[int, int]] = MappingProxyType(
    {1: 5_000_000, 2: 100_000, 3: 10_000, 4: 3_000, 5: 600, 6: 100, 7: 10, 8: 5, 9: 5}
)
DLT_NEW_FIXED_PRIZES: Final[Mapping[int, int]] = MappingProxyType(
    {1: 5_000_000, 2: 100_000, 3: 6_666, 4: 380, 5: 200, 6: 18, 7: 7}
)


SSQ_TIER_STATES: Final[Mapping[int, frozenset[tuple[int, int]]]] = MappingProxyType(
    {
        1: frozenset({(6, 1)}),
        2: frozenset({(6, 0)}),
        3: frozenset({(5, 1)}),
        4: frozenset({(5, 0), (4, 1)}),
        5: frozenset({(4, 0), (3, 1)}),
        6: frozenset({(3, 0), (2, 1), (1, 1), (0, 1)}),
    }
)

DLT_NEW_TIER_STATES: Final[Mapping[int, frozenset[tuple[int, int]]]] = MappingProxyType(
    {
        1: frozenset({(5, 2)}),
        2: frozenset({(5, 1)}),
        3: frozenset({(5, 0), (4, 2)}),
        4: frozenset({(4, 1), (3, 2)}),
        5: frozenset({(4, 0), (3, 1), (2, 2)}),
        6: frozenset({(3, 0), (2, 1), (1, 2), (0, 2)}),
        7: frozenset({(2, 0), (1, 1), (0, 1)}),
    }
)
DLT_OLD_TIER_STATES: Final[Mapping[int, frozenset[tuple[int, int]]]] = MappingProxyType(
    {
        1: frozenset({(5, 2)}),
        2: frozenset({(5, 1)}),
        3: frozenset({(5, 0), (4, 2)}),
        4: frozenset({(4, 1), (3, 2)}),
        5: frozenset({(4, 0), (3, 1), (2, 2)}),
        6: frozenset({(3, 0), (2, 1), (1, 2)}),
        7: frozenset({(2, 0), (1, 1), (0, 2)}),
        8: frozenset({(1, 0), (0, 1)}),
        9: frozenset(),
    }
)


_RULES: Final[Mapping[tuple[str, str], tuple[Mapping[int, frozenset[tuple[int, int]]], Mapping[int, int]]]] = MappingProxyType(
    {
        ("ssq", SSQ_OLD_RULE): (SSQ_TIER_STATES, SSQ_FIXED_PRIZES),
        ("ssq", SSQ_NEW_RULE): (SSQ_TIER_STATES, SSQ_FIXED_PRIZES),
        ("dlt", DLT_OLD_RULE): (DLT_OLD_TIER_STATES, DLT_OLD_FIXED_PRIZES),
        ("dlt", DLT_NEW_RULE): (DLT_NEW_TIER_STATES, DLT_NEW_FIXED_PRIZES),
    }
)

_HIT_BOUNDS: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    {"ssq": (6, 1), "dlt": (5, 2)}
)


def registered_rule_version(game: str, issue: str) -> str:
    """Resolve the repository's registered historical rule segment."""
    if game == "ssq":
        return SSQ_NEW_RULE if str(issue) >= "2026014" else SSQ_OLD_RULE
    if game == "dlt":
        return DLT_NEW_RULE if str(issue) >= "2026014" else DLT_OLD_RULE
    raise ValueError(f"unsupported game: {game}")


def fixed_bonus(
    game: str,
    rule_version: str,
    front_hits: int,
    back_hits: int,
    **_ignored_metadata: object,
) -> dict[str, object]:
    """Return one exact routine fixed prize, ignoring all draw metadata."""
    try:
        states, prizes = _RULES[(game, rule_version)]
    except KeyError as exc:
        raise ValueError(f"unregistered prize rule: {game}/{rule_version}") from exc
    if type(front_hits) is not int or type(back_hits) is not int:
        raise ValueError("hit counts must be integers")
    try:
        front_max, back_max = _HIT_BOUNDS[game]
    except KeyError as exc:
        raise ValueError(f"unsupported game: {game}") from exc
    if not 0 <= front_hits <= front_max or not 0 <= back_hits <= back_max:
        raise ValueError(
            f"hit counts outside {game} bounds: front={front_hits}, back={back_hits}"
        )
    hit_state = (front_hits, back_hits)
    matches = tuple(tier for tier, tier_states in states.items() if hit_state in tier_states)
    if len(matches) > 1:
        raise RuntimeError(f"overlapping prize tiers: {game}/{rule_version}/{hit_state}")
    tier = matches[0] if matches else None
    amount = prizes[tier] if tier is not None else 0
    return {
        "prize_tier": tier,
        "fixed_prize_yuan": amount,
        "is_floating_prize": False,
    }


def tier_states(game: str, rule_version: str) -> Mapping[int, frozenset[tuple[int, int]]]:
    """Expose the immutable registered table for audits and evidence."""
    try:
        return _RULES[(game, rule_version)][0]
    except KeyError as exc:
        raise ValueError(f"unregistered prize rule: {game}/{rule_version}") from exc
