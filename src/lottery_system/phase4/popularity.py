"""Assumption-bearing lottery-number popularity proxy.

The proxy represents a possible birthday-number preference in player picks; it
does not describe draw probabilities or an observed ticket-level distribution.
"""

from __future__ import annotations

import math
from typing import Iterable


_TICKET_SPACES = {
    (6, 1): (33, 16),  # SSQ
    (5, 2): (35, 12),  # DLT
}


def _validated_bias(birthday_bias: float) -> float:
    if type(birthday_bias) not in {int, float}:
        raise ValueError("birthday_bias must be a finite number in [0, 1)")
    bias = float(birthday_bias)
    if not math.isfinite(bias) or not 0 <= bias < 1:
        raise ValueError("birthday_bias must be a finite number in [0, 1)")
    return bias


def number_popularity_weight(
    number: int, zone: str, *, birthday_bias: float = 0.2
) -> float:
    """Return a per-number relative weight over the union of SSQ/DLT spaces."""
    bias = _validated_bias(birthday_bias)
    if zone not in {"front", "back"}:
        raise ValueError("zone must be 'front' or 'back'")
    if type(number) is not int:
        raise ValueError("number must be an integer")
    upper = 35 if zone == "front" else 16
    if not 1 <= number <= upper:
        raise ValueError(f"{zone} number must be in 1..{upper}")
    if zone == "back":
        return 1.0
    return 1.0 + bias if number <= 31 else 1.0 - bias


def _validated_numbers(numbers: Iterable[int], count: int, upper: int, zone: str) -> tuple[int, ...]:
    try:
        values = tuple(numbers)
    except TypeError as exc:
        raise ValueError(f"{zone}_numbers must be iterable") from exc
    if len(values) != count:
        raise ValueError(f"{zone}_numbers must contain exactly {count} numbers")
    if any(type(value) is not int for value in values):
        raise ValueError(f"{zone}_numbers must contain integers")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{zone}_numbers must be strictly ascending and unique")
    if any(not 1 <= value <= upper for value in values):
        raise ValueError(f"{zone}_numbers must be in 1..{upper}")
    return values


def ticket_popularity_weight(
    front_numbers: Iterable[int],
    back_numbers: Iterable[int],
    *,
    birthday_bias: float = 0.2,
) -> float:
    """Return ticket popularity relative to the mean ticket approximation.

    The supported ticket shapes are the frozen SSQ 6+1 and DLT 5+2 spaces.
    Back-zone choices are neutral in this deliberately narrow proxy.
    """
    bias = _validated_bias(birthday_bias)
    try:
        front = tuple(front_numbers)
        back = tuple(back_numbers)
    except TypeError as exc:
        raise ValueError("ticket numbers must be iterable") from exc
    try:
        front_n, back_n = _TICKET_SPACES[(len(front), len(back))]
    except KeyError as exc:
        raise ValueError("ticket shape must be SSQ 6+1 or DLT 5+2") from exc
    front = _validated_numbers(front, len(front), front_n, "front")
    _validated_numbers(back, len(back), back_n, "back")

    mean_front = sum(
        number_popularity_weight(number, "front", birthday_bias=bias)
        for number in range(1, front_n + 1)
    ) / front_n
    product = math.prod(
        number_popularity_weight(number, "front", birthday_bias=bias)
        for number in front
    )
    return product / (mean_front ** len(front))
