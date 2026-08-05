"""Produce actual, fail-closed evidence for the Phase-0 C freeze review.

This is a governance-side observer.  It is deliberately not part of the
frozen Phase-0 replay route: the production CLI accepts no arguments, uses the
repository containing this file, reads the real clock, and executes the real
subprocesses.  Tests may exercise :func:`collect_validation` with an injected
``run_fn``; that injection is not reachable from the production CLI.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from archive_p0_c_pre import PROTECTED_PATHS, canonical_sha256, decision_surface, sha256_file


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts/phase-0"
RECORD = ARTIFACTS / "freeze-validation-p0-20260801-c.json"
SIDECAR = ARTIFACTS / "freeze-validation-p0-20260801-c.json.sha256"
VALIDATION_ID = "p0-20260801-c-freeze-validation"
EXPECTED_TEST_COUNT = 160
EXPECTED_STAGES = ("p0-01", "p0-02", "p0-03", "p0-05", "p0-06")
TASK_NAME = "AutoresearchLotte-P0-06"
POWERSHELL = str(Path(shutil.which("powershell") or "powershell").resolve())
REPLAY_COMMAND = (
    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
    "scripts/phase0/p0_07_replay_launcher.ps1",
)
VERIFY_PREFIX = (
    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
    "scripts/phase0/verify_phase0.ps1", "--contract",
    "docs/roadmap/phase-0-acceptance-contract.json", "--artifacts",
    "artifacts/phase-0",
)
UNITTEST_COMMAND = (
    str(Path(sys.executable).resolve()), "-B", "-m", "unittest", "discover",
    "-s", "tests/phase0", "-p", "test_*.py", "-q",
)
SCHEDULER_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'AutoresearchLotte-P0-06' -ErrorAction Stop
$info = Get-ScheduledTaskInfo -TaskName 'AutoresearchLotte-P0-06' -ErrorAction Stop
$actions = @($task.Actions | ForEach-Object {
  [ordered]@{ execute=[string]$_.Execute; arguments=[string]$_.Arguments; working_directory=[string]$_.WorkingDirectory }
})
$triggers = @($task.Triggers | ForEach-Object {
  [ordered]@{ start_boundary=[string]$_.StartBoundary; enabled=[bool]$_.Enabled }
})
$account = [System.Security.Principal.NTAccount]::new([string]$task.Principal.UserId)
$principalSid = $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
try { $executionLimit = [System.Xml.XmlConvert]::ToTimeSpan([string]$task.Settings.ExecutionTimeLimit) }
catch { $executionLimit = [TimeSpan]::Parse([string]$task.Settings.ExecutionTimeLimit) }
[ordered]@{
  powershell = [ordered]@{
    executable = [string](Get-Process -Id $PID).Path
    version = [string]$PSVersionTable.PSVersion.ToString()
    edition = [string]$PSVersionTable.PSEdition
  }
  task_name = [string]$task.TaskName
  state = [string]$task.State
  trigger_count = [int]$triggers.Count
  actions = $actions
  triggers = $triggers
  settings = [ordered]@{
    start_when_available = [bool]$task.Settings.StartWhenAvailable
    execution_time_limit = [string]$task.Settings.ExecutionTimeLimit
    execution_time_limit_minutes = [double]$executionLimit.TotalMinutes
    multiple_instances = [string]$task.Settings.MultipleInstances
  }
  principal = [ordered]@{
    user_id = [string]$task.Principal.UserId
    resolved_sid = [string]$principalSid
    current_sid = [string][System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    logon_type = [string]$task.Principal.LogonType
    run_level = [string]$task.Principal.RunLevel
  }
  next_run_local = ([DateTime]$info.NextRunTime).ToString('o')
  last_run_local = ([DateTime]$info.LastRunTime).ToString('o')
  last_task_result = [int64]$info.LastTaskResult
  missed_runs = [int]$info.NumberOfMissedRuns
} | ConvertTo-Json -Compress
""".strip()
SCHEDULER_COMMAND = (
    POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
    SCHEDULER_SCRIPT,
)
TREE_EXCLUSIONS = {
    "subtrees": ["batches"],
    "root_name_prefixes": ["repair-manifest"],
    "root_names": [RECORD.name, SIDECAR.name],
    "governance_temporary_name_prefixes": [
        f".{RECORD.name}.", f".{SIDECAR.name}.",
    ],
}


class FreezeValidationError(RuntimeError):
    pass


RunFn = Callable[..., subprocess.CompletedProcess[bytes]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _excluded(relative: str) -> bool:
    parts = Path(relative).parts
    if parts and parts[0] in TREE_EXCLUSIONS["subtrees"]:
        return True
    if len(parts) != 1:
        return False
    name = parts[0]
    return (
        name in TREE_EXCLUSIONS["root_names"]
        or any(name.startswith(prefix) for prefix in TREE_EXCLUSIONS["root_name_prefixes"])
        or any(name.startswith(prefix) for prefix in TREE_EXCLUSIONS["governance_temporary_name_prefixes"])
    )


def operational_tree(artifacts: Path) -> dict[str, Any]:
    if not artifacts.is_dir():
        raise FreezeValidationError("operational artifacts root is missing")
    directories: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for path in sorted(artifacts.rglob("*"), key=lambda item: item.relative_to(artifacts).as_posix()):
        relative = path.relative_to(artifacts).as_posix()
        if _excluded(relative):
            continue
        if path.is_dir():
            directories.append({
                "path": relative, "type": "directory", "size": None,
                "sha256": None, "mtime_ns": path.stat().st_mtime_ns,
            })
        elif path.is_file():
            files.append({
                "path": relative, "type": "file", "size": path.stat().st_size,
                "sha256": sha256_file(path), "mtime_ns": path.stat().st_mtime_ns,
            })
        else:
            raise FreezeValidationError(f"unsupported operational tree entry: {relative}")
    closure = {"directories": directories, "files": files}
    return {
        "root": "artifacts/phase-0",
        "hash_algorithm": "sha256(canonical-json({directories,files}))",
        "exclusions": TREE_EXCLUSIONS,
        **closure,
        "directory_count": len(directories),
        "file_count": len(files),
        "root_sha256": _sha256_bytes(_canonical_bytes(closure)),
    }


def trusted_inputs(repo_root: Path) -> dict[str, Any]:
    fixed = {
        repo_root / "docs/roadmap/phase-0-acceptance-contract.json",
        repo_root / "docs/roadmap/phase-0-data-feasibility-plan.md",
        repo_root / "artifacts/phase-0/verification-command.json",
        repo_root / "artifacts/phase-0/verification-command.json.sha256",
    }
    fixed.update((repo_root / "artifacts/phase-0/schemas").glob("*.schema.json"))
    fixed.update(path for path in (repo_root / "scripts/phase0").iterdir() if path.is_file() and path.suffix in {".py", ".ps1"})
    fixed.update(path for path in (repo_root / "tests/phase0").rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    records = []
    for path in sorted(fixed, key=lambda item: item.relative_to(repo_root).as_posix()):
        if not path.is_file():
            raise FreezeValidationError(f"trusted input is missing: {path}")
        records.append({
            "path": path.relative_to(repo_root).as_posix(), "type": "file",
            "size": path.stat().st_size, "sha256": sha256_file(path),
            "mtime_ns": path.stat().st_mtime_ns,
        })
    return {
        "scope": "execution scripts, Phase-0 tests/fixtures, acceptance contract/plan, formal command/sidecar, and all schemas",
        "file_count": len(records),
        "files": records,
        "root_sha256": _sha256_bytes(_canonical_bytes(records)),
    }


def _process_record(
    role: str, argv: tuple[str, ...], completed: subprocess.CompletedProcess[bytes],
    duration: float, started_at: str, completed_at: str,
) -> dict[str, Any]:
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        raise FreezeValidationError(f"{role} runner did not return exact bytes")
    executable = Path(shutil.which(argv[0]) or argv[0]).resolve()
    if not executable.is_file():
        raise FreezeValidationError(f"{role} executable cannot be resolved")
    return {
        "role": role,
        "argv": list(argv),
        "cwd": str(ROOT),
        "exit_code": completed.returncode,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "wall_duration_seconds": round(duration, 6),
        "executable_identity": {
            "requested": argv[0],
            "resolved_path": str(executable),
            "size": executable.stat().st_size,
            "sha256": sha256_file(executable),
        },
        "stdout": {
            "encoding": "base64",
            "bytes_base64": base64.b64encode(stdout).decode("ascii"),
            "size": len(stdout),
            "sha256": _sha256_bytes(stdout),
        },
        "stderr": {
            "encoding": "base64",
            "bytes_base64": base64.b64encode(stderr).decode("ascii"),
            "size": len(stderr),
            "sha256": _sha256_bytes(stderr),
        },
    }


def _run(role: str, argv: tuple[str, ...], timeout: int, run_fn: RunFn) -> tuple[dict[str, Any], bytes, bytes]:
    started_at = _utc_now()
    started = time.monotonic()
    try:
        completed = run_fn(list(argv), cwd=ROOT, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FreezeValidationError(f"{role} exceeded {timeout}s timeout") from exc
    duration = time.monotonic() - started
    completed_at = _utc_now()
    record = _process_record(role, argv, completed, duration, started_at, completed_at)
    return record, completed.stdout or b"", completed.stderr or b""


def _one_json_line(raw: bytes, role: str) -> dict[str, Any]:
    lines = [line for line in raw.decode("utf-8", errors="strict").splitlines() if line.strip()]
    if len(lines) != 1:
        raise FreezeValidationError(f"{role} did not emit exactly one JSON line")
    value = json.loads(lines[0])
    if not isinstance(value, dict):
        raise FreezeValidationError(f"{role} JSON output is not an object")
    return value


def _parse_phase(stage: str, process: dict[str, Any], stdout: bytes, stderr: bytes) -> dict[str, Any]:
    if process["exit_code"] != 0 or stderr:
        raise FreezeValidationError(f"{stage} phase gate did not exit cleanly")
    value = _one_json_line(stdout, stage)
    if value != {"status": "PASS", "stage": stage, "contract_version": "1.3", "network_used": False}:
        raise FreezeValidationError(f"{stage} phase gate output semantics mismatch")
    return {"stage": stage, "contract_version": "1.3", "status": "PASS", "network_used": False}


def _parse_unittest(process: dict[str, Any], stdout: bytes, stderr: bytes) -> dict[str, Any]:
    if stdout:
        raise FreezeValidationError("full unittest stdout must be exactly empty")
    match = re.fullmatch(
        rb"-{70}\r?\nRan ([0-9]+) tests? in ([0-9.]+)s\r?\n\r?\nOK\r?\n?",
        stderr,
    )
    if process["exit_code"] != 0 or match is None:
        raise FreezeValidationError("full unittest stderr is not the unique complete Ran N / OK summary")
    observed_count = int(match.group(1))
    if observed_count != EXPECTED_TEST_COUNT:
        raise FreezeValidationError(f"full unittest observed {observed_count}, expected {EXPECTED_TEST_COUNT}")
    return {
        "observed_test_count": observed_count,
        "reported_duration_seconds": float(match.group(2)),
        "result": "OK",
        "count_source": "parsed_from_exact_subprocess_output",
    }


REPLAY_HOLD_ERROR = "P0-06 full completion gate cannot run before the frozen acceptance cutoff"
REPLAY_HOLD_BYTES = _canonical_bytes({"error": REPLAY_HOLD_ERROR, "network_used": False, "status": "HOLD"})


def _parse_replay(repo_root: Path, process: dict[str, Any], stdout: bytes, stderr: bytes) -> dict[str, Any]:
    if process["exit_code"] != 1 or stdout:
        raise FreezeValidationError("pre-cutoff replay did not fail closed with exit 1 and empty stdout")
    if stderr != REPLAY_HOLD_BYTES:
        raise FreezeValidationError("pre-cutoff replay stderr is not the one exact canonical HOLD JSON line")
    value = _one_json_line(stderr, "pre-cutoff replay")
    if value != {"error": REPLAY_HOLD_ERROR, "network_used": False, "status": "HOLD"}:
        raise FreezeValidationError("pre-cutoff replay did not report HOLD / network_used=false")
    runtime = json.loads((repo_root / "artifacts/phase-0/p0-06-runtime-plan.json").read_text(encoding="utf-8"))
    cutoff_text = runtime["acceptance_cutoff_utc"]
    cutoff = datetime.fromisoformat(cutoff_text.replace("Z", "+00:00"))
    observed_started = datetime.fromisoformat(process["started_at_utc"].replace("Z", "+00:00"))
    observed_completed = datetime.fromisoformat(process["completed_at_utc"].replace("Z", "+00:00"))
    if observed_started.tzinfo is None or not (observed_started <= observed_completed < cutoff):
        raise FreezeValidationError("replay HOLD observation is not wholly before the frozen cutoff")
    return {
        "status": "HOLD", "exit_code": 1, "network_used": False,
        "error": REPLAY_HOLD_ERROR, "acceptance_cutoff_utc": cutoff_text,
        "observed_started_at_utc": process["started_at_utc"],
        "observed_completed_at_utc": process["completed_at_utc"],
        "stderr_semantics": "one exact canonical JSON line",
    }


def _local_naive(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=None).isoformat(timespec="seconds")


def _parse_scheduler(repo_root: Path, process: dict[str, Any], stdout: bytes, stderr: bytes) -> dict[str, Any]:
    if process["exit_code"] != 0 or stderr:
        raise FreezeValidationError("read-only Task Scheduler query did not exit cleanly")
    value = _one_json_line(stdout, "Task Scheduler query")
    expected_keys = {
        "powershell", "task_name", "state", "trigger_count", "actions", "triggers", "settings", "principal",
        "next_run_local", "last_run_local", "last_task_result", "missed_runs",
    }
    if set(value) != expected_keys:
        raise FreezeValidationError("Task Scheduler query fields differ from the fixed contract")
    plan = json.loads((repo_root / "artifacts/phase-0/p0-06-runtime-plan.json").read_text(encoding="utf-8"))
    expected_triggers = sorted(_local_naive(item["local_at"]) for item in plan["scheduler"]["triggers"])
    observed_triggers = sorted(_local_naive(item["start_boundary"]) for item in value["triggers"])
    verification = json.loads((repo_root / "artifacts/phase-0/verification-command.json").read_text(encoding="utf-8"))
    runner = str((repo_root / "scripts/phase0/p0_06_runner.py").resolve())
    artifacts = str((repo_root / "artifacts/phase-0").resolve())
    plan_sha = (repo_root / "artifacts/phase-0/p0-06-runtime-plan.json.sha256").read_text(encoding="ascii").strip()
    expected_arguments = f'"{runner}" --action execute-due --artifacts "{artifacts}" --allow-network --expected-plan-sha256 {plan_sha}'
    expected_action = [{
        "execute": str(Path(verification["interpreter_path"]).resolve()),
        "arguments": expected_arguments, "working_directory": str(repo_root.resolve()),
    }]
    last_run = datetime.fromisoformat(value["last_run_local"])
    never_run = value["last_task_result"] == 267011 and last_run.year <= 2000
    checks = {
        "powershell_identity": Path(value["powershell"].get("executable", "")).resolve() == Path(POWERSHELL).resolve() and bool(value["powershell"].get("version")) and value["powershell"].get("edition") == "Desktop",
        "task_name_exact": value["task_name"] == TASK_NAME,
        "state_ready": value["state"] == "Ready",
        "action_exact": value["actions"] == expected_action,
        "trigger_count_24": value["trigger_count"] == 24 and len(value["triggers"]) == 24,
        "trigger_times_exact": observed_triggers == expected_triggers and all(item["enabled"] is True for item in value["triggers"]),
        "settings_exact": value["settings"].get("start_when_available") is True and value["settings"].get("execution_time_limit_minutes") == 15 and value["settings"].get("multiple_instances") == "IgnoreNew",
        "principal_exact": value["principal"].get("resolved_sid") == value["principal"].get("current_sid") and value["principal"].get("logon_type") in {"Interactive", "InteractiveToken"} and value["principal"].get("run_level") == "Limited",
        "never_run": never_run,
        "missed_runs_zero": value["missed_runs"] == 0,
        "next_run_exact": _local_naive(value["next_run_local"]) == expected_triggers[0],
    }
    if not all(checks.values()):
        raise FreezeValidationError(f"Task Scheduler live-state checks failed: {[key for key, passed in checks.items() if not passed]}")
    return {
        "query_mode": "read_only_Get-ScheduledTask_plus_Get-ScheduledTaskInfo",
        "checks": checks,
        "powershell": value["powershell"],
        "task_name": value["task_name"],
        "state": value["state"],
        "trigger_count": value["trigger_count"],
        "actions": value["actions"],
        "triggers": value["triggers"],
        "settings": value["settings"],
        "principal": value["principal"],
        "next_run_local": value["next_run_local"],
        "last_run_state": "never_run",
        "last_run_local": value["last_run_local"],
        "last_task_result": value["last_task_result"],
        "missed_runs": value["missed_runs"],
    }


def _protected_state(repo_root: Path) -> dict[str, Any]:
    artifacts = repo_root / "artifacts/phase-0"
    command = artifacts / "verification-command.json"
    command_sidecar = artifacts / "verification-command.json.sha256"
    command_sha = sha256_file(command)
    if command_sidecar.read_text(encoding="ascii").strip() != command_sha:
        raise FreezeValidationError("formal verification command sidecar mismatch")
    protected = {path: sha256_file(repo_root / path) for path in PROTECTED_PATHS}
    soak = artifacts / "soak-run-log.jsonl"
    command_record = json.loads(command.read_text(encoding="utf-8"))
    frozen_interpreter = Path(command_record["interpreter_path"])
    if not frozen_interpreter.is_file() or sha256_file(frozen_interpreter) != command_record["interpreter_sha256"]:
        raise FreezeValidationError("formal frozen interpreter identity mismatch")
    return {
        "decision_surface_sha256": canonical_sha256(decision_surface(repo_root)),
        "protected_file_hashes": protected,
        "soak_log": {"path": "artifacts/phase-0/soak-run-log.jsonl", "size": soak.stat().st_size, "sha256": sha256_file(soak)},
        "formal_verification_command": {
            "path": "artifacts/phase-0/verification-command.json",
            "sha256": command_sha,
            "sidecar_path": "artifacts/phase-0/verification-command.json.sha256",
            "sidecar_sha256": sha256_file(command_sidecar),
            "sidecar_value": command_sha,
            "frozen_interpreter": {
                "path": command_record["interpreter_path"],
                "version": command_record["interpreter_version"],
                "size": frozen_interpreter.stat().st_size,
                "sha256": command_record["interpreter_sha256"],
            },
        },
    }


def _production_identity(repo_root: Path) -> dict[str, Any]:
    command = json.loads((repo_root / "artifacts/phase-0/verification-command.json").read_text(encoding="utf-8"))
    expected = Path(command["interpreter_path"]).resolve()
    actual = Path(sys.executable).resolve()
    if os.path.normcase(str(actual)) != os.path.normcase(str(expected)):
        raise FreezeValidationError("validation must run under the exact formal frozen interpreter path")
    if platform.python_version() != command["interpreter_version"] or sha256_file(actual) != command["interpreter_sha256"]:
        raise FreezeValidationError("validation invoking interpreter version/hash differs from the formal freeze")
    override = os.environ.get("PHASE0_PYTHON")
    if override is not None and os.path.normcase(str(Path(override).resolve())) != os.path.normcase(str(expected)):
        raise FreezeValidationError("PHASE0_PYTHON may be absent or name only the exact formal frozen interpreter")
    return {
        "path": str(actual), "version": platform.python_version(),
        "size": actual.stat().st_size, "sha256": sha256_file(actual),
        "environment": {
            "PHASE0_PYTHON": override,
            "PYTHONDONTWRITEBYTECODE": os.environ.get("PYTHONDONTWRITEBYTECODE"),
            "python_dont_write_bytecode_flag": bool(sys.flags.dont_write_bytecode),
        },
    }


def collect_validation(*, repo_root: Path = ROOT, run_fn: RunFn = subprocess.run) -> dict[str, Any]:
    """Collect actual observations; tests may inject only the subprocess runner."""
    if repo_root.resolve() != ROOT:
        # A test fixture may mirror the repository, but subprocess cwd and fixed
        # commands remain rooted at the production repository by design.
        raise FreezeValidationError("validation core is fixed to its repository root")
    invoking_interpreter = _production_identity(ROOT)
    started = _utc_now()
    tree_before = operational_tree(ARTIFACTS)
    trusted_before = trusted_inputs(ROOT)
    protected_before = _protected_state(ROOT)
    processes: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []

    scheduler_before_process, stdout, stderr = _run("scheduler_live_read_only_before", SCHEDULER_COMMAND, 120, run_fn)
    scheduler_before = _parse_scheduler(ROOT, scheduler_before_process, stdout, stderr)
    scheduler_before_process["parsed_observation"] = scheduler_before
    processes.append(scheduler_before_process)
    for stage in EXPECTED_STAGES:
        argv = (*VERIFY_PREFIX, "--stage", stage)
        process, stdout, stderr = _run(f"phase_gate_{stage}", argv, 180, run_fn)
        process["parsed_observation"] = _parse_phase(stage, process, stdout, stderr)
        processes.append(process)
        phases.append(process["parsed_observation"])

    unit_process, stdout, stderr = _run("full_unittest_discover", UNITTEST_COMMAND, 900, run_fn)
    unittest_result = _parse_unittest(unit_process, stdout, stderr)
    unit_process["parsed_observation"] = unittest_result
    processes.append(unit_process)

    replay_process, stdout, stderr = _run("pre_cutoff_replay_launcher", REPLAY_COMMAND, 180, run_fn)
    replay_result = _parse_replay(ROOT, replay_process, stdout, stderr)
    replay_process["parsed_observation"] = replay_result
    processes.append(replay_process)

    scheduler_after_process, stdout, stderr = _run("scheduler_live_read_only_after", SCHEDULER_COMMAND, 120, run_fn)
    scheduler_after = _parse_scheduler(ROOT, scheduler_after_process, stdout, stderr)
    scheduler_after_process["parsed_observation"] = scheduler_after
    processes.append(scheduler_after_process)
    if scheduler_before != scheduler_after:
        raise FreezeValidationError("Task Scheduler state changed during validation")

    protected_after = _protected_state(ROOT)
    trusted_after = trusted_inputs(ROOT)
    tree_after = operational_tree(ARTIFACTS)
    if tree_before != tree_after:
        raise FreezeValidationError("operational artifacts tree changed during validation")
    if protected_before != protected_after:
        raise FreezeValidationError("protected/decision/soak/formal-command state changed during validation")
    if trusted_before != trusted_after:
        raise FreezeValidationError("trusted execution inputs changed during validation")
    completed = _utc_now()
    record = {
        "schema_version": "1.0.0",
        "artifact_type": "phase0_c_freeze_validation",
        "validation_id": VALIDATION_ID,
        "status": "PASS",
        "evidence_semantics": "actual_observation",
        "started_at_utc": started,
        "completed_at_utc": completed,
        "production_cli": {
            "entrypoint": "scripts/phase0/validate_p0_c_freeze.py",
            "arguments_forbidden": True,
            "caller_clock_override": False,
            "caller_result_override": False,
            "network_used": False,
            "invoking_interpreter": invoking_interpreter,
        },
        "integrity_model": {
            "sidecar_role": "unkeyed_sha256_commit_marker_fail_closed_not_authentication",
            "authenticated": False,
            "final_binding": "exact record and sidecar bytes must be bound by the immutable C-after snapshot and independently rerun by Reviewer4",
        },
        "phase_gates": phases,
        "full_unittest": unittest_result,
        "pre_cutoff_replay": replay_result,
        "scheduler_live_state": {"before": scheduler_before, "after": scheduler_after, "unchanged": True},
        "operational_tree": {
            "before": tree_before,
            "after": tree_after,
            "file_diff_count": 0,
            "directory_diff_count": 0,
        },
        "protected_state": {"before": protected_before, "after": protected_after, "unchanged": True},
        "trusted_inputs": {"before": trusted_before, "after": trusted_after, "unchanged": True},
        "processes": processes,
    }
    validate_record(record, ROOT, compare_current=True)
    return record


def _decode_stream(stream: dict[str, Any], role: str, name: str) -> bytes:
    if set(stream) != {"encoding", "bytes_base64", "size", "sha256"} or stream.get("encoding") != "base64":
        raise FreezeValidationError(f"{role} {name} record shape mismatch")
    try:
        raw = base64.b64decode(stream["bytes_base64"], validate=True)
    except Exception as exc:
        raise FreezeValidationError(f"{role} {name} is not valid exact-byte base64") from exc
    if len(raw) != stream["size"] or _sha256_bytes(raw) != stream["sha256"]:
        raise FreezeValidationError(f"{role} {name} exact-byte hash mismatch")
    return raw


def validate_record(record: dict[str, Any], repo_root: Path = ROOT, *, compare_current: bool = True) -> None:
    required_top = {
        "schema_version", "artifact_type", "validation_id", "status", "evidence_semantics",
        "started_at_utc", "completed_at_utc", "production_cli", "integrity_model", "phase_gates", "full_unittest",
        "pre_cutoff_replay", "scheduler_live_state", "operational_tree", "protected_state", "trusted_inputs", "processes",
    }
    if set(record) != required_top:
        raise FreezeValidationError("freeze validation top-level fields are not exact")
    if (record["schema_version"], record["artifact_type"], record["validation_id"], record["status"], record["evidence_semantics"]) != (
        "1.0.0", "phase0_c_freeze_validation", VALIDATION_ID, "PASS", "actual_observation",
    ):
        raise FreezeValidationError("freeze validation identity/status semantics mismatch")
    started = datetime.fromisoformat(record["started_at_utc"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(record["completed_at_utc"].replace("Z", "+00:00"))
    if started.tzinfo is None or completed < started:
        raise FreezeValidationError("freeze validation interval is invalid")
    if record["production_cli"] != {
        "entrypoint": "scripts/phase0/validate_p0_c_freeze.py", "arguments_forbidden": True,
        "caller_clock_override": False, "caller_result_override": False, "network_used": False,
        "invoking_interpreter": record["production_cli"].get("invoking_interpreter"),
    }:
        raise FreezeValidationError("freeze validation production-boundary claim mismatch")
    invoking = record["production_cli"]["invoking_interpreter"]
    command = json.loads((repo_root / "artifacts/phase-0/verification-command.json").read_text(encoding="utf-8"))
    expected_interpreter = Path(command["interpreter_path"]).resolve()
    if invoking.get("path") != str(expected_interpreter) or invoking.get("version") != command["interpreter_version"] or invoking.get("size") != expected_interpreter.stat().st_size or invoking.get("sha256") != command["interpreter_sha256"]:
        raise FreezeValidationError("recorded invoking interpreter identity differs from formal freeze")
    environment = invoking.get("environment")
    if set(environment or {}) != {"PHASE0_PYTHON", "PYTHONDONTWRITEBYTECODE", "python_dont_write_bytecode_flag"} or environment["python_dont_write_bytecode_flag"] is not True:
        raise FreezeValidationError("recorded validation environment/bytecode flag is incomplete")
    if environment["PHASE0_PYTHON"] is not None and os.path.normcase(str(Path(environment["PHASE0_PYTHON"]).resolve())) != os.path.normcase(str(expected_interpreter)):
        raise FreezeValidationError("recorded PHASE0_PYTHON differs from formal interpreter")
    if record["integrity_model"] != {
        "sidecar_role": "unkeyed_sha256_commit_marker_fail_closed_not_authentication",
        "authenticated": False,
        "final_binding": "exact record and sidecar bytes must be bound by the immutable C-after snapshot and independently rerun by Reviewer4",
    }:
        raise FreezeValidationError("freeze validation integrity model is overstated or changed")

    expected_roles = ["scheduler_live_read_only_before", *(f"phase_gate_{stage}" for stage in EXPECTED_STAGES), "full_unittest_discover", "pre_cutoff_replay_launcher", "scheduler_live_read_only_after"]
    if len(record["processes"]) != len(expected_roles) or [item.get("role") for item in record["processes"]] != expected_roles:
        raise FreezeValidationError("freeze validation subprocess closure is not exact")
    expected_argv = [
        list(SCHEDULER_COMMAND),
        *[list((*VERIFY_PREFIX, "--stage", stage)) for stage in EXPECTED_STAGES],
        list(UNITTEST_COMMAND), list(REPLAY_COMMAND), list(SCHEDULER_COMMAND),
    ]
    parsed_phases = []
    parsed_schedulers = []
    for process, role, argv in zip(record["processes"], expected_roles, expected_argv, strict=True):
        if process.get("argv") != argv or process.get("cwd") != str(ROOT):
            raise FreezeValidationError(f"{role} argv/cwd differs from the fixed route")
        if type(process.get("exit_code")) is not int or not isinstance(process.get("wall_duration_seconds"), (int, float)) or process["wall_duration_seconds"] < 0:
            raise FreezeValidationError(f"{role} exit/duration observation invalid")
        process_started = datetime.fromisoformat(process.get("started_at_utc", "").replace("Z", "+00:00"))
        process_completed = datetime.fromisoformat(process.get("completed_at_utc", "").replace("Z", "+00:00"))
        if process_started.tzinfo is None or not (started <= process_started <= process_completed <= completed):
            raise FreezeValidationError(f"{role} process interval lies outside the validation interval")
        executable = Path(shutil.which(argv[0]) or argv[0]).resolve()
        identity = process.get("executable_identity")
        expected_identity = {
            "requested": argv[0], "resolved_path": str(executable),
            "size": executable.stat().st_size, "sha256": sha256_file(executable),
        }
        if identity != expected_identity:
            raise FreezeValidationError(f"{role} executable identity mismatch")
        stdout = _decode_stream(process.get("stdout", {}), role, "stdout")
        stderr = _decode_stream(process.get("stderr", {}), role, "stderr")
        if role.startswith("scheduler_live_read_only_"):
            parsed = _parse_scheduler(repo_root, process, stdout, stderr)
            parsed_schedulers.append(parsed)
        elif role.startswith("phase_gate_"):
            stage = role.removeprefix("phase_gate_")
            parsed = _parse_phase(stage, process, stdout, stderr)
            parsed_phases.append(parsed)
        elif role == "full_unittest_discover":
            parsed = _parse_unittest(process, stdout, stderr)
            if parsed != record["full_unittest"]:
                raise FreezeValidationError("full unittest summary is not mechanically derived")
        elif role == "pre_cutoff_replay_launcher":
            parsed = _parse_replay(repo_root, process, stdout, stderr)
            if parsed != record["pre_cutoff_replay"]:
                raise FreezeValidationError("pre-cutoff replay summary is not mechanically derived")
        else:
            raise FreezeValidationError(f"unexpected subprocess role: {role}")
        if process.get("parsed_observation") != parsed:
            raise FreezeValidationError(f"{role} parsed observation differs from exact bytes")
    if record["phase_gates"] != parsed_phases or [item["stage"] for item in parsed_phases] != list(EXPECTED_STAGES):
        raise FreezeValidationError("phase gate summary is not mechanically derived")
    if len(parsed_schedulers) != 2 or parsed_schedulers[0] != parsed_schedulers[1] or record["scheduler_live_state"] != {"before": parsed_schedulers[0], "after": parsed_schedulers[1], "unchanged": True}:
        raise FreezeValidationError("scheduler before/after state is not identical and mechanically derived")

    tree = record["operational_tree"]
    if set(tree) != {"before", "after", "file_diff_count", "directory_diff_count"}:
        raise FreezeValidationError("operational tree comparison shape mismatch")
    if tree["file_diff_count"] != 0 or tree["directory_diff_count"] != 0 or tree["before"] != tree["after"]:
        raise FreezeValidationError("operational artifacts tree is not byte/path stable")
    for observed in (tree["before"], tree["after"]):
        closure = {"directories": observed["directories"], "files": observed["files"]}
        if observed.get("exclusions") != TREE_EXCLUSIONS or observed.get("directory_count") != len(closure["directories"]) or observed.get("file_count") != len(closure["files"]):
            raise FreezeValidationError("operational tree inventory metadata mismatch")
        if observed.get("root") != "artifacts/phase-0" or observed.get("hash_algorithm") != "sha256(canonical-json({directories,files}))" or observed.get("root_sha256") != _sha256_bytes(_canonical_bytes(closure)):
            raise FreezeValidationError("operational tree root hash mismatch")
        paths = [item["path"] for item in closure["files"]]
        directory_paths = [item["path"] for item in closure["directories"]]
        if paths != sorted(paths) or len(paths) != len(set(paths)) or directory_paths != sorted(directory_paths) or len(directory_paths) != len(set(directory_paths)):
            raise FreezeValidationError("operational tree inventory is not unique and sorted")
        if any(set(item) != {"path", "type", "size", "sha256", "mtime_ns"} or item["type"] != "file" for item in closure["files"]):
            raise FreezeValidationError("operational file inventory record shape mismatch")
        if any(set(item) != {"path", "type", "size", "sha256", "mtime_ns"} or item["type"] != "directory" or item["size"] is not None or item["sha256"] is not None for item in closure["directories"]):
            raise FreezeValidationError("operational directory inventory record shape mismatch")
    if record["protected_state"].get("unchanged") is not True or record["protected_state"].get("before") != record["protected_state"].get("after"):
        raise FreezeValidationError("protected state changed during validation")
    if record["trusted_inputs"].get("unchanged") is not True or record["trusted_inputs"].get("before") != record["trusted_inputs"].get("after"):
        raise FreezeValidationError("trusted inputs changed during validation")
    if compare_current:
        if tree["after"] != operational_tree(repo_root / "artifacts/phase-0"):
            raise FreezeValidationError("recorded operational tree differs from current tree")
        if record["protected_state"]["after"] != _protected_state(repo_root):
            raise FreezeValidationError("recorded protected state differs from current state")
        if record["trusted_inputs"]["after"] != trusted_inputs(repo_root):
            raise FreezeValidationError("recorded trusted inputs differ from current state")


def load_and_validate(record_path: Path = RECORD, sidecar_path: Path = SIDECAR, *, compare_current: bool = True) -> dict[str, Any]:
    if not record_path.is_file() or not sidecar_path.is_file():
        raise FreezeValidationError("freeze validation record or sidecar is missing")
    expected = sha256_file(record_path)
    if sidecar_path.read_text(encoding="ascii").strip() != expected:
        raise FreezeValidationError("freeze validation record sidecar mismatch")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    validate_record(record, ROOT, compare_current=compare_current)
    return record


def write_record(record: dict[str, Any]) -> str:
    validate_record(record, ROOT, compare_current=True)
    payload = _canonical_bytes(record)
    digest = _sha256_bytes(payload)
    # Both commits are atomic.  A crash between them leaves a fail-closed
    # sidecar mismatch; no consumer may treat the record alone as PASS.
    _atomic(RECORD, payload)
    _atomic(SIDECAR, (digest + "\n").encode("ascii"))
    load_and_validate()
    return digest


def main() -> int:
    if len(sys.argv) != 1:
        sys.stderr.write('{"status":"FAIL","error":"arguments are forbidden","network_used":false}\n')
        return 2
    try:
        record = collect_validation()
        digest = write_record(record)
        sys.stdout.write(json.dumps({
            "status": "PASS", "record": RECORD.relative_to(ROOT).as_posix(),
            "record_sha256": digest, "evidence_semantics": "actual_observation",
            "observed_test_count": record["full_unittest"]["observed_test_count"],
            "network_used": False,
        }, separators=(",", ":")) + "\n")
        return 0
    except (FreezeValidationError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        sys.stderr.write(json.dumps({"status": "FAIL", "error": str(exc), "network_used": False}, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
