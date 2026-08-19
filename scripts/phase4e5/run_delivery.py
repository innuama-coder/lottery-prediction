#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import subprocess
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

import numpy as np

from lottery_system.phase4e5.features import (
    BLOCKS, CANDIDATE_BLOCKS, apply_preprocessor, build_feature_rows, candidate_names,
    load_draws, load_metadata, preprocessor_from_payload, raw_matrix,
)
from lottery_system.phase4e5.metadata import canonical, sha256
from lottery_system.phase4e5.model import score_rows, top_tickets


ROOT = Path(__file__).resolve().parents[2]
BASE = "3a65b5331f8ec8cb80d288347103db8a39992654"
ROLES = ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json"
AUDIT = ROOT / "artifacts/phase-4e5/metadata-audit/coverage-audit.json"
METADATA = ROOT / "artifacts/phase-4e5/metadata-audit/dlt-official-metadata.jsonl"
SELECTION = ROOT / "artifacts/phase-4e5/selection"
REPORT = ROOT / "artifacts/phase-4e5/report"
OUTPUT = ROOT / "artifacts/phase-4e5/delivery"
PRIOR_ROOTS = {
    "r12": ROOT / "artifacts/phase-4/P4-P4E2-20260815-r12",
    "p4e3": ROOT / "artifacts/phase-4e3/delivery-20260819",
    "p4e4": ROOT / "artifacts/phase-4e4",
}


def next_draw(game: str, last: dict[str, object]) -> dict[str, object]:
    allowed = {"ssq": {1, 3, 6}, "dlt": {0, 2, 5}}[game]
    cursor = date.fromisoformat(str(last["draw_date"])) + timedelta(days=1)
    while cursor.weekday() not in allowed:
        cursor += timedelta(days=1)
    return {"game": game, "issue": str(int(str(last["issue"])) + 1), "draw_date": cursor.isoformat(), "front": [], "back": []}


def diagnostics(matrix: np.ndarray, names: list[str]) -> dict[str, object]:
    missing = ~np.isfinite(matrix)
    medians = np.asarray([float(np.nanmedian(matrix[:, column])) if np.any(np.isfinite(matrix[:, column])) else 0.0 for column in range(matrix.shape[1])])
    filled = np.where(missing, medians, matrix)
    variances = np.var(filled, axis=0)
    active = variances > 1e-15
    standardized = (filled[:, active] - np.mean(filled[:, active], axis=0)) / np.maximum(np.std(filled[:, active], axis=0), 1e-12) if np.any(active) else np.empty((len(filled), 0))
    singular = np.linalg.svd(standardized, compute_uv=False) if standardized.shape[1] else np.asarray([])
    condition = float(singular[0] / singular[-1]) if len(singular) and singular[-1] > 1e-12 else None
    pearson_max = 0.0
    spearman_max = 0.0
    if filled.shape[1] > 1:
        pearson = np.nan_to_num(np.corrcoef(filled, rowvar=False), nan=0.0)
        pearson_max = float(np.max(np.abs(pearson - np.eye(filled.shape[1]))))
        ranks = np.argsort(np.argsort(filled, axis=0), axis=0)
        spearman = np.nan_to_num(np.corrcoef(ranks, rowvar=False), nan=0.0)
        spearman_max = float(np.max(np.abs(spearman - np.eye(filled.shape[1]))))
    return {
        "row_count": len(matrix), "feature_count": len(names),
        "missing_count_by_feature": {name: int(value) for name, value in zip(names, np.sum(missing, axis=0))},
        "variance_by_feature": {name: float(value) for name, value in zip(names, variances)},
        "nonconstant_feature_count": int(np.sum(active)),
        "maximum_absolute_pearson": pearson_max, "maximum_absolute_spearman": spearman_max,
        "effective_rank": int(np.sum(singular > (singular[0] * 1e-10 if len(singular) else 0))),
        "condition_number": condition,
    }


def protected_inventory(name: str, root: Path) -> dict[str, object]:
    files = [
        {"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())}
        for path in sorted(root.rglob("*")) if path.is_file()
    ]
    unchanged = subprocess.run(["git", "diff", "--quiet", BASE, "--", str(root.relative_to(ROOT))], cwd=ROOT).returncode == 0
    return {"name": name, "root": str(root.relative_to(ROOT)), "file_count": len(files), "files": files,
            "inventory_sha256": sha256(canonical(files)), "unchanged_from_base": unchanged}


def build_game(game: str, roles: dict[str, object], audit: dict[str, object], output: Path) -> dict[str, object]:
    role = roles["games"][game]
    draws = load_draws(ROOT / role["eligible_source"])
    metadata = {} if game == "ssq" else load_metadata(METADATA)
    synthetic = next_draw(game, draws[-1])
    feature_rows = build_feature_rows(game, draws + [synthetic], metadata)
    selection = json.loads((SELECTION / f"{game}-selection-receipt.json").read_text(encoding="utf-8"))
    material = json.loads((REPORT / f"{game}-report-material.json").read_text(encoding="utf-8"))
    receipt = json.loads((REPORT / f"{game}-report-receipt.json").read_text(encoding="utf-8"))
    strongest = selection["strongest_selection_candidate"]
    selected_material = material["candidates"][strongest]
    model = material["models"][strongest]
    if strongest == "B0":
        selected_matrix = None
        prospective_x = None
    else:
        names = candidate_names(strongest, selection["provincial_distribution_enabled"])
        raw = raw_matrix(feature_rows, names)
        selected_matrix = apply_preprocessor(raw, preprocessor_from_payload(selected_material["preprocessor"]))
        prospective_x = selected_matrix[-1]
    top, proof = top_tickets(model, prospective_x, 1000)
    for row in top:
        row.update({"game": game, "target_issue": synthetic["issue"], "target_date": synthetic["draw_date"],
                    "candidate_id": strongest, "serving_status": "Top-1000 Shadow"})
    top_payload = b"".join(canonical(row) for row in top)
    top_path = output / "top1000" / f"{game}-top1000-shadow.jsonl"
    top_path.parent.mkdir(parents=True, exist_ok=True)
    top_path.write_bytes(top_payload)
    top10_path = output / "top10-shadow" / f"{game}-top10-shadow.jsonl"
    top10_path.parent.mkdir(parents=True, exist_ok=True)
    top10_path.write_bytes(b"".join(canonical(row) for row in top[:10]))
    proof.update({
        "game": game, "candidate_id": strongest, "target_issue": synthetic["issue"],
        "top1000_sha256": sha256(top_payload), "top10_sha256": sha256(top10_path.read_bytes()),
        "top1_to_top1000_probability_ratio": top[0]["joint_probability"] / top[-1]["joint_probability"],
        "top10_probability_mass": math.fsum(row["joint_probability"] for row in top[:10]),
        "top1000_probability_mass": math.fsum(row["joint_probability"] for row in top),
    })
    proof_path = output / "normalization" / f"{game}-normalization-proof.json"
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    proof_path.write_bytes(canonical(proof))
    snapshot_path = output / "features" / f"{game}-prospective-feature-snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(canonical(feature_rows[-1]))

    all_names = [name for block in BLOCKS for name in BLOCKS[block]]
    all_raw = raw_matrix(feature_rows[:-1], all_names)
    feature_diagnostics = diagnostics(all_raw, all_names)
    feature_diagnostics.update({
        "game": game, "source_revision_values": sorted({str(row["source_revision"]) for row in feature_rows if row["source_revision"] is not None}),
        "source_switch_count": sum(row["source_switch"] for row in feature_rows),
        "official_operational_metadata_available": game == "dlt",
    })
    diag_path = output / "diagnostics" / f"{game}-feature-diagnostics.json"
    diag_path.parent.mkdir(parents=True, exist_ok=True)
    diag_path.write_bytes(canonical(feature_diagnostics))

    report_indices = list(range(len(draws) - 120, len(draws)))
    experiments = []
    for candidate_id in CANDIDATE_BLOCKS:
        candidate = material["candidates"][candidate_id]
        if not candidate.get("eligible") or candidate_id == "B0":
            experiments.append({"candidate_id": candidate_id, "applicable": False, "reason": candidate.get("reason", "baseline has no feature blocks")})
            continue
        names = candidate_names(candidate_id, selection["provincial_distribution_enabled"])
        raw = raw_matrix(feature_rows[:-1], names)
        transformed = apply_preprocessor(raw, preprocessor_from_payload(candidate["preprocessor"]))
        fitted = material["models"][candidate_id]
        baseline_mean = candidate["metrics"]["mean_per_ball_bernoulli_log_loss"]
        block_rows = []
        for block in CANDIDATE_BLOCKS[candidate_id]:
            if block == "provincial_distribution_conditional" and not selection["provincial_distribution_enabled"]:
                continue
            positions = [names.index(name) for name in BLOCKS[block] if name in names]
            columns = positions + [position + len(names) for position in positions]
            ablated = transformed.copy(); ablated[:, columns] = 0.0
            ablated_rows = score_rows(fitted, ablated, draws, report_indices)
            permuted = transformed.copy(); permuted[np.asarray(report_indices)[:, None], np.asarray(columns)] = transformed[np.asarray(list(reversed(report_indices)))[:, None], np.asarray(columns)]
            permuted_rows = score_rows(fitted, permuted, draws, report_indices)
            block_rows.append({
                "block": block,
                "ablation_mean_log_loss_delta": mean(row["mean_per_ball_bernoulli_log_loss"] for row in ablated_rows) - baseline_mean,
                "reverse_permutation_mean_log_loss_delta": mean(row["mean_per_ball_bernoulli_log_loss"] for row in permuted_rows) - baseline_mean,
            })
        experiments.append({"candidate_id": candidate_id, "applicable": True, "blocks": block_rows})
    experiment_path = output / "experiments" / f"{game}-ablation-permutation.json"
    experiment_path.parent.mkdir(parents=True, exist_ok=True)
    experiment_path.write_bytes(canonical({"game": game, "report_rows": 120, "experiments": experiments}))

    card = {
        "artifact_type": "phase4e5_model_card", "game": game, "candidate_id": strongest,
        "serving_status": "SHADOW_ONLY_NOT_PROMOTED", "serving_release": "P4-P4E2-20260815-r12",
        "selection_config": selection["strongest_selection_config"], "selection_receipt_sha256": selection["receipt_sha256"],
        "report_receipt_sha256": receipt["receipt_sha256"], "report_metrics": receipt["candidate_metrics"][strongest],
        "report_fit": False, "report_reselection": False, "training_labels_end_before_report": True,
        "strictly_lagged_operational_join": True, "current_and_future_operational_fields_forbidden": True,
        "official_operational_coverage": audit["games"][game]["field_coverage"],
        "causal_interpretation": "Payout and player-behavior fields are correlational operational covariates, not causal mechanical draw information.",
        "limitations": "Lottery draws are designed to be random; no candidate passed the frozen scientific and cross-game authority gates.",
        "probability_spread_adjustment": "none",
    }
    card_path = output / "model-cards" / f"{game}-model-card.json"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_bytes(canonical(card))
    return {
        "game": game, "candidate_id": strongest, "target_issue": synthetic["issue"],
        "top1000_sha256": proof["top1000_sha256"], "top10_sha256": proof["top10_sha256"],
        "scientific_gate_pass": receipt["scientific_gate_pass"], "promotion_gate_pass": receipt["promotion_gate_pass"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    roles = json.loads(ROLES.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    games = [build_game(game, roles, audit, args.output) for game in ("ssq", "dlt")]
    inventory = {name: protected_inventory(name, root) for name, root in PRIOR_ROOTS.items()}
    inventory_path = args.output / "inventory" / "prior-release-byte-inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_bytes(canonical(inventory))
    decision = {
        "artifact_type": "phase4e5_delivery_decision", "phase": "P4E5_external_operational_metadata",
        "games": games, "all_games_required": True,
        "official_comparable_metadata_both_games": audit["all_games_comparable_official_metadata"],
        "scientific_gates_all_games": all(game["scientific_gate_pass"] for game in games),
        "release_allocation": "FORBIDDEN", "serving_release_unchanged": "P4-P4E2-20260815-r12",
        "p4e4_status_unchanged": "FEATURE_ENGINEERING_DELIVERED_PROSPECTIVE_ONLY",
        "terminal_state": "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION",
        "reason": "SSQ official operational metadata was unavailable and no selected candidate passed the frozen independent scientific gates.",
    }
    (args.output / "decision.json").write_bytes(canonical(decision))
    files = [
        {"path": str(path.relative_to(args.output)), "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())}
        for path in sorted(args.output.rglob("*")) if path.is_file() and path.name != "core-manifest.json"
    ]
    manifest = {"artifact_type": "phase4e5_delivery_manifest", "file_count": len(files), "files": files,
                "manifest_sha256": sha256(canonical(files))}
    (args.output / "core-manifest.json").write_bytes(canonical(manifest))
    print(decision["terminal_state"], manifest["manifest_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
