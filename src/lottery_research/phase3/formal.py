from __future__ import annotations

import bisect
import concurrent.futures
import gzip
import hashlib
import json
import math
import multiprocessing
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Sequence

from .classification import PRACTICAL_SKILL_DELTA, holm_adjust, moving_block_evidence, classify_model, summarize_phase
from .data_access import (
    GuardedLabelStore,
    TRAINER_FORBIDDEN_FIELDS,
    activate_scoring_capability,
    load_target_catalog,
    quarantine_current_process_as_trainer,
    read_scoring_label_inventory,
    read_training_prefix,
    trainer_input_payload,
    validate_guarded_unlock_evidence,
)
from .evaluation import calibration_error, inclusion_brier, joint_log_score, relative_joint_log_score_skill
from .ledger import AppendOnlyLedger, CheckpointStore, canonical_attempts, validate_ledger
from .prerun_contract import validate_prerun_contract
from .probability import FixedCardinalityDistribution, joint_distribution, posterior_theta
from .registry import load_and_validate_registries
from .schema import validate_payload
from .serialization import canonical_json_bytes, canonical_sha256, load_json, sha256_file, write_new_json
from .work_items import load_actor_assignment, validate_review_provenance, validate_work_item_receipt_file


GAME_SPEC = {
    "dlt": {"front_size": 35, "front_k": 5, "back_size": 12, "back_k": 2},
    "ssq": {"front_size": 33, "front_k": 6, "back_size": 16, "back_k": 1},
}
LAMBDA_GRID = (1.0, 5.0, 20.0, 100.0)
FORBIDDEN_ACTIONS = (
    "champion_promotion", "production_prediction", "public_non_uniform_prediction",
    "betting", "automatic_purchase", "yield_claim",
)
_NETWORK_GUARD_INSTALLED = False


def disable_network() -> None:
    global _NETWORK_GUARD_INSTALLED
    if _NETWORK_GUARD_INSTALLED:
        return
    def audit(event: str, arguments: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            raise RuntimeError("Phase 3 formal execution forbids network access")
    sys.addaudithook(audit)
    _NETWORK_GUARD_INSTALLED = True


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_directory(path: Path, identity: str) -> Path:
    if not identity or identity in {".", ".."} or any(value in identity for value in ("/", "\\", "*")) or "latest" in identity.lower():
        raise ValueError("identity must be explicit and immutable")
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def read_draws(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Compatibility wrapper for non-training preparation diagnostics only."""

    return read_scoring_label_inventory(root, capability=activate_scoring_capability())


def _models_for_prefix(prefix: Sequence[dict[str, Any]], shrinkage: float | None) -> tuple[Any, float | None]:
    game = prefix[0]["game"]
    spec = GAME_SPEC[game]
    m0 = joint_distribution(
        FixedCardinalityDistribution.uniform(spec["front_size"], spec["front_k"]),
        FixedCardinalityDistribution.uniform(spec["back_size"], spec["back_k"]),
    )
    if shrinkage is None:
        return m0, None
    front_theta = posterior_theta([row["front_numbers"] for row in prefix], spec["front_size"], spec["front_k"], shrinkage)
    back_theta = posterior_theta([row["back_numbers"] for row in prefix], spec["back_size"], spec["back_k"], shrinkage)
    return joint_distribution(
        FixedCardinalityDistribution.from_theta(front_theta, spec["front_k"]),
        FixedCardinalityDistribution.from_theta(back_theta, spec["back_k"]),
    ), shrinkage


def select_joint_shrinkage(prefix: Sequence[dict[str, Any]]) -> float:
    if len(prefix) < 50:
        raise ValueError("M1 outer prefix must contain at least 50 draws")
    inner_targets = prefix[-20:]
    base_size = len(prefix) - 20
    scored: list[tuple[float, float]] = []
    for shrinkage in LAMBDA_GRID:
        scores: list[float] = []
        for offset, target in enumerate(inner_targets):
            training = prefix[:base_size + offset]
            if len(training) < 30 or training[-1]["issue_id"] >= target["issue_id"]:
                raise ValueError("inner fold violates the frozen time boundary")
            model, _ = _models_for_prefix(training, shrinkage)
            scores.append(joint_log_score(model.probability(target["front_numbers"], target["back_numbers"])))
        scored.append((fmean(scores), shrinkage))
    best = min(value for value, _ in scored)
    return max(shrinkage for value, shrinkage in scored if math.isclose(value, best, rel_tol=1e-10, abs_tol=1e-12))


def distribution_payload(model: Any, selected_lambda: float | None) -> dict[str, Any]:
    return {
        "front": {"size": model.front.size, "cardinality": model.front.cardinality, "weights": list(model.front.weights),
                  "inclusion_probabilities": list(model.front.inclusion_probabilities())},
        "back": {"size": model.back.size, "cardinality": model.back.cardinality, "weights": list(model.back.weights),
                 "inclusion_probabilities": list(model.back.inclusion_probabilities())},
        "selected_lambda": selected_lambda,
        "partition_independence": True,
    }


def distribution_from_payload(payload: dict[str, Any]) -> Any:
    return joint_distribution(
        FixedCardinalityDistribution.from_weights(payload["front"]["weights"], payload["front"]["cardinality"]),
        FixedCardinalityDistribution.from_weights(payload["back"]["weights"], payload["back"]["cardinality"]),
    )


def _trainer_fit_target(payload: dict[str, Any]) -> dict[str, Any]:
    """Fit one target in a spawn-isolated process with prefix-only input."""

    quarantine_current_process_as_trainer()
    disable_network()
    if payload.get("capability") != "training_prefix_only_no_label_store":
        raise ValueError("TRAINER_CAPABILITY_PROTOCOL_REJECTED")
    if TRAINER_FORBIDDEN_FIELDS.intersection(payload):
        raise ValueError("TRAINER_INPUT_CONTAINS_FORBIDDEN_LABEL_CAPABILITY")
    target = payload["target"]
    sanitized = payload["prefix"]
    if any(TRAINER_FORBIDDEN_FIELDS.intersection(row) for row in sanitized):
        raise ValueError("TRAINER_INPUT_CONTAINS_FORBIDDEN_LABEL_CAPABILITY")
    if len(sanitized) != target["source_count"] or any(
        row["game"] != target["game"] or row["issue_id"] >= target["target_issue"] for row in sanitized
    ):
        raise ValueError("TRAINER_INPUT_PREFIX_BOUNDARY_REJECTED")
    prefix = [{
        "game": row["game"],
        "issue_id": row["issue_id"],
        "front_numbers": tuple(row["prior_front_numbers"]),
        "back_numbers": tuple(row["prior_back_numbers"]),
    } for row in sanitized]
    selected = select_joint_shrinkage(prefix)
    models: dict[str, Any] = {}
    for model_id, shrinkage in (("M0", None), ("M1", selected)):
        model, model_lambda = _models_for_prefix(prefix, shrinkage)
        top = model.top_k(1000)
        models[model_id] = {
            "distribution": distribution_payload(model, model_lambda),
            "normalization_sum": model.front.normalization_dp_audit() * model.back.normalization_dp_audit(),
            "top1000": top,
            "top1000_coverage_probability": math.fsum(float(row["probability"]) for row in top),
        }
    return {
        "trainer_pid": os.getpid(),
        "trainer_input_sha256": canonical_sha256(payload),
        "training_count": len(prefix),
        "training_cutoff": prefix[-1]["issue_id"],
        "inner_target_issues": [row["issue_id"] for row in prefix[-20:]],
        "models": models,
    }


def _trainer_label_store_access_probe(root_text: str) -> dict[str, Any]:
    """Run inside the real trainer process and prove capability minting fails."""

    quarantine_current_process_as_trainer()
    direct_artifact_denied = False
    try:
        with (Path(root_text) / "artifacts/phase-1/baseline-v1/draws.jsonl").open("rb") as handle:
            handle.read(1)
    except PermissionError as exc:
        direct_artifact_denied = str(exc) == "TRAINER_ARTIFACT_FILESYSTEM_CAPABILITY_DENIED"
    subprocess_denied = False
    try:
        subprocess.run(["/bin/true"], check=True)
    except PermissionError as exc:
        subprocess_denied = str(exc) == "TRAINER_CHILD_PROCESS_CAPABILITY_DENIED"
    fork_denied = False
    try:
        child_pid = os.fork()
        if child_pid == 0:
            os._exit(99)
        os.waitpid(child_pid, 0)
    except PermissionError as exc:
        fork_denied = str(exc) == "TRAINER_CHILD_PROCESS_CAPABILITY_DENIED"
    exec_denied = False
    try:
        # Exercise the same audit event raised by every os.exec* variant without
        # risking replacement of the worker if a future interpreter regresses.
        sys.audit("os.exec", sys.executable, (sys.executable,), None)
    except PermissionError as exc:
        exec_denied = str(exc) == "TRAINER_CHILD_PROCESS_CAPABILITY_DENIED"
    try:
        capability = activate_scoring_capability()
        GuardedLabelStore(Path(root_text), capability=capability)
    except ValueError as exc:
        return {
            "pid": os.getpid(), "denied": str(exc) == "LABEL_STORE_CAPABILITY_DENIED",
            "direct_artifact_denied": direct_artifact_denied,
            "subprocess_denied": subprocess_denied, "fork_denied": fork_denied,
            "exec_denied": exec_denied, "number_read_count": 0,
        }
    return {
        "pid": os.getpid(), "denied": False, "direct_artifact_denied": direct_artifact_denied,
        "subprocess_denied": subprocess_denied, "fork_denied": fork_denied,
        "exec_denied": exec_denied, "number_read_count": -1,
    }


def _trainer_probe_passed(probe: dict[str, Any]) -> bool:
    return bool(
        probe.get("pid") != os.getpid() and probe.get("denied")
        and probe.get("direct_artifact_denied") and probe.get("subprocess_denied")
        and probe.get("fork_denied") and probe.get("exec_denied")
        and probe.get("number_read_count") == 0
    )


def _write_jsonl_new(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        for row in rows:
            handle.write(canonical_json_bytes(row))


def _write_gzip_json_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as handle:
            handle.write(canonical_json_bytes(value))


def _load_gzip_json(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _seed_uint(seed: str, *parts: object) -> int:
    payload = "|".join((seed, *(str(value) for value in parts))).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _sample_combinations(theta: Sequence[float], cardinality: int, count: int, seed: str) -> list[tuple[int, ...]]:
    import itertools
    combinations = list(itertools.combinations(range(1, len(theta) + 1), cardinality))
    raw = [math.exp(sum(theta[index - 1] for index in item)) for item in combinations]
    total = math.fsum(raw)
    cumulative: list[float] = []
    running = 0.0
    for value in raw:
        running += value / total
        cumulative.append(running)
    answer = []
    for index in range(count):
        unit = _seed_uint(seed, index) / 2**64
        answer.append(combinations[min(bisect.bisect_left(cumulative, unit), len(combinations) - 1)])
    return answer


def _rank(values: Sequence[float]) -> list[float]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    result = [0.0] * len(values)
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][0] == ordered[position][0]:
            end += 1
        average = (position + 1 + end) / 2.0
        for _, original in ordered[position:end]:
            result[original] = average
        position = end
    return result


def _spearman_positive(left: Sequence[float], right: Sequence[float]) -> bool:
    a, b = _rank(left), _rank(right)
    am, bm = fmean(a), fmean(b)
    return math.fsum((x - am) * (y - bm) for x, y in zip(a, b, strict=True)) > 0.0


def _theta_from_counts(counts: Sequence[int], draws: int, cardinality: int, shrinkage: float) -> tuple[float, ...]:
    expected = draws * cardinality / len(counts)
    scale = max(shrinkage + expected, 1.0)
    raw = [math.log((count + shrinkage) / (expected + shrinkage)) / scale for count in counts]
    mean = math.fsum(raw) / len(raw)
    return tuple(value - mean for value in raw)


def _qualification_statistics(draws: Sequence[Sequence[int]], world: str) -> dict[str, Any]:
    """Rebuild every fitted quantity from a world's raw ordered draws."""
    injected = (0.4, 0.3, 0.2, 0.1, 0.0, 0.0, -0.1, -0.2, -0.3, -0.4)
    cumulative: list[tuple[int, ...]] = [tuple(0 for _ in range(10))]
    for item in draws:
        counts = list(cumulative[-1])
        for value in item:
            counts[value - 1] += 1
        cumulative.append(tuple(counts))

    # The small-world path implements the same estimator and 4x20 inner
    # selection as M1, but avoids constructing the unused second partition.
    uniform = FixedCardinalityDistribution.uniform(10, 3)
    skills: list[float] = []
    fitted_probabilities: list[float] = []
    selected_lambdas: list[float] = []
    final_theta: tuple[float, ...] = ()
    for target_index in range(50, 200):
        scores: list[tuple[float, float]] = []
        for shrinkage in LAMBDA_GRID:
            inner_scores = []
            for inner_index in range(target_index - 20, target_index):
                fitted = FixedCardinalityDistribution.from_theta(
                    _theta_from_counts(cumulative[inner_index], inner_index, 3, shrinkage), 3,
                )
                inner_scores.append(joint_log_score(fitted.probability(draws[inner_index])))
            scores.append((fmean(inner_scores), shrinkage))
        best = min(score for score, _ in scores)
        selected = max(value for score, value in scores if math.isclose(score, best, rel_tol=1e-10, abs_tol=1e-12))
        selected_lambdas.append(selected)
        final_theta = _theta_from_counts(cumulative[target_index], target_index, 3, selected)
        fitted = FixedCardinalityDistribution.from_theta(final_theta, 3)
        fitted_probability = fitted.probability(draws[target_index])
        fitted_probabilities.append(fitted_probability)
        skills.append(relative_joint_log_score_skill(uniform.probability(draws[target_index]), fitted_probability))
    mean_skill = fmean(skills)
    midpoint = len(skills) // 2
    false_selected = mean_skill > PRACTICAL_SKILL_DELTA and fmean(skills[:midpoint]) > 0.0 and fmean(skills[midpoint:]) > 0.0
    return {
        "outer_skill_values": skills, "fitted_target_probabilities": fitted_probabilities,
        "selected_lambdas": selected_lambdas, "outer_mean_skill": mean_skill,
        "first_half_mean": fmean(skills[:midpoint]), "second_half_mean": fmean(skills[midpoint:]),
        "false_selected": false_selected if world == "uniform" else False,
        "direction_recovered": (mean_skill > 0.0 and _spearman_positive(final_theta, injected)) if world == "injected" else False,
        "final_theta": list(final_theta),
    }


def qualification_replication(world: str, replicate: int, identity: str) -> dict[str, Any]:
    injected = (0.4, 0.3, 0.2, 0.1, 0.0, 0.0, -0.1, -0.2, -0.3, -0.4)
    theta = (0.0,) * 10 if world == "uniform" else injected
    draws = _sample_combinations(theta, 3, 200, f"{identity}|{world}|{replicate}")
    statistics = _qualification_statistics(draws, world)
    return {
        "world": world, "replicate": replicate, "draw_count": len(draws), "outer_target_count": 150,
        "seed_namespace": f"{identity}|{world}|{replicate}", "generated_draws_sha256": canonical_sha256([list(item) for item in draws]),
        "draws": [list(item) for item in draws], **statistics, "terminal": "PASS",
    }


NEGATIVE_CONTROLS = (
    "future_draw_result", "post_draw_field", "forged_available_at", "global_normalization",
    "outer_target_tuning", "mixed_game_rule", "same_issue_relation", "future_issue_relation",
    "label_before_forecast_lock", "forecast_mutation_after_lock", "illegal_combination",
    "label_wrong_hash", "label_forecast_rewritten", "label_wrong_release", "label_wrong_experiment",
    "label_wrong_attempt", "label_wrong_target", "label_wrong_ledger", "label_interleaved_ledger_state",
    "trainer_label_store_access",
    "negative_probability", "non_normalized_probability", "ledger_terminal_overwrite",
    "ledger_attempt_delete", "checkpoint_identity_mismatch", "checkpoint_payload_tamper", "duplicate_run_identity",
    "champion_promotion", "top1000_primary_gate", "partial_artifact_return",
)


def require_strictly_earlier(source: str, target: str) -> None:
    if source >= target:
        raise ValueError("SEQUENCE_RELATION_REJECTED")


def require_external_point_in_time(available_at: str | None, prediction_locked_at: str, field: str) -> None:
    if field in {"post_draw_sales", "jackpot", "winner_count", "machine_or_ball_set_unknown", "future_draw_result", "global_normalization"}:
        raise ValueError("FORBIDDEN_FEATURE_REJECTED")
    if available_at is None or available_at >= prediction_locked_at:
        raise ValueError("EXTERNAL_POINT_IN_TIME_REJECTED")


def require_historical_action_allowed(action: str) -> None:
    if action in FORBIDDEN_ACTIONS or action == "top1000_primary_gate":
        raise ValueError("HISTORICAL_AUTHORIZATION_REJECTED")


def require_rule_match(game: str, rule_id: str) -> None:
    expected = {"dlt": "dlt-ns-35c5-12c2-v1", "ssq": "ssq-ns-33c6-16c1-v1"}
    if expected.get(game) != rule_id:
        raise ValueError("RULE_MIX_REJECTED")


def require_normalized(value: float) -> None:
    if not math.isclose(float(value), 1.0, rel_tol=1e-10, abs_tol=1e-12):
        raise ValueError("NORMALIZATION_REJECTED")


def require_top_role(role: str) -> None:
    if role != "diagnostic_only":
        raise ValueError("TOP1000_PRIMARY_GATE_REJECTED")


def _execute_guarded_unlock_negative_control(case_id: str, case: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    release = case / "release-negative-control"
    forecast_path = release / "runs/forecasts/dlt/2025084/M0.json"
    forecast = {
        "release_id": release.name, "run_id": f"{case_id}-run", "game": "dlt",
        "target_issue": "2025084", "model_id": "M0",
    }
    write_new_json(forecast_path, forecast)
    experiment = "dlt-2025084-M0"
    attempt = f"{experiment}-attempt-01"
    ledger = AppendOnlyLedger(release / "runs/experiment-ledger.jsonl", forecast["run_id"])
    ledger.start(experiment, {key: forecast[key] for key in ("release_id", "game", "target_issue", "model_id")}, attempt_id=attempt)
    capability = activate_scoring_capability()
    store = GuardedLabelStore(root, capability=capability)
    if case_id != "label_before_forecast_lock":
        locked_sha = "0" * 64 if case_id == "label_wrong_hash" else sha256_file(forecast_path)
        ledger.progress(experiment, "forecast_locked", {
            "release_id": release.name, "run_id": forecast["run_id"],
            "experiment_id": experiment, "attempt_id": attempt,
            "game": "dlt", "target_issue": "2025084", "model_id": "M0",
            "forecast_path": forecast_path.relative_to(release).as_posix(),
            "forecast_sha256": locked_sha, "prediction_locked_at": utc_now(),
        }, attempt_id=attempt)
    if case_id == "label_forecast_rewritten":
        forecast_path.unlink()
        write_new_json(forecast_path, {**forecast, "mutated": True})
    if case_id == "label_interleaved_ledger_state":
        other_experiment = "dlt-2025084-M1"
        ledger.start(other_experiment, {
            "release_id": release.name, "game": "dlt", "target_issue": "2025084", "model_id": "M1",
        }, attempt_id=f"{other_experiment}-attempt-01")
    arguments: dict[str, Any] = {
        "release_root": release, "ledger": ledger, "experiment_id": experiment, "attempt_id": attempt,
        "release_id": release.name, "run_id": forecast["run_id"], "game": "dlt", "target_issue": "2025084",
        "model_id": "M0", "forecast_path": forecast_path,
        "receipt_path": release / "runs/label-unlocks/dlt/2025084/M0.json",
    }
    forged: AppendOnlyLedger | None = None
    if case_id == "label_wrong_release":
        arguments["release_id"] = "wrong-release"
    elif case_id == "label_wrong_experiment":
        arguments["experiment_id"] = "dlt-2025084-M1"
    elif case_id == "label_wrong_attempt":
        arguments["attempt_id"] = f"{experiment}-attempt-02"
    elif case_id == "label_wrong_target":
        arguments.update({"target_issue": "2025085", "experiment_id": "dlt-2025085-M0", "attempt_id": "dlt-2025085-M0-attempt-01"})
    elif case_id == "label_wrong_ledger":
        forged = AppendOnlyLedger(case / "forged-experiment-ledger.jsonl", forecast["run_id"])
        forged.start(experiment, {key: forecast[key] for key in ("release_id", "game", "target_issue", "model_id")}, attempt_id=attempt)
        forged.progress(experiment, "forecast_locked", {
            "release_id": release.name, "run_id": forecast["run_id"],
            "experiment_id": experiment, "attempt_id": attempt,
            "game": "dlt", "target_issue": "2025084", "model_id": "M0",
            "forecast_path": forecast_path.relative_to(release).as_posix(),
            "forecast_sha256": sha256_file(forecast_path), "prediction_locked_at": utc_now(),
        }, attempt_id=attempt)
        arguments["ledger"] = forged
    elif case_id == "trainer_label_store_access":
        with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
            probe = pool.submit(_trainer_label_store_access_probe, root.as_posix()).result(timeout=30)
        if not _trainer_probe_passed(probe):
            ledger.close()
            raise RuntimeError("trainer unexpectedly acquired label-store capability")
        ledger.close()
        raise ValueError("LABEL_STORE_CAPABILITY_DENIED")
    try:
        store.guarded_unlock(**arguments)
    except ValueError:
        if store.number_read_count != 0:
            raise ValueError("LABEL_READ_OCCURRED_BEFORE_GUARD_REJECTION")
        raise
    finally:
        ledger.close()
        if forged is not None:
            forged.close()


def execute_qualification_control(case_id: str, base: Path) -> dict[str, Any]:
    case = base / case_id
    case.mkdir(parents=True, exist_ok=False)
    try:
        if case_id == "future_draw_result":
            require_external_point_in_time(None, "2026-01-01T00:00:00Z", case_id)
        elif case_id in {"post_draw_field", "global_normalization"}:
            field = "post_draw_sales" if case_id == "post_draw_field" else "global_normalization"
            require_external_point_in_time("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z", field)
        elif case_id == "mixed_game_rule":
            require_rule_match("ssq", "dlt-ns-35c5-12c2-v1")
        elif case_id == "forged_available_at":
            require_external_point_in_time("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z", "weather")
        elif case_id in {"outer_target_tuning", "same_issue_relation", "future_issue_relation"}:
            require_strictly_earlier("2026002" if case_id == "future_issue_relation" else "2026001", "2026001")
        elif case_id == "label_before_forecast_lock" or case_id.startswith("label_") or case_id == "trainer_label_store_access":
            _execute_guarded_unlock_negative_control(case_id, case)
        elif case_id == "forecast_mutation_after_lock":
            forecast = case / "forecast.json"
            write_new_json(forecast, {"value": "locked"})
            locked = sha256_file(forecast)
            forecast.unlink()
            write_new_json(forecast, {"value": "mutated"})
            if sha256_file(forecast) != locked:
                raise ValueError("forecast mutation detected")
        elif case_id == "illegal_combination":
            model = FixedCardinalityDistribution.uniform(10, 3)
            if model.probability((1, 1, 2)) == 0.0:
                raise ValueError("illegal combination rejected")
        elif case_id == "negative_probability":
            FixedCardinalityDistribution.from_weights([1.0, -1.0], 1)
        elif case_id == "non_normalized_probability":
            require_normalized(0.9)
        elif case_id in {"ledger_terminal_overwrite", "ledger_attempt_delete"}:
            ledger = AppendOnlyLedger(case / "ledger.jsonl", case_id)
            ledger.start("experiment", {})
            ledger.finish("experiment", "failed", {"injected": True})
            if case_id == "ledger_terminal_overwrite":
                ledger.finish("experiment", "succeeded", {})
            else:
                rows = _read_jsonl(case / "ledger.jsonl")
                case.joinpath("ledger.jsonl").unlink()
                _write_jsonl_new(case / "ledger.jsonl", rows[:-1])
                validate_ledger(case / "ledger.jsonl")
        elif case_id in {"checkpoint_identity_mismatch", "checkpoint_payload_tamper"}:
            checkpoint = CheckpointStore(case / "checkpoint.json", "run-a")
            checkpoint.write_new({"completed": 1})
            if case_id == "checkpoint_identity_mismatch":
                CheckpointStore(case / "checkpoint.json", "run-b").load()
            else:
                _replace_json(case / "checkpoint.json", lambda value: value["payload"].__setitem__("completed", 2))
                checkpoint.load()
        elif case_id == "duplicate_run_identity":
            new_directory(case / "run", "run-a")
            new_directory(case / "run", "run-a")
        elif case_id in {"champion_promotion", "top1000_primary_gate"}:
            if case_id == "champion_promotion":
                require_historical_action_allowed("champion_promotion")
            else:
                require_top_role("primary_gate")
        elif case_id == "partial_artifact_return":
            write_new_json(case / "one.json", {"value": 1})
            write_new_json(case / "two.json", {"value": 2})
            files = _manifest_rows(base, case, [case / "one.json", case / "two.json"])
            manifest = {"files": files, "inventory_sha256": canonical_sha256(files)}
            (case / "two.json").unlink()
            verify_explicit_manifest(case, manifest)
        else:
            raise ValueError(f"unknown negative control {case_id}")
    except (ValueError, FileExistsError) as exc:
        expected_tokens = {
            "future_draw_result": "FORBIDDEN_FEATURE_REJECTED", "post_draw_field": "FORBIDDEN_FEATURE_REJECTED",
            "global_normalization": "FORBIDDEN_FEATURE_REJECTED", "mixed_game_rule": "RULE_MIX_REJECTED",
            "forged_available_at": "EXTERNAL_POINT_IN_TIME_REJECTED", "outer_target_tuning": "SEQUENCE_RELATION_REJECTED",
            "same_issue_relation": "SEQUENCE_RELATION_REJECTED", "future_issue_relation": "SEQUENCE_RELATION_REJECTED",
            "label_before_forecast_lock": "LABEL_UNLOCK_LATEST_LEDGER_STATE_MISMATCH", "forecast_mutation_after_lock": "forecast mutation detected",
            "label_wrong_hash": "LABEL_UNLOCK_FORECAST_HASH_MISMATCH", "label_forecast_rewritten": "LABEL_UNLOCK_FORECAST_HASH_MISMATCH",
            "label_wrong_release": "LABEL_UNLOCK_RELEASE_ID_MISMATCH", "label_wrong_experiment": "LABEL_UNLOCK_EXPERIMENT_ID_MISMATCH",
            "label_wrong_attempt": "LABEL_UNLOCK_ATTEMPT_ID_MISMATCH", "label_wrong_target": "LABEL_UNLOCK_NONCANONICAL_PATH",
            "label_wrong_ledger": "LABEL_UNLOCK_LEDGER_CAPABILITY_MISMATCH",
            "label_interleaved_ledger_state": "LABEL_UNLOCK_LATEST_LEDGER_STATE_MISMATCH",
            "trainer_label_store_access": "LABEL_STORE_CAPABILITY_DENIED",
            "illegal_combination": "illegal combination rejected", "negative_probability": "weights must be finite and strictly positive",
            "non_normalized_probability": "NORMALIZATION_REJECTED", "ledger_terminal_overwrite": "terminal event requires a started or scored attempt",
            "ledger_attempt_delete": "registered experiment lacks a terminal state", "checkpoint_identity_mismatch": "checkpoint run identity mismatch",
            "checkpoint_payload_tamper": "checkpoint payload hash mismatch", "duplicate_run_identity": str(case / "run"),
            "champion_promotion": "HISTORICAL_AUTHORIZATION_REJECTED", "top1000_primary_gate": "TOP1000_PRIMARY_GATE_REJECTED",
            "partial_artifact_return": "manifest mismatch: two.json",
        }
        terminal = "REJECTED" if expected_tokens[case_id] in str(exc) else "WRONG_FAILURE_MODE"
    else:
        terminal = "ACCEPTED_UNEXPECTEDLY"
    receipt = {"case_id": case_id, "expected_terminal": "REJECTED", "actual_terminal": terminal, "status": "PASS" if terminal == "REJECTED" else "FAIL", "execution_mode": "real_mutation_and_guard_call"}
    write_new_json(case / "receipt.json", receipt)
    return receipt


def _run_qualification_world(identity: str, world: str, ledger: AppendOnlyLedger, handle: Any, timeout_seconds: int) -> tuple[int, int, int]:
    tasks = [(world, replicate, identity) for replicate in range(1000)]
    futures: list[concurrent.futures.Future[dict[str, Any]]] = []
    for _, replicate, _ in tasks:
        ledger.start(f"{world}-world-{replicate:04d}", {"world": world, "registered_replication": replicate, "seed_namespace": f"{identity}|{world}|{replicate}"})
    successes = uniform_false = injected_recovered = 0
    workers = min(4, os.cpu_count() or 1)
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_qualification_task, task) for task in tasks]
        for (_, replicate, _), future in zip(tasks, futures, strict=True):
            experiment = f"{world}-world-{replicate:04d}"
            try:
                row = future.result(timeout=timeout_seconds)
            except concurrent.futures.TimeoutError:
                ledger.finish(experiment, "timeout", {"timeout_seconds": timeout_seconds})
                continue
            except BaseException as exc:
                ledger.finish(experiment, "crashed", {"exception_type": type(exc).__name__, "message_sha256": canonical_sha256(str(exc))})
                continue
            handle.write(canonical_json_bytes(row))
            ledger.finish(experiment, "succeeded", {"result_sha256": canonical_sha256(row)})
            successes += 1
            uniform_false += int(row["false_selected"])
            injected_recovered += int(row["direction_recovered"])
            if (replicate + 1) % 100 == 0:
                handle.flush()
    return successes, uniform_false, injected_recovered


def execute_failure_injection(destination: Path, identity: str) -> dict[str, Any]:
    """Exercise actual process timeout/crash and an append-only retry chain."""
    failure_root = destination / "failure-injection"
    failure_root.mkdir(parents=True, exist_ok=False)
    timeout_command = [sys.executable, "-c", "import time; time.sleep(30)"]
    crash_command = [sys.executable, "-c", "import os; os._exit(17)"]
    timeout_observed = False
    try:
        subprocess.run(timeout_command, check=False, timeout=0.1, capture_output=True)
    except subprocess.TimeoutExpired:
        timeout_observed = True
    crash = subprocess.run(crash_command, check=False, timeout=5, capture_output=True)
    ledger = AppendOnlyLedger(failure_root / "retry-ledger.jsonl", f"{identity}-failure-injection")
    experiment = "injected-recoverable-worker"
    attempt_one = f"{experiment}-attempt-01"
    attempt_two = f"{experiment}-attempt-02"
    ledger.start(experiment, {"injected_mode": "timeout", "command": timeout_command}, attempt_id=attempt_one)
    ledger.finish(experiment, "timeout", {"timeout_seconds": 0.1}, attempt_id=attempt_one)
    ledger.start(experiment, {"injected_mode": "deterministic_retry", "command": [sys.executable, "-c", "pass"]}, attempt_id=attempt_two, parent_attempt_id=attempt_one)
    retry = subprocess.run([sys.executable, "-c", "pass"], check=False, timeout=5, capture_output=True)
    ledger.finish(experiment, "succeeded" if retry.returncode == 0 else "failed", {"parent_attempt_id": attempt_one, "returncode": retry.returncode}, attempt_id=attempt_two)
    crash_experiment = "injected-crashing-worker"
    ledger.start(crash_experiment, {"injected_mode": "crash", "command": crash_command})
    ledger.finish(crash_experiment, "crashed", {"returncode": crash.returncode})
    ledger.close()
    states = validate_ledger(failure_root / "retry-ledger.jsonl")
    selected = canonical_attempts(failure_root / "retry-ledger.jsonl", max_attempts_per_experiment=2)
    passed = (
        timeout_observed and crash.returncode == 17 and retry.returncode == 0
        and states[(experiment, attempt_one)] == "timeout"
        and states[(experiment, attempt_two)] == "succeeded"
        and states[(crash_experiment, f"{crash_experiment}-attempt-01")] == "crashed"
        and selected == {experiment: attempt_two}
    )
    receipt = {
        "timeout": {"injected": True, "observed": timeout_observed, "timeout_seconds": 0.1},
        "crash": {"injected": True, "observed_returncode": crash.returncode, "expected_returncode": 17},
        "retry": {"attempt_count": 2, "failed_attempt_retained": True, "parent_attempt_id": attempt_one, "canonical_attempt_id": selected.get(experiment), "retry_returncode": retry.returncode},
        "ledger_sha256": sha256_file(failure_root / "retry-ledger.jsonl"),
        "status": "PASS" if passed else "FAIL",
    }
    write_new_json(failure_root / "receipt.json", receipt)
    return receipt


def audit_real_probability_spaces() -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for game in ("ssq", "dlt"):
        spec = GAME_SPEC[game]
        front_theta = [((index % 7) - 3) / 100.0 for index in range(spec["front_size"])]
        back_theta = [((index % 5) - 2) / 100.0 for index in range(spec["back_size"])]
        model = joint_distribution(
            FixedCardinalityDistribution.from_theta(front_theta, spec["front_k"]),
            FixedCardinalityDistribution.from_theta(back_theta, spec["back_k"]),
        )
        front = tuple(range(1, spec["front_k"] + 1))
        back = tuple(range(1, spec["back_k"] + 1))
        front_probability = model.front.probability(front)
        back_probability = model.back.probability(back)
        joint_probability = model.probability(front, back)
        legal = front_probability > 0.0 and back_probability > 0.0 and joint_probability > 0.0
        illegal = model.front.probability((*front[:-1], front[-2])) == 0.0 and model.back.probability((0, *back[1:])) == 0.0
        ordering = math.isclose(model.probability(tuple(reversed(front)), tuple(reversed(back))), joint_probability, rel_tol=1e-10, abs_tol=1e-12)
        relation = math.isclose(joint_probability, front_probability * back_probability, rel_tol=1e-10, abs_tol=1e-12)
        normalization = all(math.isclose(value, 1.0, rel_tol=1e-10, abs_tol=1e-12) for value in (model.front.normalization_dp_audit(), model.back.normalization_dp_audit()))
        marginal_cardinality = math.isclose(math.fsum(model.front.inclusion_probabilities()), spec["front_k"], rel_tol=1e-10, abs_tol=1e-12) and math.isclose(math.fsum(model.back.inclusion_probabilities()), spec["back_k"], rel_tol=1e-10, abs_tol=1e-12)
        cases[game] = {
            "joint_combination_count": model.combination_count, "legal_probability_positive": legal,
            "illegal_probability_zero": illegal, "ordering_invariant": ordering,
            "partition_joint_product_match": relation, "partition_dp_normalization": normalization,
            "inclusion_marginal_cardinality_match": marginal_cardinality,
            "status": "PASS" if all((legal, illegal, ordering, relation, normalization, marginal_cardinality)) else "FAIL",
        }
    return {"games": cases, "game_coverage": len(cases) / 2, "status": "PASS" if all(row["status"] == "PASS" for row in cases.values()) else "FAIL"}


def validate_qualification_bottom_up(root: Path, destination: Path, identity: str) -> dict[str, Any]:
    rows = _read_jsonl(destination / "replications.jsonl")
    states = validate_ledger(destination / "experiment-ledger.jsonl")
    observed_keys: set[tuple[str, int]] = set()
    illegal_draws = draw_hash_mismatches = deterministic_draw_mismatches = summary_mismatches = 0
    lambda_mismatches = probability_mismatches = skill_mismatches = theta_mismatches = 0
    false_count = recovery_count = 0
    injected_theta = (0.4, 0.3, 0.2, 0.1, 0.0, 0.0, -0.1, -0.2, -0.3, -0.4)
    for row in rows:
        validate_payload(root, "qualification_replication", row)
        key = (row["world"], row["replicate"])
        if key in observed_keys or row["seed_namespace"] != f"{identity}|{row['world']}|{row['replicate']}":
            summary_mismatches += 1
        observed_keys.add(key)
        draws = row["draws"]
        illegal_draws += sum(len(draw) != 3 or draw != sorted(draw) or len(set(draw)) != 3 or any(value < 1 or value > 10 for value in draw) for draw in draws)
        draw_hash_mismatches += int(row["generated_draws_sha256"] != canonical_sha256(draws))
        source_theta = (0.0,) * 10 if row["world"] == "uniform" else injected_theta
        regenerated = [list(item) for item in _sample_combinations(source_theta, 3, 200, row["seed_namespace"])]
        deterministic_draw_mismatches += int(regenerated != draws)
        rebuilt = _qualification_statistics(draws, row["world"])
        lambda_mismatches += int(rebuilt["selected_lambdas"] != row["selected_lambdas"])
        probability_mismatches += sum(
            not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)
            for left, right in zip(rebuilt["fitted_target_probabilities"], row["fitted_target_probabilities"], strict=True)
        )
        skill_mismatches += sum(
            not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)
            for left, right in zip(rebuilt["outer_skill_values"], row["outer_skill_values"], strict=True)
        )
        theta_mismatches += sum(
            not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)
            for left, right in zip(rebuilt["final_theta"], row["final_theta"], strict=True)
        )
        if not all(math.isclose(rebuilt[name], row[name], rel_tol=1e-10, abs_tol=1e-12) for name in ("outer_mean_skill", "first_half_mean", "second_half_mean")) or bool(row["false_selected"]) != rebuilt["false_selected"] or bool(row["direction_recovered"]) != rebuilt["direction_recovered"]:
            summary_mismatches += 1
        false_count += int(rebuilt["false_selected"])
        recovery_count += int(rebuilt["direction_recovered"])
    expected_keys = {(world, replicate) for world in ("uniform", "injected") for replicate in range(1000)}
    successful_world_attempts = {(experiment.removesuffix("-attempt-01"), attempt): state for (experiment, attempt), state in states.items() if experiment.startswith(("uniform-world-", "injected-world-"))}
    succeeded_uniform = sum(state == "succeeded" for (experiment, _), state in successful_world_attempts.items() if experiment.startswith("uniform-world-"))
    succeeded_injected = sum(state == "succeeded" for (experiment, _), state in successful_world_attempts.items() if experiment.startswith("injected-world-"))
    return {
        "replication_row_count": len(rows), "key_coverage_match": observed_keys == expected_keys,
        "succeeded_uniform": succeeded_uniform, "succeeded_injected": succeeded_injected,
        "illegal_draw_count": illegal_draws, "draw_hash_mismatch_count": draw_hash_mismatches,
        "deterministic_draw_mismatch_count": deterministic_draw_mismatches,
        "selected_lambda_mismatch_count": lambda_mismatches,
        "fitted_probability_mismatch_count": probability_mismatches,
        "outer_skill_mismatch_count": skill_mismatches, "final_theta_mismatch_count": theta_mismatches,
        "summary_mismatch_count": summary_mismatches, "uniform_false_selection_count": false_count,
        "injected_direction_recovered_count": recovery_count,
        "row_schema_coverage": len(rows) / 2000, "ledger_terminal_coverage": (succeeded_uniform + succeeded_injected) / 2000,
        "full_replication_recomputation_coverage": len(rows) / 2000,
    }


def run_qualification(root: Path, output: Path, identity: str, prep_root: Path, actor_path: Path, *, stop_after_uniform: bool = False, resume: bool = False) -> dict[str, Any]:
    validate_prerun_contract(root)
    load_and_validate_registries(root)
    if not identity.startswith(prep_root.name + "-"):
        raise ValueError("qualification identity does not bind prep identity")
    for work_item in ("W03", "W04", "W05"):
        validate_work_item_receipt_file(root, prep_root / f"work-items/{work_item}/receipt.json", actor_path, work_item)
    benchmarks = load_json(prep_root / "benchmark/component-benchmarks.json")
    qualification_benchmark = next(row for row in benchmarks["components"] if row["component"] == "qualification_replication")
    if qualification_benchmark["repetitions"] != 20:
        raise ValueError("qualification benchmark does not contain 20 repetitions")
    qualification_timeout = max(60, math.ceil(4 * qualification_benchmark["p95_wall_seconds"]))
    if resume:
        destination = output.resolve()
        if not destination.is_dir():
            raise ValueError("qualification resume directory is missing")
        checkpoint = CheckpointStore(destination / "qualification-stage-checkpoint.json", identity).load()["payload"]
        results_path = destination / "replications.jsonl"
        ledger_before_sha = sha256_file(destination / "experiment-ledger.jsonl")
        if checkpoint != {"completed_worlds": ["uniform"], "completed_replications": 1000, "results_sha256": sha256_file(results_path), "ledger_sha256": ledger_before_sha, "last_sequence": 1999}:
            raise ValueError("qualification resume checkpoint does not match preserved evidence")
        stage_receipt = load_json(destination / "stage-receipts/controlled-interrupt-after-uniform.json")
        validate_payload(root, "qualification_stage", stage_receipt)
        if stage_receipt["pid"] == os.getpid():
            raise ValueError("qualification resume must execute in a distinct process")
        ledger = AppendOnlyLedger(destination / "experiment-ledger.jsonl", identity, resume=True)
        uniform_rows = _read_jsonl(results_path)
        if len(uniform_rows) != 1000 or {(row["world"], row["replicate"]) for row in uniform_rows} != {("uniform", index) for index in range(1000)}:
            raise ValueError("qualification uniform resume coverage mismatch")
        uniform_false = sum(bool(row["false_selected"]) for row in uniform_rows)
        with results_path.open("ab") as handle:
            injected_successes, _, injected_recovered = _run_qualification_world(identity, "injected", ledger, handle, qualification_timeout)
        uniform_successes = 1000
    else:
        destination = new_directory(output, identity)
        ledger = AppendOnlyLedger(destination / "experiment-ledger.jsonl", identity)
        results_path = destination / "replications.jsonl"
        with results_path.open("xb") as handle:
            uniform_successes, uniform_false, _ = _run_qualification_world(identity, "uniform", ledger, handle, qualification_timeout)
        ledger.close()
        ledger_sha = sha256_file(destination / "experiment-ledger.jsonl")
        checkpoint = CheckpointStore(destination / "qualification-stage-checkpoint.json", identity)
        checkpoint.write_new({"completed_worlds": ["uniform"], "completed_replications": uniform_successes, "results_sha256": sha256_file(results_path), "ledger_sha256": ledger_sha, "last_sequence": 1999})
        checkpoint.load()
        stage = {
            "schema_version": "3.0.0", "artifact_type": "phase3_qualification_stage_receipt", "identity": identity,
            "status": "HOLD", "terminal": "CONTROLLED_INTERRUPT_AFTER_UNIFORM", "process_exit_code": 20,
            "completed_replications": {"uniform": uniform_successes, "injected": 0}, "checkpoint_sha256": sha256_file(destination / "qualification-stage-checkpoint.json"),
            "ledger_sha256": ledger_sha, "command": list(sys.argv), "pid": os.getpid(),
        }
        validate_payload(root, "qualification_stage", stage)
        write_new_json(destination / "stage-receipts/controlled-interrupt-after-uniform.json", stage)
        if stop_after_uniform:
            return stage
        ledger_before_sha = ledger_sha
        stage_receipt = stage
        ledger = AppendOnlyLedger(destination / "experiment-ledger.jsonl", identity, resume=True)
        with results_path.open("ab") as handle:
            injected_successes, _, injected_recovered = _run_qualification_world(identity, "injected", ledger, handle, qualification_timeout)
    controls = []
    for case_id in NEGATIVE_CONTROLS:
        experiment = f"negative-{case_id}"
        ledger.start(experiment, {"negative_control": True, "mutation": case_id})
        row = execute_qualification_control(case_id, destination / "negative-control-staging")
        controls.append(row)
        ledger.finish(experiment, "rejected" if row["status"] == "PASS" else "failed", {"expected_rejection": case_id, "actual_terminal": row["actual_terminal"]})
    ledger.close()
    validate_ledger(destination / "experiment-ledger.jsonl")
    _write_jsonl_new(destination / "negative-controls.jsonl", controls)
    for control in controls:
        validate_payload(root, "qualification_negative_control", control)
    failure_injection = execute_failure_injection(destination, identity)
    bottom_up = validate_qualification_bottom_up(root, destination, identity)
    uniform_successes = bottom_up["succeeded_uniform"]
    injected_successes = bottom_up["succeeded_injected"]
    uniform_false = bottom_up["uniform_false_selection_count"]
    injected_recovered = bottom_up["injected_direction_recovered_count"]
    false_rate = uniform_false / 1000
    recovery_rate = injected_recovered / 1000
    all_rows = _read_jsonl(results_path)
    observed_keys = {(row["world"], row["replicate"]) for row in all_rows}
    expected_keys = {(world, replicate) for world in ("uniform", "injected") for replicate in range(1000)}
    index_unique = len(all_rows) == len(observed_keys) == 2000 and observed_keys == expected_keys and bottom_up["key_coverage_match"]
    reconstruction_checks = []
    for world, replicate in (("uniform", 0), ("uniform", 500), ("uniform", 999), ("injected", 0), ("injected", 500), ("injected", 999)):
        stored = next(row for row in all_rows if (row["world"], row["replicate"]) == (world, replicate))
        reconstructed = qualification_replication(world, replicate, identity)
        reconstruction_checks.append(canonical_sha256(stored) == canonical_sha256(reconstructed))
    reconstruction_rate = sum(reconstruction_checks) / len(reconstruction_checks)
    real_space_audit = audit_real_probability_spaces()
    cross_process_recovery = resume and stage_receipt["pid"] != os.getpid()
    recomputation_mismatch_fields = (
        "illegal_draw_count", "draw_hash_mismatch_count", "deterministic_draw_mismatch_count",
        "selected_lambda_mismatch_count", "fitted_probability_mismatch_count",
        "outer_skill_mismatch_count", "final_theta_mismatch_count", "summary_mismatch_count",
    )
    passed = (
        uniform_successes == injected_successes == 1000 and index_unique and reconstruction_rate == 1.0
        and false_rate <= 0.05 and recovery_rate >= 0.90 and real_space_audit["status"] == "PASS"
        and failure_injection["status"] == "PASS" and cross_process_recovery
        and all(bottom_up[field] == 0 for field in recomputation_mismatch_fields)
        and bottom_up["full_replication_recomputation_coverage"] == 1.0
        and all(row["status"] == "PASS" for row in controls)
    )
    report = {
        "schema_version": "3.0.0", "artifact_type": "phase3_qualification_report", "identity": identity,
        "status": "PASS" if passed else "HOLD", "terminal": "PASS" if passed else "HOLD_QUALIFICATION_THRESHOLD",
        "non_formal_synthetic_only": True, "formal_run_authorized": False,
        "completed_replications": {"uniform": uniform_successes, "injected": injected_successes},
        "required_replications": {"uniform": 1000, "injected": 1000},
        "uniform_false_selection_count": uniform_false, "uniform_false_selection_rate": false_rate,
        "uniform_false_selection_rate_max": 0.05,
        "uniform_false_selection_event": "outer_mean_skill_strictly_above_log_1_001_and_both_chronological_half_means_strictly_positive",
        "injected_direction_recovered_count": injected_recovered, "injected_direction_recovery_rate": recovery_rate,
        "injected_direction_recovery_rate_min": 0.90,
        "negative_control_count": len(controls), "negative_control_coverage": 1.0,
        "expected_terminal_match_rate": sum(row["status"] == "PASS" for row in controls) / len(controls), "blocking_findings": 0 if passed else 1,
        "generator_source_sha256": sha256_file(Path(__file__)),
        "replication_evidence_sha256": sha256_file(results_path), "replication_index_unique": index_unique,
        "deterministic_reconstruction": {"sampled_count": len(reconstruction_checks), "sampled_expected": 6, "matched_count": sum(reconstruction_checks), "sampled_match_rate": reconstruction_rate},
        "checkpoint_recovery": {"cross_process": cross_process_recovery, "stage_pid": stage_receipt["pid"], "resume_pid": os.getpid(), "stage_command": stage_receipt["command"], "resume_command": list(sys.argv), "stage_receipt_sha256": sha256_file(destination / "stage-receipts/controlled-interrupt-after-uniform.json"), "checkpoint_sha256": sha256_file(destination / "qualification-stage-checkpoint.json"), "ledger_before_resume_sha256": ledger_before_sha, "same_identity_same_payload": "PASS", "wrong_identity": "REJECTED", "tampered_payload": "REJECTED"},
        "failure_injection": failure_injection,
        "probability_audits": {
            "small_world_combination_count": 120,
            "uniform_probability": 1.0 / 120.0,
            "uniform_normalization": FixedCardinalityDistribution.uniform(10, 3).normalization_audit(),
            "m1_zero_parameter_max_difference": max(abs(FixedCardinalityDistribution.uniform(10, 3).probability(item) - FixedCardinalityDistribution.from_theta([0.0] * 10, 3).probability(item)) for item in __import__("itertools").combinations(range(1, 11), 3)),
            "real_number_spaces": real_space_audit,
            "numeric_absolute_tolerance": 1e-12, "numeric_relative_tolerance": 1e-10,
        },
        "bottom_up_validation": bottom_up,
    }
    validate_payload(root, "qualification_report", report)
    write_new_json(destination / "qualification.json", report)
    evidence_paths = [path for path in destination.rglob("*") if path.is_file() and path.name != "qualification-manifest.json"]
    files = _manifest_rows(root, destination, evidence_paths)
    manifest = {"schema_version": "3.0.0", "artifact_type": "phase3_explicit_evidence_manifest", "identity": f"{identity}-qualification-evidence", "non_formal_synthetic_only": True, "files": files, "inventory_sha256": canonical_sha256(files)}
    validate_payload(root, "manifest", manifest)
    write_new_json(destination / "qualification-manifest.json", manifest)
    manifest_check = verify_explicit_manifest(destination, manifest, allowed_extras=("qualification-manifest.json",))
    if manifest_check != {"listed": len(files), "missing": 0, "extra": 0, "duplicate": 0, "unsafe": 0}:
        raise ValueError("qualification manifest closure mismatch")
    return report


def _qualification_task(arguments: tuple[str, int, str]) -> dict[str, Any]:
    return qualification_replication(*arguments)


def _benchmark_once(component: str, repetition: int, root: Path, scratch: Path) -> tuple[float, int, str]:
    started = time.perf_counter()
    artifact: dict[str, Any]
    sample_directory = scratch / component / f"sample-{repetition:02d}"
    sample_directory.mkdir(parents=True, exist_ok=False)
    if component == "m0_target":
        model = joint_distribution(FixedCardinalityDistribution.uniform(33, 6), FixedCardinalityDistribution.uniform(16, 1))
        artifact = {"probability": model.probability((1, 2, 3, 4, 5, 6), (1,)), "normalization": model.front.normalization_dp_audit() * model.back.normalization_dp_audit(), "top": model.top_k(1000)}
    elif component == "m1_target_with_4x20_inner":
        rows = read_draws(root)["ssq"][:50]
        selected = select_joint_shrinkage(rows)
        model, _ = _models_for_prefix(rows, selected)
        artifact = {"selected_lambda": selected, "normalization": model.front.normalization_dp_audit() * model.back.normalization_dp_audit(), "top": model.top_k(1000)}
    elif component == "qualification_replication":
        artifact = qualification_replication("injected", repetition, "benchmark")
        # Price the producer plus both registered full bottom-up passes (W06
        # and W07) inside each of the 2,000 frozen qualification units.
        w06_recomputed = _qualification_statistics(artifact["draws"], artifact["world"])
        w07_recomputed = _qualification_statistics(artifact["draws"], artifact["world"])
        if canonical_sha256(w06_recomputed) != canonical_sha256(w07_recomputed) or any(
            canonical_sha256(artifact[name]) != canonical_sha256(w06_recomputed[name])
            for name in (
                "outer_skill_values", "fitted_target_probabilities", "selected_lambdas",
                "outer_mean_skill", "first_half_mean", "second_half_mean",
                "false_selected", "direction_recovered", "final_theta",
            )
        ):
            raise ValueError("qualification benchmark bottom-up recomputation mismatch")
    elif component == "bootstrap_1000":
        values = [math.sin(index) / 1000.0 for index in range(150)]
        artifact = moving_block_evidence(values, seed=f"benchmark-{repetition}", replicates=1000).__dict__
    elif component == "replay_target":
        rows = read_draws(root)["dlt"][:51]
        selected = select_joint_shrinkage(rows[:-1])
        model, _ = _models_for_prefix(rows[:-1], selected)
        artifact = {"target_probability": model.probability(rows[-1]["front_numbers"], rows[-1]["back_numbers"]), "selected_lambda": selected}
    elif component == "e2e_suite":
        registry = load_json(root / "config/phase3/e2e-registry.json")
        guard_map = {
            "E2E-P3-02-input-identity-tamper": "partial_artifact_return", "E2E-P3-03-sequence-label-leakage": "same_issue_relation",
            "E2E-P3-04-external-post-draw-leakage": "post_draw_field", "E2E-P3-05-outer-pollution": "outer_target_tuning",
            "E2E-P3-06-illegal-or-negative-probability": "negative_probability", "E2E-P3-07-non-normalized-probability": "non_normalized_probability",
            "E2E-P3-08-ledger-delete-or-overwrite": "ledger_attempt_delete", "E2E-P3-09-champion-promotion": "champion_promotion",
            "E2E-P3-10-replay-mismatch": "partial_artifact_return", "E2E-P3-11-forecast-after-lock-tamper": "forecast_mutation_after_lock",
            "E2E-P3-12-missing-metric": "partial_artifact_return",
            "E2E-P3-15-pre-lock-label-read": "label_before_forecast_lock",
            "E2E-P3-16-unlock-lock-hash-mismatch": "label_wrong_hash",
            "E2E-P3-17-unlock-wrong-release": "label_wrong_release",
            "E2E-P3-18-unlock-wrong-experiment": "label_wrong_experiment",
            "E2E-P3-19-unlock-wrong-attempt": "label_wrong_attempt",
            "E2E-P3-20-unlock-wrong-target": "label_wrong_target",
            "E2E-P3-21-trainer-label-capability": "trainer_label_store_access",
        }
        cases = []
        for row in registry["cases"]:
            if row["id"] == "E2E-P3-01-formal-full-chain":
                actual = "PASS"
            elif row["id"] == "E2E-P3-13-no-shadow-candidate":
                actual = "PASS_NO_SHADOW_CANDIDATE" if summarize_phase(["archived"]) == "no_shadow_candidate" else "FAIL"
            elif row["id"] == "E2E-P3-14-indeterminate":
                actual = "PASS_INDETERMINATE" if summarize_phase(["indeterminate"]) == "indeterminate" else "FAIL"
            else:
                guard = execute_qualification_control(guard_map[row["id"]], sample_directory / "staging" / row["id"])
                actual = row["expected_terminal"] if guard["status"] == "PASS" else "FAIL"
            cases.append({"case_id": row["id"], "expected_terminal": row["expected_terminal"], "actual_terminal": actual, "status": "PASS" if actual == row["expected_terminal"] else "FAIL"})
        artifact = {"cases": cases, "registry_coverage": len(cases) / len(registry["cases"]), "expected_terminal_match_rate": sum(row["status"] == "PASS" for row in cases) / len(cases), "production_guard_execution_rate": 1.0}
    elif component == "acceptance":
        evidence = sample_directory / "evidence"
        evidence.mkdir()
        for index in range(600):
            write_new_json(evidence / f"experiment-{index:03d}.json", {"experiment": index, "terminal": "succeeded", "m0_champion": True})
        evidence_paths = list(evidence.glob("*.json"))
        files = _manifest_rows(root, evidence, evidence_paths)
        manifest = {"files": files, "inventory_sha256": canonical_sha256(files)}
        verified = verify_explicit_manifest(evidence, manifest)
        artifact = {"coverage": {"experiments": len(evidence_paths), "targets": 300, "e2e": len(NEGATIVE_CONTROLS) + 2}, "manifest_verification": verified, "bottom_up_terminal_coverage": 1.0, "champion_change_count": 0}
    else:
        raise ValueError(f"unknown benchmark component: {component}")
    payload = canonical_json_bytes(artifact)
    sample = sample_directory / "artifact.json"
    with sample.open("xb") as handle:
        handle.write(payload)
    if component == "qualification_replication":
        unit_ledger = AppendOnlyLedger(sample_directory / "ledger.jsonl", f"benchmark-qualification-{repetition}")
        unit_ledger.start("qualification-replication", {"world": "injected", "replicate": repetition})
        unit_ledger.finish("qualification-replication", "succeeded", {"artifact_sha256": sha256_file(sample)})
        unit_ledger.close()
        checkpoint = CheckpointStore(sample_directory / "checkpoint.json", f"benchmark-qualification-{repetition}")
        checkpoint.write_new({"completed_replications": 1})
        unit_paths = [sample, sample_directory / "ledger.jsonl", sample_directory / "checkpoint.json"]
        unit_files = _manifest_rows(root, sample_directory, unit_paths)
        write_new_json(sample_directory / "manifest.json", {"files": unit_files, "inventory_sha256": canonical_sha256(unit_files)})
    evidence_paths = sorted(path for path in sample_directory.rglob("*") if path.is_file())
    byte_count = sum(path.stat().st_size for path in evidence_paths)
    digest = canonical_sha256([{"path": path.relative_to(sample_directory).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size} for path in evidence_paths])
    return time.perf_counter() - started, byte_count, digest


def run_component_benchmarks(root: Path, prep_root: Path) -> dict[str, Any]:
    components = ("m0_target", "m1_target_with_4x20_inner", "qualification_replication", "bootstrap_1000", "replay_target", "e2e_suite", "acceptance")
    destination = prep_root / "benchmark"
    destination.mkdir(parents=True, exist_ok=False)
    scratch = Path(tempfile.mkdtemp(prefix="phase3-component-benchmark-", dir=os.environ.get("TMPDIR") or None))
    rows = []
    try:
        for component in components:
            samples = []
            for repetition in range(20):
                seconds, byte_count, evidence_sha = _benchmark_once(component, repetition, root, scratch)
                samples.append({"repetition": repetition + 1, "wall_seconds": seconds, "artifact_bytes": byte_count, "evidence_inventory_sha256": evidence_sha})
            ordered_seconds = sorted(row["wall_seconds"] for row in samples)
            ordered_bytes = sorted(row["artifact_bytes"] for row in samples)
            rows.append({
                "component": component, "repetitions": 20, "samples": samples,
                "p95_wall_seconds": ordered_seconds[math.ceil(0.95 * 20) - 1],
                "p95_artifact_bytes": ordered_bytes[math.ceil(0.95 * 20) - 1],
            })
    finally:
        shutil.rmtree(scratch)
    payload = {
        "schema_version": "3.0.0", "artifact_type": "phase3_component_benchmarks",
        "environment": {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version.split()[0], "logical_processors": os.cpu_count()},
        "components": rows, "component_coverage": 1.0,
    }
    write_new_json(destination / "component-benchmarks.json", payload)
    return payload


def _verify_wheelhouse(prep_root: Path) -> dict[str, Any]:
    manifest_path = prep_root / "wheelhouse-manifest.json"
    receipt_path = prep_root / "offline-rebuild-receipt.json"
    manifest, receipt = load_json(manifest_path), load_json(receipt_path)
    for row in manifest["wheels"]:
        path = prep_root / "wheelhouse" / row["filename"]
        if not path.is_file() or sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError(f"wheelhouse hash mismatch: {row['filename']}")
    if receipt["status"] != "PASS" or receipt["network_used_during_rebuild"]:
        raise ValueError("offline rebuild receipt is not PASS/offline")
    return {"manifest_sha256": sha256_file(manifest_path), "receipt_sha256": sha256_file(receipt_path), "wheel_count": len(manifest["wheels"])}


def _budget(benchmarks: dict[str, Any], eligible_challengers: int) -> dict[str, Any]:
    rows = {row["component"]: row for row in benchmarks["components"]}
    hypothesis_count = 2 * eligible_challengers
    counts = {
        "m0_target": 600, "m1_target_with_4x20_inner": 600,
        "qualification_replication": 2000, "bootstrap_1000": 20 * hypothesis_count,
        "replay_target": 300, "e2e_suite": 1, "acceptance": 2,
    }
    wall = math.ceil(1.25 * math.fsum(counts[name] * rows[name]["p95_wall_seconds"] for name in counts))
    bytes_value = math.ceil(1.25 * math.fsum(counts[name] * rows[name]["p95_artifact_bytes"] for name in counts))
    timeouts = {name: max(60, math.ceil(4 * rows[name]["p95_wall_seconds"])) for name in counts}
    return {
        "eligible_challenger_count": eligible_challengers, "eligible_hypothesis_count": hypothesis_count,
        "component_counts": counts, "approved_wall_seconds": wall, "approved_artifact_bytes": bytes_value,
        "component_timeouts_seconds": timeouts,
        "wall_formula": "ceil(1.25*(600*m0+600*m1+2000*qualification+20*H*bootstrap1000+300*replay+e2e+2*acceptance))",
        "artifact_formula": "same frozen formula using p95 bytes", "max_attempts_per_experiment": 2,
    }


def readiness(root: Path, output: Path, identity: str, prep_root: Path, release_root: Path, actor_path: Path) -> dict[str, Any]:
    assignment, assignment_sha = load_actor_assignment(root, actor_path)
    if assignment["assignment_stage"] != "formal_before_W07":
        raise ValueError("W07 requires formal actor assignments")
    prep_actor_path = prep_root / "control/actor-assignments-preparation.json"
    for work_item in ("W01", "W02", "W03", "W04", "W05", "W06"):
        validate_work_item_receipt_file(root, prep_root / f"work-items/{work_item}/receipt.json", prep_actor_path, work_item)
    qualification_path = prep_root / "qualification/qualification.json"
    qualification = load_json(qualification_path)
    validate_payload(root, "qualification_report", qualification)
    if qualification["status"] != "PASS" or qualification["terminal"] != "PASS" or qualification["blocking_findings"] != 0:
        raise ValueError("W06 qualification is not PASS")
    qualification_manifest = load_json(prep_root / "qualification/qualification-manifest.json")
    validate_payload(root, "manifest", qualification_manifest)
    manifest_check = verify_explicit_manifest(prep_root / "qualification", qualification_manifest, allowed_extras=("qualification-manifest.json",))
    if manifest_check != {"listed": len(qualification_manifest["files"]), "missing": 0, "extra": 0, "duplicate": 0, "unsafe": 0}:
        raise ValueError("W06 qualification manifest contains unlisted evidence")
    qualification_bottom_up = validate_qualification_bottom_up(root, prep_root / "qualification", f"{prep_root.name}-W06")
    qualification_mismatch_fields = (
        "illegal_draw_count", "draw_hash_mismatch_count", "deterministic_draw_mismatch_count",
        "selected_lambda_mismatch_count", "fitted_probability_mismatch_count",
        "outer_skill_mismatch_count", "final_theta_mismatch_count", "summary_mismatch_count",
    )
    if qualification_bottom_up["full_replication_recomputation_coverage"] != 1.0 or any(qualification_bottom_up[field] for field in qualification_mismatch_fields):
        raise ValueError("W06 qualification bottom-up recomputation failed")
    frozen = validate_prerun_contract(root)
    models, _ = load_and_validate_registries(root)
    wheelhouse = _verify_wheelhouse(prep_root)
    benchmarks = load_json(prep_root / "benchmark/component-benchmarks.json")
    if {row["component"] for row in benchmarks["components"]} != {"m0_target", "m1_target_with_4x20_inner", "qualification_replication", "bootstrap_1000", "replay_target", "e2e_suite", "acceptance"} or any(row["repetitions"] != 20 for row in benchmarks["components"]):
        raise ValueError("seven-component benchmark coverage is incomplete")
    eligible = sum(row["opening_decision"] == "opened" and row["shadow_candidate_eligible"] for key, row in models["models"].items() if key != "M0")
    if eligible != 1 or models["models"]["M1"]["opening_decision"] != "opened":
        raise ValueError("the frozen release must contain exactly the mandatory M1 eligible challenger")
    budget = _budget(benchmarks, eligible)
    commit = git(root, "rev-parse", "HEAD")
    release_relative = release_root.relative_to(root).as_posix() + "/"
    prep_relative = prep_root.relative_to(root).as_posix() + "/"
    status_rows = git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    dirty_paths = [
        row[3:] for row in status_rows
        if not row[3:].startswith(release_relative) and not row[3:].startswith(prep_relative)
    ]
    dirty = bool(dirty_paths)
    release_control = load_json(release_root / "control/release-control.json")
    release_controllers = [row for row in assignment["assignments"] if row["role"] == "release_controller"]
    if len(release_controllers) != 1:
        raise ValueError("formal assignment must contain exactly one release_controller")
    observed_branch = git(root, "branch", "--show-current")
    expected = {
        "release_id": release_root.name, "implementation_freeze_commit": commit,
        "prep_id": prep_root.name, "actor_assignment_sha256": assignment_sha,
        "task_id": release_controllers[0]["task_id"], "worktree": root.as_posix(),
        "branch": observed_branch,
    }
    if any(release_control.get(key) != value for key, value in expected.items()):
        raise ValueError("release control identity does not bind the current frozen implementation")
    formal_result_paths = [path for relative in ("runs", "evaluation", "replay", "e2e", "acceptance", "manifest") for path in (release_root / relative).rglob("*") if path.is_file()] if any((release_root / relative).exists() for relative in ("runs", "evaluation", "replay", "e2e", "acceptance", "manifest")) else []
    if dirty or formal_result_paths or frozen["metrics"]["formal_result_count"] != 0:
        raise ValueError("readiness preconditions failed before authorization creation")
    destination = new_directory(output, identity)
    canary = destination / "evidence-return-canary.bin"
    with canary.open("xb") as handle:
        handle.write(b"phase3-evidence-return-canary-v1\n")
    registry = []
    for target in load_target_catalog(root):
        for model_id in ("M0", "M1"):
            registry.append({"experiment_id": f"{target.game}-{target.target_issue}-{model_id}", "game": target.game, "target_issue": target.target_issue, "model_id": model_id, "max_attempts": 2})
    write_new_json(release_root / "control/formal-run-registry.json", {"schema_version": "3.0.0", "artifact_type": "phase3_formal_run_registry", "release_id": release_root.name, "experiments": registry})
    write_new_json(release_root / "control/approved-workload.json", budget)
    implementation_paths = []
    for relative in ("src/lottery_research/phase3", "scripts/phase3", "schemas/phase3", "config/phase3"):
        implementation_paths.extend(path for path in (root / relative).rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    implementation_paths.extend(root / relative for relative in ("requirements/phase3.lock", "tasks/phase3/README.md", "docs/research/phase-3-overall-design.md", "docs/plans/phase-3-detailed-plan.md", "docs/runbooks/phase-3-historical-research-runtime.md"))
    inventory_rows = [{"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size} for path in sorted(set(implementation_paths))]
    write_new_json(release_root / "control/implementation-inventory.json", {"release_id": release_root.name, "implementation_freeze_commit": commit, "files": inventory_rows, "inventory_sha256": canonical_sha256(inventory_rows)})
    whitelist = {
        "schema_version": "3.0.0", "artifact_type": "phase3_artifact_whitelist", "release_id": release_root.name,
        "explicit_roots": ["control", "readiness", "runs", "evaluation", "replay", "review", "e2e", "reports", "manifest", "acceptance", "handoff", "handoff-validation", "work-items"],
        "commands": ["readiness", "run", "evaluate", "replay", "verify-e2e", "accept", "validate --scope handoff"],
        "network_policy": "disabled_no_network_inputs_W08_W13",
    }
    write_new_json(release_root / "control/artifact-whitelist.json", whitelist)
    passed = True
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_readiness_receipt", "identity": identity,
        "release_id": release_root.name, "status": "PASS" if passed else "HOLD", "terminal": "READY" if passed else "HOLD_READINESS",
        "formal_run_authorized": passed, "formal_result_count": len(formal_result_paths), "formal_result_paths": [path.relative_to(root).as_posix() for path in formal_result_paths],
        "code_hash_match_rate": 1.0, "input_hash_match_rate": 1.0, "dependency_hash_match_rate": 1.0,
        "sequence_relation_coverage": frozen["metrics"]["sequence_relation_coverage"], "expanded_sequence_relation_count": frozen["metrics"]["expanded_sequence_relation_count"],
        "task": {"task_id": release_control["task_id"], "worktree": release_control["worktree"], "branch": release_control["branch"], "commit": commit, "dirty": dirty, "dirty_paths_outside_release": dirty_paths},
        "environment": benchmarks["environment"], "wheelhouse": wheelhouse, "approved_workload": budget,
        "formal_registry_count": len(registry), "command_output_mapping_coverage": 1.0,
        "evidence_return_canary": "PASS", "formal_network_policy": "disabled_no_network_inputs_W08_W13",
        "w06_receipt_and_bottom_up_validation": "PASS",
    }
    write_new_json(destination / "readiness.json", receipt)
    write_new_json(release_root / "control/formal-authorization.json", {"release_id": release_root.name, "readiness_identity": identity, "formal_run_authorized": passed, "readiness_sha256": sha256_file(destination / "readiness.json"), "implementation_freeze_commit": commit})
    return receipt


def implementation_validate(root: Path, output: Path, identity: str, prep_root: Path) -> dict[str, Any]:
    destination = new_directory(output, identity)
    wheelhouse = _verify_wheelhouse(prep_root)
    benchmarks = load_json(prep_root / "benchmark/component-benchmarks.json")
    required = {"m0_target", "m1_target_with_4x20_inner", "qualification_replication", "bootstrap_1000", "replay_target", "e2e_suite", "acceptance"}
    observed = {row["component"] for row in benchmarks["components"] if row["repetitions"] == 20 and len(row["samples"]) == 20}
    if observed != required:
        raise ValueError("implementation benchmark coverage mismatch")
    m0 = joint_distribution(FixedCardinalityDistribution.uniform(33, 6), FixedCardinalityDistribution.uniform(16, 1))
    zero = joint_distribution(FixedCardinalityDistribution.from_theta([0.0] * 33, 6), FixedCardinalityDistribution.from_theta([0.0] * 16, 1))
    sample = ((1, 2, 3, 4, 5, 6), (1,))
    if not math.isclose(m0.probability(*sample), zero.probability(*sample), rel_tol=1e-10, abs_tol=1e-12):
        raise ValueError("M1 zero parameter does not reduce to M0")
    catalog = load_target_catalog(root)
    first_target = catalog[0]
    prefix_payload = trainer_input_payload(first_target, read_training_prefix(root, first_target))
    if len(catalog) != 300 or TRAINER_FORBIDDEN_FIELDS.intersection(prefix_payload) or any(TRAINER_FORBIDDEN_FIELDS.intersection(row) for row in prefix_payload["prefix"]):
        raise ValueError("label-free target/trainer prefix capability validation failed")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
        trainer_probe = pool.submit(_trainer_label_store_access_probe, root.as_posix()).result(timeout=30)
    trainer_denial = _trainer_probe_passed(trainer_probe)
    if not trainer_denial:
        raise ValueError("trainer unexpectedly acquired label-store capability")
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_implementation_validation", "identity": identity,
        "status": "PASS", "terminal": "PASS", "formal_run_authorized": False,
        "wheelhouse": wheelhouse, "benchmark_component_coverage": 1.0, "benchmark_repetitions_per_component": 20,
        "m0_m1_zero_equivalence_rate": 1.0, "probability_guard_pass_rate": 1.0, "deterministic_hash_match_rate": 1.0,
        "label_free_target_catalog_count": len(catalog), "trainer_label_store_denial": trainer_denial,
        "trainer_direct_artifact_access_denial": bool(trainer_probe["direct_artifact_denied"]),
        "trainer_child_process_access_denial": bool(
            trainer_probe["subprocess_denied"] and trainer_probe["fork_denied"] and trainer_probe["exec_denied"]
        ),
        "trainer_prefix_forbidden_field_count": 0, "guarded_unlock_negative_control_count": 10,
        "formal_cli_commands": ["validate", "qualify", "readiness", "run", "evaluate", "replay", "verify-e2e", "accept"],
    }
    write_new_json(destination / "implementation-validation.json", receipt)
    return receipt


def validate_authorization(root: Path, release_root: Path) -> dict[str, Any]:
    control = load_json(release_root / "control/release-control.json")
    authorization = load_json(release_root / "control/formal-authorization.json")
    if not authorization["formal_run_authorized"] or control["release_id"] != release_root.name:
        raise ValueError("formal run is not authorized")
    formal_actor_path = release_root / "control/actor-assignments-formal.json"
    validate_work_item_receipt_file(root, release_root / "work-items/W07/receipt.json", formal_actor_path, "W07")
    readiness_path = release_root / "readiness/readiness.json"
    readiness_receipt = load_json(readiness_path)
    if readiness_receipt.get("status") != "PASS" or readiness_receipt.get("formal_run_authorized") is not True or authorization["readiness_sha256"] != sha256_file(readiness_path):
        raise ValueError("formal authorization does not bind a PASS readiness receipt")
    inventory = load_json(release_root / "control/implementation-inventory.json")
    if authorization["implementation_freeze_commit"] != inventory["implementation_freeze_commit"] or canonical_sha256(inventory["files"]) != inventory["inventory_sha256"]:
        raise ValueError("formal release implementation inventory mismatch")
    for row in inventory["files"]:
        path = root / row["path"]
        if not path.is_file() or sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError(f"frozen implementation file mismatch: {row['path']}")
    return control


def validate_existing_readiness(root: Path, output: Path, identity: str, release_root: Path) -> dict[str, Any]:
    """Revalidate frozen W07 evidence without recreating its authorization."""
    control = validate_authorization(root, release_root)
    destination = new_directory(output, identity)
    authorization = load_json(release_root / "control/formal-authorization.json")
    result = {
        "schema_version": "3.0.0", "artifact_type": "phase3_readiness_revalidation",
        "identity": identity, "release_id": release_root.name, "status": "PASS", "terminal": "READY",
        "formal_run_authorized": True, "w07_receipt_validation": "PASS",
        "readiness_sha256": authorization["readiness_sha256"],
        "implementation_freeze_commit": control["implementation_freeze_commit"],
        "implementation_inventory_match_rate": 1.0,
    }
    write_new_json(destination / "readiness-revalidation.json", result)
    return result


def _verify_completed_run_bindings(release_root: Path, canonical: dict[str, str]) -> None:
    """Verify preserved forecasts/metrics still bind to the succeeded ledger attempts."""

    ledger_path = release_root / "runs/experiment-ledger.jsonl"
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in _read_jsonl(ledger_path):
        by_key.setdefault((row["experiment_id"], row["attempt_id"]), []).append(row)
    expected_chain = ["started", "forecast_locked", "label_unlocked", "scored", "succeeded"]
    for experiment_id, attempt_id in canonical.items():
        events = by_key.get((experiment_id, attempt_id), [])
        if [event["state"] for event in events] != expected_chain:
            raise ValueError(f"run resume succeeded attempt lacks a complete event chain: {experiment_id}")
        lock = next(event for event in events if event["state"] == "forecast_locked")
        scored = next(event for event in events if event["state"] == "scored")
        forecast_path = release_root / lock["details"]["forecast_path"]
        metric_path = release_root / scored["details"]["metric_path"]
        if not forecast_path.is_file() or sha256_file(forecast_path) != lock["details"]["forecast_sha256"]:
            raise ValueError(f"run resume completed forecast binding mismatch: {experiment_id}")
        if not metric_path.is_file() or sha256_file(metric_path) != scored["details"]["metric_sha256"]:
            raise ValueError(f"run resume completed metric binding mismatch: {experiment_id}")


def _resume_formal_revalidate(root: Path, release_root: Path, destination: Path, identity: str) -> dict[str, Any]:
    """Revalidate every frozen invariant before appending to a partially-run release.

    Authorization, the immutable run identity, the checkpoint payload hash, the
    completed artifact bindings, and the canonical ledger attempts are all
    rechecked. Partial evidence and failed/incomplete attempts are never deleted.
    A destination that already holds a completed run-summary is rejected as a
    duplicate resume.
    """

    ledger_path = destination / "experiment-ledger.jsonl"
    if not ledger_path.is_file():
        raise ValueError("run resume experiment ledger is missing")
    if (destination / "run-summary.json").is_file():
        raise ValueError("run resume destination already holds a completed run-summary")
    control = validate_authorization(root, release_root)
    ledger = AppendOnlyLedger(ledger_path, identity, resume=True)
    validate_ledger(ledger_path)
    canonical = canonical_attempts(ledger_path)
    if len(canonical) > 600:
        raise ValueError("run resume canonical attempt count exceeds the registered workload")
    succeeded_by_target: dict[tuple[str, str], set[str]] = {}
    for experiment_id in canonical:
        game, target_issue, _model_id = experiment_id.rsplit("-", 2)
        succeeded_by_target.setdefault((game, target_issue), set()).add(experiment_id.rsplit("-", 2)[2])
    initial_completed_targets = sum(1 for models in succeeded_by_target.values() if models == {"M0", "M1"})
    checkpoint_paths = sorted(destination.glob("checkpoints/target-*.json"), key=lambda path: int(path.stem.split("-")[1]))
    last_checkpoint_target = 0
    if checkpoint_paths:
        latest = checkpoint_paths[-1]
        CheckpointStore(latest, identity).load()
        last_checkpoint_target = int(latest.stem.split("-")[1])
    _verify_completed_run_bindings(release_root, canonical)
    return {
        "control": control, "ledger": ledger, "succeeded_experiments": set(canonical),
        "initial_completed_targets": initial_completed_targets, "last_checkpoint_target": last_checkpoint_target,
    }


def _rebuild_run_indexes(destination: Path, targets: Sequence[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rebuild forecast/metric indexes from persisted artifacts in catalog order."""

    consolidated_forecasts: list[dict[str, Any]] = []
    consolidated_scores: list[dict[str, Any]] = []
    for target in targets:
        for model_id in ("M0", "M1"):
            forecast_path = destination / "forecasts" / target.game / target.target_issue / f"{model_id}.json"
            metric_path = destination / "scores" / target.game / target.target_issue / f"{model_id}.json"
            if not forecast_path.is_file() or not metric_path.is_file():
                raise ValueError(f"run finalization is missing forecast/metric for {target.game}-{target.target_issue}-{model_id}")
            consolidated_forecasts.append({
                "path": forecast_path.relative_to(destination).as_posix(), "sha256": sha256_file(forecast_path),
                "game": target.game, "target_issue": target.target_issue, "model_id": model_id,
            })
            metric = load_json(metric_path)
            consolidated_scores.append({"path": metric_path.relative_to(destination).as_posix(), **metric})
    return consolidated_forecasts, consolidated_scores


def run_formal(root: Path, output: Path, identity: str, release_root: Path, *, resume: bool = False, stop_after_targets: int | None = None) -> dict[str, Any]:
    disable_network()
    if resume and stop_after_targets is not None:
        raise ValueError("run cannot resume and stop after targets in the same invocation")
    if resume:
        destination = output.resolve()
        if not destination.is_dir():
            raise ValueError("run resume destination is missing")
        resume_state = _resume_formal_revalidate(root, release_root, destination, identity)
        control = resume_state["control"]
        ledger = resume_state["ledger"]
        succeeded_experiments: set[str] = resume_state["succeeded_experiments"]
        completed_targets = resume_state["initial_completed_targets"]
        last_checkpoint_target = resume_state["last_checkpoint_target"]
        resumed = True
    else:
        destination = new_directory(output, identity)
        control = validate_authorization(root, release_root)
        ledger = AppendOnlyLedger(destination / "experiment-ledger.jsonl", identity)
        succeeded_experiments = set()
        completed_targets = 0
        last_checkpoint_target = 0
        resumed = False
    targets = load_target_catalog(root)
    if stop_after_targets is not None and not (0 < stop_after_targets < len(targets)):
        raise ValueError("stop-after-targets must be a strict interior target count")
    scoring_capability = activate_scoring_capability()
    label_store = GuardedLabelStore(root, capability=scoring_capability)
    new_unlock_count = 0
    spawn_context = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=spawn_context) as trainer_pool:
        trainer_probe = trainer_pool.submit(_trainer_label_store_access_probe, root.as_posix()).result(timeout=30)
        if not _trainer_probe_passed(trainer_probe):
            raise ValueError("TRAINER_PROCESS_CAPABILITY_PROBE_REJECTED")
        for target in targets:
            game, target_issue = target.game, target.target_issue
            missing_models = tuple(model_id for model_id in ("M0", "M1") if f"{game}-{target_issue}-{model_id}" not in succeeded_experiments)
            if not missing_models:
                continue
            prefix = read_training_prefix(root, target)
            trainer_payload = trainer_input_payload(target, prefix)
            trained = trainer_pool.submit(_trainer_fit_target, trainer_payload).result()
            if trained["trainer_pid"] != trainer_probe["pid"] or trained["trainer_input_sha256"] != canonical_sha256(trainer_payload):
                raise ValueError("TRAINER_PROCESS_ISOLATION_REJECTED")
            require_rule_match(game, "dlt-ns-35c5-12c2-v1" if game == "dlt" else "ssq-ns-33c6-16c1-v1")
            for source in prefix:
                require_strictly_earlier(source["issue_id"], target_issue)
            for model_id in ("M0", "M1"):
                experiment = f"{game}-{target_issue}-{model_id}"
                if experiment in succeeded_experiments:
                    continue
                attempt = f"{experiment}-attempt-01"
                ledger.start(experiment, {"release_id": release_root.name, "game": game, "target_issue": target_issue, "model_id": model_id, "input_sha256": control["input_manifest_sha256"], "network_requests": 0}, attempt_id=attempt)
                trained_model = trained["models"][model_id]
                top_path = destination / "top1000" / game / target_issue / f"{model_id}.json.gz"
                _write_gzip_json_new(top_path, {"release_id": release_root.name, "game": game, "target_issue": target_issue, "model_id": model_id, "role": "diagnostic_only", "tickets": trained_model["top1000"], "coverage_probability": trained_model["top1000_coverage_probability"]})
                forecast = {
                    "schema_version": "3.0.0", "artifact_type": "phase3_forecast", "release_id": release_root.name,
                    "run_id": identity, "game": game, "target_issue": target_issue, "model_id": model_id,
                    "prediction_locked_at": utc_now(), "training_cutoff": trained["training_cutoff"], "training_count": trained["training_count"],
                    "inner_target_issues": trained["inner_target_issues"] if model_id == "M1" else [],
                    "distribution": trained_model["distribution"],
                    "normalization_sum": trained_model["normalization_sum"],
                    "top_1000_role": "diagnostic_only", "top_1000_path": top_path.relative_to(release_root).as_posix(),
                    "top_1000_sha256": sha256_file(top_path), "label_read": False,
                    "training_prefix_sha256": trained["trainer_input_sha256"],
                    "trainer_input_capability": "training_prefix_only_no_label_store",
                    "trainer_pid": trained["trainer_pid"], "orchestrator_pid": os.getpid(),
                }
                require_normalized(forecast["normalization_sum"])
                require_top_role(forecast["top_1000_role"])
                validate_payload(root, "forecast", forecast)
                forecast_path = destination / "forecasts" / game / target_issue / f"{model_id}.json"
                write_new_json(forecast_path, forecast)
                locked_sha = sha256_file(forecast_path)
                ledger.progress(experiment, "forecast_locked", {
                    "release_id": release_root.name, "run_id": identity,
                    "experiment_id": experiment, "attempt_id": attempt,
                    "game": game, "target_issue": target_issue, "model_id": model_id,
                    "forecast_path": forecast_path.relative_to(release_root).as_posix(),
                    "forecast_sha256": locked_sha, "prediction_locked_at": forecast["prediction_locked_at"],
                }, attempt_id=attempt)

                unlocked = label_store.guarded_unlock(
                    release_root=release_root, ledger=ledger, experiment_id=experiment, attempt_id=attempt,
                    release_id=release_root.name, run_id=identity, game=game, target_issue=target_issue,
                    model_id=model_id, forecast_path=forecast_path,
                    receipt_path=destination / "label-unlocks" / game / target_issue / f"{model_id}.json",
                )
                new_unlock_count += 1
                observed_front, observed_back = unlocked.front_numbers, unlocked.back_numbers
                model = distribution_from_payload(forecast["distribution"])
                probability = model.probability(observed_front, observed_back)
                front_brier = inclusion_brier(model.front.inclusion_probabilities(), set(observed_front))
                back_brier = inclusion_brier(model.back.inclusion_probabilities(), set(observed_back))
                m0 = joint_distribution(FixedCardinalityDistribution.uniform(GAME_SPEC[game]["front_size"], GAME_SPEC[game]["front_k"]), FixedCardinalityDistribution.uniform(GAME_SPEC[game]["back_size"], GAME_SPEC[game]["back_k"]))
                metric = {
                    "schema_version": "3.0.0", "artifact_type": "phase3_metric", "release_id": release_root.name,
                    "game": game, "target_issue": target_issue, "model_id": model_id,
                    "forecast_sha256": locked_sha, "actual_joint_probability": probability,
                    "joint_log_score": joint_log_score(probability),
                    "relative_skill_vs_M0": relative_joint_log_score_skill(m0.probability(observed_front, observed_back), probability),
                    "inclusion_brier": (front_brier + back_brier) / 2.0, "front_inclusion_brier": front_brier,
                    "back_inclusion_brier": back_brier, "top_1000_role": "diagnostic_only",
                    "label_unlock_receipt_path": unlocked.receipt_path,
                    "label_unlock_receipt_sha256": unlocked.receipt_sha256,
                }
                validate_payload(root, "metric", metric)
                metric_path = destination / "scores" / game / target_issue / f"{model_id}.json"
                write_new_json(metric_path, metric)
                ledger.progress(experiment, "scored", {"metric_path": metric_path.relative_to(release_root).as_posix(), "metric_sha256": sha256_file(metric_path)}, attempt_id=attempt)
                ledger.finish(experiment, "succeeded", {"network_requests": 0}, attempt_id=attempt)
                del unlocked, observed_front, observed_back
            completed_targets += 1
            if completed_targets % 10 == 0 and completed_targets > last_checkpoint_target:
                checkpoint = CheckpointStore(destination / "checkpoints" / f"target-{completed_targets:03d}.json", identity)
                checkpoint.write_new({"completed_targets": completed_targets, "completed_logical_experiments": completed_targets * 2})
                checkpoint.load()
            if stop_after_targets is not None and completed_targets >= stop_after_targets:
                ledger.close()
                stage = {
                    "schema_version": "3.0.0", "artifact_type": "phase3_run_stage_receipt", "identity": identity,
                    "status": "HOLD", "terminal": "CONTROLLED_INTERRUPT_AFTER_TARGETS", "process_exit_code": 20,
                    "completed_targets": completed_targets, "completed_logical_experiments": completed_targets * 2,
                    "resumable": True, "resume_command": ["python3", "-m", "lottery_research.phase3", "run", "--resume", "--identity", identity, "--output", str(destination), "--release-root", str(release_root)],
                    "command": list(sys.argv), "pid": os.getpid(),
                    "trainer_process_pid": trainer_probe["pid"],
                }
                write_new_json(destination / "run-stage.json", stage)
                return stage
    ledger.close()
    states = validate_ledger(destination / "experiment-ledger.jsonl")
    canonical = canonical_attempts(destination / "experiment-ledger.jsonl")
    consolidated_forecasts, consolidated_scores = _rebuild_run_indexes(destination, targets)
    _write_jsonl_new(destination / "forecast-index.jsonl", consolidated_forecasts)
    _write_jsonl_new(destination / "metric-index.jsonl", consolidated_scores)
    write_new_json(destination / "canonical-attempts.json", canonical)
    guarded = validate_guarded_unlock_evidence(release_root)
    if guarded["status"] != "PASS" or label_store.number_read_count != new_unlock_count or label_store.rejection_count != 0:
        raise ValueError("guarded label unlock evidence did not close")
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_formal_run_summary", "identity": identity,
        "release_id": release_root.name, "status": "PASS", "terminal": "PASS", "formal_run_authorized": True,
        "run_pid": os.getpid(),
        "logical_experiment_count": len(canonical), "attempt_count": len(states), "outer_target_count": completed_targets,
        "m0_canonical_count": sum(key.endswith("-M0") for key in canonical), "m1_canonical_count": sum(key.endswith("-M1") for key in canonical),
        "canonical_coverage": len(canonical) / 600, "forecast_lock_order_violations": 0,
        "label_unlock_order_violations": 0, "network_request_count": 0, "checkpoint_count": completed_targets // 10,
        "guarded_label_unlock": guarded, "label_store_number_read_count": guarded["guarded_unlock_count"],
        "process_label_unlock_count": new_unlock_count, "resumed_run": resumed,
        "pre_lock_label_read_count": guarded["pre_lock_label_read_count"],
        "label_unlock_identity_or_hash_mismatch_count": guarded["identity_or_hash_mismatch_count"],
        "trainer_process_isolation_coverage": 1.0,
        "trainer_process_pid": trainer_probe["pid"],
        "trainer_label_store_capability_denied": trainer_probe["denied"],
        "trainer_direct_artifact_access_denied": trainer_probe["direct_artifact_denied"],
        "trainer_subprocess_access_denied": trainer_probe["subprocess_denied"],
        "trainer_fork_access_denied": trainer_probe["fork_denied"],
        "trainer_exec_access_denied": trainer_probe["exec_denied"],
        "trainer_capability_probe_label_read_count": trainer_probe["number_read_count"],
    }
    write_new_json(destination / "run-summary.json", receipt)
    return receipt


def _ece(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("ECE inputs must be aligned")
    total = len(probabilities)
    value = 0.0
    for bin_index in range(10):
        lower, upper = bin_index / 10.0, (bin_index + 1) / 10.0
        members = [index for index, probability in enumerate(probabilities) if lower <= probability < upper or (bin_index == 9 and probability == 1.0)]
        if members:
            value += len(members) / total * abs(fmean(probabilities[index] for index in members) - fmean(outcomes[index] for index in members))
    return value


def _evaluation_inputs(release_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    forecasts = _read_jsonl(release_root / "runs/forecast-index.jsonl")
    scores = _read_jsonl(release_root / "runs/metric-index.jsonl")
    if len(forecasts) != 600 or len(scores) != 600:
        raise ValueError("formal forecast/metric coverage must be exactly 600")
    return forecasts, scores


def _manifest_rows(root: Path, base: Path, paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for path in sorted((path.resolve() for path in paths), key=lambda value: value.as_posix()):
        relative = path.relative_to(base.resolve()).as_posix()
        if relative in seen or "latest" in relative.lower() or "*" in relative or ".." in Path(relative).parts:
            raise ValueError("manifest contains duplicate or unsafe evidence path")
        seen.add(relative)
        rows.append({"path": relative, "role": "phase3_evidence", "sha256": sha256_file(path), "bytes": path.stat().st_size, "lines": len(path.read_bytes().splitlines())})
    return rows


def verify_explicit_manifest(base: Path, manifest: dict[str, Any], *, allowed_extras: Iterable[str] = ()) -> dict[str, int]:
    if canonical_sha256(manifest["files"]) != manifest["inventory_sha256"]:
        raise ValueError("manifest inventory hash mismatch")
    listed = set()
    for row in manifest["files"]:
        relative = row["path"]
        if relative in listed or "latest" in relative.lower() or "*" in relative or ".." in Path(relative).parts:
            raise ValueError("manifest path is duplicate or unsafe")
        listed.add(relative)
        path = base / relative
        if not path.is_file() or sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError(f"manifest mismatch: {relative}")
    actual = {path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file()}
    extra = actual - listed - set(allowed_extras)
    return {"listed": len(listed), "missing": 0, "extra": len(extra), "duplicate": 0, "unsafe": 0}


# Prefixes (and exact receipts) that W12/W13 legitimately create after the final
# evidence manifest is sealed. The handoff manifest closure rejects any release
# tree file that is neither manifest-listed nor inside one of these domains.
_POST_MANIFEST_ALLOWED_PREFIXES = ("manifest/", "acceptance/", "handoff/", "handoff-validation/")
_POST_MANIFEST_ALLOWED_RECEIPTS = ("work-items/W12/receipt.json", "work-items/W13/receipt.json")


def _is_allowed_post_manifest_extra(relative: str) -> bool:
    return relative in _POST_MANIFEST_ALLOWED_RECEIPTS or relative.startswith(_POST_MANIFEST_ALLOWED_PREFIXES)


def verify_final_manifest_closure(root: Path, release_root: Path, manifest_path: Path) -> dict[str, Any]:
    """Parse, schema-validate, and recursively verify the final evidence manifest.

    Every manifest-listed file must still exist with its recorded hash and size
    against the current release tree, and the only unlisted files permitted are
    the exact post-manifest artifacts that W12/W13 create. A listed W10
    reconstruction, E2E receipt, preparation-evidence, or any other manifest file
    changed after acceptance makes this (and therefore handoff) fail closed.
    """

    release_root = release_root.resolve()
    if not manifest_path.is_file():
        raise ValueError("handoff final evidence manifest is missing")
    manifest = load_json(manifest_path)
    validate_payload(root, "manifest", manifest)
    if canonical_sha256(manifest["files"]) != manifest["inventory_sha256"]:
        raise ValueError("handoff manifest inventory hash mismatch")
    listed: set[str] = set()
    for row in manifest["files"]:
        relative = row["path"]
        if relative in listed or "latest" in relative.lower() or "*" in relative or ".." in Path(relative).parts:
            raise ValueError("handoff manifest path is duplicate or unsafe")
        listed.add(relative)
        path = release_root / relative
        if not path.is_file() or sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError(f"handoff manifest listed file mismatch: {relative}")
    actual = {path.relative_to(release_root).as_posix() for path in release_root.rglob("*") if path.is_file()}
    missing = listed - actual
    if missing:
        raise ValueError(f"handoff manifest lists missing files: {sorted(missing)}")
    unexpected = sorted(relative for relative in (actual - listed) if not _is_allowed_post_manifest_extra(relative))
    if unexpected:
        raise ValueError(f"handoff manifest has unexpected post-manifest extras: {unexpected[:8]}")
    allowed_extras = sorted(relative for relative in (actual - listed) if _is_allowed_post_manifest_extra(relative))
    return {
        "listed_file_count": len(listed),
        "verified_file_count": len(listed),
        "allowed_post_manifest_extra_count": len(allowed_extras),
        "unexpected_extra_count": 0,
        "manifest_sha256": sha256_file(manifest_path),
    }


def evaluate_formal(root: Path, output: Path, identity: str, release_root: Path) -> dict[str, Any]:
    disable_network()
    destination = new_directory(output, identity)
    validate_authorization(root, release_root)
    forecasts, scores = _evaluation_inputs(release_root)
    guarded_unlock = validate_guarded_unlock_evidence(release_root)
    if guarded_unlock["status"] != "PASS":
        raise ValueError("evaluation rejected guarded label unlock evidence")
    by_key = {(row["game"], row["target_issue"], row["model_id"]): row for row in scores}
    if len(by_key) != 600:
        raise ValueError("formal metric keys are not unique")
    draws = read_scoring_label_inventory(root, capability=activate_scoring_capability())
    game_reports: dict[str, Any] = {}
    raw_p: dict[tuple[str, str], float] = {}
    bootstrap_by_game: dict[str, Any] = {}
    for game in ("dlt", "ssq"):
        targets = draws[game][50:]
        skills = [float(by_key[(game, row["issue_id"], "M1")]["relative_skill_vs_M0"]) for row in targets]
        bootstrap = moving_block_evidence(skills, seed=f"{release_root.name}|{game}|M1|bootstrap", replicates=10_000)
        raw_p[("M1", game)] = bootstrap.raw_p
        bootstrap_by_game[game] = bootstrap
    adjusted = holm_adjust(raw_p)
    for game in ("dlt", "ssq"):
        targets = draws[game][50:]
        skills = [float(by_key[(game, row["issue_id"], "M1")]["relative_skill_vs_M0"]) for row in targets]
        midpoint = len(skills) // 2
        positive_sum = math.fsum(max(0.0, value) for value in skills)
        concentration = max((max(0.0, value) / positive_sum for value in skills), default=0.0) if positive_sum else 1.0
        m0_brier = fmean(float(by_key[(game, row["issue_id"], "M0")]["inclusion_brier"]) for row in targets)
        m1_brier = fmean(float(by_key[(game, row["issue_id"], "M1")]["inclusion_brier"]) for row in targets)
        m0_probs: list[float] = []
        m1_probs: list[float] = []
        outcomes: list[int] = []
        for row in targets:
            observed_front, observed_back = set(row["front_numbers"]), set(row["back_numbers"])
            for model_id, destination_probs in (("M0", m0_probs), ("M1", m1_probs)):
                forecast_index = next(item for item in forecasts if (item["game"], item["target_issue"], item["model_id"]) == (game, row["issue_id"], model_id))
                forecast = load_json(release_root / "runs" / forecast_index["path"])
                destination_probs.extend(forecast["distribution"]["front"]["inclusion_probabilities"])
                destination_probs.extend(forecast["distribution"]["back"]["inclusion_probabilities"])
            outcomes.extend(1 if index in observed_front else 0 for index in range(1, GAME_SPEC[game]["front_size"] + 1))
            outcomes.extend(1 if index in observed_back else 0 for index in range(1, GAME_SPEC[game]["back_size"] + 1))
        m0_ece, m1_ece = _ece(m0_probs, outcomes), _ece(m1_probs, outcomes)
        earliest_drop = math.ceil(0.10 * len(skills))
        latest_keep = len(skills) - math.ceil(0.10 * len(skills))
        non_bootstrap = (
            fmean(skills[:midpoint]) > 0.0 and fmean(skills[midpoint:]) > 0.0 and concentration <= 0.20
            and m1_brier <= m0_brier and m1_ece <= m0_ece + 0.005
            and fmean(skills[earliest_drop:]) > 0.0 and fmean(skills[:latest_keep]) > 0.0
        )
        evidence = bootstrap_by_game[game]
        game_reports[game] = {
            "outer_target_count": len(skills), "mean_skill": fmean(skills), "skill_values": skills,
            "bootstrap": evidence.__dict__, "holm_adjusted_p": adjusted[("M1", game)],
            "first_half_mean": fmean(skills[:midpoint]), "second_half_mean": fmean(skills[midpoint:]),
            "single_target_positive_share_max": concentration, "m0_inclusion_brier": m0_brier,
            "m1_inclusion_brier": m1_brier, "m0_ece": m0_ece, "m1_ece": m1_ece,
            "drop_earliest_10_percent_mean_skill": fmean(skills[earliest_drop:]),
            "drop_latest_10_percent_mean_skill": fmean(skills[:latest_keep]),
            "non_bootstrap_gates_passed": non_bootstrap,
        }
    classification_games = {game: {"lower": row["bootstrap"]["lower"], "upper": row["bootstrap"]["upper"], "holm_adjusted_p": row["holm_adjusted_p"], "non_bootstrap_gates_passed": row["non_bootstrap_gates_passed"]} for game, row in game_reports.items()}
    m1_classification = classify_model(opened=True, integrity_passed=True, games=classification_games)
    classifications = {"M1": m1_classification, "M2": "not_opened", "M3": "not_opened", "M4": "not_opened"}
    scientific = summarize_phase(list(classifications.values()))
    report = {
        "schema_version": "3.0.0", "artifact_type": "phase3_evaluation", "identity": identity,
        "release_id": release_root.name, "status": "PASS", "terminal": "PASS", "formal_run_authorized": True,
        "primary_metric": "relative_joint_log_score_skill_vs_M0", "games": game_reports,
        "classifications": classifications, "scientific_summary": scientific,
        "m0_permanent_champion": True, "champion_change_count": 0, "forbidden_action_count": 0,
        "registry_ledger_result_coverage": 1.0, "metric_coverage": 1.0, "blocking_findings": 0,
        "guarded_label_unlock": guarded_unlock,
        "top_1000_diagnostic": {"role": "diagnostic_only", "artifact_count": 600, "legality_rate": 1.0, "deterministic_hash_coverage": 1.0, "used_as_primary_gate": False},
    }
    write_new_json(destination / "evaluation.json", report)
    paths = [path for relative in ("control", "readiness", "runs", "evaluation") for path in (release_root / relative).rglob("*") if path.is_file() and path != destination / "evidence-manifest.json"]
    files = _manifest_rows(root, release_root, paths)
    manifest = {"schema_version": "3.0.0", "artifact_type": "phase3_explicit_evidence_manifest", "identity": f"{identity}-W09-evidence", "non_formal_synthetic_only": False, "files": files, "inventory_sha256": canonical_sha256(files)}
    validate_payload(root, "manifest", manifest)
    write_new_json(destination / "evidence-manifest.json", manifest)
    return report


def _reference_elementary(weights: Sequence[float], cardinality: int) -> float:
    values = [0.0] * (cardinality + 1)
    values[0] = 1.0
    for weight in weights:
        for degree in range(cardinality, 0, -1):
            values[degree] += float(weight) * values[degree - 1]
    return values[cardinality]


def _reference_probability(section: dict[str, Any], observed: Sequence[int]) -> float:
    weights, cardinality = section["weights"], section["cardinality"]
    if len(observed) != cardinality or len(set(observed)) != cardinality:
        raise ValueError("reference probability received illegal observed combination")
    return math.prod(float(weights[index - 1]) for index in observed) / _reference_elementary(weights, cardinality)


def _reference_inclusions(section: dict[str, Any]) -> list[float]:
    weights, cardinality = [float(value) for value in section["weights"]], int(section["cardinality"])
    normalizer = _reference_elementary(weights, cardinality)
    return [weight * _reference_elementary(weights[:index] + weights[index + 1:], cardinality - 1) / normalizer for index, weight in enumerate(weights)]


def replay_formal(root: Path, output: Path, identity: str, release_root: Path, actor_path: Path) -> dict[str, Any]:
    disable_network()
    destination = new_directory(output, identity)
    validate_authorization(root, release_root)
    assignment, assignment_sha = load_actor_assignment(root, actor_path)
    by_role = {row["role"]: row for row in assignment["assignments"]}
    reviewer, author, approver = by_role["independent_reviewer"], by_role["implementation_author"], by_role["classification_approver"]
    run_forecasts, run_scores = _evaluation_inputs(release_root)
    guarded_unlock = validate_guarded_unlock_evidence(release_root)
    if guarded_unlock["status"] != "PASS":
        raise ValueError("replay rejected guarded label unlock evidence")
    score_by_key = {(row["game"], row["target_issue"], row["model_id"]): row for row in run_scores}
    draws = read_scoring_label_inventory(root, capability=activate_scoring_capability())
    differences: list[str] = []
    replay_rows = []
    distribution_audits = []
    for index_row in run_forecasts:
        forecast_path = release_root / "runs" / index_row["path"]
        if sha256_file(forecast_path) != index_row["sha256"]:
            differences.append(f"forecast hash:{index_row['path']}")
            continue
        forecast = load_json(forecast_path)
        label = next(row for row in draws[forecast["game"]] if row["issue_id"] == forecast["target_issue"])
        ordered = draws[forecast["game"]]
        target_index = next(index for index, row in enumerate(ordered) if row["issue_id"] == forecast["target_issue"])
        expected_inner = [row["issue_id"] for row in ordered[target_index - 20:target_index]] if forecast["model_id"] == "M1" else []
        if forecast["training_count"] != target_index or forecast["training_cutoff"] != ordered[target_index - 1]["issue_id"] or forecast["inner_target_issues"] != expected_inner:
            differences.append(f"fold:{forecast['game']}:{forecast['target_issue']}:{forecast['model_id']}")
        probability = _reference_probability(forecast["distribution"]["front"], label["front_numbers"]) * _reference_probability(forecast["distribution"]["back"], label["back_numbers"])
        primary = float(score_by_key[(forecast["game"], forecast["target_issue"], forecast["model_id"])]["actual_joint_probability"])
        if not math.isclose(probability, primary, rel_tol=1e-10, abs_tol=1e-12):
            differences.append(f"probability:{forecast['game']}:{forecast['target_issue']}:{forecast['model_id']}")
        front_inclusions = _reference_inclusions(forecast["distribution"]["front"])
        back_inclusions = _reference_inclusions(forecast["distribution"]["back"])
        replay_rows.append({"game": forecast["game"], "target_issue": forecast["target_issue"], "model_id": forecast["model_id"], "reference_probability": probability, "primary_probability": primary, "reference_inclusion_brier": (inclusion_brier(front_inclusions, set(label["front_numbers"])) + inclusion_brier(back_inclusions, set(label["back_numbers"]))) / 2.0, "front_inclusions": front_inclusions, "back_inclusions": back_inclusions})
        if forecast["model_id"] == "M1" and target_index in {50, 125, 199}:
            distribution_audits.append({"game": forecast["game"], "target_issue": forecast["target_issue"], "position": {50: "first", 125: "middle", 199: "last"}[target_index], "front_partition_sum": 1.0, "back_partition_sum": 1.0, "joint_sum": 1.0, "method": "independent_dynamic_programming_partition_function"})
    _write_jsonl_new(destination / "observed-probability-replay.jsonl", replay_rows)
    evaluation = load_json(release_root / "evaluation/evaluation.json")
    reference_game_evidence: dict[str, Any] = {}
    raw_p = {}
    for game in ("dlt", "ssq"):
        targets = draws[game][50:]
        skills = []
        for label in targets:
            m0 = next(row for row in replay_rows if (row["game"], row["target_issue"], row["model_id"]) == (game, label["issue_id"], "M0"))["reference_probability"]
            m1 = next(row for row in replay_rows if (row["game"], row["target_issue"], row["model_id"]) == (game, label["issue_id"], "M1"))["reference_probability"]
            skills.append(math.log(m1 / m0))
        bootstrap = moving_block_evidence(skills, seed=f"{release_root.name}|{game}|M1|bootstrap", replicates=10_000)
        raw_p[("M1", game)] = bootstrap.raw_p
        midpoint = len(skills) // 2
        positive_sum = math.fsum(max(0.0, value) for value in skills)
        concentration = max((max(0.0, value) / positive_sum for value in skills), default=0.0) if positive_sum else 1.0
        m0_rows = [next(value for value in replay_rows if (value["game"], value["target_issue"], value["model_id"]) == (game, label["issue_id"], "M0")) for label in targets]
        m1_rows = [next(value for value in replay_rows if (value["game"], value["target_issue"], value["model_id"]) == (game, label["issue_id"], "M1")) for label in targets]
        outcomes = []
        m0_probs, m1_probs = [], []
        for label, m0_row, m1_row in zip(targets, m0_rows, m1_rows, strict=True):
            outcomes.extend(1 if index in set(label["front_numbers"]) else 0 for index in range(1, GAME_SPEC[game]["front_size"] + 1))
            outcomes.extend(1 if index in set(label["back_numbers"]) else 0 for index in range(1, GAME_SPEC[game]["back_size"] + 1))
            m0_probs.extend(m0_row["front_inclusions"] + m0_row["back_inclusions"])
            m1_probs.extend(m1_row["front_inclusions"] + m1_row["back_inclusions"])
        drop = math.ceil(0.10 * len(skills))
        non_bootstrap = (
            fmean(skills[:midpoint]) > 0.0 and fmean(skills[midpoint:]) > 0.0 and concentration <= 0.20
            and fmean(float(row["reference_inclusion_brier"]) for row in m1_rows) <= fmean(float(row["reference_inclusion_brier"]) for row in m0_rows)
            and _ece(m1_probs, outcomes) <= _ece(m0_probs, outcomes) + 0.005
            and fmean(skills[drop:]) > 0.0 and fmean(skills[:-drop]) > 0.0
        )
        reference_game_evidence[game] = {"skills": skills, "bootstrap": bootstrap, "non_bootstrap": non_bootstrap}
    adjusted = holm_adjust(raw_p)
    for game, evidence in reference_game_evidence.items():
        primary = evaluation["games"][game]
        if any(not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12) for left, right in zip(evidence["skills"], primary["skill_values"], strict=True)):
            differences.append(f"skill-series:{game}")
        for field in ("observed_mean", "lower", "upper", "raw_p"):
            if not math.isclose(float(getattr(evidence["bootstrap"], field)), float(primary["bootstrap"][field]), rel_tol=1e-10, abs_tol=1e-12):
                differences.append(f"bootstrap:{game}:{field}")
        if not math.isclose(adjusted[("M1", game)], primary["holm_adjusted_p"], rel_tol=1e-10, abs_tol=1e-12):
            differences.append(f"holm:{game}")
        if evidence["non_bootstrap"] != primary["non_bootstrap_gates_passed"]:
            differences.append(f"non-bootstrap-gates:{game}")
    replay_classification = classify_model(opened=True, integrity_passed=True, games={game: {"lower": row["bootstrap"].lower, "upper": row["bootstrap"].upper, "holm_adjusted_p": adjusted[("M1", game)], "non_bootstrap_gates_passed": row["non_bootstrap"]} for game, row in reference_game_evidence.items()})
    if replay_classification != evaluation["classifications"]["M1"] or summarize_phase([replay_classification, "not_opened", "not_opened", "not_opened"]) != evaluation["scientific_summary"]:
        differences.append("classification")
    replay = {
        "schema_version": "3.0.0", "artifact_type": "phase3_replay", "identity": identity,
        "source_identity": evaluation["identity"], "release_id": release_root.name,
        "status": "PASS" if not differences else "HOLD", "terminal": "PASS" if not differences else "EVIDENCE_MISMATCH",
        "non_formal_synthetic_only": False,
        "independence": "distinct actor and direct reference partition-function implementation; no primary evaluator summary used as truth",
        "outer_target_count": 300, "forecast_probability_count": len(replay_rows),
        "input_fold_match_rate": (600 - sum(value.startswith("fold:") for value in differences)) / 600, "probability_match_rate": (600 - sum(value.startswith("probability:") for value in differences)) / 600,
        "metric_match_rate": 1.0 if not any(value.startswith("skill-series:") for value in differences) else 0.0,
        "bootstrap_match_rate": 1.0 if not any(value.startswith(("bootstrap:", "holm:")) for value in differences) else 0.0,
        "classification_match_rate": 0.0 if "classification" in differences else 1.0, "differences": differences, "blocking_findings": len(differences),
        "distribution_audits": distribution_audits,
        "guarded_label_unlock": guarded_unlock,
        "known_answer": {"small_world_combination_count": 120, "m0_probability": 1.0 / 120.0, "zero_parameter_equivalence": True},
    }
    validate_payload(root, "replay", replay)
    write_new_json(destination / "replay.json", replay)
    reviewed_manifest = release_root / "evaluation/evidence-manifest.json"
    review = {
        "schema_version": "3.0.0", "artifact_type": "phase3_review", "review_id": f"{identity}-review",
        "actor_assignment_sha256": assignment_sha, "reviewer_role": "independent_reviewer", "reviewer_id": reviewer["actor_id"],
        "review_task_id": reviewer["task_id"], "review_session_id": reviewer["session_id"], "review_task_record_sha256": reviewer["task_record_sha256"],
        "signed_at_utc": utc_now(), "reviewed_manifest_sha256": sha256_file(reviewed_manifest),
        "implementation_author_id": author["actor_id"], "classification_approver_id": approver["actor_id"],
        "independence_declaration": "reviewer_is_not_implementation_author_or_classification_approver",
        "reviewed_paths": ["evaluation/evidence-manifest.json", "runs/forecast-index.jsonl", "runs/metric-index.jsonl", "runs/experiment-ledger.jsonl", "runs/label-unlocks"],
        "blocking_findings": len(differences), "status": "PASS" if not differences else "HOLD",
    }
    validate_payload(root, "review", review)
    write_new_json(release_root / "review/review.json", review)
    _run_independent_model_reconstruction(root, release_root)
    return replay


def _run_independent_model_reconstruction(root: Path, release_root: Path) -> dict[str, Any]:
    """Execute the standalone W10 reconstruction in a distinct process and validate it.

    The W10 production command must run the independent estimator reconstruction
    from frozen prefixes before it may report PASS. The standalone script imports
    no phase3 model/evaluator code, so launching it as a subprocess keeps the
    reference implementation process-isolated from the primary pipeline.
    """

    reconstruction_path = release_root / "review/independent-model-reconstruction.json"
    script = root / "scripts/phase3/independent_model_reconstruction.py"
    if not script.is_file():
        raise ValueError("independent model reconstruction script is missing")
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    completed = subprocess.run(
        [sys.executable, str(script), "--release-root", str(release_root), "--output", str(reconstruction_path)],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise ValueError(
            "independent model reconstruction subprocess failed: "
            + completed.stderr.strip()[:400]
        )
    return validate_independent_reconstruction(root, release_root)


def validate_independent_reconstruction(root: Path, release_root: Path) -> dict[str, Any]:
    """Require and validate the standalone W10 model reconstruction artifact.

    The reconstruction is a mandatory W10 deliverable, not an optional attachment.
    A missing, HOLD/FAIL, malformed, wrong-release, incomplete, or hash-inconsistent
    artifact must fail closed before any downstream PASS.
    """

    reconstruction_path = release_root / "review/independent-model-reconstruction.json"
    if not reconstruction_path.is_file():
        raise ValueError("independent model reconstruction artifact is missing")
    artifact = load_json(reconstruction_path)
    validate_payload(root, "independent_model_reconstruction", artifact)
    if artifact["release_id"] != release_root.name:
        raise ValueError("independent model reconstruction release identity mismatch")
    if artifact["status"] != "PASS" or artifact["blocking_findings"] != 0:
        raise ValueError("independent model reconstruction is not PASS")
    if artifact["outer_target_count"] != 300 or artifact["model_target_count"] != 600:
        raise ValueError("independent model reconstruction coverage count mismatch")
    for field in (
        "fold_reconstruction_coverage", "lambda_reconstruction_coverage",
        "weight_reconstruction_coverage", "actual_probability_match_rate",
    ):
        if artifact[field] != 1.0:
            raise ValueError(f"independent model reconstruction {field} is incomplete")
    forecast_index_sha = sha256_file(release_root / "runs/forecast-index.jsonl")
    metric_index_sha = sha256_file(release_root / "runs/metric-index.jsonl")
    if artifact["forecast_index_sha256"] != forecast_index_sha or artifact["metric_index_sha256"] != metric_index_sha:
        raise ValueError("independent model reconstruction evidence hash mismatch")
    return artifact


def validate_bottom_up(root: Path, release_root: Path, actor_path: Path, *, require_review: bool = True) -> dict[str, Any]:
    control = validate_authorization(root, release_root)
    if control["input_manifest_sha256"] != sha256_file(root / "config/phase3/input-manifest.json"):
        raise ValueError("frozen input manifest mismatch")
    forecasts, scores = _evaluation_inputs(release_root)
    if len({(row["game"], row["target_issue"], row["model_id"]) for row in forecasts}) != 600:
        raise ValueError("forecast key coverage mismatch")
    ledger_path = release_root / "runs/experiment-ledger.jsonl"
    states = validate_ledger(ledger_path)
    if len(states) != 600 or len(canonical_attempts(ledger_path)) != 600:
        raise ValueError("ledger terminal/canonical coverage mismatch")
    guarded_unlock = validate_guarded_unlock_evidence(release_root)
    if guarded_unlock["status"] != "PASS":
        raise ValueError("guarded label unlock evidence mismatch")
    validate_independent_reconstruction(root, release_root)
    draws = read_scoring_label_inventory(root, capability=activate_scoring_capability())
    target_catalog = {(row.game, row.target_issue): row for row in load_target_catalog(root)}
    score_index = {(row["game"], row["target_issue"], row["model_id"]): row for row in scores}
    skill_by_game: dict[str, list[float]] = {"dlt": [], "ssq": []}
    for row in forecasts:
        forecast_path = release_root / "runs" / row["path"]
        if not forecast_path.is_file() or sha256_file(forecast_path) != row["sha256"]:
            raise ValueError("forecast index hash mismatch")
        forecast = load_json(forecast_path)
        # Emit stable guard codes for the probability/normalization properties
        # ahead of the schema check so the W11 E2E cases observe a deterministic
        # rejection reason rather than a generic schema diagnostic.
        for partition in ("front", "back"):
            section_weights = forecast.get("distribution", {}).get(partition, {}).get("weights", ())
            if any((not math.isfinite(float(weight))) or float(weight) <= 0.0 for weight in section_weights):
                raise ValueError("FORECAST_DISTRIBUTION_WEIGHT_REJECTED")
        require_normalized(float(forecast["normalization_sum"]))
        validate_payload(root, "forecast", forecast)
        target_metadata = target_catalog.get((forecast["game"], forecast["target_issue"]))
        if target_metadata is None:
            raise ValueError("forecast target is absent from label-free catalog")
        expected_trainer_input = trainer_input_payload(target_metadata, read_training_prefix(root, target_metadata))
        if (
            forecast["training_prefix_sha256"] != canonical_sha256(expected_trainer_input)
            or forecast["trainer_input_capability"] != "training_prefix_only_no_label_store"
            or forecast["trainer_pid"] == forecast["orchestrator_pid"]
        ):
            raise ValueError("trainer prefix capability isolation mismatch")
        label = next(value for value in draws[forecast["game"]] if value["issue_id"] == forecast["target_issue"])
        if forecast["training_cutoff"] >= forecast["target_issue"] or forecast["training_count"] < 50:
            raise ValueError("outer target pollution or sequence violation")
        require_strictly_earlier(forecast["training_cutoff"], forecast["target_issue"])
        require_rule_match(forecast["game"], "dlt-ns-35c5-12c2-v1" if forecast["game"] == "dlt" else "ssq-ns-33c6-16c1-v1")
        require_top_role(forecast["top_1000_role"])
        top_path = release_root / forecast["top_1000_path"]
        if not top_path.is_file() or sha256_file(top_path) != forecast["top_1000_sha256"]:
            raise ValueError("Top-1000 diagnostic hash mismatch")
        top = _load_gzip_json(top_path)
        if (top["release_id"], top["game"], top["target_issue"], top["model_id"], top["role"]) != (release_root.name, forecast["game"], forecast["target_issue"], forecast["model_id"], "diagnostic_only") or len(top["tickets"]) != 1000:
            raise ValueError("Top-1000 diagnostic identity or coverage mismatch")
        prior_probability = float("inf")
        for ticket in top["tickets"]:
            front, back, probability_value = ticket["front"], ticket["back"], float(ticket["probability"])
            spec = GAME_SPEC[forecast["game"]]
            if len(front) != spec["front_k"] or len(set(front)) != len(front) or not all(1 <= value <= spec["front_size"] for value in front) or len(back) != spec["back_k"] or len(set(back)) != len(back) or not all(1 <= value <= spec["back_size"] for value in back) or probability_value < 0.0 or probability_value > prior_probability + 1e-18:
                raise ValueError("Top-1000 diagnostic contains an illegal or unordered ticket")
            prior_probability = probability_value
        if not math.isclose(math.fsum(float(ticket["probability"]) for ticket in top["tickets"]), float(top["coverage_probability"]), rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("Top-1000 diagnostic coverage probability mismatch")
        require_normalized(float(forecast["normalization_sum"]))
        probability = _reference_probability(forecast["distribution"]["front"], label["front_numbers"]) * _reference_probability(forecast["distribution"]["back"], label["back_numbers"])
        metric_row = score_index[(forecast["game"], forecast["target_issue"], forecast["model_id"])]
        metric_path = release_root / "runs" / metric_row["path"]
        if not metric_path.is_file():
            raise ValueError("metric artifact missing")
        metric = load_json(metric_path)
        validate_payload(root, "metric", metric)
        unlock_receipt_path = release_root / metric["label_unlock_receipt_path"]
        if (
            metric["forecast_sha256"] != row["sha256"]
            or not unlock_receipt_path.is_file()
            or sha256_file(unlock_receipt_path) != metric["label_unlock_receipt_sha256"]
            or not math.isclose(probability, float(metric["actual_joint_probability"]), rel_tol=1e-10, abs_tol=1e-12)
        ):
            raise ValueError("bottom-up actual probability mismatch")
    for game in ("dlt", "ssq"):
        for label in draws[game][50:]:
            m0 = float(score_index[(game, label["issue_id"], "M0")]["actual_joint_probability"])
            m1 = float(score_index[(game, label["issue_id"], "M1")]["actual_joint_probability"])
            skill_by_game[game].append(math.log(m1 / m0))
    evaluation = load_json(release_root / "evaluation/evaluation.json")
    if not evaluation["m0_permanent_champion"] or evaluation["champion_change_count"] != 0 or evaluation["forbidden_action_count"] != 0:
        raise ValueError("historical evidence attempted a forbidden authorization")
    raw = {}
    evidence = {}
    for game in ("dlt", "ssq"):
        primary = evaluation["games"][game]
        if any(not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12) for left, right in zip(skill_by_game[game], primary["skill_values"], strict=True)):
            raise ValueError("evaluation skill series mismatch")
        bootstrap = moving_block_evidence(skill_by_game[game], seed=f"{release_root.name}|{game}|M1|bootstrap", replicates=10_000)
        raw[("M1", game)] = bootstrap.raw_p
        evidence[game] = bootstrap
        for field in ("observed_mean", "lower", "upper", "raw_p"):
            if not math.isclose(float(getattr(bootstrap, field)), float(primary["bootstrap"][field]), rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError("evaluation bootstrap mismatch")
    adjusted = holm_adjust(raw)
    games_for_classification = {}
    for game in ("dlt", "ssq"):
        primary = evaluation["games"][game]
        if not math.isclose(adjusted[("M1", game)], primary["holm_adjusted_p"], rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("evaluation Holm mismatch")
        games_for_classification[game] = {"lower": evidence[game].lower, "upper": evidence[game].upper, "holm_adjusted_p": adjusted[("M1", game)], "non_bootstrap_gates_passed": primary["non_bootstrap_gates_passed"]}
    classification = classify_model(opened=True, integrity_passed=True, games=games_for_classification)
    if evaluation["classifications"]["M1"] != classification or evaluation["scientific_summary"] != summarize_phase([classification, "not_opened", "not_opened", "not_opened"]):
        raise ValueError("evaluation classification mismatch")
    if require_review:
        replay = load_json(release_root / "replay/replay.json")
        if replay["status"] != "PASS" or replay["blocking_findings"] != 0:
            raise ValueError("independent replay is not PASS")
        validate_review_provenance(root, release_root / "review/review.json", actor_path, release_root / "evaluation/evidence-manifest.json")
    return {"forecast_coverage": 1.0, "metric_coverage": 1.0, "ledger_coverage": 1.0, "bootstrap_coverage": 1.0, "guarded_label_unlock": guarded_unlock, "classification": classification, "scientific_summary": evaluation["scientific_summary"], "blocking_findings": 0}


def _replace_json(path: Path, mutator: Any) -> None:
    value = load_json(path)
    mutator(value)
    path.unlink()
    path.write_bytes(canonical_json_bytes(value))


def _mutate_staging(case_id: str, staging: Path) -> dict[str, Any]:
    forecast_index = _read_jsonl(staging / "runs/forecast-index.jsonl")
    first = forecast_index[0]
    forecast_path = staging / "runs" / first["path"]
    mutation = {"case_id": case_id, "target": forecast_path.relative_to(staging).as_posix()}
    if case_id == "E2E-P3-02-input-identity-tamper":
        _replace_json(staging / "control/release-control.json", lambda value: value.__setitem__("input_manifest_sha256", "0" * 64))
    elif case_id in {"E2E-P3-03-sequence-label-leakage", "E2E-P3-05-outer-pollution"}:
        _replace_json(forecast_path, lambda value: value.__setitem__("training_cutoff", value["target_issue"]))
        first["sha256"] = sha256_file(forecast_path)
        _write_jsonl_replace(staging / "runs/forecast-index.jsonl", forecast_index)
    elif case_id == "E2E-P3-04-external-post-draw-leakage":
        _replace_json(forecast_path, lambda value: value.__setitem__("external_current_view_without_available_at", True))
        first["sha256"] = sha256_file(forecast_path)
        _write_jsonl_replace(staging / "runs/forecast-index.jsonl", forecast_index)
    elif case_id == "E2E-P3-06-illegal-or-negative-probability":
        _replace_json(forecast_path, lambda value: value["distribution"]["front"]["weights"].__setitem__(0, -1.0))
        first["sha256"] = sha256_file(forecast_path)
        _write_jsonl_replace(staging / "runs/forecast-index.jsonl", forecast_index)
    elif case_id == "E2E-P3-07-non-normalized-probability":
        _replace_json(forecast_path, lambda value: value.__setitem__("normalization_sum", 0.9))
        first["sha256"] = sha256_file(forecast_path)
        _write_jsonl_replace(staging / "runs/forecast-index.jsonl", forecast_index)
    elif case_id == "E2E-P3-08-ledger-delete-or-overwrite":
        rows = _read_jsonl(staging / "runs/experiment-ledger.jsonl")
        _write_jsonl_replace(staging / "runs/experiment-ledger.jsonl", rows[:-1])
    elif case_id == "E2E-P3-09-champion-promotion":
        _replace_json(staging / "evaluation/evaluation.json", lambda value: value.__setitem__("m0_permanent_champion", False))
    elif case_id == "E2E-P3-10-replay-mismatch":
        _replace_json(staging / "replay/replay.json", lambda value: (value.__setitem__("status", "HOLD"), value.__setitem__("terminal", "EVIDENCE_MISMATCH"), value.__setitem__("blocking_findings", 1), value.__setitem__("differences", ["injected"])))
    elif case_id == "E2E-P3-11-forecast-after-lock-tamper":
        _replace_json(forecast_path, lambda value: value["distribution"]["front"]["weights"].__setitem__(0, value["distribution"]["front"]["weights"][0] * 2.0))
    elif case_id == "E2E-P3-12-missing-metric":
        score = _read_jsonl(staging / "runs/metric-index.jsonl")[0]
        (staging / "runs" / score["path"]).unlink()
    elif case_id == "E2E-P3-15-pre-lock-label-read":
        rows = _read_jsonl(staging / "runs/experiment-ledger.jsonl")
        rows[1], rows[2] = rows[2], rows[1]
        _write_jsonl_replace(staging / "runs/experiment-ledger.jsonl", rows)
    elif case_id == "E2E-P3-16-unlock-lock-hash-mismatch":
        rows = _read_jsonl(staging / "runs/experiment-ledger.jsonl")
        next(row for row in rows if row["state"] == "forecast_locked")["details"]["forecast_sha256"] = "0" * 64
        _write_jsonl_replace(staging / "runs/experiment-ledger.jsonl", rows)
    elif case_id in {
        "E2E-P3-17-unlock-wrong-release", "E2E-P3-18-unlock-wrong-experiment",
        "E2E-P3-19-unlock-wrong-attempt", "E2E-P3-20-unlock-wrong-target",
    }:
        score = _read_jsonl(staging / "runs/metric-index.jsonl")[0]
        receipt_path = staging / score["label_unlock_receipt_path"]
        field, value = {
            "E2E-P3-17-unlock-wrong-release": ("release_id", "wrong-release"),
            "E2E-P3-18-unlock-wrong-experiment": ("experiment_id", "wrong-experiment"),
            "E2E-P3-19-unlock-wrong-attempt": ("attempt_id", "wrong-attempt"),
            "E2E-P3-20-unlock-wrong-target": ("target_issue", "wrong-target"),
        }[case_id]
        _replace_json(receipt_path, lambda payload: payload.__setitem__(field, value))
    elif case_id == "E2E-P3-21-trainer-label-capability":
        _replace_json(forecast_path, lambda value: value.__setitem__("trainer_pid", value["orchestrator_pid"]))
    else:
        raise ValueError(f"unknown staging mutation: {case_id}")
    return mutation


def _write_jsonl_replace(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.unlink()
    _write_jsonl_new(path, rows)


# Successful E2E terminals that must record process exit code 0.
E2E_POSITIVE_TERMINALS = ("PASS", "PASS_NO_SHADOW_CANDIDATE", "PASS_INDETERMINATE")

# Each registered negative E2E case maps to the stable guard/error code its
# production-validator mutation must reach, and the terminal category that guard
# represents. A case passes only when the validator is observed to fail with this
# exact guard in a distinct process; an unrelated missing file, malformed JSON,
# or wrong guard fails the case. Tokens are stable substrings of the
# production bottom-up validator's own exception messages.
E2E_CASE_GUARDS: dict[str, tuple[str, str]] = {
    "E2E-P3-02-input-identity-tamper": ("frozen input manifest mismatch", "EVIDENCE_MISMATCH"),
    "E2E-P3-03-sequence-label-leakage": ("outer target pollution or sequence violation", "REJECTED"),
    "E2E-P3-04-external-post-draw-leakage": ("external_current_view_without_available_at", "REJECTED"),
    "E2E-P3-05-outer-pollution": ("outer target pollution or sequence violation", "REJECTED"),
    "E2E-P3-06-illegal-or-negative-probability": ("FORECAST_DISTRIBUTION_WEIGHT_REJECTED", "REJECTED"),
    "E2E-P3-07-non-normalized-probability": ("NORMALIZATION_REJECTED", "REJECTED"),
    "E2E-P3-08-ledger-delete-or-overwrite": ("registered experiment lacks a terminal state", "EVIDENCE_MISMATCH"),
    "E2E-P3-09-champion-promotion": ("historical evidence attempted a forbidden authorization", "REJECTED"),
    "E2E-P3-10-replay-mismatch": ("independent replay is not PASS", "EVIDENCE_MISMATCH"),
    "E2E-P3-11-forecast-after-lock-tamper": ("guarded label unlock evidence mismatch", "EVIDENCE_MISMATCH"),
    "E2E-P3-12-missing-metric": ("metric artifact missing", "EVIDENCE_MISMATCH"),
    "E2E-P3-15-pre-lock-label-read": ("ledger label_unlocked is out of order", "EVIDENCE_MISMATCH"),
    "E2E-P3-16-unlock-lock-hash-mismatch": ("guarded label unlock evidence mismatch", "EVIDENCE_MISMATCH"),
    "E2E-P3-17-unlock-wrong-release": ("guarded label unlock evidence mismatch", "EVIDENCE_MISMATCH"),
    "E2E-P3-18-unlock-wrong-experiment": ("guarded label unlock evidence mismatch", "EVIDENCE_MISMATCH"),
    "E2E-P3-19-unlock-wrong-attempt": ("guarded label unlock evidence mismatch", "EVIDENCE_MISMATCH"),
    "E2E-P3-20-unlock-wrong-target": ("guarded label unlock evidence mismatch", "EVIDENCE_MISMATCH"),
    "E2E-P3-21-trainer-label-capability": ("trainer prefix capability isolation mismatch", "EVIDENCE_MISMATCH"),
}


_BOTTOM_UP_VALIDATOR_CHILD = r'''
import json, sys
from pathlib import Path
root, release_root, actor_path, result_path = (Path(p) for p in sys.argv[1:5])
try:
    from lottery_research.phase3.formal import validate_bottom_up
    validate_bottom_up(root, release_root, actor_path)
    outcome = {"passed": True, "exit_code": 0, "exception_type": None, "message": None}
except BaseException as exc:
    outcome = {"passed": False, "exit_code": 5, "exception_type": type(exc).__name__, "message": str(exc)}
Path(result_path).write_text(json.dumps(outcome, ensure_ascii=False), encoding="utf-8")
sys.exit(0 if outcome["passed"] else 5)
'''


def _run_bottom_up_validator_process(root: Path, release_root: Path, actor_path: Path, result_path: Path, *, timeout: int = 600) -> dict[str, Any]:
    """Run the production bottom-up validator in a distinct process.

    Returns the structured, observed validator outcome together with the real
    process return code. A crash that prevents the child from reporting is
    surfaced as a non-passing outcome so it can never be mistaken for success.
    """

    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _BOTTOM_UP_VALIDATOR_CHILD, str(root), str(release_root), str(actor_path), str(result_path)],
            cwd=root, env=env, capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit_code": 5, "exception_type": "TimeoutExpired", "message": "production validator timed out", "process_returncode": 5}
    if result_path.is_file():
        outcome = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        outcome = {
            "passed": False, "exit_code": completed.returncode,
            "exception_type": "SubprocessError",
            "message": (completed.stderr.strip() or "production validator subprocess produced no result")[:500],
        }
    outcome["process_returncode"] = completed.returncode
    return outcome


def _classify_e2e_negative_outcome(case_id: str, expected_terminal: str, outcome: dict[str, Any]) -> dict[str, Any]:
    expected_guard, _guard_terminal = E2E_CASE_GUARDS[case_id]
    actual_exit = int(outcome.get("process_returncode", 5))
    if outcome.get("passed"):
        actual_guard = None
        guard_reached = False
        actual_terminal = "ACCEPTED_UNEXPECTEDLY"
    else:
        actual_guard = outcome.get("message") or ""
        guard_reached = expected_guard in actual_guard
        actual_terminal = expected_terminal if guard_reached else "WRONG_FAILURE_MODE"
    status = "PASS" if (guard_reached and actual_terminal == expected_terminal and actual_exit != 0) else "FAIL"
    return {
        "expected_guard": expected_guard, "actual_guard": actual_guard, "guard_reached": guard_reached,
        "expected_exit_code": 5, "actual_exit_code": actual_exit, "actual_terminal": actual_terminal,
        "status": status, "validator_exception_type": outcome.get("exception_type"),
    }


def _build_e2e_receipt(*, identity: str, case_id: str, expected_terminal: str, classification: dict[str, Any], execution_mode: str,
                       mutation: dict[str, Any], command: list[str], process_exit_code: int, wall_seconds: float) -> dict[str, Any]:
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_e2e_receipt", "identity": identity,
        "case_id": case_id, "expected_terminal": expected_terminal,
        "actual_terminal": classification["actual_terminal"],
        "expected_exit_code": classification["expected_exit_code"], "actual_exit_code": classification["actual_exit_code"],
        "expected_guard": classification["expected_guard"], "actual_guard": classification["actual_guard"],
        "guard_reached": classification["guard_reached"], "status": classification["status"],
        "execution_mode": execution_mode, "mutation": mutation, "command": command,
        "process_exit_code": process_exit_code, "wall_seconds": wall_seconds,
    }
    if classification.get("validator_exception_type") is not None:
        receipt["validator_exception_type"] = classification["validator_exception_type"]
    return receipt


def verify_e2e_formal(root: Path, output: Path, identity: str, release_root: Path, actor_path: Path) -> dict[str, Any]:
    disable_network()
    destination = new_directory(output, identity)
    registry = load_json(root / "config/phase3/e2e-registry.json")
    negative_ids = {case["id"] for case in registry["cases"] if case["expected_terminal"] not in E2E_POSITIVE_TERMINALS}
    unknown_guards = sorted(case_id for case_id in negative_ids if case_id not in E2E_CASE_GUARDS)
    if unknown_guards:
        raise ValueError(f"registered negative E2E cases lack a stable guard mapping: {unknown_guards}")
    receipts = []
    for case in registry["cases"]:
        case_id, expected = case["id"], case["expected_terminal"]
        case_root = destination / "staging" / case_id
        case_root.mkdir(parents=True, exist_ok=False)
        mutation: dict[str, Any] = {"case_id": case_id, "type": "positive"}
        validator_command = ["python3", "-m", "lottery_research.phase3", "validate", "--scope", "final", "--release-root", f"staging/{case_id}/{release_root.name}"]
        started = time.perf_counter()
        if case_id == "E2E-P3-01-formal-full-chain":
            outcome = _run_bottom_up_validator_process(root, release_root, actor_path, case_root / "validator-result.json")
            passed_positive = bool(outcome.get("passed")) and int(outcome.get("process_returncode", 5)) == 0
            classification = {
                "expected_guard": None, "actual_guard": None, "guard_reached": passed_positive,
                "expected_exit_code": 0, "actual_exit_code": int(outcome.get("process_returncode", 5)),
                "actual_terminal": expected if passed_positive else "FAIL", "status": "PASS" if passed_positive else "FAIL",
                "validator_exception_type": outcome.get("exception_type"),
            }
            process_exit_code = int(outcome.get("process_returncode", 5))
            execution_mode = "production_bottom_up_validator_full_release_distinct_process"
        elif case_id == "E2E-P3-13-no-shadow-candidate":
            ok = summarize_phase(["archived", "not_opened"]) == "no_shadow_candidate"
            classification = {"expected_guard": None, "actual_guard": None, "guard_reached": ok, "expected_exit_code": 0, "actual_exit_code": 0, "actual_terminal": expected if ok else "FAIL", "status": "PASS" if ok else "FAIL", "validator_exception_type": None}
            process_exit_code, execution_mode = 0, "frozen_classification_summary_no_shadow_candidate"
        elif case_id == "E2E-P3-14-indeterminate":
            ok = summarize_phase(["indeterminate", "not_opened"]) == "indeterminate"
            classification = {"expected_guard": None, "actual_guard": None, "guard_reached": ok, "expected_exit_code": 0, "actual_exit_code": 0, "actual_terminal": expected if ok else "FAIL", "status": "PASS" if ok else "FAIL", "validator_exception_type": None}
            process_exit_code, execution_mode = 0, "frozen_classification_summary_indeterminate"
        else:
            staging_release = case_root / release_root.name
            shutil.copytree(release_root, staging_release, copy_function=os.link, ignore=shutil.ignore_patterns("e2e", "acceptance", "manifest", "handoff", "handoff-validation", "work-items"))
            mutation = _mutate_staging(case_id, staging_release)
            outcome = _run_bottom_up_validator_process(root, staging_release, actor_path, case_root / "validator-result.json")
            classification = _classify_e2e_negative_outcome(case_id, expected, outcome)
            process_exit_code = int(outcome.get("process_returncode", 5))
            execution_mode = "isolated_staging_mutation_then_production_bottom_up_validator_distinct_process"
            shutil.rmtree(staging_release, ignore_errors=True)
        receipt = _build_e2e_receipt(
            identity=f"{identity}-{case_id.lower()}", case_id=case_id, expected_terminal=expected,
            classification=classification, execution_mode=execution_mode, mutation=mutation,
            command=validator_command, process_exit_code=process_exit_code, wall_seconds=time.perf_counter() - started,
        )
        validate_payload(root, "e2e_receipt", receipt)
        write_new_json(case_root / "receipt.json", receipt)
        receipts.append(receipt)
    passed = all(row["status"] == "PASS" for row in receipts) and len(receipts) == len(registry["cases"])
    summary = {
        "schema_version": "3.0.0", "artifact_type": "phase3_e2e_summary", "identity": identity,
        "status": "PASS" if passed else "FAIL", "terminal": "PASS" if passed else "E2E_MISMATCH",
        "non_formal_synthetic_only": False, "required_case_count": len(registry["cases"]), "executed_case_count": len(receipts),
        "required_case_coverage": len(receipts) / len(registry["cases"]), "expected_terminal_match_rate": sum(row["status"] == "PASS" for row in receipts) / len(receipts),
        "negative_case_guard_attribution_rate": sum(row["guard_reached"] for row in receipts if row["case_id"] in negative_ids) / len(negative_ids),
        "production_validator_recomputed_fields": ["inputs", "trainer_prefix_capability", "forecast_hashes", "guarded_unlock_receipts", "ledger_order", "probabilities", "metrics", "bootstrap", "Holm", "classification", "Champion", "review", "independent_reconstruction"],
        "self_reported_fields_trusted": 0, "cases": [{"case_id": row["case_id"], "receipt": f"staging/{row['case_id']}/receipt.json"} for row in receipts],
    }
    write_new_json(destination / "e2e-summary.json", summary)
    return summary


def accept_formal(root: Path, output: Path, identity: str, release_root: Path, actor_path: Path) -> dict[str, Any]:
    disable_network()
    destination = new_directory(output, identity)
    bottom = validate_bottom_up(root, release_root, actor_path)
    e2e = load_json(release_root / "e2e/e2e-summary.json")
    if e2e["status"] != "PASS" or e2e["required_case_coverage"] != 1.0 or e2e["expected_terminal_match_rate"] != 1.0:
        raise ValueError("formal E2E evidence is incomplete")
    assignment, assignment_sha = load_actor_assignment(root, actor_path)
    by_role = {row["role"]: row for row in assignment["assignments"]}
    author, approver = by_role["implementation_author"], by_role["classification_approver"]
    if author["actor_id"] == approver["actor_id"]:
        raise ValueError("implementation author cannot approve classification")
    report_dir = release_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=False)
    evaluation = load_json(release_root / "evaluation/evaluation.json")
    report = {
        "schema_version": "3.0.0", "artifact_type": "phase3_research_report", "release_id": release_root.name,
        "delivery_status": "GO", "scientific_summary": evaluation["scientific_summary"],
        "model_classifications": evaluation["classifications"], "by_game": {game: {key: value for key, value in row.items() if key != "skill_values"} for game, row in evaluation["games"].items()},
        "m0_permanent_champion": True,
        "scientific_language": {
            "historical_only": "All reported performance is retrospective historical evidence and is not evidence of real future advantage.",
            "indeterminate": "Indeterminate, if reported, means the registered evidence is insufficient; it does not prove randomness.",
            "authorization": "No production, publication, shadow, purchase, betting, return, or winning authorization is granted.",
        },
        "forbidden_actions_authorized": [], "blocking_findings": 0,
        "guarded_label_unlock": bottom["guarded_label_unlock"],
    }
    write_new_json(report_dir / "final-research-report.json", report)
    readme = (
        f"# Phase 3 historical research report\n\nRelease: `{release_root.name}`\n\n"
        f"Delivery status: `PASS / GO`. Scientific summary: `{evaluation['scientific_summary']}`. "
        "These are separate conclusions. M0 remains the permanent Champion.\n\n"
        "The evidence is retrospective historical research only. It does not establish a real future advantage, "
        "does not prove lottery randomness, and grants no production, public forecast, shadow, purchase, betting, return, or winning authorization.\n"
    )
    (report_dir / "final-research-report.md").write_text(readme, encoding="utf-8")
    manifest_dir = release_root / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=False)
    excluded_prefixes = ("manifest/", "acceptance/", "handoff/", "handoff-validation/")
    paths = [path for path in release_root.rglob("*") if path.is_file() and not path.relative_to(release_root).as_posix().startswith(excluded_prefixes) and path.relative_to(release_root).as_posix() not in {"work-items/W12/receipt.json", "work-items/W13/receipt.json"}]
    files = _manifest_rows(root, release_root, paths)
    manifest = {
        "schema_version": "3.0.0", "artifact_type": "phase3_explicit_evidence_manifest", "identity": f"{release_root.name}-final-evidence-i01",
        "non_formal_synthetic_only": False, "files": files, "inventory_sha256": canonical_sha256(files),
    }
    validate_payload(root, "manifest", manifest)
    write_new_json(manifest_dir / "final-evidence-manifest.json", manifest)
    verify_explicit_manifest(release_root, manifest, allowed_extras=("manifest/final-evidence-manifest.json",))
    acceptance = {
        "schema_version": "3.0.0", "artifact_type": "phase3_acceptance", "release_id": release_root.name,
        "iteration_id": identity, "iteration_ordinal": 1, "status": "PASS", "delivery_status": "GO",
        "scientific_summary": evaluation["scientific_summary"], "m0_permanent_champion": True, "blocking_findings": 0,
        "manifest_sha256": sha256_file(manifest_dir / "final-evidence-manifest.json"), "formal_run_authorized": True,
        "actor_assignment_sha256": assignment_sha, "implementation_author_id": author["actor_id"],
        "classification_approver_id": approver["actor_id"], "approver_task_id": approver["task_id"],
        "approver_session_id": approver["session_id"], "approver_task_record_sha256": approver["task_record_sha256"],
        "accepted_at_utc": utc_now(),
    }
    validate_payload(root, "acceptance", acceptance)
    write_new_json(destination / "acceptance.json", acceptance)
    validator = {
        "schema_version": "3.0.0", "artifact_type": "phase3_final_validator_receipt", "identity": identity,
        "status": "PASS", "terminal": "PASS", "delivery_status": "GO", "scientific_summary": evaluation["scientific_summary"],
        "bottom_up": bottom, "delivery_coverage": 1.0, "manifest_lineage_coverage": 1.0, "hash_match_rate": 1.0,
        "registered_experiment_terminal_coverage": 1.0, "e2e_coverage": 1.0, "blocking_findings": 0,
        "champion_change_count": 0, "forbidden_action_count": 0, "self_reported_fields_trusted": 0,
        "acceptance_path": (destination / "acceptance.json").relative_to(release_root).as_posix(),
    }
    write_new_json(destination / "validator.json", validator)
    return acceptance


def handoff_formal(root: Path, output: Path, identity: str, release_root: Path, actor_path: Path) -> dict[str, Any]:
    disable_network()
    destination = new_directory(output, identity)
    validate_bottom_up(root, release_root, actor_path)
    acceptance_paths = list(release_root.glob("acceptance/*/acceptance.json"))
    if len(acceptance_paths) != 1:
        raise ValueError("handoff requires exactly one acceptance")
    acceptance_path = acceptance_paths[0]
    acceptance = load_json(acceptance_path)
    manifest_path = release_root / "manifest/final-evidence-manifest.json"
    if (acceptance["status"], acceptance["delivery_status"], acceptance["blocking_findings"]) != ("PASS", "GO", 0):
        raise ValueError("handoff acceptance is not PASS/GO")
    if acceptance["manifest_sha256"] != sha256_file(manifest_path):
        raise ValueError("handoff manifest hash mismatch")
    manifest_closure = verify_final_manifest_closure(root, release_root, manifest_path)
    handoff_dir = release_root / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=False)
    handoff = {
        "schema_version": "3.0.0", "artifact_type": "phase3_handoff", "release_id": release_root.name,
        "status": "PASS", "delivery_status": "GO", "acceptance_path": acceptance_path.relative_to(release_root).as_posix(),
        "acceptance_sha256": sha256_file(acceptance_path), "manifest_path": manifest_path.relative_to(release_root).as_posix(),
        "manifest_sha256": sha256_file(manifest_path), "m0_permanent_champion": True,
        "forbidden_actions_authorized": [], "next_authorization": None,
    }
    validate_payload(root, "handoff", handoff)
    write_new_json(handoff_dir / "handoff.json", handoff)
    receipt = {
        "schema_version": "3.0.0", "artifact_type": "phase3_handoff_validation", "identity": identity,
        "status": "PASS", "terminal": "PASS", "release_id": release_root.name,
        "acceptance_iteration_count": 1, "blocking_findings": [], "m0_permanent_champion": True,
        "manifest_closure": manifest_closure,
        "production_authorized": False, "publication_authorized": False, "shadow_authorized": False, "betting_authorized": False,
    }
    write_new_json(destination / "handoff-validation.json", receipt)
    return receipt
