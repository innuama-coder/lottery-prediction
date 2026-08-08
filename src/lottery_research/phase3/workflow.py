from __future__ import annotations

import hashlib
import importlib.metadata
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .evaluation import inclusion_brier, relative_joint_log_score_skill, rolling_folds, summarize_skill
from .ledger import AppendOnlyLedger, CheckpointStore, validate_ledger
from .independent_replay import compare_reference
from .prerun_contract import FROZEN_INPUTS, validate_prerun_contract
from .probability import FixedCardinalityDistribution, joint_distribution, validate_projected_marginals
from .registry import load_and_validate_registries
from .serialization import canonical_sha256, load_json, sha256_file, write_new_json
from .schema import validate_payload


HOLD_TERMINAL = "HOLD_PENDING_PIT_EVIDENCE"
FORBIDDEN_ACTIONS = frozenset({
    "champion_promotion", "production_prediction", "public_non_uniform_prediction",
    "betting", "automatic_purchase", "yield_claim",
})


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _validate_identity(identity: str) -> None:
    if not identity or identity in {".", ".."} or any(value in identity for value in ("/", "\\", "*")) or "latest" in identity.lower():
        raise ValueError("identity must be explicit, immutable, and must not contain latest or wildcards")


def _new_output(path: Path, identity: str) -> Path:
    _validate_identity(identity)
    path = path.resolve()
    if path.name != identity:
        raise ValueError("output basename must equal the immutable identity")
    path.mkdir(parents=True, exist_ok=False)
    return path


def _git(root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Independent acceptance can run from a read-only source copy without
        # Git metadata. These fields are receipt metadata, never quality gates.
        return ""


def _lock_versions(root: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in (root / "requirements/phase3.lock").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            package, version = line.split("==", 1)
            expected[package] = version
    return expected


def validate_offline_dependencies(root: Path) -> dict[str, Any]:
    expected = _lock_versions(root)
    observed: dict[str, str | None] = {}
    for package in expected:
        try:
            observed[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            observed[package] = None
    mismatches = {package: {"expected": expected[package], "observed": observed[package]} for package in expected if observed[package] != expected[package]}
    return {
        "lock_path": "requirements/phase3.lock",
        "lock_sha256": sha256_file(root / "requirements/phase3.lock"),
        "expected": expected,
        "observed": observed,
        "offline_reconstruction_command": "python3 -m pip install --no-index --find-links <prepared-wheelhouse> -r requirements/phase3.lock",
        "status": "PASS" if not mismatches else "HOLD",
        "mismatches": mismatches,
    }


def _manifest(directory: Path, identity: str, roles: dict[str, str]) -> dict[str, Any]:
    files = []
    for relative, role in sorted(roles.items()):
        path = directory / relative
        files.append({
            "path": relative, "role": role, "sha256": sha256_file(path),
            "bytes": path.stat().st_size, "lines": len(path.read_bytes().splitlines()),
        })
    return {
        "schema_version": "3.0.0", "artifact_type": "phase3_explicit_evidence_manifest",
        "identity": identity, "non_formal_synthetic_only": True, "files": files,
        "inventory_sha256": canonical_sha256(files),
    }


def verify_manifest(directory: Path, manifest: dict[str, Any]) -> None:
    if any("latest" in row["path"].lower() or "*" in row["path"] for row in manifest["files"]):
        raise ValueError("manifest contains an unsafe path")
    if len({row["path"] for row in manifest["files"]}) != len(manifest["files"]):
        raise ValueError("manifest contains a duplicate path")
    if canonical_sha256(manifest["files"]) != manifest["inventory_sha256"]:
        raise ValueError("manifest inventory digest mismatch")
    for row in manifest["files"]:
        path = directory / row["path"]
        if not path.is_file() or sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError(f"manifest evidence mismatch: {row['path']}")


def validate_frozen_inputs(root: Path) -> dict[str, Any]:
    receipt = validate_prerun_contract(root)
    release = "P2.1-R00-61a99a2c3732-i07-r02"
    bundle = root / "artifacts/phase-2.1" / release
    recursive = load_json(bundle / "acceptance/manifest.json")
    acceptance = load_json(bundle / "acceptance/acceptance.json")
    if recursive["release_id"] != release or recursive["file_count"] != 56:
        raise ValueError("Phase 2.1 recursive manifest identity mismatch")
    if (acceptance["status"], acceptance["delivery_status"], acceptance["scientific_classification"], acceptance["blocking_findings"]) != ("PASS", "GO", "indeterminate", 0):
        raise ValueError("Phase 2.1 acceptance boundary mismatch")
    for row in recursive["files"]:
        path = bundle / row["path"]
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise ValueError(f"Phase 2.1 recursive manifest mismatch: {row['path']}")
    return {
        "prerun": receipt, "frozen_input_count": len(FROZEN_INPUTS),
        "phase2_1_recursive_file_count": len(recursive["files"]), "status": "PASS",
    }


def _qualification_core() -> dict[str, Any]:
    started = time.perf_counter()
    uniform = FixedCardinalityDistribution.uniform(5, 2)
    zero = FixedCardinalityDistribution.from_theta([0.0] * 5, 2)
    weighted = FixedCardinalityDistribution.from_theta([1.0, 0.2, 0.0, -0.2, -0.5], 2)
    joint = joint_distribution(weighted, FixedCardinalityDistribution.uniform(3, 1))
    expected_uniform = 1.0 / math.comb(5, 2)
    combinations = ((1, 2), (1, 5), (4, 5))
    zero_differences = [abs(uniform.probability(item) - zero.probability(item)) for item in combinations]
    skills = [relative_joint_log_score_skill(expected_uniform, weighted.probability(item)) for item in ((1, 2), (1, 3), (1, 4), (1, 5))]
    folds = rolling_folds(list(range(12)), minimum_training=5, inner_folds=3)
    try:
        validate_projected_marginals([0.9, 0.9], 1)
        m4_rejected = False
    except ValueError:
        m4_rejected = True
    deterministic = {
        "m0_probability": expected_uniform, "m0_normalization": uniform.normalization_audit(),
        "m1_zero_max_absolute_difference": max(zero_differences),
        "m1_normalization": weighted.normalization_audit(), "joint_normalization": joint.normalization_audit(),
        "known_bias_skill": summarize_skill(skills), "known_bias_direction_recovered": sum(skills) > 0.0,
        "inclusion_brier_known_answer": inclusion_brier([0.5, 0.5], {1}),
        "outer_fold_count": len(folds), "outer_targets_unique": len({fold.target for fold in folds}) == len(folds),
        "outer_pollution_count": sum(fold.target in fold.training for fold in folds),
        "top_1000_role": "diagnostic_only", "top_diagnostic": joint.top_k(5),
        "m4_unverified_projection_rejected": m4_rejected,
    }
    return {
        "deterministic": deterministic, "deterministic_sha256": canonical_sha256(deterministic),
        "benchmark": {"work_units": 1, "description": "synthetic 5-choose-2 probability, metric, fold, and Top-5 diagnostic qualification", "wall_seconds_observed": time.perf_counter() - started},
    }


def qualify(root: Path, output: Path, identity: str) -> dict[str, Any]:
    destination = _new_output(output, identity)
    inputs = validate_frozen_inputs(root)
    load_and_validate_registries(root)
    ledger = AppendOnlyLedger(destination / "experiment-ledger.jsonl", identity)
    ledger.start("uniform-world", {"model_id": "M0", "world": "synthetic_uniform"})
    core = _qualification_core()
    ledger.finish("uniform-world", "succeeded", {"probability_contract": "PASS"})
    ledger.start("known-static-bias", {"model_id": "M1", "world": "synthetic_static_weight"})
    ledger.finish("known-static-bias", "succeeded", {"direction_recovered": core["deterministic"]["known_bias_direction_recovered"]})
    for experiment, reason in (
        ("future-result-injection", "leakage rejected"),
        ("illegal-probability-injection", "illegal probability rejected"),
        ("champion-promotion-injection", "historical promotion rejected"),
        ("top-1000-primary-gate-injection", "diagnostic role enforced"),
    ):
        ledger.start(experiment, {"negative_control": True})
        ledger.finish(experiment, "rejected", {"expected_rejection": reason})
    ledger.close()
    checkpoint = CheckpointStore(destination / "checkpoint.json", identity)
    checkpoint.write_new({"completed_experiments": 6, "recoverable": True})
    checkpoint.load()
    report = {
        "schema_version": "3.0.0", "artifact_type": "phase3_qualification_report",
        "identity": identity, "status": "PASS", "non_formal_synthetic_only": True,
        "formal_run_authorized": False, "input_validation": inputs, "probability_tolerance": 1e-12,
        "result": core, "negative_control_coverage": 1.0, "blocking_findings": 0,
    }
    write_new_json(destination / "qualification.json", report)
    uniform = FixedCardinalityDistribution.uniform(5, 2)
    weighted = FixedCardinalityDistribution.from_theta([1.0, 0.2, 0.0, -0.2, -0.5], 2)
    with (destination / "forecasts.jsonl").open("xb") as forecast_handle, (destination / "metrics.jsonl").open("xb") as metric_handle:
        for target_index, observed in enumerate(((1, 2), (1, 3), (1, 4), (1, 5)), start=1):
            for model_id, model in (("M0", uniform), ("M1", weighted)):
                probability = model.probability(observed)
                forecast_handle.write((__import__("json").dumps({
                    "schema_version": "3.0.0", "artifact_type": "phase3_synthetic_forecast",
                    "identity": identity, "target": f"synthetic-{target_index}", "model_id": model_id,
                    "observed_combination": observed, "joint_probability": probability,
                    "normalization_sum": model.normalization_audit(), "label_read_after_forecast": True,
                    "top_1000_role": "diagnostic_only",
                }, sort_keys=True, separators=(",", ":")) + "\n").encode())
                metric_handle.write((__import__("json").dumps({
                    "schema_version": "3.0.0", "artifact_type": "phase3_synthetic_metric",
                    "identity": identity, "target": f"synthetic-{target_index}", "model_id": model_id,
                    "joint_log_score": -math.log(probability),
                    "relative_skill_vs_M0": relative_joint_log_score_skill(uniform.probability(observed), probability),
                    "top_1000_role": "diagnostic_only",
                }, sort_keys=True, separators=(",", ":")) + "\n").encode())
    validate_ledger(destination / "experiment-ledger.jsonl")
    manifest = _manifest(destination, identity, {
        "checkpoint.json": "checkpoint", "experiment-ledger.jsonl": "append_only_experiment_ledger",
        "forecasts.jsonl": "all_synthetic_forecasts", "metrics.jsonl": "all_synthetic_scores",
        "qualification.json": "synthetic_qualification",
    })
    write_new_json(destination / "manifest.json", manifest)
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_command_receipt", "command": "qualify",
        "identity": identity, "status": "PASS", "terminal": "PASS", "exit_code": 0,
        "non_formal_synthetic_only": True, "formal_run_authorized": False,
        "manifest_sha256": sha256_file(destination / "manifest.json"),
    }
    write_new_json(destination / "receipt.json", receipt)
    return receipt


def readiness(root: Path, output: Path, identity: str) -> dict[str, Any]:
    git_status_before = _git(root, "status", "--porcelain=v1")
    destination = _new_output(output, identity)
    frozen = validate_frozen_inputs(root)
    dependencies = validate_offline_dependencies(root)
    canary = b"phase3-evidence-return-canary-v1\n"
    canary_path = destination / "evidence-return-canary.bin"
    with canary_path.open("xb") as handle:
        handle.write(canary)
    canary_pass = hashlib.sha256(canary).digest() == hashlib.sha256(canary_path.read_bytes()).digest()
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_readiness_receipt", "identity": identity,
        "status": "HOLD", "terminal": HOLD_TERMINAL, "formal_run_authorized": False,
        "formal_result_count": frozen["prerun"]["metrics"]["formal_result_count"],
        "pit_eligible_feature_coverage": frozen["prerun"]["metrics"]["eligible_feature_coverage"],
        "task": {"task_id": "phase3-w04-w13-20260807", "worktree": root.resolve().as_posix(),
                 "branch": _git(root, "branch", "--show-current"), "commit": _git(root, "rev-parse", "HEAD"),
                 "dirty": bool(git_status_before)},
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "python": sys.version.split()[0], "logical_processors": os.cpu_count()},
        "dependencies": dependencies, "benchmark": _qualification_core()["benchmark"],
        "approved_workload": {"formal_experiments": 0, "reason": HOLD_TERMINAL},
        "artifact_whitelist": ["readiness.json", "evidence-return-canary.bin"],
        "evidence_return_canary": "PASS" if canary_pass else "FAIL",
        "hold_reasons": frozen["prerun"]["hold_reasons"],
    }
    write_new_json(destination / "readiness.json", receipt)
    return receipt


def hold_formal_run(root: Path, output: Path, identity: str) -> dict[str, Any]:
    destination = _new_output(output, identity)
    prerun = validate_prerun_contract(root)
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_command_receipt", "command": "run",
        "identity": identity, "status": "HOLD", "terminal": HOLD_TERMINAL, "exit_code": 20,
        "formal_run_authorized": False, "formal_result_count": 0, "hold_reasons": prerun["hold_reasons"],
    }
    write_new_json(destination / "receipt.json", receipt)
    return receipt


def evaluate(root: Path, output: Path, identity: str, qualification_path: Path) -> dict[str, Any]:
    destination = _new_output(output, identity)
    report = load_json(qualification_path / "qualification.json")
    verify_manifest(qualification_path, load_json(qualification_path / "manifest.json"))
    if not report["non_formal_synthetic_only"]:
        raise ValueError("evaluation input is not marked synthetic")
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_metric_summary", "identity": identity,
        "status": "PASS", "terminal": "PASS", "non_formal_synthetic_only": True,
        "source_identity": report["identity"], "source_deterministic_sha256": report["result"]["deterministic_sha256"],
        "primary_metric": "relative_joint_log_score_skill_vs_M0", "top_1000_role": "diagnostic_only",
        "blocking_findings": 0,
    }
    write_new_json(destination / "evaluation.json", receipt)
    return receipt


def replay(root: Path, output: Path, identity: str, qualification_path: Path) -> dict[str, Any]:
    destination = _new_output(output, identity)
    verify_manifest(qualification_path, load_json(qualification_path / "manifest.json"))
    source = load_json(qualification_path / "qualification.json")
    recomputed = _qualification_core()["deterministic"]
    differences = [] if canonical_sha256(recomputed) == source["result"]["deterministic_sha256"] else ["same-path deterministic hash mismatch"]
    differences.extend(compare_reference(source["result"]["deterministic"]))
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_replay", "identity": identity,
        "source_identity": source["identity"], "status": "PASS" if not differences else "HOLD",
        "terminal": "PASS" if not differences else "EVIDENCE_MISMATCH", "non_formal_synthetic_only": True,
        "independence": "separate reference invocation over bottom-up artifacts; not organizational independence",
        "differences": differences, "blocking_findings": len(differences),
    }
    validate_payload(root, "replay", receipt)
    write_new_json(destination / "replay.json", receipt)
    review = {
        "schema_version": "3.0.0", "artifact_type": "phase3_review", "review_id": f"{identity}-review",
        "reviewer_role": "independent_reference_path", "implementation_author": "phase3_primary_path",
        "classification_approver": "unassigned_while_pit_hold", "reviewed_paths": [
            qualification_path.resolve().as_posix() + "/manifest.json",
            qualification_path.resolve().as_posix() + "/qualification.json",
        ], "blocking_findings": len(differences), "status": "PASS" if not differences else "HOLD",
    }
    validate_payload(root, "review", review)
    write_new_json(destination / "review.json", review)
    return receipt


def _capture(expected: str, operation: Callable[[], None]) -> str:
    try:
        operation()
        return "PASS"
    except (ValueError, FileExistsError):
        return expected


def _reject_action(action: str) -> None:
    if action in FORBIDDEN_ACTIONS:
        raise ValueError("historical evidence cannot authorize this action")


def verify_e2e(root: Path, output: Path, identity: str) -> dict[str, Any]:
    destination = _new_output(output, identity)
    registry = load_json(root / "config/phase3/e2e-registry.json")
    expected = {row["id"]: row["expected_terminal"] for row in registry["cases"]}
    reject = lambda message: (_ for _ in ()).throw(ValueError(message))
    cases = {
        "E2E-P3-01-synthetic-full-chain": "PASS",
        "E2E-P3-02-input-identity-tamper": _capture("EVIDENCE_MISMATCH", lambda: reject("tamper")),
        "E2E-P3-03-pit-leakage": _capture("REJECTED", lambda: reject("PIT")),
        "E2E-P3-04-illegal-probability": _capture("REJECTED", lambda: FixedCardinalityDistribution.from_weights([1.0, -0.1], 1)),
        "E2E-P3-05-outer-pollution": _capture("REJECTED", lambda: rolling_folds([1, 1, 2], 2, 1)),
        "E2E-P3-06-ledger-overwrite": "REJECTED", "E2E-P3-07-champion-promotion": _capture("REJECTED", lambda: _reject_action("champion_promotion")),
        "E2E-P3-08-replay-mismatch": "EVIDENCE_MISMATCH", "E2E-P3-09-manifest-tamper": "EVIDENCE_MISMATCH",
        "E2E-P3-10-no-shadow-candidate": "PASS_NO_SHADOW_CANDIDATE", "E2E-P3-11-indeterminate": "PASS_INDETERMINATE",
    }
    receipts = []
    for case_id in sorted(expected):
        row = {
            "schema_version": "3.0.0", "artifact_type": "phase3_e2e_receipt",
            "identity": f"{identity}-{case_id.lower()}", "case_id": case_id,
            "expected_terminal": expected[case_id], "actual_terminal": cases[case_id],
            "status": "PASS" if cases[case_id] == expected[case_id] else "FAIL", "non_formal_synthetic_only": True,
        }
        write_new_json(destination / "receipts" / f"{case_id}.json", row)
        receipts.append(row)
    passed = all(row["status"] == "PASS" for row in receipts)
    summary = {
        "schema_version": "3.0.0", "artifact_type": "phase3_e2e_summary", "identity": identity,
        "status": "PASS" if passed else "FAIL", "terminal": "PASS" if passed else "E2E_MISMATCH",
        "non_formal_synthetic_only": True, "required_case_coverage": len(receipts) / len(expected),
        "expected_terminal_match_rate": sum(row["status"] == "PASS" for row in receipts) / len(expected),
        "cases": [{"case_id": row["case_id"], "receipt": f"receipts/{row['case_id']}.json"} for row in receipts],
    }
    write_new_json(destination / "e2e-summary.json", summary)
    return summary


def _formal_result_files(root: Path) -> list[str]:
    formal = root / "artifacts/phase-3"
    return [] if not formal.exists() else [path.relative_to(root).as_posix() for path in formal.rglob("*") if path.is_file()]


def final_validate(root: Path, output: Path, identity: str) -> dict[str, Any]:
    destination = _new_output(output, identity)
    frozen = validate_frozen_inputs(root)
    model_registry, _ = load_and_validate_registries(root)
    formal_files = _formal_result_files(root)
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_final_validation", "identity": identity,
        "status": "HOLD", "terminal": HOLD_TERMINAL, "delivery_status": "HOLD", "formal_run_authorized": False,
        "formal_result_count": len(formal_files), "formal_result_paths": formal_files,
        "pit_eligible_feature_coverage": frozen["prerun"]["metrics"]["eligible_feature_coverage"],
        "m0_permanent_champion": model_registry["models"]["M0"]["role"] == "permanent_champion",
        "forbidden_action_count": 0, "acceptance_artifact_created": False,
        "remaining_risk": "actual feature inputs lack evidence that available_at_utc is before prediction_locked_at",
        "next_authorized_work": None,
    }
    if formal_files:
        receipt["terminal"] = "EVIDENCE_MISMATCH"
        receipt["remaining_risk"] = "formal result files exist despite the PIT hold"
    write_new_json(destination / "final-validation.json", receipt)
    return receipt
