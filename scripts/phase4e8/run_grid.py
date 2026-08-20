#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4_independent"))
import p4e2_oracle as oracle

SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
BASE = ROOT / "artifacts/phase4e7"
OUT = ROOT / "artifacts/phase4e8"
WINDOW = 120
L2_GRID = (8.0, 24.0, 72.0)
MASKS = {
    "all14": tuple(oracle.FEATURE_IDS),
    "history_only": tuple(oracle.FEATURE_IDS[:5]),
    "history_structure": tuple(oracle.FEATURE_IDS[:12]),
}

def canon(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()

def sha(value):
    return hashlib.sha256(value).hexdigest()

def draws(game):
    rows = [json.loads(x) for x in (SOURCE / f"{game}.jsonl").read_text().splitlines()]
    return [oracle.Draw(str(r["issue"]), tuple(r["front"]), tuple(r["back"]), r.get("source_record_sha256", "")) for r in rows]

def model(game, data, cutoff, l2, mask):
    old = oracle.L2_GRID
    oracle.L2_GRID = (l2,)
    try:
        coefficients = oracle.fit_coefficients(game, data, cutoff, l2)
    finally:
        oracle.L2_GRID = old
    zones = []
    for zone, spec in enumerate(oracle.RULES[game]):
        context = oracle.feature_context(game, data[:cutoff], zone)
        masked = {key: (value if key in mask else 0.0) for key, value in coefficients[zone].items()}
        enum = oracle.enumerate_zone(context, masked, True)
        zones.append({"n": spec[0], "k": spec[1], "coefficients": masked, "context": context,
                      "log_normalizer": enum["log_normalizer"], "probability_square_sum": enum["probability_square_sum"],
                      "top_zone_rows": [[score, list(combo)] for score, combo in enum["rows"]]})
    return {"family": "P4E2-R", "game": game, "zones": zones, "feature_mask": list(mask), "feature_groups_consumed": sorted(set(oracle.FEATURE_GROUPS.values())),
            "model_release_id": f"p4e8-{game}-{l2}-{sha(canon(list(mask)))[:12]}"}

def evaluate(game, fitted, data, indices):
    top = oracle.top_tickets(fitted, 1000)
    rows = [oracle.score_ticket(fitted, data[index], top) for index in indices]
    return rows, top

def main_game(game):
    data = draws(game)
    cutoff = len(data) - WINDOW
    folds = [(max(120, cutoff - 240), cutoff - 120), (max(120, cutoff - 120), cutoff)]
    candidates = []
    for mask_name, mask in MASKS.items():
        for l2 in L2_GRID:
            losses = []
            for train_end, val_end in folds:
                fitted = model(game, data, train_end, l2, mask)
                rows, _ = evaluate(game, fitted, data, range(train_end, val_end))
                losses.extend(float(r["joint_log_loss"]) for r in rows)
            candidates.append({"mask": mask_name, "l2": l2, "inner_mean_joint_log_loss": sum(losses) / len(losses), "inner_rows": len(losses)})
    candidates.sort(key=lambda r: (r["inner_mean_joint_log_loss"], r["mask"], r["l2"]))
    selected = candidates[0]
    fitted = model(game, data, cutoff, selected["l2"], MASKS[selected["mask"]])
    holdout_rows, top = evaluate(game, fitted, data, range(cutoff, len(data)))
    space = math.prod(math.comb(n, k) for n, k in oracle.RULES[game])
    m0 = math.log(space)
    deltas = [float(r["joint_log_loss"]) - m0 for r in holdout_rows]
    report = {"artifact_type": "phase4e8_iteration_report", "status": "RETROSPECTIVE_BACKTEST_ONLY", "game": game,
              "window_size": WINDOW, "training_count": cutoff, "holdout_first_issue": data[cutoff].issue, "holdout_last_issue": data[-1].issue,
              "folds": folds, "selected": selected, "candidates": candidates, "mean_delta_joint_log_loss_vs_m0": sum(deltas) / WINDOW,
              "joint_log_loss_bootstrap": oracle._bootstrap(deltas, 20260830 + int(game == "dlt"), 512),
              "top10_hit_rate": sum(bool(r["hit_at"]["10"]) for r in holdout_rows) / WINDOW,
              "top1000_hit_rate": sum(bool(r["hit_at"]["1000"]) for r in holdout_rows) / WINDOW,
              "promotion_eligible": False, "promotion_exclusion_reason": "retrospective historical labels; not untouched P4E6 evidence",
              "p4e6_serving_unchanged": True, "p4e6_terminal_status": "PROSPECTIVE_ONLY"}
    out = OUT / game; out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_bytes(canon(report)); (out / "top1000.jsonl").write_bytes(b"".join(canon(x) for x in top)); (out / "top10.jsonl").write_bytes(b"".join(canon(x) for x in top[:10]))
    return report

def main():
    OUT.mkdir(exist_ok=True); reports = {game: main_game(game) for game in ("ssq", "dlt")}
    summary = {"artifact_type": "phase4e8_iteration_summary", "status": "RETROSPECTIVE_BACKTEST_ONLY", "games": {g: {k: r[k] for k in ("selected", "mean_delta_joint_log_loss_vs_m0", "joint_log_loss_bootstrap", "top10_hit_rate", "top1000_hit_rate", "promotion_eligible")} for g, r in reports.items()}, "p4e6_serving_unchanged": True, "p4e6_terminal_status": "PROSPECTIVE_ONLY"}
    (OUT / "summary.json").write_bytes(canon(summary)); print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

if __name__ == "__main__": main()
