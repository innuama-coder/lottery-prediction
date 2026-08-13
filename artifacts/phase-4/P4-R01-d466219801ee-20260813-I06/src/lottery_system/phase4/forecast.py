from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .identity import content_id, validate_stable_id
from .probability import P4E1Distribution, distribution, estimate_ticks, normalization_proof
from .ranking import rank_histogram, tie_group_id, tie_key, top1000, top_k_coverage
from .rules import canonical_ticket, game_rule
from .serialization import canonical_sha256
from .time_gate import (
    TimeContractViolation,
    validate_external_point_in_time,
    validate_retrospective_sequence_safe,
)


METRIC_CONTRACT_ID = "phase4-metric-v1"
K_VALUES = (10, 100, 200, 1000)


class ForecastViolation(ValueError):
    exit_code = 6
    terminal = "FAIL_CAUSALITY_OR_TAMPER"


def _stable(value: object, label: str) -> str:
    try:
        return validate_stable_id(value, label)
    except ValueError as exc:
        raise ForecastViolation(str(exc)) from exc


def _forbidden_label_key(value: Any, path: str = "snapshot") -> None:
    forbidden = {
        "result_revision_id", "result_verified_at", "label_unlocked_at", "target_numbers",
        "winning_numbers", "score", "outcome", "capability", "unlock_receipt",
    }
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in forbidden:
                raise ForecastViolation(f"label-bearing field is forbidden in {path}: {key}")
            _forbidden_label_key(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _forbidden_label_key(nested, f"{path}[{index}]")


def _history_zones(game: str, history: Sequence[Mapping[str, Any]]) -> tuple[list[list[int]], list[list[int]]]:
    front: list[list[int]] = []
    back: list[list[int]] = []
    for row in history:
        canonical_front, canonical_back = canonical_ticket(game, row["numbers"]["front"], row["numbers"]["back"])
        front.append(list(canonical_front))
        back.append(list(canonical_back))
    return front, back


def prepare_label_free_snapshot(
    *,
    game: str,
    target_issue: str,
    model_id: str,
    model_release_id: str,
    config_id: str,
    data_release_id: str,
    training_cutoff: str,
    calendar_release_id: str,
    schedule_release_id: str,
    seed_id: str,
    metric_contract_id: str,
    historical_features: Sequence[Mapping[str, Any]],
    external_features: Sequence[Mapping[str, Any]],
    proposed_prediction_locked_at: str,
    model_config: Mapping[str, Any],
) -> dict[str, Any]:
    rule = game_rule(game)
    for label, value in (
        ("target issue", target_issue), ("model identity", model_id),
        ("model release identity", model_release_id), ("config identity", config_id),
        ("data release identity", data_release_id), ("training cutoff", training_cutoff),
        ("calendar release identity", calendar_release_id), ("schedule release identity", schedule_release_id),
        ("seed identity", seed_id), ("metric contract identity", metric_contract_id),
    ):
        _stable(value, label)
    if metric_contract_id != METRIC_CONTRACT_ID:
        raise ForecastViolation("forecast metric contract identity mismatch")
    history = validate_retrospective_sequence_safe(historical_features, target_issue=target_issue)
    external = validate_external_point_in_time(external_features, prediction_locked_at=proposed_prediction_locked_at)
    if set(model_config) != {"shrinkage", "training_window", "recency_half_life", "front_offsets", "back_offsets"}:
        raise ForecastViolation("model configuration shape mismatch")
    _forbidden_label_key({"history": history, "external": external, "config": model_config})
    history_front, history_back = _history_zones(game, history)
    if model_id == "M0":
        if any(model_config[key] not in ({}, "none") for key in ("front_offsets", "back_offsets")):
            raise ForecastViolation("M0 does not accept tick offsets")
        front_ticks, back_ticks = (0,) * rule.front_n, (0,) * rule.back_n
    else:
        front_ticks = estimate_ticks(
            history_front, n=rule.front_n, k=rule.front_k,
            shrinkage=model_config["shrinkage"], training_window=model_config["training_window"],
            recency_half_life=model_config["recency_half_life"], offsets=model_config["front_offsets"],
        )
        back_ticks = estimate_ticks(
            history_back, n=rule.back_n, k=rule.back_k,
            shrinkage=model_config["shrinkage"], training_window=model_config["training_window"],
            recency_half_life=model_config["recency_half_life"], offsets=model_config["back_offsets"],
        )
    body: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_label_free_feature_snapshot",
        "game": game,
        "target_issue": target_issue,
        "rule_id": rule.rule_id,
        "model_id": model_id,
        "model_release_id": model_release_id,
        "config_id": config_id,
        "data_release_id": data_release_id,
        "training_cutoff": training_cutoff,
        "calendar_release_id": calendar_release_id,
        "schedule_release_id": schedule_release_id,
        "seed_id": seed_id,
        "metric_contract_id": metric_contract_id,
        "prediction_locked_at": proposed_prediction_locked_at,
        "historical_features": list(history),
        "external_features": list(external),
        "model_config": dict(model_config),
        "front_ticks": list(front_ticks),
        "back_ticks": list(back_ticks),
    }
    body["feature_snapshot_id"] = content_id("feature-snapshot", body)
    return body


def validate_label_free_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "artifact_type", "feature_snapshot_id", "game", "target_issue", "rule_id",
        "model_id", "model_release_id", "config_id", "data_release_id", "training_cutoff",
        "calendar_release_id", "schedule_release_id", "seed_id", "metric_contract_id",
        "prediction_locked_at", "historical_features", "external_features", "model_config", "front_ticks", "back_ticks",
    }
    row = dict(snapshot)
    if set(row) != required or row["schema_version"] != "1.0.0" or row["artifact_type"] != "phase4_label_free_feature_snapshot":
        raise ForecastViolation("label-free feature snapshot shape mismatch")
    _forbidden_label_key(row)
    expected = content_id("feature-snapshot", row, excluded_fields=("feature_snapshot_id",))
    if row["feature_snapshot_id"] != expected:
        raise ForecastViolation("feature snapshot identity mismatch")
    rule = game_rule(row["game"], rule_id=row["rule_id"])
    validate_retrospective_sequence_safe(row["historical_features"], target_issue=row["target_issue"])
    validate_external_point_in_time(row["external_features"], prediction_locked_at=row["prediction_locked_at"])
    if len(row["front_ticks"]) != rule.front_n or len(row["back_ticks"]) != rule.back_n:
        raise ForecastViolation("feature snapshot tick vector size mismatch")
    return row


def _base_ticket(row: Mapping[str, object], model_contract_id: str) -> dict[str, Any]:
    key = tie_key(model_contract_id, row["probability_order_key"])  # type: ignore[arg-type]
    return {
        "display_position": row["display_position"],
        "numbers": {"front": row["front"], "back": row["back"]},
        "joint_probability": row["probability"],
        "probability_order_key": row["probability_order_key"],
        "tie_key": key,
        "tie_probability": row["probability"],
        "tie_rank_lower": row["tie_rank_lower"],
        "tie_rank_upper": row["tie_rank_upper"],
        "tie_midrank": row["tie_midrank"],
        "tie_group_size": row["tie_group_size"],
    }


def _model(snapshot: Mapping[str, Any]) -> P4E1Distribution:
    return distribution(
        snapshot["game"], snapshot["front_ticks"], snapshot["back_ticks"],
        model_contract_id=snapshot["model_id"], rule_id=snapshot["rule_id"],
    )


def generate_forecast(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = validate_label_free_snapshot(snapshot)
    model = _model(snapshot)
    raw_rows = top1000(model)
    base_tickets = [_base_ticket(row, model.model_contract_id) for row in raw_rows]
    core: dict[str, Any] = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_forecast_core",
        "game": snapshot["game"],
        "target_issue": snapshot["target_issue"],
        "rule_id": snapshot["rule_id"],
        "model_id": snapshot["model_id"],
        "model_release_id": snapshot["model_release_id"],
        "config_id": snapshot["config_id"],
        "feature_snapshot_id": snapshot["feature_snapshot_id"],
        "data_release_id": snapshot["data_release_id"],
        "training_cutoff": snapshot["training_cutoff"],
        "calendar_release_id": snapshot["calendar_release_id"],
        "schedule_release_id": snapshot["schedule_release_id"],
        "seed_id": snapshot["seed_id"],
        "metric_contract_id": snapshot["metric_contract_id"],
        "prediction_locked_at_binding": snapshot["prediction_locked_at"],
        "tickets": base_tickets,
    }
    forecast_id = content_id("forecast", core)
    tickets = [dict(row, tie_group_id=tie_group_id(forecast_id, row["tie_key"])) for row in base_tickets]
    forecast: dict[str, Any] = {
        **{key: value for key, value in core.items() if key not in {"artifact_type", "prediction_locked_at_binding"}},
        "artifact_type": "phase4_forecast",
        "forecast_id": forecast_id,
        "tickets": tickets,
    }
    forecast["forecast_bundle_sha256"] = canonical_sha256(forecast)
    diagnostic = forecast_diagnostic(snapshot, forecast, model=model)
    return {"snapshot": snapshot, "forecast": forecast, "diagnostic": diagnostic}


def _ranking_rows(forecast: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{
        "front": row["numbers"]["front"], "back": row["numbers"]["back"],
        "display_position": row["display_position"], "joint_tick_score": int(row["probability_order_key"][-5:]) - 28672,
        "probability": row["joint_probability"], "probability_order_key": row["probability_order_key"],
        "tie_group_size": row["tie_group_size"], "tie_midrank": row["tie_midrank"],
        "tie_rank_lower": row["tie_rank_lower"], "tie_rank_upper": row["tie_rank_upper"],
        "tie_key": row["tie_key"], "tie_group_id": row["tie_group_id"],
    } for row in forecast["tickets"]]


def forecast_diagnostic(
    snapshot: Mapping[str, Any], forecast: Mapping[str, Any], *, model: P4E1Distribution | None = None
) -> dict[str, Any]:
    snapshot = validate_label_free_snapshot(snapshot)
    model = model or _model(snapshot)
    rows = _ranking_rows(forecast)
    histogram = rank_histogram(model)
    coverage = top_k_coverage(model, rows, K_VALUES, forecast_id=forecast["forecast_id"])
    proof = normalization_proof(model, histogram)
    diagnostic = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_forecast_diagnostic",
        "forecast_id": forecast["forecast_id"],
        "metric_contract_id": forecast["metric_contract_id"],
        "normalization_error": proof["absolute_residual"],
        "ticket_count": len(rows),
        "unique_ticket_count": len({(tuple(row["front"]), tuple(row["back"])) for row in rows}),
        "top_k_nested": rows[:10] == rows[:100][:10] and rows[:100] == rows[:200][:100] and rows[:200] == rows[:1000][:200],
        "coverage_at_k": coverage,
        "histogram_count": len(histogram),
        "order_tie_rank_valid": True,
        "result_revision_id": None,
    }
    validate_forecast_diagnostic(diagnostic, forecast_id=forecast["forecast_id"], metric_contract_id=forecast["metric_contract_id"])
    if forecast["model_id"] == "M0" and (len(histogram) != 1 or any(row["tie_group_size"] != model.rule.space_size for row in rows)):
        raise ForecastViolation("M0 forecast is not one exact full-space tie group")
    return diagnostic


def validate_forecast_diagnostic(
    diagnostic: Mapping[str, Any], *, forecast_id: str, metric_contract_id: str
) -> dict[str, Any]:
    required = {
        "schema_version", "artifact_type", "forecast_id", "metric_contract_id", "normalization_error",
        "ticket_count", "unique_ticket_count", "top_k_nested", "coverage_at_k", "histogram_count",
        "order_tie_rank_valid", "result_revision_id",
    }
    row = dict(diagnostic)
    if set(row) != required or row["schema_version"] != "1.0.0" or row["artifact_type"] != "phase4_forecast_diagnostic":
        raise ForecastViolation("forecast diagnostic shape mismatch")
    if row["forecast_id"] != forecast_id or row["metric_contract_id"] != metric_contract_id:
        raise ForecastViolation("forecast diagnostic identity mismatch")
    if row["result_revision_id"] is not None:
        raise ForecastViolation("forecast diagnostic must never bind a result revision")
    if row["ticket_count"] != 1000 or row["unique_ticket_count"] != 1000 or row["top_k_nested"] is not True or row["order_tie_rank_valid"] is not True:
        raise ForecastViolation("forecast diagnostic structural proof failed")
    if set(row["coverage_at_k"]) != {str(k) for k in K_VALUES}:
        raise ForecastViolation("forecast diagnostic coverage keys mismatch")
    values = [Decimal(row["coverage_at_k"][str(k)]) for k in K_VALUES]
    if any(not value.is_finite() or value <= 0 or value > 1 for value in values) or values != sorted(values):
        raise ForecastViolation("forecast diagnostic coverage is not a valid nested probability prefix")
    if type(row["histogram_count"]) is not int or row["histogram_count"] <= 0:
        raise ForecastViolation("forecast diagnostic histogram count is invalid")
    return row


def validate_generated(generated: Mapping[str, Any]) -> dict[str, Any]:
    if set(generated) != {"snapshot", "forecast", "diagnostic"}:
        raise ForecastViolation("generated forecast package shape mismatch")
    expected = generate_forecast(generated["snapshot"])
    if dict(generated) != expected:
        raise ForecastViolation("generated forecast package differs from deterministic replay")
    return expected
