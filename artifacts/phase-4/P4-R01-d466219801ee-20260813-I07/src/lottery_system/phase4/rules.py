from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb
from typing import Iterable, Iterator, Sequence


class RuleViolation(ValueError):
    exit_code = 20
    terminal = "HOLD_UNSUPPORTED_TIE_SEMANTICS"


@dataclass(frozen=True)
class GameRule:
    game: str
    rule_id: str
    front_n: int
    front_k: int
    back_n: int
    back_k: int

    @property
    def space_size(self) -> int:
        return comb(self.front_n, self.front_k) * comb(self.back_n, self.back_k)


RULES = {
    "ssq": GameRule("ssq", "ssq-ns-33c6-16c1-v1", 33, 6, 16, 1),
    "dlt": GameRule("dlt", "dlt-ns-35c5-12c2-v1", 35, 5, 12, 2),
}


def game_rule(game: str, *, rule_id: str | None = None) -> GameRule:
    try:
        rule = RULES[game]
    except KeyError as exc:
        raise RuleViolation(f"unregistered lottery game: {game}") from exc
    if rule_id is not None and rule_id != rule.rule_id:
        raise RuleViolation(f"rule identity does not match {game}")
    return rule


def canonical_zone(numbers: Iterable[object], *, n: int, k: int, name: str) -> tuple[int, ...]:
    values = tuple(numbers)
    if len(values) != k or any(type(value) is not int for value in values):
        raise RuleViolation(f"{name} must contain exactly {k} integers")
    if values != tuple(sorted(values)) or len(set(values)) != k:
        raise RuleViolation(f"{name} must be unique and strictly increasing")
    if any(value < 1 or value > n for value in values):
        raise RuleViolation(f"{name} contains a number outside 1..{n}")
    return values


def canonical_ticket(game: str, front: Iterable[object], back: Iterable[object]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    rule = game_rule(game)
    return (
        canonical_zone(front, n=rule.front_n, k=rule.front_k, name=f"{game} front zone"),
        canonical_zone(back, n=rule.back_n, k=rule.back_k, name=f"{game} back zone"),
    )


def legal_zone_combinations(n: int, k: int) -> Iterator[tuple[int, ...]]:
    if type(n) is not int or type(k) is not int or not (0 < k <= n):
        raise RuleViolation("zone dimensions are invalid")
    return combinations(range(1, n + 1), k)


def validate_tick_vector(values: Sequence[object], *, n: int, require_anchor: bool = True) -> tuple[int, ...]:
    if len(values) != n or any(type(value) is not int for value in values):
        raise RuleViolation(f"tick vector must contain exactly {n} integers")
    ticks = tuple(values)  # type: ignore[arg-type]
    if require_anchor and ticks[0] != 0:
        raise RuleViolation("canonical tick vector must anchor number 1 at zero")
    if any(value < -4096 or value > 4096 for value in ticks):
        raise RuleViolation("normalized tick is outside [-4096,4096]")
    return ticks


def normalize_ticks(raw_ticks: Sequence[object]) -> tuple[int, ...]:
    if not raw_ticks or any(type(value) is not int for value in raw_ticks):
        raise RuleViolation("raw tick vector must be a non-empty integer sequence")
    anchor = raw_ticks[0]
    normalized = tuple(value - anchor for value in raw_ticks)  # type: ignore[operator]
    return validate_tick_vector(normalized, n=len(normalized))
