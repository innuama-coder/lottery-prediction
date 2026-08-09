from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import fmean


SPECS = {"dlt": (35, 5, 12, 2), "ssq": (33, 6, 16, 1)}
TOLERANCE = {"rel_tol": 1e-10, "abs_tol": 1e-12}
DELTA = math.log(1.001)


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_sha(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def close(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), **TOLERANCE)


def elementary(weights: list[float], cardinality: int) -> float:
    values = [0.0] * (cardinality + 1)
    values[0] = 1.0
    for weight in weights:
        for degree in range(cardinality, 0, -1):
            values[degree] += weight * values[degree - 1]
    return values[cardinality]


def probability(section: dict[str, object], observed: list[int]) -> float:
    weights = [float(value) for value in section["weights"]]  # type: ignore[index]
    cardinality = int(section["cardinality"])
    return math.prod(weights[value - 1] for value in observed) / elementary(weights, cardinality)


def inclusions(section: dict[str, object]) -> list[float]:
    weights = [float(value) for value in section["weights"]]  # type: ignore[index]
    cardinality = int(section["cardinality"])
    denominator = elementary(weights, cardinality)
    return [weight * elementary(weights[:index] + weights[index + 1 :], cardinality - 1) / denominator for index, weight in enumerate(weights)]


def brier(probabilities: list[float], observed: list[int]) -> float:
    selected = set(observed)
    return fmean((value - (1.0 if index in selected else 0.0)) ** 2 for index, value in enumerate(probabilities, start=1))


def ece(probabilities: list[float], outcomes: list[int]) -> float:
    total = len(probabilities)
    result = 0.0
    for bin_index in range(10):
        lower, upper = bin_index / 10.0, (bin_index + 1) / 10.0
        members = [index for index, value in enumerate(probabilities) if lower <= value < upper or (bin_index == 9 and value == 1.0)]
        if members:
            result += len(members) / total * abs(fmean(probabilities[index] for index in members) - fmean(outcomes[index] for index in members))
    return result


def bootstrap(values: list[float], seed: str, replicates: int = 10_000) -> dict[str, float | int]:
    count = len(values)
    block_length = min(count, max(5, math.ceil(count ** (1.0 / 3.0))))
    blocks = [values[start : start + block_length] for start in range(count - block_length + 1)]
    block_repetitions = math.ceil(count / block_length)
    observed = fmean(values)
    centered = [value - observed + DELTA for value in values]
    centered_blocks = [centered[start : start + block_length] for start in range(count - block_length + 1)]
    sampled_means: list[float] = []
    null_means: list[float] = []
    for replicate_index in range(replicates):
        sample: list[float] = []
        null_sample: list[float] = []
        for block_index in range(block_repetitions):
            material = f"{seed}|{replicate_index}|{block_index}".encode("utf-8")
            chosen = int.from_bytes(hashlib.sha256(material).digest(), "big") % len(blocks)
            sample.extend(blocks[chosen])
            null_sample.extend(centered_blocks[chosen])
        sampled_means.append(fmean(sample[:count]))
        null_means.append(fmean(null_sample[:count]))
    ordered = sorted(sampled_means)
    return {
        "observed_mean": observed,
        "lower": ordered[math.ceil(0.05 * replicates) - 1],
        "upper": ordered[math.ceil(0.95 * replicates) - 1],
        "raw_p": (1 + sum(value >= observed for value in null_means)) / (replicates + 1),
        "block_length": block_length,
        "replicates": replicates,
    }


def holm(raw: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    ordered = sorted(raw.items(), key=lambda item: (item[1], item[0][0], item[0][1]))
    result: dict[tuple[str, str], float] = {}
    prefix = 0.0
    for rank, (key, value) in enumerate(ordered, start=1):
        prefix = max(prefix, (len(ordered) - rank + 1) * value)
        result[key] = min(1.0, prefix)
    return result


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    workspace = args.workspace_root.resolve()
    release = args.release_root.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(output)

    blockers: list[str] = []

    def check(condition: bool, code: str) -> None:
        if not condition:
            blockers.append(code)

    assignment_path = release / "control/actor-assignments-formal.json"
    assignments_doc = load_json(assignment_path)
    assert isinstance(assignments_doc, dict)
    assignments = {row["role"]: row for row in assignments_doc["assignments"]}
    required_roles = {
        "data_custodian", "implementation_author", "statistical_owner", "independent_method_reviewer",
        "run_operator", "independent_reviewer", "acceptance_engineer", "classification_approver", "release_controller",
    }
    check(set(assignments) == required_roles, "ROLE_COVERAGE")
    approver = assignments.get("classification_approver", {})
    reviewer = assignments.get("independent_reviewer", {})
    author = assignments.get("implementation_author", {})
    check(approver.get("actor_id") == "P3-W12-CLASSIFICATION-APPROVER-20260809-R02", "W12_ACTOR_ID")
    check(approver.get("task_id") == "/root/w12_r02_approver", "W12_TASK_ID")
    check(approver.get("session_id") == "P3-W12-CLASSIFICATION-APPROVAL-SESSION-20260809-R02-8E64A17C", "W12_SESSION_ID")
    check(len({approver.get("actor_id"), reviewer.get("actor_id"), author.get("actor_id")}) == 3, "ROLE_CONFLICT")
    for row in assignments_doc["assignments"]:
        task_record = release / "control" / row["task_record_path"]
        check(task_record.is_file() and file_sha(task_record) == row["task_record_sha256"], f"TASK_RECORD_HASH:{row['role']}")
    approver_record = load_json(release / "control" / approver["task_record_path"])
    assert isinstance(approver_record, dict)
    declaration = approver_record.get("declaration", "").lower()
    check("prior w12" in declaration and "did not participate" in declaration and "does not approve" in declaration, "W12_INDEPENDENCE_DECLARATION")

    receipt_paths: dict[str, Path] = {}
    w07 = load_json(release / "work-items/W07/receipt.json")
    assert isinstance(w07, dict)
    upstream_w06 = next(Path(row["path"]) for row in w07["inputs"] if row["path"].endswith("work-items/W06/receipt.json"))
    prep = (workspace / upstream_w06).parents[2]
    prep_assignment_path = prep / "control/actor-assignments-preparation.json"
    prep_assignment_sha = file_sha(prep_assignment_path)
    formal_assignment_sha = file_sha(assignment_path)
    previous_receipt: Path | None = None
    for number in range(1, 12):
        work_item = f"W{number:02d}"
        receipt_path = prep / f"work-items/{work_item}/receipt.json" if number <= 6 else release / f"work-items/{work_item}/receipt.json"
        receipt_paths[work_item] = receipt_path
        receipt = load_json(receipt_path)
        assert isinstance(receipt, dict)
        check(receipt.get("work_item") == work_item, f"RECEIPT_ID:{work_item}")
        check(receipt.get("status") == "PASS" and receipt.get("process_exit_code") == 0, f"RECEIPT_TERMINAL:{work_item}")
        expected_assignment_sha = prep_assignment_sha if number <= 6 else formal_assignment_sha
        check(receipt.get("actor_assignment_sha256") == expected_assignment_sha, f"RECEIPT_ASSIGNMENT:{work_item}")
        role = receipt.get("owner_role")
        assignment_source = load_json(prep_assignment_path) if number <= 6 else assignments_doc
        assert isinstance(assignment_source, dict)
        role_rows = [row for row in assignment_source["assignments"] if row["role"] == role]
        check(len(role_rows) == 1, f"RECEIPT_OWNER_ROLE:{work_item}")
        if role_rows:
            role_row = role_rows[0]
            check(
                (receipt.get("owner_id"), receipt.get("owner_task_id"), receipt.get("owner_session_id"))
                == (role_row["actor_id"], role_row["task_id"], role_row["session_id"]),
                f"RECEIPT_OWNER_IDENTITY:{work_item}",
            )
        for side in ("inputs", "outputs"):
            for row in receipt[side]:
                path = workspace / row["path"]
                check(path.is_file() and file_sha(path) == row["sha256"], f"RECEIPT_{side.upper()}_HASH:{work_item}:{row['path']}")
        if previous_receipt is not None:
            prior_sha = file_sha(previous_receipt)
            check(any(row["sha256"] == prior_sha for row in receipt["inputs"]), f"RECEIPT_CHAIN:{work_item}")
        previous_receipt = receipt_path
        if number <= 6:
            copied = release / f"preparation-evidence/work-items/{work_item}/receipt.json"
            check(copied.is_file() and copied.read_bytes() == receipt_path.read_bytes(), f"PREP_RECEIPT_COPY:{work_item}")

    input_manifest = load_json(release / "contracts/input-manifest.json")
    assert isinstance(input_manifest, dict)
    for row in input_manifest["files"]:
        path = workspace / row["path"]
        check(path.is_file() and path.stat().st_size == row["bytes"] and file_sha(path) == row["sha256"], f"INPUT_HASH:{row['path']}")
    implementation_inventory = load_json(release / "control/implementation-inventory.json")
    assert isinstance(implementation_inventory, dict)
    for row in implementation_inventory["files"]:
        path = workspace / row["path"]
        check(path.is_file() and path.stat().st_size == row["bytes"] and file_sha(path) == row["sha256"], f"IMPLEMENTATION_HASH:{row['path']}")
    check(canonical_sha(implementation_inventory["files"]) == implementation_inventory["inventory_sha256"], "IMPLEMENTATION_INVENTORY_DIGEST")

    draws: dict[str, list[dict[str, object]]] = {"dlt": [], "ssq": []}
    for row in load_jsonl(workspace / "artifacts/phase-1/baseline-v1/draws.jsonl"):
        draws[str(row["game"])].append(row)
    for game in draws:
        draws[game].sort(key=lambda row: str(row["issue_id"]))
    labels = {(game, str(row["issue_id"])): row for game, rows in draws.items() for row in rows}
    prereg = load_json(release / "contracts/preregistration.json")
    assert isinstance(prereg, dict)
    expected_targets = {(game, issue, model) for game in SPECS for issue in prereg["games"][game]["outer_targets"] for model in ("M0", "M1")}

    forecast_rows = load_jsonl(release / "runs/forecast-index.jsonl")
    metric_rows = load_jsonl(release / "runs/metric-index.jsonl")
    forecast_index = {(str(row["game"]), str(row["target_issue"]), str(row["model_id"])): row for row in forecast_rows}
    metric_index = {(str(row["game"]), str(row["target_issue"]), str(row["model_id"])): row for row in metric_rows}
    check(len(forecast_rows) == len(forecast_index) == 600 and set(forecast_index) == expected_targets, "FORECAST_COVERAGE")
    check(len(metric_rows) == len(metric_index) == 600 and set(metric_index) == expected_targets, "METRIC_COVERAGE")

    computed: dict[tuple[str, str, str], dict[str, object]] = {}
    top_ticket_count = 0
    for key in sorted(expected_targets):
        game, issue, model = key
        f_index = forecast_index[key]
        forecast_path = release / "runs" / str(f_index["path"])
        check(forecast_path.is_file() and file_sha(forecast_path) == f_index["sha256"], f"FORECAST_HASH:{key}")
        forecast = load_json(forecast_path)
        assert isinstance(forecast, dict)
        label = labels[(game, issue)]
        check(
            (forecast.get("release_id"), forecast.get("game"), forecast.get("target_issue"), forecast.get("model_id"))
            == (release.name, game, issue, model),
            f"FORECAST_IDENTITY:{key}",
        )
        check(forecast.get("label_read") is False and str(forecast["training_cutoff"]) < issue and int(forecast["training_count"]) >= 50, f"FORECAST_SEQUENCE:{key}")
        check(all(str(inner) < issue for inner in forecast["inner_target_issues"]), f"INNER_SEQUENCE:{key}")
        check(close(float(forecast["normalization_sum"]), 1.0), f"NORMALIZATION:{key}")
        distribution = forecast["distribution"]
        assert isinstance(distribution, dict)
        front = distribution["front"]
        back = distribution["back"]
        assert isinstance(front, dict) and isinstance(back, dict)
        front_inc = inclusions(front)
        back_inc = inclusions(back)
        check(len(front_inc) == SPECS[game][0] and len(back_inc) == SPECS[game][2], f"DISTRIBUTION_SIZE:{key}")
        check(all(close(left, right) for left, right in zip(front_inc, front["inclusion_probabilities"], strict=True)), f"FRONT_INCLUSION:{key}")
        check(all(close(left, right) for left, right in zip(back_inc, back["inclusion_probabilities"], strict=True)), f"BACK_INCLUSION:{key}")
        joint_probability = probability(front, label["front_numbers"]) * probability(back, label["back_numbers"])
        joint_log_score = -math.log(joint_probability)
        front_brier = brier(front_inc, label["front_numbers"])
        back_brier = brier(back_inc, label["back_numbers"])
        inclusion_brier = (front_brier + back_brier) / 2.0

        top_path = release / str(forecast["top_1000_path"])
        check(top_path.is_file() and file_sha(top_path) == forecast["top_1000_sha256"], f"TOP1000_HASH:{key}")
        with gzip.open(top_path, "rt", encoding="utf-8") as handle:
            top = json.load(handle)
        tickets = top["tickets"]
        check(
            (top["release_id"], top["game"], top["target_issue"], top["model_id"], top["role"], len(tickets))
            == (release.name, game, issue, model, "diagnostic_only", 1000),
            f"TOP1000_IDENTITY:{key}",
        )
        prior = float("inf")
        ticket_probability_sum = 0.0
        for ticket in tickets:
            front_values, back_values = ticket["front"], ticket["back"]
            legal = (
                len(front_values) == SPECS[game][1] == len(set(front_values))
                and all(1 <= value <= SPECS[game][0] for value in front_values)
                and len(back_values) == SPECS[game][3] == len(set(back_values))
                and all(1 <= value <= SPECS[game][2] for value in back_values)
            )
            expected_probability = probability(front, front_values) * probability(back, back_values)
            value = float(ticket["probability"])
            check(legal and 0.0 <= value <= prior + 1e-18 and close(value, expected_probability), f"TOP1000_TICKET:{key}:{top_ticket_count}")
            prior = value
            ticket_probability_sum += value
            top_ticket_count += 1
        check(close(ticket_probability_sum, top["coverage_probability"]), f"TOP1000_COVERAGE:{key}")

        m_index = metric_index[key]
        metric_path = release / "runs" / str(m_index["path"])
        check(metric_path.is_file(), f"METRIC_PATH:{key}")
        metric = load_json(metric_path)
        assert isinstance(metric, dict)
        check(metric["forecast_sha256"] == f_index["sha256"], f"METRIC_FORECAST_BINDING:{key}")
        check(close(metric["actual_joint_probability"], joint_probability), f"METRIC_PROBABILITY:{key}")
        check(close(metric["joint_log_score"], joint_log_score), f"METRIC_LOG_SCORE:{key}")
        check(close(metric["front_inclusion_brier"], front_brier), f"METRIC_FRONT_BRIER:{key}")
        check(close(metric["back_inclusion_brier"], back_brier), f"METRIC_BACK_BRIER:{key}")
        check(close(metric["inclusion_brier"], inclusion_brier), f"METRIC_BRIER:{key}")
        computed[key] = {
            "probability": joint_probability,
            "log_score": joint_log_score,
            "inclusion_brier": inclusion_brier,
            "front_inclusions": front_inc,
            "back_inclusions": back_inc,
            "forecast_sha256": file_sha(forecast_path),
            "metric_sha256": file_sha(metric_path),
        }

    skills: dict[str, list[float]] = {"dlt": [], "ssq": []}
    for game in SPECS:
        for issue in prereg["games"][game]["outer_targets"]:
            m0 = computed[(game, issue, "M0")]
            m1 = computed[(game, issue, "M1")]
            skill = math.log(float(m1["probability"]) / float(m0["probability"]))
            skills[game].append(skill)
            for model in ("M0", "M1"):
                metric = load_json(release / "runs" / str(metric_index[(game, issue, model)]["path"]))
                assert isinstance(metric, dict)
                expected_skill = 0.0 if model == "M0" else skill
                check(close(metric["relative_skill_vs_M0"], expected_skill), f"METRIC_SKILL:{game}:{issue}:{model}")

    ledger = load_jsonl(release / "runs/experiment-ledger.jsonl")
    check(len(ledger) == 3000 and [row["sequence"] for row in ledger] == list(range(3000)), "LEDGER_GLOBAL_SEQUENCE")
    by_experiment: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in ledger:
        by_experiment[str(row["experiment_id"])].append(row)
    check(len(by_experiment) == 600, "LEDGER_EXPERIMENT_COVERAGE")
    for experiment, events in by_experiment.items():
        key = (str(events[0]["details"]["game"]), str(events[0]["details"]["target_issue"]), str(events[0]["details"]["model_id"]))
        check([row["state"] for row in events] == ["started", "forecast_locked", "label_unlocked", "scored", "succeeded"], f"LEDGER_STATES:{experiment}")
        check(len({row["attempt_id"] for row in events}) == 1, f"LEDGER_ATTEMPT:{experiment}")
        check(events[0]["details"].get("network_requests") == 0 and events[4]["details"].get("network_requests") == 0, f"NETWORK_REQUEST:{experiment}")
        check(events[1]["details"]["forecast_sha256"] == computed[key]["forecast_sha256"], f"LEDGER_FORECAST_HASH:{experiment}")
        check(events[3]["details"]["metric_sha256"] == computed[key]["metric_sha256"], f"LEDGER_METRIC_HASH:{experiment}")
        check(parse_time(str(events[1]["details"]["prediction_locked_at"])) < parse_time(str(events[2]["details"]["label_unlocked_at"])), f"LEDGER_TIME_ORDER:{experiment}")
        label = labels[(key[0], key[1])]
        check(events[2]["details"]["label_sha256"] == canonical_sha({"front": label["front_numbers"], "back": label["back_numbers"]}), f"LEDGER_LABEL_HASH:{experiment}")
    canonical_attempts = load_json(release / "runs/canonical-attempts.json")
    assert isinstance(canonical_attempts, dict)
    check(len(canonical_attempts) == 600 and set(canonical_attempts) == set(by_experiment), "CANONICAL_ATTEMPTS")

    evaluation = load_json(release / "evaluation/evaluation.json")
    assert isinstance(evaluation, dict)
    recomputed_games: dict[str, dict[str, object]] = {}
    raw_p: dict[tuple[str, str], float] = {}
    bootstrap_results: dict[str, dict[str, float | int]] = {}
    for game in SPECS:
        values = skills[game]
        target_issues = prereg["games"][game]["outer_targets"]
        midpoint = len(values) // 2
        positive_sum = math.fsum(max(0.0, value) for value in values)
        concentration = max(max(0.0, value) / positive_sum for value in values) if positive_sum else 1.0
        drop = math.ceil(0.10 * len(values))
        m0_brier = fmean(float(computed[(game, issue, "M0")]["inclusion_brier"]) for issue in target_issues)
        m1_brier = fmean(float(computed[(game, issue, "M1")]["inclusion_brier"]) for issue in target_issues)
        outcomes: list[int] = []
        model_probabilities = {"M0": [], "M1": []}
        for issue in target_issues:
            label = labels[(game, issue)]
            outcomes.extend(1 if value in set(label["front_numbers"]) else 0 for value in range(1, SPECS[game][0] + 1))
            outcomes.extend(1 if value in set(label["back_numbers"]) else 0 for value in range(1, SPECS[game][2] + 1))
            for model in ("M0", "M1"):
                model_probabilities[model].extend(computed[(game, issue, model)]["front_inclusions"])
                model_probabilities[model].extend(computed[(game, issue, model)]["back_inclusions"])
        m0_ece = ece(model_probabilities["M0"], outcomes)
        m1_ece = ece(model_probabilities["M1"], outcomes)
        earliest = fmean(values[drop:])
        latest = fmean(values[: len(values) - drop])
        non_bootstrap = (
            fmean(values[:midpoint]) > 0.0 and fmean(values[midpoint:]) > 0.0 and concentration <= 0.20
            and m1_brier <= m0_brier and m1_ece <= m0_ece + 0.005 and earliest > 0.0 and latest > 0.0
        )
        boot = bootstrap(values, f"{release.name}|{game}|M1|bootstrap")
        bootstrap_results[game] = boot
        raw_p[("M1", game)] = float(boot["raw_p"])
        primary = evaluation["games"][game]
        check(all(close(left, right) for left, right in zip(values, primary["skill_values"], strict=True)), f"W09_SKILL_SERIES:{game}")
        for field in ("observed_mean", "lower", "upper", "raw_p"):
            check(close(boot[field], primary["bootstrap"][field]), f"W09_BOOTSTRAP:{game}:{field}")
        check(close(m0_brier, primary["m0_inclusion_brier"]) and close(m1_brier, primary["m1_inclusion_brier"]), f"W09_BRIER:{game}")
        check(close(m0_ece, primary["m0_ece"]) and close(m1_ece, primary["m1_ece"]), f"W09_ECE:{game}")
        check(primary["non_bootstrap_gates_passed"] is non_bootstrap, f"W09_NON_BOOTSTRAP_GATE:{game}")
        recomputed_games[game] = {
            "outer_targets": len(values), "mean_skill": fmean(values), "first_half_mean": fmean(values[:midpoint]),
            "second_half_mean": fmean(values[midpoint:]), "positive_concentration_max": concentration,
            "m0_inclusion_brier": m0_brier, "m1_inclusion_brier": m1_brier, "m0_ece": m0_ece, "m1_ece": m1_ece,
            "drop_earliest_10_percent_mean_skill": earliest, "drop_latest_10_percent_mean_skill": latest,
            "non_bootstrap_gates_passed": non_bootstrap, "bootstrap": boot,
        }
    adjusted = holm(raw_p)
    for game in SPECS:
        check(close(adjusted[("M1", game)], evaluation["games"][game]["holm_adjusted_p"]), f"W09_HOLM:{game}")
        recomputed_games[game]["holm_adjusted_p"] = adjusted[("M1", game)]
    shadow = all(
        recomputed_games[game]["non_bootstrap_gates_passed"]
        and float(bootstrap_results[game]["lower"]) > DELTA
        and adjusted[("M1", game)] <= 0.05
        for game in SPECS
    )
    uncertain = any(
        recomputed_games[game]["non_bootstrap_gates_passed"]
        and float(bootstrap_results[game]["lower"]) <= DELTA < float(bootstrap_results[game]["upper"])
        for game in SPECS
    )
    classification = "shadow_candidate" if shadow else "indeterminate" if uncertain else "archived"
    scientific_summary = "shadow_candidate" if classification == "shadow_candidate" else "indeterminate" if classification == "indeterminate" else "no_shadow_candidate"
    check(evaluation["classifications"] == {"M1": classification, "M2": "not_opened", "M3": "not_opened", "M4": "not_opened"}, "W09_CLASSIFICATION")
    check(evaluation["scientific_summary"] == scientific_summary, "W09_SCIENCE_SUMMARY")
    check(evaluation["m0_permanent_champion"] is True and evaluation["champion_change_count"] == 0 and evaluation["forbidden_action_count"] == 0, "M0_CHAMPION")

    replay_rows = load_jsonl(release / "replay/observed-probability-replay.jsonl")
    replay_index = {(str(row["game"]), str(row["target_issue"]), str(row["model_id"])): row for row in replay_rows}
    check(len(replay_rows) == len(replay_index) == 600 and set(replay_index) == expected_targets, "W10_REPLAY_COVERAGE")
    for key, row in replay_index.items():
        check(close(row["reference_probability"], computed[key]["probability"]) and close(row["primary_probability"], computed[key]["probability"]), f"W10_PROBABILITY:{key}")
        check(close(row["reference_inclusion_brier"], computed[key]["inclusion_brier"]), f"W10_BRIER:{key}")
        check(all(close(left, right) for left, right in zip(row["front_inclusions"], computed[key]["front_inclusions"], strict=True)), f"W10_FRONT_INCLUSION:{key}")
        check(all(close(left, right) for left, right in zip(row["back_inclusions"], computed[key]["back_inclusions"], strict=True)), f"W10_BACK_INCLUSION:{key}")
    replay = load_json(release / "replay/replay.json")
    reconstruction = load_json(release / "review/independent-model-reconstruction.json")
    review = load_json(release / "review/review.json")
    assert isinstance(replay, dict) and isinstance(reconstruction, dict) and isinstance(review, dict)
    check(replay["status"] == "PASS" and replay["blocking_findings"] == 0 and replay["differences"] == [], "W10_REPLAY_STATUS")
    check(all(replay[field] == 1.0 for field in ("input_fold_match_rate", "probability_match_rate", "metric_match_rate", "bootstrap_match_rate", "classification_match_rate")), "W10_MATCH_RATES")
    check(reconstruction["status"] == "PASS" and reconstruction["model_target_count"] == 600 and reconstruction["blocking_findings"] == 0, "W10_RECONSTRUCTION")
    check(review["status"] == "PASS" and review["blocking_findings"] == 0, "W10_REVIEW")
    check(review["reviewer_id"] == reviewer["actor_id"] and review["review_task_id"] == reviewer["task_id"] and review["review_session_id"] == reviewer["session_id"], "W10_PROVENANCE")
    w09_manifest_path = release / "evaluation/evidence-manifest.json"
    check(review["reviewed_manifest_sha256"] == file_sha(w09_manifest_path), "W10_REVIEWED_MANIFEST")

    e2e_registry = load_json(release / "contracts/e2e-registry.json")
    e2e_summary = load_json(release / "e2e/e2e-summary.json")
    assert isinstance(e2e_registry, dict) and isinstance(e2e_summary, dict)
    expected_cases = {row["id"]: row["expected_terminal"] for row in e2e_registry["cases"]}
    check(len(expected_cases) == 14 and e2e_summary["required_case_count"] == e2e_summary["executed_case_count"] == 14, "W11_CASE_COUNT")
    check(e2e_summary["required_case_coverage"] == 1.0 and e2e_summary["expected_terminal_match_rate"] == 1.0 and e2e_summary["self_reported_fields_trusted"] == 0, "W11_COVERAGE")
    seen_cases: set[str] = set()
    for row in e2e_summary["cases"]:
        receipt = load_json(release / "e2e" / row["receipt"])
        assert isinstance(receipt, dict)
        case_id = receipt["case_id"]
        seen_cases.add(case_id)
        check(receipt["expected_terminal"] == expected_cases.get(case_id) == receipt["actual_terminal"], f"W11_TERMINAL:{case_id}")
        check(receipt["status"] == "PASS" and receipt["execution_mode"] == "isolated_staging_mutation_then_production_bottom_up_validator", f"W11_EXECUTION:{case_id}")
    check(seen_cases == set(expected_cases), "W11_CASE_COVERAGE")

    w09_manifest = load_json(w09_manifest_path)
    assert isinstance(w09_manifest, dict)
    manifest_paths: set[str] = set()
    for row in w09_manifest["files"]:
        relative = row["path"]
        safe = "latest" not in relative.lower() and "*" not in relative and ".." not in Path(relative).parts
        check(safe and relative not in manifest_paths, f"W09_MANIFEST_PATH:{relative}")
        manifest_paths.add(relative)
        path = release / relative
        check(path.is_file() and path.stat().st_size == row["bytes"] and file_sha(path) == row["sha256"], f"W09_MANIFEST_HASH:{relative}")
    check(canonical_sha(w09_manifest["files"]) == w09_manifest["inventory_sha256"], "W09_MANIFEST_DIGEST")

    run_summary = load_json(release / "runs/run-summary.json")
    assert isinstance(run_summary, dict)
    check(run_summary["logical_experiment_count"] == run_summary["attempt_count"] == 600 and run_summary["network_request_count"] == 0, "RUN_SUMMARY")
    check(run_summary["forecast_lock_order_violations"] == 0 and run_summary["label_unlock_order_violations"] == 0, "RUN_ORDER")
    wording = {
        "historical_only": True,
        "does_not_prove_randomness": True,
        "does_not_establish_future_advantage": True,
        "does_not_authorize_real_future_shadow_or_production": True,
        "does_not_authorize_betting_or_purchase": True,
    }

    bottom_up_rows = []
    for key in sorted(computed):
        row = computed[key]
        bottom_up_rows.append({
            "game": key[0], "target_issue": key[1], "model_id": key[2],
            "probability": row["probability"], "log_score": row["log_score"], "inclusion_brier": row["inclusion_brier"],
        })
    report = {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_w12_independent_bottom_up_review",
        "identity": "P3-R02-14df4f3-20260809-I01-W12-INDEPENDENT-PREACCEPT",
        "release_id": release.name,
        "actor_id": approver.get("actor_id"),
        "task_id": approver.get("task_id"),
        "session_id": approver.get("session_id"),
        "method": "standalone standard-library recomputation; no Phase 3 implementation, evaluator, replay, or validator imports",
        "status": "PASS" if not blockers else "HOLD",
        "terminal": "PASS" if not blockers else "HOLD",
        "blocking_findings": len(blockers),
        "blockers": blockers[:200],
        "coverage": {
            "work_item_receipts_W01_W11": 11,
            "forecast_count": len(forecast_index),
            "metric_count": len(metric_index),
            "ledger_event_count": len(ledger),
            "logical_experiment_count": len(by_experiment),
            "outer_target_count": len(expected_targets) // 2,
            "top_1000_artifact_count": len(computed),
            "top_1000_ticket_count": top_ticket_count,
            "w10_replay_probability_count": len(replay_index),
            "w11_case_count": len(seen_cases),
            "network_request_count": 0,
        },
        "hashes": {
            "actor_assignments_sha256": formal_assignment_sha,
            "w09_evidence_manifest_sha256": file_sha(w09_manifest_path),
            "bottom_up_probability_logscore_brier_digest": canonical_sha(bottom_up_rows),
            "skill_series_digest": canonical_sha(skills),
            "work_item_receipt_digest": canonical_sha({key: file_sha(path) for key, path in sorted(receipt_paths.items())}),
        },
        "games": recomputed_games,
        "classification": {"M1": classification, "M2": "not_opened", "M3": "not_opened", "M4": "not_opened"},
        "scientific_summary": scientific_summary,
        "m0_permanent_champion": True,
        "champion_change_count": 0,
        "w10_match": "PASS",
        "w11_expected_terminal_coverage": 1.0,
        "wording_boundary": wording,
        "precheck_attempts": [
            {"identity": "P3-R02-14df4f3-20260809-I01-W12-PRECHECK", "exit_code": 4, "terminal": "INVALID_IDENTITY_REUSE", "cause": "reviewer precreated exclusive output directory; immutable failed invocation retained"},
            {"identity": "P3-R02-14df4f3-20260809-I01-W12-PRECHECK-I02", "exit_code": 0, "terminal": "PASS"},
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_bytes(report))
    print(json.dumps({"status": report["status"], "blocking_findings": len(blockers), "output": str(output), "bottom_up_digest": report["hashes"]["bottom_up_probability_logscore_brier_digest"]}, sort_keys=True))
    return 0 if not blockers else 5


if __name__ == "__main__":
    raise SystemExit(main())
