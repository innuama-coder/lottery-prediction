from __future__ import annotations

import heapq
import itertools
import math
from collections import Counter, OrderedDict
from functools import lru_cache
from statistics import mean
from typing import Iterable, Sequence

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised by the scalar portability test
    np = None

from .data import Draw, RULES, canonical, sha256_bytes


FAMILIES = (
    "E401_MULTISCALE_REGIME",
    "E402_BAYES_RENEWAL",
    "E403_HYPERGRAPH_SURPRISE",
    "E404_TEMPORAL_GRAPH",
    "E405_SET_SHAPE_INTERACTIONS",
    "E406_CROSS_ZONE_COUPLING",
    "E407_NONLINEAR_SET_FACTOR",
)
HISTORIES = (384, 640, 0)  # zero means all available
L2_VALUES = (8.0, 32.0, 128.0)
TEMPERATURES = (0.5, 1.0)
PURGE = 8
# A front-zone matrix can exceed 160 MiB.  Two entries retain useful same-context
# grid reuse without allowing a selection process to accumulate close to a GiB.
_SET_MATRIX_CACHE_MAXSIZE = 2
_SET_MATRIX_CACHE: OrderedDict[tuple[object, ...], object] = OrderedDict()


def numbers(draw: Draw, zone: int) -> tuple[int, ...]:
    return draw.front if zone == 0 else draw.back


def elementary(weights: Sequence[float], k: int) -> float:
    result = [1.0] + [0.0] * k
    for weight in weights:
        for order in range(k, 0, -1):
            result[order] += weight * result[order - 1]
    return result[k]


def inclusion_probabilities(weights: Sequence[float], k: int, normalizer: float) -> list[float]:
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
    return [
        weight * math.fsum(prefix[index][order] * suffix[index + 1][k - 1 - order] for order in range(k)) / normalizer
        for index, weight in enumerate(weights)
    ]


def zcolumns(columns: Sequence[Sequence[float]]) -> tuple[list[list[float]], list[dict[str, float]]]:
    normalized, specs = [], []
    for values in columns:
        center = mean(values)
        scale = math.sqrt(math.fsum((value - center) ** 2 for value in values) / len(values))
        scale = max(scale, 1e-12)
        normalized.append([(value - center) / scale for value in values])
        specs.append({"mean": center, "scale": scale})
    return normalized, specs


def _rates(prefix: Sequence[Draw], zone: int, n: int, window: int) -> list[float]:
    rows = prefix[-min(window, len(prefix)):]
    counts = [0] * n
    for draw in rows:
        for value in numbers(draw, zone):
            counts[value - 1] += 1
    return [(count + 0.5) / (len(rows) + 1.0) for count in counts]


def _ewma(prefix: Sequence[Draw], zone: int, n: int, half_life: float) -> list[float]:
    decay = math.exp(math.log(0.5) / half_life)
    totals, denominator, weight = [0.0] * n, 0.0, 1.0
    for draw in reversed(prefix[-512:]):
        selected = set(numbers(draw, zone))
        denominator += weight
        for index in range(n):
            totals[index] += weight * float(index + 1 in selected)
        weight *= decay
    return [(value + 0.5) / (denominator + 1.0) for value in totals]


def _multiscale(prefix: Sequence[Draw], zone: int, n: int, k: int) -> tuple[list[list[float]], list[str]]:
    base = k / n
    rates = {window: _rates(prefix, zone, n, window) for window in (8, 16, 32, 64, 128, 256)}
    ewmas = {half: _ewma(prefix, zone, n, half) for half in (4, 12, 36, 108)}
    columns = [[rates[w][i] - base for i in range(n)] for w in rates]
    columns.extend([[ewmas[h][i] - base for i in range(n)] for h in ewmas])
    columns.extend([
        [rates[8][i] - rates[32][i] for i in range(n)],
        [rates[32][i] - rates[128][i] for i in range(n)],
        [rates[8][i] - 2 * rates[32][i] + rates[128][i] for i in range(n)],
        [max(-6.0, min(6.0, (rates[16][i] - rates[256][i]) / math.sqrt(max(1e-9, base * (1 - base) * (1 / 16 + 1 / 256))))) for i in range(n)],
    ])
    names = [f"count_residual_w{w}" for w in rates] + [f"ewma_residual_h{h}" for h in ewmas] + ["trend_8_32", "trend_32_128", "acceleration", "cusum_regime"]
    normalized, _ = zcolumns(columns)
    return [[normalized[column][row] for column in range(len(normalized))] for row in range(n)], names


def _renewal(prefix: Sequence[Draw], zone: int, n: int, k: int) -> tuple[list[list[float]], list[str]]:
    positions = [[] for _ in range(n)]
    for position, draw in enumerate(prefix):
        for value in numbers(draw, zone):
            positions[value - 1].append(position)
    columns = [[] for _ in range(4)]
    for seen in positions:
        count = len(seen)
        age = len(prefix) - 1 - seen[-1] if seen else len(prefix)
        posterior = (count + 0.5) / (len(prefix) + n / k)
        intervals = [right - left for left, right in zip(seen, seen[1:])]
        interval_mean = (math.fsum(intervals) + n / k * 2.0) / (len(intervals) + 2.0)
        interval_var = (math.fsum((value - interval_mean) ** 2 for value in intervals) + interval_mean * interval_mean) / (len(intervals) + 1.0)
        hazard = 1.0 - (1.0 - posterior) ** (age + 1)
        survival = (1.0 - posterior) ** age
        values = (hazard - posterior, -math.log(max(1e-12, survival)), (age - interval_mean) / math.sqrt(max(1.0, interval_var)), min(8.0, -math.log(max(1e-12, posterior * survival))))
        for column, value in zip(columns, values):
            column.append(value)
    normalized, _ = zcolumns(columns)
    return [[normalized[column][row] for column in range(4)] for row in range(n)], ["posterior_hazard_residual", "posterior_survival_surprise", "renewal_age_residual", "calibrated_surprise"]


def _transition_matrix(prefix: Sequence[Draw], zone: int, n: int, lag: int, window: int) -> list[list[float]]:
    rows = prefix[-min(window + lag, len(prefix)):]
    matrix = [[0.0] * n for _ in range(n)]
    for prior, current in zip(rows, rows[lag:]):
        for left in numbers(prior, zone):
            for right in numbers(current, zone):
                matrix[left - 1][right - 1] += 1.0
    return matrix


def _power_vectors(matrix: Sequence[Sequence[float]], rank: int) -> list[list[float]]:
    n = len(matrix)
    vectors: list[list[float]] = []
    for component in range(rank):
        vector = [math.sin((index + 1) * (component + 1) * 0.719) for index in range(n)]
        for old in vectors:
            projection = math.fsum(a * b for a, b in zip(vector, old))
            vector = [a - projection * b for a, b in zip(vector, old)]
        for _ in range(10):
            updated = [math.fsum(matrix[row][column] * vector[column] for column in range(n)) for row in range(n)]
            for old in vectors:
                projection = math.fsum(a * b for a, b in zip(updated, old))
                updated = [a - projection * b for a, b in zip(updated, old)]
            norm = math.sqrt(math.fsum(value * value for value in updated))
            if norm < 1e-12:
                updated = [0.0] * n
                updated[component % n] = 1.0
                norm = 1.0
            vector = [value / norm for value in updated]
        pivot = max(range(n), key=lambda index: (abs(vector[index]), -index))
        if vector[pivot] < 0:
            vector = [-value for value in vector]
        vectors.append(vector)
    return vectors


def _graph(prefix: Sequence[Draw], zone: int, n: int, k: int, window: int = 256, rank: int = 4) -> tuple[list[list[float]], list[str], list[list[float]]]:
    matrices = {lag: _transition_matrix(prefix, zone, n, lag, window) for lag in (1, 2, 4)}
    last_sets = {lag: numbers(prefix[-lag], zone) for lag in (1, 2, 4)}
    columns = []
    for lag in (1, 2, 4):
        matrix = matrices[lag]
        columns.append([math.fsum(matrix[source - 1][target] for source in last_sets[lag]) for target in range(n)])
    symmetric = [[0.0] * n for _ in range(n)]
    one = matrices[1]
    for left in range(n):
        for right in range(n):
            symmetric[left][right] = (one[left][right] + one[right][left]) / 2.0
    vectors = _power_vectors(symmetric, rank)
    columns.extend(vectors)
    older = _transition_matrix(prefix[:-64] if len(prefix) > 64 else prefix, zone, n, 1, 192)
    old_vector = _power_vectors(older, 1)[0]
    columns.append([vectors[0][i] - old_vector[i] for i in range(n)])
    normalized, _ = zcolumns(columns)
    names = ["transition_lag1", "transition_lag2", "transition_lag4"] + [f"graph_eigen{i + 1}" for i in range(rank)] + ["embedding_drift"]
    return [[normalized[column][row] for column in range(len(normalized))] for row in range(n)], names, vectors


def _opposite_category(draw: Draw, opposite_zone: int) -> tuple[int, int, int]:
    values = numbers(draw, opposite_zone)
    return (sum(values) // 10, sum(value % 2 for value in values), max(values) - min(values) if len(values) > 1 else values[0] // 3)


def _coupling(prefix: Sequence[Draw], zone: int, n: int, k: int) -> tuple[list[list[float]], list[str]]:
    opposite = 1 - zone
    current_category = _opposite_category(prefix[-1], opposite)
    conditional = [Counter() for _ in range(3)]
    exposures = [Counter() for _ in range(3)]
    rows = prefix[-640:]
    for prior, target in zip(rows, rows[1:]):
        category = _opposite_category(prior, opposite)
        selected = set(numbers(target, zone))
        for dim, value in enumerate(category):
            exposures[dim][value] += 1
            for number in selected:
                conditional[dim][(value, number)] += 1
    columns = []
    base = k / n
    for dim, value in enumerate(current_category):
        total = exposures[dim][value]
        columns.append([(conditional[dim][(value, number)] + 16 * base) / (total + 16) - base for number in range(1, n + 1)])
    columns.append([columns[0][i] * columns[1][i] for i in range(n)])
    normalized, _ = zcolumns(columns)
    return [[normalized[column][row] for column in range(4)] for row in range(n)], ["opposite_sum_condition", "opposite_parity_condition", "opposite_span_condition", "rank2_category_interaction"]


def _cooccurrence_context(prefix: Sequence[Draw], zone: int, n: int, k: int, pair_shrinkage: float, triple_shrinkage: float) -> dict[str, object]:
    rows = prefix[-640:]
    pair, triple = Counter(), Counter()
    for draw in rows:
        values = numbers(draw, zone)
        pair.update(itertools.combinations(values, 2))
        triple.update(itertools.combinations(values, 3))
    pair_base = 1 / max(1, math.comb(n, 2))
    triple_base = 1 / max(1, math.comb(n, 3))
    return {"pair": pair, "triple": triple, "draw_count": len(rows), "pair_base": pair_base, "triple_base": triple_base,
            "pair_shrinkage": pair_shrinkage, "triple_shrinkage": triple_shrinkage}


def _shape(combo: Sequence[int], n: int, k: int) -> list[float]:
    boundaries = (0, *combo, n + 1)
    gaps = [right - left - 1 for left, right in zip(boundaries, boundaries[1:])]
    positive = [value for value in gaps if value]
    entropy = -math.fsum((value / max(1, sum(gaps))) * math.log(value / max(1, sum(gaps))) for value in positive)
    occupancy = [0] * 4
    for value in combo:
        occupancy[min(3, (value - 1) * 4 // n)] += 1
    consecutive = sum(right - left == 1 for left, right in zip(combo, combo[1:]))
    longest, run = 1, 1
    for left, right in zip(combo, combo[1:]):
        run = run + 1 if right - left == 1 else 1
        longest = max(longest, run)
    span, total, odd = combo[-1] - combo[0], sum(combo), sum(value % 2 for value in combo)
    gap_sd = math.sqrt(math.fsum((value - mean(gaps)) ** 2 for value in gaps) / len(gaps))
    roughness = math.sqrt(math.fsum((right - left) ** 2 for left, right in zip(gaps, gaps[1:])) / max(1, len(gaps) - 1))
    imbalance = math.fsum((value - k / 4) ** 2 for value in occupancy)
    return [mean(gaps), gap_sd, max(gaps), entropy, roughness, span, total / n, (total / n) ** 2, odd, odd % 2, *occupancy, imbalance, consecutive, longest, span * imbalance / max(1, n), total * odd / max(1, n * k)]


def _set_features(family: str, combo: Sequence[int], context: dict[str, object]) -> list[float]:
    n, k = int(context["n"]), int(context["k"])
    if family == "E405_SET_SHAPE_INTERACTIONS":
        return _shape(combo, n, k)
    if family == "E403_HYPERGRAPH_SURPRISE":
        co = context["cooccurrence"]
        draws = int(co["draw_count"])
        pairs = []
        for item in itertools.combinations(combo, 2):
            shrinkage = float(co["pair_shrinkage"])
            rate = (co["pair"][item] + shrinkage * co["pair_base"]) / (draws + shrinkage)
            pairs.append(math.log(max(1e-12, rate / co["pair_base"])))
        triples = []
        for item in itertools.combinations(combo, 3):
            shrinkage = float(co["triple_shrinkage"])
            rate = (co["triple"][item] + shrinkage * co["triple_base"]) / (draws + shrinkage)
            triples.append(math.log(max(1e-12, rate / co["triple_base"])))
        pairs_sorted, triples_sorted = sorted(pairs), sorted(triples)
        pair_summary = [mean(pairs), max(pairs), pairs_sorted[3 * len(pairs) // 4]] if pairs else [0.0, 0.0, 0.0]
        triple_summary = [mean(triples), max(triples), triples_sorted[3 * len(triples) // 4]] if triples else [0.0, 0.0, 0.0]
        return pair_summary + triple_summary
    if family == "E407_NONLINEAR_SET_FACTOR":
        embeddings = context["embeddings"]
        pair_scores = [math.fsum(embeddings[rank][left - 1] * embeddings[rank][right - 1] for rank in range(len(embeddings))) for left, right in itertools.combinations(combo, 2)]
        temporal = math.fsum(context["temporal"][value - 1][0] for value in combo) / math.sqrt(k)
        shape = _shape(combo, n, k)
        return [mean(pair_scores) if pair_scores else 0.0, max(pair_scores) if pair_scores else 0.0, math.tanh(temporal), shape[5] * temporal / max(1, n)]
    raise ValueError(f"not a set family: {family}")


def build_context(game: str, prefix: Sequence[Draw], zone: int, family: str, config: dict[str, object] | None = None) -> dict[str, object]:
    n, k = RULES[game][zone]
    if len(prefix) < 300:
        raise ValueError("insufficient feature prefix")
    config = config or {}
    context: dict[str, object] = {"game": game, "zone": zone, "family": family, "n": n, "k": k, "prefix_count": len(prefix), "maximum_source_issue": prefix[-1].issue, "input_sha256": sha256_bytes(canonical([row.source_record_sha256 for row in prefix]))}
    if family == "E401_MULTISCALE_REGIME":
        context["number_features"], context["feature_names"] = _multiscale(prefix, zone, n, k)
    elif family == "E402_BAYES_RENEWAL":
        context["number_features"], context["feature_names"] = _renewal(prefix, zone, n, k)
    elif family == "E404_TEMPORAL_GRAPH":
        context["number_features"], context["feature_names"], context["embeddings"] = _graph(prefix, zone, n, k, int(config.get("graph_window", 256)), int(config.get("rank", 4)))
    elif family == "E406_CROSS_ZONE_COUPLING":
        context["number_features"], context["feature_names"] = _coupling(prefix, zone, n, k)
    elif family == "E403_HYPERGRAPH_SURPRISE":
        context["cooccurrence"] = _cooccurrence_context(prefix, zone, n, k, float(config.get("pair_shrinkage", 32.0)), float(config.get("triple_shrinkage", 128.0)))
        context["feature_names"] = ["pair_mean", "pair_max", "pair_q75", "triple_mean", "triple_max", "triple_q75"]
    elif family == "E405_SET_SHAPE_INTERACTIONS":
        context["feature_names"] = ["gap_mean", "gap_sd", "gap_max", "gap_entropy", "gap_roughness", "span", "sum", "sum_squared", "odd_count", "parity", "occ1", "occ2", "occ3", "occ4", "occupancy_imbalance", "consecutive_pairs", "longest_run", "span_x_occupancy", "sum_x_parity"]
    elif family == "E407_NONLINEAR_SET_FACTOR":
        temporal, _ = _multiscale(prefix, zone, n, k)
        rank = int(config.get("rank", 4))
        _, _, embeddings = _graph(prefix, zone, n, k, 256, rank)
        context["temporal"], context["embeddings"] = temporal, embeddings[:rank]
        context["feature_names"] = [f"rank{rank}_pair_inner_product_mean", f"rank{rank}_pair_inner_product_max", "tanh_temporal_score", "shape_x_temporal"]
    else:
        raise ValueError(f"unknown family: {family}")
    return context


def combo_features(combo: Sequence[int], context: dict[str, object]) -> list[float]:
    if "number_features" in context:
        return [math.fsum(context["number_features"][value - 1][column] for value in combo) / math.sqrt(int(context["k"])) for column in range(len(context["feature_names"]))]
    return _set_features(str(context["family"]), combo, context)


@lru_cache(maxsize=8)
def _combo_array(n: int, k: int):
    if np is None:
        return None
    count = math.comb(n, k)
    flat = np.fromiter((value for combo in itertools.combinations(range(1, n + 1), k) for value in combo), dtype=np.int16, count=count * k)
    return flat.reshape((count, k))


@lru_cache(maxsize=8)
def _shape_matrix(n: int, k: int):
    if np is None:
        return None
    combos = _combo_array(n, k).astype(np.float64)
    gaps = np.concatenate((combos[:, :1] - 1.0, np.diff(combos, axis=1) - 1.0, n - combos[:, -1:]), axis=1)
    gap_total = max(1, n - k)
    proportions = gaps / gap_total
    entropy = -np.sum(np.where(proportions > 0, proportions * np.log(np.where(proportions > 0, proportions, 1.0)), 0.0), axis=1)
    total = np.sum(combos, axis=1)
    odd = np.sum(combos % 2, axis=1)
    span = combos[:, -1] - combos[:, 0]
    occupancy = np.column_stack([np.sum(((combos - 1) * 4 // n) == quartile, axis=1) for quartile in range(4)])
    imbalance = np.sum((occupancy - k / 4.0) ** 2, axis=1)
    adjacent = np.diff(combos, axis=1) == 1
    longest = np.ones(combos.shape[0])
    running = np.ones(combos.shape[0])
    for column in range(max(0, k - 1)):
        running = np.where(adjacent[:, column], running + 1, 1)
        longest = np.maximum(longest, running)
    columns = [
        np.mean(gaps, axis=1), np.std(gaps, axis=1), np.max(gaps, axis=1), entropy,
        np.sqrt(np.mean(np.diff(gaps, axis=1) ** 2, axis=1)), span, total / n,
        (total / n) ** 2, odd, odd % 2,
    ]
    columns.extend(occupancy[:, index] for index in range(4))
    columns.extend((imbalance, np.sum(adjacent, axis=1), longest, span * imbalance / n, total * odd / (n * k)))
    matrix = np.column_stack(columns).astype(np.float64)
    matrix.setflags(write=False)
    return matrix


def _set_feature_matrix(context: dict[str, object]):
    if np is None:
        return None
    n, k, family = int(context["n"]), int(context["k"]), str(context["family"])
    combos = _combo_array(n, k)
    if family == "E405_SET_SHAPE_INTERACTIONS":
        return _shape_matrix(n, k)
    key = (family, n, k, context["input_sha256"],
           float(context.get("cooccurrence", {}).get("pair_shrinkage", 0.0)),
           float(context.get("cooccurrence", {}).get("triple_shrinkage", 0.0)),
           len(context.get("embeddings", [])))
    if key in _SET_MATRIX_CACHE:
        matrix = _SET_MATRIX_CACHE.pop(key)
        _SET_MATRIX_CACHE[key] = matrix
        return matrix
    if family == "E403_HYPERGRAPH_SURPRISE":
        co = context["cooccurrence"]
        draws = float(co["draw_count"])
        pair_shrinkage, triple_shrinkage = float(co["pair_shrinkage"]), float(co["triple_shrinkage"])
        pair_table = np.zeros((n + 1, n + 1), dtype=np.float64)
        for left in range(1, n + 1):
            for right in range(left + 1, n + 1):
                rate = (co["pair"][(left, right)] + pair_shrinkage * co["pair_base"]) / (draws + pair_shrinkage)
                pair_table[left, right] = math.log(max(1e-12, rate / co["pair_base"]))
        pair_columns = [pair_table[combos[:, left], combos[:, right]] for left, right in itertools.combinations(range(k), 2)]
        pair_values = np.column_stack(pair_columns) if pair_columns else np.zeros((len(combos), 1), dtype=np.float64)
        triple_table = np.zeros((n + 1, n + 1, n + 1), dtype=np.float64)
        for left in range(1, n + 1):
            for middle in range(left + 1, n + 1):
                for right in range(middle + 1, n + 1):
                    rate = (co["triple"][(left, middle, right)] + triple_shrinkage * co["triple_base"]) / (draws + triple_shrinkage)
                    triple_table[left, middle, right] = math.log(max(1e-12, rate / co["triple_base"]))
        triple_columns = [triple_table[combos[:, left], combos[:, middle], combos[:, right]] for left, middle, right in itertools.combinations(range(k), 3)]
        triple_values = np.column_stack(triple_columns) if triple_columns else np.zeros((len(combos), 1), dtype=np.float64)
        pair_sorted, triple_sorted = np.sort(pair_values, axis=1), np.sort(triple_values, axis=1)
        result = np.column_stack((np.mean(pair_values, axis=1), np.max(pair_values, axis=1), pair_sorted[:, 3 * pair_values.shape[1] // 4],
                                  np.mean(triple_values, axis=1), np.max(triple_values, axis=1), triple_sorted[:, 3 * triple_values.shape[1] // 4]))
        _SET_MATRIX_CACHE[key] = result
        while len(_SET_MATRIX_CACHE) > _SET_MATRIX_CACHE_MAXSIZE:
            _SET_MATRIX_CACHE.popitem(last=False)
        return result
    if family == "E407_NONLINEAR_SET_FACTOR":
        embeddings = np.asarray(context["embeddings"], dtype=np.float64)
        zero = combos.astype(np.int64) - 1
        pair_values = []
        for left, right in itertools.combinations(range(k), 2):
            pair_values.append(np.sum(embeddings[:, zero[:, left]] * embeddings[:, zero[:, right]], axis=0))
        pair_matrix = np.column_stack(pair_values) if pair_values else np.zeros((len(combos), 1), dtype=np.float64)
        temporal = np.asarray([row[0] for row in context["temporal"]], dtype=np.float64)
        temporal_score = np.sum(temporal[zero], axis=1) / math.sqrt(k)
        span = combos[:, -1] - combos[:, 0]
        result = np.column_stack((np.mean(pair_matrix, axis=1), np.max(pair_matrix, axis=1), np.tanh(temporal_score), span * temporal_score / n))
        _SET_MATRIX_CACHE[key] = result
        while len(_SET_MATRIX_CACHE) > _SET_MATRIX_CACHE_MAXSIZE:
            _SET_MATRIX_CACHE.popitem(last=False)
        return result
    raise ValueError(f"not a vectorized set family: {family}")


@lru_cache(maxsize=8)
def _uniform_static(family: str, n: int, k: int) -> tuple[float, ...]:
    if family != "E405_SET_SHAPE_INTERACTIONS":
        raise ValueError("only static shape is cached")
    totals: list[float] | None = None
    count = 0
    for combo in itertools.combinations(range(1, n + 1), k):
        values = _shape(combo, n, k)
        if totals is None:
            totals = [0.0] * len(values)
        for index, value in enumerate(values):
            totals[index] += value
        count += 1
    return tuple(value / count for value in totals or [])


def uniform_expectation(context: dict[str, object]) -> list[float]:
    if "number_features" in context:
        n, k = int(context["n"]), int(context["k"])
        return [k / n * math.fsum(row[column] for row in context["number_features"]) / math.sqrt(k) for column in range(len(context["feature_names"]))]
    family, n, k = str(context["family"]), int(context["n"]), int(context["k"])
    matrix = _set_feature_matrix(context)
    if matrix is not None:
        return [float(value) for value in np.mean(matrix, axis=0)]
    if family == "E405_SET_SHAPE_INTERACTIONS":
        return list(_uniform_static(family, n, k))
    totals = [0.0] * len(context["feature_names"])
    count = 0
    for combo in itertools.combinations(range(1, n + 1), k):
        for index, value in enumerate(combo_features(combo, context)):
            totals[index] += value
        count += 1
    return [value / count for value in totals]


def fit_zone(game: str, draws: Sequence[Draw], cutoff: int, zone: int, family: str, history: int, l2: float, temperature: float, config: dict[str, object] | None = None) -> dict[str, object]:
    # ``cutoff`` is the frozen exclusive train_end.  Fold construction owns the
    # eight-position gap between it and validation/report start.
    end = cutoff
    if end < 300:
        raise ValueError("insufficient purged training prefix")
    start = max(300, end - (history or end))
    # Freeze feature construction before the first fitted label.  Every training
    # and validation target is therefore strictly in the future of the complete
    # feature/normalization prefix; no target can influence its own predictors.
    context = build_context(game, draws[:start], zone, family, config)
    observed = [combo_features(numbers(draws[target], zone), context) for target in range(start, end)]
    expected = uniform_expectation(context)
    gradient = [mean(row[index] for row in observed) - expected[index] for index in range(len(expected))]
    coefficients = []
    for index, value in enumerate(gradient):
        penalty = l2
        if family == "E407_NONLINEAR_SET_FACTOR" and index < 2:
            penalty = float((config or {}).get("factor_l2", l2))
        coefficients.append(max(-0.75, min(0.75, value * temperature / penalty)))
    return {"family": family, "context": context, "history": history or "all_available", "l2": l2, "temperature": temperature, "coefficients": coefficients, "gradient_at_uniform": gradient, "training_positions": [start, end], "maximum_training_label_position": end - 1, "purge_to_validation_draws": PURGE}


def distribution(fitted: dict[str, object], keep_top: bool = False) -> dict[str, object]:
    context, coefficients = fitted["context"], fitted["coefficients"]
    n, k = int(context["n"]), int(context["k"])
    if "number_features" in context:
        scores = [math.fsum(value * coefficient for value, coefficient in zip(row, coefficients)) / math.sqrt(k) for row in context["number_features"]]
        weights = [math.exp(max(-8.0, min(8.0, score))) for score in scores]
        normalizer = elementary(weights, k)
        square_mass = elementary([weight * weight for weight in weights], k) / (normalizer * normalizer)
        marginals = inclusion_probabilities(weights, k, normalizer)
        expected_score = math.fsum(
            marginal * score for marginal, score in zip(marginals, scores)
        )
        result = {"kind": "additive", "n": n, "k": k, "weights": weights, "normalizer": normalizer, "log_normalizer": math.log(normalizer), "probability_square_sum": square_mass, "inclusion_probabilities": marginals, "normalization_mass": 1.0, "expected_score": expected_score}
        if keep_top:
            rows = [(math.prod(weights[value - 1] for value in combo) / normalizer, combo) for combo in itertools.combinations(range(1, n + 1), k)]
            rows.sort(key=lambda row: row[1]); rows.sort(key=lambda row: row[0], reverse=True)
            result["top"] = rows[:1000]
        return result
    matrix = _set_feature_matrix(context)
    if matrix is not None:
        scores = matrix @ np.asarray(coefficients, dtype=np.float64)
        maximum = float(np.max(scores))
        weights = np.exp(scores - maximum)
        total = float(np.sum(weights, dtype=np.float64))
        probabilities = weights / total
        combos = _combo_array(n, k)
        marginals = np.zeros(n + 1, dtype=np.float64)
        for column in range(k):
            marginals += np.bincount(combos[:, column], weights=probabilities, minlength=n + 1)
        result = {"kind": "enumerated", "n": n, "k": k, "context": context, "coefficients": coefficients,
                  "log_normalizer": maximum + math.log(total), "probability_square_sum": float(np.dot(probabilities, probabilities)),
                  "inclusion_probabilities": [float(value) for value in marginals[1:]], "normalization_mass": float(np.sum(probabilities)),
                  "combination_count": int(scores.shape[0]), "expected_score": float(np.dot(probabilities, scores))}
        if keep_top:
            order = np.lexsort(tuple(combos[:, index] for index in reversed(range(k))) + (-scores,))[: min(1000, len(scores))]
            result["top"] = [(float(probabilities[index]), tuple(int(value) for value in combos[index])) for index in order]
        return result
    maximum, total, square_total, score_total, count = -math.inf, 0.0, 0.0, 0.0, 0
    raw_rows: list[tuple[float, tuple[int, ...]]] = []
    inclusion_scaled = [0.0] * n
    for combo in itertools.combinations(range(1, n + 1), k):
        score = math.fsum(value * coefficient for value, coefficient in zip(combo_features(combo, context), coefficients))
        if score <= maximum:
            weight = math.exp(score - maximum)
            total += weight; square_total += weight * weight; score_total += score * weight
            for value in combo: inclusion_scaled[value - 1] += weight
        else:
            factor = 0.0 if maximum == -math.inf else math.exp(maximum - score)
            total, square_total, score_total = total * factor + 1.0, square_total * factor * factor + 1.0, score_total * factor + score
            inclusion_scaled = [value * factor for value in inclusion_scaled]
            for value in combo: inclusion_scaled[value - 1] += 1.0
            maximum = score
        count += 1
        if keep_top:
            entry = (score, tuple(-value for value in combo), combo)
            if len(raw_rows) < 1000: heapq.heappush(raw_rows, entry)
            elif entry[:2] > raw_rows[0][:2]: heapq.heapreplace(raw_rows, entry)
    logz = maximum + math.log(total)
    result = {"kind": "enumerated", "n": n, "k": k, "context": context, "coefficients": coefficients, "log_normalizer": logz, "probability_square_sum": square_total / (total * total), "inclusion_probabilities": [value / total for value in inclusion_scaled], "normalization_mass": 1.0, "combination_count": count, "expected_score": score_total / total}
    if keep_top:
        rows = [(math.exp(score - logz), combo) for score, _, combo in raw_rows]
        rows.sort(key=lambda row: row[1]); rows.sort(key=lambda row: row[0], reverse=True)
        result["top"] = rows
    return result


def subset_probability(combo: Sequence[int], fitted: dict[str, object], dist: dict[str, object]) -> float:
    if dist["kind"] == "additive":
        return math.prod(dist["weights"][value - 1] for value in combo) / float(dist["normalizer"])
    score = math.fsum(value * coefficient for value, coefficient in zip(combo_features(combo, fitted["context"]), fitted["coefficients"]))
    return math.exp(score - float(dist["log_normalizer"]))


def _category_factors(combo: Sequence[int], zone: int, n: int, k: int) -> tuple[float, float]:
    total = math.fsum(combo)
    odd = math.fsum(value % 2 for value in combo)
    if zone == 0:
        occupancy = [sum(min(3, (value - 1) * 4 // n) == quartile for value in combo) for quartile in range(4)]
        imbalance = math.fsum((value - k / 4.0) ** 2 for value in occupancy)
        return ((total - k * (n + 1) / 2.0) / max(1.0, n * math.sqrt(k)), (odd - k / 2.0) / max(1.0, k) + imbalance / max(1.0, k * k) - 0.25)
    gap = combo[-1] - combo[0] if k > 1 else combo[0] // 3
    return ((total - k * (n + 1) / 2.0) / max(1.0, n * math.sqrt(k)), (odd - k / 2.0) / max(1.0, k) + gap / max(1.0, n) - 0.5)


@lru_cache(maxsize=8)
def _category_factor_matrix(zone: int, n: int, k: int):
    if np is None:
        return None
    combos = _combo_array(n, k).astype(np.float64)
    total, odd = np.sum(combos, axis=1), np.sum(combos % 2, axis=1)
    first = (total - k * (n + 1) / 2.0) / max(1.0, n * math.sqrt(k))
    if zone == 0:
        occupancy = np.column_stack([np.sum(((combos - 1) * 4 // n) == quartile, axis=1) for quartile in range(4)])
        imbalance = np.sum((occupancy - k / 4.0) ** 2, axis=1)
        second = (odd - k / 2.0) / max(1.0, k) + imbalance / max(1.0, k * k) - 0.25
    else:
        gap = combos[:, -1] - combos[:, 0] if k > 1 else combos[:, 0] // 3
        second = (odd - k / 2.0) / max(1.0, k) + gap / max(1.0, n) - 0.5
    result = np.column_stack((first, second))
    result.setflags(write=False)
    return result


@lru_cache(maxsize=8)
def _category_groups(zone: int, n: int, k: int):
    factors = _category_factor_matrix(zone, n, k)
    if factors is None:
        return None
    unique, inverse = np.unique(factors, axis=0, return_inverse=True)
    unique.setflags(write=False)
    inverse.setflags(write=False)
    return unique, inverse


def _fit_coupling(game: str, draws: Sequence[Draw], start: int, end: int, l2: float, temperature: float) -> dict[str, object]:
    rules = RULES[game]
    observed = []
    for draw in draws[start:end]:
        front = _category_factors(draw.front, 0, *rules[0])
        back = _category_factors(draw.back, 1, *rules[1])
        observed.append((front[0] * back[0], front[1] * back[1]))
    expectations = []
    for zone, (n, k) in enumerate(rules):
        matrix = _category_factor_matrix(zone, n, k)
        if matrix is not None:
            expectations.append(tuple(float(value) for value in np.mean(matrix, axis=0)))
        else:
            values = [_category_factors(combo, zone, n, k) for combo in itertools.combinations(range(1, n + 1), k)]
            expectations.append(tuple(mean(row[index] for row in values) for index in range(2)))
    expected = (expectations[0][0] * expectations[1][0], expectations[0][1] * expectations[1][1])
    gradient = [mean(row[index] for row in observed) - expected[index] for index in range(2)]
    coefficients = [max(-0.75, min(0.75, value * temperature / l2)) for value in gradient]
    return {"rank": 2, "feature_names": ["front_sum_category_x_back_sum_category", "front_parity_occupancy_x_back_parity_gap"],
            "coefficients": coefficients, "gradient_at_uniform": gradient, "training_positions": [start, end]}


def _zone_combo_probabilities(fitted: dict[str, object], dist: dict[str, object]):
    n, k = int(dist["n"]), int(dist["k"])
    combos = _combo_array(n, k)
    if np is None:
        combo_rows = list(itertools.combinations(range(1, n + 1), k))
        return combo_rows, [subset_probability(combo, fitted, dist) for combo in combo_rows]
    if dist["kind"] == "additive":
        weights = np.asarray(dist["weights"], dtype=np.float64)
        probabilities = np.prod(weights[combos.astype(np.int64) - 1], axis=1) / float(dist["normalizer"])
    else:
        matrix = _set_feature_matrix(fitted["context"])
        scores = matrix @ np.asarray(fitted["coefficients"], dtype=np.float64)
        probabilities = np.exp(scores - float(dist["log_normalizer"]))
    return combos, probabilities


def _joint_coupling_distribution(model: dict[str, object], distributions: Sequence[dict[str, object]]) -> dict[str, object]:
    if np is None:
        raise RuntimeError("NumPy is required for exact E406 joint category normalization")
    combo_rows, probability_rows = [], []
    for zone in (0, 1):
        combos, probabilities = _zone_combo_probabilities(model["zones"][zone], distributions[zone])
        n, k = RULES[str(model["game"])][zone]
        combo_rows.append(combos); probability_rows.append(probabilities)
    uniques, inverses, masses, squares = [], [], [], []
    for zone, probabilities in enumerate(probability_rows):
        n, k = RULES[str(model["game"])][zone]
        grouped = _category_groups(zone, n, k)
        if grouped is None:
            raise RuntimeError("NumPy is required for exact E406 category groups")
        unique, inverse = grouped
        uniques.append(unique); inverses.append(inverse)
        masses.append(np.bincount(inverse, weights=probabilities, minlength=len(unique)))
        squares.append(np.bincount(inverse, weights=probabilities * probabilities, minlength=len(unique)))
    coefficients = np.asarray(model["coupling"]["coefficients"], dtype=np.float64)
    interaction = coefficients[0] * np.outer(uniques[0][:, 0], uniques[1][:, 0]) + coefficients[1] * np.outer(uniques[0][:, 1], uniques[1][:, 1])
    weights = np.exp(np.clip(interaction, -8.0, 8.0))
    normalizer = float(masses[0] @ weights @ masses[1])
    adjusted = [probability_rows[0] * (weights @ masses[1])[inverses[0]] / normalizer,
                probability_rows[1] * (weights.T @ masses[0])[inverses[1]] / normalizer]
    marginals = []
    for zone in (0, 1):
        n, k = RULES[str(model["game"])][zone]
        values = np.zeros(n + 1, dtype=np.float64)
        for column in range(k):
            values += np.bincount(combo_rows[zone][:, column], weights=adjusted[zone], minlength=n + 1)
        marginals.append([float(value) for value in values[1:]])
    square_mass = float(squares[0] @ (weights * weights) @ squares[1]) / (normalizer * normalizer)
    return {"normalizer": normalizer, "weights": weights, "uniques": uniques, "inverses": inverses,
            "probabilities": probability_rows, "adjusted_zone_probabilities": adjusted,
            "marginals": marginals, "probability_square_sum": square_mass, "normalization_mass": 1.0}


def fit_model(game: str, draws: Sequence[Draw], cutoff: int, family: str, config: dict[str, object]) -> dict[str, object]:
    zones = [fit_zone(game, draws, cutoff, zone, family, int(config["history"]), float(config["l2"]), float(config["temperature"]), config) for zone in (0, 1)]
    model = {"game": game, "family": family, "cutoff": cutoff, "config": config, "zones": zones, "maximum_training_label_position": max(int(zone["maximum_training_label_position"]) for zone in zones)}
    if family == "E406_CROSS_ZONE_COUPLING":
        start, end = zones[0]["training_positions"]
        model["coupling"] = _fit_coupling(game, draws, int(start), int(end), float(config["l2"]), float(config["temperature"]))
    return model


def _score_with_distributions(
    model: dict[str, object],
    draw: Draw,
    distributions: Sequence[dict[str, object]],
    joint: dict[str, object] | None,
) -> dict[str, object]:
    probabilities = [subset_probability(numbers(draw, zone), model["zones"][zone], distributions[zone]) for zone in (0, 1)]
    if joint is None:
        probability = probabilities[0] * probabilities[1]
        square_mass = math.prod(float(item["probability_square_sum"]) for item in distributions)
        marginals_by_zone = [item["inclusion_probabilities"] for item in distributions]
        normalization_mass = math.prod(float(item["normalization_mass"]) for item in distributions)
    else:
        front_factors = _category_factors(draw.front, 0, *RULES[str(model["game"])][0])
        back_factors = _category_factors(draw.back, 1, *RULES[str(model["game"])][1])
        coefficients = model["coupling"]["coefficients"]
        correction = math.exp(max(-8.0, min(8.0, coefficients[0] * front_factors[0] * back_factors[0] + coefficients[1] * front_factors[1] * back_factors[1])))
        probability = probabilities[0] * probabilities[1] * correction / joint["normalizer"]
        square_mass, marginals_by_zone, normalization_mass = joint["probability_square_sum"], joint["marginals"], joint["normalization_mass"]
    brier = 1.0 - 2.0 * probability + square_mass
    zone_briers = []
    for zone in (0, 1):
        actual = set(numbers(draw, zone)); marginals = marginals_by_zone[zone]
        zone_briers.append(math.fsum((value - float(index + 1 in actual)) ** 2 for index, value in enumerate(marginals)) / len(marginals))
    return {"joint_probability": probability, "joint_log_loss": -math.log(probability), "full_multiclass_brier": brier, "zone_inclusion_brier": zone_briers, "normalization_mass": normalization_mass, "distributions": distributions}


def score_model(model: dict[str, object], draw: Draw, keep_top: bool = False) -> dict[str, object]:
    distributions = [distribution(zone, keep_top) for zone in model["zones"]]
    joint = _joint_coupling_distribution(model, distributions) if "coupling" in model else None
    return _score_with_distributions(model, draw, distributions, joint)


def score_block(model: dict[str, object], draws: Sequence[Draw], positions: Sequence[int]) -> list[dict[str, object]]:
    distributions = [distribution(zone) for zone in model["zones"]]
    joint = _joint_coupling_distribution(model, distributions) if "coupling" in model else None
    return [
        {"target_position": target, "issue": draws[target].issue,
         **{key: value for key, value in _score_with_distributions(model, draws[target], distributions, joint).items() if key != "distributions"}}
        for target in positions
    ]


def configurations(family: str | None = None) -> list[dict[str, object]]:
    base = [{"history": history, "l2": l2, "temperature": temperature} for history, l2, temperature in itertools.product(HISTORIES, L2_VALUES, TEMPERATURES)]
    if family == "E403_HYPERGRAPH_SURPRISE":
        return [{**row, "pair_shrinkage": pair, "triple_shrinkage": triple} for row, pair, triple in itertools.product(base, (32.0, 128.0), (128.0, 512.0))]
    if family == "E404_TEMPORAL_GRAPH":
        return [{**row, "graph_window": window, "rank": rank} for row, window, rank in itertools.product(base, (96, 192, 256), (2, 4))]
    if family == "E407_NONLINEAR_SET_FACTOR":
        return [{**row, "rank": rank, "factor_l2": factor_l2} for row, rank, factor_l2 in itertools.product(base, (2, 4), (32.0, 128.0))]
    return base


def _top_independent_product(
    front_rows: Sequence[tuple[float, tuple[int, ...]]],
    back_rows: Sequence[tuple[float, tuple[int, ...]]],
    limit: int,
) -> list[tuple[float, tuple[int, ...], tuple[int, ...]]]:
    """Return an exact Top-K from two descending probability sequences."""
    heap: list[tuple[float, tuple[int, ...], tuple[int, ...], int, int]] = []
    seen = {(0, 0)}
    probability = front_rows[0][0] * back_rows[0][0]
    heapq.heappush(heap, (-probability, front_rows[0][1], back_rows[0][1], 0, 0))
    result = []
    while heap and len(result) < limit:
        negative, front, back, left, right = heapq.heappop(heap)
        result.append((-negative, front, back))
        for next_left, next_right in ((left + 1, right), (left, right + 1)):
            if next_left >= len(front_rows) or next_right >= len(back_rows) or (next_left, next_right) in seen:
                continue
            seen.add((next_left, next_right))
            probability = front_rows[next_left][0] * back_rows[next_right][0]
            heapq.heappush(heap, (-probability, front_rows[next_left][1], back_rows[next_right][1], next_left, next_right))
    return result


def _top_coupled_product(
    model: dict[str, object],
    distributions: Sequence[dict[str, object]],
    joint: dict[str, object],
    limit: int,
) -> list[tuple[float, tuple[int, ...], tuple[int, ...]]]:
    """Exact category-factorized Top-K for E406 without a full joint product."""
    grouped: list[list[list[tuple[float, tuple[int, ...]]]]] = []
    for zone in (0, 1):
        combos, probabilities = _zone_combo_probabilities(model["zones"][zone], distributions[zone])
        inverse = joint["inverses"][zone]
        groups: list[list[tuple[float, tuple[int, ...]]]] = [[] for _ in range(len(joint["uniques"][zone]))]
        for index in range(len(probabilities)):
            combo = tuple(int(value) for value in combos[index])
            groups[int(inverse[index])].append((float(probabilities[index]), combo))
        for rows in groups:
            rows.sort(key=lambda row: row[1])
            rows.sort(key=lambda row: row[0], reverse=True)
        grouped.append(groups)

    normalizer = float(joint["normalizer"])
    coupling_weights = joint["weights"]
    heap: list[tuple[float, tuple[int, ...], tuple[int, ...], int, int, int, int]] = []
    visited: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for front_group, front_rows in enumerate(grouped[0]):
        if not front_rows:
            continue
        for back_group, back_rows in enumerate(grouped[1]):
            if not back_rows:
                continue
            factor = float(coupling_weights[front_group, back_group]) / normalizer
            probability = front_rows[0][0] * back_rows[0][0] * factor
            heapq.heappush(heap, (-probability, front_rows[0][1], back_rows[0][1], front_group, back_group, 0, 0))
            visited[(front_group, back_group)] = {(0, 0)}

    result = []
    while heap and len(result) < limit:
        negative, front, back, front_group, back_group, left, right = heapq.heappop(heap)
        result.append((-negative, front, back))
        group_key = (front_group, back_group)
        front_rows, back_rows = grouped[0][front_group], grouped[1][back_group]
        factor = float(coupling_weights[front_group, back_group]) / normalizer
        for next_left, next_right in ((left + 1, right), (left, right + 1)):
            index = (next_left, next_right)
            if next_left >= len(front_rows) or next_right >= len(back_rows) or index in visited[group_key]:
                continue
            visited[group_key].add(index)
            probability = front_rows[next_left][0] * back_rows[next_right][0] * factor
            heapq.heappush(heap, (-probability, front_rows[next_left][1], back_rows[next_right][1], front_group, back_group, next_left, next_right))
    return result


def top_tickets(model: dict[str, object], limit: int = 1000) -> tuple[list[dict[str, object]], dict[str, object]]:
    if limit < 10 or limit > 1000:
        raise ValueError("Top-K diagnostic limit must be between 10 and 1000")
    zones = [distribution(zone, True) for zone in model["zones"]]
    if "coupling" in model:
        joint = _joint_coupling_distribution(model, zones)
        candidates = _top_coupled_product(model, zones, joint, limit)
    else:
        joint = None
        candidates = _top_independent_product(zones[0]["top"], zones[1]["top"], limit)
    rows = [{"rank": index, "front": list(front), "back": list(back), "joint_probability": probability} for index, (probability, front, back) in enumerate(candidates, 1)]
    total_space = math.prod(math.comb(n, k) for n, k in RULES[str(model["game"])])
    if joint is None:
        entropy = math.fsum(float(zone["log_normalizer"]) - float(zone["expected_score"]) for zone in zones)
        normalization_mass = math.prod(float(zone["normalization_mass"]) for zone in zones)
    else:
        base_log_expectation = math.fsum(
            float(np.dot(adjusted, np.log(probabilities)))
            for adjusted, probabilities in zip(joint["adjusted_zone_probabilities"], joint["probabilities"])
        )
        category_mass = np.outer(
            np.bincount(joint["inverses"][0], weights=joint["probabilities"][0], minlength=len(joint["uniques"][0])),
            np.bincount(joint["inverses"][1], weights=joint["probabilities"][1], minlength=len(joint["uniques"][1])),
        )
        category_joint = category_mass * joint["weights"] / float(joint["normalizer"])
        interaction_expectation = float(np.sum(category_joint * np.log(joint["weights"])))
        entropy = -base_log_expectation - interaction_expectation + math.log(float(joint["normalizer"]))
        normalization_mass = float(joint["normalization_mass"])
    summary = {"ticket_space": total_space, "top1_top1000_ratio": rows[0]["joint_probability"] / rows[-1]["joint_probability"], "top1_top10_ratio": rows[0]["joint_probability"] / rows[9]["joint_probability"], "unique_front_sets_top1000": len({tuple(row["front"]) for row in rows}), "entropy": entropy, "effective_support": math.exp(min(entropy, 700)), "probability_normalization_mass": normalization_mass}
    return rows, summary
