"""Single authoritative routine-prize rules for SSQ and DLT.

This module is deliberately independent of jackpot balances, promotions,
special draws, and issue-specific payout metadata. Each game has exactly one
frozen routine-prize policy; the issue is validated for data integrity but does
not select a different prize table.
"""

from __future__ import annotations

import hashlib
import json
import re
from types import MappingProxyType
from typing import Final, Mapping


SSQ_FIXED_RULE: Final = "SSQ_PRIZE_FIXED_6TIER"
DLT_FIXED_RULE: Final = "DLT_PRIZE_FIXED_7TIER"

SSQ_FIXED_PRIZES: Final[Mapping[int, int]] = MappingProxyType(
    {1: 5_000_000, 2: 100_000, 3: 3_000, 4: 200, 5: 10, 6: 5}
)
DLT_FIXED_PRIZES: Final[Mapping[int, int]] = MappingProxyType(
    {1: 5_000_000, 2: 100_000, 3: 5_000, 4: 300, 5: 150, 6: 15, 7: 5}
)

SSQ_TIER_STATES: Final[Mapping[int, frozenset[tuple[int, int]]]] = MappingProxyType(
    {
        1: frozenset({(6, 1)}),
        2: frozenset({(6, 0)}),
        3: frozenset({(5, 1)}),
        4: frozenset({(5, 0), (4, 1)}),
        5: frozenset({(4, 0), (3, 1)}),
        6: frozenset({(2, 1), (1, 1), (0, 1)}),
    }
)

DLT_TIER_STATES: Final[Mapping[int, frozenset[tuple[int, int]]]] = MappingProxyType(
    {
        1: frozenset({(5, 2)}),
        2: frozenset({(5, 1)}),
        3: frozenset({(5, 0), (4, 2)}),
        4: frozenset({(4, 1)}),
        5: frozenset({(4, 0), (3, 2)}),
        6: frozenset({(3, 1), (2, 2)}),
        7: frozenset({(3, 0), (2, 1), (1, 2), (0, 2)}),
    }
)

_RULES: Final[Mapping[tuple[str, str], tuple[Mapping[int, frozenset[tuple[int, int]]], Mapping[int, int]]]] = MappingProxyType(
    {
        ("ssq", SSQ_FIXED_RULE): (SSQ_TIER_STATES, SSQ_FIXED_PRIZES),
        ("dlt", DLT_FIXED_RULE): (DLT_TIER_STATES, DLT_FIXED_PRIZES),
    }
)
_HIT_BOUNDS: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    {"ssq": (6, 1), "dlt": (5, 2)}
)
_ISSUE_PATTERN = re.compile(r"\A\d{7}\Z")


def _validate_rule_registry() -> None:
    """Fail fast if a rule table is internally inconsistent."""
    for (game, _version), (states, prizes) in _RULES.items():
        if game not in _HIT_BOUNDS:
            raise RuntimeError(f"rule registry has unsupported game: {game}")
        front_max, back_max = _HIT_BOUNDS[game]
        seen: set[tuple[int, int]] = set()
        for tier, tier_states in states.items():
            if type(tier) is not int or tier <= 0:
                raise RuntimeError(f"invalid prize tier: {game}/{tier!r}")
            amount = prizes.get(tier)
            if type(amount) is not int or amount < 0:
                raise RuntimeError(f"invalid prize amount: {game}/{tier}")
            for state in tier_states:
                if (
                    type(state) is not tuple
                    or len(state) != 2
                    or type(state[0]) is not int
                    or type(state[1]) is not int
                    or not 0 <= state[0] <= front_max
                    or not 0 <= state[1] <= back_max
                ):
                    raise RuntimeError(f"invalid prize state: {game}/{tier}/{state!r}")
                if state in seen:
                    raise RuntimeError(f"overlapping prize states: {game}/{state}")
                seen.add(state)
        if set(states) != set(prizes):
            raise RuntimeError(f"tier/amount mismatch: {game}/{_version}")


_validate_rule_registry()


def _contract_payload() -> dict[str, object]:
    rows = []
    for (game, version), (states, prizes) in sorted(_RULES.items()):
        rows.append(
            {
                "game": game,
                "rule_version": version,
                "states": {
                    str(tier): sorted(list(tier_states))
                    for tier, tier_states in states.items()
                },
                "prizes": {str(tier): amount for tier, amount in prizes.items()},
            }
        )
    return {"contract": "phase4-routine-fixed-prizes-v2-single-policy", "rules": rows}


BONUS_CONTRACT_FINGERPRINT: Final = hashlib.sha256(
    json.dumps(_contract_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def registered_rule_version(game: str, issue: str) -> str:
    if game not in _HIT_BOUNDS:
        raise ValueError(f"unsupported game: {game}")
    if type(issue) is not str or _ISSUE_PATTERN.fullmatch(issue) is None:
        raise ValueError("issue must be a valid YYYYNNN string")
    year, sequence = int(issue[:4]), int(issue[4:])
    if not 2000 <= year <= 2099 or not 1 <= sequence <= 999:
        raise ValueError("issue must be a valid YYYYNNN string")
    return SSQ_FIXED_RULE if game == "ssq" else DLT_FIXED_RULE


def fixed_bonus(
    game: str,
    rule_version: str,
    front_hits: int,
    back_hits: int,
    **_ignored_metadata: object,
) -> dict[str, object]:
    try:
        states, prizes = _RULES[(game, rule_version)]
    except KeyError as exc:
        raise ValueError(f"unregistered prize rule: {game}/{rule_version}") from exc
    if type(front_hits) is not int or type(back_hits) is not int:
        raise ValueError("hit counts must be integers")
    front_max, back_max = _HIT_BOUNDS[game]
    if not 0 <= front_hits <= front_max or not 0 <= back_hits <= back_max:
        raise ValueError(
            f"hit counts outside {game} bounds: front={front_hits}, back={back_hits}"
        )
    state = (front_hits, back_hits)
    matches = tuple(tier for tier, tier_states in states.items() if state in tier_states)
    if len(matches) > 1:
        raise RuntimeError(f"overlapping prize tiers: {game}/{rule_version}/{state}")
    tier = matches[0] if matches else None
    return {
        "prize_tier": tier,
        "fixed_prize_yuan": prizes[tier] if tier is not None else 0,
        "is_floating_prize": False,
    }


def tier_states(game: str, rule_version: str) -> Mapping[int, frozenset[tuple[int, int]]]:
    try:
        return _RULES[(game, rule_version)][0]
    except KeyError as exc:
        raise ValueError(f"unregistered prize rule: {game}/{rule_version}") from exc


def prize_table(game: str, rule_version: str) -> Mapping[int, int]:
    try:
        return _RULES[(game, rule_version)][1]
    except KeyError as exc:
        raise ValueError(f"unregistered prize rule: {game}/{rule_version}") from exc
