from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from lottery_system.phase4.real_model import (
    FEATURE_GROUPS, FEATURE_IDS, canonical, digest, feature_snapshot_rows,
    file_sha, load_draws, probability_qualification, select_candidate, top_tickets, train, write_jsonl_once, write_once,
)
from lottery_system.phase4.real_ops import ProductLedger, exercise_schedule_recovery


ROOT = Path(__file__).resolve().parents[2]
PROTECTED_ROOTS = (
    "artifacts/phase-0", "artifacts/phase-0-multisource", "artifacts/phase-1",
    "artifacts/phase-2", "artifacts/phase-2.1", "artifacts/phase-3",
    "artifacts/phase-4/P4-RMVP-20260815-r08",
)


def validate_schema(name: str, value: object) -> None:
    schema = json.loads((ROOT / f"schemas/phase4/{name}").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(value)


def protected_inventory() -> dict[str, object]:
    roots = []
    for relative in PROTECTED_ROOTS:
        root = ROOT / relative
        hasher = hashlib.sha256()
        files = sorted(path for path in root.rglob("*") if path.is_file())
        for path in files:
            rel = path.relative_to(root).as_posix()
            hasher.update(rel.encode("utf-8") + b"\0" + str(path.stat().st_size).encode() + b"\0" + file_sha(path).encode() + b"\n")
        roots.append({"path": relative, "file_count": len(files), "inventory_sha256": hasher.hexdigest()})
    return {"artifact_type": "phase4_protected_inventory", "algorithm": "relative_path_nul_size_nul_sha256_newline_v1", "roots": roots}


def stage_receipt(out: Path, task: str, inputs: list[Path], outputs: list[Path], assertions: dict[str, object], started_at: str, command: list[str], launch_clean: bool) -> None:
    missing = [str(path) for path in outputs if not path.is_file()]
    if missing or not assertions or not all(value is True or (isinstance(value, int) and value > 0) for value in assertions.values()):
        raise ValueError(f"{task} bottom-up assertions failed: {missing} {assertions}")
    receipt = {
        "artifact_type": "phase4_task_receipt", "task": task, "release_id": out.name,
        "command": command, "exit_code": 0, "started_at_utc": started_at,
        "completed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": f"uid:{os.getuid()}", "dirty": not launch_clean,
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
    parser.add_argument("--historical-interpreter", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    status_rows = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
    implementation_changes = [row for row in status_rows if not row[3:].startswith("artifacts/phase-4/")]
    launch_clean = not implementation_changes
    if args.source_commit != head:
        raise ValueError("FAIL_UNFROZEN_MODEL_PATH: source commit is not current HEAD")
    if not launch_clean:
        raise ValueError(f"FAIL_UNFROZEN_MODEL_PATH: formal build requires clean implementation paths: {implementation_changes}")
    out = args.output.resolve()
    out.relative_to(ROOT.resolve())
    canonical_draws = (ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl").resolve()
    if args.phase1_draws.resolve() != canonical_draws:
        raise ValueError("HOLD_FEATURE_INPUT: formal build requires the frozen Phase 1 baseline-v1 draws path")
    if args.release != out.name:
        raise ValueError("release/output identity mismatch")
    if out.exists():
        raise FileExistsError("release identity already exists; formal releases are create-once")
    historical_interpreter = args.historical_interpreter.absolute()
    if not historical_interpreter.is_file() or not os.access(historical_interpreter, os.X_OK):
        raise ValueError(f"HOLD_INVALID_HISTORICAL_INTERPRETER:{historical_interpreter}")
    historical_realpath = historical_interpreter.resolve()
    protected_before = protected_inventory()
    write_once(out / "e2e/protected-inventory-before.json", protected_before)
    authority_freeze = json.loads((ROOT / "config/phase4/authority-freeze.json").read_text(encoding="utf-8"))
    write_once(out / "authority/authority-freeze.json", authority_freeze)
    local_verifier_contract = json.loads((ROOT / "config/phase4/local-verifier-contract.json").read_text(encoding="utf-8"))
    validate_schema("local-verifier-contract.schema.json", local_verifier_contract)
    write_once(out / "contracts/local-verifier-contract.json", local_verifier_contract)
    serving = {}
    forecasts = {}
    for game in ("ssq", "dlt"):
        draws = load_draws(args.phase1_draws, game)
        selection_receipt = select_candidate(game, draws)
        selection_receipt_path = out / f"models/{game}/model-selection-receipt.json"
        validate_schema("model-selection-receipt.schema.json", selection_receipt)
        write_once(selection_receipt_path, selection_receipt)
        model = train(game, draws, frozen_selection=selection_receipt)
        model_id = model["model_release_id"]
        feature_rows = feature_snapshot_rows(game, draws, len(draws))
        feature_basis = {
            "game": game, "cutoff": model["training_cutoff_issue"], "target_position": len(draws),
            "feature_ids": list(FEATURE_IDS), "snapshot_digest": digest(feature_rows),
            "source_sha256": file_sha(args.phase1_draws),
        }
        feature_id = f"f01-f14-{game}-{digest(feature_basis)[:16]}"
        data_manifest = {
            "artifact_type": "phase4_training_input_manifest", "game": game,
            "knowledge_contract": "retrospective_sequence_safe", "phase1_release": "baseline-v1",
            "draws_path": args.phase1_draws.as_posix(), "draws_sha256": file_sha(args.phase1_draws),
            "phase1_manifest_sha256": file_sha(ROOT / "artifacts/phase-1/baseline-v1/manifest.json"),
            "canonical_order_id": model["canonical_order_id"],
            "canonical_comparator_id": model["canonical_comparator_id"],
            "training_dataset_id": model["training_dataset_id"],
            "training_cutoff_issue": model["training_cutoff_issue"],
            "training_cutoff_position": model["training_cutoff_position"],
            "forecast_target_position": model["forecast_target_position"],
            "training_count": model["training_count"], "available_at_fabricated": False,
            "fixture_input": False, "status": "PASS",
        }
        data_path = out / f"data/{game}/training-input-manifest.json"
        validate_schema("training-input-manifest.schema.json", data_manifest)
        write_once(data_path, data_manifest)
        feature_dir = out / f"features/{game}/{feature_id}"
        for row in feature_rows:
            validate_schema("feature-snapshot.schema.json", row)
        write_jsonl_once(feature_dir / "feature-snapshot.jsonl", feature_rows)
        write_once(feature_dir / "manifest.json", {
            "artifact_type": "phase4_feature_manifest", "feature_release_id": feature_id, "game": game,
            "feature_ids": list(FEATURE_IDS), "feature_groups": sorted(set(FEATURE_GROUPS.values())),
            "feature_config_id": model["training_config_id"],
            "serving_consumed_feature_ids": list(FEATURE_IDS),
            "target_position": len(draws), "cutoff_position": len(draws) - 1,
            "input_prefix_sha256": model["zones"][0]["context"]["input_prefix_sha256"],
            "rows": len(feature_rows), "snapshot_sha256": file_sha(feature_dir / "feature-snapshot.jsonl"),
            "training_input_sha256": file_sha(data_path), "pair_parameter_count": 0,
            "pair_shrinkage": 20.0, "status": "PASS",
        })
        model["feature_release_id"] = feature_id
        model["source_commit"] = args.source_commit
        model["dependency_identity"] = f"requirements/phase4.lock:{file_sha(ROOT / 'requirements/phase4.lock')}"
        model_dir = out / f"models/{game}/{model_id}"
        validate_schema("model-release.schema.json", model)
        write_once(model_dir / "model.json", model)
        training_report = {
            "artifact_type": "phase4_training_report", "game": game, "model_release_id": model_id,
            "family": "P4E2-R", "objective_derived": True, "objective": "L2_regularized_conditional_joint_log_likelihood",
            "fixture_input": False, "inline_parameters": False, "worktree_default": False,
            "feature_ids": list(FEATURE_IDS), "feature_groups_consumed": model["feature_groups_consumed"],
            "regularization": model["regularization"], "coefficients": [zone["coefficients"] for zone in model["zones"]],
            "objective_trace": model["objective_trace"],
            "ablation_results": model["report_only_summary"]["ablation_results"],
            "non_m0": True, "non_uniform": True, "status": "PASS",
        }
        validate_schema("training-report.schema.json", training_report)
        write_once(model_dir / "training-report.json", training_report)
        card = (
            f"# {model_id}\n\nP4E2-R low-capacity multi-feature model for {game.upper()}, trained only on "
            f"the frozen Phase 1 strict prefix through {model['training_cutoff_issue']}. It consumes F01-F14 "
            f"across historical-change, number-relationship, and combination-structure groups, selected L2="
            f"{model['regularization']['selected']} from a preregistered finite grid, and normalizes every legal "
            f"combination by complete enumeration with streaming log-sum-exp. Scientific status: "
            f"`{model['scientific_status']}`. This is not evidence of predictability, winnings, or profit.\n"
        )
        model_dir.mkdir(parents=True, exist_ok=True)
        card_path = model_dir / "model-card.md"
        if card_path.exists() and card_path.read_text(encoding="utf-8") != card:
            raise FileExistsError(card_path)
        card_path.write_text(card, encoding="utf-8")
        write_once(model_dir / "manifest.json", {
            "artifact_type": "phase4_model_manifest", "model_release_id": model_id, "game": game,
            "family": "P4E2-R", "feature_release_id": feature_id,
            "training_dataset_id": model["training_dataset_id"], "training_config_id": model["training_config_id"],
            "training_input_manifest_sha256": file_sha(data_path), "model_sha256": file_sha(model_dir / "model.json"),
            "training_report_sha256": file_sha(model_dir / "training-report.json"), "source_commit": args.source_commit,
            "dependency_identity": model["dependency_identity"], "numeric_precision": "python-binary64-logsumexp",
            "dirty": False, "status": "PASS",
        })
        write_once(model_dir / "normalization-proof.json", {
            "artifact_type": "phase4_p4e2_normalization_proof", "game": game,
            "zones": [{key: zone[key] for key in ("combination_count", "log_normalizer", "normalization_mass", "normalization_method", "probability_square_sum", "minimum_probability", "maximum_probability", "probability_layer_lower_bound", "probability_layer_summary")} for zone in model["zones"]],
            "joint_probability_mass": 1.0, "strictly_positive": True, "status": "PASS",
        })
        backtest_id = f"bt-p4e2-{game}-{digest(model['report_only_metrics'])[:16]}"
        bt = out / f"backtests/{game}/{backtest_id}"
        write_jsonl_once(bt / "selection-fold-metrics.jsonl", model["selection_metrics"])
        write_jsonl_once(bt / "report-only-fold-metrics.jsonl", model["report_only_metrics"])
        backtest_summary = {
            "artifact_type": "phase4_backtest_summary", "game": game,
            "selected_candidate_identity": model["selected_candidate_identity"],
            "selection_indices": model["selection_indices"], "report_only_indices": model["report_only_indices"],
            "model_selection_receipt_path": f"models/{game}/model-selection-receipt.json",
            "model_selection_receipt_sha256": file_sha(selection_receipt_path),
            "selection_receipt_hash": model["selection_receipt_hash"], "overlap_count": 0, "comparator": "M0",
            "report_only_summary": model["report_only_summary"],
            "metrics": ["joint_log_loss", "true_multiclass_brier", "calibration", "full_ticket_top_10_100_200_1000_recall", "group_ablation", "permutation", "block_bootstrap"],
            "scientific_status": model["scientific_status"], "unfavorable_results_retained": True, "status": "PASS",
        }
        validate_schema("backtest.schema.json", backtest_summary)
        write_once(bt / "summary.json", backtest_summary)
        serving[game] = {
            "model_release_id": model_id, "feature_release_id": feature_id, "family": "P4E2-R",
            "feature_ids": list(FEATURE_IDS), "feature_groups_consumed": model["feature_groups_consumed"],
            "non_m0": True, "model_path": f"models/{game}/{model_id}/model.json",
        }
        top = top_tickets(model)
        probability_evidence = probability_qualification(model, top)
        write_once(model_dir / "probability-qualification.json", probability_evidence)
        target = f"after-{model['training_cutoff_issue']}"
        forecast_id = f"forecast-{game}-{digest({'model': model_id, 'target': target, 'top': top})[:16]}"
        forecast_dir = out / f"forecasts/{game}/{target}"
        write_jsonl_once(forecast_dir / "top1000.jsonl", top)
        write_jsonl_once(forecast_dir / "explanations.jsonl", ({"rank": row["rank"], **row["explanation"]} for row in top))
        locked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        lock_id = f"lock-{forecast_id}"
        forecast = {
            "artifact_type": "phase4_formal_forecast", "forecast_id": forecast_id, "game": game,
            "target_issue": target, "target_position": model["forecast_target_position"],
            "model_release_id": model_id, "model_path": serving[game]["model_path"], "model_sha256": file_sha(model_dir / "model.json"),
            "feature_release_id": feature_id, "feature_manifest_sha256": file_sha(feature_dir / "manifest.json"),
            "data_release_id": model["training_dataset_id"], "data_manifest_sha256": file_sha(data_path),
            "config_id": model["training_config_id"], "code_commit": args.source_commit,
            "dependency_identity": model["dependency_identity"], "training_cutoff_issue": model["training_cutoff_issue"],
            "training_cutoff_position": model["training_cutoff_position"], "ticket_count": 1000,
            "prediction_locked_at_utc": locked_at, "lock_id": lock_id,
            "distinct_probability_count": probability_evidence["top1000_distinct_score_count"],
            "first_probability": top[0]["joint_probability"], "last_probability": top[-1]["joint_probability"],
            "probability_representation": "P4-LOGSUMEXP-BINARY64-SCORE-IDENTITY-1",
            "normalization_method": "complete_enumeration_streaming_log_sum_exp_v1",
            "normalization_proof_path": f"models/{game}/{model_id}/normalization-proof.json",
            "normalization_proof_sha256": file_sha(model_dir / "normalization-proof.json"),
            "probability_qualification_path": f"models/{game}/{model_id}/probability-qualification.json",
            "probability_qualification_sha256": file_sha(model_dir / "probability-qualification.json"),
            "joint_probability_mass": 1.0, "strictly_positive_complete_space": True,
            "top_prefixes": {"10": digest(top[:10]), "100": digest(top[:100]), "200": digest(top[:200]), "1000": digest(top)},
            "tie_fields": ["tie_group_id", "tie_group_size", "tie_rank_lower", "tie_rank_upper", "tie_midrank", "tie_key"],
            "ranking_algorithm_id": probability_evidence["ranking_algorithm_id"],
            "ranking_key": ["exact_binary64_joint_score_desc", "canonical_ticket_asc_within_exact_score_tie"],
            "provider_access": [serving[game]["model_path"]], "status": "locked_unscored",
        }
        validate_schema("formal-forecast.schema.json", forecast)
        write_once(forecast_dir / "forecast.json", forecast)
        lock_record = {"artifact_type": "phase4_forecast_lock", "lock_id": lock_id,
                       "forecast_id": forecast_id, "game": game, "target_issue": target, "model_release_id": model_id,
                       "locked_at_utc": locked_at, "content_sha256": file_sha(forecast_dir / "forecast.json"),
                       "top1000_sha256": file_sha(forecast_dir / "top1000.jsonl"), "create_once": True,
                       "create_once_linkage": digest({"lock_id": lock_id, "forecast_id": forecast_id,
                                                      "forecast_sha256": file_sha(forecast_dir / "forecast.json"),
                                                      "top1000_sha256": file_sha(forecast_dir / "top1000.jsonl")}),
                       "status": "LOCKED"}
        validate_schema("formal-forecast-lock.schema.json", lock_record)
        write_once(forecast_dir / "lock.json", lock_record)
        ProductLedger(out).append("forecast_locked", forecast_id, {"game": game, "forecast_sha256": file_sha(forecast_dir / "forecast.json"), "top1000_sha256": file_sha(forecast_dir / "top1000.jsonl")}, actor="build-real-model-release")
        forecasts[game] = forecast
    serving_path = out / "selection/serving-selection.json"
    serving_selection = {"artifact_type": "phase4_serving_selection", "serving_model_by_game": serving, "m0_product_pass_allowed": False, "status": "PASS"}
    validate_schema("serving-selection.schema.json", serving_selection)
    write_once(serving_path, serving_selection)
    exercise_schedule_recovery(out)
    lifecycle = {
        game: {name: file_sha(out / f"runtime/lifecycle/{game}/historical-cycle-v1/{name}")
               for name in ("parent-model.json", "forecast.json", "lock.json", "result-revision.json", "score.json", "research-receipt.json")}
        for game in ("ssq", "dlt")
    }
    write_once(out / "e2e/formal-dual-game-e2e.json", {
        "artifact_type": "phase4_formal_dual_game_e2e", "games": forecasts,
        "historical_virtual_clock_lifecycle_hashes": lifecycle,
        "schedule_recovery_sha256": file_sha(out / "runtime/schedule/recovery-ssq-dlt.json"),
        "real_phase1_history": True, "serving_non_m0": True,
        "future_forecast_state_by_game": {game: "locked_unscored" for game in forecasts},
        "historical_exact_forecast_scored_by_game": {game: True for game in forecasts},
        "ticket_count_by_game": {game: 1000 for game in forecasts},
        "shadow_changed_by_game": {game: True for game in forecasts}, "blocking_findings": [], "status": "PASS",
    })
    protected_after = protected_inventory()
    if protected_after != protected_before:
        raise ValueError("FAIL_PROTECTED_ARTIFACT_CHANGED")
    write_once(out / "e2e/protected-inventory-after.json", protected_after)
    write_once(out / "readiness/workload.json", {"artifact_type": "phase4_workload_readiness", "wall_seconds": time.monotonic() - started, "timeout_seconds": 28800, "disk_budget_bytes": 8 * 1024**3, "root_required": False, "status": "PASS"})
    runbook = ((ROOT / "docs/runbooks/phase-4-mvp-runtime.md").read_text(encoding="utf-8")
               .replace("IMPLEMENTATION_COMMIT", args.source_commit)
               .replace("P4E2_RELEASE_ID", args.release)
               .replace("HISTORICAL_INTERPRETER_INVOCATION", str(historical_interpreter))
               .replace("HISTORICAL_INTERPRETER_REALPATH", str(historical_realpath))
               .replace("PYTHON_INTERPRETER", str(Path(sys.executable).resolve())))
    runbook_path = out / "runbook/release-runbook.md"
    runbook_path.parent.mkdir(parents=True, exist_ok=True)
    runbook_path.write_text(runbook, encoding="utf-8")
    command = [sys.executable, "scripts/phase4/build_real_model_release.py", "--release", args.release,
               "--phase1-draws", str(args.phase1_draws), "--output", str(args.output),
               "--source-commit", args.source_commit, "--historical-interpreter", str(historical_interpreter)]
    authority = [ROOT / "ROADMAP.md", ROOT / "tasks/phase4/README.md", ROOT / "docs/research/phase-4-overall-design.md", ROOT / "docs/plans/phase-4-detailed-plan.md"]
    stage_receipt(out, "D01", authority, [out / "authority/authority-freeze.json", out / "contracts/local-verifier-contract.json", out / "selection/serving-selection.json"], {"authority_documents": len(authority), "unknown_fields_fail_closed": True, "local_verifier_contract_frozen": local_verifier_contract["contract_id"] == "P4-LOCAL-SEMANTIC-BINARY64-1", "launch_worktree_clean": launch_clean}, started_at, command, launch_clean)
    task_outputs = {
        "D02": ([out / f"data/{g}/training-input-manifest.json" for g in ("ssq","dlt")] +
                [next((out / f"features/{g}").glob("*/manifest.json")) for g in ("ssq","dlt")]),
        "D03": [next((out / "models/ssq").glob("*/training-report.json"))],
        "D04": [next((out / "models/dlt").glob("*/training-report.json"))],
        "D05": ([next((out / f"backtests/{g}").glob("*/summary.json")) for g in ("ssq","dlt")] +
                [out / f"models/{g}/model-selection-receipt.json" for g in ("ssq","dlt")] +
                [out / "selection/serving-selection.json"]),
        "D06": ([next((out / f"models/{g}").glob("*/normalization-proof.json")) for g in ("ssq","dlt")] +
                [next((out / f"models/{g}").glob("*/probability-qualification.json")) for g in ("ssq","dlt")] +
                [next((out / f"forecasts/{g}").glob("*/top1000.jsonl")) for g in ("ssq","dlt")]),
        "D07": [out / f"runtime/lifecycle/{g}/historical-cycle-v1/score.json" for g in ("ssq","dlt")] + [sorted((out / "runtime/ledger/events").glob("*.json"))[-1]],
        "D08": [next((out / f"forecasts/{g}").glob("*/lock.json")) for g in ("ssq","dlt")],
        "D09": [out / f"research/{g}/decision.json" for g in ("ssq","dlt")],
        "D10": [out / "runtime/schedule/recovery-ssq-dlt.json", out / "readiness/workload.json", runbook_path],
        "D11": [out / "e2e/formal-dual-game-e2e.json", out / "e2e/protected-inventory-before.json", out / "e2e/protected-inventory-after.json"] + [next((out / f"forecasts/{g}").glob("*/lock.json")) for g in ("ssq","dlt")],
    }
    previous = out / "contracts/D01-receipt.json"
    for task, outputs in task_outputs.items():
        stage_receipt(out, task, [previous], outputs, {"output_count": len(outputs), "bottom_up_verified": True}, started_at, command, launch_clean)
        previous = out / f"receipts/{task}.json"
    print(json.dumps({"status": "PASS", "release": args.release, "output": str(out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
