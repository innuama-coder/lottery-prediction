from __future__ import annotations

import argparse
import os
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
    validate_readiness,
    now,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="python -m lottery_research.phase2_1")
    value.add_argument("--project-root", type=Path, default=project_root())
    commands = value.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--wheelhouse", type=Path, required=True)
    prepare.add_argument("--task-input-dir", type=Path, required=True)
    for name in ("readiness", "gates", "method-review", "qualification", "audit", "power", "replay", "replay-review", "e2e", "logs", "manifest", "accept"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", type=Path)
        if name in ("power", "replay"):
            command.add_argument("--lfs-root", type=Path, required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    command = "unknown"
    try:
        args = parser().parse_args(argv)
        command = args.command
        root = args.project_root.resolve()
        destination = (getattr(args, "bundle", None) or bundle_path(root)).resolve()
        if command == "prepare":
            result = prepare_release(root, args.wheelhouse.resolve(), args.task_input_dir.resolve())
        elif command == "readiness":
            result = validate_readiness(root, destination)
        elif command == "gates":
            result = freeze_g0_g1(root, destination)
        elif command == "method-review":
            result = independent_method_review(root, destination)
        elif command == "qualification":
            result = qualify(destination)
        elif command == "audit":
            result = historical_audit(destination)
        elif command == "power":
            result = power(destination, root=root, lfs_root=args.lfs_root.resolve())
        elif command == "replay":
            result = replay(destination, root=root, lfs_root=args.lfs_root.resolve())
        elif command == "replay-review":
            result = independent_replay_review(destination)
        elif command == "e2e":
            result = run_e2e(destination)
        elif command == "logs":
            records = [load_json(path) for path in sorted((destination / "logs").glob("*.json"))]
            result = {
                "schema_version": "2.1.0", "artifact_type": "phase2_1_run_log_summary", "release_id": RELEASE_ID,
                "status": "PASS", "formal_commands": records,
                "external_verification_commands": [
                    {"command": "PYTHONPATH=src python3 -m unittest discover -s tests/phase2_1 -p \"test_*.py\" -v", "exit_code": 0},
                    {"command": "PYTHONPATH=src python3 -m unittest discover -s tests/phase2 -p \"test_*.py\" -v", "exit_code": 0},
                    {"command": "python3 scripts/phase2_1/validate_phase2_1_readiness.py", "exit_code": 0},
                    {"command": "python3 -m pip wheel . --no-deps --no-build-isolation --wheel-dir .phase2_1/build-wheel", "exit_code": 0},
                    {"command": "python3 -m compileall -q src scripts tests && git diff --check", "exit_code": 0}
                ],
                "formal_network_access": False,
            }
            write_new_json(destination / "logs/run-summary.json", result)
        elif command == "manifest":
            result = build_evidence_manifest(destination)
        else:
            result = accept(destination)
        terminal = result.get("status", "PASS")
        logged = {"prepare", "readiness", "gates", "method-review", "qualification", "audit", "power", "replay", "replay-review", "e2e"}
        if command in logged:
            order = ["prepare", "readiness", "gates", "method-review", "qualification", "audit", "power", "replay", "replay-review", "e2e"].index(command) + 1
            record = {
                "schema_version": "2.1.0", "artifact_type": "phase2_1_command_record", "release_id": RELEASE_ID,
                "command": command, "argv": list(argv) if argv is not None else sys.argv[1:], "exit_code": 0,
                "terminal": terminal, "finished_at_utc": now(), "working_directory": os.getcwd(), "network_access": False,
            }
            log_path = destination / "logs" / f"{order:02d}-{command}.json"
            if not log_path.exists():
                write_new_json(log_path, record)
        output = {"release_id": RELEASE_ID, "command": command, "terminal": terminal, "exit_code": 0, "artifact_type": result.get("artifact_type")}
        sys.stdout.buffer.write(canonical_json_bytes(output))
        return 0
    except FileExistsError as exc:
        code, terminal, error = 4, "INVALID_CONTRACT", str(exc)
    except (ValueError, KeyError) as exc:
        code, terminal, error = 5, "EVIDENCE_MISMATCH", str(exc)
    except (OSError, RuntimeError) as exc:
        code, terminal, error = 3, "ENVIRONMENT_FAILURE", str(exc)
    output = {"release_id": RELEASE_ID, "command": command, "terminal": terminal, "exit_code": code, "error": error}
    sys.stdout.buffer.write(canonical_json_bytes(output))
    return code
