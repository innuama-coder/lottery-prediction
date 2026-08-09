from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .prerun_contract import validate_prerun_contract
from .registry import load_and_validate_registries
from .serialization import canonical_json_bytes
from .formal import (
    accept_formal,
    evaluate_formal,
    handoff_formal,
    implementation_validate,
    new_directory,
    readiness,
    replay_formal,
    run_formal,
    run_qualification,
    validate_bottom_up,
    validate_existing_readiness,
    verify_e2e_formal,
)
from .workflow import HOLD_TERMINAL, project_root, validate_frozen_inputs
from .work_items import create_command_work_item_receipt, load_actor_assignment


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="python3 -m lottery_research.phase3")
    value.add_argument("--project-root", type=Path, default=project_root())
    commands = value.add_subparsers(dest="command", required=True)
    for name in ("validate", "qualify", "run", "evaluate", "readiness", "replay", "verify-e2e", "accept"):
        command = commands.add_parser(name)
        command.add_argument("--identity", required=True)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--actor-assignments", required=True, type=Path)
        command.add_argument("--emit-work-item-receipt", action="store_true")
        command.add_argument("--upstream-receipt", type=Path)
        command.add_argument("--upstream-actor-assignments", type=Path)
        command.add_argument("--work-item-receipt", type=Path)
        if name in ("validate", "qualify", "readiness"):
            command.add_argument("--prep-root", type=Path)
        if name == "qualify":
            command.add_argument("--stop-after-uniform", action="store_true")
            command.add_argument("--resume", action="store_true")
        if name == "run":
            command.add_argument("--resume", action="store_true")
            command.add_argument("--stop-after-targets", type=int, default=None)
        if name in ("validate", "run", "evaluate", "readiness", "replay", "verify-e2e", "accept"):
            command.add_argument("--release-root", type=Path)
        if name == "validate":
            command.add_argument("--scope", choices=("contract", "inputs", "implementation", "registries", "readiness", "final", "handoff"), default="contract")
    return value


def _exit_code(result: dict[str, object]) -> int:
    terminal = result.get("terminal")
    if terminal == HOLD_TERMINAL or result.get("status") == "HOLD":
        return 20
    if terminal in ("EVIDENCE_MISMATCH", "E2E_MISMATCH"):
        return 5
    return 0 if result.get("status") in ("PASS", "READY") else 2


def main(argv: Sequence[str] | None = None) -> int:
    command = "unknown"
    started_at_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        args = parser().parse_args(argv)
        command, root, output = args.command, args.project_root.resolve(), args.output.resolve()
        actor_assignment, _ = load_actor_assignment(root, args.actor_assignments.resolve())
        formal_command = command in ("run", "evaluate", "readiness", "replay", "verify-e2e", "accept") or (command == "validate" and args.scope in ("readiness", "final", "handoff"))
        if formal_command and actor_assignment["assignment_stage"] != "formal_before_W07":
            raise ValueError("formal command requires formal_before_W07 actor assignments")
        if command == "validate":
            if args.scope in ("contract", "inputs", "registries"):
                if args.scope == "registries" and args.prep_root is None:
                    raise ValueError("registry validation requires --prep-root")
                output.mkdir(parents=True, exist_ok=False)
                if args.scope == "contract":
                    result = validate_prerun_contract(root)
                elif args.scope == "inputs":
                    result = {"status": "PASS", "terminal": "PASS", "identity": args.identity, **validate_frozen_inputs(root)}
                else:
                    models, features = load_and_validate_registries(root)
                    result = {"status": "PASS", "terminal": "PASS", "identity": args.identity,
                              "model_count": len(models["models"]), "feature_count": len(features["features"])}
                (output / f"{args.scope}-validation.json").write_bytes(canonical_json_bytes(result))
            elif args.scope == "implementation":
                if args.prep_root is None:
                    raise ValueError("implementation validation requires --prep-root")
                result = implementation_validate(root, output, args.identity, args.prep_root.resolve())
            elif args.scope == "readiness":
                if args.prep_root is None or args.release_root is None:
                    raise ValueError("readiness validation requires --prep-root and --release-root")
                result = validate_existing_readiness(root, output, args.identity, args.release_root.resolve())
            elif args.scope == "handoff":
                if args.release_root is None:
                    raise ValueError("handoff validation requires --release-root")
                result = handoff_formal(root, output, args.identity, args.release_root.resolve(), args.actor_assignments.resolve())
            else:
                if args.release_root is None:
                    raise ValueError("final validation requires --release-root")
                destination = new_directory(output, args.identity)
                checked = validate_bottom_up(root, args.release_root.resolve(), args.actor_assignments.resolve())
                result = {"schema_version": "3.0.0", "artifact_type": "phase3_final_validation", "identity": args.identity, "status": "PASS", "terminal": "PASS", **checked}
                (destination / "final-validation.json").write_bytes(canonical_json_bytes(result))
        elif command == "qualify":
            if args.prep_root is None:
                raise ValueError("qualify requires --prep-root")
            if args.stop_after_uniform and args.resume:
                raise ValueError("qualify cannot stop and resume in the same invocation")
            result = run_qualification(root, output, args.identity, args.prep_root.resolve(), args.actor_assignments.resolve(), stop_after_uniform=args.stop_after_uniform, resume=args.resume)
        elif command == "run":
            if args.release_root is None:
                raise ValueError("run requires --release-root")
            if args.resume and args.stop_after_targets is not None:
                raise ValueError("run cannot resume and stop after targets in the same invocation")
            result = run_formal(root, output, args.identity, args.release_root.resolve(), resume=args.resume, stop_after_targets=args.stop_after_targets)
        elif command == "evaluate":
            if args.release_root is None:
                raise ValueError("evaluate requires --release-root")
            result = evaluate_formal(root, output, args.identity, args.release_root.resolve())
        elif command == "readiness":
            if args.prep_root is None or args.release_root is None:
                raise ValueError("readiness requires --prep-root and --release-root")
            result = readiness(root, output, args.identity, args.prep_root.resolve(), args.release_root.resolve(), args.actor_assignments.resolve())
        elif command == "replay":
            if args.release_root is None:
                raise ValueError("replay requires --release-root")
            result = replay_formal(root, output, args.identity, args.release_root.resolve(), args.actor_assignments.resolve())
        elif command == "verify-e2e":
            if args.release_root is None:
                raise ValueError("verify-e2e requires --release-root")
            result = verify_e2e_formal(root, output, args.identity, args.release_root.resolve(), args.actor_assignments.resolve())
        else:
            if args.release_root is None:
                raise ValueError("accept requires --release-root")
            result = accept_formal(root, output, args.identity, args.release_root.resolve(), args.actor_assignments.resolve())
        code = _exit_code(result)
        if args.emit_work_item_receipt:
            work_item = {
                ("validate", "implementation"): "W04", ("validate", "registries"): "W05",
                ("qualify", None): "W06", ("readiness", None): "W07", ("run", None): "W08",
                ("evaluate", None): "W09", ("replay", None): "W10", ("verify-e2e", None): "W11",
                ("accept", None): "W12", ("validate", "handoff"): "W13",
            }.get((command, getattr(args, "scope", None)))
            if work_item is None or args.upstream_receipt is None or args.work_item_receipt is None:
                raise ValueError("work item receipt emission requires a mapped command, --upstream-receipt, and --work-item-receipt")
            create_command_work_item_receipt(
                root, work_item=work_item, identity=args.identity, actor_path=args.actor_assignments.resolve(),
                upstream_actor_path=(args.upstream_actor_assignments or args.actor_assignments).resolve(),
                upstream_receipt=args.upstream_receipt.resolve(), command_output=output,
                receipt_output=args.work_item_receipt.resolve(), command=list(argv if argv is not None else sys.argv[1:]),
                started_at_utc=started_at_utc, ended_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                process_exit_code=code, status=str(result.get("status")), terminal=str(result.get("terminal", result.get("status"))),
            )
        output_value = {"artifact_type": result.get("artifact_type", "phase3_command_receipt"), "command": command,
                        "identity": args.identity, "status": result.get("status"),
                        "terminal": result.get("terminal", result.get("status")), "exit_code": code, "output": output.as_posix()}
    except FileExistsError as exc:
        code, output_value = 4, {"command": command, "status": "FAIL", "terminal": "INVALID_IDENTITY_REUSE", "exit_code": 4, "error": str(exc)}
    except (ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        code, output_value = 5, {"command": command, "status": "FAIL", "terminal": "EVIDENCE_MISMATCH", "exit_code": 5, "error": str(exc)}
    except OSError as exc:
        code, output_value = 3, {"command": command, "status": "FAIL", "terminal": "ENVIRONMENT_FAILURE", "exit_code": 3, "error": str(exc)}
    sys.stdout.buffer.write(canonical_json_bytes(output_value))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
