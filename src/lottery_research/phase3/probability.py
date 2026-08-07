from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass
from typing import Iterable, Sequence


def _validate_cardinality(size: int, cardinality: int) -> None:
    if not isinstance(size, int) or not isinstance(cardinality, int):
        raise ValueError("size and cardinality must be integers")
    if size < 1 or cardinality < 1 or cardinality > size:
        raise ValueError("fixed-cardinality space is invalid")


def _elementary_symmetric(weights: Sequence[float], cardinality: int) -> float:
    values = [0.0] * (cardinality + 1)
    values[0] = 1.0
    for weight in weights:
        for degree in range(cardinality, 0, -1):
            values[degree] += weight * values[degree - 1]
    return values[cardinality]


@dataclass(frozen=True)
class FixedCardinalityDistribution:
    weights: tuple[float, ...]
    cardinality: int
    normalizer: float

    @classmethod
    def uniform(cls, size: int, cardinality: int) -> "FixedCardinalityDistribution":
        _validate_cardinality(size, cardinality)
        return cls(tuple(1.0 for _ in range(size)), cardinality, float(math.comb(size, cardinality)))

    @classmethod
    def from_theta(cls, theta: Sequence[float], cardinality: int) -> "FixedCardinalityDistribution":
        if not theta or any(not math.isfinite(float(value)) for value in theta):
            raise ValueError("theta must contain only finite values")
        _validate_cardinality(len(theta), cardinality)
        offset = max(float(value) for value in theta)
        return cls.from_weights([math.exp(float(value) - offset) for value in theta], cardinality)

    @classmethod
    def from_weights(cls, weights: Sequence[float], cardinality: int) -> "FixedCardinalityDistribution":
        if not weights:
            raise ValueError("weights must not be empty")
        numeric = tuple(float(value) for value in weights)
        if any(not math.isfinite(value) or value <= 0.0 for value in numeric):
            raise ValueError("weights must be finite and strictly positive")
        _validate_cardinality(len(numeric), cardinality)
        normalizer = _elementary_symmetric(numeric, cardinality)
        if not math.isfinite(normalizer) or normalizer <= 0.0:
            raise ValueError("probability normalizer is invalid")
        return cls(numeric, cardinality, normalizer)

    @property
    def size(self) -> int:
        return len(self.weights)

    @property
    def combination_count(self) -> int:
        return math.comb(self.size, self.cardinality)

    def is_legal(self, combination: Iterable[int]) -> bool:
        values = tuple(combination)
        return (
            len(values) == self.cardinality
            and len(set(values)) == self.cardinality
            and all(isinstance(value, int) and 1 <= value <= self.size for value in values)
        )

    def probability(self, combination: Iterable[int]) -> float:
        values = tuple(combination)
        if not self.is_legal(values):
            return 0.0
        numerator = math.prod(self.weights[value - 1] for value in values)
        probability = numerator / self.normalizer
        if not math.isfinite(probability) or probability < 0.0:
            raise ValueError("model emitted an illegal probability")
        return probability

    def inclusion_probabilities(self) -> tuple[float, ...]:
        answer = []
        for index, weight in enumerate(self.weights):
            remaining = self.weights[:index] + self.weights[index + 1 :]
            answer.append(weight * _elementary_symmetric(remaining, self.cardinality - 1) / self.normalizer)
        return tuple(answer)

    def normalization_audit(self) -> float:
        return math.fsum(self.probability(item) for item in itertools.combinations(range(1, self.size + 1), self.cardinality))

    def normalization_dp_audit(self) -> float:
        """Recompute the full-space partition function without enumerating tickets."""
        return _elementary_symmetric(self.weights, self.cardinality) / self.normalizer

    def top_k(self, count: int) -> list[tuple[tuple[int, ...], float]]:
        if count < 1:
            raise ValueError("top-k count must be positive")
        heap: list[tuple[float, tuple[int, ...]]] = []
        for item in itertools.combinations(range(1, self.size + 1), self.cardinality):
            probability = self.probability(item)
            candidate = (probability, item)
            if len(heap) < count:
                heapq.heappush(heap, candidate)
            elif candidate > heap[0]:
                heapq.heapreplace(heap, candidate)
        return [(item, probability) for probability, item in sorted(heap, reverse=True)]


@dataclass(frozen=True)
class PartitionedJointDistribution:
    front: FixedCardinalityDistribution
    back: FixedCardinalityDistribution

    @property
    def combination_count(self) -> int:
        return self.front.combination_count * self.back.combination_count

    def probability(self, front: Iterable[int], back: Iterable[int]) -> float:
        return self.front.probability(front) * self.back.probability(back)

    def normalization_audit(self) -> float:
        return self.front.normalization_audit() * self.back.normalization_audit()

    def top_k(self, count: int) -> list[dict[str, object]]:
        front = self.front.top_k(min(count, self.front.combination_count))
        back = self.back.top_k(min(count, self.back.combination_count))
        heap: list[tuple[float, tuple[int, ...], tuple[int, ...]]] = []
        for front_item, front_probability in front:
            for back_item, back_probability in back:
                probability = front_probability * back_probability
                candidate = (probability, front_item, back_item)
                if len(heap) < count:
                    heapq.heappush(heap, candidate)
                elif candidate > heap[0]:
                    heapq.heapreplace(heap, candidate)
        return [
            {"front": front_item, "back": back_item, "probability": probability}
            for probability, front_item, back_item in sorted(heap, reverse=True)
        ]


def joint_distribution(
    front: FixedCardinalityDistribution,
    back: FixedCardinalityDistribution,
) -> PartitionedJointDistribution:
    return PartitionedJointDistribution(front=front, back=back)


def posterior_theta(draws: Sequence[Iterable[int]], size: int, cardinality: int, shrinkage: float) -> tuple[float, ...]:
    """Strongly shrunk static log weights, fit only from the supplied training prefix."""
    _validate_cardinality(size, cardinality)
    if not math.isfinite(shrinkage) or shrinkage <= 0.0:
        raise ValueError("shrinkage must be finite and positive")
    counts = [0] * size
    for draw in draws:
        values = tuple(draw)
        if len(values) != cardinality or len(set(values)) != cardinality or any(value < 1 or value > size for value in values):
            raise ValueError("training draw is illegal")
        for value in values:
            counts[value - 1] += 1
    expected = (len(draws) * cardinality / size) if draws else 0.0
    scale = max(shrinkage + expected, 1.0)
    raw = [math.log((count + shrinkage) / (expected + shrinkage)) / scale for count in counts]
    mean = math.fsum(raw) / size
    return tuple(value - mean for value in raw)


def validate_projected_marginals(marginals: Sequence[float], cardinality: int) -> None:
    if any(not math.isfinite(float(value)) or value <= 0.0 or value >= 1.0 for value in marginals):
        raise ValueError("M4 marginals must be finite and strictly between zero and one")
    if not math.isclose(math.fsum(marginals), cardinality, rel_tol=0.0, abs_tol=1e-10):
        raise ValueError("M4 marginals lack a verified fixed-cardinality projection")
