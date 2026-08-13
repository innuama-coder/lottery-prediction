from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any, Mapping

from ..cli_kernel import ProviderRegistry, parse_clock, producer_provenance, project_root
from ..correction import (
    CorrectionViolation,
    apply_current_replacements,
    build_score_correction_impact,
    correction_impact_id,
    validate_correction_policy,
)
from ..data_chain import append_data_release, create_genesis, load_data_release, proposed_data_release_id
from ..identity import content_id, validate_stable_id
from ..label_capability import LabelStore, _load_revision, _require_latest_revision, _validate_result_ledger
from ..ledger import AppendOnlyLedger
from ..lock import load_locked_forecast
from ..metrics import MetricViolation, derive_score_id, score_zones
from ..probability import distribution, zone_distribution
from ..ranking import rank_histogram, zone_histogram
from ..serialization import canonical_sha256, load_json, sha256_file
from ..storage import AdvisoryFileLock, atomic_replace_json, resolve_inside, validate_runtime_root, write_once_json
from ..windows import (
    _exact_recomputation,
    aggregate_id,
    build_oracle_fixture_window_metric,
    build_window_metric,
    resolve_trusted_window_inputs,
)


T10_I07_MANIFEST_SHA256 = "3f85d9fc36eb67452da3dc4552e07a2c7635ece1ab865a922587640856e40f0c"
T10_I07_METRIC_SHA256 = "b424553bef790ad0d99269a6741f95309dc4da2b698662d233603a51b877efd9"


def _joint_histogram(front: Mapping[int, int], back: Mapping[int, int]) -> dict[int, int]:
    result: dict[int, int] = {}
    for left, left_count in front.items():
        for right, right_count in back.items():
            result[left + right] = result.get(left + right, 0) + left_count * right_count
    return dict(sorted(result.items()))


def _score_from_input(supplied: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "forecast_id", "result_revision_id", "metric_contract_id", "comparator_forecast_id",
        "model_front", "model_back", "champion_front", "champion_back", "result",
        "ordered_tickets", "context",
    }
    if set(supplied) != required:
        raise MetricViolation("score input shape mismatch")
    zones = {}
    for name in ("model_front", "model_back", "champion_front", "champion_back"):
        spec = supplied[name]
        if not isinstance(spec, Mapping) or set(spec) != {"ticks", "k"}:
            raise MetricViolation("score zone input shape mismatch")
        zones[name] = zone_distribution(spec["ticks"], spec["k"])
    histogram = _joint_histogram(
        zone_histogram(zones["model_front"].ticks, zones["model_front"].k),
        zone_histogram(zones["model_back"].ticks, zones["model_back"].k),
    )
    result = supplied["result"]
    if not isinstance(result, Mapping) or set(result) != {"front", "back"}:
        raise MetricViolation("score result input shape mismatch")
    return score_zones(
        model_front=zones["model_front"], model_back=zones["model_back"],
        champion_front=zones["champion_front"], champion_back=zones["champion_back"],
        result_front=result["front"], result_back=result["back"], histogram=histogram,
        ordered_tickets=supplied["ordered_tickets"], forecast_id=supplied["forecast_id"],
        result_revision_id=supplied["result_revision_id"], metric_contract_id=supplied["metric_contract_id"],
        comparator_forecast_id=supplied["comparator_forecast_id"], context=supplied["context"],
    )


def score_one(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    event_at = parse_clock(args.clock)
    locked = load_locked_forecast(runtime, args.forecast_id)
    forecast, snapshot, receipt = locked["forecast"], locked["snapshot"], locked["lock_receipt"]
    if forecast["metric_contract_id"] != args.metric_contract_id:
        raise MetricViolation("score command metric identity differs from the locked forecast")
    candidates = []
    forecasts_root = resolve_inside(runtime, "forecasts")
    for path in forecasts_root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        row = load_json(path / "forecast.json", reject_floats=True)
        if (row.get("game"), row.get("target_issue"), row.get("model_id"), row.get("metric_contract_id")) == (
            forecast["game"], forecast["target_issue"], "M0", args.metric_contract_id,
        ):
            candidates.append(load_locked_forecast(runtime, row["forecast_id"]))
    if len(candidates) != 1:
        raise MetricViolation("score requires exactly one locked M0 Champion forecast for the issue")
    champion = candidates[0]
    expected_identity = {key: receipt[key] for key in (
        "game", "target_issue", "model_id", "model_release_id", "data_release_id",
        "calendar_release_id", "schedule_release_id", "metric_contract_id",
    )}
    labels = LabelStore(runtime)
    capability = labels.acquire_for_scoring(
        forecast_id=args.forecast_id, result_revision_id=args.result_revision_id,
        metric_contract_id=args.metric_contract_id, clock=event_at, expected_identity=expected_identity,
    )
    numbers = labels.read_once(capability)
    model = distribution(snapshot["game"], snapshot["front_ticks"], snapshot["back_ticks"],
                         model_contract_id=snapshot["model_id"], rule_id=snapshot["rule_id"])
    champion_model = distribution(champion["snapshot"]["game"], champion["snapshot"]["front_ticks"], champion["snapshot"]["back_ticks"],
                                  model_contract_id=champion["snapshot"]["model_id"], rule_id=champion["snapshot"]["rule_id"])
    tickets = [{"front": row["numbers"]["front"], "back": row["numbers"]["back"], "display_position": row["display_position"]}
               for row in forecast["tickets"]]
    package = score_zones(
        model_front=model.front, model_back=model.back, champion_front=champion_model.front, champion_back=champion_model.back,
        result_front=numbers["front"], result_back=numbers["back"], histogram=rank_histogram(model), ordered_tickets=tickets,
        forecast_id=args.forecast_id, result_revision_id=args.result_revision_id, metric_contract_id=args.metric_contract_id,
        comparator_forecast_id=champion["forecast"]["forecast_id"],
        context={"game": forecast["game"], "issue_id": forecast["target_issue"], "model_id": forecast["model_id"],
                 "model_release_id": forecast["model_release_id"], "config_id": forecast["config_id"], "comparator_champion_id": "M0"},
    )
    score = package["score"]
    provenance = producer_provenance(root, runtime.relative_to(root).as_posix())
    with AdvisoryFileLock(resolve_inside(runtime, "locks/score.lock")):
        ledger = AppendOnlyLedger(runtime, "scores")
        validation = ledger.validate()
        destination = resolve_inside(runtime, f"scores/{score['score_id']}")
        score_path, detail_path, receipt_path = destination / "score.json", destination / "window-detail.json", destination / "score-receipt.json"
        if destination.exists():
            if not all(path.is_file() for path in (score_path, detail_path, receipt_path)) or load_json(score_path, reject_floats=True) != score or load_json(detail_path, reject_floats=True) != package["detail"]:
                raise MetricViolation("immutable score identity reuse mismatch")
            score_receipt = load_json(receipt_path, reject_floats=True)
        else:
            write_once_json(score_path, score)
            write_once_json(detail_path, package["detail"])
            score_receipt = {
                "schema_version": "1.0.0", "artifact_type": "phase4_score_receipt", "score_id": score["score_id"],
                "score_sha256": sha256_file(score_path), "window_detail_sha256": sha256_file(detail_path),
                "forecast_ledger_head_sha256": locked["ledger_head_sha256"], "previous_score_ledger_head_sha256": validation["head_sha256"],
                "forecast_sha256": receipt["forecast_sha256"],
                "comparator_forecast_id": champion["forecast"]["forecast_id"],
                "comparator_forecast_sha256": champion["lock_receipt"]["forecast_sha256"],
                "result_revision_sha256": sha256_file(resolve_inside(runtime, f"result-revisions/{args.result_revision_id}.json")),
                "scored_at": event_at, "producer_provenance": provenance,
            }
            write_once_json(receipt_path, score_receipt)
        view = load_json(ledger.current_view_path, reject_floats=True) if validation["event_count"] else {"objects": {}}
        existing = view.get("objects", {}).get(score["score_id"])
        if existing is None:
            if score_receipt["previous_score_ledger_head_sha256"] != validation["head_sha256"]:
                raise MetricViolation("score ledger head changed before recovery")
            ledger.append_event(object_id=score["score_id"], event_type="score_recorded", event_at_utc=score_receipt["scored_at"],
                                payload={"score_id": score["score_id"], "score_sha256": score_receipt["score_sha256"],
                                         "window_detail_sha256": score_receipt["window_detail_sha256"],
                                         "score_receipt_sha256": sha256_file(receipt_path)},
                                producer_provenance=score_receipt["producer_provenance"], expected_head_sha256=validation["head_sha256"])
        current_path = resolve_inside(runtime, f"scores/current/{args.forecast_id}.json")
        current = {"schema_version": "1.0.0", "artifact_type": "phase4_score_current_view", "forecast_id": args.forecast_id,
                   "score_id": score["score_id"], "result_revision_id": args.result_revision_id}
        if current_path.exists() and load_json(current_path, reject_floats=True) != current:
            raise MetricViolation("score current view requires the correction workflow")
        if not current_path.exists():
            atomic_replace_json(current_path, current)
    return {"status": "PASS", "terminal": "PASS", "score_id": score["score_id"], "exit_code": 0}


def _load_canonical_score_package(runtime: Path, score_id: str) -> dict[str, Any]:
    destination = resolve_inside(runtime, f"scores/{score_id}")
    score_path, detail_path, receipt_path = destination / "score.json", destination / "window-detail.json", destination / "score-receipt.json"
    if not all(path.is_file() for path in (score_path, detail_path, receipt_path)):
        raise MetricViolation("canonical score package is incomplete")
    score = load_json(score_path, reject_floats=True)
    detail = load_json(detail_path, reject_floats=True)
    receipt = load_json(receipt_path, reject_floats=True)
    if score.get("score_id") != score_id or detail.get("score_id") != score_id or receipt.get("score_id") != score_id:
        raise MetricViolation("canonical score identity chain mismatch")
    if receipt.get("score_sha256") != sha256_file(score_path) or receipt.get("window_detail_sha256") != sha256_file(detail_path):
        raise MetricViolation("canonical score file hash mismatch")
    ledger = AppendOnlyLedger(runtime, "scores")
    validation = ledger.validate()
    if not validation["event_count"]:
        raise MetricViolation("score ledger is empty")
    view = load_json(ledger.current_view_path, reject_floats=True)
    item = view.get("objects", {}).get(score_id)
    if item is None or item.get("event_type") != "score_recorded":
        raise MetricViolation("canonical score ledger event is missing")
    payload = load_json(ledger.payloads_root / f"{item['payload_sha256']}.json", reject_floats=True)
    expected_payload = {"score_id": score_id, "score_sha256": sha256_file(score_path),
                        "window_detail_sha256": sha256_file(detail_path), "score_receipt_sha256": sha256_file(receipt_path)}
    if payload != expected_payload:
        raise MetricViolation("canonical score ledger payload hash chain mismatch")
    locked = load_locked_forecast(runtime, score["forecast_id"])
    comparator = load_locked_forecast(runtime, score["comparator_forecast_id"])
    revision = _load_revision(runtime, score["result_revision_id"])
    if (locked["forecast"]["forecast_id"] != score["forecast_id"]
            or comparator["forecast"].get("model_id") != "M0"
            or comparator["forecast"].get("game") != locked["forecast"].get("game")
            or comparator["forecast"].get("target_issue") != locked["forecast"].get("target_issue")
            or revision["game"] != detail["game"] or revision["issue_id"] != detail["issue_id"]
            or revision["numbers"] != {"front": detail["observed_front"], "back": detail["observed_back"]}):
        raise MetricViolation("canonical forecast/result/comparator identity chain mismatch")
    if (receipt.get("forecast_sha256") != locked["lock_receipt"]["forecast_sha256"]
            or receipt.get("comparator_forecast_id") != comparator["forecast"]["forecast_id"]
            or receipt.get("comparator_forecast_sha256") != comparator["lock_receipt"]["forecast_sha256"]
            or receipt.get("result_revision_sha256") != sha256_file(resolve_inside(runtime, f"result-revisions/{score['result_revision_id']}.json"))):
        raise MetricViolation("canonical score receipt upstream hash chain mismatch")
    current_path = resolve_inside(runtime, f"scores/current/{score['forecast_id']}.json")
    if not current_path.is_file() or load_json(current_path, reject_floats=True).get("score_id") != score_id:
        raise MetricViolation("window attempted to use a non-current score")
    return {"score": score, "detail": detail}


def _ledger_binding(runtime: Path, ledger_id: str, object_id: str, event_type: str) -> tuple[dict[str, Any], str]:
    ledger = AppendOnlyLedger(runtime, ledger_id)
    validation = ledger.validate()
    if not validation["event_count"] or validation["head_sha256"] is None:
        raise CorrectionViolation(f"{ledger_id} ledger is empty")
    view = load_json(ledger.current_view_path, reject_floats=True)
    item = view.get("objects", {}).get(object_id)
    if item is None or item.get("event_type") != event_type:
        raise CorrectionViolation(f"{object_id} is absent from the canonical {ledger_id} ledger")
    payload_path = ledger.payloads_root / f"{item['payload_sha256']}.json"
    if not payload_path.is_file() or sha256_file(payload_path) != item["payload_sha256"]:
        raise CorrectionViolation(f"{ledger_id} ledger payload is missing or corrupt")
    return load_json(payload_path, reject_floats=True), validation["head_sha256"]


def _load_correction_score_package(runtime: Path, score_id: str) -> dict[str, Any]:
    validate_stable_id(score_id, "score identity")
    destination = resolve_inside(runtime, f"scores/{score_id}")
    score_path = destination / "score.json"
    detail_path = destination / "window-detail.json"
    receipt_path = destination / "score-receipt.json"
    if not all(path.is_file() for path in (score_path, detail_path, receipt_path)):
        raise CorrectionViolation("correction score package is incomplete")
    score = load_json(score_path, reject_floats=True)
    detail = load_json(detail_path, reject_floats=True)
    receipt = load_json(receipt_path, reject_floats=True)
    if score.get("score_id") != score_id or detail.get("score_id") != score_id or receipt.get("score_id") != score_id:
        raise CorrectionViolation("correction score identity chain mismatch")
    if score_id != derive_score_id(score.get("forecast_id"), score.get("result_revision_id"), score.get("metric_contract_id")):
        raise CorrectionViolation("correction score content identity mismatch")
    if receipt.get("score_sha256") != sha256_file(score_path) or receipt.get("window_detail_sha256") != sha256_file(detail_path):
        raise CorrectionViolation("correction score package hash mismatch")
    _exact_recomputation(score, detail)
    payload, head = _ledger_binding(runtime, "scores", score_id, "score_recorded")
    expected = {
        "score_id": score_id,
        "score_sha256": sha256_file(score_path),
        "window_detail_sha256": sha256_file(detail_path),
        "score_receipt_sha256": sha256_file(receipt_path),
    }
    if payload != expected:
        raise CorrectionViolation("correction score ledger payload mismatch")
    return {"score": score, "detail": detail, "ledger_head_sha256": head,
            "paths": {"score": score_path, "detail": detail_path, "receipt": receipt_path}}


def _load_correction_window(runtime: Path, aggregate_identity: str) -> dict[str, Any]:
    validate_stable_id(aggregate_identity, "aggregate identity")
    path = resolve_inside(runtime, f"window-metrics/{aggregate_identity}/window-metric.json")
    if not path.is_file():
        raise CorrectionViolation("correction window metric is missing")
    window = load_json(path, reject_floats=True)
    if aggregate_id(window) != aggregate_identity:
        raise CorrectionViolation("correction window identity mismatch")
    payload, head = _ledger_binding(runtime, "window-metrics", aggregate_identity, "window_metric_recorded")
    if payload != {"aggregate_id": aggregate_identity, "window_metric_sha256": sha256_file(path)}:
        raise CorrectionViolation("correction window ledger payload mismatch")
    return {"window": window, "path": path, "ledger_head_sha256": head}


def _current_pointer_rows(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not root.is_dir():
        return []
    return [(path, load_json(path, reject_floats=True)) for path in sorted(root.iterdir())
            if path.is_file() and path.suffix == ".json"]


def derive_runtime_correction_graph(runtime: Path, new_result_revision_id: str) -> dict[str, Any]:
    """Derive the complete score-side correction graph exclusively from runtime facts."""
    new = _load_revision(runtime, validate_stable_id(new_result_revision_id, "new result revision identity"))
    _require_latest_revision(runtime, new)
    result_head = _validate_result_ledger(runtime, new_result_revision_id)
    old_id = new.get("supersedes_revision_id")
    if type(old_id) is not str:
        raise CorrectionViolation("new result revision is not a correction")
    old = _load_revision(runtime, old_id)
    if (old["game"], old["issue_id"]) != (new["game"], new["issue_id"]):
        raise CorrectionViolation("correction revision changes game or issue")
    _validate_result_ledger(runtime, old_id)

    data_view_path = resolve_inside(runtime, "data-releases/current-view.json")
    if not data_view_path.is_file():
        raise CorrectionViolation("current data release projection is missing")
    data_view = load_json(data_view_path, reject_floats=True)
    data_release = load_data_release(runtime, data_view.get("data_release_id"))
    data_validation = AppendOnlyLedger(runtime, "data-chain").validate()
    if (data_validation["head_sha256"] != data_view.get("ledger_head_sha256")
            or new_result_revision_id not in data_release.get("result_revision_ids", [])
            or old_id in data_release.get("result_revision_ids", [])):
        raise CorrectionViolation("current data release does not canonically replace the corrected revision")

    current_scores: dict[str, str] = {}
    score_replacements: dict[str, str] = {}
    binding_files: dict[str, str] = {
        _load_revision.__name__ + ":old": sha256_file(resolve_inside(runtime, f"result-revisions/{old_id}.json")),
        _load_revision.__name__ + ":new": sha256_file(resolve_inside(runtime, f"result-revisions/{new_result_revision_id}.json")),
        "data-current": sha256_file(data_view_path),
        "data-release": sha256_file(resolve_inside(runtime, f"data-releases/{data_release['data_release_id']}/data-release.json")),
    }
    score_ledger = AppendOnlyLedger(runtime, "scores")
    score_validation = score_ledger.validate()
    if not score_validation["event_count"]:
        raise CorrectionViolation("runtime graph score ledger is empty")
    score_ledger_view = load_json(score_ledger.current_view_path, reject_floats=True)
    affected_ledger_scores: set[str] = set()
    for score_id, item in score_ledger_view["objects"].items():
        if item.get("event_type") != "score_recorded":
            continue
        package = _load_correction_score_package(runtime, score_id)
        if (package["score"]["result_revision_id"] == old_id
                and (package["detail"]["game"], package["detail"]["issue_id"]) == (new["game"], new["issue_id"])):
            affected_ledger_scores.add(score_id)
    for current_path, current in _current_pointer_rows(resolve_inside(runtime, "scores/current")):
        if set(current) != {"schema_version", "artifact_type", "forecast_id", "score_id", "result_revision_id"}:
            raise CorrectionViolation("score current projection shape mismatch")
        old_package = _load_correction_score_package(runtime, current["score_id"])
        old_score, old_detail = old_package["score"], old_package["detail"]
        if current["forecast_id"] != old_score["forecast_id"] or current["result_revision_id"] != old_score["result_revision_id"]:
            raise CorrectionViolation("score current projection identity mismatch")
        if old_score["result_revision_id"] != old_id:
            continue
        if (old_detail["game"], old_detail["issue_id"]) != (new["game"], new["issue_id"]):
            raise CorrectionViolation("current score correction target differs from result revision")
        replacement_id = derive_score_id(old_score["forecast_id"], new_result_revision_id, old_score["metric_contract_id"])
        replacement = _load_correction_score_package(runtime, replacement_id)
        new_score, new_detail = replacement["score"], replacement["detail"]
        stable_score_fields = {"forecast_id", "metric_contract_id", "comparator_forecast_id"}
        stable_detail_fields = {"game", "issue_id", "model_id", "model_release_id", "config_id", "comparator_champion_id"}
        if (any(new_score[field] != old_score[field] for field in stable_score_fields)
                or any(new_detail[field] != old_detail[field] for field in stable_detail_fields)
                or new_score["result_revision_id"] != new_result_revision_id):
            raise CorrectionViolation("corrected score does not preserve its canonical forecast chain")
        current_scores[old_score["forecast_id"]] = old_score["score_id"]
        score_replacements[old_score["score_id"]] = new_score["score_id"]
        binding_files[f"score-current:{old_score['forecast_id']}"] = sha256_file(current_path)
        for label, package in (("old-score", old_package), ("new-score", replacement)):
            for role, path in package["paths"].items():
                binding_files[f"{label}:{old_score['forecast_id']}:{role}"] = sha256_file(path)
    if not current_scores:
        raise CorrectionViolation("runtime graph has no current scores affected by the correction")
    if set(score_replacements) != affected_ledger_scores:
        raise CorrectionViolation("runtime score current projections omit an affected ledger score")

    current_aggregates: dict[str, str] = {}
    aggregate_replacements: dict[str, str] = {}
    old_score_ids = set(score_replacements)
    expected_new_scores = score_replacements
    window_ledger = AppendOnlyLedger(runtime, "window-metrics")
    window_validation = window_ledger.validate()
    if not window_validation["event_count"]:
        raise CorrectionViolation("runtime graph window ledger is empty")
    window_ledger_view = load_json(window_ledger.current_view_path, reject_floats=True)
    affected_window_ids: set[str] = set()
    for aggregate_identity, item in window_ledger_view["objects"].items():
        if item.get("event_type") != "window_metric_recorded":
            continue
        package = _load_correction_window(runtime, aggregate_identity)
        if old_score_ids.intersection(package["window"]["score_ids"]):
            affected_window_ids.add(package["window"]["window_id"])
    for current_path, current in _current_pointer_rows(resolve_inside(runtime, "window-metrics/current")):
        if set(current) != {"schema_version", "artifact_type", "window_id", "aggregate_id"}:
            raise CorrectionViolation("window current projection shape mismatch")
        old_package = _load_correction_window(runtime, current["aggregate_id"])
        old_window = old_package["window"]
        if current["window_id"] != old_window["window_id"]:
            raise CorrectionViolation("window current projection identity mismatch")
        if not old_score_ids.intersection(old_window["score_ids"]):
            continue
        wanted_scores = [expected_new_scores.get(score_id, score_id) for score_id in old_window["score_ids"]]
        candidates = []
        windows_root = resolve_inside(runtime, "window-metrics")
        for directory in sorted(windows_root.iterdir()):
            if not directory.is_dir() or directory.name == "current":
                continue
            candidate_path = directory / "window-metric.json"
            if not candidate_path.is_file():
                continue
            candidate = load_json(candidate_path, reject_floats=True)
            stable = ("game", "model_id", "comparator_champion_id", "model_release_id", "window_id", "metric_contract_id")
            if candidate.get("score_ids") == wanted_scores and all(candidate.get(field) == old_window.get(field) for field in stable):
                candidates.append((aggregate_id(candidate), candidate))
        if len(candidates) != 1:
            raise CorrectionViolation("runtime graph does not contain exactly one corrected aggregate")
        replacement_id, _ = candidates[0]
        replacement = _load_correction_window(runtime, replacement_id)
        current_aggregates[old_window["window_id"]] = current["aggregate_id"]
        aggregate_replacements[current["aggregate_id"]] = replacement_id
        binding_files[f"window-current:{old_window['window_id']}"] = sha256_file(current_path)
        binding_files[f"old-window:{old_window['window_id']}"] = sha256_file(old_package["path"])
        binding_files[f"new-window:{old_window['window_id']}"] = sha256_file(replacement["path"])
    if set(current_aggregates) != affected_window_ids:
        raise CorrectionViolation("runtime window current projections omit an affected ledger window")

    def ledger_objects(ledger_id: str) -> tuple[list[str], str | None]:
        ledger = AppendOnlyLedger(runtime, ledger_id)
        validation = ledger.validate()
        if not validation["event_count"]:
            return [], None
        view = load_json(ledger.current_view_path, reject_floats=True)
        ordered = sorted(view["objects"].items(), key=lambda item: item[1]["ordinal"])
        return [object_id for object_id, _ in ordered], validation["head_sha256"]

    pending_research, research_head = ledger_objects("research-objects")
    alpha_events, alpha_head = ledger_objects("alpha-events")
    score_head = score_validation["head_sha256"]
    window_head = window_validation["head_sha256"]
    graph = {
        "game": new["game"], "issue_id": new["issue_id"],
        "old_result_revision_id": old_id, "new_result_revision_id": new_result_revision_id,
        "new_supersedes_revision_id": new["supersedes_revision_id"],
        "new_data_release_id": data_release["data_release_id"],
        "new_data_release_result_revision_ids": data_release["result_revision_ids"],
        "current_scores": current_scores, "current_aggregates": current_aggregates,
        "score_replacements": score_replacements, "aggregate_replacements": aggregate_replacements,
        "pending_research_object_ids": pending_research, "alpha_event_ids_before": alpha_events,
    }
    build_score_correction_impact(canonical_graph=graph)
    return {"graph": graph, "bindings": {
        "result_ledger_head_sha256": result_head,
        "data_chain_head_sha256": data_validation["head_sha256"],
        "score_ledger_head_sha256": score_head,
        "window_ledger_head_sha256": window_head,
        "research_ledger_head_sha256": research_head,
        "alpha_ledger_head_sha256": alpha_head,
        "files": dict(sorted(binding_files.items())),
        "graph_sha256": canonical_sha256(graph),
    }}


def score_window(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    parse_clock(args.clock)
    trusted_packages, trust, supplied = resolve_trusted_window_inputs(
        runtime_root=runtime, window_id=args.window_id,
    )
    if supplied.get("window_id") != args.window_id or supplied.get("metric_contract_id") != args.metric_contract_id:
        raise MetricViolation("window command/input identity mismatch")
    packages = [_load_canonical_score_package(runtime, score_id) for score_id in supplied["score_ids"]]
    if trusted_packages != packages:
        raise MetricViolation("trusted window packages differ from canonical upstream validation")
    window = build_window_metric(
        packages=packages, **{field: supplied[field] for field in (
            "game", "model_id", "comparator_champion_id", "model_release_id", "window_id", "metric_contract_id")},
        trusted_anchor=trust,
    )
    identity = aggregate_id(window)
    window_path = resolve_inside(runtime, f"window-metrics/{identity}/window-metric.json")
    if window_path.exists():
        if load_json(window_path, reject_floats=True) != window:
            raise MetricViolation("immutable window identity reuse mismatch")
    else:
        write_once_json(window_path, window)
    ledger = AppendOnlyLedger(runtime, "window-metrics")
    validation = ledger.validate()
    view = load_json(ledger.current_view_path, reject_floats=True) if validation["event_count"] else {"objects": {}}
    if identity not in view.get("objects", {}):
        ledger.append_event(
            object_id=identity, event_type="window_metric_recorded", event_at_utc=parse_clock(args.clock),
            payload={"aggregate_id": identity, "window_metric_sha256": sha256_file(window_path)},
            producer_provenance=producer_provenance(root, window_path.relative_to(root).as_posix()),
            expected_head_sha256=validation["head_sha256"],
        )
    atomic_replace_json(resolve_inside(runtime, f"window-metrics/current/{args.window_id}.json"), {
        "schema_version": "1.0.0", "artifact_type": "phase4_window_metric_current_view",
        "window_id": args.window_id, "aggregate_id": identity,
    })
    return {"status": "PASS", "terminal": "PASS", "aggregate_id": identity, "aggregate_state": window["aggregate_state"], "exit_code": 0}


def _validate_metric_oracle(oracle_root: Path) -> dict[str, Any]:
    manifest_path = resolve_inside(oracle_root, "known-answer-manifest.json")
    metric_path = resolve_inside(oracle_root, "small-space-metrics.json")
    if sha256_file(manifest_path) != T10_I07_MANIFEST_SHA256 or sha256_file(metric_path) != T10_I07_METRIC_SHA256:
        raise MetricViolation("T10-I07 metric oracle identity mismatch")
    manifest = load_json(manifest_path, reject_floats=True)
    matches = [row for row in manifest.get("files", []) if row.get("sha256") == T10_I07_METRIC_SHA256]
    if len(matches) != 1 or not matches[0].get("path", "").endswith("/T10/attempts/T10-I01/known-answers/small-space-metrics.json"):
        raise MetricViolation("T10-I07 metric oracle is absent from its frozen manifest")
    expected = load_json(metric_path, reject_floats=True)
    fixture = expected["fixture"]
    front_spec, back_spec = fixture["front"], fixture["back"]
    model_front = zone_distribution(front_spec["ticks"], front_spec["k"])
    model_back = zone_distribution(back_spec["ticks"], back_spec["k"])
    champion_front = zone_distribution([0] * front_spec["N"], front_spec["k"])
    champion_back = zone_distribution([0] * back_spec["N"], back_spec["k"])
    ordered = []
    for front in combinations(range(1, front_spec["N"] + 1), front_spec["k"]):
        for back in combinations(range(1, back_spec["N"] + 1), back_spec["k"]):
            score = model_front.score(front) + model_back.score(back)
            ordered.append((score, front, back))
    ordered.sort(key=lambda row: (-row[0], row[1] + row[2]))
    tickets = [{"front": list(front), "back": list(back), "display_position": index}
               for index, (_, front, back) in enumerate(ordered, start=1)]
    histogram = _joint_histogram(zone_histogram(model_front.ticks, model_front.k), zone_histogram(model_back.ticks, model_back.k))
    packages = []
    for row in expected["per_forecast"]:
        package = score_zones(
            model_front=model_front, model_back=model_back,
            champion_front=champion_front, champion_back=champion_back,
            result_front=row["front"], result_back=row["back"], histogram=histogram, ordered_tickets=tickets,
            forecast_id=f"fixture-forecast-{row['ordinal']:02d}", result_revision_id=f"fixture-revision-{row['ordinal']:02d}",
            metric_contract_id="phase4-metric-v1", comparator_forecast_id=f"fixture-m0-{row['ordinal']:02d}",
            context={"game": "ssq", "issue_id": f"fixture-{row['ordinal']:02d}", "model_id": "P4E1",
                     "model_release_id": "fixture-release-v1", "config_id": "fixture-config-v1", "comparator_champion_id": "M0"},
        )
        score = package["score"]
        comparisons = {
            "joint_log_score": row["joint_log_score"], "skill_vs_champion": row["skill_vs_m0"],
            "inclusion_brier": row["inclusion_brier"], "tie_midrank": row["tie_midrank"],
            "midrank_percentile": row["midrank_percentile"],
        }
        if any(score[key] != value for key, value in comparisons.items()):
            raise MetricViolation("small-space per-forecast oracle mismatch")
        if score["tie_rank_lower"] != row["tie_rank_lower"] or score["tie_rank_upper"] != row["tie_rank_upper"]:
            raise MetricViolation("small-space rank oracle mismatch")
        if any(score["hit_at_k"][str(k)] != row[f"hit_at_{k}"] for k in (10, 100, 200, 1000)):
            raise MetricViolation("small-space hit oracle mismatch")
        packages.append(package)
    window = build_oracle_fixture_window_metric(
        packages=packages, game="ssq", model_id="P4E1", comparator_champion_id="M0",
        model_release_id="fixture-release-v1", window_id="fixture-window-30", metric_contract_id="phase4-metric-v1",
    )
    window_expected = expected["window_30"]
    values = window["values"]
    expected_means = window_expected["means"]
    if values["mean_joint_log_score"] != expected_means["joint_log_score"] or values["mean_skill"] != expected_means["skill_vs_m0"] or values["mean_inclusion_brier"] != expected_means["inclusion_brier"] or values["mean_rank_percentile"] != expected_means["midrank_percentile"]:
        raise MetricViolation("small-space window mean oracle mismatch")
    if values["ece"] != window_expected["ece"] or values["stability"] != window_expected["stability"]:
        raise MetricViolation("small-space calibration/stability oracle mismatch")
    for k in (10, 100, 200, 1000):
        cell = window_expected["cumulative_hit_rate"][str(k)]
        if values["cumulative_hit_rate"][str(k)] != cell["rate"] or [values["wilson_95"][str(k)]["lower"], values["wilson_95"][str(k)]["upper"]] != cell["wilson_95"]:
            raise MetricViolation("small-space Wilson oracle mismatch")
    for observed, wanted in zip(values["reliability"], window_expected["reliability_bins"]):
        if {"bin": observed["bin_index"], "count": observed["count"], "mean_probability": observed["mean_predicted_probability"], "observed_rate": observed["observed_rate"]} != wanted:
            raise MetricViolation("small-space reliability oracle mismatch")
    return {"oracle_artifact_type": expected["artifact_type"], "observations": len(packages), "aggregate_id": aggregate_id(window)}


def _load_correction_fixture(args: Any, runtime: Path) -> tuple[Path, Path]:
    fixture = getattr(args, "fixture", None)
    oracle = getattr(args, "oracle", None)
    if fixture is not None:
        return Path(fixture).resolve(), Path(oracle).resolve()
    revision = args.result_revision_id
    return resolve_inside(runtime, f"correction-inputs/{revision}.json"), Path("")


def _seed_fixture_runtime(*, project: Path, runtime: Path, fixture_path: Path,
                          supplied: Mapping[str, Any], event_at: str,
                          provenance: Mapping[str, Any]) -> str:
    """Materialize a synthetic fixture into an otherwise isolated runtime."""
    seed = supplied.get("seed_runtime")
    if not isinstance(seed, Mapping):
        raise CorrectionViolation("seed fixture omits its runtime seed")
    marker_path = resolve_inside(runtime, "control/t06-correction-fixture-seed.json")
    marker = {"schema_version": "1.0.0", "artifact_type": "phase4_t06_fixture_seed",
              "fixture_sha256": sha256_file(fixture_path)}
    if marker_path.exists():
        if load_json(marker_path, reject_floats=True) != marker:
            raise CorrectionViolation("isolated fixture runtime was seeded from different bytes")
        return load_json(resolve_inside(runtime, "control/t06-correction-fixture-identities.json"), reject_floats=True)["new_result_revision_id"]
    if runtime.exists() and any(runtime.iterdir()):
        raise CorrectionViolation("fixture mode requires an isolated empty runtime")
    required = {"game", "issue_id", "draw_business_date", "old_numbers", "new_numbers", "scores", "window_id",
                "pending_research_object_ids", "alpha_event_ids_before"}
    if set(seed) != required or seed["game"] not in {"ssq", "dlt"} or not isinstance(seed["scores"], list) or not seed["scores"]:
        raise CorrectionViolation("fixture runtime seed shape mismatch")

    revisions = []
    previous = None
    for ordinal, numbers in enumerate((seed["old_numbers"], seed["new_numbers"]), start=1):
        body = {
            "schema_version": "1.0.0", "artifact_type": "phase4_result_revision",
            "game": seed["game"], "issue_id": seed["issue_id"], "draw_business_date": seed["draw_business_date"],
            "numbers": numbers, "primary_observation_id": f"fixture-primary-{ordinal}",
            "corroborating_observation_id": f"fixture-corroborating-{ordinal}",
            "verified_at_utc": f"2026-01-0{ordinal}T00:00:00Z", "supersedes_revision_id": previous,
        }
        body["result_revision_id"] = content_id("result-revision", body)
        write_once_json(resolve_inside(runtime, f"result-revisions/{body['result_revision_id']}.json"), body)
        ledger = AppendOnlyLedger(runtime, "result-revisions")
        validation = ledger.validate()
        ledger.append_event(
            object_id=body["result_revision_id"], event_type="result_revision_verified",
            event_at_utc=body["verified_at_utc"],
            payload={"result_revision_id": body["result_revision_id"],
                     "result_revision_sha256": sha256_file(resolve_inside(runtime, f"result-revisions/{body['result_revision_id']}.json"))},
            producer_provenance=provenance, expected_head_sha256=validation["head_sha256"],
        )
        revisions.append(body)
        previous = body["result_revision_id"]

    genesis = create_genesis(project, runtime, resolve_inside(project, "config/phase4/genesis.json"),
                             clock=event_at, producer_provenance=provenance)
    successor_id = proposed_data_release_id(
        project, runtime, previous_phase4_release_id=genesis["release"]["data_release_id"],
        result_revision_ids=[revisions[1]["result_revision_id"]],
    )
    append_data_release(
        project, runtime, data_release_id=successor_id,
        previous_phase4_release_id=genesis["release"]["data_release_id"],
        result_revision_ids=[revisions[1]["result_revision_id"]], clock=event_at,
        producer_provenance=provenance,
    )

    old_packages, new_packages = [], []
    for spec in seed["scores"]:
        required_score = {"forecast_id", "comparator_forecast_id", "model_front", "model_back",
                          "champion_front", "champion_back", "ordered_tickets", "model_release_id", "config_id"}
        if not isinstance(spec, Mapping) or set(spec) != required_score:
            raise CorrectionViolation("fixture score seed shape mismatch")
        built = []
        for revision in revisions:
            score_input = {
                "forecast_id": spec["forecast_id"], "result_revision_id": revision["result_revision_id"],
                "metric_contract_id": supplied["metric_contract_id"],
                "comparator_forecast_id": spec["comparator_forecast_id"],
                "model_front": spec["model_front"], "model_back": spec["model_back"],
                "champion_front": spec["champion_front"], "champion_back": spec["champion_back"],
                "result": revision["numbers"], "ordered_tickets": spec["ordered_tickets"],
                "context": {"game": seed["game"], "issue_id": seed["issue_id"], "model_id": "P4E1",
                            "model_release_id": spec["model_release_id"], "config_id": spec["config_id"],
                            "comparator_champion_id": "M0"},
            }
            package = _score_from_input(score_input)
            identity = package["score"]["score_id"]
            destination = resolve_inside(runtime, f"scores/{identity}")
            score_path, detail_path, receipt_path = destination / "score.json", destination / "window-detail.json", destination / "score-receipt.json"
            write_once_json(score_path, package["score"])
            write_once_json(detail_path, package["detail"])
            write_once_json(receipt_path, {"schema_version": "1.0.0", "artifact_type": "phase4_score_receipt",
                                           "score_id": identity, "score_sha256": sha256_file(score_path),
                                           "window_detail_sha256": sha256_file(detail_path)})
            ledger = AppendOnlyLedger(runtime, "scores")
            validation = ledger.validate()
            ledger.append_event(
                object_id=identity, event_type="score_recorded", event_at_utc=event_at,
                payload={"score_id": identity, "score_sha256": sha256_file(score_path),
                         "window_detail_sha256": sha256_file(detail_path), "score_receipt_sha256": sha256_file(receipt_path)},
                producer_provenance=provenance, expected_head_sha256=validation["head_sha256"],
            )
            built.append(package)
        old_packages.append(built[0])
        new_packages.append(built[1])
        old_score = built[0]["score"]
        atomic_replace_json(resolve_inside(runtime, f"scores/current/{old_score['forecast_id']}.json"), {
            "schema_version": "1.0.0", "artifact_type": "phase4_score_current_view",
            "forecast_id": old_score["forecast_id"], "score_id": old_score["score_id"],
            "result_revision_id": old_score["result_revision_id"],
        })

    ordered_old = sorted(old_packages, key=lambda package: (
        package["detail"]["issue_id"], package["score"]["result_revision_id"], package["score"]["score_id"],
    ))
    window_contract = {
        "schema_version": "1.0.0", "artifact_type": "phase4_window_input",
        "game": seed["game"], "model_id": "P4E1", "comparator_champion_id": "M0",
        "model_release_id": seed["scores"][0]["model_release_id"], "window_id": seed["window_id"],
        "metric_contract_id": supplied["metric_contract_id"],
        "score_ids": [package["score"]["score_id"] for package in ordered_old],
        "issue_ids": [package["detail"]["issue_id"] for package in ordered_old],
        "comparator_forecast_ids": [package["score"]["comparator_forecast_id"] for package in ordered_old],
    }
    window_contract["window_contract_id"] = content_id("window-input", window_contract)
    window_input_path = resolve_inside(runtime, f"window-inputs/{seed['window_id']}.json")
    write_once_json(window_input_path, window_contract)
    input_ledger = AppendOnlyLedger(runtime, "window-inputs")
    input_validation = input_ledger.validate()
    input_ledger.append_event(
        object_id=window_contract["window_contract_id"], event_type="window_input_registered", event_at_utc=event_at,
        payload={"window_contract_id": window_contract["window_contract_id"], "window_id": seed["window_id"],
                 "window_input_sha256": sha256_file(window_input_path)},
        producer_provenance=provenance, expected_head_sha256=input_validation["head_sha256"],
    )

    for package_set, is_current in ((old_packages, True), (new_packages, False)):
        window = build_oracle_fixture_window_metric(
            packages=package_set, game=seed["game"], model_id="P4E1", comparator_champion_id="M0",
            model_release_id=seed["scores"][0]["model_release_id"], window_id=seed["window_id"],
            metric_contract_id=supplied["metric_contract_id"],
        )
        identity = aggregate_id(window)
        window_path = resolve_inside(runtime, f"window-metrics/{identity}/window-metric.json")
        write_once_json(window_path, window)
        ledger = AppendOnlyLedger(runtime, "window-metrics")
        validation = ledger.validate()
        ledger.append_event(
            object_id=identity, event_type="window_metric_recorded", event_at_utc=event_at,
            payload={"aggregate_id": identity, "window_metric_sha256": sha256_file(window_path)},
            producer_provenance=provenance, expected_head_sha256=validation["head_sha256"],
        )
        if is_current:
            atomic_replace_json(resolve_inside(runtime, f"window-metrics/current/{seed['window_id']}.json"), {
                "schema_version": "1.0.0", "artifact_type": "phase4_window_metric_current_view",
                "window_id": seed["window_id"], "aggregate_id": identity,
            })

    for ledger_id, event_type, identities in (
        ("research-objects", "research_object_pending", seed["pending_research_object_ids"]),
        ("alpha-events", "alpha_event_recorded", seed["alpha_event_ids_before"]),
    ):
        for identity in identities:
            ledger = AppendOnlyLedger(runtime, ledger_id)
            validation = ledger.validate()
            ledger.append_event(object_id=identity, event_type=event_type, event_at_utc=event_at,
                                payload={"object_id": identity}, producer_provenance=provenance,
                                expected_head_sha256=validation["head_sha256"])
    write_once_json(resolve_inside(runtime, "control/t06-correction-fixture-identities.json"), {
        "schema_version": "1.0.0", "artifact_type": "phase4_t06_fixture_seed_identities",
        "new_result_revision_id": revisions[1]["result_revision_id"],
    })
    write_once_json(marker_path, marker)
    return revisions[1]["result_revision_id"]


def score_correct(args: Any) -> dict[str, Any]:
    root = project_root().resolve()
    runtime = validate_runtime_root(root, args.runtime_root)
    event_at = parse_clock(args.clock)
    fixture_path, oracle_root = _load_correction_fixture(args, runtime)
    if not fixture_path.is_file():
        raise CorrectionViolation("correction request is missing")
    supplied = load_json(fixture_path, reject_floats=True)
    common_fields = {"mode", "metric_contract_id", "new_result_revision_id", "correction_policy_path",
                     "correction_policy_version", "correction_policy_sha256"}
    mode = supplied.get("mode")
    expected_fields = common_fields | ({"seed_runtime"} if mode == "seed_isolated_runtime" else set())
    if mode not in {"seed_isolated_runtime", "runtime_claim"} or set(supplied) != expected_fields:
        raise CorrectionViolation("correction request shape mismatch")
    if type(supplied.get("correction_policy_path")) is not str:
        raise CorrectionViolation("correction request omits its correction policy path identity")
    requested_policy = Path(supplied["correction_policy_path"])
    policy_path = requested_policy.resolve() if requested_policy.is_absolute() else (fixture_path.parent / requested_policy).resolve()
    policy = validate_correction_policy(
        policy_path, expected_sha256=supplied.get("correction_policy_sha256"),
        expected_version=supplied.get("correction_policy_version"),
    )
    if (
        supplied.get("metric_contract_id") != "phase4-metric-v1"
        or supplied.get("correction_policy_version") != policy["correction_policy_version"]
        or supplied.get("correction_policy_sha256") != sha256_file(policy_path)
    ):
        raise CorrectionViolation("correction request does not bind the result, metric and policy identities")
    provenance = producer_provenance(root, runtime.relative_to(root).as_posix())
    if mode == "seed_isolated_runtime":
        seeded_revision = _seed_fixture_runtime(
            project=root, runtime=runtime, fixture_path=fixture_path, supplied=supplied,
            event_at=event_at, provenance=provenance,
        )
        if supplied.get("new_result_revision_id") != seeded_revision:
            raise CorrectionViolation("fixture claim does not match the seeded result revision")
    validate_stable_id(supplied.get("new_result_revision_id"), "new result revision identity")
    oracle_summary = _validate_metric_oracle(oracle_root) if str(oracle_root) not in {"", "."} else None

    selected_revision = _load_revision(runtime, supplied["new_result_revision_id"])
    resume_path = resolve_inside(runtime, f"corrections/current/{selected_revision['game']}-{selected_revision['issue_id']}.json")
    if resume_path.is_file():
        current = load_json(resume_path, reject_floats=True)
        if current.get("new_result_revision_id") != supplied["new_result_revision_id"]:
            raise CorrectionViolation("correction current view would roll back or fork")
        identity = current.get("score_correction_impact_id")
        destination = resolve_inside(runtime, f"corrections/{identity}")
        impact = load_json(destination / "score-correction-impact.json", reject_floats=True)
        receipt = load_json(destination / "score-side-receipt.json", reject_floats=True)
        if correction_impact_id(impact) != identity or receipt.get("score_correction_impact_id") != identity:
            raise CorrectionViolation("correction resume identity chain mismatch")
        replacements = receipt.get("current_replacements")
        for forecast_id, expected_score in replacements.get("scores", {}).items():
            if load_json(resolve_inside(runtime, f"scores/current/{forecast_id}.json"), reject_floats=True).get("score_id") != expected_score:
                raise CorrectionViolation("correction resume score projection mismatch")
        for window_id, expected_aggregate in replacements.get("aggregates", {}).items():
            if load_json(resolve_inside(runtime, f"window-metrics/current/{window_id}.json"), reject_floats=True).get("aggregate_id") != expected_aggregate:
                raise CorrectionViolation("correction resume window projection mismatch")
        return {"status": "PASS", "terminal": "PASS", "score_correction_impact_id": identity,
                "oracle_summary": oracle_summary, "score_side_complete": True, "idempotent_resume": True, "exit_code": 0}

    derived = derive_runtime_correction_graph(runtime, supplied["new_result_revision_id"])
    graph, bindings = derived["graph"], derived["bindings"]
    replacements = apply_current_replacements(
        current_scores=graph["current_scores"], current_aggregates=graph["current_aggregates"],
        score_replacements=graph["score_replacements"], aggregate_replacements=graph["aggregate_replacements"],
        expected_old_score_ids=list(graph["current_scores"].values()),
        expected_old_aggregate_ids=list(graph["current_aggregates"].values()),
    )
    impact = build_score_correction_impact(canonical_graph=graph)
    identity = correction_impact_id(impact)
    lock = AdvisoryFileLock(resolve_inside(runtime, "locks/score-correction.lock"))
    with lock:
        projection_path = resolve_inside(runtime, "corrections/score-window-current-view.json")
        if projection_path.exists():
            projection = load_json(projection_path, reject_floats=True)
            if projection != replacements:
                raise CorrectionViolation("score/window current-view head is stale or forked")
        else:
            atomic_replace_json(projection_path, replacements)
        destination = resolve_inside(runtime, f"corrections/{identity}")
        impact_path = destination / "score-correction-impact.json"
        if impact_path.exists():
            if load_json(impact_path, reject_floats=True) != impact:
                raise CorrectionViolation("correction impact identity reuse mismatch")
        else:
            write_once_json(impact_path, impact)
        plan_path = destination / "current-replacement-plan.json"
        plan = {"schema_version": "1.0.0", "artifact_type": "phase4_score_window_replacement_plan",
                "score_correction_impact_id": identity, "current_replacements": replacements,
                "runtime_bindings": bindings}
        if plan_path.exists():
            if load_json(plan_path, reject_floats=True) != plan:
                raise CorrectionViolation("correction replacement plan identity reuse mismatch")
        else:
            write_once_json(plan_path, plan)
        for forecast_id, old_score_id in graph["current_scores"].items():
            path = resolve_inside(runtime, f"scores/current/{forecast_id}.json")
            observed = load_json(path, reject_floats=True)
            replacement = graph["score_replacements"][old_score_id]
            if observed.get("score_id") == old_score_id:
                atomic_replace_json(path, {**observed, "score_id": replacement,
                                           "result_revision_id": graph["new_result_revision_id"]})
            elif observed.get("score_id") != replacement:
                raise CorrectionViolation("score current projection changed during correction")
        for window_id, old_aggregate_id in graph["current_aggregates"].items():
            path = resolve_inside(runtime, f"window-metrics/current/{window_id}.json")
            observed = load_json(path, reject_floats=True)
            replacement = graph["aggregate_replacements"][old_aggregate_id]
            if observed.get("aggregate_id") == old_aggregate_id:
                atomic_replace_json(path, {**observed, "aggregate_id": replacement})
            elif observed.get("aggregate_id") != replacement:
                raise CorrectionViolation("window current projection changed during correction")
        receipt = {
            "schema_version": "1.0.0", "artifact_type": "phase4_score_side_correction_receipt",
            "score_correction_impact_id": identity, "new_data_release_id": graph["new_data_release_id"],
            "current_replacements": replacements, "runtime_bindings": bindings, "correction_policy": {
                "path": str(policy_path), "sha256": supplied["correction_policy_sha256"],
                "version": supplied["correction_policy_version"],
            }, "corrected_at": event_at,
            "producer_provenance": provenance,
        }
        receipt_path = destination / "score-side-receipt.json"
        if receipt_path.exists():
            stored_receipt = load_json(receipt_path, reject_floats=True)
            stable_fields = {"schema_version", "artifact_type", "score_correction_impact_id", "new_data_release_id", "current_replacements", "runtime_bindings", "correction_policy"}
            if {key: stored_receipt.get(key) for key in stable_fields} != {key: receipt[key] for key in stable_fields}:
                raise CorrectionViolation("correction receipt identity reuse mismatch")
        else:
            write_once_json(receipt_path, receipt)
        current_path = resolve_inside(runtime, f"corrections/current/{graph['game']}-{graph['issue_id']}.json")
        if current_path.exists():
            current = load_json(current_path, reject_floats=True)
            if current.get("score_correction_impact_id") != identity:
                raise CorrectionViolation("correction current view would roll back or fork")
        else:
            atomic_replace_json(current_path, {
                "schema_version": "1.0.0", "artifact_type": "phase4_score_correction_current_view",
                "game": graph["game"], "issue_id": graph["issue_id"],
                "new_result_revision_id": graph["new_result_revision_id"], "score_correction_impact_id": identity,
            })
    return {"status": "PASS", "terminal": "PASS", "score_correction_impact_id": identity,
            "oracle_summary": oracle_summary, "score_side_complete": True, "idempotent_resume": False, "exit_code": 0}


def register(registry: ProviderRegistry) -> None:
    registry.register("score", "one", score_one)
    registry.register("score", "window", score_window)
    registry.register("score", "correct", score_correct)
