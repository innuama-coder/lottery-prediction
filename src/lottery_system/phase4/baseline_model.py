"""Sequence-safe per-number logistic baseline for Phase4E31.

The twelve registered feature *families* produce thirteen numeric columns
because the Markov feature has the two pre-registered transition dimensions.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

from lottery_system.phase4.features import per_number


LAMBDA_CANDIDATES = (1e-4, 1e-3, 1e-2)
FEATURE_NAMES = (
    "rolling_rate", "waiting_time", "uniformity_residual",
    "lag_autocorrelation", "markov_p_1_given_1", "markov_p_1_given_0",
    "shannon_entropy", "renyi_entropy", "permutation_entropy",
    "sample_entropy", "run_length", "change_point_score",
    "hypergeometric_mahalanobis",
)


def _finite(value: object) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def feature_rows(history: Sequence[Iterable[int]], n: int, k: int) -> list[list[float]]:
    """Return one point-in-time feature vector per number in ``1..n``."""
    rate = per_number.FREQ_ROLLING_RATE(history, n)
    waiting, _ = per_number.FREQ_WAITING_TIME(history, n)
    residual = per_number.FREQ_UNIFORMITY_RESIDUAL(history, n, k)
    autocorrelation = per_number.STAT_LAG_AUTOCORRELATION(history, n)
    markov = per_number.STAT_MARKOV_TRANSITION(history, n)
    shannon = per_number.STAT_SHANNON_ENTROPY(history, n)
    renyi = per_number.STAT_RENYI_ENTROPY(history, n)
    permutation = per_number.STAT_PERMUTATION_ENTROPY(history, n)
    sample = per_number.STAT_SAMPLE_ENTROPY(history, n)
    runs = per_number.STAT_RUN_LENGTH(history, n)
    change = per_number.STAT_CHANGE_POINT_SCORE(history, n)
    mahalanobis = per_number.STAT_HYPERGEOMETRIC_MAHALANOBIS(history, n, k)
    return [[_finite(v) for v in (
        rate.get(number, 0.0), waiting.get(number, 0.0), residual.get(number, 0.0),
        autocorrelation.get(number, 0.0), markov.get(number, {}).get("p_1_given_1", 0.0),
        markov.get(number, {}).get("p_1_given_0", 0.0), shannon.get(number, 0.0),
        renyi.get(number, 0.0), permutation.get(number, 0.0), sample.get(number, 0.0),
        runs.get(number, {}).get("terminal_length", 0.0), change.get(number, 0.0),
        mahalanobis.get(number, 0.0),
    )] for number in range(1, n + 1)]


def build_point_in_time_dataset(draws: Sequence[Iterable[int]], n: int, k: int):
    """Build X/y where row t uses features calculated from draws[:t] only."""
    matrices, labels = [], []
    history: list[Iterable[int]] = []
    for draw in draws:
        matrices.append(feature_rows(history, n, k))
        winning = set(map(int, draw))
        labels.append([1.0 if number in winning else 0.0 for number in range(1, n + 1)])
        history.append(draw)
    return matrices, labels


def binary_log_loss(y, probabilities) -> float:
    pairs = list(zip(y, probabilities))
    if not pairs:
        raise ValueError("log loss requires observations")
    total = 0.0
    for target, probability in pairs:
        p = min(max(float(probability), 1e-12), 1.0 - 1e-12)
        total -= float(target) * math.log(p) + (1.0 - float(target)) * math.log1p(-p)
    return total / len(pairs)


@dataclass(frozen=True)
class LogisticNumberModel:
    theta: tuple[float, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    regularization_lambda: float

    def predict_proba(self, rows: Sequence[Sequence[float]]) -> list[float]:
        probabilities = []
        for row in rows:
            score = self.theta[0] + sum(weight * ((_finite(value) - mean) / scale)
                                                for weight, value, mean, scale
                                                in zip(self.theta[1:], row, self.mean, self.scale))
            score = min(max(score, -40.0), 40.0)
            probabilities.append(1.0 / (1.0 + math.exp(-score)))
        return probabilities


def fit_logistic(rows, labels, regularization_lambda: float, max_iter: int = 5) -> LogisticNumberModel:
    """Fit deterministic L2 logistic regression with full-prefix SGD passes."""
    x = [list(map(_finite, row)) for row in rows]
    y = [float(value) for value in labels]
    if not x or len(x) != len(y) or not x[0]:
        raise ValueError("non-empty two-dimensional rows and matching labels required")
    dimensions = len(x[0])
    mean = [sum(row[j] for row in x) / len(x) for j in range(dimensions)]
    scale = [math.sqrt(sum((row[j] - mean[j]) ** 2 for row in x) / len(x)) for j in range(dimensions)]
    scale = [value if value >= 1e-12 else 1.0 for value in scale]
    prevalence = min(max(sum(y) / len(y), 1e-9), 1.0 - 1e-9)
    theta = [math.log(prevalence / (1.0 - prevalence))] + [0.0] * dimensions
    # Averaged gradients make the L2 strength independent of prefix length.
    for epoch in range(max_iter):
        gradient = [0.0] * len(theta)
        for row, target in zip(x, y):
            standardized = [(value - center) / spread for value, center, spread in zip(row, mean, scale)]
            score = min(max(theta[0] + sum(w * v for w, v in zip(theta[1:], standardized)), -40.0), 40.0)
            error = 1.0 / (1.0 + math.exp(-score)) - target
            gradient[0] += error
            for j, value in enumerate(standardized, 1):
                gradient[j] += error * value
        learning_rate = 0.5 / math.sqrt(epoch + 1.0)
        theta[0] -= learning_rate * gradient[0] / len(x)
        for j in range(1, len(theta)):
            theta[j] -= learning_rate * (gradient[j] / len(x) + regularization_lambda * theta[j])
    return LogisticNumberModel(tuple(theta), tuple(mean), tuple(scale), float(regularization_lambda))


def select_lambda_and_fit(rows, labels, candidates=LAMBDA_CANDIDATES):
    """Prefix-only chronological validation followed by a full-prefix refit."""
    x = rows
    y = labels
    draw_count, number_count = len(y), len(y[0])
    validation_draws = min(120, max(30, draw_count // 5))
    split = draw_count - validation_draws
    if split < 30:
        split = max(1, draw_count - 1)
    train_x = [row for draw in x[:split] for row in draw]
    train_y = [value for draw in y[:split] for value in draw]
    validation_x = [row for draw in x[split:] for row in draw]
    validation_y = [value for draw in y[split:] for value in draw]
    scores = []
    for value in candidates:
        # Coarse but deterministic prefix fits are sufficient for selecting
        # among the three adjacent pre-registered regularization strengths.
        candidate = fit_logistic(train_x, train_y, value, max_iter=1)
        scores.append((binary_log_loss(validation_y, candidate.predict_proba(validation_x)), value))
    selected = min(scores, key=lambda item: (item[0], item[1]))[1]
    final = fit_logistic([row for draw in x for row in draw], [value for draw in y for value in draw], selected,
                         max_iter=4)
    return final, {"selected": selected, "validation_log_loss": {str(v): loss for loss, v in scores},
                   "selection_scope": "training_prefix_only", "validation_draws": draw_count - split}
