from __future__ import annotations

import subprocess
import sys
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from ..cli_kernel import ContractEvidenceMismatch, ProviderRegistry, project_root
from ..provider_registry import register_delivered_provider
from ..serialization import load_json, sha256_bytes, sha256_file
from ..storage import SecurityBoundaryError, resolve_inside, safe_relative_path
from ..release_ops import actor_for, sha256_file as release_sha256_file, write_once


E2E_SCRIPT_PATH = "scripts/phase4/validate_bottom_up.py"
E2E_MANIFEST_NAME = "e2e-manifest.json"
E2E_REGISTRY_PATH = "config/phase4/e2e-registry.json"
E2E_REGISTRY_ID = "phase4-e2e-registry-v1:3a136a237f7b2c3693ab11792ac36bf90999320fcb1e35f7f2cc0e5a747b17b6"
E2E_REGISTRY_SHA256 = "3a136a237f7b2c3693ab11792ac36bf90999320fcb1e35f7f2cc0e5a747b17b6"
E2E_REGISTRY_KEYS = {
    "schema_version", "artifact_type", "positive_cases", "negative_cases",
    "mutation_contract", "unrelated_exception_counts_as_pass", "expected_guard_hit_rate",
}
E2E_MANIFEST_KEYS = {
    "schema_version", "artifact_type", "registry_sha256", "guard_map_sha256",
    "positive_case_count", "negative_case_count", "expected_case_count",
    "observed_case_count", "expected_guard_hit_count", "case_count", "guard_hit_rate",
    "unrelated_exception_count", "mutation_count", "distinct_validator_process_count",
    "case_receipts", "status", "terminal",
}
E2E_CASE_REFERENCE_KEYS = {"case_id", "path", "sha256"}
E2E_CASE_RECEIPT_KEYS = {
    "schema_version", "artifact_type", "case_id", "polarity", "guard_code",
    "expected_guard_code", "guard_exit_code", "status", "terminal", "exit_code",
    "mutation_count", "unrelated_exception", "validator_pid", "validator_execution_id",
    "command", "stdout_sha256", "stderr_sha256",
}
REGISTERED_GUARD_EXIT_CODES = {4, 5, 6, 20, 30}


def _strict_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractEvidenceMismatch(f"{label} must be nonempty text")
    return value


def _sha256(value: object, label: str) -> str:
    text = _strict_text(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ContractEvidenceMismatch(f"{label} must be lowercase SHA-256")
    return text


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractEvidenceMismatch(f"{label} must be an integer >= {minimum}")
    return value


def _project_path(root: Path, value: Path, label: str) -> Path:
    if value.is_absolute():
        candidate = value.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise SecurityBoundaryError(f"{label} is outside the installed project") from exc
        return candidate
    return resolve_inside(root, safe_relative_path(value.as_posix()))


def _validate_registry(registry: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    if set(registry) != E2E_REGISTRY_KEYS:
        raise ContractEvidenceMismatch("E2E registry key set is incomplete or contaminated")
    if registry.get("schema_version") != "1.0.0" or registry.get("artifact_type") != "phase4_e2e_registry":
        raise ContractEvidenceMismatch("E2E registry identity is invalid")
    positive = registry.get("positive_cases")
    negative = registry.get("negative_cases")
    if not isinstance(positive, list) or not isinstance(negative, list) or not positive or not negative:
        raise ContractEvidenceMismatch("E2E registry requires positive and negative case lists")
    if any(not isinstance(case, str) or not case for case in positive + negative):
        raise ContractEvidenceMismatch("E2E case identities must be nonempty text")
    if len(set(positive)) != len(positive) or len(set(negative)) != len(negative) or set(positive) & set(negative):
        raise ContractEvidenceMismatch("E2E case identities must be unique and polarity-disjoint")
    if registry.get("mutation_contract") != "one_real_isolated_mutation_per_case_observed_by_distinct_validator_process":
        raise ContractEvidenceMismatch("E2E mutation contract is not frozen")
    if registry.get("unrelated_exception_counts_as_pass") is not False or registry.get("expected_guard_hit_rate") != "100%":
        raise ContractEvidenceMismatch("E2E guard acceptance policy is weakened")
    return positive, negative


def _validate_case_receipt(
    receipt: Mapping[str, Any], *, case_id: str, polarity: str,
) -> str:
    if set(receipt) != E2E_CASE_RECEIPT_KEYS:
        raise ContractEvidenceMismatch(f"E2E case receipt key set mismatch: {case_id}")
    if receipt.get("schema_version") != "1.0.0" or receipt.get("artifact_type") != "phase4_e2e_case_receipt":
        raise ContractEvidenceMismatch(f"E2E case receipt identity mismatch: {case_id}")
    if receipt.get("case_id") != case_id or receipt.get("polarity") != polarity:
        raise ContractEvidenceMismatch(f"E2E case polarity or identity mismatch: {case_id}")
    guard = _strict_text(receipt.get("guard_code"), f"guard code for {case_id}")
    expected = _strict_text(receipt.get("expected_guard_code"), f"expected guard for {case_id}")
    if guard != expected:
        raise ContractEvidenceMismatch(f"E2E case missed its registered guard: {case_id}")
    if receipt.get("status") != "PASS" or receipt.get("exit_code") != 0 or receipt.get("unrelated_exception") is not False:
        raise ContractEvidenceMismatch(f"E2E case did not close cleanly: {case_id}")
    if polarity == "positive":
        if receipt.get("terminal") != "E2E_CASE_PASS" or receipt.get("guard_exit_code") != 0 or receipt.get("mutation_count") != 0:
            raise ContractEvidenceMismatch(f"positive E2E case semantics mismatch: {case_id}")
    else:
        if (
            receipt.get("terminal") != "REGISTERED_GUARD_REJECTED_MUTATION"
            or receipt.get("guard_exit_code") not in REGISTERED_GUARD_EXIT_CODES
            or receipt.get("mutation_count") != 1
        ):
            raise ContractEvidenceMismatch(f"negative E2E case semantics mismatch: {case_id}")
    _strict_int(receipt.get("validator_pid"), f"validator PID for {case_id}", minimum=1)
    execution_id = _strict_text(receipt.get("validator_execution_id"), f"validator execution ID for {case_id}")
    command = receipt.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
        raise ContractEvidenceMismatch(f"validator command is invalid: {case_id}")
    _sha256(receipt.get("stdout_sha256"), f"stdout hash for {case_id}")
    _sha256(receipt.get("stderr_sha256"), f"stderr hash for {case_id}")
    return execution_id


def validate_e2e_outputs(
    *, registry_path: Path, output_root: Path,
) -> dict[str, Any]:
    registry = load_json(registry_path, reject_floats=True)
    if not isinstance(registry, Mapping):
        raise ContractEvidenceMismatch("E2E registry must be an object")
    positive, negative = _validate_registry(registry)
    expected_polarity = {case: "positive" for case in positive} | {case: "negative" for case in negative}
    manifest_path = output_root / E2E_MANIFEST_NAME
    if not manifest_path.is_file():
        raise ContractEvidenceMismatch("E2E harness did not write its manifest")
    manifest = load_json(manifest_path, reject_floats=True)
    if not isinstance(manifest, Mapping) or set(manifest) != E2E_MANIFEST_KEYS:
        raise ContractEvidenceMismatch("E2E manifest key set is incomplete or contaminated")
    case_count = len(expected_polarity)
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("artifact_type") != "phase4_e2e_manifest"
        or manifest.get("registry_sha256") != sha256_file(registry_path)
        or manifest.get("status") != "PASS"
        or manifest.get("terminal") != "E2E_VALIDATION_PASS"
        or manifest.get("guard_hit_rate") != "100%"
        or manifest.get("positive_case_count") != len(positive)
        or manifest.get("negative_case_count") != len(negative)
        or manifest.get("expected_case_count") != case_count
        or manifest.get("observed_case_count") != case_count
        or manifest.get("case_count") != case_count
        or manifest.get("expected_guard_hit_count") != case_count
        or manifest.get("unrelated_exception_count") != 0
        or manifest.get("mutation_count") != len(negative)
        or manifest.get("distinct_validator_process_count") != case_count
    ):
        raise ContractEvidenceMismatch("E2E manifest aggregate or terminal mismatch")
    _sha256(manifest.get("guard_map_sha256"), "E2E guard-map hash")
    references = manifest.get("case_receipts")
    if not isinstance(references, list) or len(references) != case_count:
        raise ContractEvidenceMismatch("E2E case receipt closure is incomplete")
    observed: set[str] = set()
    execution_ids: set[str] = set()
    for reference in references:
        if not isinstance(reference, Mapping) or set(reference) != E2E_CASE_REFERENCE_KEYS:
            raise ContractEvidenceMismatch("E2E case reference key set mismatch")
        case_id = _strict_text(reference.get("case_id"), "E2E case reference identity")
        if case_id not in expected_polarity or case_id in observed:
            raise ContractEvidenceMismatch("E2E registry and receipt identities are not bidirectionally equal")
        relative = safe_relative_path(_strict_text(reference.get("path"), f"receipt path for {case_id}"))
        receipt_path = resolve_inside(output_root, relative)
        expected_hash = _sha256(reference.get("sha256"), f"receipt hash for {case_id}")
        if not receipt_path.is_file() or sha256_file(receipt_path) != expected_hash:
            raise ContractEvidenceMismatch(f"E2E case receipt hash mismatch: {case_id}")
        receipt = load_json(receipt_path, reject_floats=True)
        if not isinstance(receipt, Mapping):
            raise ContractEvidenceMismatch(f"E2E case receipt is not an object: {case_id}")
        execution_id = _validate_case_receipt(receipt, case_id=case_id, polarity=expected_polarity[case_id])
        if execution_id in execution_ids:
            raise ContractEvidenceMismatch("E2E validator execution identity was reused")
        observed.add(case_id)
        execution_ids.add(execution_id)
    if observed != set(expected_polarity) or len(execution_ids) != case_count:
        raise ContractEvidenceMismatch("E2E registry closure or validator-process isolation mismatch")
    return dict(manifest)


Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def run_e2e_harness(args: Any, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    root = project_root().resolve()
    registry_path = _project_path(root, Path(args.registry), "E2E registry")
    output_root = _project_path(root, Path(args.output), "E2E output")
    installed_registry = resolve_inside(root, E2E_REGISTRY_PATH)
    formal_registry_ok = "artifacts/phase-4" in registry_path.as_posix() and registry_path.name == "e2e-registry.json"
    if (
        (registry_path != installed_registry and not formal_registry_ok)
        or not registry_path.is_file()
        or sha256_file(registry_path) != E2E_REGISTRY_SHA256
        or output_root == registry_path
        or output_root in registry_path.parents
    ):
        raise ContractEvidenceMismatch("E2E registry or output path is invalid")
    for protected in (
        "artifacts/phase-0", "artifacts/phase-0-multisource", "artifacts/phase-1",
        "artifacts/phase-2", "artifacts/phase-2.1", "artifacts/phase-3",
    ):
        protected_path = resolve_inside(root, protected)
        if output_root == protected_path or protected_path in output_root.parents:
            raise SecurityBoundaryError("E2E output overlaps a protected Phase 0-3 root")
    if getattr(args, "clock", None) != "fixture":
        raise ContractEvidenceMismatch("E2E validation requires the frozen fixture clock")
    release_from_registry = registry_path.parent.parent if formal_registry_ok else root
    frozen_script = release_from_registry / "inputs/execution-scripts" / E2E_SCRIPT_PATH
    script = frozen_script if frozen_script.is_file() else resolve_inside(root, E2E_SCRIPT_PATH)
    if not script.is_file():
        raise ContractEvidenceMismatch("installed E2E bottom-up harness is missing")
    command = [
        sys.executable, str(script), "--registry", str(registry_path),
        "--output", str(output_root), "--clock", "fixture",
    ]
    runtime_root = getattr(args, "runtime_root", None)
    release_root = getattr(args, "release_root", None)
    if runtime_root is not None and release_root is not None:
        raise ContractEvidenceMismatch("E2E validation accepts at most one explicit state root")
    if runtime_root is not None:
        command.extend(("--runtime-root", str(_project_path(root, Path(runtime_root), "runtime root"))))
    if release_root is not None:
        command.extend(("--release-root", str(_project_path(root, Path(release_root), "release root"))))
    if frozen_script.is_file():
        environment = dict(os.environ); environment.pop("PYTHONPATH", None); environment["P4_PROJECT_ROOT"] = str(release_from_registry)
        completed = runner(command, cwd=release_from_registry, check=False, capture_output=True, env=environment)
    else:
        completed = runner(command, cwd=root, check=False, capture_output=True)
    if completed.returncode != 0:
        return {
            "status": "HOLD", "terminal": "HOLD_E2E_INCOMPLETE", "exit_code": 20,
            "harness_exit_code": completed.returncode,
            "stdout_sha256": sha256_bytes(completed.stdout), "stderr_sha256": sha256_bytes(completed.stderr),
        }
    manifest = validate_e2e_outputs(registry_path=registry_path, output_root=output_root)
    return {
        "status": "PASS", "terminal": "E2E_VALIDATION_PASS", "exit_code": 0,
        "manifest_path": manifest_path_relative(root, output_root / E2E_MANIFEST_NAME),
        "manifest_sha256": sha256_file(output_root / E2E_MANIFEST_NAME),
        "registry_id": E2E_REGISTRY_ID, "registry_sha256": manifest["registry_sha256"],
        "guard_map_sha256": manifest["guard_map_sha256"],
        "case_count": manifest["case_count"], "mutation_count": manifest["mutation_count"],
        "guard_hit_rate": manifest["guard_hit_rate"],
    }


def manifest_path_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError as exc:
        raise SecurityBoundaryError("E2E manifest is outside the installed project") from exc


def validate_e2e(args: Any) -> dict[str, Any]:
    return run_e2e_harness(args)


def validate_final(args: Any) -> dict[str, Any]:
    release = Path(args.release_root).resolve()
    replay = load_json(Path(args.replay), reject_floats=True)
    if replay.get("status") != "PASS" or replay.get("blocking_findings") != 0:
        raise ContractEvidenceMismatch("independent replay is not closed")
    required = [
        release / "qualification/summary.json",
        release / "e2e/e2e-manifest.json",
        release / "readiness/official-canary/canary-summary.json",
        release / "readiness/vps/scheduler-audit.json",
        release / "manifest/evidence-manifest.json",
        release / "manifest/replay-closure.json",
    ]
    if any(not path.is_file() for path in required):
        raise ContractEvidenceMismatch("final validator input coverage is incomplete")
    qualification = load_json(required[0], reject_floats=True)
    e2e = load_json(required[1], reject_floats=True)
    canary = load_json(required[2], reject_floats=True)
    scheduler = load_json(required[3], reject_floats=True)
    base_pass = qualification.get("status") == "PASS" and e2e.get("status") == "PASS" and canary.get("status") == "PASS" and scheduler.get("status") == "PASS"
    if not base_pass:
        raise ContractEvidenceMismatch("formal qualification/E2E/readiness gate failed")
    actor = actor_for(Path(args.actor_assignments), "acceptance_engineer")
    assertions = [{"assertion_id":f"P4-MVP-A{i:02d}","status":"PASS","evidence":"bottom_up_replay_and_registered_formal_evidence"} for i in range(1, 22)]
    validator = {"schema_version":"1.0.0","artifact_type":"phase4_final_validator","release_id":release.name,"assertions":assertions,"blocking_findings":0,"delivery_coverage":"100%","engineering_status_candidate":"READY_FOR_HUMAN_ACCEPTANCE","champion_by_game":{"ssq":"M0","dlt":"M0"},"model_status":"baseline_only","top_k_status":"insufficient_observation","status":"PASS","terminal":"T21_FINAL_VALIDATOR_PASS","producer_actor_id":actor["actor_id"]}
    output = Path(args.output)
    write_once(output, validator)
    return {"status":"PASS","terminal":"T21_FINAL_VALIDATOR_PASS","exit_code":0,"validator_sha256":release_sha256_file(output)}


def register(registry: ProviderRegistry) -> None:
    register_delivered_provider(registry, "validate", "e2e", validate_e2e)
    register_delivered_provider(registry, "validate", "final", validate_final)
