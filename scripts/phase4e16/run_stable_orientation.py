#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import multiprocessing
import os
import statistics
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4e15"))
import run_number_orientation as e15

e14 = e15.e14
e13 = e15.e13
OUT = ROOT / "artifacts/phase4e16"
E13 = ROOT / "artifacts/phase4e13"
E14 = ROOT / "artifacts/phase4e14"
E15 = ROOT / "artifacts/phase4e15"
SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
ORACLE = ROOT / "scripts/phase4_independent/p4e2_oracle.py"
E13_SCRIPT = ROOT / "scripts/phase4e13/run_partial_hit_evaluation.py"
E14_SCRIPT = ROOT / "scripts/phase4e14/run_confidence_calibration.py"
E15_SCRIPT = ROOT / "scripts/phase4e15/run_number_orientation.py"
E16_SCRIPT = Path(__file__).resolve()
OUTER_DRAWS = 120
OUTER_CALIBRATION_DRAWS = 60
SELECTION_DRAWS = 240
SELECTION_BLOCK_DRAWS = 60
SELECTION_BLOCKS = 4
STABLE_POSITIVE_BLOCKS = 3
CANDIDATE_ORDER = e15.CANDIDATE_ORDER
ZONE_SIZES = e15.ZONE_SIZES


canonical = e15.canonical
digest = e15.digest
sha256 = e15.sha256
read_json = e15.read_json
read_jsonl = e15.read_jsonl
row_identity = e15.row_identity
outer_identity_digest = e15.e13_identity_digest


def raw_report(phase: Path, game: str) -> dict[str, object]:
    return read_json(phase / game / "report.json")


def raw_rows(phase: Path, game: str, kind: str) -> list[dict[str, object]]:
    return read_jsonl(phase / game / f"{kind}-rolling-report.jsonl")


def selection_targets(game: str) -> list[int]:
    data = e13.load(game)
    outer_start = len(data) - OUTER_DRAWS
    start = len(data) - OUTER_DRAWS - SELECTION_DRAWS
    if start < e13.e12.e9.oracle.MIN_HISTORY:
        raise ValueError("FAIL_PHASE4E16_SELECTION_HISTORY")
    return list(range(start, outer_start))


def recompute_inner_row(task: tuple[str, int]) -> dict[str, object]:
    return e15.recompute_inner_row(task)


def verify_e14_e15_inner_overlap(
    game: str, rows: Sequence[dict[str, object]]
) -> dict[str, object]:
    rows14 = raw_rows(E14, game, "inner")
    rows15 = raw_rows(E15, game, "inner")
    overlap = list(rows[-e14.SELECTION_DRAWS :])
    if row_identity(rows14) != row_identity(rows15) or row_identity(overlap) != row_identity(rows14):
        raise ValueError("FAIL_PHASE4E14_E15_INNER_IDENTITY")
    for recomputed, row14, row15 in zip(overlap, rows14, rows15):
        if recomputed["zones"] != row15["zones"]:
            raise ValueError("FAIL_PHASE4E15_INNER_ZONE_IDENTITY")
        for zone in ("front", "back"):
            for field in ("marginal_ranking", "confidence_sets"):
                if recomputed["zones"][zone][field] != row14["zones"][zone][field]:
                    raise ValueError(f"FAIL_PHASE4E14_INNER_{field.upper()}_IDENTITY")
    return {
        "draws": len(overlap),
        "first_target_position": overlap[0]["target_position"],
        "last_target_position": overlap[-1]["target_position"],
        "identity_sha256": digest(row_identity(overlap)),
        "phase4e14_identity_sha256": digest(row_identity(rows14)),
        "phase4e15_identity_sha256": digest(row_identity(rows15)),
        "rank_and_set_identity_verified": True,
        "full_phase4e15_zone_identity_verified": True,
    }


def compute_inner_rows(game: str, workers: int) -> tuple[list[dict[str, object]], dict[str, object]]:
    targets = selection_targets(game)
    tasks = [(game, target) for target in targets]
    if workers == 1:
        rows = [recompute_inner_row(task) for task in tasks]
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=workers) as pool:
            rows = pool.map(recompute_inner_row, tasks, chunksize=1)
    rows.sort(key=lambda row: int(row["target_position"]))
    if [int(row["target_position"]) for row in rows] != targets:
        raise ValueError("FAIL_PHASE4E16_SELECTION_TARGETS")
    outer_start = targets[-1] + 1
    for index, row in enumerate(rows):
        if (
            int(row["target_position"]) >= outer_start
            or not bool(row["strict_lag"])
            or int(row["maximum_training_position"]) != int(row["target_position"]) - 1
        ):
            raise ValueError("FAIL_PHASE4E16_SELECTION_STRICT_LAG_OR_OUTER_LEAKAGE")
        row["selection_block"] = f"chronological_block_{index // SELECTION_BLOCK_DRAWS + 1}"
        row["selection_block_index"] = index // SELECTION_BLOCK_DRAWS + 1
        row["outer_label_used_for_orientation_selection"] = False
    overlap = verify_e14_e15_inner_overlap(game, rows)
    for row in rows[-e14.SELECTION_DRAWS :]:
        row["phase4e14_phase4e15_overlap_identity_verified"] = True
    return rows, overlap


def select_orientations(
    game: str, inner_rows: Sequence[dict[str, object]]
) -> dict[str, dict[str, object]]:
    if len(inner_rows) != SELECTION_DRAWS:
        raise ValueError("FAIL_PHASE4E16_SELECTION_WINDOW")
    blocks = [
        inner_rows[index * SELECTION_BLOCK_DRAWS : (index + 1) * SELECTION_BLOCK_DRAWS]
        for index in range(SELECTION_BLOCKS)
    ]
    if any(len(block) != SELECTION_BLOCK_DRAWS for block in blocks):
        raise ValueError("FAIL_PHASE4E16_BLOCK_LENGTH")
    report15 = raw_report(E15, game)
    fallback_results = report15["orientation_experiment"]["selection_results"]
    selection_identity = digest(row_identity(inner_rows))
    results: dict[str, dict[str, object]] = {}
    for zone in ("front", "back"):
        candidates = []
        for order, candidate in enumerate(CANDIDATE_ORDER, start=1):
            block_metrics = []
            for block_index, block in enumerate(blocks, start=1):
                metrics = e15.association_metrics(
                    e15.individual_observations(block, zone, candidate),
                    "orientation_score",
                    "binary_hit",
                )
                block_metrics.append(
                    {
                        "block": block_index,
                        "draws": len(block),
                        "first_issue": block[0]["issue"],
                        "last_issue": block[-1]["issue"],
                        "first_target_position": block[0]["target_position"],
                        "last_target_position": block[-1]["target_position"],
                        "identity_sha256": digest(row_identity(block)),
                        "metrics": metrics,
                        "positive_rho_and_slope": bool(metrics["positive_association"]),
                    }
                )
            positive_count = sum(bool(value["positive_rho_and_slope"]) for value in block_metrics)
            median_rho = statistics.median(
                float(value["metrics"]["spearman_rho"]) for value in block_metrics
            )
            median_slope = statistics.median(
                float(value["metrics"]["descriptive_linear_association"]["slope"])
                for value in block_metrics
            )
            candidates.append(
                {
                    "candidate": candidate,
                    "candidate_order": order,
                    "registered_control": candidate == "reverse_ascending_marginal_mass_control",
                    "positive_block_count": positive_count,
                    "required_positive_block_count": STABLE_POSITIVE_BLOCKS,
                    "median_spearman_rho": median_rho,
                    "median_descriptive_slope": median_slope,
                    "stable_eligible": positive_count >= STABLE_POSITIVE_BLOCKS,
                    "blocks": block_metrics,
                }
            )
        eligible = [candidate for candidate in candidates if bool(candidate["stable_eligible"])]
        fallback = str(fallback_results[zone]["selected_candidate"])
        if eligible:
            winner = max(
                eligible,
                key=lambda value: (
                    int(value["positive_block_count"]),
                    float(value["median_spearman_rho"]),
                    -int(value["candidate_order"]),
                ),
            )
            selected = str(winner["candidate"])
            stable = True
            status = "stable_selected"
        else:
            selected = fallback
            stable = False
            status = "unstable_phase4e15_fallback"
        orientation_id = (
            f"p4e16-{status}-{selected}-"
            f"{digest({'game': game, 'zone': zone, 'candidate': selected, 'selection_identity': selection_identity, 'stable': stable})[:16]}"
        )
        results[zone] = {
            "selection_rule": "candidate must have rho>0 and slope>0 in at least 3 of 4 chronological 60-draw blocks; eligible candidates are ordered by positive-block count, median rho, then registered candidate order",
            "selection_uses_outer_labels": False,
            "selection_status": status,
            "stable": stable,
            "selected_candidate": selected,
            "selected_registered_control": selected == "reverse_ascending_marginal_mass_control",
            "selected_orientation_id": orientation_id,
            "registered_phase4e15_fallback_candidate": fallback,
            "registered_phase4e15_fallback_orientation_id": fallback_results[zone][
                "selected_orientation_id"
            ],
            "fallback_used": not stable,
            "candidates": candidates,
        }
    return results


def validate_frozen_outer(game: str) -> list[dict[str, object]]:
    rows13 = raw_rows(E13, game, "outer")
    rows14 = raw_rows(E14, game, "outer")
    rows15 = raw_rows(E15, game, "outer")
    if not (len(rows13) == len(rows14) == len(rows15) == OUTER_DRAWS):
        raise ValueError("FAIL_PHASE4E16_FROZEN_OUTER_LENGTH")
    projected15 = []
    for row in rows15:
        source = copy.deepcopy(row)
        source.pop("phase4e15_number_orientation")
        projected15.append(source)
    if projected15 != rows14:
        raise ValueError("FAIL_PHASE4E15_EMBEDDED_PHASE4E14_OUTER")
    projected14 = []
    for row in rows14:
        source = copy.deepcopy(row)
        source.pop("phase4e14_confidence_calibration")
        projected14.append(source)
    if projected14 != rows13:
        raise ValueError("FAIL_PHASE4E14_EMBEDDED_PHASE4E13_OUTER")
    if not (row_identity(rows13) == row_identity(rows14) == row_identity(rows15)):
        raise ValueError("FAIL_PHASE4E13_E14_E15_OUTER_IDENTITY")
    identity_hash = outer_identity_digest(rows15)
    for phase in (E13, E14, E15):
        if identity_hash != str(raw_report(phase, game)["outer_window"]["identity_sha256"]):
            raise ValueError("FAIL_PHASE4E13_E14_E15_OUTER_IDENTITY_HASH")
    for row13, row14, row15 in zip(rows13, rows14, rows15):
        for zone in ("front", "back"):
            for field in ("marginal_ranking", "confidence_sets"):
                if not (
                    row13["zones"][zone][field]
                    == row14["zones"][zone][field]
                    == row15["zones"][zone][field]
                ):
                    raise ValueError(f"FAIL_PHASE4E13_E14_E15_OUTER_{field.upper()}_IDENTITY")
        if not bool(row15["strict_lag"]) or int(row15["maximum_training_position"]) != int(
            row15["target_position"]
        ) - 1:
            raise ValueError("FAIL_PHASE4E16_OUTER_STRICT_LAG")
    return rows15


def decorate_outer_rows(
    rows: Sequence[dict[str, object]], selections: dict[str, dict[str, object]]
) -> list[dict[str, object]]:
    decorated = copy.deepcopy(list(rows))
    for row in decorated:
        zone_outputs: dict[str, object] = {}
        for zone, sizes in ZONE_SIZES.items():
            source = row["zones"][zone]
            selection = selections[zone]
            candidate = str(selection["selected_candidate"])
            ranking = e15.oriented_ranking(source["marginal_probabilities"], candidate)
            actual = set(int(number) for number in source["actual_numbers"])
            raw_rank = {
                int(number): rank for rank, number in enumerate(source["marginal_ranking"], start=1)
            }
            selected_rank = {number: rank for rank, number in enumerate(ranking, start=1)}
            observations = []
            for value in source["marginal_probabilities"]:
                number = int(value["number"])
                mass = float(value["inclusion_mass"])
                observations.append(
                    {
                        "number": number,
                        "marginal_inclusion_mass": mass,
                        "orientation_score": e15.orientation_score(candidate, mass),
                        "binary_hit": int(number in actual),
                        "raw_descending_rank": raw_rank[number],
                        "selected_orientation_rank": selected_rank[number],
                    }
                )
            zone_outputs[zone] = {
                "selected_candidate": candidate,
                "selected_registered_control": selection["selected_registered_control"],
                "selected_orientation_id": selection["selected_orientation_id"],
                "stable": selection["stable"],
                "fallback_used": selection["fallback_used"],
                "registered_phase4e15_fallback_candidate": selection[
                    "registered_phase4e15_fallback_candidate"
                ],
                "canonical_ascending_number_tie_break": True,
                "selected_orientation_ranking": ranking,
                "number_observations": observations,
                "confidence_sets": {
                    str(size): e15.confidence_set(source, candidate, ranking, size) for size in sizes
                },
            }
        row["phase4e16_stable_orientation"] = {
            "strict_lag": True,
            "maximum_training_position": row["maximum_training_position"],
            "outer_label_used_for_orientation_selection": False,
            "zones": zone_outputs,
        }
    return decorated


def metrics_view(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    view = copy.deepcopy(list(rows))
    for row in view:
        row["phase4e15_number_orientation"] = copy.deepcopy(row["phase4e16_stable_orientation"])
    return view


def per_canonical_number_association(
    rows: Sequence[dict[str, object]], zone: str
) -> list[dict[str, object]]:
    number_pool_size = int(rows[0]["zones"][zone]["number_pool_size"])
    result = []
    for number in range(1, number_pool_size + 1):
        observations = []
        for row in rows:
            item = next(
                value
                for value in row["phase4e16_stable_orientation"]["zones"][zone][
                    "number_observations"
                ]
                if int(value["number"]) == number
            )
            observations.append(
                {
                    "issue": row["issue"],
                    "number": number,
                    "orientation_score": item["orientation_score"],
                    "binary_hit": item["binary_hit"],
                }
            )
        metrics = e15.association_metrics(observations, "orientation_score", "binary_hit")
        result.append(
            {
                "number": number,
                "draws": len(rows),
                "spearman_rho": metrics["spearman_rho"],
                "descriptive_slope": metrics["descriptive_linear_association"]["slope"],
                "positive_association": metrics["positive_association"],
            }
        )
    return result


def split_metrics(
    rows: Sequence[dict[str, object]], selections: dict[str, dict[str, object]]
) -> dict[str, object]:
    metrics = e15.split_metrics(metrics_view(rows), selections)
    for zone in ("front", "back"):
        metrics[zone]["per_canonical_number_association"] = per_canonical_number_association(
            rows, zone
        )
        metrics[zone]["orientation_stable"] = selections[zone]["stable"]
        metrics[zone]["orientation_fallback_used"] = selections[zone]["fallback_used"]
    return metrics


def selection_window_metadata(
    inner_rows: Sequence[dict[str, object]], overlap: dict[str, object]
) -> dict[str, object]:
    blocks = []
    for index in range(SELECTION_BLOCKS):
        block = inner_rows[
            index * SELECTION_BLOCK_DRAWS : (index + 1) * SELECTION_BLOCK_DRAWS
        ]
        blocks.append(
            {
                "block": index + 1,
                "name": f"chronological_block_{index + 1}",
                "draws": len(block),
                "first_issue": block[0]["issue"],
                "last_issue": block[-1]["issue"],
                "first_target_position": block[0]["target_position"],
                "last_target_position": block[-1]["target_position"],
                "identity_fields": ["issue", "target_position"],
                "identity_sha256": digest(row_identity(block)),
                "strictly_pre_outer": True,
                "outer_labels_used": False,
            }
        )
    return {
        "definition": "N-360 through N-121 inclusive (Python half-open N-360..N-120)",
        "draws": len(inner_rows),
        "first_issue": inner_rows[0]["issue"],
        "last_issue": inner_rows[-1]["issue"],
        "first_target_position": inner_rows[0]["target_position"],
        "last_target_position": inner_rows[-1]["target_position"],
        "outer_first_target_position": int(inner_rows[-1]["target_position"]) + 1,
        "identity_fields": ["issue", "target_position"],
        "identity_sha256": digest(row_identity(inner_rows)),
        "immediately_before_outer": True,
        "strictly_before_outer": True,
        "outer_labels_used_for_orientation_selection": False,
        "blocks": blocks,
        "phase4e14_phase4e15_overlap": overlap,
    }


def lineage(game: str, inner_path: Path, outer_path: Path) -> dict[str, object]:
    source_path = SOURCE / f"{game}.jsonl"
    result: dict[str, object] = {
        "source_data_path": str(source_path.relative_to(ROOT)),
        "source_data_sha256": sha256(source_path),
        "registered_p4e2_oracle_path": str(ORACLE.relative_to(ROOT)),
        "registered_p4e2_oracle_sha256": sha256(ORACLE),
        "phase4e16_script_path": str(E16_SCRIPT.relative_to(ROOT)),
        "phase4e16_script_sha256": sha256(E16_SCRIPT),
        "phase4e16_inner_rows_sha256": sha256(inner_path),
        "phase4e16_outer_rows_sha256": sha256(outer_path),
    }
    for phase_number, phase, script in (
        (13, E13, E13_SCRIPT),
        (14, E14, E14_SCRIPT),
        (15, E15, E15_SCRIPT),
    ):
        prefix = f"phase4e{phase_number}"
        result[f"{prefix}_script_path"] = str(script.relative_to(ROOT))
        result[f"{prefix}_script_sha256"] = sha256(script)
        result[f"{prefix}_summary_sha256"] = sha256(phase / "summary.json")
        result[f"{prefix}_report_sha256"] = sha256(phase / game / "report.json")
        result[f"{prefix}_outer_rows_sha256"] = sha256(
            phase / game / "outer-rolling-report.jsonl"
        )
        if phase_number >= 14:
            result[f"{prefix}_inner_rows_sha256"] = sha256(
                phase / game / "inner-rolling-report.jsonl"
            )
        result[f"{prefix}_outer_identity_sha256"] = raw_report(phase, game)[
            "outer_window"
        ]["identity_sha256"]
    return result


def run(game: str, workers: int) -> dict[str, object]:
    report13 = raw_report(E13, game)
    report14 = raw_report(E14, game)
    report15 = raw_report(E15, game)
    outer15 = validate_frozen_outer(game)
    inner_rows, overlap = compute_inner_rows(game, workers)
    selections = select_orientations(game, inner_rows)
    decorated_outer = decorate_outer_rows(outer15, selections)
    for source, row in zip(outer15, decorated_outer):
        projected = copy.deepcopy(row)
        projected.pop("phase4e16_stable_orientation")
        if projected != source:
            raise ValueError("FAIL_PHASE4E15_OUTER_ROW_MUTATION")
    metrics = {
        "calibration": split_metrics(
            decorated_outer[:OUTER_CALIBRATION_DRAWS], selections
        ),
        "evaluation": split_metrics(
            decorated_outer[OUTER_CALIBRATION_DRAWS:], selections
        ),
        "all_120_descriptive": split_metrics(decorated_outer, selections),
    }
    evaluation_zone_pass = {
        zone: bool(metrics["evaluation"][zone]["acceptance_pass"])
        for zone in ("front", "back")
    }
    path = OUT / game
    path.mkdir(parents=True, exist_ok=True)
    inner_path = path / "inner-rolling-report.jsonl"
    outer_path = path / "outer-rolling-report.jsonl"
    inner_path.write_bytes(b"".join(canonical(row) for row in inner_rows))
    outer_path.write_bytes(b"".join(canonical(row) for row in decorated_outer))
    selection_window = selection_window_metadata(inner_rows, overlap)
    outer_hash = outer_identity_digest(outer15)
    exact_ticket = report15["full_ticket_comparison"]
    report = {
        "artifact_type": "phase4e16_stable_per_number_orientation_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "diagnostic_claim": "orientation/model diagnostic; marginal inclusion scores are not true lottery probabilities",
        "outer_window": {
            **report15["outer_window"],
            "first_60_role": "frozen_outer_calibration_evaluation_only_not_selection",
            "last_60_role": "frozen_outer_evaluation",
            "identity_sha256": outer_hash,
            "identity_named_fields_sha256": digest(row_identity(outer15)),
            "identity_matches_phase4e13_e14_e15": all(
                outer_hash == raw_report(phase, game)["outer_window"]["identity_sha256"]
                for phase in (E13, E14, E15)
            ),
            "phase4e14_phase4e15_rank_and_set_identity_verified": True,
        },
        "candidate_selection_window": selection_window,
        "stable_orientation_experiment": {
            "bounded_candidates": list(CANDIDATE_ORDER),
            "candidate_count": len(CANDIDATE_ORDER),
            "selection_scope": "separate_for_each_game_and_zone",
            "canonical_ascending_number_tie_break": True,
            "selection_uses_outer_labels": False,
            "selection_results": selections,
            "score_claim": "signed marginal inclusion mass used only as an orientation association score",
        },
        "outer_splits": metrics,
        "acceptance": {
            "rule": "every game/zone on the last-60 frozen outer evaluation requires individual-number Spearman rho > 0 and positive descriptive slope; no pooled-set or fixed-set association is an acceptance gate",
            "evaluation_zone_pass": evaluation_zone_pass,
            "failed_zones": [zone for zone, passed in evaluation_zone_pass.items() if not passed],
            "accepted": all(evaluation_zone_pass.values()),
            "pooled_or_set_level_confidence_used_for_acceptance": False,
        },
        "full_ticket_comparison": exact_ticket,
        "full_ticket_comparison_unchanged_from_phase4e13_e14_e15": (
            exact_ticket
            == report14["full_ticket_comparison"]
            == report13["full_ticket_comparison"]
        ),
        "lineage": lineage(game, inner_path, outer_path),
        "strict_lag": {
            "target_t_uses_through_t_minus_1_only": True,
            "all_selection_rows_strict_lag": all(bool(row["strict_lag"]) for row in inner_rows),
            "all_outer_rows_strict_lag": all(bool(row["strict_lag"]) for row in outer15),
            "all_maximum_training_positions_equal_target_minus_one": all(
                int(row["maximum_training_position"]) == int(row["target_position"]) - 1
                for row in [*inner_rows, *outer15]
            ),
            "outer_labels_used_for_orientation_selection": False,
        },
    }
    report["lineage"]["phase4e16_outer_identity_sha256"] = outer_hash
    report["lineage"]["phase4e16_selection_identity_sha256"] = selection_window[
        "identity_sha256"
    ]
    report["lineage"]["phase4e16_block_identity_sha256"] = [
        block["identity_sha256"] for block in selection_window["blocks"]
    ]
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Phase4E16 stable per-number marginal orientation diagnostic"
    )
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    OUT.mkdir(exist_ok=True)
    reports = {game: run(game, args.workers) for game in ("ssq", "dlt")}
    selected = {
        game: {
            zone: {
                "candidate": report["stable_orientation_experiment"]["selection_results"][zone][
                    "selected_candidate"
                ],
                "stable": report["stable_orientation_experiment"]["selection_results"][zone][
                    "stable"
                ],
                "fallback_used": report["stable_orientation_experiment"]["selection_results"][
                    zone
                ]["fallback_used"],
            }
            for zone in ("front", "back")
        }
        for game, report in reports.items()
    }
    failed = [
        {"game": game, "zone": zone}
        for game, report in reports.items()
        for zone, passed in report["acceptance"]["evaluation_zone_pass"].items()
        if not passed
    ]
    summary = {
        "artifact_type": "phase4e16_stable_per_number_orientation_summary",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "games": reports,
        "selected_orientations": selected,
        "unstable_game_zones": [
            {"game": game, "zone": zone}
            for game, zones in selected.items()
            for zone, value in zones.items()
            if not bool(value["stable"])
        ],
        "acceptance_rule": "all games and zones pass individual-number rho>0 and slope>0 on last-60 frozen outer evaluation",
        "failed_game_zones": failed,
        "accepted_all_games_zones": not failed,
        "expansion_stopped_if_dlt_front_failed": any(
            value == {"game": "dlt", "zone": "front"} for value in failed
        ),
    }
    (OUT / "summary.json").write_bytes(canonical(summary))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "selected_orientations": selected,
                "unstable_game_zones": summary["unstable_game_zones"],
                "failed_game_zones": failed,
                "accepted_all_games_zones": summary["accepted_all_games_zones"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
