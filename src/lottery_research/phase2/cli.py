from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from .errors import HOLD, INVALID_CONTRACT, PASS, REJECTED, Hold, InvalidContract, Phase2Error
from .input_validation import sha256, validate_formal_inputs
from .schema import load_json, validate_payload
from .serialization import canonical_json_bytes
from .formal_workflows import historical_audit, power_envelope, qualify_harness
from .workflows import final_acceptance, replay_evidence

COMMANDS = ("validate-input", "qualify-harness", "audit", "power", "replay", "accept")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InvalidContract(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m lottery_research.phase2")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-input")
    validate.add_argument("--contract", required=True, type=Path)
    validate.add_argument("--input-rule-contract", required=True, type=Path)
    validate.add_argument("--input-manifest", required=True, type=Path)
    validate.add_argument("--preregistration", required=True, type=Path)
    validate.add_argument("--reviewer-assignment", required=True, type=Path)
    validate.add_argument("--schema-root", type=Path)

    qualify = commands.add_parser("qualify-harness")
    qualify.add_argument("--contract", required=True, type=Path)
    qualify.add_argument("--input-manifest", required=True, type=Path)
    qualify.add_argument("--preregistration", required=True, type=Path)
    qualify.add_argument("--output", required=True, type=Path)

    audit = commands.add_parser("audit")
    audit.add_argument("--contract", required=True, type=Path)
    audit.add_argument("--input-manifest", required=True, type=Path)
    audit.add_argument("--preregistration", required=True, type=Path)
    audit.add_argument("--output", required=True, type=Path)

    power = commands.add_parser("power")
    power.add_argument("--contract", required=True, type=Path)
    power.add_argument("--input-manifest", required=True, type=Path)
    power.add_argument("--preregistration", required=True, type=Path)
    power.add_argument("--output", required=True, type=Path)
    power.add_argument("--checkpoint-root", type=Path)
    power.add_argument("--interrupt-after-batches", type=int)

    replay = commands.add_parser("replay")
    replay.add_argument("--contract", required=True, type=Path)
    replay.add_argument("--evidence-manifest", required=True, type=Path)
    replay.add_argument("--output", required=True, type=Path)
    replay.add_argument("--seed-set", required=True)

    accept = commands.add_parser("accept")
    accept.add_argument("--contract", required=True, type=Path)
    accept.add_argument("--evidence-manifest", required=True, type=Path)
    accept.add_argument("--output", required=True, type=Path)
    return parser


def _result(command: str, terminal: str, exit_code: int, *, checks: dict[str, Any] | None = None, errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase2_cli_result",
        "command": command,
        "terminal": terminal,
        "exit_code": exit_code,
        "checks": checks or {},
        "errors": errors or [],
        "output_written": False,
    }


def _require_entry_gate(namespace: argparse.Namespace) -> Path:
    contract = load_json(namespace.contract)
    if contract.get("contract_version") != "1.3.0":
        raise InvalidContract("unsupported Phase 2 acceptance contract")
    root = namespace.contract.resolve().parents[2]
    required = {
        "document": root / "docs/research/phase-2-input-rule-and-time-contract.md",
        "reviewers": root / "artifacts/phase-2/contracts/reviewer-assignment.json",
        "method_review": root / "artifacts/phase-2/reviews/method-review.json",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise Hold(f"G0/G1 prerequisites are not complete: {', '.join(missing)}")
    gate_path = root / "artifacts/phase-2/gates/g0-g1.json"
    if gate_path.is_file():
        gate = load_json(gate_path)
        if gate.get("status") != "PASS" or gate.get("gates") != ["G0", "G1"]:
            raise Hold("frozen G0/G1 evidence is not PASS")
        for item in gate["frozen_input_identities"]:
            target = root / item["path"]
            if not target.is_file() or sha256(target) != item["sha256"]:
                raise InvalidContract(f"G0/G1 frozen identity mismatch: {item['path']}")
        return root
    checks = validate_formal_inputs(
        contract_path=namespace.contract,
        input_rule_contract_path=required["document"],
        input_manifest_path=namespace.input_manifest,
        preregistration_path=namespace.preregistration,
        reviewer_assignment_path=required["reviewers"],
    )
    review = load_json(required["method_review"])
    validate_payload("method_review", review)
    if review.get("status") != "PASS" or review.get("blocking_findings") not in (0, []):
        raise Hold("independent method review has not passed")
    frozen_paths = [namespace.contract, required["document"], namespace.input_manifest, namespace.preregistration, required["reviewers"], required["method_review"], root / "artifacts/phase-2/contracts/pre-g0-contract-amendment.json", root / "artifacts/phase-2/contracts/environment-lock.json"]
    gate_payload = {"schema_version": "1.0.0", "artifact_type": "phase2_gate_evidence", "status": "PASS", "gates": ["G0", "G1"], "checks": checks, "frozen_input_identities": [{"path": path.resolve().relative_to(root).as_posix(), "sha256": sha256(path)} for path in frozen_paths]}
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_bytes(canonical_json_bytes(gate_payload))
    return root


def _require_qualification(root: Path) -> None:
    path = root / "artifacts/phase-2/qualification/harness-qualification.json"
    if not path.is_file() or load_json(path).get("status") != "PASS":
        raise Hold("G2 harness qualification has not passed")


def _require_replay_inputs(namespace: argparse.Namespace) -> Path:
    root = namespace.contract.resolve().parents[2]
    required = [
        root / "artifacts/phase-2/results/historical-audit.json",
        root / "artifacts/phase-2/results/power-envelope.json",
        root / "artifacts/phase-2/qualification/harness-qualification.json",
    ]
    if any(not path.is_file() for path in required):
        raise Hold("P2-05 requires completed D2-07, D2-08 and D2-09")
    if any(load_json(path).get("status") != "PASS" for path in required):
        raise Hold("P2-05 requires G2, G3 and G4 PASS")
    return root


def main(argv: Sequence[str] | None = None) -> int:
    command = "unknown"
    try:
        namespace = build_parser().parse_args(list(argv) if argv is not None else None)
        command = namespace.command
        if command == "validate-input":
            project_root = namespace.contract.resolve().parents[2]
            gate_already_frozen = (project_root / "artifacts/phase-2/gates/g0-g1.json").is_file()
            checks = validate_formal_inputs(
                contract_path=namespace.contract,
                input_rule_contract_path=namespace.input_rule_contract,
                input_manifest_path=namespace.input_manifest,
                preregistration_path=namespace.preregistration,
                reviewer_assignment_path=namespace.reviewer_assignment,
                schema_root=namespace.schema_root,
                require_no_formal_results=not gate_already_frozen,
            )
            code = PASS
            result = _result(command, "PASS", code, checks=checks)
        elif command == "qualify-harness":
            _require_entry_gate(namespace)
            payload = qualify_harness(namespace.contract, namespace.input_manifest, namespace.preregistration, namespace.output)
            code = PASS if payload["status"] == "PASS" else REJECTED
            result = _result(command, "PASS" if code == PASS else "REJECTED", code, checks=payload.get("metrics"))
            result["output_written"] = True
        elif command == "audit":
            root = _require_entry_gate(namespace)
            _require_qualification(root)
            payload = historical_audit(namespace.contract, namespace.input_manifest, namespace.preregistration, namespace.output)
            code = PASS if payload["status"] == "PASS" else REJECTED
            result = _result(command, "PASS" if code == PASS else "REJECTED", code, checks=payload.get("metrics"))
            result["output_written"] = True
        elif command == "power":
            root = _require_entry_gate(namespace)
            _require_qualification(root)
            payload = power_envelope(namespace.contract, namespace.input_manifest, namespace.preregistration, namespace.output, checkpoint_root=namespace.checkpoint_root, interrupt_after_batches=namespace.interrupt_after_batches)
            code = PASS if payload["status"] == "PASS" else REJECTED
            result = _result(command, "PASS" if code == PASS else "REJECTED", code, checks=payload.get("metrics"))
            result["output_written"] = True
        elif command == "replay":
            _require_replay_inputs(namespace)
            payload = replay_evidence(namespace.contract, namespace.evidence_manifest, namespace.output, namespace.seed_set)
            code = PASS if payload["status"] == "PASS" else HOLD
            result = _result(command, "PASS" if code == PASS else "HOLD", code, checks=payload.get("metrics"))
            result["output_written"] = True
        else:
            root = namespace.contract.resolve().parents[2]
            replay_review = root / "artifacts/phase-2/reviews/replay-review.json"
            if not replay_review.is_file() or load_json(replay_review).get("status") != "PASS":
                raise Hold("P2-06 requires G0 through G5 and an independent replay review")
            payload = final_acceptance(namespace.contract, namespace.evidence_manifest, namespace.output)
            code = PASS
            result = _result(command, "PASS", code, checks={"delivery_status": payload["delivery_status"], "signal_status": payload["signal_status"]})
            result["output_written"] = True
    except Phase2Error as exc:
        code = exc.exit_code
        result = _result(command, exc.terminal, code, errors=[str(exc)])
    except Exception as exc:
        code = INVALID_CONTRACT
        result = _result(command, "INVALID_CONTRACT", code, errors=[f"unexpected preflight error: {exc}"])
    sys.stdout.buffer.write(canonical_json_bytes(result))
    return code
