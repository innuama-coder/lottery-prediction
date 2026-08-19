#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from statistics import mean

import numpy as np

from lottery_system.phase4e5.features import (
    CANDIDATE_BLOCKS, apply_preprocessor, build_feature_rows, candidate_names,
    fit_preprocessor, load_draws, load_metadata, raw_matrix,
)
from lottery_system.phase4e5.metadata import canonical, sha256
from lottery_system.phase4e5.model import fit_model, score_rows, top_tickets


ROOT = Path(__file__).resolve().parents[2]
BRANCH = "origin/codex/phase4e5-exogenous-metadata-20260820-r01"
ROLES = ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json"
REGISTRY = ROOT / "config/phase4e5_registry.json"
METADATA = ROOT / "artifacts/phase-4e5/metadata-audit/dlt-official-metadata.jsonl"
AUDIT = ROOT / "artifacts/phase-4e5/metadata-audit/coverage-audit.json"
SELECTION = ROOT / "artifacts/phase-4e5/selection"
OUTPUT = ROOT / "artifacts/phase-4e5/report"


def verify_selection_checkpoint() -> str:
    subprocess.run(["git", "fetch", "origin", BRANCH.removeprefix("origin/")], cwd=ROOT, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    subprocess.run(["git", "merge-base", "--is-ancestor", head, BRANCH], cwd=ROOT, check=True)
    for game in ("ssq", "dlt"):
        subprocess.run(["git", "cat-file", "-e", f"{head}:artifacts/phase-4e5/selection/{game}-selection-receipt.json"], cwd=ROOT, check=True)
    return head


def calibration(rows: list[dict[str, object]]) -> dict[str, float]:
    probabilities = np.clip(np.asarray([value for row in rows for value in row["probabilities"]]), 1e-9, 1 - 1e-9)
    targets = np.asarray([value for row in rows for value in row["targets"]])
    logits = np.log(probabilities / (1 - probabilities))
    design = np.column_stack((np.ones(len(logits)), logits))
    beta = np.asarray([0.0, 1.0])
    for _ in range(30):
        fitted = 1 / (1 + np.exp(-np.clip(design @ beta, -30, 30)))
        weights = np.maximum(fitted * (1 - fitted), 1e-8)
        delta = np.linalg.solve(design.T @ (weights[:, None] * design) + np.eye(2) * 1e-10, design.T @ (fitted - targets))
        beta -= delta
        if np.max(np.abs(delta)) < 1e-10:
            break
    bins = np.minimum((probabilities * 10).astype(int), 9)
    ece = 0.0
    for bucket in range(10):
        selected = bins == bucket
        if np.any(selected):
            ece += float(np.mean(selected)) * abs(float(np.mean(probabilities[selected]) - np.mean(targets[selected])))
    return {"calibration_intercept": float(beta[0]), "calibration_slope": float(beta[1]), "ece_10_equal_width": ece}


def bootstrap(delta: np.ndarray, seed: int, resamples: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    means = np.empty(resamples)
    for start in range(0, resamples, 1000):
        size = min(1000, resamples - start)
        indices = rng.integers(0, len(delta), size=(size, len(delta)))
        means[start : start + size] = np.mean(delta[indices], axis=1)
    return {
        "mean_delta": float(np.mean(delta)),
        "ci95_lower": float(np.quantile(means, 0.025)),
        "ci95_upper": float(np.quantile(means, 0.975)),
        "one_sided_p": float((1 + np.sum(means >= 0)) / (resamples + 1)),
    }


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    ordered = sorted(pvalues, key=lambda key: (pvalues[key], key))
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for rank, key in enumerate(ordered):
        running = max(running, min(1.0, (count - rank) * pvalues[key]))
        adjusted[key] = running
    return adjusted


def report_game(game: str, roles: dict[str, object], audit: dict[str, object], registry: dict[str, object], checkpoint: str, output: Path) -> dict[str, object]:
    role = roles["games"][game]
    draws = load_draws(ROOT / role["eligible_source"])
    report_start = len(draws) - 120
    train_end = report_start - 1
    train = list(range(train_end))
    report_indices = list(range(report_start, len(draws)))
    metadata = {} if game == "ssq" else load_metadata(METADATA)
    feature_rows = build_feature_rows(game, draws, metadata)
    selection = json.loads((SELECTION / f"{game}-selection-receipt.json").read_text(encoding="utf-8"))
    provincial_enabled = bool(selection["provincial_distribution_enabled"])
    candidates: dict[str, object] = {}
    models: dict[str, object] = {}
    matrices: dict[str, np.ndarray | None] = {}
    for candidate_id in CANDIDATE_BLOCKS:
        selected = selection["candidates"][candidate_id]
        if not selected["eligible"]:
            candidates[candidate_id] = {"candidate_id": candidate_id, "eligible": False, "reason": selected["reason"]}
            matrices[candidate_id] = None
            continue
        config = selected["selected_config"]
        if candidate_id == "B0":
            transformed = None; spec_payload = None
            model = fit_model(game, None, draws, train, 1.0, candidate_id)
        else:
            names = candidate_names(candidate_id, provincial_enabled)
            raw = raw_matrix(feature_rows, names)
            spec = fit_preprocessor(raw, train, names, str(config["numeric_transform"]), tuple(config["winsor_quantiles"]))
            transformed = apply_preprocessor(raw, spec)
            spec_payload = spec.payload()
            model = fit_model(game, transformed, draws, train, float(config["regularization_c"]), candidate_id)
        rows = score_rows(model, transformed, draws, report_indices)
        metrics = {
            "mean_per_ball_bernoulli_log_loss": mean(row["mean_per_ball_bernoulli_log_loss"] for row in rows),
            "mean_per_ball_brier": mean(row["mean_per_ball_brier"] for row in rows),
            **calibration(rows),
        }
        candidates[candidate_id] = {
            "candidate_id": candidate_id, "eligible": True, "selected_config": config,
            "preprocessor": spec_payload, "metrics": metrics, "rows": rows,
        }
        models[candidate_id] = model
        matrices[candidate_id] = transformed
    baseline_rows = candidates["B0"]["rows"]
    comparisons = {}
    pvalues = {}
    for candidate_id in ("C1", "C2", "O1", "O2", "O3"):
        if not candidates[candidate_id]["eligible"]:
            comparisons[candidate_id] = {"eligible": False, "one_sided_p": 1.0}
            pvalues[candidate_id] = 1.0
            continue
        rows = candidates[candidate_id]["rows"]
        log_delta = np.asarray([row["mean_per_ball_bernoulli_log_loss"] - base["mean_per_ball_bernoulli_log_loss"] for row, base in zip(rows, baseline_rows)])
        brier_delta = np.asarray([row["mean_per_ball_brier"] - base["mean_per_ball_brier"] for row, base in zip(rows, baseline_rows)])
        comparisons[candidate_id] = {
            "eligible": True,
            "log_loss": bootstrap(log_delta, 20260820 + (0 if game == "ssq" else 1), 10000),
            "brier": bootstrap(brier_delta, 20261820 + (0 if game == "ssq" else 1), 10000),
        }
        pvalues[candidate_id] = comparisons[candidate_id]["log_loss"]["one_sided_p"]
    adjusted = holm(pvalues)
    for candidate_id in comparisons:
        comparisons[candidate_id]["holm_adjusted_log_loss_p"] = adjusted[candidate_id]
    strongest = selection["strongest_selection_candidate"]
    top1000_hits = 0
    top10_hits = 0
    top_cache = None
    for index in report_indices:
        if strongest == "B0" and top_cache is not None:
            top = top_cache
        else:
            top, _ = top_tickets(models[strongest], None if matrices[strongest] is None else matrices[strongest][index], 1000)
            if strongest == "B0":
                top_cache = top
        actual = (tuple(draws[index]["front"]), tuple(draws[index]["back"]))
        ranks = {(tuple(row["front"]), tuple(row["back"])): row["rank"] for row in top}
        rank = ranks.get(actual)
        top1000_hits += rank is not None
        top10_hits += rank is not None and rank <= 10
    selected_comparison = comparisons.get(strongest)
    selected_metrics = candidates[strongest]["metrics"]
    scientific_pass = bool(
        strongest != "B0" and selected_comparison and selected_comparison["eligible"]
        and selected_comparison["log_loss"]["mean_delta"] < 0
        and selected_comparison["log_loss"]["ci95_upper"] < 0
        and selected_comparison["holm_adjusted_log_loss_p"] < 0.05
        and selected_comparison["brier"]["mean_delta"] <= registry["promotion_gates"]["brier_noninferiority_margin"]
        and 0.8 <= selected_metrics["calibration_slope"] <= 1.2
        and selected_metrics["ece_10_equal_width"] <= 0.03
    )
    receipt = {
        "artifact_type": "phase4e5_report_receipt", "game": game,
        "selection_checkpoint": checkpoint, "selection_receipt_sha256": selection["receipt_sha256"],
        "report_boundary": role["report"], "report_rows_read": 120, "report_evaluations": 1,
        "training_end_exclusive": train_end, "report_start": report_start, "embargo_draws": 1,
        "selected_candidate": strongest, "selected_config": selection["strongest_selection_config"],
        "candidate_metrics": {candidate_id: value.get("metrics") for candidate_id, value in candidates.items()},
        "comparisons_vs_B0": comparisons,
        "top1000_realized_ticket_containment": top1000_hits / 120,
        "top10_shadow_realized_ticket_containment": top10_hits / 120,
        "scientific_gate_pass": scientific_pass,
        "official_comparable_metadata": audit["games"][game]["comparable_official_per_draw_metadata"],
        "promotion_gate_pass": scientific_pass and audit["games"][game]["comparable_official_per_draw_metadata"],
        "probability_spread_adjustment": "none",
    }
    receipt["receipt_sha256"] = sha256(canonical(receipt))
    (output / f"{game}-report-receipt.json").write_bytes(canonical(receipt))
    material = {
        "artifact_type": "phase4e5_report_material", "game": game,
        "candidates": candidates, "models": models,
        "feature_rows_sha256": sha256(canonical(feature_rows)),
    }
    (output / f"{game}-report-material.json").write_bytes(canonical(material))
    return {"game": game, "selected_candidate": strongest, "scientific_gate_pass": scientific_pass, "promotion_gate_pass": receipt["promotion_gate_pass"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    checkpoint = verify_selection_checkpoint()
    roles = json.loads(ROLES.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    games = [report_game(game, roles, audit, registry, checkpoint, args.output) for game in ("ssq", "dlt")]
    result = {
        "artifact_type": "phase4e5_report_summary", "selection_checkpoint": checkpoint,
        "games": games, "all_games_promotion_gate_pass": all(game["promotion_gate_pass"] for game in games),
    }
    result["receipt_sha256"] = sha256(canonical(result))
    (args.output / "report-summary.json").write_bytes(canonical(result))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
