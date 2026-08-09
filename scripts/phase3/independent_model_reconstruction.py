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
                forecast_path = release / "runs" / index_row["path"]
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
        "release_id": release.name, "status": "PASS" if not mismatches and reconstructed == 600 else "HOLD",
        "implementation": "standalone reference estimator with independent elementary-symmetric DP; no phase3 model/evaluator imports",
        "outer_target_count": 300, "model_target_count": reconstructed,
        "fold_reconstruction_coverage": reconstructed / 600, "lambda_reconstruction_coverage": reconstructed / 600,
        "weight_reconstruction_coverage": reconstructed / 600, "actual_probability_match_rate": (reconstructed - len([row for row in mismatches if row.startswith("actual-probability:")])) / 600,
        "mismatches": mismatches, "blocking_findings": len(mismatches),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(report))
    return 0 if report["status"] == "PASS" else 5


if __name__ == "__main__":
    raise SystemExit(main())
