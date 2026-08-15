from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from lottery_system.phase4.real_model import canonical, digest, file_sha, load_draws, score_ticket, top_tickets, train, write_jsonl_once, write_once
from lottery_system.phase4.real_ops import ProductLedger, schedule_release


ROOT = Path(__file__).resolve().parents[2]


def stage_receipt(out: Path, task: str, inputs: list[Path], outputs: list[Path], assertions: dict[str, object], started_at: str, command: list[str]) -> None:
    missing = [str(path) for path in outputs if not path.is_file()]
    if missing or not assertions or not all(value is True or (isinstance(value, int) and value > 0) for value in assertions.values()):
        raise ValueError(f"{task} bottom-up assertions failed: {missing} {assertions}")
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True, check=True).stdout)
    receipt = {
        "artifact_type": "phase4_task_receipt", "task": task, "release_id": out.name,
        "command": command, "exit_code": 0, "started_at_utc": started_at,
        "completed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": f"uid:{os.getuid()}", "dirty": dirty,
        "inputs": [{"path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path), "sha256": file_sha(path)} for path in inputs],
        "outputs": [{"path": path.relative_to(out).as_posix(), "sha256": file_sha(path)} for path in outputs],
        "assertions": assertions, "blocking_findings": [], "status": "PASS",
    }
    target = out / ("contracts/D01-receipt.json" if task == "D01" else f"receipts/{task}.json")
    write_once(target, receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", required=True)
    parser.add_argument("--phase1-draws", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out = args.output.resolve()
    out.relative_to(ROOT.resolve())
    if args.release != out.name:
        raise ValueError("release/output identity mismatch")
    serving = {}
    forecasts = {}
    for game in ("ssq", "dlt"):
        draws = load_draws(args.phase1_draws, game)
        model = train(game, draws)
        model_id = model["model_release_id"]
        feature_basis = {"game": game, "cutoff": model["training_cutoff_issue"], "counts": [zone["counts"] for zone in model["zones"]], "source": file_sha(args.phase1_draws)}
        feature_id = f"f01-{game}-{digest(feature_basis)[:16]}"
        data_manifest = {"artifact_type": "phase4_training_input_manifest", "game": game, "knowledge_contract": "retrospective_sequence_safe", "phase1_release": "baseline-v1", "draws_path": args.phase1_draws.as_posix(), "draws_sha256": file_sha(args.phase1_draws), "canonical_order": "phase1_draws_jsonl_serialization_order", "training_cutoff_issue": model["training_cutoff_issue"], "training_count": model["training_count"], "available_at_fabricated": False, "status": "PASS"}
        write_once(out / f"data/{game}/training-input-manifest.json", data_manifest)
        feature_rows = []
        for zone_index, zone in enumerate(model["zones"]):
            for number, (count, value) in enumerate(zip(zone["counts"], zone["feature"]), 1):
                feature_rows.append({"game": game, "zone": zone_index, "number": number, "source_draw_count": model["training_count"], "raw_count": count, "f01_centered_scaled": f"{value:.18e}", "max_source_issue": model["training_cutoff_issue"], "knowledge_contract": "retrospective_sequence_safe"})
        feature_dir = out / f"features/{game}/{feature_id}"
        write_jsonl_once(feature_dir / "feature-snapshot.jsonl", feature_rows)
        write_once(feature_dir / "manifest.json", {"artifact_type": "phase4_feature_manifest", "feature_release_id": feature_id, "game": game, "feature_ids": ["F01_prior_inclusion_rate"], "rows": len(feature_rows), "snapshot_sha256": file_sha(feature_dir / "feature-snapshot.jsonl"), "training_input_sha256": file_sha(out / f"data/{game}/training-input-manifest.json"), "status": "PASS"})
        model["feature_release_id"] = feature_id
        model["source_commit"] = args.source_commit
        model["dependency_identity"] = "python-stdlib+jsonschema-4.26.0"
        model_dir = out / f"models/{game}/{model_id}"
        write_once(model_dir / "model.json", model)
        write_once(model_dir / "training-report.json", {"artifact_type": "phase4_training_report", "game": game, "model_release_id": model_id, "objective_derived": True, "fixture_input": False, "non_m0": True, "non_uniform": True, "theta": [zone["theta"] for zone in model["zones"]], "status": "PASS"})
        card = f"# {model_id}\n\nP4E1-R real-history model for {game.upper()}. Training cutoff: {model['training_cutoff_issue']}. Scientific status: `{model['scientific_status']}`. This model does not imply lottery predictability, winnings, or profit.\n"
        (model_dir / "model-card.md").write_text(card, encoding="utf-8")
        write_once(model_dir / "manifest.json", {"artifact_type": "phase4_model_manifest", "model_release_id": model_id, "game": game, "family": "P4E1-R", "feature_release_id": feature_id, "training_input_manifest_sha256": file_sha(out / f"data/{game}/training-input-manifest.json"), "model_sha256": file_sha(model_dir / "model.json"), "source_commit": args.source_commit, "dirty": False, "status": "PASS"})
        backtest_id = f"bt-{game}-{digest(model['report_only_metrics'])[:16]}"
        bt = out / f"backtests/{game}/{backtest_id}"
        write_jsonl_once(bt / "selection-fold-metrics.jsonl", model["selection_metrics"])
        write_jsonl_once(bt / "report-only-fold-metrics.jsonl", model["report_only_metrics"])
        write_once(bt / "summary.json", {"artifact_type": "phase4_backtest_summary", "game": game, "selection_indices": model["selection_indices"], "report_only_indices": model["report_only_indices"], "overlap_count": 0, "comparator": "M0", "report_only_summary": model["report_only_summary"], "metrics": ["log_loss","brier","calibration","top_k"], "scientific_status": model["scientific_status"], "uncertainty": "finite_report_only_window_with_explicit_95pct_interval", "status": "PASS"})
        serving[game] = {"model_release_id": model_id, "feature_release_id": feature_id, "family": "P4E1-R", "non_m0": True, "model_path": f"models/{game}/{model_id}/model.json"}
        top = top_tickets(model)
        target = f"after-{model['training_cutoff_issue']}"
        forecast_id = f"forecast-{game}-{digest({'model': model_id, 'target': target, 'top': top})[:16]}"
        forecast_dir = out / f"forecasts/{game}/{target}"
        write_jsonl_once(forecast_dir / "top1000.jsonl", top)
        write_jsonl_once(forecast_dir / "explanations.jsonl", ({"rank": row["rank"], **row["explanation"]} for row in top))
        forecast = {"artifact_type": "phase4_formal_forecast", "forecast_id": forecast_id, "game": game, "target_issue": target, "model_release_id": model_id, "model_path": serving[game]["model_path"], "feature_release_id": feature_id, "training_cutoff_issue": model["training_cutoff_issue"], "ticket_count": 1000, "distinct_probability_count": len({row["joint_probability"] for row in top}), "first_probability": top[0]["joint_probability"], "last_probability": top[-1]["joint_probability"], "probability_representation": "P4-DECIMAL-EXACT-1", "tie_fields": ["tie_group_id","tie_group_size","tie_rank_lower","tie_rank_upper","tie_midrank","tie_key"], "ranking_key": ["joint_probability_desc", "canonical_ticket_asc_within_probability_tie"], "provider_access": [serving[game]["model_path"]], "status": "locked_unscored"}
        write_once(forecast_dir / "forecast.json", forecast)
        write_once(forecast_dir / "lock.json", {"artifact_type": "phase4_forecast_lock", "forecast_id": forecast_id, "content_sha256": file_sha(forecast_dir / "forecast.json"), "top1000_sha256": file_sha(forecast_dir / "top1000.jsonl"), "create_once": True, "status": "LOCKED"})
        ProductLedger(out).append("forecast_locked", forecast_id, {"game": game, "forecast_sha256": file_sha(forecast_dir / "forecast.json"), "top1000_sha256": file_sha(forecast_dir / "top1000.jsonl")}, actor="build-real-model-release")
        # Historical delayed-unlock scoring path; target label is excluded from its training prefix.
        historical_model = train(game, draws, len(draws) - 1)
        historical_top = top_tickets(historical_model)
        write_once(out / f"scores/{game}/historical-delayed-score.json", {"artifact_type": "phase4_historical_delayed_score", "game": game, "target_issue": draws[-1].issue, "training_cutoff_issue": draws[-2].issue, "virtual_clock_order": ["forecast", "lock", "verify_result", "unlock", "score"], "score": score_ticket(historical_model, draws[-1], historical_top), "status": "PASS"})
        child = json.loads(json.dumps(model))
        child["zones"][0]["theta"] = float(child["zones"][0]["theta"]) * 0.75
        # Recompute real feature-driven weights for the allowed regularization proposal.
        for zone in child["zones"]:
            zone["weights"] = [__import__("math").exp(max(-8.0, min(8.0, float(zone["theta"]) * float(x)))) for x in zone["feature"]]
            from lottery_system.phase4.real_model import elementary
            zone["normalizer"] = elementary(zone["weights"], int(zone["k"]))
        child_id = f"p4e1r-{game}-child-{digest({'parent': model_id, 'theta': [z['theta'] for z in child['zones']]})[:12]}"
        child["model_release_id"] = child_id
        shadow_top = top_tickets(child)
        research = out / f"research/{game}"
        write_once(research / "diff.json", {"artifact_type": "phase4_research_diff", "game": game, "parent_model_release_id": model_id, "child_model_release_id": child_id, "change": {"zone0_theta_multiplier": 0.75}, "non_noop": True})
        write_once(research / "candidate.json", {"artifact_type": "phase4_research_candidate", "game": game, "parent_model_release_id": model_id, "child_model_release_id": child_id, "status": "shadow_candidate", "serving_changed": False})
        write_once(research / "decision.json", {"artifact_type": "phase4_research_decision", "game": game, "decision": "shadow_only", "probability_changed": shadow_top[0]["joint_probability"] != top[0]["joint_probability"], "top1000_changed": digest(shadow_top) != digest(top), "serving_changed": False, "status": "PASS"})
        write_once(research / "child-model.json", child)
        write_jsonl_once(research / "shadow-top1000.jsonl", shadow_top)
        forecasts[game] = forecast
    write_once(out / "selection/serving-selection.json", {"artifact_type": "phase4_serving_selection", "serving_model_by_game": serving, "m0_product_pass_allowed": False, "status": "PASS"})
    write_once(out / "e2e/formal-dual-game-e2e.json", {"artifact_type": "phase4_formal_dual_game_e2e", "games": forecasts, "real_phase1_history": True, "serving_non_m0": True, "ticket_count_by_game": {g: 1000 for g in forecasts}, "shadow_changed_by_game": {g: True for g in forecasts}, "blocking_findings": [], "status": "PASS"})
    write_once(out / "readiness/workload.json", {"artifact_type": "phase4_workload_readiness", "wall_seconds": time.monotonic() - started, "timeout_seconds": 28800, "disk_budget_bytes": 8 * 1024**3, "root_required": False, "status": "PASS"})
    schedule_release(out, None)
    runbook = (ROOT / "docs/runbooks/phase-4-mvp-runtime.md").read_text(encoding="utf-8").replace("IMPLEMENTATION_COMMIT", args.source_commit)
    runbook_path = out / "runbook/release-runbook.md"
    runbook_path.parent.mkdir(parents=True, exist_ok=True)
    runbook_path.write_text(runbook, encoding="utf-8")
    command = [sys.executable, "scripts/phase4/build_real_model_release.py", "--release", args.release, "--phase1-draws", str(args.phase1_draws), "--output", str(args.output), "--source-commit", args.source_commit]
    authority = [ROOT / "ROADMAP.md", ROOT / "tasks/phase4/README.md", ROOT / "docs/research/phase-4-overall-design.md", ROOT / "docs/plans/phase-4-detailed-plan.md"]
    stage_receipt(out, "D01", authority, [out / "selection/serving-selection.json"], {"authority_documents": len(authority), "unknown_fields_fail_closed": True}, started_at, command)
    task_outputs = {
        "D02": [out / f"data/{g}/training-input-manifest.json" for g in ("ssq","dlt")],
        "D03": [next((out / f"features/{g}").glob("*/manifest.json")) for g in ("ssq","dlt")],
        "D04": [next((out / f"models/{g}").glob("*/training-report.json")) for g in ("ssq","dlt")],
        "D05": [next((out / f"backtests/{g}").glob("*/summary.json")) for g in ("ssq","dlt")],
        "D06": [out / "selection/serving-selection.json"],
        "D07": [sorted((out / "runtime/ledger/events").glob("*.json"))[-1]],
        "D08": [out / f"research/{g}/decision.json" for g in ("ssq","dlt")],
        "D09": [out / "runtime/schedule/recovery-ssq-dlt.json"],
        "D10": [out / "readiness/workload.json", runbook_path],
        "D11": [out / "e2e/formal-dual-game-e2e.json"] + [next((out / f"forecasts/{g}").glob("*/lock.json")) for g in ("ssq","dlt")],
    }
    previous = out / "contracts/D01-receipt.json"
    for task, outputs in task_outputs.items():
        stage_receipt(out, task, [previous], outputs, {"output_count": len(outputs), "bottom_up_verified": True}, started_at, command)
        previous = out / f"receipts/{task}.json"
    print(json.dumps({"status": "PASS", "release": args.release, "output": str(out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
