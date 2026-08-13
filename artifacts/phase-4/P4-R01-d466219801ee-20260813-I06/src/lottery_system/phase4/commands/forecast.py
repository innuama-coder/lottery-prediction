from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..cli_kernel import ProviderRegistry, parse_clock, producer_provenance, project_root
from ..forecast import ForecastViolation, generate_forecast, prepare_label_free_snapshot
from ..identity import content_id, validate_stable_id
from ..lock import ForecastLockViolation, load_locked_forecast, lock_forecast
from ..rules import game_rule
from ..serialization import load_json, sha256_file
from ..storage import resolve_inside, validate_runtime_root, write_once_json


T10_MANIFEST_SHA256 = "8073aa54c1d1fa8d06ff1fc56e9c2fa1c625744cd3d81e17b9575906f8157803"


def _write_same(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        if load_json(path, reject_floats=True) != dict(payload):
            raise ForecastViolation("immutable forecast stage identity reuse mismatch")
    else:
        write_once_json(path, dict(payload))


def forecast_prepare(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    forecast_id = validate_stable_id(args.forecast_id, "forecast identity")
    input_path = resolve_inside(runtime, f"forecast-inputs/{forecast_id}.json")
    supplied = load_json(input_path, reject_floats=True)
    if supplied.get("data_release_id") != args.data_release_id or supplied.get("calendar_release_id") != args.calendar_id:
        raise ForecastViolation("forecast prepare input release/calendar identity mismatch")
    if args.contract_id != "phase4-time-contract-v1":
        raise ForecastViolation("forecast prepare time contract identity mismatch")
    clock = parse_clock(args.clock)
    snapshot = prepare_label_free_snapshot(
        **{key: supplied[key] for key in (
            "game", "target_issue", "model_id", "model_release_id", "config_id", "data_release_id",
            "training_cutoff", "calendar_release_id", "schedule_release_id", "seed_id", "metric_contract_id",
            "historical_features", "external_features", "model_config",
        )},
        proposed_prediction_locked_at=clock,
    )
    derived_forecast_id = generate_forecast(snapshot)["forecast"]["forecast_id"]
    if derived_forecast_id != forecast_id:
        raise ForecastViolation("forecast prepare identity is not derived from the frozen label-free snapshot")
    provenance = producer_provenance(root, runtime.relative_to(root).as_posix())
    artifact = {"snapshot": snapshot, "hard_deadline_at": supplied["hard_deadline_at"], "producer_provenance": provenance}
    _write_same(resolve_inside(runtime, f"forecast-prepared/{forecast_id}.json"), artifact)
    return {"status": "PASS", "terminal": "PASS", "forecast_id": derived_forecast_id, "feature_snapshot_id": snapshot["feature_snapshot_id"], "exit_code": 0}


def forecast_generate(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    forecast_id = validate_stable_id(args.forecast_id, "forecast identity")
    prepared = load_json(resolve_inside(runtime, f"forecast-prepared/{forecast_id}.json"), reject_floats=True)
    if prepared["snapshot"]["model_id"] != args.model_id or prepared["snapshot"]["config_id"] != args.config_id:
        raise ForecastViolation("forecast generate model/config identity mismatch")
    validate_stable_id(args.contract_id, "probability contract identity")
    parse_clock(args.clock)
    generated = generate_forecast(prepared["snapshot"])
    if generated["forecast"]["forecast_id"] != forecast_id:
        raise ForecastViolation("generated forecast identity differs from the requested identity")
    provenance = producer_provenance(root, runtime.relative_to(root).as_posix())
    artifact = {"generated": generated, "hard_deadline_at": prepared["hard_deadline_at"], "producer_provenance": provenance}
    _write_same(resolve_inside(runtime, f"forecast-generated/{forecast_id}.json"), artifact)
    return {"status": "PASS", "terminal": "PASS", "forecast_id": generated["forecast"]["forecast_id"], "exit_code": 0}


def forecast_lock(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    request_id = validate_stable_id(args.forecast_id, "forecast identity")
    if args.contract_id != "phase4-time-contract-v1":
        raise ForecastLockViolation("forecast lock time contract identity mismatch")
    artifact = load_json(resolve_inside(runtime, f"forecast-generated/{request_id}.json"), reject_floats=True)
    clock = parse_clock(args.clock)
    result = lock_forecast(
        runtime,
        artifact["generated"],
        prediction_locked_at=clock,
        hard_deadline_at=artifact["hard_deadline_at"],
        producer_provenance=producer_provenance(root, runtime.relative_to(root).as_posix()),
    )
    return {"status": "PASS", "terminal": "PASS", "forecast_id": result["forecast"]["forecast_id"], "idempotent_resume": result["idempotent_resume"], "exit_code": 0}


def forecast_show(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    locked = load_locked_forecast(runtime, validate_stable_id(args.forecast_id, "forecast identity"))
    return {
        "status": "PASS", "terminal": "PASS", "forecast": locked["forecast"],
        "diagnostic": locked["diagnostic"], "lock_receipt": locked["lock_receipt"],
        "ledger_head_sha256": locked["ledger_head_sha256"], "exit_code": 0,
    }


def _snapshot_from_ticks(expected: Mapping[str, Any], game: str, model_id: str, front: list[int], back: list[int]) -> dict[str, Any]:
    rule = game_rule(game, rule_id=expected["rule_id"])
    body: dict[str, Any] = {
        "schema_version": "1.0.0", "artifact_type": "phase4_label_free_feature_snapshot",
        "game": game, "target_issue": "2099999", "rule_id": rule.rule_id, "model_id": model_id,
        "model_release_id": f"{model_id}-release-v1", "config_id": f"{model_id}-config-v1",
        "data_release_id": "diagnostic-data-v1", "training_cutoff": "2099998",
        "calendar_release_id": "diagnostic-calendar-v1", "schedule_release_id": "diagnostic-schedule-v1",
        "seed_id": "result-blind-diagnostic-v1", "metric_contract_id": "phase4-metric-v1",
        "prediction_locked_at": "2026-01-02T09:00:00Z",
        "historical_features": [{"time_class": "retrospective_sequence_safe", "source_issue": "2099998", "numbers": {
            "front": list(range(1, rule.front_k + 1)), "back": list(range(1, rule.back_k + 1)),
        }}],
        "external_features": [],
        "model_config": {"shrinkage": 1, "training_window": "expanding", "recency_half_life": "none", "front_offsets": {}, "back_offsets": {}},
        "front_ticks": front, "back_ticks": back,
    }
    body["feature_snapshot_id"] = content_id("feature-snapshot", body)
    return body


def validate_forecast_diagnostic_scope(args: Any) -> dict[str, Any]:
    if args.scope != "forecast-diagnostic-time-label":
        return {"status": "HOLD", "terminal": "HOLD_UNSUPPORTED_TIE_SEMANTICS", "error": "T05 validator owns only forecast-diagnostic-time-label", "exit_code": 20}
    root = project_root().resolve()
    oracle = args.oracle.resolve()
    oracle.relative_to(root)
    manifest = resolve_inside(oracle, "known-answer-manifest.json")
    if sha256_file(manifest) != T10_MANIFEST_SHA256:
        raise ForecastViolation("T10-I07 diagnostic manifest identity mismatch")
    m0 = load_json(resolve_inside(oracle, "real-rule-m0.json"), reject_floats=True)
    full = load_json(resolve_inside(oracle, "full-rule-oracle.json"), reject_floats=True)
    spec = load_json(root / "qualification-design/full-rule-spec-candidate.json", reject_floats=True)
    summaries: list[dict[str, Any]] = []
    for expected in m0["games"]:
        rule = game_rule(expected["game"])
        generated = generate_forecast(_snapshot_from_ticks(expected, expected["game"], "M0", [0] * rule.front_n, [0] * rule.back_n))
        diagnostic = generated["diagnostic"]
        if diagnostic["histogram_count"] != 1:
            raise ForecastViolation("M0 diagnostic is not one full-space group")
        summaries.append({"game": expected["game"], "model_id": "M0", "forecast_id": generated["forecast"]["forecast_id"], "diagnostic": diagnostic})
    for expected in full["results"]:
        generated = generate_forecast(_snapshot_from_ticks(expected, expected["game"], spec["spec_id"], expected["front_ticks"], expected["back_ticks"]))
        coverage = generated["diagnostic"]["coverage_at_k"]
        for cell in expected["cells"]:
            from decimal import Decimal

            if abs(Decimal(coverage[str(cell["K"])]) - Decimal(cell["candidate_coverage"])) > Decimal(cell["absolute_error_bound"]):
                raise ForecastViolation("full-rule forecast diagnostic coverage mismatch")
        summaries.append({"game": expected["game"], "model_id": spec["spec_id"], "forecast_id": generated["forecast"]["forecast_id"], "diagnostic": generated["diagnostic"]})
    output = args.output.resolve()
    relative = output.relative_to(root)
    if "work-items" not in relative.parts or not any(part.startswith("T05") for part in relative.parts):
        raise ForecastViolation("T05 diagnostic validation output is outside a T05 work-item root")
    payload = {
        "schema_version": "1.0.0", "artifact_type": "phase4_product_forecast_diagnostic_time_label_known_answer",
        "scope": args.scope, "t10_known_answer_manifest_sha256": T10_MANIFEST_SHA256,
        "forecasts": summaries, "status": "PASS",
        "producer_provenance": producer_provenance(root, relative.as_posix()),
    }
    path = resolve_inside(output, "product-known-answer.json")
    _write_same(path, payload)
    return {"status": "PASS", "terminal": "PASS", "scope": args.scope, "product_known_answer_sha256": sha256_file(path), "exit_code": 0}


def register(registry: ProviderRegistry) -> None:
    registry.register("forecast", "prepare", forecast_prepare)
    registry.register("forecast", "generate", forecast_generate)
    registry.register("forecast", "lock", forecast_lock)
    registry.register("forecast", "show", forecast_show)
