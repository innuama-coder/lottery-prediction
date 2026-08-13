from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Iterable, Mapping, Sequence

from .rules import GameRule, RuleViolation, canonical_ticket, canonical_zone, game_rule, normalize_ticks, validate_tick_vector


DECIMAL_PRECISION = 80
SCALE = Decimal(1024)
ABSOLUTE_NORMALIZATION_TOLERANCE = Decimal("1e-45")
RELATIVE_NORMALIZATION_TOLERANCE = Decimal("1e-40")
PROBABILITY_QUANTUM = Decimal("1e-50")


class ProbabilityViolation(RuleViolation):
    pass


def _finite_positive(value: Decimal, name: str) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ProbabilityViolation(f"{name} must be finite and strictly positive")
    return value


def decimal_probability(value: Decimal) -> str:
    _finite_positive(value, "probability")
    try:
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            serialized = value.quantize(PROBABILITY_QUANTUM, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise ProbabilityViolation("probability cannot be serialized to 50 decimal places") from exc
    if serialized <= 0:
        raise ProbabilityViolation("50-place probability serialization underflowed to zero")
    return format(serialized, "f")


def tick_weights(ticks: Sequence[int]) -> tuple[Decimal, ...]:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        weights = tuple(_finite_positive((Decimal(tick) / SCALE).exp(), "tick weight") for tick in ticks)
    return weights


def elementary_symmetric(weights: Sequence[Decimal], k: int) -> Decimal:
    if not (0 < k <= len(weights)):
        raise ProbabilityViolation("partition cardinality is invalid")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        values = [Decimal(0)] * (k + 1)
        values[0] = Decimal(1)
        for index, weight in enumerate(weights, start=1):
            _finite_positive(weight, "partition weight")
            for chosen in range(min(index, k), 0, -1):
                values[chosen] += values[chosen - 1] * weight
        return _finite_positive(+values[k], "partition function")


@dataclass(frozen=True)
class ZoneDistribution:
    ticks: tuple[int, ...]
    k: int
    weights: tuple[Decimal, ...]
    partition: Decimal

    def score(self, ticket: Iterable[object]) -> int:
        values = canonical_zone(ticket, n=len(self.ticks), k=self.k, name="probability zone")
        return sum(self.ticks[number - 1] for number in values)

    def probability(self, ticket: Iterable[object]) -> Decimal:
        score = self.score(ticket)
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            return _finite_positive((Decimal(score) / SCALE).exp() / self.partition, "zone probability")

    def probability_for_score(self, score: int) -> Decimal:
        if type(score) is not int:
            raise ProbabilityViolation("tick score must be an integer")
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            return _finite_positive((Decimal(score) / SCALE).exp() / self.partition, "score probability")


def zone_distribution(ticks: Sequence[object], k: int) -> ZoneDistribution:
    canonical = validate_tick_vector(ticks, n=len(ticks))
    weights = tick_weights(canonical)
    return ZoneDistribution(canonical, k, weights, elementary_symmetric(weights, k))


@dataclass(frozen=True)
class P4E1Distribution:
    rule: GameRule
    model_contract_id: str
    front: ZoneDistribution
    back: ZoneDistribution

    @property
    def partition(self) -> Decimal:
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            return +(self.front.partition * self.back.partition)

    def joint_score(self, front: Iterable[object], back: Iterable[object]) -> int:
        canonical_front, canonical_back = canonical_ticket(self.rule.game, front, back)
        return self.front.score(canonical_front) + self.back.score(canonical_back)

    def probability_for_score(self, score: int) -> Decimal:
        if type(score) is not int or score < -28672 or score > 28672:
            raise ProbabilityViolation("joint tick score is outside the registered bound")
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            return _finite_positive((Decimal(score) / SCALE).exp() / self.partition, "joint probability")

    def probability(self, front: Iterable[object], back: Iterable[object]) -> Decimal:
        return self.probability_for_score(self.joint_score(front, back))


def distribution(
    game: str,
    front_ticks: Sequence[object],
    back_ticks: Sequence[object],
    *,
    model_contract_id: str,
    rule_id: str | None = None,
) -> P4E1Distribution:
    rule = game_rule(game, rule_id=rule_id)
    if not isinstance(model_contract_id, str) or not model_contract_id:
        raise ProbabilityViolation("model contract identity is required")
    front = zone_distribution(validate_tick_vector(front_ticks, n=rule.front_n), rule.front_k)
    back = zone_distribution(validate_tick_vector(back_ticks, n=rule.back_n), rule.back_k)
    return P4E1Distribution(rule, model_contract_id, front, back)


def normalization_proof(model: P4E1Distribution, histogram: Mapping[int, int]) -> dict[str, str]:
    if not histogram or any(type(score) is not int or type(count) is not int or count <= 0 for score, count in histogram.items()):
        raise ProbabilityViolation("normalization histogram is invalid")
    if sum(histogram.values()) != model.rule.space_size:
        raise ProbabilityViolation("normalization histogram does not cover the full legal space")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        total = sum((model.probability_for_score(score) * count for score, count in histogram.items()), Decimal(0))
        residual = abs(total - Decimal(1))
        allowed = max(ABSOLUTE_NORMALIZATION_TOLERANCE, RELATIVE_NORMALIZATION_TOLERANCE)
    if not total.is_finite() or residual > allowed:
        raise ProbabilityViolation("joint distribution does not normalize within the frozen tolerance")
    return {
        "decimal_precision": str(DECIMAL_PRECISION),
        "normalization": str(total),
        "absolute_residual": str(residual),
        "absolute_tolerance": str(ABSOLUTE_NORMALIZATION_TOLERANCE),
        "relative_tolerance": str(RELATIVE_NORMALIZATION_TOLERANCE),
    }


def estimate_ticks(
    draws: Sequence[Sequence[object]],
    *,
    n: int,
    k: int,
    shrinkage: int,
    training_window: int | str,
    recency_half_life: int | str,
    offsets: Mapping[int, int] | None = None,
) -> tuple[int, ...]:
    if shrinkage not in {1, 5, 20, 100}:
        raise ProbabilityViolation("P01 shrinkage is not registered")
    if training_window not in {50, 100, 150, "expanding"}:
        raise ProbabilityViolation("P02 training window is not registered")
    if recency_half_life not in {26, 52, 104, "none"}:
        raise ProbabilityViolation("P03 recency half-life is not registered")
    selected = list(draws if training_window == "expanding" else draws[-training_window:])
    if not selected:
        raise ProbabilityViolation("tick estimation requires a non-empty strict history prefix")
    canonical = [canonical_zone(row, n=n, k=k, name="historical draw zone") for row in selected]
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        counts = [Decimal(0)] * n
        total_weight = Decimal(0)
        for age, row in enumerate(reversed(canonical)):
            weight = Decimal(1) if recency_half_life == "none" else context.power(
                Decimal(2), -(Decimal(age) / Decimal(recency_half_life)),
            )
            total_weight += weight
            for number in row:
                counts[number - 1] += weight
        expected = total_weight * Decimal(k) / Decimal(n)
        denominator = max(Decimal(shrinkage) + expected, Decimal(1))
        raw = [
            int((SCALE * context.ln((count + Decimal(shrinkage)) / (expected + Decimal(shrinkage))) / denominator).to_integral_value(rounding=ROUND_HALF_EVEN))
            for count in counts
        ]
    if offsets:
        for number, offset in offsets.items():
            if type(number) is not int or not 1 <= number <= n or type(offset) is not int:
                raise ProbabilityViolation("P04 offsets must be registered integer number offsets")
            raw[number - 1] += offset
    return normalize_ticks(raw)
