#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
from statistics import mean

from lottery_system.phase4e5.features import (
    CANDIDATE_BLOCKS, apply_preprocessor, build_feature_rows, candidate_names,
    fit_preprocessor, load_metadata, raw_matrix, transformed_names,
)
from lottery_system.phase4e5.metadata import canonical, sha256
from lottery_system.phase4e5.model import fit_model, score_rows


ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "artifacts/phase-4e5/roles/role-boundary-receipt.json"
REGISTRY = ROOT / "config/phase4e5_registry.json"
METADATA = ROOT / "artifacts/phase-4e5/metadata-audit/dlt-official-metadata.jsonl"
AUDIT = ROOT / "artifacts/phase-4e5/metadata-audit/coverage-audit.json"
OUTPUT = ROOT / "artifacts/phase-4e5/selection"


def load_prefix(path: Path, count: int) -> list[dict[str, object]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in itertools.islice(handle, count):
            rows.append(json.loads(line))
    if len(rows) != count:
        raise ValueError(f"short selection capability: {path}")
    return rows


def selection_folds(count: int) -> list[dict[str, object]]:
    selection_start = count - 120
    return [
        {
            "fold": fold + 1,
            "train": [0, selection_start + fold * 24 - 1],
            "embargo": [selection_start + fold * 24 - 1, selection_start + fold * 24],
            "validation": [selection_start + fold * 24, selection_start + (fold + 1) * 24],
        }
        for fold in range(5)
    ]


def configurations() -> list[dict[str, object]]:
    return [
        {"numeric_transform": transform, "winsor_quantiles": list(quantiles), "regularization_c": c_value}
        for transform, quantiles, c_value in itertools.product(
            ("robust_z", "log1p_robust_z"), ((0.01, 0.99), (0.005, 0.995)), (0.01, 0.1, 1.0)
        )
    ]


def evaluate_candidate(
    game: str,
    candidate_id: str,
    draws: list[dict[str, object]],
    feature_rows: list[dict[str, object]],
    folds: list[dict[str, object]],
    provincial_enabled: bool,
) -> dict[str, object]:
    if candidate_id == "O3" and not provincial_enabled:
        return {"candidate_id": candidate_id, "eligible": False, "reason": "official provincial coverage gate failed", "configurations": []}
    if candidate_id == "B0":
        fold_rows = []
        for fold in folds:
            train = list(range(*fold["train"])); validation = list(range(*fold["validation"]))
            model = fit_model(game, None, draws, train, 1.0, candidate_id)
            rows = score_rows(model, None, draws, validation)
            fold_rows.append({
                "fold": fold["fold"], "mean_log_loss": mean(row["mean_per_ball_bernoulli_log_loss"] for row in rows),
                "mean_brier": mean(row["mean_per_ball_brier"] for row in rows),
            })
        return {
            "candidate_id": candidate_id, "eligible": True, "feature_blocks": [], "feature_names": [],
            "configurations": [{
                "config": {"baseline": "prefix_empirical_frequency"}, "folds": fold_rows,
                "mean_log_loss": mean(row["mean_log_loss"] for row in fold_rows),
                "mean_brier": mean(row["mean_brier"] for row in fold_rows),
            }],
            "selected_config": {"baseline": "prefix_empirical_frequency"},
        }
    names = candidate_names(candidate_id, provincial_enabled)
    matrix = raw_matrix(feature_rows, names)
    evaluated = []
    for config in configurations():
        fold_rows = []
        for fold in folds:
            train = list(range(*fold["train"])); validation = list(range(*fold["validation"]))
            spec = fit_preprocessor(matrix, train, names, str(config["numeric_transform"]), tuple(config["winsor_quantiles"]))
            transformed = apply_preprocessor(matrix, spec)
            model = fit_model(game, transformed, draws, train, float(config["regularization_c"]), candidate_id)
            rows = score_rows(model, transformed, draws, validation)
            fold_rows.append({
                "fold": fold["fold"], "train_end_exclusive": fold["train"][1],
                "validation": fold["validation"], "preprocessor_fit_end_exclusive": fold["train"][1],
                "mean_log_loss": mean(row["mean_per_ball_bernoulli_log_loss"] for row in rows),
                "mean_brier": mean(row["mean_per_ball_brier"] for row in rows),
            })
        evaluated.append({
            "config": config, "folds": fold_rows,
            "mean_log_loss": mean(row["mean_log_loss"] for row in fold_rows),
            "mean_brier": mean(row["mean_brier"] for row in fold_rows),
        })
    evaluated.sort(key=lambda row: (row["mean_log_loss"], row["mean_brier"], canonical(row["config"])))
    return {
        "candidate_id": candidate_id, "eligible": True,
        "feature_blocks": [block for block in CANDIDATE_BLOCKS[candidate_id] if provincial_enabled or block != "provincial_distribution_conditional"],
        "feature_names": names, "transformed_feature_names": transformed_names(names),
        "configurations": evaluated, "selected_config": evaluated[0]["config"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    roles = json.loads(ROLES.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    dlt_metadata = load_metadata(METADATA)
    summaries = []
    for game in ("ssq", "dlt"):
        role = roles["games"][game]
        selection_count = role["development"]["row_count"] + role["selection"]["row_count"]
        source = ROOT / role["eligible_source"]
        draws = load_prefix(source, selection_count)
        metadata = {} if game == "ssq" else {issue: row for issue, row in dlt_metadata.items() if int(issue) <= int(role["selection"]["last_issue"])}
        feature_rows = build_feature_rows(game, draws, metadata)
        folds = selection_folds(selection_count)
        provincial_coverage = audit["games"][game]["field_coverage"]["province_first_prize_distribution"]
        provincial_enabled = bool(game == "dlt" and provincial_coverage >= registry["conditional_enablement"]["provincial_distribution_conditional"]["minimum_overall_coverage"])
        candidates = {
            candidate_id: evaluate_candidate(game, candidate_id, draws, feature_rows, folds, provincial_enabled)
            for candidate_id in CANDIDATE_BLOCKS
        }
        eligible = []
        for candidate_id, result in candidates.items():
            if result["eligible"]:
                best = result["configurations"][0]
                eligible.append((best["mean_log_loss"], best["mean_brier"], candidate_id))
        strongest = min(eligible)[2]
        receipt = {
            "artifact_type": "phase4e5_selection_receipt", "game": game,
            "registry_sha256": sha256(REGISTRY.read_bytes()), "role_receipt_sha256": sha256(ROLES.read_bytes()),
            "selection_input": str(source.relative_to(ROOT)), "selection_input_rows_read": selection_count,
            "selection_input_prefix_sha256": hashlib.sha256(b"".join(canonical(row) for row in draws)).hexdigest(),
            "report_labels_read": False, "report_row_count_read": 0, "original_200_labels_read": False,
            "folds": folds, "candidates": candidates, "strongest_selection_candidate": strongest,
            "strongest_selection_config": candidates[strongest]["selected_config"],
            "provincial_distribution_enabled": provincial_enabled,
            "official_operational_metadata_available": game == "dlt",
        }
        receipt["receipt_sha256"] = sha256(canonical(receipt))
        path = args.output / f"{game}-selection-receipt.json"
        path.write_bytes(canonical(receipt))
        (args.output / f"{game}-selection-feature-snapshot.jsonl").write_bytes(b"".join(canonical(row) for row in feature_rows[-120:]))
        summaries.append({"game": game, "strongest": strongest, "receipt_sha256": receipt["receipt_sha256"]})
    summary = {"artifact_type": "phase4e5_selection_summary", "games": summaries, "report_labels_read": False}
    summary["receipt_sha256"] = sha256(canonical(summary))
    (args.output / "selection-summary.json").write_bytes(canonical(summary))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
