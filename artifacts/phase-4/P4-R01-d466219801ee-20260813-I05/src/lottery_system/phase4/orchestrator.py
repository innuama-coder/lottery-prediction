from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .alerts import build_alert, publish_alert
from .identity import content_id
from .ledger import AppendOnlyLedger, StaleLedgerHead
from .recovery import STAGES, CheckpointViolation, build_checkpoint, validate_checkpoint
from .serialization import canonical_sha256, load_json, sha256_file
from .storage import AdvisoryFileLock, IdentityReuseError, LockUnavailable, resolve_inside, write_once_json


class OrchestrationViolation(ValueError):
    exit_code = 5


class ProcessInterruption(RuntimeError):
    exit_code = 20


ZERO_SHA256 = "0" * 64


def _append_current(
    ledger: AppendOnlyLedger, *, object_id: str, event_type: str, event_at_utc: str,
    payload: Mapping[str, Any], producer_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Append after selecting the head, retrying only a concurrent sibling commit."""
    for _attempt in range(16):
        state = ledger.validate()
        try:
            return ledger.append_event(
                object_id=object_id, event_type=event_type, event_at_utc=event_at_utc,
                payload=payload, producer_provenance=producer_provenance,
                expected_head_sha256=state["head_sha256"],
            )
        except StaleLedgerHead:
            continue
    raise OrchestrationViolation("ledger head did not stabilize during concurrent plan commits")


def _write_same(path: Path, value: Mapping[str, Any]) -> bool:
    if path.is_file():
        if load_json(path, reject_floats=True) != dict(value):
            raise IdentityReuseError(path)
        return True
    write_once_json(path, dict(value))
    return False


def _accepted(
    root: Path, path: str, expected_sha256: str, task_id: str, *,
    receipt_path: str, receipt_sha256: str,
) -> None:
    candidate = resolve_inside(root, path)
    if not candidate.is_file() or sha256_file(candidate) != expected_sha256:
        raise OrchestrationViolation(f"accepted {task_id} identity is missing or changed")
    value = load_json(candidate, reject_floats=True)
    if value.get("status") != "PASS" or value.get("task_id") != task_id:
        raise OrchestrationViolation(f"accepted {task_id} verdict is not PASS")
    binding = value.get("validated_receipt")
    if not isinstance(binding, Mapping) or binding.get("path") != receipt_path or binding.get("sha256") != receipt_sha256:
        raise OrchestrationViolation(f"accepted {task_id} verdict is not bound to the supplied receipt")


def validate_correction_dependencies(project_root: Path, correction: Mapping[str, Any]) -> None:
    required = {"correction_key","t06_receipt_path","t06_receipt_sha256","t06_verdict_path","t06_verdict_sha256","t07_receipt_path","t07_receipt_sha256","t07_verdict_path","t07_verdict_sha256"}
    if set(correction) != required or not isinstance(correction["correction_key"], list) or len(correction["correction_key"]) != 4:
        raise OrchestrationViolation("correction dependency closure shape is invalid")
    for task in ("t06", "t07"):
        receipt_path = resolve_inside(project_root, correction[f"{task}_receipt_path"])
        if not receipt_path.is_file() or sha256_file(receipt_path) != correction[f"{task}_receipt_sha256"]:
            raise OrchestrationViolation(f"{task.upper()} correction-side receipt identity mismatch")
        receipt = load_json(receipt_path, reject_floats=True)
        expected_task = task.upper()
        if receipt.get("task_id") != expected_task or receipt.get("status") != "PASS":
            raise OrchestrationViolation(f"{expected_task} correction-side receipt is not PASS")
        _accepted(
            project_root, correction[f"{task}_verdict_path"], correction[f"{task}_verdict_sha256"], expected_task,
            receipt_path=correction[f"{task}_receipt_path"], receipt_sha256=correction[f"{task}_receipt_sha256"],
        )


def _validate_terminal(
    runtime_root: Path, terminal: Mapping[str, Any], *,
    plan_id: str, run_id: str, plan_key: Sequence[str],
) -> None:
    required = {"schema_version","artifact_type","plan_id","run_id","plan_key","terminal","completed_at_utc","effect_ids","correction_closure_id","alert_ids","idempotent"}
    if set(terminal) != required or terminal.get("schema_version") != "1.0.0" or terminal.get("artifact_type") != "phase4_plan_terminal":
        raise OrchestrationViolation("plan terminal shape is invalid")
    if terminal["plan_id"] != plan_id or terminal["run_id"] != run_id or terminal["plan_key"] != list(plan_key):
        raise OrchestrationViolation("plan terminal identity mismatch")
    if terminal["terminal"] not in {"succeeded","late_completed","missed_deadline","blocked","failed"} or terminal["idempotent"] is not False:
        raise OrchestrationViolation("persisted plan terminal value is invalid")
    if not isinstance(terminal["effect_ids"], list) or len(set(terminal["effect_ids"])) != len(terminal["effect_ids"]):
        raise OrchestrationViolation("plan terminal effect identities are invalid")
    if not isinstance(terminal["alert_ids"], list) or len(set(terminal["alert_ids"])) != len(terminal["alert_ids"]):
        raise OrchestrationViolation("plan terminal alert identities are invalid")
    if terminal["terminal"] in {"succeeded","late_completed"}:
        if len(terminal["effect_ids"]) != 1:
            raise OrchestrationViolation("completed plan terminal must bind exactly one effect")
        effect_path = resolve_inside(runtime_root, f"scheduler/runs/{run_id}/effect.json")
        if not effect_path.is_file():
            raise OrchestrationViolation("completed plan effect is missing")
        effect = load_json(effect_path, reject_floats=True)
        if effect.get("effect_id") != terminal["effect_ids"][0] or content_id("scheduled-effect", effect, excluded_fields=("effect_id",)) != effect["effect_id"]:
            raise OrchestrationViolation("completed plan effect identity mismatch")
    elif terminal["effect_ids"]:
        raise OrchestrationViolation("non-completed plan terminal cannot bind effects")
    closure_id = terminal["correction_closure_id"]
    if closure_id is not None:
        closure_path = resolve_inside(runtime_root, f"correction-closures/{closure_id}/closure.json")
        if not closure_path.is_file():
            raise OrchestrationViolation("correction closure bound by terminal is missing")
        closure = load_json(closure_path, reject_floats=True)
        if closure.get("correction_closure_id") != closure_id or content_id("correction-closure", closure, excluded_fields=("correction_closure_id",)) != closure_id:
            raise OrchestrationViolation("correction closure identity mismatch")


def _commit_terminal(
    runtime_root: Path, ledger: AppendOnlyLedger, terminal_path: Path, terminal: Mapping[str, Any], *,
    event_type: str, provenance: Mapping[str, Any],
) -> None:
    state = ledger.validate()
    existing = None
    if state["event_count"]:
        view = load_json(ledger.current_view_path, reject_floats=True)
        existing = view["objects"].get(terminal["run_id"])
    if existing is not None:
        payload_path = resolve_inside(runtime_root, f"ledgers/schedule-runs/payloads/{existing['payload_sha256']}.json")
        if existing["event_type"] != event_type or load_json(payload_path, reject_floats=True) != dict(terminal):
            raise OrchestrationViolation("schedule terminal ledger identity was reused")
    else:
        _append_current(
            ledger, object_id=terminal["run_id"], event_type=event_type,
            event_at_utc=terminal["completed_at_utc"], payload=terminal,
            producer_provenance=provenance,
        )
    _write_same(terminal_path, terminal)


def _checkpoint(
    runtime_root: Path, *, run_id: str, plan_key: Sequence[str], stage: str, clock: str,
    input_hashes: Sequence[str], output_hashes: Sequence[str], ledger_head_sha256: str,
) -> dict[str, Any]:
    path = resolve_inside(runtime_root, f"scheduler/runs/{run_id}/checkpoints/{stage}.json")
    if path.is_file():
        existing = load_json(path, reject_floats=True)
        validate_checkpoint(existing, run_id=run_id, plan_key=plan_key, expected_stage=stage)
        frozen_head = existing["ledger_head_sha256"]
        if frozen_head != ZERO_SHA256:
            event_root = resolve_inside(runtime_root, "ledgers/schedule-runs/events")
            if not event_root.is_dir() or not any(sha256_file(candidate) == frozen_head for candidate in event_root.glob("*.json")):
                raise CheckpointViolation("checkpoint ledger head is not a historical schedule head")
        ledger_head_sha256 = frozen_head
        clock = existing["created_at_utc"]
    value = build_checkpoint(
        run_id=run_id, plan_key=plan_key, ledger_head_sha256=ledger_head_sha256,
        input_hashes=input_hashes, output_hashes=output_hashes, stage=stage,
        next_ordinal=STAGES.index(stage) + 2, rng_counter=0, created_at_utc=clock,
    )
    _write_same(path, value)
    return value


def _resume_stage(runtime_root: Path, run_id: str, plan_key: Sequence[str]) -> str | None:
    root = resolve_inside(runtime_root, f"scheduler/runs/{run_id}/checkpoints")
    if not root.exists():
        return None
    actual_names = {path.name for path in root.iterdir() if path.is_file()}
    if not actual_names <= {f"{stage}.json" for stage in STAGES}:
        raise CheckpointViolation("checkpoint directory contains an unregistered stage")
    found = []
    for stage in STAGES:
        path = root / f"{stage}.json"
        if path.exists():
            value = load_json(path, reject_floats=True)
            validate_checkpoint(value, run_id=run_id, plan_key=plan_key, expected_stage=stage)
            found.append(stage)
    if not found:
        return None
    expected_prefix = list(STAGES[: STAGES.index(found[-1]) + 1])
    if found != expected_prefix:
        raise CheckpointViolation("checkpoint stage chain is partial or noncontiguous")
    return found[-1]


def execute_plan(
    project_root: Path, runtime_root: Path, plan: Mapping[str, Any], *,
    plan_key: Sequence[str], schedule_sha256: str, clock: str, provenance: Mapping[str, Any],
    late: bool, behavior: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    behavior = dict(behavior or {})
    allowed_behavior = {"outcome", "stop_after_stage", "correction"}
    if not set(behavior) <= allowed_behavior:
        raise OrchestrationViolation("unregistered scheduler fixture behavior")
    if behavior.get("outcome") not in {None, "network_failure", "partial_correction"}:
        raise OrchestrationViolation("unregistered scheduler fixture outcome")
    if behavior.get("stop_after_stage") is not None and behavior["stop_after_stage"] not in STAGES:
        raise OrchestrationViolation("unregistered scheduler interruption stage")
    plan_id = content_id("plan", {"plan_key": list(plan_key)})
    run_id = content_id("run", {"plan_key": list(plan_key), "schedule_sha256": schedule_sha256})
    terminal_path = resolve_inside(runtime_root, f"scheduler/terminals/{plan_id}.json")
    if terminal_path.is_file():
        terminal = load_json(terminal_path, reject_floats=True)
        _validate_terminal(runtime_root, terminal, plan_id=plan_id, run_id=run_id, plan_key=plan_key)
        _commit_terminal(runtime_root, AppendOnlyLedger(runtime_root, "schedule-runs"), terminal_path, terminal, event_type="plan_terminal" if terminal["terminal"] not in {"blocked","missed_deadline"} else f"plan_{terminal['terminal']}", provenance=provenance)
        return {**terminal, "idempotent": True, "terminal":"skipped_idempotent"}
    lease = AdvisoryFileLock(resolve_inside(runtime_root, f"scheduler/leases/{plan_id}.lock"))
    try:
        lease.acquire(blocking=False)
    except LockUnavailable:
        alert = build_alert(
            severity="WARNING", game=plan["game"], object_id=plan_id,
            reason_code="concurrent_trigger", first_seen=clock, last_event_id=run_id,
            runbook_ref="phase4-concurrent-trigger",
        )
        publish_alert(runtime_root, alert, provenance=provenance)
        raise
    try:
        if terminal_path.is_file():
            terminal = load_json(terminal_path, reject_floats=True)
            _validate_terminal(runtime_root, terminal, plan_id=plan_id, run_id=run_id, plan_key=plan_key)
            _commit_terminal(runtime_root, AppendOnlyLedger(runtime_root, "schedule-runs"), terminal_path, terminal, event_type="plan_terminal" if terminal["terminal"] not in {"blocked","missed_deadline"} else f"plan_{terminal['terminal']}", provenance=provenance)
            return {**terminal, "idempotent": True, "terminal":"skipped_idempotent"}
        ledger = AppendOnlyLedger(runtime_root, "schedule-runs")
        ledger_state = ledger.validate()
        head = ledger_state["head_sha256"] or ZERO_SHA256
        input_hashes = [schedule_sha256, canonical_sha256(dict(plan))]
        last_stage = _resume_stage(runtime_root, run_id, plan_key)
        last_index = -1 if last_stage is None else STAGES.index(last_stage)
        run_clock = clock
        if last_stage is not None:
            run_clock = load_json(
                resolve_inside(runtime_root, f"scheduler/runs/{run_id}/checkpoints/leased.json"),
                reject_floats=True,
            )["created_at_utc"]
        run_instant = datetime.fromisoformat(run_clock.replace("Z", "+00:00")).astimezone(timezone.utc)
        planned_instant = datetime.fromisoformat(str(plan["planned_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        run_late = run_instant > planned_instant
        if last_stage is None and run_late != late:
            raise OrchestrationViolation("scheduler late classification disagrees with the virtual clock")
        if last_stage is not None:
            recovery_alert = build_alert(
                severity="WARNING", game=plan["game"], object_id=plan_id,
                reason_code="dependency", first_seen=clock, last_event_id=run_id,
                runbook_ref="phase4-checkpoint-recovery",
            )
            publish_alert(runtime_root, recovery_alert, provenance=provenance)

        def advance(stage: str, output_hashes: Sequence[str]) -> None:
            nonlocal last_index
            index = STAGES.index(stage)
            if index <= last_index:
                _checkpoint(runtime_root, run_id=run_id, plan_key=plan_key, stage=stage, clock=run_clock,
                            input_hashes=input_hashes, output_hashes=output_hashes, ledger_head_sha256=head)
                return
            _checkpoint(runtime_root, run_id=run_id, plan_key=plan_key, stage=stage, clock=run_clock,
                        input_hashes=input_hashes, output_hashes=output_hashes, ledger_head_sha256=head)
            last_index = index
            if behavior.get("stop_after_stage") == stage:
                raise ProcessInterruption(f"fixture interruption after {stage}")

        advance("leased", [])
        if behavior.get("outcome") == "network_failure":
            alert = build_alert(severity="WARNING", game=plan["game"], object_id=plan_id, reason_code="network", first_seen=run_clock, last_event_id=run_id, runbook_ref="phase4-network")
            publish_alert(runtime_root, alert, provenance=provenance)
            terminal = {
                "schema_version":"1.0.0", "artifact_type":"phase4_plan_terminal", "plan_id":plan_id,
                "run_id":run_id, "plan_key":list(plan_key), "terminal":"blocked", "completed_at_utc":run_clock,
                "effect_ids":[], "correction_closure_id":None, "alert_ids":[alert["alert_id"]], "idempotent":False,
            }
            _validate_terminal(runtime_root, terminal, plan_id=plan_id, run_id=run_id, plan_key=plan_key)
            _commit_terminal(runtime_root, ledger, terminal_path, terminal, event_type="plan_blocked", provenance=provenance)
            return terminal

        effect = {
            "schema_version":"1.0.0", "artifact_type":"phase4_scheduled_effect", "plan_id":plan_id,
            "run_id":run_id, "game":plan["game"], "target_issue":plan["target_issue"], "action":plan["action"],
            "score_effect_id":content_id("score-effect", {"plan_id":plan_id}) if plan["action"] == "unlock_score_research" else None,
            "alpha_spend_id":content_id("alpha-spend", {"plan_id":plan_id}) if plan["action"] == "unlock_score_research" else None,
        }
        effect["effect_id"] = content_id("scheduled-effect", effect)
        effect_path = resolve_inside(runtime_root, f"scheduler/runs/{run_id}/effect.json")
        _write_same(effect_path, effect)
        effect_sha = canonical_sha256(effect)
        advance("effects_committed", [effect_sha])

        closure = None
        correction = behavior.get("correction")
        if correction is not None:
            if not isinstance(correction, Mapping):
                raise OrchestrationViolation("correction dependencies must be an object")
            validate_correction_dependencies(project_root, correction)
            advance("correction_score_bound", [effect_sha, correction["t06_receipt_sha256"]])
            advance("correction_research_bound", [effect_sha, correction["t06_receipt_sha256"], correction["t07_receipt_sha256"]])
            closure = {
                "schema_version":"1.0.0", "artifact_type":"phase4_correction_closure", "correction_key":correction["correction_key"],
                "score_side_receipt_sha256":correction["t06_receipt_sha256"],
                "research_side_receipt_sha256":correction["t07_receipt_sha256"],
                "t06_verdict_sha256":correction["t06_verdict_sha256"], "t07_verdict_sha256":correction["t07_verdict_sha256"],
                "closed_at_utc":run_clock,
            }
            closure["correction_closure_id"] = content_id("correction-closure", closure)
            _write_same(resolve_inside(runtime_root, f"correction-closures/{closure['correction_closure_id']}/closure.json"), closure)
            closure_ledger = AppendOnlyLedger(runtime_root, "correction-closures")
            closure_state = closure_ledger.validate()
            existing = None
            if closure_ledger.current_view_path.is_file():
                existing = load_json(closure_ledger.current_view_path, reject_floats=True)["objects"].get(closure["correction_closure_id"])
            if existing is None:
                _append_current(closure_ledger, object_id=closure["correction_closure_id"], event_type="correction_closed", event_at_utc=run_clock, payload=closure, producer_provenance=provenance)
        elif plan["action"] == "unlock_score_research" and behavior.get("outcome") == "partial_correction":
            raise OrchestrationViolation("partial correction cannot close")
        else:
            advance("correction_score_bound", [effect_sha])
            advance("correction_research_bound", [effect_sha])

        output_hashes = [effect_sha] + ([] if closure is None else [canonical_sha256(closure)])
        advance("completed", output_hashes)
        terminal_alerts: list[str] = []
        if run_late:
            late_alert = build_alert(
                severity="WARNING", game=plan["game"], object_id=plan_id,
                reason_code="late_or_missed_deadline", first_seen=run_clock,
                last_event_id=run_id, runbook_ref="phase4-late-plan",
            )
            publish_alert(runtime_root, late_alert, provenance=provenance)
            terminal_alerts.append(late_alert["alert_id"])
        terminal = {
            "schema_version":"1.0.0", "artifact_type":"phase4_plan_terminal", "plan_id":plan_id,
            "run_id":run_id, "plan_key":list(plan_key), "terminal":"late_completed" if run_late else "succeeded",
            "completed_at_utc":run_clock, "effect_ids":[effect["effect_id"]],
            "correction_closure_id":None if closure is None else closure["correction_closure_id"], "alert_ids":terminal_alerts, "idempotent":False,
        }
        _validate_terminal(runtime_root, terminal, plan_id=plan_id, run_id=run_id, plan_key=plan_key)
        _commit_terminal(runtime_root, ledger, terminal_path, terminal, event_type="plan_terminal", provenance=provenance)
        return terminal
    finally:
        lease.release()
