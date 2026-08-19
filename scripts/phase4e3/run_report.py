from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
from pathlib import Path
from statistics import mean

from lottery_system.phase4.p4e2_model import _fast_score, _score_plan, feature_context, fit_coefficients
from lottery_system.phase4.real_common import RULES, digest
from lottery_system.phase4.real_model import load_draws, write_once
from lottery_system.phase4e3.model import score_shape_observation, score_zone_observation, shape_distribution, zone_distribution
from scripts.phase4e3.run_selection import fit_family, m0_zone


ROOT = Path(__file__).resolve().parents[2]


def bootstrap(values, seed, iterations=4096, block_length=4):
    generator, means = random.Random(seed), []
    for _ in range(iterations):
        sample = []
        while len(sample) < len(values):
            start = generator.randrange(len(values))
            sample.extend(values[(start + offset) % len(values)] for offset in range(block_length))
        means.append(math.fsum(sample[: len(values)]) / len(values))
    means.sort()
    p_one_sided = (1 + sum(value >= 0 for value in means)) / (iterations + 1)
    return {"method": "moving_block_bootstrap_v1", "iterations": iterations, "block_length": block_length, "seed": seed,
            "ci95": [means[int(iterations * 0.025)], means[int(iterations * 0.975)]], "one_sided_p_mean_ge_zero": p_one_sided}


def r12_zone_distribution(game, draws, target, zone, coefficients):
    context = feature_context(game, draws[:target], zone)
    plan = _score_plan(context, coefficients)
    n, k = RULES[game][zone]
    maximum, total, square_total = -math.inf, 0.0, 0.0
    marginal_totals = [0.0] * n
    for combo in itertools.combinations(range(1, n + 1), k):
        score = _fast_score(combo, context, plan)
        if score <= maximum:
            weight = math.exp(score - maximum)
            total += weight
            square_total += weight * weight
            for number in combo:
                marginal_totals[number - 1] += weight
        else:
            factor = 0.0 if maximum == -math.inf else math.exp(maximum - score)
            total, square_total, maximum = total * factor + 1.0, square_total * factor * factor + 1.0, score
            marginal_totals = [value * factor for value in marginal_totals]
            for number in combo:
                marginal_totals[number - 1] += 1.0
    return {"context": context, "plan": plan, "log_normalizer": maximum + math.log(total),
            "inclusion_probabilities": [value / total for value in marginal_totals],
            "probability_square_sum": square_total / (total * total)}


def score_r12(numbers, distribution):
    score = _fast_score(numbers, distribution["context"], distribution["plan"])
    probability = math.exp(score - distribution["log_normalizer"])
    observed = set(numbers)
    marginals = distribution["inclusion_probabilities"]
    return {
        "subset_probability": probability, "joint_log_loss": -math.log(probability),
        "inclusion_log_loss": -math.fsum(math.log(max(1e-15, marginal if index + 1 in observed else 1 - marginal)) for index, marginal in enumerate(marginals)) / len(marginals),
        "inclusion_brier": math.fsum((marginal - float(index + 1 in observed)) ** 2 for index, marginal in enumerate(marginals)) / len(marginals),
        "probability_square_sum": distribution["probability_square_sum"],
    }


def combine(zones):
    probability = math.prod(zone["subset_probability"] for zone in zones)
    return {"joint_probability": probability, "joint_log_loss": -math.log(probability),
            "multiclass_brier": 1 - 2 * probability + math.prod(zone["probability_square_sum"] for zone in zones),
            "front_inclusion_log_loss": zones[0]["inclusion_log_loss"],
            "back_inclusion_log_loss": zones[1]["inclusion_log_loss"],
            "front_inclusion_brier": zones[0]["inclusion_brier"],
            "back_inclusion_brier": zones[1]["inclusion_brier"],
            "mean_zone_inclusion_log_loss": mean(zone["inclusion_log_loss"] for zone in zones),
            "mean_zone_inclusion_brier": mean(zone["inclusion_brier"] for zone in zones)}


def choose_candidate(selection):
    if selection["strongest_selection_candidate"]:
        return selection["strongest_selection_candidate"]
    modelled = [(family, row) for family, row in selection["families"].items() if row.get("outer_folds")]
    return min(modelled, key=lambda item: item[1]["nested_mean_delta_joint_log_loss_vs_m0"])[0] if modelled else None


def holm_table(selection, candidate, raw_p):
    """Apply the frozen six-family Holm correction, retaining unopened hypotheses."""
    hypotheses = [
        {
            "candidate": family,
            "raw_one_sided_p": raw_p if family == candidate else 1.0,
            "report_only_evaluated": family == candidate,
            "selection_eligible": family in selection["eligible_for_report_only"],
        }
        for family in (
            "C01_SURPRISE_REGIME", "C02_RENEWAL_HAZARD", "C03_TRANSITION",
            "C04_GRAPH", "C05_SET_SHAPE", "C06_GATED_COMPOSITE_NONLINEAR",
        )
    ]
    ordered = sorted(hypotheses, key=lambda row: (row["raw_one_sided_p"], row["candidate"]))
    running = 0.0
    for rank, row in enumerate(ordered, 1):
        running = max(running, min(1.0, (len(ordered) - rank + 1) * row["raw_one_sided_p"]))
        row["holm_rank"] = rank
        row["holm_adjusted_p"] = min(1.0, running)
    return ordered


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=ROOT / "config/phase4e3/phase-contract.json")
    parser.add_argument("--draws", type=Path, default=ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl")
    parser.add_argument("--selection", type=Path, default=ROOT / "artifacts/phase-4e3/delivery-20260819/selection")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/phase-4e3/delivery-20260819/report")
    parser.add_argument("--game", choices=("ssq", "dlt"), required=True)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text())
    if hashlib.sha256(args.draws.read_bytes()).hexdigest() != contract["data_authority"]["sha256"]:
        raise ValueError("FAIL_DATA_AUTHORITY")
    for game in (args.game,):
        selection_path = args.selection / f"{game}-selection-receipt.json"
        selection = json.loads(selection_path.read_text())
        receipt_hash = selection.pop("receipt_sha256")
        if digest(selection) != receipt_hash or selection["report_only_labels_read"] or selection["report_only_first_position"] != 176:
            raise ValueError("FAIL_SELECTION_RECEIPT")
        selection["receipt_sha256"] = receipt_hash
        candidate = choose_candidate(selection)
        if candidate is None:
            raise ValueError("HOLD_NO_REPRODUCIBLE_CANDIDATE")
        candidate_row = selection["families"][candidate]
        selection_eligible = candidate in selection["eligible_for_report_only"]
        feature_ids = candidate_row["feature_ids"]
        config = candidate_row["final_config"]
        draws = load_draws(args.draws, game)
        candidate_fitted = [fit_family(game, draws, 176, zone, candidate, config, feature_ids) for zone in (0, 1)]
        candidate_shapes = [shape_distribution(game, zone, item["model"]) if item["kind"] == "shape" else None for zone, item in enumerate(candidate_fitted)]
        r12_coefficients = fit_coefficients(game, draws, 176, 72.0)
        rows = []
        for target in range(176, 200):
            candidate_zones, r12_zones, m0_zones = [], [], []
            for zone in (0, 1):
                observed = draws[target].front if zone == 0 else draws[target].back
                fitted = candidate_fitted[zone]
                if fitted["kind"] == "uniform":
                    candidate_score = m0_zone(game, zone, observed)
                elif fitted["kind"] == "shape":
                    candidate_score = score_shape_observation(observed, candidate_shapes[zone])
                else:
                    candidate_score = score_zone_observation(observed, zone_distribution(game, draws[:target], zone, fitted["model"]))
                candidate_zones.append(candidate_score)
                r12_zones.append(score_r12(observed, r12_zone_distribution(game, draws, target, zone, r12_coefficients[zone])))
                m0_zones.append(m0_zone(game, zone, observed))
            candidate_metrics, r12_metrics, m0_metrics = combine(candidate_zones), combine(r12_zones), combine(m0_zones)
            rows.append({
                "target_position": target, "issue": draws[target].issue, "label_fact_hash": draws[target].fact_hash,
                "candidate": candidate_metrics,
                "r12": r12_metrics, "m0": m0_metrics,
                "delta_joint_log_loss_vs_r12": candidate_metrics["joint_log_loss"] - r12_metrics["joint_log_loss"],
                "delta_joint_log_loss_vs_m0": candidate_metrics["joint_log_loss"] - m0_metrics["joint_log_loss"],
                "fold_role": "report_only", "used_for_selection": False,
            })
        comparisons = {}
        for comparator in ("r12", "m0"):
            values = [row[f"delta_joint_log_loss_vs_{comparator}"] for row in rows]
            seed = int(contract["evaluation"]["bootstrap"][f"seed_{game}"])
            evidence = bootstrap(values, seed)
            table = holm_table(selection, candidate, evidence["one_sided_p_mean_ge_zero"])
            selected_hypothesis = next(row for row in table if row["candidate"] == candidate)
            evidence.update({"mean_delta_joint_log_loss": mean(values), "favorable_draw_count": sum(value <= 0 for value in values),
                             "holm_family_size": len(table), "holm_method": "holm_bonferroni_one_sided_familywise_0.05",
                             "holm_table": table, "holm_adjusted_p": selected_hypothesis["holm_adjusted_p"]})
            comparisons[comparator] = evidence
        candidate_brier = mean(row["candidate"]["mean_zone_inclusion_brier"] for row in rows)
        candidate_ratio_vs_m0 = mean(row["candidate"]["joint_probability"] / row["m0"]["joint_probability"] for row in rows)
        candidate_ratio_vs_r12 = mean(row["candidate"]["joint_probability"] / row["r12"]["joint_probability"] for row in rows)
        r12_ratio_vs_m0 = mean(row["r12"]["joint_probability"] / row["m0"]["joint_probability"] for row in rows)
        calibration = {
            "candidate_mean_zone_inclusion_brier": candidate_brier,
            "r12_mean_zone_inclusion_brier": mean(row["r12"]["mean_zone_inclusion_brier"] for row in rows),
            "m0_mean_zone_inclusion_brier": mean(row["m0"]["mean_zone_inclusion_brier"] for row in rows),
            "candidate_mean_observed_probability_ratio_vs_m0": candidate_ratio_vs_m0,
            "candidate_mean_observed_probability_ratio_vs_r12": candidate_ratio_vs_r12,
            "r12_mean_observed_probability_ratio_vs_m0": r12_ratio_vs_m0,
            "material_absolute_tolerance": 0.002,
        }
        selection_pass = selection_eligible and bool(candidate_row.get("selection_direction_pass"))
        statistical_pass = all(comparisons[key]["mean_delta_joint_log_loss"] < 0
                               and comparisons[key]["ci95"][1] < 0 and comparisons[key]["holm_adjusted_p"] < 0.05
                               and comparisons[key]["favorable_draw_count"] >= 18 for key in ("r12", "m0"))
        brier_pass = candidate_brier <= min(calibration["r12_mean_zone_inclusion_brier"], calibration["m0_mean_zone_inclusion_brier"]) + 0.002
        probability_ratio_pass = candidate_ratio_vs_m0 >= 0.998 and candidate_ratio_vs_r12 >= 0.998
        calibration_pass = brier_pass and probability_ratio_pass
        primary_metric_summary = {
            model: {
                metric: mean(row[model][metric] for row in rows)
                for metric in (
                    "joint_log_loss", "front_inclusion_log_loss", "back_inclusion_log_loss",
                    "front_inclusion_brier", "back_inclusion_brier", "multiclass_brier",
                )
            }
            for model in ("candidate", "r12", "m0")
        }
        payload = {
            "artifact_type": "phase4e3_independent_report_only_evidence", "game": game, "candidate": candidate,
            "candidate_feature_ids": feature_ids, "candidate_config": config,
            "evaluation_role": "promotion_candidate" if selection_eligible else "adverse_shadow_no_promotion",
            "selection_eligible": selection_eligible,
            "selection_receipt_path": str(selection_path.relative_to(ROOT)), "selection_receipt_sha256": receipt_hash,
            "report_positions": [176, 200], "report_count": len(rows), "rows": rows,
            "primary_metric_summary": primary_metric_summary,
            "comparisons": comparisons, "calibration": {**calibration, "inclusion_brier_pass": brier_pass,
                                                            "mean_probability_ratio_pass": probability_ratio_pass,
                                                            "pass": calibration_pass},
            "selection_direction_pass": selection_pass, "statistical_gate_pass": statistical_pass,
            "preliminary_promotion_gate_pass": selection_pass and statistical_pass and calibration_pass,
            "multiple_candidate_correction": "holm_bonferroni_one_sided_familywise_0.05_across_C01_C06",
            "adverse_results_retained": True,
        }
        payload["report_sha256"] = digest(payload)
        write_once(args.output / f"{game}-report-only.json", payload)
        print(game, candidate, payload["preliminary_promotion_gate_pass"], comparisons, payload["report_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
