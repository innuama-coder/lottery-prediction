"""Fixed-path P0-07 replay driver; no caller-controlled arguments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from p0_07_closeout import CloseoutHold, _utc_text, require_prepare_ready, validate_candidate_snapshot
from p0_07_replay_model import build_replay_report, validate_replay_report
from phase0lib import canonical_json_bytes, load_json, sha256_file, validate_schema_instance


REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / "artifacts" / "phase-0"
CANDIDATE = ARTIFACTS / "p0-07-candidate"
STAGING = ARTIFACTS / ".p0-07-replay-staging"
OUTPUT = ARTIFACTS / "p0-07-review-bundle"
LAUNCHER_REF = "scripts/phase0/p0_07_replay_launcher.ps1"
DRIVER_REF = "scripts/phase0/p0_07_replay_driver.py"
WORKER_REF = "scripts/phase0/p0_07_clean_worker.py"
CONSUMER_REF = "scripts/phase0/p0_07_stage1_consumer.py"
MODEL_REF = "scripts/phase0/p0_07_replay_model.py"
CANONICAL_COMMAND = "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/phase0/p0_07_replay_launcher.ps1"
FROZEN_TOOL_REFS = (LAUNCHER_REF, DRIVER_REF, WORKER_REF, CONSUMER_REF, MODEL_REF)
META_FILES = {"content-manifest.json", "execution-receipt.json", "bundle-manifest.json"}


class ChildProcessFailure(ValueError):
    def __init__(self, message: str, stderr: bytes) -> None:
        super().__init__(message)
        self.child_stderr = stderr


def _write(path: Path, value: Any) -> None:
    if path.exists():
        raise ValueError(f"copy-once target exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _copy_once(source: Path, target: Path) -> None:
    if target.exists():
        raise ValueError(f"copy-once target exists: {target}")
    target.write_bytes(source.read_bytes())


def _child_environment() -> dict[str, str]:
    system_root = os.environ.get("SystemRoot")
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if not system_root or not temp:
        raise ValueError("required frozen child environment values are unavailable")
    return {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "SystemRoot": system_root,
        "TEMP": temp,
        "TMP": temp,
        "TZ": "UTC",
    }


def _environment_receipt(environment: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"key": key, "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()}
        for key, value in sorted(environment.items())
    ]


def _verify_frozen_route(verification: dict[str, Any]) -> dict[str, str]:
    if verification.get("command") != CANONICAL_COMMAND or verification.get("full_replay_command") != CANONICAL_COMMAND or verification.get("replay_command") != CANONICAL_COMMAND:
        raise ValueError("frozen canonical command is not the fixed replay launcher")
    records = verification.get("verifier_file_hashes")
    if not isinstance(records, list):
        raise ValueError("frozen verifier inventory is absent")
    by_path = {item.get("path"): item.get("sha256") for item in records if isinstance(item, dict)}
    if len(by_path) != len(records):
        raise ValueError("frozen verifier inventory contains duplicate or malformed paths")
    frozen: dict[str, str] = {}
    for ref in FROZEN_TOOL_REFS:
        expected = by_path.get(ref)
        if not isinstance(expected, str) or sha256_file(REPO / ref) != expected:
            raise ValueError(f"frozen replay tool unavailable or hash mismatch: {ref}")
        frozen[ref] = expected
    return frozen


def _process_record(
    role: str,
    argv: list[str],
    script_ref: str,
    environment: dict[str, str],
    completed: subprocess.CompletedProcess[bytes],
) -> dict[str, Any]:
    stdout_ref = f"process/{role}.stdout"
    stderr_ref = f"process/{role}.stderr"
    (STAGING / stdout_ref).parent.mkdir(parents=True, exist_ok=True)
    (STAGING / stdout_ref).write_bytes(completed.stdout)
    (STAGING / stderr_ref).write_bytes(completed.stderr)
    return {
        "role": role,
        "argv": argv,
        "cwd": str(REPO),
        "environment": _environment_receipt(environment),
        "script_ref": script_ref,
        "script_sha256": sha256_file(REPO / script_ref),
        "stdout_ref": stdout_ref,
        "stdout_size": len(completed.stdout),
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderr_ref": stderr_ref,
        "stderr_size": len(completed.stderr),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
        "exit_code": completed.returncode,
    }


def _file_records(paths: list[Path], root: Path) -> list[dict[str, Any]]:
    return [
        {"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(paths)
    ]


def _validate_child_stdout(completed: subprocess.CompletedProcess[bytes], *, expected_key: str, expected_value: str) -> None:
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except Exception as exc:
        raise ValueError("child stdout is not one canonical JSON record") from exc
    if completed.stdout != canonical_json_bytes(value) + b"\n" or value != {"status": "PASS", expected_key: expected_value}:
        raise ValueError("child stdout declaration mismatch")


def _derive_fixture_semantics(fixture: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    games = fixture.get("game_results")
    if not isinstance(games, list) or [item.get("game") for item in games] != ["dlt", "ssq"]:
        raise ValueError("fixture game declarations are not exact")
    outcomes = []
    for item in games:
        outcome = item.get("per_game_outcome")
        coverage = item.get("coverage_tier")
        if outcome not in {"PASS_FULL", "PASS_LIMITED", "HOLD", "STOP"} or coverage not in {"target", "minimum_viable", "none"}:
            raise ValueError("fixture outcome or coverage tier invalid")
        if outcome == "PASS_FULL" and coverage != "target":
            raise ValueError("fixture PASS_FULL requires target coverage")
        if outcome == "PASS_LIMITED" and coverage != "minimum_viable":
            raise ValueError("fixture PASS_LIMITED requires minimum_viable coverage")
        counts = item.get("corroboration_counts")
        if not isinstance(counts, list) or [entry.get("tier") for entry in counts] != ["corroborated_official", "shared_upstream", "primary_only"]:
            raise ValueError("fixture corroboration counts are not exact")
        if any(type(entry.get("count")) is not int or entry["count"] < 0 for entry in counts):
            raise ValueError("fixture corroboration counts invalid")
        by_tier = {entry["tier"]: entry["count"] for entry in counts}
        tier = "none" if sum(by_tier.values()) == 0 else "primary_only" if by_tier["primary_only"] else "shared_upstream" if by_tier["shared_upstream"] else "corroborated_official"
        if item.get("corroboration_tier") != tier:
            raise ValueError("fixture corroboration tier differs from counts")
        outcomes.append(outcome)
    if all(outcome == "PASS_FULL" for outcome in outcomes):
        decision = "GO"
    elif any(outcome in {"PASS_FULL", "PASS_LIMITED"} for outcome in outcomes):
        decision = "LIMITED_GO"
    elif any(outcome == "HOLD" for outcome in outcomes):
        decision = "HOLD"
    elif all(outcome == "STOP" for outcome in outcomes):
        decision = "STOP"
    else:
        raise ValueError("fixture per-game outcomes have no project decision")
    active = [item["game"] for item in games if item["per_game_outcome"] in {"PASS_FULL", "PASS_LIMITED"}]
    excluded = [item["game"] for item in games if item["per_game_outcome"] not in {"PASS_FULL", "PASS_LIMITED"}]
    if fixture.get("project_decision") != decision:
        raise ValueError("fixture project decision differs from outcomes")
    if fixture.get("active_games") != active or fixture.get("excluded_games") != excluded:
        raise ValueError("fixture active/excluded partition differs from outcomes")
    return decision, active, excluded


def validate_bundle(
    bundle: Path, *, repo_root: Path | None = None, artifacts: Path | None = None,
    staging: Path | None = None,
) -> None:
    repo_root = REPO if repo_root is None else repo_root.resolve()
    artifacts = ARTIFACTS if artifacts is None else artifacts.resolve()
    staging = STAGING if staging is None else staging.resolve()
    root_manifest = load_json(bundle / "bundle-manifest.json")
    validate_schema_instance(root_manifest, load_json(artifacts / "schemas" / "p0-07-bundle-manifest.schema.json"))
    root_paths = [bundle / "content-manifest.json", bundle / "execution-receipt.json"]
    if root_manifest["files"] != _file_records(root_paths, bundle):
        raise ValueError("root manifest does not bind exactly content manifest plus execution receipt")

    content_manifest = load_json(bundle / "content-manifest.json")
    validate_schema_instance(content_manifest, load_json(artifacts / "schemas" / "p0-07-content-manifest.schema.json"))
    content_paths = [
        path for path in bundle.rglob("*")
        if path.is_file() and path.relative_to(bundle).as_posix() not in META_FILES
    ]
    if content_manifest["files"] != _file_records(content_paths, bundle):
        raise ValueError("content manifest is not the exact pre-receipt content closure")
    logical = load_json(bundle / "worker" / "logical-ref-index.json")
    expected_refs = [{**record, "bundle_path": f"worker/{record['bundle_path']}"} for _, record in sorted(logical.items())]
    if content_manifest["logical_refs"] != expected_refs:
        raise ValueError("content manifest logical refs differ from worker index")
    for record in content_manifest["logical_refs"]:
        path = bundle / record["bundle_path"]
        if path.stat().st_size != record["size"] or sha256_file(path) != record["sha256"]:
            raise ValueError(f"logical ref content mismatch: {record['logical_ref']}")

    execution = load_json(bundle / "execution-receipt.json")
    validate_schema_instance(execution, load_json(artifacts / "schemas" / "p0-07-execution-receipt.schema.json"))
    if execution["canonical_command"] != CANONICAL_COMMAND or execution["canonical_command_sha256"] != hashlib.sha256(CANONICAL_COMMAND.encode("utf-8")).hexdigest():
        raise ValueError("execution receipt canonical command binding mismatch")
    verification_path = artifacts / "verification-command.json"
    if execution["verification_command_file_sha256"] != sha256_file(verification_path):
        raise ValueError("execution receipt verification-command file binding mismatch")
    if execution["launcher_sha256"] != sha256_file(repo_root / LAUNCHER_REF) or execution["driver_sha256"] != sha256_file(repo_root / DRIVER_REF):
        raise ValueError("execution receipt launcher/driver binding mismatch")
    interpreter = Path(execution["interpreter_path"])
    if not interpreter.is_file() or execution["interpreter_sha256"] != sha256_file(interpreter):
        raise ValueError("execution receipt interpreter binding mismatch")
    started = datetime.fromisoformat(execution["started_at_utc"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(execution["completed_at_utc"].replace("Z", "+00:00"))
    if started.tzinfo is None or completed < started:
        raise ValueError("execution receipt time interval is invalid")
    expected_environment = _environment_receipt(_child_environment())
    expected_processes = (
        ("clean_replay_worker", WORKER_REF, "facts_sha256", sha256_file(bundle / "worker" / "facts.json")),
        ("stage1_external_consumer", CONSUMER_REF, "receipt", (staging / "consumer" / "p0-07-stage1-consumer-receipt.json").relative_to(repo_root).as_posix()),
    )
    for process, (role, script_ref, stdout_key, stdout_value) in zip(execution["processes"], expected_processes, strict=True):
        if process["role"] != role or process["argv"] != [execution["interpreter_path"], script_ref] or process["cwd"] != str(repo_root):
            raise ValueError(f"execution receipt process route mismatch: {role}")
        if process["environment"] != expected_environment or process["script_sha256"] != sha256_file(repo_root / script_ref):
            raise ValueError(f"execution receipt process environment/script mismatch: {role}")
        stdout = bundle / process["stdout_ref"]
        stderr = bundle / process["stderr_ref"]
        for path, size_field, hash_field in (
            (stdout, "stdout_size", "stdout_sha256"), (stderr, "stderr_size", "stderr_sha256"),
        ):
            if path.stat().st_size != process[size_field] or sha256_file(path) != process[hash_field]:
                raise ValueError(f"execution receipt process log mismatch: {role}")
        _validate_child_stdout(
            subprocess.CompletedProcess(process["argv"], 0, stdout=stdout.read_bytes(), stderr=stderr.read_bytes()),
            expected_key=stdout_key,
            expected_value=stdout_value,
        )
    bindings = {
        "content_manifest_sha256": bundle / "content-manifest.json",
        "technical_report_sha256": bundle / "technical-replay-report.json",
        "stage1_consumer_receipt_sha256": bundle / "p0-07-stage1-consumer-receipt.json",
        "proposed_handoff_file_bytes_sha256": bundle / "proposed-stage1-handoff-fixture.json",
    }
    for field, path in bindings.items():
        if execution[field] != sha256_file(path):
            raise ValueError(f"execution receipt binding mismatch: {field}")
    if (bundle / "proposed-stage1-handoff-fixture.json").read_bytes() != (bundle / "worker" / "proposed-stage1-handoff-fixture.json").read_bytes():
        raise ValueError("promoted fixture is not the exact worker fixture bytes")
    if (bundle / "p0-07-stage1-consumer-receipt.json").read_bytes() != (bundle / "consumer" / "p0-07-stage1-consumer-receipt.json").read_bytes():
        raise ValueError("promoted consumer receipt is not the exact consumer receipt bytes")
    consumer_receipt_bytes = (bundle / "p0-07-stage1-consumer-receipt.json").read_bytes()
    consumer_receipt = load_json(bundle / "p0-07-stage1-consumer-receipt.json")
    validate_schema_instance(consumer_receipt, load_json(artifacts / "schemas" / "p0-07-stage1-consumer-receipt.schema.json"))
    if consumer_receipt["consumed_fixture_file_bytes_sha256"] != sha256_file(bundle / "proposed-stage1-handoff-fixture.json"):
        raise ValueError("consumer receipt does not bind exact promoted fixture bytes")
    fixture = load_json(bundle / "proposed-stage1-handoff-fixture.json")
    derived_decision, derived_active, derived_excluded = _derive_fixture_semantics(fixture)
    if (consumer_receipt["project_decision"], consumer_receipt["active_games"], consumer_receipt["excluded_games"]) != (derived_decision, derived_active, derived_excluded):
        raise ValueError("consumer receipt differs from independently derived fixture semantics")
    replay = load_json(bundle / "technical-replay-report.json")
    if replay["started_at_utc"] != execution["started_at_utc"] or datetime.fromisoformat(replay["completed_at_utc"].replace("Z", "+00:00")) > completed:
        raise ValueError("replay report time interval is outside execution receipt")
    replay_args = {
        "started_at_utc": replay["started_at_utc"],
        "completed_at_utc": replay["completed_at_utc"],
        "verification_command_sha256": execution["verification_command_file_sha256"],
        "consumer_receipt_bytes": consumer_receipt_bytes,
    }
    validate_replay_report(replay, load_json(bundle / "worker" / "facts.json"), **replay_args)
    validate_schema_instance(replay, load_json(artifacts / "schemas" / "technical-replay-report.schema.json"))


def run_driver(
    *,
    utcnow_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    run_fn: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Path:
    started = utcnow_fn()
    if started.tzinfo is None:
        raise ValueError("driver clock must be timezone-aware")
    if OUTPUT.exists() and (not OUTPUT.is_dir() or any(OUTPUT.iterdir())):
        raise ValueError("canonical review bundle is non-empty")
    if STAGING.exists():
        raise ValueError("stale replay staging directory exists")
    # Every precondition is checked before the first staging write.
    require_prepare_ready(REPO, ARTIFACTS, utcnow_fn=lambda: started)
    validate_candidate_snapshot(REPO, ARTIFACTS, CANDIDATE, now=started)
    verification_path = ARTIFACTS / "verification-command.json"
    verification = load_json(verification_path)
    verification_file_sha256 = sha256_file(verification_path)
    frozen_tools = _verify_frozen_route(verification)
    interpreter = Path(verification["interpreter_path"])
    if not interpreter.is_file() or sha256_file(interpreter) != verification["interpreter_sha256"]:
        raise ValueError("frozen interpreter unavailable or hash mismatch")
    environment = _child_environment()

    STAGING.mkdir()
    try:
        processes = []
        worker_argv = [str(interpreter), WORKER_REF]
        worker = run_fn(worker_argv, cwd=REPO, capture_output=True, check=False, timeout=120, env=environment)
        processes.append(_process_record("clean_replay_worker", worker_argv, WORKER_REF, environment, worker))
        if worker.returncode != 0:
            raise ChildProcessFailure("clean replay worker failed", worker.stderr)
        _validate_child_stdout(worker, expected_key="facts_sha256", expected_value=sha256_file(STAGING / "worker" / "facts.json"))

        consumer_argv = [str(interpreter), CONSUMER_REF]
        consumer = run_fn(consumer_argv, cwd=REPO, capture_output=True, check=False, timeout=120, env=environment)
        processes.append(_process_record("stage1_external_consumer", consumer_argv, CONSUMER_REF, environment, consumer))
        if consumer.returncode != 0:
            raise ChildProcessFailure("external Stage 1 consumer failed", consumer.stderr)
        receipt_path = STAGING / "consumer" / "p0-07-stage1-consumer-receipt.json"
        fixture_path = STAGING / "worker" / "proposed-stage1-handoff-fixture.json"
        _validate_child_stdout(consumer, expected_key="receipt", expected_value=receipt_path.relative_to(REPO).as_posix())

        facts = load_json(STAGING / "worker" / "facts.json")
        receipt_bytes = receipt_path.read_bytes()
        receipt = load_json(receipt_path)
        validate_schema_instance(receipt, load_json(ARTIFACTS / "schemas" / "p0-07-stage1-consumer-receipt.schema.json"))
        if receipt["consumed_fixture_file_bytes_sha256"] != sha256_file(fixture_path):
            raise ValueError("consumer receipt is not bound to exact fixture file bytes")
        fixture = load_json(fixture_path)
        derived_decision, derived_active, derived_excluded = _derive_fixture_semantics(fixture)
        if (receipt["project_decision"], receipt["active_games"], receipt["excluded_games"]) != (derived_decision, derived_active, derived_excluded):
            raise ValueError("consumer receipt decision partition mismatch")

        replay_completed = utcnow_fn()
        if replay_completed.tzinfo is None or replay_completed < started:
            raise ValueError("replay completion clock invalid")
        builder_args = {
            "started_at_utc": _utc_text(started),
            "completed_at_utc": _utc_text(replay_completed),
            "verification_command_sha256": verification_file_sha256,
            "consumer_receipt_bytes": receipt_bytes,
        }
        report = build_replay_report(facts, **builder_args)
        validate_replay_report(report, facts, **builder_args)
        validate_schema_instance(report, load_json(ARTIFACTS / "schemas" / "technical-replay-report.schema.json"))
        _write(STAGING / "technical-replay-report.json", report)
        _copy_once(fixture_path, STAGING / "proposed-stage1-handoff-fixture.json")
        _copy_once(receipt_path, STAGING / "p0-07-stage1-consumer-receipt.json")

        logical_index = load_json(STAGING / "worker" / "logical-ref-index.json")
        logical_refs = [{**record, "bundle_path": f"worker/{record['bundle_path']}"} for _, record in sorted(logical_index.items())]
        content_paths = [path for path in STAGING.rglob("*") if path.is_file() and path.relative_to(STAGING).as_posix() not in META_FILES]
        content_manifest = {
            "schema_version": "1.0.0",
            "artifact_type": "p0_07_content_manifest",
            "contract_version": "1.3",
            "manifest_excludes": ["content-manifest.json", "execution-receipt.json", "bundle-manifest.json"],
            "files": _file_records(content_paths, STAGING),
            "logical_refs": logical_refs,
        }
        validate_schema_instance(content_manifest, load_json(ARTIFACTS / "schemas" / "p0-07-content-manifest.schema.json"))
        _write(STAGING / "content-manifest.json", content_manifest)

        completed = utcnow_fn()
        if completed.tzinfo is None or completed < replay_completed:
            raise ValueError("driver completion clock invalid")
        # Detect any trusted-tool mutation while the replay was running.
        if any(sha256_file(REPO / ref) != expected for ref, expected in frozen_tools.items()):
            raise ValueError("frozen replay tool changed during execution")
        if sha256_file(verification_path) != verification_file_sha256:
            raise ValueError("frozen canonical command file changed during execution")
        if sha256_file(interpreter) != verification["interpreter_sha256"]:
            raise ValueError("frozen interpreter changed during execution")
        execution = {
            "schema_version": "1.1.0",
            "artifact_type": "p0_07_execution_receipt",
            "contract_version": "1.3",
            "driver_role": "fixed_replay_driver",
            "launcher_ref": LAUNCHER_REF,
            "launcher_sha256": frozen_tools[LAUNCHER_REF],
            "canonical_command_ref": "artifacts/phase-0/verification-command.json",
            "canonical_command": CANONICAL_COMMAND,
            "canonical_command_sha256": hashlib.sha256(CANONICAL_COMMAND.encode("utf-8")).hexdigest(),
            "verification_command_file_sha256": verification_file_sha256,
            "driver_ref": DRIVER_REF,
            "driver_sha256": frozen_tools[DRIVER_REF],
            "started_at_utc": _utc_text(started),
            "completed_at_utc": _utc_text(completed),
            "cwd": str(REPO),
            "interpreter_path": str(interpreter),
            "interpreter_sha256": verification["interpreter_sha256"],
            "content_manifest_sha256": sha256_file(STAGING / "content-manifest.json"),
            "technical_report_sha256": sha256_file(STAGING / "technical-replay-report.json"),
            "stage1_consumer_receipt_sha256": sha256_file(STAGING / "p0-07-stage1-consumer-receipt.json"),
            "proposed_handoff_file_bytes_sha256": sha256_file(STAGING / "proposed-stage1-handoff-fixture.json"),
            "processes": processes,
        }
        validate_schema_instance(execution, load_json(ARTIFACTS / "schemas" / "p0-07-execution-receipt.schema.json"))
        _write(STAGING / "execution-receipt.json", execution)

        root_paths = [STAGING / "content-manifest.json", STAGING / "execution-receipt.json"]
        root_manifest = {
            "schema_version": "1.1.0",
            "artifact_type": "p0_07_bundle_manifest",
            "contract_version": "1.3",
            "manifest_excludes_itself": True,
            "files": _file_records(root_paths, STAGING),
        }
        validate_schema_instance(root_manifest, load_json(ARTIFACTS / "schemas" / "p0-07-bundle-manifest.schema.json"))
        _write(STAGING / "bundle-manifest.json", root_manifest)
        validate_bundle(STAGING)
        if OUTPUT.exists():
            OUTPUT.rmdir()
        STAGING.replace(OUTPUT)
        return OUTPUT / "technical-replay-report.json"
    except Exception:
        if STAGING.exists():
            shutil.rmtree(STAGING)
        raise


def main() -> int:
    if len(sys.argv) != 1:
        sys.stderr.write('{"status":"FAIL","error":"arguments are forbidden"}\n')
        return 2
    try:
        path = run_driver()
        sys.stdout.buffer.write(canonical_json_bytes({"status": "PASS", "report": path.relative_to(REPO).as_posix()}) + b"\n")
        return 0
    except ChildProcessFailure as exc:
        if exc.child_stderr:
            sys.stderr.buffer.write(exc.child_stderr)
        sys.stderr.buffer.write(canonical_json_bytes({"status": "FAIL", "error": str(exc)}) + b"\n")
        return 2
    except CloseoutHold as exc:
        sys.stderr.buffer.write(canonical_json_bytes({"status": "HOLD", "error": str(exc), "network_used": False}) + b"\n")
        return 1
    except Exception as exc:
        sys.stderr.buffer.write(canonical_json_bytes({"status": "FAIL", "error": str(exc)}) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
