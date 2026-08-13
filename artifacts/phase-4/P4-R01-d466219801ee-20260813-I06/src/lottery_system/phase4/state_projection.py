from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .identity import content_id, validate_stable_id
from .ledger import AppendOnlyLedger
from .serialization import load_json


class StateProjectionViolation(ValueError):
    exit_code = 5


ENGINEERING_KEYS = {"schema_version", "artifact_type", "system_release_id", "status"}
MODEL_KEYS = {
    "schema_version", "artifact_type", "game", "model_id", "comparator_champion_id",
    "model_release_id", "window_id", "status",
}
TOP_K_KEYS = MODEL_KEYS | {"K"}
MODEL_KEY = ("game", "model_id", "comparator_champion_id", "model_release_id", "window_id")
TOP_K_KEY = ("game", "K", "model_id", "comparator_champion_id", "model_release_id", "window_id")
TOP_K_VALUES = (10, 100, 200, 1000)


def _strict_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise StateProjectionViolation(f"{label} must be nonempty text")
    if value.lower() == "latest" or any(token in value for token in ("*", "/", "\\")):
        raise StateProjectionViolation(f"{label} must be an explicit immutable identity")
    validate_stable_id(value, label)
    return value


def _validate_record(record: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]]:
    if "improved" in record or any(key.startswith("global_") for key in record):
        raise StateProjectionViolation("global improvement fields are forbidden")
    artifact = record.get("artifact_type")
    if artifact == "phase4_engineering_status":
        if set(record) != ENGINEERING_KEYS or record.get("schema_version") != "1.0.0":
            raise StateProjectionViolation("engineering state key set is incomplete or contaminated")
        _strict_text(record["system_release_id"], "system release identity")
        if record["status"] not in contract["engineering"]["phase4_values"]:
            raise StateProjectionViolation("engineering state is unavailable in Phase 4")
        return "engineering", (record["system_release_id"],)
    if artifact == "phase4_model_status":
        if set(record) != MODEL_KEYS or record.get("schema_version") != "1.0.0":
            raise StateProjectionViolation("model state key set is incomplete or contaminated")
        for key in MODEL_KEY:
            _strict_text(record[key], key)
        if record["game"] not in {"ssq", "dlt"} or record["comparator_champion_id"] != contract["champion_by_game"][record["game"]]:
            raise StateProjectionViolation("model state crosses game or Champion dimensions")
        if record["status"] not in contract["model"]["phase4_values"]:
            raise StateProjectionViolation("future-phase model state is forbidden")
        if (record["model_id"] == "M0") != (record["status"] == "baseline_only"):
            raise StateProjectionViolation("baseline and shadow model identities are inconsistent")
        return "model", tuple(record[key] for key in MODEL_KEY)
    if artifact == "phase4_top_k_status":
        if set(record) != TOP_K_KEYS or record.get("schema_version") != "1.0.0":
            raise StateProjectionViolation("Top-K state key set is incomplete or contaminated")
        for key in MODEL_KEY:
            _strict_text(record[key], key)
        if isinstance(record["K"], bool) or record["K"] not in TOP_K_VALUES:
            raise StateProjectionViolation("Top-K state K is not registered")
        if record["game"] not in {"ssq", "dlt"} or record["comparator_champion_id"] != contract["champion_by_game"][record["game"]]:
            raise StateProjectionViolation("Top-K state crosses game or Champion dimensions")
        if record["status"] not in contract["top_k"]["phase4_values"]:
            raise StateProjectionViolation("future-phase Top-K state is forbidden")
        return "top_k", tuple(record[key] for key in TOP_K_KEY)
    raise StateProjectionViolation("state event has an unregistered record type")


def state_object_id(record: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    kind, key = _validate_record(record, contract)
    return content_id("state-key", {"kind": kind, "key": list(key)})


def reduce_state_events(
    events: Sequence[Mapping[str, Any]], *, contract: Mapping[str, Any], ledger_head_sha256: str,
) -> dict[str, Any]:
    if contract.get("artifact_type") != "phase4_state_contract" or contract.get("global_improved_field_allowed") is not False:
        raise StateProjectionViolation("state contract is not the frozen Phase 4 contract")
    if not isinstance(events, list) or not events:
        raise StateProjectionViolation("state projection requires immutable state events")
    current: dict[tuple[str, tuple[Any, ...]], dict[str, Any]] = {}
    for raw in events:
        if not isinstance(raw, Mapping):
            raise StateProjectionViolation("state event payload must be an object")
        record = dict(raw)
        kind, key = _validate_record(record, contract)
        composite = (kind, key)
        prior = current.get(composite)
        if prior is not None:
            before, after = prior["status"], record["status"]
            allowed = (
                before == after
                or (kind == "engineering" and before == "HOLD" and after in {"FAIL", "READY_FOR_HUMAN_ACCEPTANCE"})
                or (kind == "model" and before == "baseline_only" and after == "shadow_candidate")
            )
            if not allowed:
                raise StateProjectionViolation("state transition is reversed or unavailable in Phase 4")
        current[composite] = record
    engineering = [row for (kind, _key), row in current.items() if kind == "engineering"]
    models = [row for (kind, _key), row in current.items() if kind == "model"]
    top_k = [row for (kind, _key), row in current.items() if kind == "top_k"]
    if len(engineering) != 1:
        raise StateProjectionViolation("exactly one engineering release state is required")
    model_keys = {tuple(row[key] for key in MODEL_KEY) for row in models}
    required_baselines = {
        (game, "M0", "M0", "baseline-v1", "phase4-window-v1")
        for game in ("ssq", "dlt")
    }
    if not required_baselines <= model_keys:
        raise StateProjectionViolation("both game-specific baseline model states are required")
    top_keys = {tuple(row[key] for key in TOP_K_KEY) for row in top_k}
    required_top = {
        (game, K, "M0", "M0", "baseline-v1", "phase4-window-v1")
        for game in ("ssq", "dlt") for K in TOP_K_VALUES
    }
    if not required_top <= top_keys:
        raise StateProjectionViolation("all eight game/K baseline Top-K states are required")
    for model_key in model_keys:
        game, model_id, comparator, release_id, window_id = model_key
        model_top_keys = {
            (game, K, model_id, comparator, release_id, window_id)
            for K in TOP_K_VALUES
        }
        if not model_top_keys <= top_keys:
            raise StateProjectionViolation("every model state requires all four exact-dimension Top-K cells")
    for row in top_k:
        model_key = tuple(row[key] for key in MODEL_KEY)
        if model_key not in model_keys:
            raise StateProjectionViolation("Top-K state has no exact same-dimension model state")
    projection: dict[str, Any] = {
        "schema_version": "1.0.0", "artifact_type": "phase4_state_projection",
        "contract_id": "phase4-state-v1", "ledger_head_sha256": ledger_head_sha256,
        "engineering_status": engineering[0], "champion_by_game": dict(contract["champion_by_game"]),
        "model_status": sorted(models, key=lambda row: tuple(row[key] for key in MODEL_KEY)),
        "top_k_status": sorted(top_k, key=lambda row: tuple(row[key] for key in TOP_K_KEY)),
    }
    projection["projection_id"] = content_id("state-projection", projection)
    return projection


def project_runtime_state(runtime_root: Path, contract: Mapping[str, Any]) -> dict[str, Any]:
    ledger = AppendOnlyLedger(runtime_root, "state-events")
    state = ledger.validate()
    if state["event_count"] == 0 or state["head_sha256"] is None:
        raise StateProjectionViolation("state event ledger is empty")
    rows = ledger._event_files()
    payloads: list[dict[str, Any]] = []
    for path in rows:
        event = load_json(path, reject_floats=True)
        payload_path = ledger.payloads_root / f"{event['payload_sha256']}.json"
        payload = load_json(payload_path, reject_floats=True)
        if state_object_id(payload, contract) != event["object_id"] or event["event_type"] != "state_recorded":
            raise StateProjectionViolation("state ledger event identity or type mismatch")
        payloads.append(payload)
    return reduce_state_events(payloads, contract=contract, ledger_head_sha256=state["head_sha256"])
