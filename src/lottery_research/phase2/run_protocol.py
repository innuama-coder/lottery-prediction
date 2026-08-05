"""Execute one Phase 2 CLI command with immutable request/result receipts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .input_validation import sha256
from .schema import validate_payload
from .serialization import canonical_json_bytes


PATH_FLAGS = {
    "--contract",
    "--input-rule-contract",
    "--input-manifest",
    "--preregistration",
    "--reviewer-assignment",
    "--evidence-manifest",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _label(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _identity(root: Path, path: Path) -> dict[str, str]:
    return {"path": _label(root, path), "sha256": sha256(path.resolve())}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(payload))
    temporary.replace(path)


def _flag_value(argv: Sequence[str], name: str) -> str | None:
    try:
        index = list(argv).index(name)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def _effective_inputs(root: Path, command: str, argv: Sequence[str]) -> list[Path]:
    paths: list[Path] = []
    for index, token in enumerate(argv[:-1]):
        if token in PATH_FLAGS:
            candidate = Path(argv[index + 1])
            if not candidate.is_absolute():
                candidate = root / candidate
            if candidate.is_file():
                paths.append(candidate)
    fixed = [
        root / "artifacts/phase-2/contracts/environment-lock.json",
        root / "artifacts/phase-2/reviews/method-review.json",
    ]
    if command in {"audit", "power", "replay"}:
        fixed.append(root / "artifacts/phase-2/qualification/harness-qualification.json")
    if command == "replay":
        fixed.extend(
            [
                root / "artifacts/phase-2/results/historical-audit.json",
                root / "artifacts/phase-2/results/power-envelope.json",
            ]
        )
    paths.extend(path for path in fixed if path.is_file())
    unique: dict[str, Path] = {str(path.resolve()).lower(): path for path in paths}
    return [unique[key] for key in sorted(unique)]


def execute_recorded(root: Path, run_id: str, argv: Sequence[str], run_root: Path) -> tuple[int, dict[str, Any], Path, Path]:
    """Run the Phase 2 CLI and write validated request/result receipts."""

    root = root.resolve()
    if not argv or argv[0] not in {"validate-input", "qualify-harness", "audit", "power", "replay", "accept"}:
        raise ValueError("argv must start with a Phase 2 contract command")
    command = argv[0]
    run_dir = run_root.resolve() / run_id
    if run_dir.exists():
        raise FileExistsError(f"run id already exists: {run_id}")
    run_dir.mkdir(parents=True)
    output_value = _flag_value(argv, "--output")
    output_path = Path(output_value) if output_value else None
    if output_path is not None and not output_path.is_absolute():
        output_path = root / output_path
    contract_value = _flag_value(argv, "--contract")
    if contract_value is None:
        raise ValueError("recorded Phase 2 run requires --contract")
    contract_path = Path(contract_value)
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    inputs = _effective_inputs(root, command, argv)
    request = {
        "schema_version": "1.0.0",
        "artifact_type": "phase2_run_request",
        "run_id": run_id,
        "command": command,
        "argv": list(argv),
        "created_at_utc": _now(),
        "contract_identity": _identity(root, contract_path),
        "input_identities": [_identity(root, path) for path in inputs],
        "seed_set_id": _flag_value(argv, "--seed-set"),
        "output_path": _label(root, output_path) if output_path is not None else "none",
    }
    validate_payload("run_request", request)
    request_path = run_dir / "request.json"
    _atomic_write(request_path, request)

    started = _now()
    process = subprocess.run(
        [sys.executable, "-m", "lottery_research.phase2", *argv],
        cwd=root,
        capture_output=True,
        check=False,
    )
    finished = _now()
    stdout_path = run_dir / "stdout.json"
    stderr_path = run_dir / "stderr.txt"
    stdout_path.write_bytes(process.stdout)
    stderr_path.write_bytes(process.stderr)
    try:
        cli_result = json.loads(process.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Phase 2 CLI did not emit a valid JSON result: {exc}") from exc
    if int(cli_result.get("exit_code", -999)) != process.returncode:
        raise RuntimeError("process return code differs from CLI result exit_code")
    outputs = [stdout_path, stderr_path]
    if output_path is not None and output_path.is_file():
        outputs.append(output_path)
    result = {
        "schema_version": "1.0.0",
        "artifact_type": "phase2_run_result",
        "run_id": run_id,
        "command": command,
        "terminal": cli_result["terminal"],
        "exit_code": process.returncode,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "request_identity": _identity(root, request_path),
        "input_identities": request["input_identities"],
        "output_identities": [_identity(root, path) for path in outputs],
        "metrics": cli_result.get("checks", {}),
        "errors": cli_result.get("errors", []),
    }
    validate_payload("run_result", result)
    result_path = run_dir / "result.json"
    _atomic_write(result_path, result)
    return process.returncode, cli_result, request_path, result_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("phase2_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv) if argv is not None else None)
    command_argv = args.phase2_argv
    if command_argv and command_argv[0] == "--":
        command_argv = command_argv[1:]
    code, _result, _request_path, _result_path = execute_recorded(
        args.project_root, args.run_id, command_argv, args.run_root
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
