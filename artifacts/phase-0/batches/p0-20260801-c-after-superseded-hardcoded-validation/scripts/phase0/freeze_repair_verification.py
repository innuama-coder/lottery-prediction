"""Freeze the C repair-layer verification contract without changing decision inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts/phase-0"
COMMAND = ARTIFACTS / "verification-command.json"
COMMAND_SIDECAR = ARTIFACTS / "verification-command.json.sha256"
FREEZE_ID = "P0-01-p0-20260801-c-repair"
EXPECTED_SCHEMA_COUNT = 32
EXPECTED_VERIFIER_COUNT = 23


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def validate_utc(value: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None:
        raise ValueError("--frozen-at-utc must be an RFC3339 UTC timestamp with a Z suffix")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_command(frozen_at_utc: str) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "scripts/phase0"))
    from phase0lib import schemas_manifest_sha256
    from verify_phase0 import (
        BOOTSTRAP_COMMAND,
        FINALIZE_COMMAND,
        FULL_VERIFY_COMMAND,
        REPLAY_COMMAND,
        VERIFIER_FILES,
    )

    if len(VERIFIER_FILES) != EXPECTED_VERIFIER_COUNT:
        raise ValueError(f"expected {EXPECTED_VERIFIER_COUNT} exact verifier files, got {len(VERIFIER_FILES)}")
    missing_tools = [relative for relative in sorted(VERIFIER_FILES) if not (ROOT / relative).is_file()]
    if missing_tools:
        raise ValueError(f"frozen verifier files are missing: {missing_tools}")

    schema_dir = ARTIFACTS / "schemas"
    schema_paths = sorted(schema_dir.glob("*.schema.json"), key=lambda path: path.name)
    if len(schema_paths) != EXPECTED_SCHEMA_COUNT:
        raise ValueError(f"expected {EXPECTED_SCHEMA_COUNT} exact schemas, got {len(schema_paths)}")

    command = json.loads(COMMAND.read_text(encoding="utf-8"))
    command.update({
        "freeze_id": FREEZE_ID,
        "frozen_at_utc": validate_utc(frozen_at_utc),
        "bootstrap_gate_command": BOOTSTRAP_COMMAND,
        "bootstrap_expected_exit_code": 0,
        "command": REPLAY_COMMAND,
        "full_replay_command": REPLAY_COMMAND,
        "replay_command": REPLAY_COMMAND,
        "finalize_command": FINALIZE_COMMAND,
        "full_verify_command": FULL_VERIFY_COMMAND,
        "working_directory": ".",
        "launcher_path": "scripts/phase0/p0_07_replay_launcher.ps1",
        "finalize_launcher_path": "scripts/phase0/p0_07_finalize_launcher.ps1",
        "finalize_launcher_sha256": sha256(ROOT / "scripts/phase0/p0_07_finalize_launcher.ps1"),
        "finalizer_path": "scripts/phase0/p0_07_finalize.py",
        "finalizer_sha256": sha256(ROOT / "scripts/phase0/p0_07_finalize.py"),
        "expected_exit_code": 0,
        "non_interactive": True,
        "network_required": False,
        "contract_sha256": sha256(ROOT / "docs/roadmap/phase-0-acceptance-contract.json"),
        "frozen_input_hashes": {
            "scope_freeze": sha256(ARTIFACTS / "scope-freeze.json"),
            "observation_plan": sha256(ARTIFACTS / "observation-plan.json"),
            "reviewer_assignment": sha256(ARTIFACTS / "reviewer-assignment.json"),
        },
        "schemas_manifest_sha256": schemas_manifest_sha256(schema_dir),
        "schema_hashes": [
            {"path": f"artifacts/phase-0/schemas/{path.name}", "sha256": sha256(path)}
            for path in schema_paths
        ],
        "verifier_file_hashes": [
            {"path": relative, "sha256": sha256(ROOT / relative)}
            for relative in sorted(VERIFIER_FILES)
        ],
        "prerequisites": [
            "Run from the repository root on Windows with powershell available.",
            "The frozen CPython interpreter exists at interpreter_path and matches interpreter_version and interpreter_sha256.",
            f"The {EXPECTED_VERIFIER_COUNT} frozen verification, replay, consumer, reviewer, and finalize files match verifier_file_hashes before execution.",
            f"The three P0-01 frozen input files and all {EXPECTED_SCHEMA_COUNT} schema files match the exact SHA-256 values recorded here.",
            "The bootstrap gate validates P0-01 without network; the frozen replay command is reserved for P0-07 after the cutoff and exact 24-request closeout gate.",
            "Finalize is a distinct post-attestation command; full_verify_command is read-only and must not generate terminal artifacts.",
            "No network connection is required or permitted by bootstrap, replay, finalize, or full verification.",
        ],
        "self_hash_sidecar": "artifacts/phase-0/verification-command.json.sha256",
    })
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-at-utc", required=True)
    args = parser.parse_args(argv)
    try:
        command = build_command(args.frozen_at_utc)
        payload = json_bytes(command)
        digest = hashlib.sha256(payload).hexdigest()
        atomic_write(COMMAND, payload)
        atomic_write(COMMAND_SIDECAR, (digest + "\n").encode("ascii"))
        if sha256(COMMAND) != digest or COMMAND_SIDECAR.read_text(encoding="ascii").strip() != digest:
            raise RuntimeError("verification-command atomic freeze verification failed")
        print(json.dumps({
            "status": "FROZEN",
            "freeze_id": command["freeze_id"],
            "frozen_at_utc": command["frozen_at_utc"],
            "schema_count": len(command["schema_hashes"]),
            "verifier_file_count": len(command["verifier_file_hashes"]),
            "verification_command_sha256": digest,
            "network_used": False,
        }, separators=(",", ":")))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc), "network_used": False}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
