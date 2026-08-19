#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
from pathlib import Path
from statistics import mean, pstdev

import numpy as np

from lottery_system.phase4e4.data import canonical, load_jsonl, sha256_bytes, sha256_file
from lottery_system.phase4e4.model import FAMILIES, combo_features, fit_model, score_block, top_tickets
from run_selection import outer_folds, write_once


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "artifacts/phase-4e4/data-20260819"
SELECTION = ROOT / "artifacts/phase-4e4/selection-20260819"
REPORT = ROOT / "artifacts/phase-4e4/report-20260819"
OUTPUT = ROOT / "artifacts/phase-4e4/delivery-20260819"
R12 = ROOT / "artifacts/phase-4/P4-P4E2-20260815-r12"
E3 = ROOT / "artifacts/phase-4e3/delivery-20260819"


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        value = (cursor + end - 1) / 2.0
        for index in order[cursor:end]:
            result[index] = value
        cursor = end
    return result


def matrix_diagnostics(matrix: np.ndarray, names: list[str]) -> dict[str, object]:
    missing = int(np.size(matrix) - np.count_nonzero(np.isfinite(matrix)))
    variances = np.var(matrix, axis=0)
    active = variances > 1e-15
    standardized = matrix[:, active]
    if standardized.shape[1]:
        standardized = (standardized - np.mean(standardized, axis=0)) / np.maximum(np.std(standardized, axis=0), 1e-12)
        singular = np.linalg.svd(standardized, compute_uv=False)
        effective_rank = int(np.sum(singular > max(1e-12, singular[0] * 1e-10))) if len(singular) else 0
        condition = float(singular[0] / singular[-1]) if len(singular) and singular[-1] > 1e-12 else None
    else:
        effective_rank, condition = 0, None
    pearson_max = 0.0
    spearman_max = 0.0
    if matrix.shape[1] > 1:
        pearson = np.nan_to_num(np.corrcoef(matrix, rowvar=False), nan=0.0)
        pearson_max = float(np.max(np.abs(pearson - np.eye(matrix.shape[1]))))
        ranked = np.asarray([ranks([float(value) for value in matrix[:, column]]) for column in range(matrix.shape[1])]).T
        spearman = np.nan_to_num(np.corrcoef(ranked, rowvar=False), nan=0.0)
        spearman_max = float(np.max(np.abs(spearman - np.eye(matrix.shape[1]))))
    return {
        "row_count": int(matrix.shape[0]), "feature_count": int(matrix.shape[1]), "missing_value_count": missing,
        "variance_by_feature": {name: float(value) for name, value in zip(names, variances)},
        "nonconstant_feature_count": int(np.sum(active)), "maximum_absolute_pearson": pearson_max,
        "maximum_absolute_spearman": spearman_max, "effective_rank": effective_rank, "condition_number": condition,
    }


def sampled_features(context: dict[str, object]) -> np.ndarray:
    if "number_features" in context:
        return np.asarray(context["number_features"], dtype=np.float64)
    n, k = int(context["n"]), int(context["k"])
    combos = itertools.islice(itertools.combinations(range(1, n + 1), k), 4096)
    return np.asarray([combo_features(combo, context) for combo in combos], dtype=np.float64)


def protected_inventory(root: Path) -> dict[str, object]:
    rows = [{"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in sorted(root.rglob("*")) if path.is_file()]
    return {"root": str(root.relative_to(ROOT)), "file_count": len(rows), "files": rows,
            "inventory_sha256": sha256_bytes(canonical(rows))}


def build_game(game: str, output: Path) -> dict[str, object]:
    selection = json.loads((SELECTION / f"{game}-selection-receipt.json").read_text(encoding="utf-8"))
    report_receipt = json.loads((REPORT / f"{game}-report-receipt.json").read_text(encoding="utf-8"))
    material = json.loads((REPORT / f"{game}-report-material.json").read_text(encoding="utf-8"))
    prefix = load_jsonl(DATA / f"selection-prefix/{game}.jsonl", game)
    report = load_jsonl(DATA / f"sealed-report/{game}.jsonl", game)
    draws, cutoff = prefix + report, len(prefix)
    strongest = selection["strongest_selection_candidate"]
    models, diagnostics, stability = {}, {}, {}
    for family in FAMILIES:
        config = selection["candidates"][family]["final_config"]
        model = fit_model(game, draws, cutoff, family, config)
        models[family] = model
        zones = []
        for fitted in model["zones"]:
            matrix = sampled_features(fitted["context"])
            zones.append({"zone": fitted["context"]["zone"], "feature_names": fitted["context"]["feature_names"],
                          "source_prefix_count": fitted["context"]["prefix_count"],
                          "source_prefix_sha256": fitted["context"]["input_sha256"],
                          "coefficients": fitted["coefficients"], "diagnostics": matrix_diagnostics(matrix, list(fitted["context"]["feature_names"]))})
        diagnostics[family] = {"candidate_id": family, "final_config": config, "zones": zones}
        fold_coefficients = [[], []]
        for fold in outer_folds(cutoff):
            fold_config = selection["candidates"][family]["outer_folds"][int(fold["fold"]) - 1]["selected_config"]
            folded = fit_model(game, prefix, int(fold["train_end"]), family, fold_config)
            for zone in (0, 1):
                fold_coefficients[zone].append([float(value) for value in folded["zones"][zone]["coefficients"]])
        stability[family] = {"zones": [
            {"zone": zone, "fold_coefficient_mean": [mean(column) for column in zip(*fold_coefficients[zone])],
             "fold_coefficient_population_sd": [pstdev(column) for column in zip(*fold_coefficients[zone])],
             "fold_count": 6} for zone in (0, 1)
        ]}

    top, summary = top_tickets(models[strongest], 1000)
    top_path = output / "top1000" / f"{game}-{strongest}.jsonl"
    top_payload = b"".join(canonical(row) for row in top)
    top_path.parent.mkdir(parents=True, exist_ok=True)
    if top_path.exists() and top_path.read_bytes() != top_payload:
        raise FileExistsError(f"immutable output collision: {top_path}")
    top_path.write_bytes(top_payload)
    report_hits = []
    ranks_by_ticket = {(tuple(row["front"]), tuple(row["back"])): row["rank"] for row in top}
    for draw in report:
        rank = ranks_by_ticket.get((draw.front, draw.back))
        report_hits.append({"issue": draw.issue, "rank_if_top1000": rank, "top10_hit": rank is not None and rank <= 10,
                            "top1000_hit": rank is not None})
    summary.update({"game": game, "candidate_id": strongest, "top1000_sha256": sha256_file(top_path),
                    "top10_probability_mass": math.fsum(row["joint_probability"] for row in top[:10]),
                    "top1000_probability_mass": math.fsum(row["joint_probability"] for row in top),
                    "report_top10_hits": sum(row["top10_hit"] for row in report_hits),
                    "report_top1000_hits": sum(row["top1000_hit"] for row in report_hits), "report_rank_rows": report_hits})

    base_model = models[strongest]
    base_rows = material["candidates"][strongest]["rows"]
    base_mean = mean(float(row["joint_log_loss"]) for row in base_rows)
    ablations = []
    for zone, fitted in enumerate(base_model["zones"]):
        for index, name in enumerate(fitted["context"]["feature_names"]):
            ablated = copy.deepcopy(base_model)
            ablated["zones"][zone]["coefficients"][index] = 0.0
            rows = score_block(ablated, draws, range(cutoff, cutoff + 60))
            ablations.append({"zone": zone, "feature": name,
                              "mean_joint_log_loss_delta_vs_full": mean(float(row["joint_log_loss"]) for row in rows) - base_mean})
    permutation = list(reversed(range(cutoff, cutoff + 60)))
    permuted = score_block(base_model, draws, permutation)
    experiment = {"candidate_id": strongest, "ablation_rows": ablations,
                  "deterministic_permutation": "reverse_report_order",
                  "permuted_mean_joint_log_loss": mean(float(row["joint_log_loss"]) for row in permuted),
                  "full_mean_joint_log_loss": base_mean}

    comparator_summary = {
        comparator: {metric: mean(float(row[metric]) for row in rows) for metric in ("joint_log_loss", "full_multiclass_brier")}
        for comparator, rows in material["comparators"].items()
    }
    card = {
        "artifact_type": "phase4e4_model_card", "game": game, "candidate_id": strongest,
        "serving_status": "SHADOW_ONLY_NOT_PROMOTED", "frozen_config": selection["candidates"][strongest]["final_config"],
        "selection_direction_pass": selection["candidates"][strongest]["selection_direction_pass"],
        "report_gates": report_receipt["candidate_gates"][strongest],
        "promotion_authority": selection["promotion_authority"] if game != "ssq" else False,
        "training_labels_end_before_report": True, "report_refit": False,
    }
    write_once(output / "features" / f"{game}-diagnostics.json", diagnostics)
    write_once(output / "models" / f"{game}-coefficient-stability.json", stability)
    write_once(output / "models" / f"{game}-model-card.json", card)
    write_once(output / "experiments" / f"{game}-ablation-permutation.json", experiment)
    write_once(output / "diagnostics" / f"{game}-comparators.json", comparator_summary)
    write_once(output / "top1000" / f"{game}-summary.json", summary)
    return {"game": game, "strongest_candidate": strongest, "top1000_sha256": summary["top1000_sha256"],
            "promotion_authority": card["promotion_authority"], "promoted": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = args.output
    games = [build_game(game, output) for game in ("ssq", "dlt")]
    prior = {"r12": protected_inventory(R12), "p4e3": protected_inventory(E3)}
    write_once(output / "audits/prior-release-byte-inventory.json", prior)
    decision = {
        "artifact_type": "phase4e4_delivery_decision", "phase": "P4E4_feature_strengthening",
        "games": games, "all_games_required": True, "serving_release_unchanged": "P4-P4E2-20260815-r12",
        "release_allocation": "FORBIDDEN", "terminal_state": "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION",
        "ssq_promotion_authority": False,
    }
    write_once(output / "decision.json", decision)
    files = [{"path": str(path.relative_to(output)), "sha256": sha256_file(path), "bytes": path.stat().st_size}
             for path in sorted(output.rglob("*")) if path.is_file() and path.name != "core-manifest.json"]
    manifest = {"artifact_type": "phase4e4_core_manifest", "file_count": len(files), "files": files,
                "manifest_sha256": sha256_bytes(canonical(files))}
    write_once(output / "core-manifest.json", manifest)
    print(decision["terminal_state"], manifest["manifest_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
