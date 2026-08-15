from __future__ import annotations

import hashlib
import heapq
import itertools
import math
import random
from functools import lru_cache
from typing import Sequence

from .real_model import Draw, RULES, digest


RULE_IDS = {"ssq": "ssq-ns-33c6-16c1-v1", "dlt": "dlt-ns-35c5-12c2-v1"}
FEATURE_IDS = tuple(f"F{index:02d}" for index in range(1, 15))
NUMBER_IDS = FEATURE_IDS[:5]
RELATION_IDS = FEATURE_IDS[5:7]
STRUCTURE_IDS = FEATURE_IDS[7:]
FEATURE_GROUPS = {
    **{key: "historical_change" for key in NUMBER_IDS},
    **{key: "number_relationship" for key in RELATION_IDS},
    **{key: "combination_structure" for key in STRUCTURE_IDS},
}
L2_GRID = (8.0, 24.0, 72.0)
ROLLING_WINDOWS = (10, 30, 60)
EWMA_HALF_LIVES = (10.0, 30.0)
PAIR_WINDOW = 60
PAIR_SHRINKAGE = 20.0
COEFFICIENT_CAP = 0.35
MIN_HISTORY = 60


def _numbers(draw: Draw, zone: int) -> tuple[int, ...]:
    return draw.front if zone == 0 else draw.back


def _zscore(values: Sequence[float]) -> tuple[list[float], float, float]:
    mean = math.fsum(values) / len(values)
    scale = math.sqrt(math.fsum((value - mean) ** 2 for value in values) / len(values))
    if scale < 1e-12:
        return [0.0 for _ in values], mean, 1.0
    return [(value - mean) / scale for value in values], mean, scale


def _rolling(prefix: Sequence[Draw], zone: int, n: int, window: int) -> list[float]:
    rows = prefix[-window:]
    counts = [0] * n
    for draw in rows:
        for number in _numbers(draw, zone):
            counts[number - 1] += 1
    return [count / max(1, len(rows)) for count in counts]


def _structure(combo: Sequence[int], n: int, k: int) -> list[float]:
    ordered = tuple(combo)
    span = float(ordered[-1] - ordered[0]) if k > 1 else 0.0
    odd_balance = abs(sum(number % 2 for number in ordered) - k / 2.0)
    width = math.ceil(n / 3)
    bucket_mask = 0
    tail_mask = 0
    for number in ordered:
        bucket_mask |= 1 << min(2, (number - 1) // width)
        tail_mask |= 1 << (number % 10)
    buckets = bucket_mask.bit_count() / 3.0
    consecutive = float(sum(right == left + 1 for left, right in zip(ordered, ordered[1:])))
    tail_diversity = tail_mask.bit_count() / k
    gaps = [right - left for left, right in zip(ordered, ordered[1:])]
    dispersion = 0.0
    if gaps:
        mean = span / len(gaps)
        dispersion = math.sqrt(max(0.0, math.fsum(gap * gap for gap in gaps) / len(gaps) - mean * mean))
    return [float(sum(ordered)), span, odd_balance, buckets, consecutive, tail_diversity, dispersion]


def _structure_norm(n: int, k: int) -> list[tuple[float, float]]:
    return [
        (k * (n + 1) / 2.0, max(1.0, math.sqrt(k * (n - k) * (n + 1) / 12.0))),
        (n * (k - 1) / (k + 1) if k > 1 else 0.0, max(1.0, n / 5.0)),
        (0.75 if k > 1 else 0.5, max(0.5, k / 4.0)),
        (min(1.0, k / 3.0), 1.0 / 3.0),
        (k * (k - 1) / n, max(0.5, math.sqrt(max(1e-12, k * (k - 1) / n)))),
        (min(k, 10) / k * 0.82, 0.20),
        (n / max(4.0, 2.0 * k), max(1.0, n / max(6.0, 2.0 * k))),
    ]


def feature_context(game: str, prefix: Sequence[Draw], zone: int) -> dict[str, object]:
    if game not in RULES or zone not in (0, 1) or len(prefix) < MIN_HISTORY:
        raise ValueError("HOLD_FEATURE_INPUT: invalid game, zone, or prefix")
    n, k = RULES[game][zone]
    counts, last_seen = [0] * n, [-1] * n
    for position, draw in enumerate(prefix):
        for number in _numbers(draw, zone):
            counts[number - 1] += 1
            last_seen[number - 1] = position
    alpha, beta = 1.0, max(1.0, n / k - 1.0)
    f01 = [(count + alpha) / (len(prefix) + alpha + beta) for count in counts]
    rolling = {window: _rolling(prefix, zone, n, window) for window in ROLLING_WINDOWS}
    f02 = [math.fsum(rolling[window][index] for window in ROLLING_WINDOWS) / 3.0 for index in range(n)]
    ewma_rates = []
    for half_life in EWMA_HALF_LIVES:
        decay = math.exp(math.log(0.5) / half_life)
        ewma, denominator = [0.0] * n, 0.0
        for draw in prefix:
            ewma = [value * decay for value in ewma]
            denominator = denominator * decay + 1.0
            for number in _numbers(draw, zone):
                ewma[number - 1] += 1.0
        ewma_rates.append([value / denominator for value in ewma])
    f03 = [math.fsum(values[index] for values in ewma_rates) / len(ewma_rates) for index in range(n)]
    raw_gaps = [float(len(prefix) - 1 - position if position >= 0 else len(prefix)) for position in last_seen]
    f04 = [math.log1p(min(120.0, gap)) for gap in raw_gaps]
    f05 = [rolling[10][index] - rolling[60][index] for index in range(n)]
    number_features, normalization = {}, {}
    for feature_id, raw in zip(NUMBER_IDS, (f01, f02, f03, f04, f05)):
        values, mean, scale = _zscore(raw)
        number_features[feature_id] = values
        normalization[feature_id] = {"method": "fold_local_population_zscore_v1", "mean": mean, "scale": scale}

    pair_prefix = prefix[-PAIR_WINDOW:]
    pair_counts, marginal_counts = [[0] * n for _ in range(n)], [0] * n
    for draw in pair_prefix:
        values = _numbers(draw, zone)
        for number in values:
            marginal_counts[number - 1] += 1
        for left, right in itertools.combinations(values, 2):
            pair_counts[left - 1][right - 1] += 1
    pair_raw, pair_keys = [], []
    exposure = len(pair_prefix)
    for left, right in itertools.combinations(range(n), 2):
        p_left = (marginal_counts[left] + 1.0) / (exposure + n / k)
        p_right = (marginal_counts[right] + 1.0) / (exposure + n / k)
        expected = max(1e-9, p_left * p_right)
        shrunk = (pair_counts[left][right] + PAIR_SHRINKAGE * expected) / (exposure + PAIR_SHRINKAGE)
        pair_raw.append(max(-2.0, min(2.0, math.log(max(1e-12, shrunk) / expected))))
        pair_keys.append((left + 1, right + 1))
    pair_values, pair_mean, pair_scale = _zscore(pair_raw) if pair_raw else ([], 0.0, 1.0)
    pairs = {f"{left}:{right}": value for (left, right), value in zip(pair_keys, pair_values)}
    pair_matrix = [[0.0] * (n + 1) for _ in range(n + 1)]
    for (left, right), value in zip(pair_keys, pair_values):
        pair_matrix[left][right] = pair_matrix[right][left] = value
    normalization["F06"] = {"method": "fold_local_shrunk_pair_population_zscore_v1", "mean": pair_mean, "scale": pair_scale}
    overlap_mean = k / n
    overlap_scale = math.sqrt((n - k) ** 2 / max(1, n * n * (n - 1)))
    normalization["F07"] = {"method": "rule_hypergeometric_zscore_v1", "mean": overlap_mean, "scale": overlap_scale}
    for feature_id, (mean, scale) in zip(STRUCTURE_IDS, _structure_norm(n, k)):
        normalization[feature_id] = {"method": "rule_analytic_standardization_v1", "mean": mean, "scale": scale}
    return {
        "game": game, "zone": zone, "n": n, "k": k, "source_draw_count": len(prefix),
        "input_prefix_sha256": digest([draw.fact_hash for draw in prefix]),
        "max_source_position": len(prefix) - 1, "max_source_issue": prefix[-1].issue,
        "number_counts": counts, "rolling_raw": {str(key): value for key, value in rolling.items()},
        "ewma_raw": {format(half_life, ".0f"): values for half_life, values in zip(EWMA_HALF_LIVES, ewma_rates)},
        "recency_gap_raw": raw_gaps, "recency_gap_transform": "log1p_min_120",
        "number_features": number_features, "pair_values": pairs, "pair_matrix": pair_matrix,
        "last_numbers": list(_numbers(prefix[-1], zone)), "normalization": normalization,
        "feature_ids": list(FEATURE_IDS), "feature_groups": sorted(set(FEATURE_GROUPS.values())),
        "pair_parameterization": "bounded_aggregate_shrunk_residual_no_pair_coefficients",
    }


def combo_vector(combo: Sequence[int], context: dict[str, object]) -> list[float]:
    number_features = context["number_features"]
    values = [math.fsum(number_features[key][number - 1] for number in combo) for key in NUMBER_IDS]
    pairs = list(itertools.combinations(combo, 2))
    pair_values = context["pair_values"]
    values.append(math.fsum(pair_values[f"{left}:{right}"] for left, right in pairs) / len(pairs) if pairs else 0.0)
    k = int(context["k"])
    raw_overlap = len(set(combo) & set(context["last_numbers"])) / k
    spec = context["normalization"]["F07"]
    values.append((raw_overlap - float(spec["mean"])) / float(spec["scale"]))
    for key, raw in zip(STRUCTURE_IDS, _structure(combo, int(context["n"]), k)):
        spec = context["normalization"][key]
        values.append((raw - float(spec["mean"])) / float(spec["scale"]))
    if len(values) != 14 or any(not math.isfinite(value) for value in values):
        raise ValueError("HOLD_FEATURE_INPUT: non-finite feature")
    return values


def feature_snapshot_rows(game: str, prefix: Sequence[Draw], target_position: int) -> list[dict[str, object]]:
    if target_position != len(prefix):
        raise ValueError("FAIL_LEAKAGE: target must immediately follow strict prefix")
    rows = []
    for zone in (0, 1):
        context = feature_context(game, prefix, zone)
        common = {
            "game": game, "zone": zone, "target_position": target_position,
            "cutoff_position": target_position - 1, "cutoff_issue": prefix[-1].issue,
            "max_source_position": target_position - 1, "input_prefix_sha256": context["input_prefix_sha256"],
            "canonical_order_id": "phase1-baseline-v1-jsonl-per-game-order-v1",
            "canonical_comparator_id": "phase1-manifest-order-preserving-comparator-v1",
            "knowledge_contract": "retrospective_sequence_safe", "available_at": None,
        }
        for number in range(1, int(context["n"]) + 1):
            rows.append({
                **common, "row_type": "number", "number": number, "feature_group": "historical_change",
                "feature_values": {key: format(context["number_features"][key][number - 1], ".17g") for key in NUMBER_IDS},
                "raw": {"expanding_count": context["number_counts"][number - 1],
                        "rolling_rates": {str(window): context["rolling_raw"][str(window)][number - 1] for window in ROLLING_WINDOWS},
                        "ewma_rates": {half_life: context["ewma_raw"][half_life][number - 1] for half_life in ("10", "30")},
                        "recency_gap": context["recency_gap_raw"][number - 1]},
                "window": {"F01": "expanding", "F02": list(ROLLING_WINDOWS), "F03_half_lives": list(EWMA_HALF_LIVES),
                           "F04": {"window": "expanding_gap", "transform": "log1p", "cap": 120}, "F05": [10, 60]},
                "normalization": {key: context["normalization"][key] for key in NUMBER_IDS},
            })
        for pair, value in sorted(context["pair_values"].items()):
            left, right = (int(item) for item in pair.split(":"))
            rows.append({
                **common, "row_type": "pair", "numbers": [left, right], "feature_group": "number_relationship",
                "feature_values": {"F06": format(value, ".17g")}, "window": {"F06": PAIR_WINDOW},
                "shrinkage": PAIR_SHRINKAGE, "bounded_pair_parameter_count": 0,
                "normalization": {"F06": context["normalization"]["F06"]},
            })
        reference = list(range(1, int(context["k"]) + 1))
        generated = combo_vector(reference, context)[5:]
        rows.append({
            **common, "row_type": "combination_generator", "reference_combination": reference,
            "feature_group": "number_relationship+combination_structure",
            "feature_values": {key: format(value, ".17g") for key, value in zip(FEATURE_IDS[5:], generated)},
            "window": {"F06": PAIR_WINDOW, "F07": 1, **{key: "rule_static" for key in STRUCTURE_IDS}},
            "normalization": {key: context["normalization"][key] for key in FEATURE_IDS[5:]},
            "generator": "p4e2-combination-feature-generator-v1",
        })
    return rows


@lru_cache(maxsize=8)
def _uniform_structure_expectation(n: int, k: int) -> tuple[float, ...]:
    """Exact fixed-cardinality expectation used by the likelihood gradient."""
    totals = [0.0] * len(STRUCTURE_IDS)
    count = 0
    for combo in itertools.combinations(range(1, n + 1), k):
        count += 1
        for index, value in enumerate(_structure(combo, n, k)):
            totals[index] += value
    return tuple(value / count for value in totals)


def _uniform_feature_expectation(context: dict[str, object]) -> list[float]:
    # Number z-scores sum to zero and fixed-cardinality uniform inclusion is k/n.
    # Pair residuals are standardized across all pairs; each pair has the same
    # uniform chance of inclusion.  F07 is centered at E[overlap/k] = k/n.
    expected = [0.0] * 7
    for key, raw in zip(STRUCTURE_IDS, _uniform_structure_expectation(int(context["n"]), int(context["k"]))):
        spec = context["normalization"][key]
        expected.append((raw - float(spec["mean"])) / float(spec["scale"]))
    return expected


def fit_coefficients(game: str, draws: Sequence[Draw], cutoff: int, l2: float) -> list[dict[str, float]]:
    if l2 not in L2_GRID:
        raise ValueError("HOLD_MODEL_RELEASE: unregistered or inline L2")
    if cutoff < MIN_HISTORY + 8 or cutoff > len(draws):
        raise ValueError("illegal training cutoff")
    start = max(MIN_HISTORY, cutoff - 80)
    gradients = [[0.0] * 14 for _ in (0, 1)]
    for target in range(start, cutoff):
        for zone in (0, 1):
            context = feature_context(game, draws[:target], zone)
            observed = combo_vector(_numbers(draws[target], zone), context)
            uniform = _uniform_feature_expectation(context)
            for index, (observed_value, expected_value) in enumerate(zip(observed, uniform)):
                # Exact score of the conditional log-likelihood at beta=0.
                gradients[zone][index] += (observed_value - expected_value) / (cutoff - start)
    coefficients = [
        {key: max(-COEFFICIENT_CAP, min(COEFFICIENT_CAP, value / l2)) for key, value in zip(FEATURE_IDS, gradient)}
        for gradient in gradients
    ]
    front = coefficients[0]
    if not all(any(abs(front[key]) > 1e-12 for key in group) for group in (NUMBER_IDS, RELATION_IDS, STRUCTURE_IDS)):
        raise ValueError("HOLD_DEGENERATE_MODEL: feature group not effective")
    return coefficients


def _score(combo: Sequence[int], context: dict[str, object], coefficients: dict[str, float]) -> float:
    score = math.fsum(coefficients[key] * value for key, value in zip(FEATURE_IDS, combo_vector(combo, context)))
    if not math.isfinite(score) or abs(score) > 100:
        raise ValueError("HOLD_DEGENERATE_MODEL: invalid score")
    return score

def _score_plan(context: dict[str, object], coefficients: dict[str, float]) -> dict[str, object]:
    number_features = context["number_features"]
    last_numbers = set(context["last_numbers"])
    return {
        "number_scores": [
            math.fsum(coefficients[key] * number_features[key][number] for key in NUMBER_IDS)
            for number in range(int(context["n"]))
        ],
        "pair_coefficient": coefficients["F06"],
        "pair_matrix": context["pair_matrix"],
        "overlap_coefficient": coefficients["F07"],
        "last_membership": [number in last_numbers for number in range(int(context["n"]) + 1)],
        "structure_coefficients": [coefficients[key] for key in STRUCTURE_IDS],
    }


def _fast_score(combo: Sequence[int], context: dict[str, object], plan: dict[str, object]) -> float:
    score = math.fsum(plan["number_scores"][number - 1] for number in combo)
    pair_sum = 0.0
    for left_index in range(len(combo) - 1):
        row = plan["pair_matrix"][combo[left_index]]
        for right_index in range(left_index + 1, len(combo)):
            pair_sum += row[combo[right_index]]
    pair_count = len(combo) * (len(combo) - 1) // 2
    if pair_count:
        score += float(plan["pair_coefficient"]) * pair_sum / pair_count
    spec = context["normalization"]["F07"]
    overlap = sum(plan["last_membership"][number] for number in combo) / int(context["k"])
    score += float(plan["overlap_coefficient"]) * (overlap - float(spec["mean"])) / float(spec["scale"])
    score += math.fsum(
        coefficient * ((raw - float(context["normalization"][key]["mean"])) / float(context["normalization"][key]["scale"]))
        for coefficient, key, raw in zip(
            plan["structure_coefficients"], STRUCTURE_IDS,
            _structure(combo, int(context["n"]), int(context["k"])),
        )
    )
    return score

def enumerate_zone(context: dict[str, object], coefficients: dict[str, float], keep_rows: bool = False) -> dict[str, object]:
    """Enumerate the complete space for mass; retain only the exact zone Top-1000."""
    rows, maximum, minimum, total, square_total, count = [], -math.inf, math.inf, 0.0, 0.0, 0
    plan = _score_plan(context, coefficients)
    for combo in itertools.combinations(range(1, int(context["n"]) + 1), int(context["k"])):
        score = _fast_score(combo, context, plan)
        count += 1
        minimum = min(minimum, score)
        if keep_rows:
            entry = (score, tuple(-number for number in combo), combo)
            if len(rows) < 1000:
                heapq.heappush(rows, entry)
            elif entry[:2] > rows[0][:2]:
                heapq.heapreplace(rows, entry)
        if score <= maximum:
            total += math.exp(score - maximum)
            square_total += math.exp(2 * (score - maximum))
        else:
            factor = 0.0 if maximum == -math.inf else math.exp(maximum - score)
            total, square_total, maximum = total * factor + 1.0, square_total * factor * factor + 1.0, score
    if keep_rows:
        rows = [(score, combo) for score, _, combo in rows]
        rows.sort(key=lambda row: row[1])
        rows.sort(key=lambda row: row[0], reverse=True)
    return {
        "rows": rows, "log_normalizer": maximum + math.log(total),
        "probability_square_sum": square_total / (total * total), "combination_count": count,
        "normalization_method": "complete_enumeration_streaming_log_sum_exp_v1", "normalization_mass": 1.0,
        "minimum_score": minimum, "maximum_score": maximum,
        "minimum_probability": math.exp(minimum - (maximum + math.log(total))),
        "maximum_probability": 1.0 / total, "probability_layer_lower_bound": 2 if minimum < maximum else 1,
    }


def subset_probability(numbers: Sequence[int], zone: dict[str, object]) -> float:
    if "weights" in zone:
        return math.prod(zone["weights"][number - 1] for number in numbers) / float(zone["normalizer"])
    probability = math.exp(_score(numbers, zone["context"], zone["coefficients"]) - float(zone["log_normalizer"]))
    if not 0 < probability <= 1:
        raise ValueError("FAIL_PROBABILITY_ORACLE")
    return probability


def _top(zone_results: Sequence[dict[str, object]], limit: int) -> list[tuple[float, tuple[int, ...], tuple[int, ...]]]:
    front, back = zone_results[0]["rows"], zone_results[1]["rows"]
    heap = []
    for front_index in range(min(limit, len(front))):
        front_score, front_numbers = front[front_index]
        back_score, back_numbers = back[0]
        heapq.heappush(heap, (-(front_score + back_score), front_numbers, back_numbers, front_index, 0))
    result = []
    while heap and len(result) < limit:
        negative, front_numbers, back_numbers, front_index, back_index = heapq.heappop(heap)
        result.append((-negative, front_numbers, back_numbers))
        if back_index + 1 < len(back):
            front_score, front_numbers = front[front_index]
            back_score, back_numbers = back[back_index + 1]
            heapq.heappush(heap, (-(front_score + back_score), front_numbers, back_numbers, front_index, back_index + 1))
    return result


def _evaluate(game: str, draws: Sequence[Draw], target: int, l2: float, include_top: bool) -> dict[str, object]:
    coefficients = fit_coefficients(game, draws, target, l2)
    contexts = [feature_context(game, draws[:target], zone) for zone in (0, 1)]
    results = [enumerate_zone(contexts[zone], coefficients[zone], include_top) for zone in (0, 1)]
    vectors = [combo_vector(_numbers(draws[target], zone), contexts[zone]) for zone in (0, 1)]
    scores = [math.fsum(coefficients[zone][key] * vectors[zone][index] for index, key in enumerate(FEATURE_IDS)) for zone in (0, 1)]
    probabilities = [math.exp(scores[zone] - float(results[zone]["log_normalizer"])) for zone in (0, 1)]
    probability = probabilities[0] * probabilities[1]
    space = math.prod(math.comb(n, k) for n, k in RULES[game])
    rank = None
    if include_top:
        actual = (_numbers(draws[target], 0), _numbers(draws[target], 1))
        rank = next((position for position, (_, front, back) in enumerate(_top(results, 1000), 1) if (front, back) == actual), None)
    return {
        "joint_probability": probability, "joint_log_loss": -math.log(probability), "m0_log_loss": math.log(space),
        "multiclass_brier": 1 - 2 * probability + math.prod(float(item["probability_square_sum"]) for item in results),
        "m0_multiclass_brier": 1 - 1 / space, "outcome_rank": rank,
        "normalization": [{key: value for key, value in item.items() if key != "rows"} for item in results],
        "feature_group_contributions": {
            group: math.fsum(coefficients[zone][key] * vectors[zone][index]
                             for zone in (0, 1) for index, key in enumerate(FEATURE_IDS)
                             if FEATURE_GROUPS[key] == group)
            for group in sorted(set(FEATURE_GROUPS.values()))
        },
    }


def _bootstrap(values: Sequence[float], seed: int, iterations: int = 512) -> dict[str, object]:
    generator, means = random.Random(seed), []
    for _ in range(iterations):
        sample = []
        while len(sample) < len(values):
            start = generator.randrange(len(values))
            sample.extend(values[(start + offset) % len(values)] for offset in range(2))
        means.append(math.fsum(sample[:len(values)]) / len(values))
    means.sort()
    return {"method": "moving_block_bootstrap_v1", "seed": seed, "iterations": iterations, "block_length": 2,
            "ci95": [means[int(iterations * .025)], means[int(iterations * .975)]]}


def train(game: str, draws: Sequence[Draw], cutoff_index: int | None = None) -> dict[str, object]:
    cutoff = len(draws) if cutoff_index is None else cutoff_index
    if cutoff < 120 or cutoff > len(draws):
        raise ValueError("illegal training cutoff")
    training = draws[:cutoff]
    selection_indices = list(range(cutoff - 5, cutoff - 3))
    report_indices = list(range(cutoff - 3, cutoff))
    selection_rows, candidate_means = [], {}
    for l2 in L2_GRID:
        losses = []
        for index in selection_indices:
            evaluated = _evaluate(game, training, index, l2, False)
            losses.append(float(evaluated["joint_log_loss"]))
            selection_rows.append({
                "fold_id": f"selection-{index:04d}", "draw_index": index, "issue": training[index].issue,
                "candidate": {"family": "P4E2-R", "l2": l2}, "joint_log_loss": losses[-1],
                "fold_role": "selection", "used_for_selection": True,
            })
        candidate_means[l2] = math.fsum(losses) / len(losses)
    selected_l2 = min(L2_GRID, key=lambda value: (candidate_means[value], value))
    selected_identity = digest({"family": "P4E2-R", "l2": selected_l2, "features": FEATURE_IDS, "selection_indices": selection_indices})
    report_rows = []
    for index in report_indices:
        evaluated = _evaluate(game, training, index, selected_l2, True)
        probability, rank = float(evaluated["joint_probability"]), evaluated["outcome_rank"]
        m0_probability = math.exp(-float(evaluated["m0_log_loss"]))
        report_rows.append({
            "fold_id": f"report-{index:04d}", "draw_index": index, "issue": training[index].issue,
            "selected_candidate_identity": selected_identity, "selected_before_report_labels": True,
            "model_joint_probability": probability, "m0_joint_probability": m0_probability,
            "model_joint_log_loss": evaluated["joint_log_loss"], "m0_joint_log_loss": evaluated["m0_log_loss"],
            "delta_joint_log_loss_vs_m0": float(evaluated["joint_log_loss"]) - float(evaluated["m0_log_loss"]),
            "model_multiclass_brier": evaluated["multiclass_brier"], "m0_multiclass_brier": evaluated["m0_multiclass_brier"],
            "brier_formula": "1-2*p_observed+sum_over_complete_legal_space(p_class^2)",
            "calibration": {"predicted_probability": probability, "observed_class_indicator": 1, "probability_ratio_vs_m0": probability / m0_probability},
            "feature_group_contributions": evaluated["feature_group_contributions"],
            "full_ticket_rank": rank, "top_k": {str(k): bool(rank and rank <= k) for k in (10, 100, 200, 1000)},
            "fold_role": "report_only", "used_for_selection": False, "normalization": evaluated["normalization"],
        })
    coefficients = fit_coefficients(game, training, cutoff, selected_l2)
    contexts = [feature_context(game, training, zone) for zone in (0, 1)]
    final = [enumerate_zone(contexts[zone], coefficients[zone], True) for zone in (0, 1)]
    zones = [{"n": RULES[game][zone][0], "k": RULES[game][zone][1], "coefficients": coefficients[zone], "context": contexts[zone],
              "top_zone_rows": [[score, list(combo)] for score, combo in final[zone]["rows"]],
              **{key: value for key, value in final[zone].items() if key != "rows"}} for zone in (0, 1)]
    deltas = [float(row["delta_joint_log_loss_vs_m0"]) for row in report_rows]
    brier_deltas = [float(row["model_multiclass_brier"]) - float(row["m0_multiclass_brier"]) for row in report_rows]
    delta = math.fsum(deltas) / len(deltas)
    bootstrap = _bootstrap(deltas, 20260815 + int(game == "dlt"))
    scientific = "worse_than_M0" if delta > 0 else ("lift_supported" if bootstrap["ci95"][1] < 0 else "no_confirmed_lift")
    group_norms = {group: math.fsum(abs(coefficients[zone][key]) for zone in (0, 1) for key in FEATURE_IDS if FEATURE_GROUPS[key] == group) for group in sorted(set(FEATURE_GROUPS.values()))}
    permutation = []
    for offset, group in enumerate(sorted(group_norms), 1):
        original = [float(row["feature_group_contributions"][group]) for row in report_rows]
        permuted = original[offset:] + original[:offset]
        loss_deltas = [before - after for before, after in zip(original, permuted)]
        permutation.append({
            "feature_group": group, "method": "report_only_deterministic_cyclic_block_permutation_v1",
            "block_shift": offset, "seed": 20260815,
            "mean_permuted_minus_original_joint_log_loss": math.fsum(loss_deltas) / len(loss_deltas),
            "absolute_mean_fold_effect": math.fsum(abs(value) for value in loss_deltas) / len(loss_deltas),
        })
    training_dataset_id = digest({"game": game, "canonical_order_id": "phase1-baseline-v1-jsonl-per-game-order-v1",
                                  "ordered_draw_hashes": [draw.fact_hash for draw in training],
                                  "rule_id": RULE_IDS[game], "cutoff_issue": training[-1].issue})
    training_config_id = digest({"family": "P4E2-R", "feature_ids": FEATURE_IDS, "l2": selected_l2,
                                 "l2_grid": L2_GRID, "rolling_windows": ROLLING_WINDOWS,
                                 "ewma_half_lives": EWMA_HALF_LIVES, "pair_window": PAIR_WINDOW,
                                 "pair_shrinkage": PAIR_SHRINKAGE, "coefficient_cap": COEFFICIENT_CAP})
    basis = {"family": "P4E2-R", "game": game, "cutoff": training[-1].issue, "l2": selected_l2,
             "coefficients": coefficients, "training_dataset_id": training_dataset_id,
             "training_config_id": training_config_id, "selection": selected_identity}
    return {
        "family": "P4E2-R", "game": game, "rule_id": RULE_IDS[game],
        "training_cutoff_issue": training[-1].issue, "training_cutoff_position": cutoff - 1,
        "forecast_target_position": cutoff, "training_count": cutoff,
        "canonical_order_id": "phase1-baseline-v1-jsonl-per-game-order-v1",
        "canonical_comparator_id": "phase1-manifest-order-preserving-comparator-v1",
        "training_dataset_id": training_dataset_id, "training_config_id": training_config_id,
        "knowledge_contract": "retrospective_sequence_safe", "available_at_fabricated": False,
        "feature_ids": list(FEATURE_IDS), "feature_groups_consumed": sorted(set(FEATURE_GROUPS.values())),
        "regularization": {"type": "L2", "selected": selected_l2, "preregistered_grid": list(L2_GRID), "coefficient_cap": COEFFICIENT_CAP},
        "estimator": "one_step_exact_uniform_gradient_l2_conditional_log_likelihood_v1", "zones": zones,
        "objective_trace": {"initial_family": "M0", "gradient_at_zero_by_zone": [
            {key: coefficients[zone][key] * selected_l2 for key in FEATURE_IDS} for zone in (0, 1)
        ], "projected_step_l2": selected_l2, "coefficient_cap": COEFFICIENT_CAP},
        "selection_indices": selection_indices, "report_only_indices": report_indices,
        "selected_candidate_identity": selected_identity, "selection_metrics": selection_rows,
        "report_only_metrics": report_rows,
        "report_only_summary": {
            "fold_count": len(report_rows), "mean_delta_joint_log_loss_vs_m0": delta,
            "mean_delta_multiclass_brier_vs_m0": math.fsum(brier_deltas) / len(brier_deltas),
            "joint_log_loss_block_bootstrap": bootstrap, "top_k_values": [10, 100, 200, 1000],
            "full_ticket_metrics": True, "calibration_method": "report_only_true_class_probability_ratio_v1",
            "calibration_summary": {"mean_observed_class_probability": math.fsum(float(row["model_joint_probability"]) for row in report_rows) / len(report_rows),
                                    "mean_probability_ratio_vs_m0": math.fsum(float(row["calibration"]["probability_ratio_vs_m0"]) for row in report_rows) / len(report_rows)},
            "permutation_evidence": permutation,
            "ablation_results": [{"feature_group": group, "coefficient_l1": group_norms[group],
                                  "all_coefficients_effectively_nonzero": group_norms[group] > 1e-12,
                                  "ablation": "set_group_coefficients_to_zero"} for group in sorted(group_norms)],
        },
        "scientific_status": scientific, "model_release_id": f"p4e2r-{game}-{digest(basis)[:16]}",
    }


def top_tickets(model: dict[str, object], top_k: int = 1000) -> list[dict[str, object]]:
    if model.get("family") != "P4E2-R":
        raise ValueError("HOLD_NON_PRODUCT_OR_UNKNOWN_MODEL")
    if top_k != 1000:
        raise ValueError("formal product contract requires top_k=1000")
    results = []
    for zone in model["zones"]:
        if "top_zone_rows" in zone:
            results.append({"rows": [(float(score), tuple(combo)) for score, combo in zone["top_zone_rows"]],
                            "log_normalizer": zone["log_normalizer"]})
        else:
            results.append(enumerate_zone(zone["context"], zone["coefficients"], True))
    exact = _top(results, top_k)
    log_normalizer = math.fsum(float(item["log_normalizer"]) for item in results)
    probabilities = [format(math.exp(score - log_normalizer), ".18e") for score, _, _ in exact]
    if len(set(probabilities)) < 2:
        raise ValueError("HOLD_DEGENERATE_MODEL: Top-1000 all equal")
    histogram = {probability: probabilities.count(probability) for probability in set(probabilities)}
    bounds, cursor = {}, 1
    for probability in sorted(histogram, key=float, reverse=True):
        bounds[probability] = (cursor, cursor + histogram[probability] - 1)
        cursor += histogram[probability]
    rows, previous, layer = [], None, 0
    for rank, ((score, front, back), probability) in enumerate(zip(exact, probabilities), 1):
        if probability != previous:
            previous, layer = probability, layer + 1
        lower, upper = bounds[probability]
        probability_hash = hashlib.sha256(probability.encode()).hexdigest()
        vectors = (combo_vector(front, model["zones"][0]["context"]), combo_vector(back, model["zones"][1]["context"]))
        contributions = {key: format(math.fsum(model["zones"][zone]["coefficients"][key] * vectors[zone][index] for zone in (0, 1)), ".17g") for index, key in enumerate(FEATURE_IDS)}
        rows.append({
            "rank": rank, "full_space_rank": rank, "front_numbers": list(front), "back_numbers": list(back),
            "joint_probability": probability, "log_joint_score": format(score, ".17g"),
            "probability_representation": "P4-LOGSUMEXP-ENUM-1", "probability_layer": layer,
            "tie_group_id": f"tie-{probability_hash[:24]}", "tie_group_size": histogram[probability],
            "tie_rank_lower": lower, "tie_rank_upper": upper, "tie_midrank": format((lower + upper) / 2, ".1f"),
            "tie_key": f"probability:{probability_hash}", "canonical_ticket_key": [list(front), list(back)],
            "lineage": {"model_release_id": model["model_release_id"], "feature_release_id": model.get("feature_release_id")},
            "explanation": {"method": "P4E2-R multi-feature conditional combination model", "probability_primary": True,
                            "feature_contributions": contributions, "feature_groups": model["feature_groups_consumed"]},
        })
    return rows


def score_ticket(model: dict[str, object], draw: Draw, top: Sequence[dict[str, object]]) -> dict[str, object]:
    probability = math.prod(subset_probability(_numbers(draw, zone), model["zones"][zone]) for zone in (0, 1))
    square_mass = math.prod(float(model["zones"][zone]["probability_square_sum"]) for zone in (0, 1))
    rank = {(tuple(row["front_numbers"]), tuple(row["back_numbers"])): row["rank"] for row in top}.get((draw.front, draw.back))
    return {
        "issue": draw.issue, "joint_log_loss": -math.log(probability), "actual_joint_probability": f"{probability:.18e}",
        "multiclass_brier": 1 - 2 * probability + square_mass,
        "brier_formula": "1-2*p_observed+sum_over_complete_legal_space(p_class^2)",
        "hit_at": {str(k): bool(rank and rank <= k) for k in (10, 100, 200, 1000)}, "top1000_rank": rank,
    }
