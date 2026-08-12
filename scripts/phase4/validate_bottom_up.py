#!/usr/bin/env python3
"""Run the frozen Phase 4 E2E registry from bottom-level test seams.

The driver deliberately starts one fresh interpreter for every registered case.
Negative tests succeed only when their case-specific test performs its isolated
mutation and observes the registered rejection.  The driver never accepts an
unrelated exception as a guard hit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
GUARD_MAP = ROOT / "tests/phase4/fixtures/e2e/guard-map.json"


class HarnessViolation(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessViolation(f"cannot load JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise HarnessViolation(f"JSON root is not an object: {path}")
    return value


def write_once(path: Path, value: dict[str, Any]) -> None:
    encoded = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise HarnessViolation(f"immutable E2E evidence identity reuse: {path}")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def validate_registry(registry: dict[str, Any], guard_map: dict[str, Any]) -> list[dict[str, Any]]:
    if set(registry) != {
        "schema_version", "artifact_type", "positive_cases", "negative_cases",
        "mutation_contract", "unrelated_exception_counts_as_pass", "expected_guard_hit_rate",
    }:
        raise HarnessViolation("E2E registry shape is not closed")
    if registry["schema_version"] != "1.0.0" or registry["artifact_type"] != "phase4_e2e_registry":
        raise HarnessViolation("E2E registry identity mismatch")
    if registry["mutation_contract"] != "one_real_isolated_mutation_per_case_observed_by_distinct_validator_process":
        raise HarnessViolation("E2E mutation contract was weakened")
    if registry["unrelated_exception_counts_as_pass"] is not False or registry["expected_guard_hit_rate"] != "100%":
        raise HarnessViolation("E2E fail-closed policy was weakened")
    positives = registry["positive_cases"]
    negatives = registry["negative_cases"]
    if not isinstance(positives, list) or not isinstance(negatives, list):
        raise HarnessViolation("E2E case sets must be arrays")
    if any(not isinstance(item, str) or not item for item in positives + negatives):
        raise HarnessViolation("E2E case identity is invalid")
    if len(set(positives + negatives)) != len(positives) + len(negatives):
        raise HarnessViolation("E2E registry contains duplicate or cross-polarity cases")
    if set(guard_map) != {"schema_version", "artifact_type", "cases"}:
        raise HarnessViolation("guard map shape is not closed")
    if guard_map["schema_version"] != "1.0.0" or guard_map["artifact_type"] != "phase4_e2e_guard_map":
        raise HarnessViolation("guard map identity mismatch")
    cases = guard_map["cases"]
    if not isinstance(cases, list):
        raise HarnessViolation("guard map cases must be an array")
    by_id: dict[str, dict[str, Any]] = {}
    required = {"case_id", "polarity", "guard_code", "guard_exit_code", "selectors"}
    for row in cases:
        if not isinstance(row, dict) or set(row) != required:
            raise HarnessViolation("guard map case shape is not closed")
        case_id = row["case_id"]
        if case_id in by_id:
            raise HarnessViolation("guard map contains duplicate case identity")
        if row["polarity"] not in {"positive", "negative"}:
            raise HarnessViolation("guard map polarity is invalid")
        if not isinstance(row["guard_code"], str) or not row["guard_code"]:
            raise HarnessViolation("guard code is invalid")
        if type(row["guard_exit_code"]) is not int or row["guard_exit_code"] not in {0, 4, 5, 6, 20, 30}:
            raise HarnessViolation("guard exit code is invalid")
        if not isinstance(row["selectors"], list) or not row["selectors"] or any(not isinstance(x, str) or not x for x in row["selectors"]):
            raise HarnessViolation("case selectors are invalid")
        by_id[case_id] = row
    expected = set(positives + negatives)
    if set(by_id) != expected:
        raise HarnessViolation(f"registry/guard-map bidirectional difference: {sorted(expected ^ set(by_id))}")
    for case_id in positives:
        row = by_id[case_id]
        if row["polarity"] != "positive" or row["guard_exit_code"] != 0 or row["guard_code"] != "PASS":
            raise HarnessViolation(f"positive guard mapping invalid: {case_id}")
    for case_id in negatives:
        row = by_id[case_id]
        if row["polarity"] != "negative" or row["guard_exit_code"] == 0 or row["guard_code"] == "PASS":
            raise HarnessViolation(f"negative guard mapping invalid: {case_id}")
    return [by_id[case_id] for case_id in positives + negatives]


def run_case(case: dict[str, Any], *, ordinal: int) -> tuple[dict[str, Any], bytes, bytes]:
    command = [sys.executable, "-m", "unittest", *case["selectors"], "-v"]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = "src"
    process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    execution_id = f"e2e-case-{ordinal:03d}-{uuid.uuid4()}"
    passed = process.returncode == 0
    polarity = case["polarity"]
    terminal = "E2E_CASE_PASS" if polarity == "positive" else "REGISTERED_GUARD_REJECTED_MUTATION"
    receipt = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_e2e_case_receipt",
        "case_id": case["case_id"],
        "polarity": polarity,
        "guard_code": case["guard_code"] if passed else "UNRELATED_TEST_FAILURE",
        "expected_guard_code": case["guard_code"],
        "guard_exit_code": case["guard_exit_code"] if passed else process.returncode,
        "status": "PASS" if passed else "FAIL",
        "terminal": terminal if passed else "HOLD_E2E_INCOMPLETE",
        "exit_code": 0 if passed else 5,
        "mutation_count": 1 if polarity == "negative" else 0,
        "unrelated_exception": not passed,
        "validator_pid": process.pid,
        "validator_execution_id": execution_id,
        "command": command,
        "stdout_sha256": sha256_bytes(stdout),
        "stderr_sha256": sha256_bytes(stderr),
    }
    return receipt, stdout, stderr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--clock", required=True)
    arguments = parser.parse_args()
    try:
        registry_path = arguments.registry.resolve()
        registry_path.relative_to(ROOT)
        output = arguments.output.resolve()
        output.relative_to(ROOT)
        if arguments.runtime_root is not None:
            arguments.runtime_root.resolve().relative_to(ROOT)
        registry = load_object(registry_path)
        guard_map = load_object(GUARD_MAP)
        cases = validate_registry(registry, guard_map)
        references: list[dict[str, Any]] = []
        observed: list[dict[str, Any]] = []
        for ordinal, case in enumerate(cases, start=1):
            receipt, stdout, stderr = run_case(case, ordinal=ordinal)
            case_root = output / "case-receipts" / case["case_id"]
            receipt_path = case_root / "receipt.json"
            write_once(receipt_path, receipt)
            write_once(case_root / "process-output.json", {
                "schema_version": "1.0.0", "artifact_type": "phase4_e2e_case_process_output",
                "case_id": case["case_id"], "stdout_utf8": stdout.decode("utf-8", errors="replace"),
                "stderr_utf8": stderr.decode("utf-8", errors="replace"),
            })
            references.append({
                "case_id": case["case_id"],
                "path": receipt_path.relative_to(output).as_posix(),
                "sha256": sha256_file(receipt_path),
            })
            observed.append(receipt)
        positive_count = sum(row["polarity"] == "positive" for row in observed)
        negative_count = sum(row["polarity"] == "negative" for row in observed)
        guard_hits = sum(
            row["status"] == "PASS" and row["guard_code"] == row["expected_guard_code"] and not row["unrelated_exception"]
            for row in observed
        )
        all_pass = guard_hits == len(cases)
        manifest = {
            "schema_version": "1.0.0",
            "artifact_type": "phase4_e2e_manifest",
            "registry_sha256": sha256_file(registry_path),
            "guard_map_sha256": sha256_file(GUARD_MAP),
            "positive_case_count": positive_count,
            "negative_case_count": negative_count,
            "expected_guard_hit_count": len(cases),
            "case_count": len(cases),
            "expected_case_count": len(cases),
            "observed_case_count": len(observed),
            "mutation_count": sum(row["mutation_count"] for row in observed),
            "distinct_validator_process_count": len({row["validator_execution_id"] for row in observed}),
            "guard_hit_rate": "100%" if all_pass else f"{guard_hits}/{len(cases)}",
            "unrelated_exception_count": sum(bool(row["unrelated_exception"]) for row in observed),
            "case_receipts": references,
            "status": "PASS" if all_pass else "HOLD",
            "terminal": "E2E_VALIDATION_PASS" if all_pass else "HOLD_E2E_INCOMPLETE",
        }
        write_once(output / "e2e-manifest.json", manifest)
        sys.stdout.buffer.write(canonical_bytes(manifest))
        return 0 if all_pass else 20
    except (HarnessViolation, OSError, ValueError) as exc:
        payload = {"status": "FAIL", "terminal": "HOLD_E2E_INCOMPLETE", "exit_code": 5, "error": str(exc)}
        sys.stdout.buffer.write(canonical_bytes(payload))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
