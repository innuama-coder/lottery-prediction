#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
from pathlib import Path
from statistics import mean
from typing import Sequence

from lottery_system.phase4e4.data import canonical, load_jsonl, sha256_bytes, sha256_file
from lottery_system.phase4e4.model import FAMILIES, fit_model, score_block
from run_selection import m0_rows, r12_rows, transition_rows, write_once


ROOT = Path(__file__).resolve().parents[2]
BRANCH = "origin/codex/phase4e4-feature-strengthening-20260819-r01"
SELECTION_CHECKPOINT = "f7a730dcb0f617971f686241939c425db2b7f7a3"
DATA = ROOT / "artifacts/phase-4e4/data-20260819"
SELECTION = ROOT / "artifacts/phase-4e4/selection-20260819"
OUTPUT = ROOT / "artifacts/phase-4e4/report-20260819"
AUTHORITY = ROOT / "config/phase4e4/authority-contract.json"
ITERATIONS = 8192
BLOCK_LENGTH = 6
SEEDS = {"ssq": 2026081904, "dlt": 2026081905}
COMPARATORS = ("M0", "P4E2_r12_retrained", "P4E3_Transition_retrained")
METRICS = ("joint_log_loss", "full_multiclass_brier")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def verify_capability_boundary() -> None:
    if subprocess.run(["git", "merge-base", "--is-ancestor", SELECTION_CHECKPOINT, BRANCH], cwd=ROOT).returncode:
        raise SystemExit("selection checkpoint is not present on the remote branch")
    for game in ("ssq", "dlt"):
        relative = f"artifacts/phase-4e4/selection-20260819/{game}-selection-receipt.json"
        committed = subprocess.check_output(["git", "show", f"{SELECTION_CHECKPOINT}:{relative}"], cwd=ROOT)
        if committed != (ROOT / relative).read_bytes():
            raise SystemExit(f"selection receipt differs from pushed checkpoint: {game}")
        receipt = json.loads(committed)
        if receipt["report_labels_read"] is not False:
            raise SystemExit("selection capability receipt is not closed")


def moving_block(values: Sequence[float], seed: int) -> dict[str, object]:
    generator = random.Random(seed)
    observed = mean(values)
    raw_means, centered_means = [], []
    centered = [value - observed for value in values]
    for _ in range(ITERATIONS):
        indices = []
        while len(indices) < len(values):
            start = generator.randrange(len(values))
            indices.extend((start + offset) % len(values) for offset in range(BLOCK_LENGTH))
        indices = indices[: len(values)]
        raw_means.append(mean(values[index] for index in indices))
        centered_means.append(mean(centered[index] for index in indices))
    raw_means.sort()
    p_value = (1 + sum(value <= observed for value in centered_means)) / (ITERATIONS + 1)
    return {
        "method": "moving_block_bootstrap", "iterations": ITERATIONS, "block_length": BLOCK_LENGTH,
        "seed": seed, "mean_delta": observed,
        "ci95": [raw_means[int(ITERATIONS * 0.025)], raw_means[int(ITERATIONS * 0.975)]],
        "one_sided_p": p_value, "direction": "candidate_minus_comparator_less_than_zero",
    }


def reliability_ece(rows: Sequence[dict[str, object]], zone: int, game: str) -> float | None:
    if any(row["zone_inclusion_brier"][zone] is None for row in rows):
        return None
    # Row-level inclusion Brier is available, but number-level probabilities are
    # intentionally not reconstructed for the immutable r12 comparator.
    return None


def game_scores(game: str) -> tuple[dict[str, object], dict[str, object]]:
    selection_receipt = json.loads((SELECTION / f"{game}-selection-receipt.json").read_text(encoding="utf-8"))
    prefix = load_jsonl(DATA / f"selection-prefix/{game}.jsonl", game)
    report = load_jsonl(DATA / f"sealed-report/{game}.jsonl", game)
    draws = prefix + report
    cutoff = len(prefix)
    positions = list(range(cutoff, cutoff + len(report)))
    comparators = {
        "M0": m0_rows(game, draws, positions),
        "P4E2_r12_retrained": r12_rows(game, draws, cutoff, positions),
        "P4E3_Transition_retrained": transition_rows(game, draws, cutoff, positions),
    }
    candidates = {}
    for family in FAMILIES:
        config = selection_receipt["candidates"][family]["final_config"]
        model = fit_model(game, draws, cutoff, family, config)
        rows = score_block(model, draws, positions)
        candidates[family] = {"candidate_id": family, "frozen_config": config, "rows": rows,
                              "maximum_training_label_position": model["maximum_training_label_position"]}
    payload = {
        "artifact_type": "phase4e4_report_score_material", "game": game,
        "selection_checkpoint": SELECTION_CHECKPOINT,
        "selection_receipt_sha256": sha256_file(SELECTION / f"{game}-selection-receipt.json"),
        "report_input_sha256": sha256_file(DATA / f"sealed-report/{game}.jsonl"),
        "report_draw_count": len(report), "report_first_position": cutoff,
        "report_labels_read": True, "post_report_refit_or_reselection": False,
        "comparators": comparators, "candidates": candidates,
    }
    return payload, selection_receipt


def apply_holm(hypotheses: list[dict[str, object]]) -> None:
    ordered = sorted(range(len(hypotheses)), key=lambda index: (float(hypotheses[index]["bootstrap"]["one_sided_p"]), hypotheses[index]["hypothesis_id"]))
    running = 0.0
    total = len(hypotheses)
    for rank, index in enumerate(ordered):
        adjusted = min(1.0, (total - rank) * float(hypotheses[index]["bootstrap"]["one_sided_p"]))
        running = max(running, adjusted)
        hypotheses[index]["holm_adjusted_p"] = running
        hypotheses[index]["holm_pass"] = running < 0.05


def build_receipts(materials: dict[str, dict[str, object]], selection: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    hypotheses = []
    for game in ("ssq", "dlt"):
        for family in FAMILIES:
            candidate_rows = materials[game]["candidates"][family]["rows"]
            for comparator in COMPARATORS:
                comparator_rows = materials[game]["comparators"][comparator]
                for metric in METRICS:
                    deltas = [float(left[metric]) - float(right[metric]) for left, right in zip(candidate_rows, comparator_rows)]
                    hypothesis_id = f"{game}:{family}:{comparator}:{metric}"
                    hypotheses.append({
                        "hypothesis_id": hypothesis_id, "game": game, "candidate_id": family,
                        "comparator": comparator, "metric": metric, "draw_count": len(deltas),
                        "bootstrap": moving_block(deltas, SEEDS[game]),
                    })
    if len(hypotheses) != 84:
        raise AssertionError("frozen Holm family must contain 84 hypotheses")
    apply_holm(hypotheses)
    receipts = {}
    for game in ("ssq", "dlt"):
        game_hypotheses = [row for row in hypotheses if row["game"] == game]
        candidate_gates = {}
        for family in FAMILIES:
            family_hypotheses = [row for row in game_hypotheses if row["candidate_id"] == family]
            proper = all(float(row["bootstrap"]["mean_delta"]) < 0.0 and float(row["bootstrap"]["ci95"][1]) < 0.0 and row["holm_pass"] for row in family_hypotheses)
            report_counts = {}
            candidate_rows = materials[game]["candidates"][family]["rows"]
            for comparator in COMPARATORS:
                comparator_rows = materials[game]["comparators"][comparator]
                report_counts[comparator] = sum(float(left["joint_log_loss"]) - float(right["joint_log_loss"]) < 0.0 for left, right in zip(candidate_rows, comparator_rows))
            calibration_available = all(
                all(value is not None for value in row["zone_inclusion_brier"])
                for comparator in COMPARATORS for row in materials[game]["comparators"][comparator]
            )
            exact_normalization = all(abs(float(row["normalization_mass"]) - 1.0) <= 1e-12 for row in candidate_rows)
            selection_pass = bool(selection[game]["candidates"][family]["selection_direction_pass"])
            report_pass = all(count >= 42 for count in report_counts.values())
            candidate_gates[family] = {
                "selection_direction_pass": selection_pass, "report_favorable_draw_count_by_comparator": report_counts,
                "report_direction_pass": report_pass, "proper_score_statistics_pass": proper,
                "calibration_comparison_available": calibration_available,
                "calibration_pass": False if not calibration_available else None,
                "exact_normalization_pass": exact_normalization,
                "all_scientific_gates_pass": selection_pass and report_pass and proper and calibration_available and exact_normalization,
            }
        promotion_authority = bool(selection[game]["promotion_authority"]) and game != "ssq"
        promoted = [family for family in FAMILIES if candidate_gates[family]["all_scientific_gates_pass"]] if promotion_authority else []
        receipt = {
            "artifact_type": "phase4e4_frozen_report_receipt", "game": game,
            "selection_checkpoint": SELECTION_CHECKPOINT, "report_draw_count": 60,
            "report_labels_read": True, "report_used_for_selection_or_tuning": False,
            "post_report_refit_or_reselection": False, "hypothesis_family_size": 84,
            "game_hypotheses": game_hypotheses, "candidate_gates": candidate_gates,
            "promotion_authority": promotion_authority, "promoted_candidates": promoted,
            "terminal_state": "IMPROVED_SERVING_ACCEPTED" if promoted else "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION",
        }
        if game == "ssq":
            receipt["promotion_authority"] = False
        receipt["receipt_sha256"] = sha256_bytes(canonical(receipt))
        receipts[game] = receipt
    return receipts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    verify_capability_boundary()
    materials, selection = {}, {}
    for game in ("ssq", "dlt"):
        materials[game], selection[game] = game_scores(game)
        print(game, "report_scoring_complete", flush=True)
    receipts = build_receipts(materials, selection)
    for game in ("ssq", "dlt"):
        write_once(args.output / f"{game}-report-material.json", materials[game])
        write_once(args.output / f"{game}-report-receipt.json", receipts[game])
        print(game, receipts[game]["terminal_state"], receipts[game]["receipt_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
