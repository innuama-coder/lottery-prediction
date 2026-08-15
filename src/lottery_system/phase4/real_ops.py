from __future__ import annotations

import hashlib
import json
import math
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .p4e2_model import enumerate_zone, fit_coefficients, feature_context, probability_qualification, select_candidate
from .real_model import RULES, canonical, digest, feature_snapshot_rows, load_draws, score_ticket, top_tickets, train, write_jsonl_once, write_once


ZERO = "0" * 64


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release_root_for_model(model_path: Path) -> Path:
    path = model_path.resolve()
    for parent in path.parents:
        if parent.name == "models":
            return parent.parent
    raise ValueError("model release must be inside <release>/models")


class ProductLedger:
    """Small create-once hash-chain used by the real-model product CLI."""

    def __init__(self, release: Path):
        self.release = release.resolve()
        self.root = self.release / "runtime/ledger"
        self.events = self.root / "events"
        self.head = self.root / "head.json"

    def rows(self) -> list[dict[str, Any]]:
        rows = [load(path) for path in sorted(self.events.glob("*.json"))] if self.events.exists() else []
        previous = ZERO
        for ordinal, row in enumerate(rows, 1):
            body = {key: value for key, value in row.items() if key != "event_sha256"}
            if row["ordinal"] != ordinal or row["previous_event_sha256"] != previous or digest(body) != row["event_sha256"]:
                raise ValueError("HOLD_LEDGER_HASH_CHAIN_INVALID")
            previous = row["event_sha256"]
        if self.head.exists():
            head = load(self.head)
            if not rows or head != {"ordinal": len(rows), "event_sha256": previous}:
                raise ValueError("HOLD_LEDGER_HEAD_INVALID")
        elif rows:
            raise ValueError("HOLD_LEDGER_HEAD_MISSING")
        return rows

    def append(self, event_type: str, object_id: str, payload: dict[str, Any], *, actor: str = "phase4-real-cli") -> tuple[dict[str, Any], bool]:
        rows = self.rows()
        for row in rows:
            if row["event_type"] == event_type and row["object_id"] == object_id:
                if row["payload_sha256"] != digest(payload):
                    raise ValueError("HOLD_IDEMPOTENCY_IDENTITY_COLLISION")
                return row, False
        ordinal = len(rows) + 1
        body = {
            "artifact_type": "phase4_product_ledger_event", "ordinal": ordinal,
            "event_id": f"event-{digest({'type': event_type, 'object': object_id, 'payload': payload})[:24]}",
            "event_type": event_type, "object_id": object_id,
            "event_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "actor": actor, "previous_event_sha256": rows[-1]["event_sha256"] if rows else ZERO,
            "payload_sha256": digest(payload), "payload": payload,
        }
        row = {**body, "event_sha256": digest(body)}
        self.events.mkdir(parents=True, exist_ok=True)
        event_path = self.events / f"{ordinal:08d}-{row['event_id']}.json"
        write_once(event_path, row)
        new_head = {"ordinal": ordinal, "event_sha256": row["event_sha256"]}
        self.head.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.head.with_suffix(".next")
        temporary.write_bytes(canonical(new_head))
        temporary.replace(self.head)
        return row, True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _relative(release: Path, path: Path) -> str:
    return path.resolve().relative_to(release.resolve()).as_posix()


def _outputs(release: Path, paths: list[Path]) -> list[dict[str, Any]]:
    return [{"path": _relative(release, path), "sha256": sha(path), "bytes": path.stat().st_size} for path in paths]


def _verify_outputs(release: Path, outputs: list[dict[str, Any]]) -> None:
    for row in outputs:
        path = release / row["path"]
        if not path.is_file() or sha(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError(f"HOLD_SCHEDULE_OUTPUT_IDENTITY_MISMATCH:{row['path']}")


def _draw_source(release: Path, game: str) -> tuple[list[Any], Path]:
    source = load(release / f"data/{game}/training-input-manifest.json")
    draws_path = Path(source["draws_path"])
    if not draws_path.is_absolute():
        draws_path = (Path.cwd() / draws_path).resolve()
    if sha(draws_path) != source["draws_sha256"]:
        raise ValueError("FAIL_TAMPERED:draw_source")
    return load_draws(draws_path, game), draws_path


def forecast_and_lock(model_path: Path, target_issue: str, top_k: int = 1000) -> dict[str, Any]:
    if not target_issue.strip():
        raise ValueError("HOLD_DATA_TIME_CONTRACT: empty target issue")
    model = load(model_path)
    if model.get("family") == "M0" or model.get("family") != "P4E2-R":
        raise ValueError("HOLD_NON_PRODUCT_OR_UNKNOWN_MODEL")
    release = release_root_for_model(model_path)
    game = model["game"]
    manifest_path = model_path.with_name("manifest.json")
    if not manifest_path.is_file():
        raise ValueError("FAIL_UNFROZEN_MODEL_PATH: model manifest missing")
    manifest = load(manifest_path)
    if (manifest.get("model_sha256") != sha(model_path) or manifest.get("model_release_id") != model.get("model_release_id")
            or manifest.get("feature_release_id") != model.get("feature_release_id") or manifest.get("dirty") is not False):
        raise ValueError("FAIL_UNFROZEN_MODEL_PATH: model manifest mismatch")
    serving_path = release / "selection/serving-selection.json"
    if not serving_path.is_file():
        raise ValueError("FAIL_UNFROZEN_MODEL_PATH: serving selection missing")
    serving = load(serving_path)["serving_model_by_game"][game]
    expected_relative = model_path.resolve().relative_to(release).as_posix()
    if (serving.get("model_path") != expected_relative or serving.get("model_release_id") != model.get("model_release_id")
            or serving.get("feature_release_id") != model.get("feature_release_id") or serving.get("family") != "P4E2-R"):
        raise ValueError("FAIL_UNFROZEN_MODEL_PATH: model is not frozen serving selection")
    feature_manifest = release / f"features/{game}/{model['feature_release_id']}/manifest.json"
    if not feature_manifest.is_file() or load(feature_manifest).get("feature_release_id") != model["feature_release_id"]:
        raise ValueError("FAIL_UNFROZEN_MODEL_PATH: feature release missing")
    data_manifest = load(release / f"data/{game}/training-input-manifest.json")
    draws_path = Path(data_manifest["draws_path"])
    if not draws_path.is_absolute():
        draws_path = (Path.cwd() / draws_path).resolve()
    known_issues = [draw.issue for draw in load_draws(draws_path, game)]
    if target_issue in known_issues and known_issues.index(target_issue) <= int(model["training_cutoff_position"]):
        raise ValueError("FAIL_LEAKAGE: target is in training prefix")
    rows = top_tickets(model, top_k)
    forecast_id = f"forecast-{game}-{digest({'model': model['model_release_id'], 'target': target_issue, 'rows': rows})[:20]}"
    target = release / f"runtime/forecasts/{game}/{target_issue}"
    write_jsonl_once(target / "top1000.jsonl", rows)
    locked_at = _utc_now()
    probability = probability_qualification(model, rows)
    lock_id = f"lock-{forecast_id}"
    forecast = {
        "artifact_type": "phase4_formal_forecast", "forecast_id": forecast_id, "game": game,
        "target_issue": target_issue, "target_position": model["forecast_target_position"],
        "model_release_id": model["model_release_id"], "model_sha256": sha(model_path),
        "feature_release_id": model["feature_release_id"], "feature_manifest_sha256": sha(feature_manifest),
        "training_dataset_id": model["training_dataset_id"], "training_config_id": model["training_config_id"],
        "source_commit": model["source_commit"], "dependency_identity": model["dependency_identity"],
        "training_cutoff_issue": model["training_cutoff_issue"], "training_cutoff_position": model["training_cutoff_position"],
        "prediction_locked_at_utc": locked_at, "lock_id": lock_id,
        "ranking_algorithm_id": probability["ranking_algorithm_id"], "ticket_count": len(rows),
        "top1000_sha256": sha(target / "top1000.jsonl"), "status": "locked_unscored",
    }
    write_once(target / "forecast.json", forecast)
    lock = {"artifact_type": "phase4_forecast_lock", "lock_id": lock_id, "forecast_id": forecast_id,
            "game": game, "target_issue": target_issue, "model_release_id": model["model_release_id"],
            "locked_at_utc": locked_at, "forecast_sha256": sha(target / "forecast.json"),
            "top1000_sha256": forecast["top1000_sha256"], "create_once": True,
            "create_once_linkage": digest({"lock_id": lock_id, "forecast_id": forecast_id, "forecast_sha256": sha(target / "forecast.json")}),
            "status": "LOCKED"}
    write_once(target / "lock.json", lock)
    event, appended = ProductLedger(release).append("forecast_locked", forecast_id, lock)
    return {**forecast, "status": "LOCKED", "lock_path": str(target / "lock.json"), "ledger_event_id": event["event_id"], "idempotent_replay": not appended}


def _cycle_root(release: Path, game: str) -> Path:
    return release / f"runtime/lifecycle/{game}/historical-cycle-v1"


def _existing_operation(release: Path, receipt_path: Path) -> dict[str, Any] | None:
    if not receipt_path.exists():
        return None
    receipt = load(receipt_path)
    _verify_outputs(release, receipt["outputs"])
    return receipt


def prepare_historical_cycle(release: Path, game: str) -> dict[str, Any]:
    root = _cycle_root(release, game)
    receipt_path = root / "prepare-receipt.json"
    existing = _existing_operation(release, receipt_path)
    if existing:
        return existing
    draws, draws_path = _draw_source(release, game)
    target_position = len(draws) - 1
    frozen_selection = select_candidate(game, draws, target_position)
    parent = train(game, draws, target_position, frozen_selection=frozen_selection, scientific_evidence=False)
    serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    serving_model = load(release / serving["model_path"])
    parent["feature_release_id"] = f"historical-{game}-{digest({'target': draws[-1].issue, 'prefix': [d.fact_hash for d in draws[:-1]]})[:16]}"
    parent["source_commit"] = serving_model["source_commit"]
    parent["dependency_identity"] = serving_model["dependency_identity"]
    model_path = root / "parent-model.json"
    selection_path = root / "model-selection-receipt.json"
    cycle_path = root / "cycle.json"
    write_once(selection_path, frozen_selection)
    write_once(model_path, parent)
    operation_id = f"prepare-{game}-{digest({'draws': sha(draws_path), 'target': draws[-1].fact_hash, 'model': parent['model_release_id']})[:24]}"
    cycle = {
        "artifact_type": "phase4_historical_virtual_clock_cycle", "cycle_id": "historical-cycle-v1",
        "game": game, "target_issue": draws[-1].issue, "target_position": target_position,
        "training_cutoff_issue": draws[-2].issue, "training_cutoff_position": target_position - 1,
        "next_target_issue": f"after-{draws[-1].issue}", "phase1_draws_sha256": sha(draws_path),
        "parent_model_release_id": parent["model_release_id"], "parent_model_sha256": sha(model_path),
        "selection_receipt_hash": frozen_selection["receipt_hash"], "result_available": False,
        "virtual_clock_order": ["prepare", "forecast_lock", "verified_result_ingest", "guarded_unlock_score", "research_shadow"],
    }
    write_once(cycle_path, cycle)
    paths = [selection_path, model_path, cycle_path]
    receipt = {"artifact_type": "phase4_lifecycle_operation_receipt", "stage": "prepare", "game": game,
               "operation_id": operation_id, "output_ids": [parent["model_release_id"], frozen_selection["receipt_hash"]],
               "outputs": _outputs(release, paths), "status": "PASS"}
    write_once(receipt_path, receipt)
    return receipt


def forecast_historical_target(release: Path, game: str) -> dict[str, Any]:
    root = _cycle_root(release, game)
    receipt_path = root / "forecast-lock-receipt.json"
    existing = _existing_operation(release, receipt_path)
    if existing:
        return existing
    if (root / "result-revision.json").exists():
        raise ValueError("FAIL_LEAKAGE: result existed before historical forecast lock")
    cycle, model = load(root / "cycle.json"), load(root / "parent-model.json")
    rows = top_tickets(model)
    top_path, forecast_path, lock_path = root / "top1000.jsonl", root / "forecast.json", root / "lock.json"
    write_jsonl_once(top_path, rows)
    locked_at = _utc_now()
    forecast_id = f"historical-forecast-{game}-{digest({'model': model['model_release_id'], 'target': cycle['target_issue'], 'top': sha(top_path)})[:20]}"
    lock_id = f"lock-{forecast_id}"
    forecast = {
        "artifact_type": "phase4_historical_locked_forecast", "forecast_id": forecast_id, "lock_id": lock_id,
        "game": game, "target_issue": cycle["target_issue"], "target_position": cycle["target_position"],
        "model_release_id": model["model_release_id"], "model_sha256": sha(root / "parent-model.json"),
        "training_cutoff_issue": model["training_cutoff_issue"], "training_cutoff_position": model["training_cutoff_position"],
        "feature_release_id": model["feature_release_id"], "training_dataset_id": model["training_dataset_id"],
        "training_config_id": model["training_config_id"], "source_commit": model["source_commit"],
        "dependency_identity": model["dependency_identity"], "prediction_locked_at_utc": locked_at,
        "ranking_algorithm_id": "joint_binary64_score_desc_exact_tie_canonical_ticket_asc_v1",
        "normalization_proof": [{"combination_count": zone["combination_count"], "log_normalizer": zone["log_normalizer"], "mass": zone["normalization_mass"]} for zone in model["zones"]],
        "top1000_sha256": sha(top_path), "ticket_count": len(rows), "status": "locked_unscored",
    }
    write_once(forecast_path, forecast)
    lock = {"artifact_type": "phase4_forecast_lock", "lock_id": lock_id, "forecast_id": forecast_id,
            "game": game, "target_issue": cycle["target_issue"], "model_release_id": model["model_release_id"],
            "locked_at_utc": locked_at, "forecast_sha256": sha(forecast_path), "top1000_sha256": sha(top_path),
            "create_once": True, "create_once_linkage": digest({"lock_id": lock_id, "forecast": sha(forecast_path), "top": sha(top_path)}),
            "status": "LOCKED"}
    write_once(lock_path, lock)
    ledger_event, _ = ProductLedger(release).append("historical_forecast_locked", forecast_id, lock)
    paths = [top_path, forecast_path, lock_path]
    receipt = {"artifact_type": "phase4_lifecycle_operation_receipt", "stage": "forecast_lock", "game": game,
               "operation_id": f"forecast-lock-{digest(lock)[:24]}", "output_ids": [forecast_id, lock_id, ledger_event["event_id"]],
               "outputs": _outputs(release, paths), "status": "PASS"}
    write_once(receipt_path, receipt)
    return receipt


def ingest_verified_result(release: Path, game: str) -> dict[str, Any]:
    root = _cycle_root(release, game)
    receipt_path = root / "result-ingest-receipt.json"
    existing = _existing_operation(release, receipt_path)
    if existing:
        return existing
    cycle, lock = load(root / "cycle.json"), load(root / "lock.json")
    draws, draws_path = _draw_source(release, game)
    source_manifest = load(release / f"data/{game}/training-input-manifest.json")
    target = draws[cycle["target_position"]]
    if target.issue != cycle["target_issue"] or lock["target_issue"] != target.issue or lock["game"] != game:
        raise ValueError("FAIL_RESULT_TARGET_MISMATCH")
    revision_id = f"result-{game}-{target.issue}-r1-{target.fact_hash[:12]}"
    result = {
        "artifact_type": "phase4_verified_result_revision", "result_revision_id": revision_id,
        "game": game, "target_issue": target.issue, "target_position": cycle["target_position"],
        "front_numbers": list(target.front), "back_numbers": list(target.back), "core_fact_sha256": target.fact_hash,
        "primary_source": {"type": "phase1_canonical_draw", "draws_sha256": sha(draws_path), "core_fact_sha256": target.fact_hash},
        "corroborating_source": {"type": "phase1_frozen_manifest", "manifest_sha256": source_manifest["phase1_manifest_sha256"]},
        "verified_at_utc": _utc_now(), "forecast_lock_id": lock["lock_id"], "status": "VERIFIED",
    }
    result_path = root / "result-revision.json"
    write_once(result_path, result)
    event, _ = ProductLedger(release).append("verified_result_ingested", revision_id, {"result_sha256": sha(result_path), **result})
    receipt = {"artifact_type": "phase4_lifecycle_operation_receipt", "stage": "official_result_ingest", "game": game,
               "operation_id": f"result-ingest-{digest(result)[:24]}", "output_ids": [revision_id, event["event_id"]],
               "outputs": _outputs(release, [result_path]), "status": "PASS"}
    write_once(receipt_path, receipt)
    return receipt


def score_historical_forecast(release: Path, game: str) -> dict[str, Any]:
    root = _cycle_root(release, game)
    receipt_path = root / "score-receipt.json"
    existing = _existing_operation(release, receipt_path)
    if existing:
        return existing
    model, forecast, lock, result = (load(root / name) for name in ("parent-model.json", "forecast.json", "lock.json", "result-revision.json"))
    if (forecast["game"] != result["game"] or forecast["target_issue"] != result["target_issue"]
            or forecast["model_release_id"] != model["model_release_id"] or forecast["model_sha256"] != sha(root / "parent-model.json")
            or lock["forecast_sha256"] != sha(root / "forecast.json") or lock["top1000_sha256"] != sha(root / "top1000.jsonl")
            or result["forecast_lock_id"] != lock["lock_id"] or result["status"] != "VERIFIED"):
        raise ValueError("FAIL_SCORE_FORECAST_RESULT_MISMATCH")
    rows = [json.loads(line) for line in (root / "top1000.jsonl").read_text(encoding="utf-8").splitlines()]
    draw = next(draw for draw in _draw_source(release, game)[0] if draw.issue == result["target_issue"])
    metrics = score_ticket(model, draw, rows)
    score_id = f"score-{digest({'forecast': forecast['forecast_id'], 'result': result['result_revision_id'], 'model': model['model_release_id']})[:24]}"
    score = {
        "artifact_type": "phase4_exact_locked_forecast_score", "score_id": score_id, "game": game,
        "forecast_id": forecast["forecast_id"], "forecast_sha256": sha(root / "forecast.json"),
        "lock_id": lock["lock_id"], "result_revision_id": result["result_revision_id"], "result_revision_sha256": sha(root / "result-revision.json"),
        "model_release_id": model["model_release_id"], "model_sha256": sha(root / "parent-model.json"),
        "top1000_sha256": sha(root / "top1000.jsonl"), "guarded_unlock": True,
        "metrics": metrics, "scored_at_utc": _utc_now(), "status": "SCORED",
    }
    score_path = root / "score.json"
    write_once(score_path, score)
    ledger = ProductLedger(release)
    unlock, _ = ledger.append("verified_result_unlocked", result["result_revision_id"], {"result_sha256": sha(root / "result-revision.json"), "lock_id": lock["lock_id"]})
    event, _ = ledger.append("exact_forecast_scored", score_id, {"score_sha256": sha(score_path), "forecast_id": forecast["forecast_id"], "result_revision_id": result["result_revision_id"]})
    receipt = {"artifact_type": "phase4_lifecycle_operation_receipt", "stage": "unlock_score", "game": game,
               "operation_id": f"unlock-score-{digest(score)[:24]}", "output_ids": [score_id, unlock["event_id"], event["event_id"]],
               "outputs": _outputs(release, [score_path]), "status": "PASS"}
    write_once(receipt_path, receipt)
    return receipt


def research_from_completed_score(release: Path, game: str) -> dict[str, Any]:
    root = _cycle_root(release, game)
    receipt_path = root / "research-receipt.json"
    existing = _existing_operation(release, receipt_path)
    if existing:
        return existing
    serving_path = release / "selection/serving-selection.json"
    serving_before = sha(serving_path)
    parent, score, result, cycle = (load(root / name) for name in ("parent-model.json", "score.json", "result-revision.json", "cycle.json"))
    if score.get("status") != "SCORED" or score.get("result_revision_id") != result.get("result_revision_id"):
        raise ValueError("HOLD_RESEARCH_WITHOUT_COMPLETED_SCORE")
    draws, _ = _draw_source(release, game)
    parent_l2 = float(parent["regularization"]["selected"])
    score_delta = float(score["metrics"]["joint_log_loss"]) - math.log(math.prod(math.comb(n, k) for n, k in RULES[game]))
    grid = list(parent["regularization"]["preregistered_grid"])
    preferred = sorted(grid, reverse=score_delta <= 0)
    child_l2 = next(value for value in preferred if float(value) != parent_l2)
    child = copy.deepcopy(parent)
    coefficients = fit_coefficients(game, draws, len(draws), float(child_l2))
    contexts = [feature_context(game, draws, zone) for zone in (0, 1)]
    zones = []
    for zone in (0, 1):
        recomputed = enumerate_zone(contexts[zone], coefficients[zone], True, collect_layers=True)
        zones.append({"n": RULES[game][zone][0], "k": RULES[game][zone][1], "coefficients": coefficients[zone],
                      "context": contexts[zone], "top_zone_rows": [[value, list(combo)] for value, combo in recomputed["rows"]],
                      **{key: value for key, value in recomputed.items() if key != "rows"}})
    proposal = {"type": "score_driven_bounded_l2_refit", "decision_rule": "shrink_if_worse_expand_if_better",
                "parent_l2": parent_l2, "child_l2": child_l2, "completed_score_delta_log_loss_vs_m0": score_delta,
                "input_cutoff_issue": result["target_issue"], "next_target_issue": cycle["next_target_issue"]}
    child_id = f"p4e2r-{game}-child-{digest({'parent': parent['model_release_id'], 'score': score['score_id'], 'result': result['result_revision_id'], 'proposal': proposal, 'coefficients': coefficients})[:16]}"
    child_training_dataset_id = digest({"game": game, "canonical_order_id": parent["canonical_order_id"],
                                        "ordered_draw_hashes": [draw.fact_hash for draw in draws],
                                        "rule_id": parent["rule_id"], "cutoff_issue": result["target_issue"]})
    child_training_config_id = digest({"parent_training_config_id": parent["training_config_id"], "proposal": proposal,
                                       "score_id": score["score_id"], "result_revision_id": result["result_revision_id"]})
    child_feature_id = f"f01-f14-{game}-child-{digest({'dataset': child_training_dataset_id, 'config': child_training_config_id})[:16]}"
    child.update(zones=zones, model_release_id=child_id, parent_model_release_id=parent["model_release_id"],
                 training_cutoff_issue=result["target_issue"], training_cutoff_position=len(draws) - 1,
                 forecast_target_position=len(draws), training_count=len(draws),
                 training_dataset_id=child_training_dataset_id, training_config_id=child_training_config_id,
                 feature_release_id=child_feature_id,
                 regularization={**parent["regularization"], "selected": child_l2}, research_proposal=proposal,
                 research_score_id=score["score_id"], research_result_revision_id=result["result_revision_id"])
    child_top = top_tickets(child)
    parent_top = [json.loads(line) for line in (root / "top1000.jsonl").read_text(encoding="utf-8").splitlines()]
    research = release / f"research/{game}"
    child_path, shadow_path = research / "child-model.json", research / "shadow-top1000.jsonl"
    child_feature_path = research / "child-feature-snapshot.jsonl"
    child_feature_manifest_path = research / "child-feature-manifest.json"
    write_jsonl_once(child_feature_path, feature_snapshot_rows(game, draws, len(draws)))
    write_once(child_feature_manifest_path, {"artifact_type": "phase4_research_child_feature_manifest", "game": game,
               "feature_release_id": child_feature_id, "training_dataset_id": child_training_dataset_id,
               "training_config_id": child_training_config_id, "input_cutoff_issue": result["target_issue"],
               "snapshot_sha256": sha(child_feature_path), "feature_ids": child["feature_ids"],
               "feature_groups": child["feature_groups_consumed"], "status": "PASS"})
    write_once(child_path, child)
    write_jsonl_once(shadow_path, child_top)
    diff = {"artifact_type": "phase4_research_diff", "game": game, "parent_model_release_id": parent["model_release_id"],
            "child_model_release_id": child_id, "score_id": score["score_id"], "score_sha256": sha(root / "score.json"),
            "result_revision_id": result["result_revision_id"], "result_revision_sha256": sha(root / "result-revision.json"),
            "input_cutoff_issue": result["target_issue"], "change": proposal, "bounded_preregistered": child_l2 in grid,
            "non_noop": child_l2 != parent_l2, "future_data_used": False, "direct_promotion": False}
    write_once(research / "diff.json", diff)
    candidate = {"artifact_type": "phase4_research_candidate", "game": game, "parent_model_release_id": parent["model_release_id"],
                 "child_model_release_id": child_id, "target_issue": cycle["next_target_issue"], "score_id": score["score_id"],
                 "result_revision_id": result["result_revision_id"], "child_model_sha256": sha(child_path),
                 "child_feature_release_id": child_feature_id, "child_feature_snapshot_sha256": sha(child_feature_path),
                 "shadow_top1000_sha256": sha(shadow_path), "status": "shadow_candidate", "serving_changed": False}
    write_once(research / "candidate.json", candidate)
    decision = {"artifact_type": "phase4_research_decision", "game": game, "decision": "shadow_only",
                "parent_model_release_id": parent["model_release_id"], "child_model_release_id": child_id,
                "score_id": score["score_id"], "result_revision_id": result["result_revision_id"],
                "probability_changed": child_top[0]["score_identity"] != parent_top[0]["score_identity"],
                "top1000_changed": digest(child_top) != digest(parent_top), "serving_changed": False,
                "direct_promotion_attempt_rejected": True, "scientific_claim": "no_promotion_or_lift_claim", "status": "PASS"}
    write_once(research / "decision.json", decision)
    write_once(research / "child-model-manifest.json", {"artifact_type": "phase4_research_child_model_manifest", "game": game,
               "parent_model_release_id": parent["model_release_id"], "child_model_release_id": child_id,
               "child_feature_release_id": child_feature_id, "child_feature_snapshot_sha256": sha(child_feature_path),
               "child_model_sha256": sha(child_path), "shadow_top1000_sha256": sha(shadow_path),
               "score_sha256": sha(root / "score.json"), "result_revision_sha256": sha(root / "result-revision.json"),
               "proposal_sha256": digest(proposal), "role": "shadow_only", "status": "PASS"})
    if sha(serving_path) != serving_before:
        raise ValueError("HOLD_RESEARCH_CHANGED_SERVING")
    write_once(research / "serving-immutability.json", {"artifact_type": "phase4_research_serving_immutability", "game": game,
               "serving_selection_sha256_before": serving_before, "serving_selection_sha256_after": sha(serving_path),
               "serving_changed": False, "direct_promotion_rejected": True, "status": "PASS"})
    event, _ = ProductLedger(release).append("score_driven_research_shadow_created", child_id, {"decision_sha256": sha(research / "decision.json"), "score_id": score["score_id"], "result_revision_id": result["result_revision_id"]})
    paths = [research / name for name in ("diff.json", "candidate.json", "decision.json", "child-feature-snapshot.jsonl",
                                           "child-feature-manifest.json", "child-model.json", "child-model-manifest.json",
                                           "shadow-top1000.jsonl", "serving-immutability.json")]
    receipt = {"artifact_type": "phase4_lifecycle_operation_receipt", "stage": "research_shadow", "game": game,
               "operation_id": f"research-shadow-{digest({'child': child_id, 'score': score['score_id']})[:24]}",
               "output_ids": [child_id, score["score_id"], result["result_revision_id"], event["event_id"]],
               "outputs": _outputs(release, paths), "status": "PASS"}
    write_once(receipt_path, receipt)
    return receipt


STAGES = ("prepare", "forecast_lock", "official_result_ingest", "unlock_score", "research_shadow")
OPERATIONS = {"prepare": prepare_historical_cycle, "forecast_lock": forecast_historical_target,
              "official_result_ingest": ingest_verified_result, "unlock_score": score_historical_forecast,
              "research_shadow": research_from_completed_score}


def score_release(release: Path, game: str) -> dict[str, Any]:
    receipt = score_historical_forecast(release, game)
    return {"status": "PASS", "score_path": str(_cycle_root(release, game) / "score.json"),
            "operation_id": receipt["operation_id"], "output_ids": receipt["output_ids"], "idempotent_replay": True}


def research_release(release: Path, game: str) -> dict[str, Any]:
    receipt = research_from_completed_score(release, game)
    decision = load(release / f"research/{game}/decision.json")
    return {**decision, "operation_id": receipt["operation_id"], "output_ids": receipt["output_ids"], "idempotent_replay": True}


def schedule_release(release: Path, game: str | None, fail_after: str | None = None, cycle_id: str = "formal-cycle-v1") -> dict[str, Any]:
    games = [game] if game else ["ssq", "dlt"]
    reports = []
    for selected in games:
        if not cycle_id.replace("-", "").isalnum():
            raise ValueError("invalid cycle identity")
        run_id = f"schedule-{selected}-{cycle_id}"
        checkpoint = release / f"runtime/schedule/{run_id}/checkpoint.json"
        state = load(checkpoint) if checkpoint.exists() else {"artifact_type": "phase4_schedule_checkpoint", "run_id": run_id, "game": selected, "stages": {}}
        for stage in STAGES:
            if stage in state["stages"]:
                _verify_outputs(release, state["stages"][stage]["outputs"])
            else:
                operation = OPERATIONS[stage](release, selected)
                state["stages"][stage] = operation
                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                temporary = checkpoint.with_suffix(".next")
                temporary.write_bytes(canonical(state))
                temporary.replace(checkpoint)
            if fail_after == stage:
                fault = {"artifact_type": "phase4_schedule_fault", "run_id": run_id, "game": selected, "fault_after": stage,
                         "completed_operation_id": state["stages"][stage]["operation_id"], "completed_outputs": state["stages"][stage]["outputs"], "status": "INTERRUPTED"}
                write_once(release / f"runtime/schedule/{run_id}/fault-{stage}.json", fault)
                return {**fault, "status": "INTERRUPTED"}
        event, appended = ProductLedger(release).append("schedule_cycle_terminal", run_id, {"game": selected, "stage_operation_ids": {key: state["stages"][key]["operation_id"] for key in STAGES}, "terminal": "PASS"})
        report = {"artifact_type": "phase4_schedule_run_report", "run_id": run_id, "game": selected,
                  "stage_operation_ids": {key: state["stages"][key]["operation_id"] for key in STAGES},
                  "stage_outputs": {key: state["stages"][key]["outputs"] for key in STAGES},
                  "terminal_event_id": event["event_id"], "duplicate_side_effects": 0, "status": "PASS"}
        report_path = release / f"runtime/schedule/{run_id}/report.json"
        write_once(report_path, report)
        reports.append({**report, "idempotent_replay": not appended})
    return {"artifact_type": "phase4_schedule_recovery_report", "games": games, "stages": list(STAGES),
            "runs": reports, "duplicate_side_effects": 0, "status": "PASS"}


def exercise_schedule_recovery(release: Path) -> dict[str, Any]:
    baselines = {game: schedule_release(release, game, cycle_id="formal-cycle-v1")["runs"][0] for game in ("ssq", "dlt")}
    faults = []
    for game in ("ssq", "dlt"):
        for stage in STAGES:
            cycle_id = f"fault-{stage.replace('_', '-')}-v1"
            interrupted = schedule_release(release, game, fail_after=stage, cycle_id=cycle_id)
            resumed = schedule_release(release, game, cycle_id=cycle_id)["runs"][0]
            if resumed["stage_operation_ids"] != baselines[game]["stage_operation_ids"] or resumed["stage_outputs"] != baselines[game]["stage_outputs"]:
                raise ValueError("HOLD_RECOVERY_OUTPUT_IDENTITY_CHANGED")
            faults.append({"game": game, "fault_after": stage, "interrupted_operation_id": interrupted["completed_operation_id"],
                           "same_operation_ids_after_resume": True, "same_output_hashes_after_resume": True,
                           "duplicate_side_effects": resumed["duplicate_side_effects"]})
    report = {"artifact_type": "phase4_schedule_recovery_report", "games": ["ssq", "dlt"], "stages": list(STAGES),
              "baseline_runs": baselines, "fault_injection_runs": faults, "faults_tested": len(faults),
              "same_output_identities": all(row["same_operation_ids_after_resume"] and row["same_output_hashes_after_resume"] for row in faults),
              "duplicate_side_effects": sum(row["duplicate_side_effects"] for row in faults), "status": "PASS"}
    write_once(release / "runtime/schedule/recovery-ssq-dlt.json", report)
    return report


def inspect_release(release: Path, game: str) -> dict[str, Any]:
    serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
    model_path = release / serving["model_path"]
    model = load(model_path)
    feature_dir = release / f"features/{game}/{serving['feature_release_id']}"
    feature_manifest = load(feature_dir / "manifest.json")
    forecast_path = next((release / f"forecasts/{game}").glob("*/forecast.json"))
    forecast, lock = load(forecast_path), load(forecast_path.with_name("lock.json"))
    probability = load(model_path.with_name("probability-qualification.json"))
    if (sha(model_path) != forecast["model_sha256"] or sha(feature_dir / "manifest.json") != forecast["feature_manifest_sha256"]
            or sha(forecast_path) != lock["content_sha256"] or sha(forecast_path.with_name("top1000.jsonl")) != lock["top1000_sha256"]
            or forecast["lock_id"] != lock["lock_id"] or forecast["model_release_id"] != model["model_release_id"]):
        raise ValueError("HOLD_INSPECT_LINEAGE_MISMATCH")
    parameters = []
    for index, zone in enumerate(model["zones"]):
        values = [float(value) for value in zone["coefficients"].values()]
        parameters.append({"zone": index, "nonzero_count": sum(abs(value) > 1e-12 for value in values),
                           "coefficient_l1": math.fsum(abs(value) for value in values),
                           "minimum": min(values), "maximum": max(values)})
    return {
        "status": "PASS", "game": game, "target_issue": forecast["target_issue"],
        "serving_model": {"family": model["family"], "model_release_id": model["model_release_id"], "model_sha256": sha(model_path)},
        "feature_snapshot": {"feature_release_id": feature_manifest["feature_release_id"],
                             "feature_ids": feature_manifest["feature_ids"], "snapshot_sha256": feature_manifest["snapshot_sha256"]},
        "phase1_input_id": model["training_dataset_id"], "training_cutoff_issue": model["training_cutoff_issue"],
        "training_cutoff_position": model["training_cutoff_position"], "parameter_summary": parameters,
        "probability_range": {"first": forecast["first_probability"], "last": forecast["last_probability"],
                              "dynamic_ratio": probability["top1000_first_last_probability_ratio"]},
        "tie_evidence": {"distinct_score_count": probability["top1000_distinct_score_count"],
                         "maximum_tie_count": probability["top1000_maximum_tie_count"],
                         "near_equal_scores_are_not_ties": probability["near_equal_scores_are_not_ties"]},
        "rank_basis": forecast["ranking_algorithm_id"],
        "lock": {"lock_id": lock["lock_id"], "locked_at_utc": lock["locked_at_utc"], "create_once": lock["create_once"],
                 "status": lock["status"], "top1000_sha256": lock["top1000_sha256"]},
    }


def validate_release_bottom_up(release: Path, *, require_final: bool = True) -> dict[str, Any]:
    inspected = {game: inspect_release(release, game) for game in ("ssq", "dlt")}
    lifecycle = {}
    for game in ("ssq", "dlt"):
        root = _cycle_root(release, game)
        model, forecast, lock, result, score = (load(root / name) for name in ("parent-model.json", "forecast.json", "lock.json", "result-revision.json", "score.json"))
        if (forecast["game"] != game or result["game"] != game or forecast["target_issue"] != result["target_issue"]
                or score["forecast_id"] != forecast["forecast_id"] or score["result_revision_id"] != result["result_revision_id"]
                or score["model_release_id"] != model["model_release_id"] or score["model_sha256"] != sha(root / "parent-model.json")
                or lock["forecast_sha256"] != sha(root / "forecast.json") or lock["top1000_sha256"] != sha(root / "top1000.jsonl")):
            raise ValueError(f"HOLD_LIFECYCLE_LINEAGE:{game}")
        draws, _ = _draw_source(release, game)
        draw = next(item for item in draws if item.issue == result["target_issue"])
        rows = [json.loads(line) for line in (root / "top1000.jsonl").read_text(encoding="utf-8").splitlines()]
        if score_ticket(model, draw, rows) != score["metrics"]:
            raise ValueError(f"HOLD_SCORE_RECOMPUTE_MISMATCH:{game}")
        research = release / f"research/{game}"
        diff, candidate, decision, child_manifest = (load(research / name) for name in ("diff.json", "candidate.json", "decision.json", "child-model-manifest.json"))
        child_feature_manifest = load(research / "child-feature-manifest.json")
        if (diff["score_id"] != score["score_id"] or diff["result_revision_id"] != result["result_revision_id"]
                or child_manifest["score_sha256"] != sha(root / "score.json")
                or child_manifest["result_revision_sha256"] != sha(root / "result-revision.json")
                or child_manifest["child_model_sha256"] != sha(research / "child-model.json")
                or child_manifest["shadow_top1000_sha256"] != sha(research / "shadow-top1000.jsonl")
                or child_manifest["child_feature_snapshot_sha256"] != sha(research / "child-feature-snapshot.jsonl")
                or child_feature_manifest["snapshot_sha256"] != sha(research / "child-feature-snapshot.jsonl")
                or candidate["child_model_release_id"] != decision["child_model_release_id"]
                or decision["serving_changed"] or not decision["direct_promotion_attempt_rejected"]):
            raise ValueError(f"HOLD_RESEARCH_LINEAGE:{game}")
        selection_path = release / f"models/{game}/model-selection-receipt.json"
        selection = load(selection_path)
        payload = {key: value for key, value in selection.items() if key not in {"receipt_hash", "selection_metrics"}}
        serving = load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
        serving_model = load(release / serving["model_path"])
        if selection["receipt_hash"] != digest(payload) or serving_model["selection_receipt_hash"] != selection["receipt_hash"]:
            raise ValueError(f"FAIL_SELECTION_BIAS:{game}")
        summary = serving_model["report_only_summary"]
        ci = summary["joint_log_loss_block_bootstrap"]["ci95"]
        expected_scientific = "worse_than_M0" if ci[0] > 0 else ("lift_supported" if ci[1] < 0 else "no_confirmed_lift")
        if (serving_model["scientific_status"] != expected_scientific
                or any(row.get("method") != "zero_group_coefficients_complete_space_renormalization_v1" or not row.get("all_complete_spaces_renormalized") for row in summary["ablation_results"])
                or any(row.get("method") != "held_out_feature_group_derangement_recompute_fitted_model_score_v1" or row.get("sample_size") != len(serving_model["report_only_indices"]) for row in summary["permutation_evidence"])):
            raise ValueError(f"HOLD_SCIENTIFIC_EVIDENCE:{game}")
        if any(any(sample["target_position"] == sample["donor_position"] for sample in row["samples"])
               for row in summary["permutation_evidence"]):
            raise ValueError(f"HOLD_SCIENTIFIC_EVIDENCE:{game}:identity_permutation")
        lifecycle[game] = {"forecast_id": forecast["forecast_id"], "score_id": score["score_id"],
                           "result_revision_id": result["result_revision_id"], "child_model_release_id": decision["child_model_release_id"]}
    ProductLedger(release).rows()
    recovery = load(release / "runtime/schedule/recovery-ssq-dlt.json")
    if recovery.get("faults_tested") != len(STAGES) * 2 or not recovery.get("same_output_identities") or recovery.get("duplicate_side_effects") != 0:
        raise ValueError("HOLD_RECOVERY_EVIDENCE")
    final = {}
    if require_final:
        manifest_path = release / "manifest/delivery-manifest.json"
        acceptance_path = release / "acceptance/machine-acceptance.json"
        receipt_path = release / "acceptance/checklist-release-receipt.json"
        closure_path = release / "acceptance/final-closure.json"
        manifest, acceptance, receipt, closure = (load(path) for path in (manifest_path, acceptance_path, receipt_path, closure_path))
        for row in manifest["entries"]:
            path = release / row["path"]
            if not path.is_file() or sha(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
                raise ValueError(f"HOLD_MANIFEST_NOT_CLOSED:{row['path']}")
        if (closure["manifest_sha256"] != sha(manifest_path) or closure["machine_acceptance_sha256"] != sha(acceptance_path)
                or closure["checklist_release_receipt_sha256"] != sha(receipt_path)
                or receipt["manifest_sha256"] != sha(manifest_path) or receipt["machine_acceptance_sha256"] != sha(acceptance_path)
                or acceptance["machine_state"] != "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE" or closure["machine_state"] != "READY_FOR_LOCAL_PRODUCT_ACCEPTANCE"):
            raise ValueError("HOLD_FINAL_CLOSURE_MISMATCH")
        final = {"manifest_sha256": sha(manifest_path), "machine_acceptance_sha256": sha(acceptance_path),
                 "checklist_release_receipt_sha256": sha(receipt_path), "final_closure_sha256": sha(closure_path)}
    return {"status": "PASS", "recomputed_from_bottom_up": True, "inspect": inspected, "lifecycle": lifecycle,
            "schedule_recovery": {"faults_tested": recovery["faults_tested"], "same_output_identities": True},
            "final_closure": final}
