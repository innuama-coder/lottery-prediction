from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean


ROOT = Path(__file__).resolve().parents[2]
SPECS = {"dlt": (35, 5, 12, 2), "ssq": (33, 6, 16, 1)}
LAMBDAS = (1.0, 5.0, 20.0, 100.0)


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def elementary(weights: list[float], cardinality: int) -> float:
    values = [0.0] * (cardinality + 1)
    values[0] = 1.0
    for weight in weights:
        for degree in range(cardinality, 0, -1):
            values[degree] += weight * values[degree - 1]
    return values[cardinality]


def weights(draws: list[list[int]], size: int, cardinality: int, shrinkage: float) -> list[float]:
    counts = [0] * size
    for draw in draws:
        for value in draw:
            counts[value - 1] += 1
    expected = len(draws) * cardinality / size
    scale = max(shrinkage + expected, 1.0)
    theta = [math.log((count + shrinkage) / (expected + shrinkage)) / scale for count in counts]
    mean = math.fsum(theta) / size
    centered = [value - mean for value in theta]
    offset = max(centered)
    return [math.exp(value - offset) for value in centered]


def probability(model_weights: list[float], cardinality: int, observed: list[int]) -> float:
    return math.prod(model_weights[value - 1] for value in observed) / elementary(model_weights, cardinality)


def select_lambda(prefix: list[dict[str, object]], game: str) -> float:
    front_size, front_k, back_size, back_k = SPECS[game]
    candidates = []
    base_size = len(prefix) - 20
    for shrinkage in LAMBDAS:
        scores = []
        for offset, target in enumerate(prefix[-20:]):
            training = prefix[:base_size + offset]
            front = weights([row["front_numbers"] for row in training], front_size, front_k, shrinkage)  # type: ignore[list-item]
            back = weights([row["back_numbers"] for row in training], back_size, back_k, shrinkage)  # type: ignore[list-item]
            actual = probability(front, front_k, target["front_numbers"]) * probability(back, back_k, target["back_numbers"])  # type: ignore[arg-type]
            scores.append(-math.log(actual))
        candidates.append((fmean(scores), shrinkage))
    best = min(score for score, _ in candidates)
    return max(value for score, value in candidates if math.isclose(score, best, rel_tol=1e-10, abs_tol=1e-12))


def guarded_unlock_recomputation(
    release: Path,
    forecast_index: dict[tuple[str, str, str], dict[str, object]],
    metric_index: dict[tuple[str, str, str], dict[str, object]],
) -> dict[str, object]:
    with (release / "runs/experiment-ledger.jsonl").open(encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]
    sequences: dict[tuple[str, str], list[dict[str, object]]] = {}
    positions: dict[tuple[str, str], list[int]] = {}
    for position, event in enumerate(events):
        key = (event["experiment_id"], event["attempt_id"])
        sequences.setdefault(key, []).append(event)
        positions.setdefault(key, []).append(position)
    input_manifest = json.loads((release / "contracts/input-manifest.json").read_text(encoding="utf-8"))
    source = next(row for row in input_manifest["files"] if row["role"] == "phase1_draws")
    expected_store = f"phase3-label-store-v1:{source['sha256']}"
    mismatches: list[str] = []
    receipt_hashes = set()
    run_ids = set()
    for (game, target, model_id), row in forecast_index.items():
        expected_index_path = f"forecasts/{game}/{target}/{model_id}.json"
        if row.get("path") != expected_index_path:
            mismatches.append(f"forecast-noncanonical-path:{game}-{target}-{model_id}")
            continue
        path = (release / "runs" / expected_index_path).resolve()
        if not path.is_relative_to(release) or not path.is_file():
            mismatches.append(f"forecast-path-escape-or-missing:{game}-{target}-{model_id}")
            continue
        run_ids.add(json.loads(path.read_text(encoding="utf-8")).get("run_id"))
    run_id = next(iter(run_ids)) if len(run_ids) == 1 else None
    ledger_mismatches = sum(
        event.get("sequence") != position or event.get("ledger_identity") != run_id
        for position, event in enumerate(events)
    )
    referenced_receipts: set[Path] = set()
    for (game, target, model_id), index in forecast_index.items():
        experiment = f"{game}-{target}-{model_id}"
        attempt = f"{experiment}-attempt-01"
        rows = sequences.get((experiment, attempt), [])
        if [row["state"] for row in rows] != ["started", "forecast_locked", "label_unlocked", "scored", "succeeded"]:
            mismatches.append(f"unlock-order:{experiment}")
            continue
        started, lock, unlock = rows[:3]
        key_positions = positions[(experiment, attempt)]
        if key_positions[2] != key_positions[1] + 1 or events[key_positions[2] - 1] is not lock:
            mismatches.append(f"unlock-global-adjacency:{experiment}")
            continue
        expected_forecast_index_path = f"forecasts/{game}/{target}/{model_id}.json"
        forecast_path = (release / "runs" / expected_forecast_index_path).resolve()
        current_sha = hashlib.sha256(forecast_path.read_bytes()).hexdigest() if forecast_path.is_file() else None
        details = unlock["details"]
        expected_receipt_path = f"runs/label-unlocks/{game}/{target}/{model_id}.json"
        receipt_path = (release / expected_receipt_path).resolve()
        referenced_receipts.add(receipt_path)
        if not receipt_path.is_file():
            mismatches.append(f"unlock-receipt-missing:{experiment}")
            continue
        receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        receipt_hashes.add(receipt_sha)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        metric = metric_index[(game, target, model_id)]
        identities = (release.name, experiment, attempt, game, target, model_id)
        expected_path = f"runs/{expected_forecast_index_path}"
        lock_details = lock["details"]
        if (
            current_sha != index["sha256"]
            or lock["details"].get("forecast_sha256") != current_sha
            or lock_details.get("forecast_path") != expected_path
            or (lock_details.get("release_id"), lock_details.get("experiment_id"), lock_details.get("attempt_id"), lock_details.get("game"), lock_details.get("target_issue"), lock_details.get("model_id")) != identities
            or lock_details.get("run_id") != run_id
            or lock.get("ledger_identity") != run_id
            or details.get("forecast_sha256") != current_sha
            or details.get("forecast_path") != expected_path
            or (details.get("release_id"), details.get("experiment_id"), details.get("attempt_id"), details.get("game"), details.get("target_issue"), details.get("model_id")) != identities
            or details.get("run_id") != run_id
            or unlock.get("ledger_identity") != run_id
            or details.get("unlock_receipt_sha256") != receipt_sha
            or details.get("unlock_receipt_path") != expected_receipt_path
            or metric.get("label_unlock_receipt_sha256") != receipt_sha
            or metric.get("label_unlock_receipt_path") != expected_receipt_path
            or (receipt.get("release_id"), receipt.get("experiment_id"), receipt.get("attempt_id"), receipt.get("game"), receipt.get("target_issue"), receipt.get("model_id")) != identities
            or receipt.get("forecast_path") != expected_path
            or receipt.get("forecast_sha256") != current_sha
            or receipt.get("run_id") != run_id
            or (started["details"].get("release_id"), started["experiment_id"], started["attempt_id"], started["details"].get("game"), started["details"].get("target_issue"), started["details"].get("model_id")) != identities
            or receipt.get("label_store_identity") != expected_store
            or details.get("label_store_identity") != expected_store
            or len(receipt.get("guard_validation", {})) != 5
            or not all(value is True for value in receipt.get("guard_validation", {}).values())
        ):
            mismatches.append(f"unlock-binding:{experiment}")
    receipt_files = list((release / "runs/label-unlocks").rglob("*.json"))
    if referenced_receipts != {path.resolve() for path in receipt_files}:
        mismatches.append("unlock-receipt-inventory-set")
    if ledger_mismatches:
        mismatches.append(f"ledger-identity-or-sequence:{ledger_mismatches}")
    guarded = len(forecast_index) - len(mismatches)
    return {
        "guarded_unlock_count": guarded,
        "unique_unlock_receipt_count": len(receipt_hashes),
        "unlock_receipt_file_count": len(receipt_files),
        "pre_lock_label_read_count": sum(row.startswith("unlock-order:") for row in mismatches),
        "identity_or_hash_mismatch_count": sum(row.startswith("unlock-binding:") for row in mismatches),
        "ledger_identity_or_sequence_mismatch_count": ledger_mismatches,
        "mismatches": mismatches,
        "status": "PASS" if guarded == len(receipt_hashes) == len(receipt_files) == 600 and not mismatches else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    release, output = args.release_root.resolve(), args.output.resolve()
    if output.exists():
        raise FileExistsError(output)
    draws = {"dlt": [], "ssq": []}
    with (ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            draws[row["game"]].append(row)
    for rows in draws.values():
        rows.sort(key=lambda row: row["issue_id"])
    forecast_index = {}
    with (release / "runs/forecast-index.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            forecast_index[(row["game"], row["target_issue"], row["model_id"])] = row
    metric_index = {}
    with (release / "runs/metric-index.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            metric_index[(row["game"], row["target_issue"], row["model_id"])] = row
    guarded_unlock = guarded_unlock_recomputation(release, forecast_index, metric_index)
    mismatches = []
    reconstructed = 0
    for game in ("dlt", "ssq"):
        front_size, front_k, back_size, back_k = SPECS[game]
        for target_index in range(50, 200):
            prefix, target = draws[game][:target_index], draws[game][target_index]
            selected = select_lambda(prefix, game)
            for model_id in ("M0", "M1"):
                key = (game, target["issue_id"], model_id)
                index_row = forecast_index[key]
                expected_index_path = f"forecasts/{game}/{target['issue_id']}/{model_id}.json"
                if index_row.get("path") != expected_index_path:
                    mismatches.append(f"forecast-path:{key}")
                    continue
                forecast_path = release / "runs" / expected_index_path
                if hashlib.sha256(forecast_path.read_bytes()).hexdigest() != index_row["sha256"]:
                    mismatches.append(f"forecast-hash:{key}")
                    continue
                forecast = json.loads(forecast_path.read_text(encoding="utf-8"))
                if model_id == "M0":
                    front = [1.0] * front_size
                    back = [1.0] * back_size
                    expected_lambda = None
                else:
                    front = weights([row["front_numbers"] for row in prefix], front_size, front_k, selected)
                    back = weights([row["back_numbers"] for row in prefix], back_size, back_k, selected)
                    expected_lambda = selected
                actual = probability(front, front_k, target["front_numbers"]) * probability(back, back_k, target["back_numbers"])
                metric = json.loads((release / "runs" / metric_index[key]["path"]).read_text(encoding="utf-8"))
                if forecast["distribution"]["selected_lambda"] != expected_lambda:
                    mismatches.append(f"lambda:{key}")
                if any(not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12) for left, right in zip(front, forecast["distribution"]["front"]["weights"], strict=True)):
                    mismatches.append(f"front-weights:{key}")
                if any(not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12) for left, right in zip(back, forecast["distribution"]["back"]["weights"], strict=True)):
                    mismatches.append(f"back-weights:{key}")
                if not math.isclose(actual, metric["actual_joint_probability"], rel_tol=1e-10, abs_tol=1e-12):
                    mismatches.append(f"actual-probability:{key}")
                reconstructed += 1
    report = {
        "schema_version": "3.0.0", "artifact_type": "phase3_independent_model_reconstruction",
        "release_id": release.name, "status": "PASS" if not mismatches and reconstructed == 600 and guarded_unlock["status"] == "PASS" else "HOLD",
        "implementation": "standalone reference estimator with independent elementary-symmetric DP; no phase3 model/evaluator imports",
        "outer_target_count": 300, "model_target_count": reconstructed,
        "fold_reconstruction_coverage": reconstructed / 600, "lambda_reconstruction_coverage": reconstructed / 600,
        "weight_reconstruction_coverage": reconstructed / 600, "actual_probability_match_rate": (reconstructed - len([row for row in mismatches if row.startswith("actual-probability:")])) / 600,
        "guarded_label_unlock": guarded_unlock,
        "mismatches": mismatches, "blocking_findings": len(mismatches) + len(guarded_unlock["mismatches"]),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(report))
    return 0 if report["status"] == "PASS" else 5


if __name__ == "__main__":
    raise SystemExit(main())
