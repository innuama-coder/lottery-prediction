from __future__ import annotations

import os
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Mapping, Sequence

from .identity import content_id, validate_stable_id
from .ledger import AppendOnlyLedger
from .metrics import METRIC_CONTRACT_ID, TOP_K, MetricViolation, derive_score_id, score_zones
from .probability import DECIMAL_PRECISION, zone_distribution
from .ranking import zone_histogram
from .rules import game_rule
from .serialization import canonical_sha256, decimal_string, load_json, sha256_file
from .storage import resolve_inside


WILSON_Z_95 = Decimal("1.95996398454005423552")
MINIMUM_OBSERVATIONS = 30
ABSOLUTE_TOLERANCE = Decimal("1e-40")
RELATIVE_TOLERANCE = Decimal("1e-35")
T10_FIXTURE_CONTRACT_ID = "8073aa54c1d1fa8d06ff1fc56e9c2fa1c625744cd3d81e17b9575906f8157803"


class WindowViolation(MetricViolation):
    pass


class TrustedWindowAnchor:
    """Opaque, process-bound proof resolved from canonical runtime state."""

    __slots__ = ()

    def __init__(self, _forbidden: object = None) -> None:
        raise TypeError("trusted window anchors can only be resolved from runtime state")

    def __repr__(self) -> str:
        return "<trusted phase4 window anchor>"

    def __reduce__(self) -> Any:
        raise TypeError("trusted window anchors are nonserializable")

    def __copy__(self) -> Any:
        raise TypeError("trusted window anchors cannot be copied")

    def __deepcopy__(self, _memo: Mapping[int, Any]) -> Any:
        raise TypeError("trusted window anchors cannot be deep-copied")


def _parse_decimal(value: object, label: str) -> Decimal:
    if type(value) is not str:
        raise WindowViolation(f"{label} is not a decimal string")
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise WindowViolation(f"{label} is not decimal") from exc
    if not parsed.is_finite():
        raise WindowViolation(f"{label} is non-finite")
    return parsed


def aggregate_id(window: Mapping[str, Any]) -> str:
    return content_id("window-metric", window)


def wilson_95(successes: int, count: int) -> tuple[Decimal, Decimal]:
    if type(successes) is not int or type(count) is not int or count <= 0 or successes < 0 or successes > count:
        raise WindowViolation("Wilson inputs are invalid")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        n = Decimal(count)
        p = Decimal(successes) / n
        z2 = WILSON_Z_95 * WILSON_Z_95
        denominator = Decimal(1) + z2 / n
        center = (p + z2 / (Decimal(2) * n)) / denominator
        radius = WILSON_Z_95 * ((p * (Decimal(1) - p) / n + z2 / (Decimal(4) * n * n)).sqrt()) / denominator
        return +(center - radius), +(center + radius)


def reliability_summary(atoms: Sequence[tuple[Decimal, int]]) -> tuple[list[dict[str, Any]], Decimal]:
    if type(atoms) not in {list, tuple} or not atoms:
        raise WindowViolation("reliability atoms must be a nonempty sequence")
    if any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not Decimal or not row[0].is_finite()
           or row[0] < 0 or row[0] > 1 or type(row[1]) is not int or row[1] not in {0, 1} for row in atoms):
        raise WindowViolation("reliability atom is invalid")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        bins: list[dict[str, Any]] = []
        ece = Decimal(0)
        for index in range(10):
            selected = [(predicted, observed) for predicted, observed in atoms if min(9, int(predicted * Decimal(10))) == index]
            lower = Decimal(index) / Decimal(10)
            upper = Decimal(index + 1) / Decimal(10)
            if selected:
                mean = sum((value for value, _ in selected), Decimal(0)) / Decimal(len(selected))
                observed_rate = Decimal(sum(observed for _, observed in selected)) / Decimal(len(selected))
                ece += Decimal(len(selected)) / Decimal(len(atoms)) * abs(mean - observed_rate)
                mean_text, observed_text = decimal_string(mean), decimal_string(observed_rate)
            else:
                mean_text = observed_text = None
            bins.append({"bin_index": index, "lower": decimal_string(lower), "upper": decimal_string(upper),
                         "count": len(selected), "mean_predicted_probability": mean_text, "observed_rate": observed_text})
        return bins, +ece


def _joint_histogram(front_ticks: Sequence[int], front_k: int, back_ticks: Sequence[int], back_k: int) -> dict[int, int]:
    result: dict[int, int] = {}
    for left, left_count in zone_histogram(front_ticks, front_k).items():
        for right, right_count in zone_histogram(back_ticks, back_k).items():
            result[left + right] = result.get(left + right, 0) + left_count * right_count
    return result


def _exact_recomputation(score: Mapping[str, Any], detail: Mapping[str, Any]) -> dict[str, Any]:
    source = detail.get("recomputation")
    required = {
        "model_front", "model_back", "champion_front", "champion_back", "result", "ordered_tickets",
        "forecast_id", "result_revision_id", "metric_contract_id", "comparator_forecast_id",
    }
    if not isinstance(source, Mapping) or set(source) != required:
        raise WindowViolation("score recomputation source is incomplete")
    zones = {}
    for name in ("model_front", "model_back", "champion_front", "champion_back"):
        spec = source[name]
        if not isinstance(spec, Mapping) or set(spec) != {"ticks", "k"}:
            raise WindowViolation("score recomputation zone is incomplete")
        zones[name] = zone_distribution(spec["ticks"], spec["k"])
    result = source["result"]
    if not isinstance(result, Mapping) or set(result) != {"front", "back"}:
        raise WindowViolation("score recomputation result is incomplete")
    context = {field: detail[field] for field in (
        "game", "issue_id", "model_id", "model_release_id", "config_id", "comparator_champion_id",
    )}
    recomputed = score_zones(
        model_front=zones["model_front"], model_back=zones["model_back"],
        champion_front=zones["champion_front"], champion_back=zones["champion_back"],
        result_front=result["front"], result_back=result["back"],
        histogram=_joint_histogram(zones["model_front"].ticks, zones["model_front"].k,
                                   zones["model_back"].ticks, zones["model_back"].k),
        ordered_tickets=source["ordered_tickets"], forecast_id=source["forecast_id"],
        result_revision_id=source["result_revision_id"], metric_contract_id=source["metric_contract_id"],
        comparator_forecast_id=source["comparator_forecast_id"], context=context,
    )
    if recomputed["score"] != dict(score) or recomputed["detail"] != dict(detail):
        raise WindowViolation("score package differs from exact recomputation")
    return recomputed


def canonical_window_anchor(*, packages: Sequence[Mapping[str, Any]], window_id: str,
                            anchor_source_sha256: str,
                            current_projection_sha256: str | None = None,
                            window_contract_id: str = "oracle-window-contract",
                            window_input_sha256: str | None = None,
                            window_input_ledger_head_sha256: str | None = None) -> dict[str, Any]:
    if type(anchor_source_sha256) is not str or len(anchor_source_sha256) != 64 or any(c not in "0123456789abcdef" for c in anchor_source_sha256):
        raise WindowViolation("window anchor source hash is invalid")
    rows = list(packages)
    projection_sha256 = anchor_source_sha256 if current_projection_sha256 is None else current_projection_sha256
    if type(projection_sha256) is not str or len(projection_sha256) != 64 or any(c not in "0123456789abcdef" for c in projection_sha256):
        raise WindowViolation("window current-projection hash is invalid")
    contract_id = validate_stable_id(window_contract_id, "window contract identity")
    input_sha256 = anchor_source_sha256 if window_input_sha256 is None else window_input_sha256
    input_head = anchor_source_sha256 if window_input_ledger_head_sha256 is None else window_input_ledger_head_sha256
    for value in (input_sha256, input_head):
        if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise WindowViolation("window input trust hash is invalid")
    games = [package["detail"]["game"] for package in rows]
    if not games or len(set(games)) != 1:
        raise WindowViolation("window anchor packages do not bind exactly one game")
    body = {
        "schema_version": "1.0.0", "artifact_type": "phase4_canonical_window_anchor",
        "window_id": validate_stable_id(window_id, "window identity"),
        "window_contract_id": contract_id,
        "window_input_sha256": input_sha256,
        "window_input_ledger_head_sha256": input_head,
        "game": games[0],
        "anchor_source_sha256": anchor_source_sha256,
        "score_ledger_head_sha256": anchor_source_sha256,
        "current_projection_sha256": projection_sha256,
        "package_sha256": [canonical_sha256(dict(package)) for package in rows],
        "score_ids": [package["score"]["score_id"] for package in rows],
        "result_revision_ids": [package["score"]["result_revision_id"] for package in rows],
        "issue_ids": [package["detail"]["issue_id"] for package in rows],
        "comparator_forecast_ids": [package["score"]["comparator_forecast_id"] for package in rows],
    }
    body["anchor_sha256"] = canonical_sha256(body)
    return body


def _resolve_trusted_window_state(*, runtime_root: Path,
                                  window_id: str) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Resolve packages and a trust capability from the score ledger/current views."""
    runtime = Path(runtime_root).resolve()
    contract_path = resolve_inside(runtime, f"window-inputs/{window_id}.json")
    if not contract_path.is_file():
        raise WindowViolation("registered immutable window input is missing")
    contract = load_json(contract_path, reject_floats=True)
    contract_fields = {"schema_version", "artifact_type", "window_contract_id", "game", "model_id",
                       "comparator_champion_id", "model_release_id", "window_id", "metric_contract_id",
                       "score_ids", "issue_ids", "comparator_forecast_ids"}
    if (set(contract) != contract_fields or contract.get("schema_version") != "1.0.0"
            or contract.get("artifact_type") != "phase4_window_input" or contract.get("window_id") != window_id
            or content_id("window-input", contract, excluded_fields=("window_contract_id",)) != contract.get("window_contract_id")):
        raise WindowViolation("registered window input identity mismatch")
    game = contract["game"]
    model_id = contract["model_id"]
    comparator_champion_id = contract["comparator_champion_id"]
    model_release_id = contract["model_release_id"]
    metric_contract_id = contract["metric_contract_id"]
    ordered_score_ids = contract["score_ids"]
    requested_key = {"game": game, "model_id": model_id, "comparator_champion_id": comparator_champion_id,
                     "model_release_id": model_release_id, "metric_contract_id": metric_contract_id}
    if (game not in {"ssq", "dlt"} or comparator_champion_id != "M0" or metric_contract_id != METRIC_CONTRACT_ID
            or type(ordered_score_ids) not in {list, tuple} or not ordered_score_ids):
        raise WindowViolation("trusted window request is invalid")
    input_ledger = AppendOnlyLedger(runtime, "window-inputs")
    input_validation = input_ledger.validate()
    if not input_validation["event_count"] or input_validation["head_sha256"] is None:
        raise WindowViolation("registered window input ledger is empty")
    input_view = load_json(input_ledger.current_view_path, reject_floats=True)
    input_item = input_view.get("objects", {}).get(contract["window_contract_id"])
    if input_item is None or input_item.get("event_type") != "window_input_registered":
        raise WindowViolation("window input is not registered in its immutable ledger")
    input_payload_path = input_ledger.payloads_root / f"{input_item['payload_sha256']}.json"
    expected_input_payload = {"window_contract_id": contract["window_contract_id"],
                              "window_id": window_id, "window_input_sha256": sha256_file(contract_path)}
    if not input_payload_path.is_file() or load_json(input_payload_path, reject_floats=True) != expected_input_payload:
        raise WindowViolation("window input ledger payload mismatch")
    ledger = AppendOnlyLedger(runtime, "scores")
    validation = ledger.validate()
    if not validation["event_count"] or validation["head_sha256"] is None:
        raise WindowViolation("trusted window score ledger is empty")
    ledger_view = load_json(ledger.current_view_path, reject_floats=True)
    candidates: list[tuple[str, str, str, dict[str, Any], dict[str, Any]]] = []
    current_root = resolve_inside(runtime, "scores/current")
    if not current_root.is_dir():
        raise WindowViolation("trusted window current score projection root is missing")
    for current_path in sorted(path for path in current_root.iterdir() if path.is_file() and path.suffix == ".json"):
        current = load_json(current_path, reject_floats=True)
        if set(current) != {"schema_version", "artifact_type", "forecast_id", "score_id", "result_revision_id"}:
            raise WindowViolation("trusted window current score projection shape mismatch")
        score_id = current["score_id"]
        validate_stable_id(score_id, "score identity")
        destination = resolve_inside(runtime, f"scores/{score_id}")
        score_path, detail_path, receipt_path = destination / "score.json", destination / "window-detail.json", destination / "score-receipt.json"
        if not all(path.is_file() for path in (score_path, detail_path, receipt_path)):
            raise WindowViolation("trusted window score package is incomplete")
        score, detail, receipt = (load_json(path, reject_floats=True) for path in (score_path, detail_path, receipt_path))
        if score.get("score_id") != score_id or detail.get("score_id") != score_id or receipt.get("score_id") != score_id:
            raise WindowViolation("trusted window score identity chain mismatch")
        if receipt.get("score_sha256") != sha256_file(score_path) or receipt.get("window_detail_sha256") != sha256_file(detail_path):
            raise WindowViolation("trusted window score hashes mismatch")
        item = ledger_view.get("objects", {}).get(score_id)
        if item is None or item.get("event_type") != "score_recorded":
            raise WindowViolation("trusted window score is absent from the score ledger")
        payload_path = ledger.payloads_root / f"{item['payload_sha256']}.json"
        expected_payload = {"score_id": score_id, "score_sha256": sha256_file(score_path),
                            "window_detail_sha256": sha256_file(detail_path),
                            "score_receipt_sha256": sha256_file(receipt_path)}
        if not payload_path.is_file() or load_json(payload_path, reject_floats=True) != expected_payload:
            raise WindowViolation("trusted window score ledger payload mismatch")
        if (current.get("forecast_id") != score.get("forecast_id") or current.get("score_id") != score_id
                or current.get("result_revision_id") != score.get("result_revision_id")):
            raise WindowViolation("trusted window score is not current")
        observed_key = {"game": detail.get("game"), "model_id": detail.get("model_id"),
                        "comparator_champion_id": detail.get("comparator_champion_id"),
                        "model_release_id": detail.get("model_release_id"),
                        "metric_contract_id": score.get("metric_contract_id")}
        if observed_key == requested_key:
            candidates.append((detail["issue_id"], score["result_revision_id"], score_id,
                               {"score": score, "detail": detail},
                               {"path": current_path.relative_to(runtime).as_posix(), "sha256": sha256_file(current_path),
                                "forecast_id": score["forecast_id"], "score_id": score_id,
                                "result_revision_id": score["result_revision_id"]}))
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    derived_score_ids = [row[2] for row in candidates]
    if list(ordered_score_ids) != derived_score_ids:
        raise WindowViolation("caller score membership/order differs from the complete current projection")
    packages = [row[3] for row in candidates]
    projection_rows = [row[4] for row in candidates]
    if not packages:
        raise WindowViolation("trusted window current projection has no matching scores")
    if contract["issue_ids"] != [package["detail"]["issue_id"] for package in packages]:
        raise WindowViolation("registered window issue membership/order differs from current scores")
    if contract["comparator_forecast_ids"] != [package["score"]["comparator_forecast_id"] for package in packages]:
        raise WindowViolation("registered window comparator membership/order differs from current scores")
    projection_sha256 = canonical_sha256(projection_rows)
    anchor = canonical_window_anchor(
        packages=packages, window_id=window_id, anchor_source_sha256=validation["head_sha256"],
        current_projection_sha256=projection_sha256,
        window_contract_id=contract["window_contract_id"], window_input_sha256=sha256_file(contract_path),
        window_input_ledger_head_sha256=input_validation["head_sha256"],
    )
    payload = {"runtime_root": str(runtime), "window_id": window_id, "game": game,
               "score_ledger_head_sha256": validation["head_sha256"],
               "current_projection_sha256": projection_sha256,
               "current_projection_rows": projection_rows, "anchor": anchor,
               "expected_anchor_sha256": anchor["anchor_sha256"],
               "window_input_path": contract_path.relative_to(runtime).as_posix(),
               "window_input_sha256": sha256_file(contract_path),
               "window_input_ledger_head_sha256": input_validation["head_sha256"]}
    return packages, payload, contract


def _closed_window_authority_boundary():
    registry: dict[TrustedWindowAnchor, tuple[int, Path, str, str]] = {}

    def resolve(*, runtime_root: Path,
                window_id: str) -> tuple[list[dict[str, Any]], TrustedWindowAnchor, dict[str, Any]]:
        runtime = Path(runtime_root).resolve()
        packages, payload, contract = _resolve_trusted_window_state(
            runtime_root=runtime, window_id=window_id,
        )
        capability = object.__new__(TrustedWindowAnchor)
        registry[capability] = (os.getpid(), runtime, window_id, payload["expected_anchor_sha256"])
        return packages, capability, contract

    def validate(value: object) -> dict[str, Any]:
        if type(value) is not TrustedWindowAnchor:
            raise WindowViolation("missing externally trusted window anchor")
        record = registry.get(value)
        if record is None:
            raise WindowViolation("trusted window anchor is unknown to this process")
        owner_pid, runtime, window_id, expected_anchor_sha256 = record
        if owner_pid != os.getpid():
            raise WindowViolation("trusted window anchor belongs to another process")
        _packages, payload, _contract = _resolve_trusted_window_state(
            runtime_root=runtime, window_id=window_id,
        )
        if payload["expected_anchor_sha256"] != expected_anchor_sha256:
            raise WindowViolation("trusted window anchor runtime state changed after resolution")
        return payload

    if hasattr(os, "register_at_fork"):
        os.register_at_fork(after_in_child=registry.clear)
    return resolve, validate


resolve_trusted_window_inputs, _trusted_anchor_payload = _closed_window_authority_boundary()


def _validated_packages(packages: Sequence[Mapping[str, Any]], key: Mapping[str, str],
                        canonical_anchor: Mapping[str, Any], *, allow_oracle_fixture: bool) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if type(packages) not in {list, tuple}:
        raise WindowViolation("window packages must be an ordered sequence")
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    issues: set[str] = set()
    score_ids: set[str] = set()
    for package in packages:
        if not isinstance(package, Mapping) or set(package) != {"score", "detail"} or not isinstance(package["score"], Mapping) or not isinstance(package["detail"], Mapping):
            raise WindowViolation("window score package shape mismatch")
        score, detail = dict(package["score"]), dict(package["detail"])
        score_fields = {
            "schema_version", "artifact_type", "score_id", "forecast_id", "result_revision_id",
            "metric_contract_id", "comparator_forecast_id", "hit_at_k", "joint_log_score",
            "skill_vs_champion", "inclusion_brier", "tie_rank_lower", "tie_rank_upper",
            "tie_midrank", "midrank_percentile",
        }
        detail_fields = {
            "schema_version", "artifact_type", "score_id", "game", "issue_id", "model_id",
            "model_release_id", "config_id", "comparator_champion_id", "front_inclusion",
            "back_inclusion", "observed_front", "observed_back", "recomputation",
        }
        if set(score) != score_fields or set(detail) != detail_fields:
            raise WindowViolation("window score/detail field set mismatch")
        if score["schema_version"] != "1.0.0" or score["artifact_type"] != "phase4_score" or detail["schema_version"] != "1.0.0" or detail["artifact_type"] != "phase4_score_window_detail":
            raise WindowViolation("window score/detail artifact identity mismatch")
        if score.get("score_id") != derive_score_id(score.get("forecast_id"), score.get("result_revision_id"), score.get("metric_contract_id")):
            raise WindowViolation("window score identity mismatch")
        if detail.get("score_id") != score["score_id"]:
            raise WindowViolation("window score/detail identity mismatch")
        if score["score_id"] in score_ids:
            raise WindowViolation("window contains a duplicate score")
        score_ids.add(score["score_id"])
        issue = detail.get("issue_id")
        if type(issue) is not str or not issue or issue in issues:
            raise WindowViolation("window must contain unique issue identities")
        issues.add(issue)
        comparisons = {
            "game": detail.get("game"), "model_id": detail.get("model_id"),
            "model_release_id": detail.get("model_release_id"),
            "comparator_champion_id": detail.get("comparator_champion_id"),
            "metric_contract_id": score.get("metric_contract_id"),
        }
        if any(comparisons[field] != key[field] for field in comparisons):
            raise WindowViolation("window member differs from the frozen window key")
        if set(score["hit_at_k"]) != {str(value) for value in TOP_K} or any(type(value) is not int or value not in {0, 1} for value in score["hit_at_k"].values()):
            raise WindowViolation("window score hit fields are invalid")
        if [score["hit_at_k"][str(value)] for value in TOP_K] != sorted(score["hit_at_k"][str(value)] for value in TOP_K):
            raise WindowViolation("window hit@K values are not monotone")
        if type(score["tie_rank_lower"]) is not int or type(score["tie_rank_upper"]) is not int or not 1 <= score["tie_rank_lower"] <= score["tie_rank_upper"]:
            raise WindowViolation("window score rank fields are invalid")
        for field in ("joint_log_score", "skill_vs_champion", "inclusion_brier", "tie_midrank", "midrank_percentile"):
            _parse_decimal(score[field], f"window {field}")
        midrank = _parse_decimal(score["tie_midrank"], "window tie midrank")
        if midrank != (Decimal(score["tie_rank_lower"]) + Decimal(score["tie_rank_upper"])) / Decimal(2):
            raise WindowViolation("window tie midrank is inconsistent")
        log_score = _parse_decimal(score["joint_log_score"], "window joint log score")
        brier = _parse_decimal(score["inclusion_brier"], "window inclusion Brier")
        percentile = _parse_decimal(score["midrank_percentile"], "window rank percentile")
        source = detail["recomputation"]
        if not isinstance(source, Mapping) or not isinstance(source.get("model_front"), Mapping) or not isinstance(source.get("model_back"), Mapping):
            raise WindowViolation("window recomputation rule is incomplete")
        front_n, back_n = len(source["model_front"].get("ticks", [])), len(source["model_back"].get("ticks", []))
        front_k, back_k = source["model_front"].get("k"), source["model_back"].get("k")
        if any(type(value) is not int for value in (front_k, back_k)):
            raise WindowViolation("window recomputation cardinality is invalid")
        if not allow_oracle_fixture:
            rule = game_rule(detail["game"])
            if (front_n, front_k, back_n, back_k) != (rule.front_n, rule.front_k, rule.back_n, rule.back_k):
                raise WindowViolation("window inclusion vectors differ from the registered game rule")
            full_space = rule.space_size
        else:
            if canonical_anchor["anchor_source_sha256"] != T10_FIXTURE_CONTRACT_ID:
                raise WindowViolation("window oracle anchor source is not frozen")
            from math import comb
            full_space = comb(front_n, front_k) * comb(back_n, back_k)
        if log_score < 0 or not 0 <= brier <= 1 or not 0 <= percentile <= 1:
            raise WindowViolation("window score range is invalid")
        if score["tie_rank_upper"] > full_space:
            raise WindowViolation("window tie rank exceeds the legal full space")
        with localcontext() as percentile_context:
            percentile_context.prec = DECIMAL_PRECISION
            expected_percentile = +((midrank - Decimal("0.5")) / Decimal(full_space))
        if abs(percentile - expected_percentile) > max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * abs(expected_percentile)):
            raise WindowViolation("window rank percentile is inconsistent with the exact tie band")
        for vector, observed, n, k in (
            (detail.get("front_inclusion"), detail.get("observed_front"), front_n, front_k),
            (detail.get("back_inclusion"), detail.get("observed_back"), back_n, back_k),
        ):
            if type(vector) is not list or len(vector) != n or type(observed) is not list or len(observed) != k:
                raise WindowViolation("window inclusion detail is incomplete")
            try:
                parsed = [_parse_decimal(value, "window inclusion probability") for value in vector]
            except Exception as exc:
                raise WindowViolation("window inclusion probability is not decimal") from exc
            if any(value < 0 or value > 1 for value in parsed):
                raise WindowViolation("window inclusion probability is invalid")
            with localcontext() as sum_context:
                sum_context.prec = DECIMAL_PRECISION
                inclusion_sum = sum(parsed, Decimal(0))
            if abs(inclusion_sum - Decimal(k)) > max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * Decimal(k)):
                raise WindowViolation("window inclusion probabilities do not sum to the fixed cardinality")
            if (any(type(number) is not int for number in observed) or len(set(observed)) != len(observed)
                    or observed != sorted(observed) or any(number < 1 or number > n for number in observed)):
                raise WindowViolation("window observed ticket is invalid")
        _exact_recomputation(score, detail)
        rows.append((score, detail))
    observed_anchor = canonical_window_anchor(
        packages=[{"score": score, "detail": detail} for score, detail in rows],
        window_id=key["window_id"], anchor_source_sha256=canonical_anchor["anchor_source_sha256"],
        current_projection_sha256=canonical_anchor["current_projection_sha256"],
        window_contract_id=canonical_anchor["window_contract_id"],
        window_input_sha256=canonical_anchor["window_input_sha256"],
        window_input_ledger_head_sha256=canonical_anchor["window_input_ledger_head_sha256"],
    )
    if dict(canonical_anchor) != observed_anchor:
        raise WindowViolation("window packages differ from the canonical anchor")
    return rows


def _build_window_metric(*, packages: Sequence[Mapping[str, Any]], game: str, model_id: str,
                         comparator_champion_id: str, model_release_id: str, window_id: str,
                         metric_contract_id: str, canonical_anchor: Mapping[str, Any],
                         allow_oracle_fixture: bool) -> dict[str, Any]:
    key = {
        "game": game,
        "model_id": validate_stable_id(model_id, "model identity"),
        "comparator_champion_id": validate_stable_id(comparator_champion_id, "Champion identity"),
        "model_release_id": validate_stable_id(model_release_id, "model release identity"),
        "window_id": validate_stable_id(window_id, "window identity"),
        "metric_contract_id": metric_contract_id,
    }
    if game not in {"ssq", "dlt"} or metric_contract_id != METRIC_CONTRACT_ID:
        raise WindowViolation("window key is not registered")
    anchor_fields = {"schema_version", "artifact_type", "window_id", "window_contract_id", "window_input_sha256",
                     "window_input_ledger_head_sha256", "game", "anchor_source_sha256",
                     "score_ledger_head_sha256", "current_projection_sha256", "package_sha256", "score_ids",
                     "result_revision_ids", "issue_ids", "comparator_forecast_ids", "anchor_sha256"}
    if not isinstance(canonical_anchor, Mapping) or set(canonical_anchor) != anchor_fields:
        raise WindowViolation("canonical window anchor shape mismatch")
    if canonical_anchor["schema_version"] != "1.0.0" or canonical_anchor["artifact_type"] != "phase4_canonical_window_anchor":
        raise WindowViolation("canonical window anchor artifact mismatch")
    supplied_hash = canonical_anchor["anchor_sha256"]
    if supplied_hash != canonical_sha256({field: canonical_anchor[field] for field in canonical_anchor if field != "anchor_sha256"}):
        raise WindowViolation("canonical window anchor hash mismatch")
    if canonical_anchor["window_id"] != key["window_id"] or canonical_anchor["game"] != key["game"]:
        raise WindowViolation("canonical window anchor key mismatch")
    if canonical_anchor["anchor_source_sha256"] != canonical_anchor["score_ledger_head_sha256"]:
        raise WindowViolation("canonical window anchor ledger identity mismatch")
    rows = _validated_packages(packages, key, canonical_anchor, allow_oracle_fixture=allow_oracle_fixture)
    count = len(rows)
    body: dict[str, Any] = {
        "schema_version": "1.0.0", "artifact_type": "phase4_window_metric", **key,
        "observation_count": count,
        "score_ids": [score["score_id"] for score, _ in rows],
        "aggregate_state": "insufficient_observation" if count < MINIMUM_OBSERVATIONS else "available",
        "values": None,
    }
    if count < MINIMUM_OBSERVATIONS:
        return body
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        divisor = Decimal(count)
        means = {
            "mean_joint_log_score": sum(Decimal(score["joint_log_score"]) for score, _ in rows) / divisor,
            "mean_skill": sum(Decimal(score["skill_vs_champion"]) for score, _ in rows) / divisor,
            "mean_inclusion_brier": sum(Decimal(score["inclusion_brier"]) for score, _ in rows) / divisor,
            "mean_rank_percentile": sum(Decimal(score["midrank_percentile"]) for score, _ in rows) / divisor,
        }
        rates: dict[str, str] = {}
        intervals: dict[str, dict[str, str]] = {}
        for k in TOP_K:
            successes = sum(score["hit_at_k"][str(k)] for score, _ in rows)
            low, high = wilson_95(successes, count)
            rates[str(k)] = decimal_string(Decimal(successes) / divisor)
            intervals[str(k)] = {"lower": decimal_string(low), "upper": decimal_string(high)}
        atoms: list[tuple[Decimal, int]] = []
        for _, detail in rows:
            for vector_key, observed_key in (("front_inclusion", "observed_front"), ("back_inclusion", "observed_back")):
                observed = set(detail[observed_key])
                atoms.extend((Decimal(value), int(number in observed)) for number, value in enumerate(detail[vector_key], start=1))
        bins, ece = reliability_summary(atoms)
        changes: list[Decimal] = []
        for (_, prior), (_, current) in zip(rows, rows[1:]):
            if prior["config_id"] != current["config_id"]:
                continue
            before = [Decimal(value) for value in prior["front_inclusion"] + prior["back_inclusion"]]
            after = [Decimal(value) for value in current["front_inclusion"] + current["back_inclusion"]]
            if len(before) != len(after):
                raise WindowViolation("adjacent stability vectors have different rules")
            changes.extend(abs(left - right) for left, right in zip(before, after))
        stability = sum(changes, Decimal(0)) / Decimal(len(changes)) if changes else Decimal(0)
        body["values"] = {**{field: decimal_string(value) for field, value in means.items()},
                          "cumulative_hit_rate": rates, "wilson_95": intervals,
                          "reliability": bins, "ece": decimal_string(ece), "stability": decimal_string(stability)}
    return body


def build_window_metric(*, packages: Sequence[Mapping[str, Any]], game: str, model_id: str,
                        comparator_champion_id: str, model_release_id: str, window_id: str,
                        metric_contract_id: str, trusted_anchor: TrustedWindowAnchor) -> dict[str, Any]:
    trust = _trusted_anchor_payload(trusted_anchor)
    anchor = trust["anchor"]
    if (trust["window_id"] != window_id or trust["game"] != game
            or trust["expected_anchor_sha256"] != anchor["anchor_sha256"]):
        raise WindowViolation("externally trusted window anchor identity mismatch")
    return _build_window_metric(
        packages=packages, game=game, model_id=model_id, comparator_champion_id=comparator_champion_id,
        model_release_id=model_release_id, window_id=window_id, metric_contract_id=metric_contract_id,
        canonical_anchor=anchor, allow_oracle_fixture=False,
    )


def build_oracle_fixture_window_metric(*, packages: Sequence[Mapping[str, Any]], game: str, model_id: str,
                                       comparator_champion_id: str, model_release_id: str, window_id: str,
                                       metric_contract_id: str) -> dict[str, Any]:
    anchor = canonical_window_anchor(packages=packages, window_id=window_id, anchor_source_sha256=T10_FIXTURE_CONTRACT_ID)
    return _build_window_metric(
        packages=packages, game=game, model_id=model_id, comparator_champion_id=comparator_champion_id,
        model_release_id=model_release_id, window_id=window_id, metric_contract_id=metric_contract_id,
        canonical_anchor=anchor, allow_oracle_fixture=True,
    )
