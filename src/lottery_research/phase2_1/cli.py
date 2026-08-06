from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from . import RELEASE_ID
from .serialization import canonical_json_bytes, load_json, write_new_json
from .workflow import (
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
    run_e2e,
    validate_final_bundle,
    validate_preregistration,
    validate_readiness,
    verify_evidence_manifest,
    now,
)


def execute_external_commands(root: Path, destination: Path, commands: Sequence[str]) -> list[dict[str, object]]:
    environment = os.environ.copy()
    environment["PATH"] = f"{root / '.phase2_1/venv/bin'}{os.pathsep}{environment.get('PATH', '')}"
    receipts = []
    for index, external_command in enumerate(commands, start=1):
        external_started = now()
        completed = subprocess.run(["bash", "-c", external_command], cwd=root, env=environment, capture_output=True, check=False)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        receipt = {
            "schema_version": "2.1.0", "artifact_type": "phase2_1_external_command_receipt", "release_id": RELEASE_ID,
            "command": external_command, "started_at_utc": external_started, "finished_at_utc": now(), "exit_code": completed.returncode,
            "stdout_summary": stdout[-4000:], "stderr_summary": stderr[-4000:],
            "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(), "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
            "executed": True, "network_access": False,
        }
        write_new_json(destination / "logs" / f"external-{index:02d}.json", receipt)
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
    for name in ("readiness", "gates", "method-review", "qualification", "audit", "power", "replay", "replay-review", "e2e", "logs", "manifest", "accept", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", type=Path)
        if name in ("power", "replay"):
            command.add_argument("--lfs-root", type=Path, required=True)
        if name == "verify":
            command.add_argument("--scope", required=True, choices=("readiness", "preregistration", "manifest", "final"))
    return value


def main(argv: Sequence[str] | None = None) -> int:
    command = "unknown"
    started_at = now()
    try:
        args = parser().parse_args(argv)
        command = args.command
        root = args.project_root.resolve()
        destination = (getattr(args, "bundle", None) or bundle_path(root)).resolve()
        if command == "prepare":
            result = prepare_release(root, args.wheelhouse.resolve(), args.task_input_dir.resolve(), args.corpus_root.resolve())
        elif command == "readiness":
            result = validate_readiness(root, destination)
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
            result = independent_replay_review(destination)
        elif command == "e2e":
            result = run_e2e(destination, root=root)
        elif command == "logs":
            commands_to_run = [
                "PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p \"test_*.py\" -v",
                "PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p \"test_*.py\" -v",
                "python3 scripts/phase2_1/validate_phase2_1_readiness.py",
                "python3 -m pip wheel . --no-deps --no-build-isolation --wheel-dir .phase2_1/build-wheel-i02",
                "python3 -m compileall -q src scripts tests && git diff --check",
            ]
            external = execute_external_commands(root, destination, commands_to_run)
            records = [load_json(path) for path in sorted((destination / "logs").glob("[0-9][0-9]-*.json"))]
            passed = all(row["executed"] and row["exit_code"] == 0 for row in external)
            result = {
                "schema_version": "2.1.0", "artifact_type": "phase2_1_run_log_summary", "release_id": RELEASE_ID,
                "status": "PASS" if passed else "FAIL", "formal_commands": records,
                "external_verification_commands": external,
                "formal_network_access": False,
            }
            write_new_json(destination / "logs/run-summary.json", result)
        elif command == "manifest":
            result = build_evidence_manifest(destination)
        elif command == "accept":
            result = accept(root, destination)
        else:
            if args.scope == "readiness":
                result = validate_readiness(root, destination)
            elif args.scope == "preregistration":
                result = validate_preregistration(root, destination)
            elif args.scope == "manifest":
                closure = verify_evidence_manifest(destination, load_json(destination / "acceptance/manifest.json"))
                result = {"status": "PASS", "artifact_type": "phase2_1_verification", "closure": closure}
            else:
                result = validate_final_bundle(root, destination)
        terminal = result.get("status", "PASS")
        logged = {"prepare", "readiness", "gates", "method-review", "qualification", "audit", "power", "replay", "replay-review", "e2e"}
        if command in logged:
            order = ["prepare", "readiness", "gates", "method-review", "qualification", "audit", "power", "replay", "replay-review", "e2e"].index(command) + 1
            record = {
                "schema_version": "2.1.0", "artifact_type": "phase2_1_command_record", "release_id": RELEASE_ID,
                "command": command, "argv": list(argv) if argv is not None else sys.argv[1:], "exit_code": 0,
                "terminal": terminal, "started_at_utc": started_at, "finished_at_utc": now(), "working_directory": os.getcwd(), "network_access": False,
            }
            log_path = destination / "logs" / f"{order:02d}-{command}.json"
            if not log_path.exists():
                write_new_json(log_path, record)
        result_code = 0 if terminal in ("PASS", "READY", "frozen") else 2
        output = {"release_id": RELEASE_ID, "command": command, "terminal": terminal, "exit_code": result_code, "artifact_type": result.get("artifact_type")}
        sys.stdout.buffer.write(canonical_json_bytes(output))
        return result_code
    except FileExistsError as exc:
        code, terminal, error = 4, "INVALID_CONTRACT", str(exc)
    except (ValueError, KeyError) as exc:
        code, terminal, error = 5, "EVIDENCE_MISMATCH", str(exc)
    except (OSError, RuntimeError) as exc:
        code, terminal, error = 3, "ENVIRONMENT_FAILURE", str(exc)
    output = {"release_id": RELEASE_ID, "command": command, "terminal": terminal, "exit_code": code, "error": error}
    sys.stdout.buffer.write(canonical_json_bytes(output))
    return code
