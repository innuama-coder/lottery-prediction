#!/usr/bin/env python3
"""Run the pre-registered Phase4E31 120-draw walk-forward evaluation."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys

from lottery_system.phase4.baseline_model import (
    FEATURE_NAMES, LAMBDA_CANDIDATES, binary_log_loss, build_point_in_time_dataset,
    select_lambda_and_fit,
)

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "artifacts/phase4e30_data_expansion/dlt-draws-full.jsonl"
OUTPUT = ROOT / "artifacts/phase4e31_baseline"
WINDOW = 120


def _zone_prediction(x, y, t, n, k):
    model, selection = select_lambda_and_fit(x[:t], y[:t])
    probability = model.predict_proba(x[t])
    truth = y[t]
    uniform = [k / n] * n
    order = sorted(range(n), key=lambda i: (-probability[i], i + 1))
    rank = {index: position + 1 for position, index in enumerate(order)}
    winning_indices = [i for i, value in enumerate(truth) if value]
    return {
        "probabilities": {str(i + 1): probability[i] for i in range(n)},
        "model_log_loss": binary_log_loss(truth, probability),
        "uniform_log_loss": binary_log_loss(truth, uniform),
        "winning_number_mean_rank": sum(rank[i] for i in winning_indices) / k,
        "normalized_winning_number_mean_rank": sum(rank[i] for i in winning_indices) / (k * n),
        "top_k_hits": {str(top): sum(i in set(order[:top]) for i in winning_indices)
                       for top in ((5, 10) if n == 35 else (2, 4))},
        "top_k_any_hit": {str(top): any(i in set(order[:top]) for i in winning_indices)
                          for top in ((5, 10) if n == 35 else (2, 4))},
        "lambda": selection["selected"],
        "lambda_selection": selection,
        "theta": list(model.theta),
        "standardization": {"mean": list(model.mean), "scale": list(model.scale), "scope": "training_prefix_only"},
    }


def _mean(rows, zone, field):
    return sum(row[zone][field] for row in rows) / len(rows)


def _paired_significance(rows):
    """Two-sided normal approximation for the paired mean loss difference."""
    differences = [(row["front"]["uniform_log_loss"] + row["back"]["uniform_log_loss"])
                   - (row["front"]["model_log_loss"] + row["back"]["model_log_loss"])
                   for row in rows]
    mean = sum(differences) / len(differences)
    variance = sum((value - mean) ** 2 for value in differences) / (len(differences) - 1)
    standard_error = math.sqrt(variance / len(differences))
    z = mean / standard_error if standard_error else 0.0
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return {"test": "paired_two_sided_normal_approximation", "mean_uniform_minus_model": mean,
            "standard_error": standard_error, "z": z, "p_value": p, "alpha": 0.05,
            "significantly_better": bool(mean > 0 and p < 0.05)}


def main() -> int:
    draws = [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines() if line]
    if len(draws) != 1430:
        raise ValueError(f"expected 1430 draws, found {len(draws)}")
    front = [row["front_numbers"] for row in draws]
    back = [row["back_numbers"] for row in draws]
    front_x, front_y = build_point_in_time_dataset(front, 35, 5)
    back_x, back_y = build_point_in_time_dataset(back, 12, 2)
    rows = []
    for t in range(len(draws) - WINDOW, len(draws)):
        row = {
            "issue_id": draws[t]["issue_id"], "draw_index": t,
            "training_prefix": [0, t],
            "actual": {"front_numbers": front[t], "back_numbers": back[t]},
            "front": _zone_prediction(front_x, front_y, t, 35, 5),
            "back": _zone_prediction(back_x, back_y, t, 12, 2),
        }
        rows.append(row)
        print(f"evaluated {t + 1}/{len(draws)} issue={row['issue_id']}", file=sys.stderr)
    significance = _paired_significance(rows)
    zones = {}
    for zone, tops in (("front", (5, 10)), ("back", (2, 4))):
        zones[zone] = {
            "mean_model_log_loss": _mean(rows, zone, "model_log_loss"),
            "mean_uniform_log_loss": _mean(rows, zone, "uniform_log_loss"),
            "mean_normalized_rank": _mean(rows, zone, "normalized_winning_number_mean_rank"),
            "top_k_any_hit_draw_rate": {str(top): sum(r[zone]["top_k_any_hit"][str(top)] for r in rows) / WINDOW for top in tops},
            "top_k_winning_number_hit_rate": {str(top): sum(r[zone]["top_k_hits"][str(top)] for r in rows) / (WINDOW * (5 if zone == "front" else 2)) for top in tops},
            "lambda_counts": {str(value): sum(r[zone]["lambda"] == value for r in rows) for value in LAMBDA_CANDIDATES},
        }
    summary = {
        "phase": "phase4e31", "game": "dlt", "draw_count": len(draws), "evaluation_window": WINDOW,
        "evaluation_issue_ids": [rows[0]["issue_id"], rows[-1]["issue_id"]],
        "model": "per-number L2 logistic regression", "feature_families": 12,
        "feature_dimensions": list(FEATURE_NAMES), "lambda_candidates": list(LAMBDA_CANDIDATES),
        "lambda_selection_prefix_only": True, "standardization_prefix_only": True,
        "zones": zones, "paired_significance": significance,
        "scientific_conclusion": "confirmed_lift" if significance["significantly_better"] else "no_confirmed_lift",
        "conclusion_note": "No predictive lift is claimed unless the pre-registered paired test is positive at alpha=0.05.",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "walk-forward.jsonl").write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows), encoding="utf-8")
    (OUTPUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {"phase": "phase4e31", "inputs": {str(INPUT.relative_to(ROOT)): hashlib.sha256(INPUT.read_bytes()).hexdigest()},
                "outputs": {name: hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest() for name in ("walk-forward.jsonl", "summary.json")}}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
