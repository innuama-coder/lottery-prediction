from __future__ import annotations

import itertools
import heapq
import math
from functools import lru_cache
from typing import Sequence

from lottery_system.phase4.real_common import Draw, RULES, digest


MIN_HISTORY = 60
FEATURE_FAMILIES = {
    "C01_SURPRISE_REGIME": ("E01", "E02"),
    "C02_RENEWAL_HAZARD": ("E03", "E04"),
    "C03_TRANSITION": ("E05", "E06"),
    "C04_GRAPH": ("E07", "E08"),
    "C05_SET_SHAPE": ("E09", "E10", "E11"),
}
FEATURE_DEFINITIONS = {
    "E01": "short_12_minus_long_96_inclusion_rate_divided_by_binomial_standard_error",
    "E02": "signed_maximum_of_12_vs_36_and_36_vs_96_standardized_rate_changes",
    "E03": "current_gap_times_shrunk_expanding_inclusion_rate_minus_one_geometric_hazard_residual",
    "E04": "current_gap_minus_prior_interarrival_mean_divided_by_prior_interarrival_scale",
    "E05": "shrunk_lag1_transition_residual_from_current_last_draw_set",
    "E06": "same_number_lag2_autocovariance_residual",
    "E07": "absolute_shrunk_cooccurrence_residual_graph_degree",
    "E08": "deterministically_oriented_principal_shrunk_graph_projection",
    "N01": "tanh_of_surprise_plus_hazard_residual",
    "N02": "tanh_of_transition_plus_graph_projection",
    "E09": "normalized_entropy_of_sorted_internal_and_boundary_gaps",
    "E10": "root_mean_square_adjacent_gap_change",
    "E11": "four_bin_occupancy_squared_deviation_from_equal_occupancy",
}
_TRAINING_SAMPLE_CACHE: dict[tuple[object, ...], list[tuple[list[list[float]], set[int]]]] = {}


def _numbers(draw: Draw, zone: int) -> tuple[int, ...]:
    return draw.front if zone == 0 else draw.back


def _zscore(values: Sequence[float]) -> tuple[list[float], float, float]:
    mean = math.fsum(values) / len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / len(values)
    scale = math.sqrt(variance)
    if scale < 1e-12:
        return [0.0] * len(values), mean, 1.0
    return [(value - mean) / scale for value in values], mean, scale


def _rates(prefix: Sequence[Draw], zone: int, n: int, window: int) -> list[float]:
    rows = prefix[-window:]
    counts = [0] * n
    for draw in rows:
        for number in _numbers(draw, zone):
            counts[number - 1] += 1
    # Jeffreys shrinkage is symmetric over numbers and uses only the strict prefix.
    return [(count + 0.5) / (len(rows) + 1.0) for count in counts]


def _graph(prefix: Sequence[Draw], zone: int, n: int, k: int, window: int, shrinkage: float) -> tuple[list[float], list[float]]:
    rows = prefix[-window:]
    matrix = [[0.0] * n for _ in range(n)]
    pair_probability = k * (k - 1) / max(1, n * (n - 1))
    counts = [[0] * n for _ in range(n)]
    for draw in rows:
        for left, right in itertools.combinations(_numbers(draw, zone), 2):
            counts[left - 1][right - 1] += 1
            counts[right - 1][left - 1] += 1
    for left in range(n):
        for right in range(n):
            if left != right:
                rate = (counts[left][right] + shrinkage * pair_probability) / (len(rows) + shrinkage)
                matrix[left][right] = rate - pair_probability
    degree = [math.fsum(abs(value) for value in row) for row in matrix]
    vector = [math.cos((index + 1) * 0.731) for index in range(n)]
    norm = math.sqrt(math.fsum(value * value for value in vector))
    vector = [value / norm for value in vector]
    for _ in range(12):
        updated = [math.fsum(matrix[row][column] * vector[column] for column in range(n)) for row in range(n)]
        norm = math.sqrt(math.fsum(value * value for value in updated))
        if norm < 1e-15:
            vector = [0.0] * n
            break
        vector = [value / norm for value in updated]
    if vector and vector[max(range(n), key=lambda index: (abs(vector[index]), -index))] < 0:
        vector = [-value for value in vector]
    return degree, vector


def build_context(
    game: str,
    prefix: Sequence[Draw],
    zone: int,
    *,
    graph_window: int = 80,
    pair_shrinkage: float = 20.0,
) -> dict[str, object]:
    """Build all E01-E08 values from a strict prefix only."""
    if game not in RULES or zone not in (0, 1) or len(prefix) < MIN_HISTORY:
        raise ValueError("HOLD_FEATURE_INPUT")
    n, k = RULES[game][zone]
    long_rates = _rates(prefix, zone, n, 96)
    medium_rates = _rates(prefix, zone, n, 36)
    short_rates = _rates(prefix, zone, n, 12)
    expected = k / n
    surprise, change = [], []
    for short, medium, long in zip(short_rates, medium_rates, long_rates):
        scale_12_96 = math.sqrt(max(1e-9, expected * (1 - expected) * (1 / 12 + 1 / min(96, len(prefix)))))
        scale_12_36 = math.sqrt(max(1e-9, expected * (1 - expected) * (1 / 12 + 1 / 36)))
        scale_36_96 = math.sqrt(max(1e-9, expected * (1 - expected) * (1 / 36 + 1 / min(96, len(prefix)))))
        surprise.append((short - long) / scale_12_96)
        candidates = ((short - medium) / scale_12_36, (medium - long) / scale_36_96)
        change.append(max(candidates, key=lambda value: (abs(value), value)))

    positions: list[list[int]] = [[] for _ in range(n)]
    counts = [0] * n
    for position, draw in enumerate(prefix):
        for number in _numbers(draw, zone):
            positions[number - 1].append(position)
            counts[number - 1] += 1
    hazard, renewal = [], []
    for index, seen in enumerate(positions):
        gap = len(prefix) - 1 - seen[-1] if seen else len(prefix)
        rate = (counts[index] + 0.5) / (len(prefix) + n / k)
        hazard.append(min(8.0, gap * rate - 1.0))
        intervals = [right - left for left, right in zip(seen, seen[1:])]
        if intervals:
            mean = math.fsum(intervals) / len(intervals)
            scale = math.sqrt(math.fsum((value - mean) ** 2 for value in intervals) / len(intervals))
            renewal.append((gap - mean) / max(1.0, scale))
        else:
            renewal.append(0.0)

    rows = prefix[-min(graph_window, len(prefix)):]
    marginal = [0] * n
    lag2 = [0.0] * n
    transition = [[0.0] * n for _ in range(n)]
    exposure = max(1, len(rows) - 1)
    for draw in rows:
        for number in _numbers(draw, zone):
            marginal[number - 1] += 1
    for previous, current in zip(rows, rows[1:]):
        for source in _numbers(previous, zone):
            for target in _numbers(current, zone):
                transition[source - 1][target - 1] += 1.0
    for index in range(n):
        hits = 0
        for left, right in zip(rows, rows[2:]):
            hits += int(index + 1 in _numbers(left, zone) and index + 1 in _numbers(right, zone))
        base = marginal[index] / max(1, len(rows))
        lag2[index] = hits / max(1, len(rows) - 2) - base * base
    last_set = _numbers(prefix[-1], zone)
    transition_residual = []
    for target in range(n):
        conditional = math.fsum(transition[source - 1][target] for source in last_set)
        conditional = (conditional + 20.0 * k * expected) / (exposure * k + 20.0 * k)
        transition_residual.append(conditional - marginal[target] / max(1, len(rows)))
    degree, projection = _graph(prefix, zone, n, k, graph_window, pair_shrinkage)

    raw = {
        "E01": surprise, "E02": change, "E03": hazard, "E04": renewal,
        "E05": transition_residual, "E06": lag2, "E07": degree, "E08": projection,
    }
    values: dict[str, list[float]] = {}
    normalization: dict[str, dict[str, float | str]] = {}
    for feature_id, feature_values in raw.items():
        standardized, mean, scale = _zscore(feature_values)
        values[feature_id] = standardized
        normalization[feature_id] = {"method": "strict_prefix_number_population_zscore_v1", "mean": mean, "scale": scale}
    for feature_id, inputs in {"N01": ("E01", "E03"), "N02": ("E05", "E08")}.items():
        nonlinear = [math.tanh((values[inputs[0]][index] + values[inputs[1]][index]) / math.sqrt(2.0)) for index in range(n)]
        values[feature_id], mean, scale = _zscore(nonlinear)
        normalization[feature_id] = {"method": "strict_prefix_tanh_then_number_population_zscore_v1", "mean": mean, "scale": scale}
    return {
        "game": game,
        "zone": zone,
        "n": n,
        "k": k,
        "source_draw_count": len(prefix),
        "cutoff_position": len(prefix) - 1,
        "max_source_position": len(prefix) - 1,
        "max_source_issue": prefix[-1].issue,
        "input_prefix_sha256": digest([draw.fact_hash for draw in prefix]),
        "feature_values": values,
        "normalization": normalization,
        "parameters": {"graph_window": graph_window, "pair_shrinkage": pair_shrinkage},
    }


def elementary(weights: Sequence[float], k: int) -> float:
    coefficients = [1.0] + [0.0] * k
    for weight in weights:
        for order in range(k, 0, -1):
            coefficients[order] += weight * coefficients[order - 1]
    return coefficients[k]


def inclusion_probabilities(weights: Sequence[float], k: int, normalizer: float | None = None) -> list[float]:
    denominator = elementary(weights, k) if normalizer is None else normalizer
    n = len(weights)
    prefix = [[0.0] * (k + 1) for _ in range(n + 1)]
    suffix = [[0.0] * (k + 1) for _ in range(n + 1)]
    prefix[0][0] = suffix[n][0] = 1.0
    for index, weight in enumerate(weights):
        prefix[index + 1][0] = 1.0
        for order in range(1, k + 1):
            prefix[index + 1][order] = prefix[index][order] + weight * prefix[index][order - 1]
    for index in range(n - 1, -1, -1):
        suffix[index][0] = 1.0
        for order in range(1, k + 1):
            suffix[index][order] = suffix[index + 1][order] + weights[index] * suffix[index + 1][order - 1]
    result = []
    for index, weight in enumerate(weights):
        excluded = math.fsum(prefix[index][order] * suffix[index + 1][k - 1 - order] for order in range(k))
        result.append(weight * excluded / denominator)
    return result


def _feature_matrix(context: dict[str, object], feature_ids: Sequence[str]) -> list[list[float]]:
    values = context["feature_values"]
    return [[float(values[feature_id][number]) for feature_id in feature_ids] for number in range(int(context["n"]))]


def _weights(matrix: Sequence[Sequence[float]], coefficients: Sequence[float]) -> list[float]:
    return [math.exp(max(-6.0, min(6.0, math.fsum(value * coefficient for value, coefficient in zip(row, coefficients))))) for row in matrix]


def fit_zone(
    game: str,
    draws: Sequence[Draw],
    cutoff: int,
    zone: int,
    feature_ids: Sequence[str],
    *,
    history: int,
    l2: float,
    temperature: float,
    graph_window: int = 80,
    pair_shrinkage: float = 20.0,
    purge: int = 2,
) -> dict[str, object]:
    if cutoff > len(draws) or cutoff < MIN_HISTORY + purge + 8 or not feature_ids:
        raise ValueError("HOLD_MODEL_INPUT")
    end = cutoff - purge
    start = max(MIN_HISTORY, end - history)
    sample_key = (
        game, digest([draw.fact_hash for draw in draws[:cutoff]]), cutoff, zone, tuple(feature_ids),
        history, graph_window, pair_shrinkage, purge,
    )
    if sample_key not in _TRAINING_SAMPLE_CACHE:
        samples = []
        for target in range(start, end):
            context = build_context(game, draws[:target], zone, graph_window=graph_window, pair_shrinkage=pair_shrinkage)
            samples.append((_feature_matrix(context, feature_ids), set(_numbers(draws[target], zone))))
        _TRAINING_SAMPLE_CACHE[sample_key] = samples
    samples = _TRAINING_SAMPLE_CACHE[sample_key]
    coefficients = [0.0] * len(feature_ids)
    penalty = l2 / max(1, len(samples))
    iterations_run = 0
    for iteration in range(32):
        iterations_run = iteration + 1
        gradient = [0.0] * len(feature_ids)
        for matrix, observed in samples:
            weights = _weights(matrix, coefficients)
            marginals = inclusion_probabilities(weights, RULES[game][zone][1])
            for number, row in enumerate(matrix, 1):
                residual = float(number in observed) - marginals[number - 1]
                for feature_index, value in enumerate(row):
                    gradient[feature_index] += residual * value / len(samples)
        rate = 0.08 / (1.0 + iteration / 20.0)
        maximum_change = 0.0
        for feature_index in range(len(coefficients)):
            gradient[feature_index] -= penalty * coefficients[feature_index]
            updated = max(-0.75, min(0.75, coefficients[feature_index] + rate * gradient[feature_index]))
            maximum_change = max(maximum_change, abs(updated - coefficients[feature_index]))
            coefficients[feature_index] = updated
        if maximum_change < 1e-10:
            break
    coefficients = [value * temperature for value in coefficients]
    return {
        "feature_ids": list(feature_ids),
        "coefficients": coefficients,
        "history": history,
        "l2": l2,
        "temperature": temperature,
        "graph_window": graph_window,
        "pair_shrinkage": pair_shrinkage,
        "purge": purge,
        "training_target_positions": [start, end],
        "max_training_label_position": end - 1,
        "training_input_sha256": digest([draw.fact_hash for draw in draws[:cutoff]]),
        "estimator": "exact_fixed_cardinality_joint_likelihood_ridge_gradient_v1",
        "optimizer": {"method": "deterministic_batch_gradient_ascent", "maximum_iterations": 32,
                      "convergence_maximum_coefficient_change": 1e-10, "iterations_run": iterations_run},
    }


def zone_distribution(
    game: str,
    prefix: Sequence[Draw],
    zone: int,
    fitted: dict[str, object],
) -> dict[str, object]:
    context = build_context(
        game, prefix, zone,
        graph_window=int(fitted["graph_window"]),
        pair_shrinkage=float(fitted["pair_shrinkage"]),
    )
    matrix = _feature_matrix(context, fitted["feature_ids"])
    weights = _weights(matrix, fitted["coefficients"])
    normalizer = elementary(weights, int(context["k"]))
    marginals = inclusion_probabilities(weights, int(context["k"]), normalizer)
    return {
        "n": context["n"], "k": context["k"], "weights": weights, "normalizer": normalizer,
        "inclusion_probabilities": marginals,
        "probability_square_sum": elementary([weight * weight for weight in weights], int(context["k"])) / (normalizer * normalizer),
        "context": context, "fitted": fitted,
    }


def subset_probability(numbers: Sequence[int], distribution: dict[str, object]) -> float:
    return math.prod(distribution["weights"][number - 1] for number in numbers) / float(distribution["normalizer"])


def score_zone_observation(numbers: Sequence[int], distribution: dict[str, object]) -> dict[str, float]:
    probability = subset_probability(numbers, distribution)
    observed = set(numbers)
    marginals = distribution["inclusion_probabilities"]
    inclusion_log_loss = -math.fsum(
        math.log(max(1e-15, min(1 - 1e-15, marginal if index + 1 in observed else 1 - marginal)))
        for index, marginal in enumerate(marginals)
    ) / len(marginals)
    inclusion_brier = math.fsum((marginal - float(index + 1 in observed)) ** 2 for index, marginal in enumerate(marginals)) / len(marginals)
    return {"subset_probability": probability, "joint_log_loss": -math.log(probability), "inclusion_log_loss": inclusion_log_loss,
            "inclusion_brier": inclusion_brier, "probability_square_sum": float(distribution["probability_square_sum"])}


def top_zone(distribution: dict[str, object], limit: int = 1000) -> list[tuple[float, tuple[int, ...]]]:
    rows = []
    for combo in itertools.combinations(range(1, int(distribution["n"]) + 1), int(distribution["k"])):
        probability = subset_probability(combo, distribution)
        entry = (probability, tuple(-number for number in combo), combo)
        if len(rows) < limit:
            heapq.heappush(rows, entry)
        elif entry[:2] > rows[0][:2]:
            heapq.heapreplace(rows, entry)
    result = [(probability, combo) for probability, _, combo in rows]
    result.sort(key=lambda row: row[1])
    result.sort(key=lambda row: row[0], reverse=True)
    return result


def _shape_raw(combo: Sequence[int], n: int) -> tuple[float, float, float]:
    boundaries = (0, *combo, n + 1)
    gaps = [right - left - 1 for left, right in zip(boundaries, boundaries[1:])]
    total = max(1, math.fsum(gaps))
    entropy = -math.fsum((gap / total) * math.log(gap / total) for gap in gaps if gap) / math.log(len(gaps))
    roughness = math.sqrt(math.fsum((right - left) ** 2 for left, right in zip(gaps, gaps[1:])) / max(1, len(gaps) - 1))
    occupancy = [0, 0, 0, 0]
    for number in combo:
        occupancy[min(3, (number - 1) * 4 // n)] += 1
    mean = len(combo) / 4.0
    deviation = math.fsum((count - mean) ** 2 for count in occupancy) / len(combo)
    return entropy, roughness, deviation


@lru_cache(maxsize=8)
def _shape_raw_groups(n: int, k: int):
    groups: dict[tuple[float, float, float], list[object]] = {}
    for combo in itertools.combinations(range(1, n + 1), k):
        raw = _shape_raw(combo, n)
        if raw not in groups:
            groups[raw] = [0, [0] * n]
        groups[raw][0] += 1
        for number in combo:
            groups[raw][1][number - 1] += 1
    return tuple((raw, int(payload[0]), tuple(payload[1])) for raw, payload in sorted(groups.items()))


@lru_cache(maxsize=8)
def shape_population(n: int, k: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    sums, squares, count = [0.0] * 3, [0.0] * 3, 0
    for raw, multiplicity, _ in _shape_raw_groups(n, k):
        count += multiplicity
        for index, value in enumerate(raw):
            sums[index] += multiplicity * value
            squares[index] += multiplicity * value * value
    means = [value / count for value in sums]
    scales = [math.sqrt(max(1e-15, squares[index] / count - means[index] ** 2)) for index in range(3)]
    return tuple(means), tuple(scales)


def shape_vector(combo: Sequence[int], n: int, k: int) -> list[float]:
    means, scales = shape_population(n, k)
    return [(value - means[index]) / scales[index] for index, value in enumerate(_shape_raw(combo, n))]


def fit_shape_zone(
    game: str,
    draws: Sequence[Draw],
    cutoff: int,
    zone: int,
    *,
    history: int,
    l2: float,
    temperature: float,
    purge: int = 2,
) -> dict[str, object]:
    n, k = RULES[game][zone]
    end = cutoff - purge
    start = max(MIN_HISTORY, end - history)
    vectors = [shape_vector(_numbers(draws[target], zone), n, k) for target in range(start, end)]
    gradient = [math.fsum(row[index] for row in vectors) / len(vectors) for index in range(3)]
    coefficients = [max(-0.75, min(0.75, value / l2)) * temperature for value in gradient]
    return {
        "feature_ids": ["E09", "E10", "E11"], "coefficients": coefficients,
        "history": history, "l2": l2, "temperature": temperature, "purge": purge,
        "training_target_positions": [start, end], "max_training_label_position": end - 1,
        "training_input_sha256": digest([draw.fact_hash for draw in draws[:cutoff]]),
        "estimator": "uniform_expectation_one_step_ridge_static_shape_exponential_family_v1",
    }


def shape_distribution(game: str, zone: int, fitted: dict[str, object]) -> dict[str, object]:
    n, k = RULES[game][zone]
    coefficients = fitted["coefficients"]
    maximum, total, square_total, count = -math.inf, 0.0, 0.0, 0
    marginal_totals = [0.0] * n
    means, scales = shape_population(n, k)
    for raw, multiplicity, inclusion_counts in _shape_raw_groups(n, k):
        vector = [(value - means[index]) / scales[index] for index, value in enumerate(raw)]
        score = math.fsum(value * coefficient for value, coefficient in zip(vector, coefficients))
        count += multiplicity
        if score <= maximum:
            weight = math.exp(score - maximum)
            total += multiplicity * weight
            square_total += multiplicity * weight * weight
            for index, inclusion_count in enumerate(inclusion_counts):
                marginal_totals[index] += inclusion_count * weight
        else:
            factor = 0.0 if maximum == -math.inf else math.exp(maximum - score)
            total, square_total, maximum = total * factor + multiplicity, square_total * factor * factor + multiplicity, score
            marginal_totals = [value * factor for value in marginal_totals]
            for index, inclusion_count in enumerate(inclusion_counts):
                marginal_totals[index] += inclusion_count
    return {
        "n": n, "k": k, "fitted": fitted, "log_normalizer": maximum + math.log(total),
        "probability_square_sum": square_total / (total * total), "combination_count": count,
        "inclusion_probabilities": [value / total for value in marginal_totals],
    }


def shape_probability(numbers: Sequence[int], distribution: dict[str, object]) -> float:
    vector = shape_vector(numbers, int(distribution["n"]), int(distribution["k"]))
    score = math.fsum(value * coefficient for value, coefficient in zip(vector, distribution["fitted"]["coefficients"]))
    return math.exp(score - float(distribution["log_normalizer"]))


def score_shape_observation(numbers: Sequence[int], distribution: dict[str, object]) -> dict[str, float]:
    probability = shape_probability(numbers, distribution)
    observed = set(numbers)
    marginals = distribution["inclusion_probabilities"]
    inclusion_log_loss = -math.fsum(
        math.log(max(1e-15, min(1 - 1e-15, marginal if index + 1 in observed else 1 - marginal)))
        for index, marginal in enumerate(marginals)
    ) / len(marginals)
    inclusion_brier = math.fsum((marginal - float(index + 1 in observed)) ** 2 for index, marginal in enumerate(marginals)) / len(marginals)
    return {"subset_probability": probability, "joint_log_loss": -math.log(probability), "inclusion_log_loss": inclusion_log_loss,
            "inclusion_brier": inclusion_brier, "probability_square_sum": float(distribution["probability_square_sum"])}


def top_shape_zone(distribution: dict[str, object], limit: int = 1000) -> list[tuple[float, tuple[int, ...]]]:
    rows = [(shape_probability(combo, distribution), combo) for combo in itertools.combinations(range(1, int(distribution["n"]) + 1), int(distribution["k"]))]
    rows.sort(key=lambda row: row[1])
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows[:limit]
