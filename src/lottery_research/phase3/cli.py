from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .prerun_contract import validate_prerun_contract
from .serialization import canonical_json_bytes
from .workflow import HOLD_TERMINAL, evaluate, final_validate, hold_formal_run, project_root, qualify, readiness, replay, validate_frozen_inputs, verify_e2e


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="python3 -m lottery_research.phase3")
    value.add_argument("--project-root", type=Path, default=project_root())
    commands = value.add_subparsers(dest="command", required=True)
    for name in ("validate", "qualify", "run", "evaluate", "readiness", "replay", "verify-e2e", "accept"):
        command = commands.add_parser(name)
        command.add_argument("--identity", required=True)
        command.add_argument("--output", required=True, type=Path)
        if name in ("evaluate", "replay"):
            command.add_argument("--qualification", required=True, type=Path)
        if name == "validate":
            command.add_argument("--scope", choices=("contract", "inputs", "readiness", "final"), default="contract")
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
    try:
        args = parser().parse_args(argv)
        command, root, output = args.command, args.project_root.resolve(), args.output.resolve()
        if command == "validate":
            if args.scope in ("contract", "inputs"):
                output.mkdir(parents=True, exist_ok=False)
                result = validate_prerun_contract(root) if args.scope == "contract" else {"status": "PASS", "terminal": "PASS", "identity": args.identity, **validate_frozen_inputs(root)}
                (output / f"{args.scope}-validation.json").write_bytes(canonical_json_bytes(result))
            elif args.scope == "readiness":
                result = readiness(root, output, args.identity)
            else:
                result = final_validate(root, output, args.identity)
        elif command == "qualify":
            result = qualify(root, output, args.identity)
        elif command == "run":
            result = hold_formal_run(root, output, args.identity)
        elif command == "evaluate":
            result = evaluate(root, output, args.identity, args.qualification.resolve())
        elif command == "readiness":
            result = readiness(root, output, args.identity)
        elif command == "replay":
            result = replay(root, output, args.identity, args.qualification.resolve())
        elif command == "verify-e2e":
            result = verify_e2e(root, output, args.identity)
        else:
            result = final_validate(root, output, args.identity)
        code = _exit_code(result)
        output_value = {"artifact_type": result.get("artifact_type", "phase3_command_receipt"), "command": command,
                        "identity": args.identity, "status": result.get("status"),
                        "terminal": result.get("terminal", result.get("status")), "exit_code": code, "output": output.as_posix()}
    except FileExistsError as exc:
        code, output_value = 4, {"command": command, "status": "FAIL", "terminal": "INVALID_IDENTITY_REUSE", "exit_code": 4, "error": str(exc)}
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        code, output_value = 5, {"command": command, "status": "FAIL", "terminal": "EVIDENCE_MISMATCH", "exit_code": 5, "error": str(exc)}
    except OSError as exc:
        code, output_value = 3, {"command": command, "status": "FAIL", "terminal": "ENVIRONMENT_FAILURE", "exit_code": 3, "error": str(exc)}
    sys.stdout.buffer.write(canonical_json_bytes(output_value))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
