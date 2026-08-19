#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import math
import subprocess
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from lottery_system.phase4e5.features import build_feature_rows, candidate_names, load_draws, load_metadata, raw_matrix
from lottery_system.phase4e5.metadata import canonical, sha256


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/phase-4e5"
DELIVERY = BASE / "delivery"
ACCEPTANCE = BASE / "acceptance"


def verify_receipt(path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    claimed = payload.pop("receipt_sha256")
    return claimed == sha256(canonical(payload))


def verify_manifest_and_inventory() -> list[str]:
    failures = []
    manifest = json.loads((DELIVERY / "core-manifest.json").read_text(encoding="utf-8"))
    if manifest["manifest_sha256"] != sha256(canonical(manifest["files"])):
        failures.append("manifest_digest")
    for row in manifest["files"]:
        path = DELIVERY / row["path"]
        if not path.is_file() or path.stat().st_size != row["bytes"] or sha256(path.read_bytes()) != row["sha256"]:
            failures.append(f"manifest_file:{row['path']}")
    inventory = json.loads((DELIVERY / "inventory/prior-release-byte-inventory.json").read_text(encoding="utf-8"))
    for name, item in inventory.items():
        if not item["unchanged_from_base"] or item["inventory_sha256"] != sha256(canonical(item["files"])):
            failures.append(f"protected_inventory:{name}")
        for row in item["files"]:
            path = ROOT / row["path"]
            if not path.is_file() or sha256(path.read_bytes()) != row["sha256"]:
                failures.append(f"protected_file:{row['path']}")
    return failures


def independent_apply(matrix: np.ndarray, payload: dict[str, object]) -> np.ndarray:
    transformed = matrix.copy()
    if payload["transform"] == "log1p_robust_z":
        transformed = np.sign(transformed) * np.log1p(np.abs(transformed))
    missing = ~np.isfinite(transformed)
    filled = np.where(missing, np.asarray(payload["median"]), transformed)
    clipped = np.minimum(np.maximum(filled, np.asarray(payload["lower"])), np.asarray(payload["upper"]))
    normalized = (clipped - np.asarray(payload["center"])) / np.asarray(payload["scale"])
    return np.column_stack((normalized, missing.astype(float)))


def independent_elementary(weights: np.ndarray, k: int) -> float:
    coefficients = [1.0] + [0.0] * k
    for weight in weights:
        for order in range(k, 0, -1):
            coefficients[order] += float(weight) * coefficients[order - 1]
    return coefficients[k]


def independent_weights(model: dict[str, object], zone: int, x: np.ndarray | None) -> np.ndarray:
    coefficients = np.asarray(model["zones"][zone]["coefficients"])
    logits = coefficients[0] if model["candidate_id"] == "B0" else np.concatenate(([1.0], x)) @ coefficients
    return np.exp(logits - np.max(logits))


def ticket_probability(model: dict[str, object], x: np.ndarray | None, front: list[int], back: list[int]) -> float:
    result = 1.0
    for zone, combo in enumerate((front, back)):
        weights = independent_weights(model, zone, x)
        k = int(model["zones"][zone]["k"])
        result *= math.prod(float(weights[value - 1]) for value in combo) / independent_elementary(weights, k)
    return result


def next_draw(game: str, last: dict[str, object]) -> dict[str, object]:
    allowed = {"ssq": {1, 3, 6}, "dlt": {0, 2, 5}}[game]
    cursor = date.fromisoformat(str(last["draw_date"])) + timedelta(days=1)
    while cursor.weekday() not in allowed:
        cursor += timedelta(days=1)
    return {"game": game, "issue": str(int(str(last["issue"])) + 1), "draw_date": cursor.isoformat(), "front": [], "back": []}


def verify_raw_provenance() -> list[str]:
    failures = []
    inventory = json.loads((BASE / "acquisition/raw-inventory.json").read_text(encoding="utf-8"))
    if inventory["request_count"] != 489:
        failures.append("raw_request_count")
    for item in inventory["requests"]:
        directory = BASE / "acquisition/raw" / item["source_id"]
        receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
        stored = (directory / receipt["response"]["body_path"]).read_bytes()
        raw = gzip.decompress(stored)
        if hashlib.sha256(raw).hexdigest() != receipt["response"]["body_sha256"]:
            failures.append(f"raw_body:{item['source_id']}")
        request_body = (directory / "request-body.bin").read_bytes()
        if hashlib.sha256(request_body).hexdigest() != receipt["request"]["body_sha256"]:
            failures.append(f"request_body:{item['source_id']}")
    return failures


def main() -> int:
    failures = verify_manifest_and_inventory() + verify_raw_provenance()
    roles = json.loads((BASE / "roles/role-boundary-receipt.json").read_text(encoding="utf-8"))
    audit = json.loads((BASE / "metadata-audit/coverage-audit.json").read_text(encoding="utf-8"))
    metadata_all = load_metadata(BASE / "metadata-audit/dlt-official-metadata.jsonl")
    replay = {}
    mutation = {}
    for game in ("ssq", "dlt"):
        selection_path = BASE / f"selection/{game}-selection-receipt.json"
        report_path = BASE / f"report/{game}-report-receipt.json"
        if not verify_receipt(selection_path) or not verify_receipt(report_path):
            failures.append(f"receipt_digest:{game}")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if selection["report_labels_read"] or report["report_evaluations"] != 1:
            failures.append(f"capability_or_report_count:{game}")
        if len(report["comparisons_vs_B0"]) != 5 or any(value["holm_adjusted_log_loss_p"] < 0 or value["holm_adjusted_log_loss_p"] > 1 for value in report["comparisons_vs_B0"].values()):
            failures.append(f"holm_family:{game}")
        draws = load_draws(ROOT / roles["games"][game]["eligible_source"])
        metadata = {} if game == "ssq" else metadata_all
        synthetic = next_draw(game, draws[-1])
        features = build_feature_rows(game, draws + [synthetic], metadata)
        material = json.loads((BASE / f"report/{game}-report-material.json").read_text(encoding="utf-8"))
        candidate = selection["strongest_selection_candidate"]
        model = material["models"][candidate]
        if candidate == "B0":
            x = None
        else:
            names = candidate_names(candidate, selection["provincial_distribution_enabled"])
            matrix = raw_matrix(features, names)
            x = independent_apply(matrix, material["candidates"][candidate]["preprocessor"])[-1]
        expected = [json.loads(line) for line in (DELIVERY / f"top1000/{game}-top1000-shadow.jsonl").read_text().splitlines()]
        errors = [abs(ticket_probability(model, x, row["front"], row["back"]) - row["joint_probability"]) for row in expected]
        ordered = all(left["joint_probability"] >= right["joint_probability"] for left, right in zip(expected, expected[1:]))
        exact_identity = all(row["rank"] == index for index, row in enumerate(expected, 1))
        replay[game] = {
            "row_count": len(expected), "exact_rank_identity": exact_identity,
            "ordered": ordered, "maximum_independent_probability_absolute_error": max(errors),
            "pass": len(expected) == 1000 and exact_identity and ordered and max(errors) <= 1e-20,
        }
        if not replay[game]["pass"]:
            failures.append(f"independent_top1000_replay:{game}")
        mutated = json.loads(json.dumps(metadata))
        if game == "dlt":
            mutated[str(draws[-1]["issue"])]["sales"] = 1e99
        original_prior = build_feature_rows(game, draws, metadata)[:-1]
        mutated_prior = build_feature_rows(game, draws, mutated)[:-1]
        mutation[game] = {"future_metadata_mutated": game == "dlt", "all_prior_feature_rows_unchanged": original_prior == mutated_prior}
        if original_prior != mutated_prior:
            failures.append(f"future_mutation:{game}")
    if audit["all_games_comparable_official_metadata"] or audit["unofficial_substitution_count"]:
        failures.append("source_authority_gate")
    tests = json.loads((ACCEPTANCE / "full-current-phase4-tests.json").read_text(encoding="utf-8"))
    if tests["status"] != "PASS":
        failures.append("full_phase4_tests")
    if subprocess.run(["git", "diff", "--quiet", "3a65b5331f8ec8cb80d288347103db8a39992654", "--", "artifacts/phase-4/P4-P4E2-20260815-r12", "artifacts/phase-4e3", "artifacts/phase-4e4"], cwd=ROOT).returncode:
        failures.append("prior_release_git_diff")
    payload = {
        "artifact_type": "phase4e5_independent_acceptance", "blocking_findings": failures,
        "independent_top1000_replay": replay, "negative_future_metadata_mutation": mutation,
        "raw_provenance_request_count": 489, "serving_release": "P4-P4E2-20260815-r12",
        "p4e4_terminal_state": "FEATURE_ENGINEERING_DELIVERED_PROSPECTIVE_ONLY",
        "terminal_state": "FEATURE_ENGINEERING_DELIVERED_NO_PROMOTION",
        "status": "PASS" if not failures else "FAIL",
    }
    payload["receipt_sha256"] = sha256(canonical(payload))
    (ACCEPTANCE / "independent-acceptance.json").write_bytes(canonical(payload))
    print(json.dumps(payload, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
