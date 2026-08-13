from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

from .identity import content_id, validate_stable_id
from .ledger import AppendOnlyLedger, StaleLedgerHead
from .serialization import canonical_sha256, load_json
from .storage import IdentityReuseError, resolve_inside, write_once_json


class AlertViolation(ValueError):
    exit_code = 5


SEVERITIES = {"INFO", "WARNING", "ERROR", "CRITICAL"}
TIMER_EXPRESSION = "*-*-* *:0/5:00 Asia/Shanghai"


def build_alert(
    *, severity: str, game: str | None, object_id: str, reason_code: str,
    first_seen: str, last_event_id: str, runbook_ref: str,
) -> dict[str, Any]:
    if severity not in SEVERITIES or game not in {None, "ssq", "dlt"}:
        raise AlertViolation("alert severity or game is invalid")
    for label, value in (("object", object_id), ("reason", reason_code), ("event", last_event_id), ("runbook", runbook_ref)):
        validate_stable_id(value, label)
    body: dict[str, Any] = {
        "schema_version":"1.0.0", "artifact_type":"phase4_alert", "severity":severity, "game":game,
        "object_id":object_id, "reason_code":reason_code, "first_seen":first_seen,
        "last_event_id":last_event_id, "runbook_ref":runbook_ref, "ack_state":"unacknowledged",
    }
    body["alert_id"] = content_id("alert", body)
    return body


def publish_alert(runtime_root: Path, alert: Mapping[str, Any], *, provenance: Mapping[str, Any]) -> bool:
    ledger = AppendOnlyLedger(runtime_root, "alerts")
    for _attempt in range(16):
        state = ledger.validate()
        if ledger.current_view_path.is_file():
            view = load_json(ledger.current_view_path, reject_floats=True)
            existing = view["objects"].get(alert["alert_id"])
            if existing is not None:
                if existing["payload_sha256"] != canonical_sha256(dict(alert)):
                    raise IdentityReuseError("alert identity reused")
                return True
        try:
            ledger.append_event(
                object_id=alert["alert_id"], event_type="alert_opened", event_at_utc=alert["first_seen"],
                payload=alert, producer_provenance=provenance, expected_head_sha256=state["head_sha256"],
            )
            break
        except StaleLedgerHead:
            continue
    else:
        raise AlertViolation("alert ledger head did not stabilize")
    path = resolve_inside(runtime_root, f"alerts/{alert['alert_id']}/alert.json")
    if path.exists():
        if load_json(path, reject_floats=True) != dict(alert):
            raise IdentityReuseError(path)
    else:
        write_once_json(path, dict(alert))
    return False


def _unit_value(text: str, section: str, key: str) -> str | None:
    current = None
    values: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        if current == section and line.startswith(key + "="):
            values.append(line.split("=", 1)[1])
    return values[0] if len(values) == 1 else None


def _writable_target(path: Path) -> bool:
    current = path.resolve(strict=False)
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.is_dir() and os.access(current, os.W_OK | os.X_OK)


def audit_user_scheduler(
    release_root: Path, runtime_root: Path, *,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    release = release_root.resolve(strict=False)
    runtime = runtime_root.resolve(strict=False)
    installed_snapshot = release / "readiness/vps/installed-units"
    unit_root = installed_snapshot if installed_snapshot.is_dir() else release / "deploy/systemd-user"
    service_path = unit_root / "lottery-phase4.service"
    timer_path = unit_root / "lottery-phase4.timer"
    if not service_path.is_file() or not timer_path.is_file():
        raise AlertViolation("systemd user unit templates are missing from the explicit release")
    service_text = service_path.read_text(encoding="utf-8")
    timer_text = timer_path.read_text(encoding="utf-8")
    executable_line = _unit_value(service_text, "Service", "ExecStart")
    working_directory = _unit_value(service_text, "Service", "WorkingDirectory")
    environment_file = _unit_value(service_text, "Service", "EnvironmentFile")
    executable = None if executable_line is None else executable_line.split()[0]
    static = {
        "type_oneshot":_unit_value(service_text, "Service", "Type") == "oneshot",
        "umask_0077":_unit_value(service_text, "Service", "UMask") == "0077",
        "absolute_executable":bool(executable and PurePosixPath(executable).is_absolute()),
        "absolute_working_directory":bool(working_directory and PurePosixPath(working_directory).is_absolute()),
        "absolute_environment_file":bool(environment_file and PurePosixPath(environment_file.removeprefix("-")).is_absolute()),
        "on_calendar":_unit_value(timer_text, "Timer", "OnCalendar") == TIMER_EXPRESSION,
        "persistent":_unit_value(timer_text, "Timer", "Persistent") == "true",
        "accuracy_sec":_unit_value(timer_text, "Timer", "AccuracySec") == "1s",
        "randomized_delay_sec":_unit_value(timer_text, "Timer", "RandomizedDelaySec") == "0",
    }
    commands = [
        ["systemctl", "--user", "is-system-running"],
        ["loginctl", "show-user", os.environ.get("USER", ""), "-p", "Linger", "-p", "State"],
        ["systemd-analyze", "calendar", TIMER_EXPRESSION],
    ]
    results = []
    for argv in commands:
        completed = runner(argv, check=False, capture_output=True, text=True)
        results.append({
            "argv":argv, "exit_code":completed.returncode,
            "stdout":completed.stdout.strip(), "stderr":completed.stderr.strip(),
        })
    linger_match = re.search(r"(?m)^Linger=(yes|no)$", results[1]["stdout"])
    state_match = re.search(r"(?m)^State=([^\n]+)$", results[1]["stdout"])
    capability = {
        "user_manager_query":results[0]["exit_code"] == 0,
        "timer_expression_parse":results[2]["exit_code"] == 0,
        "release_target_writable":_writable_target(release),
        "runtime_target_writable":_writable_target(runtime),
        "no_sudo":all(argv[0] != "sudo" for argv in commands),
        "linger_enabled":bool(linger_match and linger_match.group(1) == "yes"),
    }
    passed = all(static.values()) and all(capability.values())
    return {
        "schema_version":"1.0.0", "artifact_type":"phase4_scheduler_audit",
        "status":"PASS" if passed else "HOLD", "terminal":"PASS" if passed else "HOLD_SCHEDULER_AUDIT",
        "release_root":str(release), "runtime_root":str(runtime),
        "templates":{"service":str(service_path),"timer":str(timer_path),"checks":static},
        "capability":capability,
        "observed":{"linger":None if linger_match is None else linger_match.group(1),"user_state":None if state_match is None else state_match.group(1)},
        "commands":results,
    }
