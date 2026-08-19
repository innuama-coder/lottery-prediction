from __future__ import annotations

import argparse
import itertools
import json
import math
import random
from pathlib import Path
from statistics import mean

from lottery_system.phase4.p4e2_model import (
    FEATURE_GROUPS,
    FEATURE_IDS,
    _evaluate_coefficients,
    _fast_score,
    _score_plan,
    combo_vector,
    feature_context,
    fit_coefficients,
)
from lottery_system.phase4.real_common import RULES, canonical, digest
from lottery_system.phase4.real_model import load_draws, write_once


ROOT = Path(__file__).resolve().parents[2]
R12 = ROOT / "artifacts/phase-4/P4-P4E2-20260815-r12"


def ranks(values):
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        value = (cursor + end - 1) / 2
        for index in order[cursor:end]:
            result[index] = value
        cursor = end
    return result


def correlation(left, right):
    lm, rm = mean(left), mean(right)
    numerator = math.fsum((x - lm) * (y - rm) for x, y in zip(left, right))
    denominator = math.sqrt(math.fsum((x - lm) ** 2 for x in left) * math.fsum((y - rm) ** 2 for y in right))
    return 0.0 if denominator < 1e-15 else numerator / denominator


def jacobi_eigenvalues(matrix):
    values = [list(row) for row in matrix]
    n = len(values)
    for _ in range(100 * n * n):
        p, q = max(((i, j) for i in range(n) for j in range(i + 1, n)), key=lambda pair: abs(values[pair[0]][pair[1]]))
        if abs(values[p][q]) < 1e-12:
            break
        angle = 0.5 * math.atan2(2 * values[p][q], values[q][q] - values[p][p])
        c, s = math.cos(angle), math.sin(angle)
        app, aqq, apq = values[p][p], values[q][q], values[p][q]
        values[p][p] = c * c * app - 2 * s * c * apq + s * s * aqq
        values[q][q] = s * s * app + 2 * s * c * apq + c * c * aqq
        values[p][q] = values[q][p] = 0.0
        for index in range(n):
            if index in (p, q):
                continue
            aip, aiq = values[index][p], values[index][q]
            values[index][p] = values[p][index] = c * aip - s * aiq
            values[index][q] = values[q][index] = s * aip + c * aiq
    return sorted((max(0.0, values[index][index]) for index in range(n)), reverse=True)


def sample_combos(n: int, k: int, seed: int, count: int = 4096):
    generator, observed = random.Random(seed), set()
    while len(observed) < min(count, math.comb(n, k)):
        observed.add(tuple(sorted(generator.sample(range(1, n + 1), k))))
    return sorted(observed)


def matrix_audit(game, draws, cutoff, zone, combos):
    context = feature_context(game, draws[:cutoff], zone)
    rows = [combo_vector(combo, context) for combo in combos]
    columns = list(zip(*rows))
    variance = {feature: math.fsum((value - mean(column)) ** 2 for value in column) / len(column) for feature, column in zip(FEATURE_IDS, columns)}
    missingness = {feature: sum(not math.isfinite(value) for value in column) / len(column) for feature, column in zip(FEATURE_IDS, columns)}
    pearson = [[correlation(left, right) for right in columns] for left in columns]
    rank_columns = [ranks(column) for column in columns]
    spearman = [[correlation(left, right) for right in rank_columns] for left in rank_columns]
    eigenvalues = jacobi_eigenvalues(pearson)
    total = math.fsum(eigenvalues)
    proportions = [value / total for value in eigenvalues if value > 1e-12]
    effective_rank = math.exp(-math.fsum(value * math.log(value) for value in proportions))
    positive = [value for value in eigenvalues if value > 1e-10]
    condition = math.sqrt(max(positive) / min(positive)) if positive else math.inf
    maximum_pair = max((abs(spearman[i][j]), FEATURE_IDS[i], FEATURE_IDS[j]) for i in range(14) for j in range(i + 1, 14))
    return {
        "cutoff_position": cutoff - 1, "zone": zone, "sample_method": "seeded_unique_uniform_number_set_sample_v1",
        "sample_size": len(rows), "feature_variance": variance, "missingness": missingness,
        "pearson_correlation": pearson, "spearman_correlation": spearman,
        "maximum_absolute_spearman_pair": {"absolute_correlation": maximum_pair[0], "features": list(maximum_pair[1:])},
        "correlation_eigenvalues": eigenvalues, "effective_rank": effective_rank, "condition_number": condition,
    }


def zone_dispersion(game, draws, zone, coefficients):
    context = feature_context(game, draws, zone)
    plan = _score_plan(context, coefficients)
    scores = []
    maximum = -math.inf
    total = weighted_score = square_total = 0.0
    for combo in itertools.combinations(range(1, int(context["n"]) + 1), int(context["k"])):
        score = _fast_score(combo, context, plan)
        scores.append(score)
        if score <= maximum:
            weight = math.exp(score - maximum)
            total += weight
            weighted_score += weight * score
            square_total += weight * weight
        else:
            factor = 0.0 if maximum == -math.inf else math.exp(maximum - score)
            total, weighted_score, square_total, maximum = total * factor + 1, weighted_score * factor + score, square_total * factor * factor + 1, score
    scores.sort()
    log_normalizer = maximum + math.log(total)
    entropy = log_normalizer - weighted_score / total
    quantiles = {str(q): scores[round(q * (len(scores) - 1))] - log_normalizer for q in (0.0, 0.01, 0.1, 0.5, 0.9, 0.99, 1.0)}
    score_mean = math.fsum(scores) / len(scores)
    return {
        "combination_count": len(scores), "minimum_score": scores[0], "maximum_score": scores[-1],
        "score_variance_uniform_space": math.fsum((value - score_mean) ** 2 for value in scores) / len(scores),
        "log_normalizer": log_normalizer, "entropy": entropy, "effective_support": math.exp(entropy),
        "probability_square_sum": square_total / (total * total), "log_probability_quantiles": quantiles,
        "maximum_minimum_probability_ratio": math.exp(scores[-1] - scores[0]),
    }


def top1000_audit(game):
    path = next((R12 / f"forecasts/{game}").glob("after-*/top1000.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    probabilities = [float(row["joint_probability"]) for row in rows]
    fronts = [tuple(row["front_numbers"]) for row in rows]
    groups = {}
    for row in rows:
        groups.setdefault(tuple(row["front_numbers"]), []).append(float(row["log_joint_score"]))
    overall = mean(float(row["log_joint_score"]) for row in rows)
    total_variance = mean((float(row["log_joint_score"]) - overall) ** 2 for row in rows)
    between = math.fsum(len(values) * (mean(values) - overall) ** 2 for values in groups.values()) / len(rows)
    within = math.fsum(math.fsum((value - mean(values)) ** 2 for value in values) for values in groups.values()) / len(rows)
    normalized = [value / math.fsum(probabilities) for value in probabilities]
    entropy = -math.fsum(value * math.log(value) for value in normalized)
    counts = sorted((len(values) for values in groups.values()), reverse=True)
    return {
        "path": str(path.relative_to(ROOT)), "unique_front_number_sets": len(groups),
        "front_set_repetition_counts": {"maximum": counts[0], "median": counts[len(counts) // 2], "minimum": counts[-1]},
        "share_tickets_in_repeated_front_sets": sum(count for count in counts if count > 1) / len(rows),
        "front_between_score_variance_share": between / total_variance if total_variance else 0.0,
        "back_within_front_score_variance_share": within / total_variance if total_variance else 0.0,
        "top1000_conditional_entropy": entropy, "top1000_conditional_effective_support": math.exp(entropy),
        "top1_top10_probability_ratio": probabilities[0] / probabilities[9],
        "top1_top1000_probability_ratio": probabilities[0] / probabilities[-1],
        "minimum_probability": probabilities[-1], "maximum_probability": probabilities[0],
        "structural_explanation": f"back zone has only {math.comb(*RULES[game][1])} legal sets, so high-scoring front sets repeat across back-area variants",
    }


def coefficient_stability(game, draws, cutoffs):
    fitted = [{"cutoff": cutoff, "zones": fit_coefficients(game, draws, cutoff, 72.0)} for cutoff in cutoffs]
    summary = []
    for zone in (0, 1):
        for feature in FEATURE_IDS:
            values = [row["zones"][zone][feature] for row in fitted]
            center = mean(values)
            sd = math.sqrt(mean((value - center) ** 2 for value in values))
            signs = [0 if abs(value) < 1e-15 else (1 if value > 0 else -1) for value in values]
            mode = max((-1, 0, 1), key=lambda sign: signs.count(sign))
            summary.append({"zone": zone, "feature_id": feature, "mean": center, "sd": sd,
                            "coefficient_of_variation": sd / abs(center) if abs(center) > 1e-15 else None,
                            "modal_sign": mode, "modal_sign_share": signs.count(mode) / len(signs), "values": values})
    return {"fold_coefficients": fitted, "summary": summary}


def importance(game, draws, targets):
    rows = []
    groups = sorted(set(FEATURE_GROUPS.values()))
    for target in targets:
        coefficients = fit_coefficients(game, draws, target, 72.0)
        baseline = _evaluate_coefficients(game, draws, target, coefficients, False)
        ablations = []
        for group in groups:
            changed = [{feature: (0.0 if FEATURE_GROUPS[feature] == group else zone[feature]) for feature in FEATURE_IDS} for zone in coefficients]
            scored = _evaluate_coefficients(game, draws, target, changed, False)
            ablations.append({"feature_group": group, "ablated_minus_full_joint_log_loss": scored["joint_log_loss"] - baseline["joint_log_loss"],
                              "ablated_minus_full_multiclass_brier": scored["multiclass_brier"] - baseline["multiclass_brier"]})
        donor = targets[(targets.index(target) + 1) % len(targets)]
        contexts = [feature_context(game, draws[:target], zone) for zone in (0, 1)]
        observed = [combo_vector(draws[target].front if zone == 0 else draws[target].back, contexts[zone]) for zone in (0, 1)]
        donated = [combo_vector(draws[donor].front if zone == 0 else draws[donor].back, contexts[zone]) for zone in (0, 1)]
        permutations = []
        log_normalizer = math.fsum(item["log_normalizer"] for item in baseline["normalization"])
        for group in groups:
            score = math.fsum(coefficients[zone][feature] * (donated[zone][index] if FEATURE_GROUPS[feature] == group else observed[zone][index])
                              for zone in (0, 1) for index, feature in enumerate(FEATURE_IDS))
            permutations.append({"feature_group": group, "donor_position": donor,
                                 "permuted_minus_full_joint_log_loss": (-score + log_normalizer) - baseline["joint_log_loss"]})
        rows.append({"target_position": target, "baseline_joint_log_loss": baseline["joint_log_loss"],
                     "baseline_multiclass_brier": baseline["multiclass_brier"], "ablations": ablations, "permutations": permutations})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draws", type=Path, default=ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/phase-4e3/delivery-20260819/audit")
    args = parser.parse_args()
    cutoffs = [128, 140, 152, 164, 176, 200]
    for game in ("ssq", "dlt"):
        draws = load_draws(args.draws, game)
        samples = {zone: sample_combos(*RULES[game][zone], 20260819 + 10 * int(game == "dlt") + zone) for zone in (0, 1)}
        matrices = [matrix_audit(game, draws, cutoff, zone, samples[zone]) for cutoff in cutoffs for zone in (0, 1)]
        stability = coefficient_stability(game, draws, cutoffs)
        final_coefficients = stability["fold_coefficients"][-1]["zones"]
        dispersions = [zone_dispersion(game, draws, zone, final_coefficients[zone]) for zone in (0, 1)]
        payload = {
            "artifact_type": "phase4e3_p4e2_feature_model_audit", "game": game,
            "accepted_baseline_commit": "61afa5be20b49e5545d106d490cdc0c33cba9dc4", "accepted_release": "P4-P4E2-20260815-r12",
            "feature_ids": list(FEATURE_IDS), "fold_matrix_audits": matrices, "coefficient_stability": stability,
            "walk_forward_group_importance": importance(game, draws, [130, 142, 154, 166]),
            "full_legal_space_dispersion": {
                "zones": dispersions, "joint_combination_count": math.prod(row["combination_count"] for row in dispersions),
                "joint_entropy": math.fsum(row["entropy"] for row in dispersions),
                "joint_effective_support": math.prod(row["effective_support"] for row in dispersions),
                "joint_maximum_minimum_probability_ratio": math.prod(row["maximum_minimum_probability_ratio"] for row in dispersions),
                "joint_score_variance_uniform_space": math.fsum(row["score_variance_uniform_space"] for row in dispersions),
            },
            "top1000_dispersion_and_concentration": top1000_audit(game),
            "diagnosis": [
                "F01-F05 are multiple transforms of the same marginal occurrence history and exhibit high rank correlation.",
                "The one-step gradient divided by L2=72 produces small coefficients and intentionally calibrated near-uniform probabilities.",
                "The back legal space is much smaller than the front legal space, so each strong front set is paired with many back variants before the next front set enters Top-1000.",
                "Concentration of repeated front sets is a product-space ranking geometry effect, not evidence of higher winning odds.",
            ],
        }
        payload["audit_sha256"] = digest(payload)
        write_once(args.output / f"{game}-p4e2-audit.json", payload)
        print(game, payload["audit_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
