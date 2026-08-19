from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import subprocess
from pathlib import Path
from statistics import mean

from lottery_system.phase4.p4e2_model import NUMBER_IDS, feature_context
from lottery_system.phase4.real_common import Draw, RULES, canonical, digest
from lottery_system.phase4.real_model import load_draws, write_jsonl_once, write_once
from lottery_system.phase4e3.model import (
    _feature_matrix,
    _weights,
    build_context,
    elementary,
    inclusion_probabilities,
    score_zone_observation,
    subset_probability,
    top_zone,
    zone_distribution,
)
from scripts.phase4e3.run_r12_audit import correlation, jacobi_eigenvalues, ranks
from scripts.phase4e3.run_selection import fit_family


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "artifacts/phase-4e3/delivery-20260819"
FAMILIES = (
    "C01_SURPRISE_REGIME", "C02_RENEWAL_HAZARD", "C03_TRANSITION",
    "C04_GRAPH", "C05_SET_SHAPE", "C06_GATED_COMPOSITE_NONLINEAR",
)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_shadow(selection: dict[str, object]) -> str:
    if selection["strongest_selection_candidate"]:
        return str(selection["strongest_selection_candidate"])
    modelled = [(family, row) for family, row in selection["families"].items() if row.get("outer_folds")]
    if not modelled:
        raise ValueError("HOLD_NO_SHADOW_CANDIDATE")
    return min(modelled, key=lambda item: (item[1]["nested_mean_delta_joint_log_loss_vs_m0"], item[0]))[0]


def distribution_from_matrix(game, zone, fitted, matrix):
    weights = _weights(matrix, fitted["coefficients"])
    n, k = RULES[game][zone]
    normalizer = elementary(weights, k)
    return {
        "n": n, "k": k, "weights": weights, "normalizer": normalizer,
        "inclusion_probabilities": inclusion_probabilities(weights, k, normalizer),
        "probability_square_sum": elementary([weight * weight for weight in weights], k) / normalizer**2,
    }


def joint_score(game, observed, distributions):
    zones = [score_zone_observation(observed[zone], distributions[zone]) for zone in (0, 1)]
    probability = math.prod(row["subset_probability"] for row in zones)
    return {
        "joint_probability": probability,
        "joint_log_loss": -math.log(probability),
        "multiclass_brier": 1 - 2 * probability + math.prod(row["probability_square_sum"] for row in zones),
    }


def top_product(front, back, limit=1000):
    heap = []
    for front_probability, front_numbers in front:
        for back_probability, back_numbers in back:
            probability = front_probability * back_probability
            tie = tuple(-value for value in (*front_numbers, *back_numbers))
            entry = (probability, tie, front_numbers, back_numbers)
            if len(heap) < limit:
                heapq.heappush(heap, entry)
            elif entry[:2] > heap[0][:2]:
                heapq.heapreplace(heap, entry)
    result = [(probability, front_numbers, back_numbers) for probability, _, front_numbers, back_numbers in heap]
    result.sort(key=lambda row: (row[1], row[2]))
    result.sort(key=lambda row: row[0], reverse=True)
    return result


def shadow_rows(game, distributions):
    zone_rows = [top_zone(distribution, 1000) for distribution in distributions]
    return [
        {
            "rank": rank, "front_numbers": list(front), "back_numbers": list(back),
            "joint_probability": probability, "log_joint_probability": math.log(probability),
        }
        for rank, (probability, front, back) in enumerate(top_product(*zone_rows), 1)
    ]


def top_audit(game, rows):
    probabilities = [row["joint_probability"] for row in rows]
    groups: dict[tuple[int, ...], list[float]] = {}
    for row in rows:
        groups.setdefault(tuple(row["front_numbers"]), []).append(row["log_joint_probability"])
    overall = mean(row["log_joint_probability"] for row in rows)
    total = mean((row["log_joint_probability"] - overall) ** 2 for row in rows)
    between = math.fsum(len(values) * (mean(values) - overall) ** 2 for values in groups.values()) / len(rows)
    within = math.fsum(math.fsum((value - mean(values)) ** 2 for value in values) for values in groups.values()) / len(rows)
    normalized = [value / math.fsum(probabilities) for value in probabilities]
    entropy = -math.fsum(value * math.log(value) for value in normalized)
    counts = sorted((len(values) for values in groups.values()), reverse=True)
    if len(groups) == len(rows):
        explanation = (
            f"The back space has only {math.comb(*RULES[game][1])} legal sets, but front-score differences dominate "
            "this shadow cutoff: every Top-1000 row has a distinct front set, so back-zone variants of the same "
            "front fall below the cutoff."
        )
    else:
        explanation = (
            f"The front contributes {math.comb(*RULES[game][0])} legal sets while the back contributes only "
            f"{math.comb(*RULES[game][1])}; {len(rows) - len(groups)} Top-1000 rows therefore reuse a front set "
            "with another back-zone variant. This is product-ranking geometry, not evidence of increased winning probability."
        )
    return {
        "unique_front_number_sets": len(groups),
        "front_set_repetition_counts": {"maximum": counts[0], "median": counts[len(counts) // 2], "minimum": counts[-1]},
        "share_tickets_in_repeated_front_sets": sum(value for value in counts if value > 1) / len(rows),
        "front_between_log_probability_variance_share": between / total if total else 0.0,
        "back_within_front_log_probability_variance_share": within / total if total else 0.0,
        "top1000_conditional_entropy": entropy, "top1000_conditional_effective_support": math.exp(entropy),
        "top1_top10_probability_ratio": probabilities[0] / probabilities[9],
        "top1_top1000_probability_ratio": probabilities[0] / probabilities[-1],
        "structural_explanation": explanation,
    }


def matrix_audit(game, draws, cutoff, zone, feature_ids):
    current = build_context(game, draws[:cutoff], zone)
    accepted = feature_context(game, draws[:cutoff], zone)
    columns = [current["feature_values"][feature] for feature in feature_ids]
    pearson = [[correlation(left, right) for right in columns] for left in columns]
    rank_columns = [ranks(column) for column in columns]
    spearman = [[correlation(left, right) for right in rank_columns] for left in rank_columns]
    eigenvalues = jacobi_eigenvalues(pearson)
    total = math.fsum(eigenvalues)
    proportions = [value / total for value in eigenvalues if value > 1e-12]
    positive = [value for value in eigenvalues if value > 1e-10]
    cross = []
    for feature, values in zip(feature_ids, columns):
        cross.append({
            "feature_id": feature,
            "maximum_absolute_spearman_vs_r12_number_features": max(
                abs(correlation(ranks(values), ranks(accepted["number_features"][old]))) for old in NUMBER_IDS
            ),
        })
    return {
        "cutoff_position": cutoff - 1, "zone": zone, "sample_unit": "complete_number_population",
        "sample_size": len(columns[0]),
        "feature_variance": {feature: mean((value - mean(column)) ** 2 for value in column) for feature, column in zip(feature_ids, columns)},
        "missingness": {feature: sum(not math.isfinite(value) for value in column) / len(column) for feature, column in zip(feature_ids, columns)},
        "pearson_correlation": pearson, "spearman_correlation": spearman,
        "cross_model_spearman": cross,
        "correlation_eigenvalues": eigenvalues,
        "effective_rank": math.exp(-math.fsum(value * math.log(value) for value in proportions)),
        "condition_number": math.sqrt(max(positive) / min(positive)) if positive else None,
    }


def stability(game, draws, family, feature_ids, config, cutoffs):
    fits = []
    for cutoff in cutoffs:
        zones = [fit_family(game, draws, cutoff, zone, family, config, feature_ids)["model"] for zone in (0, 1)]
        fits.append({"cutoff": cutoff, "zones": zones})
    summary = []
    for zone in (0, 1):
        for index, feature in enumerate(feature_ids):
            values = [row["zones"][zone]["coefficients"][index] for row in fits]
            center = mean(values)
            sd = math.sqrt(mean((value - center) ** 2 for value in values))
            signs = [0 if abs(value) < 1e-15 else (1 if value > 0 else -1) for value in values]
            mode = max((-1, 0, 1), key=lambda sign: signs.count(sign))
            summary.append({
                "zone": zone, "feature_id": feature, "mean": center, "sd": sd,
                "coefficient_of_variation": sd / abs(center) if abs(center) > 1e-15 else None,
                "modal_sign": mode, "modal_sign_share": signs.count(mode) / len(signs), "values": values,
            })
    return {"fold_fits": fits, "summary": summary}


def importance(game, draws, fitted, feature_ids):
    rows = []
    for target in range(176, 200):
        contexts = [build_context(game, draws[:target], zone) for zone in (0, 1)]
        matrices = [_feature_matrix(contexts[zone], feature_ids) for zone in (0, 1)]
        baseline_distributions = [distribution_from_matrix(game, zone, fitted[zone], matrices[zone]) for zone in (0, 1)]
        observed = [draws[target].front, draws[target].back]
        baseline = joint_score(game, observed, baseline_distributions)
        ablations, permutations = [], []
        for feature_index, feature in enumerate(feature_ids):
            ablated = []
            permuted = []
            for zone in (0, 1):
                changed_fit = {**fitted[zone], "coefficients": list(fitted[zone]["coefficients"])}
                changed_fit["coefficients"][feature_index] = 0.0
                ablated.append(distribution_from_matrix(game, zone, changed_fit, matrices[zone]))
                changed_matrix = [list(row) for row in matrices[zone]]
                values = [row[feature_index] for row in matrices[zone]]
                shift = feature_index + 1
                for number, row in enumerate(changed_matrix):
                    row[feature_index] = values[(number + shift) % len(values)]
                permuted.append(distribution_from_matrix(game, zone, fitted[zone], changed_matrix))
            ablated_score = joint_score(game, observed, ablated)
            permuted_score = joint_score(game, observed, permuted)
            ablations.append({
                "feature_id": feature,
                "ablated_minus_full_joint_log_loss": ablated_score["joint_log_loss"] - baseline["joint_log_loss"],
                "ablated_minus_full_multiclass_brier": ablated_score["multiclass_brier"] - baseline["multiclass_brier"],
            })
            permutations.append({
                "feature_id": feature, "method": "deterministic_number_id_cyclic_shift_v1",
                "shift": feature_index + 1,
                "permuted_minus_full_joint_log_loss": permuted_score["joint_log_loss"] - baseline["joint_log_loss"],
                "permuted_minus_full_multiclass_brier": permuted_score["multiclass_brier"] - baseline["multiclass_brier"],
            })
        rows.append({"target_position": target, "baseline": baseline, "ablations": ablations, "permutations": permutations})
    summary = {}
    for feature in feature_ids:
        summary[feature] = {
            "mean_ablation_delta_joint_log_loss": mean(next(item for item in row["ablations"] if item["feature_id"] == feature)["ablated_minus_full_joint_log_loss"] for row in rows),
            "mean_permutation_delta_joint_log_loss": mean(next(item for item in row["permutations"] if item["feature_id"] == feature)["permuted_minus_full_joint_log_loss"] for row in rows),
        }
    return {"method": "24_draw_report_only_diagnostic_no_tuning", "rows": rows, "summary": summary}


def dispersion(game, distribution):
    log_weights = [math.log(value) for value in distribution["weights"]]
    expected_score = math.fsum(p * value for p, value in zip(distribution["inclusion_probabilities"], log_weights))
    entropy = math.log(distribution["normalizer"]) - expected_score
    population_mean = mean(log_weights)
    population_variance = mean((value - population_mean) ** 2 for value in log_weights)
    return {
        "combination_count": math.comb(int(distribution["n"]), int(distribution["k"])),
        "entropy": entropy, "effective_support": math.exp(entropy),
        "score_variance_uniform_space": int(distribution["k"]) * (int(distribution["n"]) - int(distribution["k"])) / (int(distribution["n"]) - 1) * population_variance,
        "maximum_minimum_probability_ratio": math.exp(math.fsum(sorted(log_weights, reverse=True)[: int(distribution["k"])]) - math.fsum(sorted(log_weights)[: int(distribution["k"])])),
        "probability_square_sum": distribution["probability_square_sum"],
    }


def mutation_evidence(game, draws, fitted, feature_ids):
    def identity(values):
        contexts = [build_context(game, values[:176], zone) for zone in (0, 1)]
        distributions = [zone_distribution(game, values[:176], zone, fitted[zone]) for zone in (0, 1)]
        return digest({"contexts": contexts, "distributions": [{key: row[key] for key in ("weights", "normalizer", "inclusion_probabilities")} for row in distributions]})

    baseline = identity(draws)
    future = list(draws)
    for position in (176, 199):
        draw = future[position]
        future[position] = Draw(draw.issue, tuple(reversed(draw.front)), tuple(reversed(draw.back)), "f" * 64)
    prefix = list(draws)
    draw = prefix[175]
    prefix[175] = Draw(draw.issue, tuple(reversed(draw.front)), tuple(reversed(draw.back)), "e" * 64)
    return {
        "prediction_target_position": 176, "maximum_source_position": 175,
        "baseline_identity": baseline, "future_and_target_mutation_identity": identity(future),
        "future_and_target_mutation_invariant_pass": baseline == identity(future),
        "strict_prefix_mutation_identity": identity(prefix),
        "strict_prefix_mutation_detected_pass": baseline != identity(prefix),
        "feature_ids": list(feature_ids),
    }


def release_inventory(contract):
    roots = [path for path in sorted((ROOT / "artifacts").iterdir()) if path.is_dir() and path.name != "phase-4e3"]
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", contract["baseline"]["commit"], "--", *[str(path.relative_to(ROOT)) for path in roots]],
        cwd=ROOT, text=True,
    ).splitlines()
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", *[str(path.relative_to(ROOT)) for path in roots]],
        cwd=ROOT, text=True,
    ).splitlines()
    summaries = []
    for root in roots:
        rows = [{"path": str(path.relative_to(root)), "size": path.stat().st_size, "sha256": file_sha(path)} for path in sorted(root.rglob("*")) if path.is_file()]
        summaries.append({"path": str(root.relative_to(ROOT)), "file_count": len(rows), "working_tree_inventory_sha256": digest(rows)})
    release_path = "artifacts/phase-4/P4-P4E2-20260815-r12"
    accepted_tree = subprocess.check_output(["git", "rev-parse", f"{contract['baseline']['commit']}:{release_path}"], cwd=ROOT, text=True).strip()
    current_tree = subprocess.check_output(["git", "rev-parse", f"HEAD:{release_path}"], cwd=ROOT, text=True).strip()
    return {
        "artifact_type": "phase4e3_prior_release_byte_inventory_check",
        "comparison_commit": contract["baseline"]["commit"], "roots": summaries,
        "changed_tracked_paths": changed, "untracked_prior_release_paths": untracked,
        "r12_contract_inventory_digest": contract["baseline"]["release_tree_inventory_digest"],
        "r12_accepted_git_tree": accepted_tree, "r12_current_git_tree": current_tree,
        "r12_git_tree_match": accepted_tree == current_tree,
        "all_prior_release_bytes_unchanged": not changed and not untracked and accepted_tree == current_tree,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draws", type=Path, default=ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    contract = json.loads((ROOT / "config/phase4e3/phase-contract.json").read_text())
    if file_sha(args.draws) != contract["data_authority"]["sha256"]:
        raise ValueError("FAIL_DATA_AUTHORITY")
    decisions = {}
    for game in ("ssq", "dlt"):
        draws = load_draws(args.draws, game)
        selection_path = args.output / f"selection/{game}-selection-receipt.json"
        selection = json.loads(selection_path.read_text())
        family = selected_shadow(selection)
        row = selection["families"][family]
        feature_ids, config = row["feature_ids"], row["final_config"]
        fitted_wrapped = [fit_family(game, draws, 176, zone, family, config, feature_ids) for zone in (0, 1)]
        if not all(item["kind"] == "number" for item in fitted_wrapped):
            raise ValueError("HOLD_DELIVERY_REQUIRES_NUMBER_MODEL")
        fitted = [item["model"] for item in fitted_wrapped]
        distributions = [zone_distribution(game, draws, zone, fitted[zone]) for zone in (0, 1)]
        rows = shadow_rows(game, distributions)
        model_identity = digest({"game": game, "family": family, "feature_ids": feature_ids, "config": config, "fitted": fitted})
        rows = [{**item, "game": game, "target_position": 200, "model_identity": model_identity} for item in rows]
        top_path = args.output / f"shadow/{game}-top1000.jsonl"
        write_jsonl_once(top_path, rows)
        top_summary = top_audit(game, rows)
        forecast = {
            "artifact_type": "phase4e3_deterministic_shadow_forecast", "game": game,
            "target_position": 200, "fit_input_cutoff_position": 175, "maximum_training_label_position": 173,
            "maximum_dynamic_feature_source_position": 199,
            "family": family, "feature_ids": feature_ids, "config": config, "model_identity": model_identity,
            "selection_eligible": family in selection["eligible_for_report_only"], "serving": False,
            "ticket_count": len(rows), "top1000_path": str(top_path.relative_to(ROOT)),
            "top1000_sha256": file_sha(top_path), "top1000_audit": top_summary,
        }
        forecast["forecast_sha256"] = digest(forecast)
        write_once(args.output / f"shadow/{game}-forecast.json", forecast)
        snapshot = {
            "artifact_type": "phase4e3_feature_snapshot", "game": game, "target_position": 200,
            "maximum_source_position": 199, "family": family, "feature_ids": feature_ids,
            "zones": [build_context(game, draws, zone) for zone in (0, 1)],
        }
        snapshot["snapshot_sha256"] = digest(snapshot)
        write_once(args.output / f"snapshots/{game}-features.json", snapshot)
        cutoffs = [128, 140, 152, 164, 176, 200]
        dispersions = [dispersion(game, distribution) for distribution in distributions]
        candidate_audit = {
            "artifact_type": "phase4e3_candidate_feature_model_audit", "game": game, "family": family,
            "feature_ids": feature_ids, "config": config,
            "fold_matrix_audits": [matrix_audit(game, draws, cutoff, zone, feature_ids) for cutoff in cutoffs for zone in (0, 1)],
            "coefficient_stability": stability(game, draws, family, feature_ids, config, cutoffs),
            "report_only_ablation_permutation": importance(game, draws, fitted, feature_ids),
            "full_legal_space_dispersion": {
                "zones": dispersions, "joint_combination_count": math.prod(item["combination_count"] for item in dispersions),
                "joint_entropy": math.fsum(item["entropy"] for item in dispersions),
                "joint_effective_support": math.prod(item["effective_support"] for item in dispersions),
                "joint_maximum_minimum_probability_ratio": math.prod(item["maximum_minimum_probability_ratio"] for item in dispersions),
                "joint_score_variance_uniform_space": math.fsum(item["score_variance_uniform_space"] for item in dispersions),
            },
            "top1000_dispersion_and_concentration": top_summary,
            "interpretation": [
                "Transition features alter fixed-cardinality number weights while preserving exact normalization.",
                top_summary["structural_explanation"],
                "Probability dispersion is diagnostic only and was not used to override proper-score promotion gates.",
            ],
        }
        candidate_audit["audit_sha256"] = digest(candidate_audit)
        write_once(args.output / f"audit/{game}-candidate-audit.json", candidate_audit)
        mutation = mutation_evidence(game, draws, fitted, feature_ids)
        mutation["artifact_type"] = "phase4e3_negative_mutation_evidence"
        mutation["evidence_sha256"] = digest(mutation)
        write_once(args.output / f"replay/{game}-mutation-evidence.json", mutation)
        report = json.loads((args.output / f"report/{game}-report-only.json").read_text())
        model_card = {
            "artifact_type": "phase4e3_model_card", "game": game, "model_identity": model_identity,
            "family": family, "features": feature_ids, "configuration": config,
            "intended_use": "research_shadow_only", "serving_eligible": False,
            "selection_eligible": family in selection["eligible_for_report_only"],
            "report_only_preliminary_promotion_gate_pass": report["preliminary_promotion_gate_pass"],
            "proper_score_evidence": report["comparisons"], "calibration": report["calibration"],
            "limitations": [
                "Lottery outcomes are highly stochastic and no winning outcome is promised.",
                "The 24-draw report-only sample does not establish multiplicity-corrected lift.",
                "Top-1000 spread and repeated front sets are diagnostics, not promotion evidence.",
            ],
            "decision": "retain_immutable_r12_serving",
        }
        model_card["model_card_sha256"] = digest(model_card)
        write_once(args.output / f"model-cards/{game}-model-card.json", model_card)
        decisions[game] = {
            "selection_eligible": family in selection["eligible_for_report_only"],
            "selection_direction_pass": report["selection_direction_pass"],
            "statistical_gate_pass": report["statistical_gate_pass"],
            "calibration_pass": report["calibration"]["pass"],
            "preliminary_promotion_gate_pass": report["preliminary_promotion_gate_pass"],
        }
    inventory = release_inventory(contract)
    inventory["inventory_check_sha256"] = digest(inventory)
    write_once(args.output / "inventory/prior-release-byte-inventory.json", inventory)
    final_status = "IMPROVED_SERVING_ACCEPTED" if all(row["preliminary_promotion_gate_pass"] for row in decisions.values()) else "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION"
    decision = {
        "artifact_type": "phase4e3_final_decision", "phase": "P4E3_feature_strengthening",
        "status": final_status, "promotion_decision": final_status == "IMPROVED_SERVING_ACCEPTED",
        "games": decisions, "immutable_r12_remains_serving": final_status != "IMPROVED_SERVING_ACCEPTED",
        "probability_spread_used_for_promotion": False, "adverse_evidence_retained": True,
        "prior_release_bytes_unchanged": inventory["all_prior_release_bytes_unchanged"],
    }
    decision["decision_sha256"] = digest(decision)
    write_once(args.output / "decision/final-decision.json", decision)
    excluded = {"manifest/delivery-manifest.json", "acceptance/independent-acceptance.json", "acceptance/full-test-receipt.json"}
    paths = [path for path in sorted(args.output.rglob("*")) if path.is_file() and str(path.relative_to(args.output)) not in excluded]
    entries = [{"path": str(path.relative_to(args.output)), "size": path.stat().st_size, "sha256": file_sha(path)} for path in paths]
    manifest = {
        "artifact_type": "phase4e3_core_delivery_manifest", "delivery_id": "phase4e3-delivery-20260819",
        "entry_count": len(entries), "entries": entries,
        "excluded_final_append_only": sorted(excluded), "status": final_status,
    }
    manifest["manifest_sha256"] = digest(manifest)
    write_once(args.output / "manifest/delivery-manifest.json", manifest)
    print(final_status, decision["decision_sha256"], manifest["manifest_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
