from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from lottery_data import cli
from lottery_data.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_file
from lottery_data.steps.live_policy import LivePolicyError
from lottery_data.steps.locking import OSFileLock
from lottery_data.steps.preflight import BootstrapArguments, IncrementalArguments
from lottery_data.workflow import execute_bootstrap, execute_incremental
import lottery_data.workflow as workflow_module


REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "artifacts/phase-0-multisource/snapshots/20260802T025000Z"
CONFIG = REPO / "config/phase1"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
# The READY marker follows a complete snapshot bootstrap through durable
# POINTER_COMMITTED. Keep this as an acceptance-load watchdog, not a unit wait.
POINTER_COMMITTED_READY_BUDGET_SECONDS = 120.0


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _resign_raw(snapshot: Path, raw_ref: str) -> None:
    raw = snapshot / raw_ref
    capture_path = snapshot / "capture-manifest.jsonl"
    capture = _jsonl(capture_path)
    target = next(item for item in capture if item["raw_ref"] == raw_ref)
    target.update({"raw_sha256": sha256_file(raw), "content_length": raw.stat().st_size})
    capture_path.write_bytes(b"".join(canonical_json_bytes(row) for row in capture))
    events_path = snapshot / "request-events.jsonl"
    events = _jsonl(events_path)
    terminal = next(item for item in events if item.get("event") == "request_succeeded" and item.get("request_id") == target["request_id"])
    terminal.update({"raw_sha256": target["raw_sha256"], "content_length": target["content_length"]})
    events_path.write_bytes(b"".join(canonical_json_bytes(row) for row in events))
    hashes_path = snapshot / "artifact-hashes.json"
    hashes = _json(hashes_path)
    hashes.update({"capture-manifest.jsonl": sha256_file(capture_path), "request-events.jsonl": sha256_file(events_path)})
    _write_json(hashes_path, hashes)


def _run_closure(root: Path, run_id: str) -> tuple[bool, int]:
    run = root / "runs" / run_id
    if not run.is_dir():
        return True, 0
    events = _jsonl(run / "events.jsonl")
    terminals = {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
    terminal_count = sum(item["event_type"] in terminals for item in events)
    closed = terminal_count == 1 and events[-1]["event_type"] in {"run_rejected", "run_interrupted"}
    for started in (item for item in events if item["event_type"] == "request_started"):
        closed &= sum(row.get("request_id") == started["request_id"] and row["event_type"] in {"request_succeeded", "request_failed"} for row in events) == 1
    return bool(closed), terminal_count


def _publication_snapshot(root: Path) -> tuple[bytes | None, frozenset[str]]:
    pointer = root / "current-release.json"
    releases = root / "releases"
    return (
        pointer.read_bytes() if pointer.is_file() else None,
        frozenset(path.name for path in releases.iterdir() if path.is_dir()) if releases.is_dir() else frozenset(),
    )


def _outcome(
    name: str, expected: int | None, actual: int | None, root: Path, run_id: str,
    *, pointer_before: bytes | None, pointer_after: bytes | None,
    releases_before: frozenset[str], releases_after: frozenset[str],
    recovered: bool = False, idempotent: bool = False, stdout: bytes = b"", stderr: bytes = b"",
) -> dict:
    closed, terminals = _run_closure(root, run_id)
    return {
        "fault": name, "expected_exit": expected, "actual_exit": actual,
        "request_terminal_closed": closed, "terminal_count": terminals,
        "release_created": bool(releases_after - releases_before),
        "release_names_before": releases_before, "release_names_after": releases_after,
        "pointer_before": pointer_before, "pointer_after": pointer_after,
        "recovered": recovered, "recovery_idempotent": idempotent,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(), "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
    }


def wait_for_ready_then_kill(
    process: subprocess.Popen[str], *,
    timeout_seconds: float = POINTER_COMMITTED_READY_BUDGET_SECONDS,
) -> None:
    messages: queue.Queue[tuple[str, str | BaseException | None]] = queue.Queue()

    def read_one(label: str, stream: object) -> None:
        try:
            line = stream.readline()  # type: ignore[attr-defined]
            messages.put((label, line if line else None))
        except BaseException as exc:
            messages.put((label, exc))

    if process.stdout is None or process.stderr is None:
        raise AssertionError("fault driver pipes were not created")
    readers = [
        threading.Thread(target=read_one, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_one, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    ready = False
    failure: str | None = None
    try:
        while not ready:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timed out waiting for durable READY marker"
                break
            try:
                label, value = messages.get(timeout=remaining)
            except queue.Empty:
                failure = "timed out waiting for durable READY marker"
                break
            if isinstance(value, BaseException):
                failure = f"{label} reader failed: {value}"
            elif label == "stderr" and value:
                failure = f"fault driver wrote stderr before READY: {value!r}"
            elif label == "stdout" and value is None:
                failure = "fault driver stdout reached EOF before READY"
            elif label == "stdout" and str(value).strip() == "READY POINTER_COMMITTED":
                ready = True
            elif label == "stdout":
                failure = f"unexpected fault driver stdout: {value!r}"
            if failure:
                break
    finally:
        if process.poll() is None:
            process.kill()
        try:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            for reader in readers:
                reader.join(timeout=1)
            while True:
                try:
                    label, value = messages.get_nowait()
                except queue.Empty:
                    break
                if label == "stderr" and value and failure is None:
                    failure = f"fault driver wrote stderr: {value!r}"
                if isinstance(value, BaseException) and failure is None:
                    failure = f"{label} reader failed: {value}"
        finally:
            process.stdout.close()
            process.stderr.close()
    if failure:
        raise AssertionError(failure)
    if not ready:
        raise AssertionError("fault driver did not reach READY")


def _trigger_recovery(root: Path, missing: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([
        sys.executable, "-m", "lottery_data", "run", "--mode", "bootstrap", "--source-mode", "snapshot",
        "--phase0-snapshot", str(missing), "--artifacts-root", str(root), "--config-root", str(CONFIG),
        "--run-id", "e2e07-recovery-trigger", "--release-id", "unused",
    ], cwd=REPO, env=env, capture_output=True, text=True, timeout=30, check=False)


def execute_fault_matrix() -> list[dict]:
    outcomes: list[dict] = []
    for fault in ("source_conflict", "truncated_html", "wrong_encoding"):
        with tempfile.TemporaryDirectory(prefix=f"e2e07-{fault}-") as directory:
            base = Path(directory); snapshot = base / SNAPSHOT.name; shutil.copytree(SNAPSHOT, snapshot)
            raw_ref = "raw/eastmoney/ssq/page-001.html"; raw = snapshot / raw_ref
            if fault == "source_conflict":
                text = raw.read_text(encoding="utf-8-sig"); issue = text.index("id=2026085"); ball = text.index(">06</span>", issue)
                raw.write_text(text[:ball] + ">07</span>" + text[ball + len(">06</span>"):], encoding="utf-8")
            elif fault == "truncated_html": raw.write_bytes(b"<html><span id=2026085><")
            else: raw.write_bytes(b"\xff\xfe\x00\x81wrong-encoding")
            _resign_raw(snapshot, raw_ref)
            root = base / "artifacts"; run_id = f"fault-{fault}"; release_id = f"release-{fault}"
            pointer_before, releases_before = _publication_snapshot(root)
            code, result = execute_bootstrap(BootstrapArguments("bootstrap", "snapshot", snapshot, root, CONFIG, run_id, release_id))
            pointer_after, releases_after = _publication_snapshot(root)
            outcomes.append(_outcome(fault, 2, code, root, run_id, pointer_before=pointer_before, pointer_after=pointer_after, releases_before=releases_before, releases_after=releases_after, stdout=canonical_json_bytes(result)))

    with tempfile.TemporaryDirectory(prefix="e2e07-raw-") as directory:
        base = Path(directory); snapshot = base / SNAPSHOT.name; shutil.copytree(SNAPSHOT, snapshot)
        (snapshot / "raw/eastmoney/ssq/page-001.html").write_bytes(b"tamper"); root = base / "artifacts"
        pointer_before, releases_before = _publication_snapshot(root)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["run", "--mode", "bootstrap", "--source-mode", "snapshot", "--phase0-snapshot", str(snapshot), "--artifacts-root", str(root), "--config-root", str(CONFIG), "--run-id", "raw-tamper", "--release-id", "raw-release"])
        pointer_after, releases_after = _publication_snapshot(root)
        outcomes.append(_outcome("raw_hash_mismatch", 5, code, root, "raw-tamper", pointer_before=pointer_before, pointer_after=pointer_after, releases_before=releases_before, releases_after=releases_after, stdout=stdout.getvalue().encode(), stderr=stderr.getvalue().encode()))

    with tempfile.TemporaryDirectory(prefix="e2e07-config-") as directory:
        base = Path(directory); config = base / "config"; config.mkdir(); root = base / "artifacts"
        (config / "live-source-policy.json").write_bytes((CONFIG / "live-source-policy.json").read_bytes() + b"\n")
        pointer_before, releases_before = _publication_snapshot(root)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["run", "--mode", "incremental", "--source-mode", "live", "--artifacts-root", str(root), "--config-root", str(config), "--run-id", "bad-config", "--release-id", "unused"])
        pointer_after, releases_after = _publication_snapshot(root)
        outcomes.append(_outcome("invalid_configuration", 4, code, root, "bad-config", pointer_before=pointer_before, pointer_after=pointer_after, releases_before=releases_before, releases_after=releases_after, stdout=stdout.getvalue().encode(), stderr=stderr.getvalue().encode()))

    with tempfile.TemporaryDirectory(prefix="e2e07-network-") as directory:
        root = Path(directory) / "artifacts"; shutil.copytree(REPO / "artifacts/phase-1", root); pointer_before, releases_before = _publication_snapshot(root)
        failure = LivePolicyError("network_failure", "controlled network failure", stage="runtime", exit_code=3)
        with patch("lottery_data.steps.live.fetch_to_raw", side_effect=failure):
            code, result = execute_incremental(IncrementalArguments("incremental", "live", None, root, CONFIG, "network-failure", "network-release"))
        pointer_after, releases_after = _publication_snapshot(root)
        outcomes.append(_outcome("network_failure", 3, code, root, "network-failure", pointer_before=pointer_before, pointer_after=pointer_after, releases_before=releases_before, releases_after=releases_after, stdout=canonical_json_bytes(result)))

    for fault in ("publish_lock", "compare_and_swap"):
        with tempfile.TemporaryDirectory(prefix=f"e2e07-{fault}-") as directory:
            root = Path(directory) / "artifacts"; run_id = fault; release_id = f"{fault}-release"
            args = BootstrapArguments("bootstrap", "snapshot", SNAPSHOT, root, CONFIG, run_id, release_id)
            original = workflow_module.publish_release
            pointer_before, releases_before = _publication_snapshot(root)
            if fault == "publish_lock":
                def injected(**kwargs):
                    with OSFileLock(root / ".publish.lock"): return original(**kwargs)
            else:
                barrier = canonical_json_bytes({"pointer_schema_version":"1.0.0","release_id":"third-party","manifest_ref":"releases/third-party/manifest.json","manifest_sha256":"0"*64,"updated_at_utc":"2026-08-03T00:00:00Z","updated_by_run_id":"third-party"})
                def injected(**kwargs):
                    nonlocal_pointer[0] = barrier
                    (root / "current-release.json").write_bytes(barrier)
                    nonlocal_releases[0] = _publication_snapshot(root)[1]
                    return original(**kwargs)
                nonlocal_pointer = [pointer_before]
                nonlocal_releases = [releases_before]
            with patch.object(workflow_module, "publish_release", side_effect=injected): code, result = execute_bootstrap(args)
            if fault == "compare_and_swap":
                pointer_before, releases_before = nonlocal_pointer[0], nonlocal_releases[0]
            pointer_after, releases_after = _publication_snapshot(root)
            outcomes.append(_outcome(fault, 6, code, root, run_id, pointer_before=pointer_before, pointer_after=pointer_after, releases_before=releases_before, releases_after=releases_after, stdout=canonical_json_bytes(result)))

    with tempfile.TemporaryDirectory(prefix="e2e07-crash-") as directory:
        base = Path(directory); root = base / "artifacts"; marker = base / "ready.marker"; env = dict(os.environ); env["PYTHONPATH"] = str(REPO / "src")
        pointer_before, releases_before = _publication_snapshot(root)
        process = subprocess.Popen([sys.executable, "-m", "tests.phase1.e2e_fault_driver", "--artifacts-root", str(root), "--snapshot", str(SNAPSHOT), "--config", str(CONFIG), "--marker", str(marker)], cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        wait_for_ready_then_kill(process)
        first = _trigger_recovery(root, base / "missing" / SNAPSHOT.name, env)
        run = root / "runs/e2e07-crash"; before = {p.relative_to(run).as_posix(): sha256_file(p) for p in run.rglob("*") if p.is_file()}
        second = _trigger_recovery(root, base / "missing" / SNAPSHOT.name, env)
        after = {p.relative_to(run).as_posix(): sha256_file(p) for p in run.rglob("*") if p.is_file()}
        result = _json(run / "run-result.json")
        recovered = result.get("status") == "interrupted" and result.get("exit_code") is None and not (root / "current-release.json").exists()
        pointer_after, releases_after = _publication_snapshot(root)
        outcomes.append(_outcome("forced_process_termination", None, None, root, "e2e07-crash", pointer_before=pointer_before, pointer_after=pointer_after, releases_before=releases_before, releases_after=releases_after, recovered=recovered, idempotent=first.returncode == second.returncode == 2 and first.stdout == second.stdout and first.stderr == second.stderr and before == after, stdout=(first.stdout + second.stdout).encode(), stderr=(first.stderr + second.stderr).encode()))
    return outcomes
