from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


AUTHORITY = {
    "path": "tasks/phase3/README.md",
    "commit": "0f62062d30af0cc676edde15849a33f5bc33a8aa",
    "sha256": "0b1bcc329c8063a8336e188e7e88b99542c038cc28a51387b81867d5953e1cdf",
}

FROZEN_INPUTS = (
    ("phase1_acceptance", "artifacts/phase-1/acceptance/phase1-acceptance.json", "959b1dddacf453dbff347786d572de4cd8c52d1b7eb2e7a3805cffa2a166bb18", "phase1_delivery_closure"),
    ("phase1_manifest", "artifacts/phase-1/baseline-v1/manifest.json", "0ddcccb72dce7662af665b369995b1c1fd28c68554b32e175f4561fc9f9683d1", "data_identity"),
    ("phase1_draws", "artifacts/phase-1/baseline-v1/draws.jsonl", "f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1", "historical_labels_only_until_pit_proven"),
    ("phase2_rule_manifest", "artifacts/phase-2/contracts/input-manifest.json", "36ad90a204a2d0ebab5ddbfff3a4246f267e02cdd2cfe961200e515c27ef90ad", "game_rule_identity"),
    ("phase2_1_acceptance", "artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/acceptance.json", "d5dde1d4488290e41998c1e7f6d04b1b3ae094408716571ceb5451324cb8e8b4", "accepted_statistical_boundary"),
    ("phase2_1_manifest", "artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/acceptance/manifest.json", "c2fb2e4a60ed214ce4648a93a1d8b11aed2ebd41b920dd549158e5adc821e3c6", "accepted_evidence_identity"),
    ("phase2_1_historical_audit", "artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/results/historical-audit.json", "a3d0f1f2dc371e3ff53256c6f09d5b47471f84567e33feaa8efa9c8349b8a8d1", "registered_historical_context"),
    ("phase2_1_power", "artifacts/phase-2.1/P2.1-R00-61a99a2c3732-i07-r02/results/power.json", "99bca12e9452435fbc32c67686d4dc905ea4771b8bb7b7d62c02983e24b98a10", "power_boundary"),
)

REQUIRED_FORBIDDEN_FIELDS = frozenset({
    "current_view_without_available_at",
    "future_draw_result",
    "post_draw_sales",
    "jackpot",
    "winner_count",
    "machine_or_ball_set_unknown",
    "global_normalization",
})

SCHEMAS = {
    "input_manifest": "input-manifest.schema.json",
    "availability_ledger": "availability-ledger.schema.json",
    "data_time_contract": "data-time-contract.schema.json",
    "preregistration": "preregistration.schema.json",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def schema_root(root: Path) -> Path:
    return root / "schemas/phase3"


def validate_schema(root: Path, kind: str, payload: Any) -> None:
    if kind not in SCHEMAS:
        raise ValueError(f"unknown Phase 3 schema kind: {kind}")
    schema = load_json(schema_root(root) / SCHEMAS[kind])
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(map(str, first.absolute_path)) or "$"
        raise ValueError(f"{kind} schema violation at {location}: {first.message}")


def _config_path(root: Path, name: str) -> Path:
    return root / "config/phase3" / name


def _read_draws(root: Path) -> list[dict[str, Any]]:
    path = root / "artifacts/phase-1/baseline-v1/draws.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _input_manifest(root: Path, draws: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for role, relative, expected_sha256, allowed_use in FROZEN_INPUTS:
        path = root / relative
        if not path.is_file() or sha256(path) != expected_sha256:
            raise ValueError(f"frozen input identity mismatch: {relative}")
        rows.append({
            "role": role,
            "path": relative,
            "sha256": expected_sha256,
            "bytes": path.stat().st_size,
            "allowed_use": allowed_use,
        })
    counts = Counter(row["game"] for row in draws)
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_input_manifest",
        "status": "frozen_candidate",
        "authority": AUTHORITY,
        "files": rows,
        "draw_inventory": {"draw_count": len(draws), "by_game": dict(sorted(counts.items())), "source_observation_count": 800},
        "game_rules": {
            "dlt": "dlt-ns-35c5-12c2-v1",
            "ssq": "ssq-ns-33c6-16c1-v1",
        },
    }


def _availability_ledger(draws: list[dict[str, Any]], input_manifest_sha256: str) -> dict[str, Any]:
    entries = []
    for draw in sorted(draws, key=lambda row: (row["game"], row["issue_id"])):
        entries.append({
            "game": draw["game"],
            "target_issue": draw["issue_id"],
            "source_field": "prior_draw_result",
            "prediction_locked_at": None,
            "available_at_utc": draw["available_at_utc"],
            "source_path": "artifacts/phase-1/baseline-v1/draws.jsonl",
            "source_sha256": "f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1",
            "evidence_method": "none",
            "eligibility": "unknown",
            "reason_code": "PIT_AVAILABILITY_UNPROVEN",
        })
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_availability_ledger",
        "status": "blocked_missing_pit_evidence",
        "input_manifest_sha256": input_manifest_sha256,
        "append_only": True,
        "entries": entries,
    }


def _data_time_contract(input_manifest_sha256: str, ledger_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_data_time_contract",
        "status": "blocked_missing_pit_evidence",
        "input_manifest_sha256": input_manifest_sha256,
        "availability_ledger_sha256": ledger_sha256,
        "feature_fields": [
            {
                "field_id": "prior_draw_result",
                "source_path": "artifacts/phase-1/baseline-v1/draws.jsonl",
                "availability_proof_required": True,
                "default_eligibility": "unknown",
                "fit_scope": "strictly_before_outer_target",
            }
        ],
        "label_fields": ["front_numbers", "back_numbers"],
        "forbidden_fields": [
            "current_view_without_available_at",
            "future_draw_result",
            "post_draw_sales",
            "jackpot",
            "winner_count",
            "machine_or_ball_set_unknown",
            "global_normalization",
        ],
        "rule_policy": "one registered rule segment per game and target issue; zero or multiple matches reject the candidate release",
        "unknown_availability_policy": "fail_closed_hold",
    }


def _preregistration(input_manifest_sha256: str, ledger_sha256: str, data_time_contract_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_preregistration",
        "status": "blocked_pending_pit_evidence",
        "formal_run_authorized": False,
        "input_manifest_sha256": input_manifest_sha256,
        "availability_ledger_sha256": ledger_sha256,
        "data_time_contract_sha256": data_time_contract_sha256,
        "games": {
            "dlt": {"outer_targets": [], "minimum_training_draws": 50, "inner_folds": 3},
            "ssq": {"outer_targets": [], "minimum_training_draws": 50, "inner_folds": 3},
        },
        "activation_requirements": [
            "all candidate feature rows have proven available_at_utc before prediction_locked_at",
            "each game has an explicit nonempty outer target list with the required training history",
            "independent method review has zero blocking findings",
        ],
        "models": {
            "M0": {"role": "permanent_champion", "required": True, "joint_probability": "uniform_fixed_cardinality_without_replacement"},
            "M1": {"role": "mandatory_challenger", "required": True, "joint_probability": "fixed_cardinality_exponential_weights", "zero_parameter_equivalence": "M0"},
            "M2": {"role": "not_opened", "required": False},
            "M3": {"role": "not_opened", "required": False},
            "M4": {"role": "not_opened", "required": False},
        },
        "metrics": {
            "primary": "relative_joint_log_score_skill_vs_M0",
            "secondary": ["inclusion_brier", "calibration", "reliability", "stability"],
            "top_1000": "diagnostic_only",
        },
        "search_policy": {"one_factor_change": True, "M1_parameter_range": "freeze_after_pit_activation", "seed_derivation": "model_game_outer_target_inner_fold"},
        "classification": ["rejected", "archived", "shadow_candidate", "not_opened", "indeterminate"],
        "forbidden_actions": ["champion_promotion", "production_prediction", "public_non_uniform_prediction", "betting", "automatic_purchase", "yield_claim"],
        "results": [],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def bootstrap_prerun_contract(root: Path) -> dict[str, Any]:
    """Write the deterministic W01-W03 candidate package without a formal release."""
    root = root.resolve()
    authority_path = root / AUTHORITY["path"]
    if sha256(authority_path) != AUTHORITY["sha256"]:
        raise ValueError("Phase 3 authority identity mismatch")
    draws = _read_draws(root)
    manifest = _input_manifest(root, draws)
    manifest_path = _config_path(root, "input-manifest.json")
    _write_json(manifest_path, manifest)
    ledger = _availability_ledger(draws, sha256(manifest_path))
    ledger_path = _config_path(root, "availability-ledger.json")
    _write_json(ledger_path, ledger)
    data_time = _data_time_contract(sha256(manifest_path), sha256(ledger_path))
    data_time_path = _config_path(root, "data-time-contract.json")
    _write_json(data_time_path, data_time)
    preregistration = _preregistration(sha256(manifest_path), sha256(ledger_path), sha256(data_time_path))
    preregistration_path = _config_path(root, "preregistration.json")
    _write_json(preregistration_path, preregistration)
    return validate_prerun_contract(root)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_prerun_contract(root: Path) -> dict[str, Any]:
    """Validate the candidate contract and report whether a formal run is authorized."""
    root = root.resolve()
    payloads = {
        "input_manifest": load_json(_config_path(root, "input-manifest.json")),
        "availability_ledger": load_json(_config_path(root, "availability-ledger.json")),
        "data_time_contract": load_json(_config_path(root, "data-time-contract.json")),
        "preregistration": load_json(_config_path(root, "preregistration.json")),
    }
    for kind, payload in payloads.items():
        validate_schema(root, kind, payload)

    manifest = payloads["input_manifest"]
    if manifest["authority"] != AUTHORITY:
        raise ValueError("Phase 3 authority reference mismatch")
    if sha256(root / AUTHORITY["path"]) != AUTHORITY["sha256"]:
        raise ValueError("Phase 3 authority file identity mismatch")
    expected_roles = [row[0] for row in FROZEN_INPUTS]
    if [row["role"] for row in manifest["files"]] != expected_roles:
        raise ValueError("input manifest role coverage mismatch")
    for expected, actual in zip(FROZEN_INPUTS, manifest["files"], strict=True):
        role, relative, expected_sha256, allowed_use = expected
        if actual["path"] != relative or actual["sha256"] != expected_sha256 or actual["allowed_use"] != allowed_use:
            raise ValueError(f"input manifest mismatch: {role}")
        path = root / relative
        if not path.is_file() or sha256(path) != expected_sha256 or actual["bytes"] != path.stat().st_size:
            raise ValueError(f"frozen input identity mismatch: {relative}")

    draws = _read_draws(root)
    counts = Counter(row["game"] for row in draws)
    if manifest["draw_inventory"] != {"draw_count": 400, "by_game": {"dlt": 200, "ssq": 200}, "source_observation_count": 800}:
        raise ValueError("input manifest draw inventory mismatch")
    if len(draws) != 400 or dict(counts) != {"dlt": 200, "ssq": 200}:
        raise ValueError("frozen draw inventory mismatch")

    manifest_sha256 = sha256(_config_path(root, "input-manifest.json"))
    ledger = payloads["availability_ledger"]
    if ledger["input_manifest_sha256"] != manifest_sha256:
        raise ValueError("availability ledger input manifest mismatch")
    expected_ledger_keys = {(row["game"], row["issue_id"], "prior_draw_result") for row in draws}
    observed_ledger_keys = {(row["game"], row["target_issue"], row["source_field"]) for row in ledger["entries"]}
    if observed_ledger_keys != expected_ledger_keys or len(ledger["entries"]) != len(expected_ledger_keys):
        raise ValueError("availability ledger coverage mismatch")
    eligible = 0
    for row in ledger["entries"]:
        if row["eligibility"] == "eligible":
            if not row["prediction_locked_at"] or not row["available_at_utc"]:
                raise ValueError("eligible availability row lacks timestamps")
            if _parse_time(row["available_at_utc"]) >= _parse_time(row["prediction_locked_at"]):
                raise ValueError("availability row violates point-in-time ordering")
            eligible += 1
        elif row["eligibility"] == "unknown" and row["reason_code"] != "PIT_AVAILABILITY_UNPROVEN":
            raise ValueError("unknown availability row lacks fail-closed reason")

    ledger_sha256 = sha256(_config_path(root, "availability-ledger.json"))
    data_time = payloads["data_time_contract"]
    if data_time["input_manifest_sha256"] != manifest_sha256 or data_time["availability_ledger_sha256"] != ledger_sha256:
        raise ValueError("data-time contract identity mismatch")
    if data_time["unknown_availability_policy"] != "fail_closed_hold":
        raise ValueError("data-time contract does not fail closed")
    if not REQUIRED_FORBIDDEN_FIELDS.issubset(set(data_time["forbidden_fields"])):
        raise ValueError("forbidden field coverage is incomplete")

    preregistration = payloads["preregistration"]
    if preregistration["input_manifest_sha256"] != manifest_sha256 or preregistration["availability_ledger_sha256"] != ledger_sha256:
        raise ValueError("preregistration input identity mismatch")
    if preregistration["data_time_contract_sha256"] != sha256(_config_path(root, "data-time-contract.json")):
        raise ValueError("preregistration data-time identity mismatch")
    if preregistration["results"]:
        raise ValueError("results are prohibited before Phase 3 formal release")
    if preregistration["models"]["M0"]["role"] != "permanent_champion" or not preregistration["models"]["M1"]["required"]:
        raise ValueError("M0/M1 preregistration contract mismatch")
    if any(preregistration["models"][model]["role"] != "not_opened" for model in ("M2", "M3", "M4")):
        raise ValueError("M2-M4 preregistration contract mismatch")
    if preregistration["metrics"]["primary"] != "relative_joint_log_score_skill_vs_M0" or preregistration["metrics"]["top_1000"] != "diagnostic_only":
        raise ValueError("preregistration metric contract mismatch")
    if preregistration["formal_run_authorized"]:
        raise ValueError("formal run cannot be authorized by the candidate preregistration")

    coverage = eligible / len(ledger["entries"])
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_prerun_contract_receipt",
        "status": "HOLD" if coverage < 1.0 else "READY",
        "terminal": "HOLD_PENDING_PIT_EVIDENCE" if coverage < 1.0 else "READY_FOR_RESULTS_BLIND_FREEZE",
        "formal_run_authorized": coverage == 1.0 and preregistration["status"] == "results_blind",
        "hold_reasons": ["PIT_AVAILABILITY_UNPROVEN"] if coverage < 1.0 else [],
        "metrics": {
            "input_identity_coverage": 1.0,
            "draw_inventory_coverage": 1.0,
            "availability_ledger_coverage": 1.0,
            "eligible_feature_coverage": coverage,
            "formal_result_count": 0,
        },
    }
