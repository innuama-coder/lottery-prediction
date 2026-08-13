from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Mapping

from .forecast import ForecastViolation, validate_generated
from .identity import content_id
from .ledger import AppendOnlyLedger, LedgerMismatch
from .serialization import canonical_sha256, load_json, sha256_file
from .storage import (
    AdvisoryFileLock,
    IdentityReuseError,
    ensure_directory,
    fsync_directory,
    publish_directory_once,
    resolve_inside,
    write_once_json,
)
from .time_gate import (
    require_before_deadline,
    validate_external_point_in_time,
    validate_retrospective_sequence_safe,
)


class ForecastLockViolation(ForecastViolation):
    pass


def _runtime_binding(runtime_root: Path, relative: str, expected_id: str, id_field: str) -> dict[str, Any]:
    path = resolve_inside(runtime_root, relative)
    if not path.is_file():
        raise ForecastLockViolation(f"required immutable runtime input is missing: {relative}")
    value = load_json(path, reject_floats=True)
    if value.get(id_field) != expected_id:
        raise ForecastLockViolation(f"runtime input identity mismatch: {relative}")
    return {"path": relative, "sha256": sha256_file(path)}


def _bundle_paths(runtime_root: Path, forecast_id: str) -> tuple[Path, Path]:
    return (
        resolve_inside(runtime_root, f"forecasts/{forecast_id}"),
        resolve_inside(runtime_root, f"forecasts/.stage-{forecast_id}"),
    )


def _load_locked(directory: Path) -> dict[str, Any]:
    required = {"snapshot": "snapshot.json", "forecast": "forecast.json", "diagnostic": "diagnostic.json", "lock_receipt": "lock-receipt.json"}
    if not directory.is_dir():
        raise ForecastLockViolation("forecast lock directory is missing")
    result = {key: load_json(directory / name, reject_floats=True) for key, name in required.items()}
    receipt = result["lock_receipt"]
    for key, name in (("snapshot_sha256", "snapshot.json"), ("forecast_sha256", "forecast.json"), ("diagnostic_sha256", "diagnostic.json")):
        if receipt.get(key) != sha256_file(directory / name):
            raise ForecastLockViolation("locked forecast file hash mismatch")
    if result["forecast"].get("forecast_bundle_sha256") != canonical_sha256(
        {key: value for key, value in result["forecast"].items() if key != "forecast_bundle_sha256"}
    ):
        raise ForecastLockViolation("forecast bundle identity mismatch")
    validate_generated({key: result[key] for key in ("snapshot", "forecast", "diagnostic")})
    return result


def lock_forecast(
    runtime_root: Path,
    generated: Mapping[str, Any],
    *,
    prediction_locked_at: str,
    hard_deadline_at: str,
    producer_provenance: Mapping[str, Any],
    fault: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    generated = validate_generated(generated)
    forecast, snapshot = generated["forecast"], generated["snapshot"]
    forecast_id = forecast["forecast_id"]
    require_before_deadline(prediction_locked_at=prediction_locked_at, hard_deadline_at=hard_deadline_at)
    if snapshot["prediction_locked_at"] != prediction_locked_at:
        raise ForecastLockViolation("actual immutable lock time differs from the content-bound snapshot lock time")
    validate_retrospective_sequence_safe(snapshot["historical_features"], target_issue=snapshot["target_issue"])
    validate_external_point_in_time(snapshot["external_features"], prediction_locked_at=prediction_locked_at)
    data_binding = _runtime_binding(
        runtime_root,
        f"data-releases/{forecast['data_release_id']}/data-release.json",
        forecast["data_release_id"],
        "data_release_id",
    )
    calendar_binding = _runtime_binding(
        runtime_root,
        f"calendar-releases/{forecast['calendar_release_id']}/calendar.json",
        forecast["calendar_release_id"],
        "calendar_release_id",
    )
    callback = fault or (lambda _stage: None)
    destination, staging = _bundle_paths(runtime_root, forecast_id)
    lock_path = resolve_inside(runtime_root, ".forecast-lock")
    with AdvisoryFileLock(lock_path):
        ledger = AppendOnlyLedger(runtime_root, "forecast-locks")
        validation = ledger.validate()
        if destination.exists():
            locked = _load_locked(destination)
            if {key: locked[key] for key in ("snapshot", "forecast", "diagnostic")} != generated:
                raise ForecastLockViolation("existing lock directory differs from the deterministic forecast package")
            receipt = locked["lock_receipt"]
            if receipt["prediction_locked_at"] != prediction_locked_at or receipt["hard_deadline_at"] != hard_deadline_at:
                raise ForecastLockViolation("existing forecast lock time identity differs")
            view = load_json(ledger.current_view_path, reject_floats=True) if validation["event_count"] else {"objects": {}}
            item = view.get("objects", {}).get(forecast_id)
            if item is None:
                if receipt["previous_ledger_head_sha256"] != validation["head_sha256"]:
                    raise ForecastLockViolation("published forecast cannot recover against the current ledger head")
                recovered = ledger.append_event(
                    object_id=forecast_id,
                    event_type="forecast_locked",
                    event_at_utc=prediction_locked_at,
                    payload={
                        "forecast_id": forecast_id,
                        "lock_receipt_id": receipt["lock_receipt_id"],
                        "lock_receipt_sha256": sha256_file(destination / "lock-receipt.json"),
                        "forecast_sha256": receipt["forecast_sha256"],
                        "diagnostic_sha256": receipt["diagnostic_sha256"],
                    },
                    producer_provenance=producer_provenance,
                    expected_head_sha256=validation["head_sha256"],
                )
                return {**locked, "ledger_head_sha256": recovered["head"]["event_sha256"], "idempotent_resume": True}
            if item.get("event_type") != "forecast_locked":
                raise ForecastLockViolation("locked forecast has an invalid ledger terminal")
            return {**locked, "ledger_head_sha256": validation["head_sha256"], "idempotent_resume": True}
        if staging.exists():
            raise ForecastLockViolation("unreconciled forecast lock staging directory exists")
        ensure_directory(staging)
        try:
            write_once_json(staging / "snapshot.json", snapshot)
            write_once_json(staging / "forecast.json", forecast)
            write_once_json(staging / "diagnostic.json", generated["diagnostic"])
            receipt: dict[str, Any] = {
                "schema_version": "1.0.0",
                "artifact_type": "phase4_forecast_lock_receipt",
                "forecast_id": forecast_id,
                "game": forecast["game"],
                "target_issue": forecast["target_issue"],
                "rule_id": forecast["rule_id"],
                "model_id": forecast["model_id"],
                "model_release_id": forecast["model_release_id"],
                "config_id": forecast["config_id"],
                "feature_snapshot_id": forecast["feature_snapshot_id"],
                "data_release_id": forecast["data_release_id"],
                "calendar_release_id": forecast["calendar_release_id"],
                "schedule_release_id": forecast["schedule_release_id"],
                "metric_contract_id": forecast["metric_contract_id"],
                "forecast_bundle_sha256": forecast["forecast_bundle_sha256"],
                "snapshot_sha256": sha256_file(staging / "snapshot.json"),
                "forecast_sha256": sha256_file(staging / "forecast.json"),
                "diagnostic_sha256": sha256_file(staging / "diagnostic.json"),
                "data_release_file": data_binding,
                "calendar_release_file": calendar_binding,
                "prediction_locked_at": prediction_locked_at,
                "hard_deadline_at": hard_deadline_at,
                "previous_ledger_head_sha256": validation["head_sha256"],
                "producer_provenance": dict(producer_provenance),
            }
            receipt["lock_receipt_id"] = content_id("forecast-lock", receipt)
            write_once_json(staging / "lock-receipt.json", receipt)
            fsync_directory(staging)
            callback("before_publish")
            publish_directory_once(staging, destination)
            callback("after_publish")
            event = ledger.append_event(
                object_id=forecast_id,
                event_type="forecast_locked",
                event_at_utc=prediction_locked_at,
                payload={
                    "forecast_id": forecast_id,
                    "lock_receipt_id": receipt["lock_receipt_id"],
                    "lock_receipt_sha256": sha256_file(destination / "lock-receipt.json"),
                    "forecast_sha256": receipt["forecast_sha256"],
                    "diagnostic_sha256": receipt["diagnostic_sha256"],
                },
                producer_provenance=producer_provenance,
                expected_head_sha256=validation["head_sha256"],
            )
            callback("after_ledger")
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
                fsync_directory(staging.parent)
            raise
    return {
        "snapshot": snapshot,
        "forecast": forecast,
        "diagnostic": generated["diagnostic"],
        "lock_receipt": receipt,
        "ledger_head_sha256": event["head"]["event_sha256"],
        "idempotent_resume": False,
    }


def load_locked_forecast(runtime_root: Path, forecast_id: str) -> dict[str, Any]:
    destination, _ = _bundle_paths(runtime_root, forecast_id)
    locked = _load_locked(destination)
    ledger = AppendOnlyLedger(runtime_root, "forecast-locks")
    validation = ledger.validate()
    view = load_json(ledger.current_view_path, reject_floats=True)
    item = view.get("objects", {}).get(forecast_id)
    if item is None or item.get("event_type") != "forecast_locked":
        raise ForecastLockViolation("forecast lock ledger binding is missing")
    payload_path = ledger.payloads_root / f"{item['payload_sha256']}.json"
    payload = load_json(payload_path, reject_floats=True)
    if (
        payload.get("lock_receipt_sha256") != sha256_file(destination / "lock-receipt.json")
        or payload.get("forecast_sha256") != sha256_file(destination / "forecast.json")
        or payload.get("diagnostic_sha256") != sha256_file(destination / "diagnostic.json")
    ):
        raise ForecastLockViolation("forecast lock ledger payload/file hash mismatch")
    return {**locked, "ledger_head_sha256": validation["head_sha256"]}
