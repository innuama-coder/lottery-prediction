from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .prerun_contract import FROZEN_INPUTS, validate_prerun_work_item
from .schema import validate_payload
from .serialization import load_json, sha256_file, write_new_json


PRERUN_ROLES = {"W01": "data_custodian", "W02": "data_custodian", "W03": "statistical_owner"}
WORK_ITEM_ROLES = {
    **PRERUN_ROLES,
    "W04": "implementation_author", "W05": "statistical_owner", "W06": "independent_method_reviewer",
    "W07": "release_controller", "W08": "run_operator", "W09": "statistical_owner",
    "W10": "independent_reviewer", "W11": "acceptance_engineer", "W12": "classification_approver",
    "W13": "release_controller",
}
PRERUN_OUTPUTS = {
    "W01": ("config/phase3/input-manifest.json",),
    "W02": ("config/phase3/availability-ledger.json", "config/phase3/data-time-contract.json"),
    "W03": ("config/phase3/preregistration.json",),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _artifact_rows(root: Path, paths: Sequence[Path]) -> list[dict[str, str]]:
    rows = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            raise ValueError(f"work item artifact is missing: {resolved}")
        rows.append({"path": _display_path(root, resolved), "sha256": sha256_file(resolved)})
    return rows


def load_actor_assignment(root: Path, path: Path) -> tuple[dict[str, Any], str]:
    resolved = _resolve(root, path).resolve()
    payload = load_json(resolved)
    validate_payload(root, "actor_assignment", payload)
    for row in payload["assignments"]:
        record = (resolved.parent / row["task_record_path"]).resolve()
        try:
            record.relative_to(resolved.parent)
        except ValueError as exc:
            raise ValueError(f"actor assignment task record escapes its bundle: {row['role']}") from exc
        if not record.is_file() or sha256_file(record) != row["task_record_sha256"]:
            raise ValueError(f"actor assignment task record mismatch: {row['role']}")
    return payload, sha256_file(resolved)


def validate_review_provenance(root: Path, review_path: Path, actor_path: Path, manifest_path: Path) -> dict[str, Any]:
    review = load_json(_resolve(root, review_path))
    validate_payload(root, "review", review)
    assignment, assignment_sha256 = load_actor_assignment(root, actor_path)
    if assignment["assignment_stage"] != "formal_before_W07" or review["actor_assignment_sha256"] != assignment_sha256:
        raise ValueError("review does not bind the formal actor assignment")
    by_role = {row["role"]: row for row in assignment["assignments"]}
    reviewer = by_role["independent_reviewer"]
    author = by_role["implementation_author"]
    approver = by_role["classification_approver"]
    if (review["reviewer_id"], review["review_task_id"], review["review_session_id"], review["review_task_record_sha256"]) != (reviewer["actor_id"], reviewer["task_id"], reviewer["session_id"], reviewer["task_record_sha256"]):
        raise ValueError("reviewer provenance does not match actor assignment")
    if review["implementation_author_id"] != author["actor_id"] or review["classification_approver_id"] != approver["actor_id"]:
        raise ValueError("review conflict identities do not match actor assignment")
    if review["reviewed_manifest_sha256"] != sha256_file(_resolve(root, manifest_path)):
        raise ValueError("reviewed manifest hash mismatch")
    return review


def validate_acceptance_provenance(root: Path, acceptance_path: Path, actor_path: Path) -> dict[str, Any]:
    acceptance = load_json(_resolve(root, acceptance_path))
    validate_payload(root, "acceptance", acceptance)
    assignment, assignment_sha256 = load_actor_assignment(root, actor_path)
    if assignment["assignment_stage"] != "formal_before_W07" or acceptance["actor_assignment_sha256"] != assignment_sha256:
        raise ValueError("acceptance does not bind the formal actor assignment")
    by_role = {row["role"]: row for row in assignment["assignments"]}
    author = by_role["implementation_author"]
    approver = by_role["classification_approver"]
    if acceptance["implementation_author_id"] != author["actor_id"]:
        raise ValueError("acceptance implementation author does not match actor assignment")
    if (acceptance["classification_approver_id"], acceptance["approver_task_id"], acceptance["approver_session_id"], acceptance["approver_task_record_sha256"]) != (approver["actor_id"], approver["task_id"], approver["session_id"], approver["task_record_sha256"]):
        raise ValueError("acceptance approver provenance does not match actor assignment")
    return acceptance


def validate_work_item_receipt_file(root: Path, receipt_path: Path, actor_path: Path, expected: str) -> dict[str, Any]:
    receipt_resolved = _resolve(root, receipt_path).resolve()
    actor_resolved = _resolve(root, actor_path).resolve()
    receipt = load_json(receipt_resolved)
    validate_payload(root, "work_item_receipt", receipt)
    assignment, assignment_sha256 = load_actor_assignment(root, actor_resolved)
    if receipt["work_item"] != expected:
        raise ValueError(f"work item mismatch: expected {expected}, got {receipt['work_item']}")
    if receipt["actor_assignment_sha256"] != assignment_sha256:
        raise ValueError("work item receipt actor assignment hash mismatch")
    owners = [row for row in assignment["assignments"] if row["role"] == receipt["owner_role"]]
    if len(owners) != 1:
        raise ValueError("work item owner role is not uniquely assigned")
    owner = owners[0]
    if (receipt["owner_id"], receipt["owner_task_id"], receipt["owner_session_id"]) != (owner["actor_id"], owner["task_id"], owner["session_id"]):
        raise ValueError("work item owner does not match actor assignment")
    if any("..." in item or "<" in item or ">" in item for item in receipt["command"]):
        raise ValueError("work item command contains an unresolved placeholder")
    for row in receipt["inputs"] + receipt["outputs"]:
        path = _resolve(root, row["path"])
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise ValueError(f"work item artifact hash mismatch: {row['path']}")
    return {"status": "PASS", "work_item": expected, "receipt_sha256": sha256_file(receipt_resolved), "actor_assignment_sha256": assignment_sha256}


def create_prerun_work_item_receipt(
    root: Path,
    work_item: str,
    identity: str,
    actor_path: Path,
    output: Path,
    upstream_receipt: Path | None,
    command: Sequence[str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    if work_item not in PRERUN_ROLES:
        raise ValueError(f"unsupported prerun work item: {work_item}")
    if (work_item == "W01") != (upstream_receipt is None):
        raise ValueError("W01 must not have an upstream receipt; W02/W03 require one")
    assignment, assignment_sha256 = load_actor_assignment(root, actor_path)
    role = PRERUN_ROLES[work_item]
    owners = [row for row in assignment["assignments"] if row["role"] == role]
    if len(owners) != 1:
        raise ValueError(f"actor assignment must contain exactly one {role}")
    owner = owners[0]
    inputs = [_resolve(root, actor_path)]
    if work_item == "W01":
        inputs.extend(root / relative for _, relative, _, _ in FROZEN_INPUTS)
        inputs.append(root / "tasks/phase3/README.md")
    else:
        assert upstream_receipt is not None
        expected_upstream = f"W0{int(work_item[2:]) - 1}"
        validate_work_item_receipt_file(root, upstream_receipt, actor_path, expected_upstream)
        inputs.append(_resolve(root, upstream_receipt))
        if work_item == "W02":
            inputs.append(root / "config/phase3/input-manifest.json")
        else:
            inputs.extend((root / "config/phase3/availability-ledger.json", root / "config/phase3/data-time-contract.json"))
    started = _utc_now()
    result = validate_prerun_work_item(root, work_item)
    ended = _utc_now()
    receipt = {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_work_item_receipt",
        "work_item": work_item,
        "identity": identity,
        "actor_assignment_sha256": assignment_sha256,
        "owner_role": role,
        "owner_id": owner["actor_id"],
        "owner_task_id": owner["task_id"],
        "owner_session_id": owner["session_id"],
        "inputs": _artifact_rows(root, inputs),
        "outputs": _artifact_rows(root, [root / value for value in PRERUN_OUTPUTS[work_item]]),
        "command": list(command if command is not None else sys.argv),
        "started_at_utc": started,
        "ended_at_utc": ended,
        "process_exit_code": 0,
        "status": "PASS",
        "terminal": result["terminal"],
    }
    validate_payload(root, "work_item_receipt", receipt)
    write_new_json(_resolve(root, output), receipt)
    return {"status": "PASS", "terminal": result["terminal"], "work_item": work_item, "metrics": result["metrics"], "receipt": _display_path(root, _resolve(root, output))}


def create_command_work_item_receipt(
    root: Path,
    *,
    work_item: str,
    identity: str,
    actor_path: Path,
    upstream_actor_path: Path,
    upstream_receipt: Path,
    command_output: Path,
    receipt_output: Path,
    command: Sequence[str],
    started_at_utc: str,
    ended_at_utc: str,
    process_exit_code: int,
    status: str,
    terminal: str,
) -> dict[str, Any]:
    if work_item not in WORK_ITEM_ROLES or work_item in PRERUN_ROLES:
        raise ValueError(f"command receipt cannot be emitted for {work_item}")
    expected_upstream = f"W{int(work_item[1:]) - 1:02d}"
    validate_work_item_receipt_file(root, upstream_receipt, upstream_actor_path, expected_upstream)
    _, upstream_assignment_sha256 = load_actor_assignment(root, upstream_actor_path)
    assignment, assignment_sha256 = load_actor_assignment(root, actor_path)
    if work_item == "W07" and assignment["parent_assignment_sha256"] != upstream_assignment_sha256:
        raise ValueError("formal actor assignment does not bind the W06 preparation assignment")
    role = WORK_ITEM_ROLES[work_item]
    owners = [row for row in assignment["assignments"] if row["role"] == role]
    if len(owners) != 1:
        raise ValueError(f"actor assignment must contain exactly one {role}")
    owner = owners[0]
    output_root = _resolve(root, command_output)
    output_files = sorted(path for path in output_root.rglob("*") if path.is_file())
    if not output_files:
        raise ValueError("work item command produced no output artifacts")
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_work_item_receipt",
        "work_item": work_item, "identity": identity, "actor_assignment_sha256": assignment_sha256,
        "owner_role": role, "owner_id": owner["actor_id"], "owner_task_id": owner["task_id"], "owner_session_id": owner["session_id"],
        "inputs": _artifact_rows(root, [_resolve(root, actor_path), _resolve(root, upstream_actor_path), _resolve(root, upstream_receipt)]),
        "outputs": _artifact_rows(root, output_files), "command": list(command),
        "started_at_utc": started_at_utc, "ended_at_utc": ended_at_utc,
        "process_exit_code": process_exit_code, "status": status, "terminal": terminal,
    }
    validate_payload(root, "work_item_receipt", receipt)
    write_new_json(_resolve(root, receipt_output), receipt)
    return receipt
