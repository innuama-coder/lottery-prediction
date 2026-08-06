from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from . import BASELINE_SHA, RELEASE_ID
from .serialization import canonical_json_bytes, load_json, write_new_json
from .schema import validate
from .workflow import (
    _canonical_external_command_contract,
    accept,
    build_evidence_manifest,
    bundle_path,
    freeze_g0_g1,
    historical_audit,
    independent_method_review,
    independent_replay_review,
    power,
    prepare_release,
    project_root,
    qualify,
    replay,
    run_final_validation_negative_suite,
    run_e2e,
    validate_final_bundle,
    validate_preregistration,
    validate_readiness,
    verify_readiness_read_only,
    verify_evidence_manifest,
    now,
)


def receipt_input_identity(destination: Path) -> dict[str, object]:
    readiness_path = destination / "readiness/readiness.json"
    if readiness_path.is_file():
        return load_json(readiness_path)["input_identity"]
    return {
        "release_id": RELEASE_ID,
        "baseline_sha": BASELINE_SHA,
        "phase1_frozen": [],
        "phase2_frozen": [],
        "task_inputs": {},
        "task_input_aggregate_sha256": "0" * 64,
    }


def execute_external_commands(root: Path, destination: Path) -> list[dict[str, object]]:
    environment = os.environ.copy()
    environment["PATH"] = f"{root / '.phase2_1/venv/bin'}{os.pathsep}{environment.get('PATH', '')}"
    environment["PIP_NO_INDEX"] = "1"
    receipts = []
    for definition in _canonical_external_command_contract(destination):
        external_started = now()
        completed = subprocess.run(["bash", "-c", definition["command"]], cwd=root, env=environment, capture_output=True, check=False)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        receipt = {
            "schema_version": "2.1.0", "artifact_type": "phase2_1_external_command_receipt", "release_id": RELEASE_ID,
            "command_id": definition["id"], "order": definition["order"], "command": definition["command"],
            "working_directory_scope": definition["working_directory_scope"], "working_directory": root.resolve().as_posix(),
            "offline_policy": definition["offline_policy"], "expected_status": definition["expected_status"],
            "expected_exit_code": definition["expected_exit_code"],
            "started_at_utc": external_started, "finished_at_utc": now(), "exit_code": completed.returncode,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "terminal": "PASS" if completed.returncode == 0 else "FAIL",
            "stdout_summary": stdout[-4000:], "stderr_summary": stderr[-4000:],
            "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(), "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
            "executed": True, "network_access": False,
            "input_identity": receipt_input_identity(destination),
        }
        validate("external_command_receipt", receipt)
        write_new_json(destination / "logs" / f"external-{definition['order']:02d}.json", receipt)
        receipts.append(receipt)
    return receipts


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="python -m lottery_research.phase2_1")
    value.add_argument("--project-root", type=Path, default=project_root())
    commands = value.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--wheelhouse", type=Path, required=True)
    prepare.add_argument("--task-input-dir", type=Path, required=True)
    prepare.add_argument("--corpus-root", type=Path, required=True)
    for name in ("readiness", "verify-readiness", "gates", "method-review", "qualification", "audit", "power", "replay", "replay-review", "e2e", "logs", "negative-suite", "manifest", "accept", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", type=Path)
        if name in ("power", "replay", "replay-review"):
            command.add_argument("--lfs-root", type=Path, required=True)
        if name == "e2e":
            command.add_argument("--staging-bundle", type=Path)
        if name == "verify":
            command.add_argument("--scope", required=True, choices=("readiness", "preregistration", "manifest", "final"))
    return value


FORMAL_COMMANDS = (
    "prepare", "readiness", "gates", "method-review", "qualification", "audit",
    "power", "replay", "replay-review", "e2e", "logs", "negative-suite",
)


def write_command_receipt(
    destination: Path,
    *,
    command: str,
    argv: Sequence[str],
    started_at_utc: str,
    terminal: str,
    exit_code: int,
    stdout: bytes,
    stderr: bytes,
    working_directory: str,
) -> dict[str, object]:
    receipt = {
        "schema_version": "2.1.0", "artifact_type": "phase2_1_command_record", "release_id": RELEASE_ID,
        "command": command, "argv": list(argv), "status": "PASS" if exit_code == 0 else "FAIL",
        "terminal": terminal, "exit_code": exit_code, "started_at_utc": started_at_utc, "finished_at_utc": now(),
        "working_directory": working_directory, "executed": True, "network_access": False,
        "stdout_summary": stdout.decode("utf-8", errors="replace")[-4000:],
        "stderr_summary": stderr.decode("utf-8", errors="replace")[-4000:],
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(), "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "input_identity": receipt_input_identity(destination),
    }
    validate("command_receipt", receipt)
    order = FORMAL_COMMANDS.index(command) + 1
    write_new_json(destination / "logs" / f"{order:02d}-{command}.json", receipt)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    command = "unknown"
    started_at = now()
    destination: Path | None = None
    args_list = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = parser().parse_args(argv)
        command = args.command
        root = args.project_root.resolve()
        destination = (getattr(args, "bundle", None) or bundle_path(root)).resolve()
        if command == "prepare":
            result = prepare_release(root, args.wheelhouse.resolve(), args.task_input_dir.resolve(), args.corpus_root.resolve())
        elif command == "readiness":
            result = validate_readiness(root, destination)
        elif command == "verify-readiness":
            result = verify_readiness_read_only(root, destination)
        elif command == "gates":
            result = freeze_g0_g1(root, destination)
        elif command == "method-review":
            result = independent_method_review(root, destination)
        elif command == "qualification":
            result = qualify(destination)
        elif command == "audit":
            result = historical_audit(destination, root=root)
        elif command == "power":
            result = power(destination, root=root, lfs_root=args.lfs_root.resolve())
        elif command == "replay":
            result = replay(destination, root=root, lfs_root=args.lfs_root.resolve())
        elif command == "replay-review":
            result = independent_replay_review(destination, root=root, lfs_root=args.lfs_root.resolve())
        elif command == "e2e":
            staging = args.staging_bundle.resolve() if args.staging_bundle else None
            result = run_e2e(destination, root=root, staging_bundle=staging)
        elif command == "logs":
            external = execute_external_commands(root, destination)
            records = [load_json(path) for path in sorted((destination / "logs").glob("[0-9][0-9]-*.json"))]
            passed = all(row["executed"] and row["exit_code"] == 0 for row in external)
            result = {
                "schema_version": "2.1.0", "artifact_type": "phase2_1_run_log_summary", "release_id": RELEASE_ID,
                "status": "PASS" if passed else "FAIL", "formal_commands": records,
                "external_verification_commands": external,
                "formal_network_access": False,
                "input_identity": load_json(destination / "readiness/readiness.json")["input_identity"],
            }
            if passed:
                validate("run_log_summary", result)
            write_new_json(destination / "logs/run-summary.json", result)
        elif command == "negative-suite":
            result = run_final_validation_negative_suite(root, destination)
        elif command == "manifest":
            result = build_evidence_manifest(destination)
        elif command == "accept":
            result = accept(root, destination)
        else:
            if args.scope == "readiness":
                result = verify_readiness_read_only(root, destination)
            elif args.scope == "preregistration":
                result = validate_preregistration(root, destination)
            elif args.scope == "manifest":
                closure = verify_evidence_manifest(destination, load_json(destination / "acceptance/manifest.json"))
                result = {"status": "PASS", "artifact_type": "phase2_1_verification", "closure": closure}
            else:
                result = validate_final_bundle(root, destination)
        terminal = result.get("status", "PASS")
        receipt_destination = destination
        if command == "e2e" and "_staging_bundle" in result:
            receipt_destination = Path(result.pop("_staging_bundle"))
        result_code = 0 if terminal in ("PASS", "READY", "frozen") else 2
        output = {"release_id": RELEASE_ID, "command": command, "terminal": terminal, "exit_code": result_code, "artifact_type": result.get("artifact_type")}
        stdout = canonical_json_bytes(output)
        if command in FORMAL_COMMANDS:
            write_command_receipt(receipt_destination, command=command, argv=args_list, started_at_utc=started_at, terminal=terminal, exit_code=result_code, stdout=stdout, stderr=b"", working_directory=os.getcwd())
        sys.stdout.buffer.write(stdout)
        return result_code
    except FileExistsError as exc:
        code, terminal, error = 4, "INVALID_CONTRACT", str(exc)
    except (ValueError, KeyError) as exc:
        code, terminal, error = 5, "EVIDENCE_MISMATCH", str(exc)
    except (OSError, RuntimeError) as exc:
        code, terminal, error = 3, "ENVIRONMENT_FAILURE", str(exc)
    output = {"release_id": RELEASE_ID, "command": command, "terminal": terminal, "exit_code": code, "error": error}
    stdout = canonical_json_bytes(output)
    if destination is not None and command in FORMAL_COMMANDS and (destination / "logs").is_dir():
        write_command_receipt(destination, command=command, argv=args_list, started_at_utc=started_at, terminal=terminal, exit_code=code, stdout=stdout, stderr=b"", working_directory=os.getcwd())
    sys.stdout.buffer.write(stdout)
    return code
