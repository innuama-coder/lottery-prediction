from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from lottery_system.phase4.p4e2_model import (  # noqa: E402
    PROBABILITY_REPRESENTATION_ID,
    RANKING_ALGORITHM_ID,
    score_identity,
    score_order_key,
    tie_group_id_for_score,
    tie_key_for_score,
)
FINAL = {"acceptance/machine-acceptance.json", "acceptance/checklist-release-receipt.json", "acceptance/final-closure.json"}
PROTECTED_ROOTS = (
    "artifacts/phase-0", "artifacts/phase-0-multisource", "artifacts/phase-1",
    "artifacts/phase-2", "artifacts/phase-2.1", "artifacts/phase-3",
    "artifacts/phase-4/P4-RMVP-20260815-r08",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def once(path: Path, value: Any) -> None:
    encoded = value.encode() if isinstance(value, str) else canon(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable identity collision: {path}")
        return
    path.write_bytes(encoded)


def protected_inventory() -> dict[str, Any]:
    roots = []
    for relative in PROTECTED_ROOTS:
        root = ROOT / relative
        hasher = hashlib.sha256()
        files = sorted(path for path in root.rglob("*") if path.is_file())
        for path in files:
            rel = path.relative_to(root).as_posix()
            hasher.update(rel.encode("utf-8") + b"\0" + str(path.stat().st_size).encode() + b"\0" + sha(path).encode() + b"\n")
        roots.append({"path": relative, "file_count": len(files), "inventory_sha256": hasher.hexdigest()})
    return {"artifact_type": "phase4_protected_inventory", "algorithm": "relative_path_nul_size_nul_sha256_newline_v1", "roots": roots}


def receipt(release: Path, task: str, inputs: list[Path], outputs: list[Path], assertions: dict[str, Any], started: str) -> dict[str, Any]:
    if not inputs or not outputs or not assertions or any(not path.is_file() for path in inputs + outputs):
        raise ValueError(f"{task} receipt evidence incomplete")
    value = {
        "artifact_type": "phase4_task_receipt", "task": task, "release_id": release.name,
        "command": [str(Path(sys.executable).resolve()), *sys.argv], "exit_code": 0,
        "started_at_utc": started, "completed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": f"uid:{os.getuid()}", "dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True, check=True).stdout),
        "inputs": [{"path": path.relative_to(release).as_posix(), "sha256": sha(path)} for path in inputs],
        "outputs": [{"path": path.relative_to(release).as_posix(), "sha256": sha(path)} for path in outputs],
        "assertions": assertions, "blocking_findings": [], "status": "PASS",
    }
    once(release / f"receipts/{task}.json", value)
    return value


def validate_task_receipts(release: Path) -> dict[str, str]:
    hashes = {}
    required = {"artifact_type", "task", "release_id", "command", "exit_code", "started_at_utc", "completed_at_utc", "actor", "dirty", "inputs", "outputs", "assertions", "blocking_findings", "status"}
    paths = [release / "contracts/D01-receipt.json"] + [release / f"receipts/D{i:02d}.json" for i in range(2, 13)] + [release / "receipts/D14.json"]
    for path in paths:
        value = load(path)
        if set(value) != required or value["status"] != "PASS" or value["exit_code"] != 0 or value["blocking_findings"] or not value["inputs"] or not value["outputs"] or not value["assertions"]:
            raise ValueError(f"HOLD_INVALID_TASK_RECEIPT:{path}")
        for record in value["inputs"] + value["outputs"]:
            evidence = release / record["path"] if not record["path"].startswith(("docs/", "tasks/", "ROADMAP")) else ROOT / record["path"]
            if evidence.is_file() and sha(evidence) != record["sha256"]:
                raise ValueError(f"HOLD_RECEIPT_HASH_MISMATCH:{path}:{evidence}")
        hashes[value["task"]] = sha(path)
    return hashes


def validate_forecast(release: Path, game: str) -> dict[str, Any]:
    forecast_path = next((release / f"forecasts/{game}").glob("*/forecast.json"))
    forecast = load(forecast_path)
    rows_path = forecast_path.with_name("top1000.jsonl")
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    if len(rows) != 1000 or len({(tuple(row["front_numbers"]), tuple(row["back_numbers"])) for row in rows}) != 1000:
        raise ValueError(f"HOLD_ILLEGAL_TOP1000:{game}")
    required_tie = {"tie_group_id", "tie_group_size", "tie_rank_lower", "tie_rank_upper", "tie_midrank", "tie_key"}
    probabilities = [Decimal(row["joint_probability"]) for row in rows]
    if len(set(probabilities)) < 2 or any(value <= 0 for value in probabilities) or any(left < right for left, right in zip(probabilities, probabilities[1:])):
        raise ValueError(f"HOLD_PROBABILITY_ORDER:{game}")
    expected_order = sorted(
        rows,
        key=lambda row: (
            -int(str(row["score_order_key"]).split(":", 1)[1]),
            tuple(row["front_numbers"]),
            tuple(row["back_numbers"]),
        ),
    )
    if rows != expected_order:
        raise ValueError(f"HOLD_STABLE_SCORE_ORDER:{game}")
    for index, row in enumerate(rows, 1):
        score = float(row["log_joint_score"])
        if (row["rank"] != index or row.get("full_space_rank") != index or not required_tie <= row.keys()
                or row.get("probability_representation") != PROBABILITY_REPRESENTATION_ID
                or row.get("score_order_key") != score_order_key(score)
                or row.get("score_identity") != score_identity(score)
                or row.get("tie_key") != tie_key_for_score(score)
                or row.get("tie_group_id") != tie_group_id_for_score(score)
                or row.get("ranking_algorithm_id") != RANKING_ALGORITHM_ID):
            raise ValueError(f"HOLD_TIE_CONTRACT:{game}:{index}")
        peers = [position for position, value in enumerate(rows, 1) if value["score_identity"] == row["score_identity"]]
        if row["tie_group_size"] != len(peers) or row["tie_rank_lower"] != min(peers) or row["tie_rank_upper"] != max(peers) or Decimal(row["tie_midrank"]) != (Decimal(min(peers)) + Decimal(max(peers))) / 2:
            raise ValueError(f"HOLD_TIE_BOUNDS:{game}:{index}")
    serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    if serving["family"] != "P4E2-R" or not serving["non_m0"] or len(serving.get("feature_ids", [])) != 14 or len(serving.get("feature_groups_consumed", [])) != 3:
        raise ValueError(f"HOLD_M0_SERVING:{game}")
    lock = load(forecast_path.with_name("lock.json"))
    model_path = release / serving["model_path"]
    model = load(model_path)
    qualification_path = model_path.with_name("probability-qualification.json")
    qualification = load(qualification_path)
    if (qualification.get("probability_semantics") != "stable_decimal_score_order_key_then_exp_for_display_v1"
            or qualification.get("score_order_key_id") != "P4S10HE1"
            or qualification.get("score_order_quantum") != "0.0000000001"
            or qualification.get("score_order_rounding") != "ROUND_HALF_EVEN"
            or qualification.get("ranking_algorithm_id") != RANKING_ALGORITHM_ID
            or qualification.get("one_ulp_score_drift_preserves_identity") is not True
            or qualification.get("complete_space_zone_score_order_contracts") != [zone["score_order_contract"] for zone in model["zones"]]):
        raise ValueError(f"HOLD_PROBABILITY_QUALIFICATION:{game}")
    feature_manifest = release / f"features/{game}/{serving['feature_release_id']}/manifest.json"
    data_manifest = release / f"data/{game}/training-input-manifest.json"
    required_lineage = (forecast.get("model_sha256") == sha(model_path) and forecast.get("feature_manifest_sha256") == sha(feature_manifest)
                        and forecast.get("data_manifest_sha256") == sha(data_manifest) and bool(forecast.get("config_id"))
                        and bool(forecast.get("code_commit")) and bool(forecast.get("dependency_identity"))
                        and bool(forecast.get("prediction_locked_at_utc")) and forecast.get("lock_id") == lock.get("lock_id")
                        and forecast.get("ranking_algorithm_id") == RANKING_ALGORITHM_ID
                        and forecast.get("probability_representation") == PROBABILITY_REPRESENTATION_ID
                        and forecast.get("ranking_key") == ["stable_score_order_key_desc_P4S10HE1", "canonical_ticket_asc_within_stable_score_key_tie"]
                        and forecast.get("probability_qualification_sha256") == sha(qualification_path)
                        and lock.get("create_once") and lock.get("content_sha256") == sha(forecast_path)
                        and lock.get("top1000_sha256") == sha(rows_path))
    if not required_lineage:
        raise ValueError(f"HOLD_FORECAST_LINEAGE:{game}")
    return {"forecast_sha256": sha(forecast_path), "top1000_sha256": sha(rows_path),
            "distinct_probability_count": len({row["score_identity"] for row in rows}),
            "model_release_id": serving["model_release_id"], "lock_id": lock["lock_id"], "lineage_complete": True}


def validate_model_evidence(release: Path, game: str) -> dict[str, Any]:
    serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    model_path = release / serving["model_path"]
    model, model_manifest = load(model_path), load(model_path.with_name("manifest.json"))
    if model_manifest.get("model_sha256") != sha(model_path) or model.get("family") != "P4E2-R":
        raise ValueError(f"HOLD_MODEL_RELEASE:{game}")
    expected_features = {f"F{index:02d}" for index in range(1, 15)}
    expected_groups = {"historical_change", "number_relationship", "combination_structure"}
    if set(model.get("feature_ids", [])) != expected_features or set(model.get("feature_groups_consumed", [])) != expected_groups:
        raise ValueError(f"HOLD_FEATURE_INCOMPLETE:{game}")
    data = load(release / f"data/{game}/training-input-manifest.json")
    if (data.get("available_at_fabricated") or data.get("fixture_input") or
            data.get("training_cutoff_position") >= data.get("forecast_target_position") or
            data.get("training_dataset_id") != model.get("training_dataset_id")):
        raise ValueError(f"FAIL_LEAKAGE:{game}")
    feature_dir = release / f"features/{game}/{serving['feature_release_id']}"
    feature_manifest = load(feature_dir / "manifest.json")
    rows = [json.loads(line) for line in (feature_dir / "feature-snapshot.jsonl").read_text(encoding="utf-8").splitlines()]
    observed_features = set().union(*(row.get("feature_values", {}) for row in rows))
    if (sha(feature_dir / "feature-snapshot.jsonl") != feature_manifest.get("snapshot_sha256") or
            observed_features != expected_features or feature_manifest.get("pair_parameter_count") != 0 or
            any(row.get("available_at") is not None or row["max_source_position"] >= row["target_position"] for row in rows)):
        raise ValueError(f"FAIL_LEAKAGE_OR_FEATURE_SNAPSHOT:{game}")
    for group in expected_groups:
        if not any(abs(float(zone["coefficients"][feature])) > 1e-12 for zone in model["zones"]
                   for feature in expected_features if ({**{f"F{i:02d}": "historical_change" for i in range(1, 6)},
                                                          "F06": "number_relationship", "F07": "number_relationship",
                                                          **{f"F{i:02d}": "combination_structure" for i in range(8, 15)}})[feature] == group):
            raise ValueError(f"HOLD_DEGENERATE_MODEL:{game}:{group}")
    if any(zone.get("normalization_mass") != 1.0 or zone.get("minimum_probability", 0) <= 0 or
           zone.get("probability_layer_lower_bound", 0) < 2 for zone in model["zones"]):
        raise ValueError(f"HOLD_DEGENERATE_MODEL:{game}:normalization")
    if set(model["selection_indices"]) & set(model["report_only_indices"]) or max(model["selection_indices"]) >= min(model["report_only_indices"]):
        raise ValueError(f"FAIL_SELECTION_BIAS:{game}")
    selection_path = release / f"models/{game}/model-selection-receipt.json"
    selection = load(selection_path)
    selection_payload = {key: value for key, value in selection.items() if key not in {"receipt_hash", "selection_metrics"}}
    if (selection.get("receipt_hash") != hashlib.sha256(canon(selection_payload)).hexdigest()
            or selection.get("receipt_hash") != model.get("selection_receipt_hash")
            or selection["selection_input"]["last_position"] >= selection["report_only_capability_boundary"]["first_position"]):
        raise ValueError(f"FAIL_SELECTION_BIAS:{game}:receipt")
    if any(row.get("brier_formula") != "1-2*p_observed+sum_over_complete_legal_space(p_class^2)" or
           row.get("fold_role") != "report_only" or row.get("used_for_selection") for row in model["report_only_metrics"]):
        raise ValueError(f"HOLD_BACKTEST_INCOMPLETE:{game}")
    summary = model["report_only_summary"]
    if (not summary.get("full_ticket_metrics") or summary.get("top_k_values") != [10, 100, 200, 1000] or
            len(summary.get("permutation_evidence", [])) != 3 or len(summary.get("ablation_results", [])) != 3 or
            summary.get("joint_log_loss_block_bootstrap", {}).get("method") != "moving_block_bootstrap_v1"):
        raise ValueError(f"HOLD_BACKTEST_INCOMPLETE:{game}")
    if (any(row.get("method") != "zero_group_coefficients_complete_space_renormalization_v1" or not row.get("all_complete_spaces_renormalized")
            or row.get("sample_size") != len(model["report_only_indices"]) for row in summary["ablation_results"])
            or any(row.get("method") != "held_out_feature_group_derangement_recompute_fitted_model_score_v1"
                   or row.get("sample_size") != len(model["report_only_indices"]) or not row.get("samples")
                   or any(sample["target_position"] == sample["donor_position"] for sample in row["samples"])
                   for row in summary["permutation_evidence"])):
        raise ValueError(f"HOLD_FAKE_SCIENTIFIC_EVIDENCE:{game}")
    ci = summary["joint_log_loss_block_bootstrap"]["ci95"]
    expected_scientific = "worse_than_M0" if ci[0] > 0 else ("lift_supported" if ci[1] < 0 else "no_confirmed_lift")
    if model["scientific_status"] != expected_scientific:
        raise ValueError(f"FAIL_FALSE_CLAIM:{game}")
    return {"model_sha256": sha(model_path), "feature_snapshot_sha256": sha(feature_dir / "feature-snapshot.jsonl"),
            "training_dataset_id": model["training_dataset_id"], "training_config_id": model["training_config_id"],
            "selection_fold_count": len(model["selection_indices"]), "report_only_fold_count": len(model["report_only_indices"]),
            "model_selection_receipt_sha256": sha(selection_path), "true_multiclass_brier": True,
            "ablation_recomputed": True, "permutation_recomputed": True, "feature_groups_effective": sorted(expected_groups)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    release = args.release.resolve()
    release.relative_to(ROOT.resolve())
    if not release.name.startswith("P4-P4E2-") or release.name == "P4-RMVP-20260815-r08":
        raise ValueError("P4E2 finalization requires a new unique P4-P4E2-* release and forbids immutable r08")
    if any((release / path).exists() for path in FINAL):
        raise FileExistsError("D15 final files already exist; release identity is immutable")

    command_receipts = sorted((release / "validation/attempts").glob("*/receipt.json"))
    passed = {path.parent.name for path in command_receipts if load(path).get("status") == "PASS" and load(path).get("exit_code") == 0}
    required_commands = {"A01-compileall", "A02-phase4", "A03-phase4-oracle", "A04-phase3", "A05-phase2-1", "A06-phase2", "A07-authority", "A08-contract", "A09-bottom-up", "A10-replay-validation"}
    if not required_commands <= passed:
        raise ValueError(f"HOLD_FINAL_REGRESSION_INCOMPLETE:{sorted(required_commands - passed)}")

    replay_path = release / "replay/replay-report.json"
    replay = load(replay_path)
    if replay.get("match_rate") != 1.0 or replay.get("mutation_detection_rate") != 1.0 or replay.get("product_core_import_count") != 0:
        raise ValueError("HOLD_D12_INDEPENDENT_REPLAY")
    recorded_before = load(release / "e2e/protected-inventory-before.json")
    recorded_after = load(release / "e2e/protected-inventory-after.json")
    current_protected = protected_inventory()
    if recorded_before != recorded_after or recorded_after != current_protected:
        raise ValueError("FAIL_PROTECTED_ARTIFACT_CHANGED")
    sys.path.insert(0, str(ROOT / "src"))
    from lottery_system.phase4.real_ops import validate_release_bottom_up
    bottom_up = validate_release_bottom_up(release, require_final=False)
    if bottom_up.get("status") != "PASS" or not bottom_up.get("recomputed_from_bottom_up"):
        raise ValueError("HOLD_D15_BOTTOM_UP_VALIDATION")
    receipt(release, "D12", [replay_path], [replay_path], {"match_rate_100pct": True, "mutation_detection_100pct": True, "independent_imports_zero": True}, started)

    local_contract_path = release / "contracts/local-verifier-contract.json"
    local_contract = load(local_contract_path)
    if local_contract.get("contract_id") != "P4-LOCAL-PATH-CLASSIFIED-BINARY64-5":
        raise ValueError("HOLD_LOCAL_VERIFIER_CONTRACT")
    checklist_rows = {}
    for game in ("ssq", "dlt"):
        serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
        model = load(release / serving["model_path"])
        forecast = load(next((release / f"forecasts/{game}").glob("*/forecast.json")))
        checklist_rows[game] = {"model": serving["model_release_id"], "feature": serving["feature_release_id"],
                                "cutoff": model["training_cutoff_issue"], "target": forecast["target_issue"],
                                "scientific": model["scientific_status"]}
    checklist = f"""# Phase 4 P4E2 local product acceptance candidate

Status: `CANDIDATE_NOT_RELEASED`

Release: `{release.name}`

## Prerequisites and local setup

- A clean checkout of this repository at the release source commit.
- CPython 3.12 (any supported patch release; CPython 3.12.11 on macOS is explicitly in scope).
- No Phase 2/2.1 historical virtual environment and no builder/VPS path is required.

Copy-paste setup (run once from the repository root):

```bash
python3.12 -m venv .p4-local-venv
.p4-local-venv/bin/python -m pip install 'jsonschema==4.26.0'
```

## One read-only acceptance command

```bash
PHASE4_PYTHON=.p4-local-venv/bin/python scripts/phase4/local-accept-release --release artifacts/phase-4/{release.name}
```

Expected first line: `LOCAL ACCEPTANCE: PASS (READY_FOR_LOCAL_PRODUCT_ACCEPTANCE)`.
The command snapshots the release before verification and fails if any byte is changed. It verifies authority and
schemas; the final manifest/closure; immutable formal Phase 2/2.1 receipts; serving lineage and create-once locks;
1,000 ordered tickets for each game; probability qualification and exact stable-key-derived score/tie identities; lifecycle score and
AutoResearch shadow; dual-game scheduler recovery; protected roots; independent replay and negative mutations.
Only the explicitly enumerated recomputed numeric fields in `contracts/local-verifier-contract.json` use the finite,
conjunctive absolute/relative/ULP bounds. Ranking uses `P4S10HE1` (`1e-10`, round-half-even) then canonical ticket order.
IDs, hashes, issues, cutoffs, lineage, tickets, rank/order, score order keys, score/tie identities, tie bounds,
and create-once files remain exact; raw binary64 score bits are not identities.

## Frozen inspect expectations

- SSQ: model `{checklist_rows['ssq']['model']}`; feature `{checklist_rows['ssq']['feature']}`; cutoff `{checklist_rows['ssq']['cutoff']}`; target `{checklist_rows['ssq']['target']}`; rows `1000`; scientific status `{checklist_rows['ssq']['scientific']}`.
- DLT: model `{checklist_rows['dlt']['model']}`; feature `{checklist_rows['dlt']['feature']}`; cutoff `{checklist_rows['dlt']['cutoff']}`; target `{checklist_rows['dlt']['target']}`; rows `1000`; scientific status `{checklist_rows['dlt']['scientific']}`.

Inspect the concise SSQ/DLT lines printed by the command. Evidence paths: `acceptance/final-closure.json`,
`manifest/delivery-manifest.json`, `replay/replay-report.json`, `contracts/local-verifier-contract.json`,
`validation/attempts/A05-phase2-1/receipt.json`, and `validation/attempts/A06-phase2/receipt.json`.

Engineering readiness and model-internal ranking do not establish predictability, lift, winnings, or profit.
"""
    checklist_path = release / "acceptance/local-product-checklist-candidate.md"
    once(checklist_path, checklist)
    candidate_receipt = release / "acceptance/checklist-candidate-receipt.json"
    once(candidate_receipt, {"artifact_type": "phase4_checklist_candidate_receipt", "release_id": release.name, "checklist_sha256": sha(checklist_path), "status": "CANDIDATE_NOT_RELEASED"})
    receipt(release, "D14", [local_contract_path, replay_path], [checklist_path, candidate_receipt], {"content_addressed": True, "candidate_not_released": True, "portable_python_3_12": True, "read_only_entry_point": "scripts/phase4/local-accept-release"}, started)

    environment_path = release / "readiness/environment.json"
    lock = ROOT / "requirements/phase4.lock"
    wheels = sorted((ROOT / "wheelhouse/phase4").glob("*")) if (ROOT / "wheelhouse/phase4").exists() else []
    environment = {"artifact_type": "phase4_execution_environment", "provenance_role": "immutable_linux_formal_builder", "interpreter_realpath": str(Path(sys.executable).resolve()), "python_version": platform.python_version(), "platform": platform.platform(), "dependency_lock_path": "requirements/phase4.lock", "dependency_lock_sha256": sha(lock), "local_verifier_contract_path": "contracts/local-verifier-contract.json", "local_verifier_contract_sha256": sha(local_contract_path), "local_verifier_runtime": "CPython 3.12 any patch on a supported local platform", "historical_suites_local_reexecution_required": False, "wheelhouse": [{"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)} for path in wheels if path.is_file()], "command_receipts": [{"path": path.relative_to(release).as_posix(), "sha256": sha(path)} for path in command_receipts], "status": "PASS"}
    once(environment_path, environment)
    forecast_evidence = {game: validate_forecast(release, game) for game in ("ssq", "dlt")}
    model_evidence = {game: validate_model_evidence(release, game) for game in ("ssq", "dlt")}
    task_hashes = validate_task_receipts(release)

    excluded = FINAL | {"manifest/delivery-manifest.json"}
    entries = []
    for path in sorted(item for item in release.rglob("*") if item.is_file()):
        relative = path.relative_to(release).as_posix()
        if relative not in excluded:
            entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha(path)})
    manifest = {"artifact_type": "phase4_pre_acceptance_delivery_manifest", "release_id": release.name, "entries": entries, "entry_count": len(entries), "coverage": 1.0, "covers_d14": all(any(row["path"] == name for row in entries) for name in ("acceptance/local-product-checklist-candidate.md", "acceptance/checklist-candidate-receipt.json", "receipts/D14.json")), "excluded_final_append_only": sorted(FINAL), "status": "PASS"}
    if not manifest["covers_d14"]:
        raise ValueError("HOLD_D13_DOES_NOT_COVER_D14")
    manifest_path = release / "manifest/delivery-manifest.json"
    once(manifest_path, manifest)
    pre_hashes = {row["path"]: row["sha256"] for row in entries}

    # D15 bottom-up recomputation; no top-level PASS is trusted.
    ledger_events = sorted((release / "runtime/ledger/events").glob("*.json"))
    if not ledger_events or not (release / "runtime/ledger/head.json").is_file():
        raise ValueError("HOLD_APPEND_ONLY_LEDGER_MISSING")
    for relative, expected in pre_hashes.items():
        if sha(release / relative) != expected:
            raise ValueError(f"HOLD_PRE_ACCEPTANCE_CHANGED:{relative}")
    scientific = {game: load(next((release / f"backtests/{game}").glob("*/summary.json")))["scientific_status"] for game in ("ssq", "dlt")}
    acceptance = {"artifact_type": "phase4_machine_acceptance", "release_id": release.name, "recomputed_from_bottom_up": True,
                  "bottom_up_lifecycle": bottom_up["lifecycle"], "bottom_up_schedule_recovery": bottom_up["schedule_recovery"],
                  "task_receipt_hashes": task_hashes, "forecast_evidence": forecast_evidence, "model_evidence": model_evidence,
                  "scientific_status_by_game": scientific, "replay_match_rate": replay["match_rate"],
                  "mutation_detection_rate": replay["mutation_detection_rate"], "protected_inventory": current_protected,
                  "protected_roots_unchanged": True, "manifest_sha256": sha(manifest_path), "manifest_coverage": 1.0,
                  "pre_acceptance_hashes": pre_hashes, "pre_acceptance_unchanged": True, "blocking_findings": [],
                  "machine_state": "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE", "status": "PASS"}
    acceptance_path = release / "acceptance/machine-acceptance.json"
    once(acceptance_path, acceptance)
    release_receipt = {"artifact_type": "phase4_checklist_release_receipt", "checklist_sha256": sha(checklist_path), "manifest_sha256": sha(manifest_path), "machine_acceptance_sha256": sha(acceptance_path), "released_after_machine_pass": True, "status": "PASS"}
    release_receipt_path = release / "acceptance/checklist-release-receipt.json"
    once(release_receipt_path, release_receipt)
    closure = {"artifact_type": "phase4_final_closure", "release_id": release.name, "manifest_sha256": sha(manifest_path), "machine_acceptance_sha256": sha(acceptance_path), "checklist_release_receipt_sha256": sha(release_receipt_path), "pre_acceptance_unchanged": all(sha(release / path) == expected for path, expected in pre_hashes.items()), "machine_state": "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE", "status": "PASS"}
    once(release / "acceptance/final-closure.json", closure)
    print(json.dumps({"status": "PASS", "machine_state": closure["machine_state"], "release": release.name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
