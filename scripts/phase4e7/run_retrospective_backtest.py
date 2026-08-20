#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4_independent"))
import p4e2_oracle as oracle  # noqa: E402

SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
OUT = ROOT / "artifacts/phase4e7"
WINDOW = 120


def canon(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_draws(game: str) -> list[oracle.Draw]:
    rows = [json.loads(line) for line in (SOURCE / f"{game}.jsonl").read_text().splitlines()]
    return [oracle.Draw(str(row["issue"]), tuple(row["front"]), tuple(row["back"]), row.get("source_record_sha256", "")) for row in rows]


def run_game(game: str) -> dict[str, object]:
    draws = load_draws(game)
    cutoff = len(draws) - WINDOW
    train = draws[:cutoff]
    holdout = draws[cutoff:]
    model = oracle.train(game, draws, cutoff_index=cutoff)
    top = oracle.top_tickets(model, 1000)
    scores = [oracle.score_ticket(model, draw, top) for draw in holdout]
    space = math.prod(math.comb(n, k) for n, k in oracle.RULES[game])
    m0_ll = math.log(space)
    m0_brier = 1.0 - 1.0 / space
    deltas = [float(row["joint_log_loss"]) - m0_ll for row in scores]
    brier_deltas = [float(row["multiclass_brier"]) - m0_brier for row in scores]
    bootstrap = oracle._bootstrap(deltas, 20260820 + int(game == "dlt"), 512)
    report = {
        "artifact_type": "phase4e7_retrospective_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "window_size": WINDOW,
        "training_count": cutoff,
        "training_first_issue": train[0].issue,
        "training_last_issue": train[-1].issue,
        "holdout_first_issue": holdout[0].issue,
        "holdout_last_issue": holdout[-1].issue,
        "holdout_first_fact_hash": holdout[0].fact_hash,
        "holdout_last_fact_hash": holdout[-1].fact_hash,
        "model_release_id": model["model_release_id"],
        "training_dataset_id": model["training_dataset_id"],
        "strict_lag": True,
        "labels_used_for_training": False,
        "mean_joint_log_loss": sum(float(row["joint_log_loss"]) for row in scores) / WINDOW,
        "m0_joint_log_loss": m0_ll,
        "mean_delta_joint_log_loss_vs_m0": sum(deltas) / WINDOW,
        "mean_multiclass_brier": sum(float(row["multiclass_brier"]) for row in scores) / WINDOW,
        "m0_multiclass_brier": m0_brier,
        "mean_delta_multiclass_brier_vs_m0": sum(brier_deltas) / WINDOW,
        "joint_log_loss_bootstrap": bootstrap,
        "top10_hit_rate": sum(bool(row["hit_at"]["10"]) for row in scores) / WINDOW,
        "top1000_hit_rate": sum(bool(row["hit_at"]["1000"]) for row in scores) / WINDOW,
        "mean_observed_class_probability": sum(float(row["actual_joint_probability"]) for row in scores) / WINDOW,
        "probability_spread_adjustment": "none",
        "promotion_eligible": False,
        "promotion_exclusion_reason": "historical labels were available before this experiment; window is not untouched under P4E6 authority contract",
    }
    report["receipt_sha256"] = sha_bytes(canon(report))
    game_dir = OUT / game
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / "report.json").write_bytes(canon(report))
    (game_dir / "top1000.jsonl").write_bytes(b"".join(canon(row) for row in top))
    (game_dir / "top10.jsonl").write_bytes(b"".join(canon(row) for row in top[:10]))
    return {"report": report, "top1000_sha256": sha_bytes((game_dir / "top1000.jsonl").read_bytes()), "top10_sha256": sha_bytes((game_dir / "top10.jsonl").read_bytes())}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    split = {"artifact_type": "phase4e7_retrospective_split_manifest", "status": "RETROSPECTIVE_BACKTEST_ONLY", "window_size": WINDOW, "source_root": str(SOURCE.relative_to(ROOT)), "games": {}}
    for game in ("ssq", "dlt"):
        rows = [json.loads(line) for line in (SOURCE / f"{game}.jsonl").read_text().splitlines()]
        cut = len(rows) - WINDOW
        split["games"][game] = {"canonical_row_count": len(rows), "train_issue_range": [rows[0]["issue"], rows[cut - 1]["issue"]], "holdout_issue_range": [rows[cut]["issue"], rows[-1]["issue"]], "holdout_draw_date_range": [rows[cut]["draw_date"], rows[-1]["draw_date"]], "source_sha256": sha_bytes((SOURCE / f"{game}.jsonl").read_bytes()), "holdout_row_hashes": [rows[index].get("source_record_sha256", "") for index in range(cut, len(rows))]}
    split["receipt_sha256"] = sha_bytes(canon(split))
    (OUT / "split-manifest.json").write_bytes(canon(split))
    results = {game: run_game(game) for game in ("ssq", "dlt")}
    summary = {"artifact_type": "phase4e7_retrospective_summary", "status": "RETROSPECTIVE_BACKTEST_ONLY", "split_manifest_sha256": sha_bytes((OUT / "split-manifest.json").read_bytes()), "games": {game: {key: value for key, value in result["report"].items() if key in ("window_size", "training_count", "holdout_first_issue", "holdout_last_issue", "mean_delta_joint_log_loss_vs_m0", "mean_delta_multiclass_brier_vs_m0", "top10_hit_rate", "top1000_hit_rate", "promotion_eligible", "promotion_exclusion_reason", "receipt_sha256")} for game, result in results.items()}, "p4e6_serving_unchanged": True, "p4e6_terminal_status": "PROSPECTIVE_ONLY"}
    summary["receipt_sha256"] = sha_bytes(canon(summary))
    (OUT / "summary.json").write_bytes(canon(summary))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
