#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import multiprocessing
import os
import statistics
import struct
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4e16"))
import run_stable_orientation as e16

from lottery_system.phase4e3 import model as p4e3
from lottery_system.phase4.bonus import (
    DLT_NEW_FIXED_PRIZES,
    DLT_OLD_FIXED_PRIZES,
    SSQ_FIXED_PRIZES,
    fixed_bonus,
    registered_rule_version,
)

OUT = ROOT / "artifacts/phase4e17"
E13 = ROOT / "artifacts/phase4e13"
E14 = ROOT / "artifacts/phase4e14"
E15 = ROOT / "artifacts/phase4e15"
E16 = ROOT / "artifacts/phase4e16"
SOURCE = ROOT / "artifacts/phase-4e4/data-20260819/canonical"
ORACLE = ROOT / "scripts/phase4_independent/p4e2_oracle.py"
P4E3_MODEL = ROOT / "src/lottery_system/phase4e3/model.py"
P4E3_DLT_SELECTION = (
    ROOT / "artifacts/phase-4e3/delivery-20260819/selection/dlt-selection-receipt.json"
)
E13_SCRIPT = ROOT / "scripts/phase4e13/run_partial_hit_evaluation.py"
E14_SCRIPT = ROOT / "scripts/phase4e14/run_confidence_calibration.py"
E15_SCRIPT = ROOT / "scripts/phase4e15/run_number_orientation.py"
E16_SCRIPT = ROOT / "scripts/phase4e16/run_stable_orientation.py"
E17_SCRIPT = Path(__file__).resolve()

TARGET_GAME = "dlt"
TARGET_ZONE = "front"
TARGET_ZONE_INDEX = 0
OUTER_DRAWS = 120
OUTER_CALIBRATION_DRAWS = 60
SELECTION_DRAWS = 240
SELECTION_BLOCK_DRAWS = 60
SELECTION_BLOCKS = 4
STABLE_POSITIVE_BLOCKS = 3
ZONE_SIZES = e16.ZONE_SIZES
TICKET_PARTITION_SIZES = (1000, 5000, 10000, 20000, 30000, 40000, 50000, 60000, 70000, 80000, 90000, 100000)

# The candidate order and every configuration are registered before any E17 label is
# read.  The two model configurations reuse the conservative and strongest DLT
# Phase4E3 receipt settings; there is no E17 hyperparameter grid.
CANDIDATE_SPECS: tuple[dict[str, object], ...] = (
    {
        "candidate_id": "raw_descending_marginal_mass",
        "kind": "phase4e16_control",
        "feature_ids": [],
        "configuration": None,
        "justification": "unchanged Phase4E16 raw descending marginal-mass control",
    },
    {
        "candidate_id": "reverse_ascending_marginal_mass_control",
        "kind": "phase4e16_control",
        "feature_ids": [],
        "configuration": None,
        "justification": "unchanged Phase4E16 signed reverse-orientation control",
    },
    {
        "candidate_id": "p4e3_surprise_renewal_nonlinear",
        "kind": "phase4e3_walk_forward_model",
        "feature_ids": ["E01", "E02", "E03", "E04", "N01"],
        "configuration": {
            "history": 120,
            "l2": 36.0,
            "temperature": 0.5,
            "graph_window": 80,
            "pair_shrinkage": 20.0,
            "purge": 2,
        },
        "justification": "bounded surprise/renewal subset with its preregistered N01 interaction and the frozen conservative DLT Phase4E3 C01 configuration",
    },
    {
        "candidate_id": "p4e3_transition_graph_nonlinear",
        "kind": "phase4e3_walk_forward_model",
        "feature_ids": ["E05", "E06", "E07", "E08", "N02"],
        "configuration": {
            "history": 120,
            "l2": 4.0,
            "temperature": 0.5,
            "graph_window": 80,
            "pair_shrinkage": 20.0,
            "purge": 2,
        },
        "justification": "bounded transition/graph subset with its preregistered N02 interaction and the frozen strongest DLT Phase4E3 C03 configuration",
    },
)
CANDIDATE_ORDER = tuple(str(spec["candidate_id"]) for spec in CANDIDATE_SPECS)
SPEC_BY_ID = {str(spec["candidate_id"]): spec for spec in CANDIDATE_SPECS}
MODEL_CANDIDATES = tuple(
    candidate
    for candidate in CANDIDATE_ORDER
    if str(SPEC_BY_ID[candidate]["kind"]) == "phase4e3_walk_forward_model"
)
AVAILABLE_FEATURE_IDS = tuple(f"E{index:02d}" for index in range(1, 9)) + ("N01", "N02")

canonical = e16.canonical
digest = e16.digest
sha256 = e16.sha256
read_json = e16.read_json
read_jsonl = e16.read_jsonl
row_identity = e16.row_identity
outer_identity_digest = e16.outer_identity_digest

_E16_INNER_CACHE: dict[str, list[dict[str, object]]] = {}
_E16_OUTER_CACHE: dict[str, list[dict[str, object]]] = {}


def candidate_registry() -> list[dict[str, object]]:
    return copy.deepcopy(list(CANDIDATE_SPECS))


def score_vector_sha256(scores: Sequence[float]) -> str:
    return hashlib.sha256(b"".join(struct.pack("<d", float(value)) for value in scores)).hexdigest()


def e16_rows(game: str, kind: str) -> list[dict[str, object]]:
    cache = _E16_INNER_CACHE if kind == "inner" else _E16_OUTER_CACHE
    if game not in cache:
        cache[game] = read_jsonl(E16 / game / f"{kind}-rolling-report.jsonl")
    return cache[game]


def e16_report(game: str) -> dict[str, object]:
    return read_json(E16 / game / "report.json")


def validate_registry() -> None:
    if len(CANDIDATE_ORDER) != len(set(CANDIDATE_ORDER)) or len(CANDIDATE_ORDER) != 4:
        raise ValueError("FAIL_PHASE4E17_BOUNDED_CANDIDATE_REGISTRY")
    if CANDIDATE_ORDER[:2] != e16.CANDIDATE_ORDER:
        raise ValueError("FAIL_PHASE4E17_E16_CONTROL_ORDER")
    feature_ids = [
        str(feature)
        for candidate in CANDIDATE_SPECS
        for feature in candidate["feature_ids"]
    ]
    if tuple(sorted(feature_ids)) != tuple(sorted(AVAILABLE_FEATURE_IDS)):
        raise ValueError("FAIL_PHASE4E17_FEATURE_PARTITION")
    if len(feature_ids) != len(set(feature_ids)):
        raise ValueError("FAIL_PHASE4E17_FEATURE_SUBSETS_OVERLAP")
    for feature_id in feature_ids:
        if feature_id not in p4e3.FEATURE_DEFINITIONS:
            raise ValueError("FAIL_PHASE4E17_UNKNOWN_FEATURE")
    receipt = read_json(P4E3_DLT_SELECTION)
    c01 = receipt["families"]["C01_SURPRISE_REGIME"]["final_config"]
    c03 = receipt["families"]["C03_TRANSITION"]["final_config"]
    expected = ((120, 36.0, 0.5), (120, 4.0, 0.5))
    observed = tuple(
        (
            int(config["history"]),
            float(config["l2"]),
            float(config["temperature"]),
        )
        for config in (
            SPEC_BY_ID[MODEL_CANDIDATES[0]]["configuration"],
            SPEC_BY_ID[MODEL_CANDIDATES[1]]["configuration"],
        )
    )
    receipt_values = (
        (int(c01["history"]), float(c01["l2"]), float(c01["temperature"])),
        (int(c03["history"]), float(c03["l2"]), float(c03["temperature"])),
    )
    if observed != expected or observed != receipt_values:
        raise ValueError("FAIL_PHASE4E17_FROZEN_P4E3_CONFIGURATIONS")


def control_scores(source: dict[str, object], candidate_id: str) -> list[float]:
    marginal = source["zones"][TARGET_ZONE]["marginal_probabilities"]
    return [
        e16.e15.orientation_score(candidate_id, float(value["inclusion_mass"]))
        for value in marginal
    ]


def walk_forward_model_output(
    game: str,
    data: Sequence[object],
    target: int,
    spec: dict[str, object],
) -> dict[str, object]:
    if game != TARGET_GAME or target <= 0 or target > len(data):
        raise ValueError("FAIL_PHASE4E17_MODEL_TARGET")
    if str(spec["kind"]) != "phase4e3_walk_forward_model":
        raise ValueError("FAIL_PHASE4E17_MODEL_SPEC")
    # Passing only the strict prefix makes target/future labels structurally
    # unavailable to both fitting and feature construction.
    prefix = list(data[:target])
    config = spec["configuration"]
    fitted = p4e3.fit_zone(
        game,
        prefix,
        len(prefix),
        TARGET_ZONE_INDEX,
        tuple(str(feature) for feature in spec["feature_ids"]),
        history=int(config["history"]),
        l2=float(config["l2"]),
        temperature=float(config["temperature"]),
        graph_window=int(config["graph_window"]),
        pair_shrinkage=float(config["pair_shrinkage"]),
        purge=int(config["purge"]),
    )
    distribution = p4e3.zone_distribution(game, prefix, TARGET_ZONE_INDEX, fitted)
    context = distribution["context"]
    expected_prefix_hash = p4e3.digest([draw.fact_hash for draw in prefix])
    maximum_training_label = int(fitted["max_training_label_position"])
    maximum_feature_source = int(context["max_source_position"])
    if maximum_training_label > target - 1 or maximum_feature_source != target - 1:
        raise ValueError("FAIL_PHASE4E17_WALK_FORWARD_STRICT_LAG")
    if maximum_training_label != target - int(config["purge"]) - 1:
        raise ValueError("FAIL_PHASE4E17_MODEL_PURGE")
    if (
        str(fitted["training_input_sha256"]) != expected_prefix_hash
        or str(context["input_prefix_sha256"]) != expected_prefix_hash
        or int(context["source_draw_count"]) != target
        or str(context["max_source_issue"]) != str(prefix[-1].issue)
    ):
        raise ValueError("FAIL_PHASE4E17_STRICT_PREFIX_IDENTITY")
    scores = [float(value) for value in distribution["inclusion_probabilities"]]
    expected_cardinality = int(distribution["k"])
    if len(scores) != int(distribution["n"]) or not math.isclose(
        math.fsum(scores), float(expected_cardinality), rel_tol=0.0, abs_tol=1e-10
    ):
        raise ValueError("FAIL_PHASE4E17_MODEL_MARGINAL_NORMALIZATION")
    return {
        "scores": scores,
        "ranking": sorted(range(1, len(scores) + 1), key=lambda number: (-scores[number - 1], number)),
        "score_vector_sha256_float64_le": score_vector_sha256(scores),
        "walk_forward_fit": {
            "api": "lottery_system.phase4e3.model.fit_zone+zone_distribution",
            "feature_ids": list(fitted["feature_ids"]),
            "coefficients": list(fitted["coefficients"]),
            "coefficient_sha256": digest(list(fitted["coefficients"])),
            "configuration": copy.deepcopy(config),
            "estimator": fitted["estimator"],
            "optimizer": fitted["optimizer"],
            "training_target_positions": list(fitted["training_target_positions"]),
            "max_training_label_position": maximum_training_label,
            "maximum_feature_source_position": maximum_feature_source,
            "maximum_feature_source_issue": context["max_source_issue"],
            "strict_prefix_draws": len(prefix),
            "strict_prefix_sha256": expected_prefix_hash,
            "fit_training_input_sha256": fitted["training_input_sha256"],
            "distribution_context_input_sha256": context["input_prefix_sha256"],
            "target_or_future_label_available_to_fit": False,
        },
    }


def target_candidate_output(
    game: str, target: int, source: dict[str, object], candidate_id: str
) -> dict[str, object]:
    spec = SPEC_BY_ID[candidate_id]
    if str(spec["kind"]) == "phase4e16_control":
        scores = control_scores(source, candidate_id)
        ranking = sorted(
            range(1, len(scores) + 1), key=lambda number: (-scores[number - 1], number)
        )
        return {
            "candidate_id": candidate_id,
            "candidate_kind": spec["kind"],
            "feature_ids": [],
            "scores": scores,
            "ranking": ranking,
            "score_vector_sha256_float64_le": score_vector_sha256(scores),
            "walk_forward_fit": None,
            "strict_prefix": True,
            "maximum_feature_source_position": target - 1,
            "maximum_training_label_position": target - 1,
            "source": "unchanged_phase4e16_e13_marginal_output",
        }
    model = walk_forward_model_output(game, e16.e13.load(game), target, spec)
    return {
        "candidate_id": candidate_id,
        "candidate_kind": spec["kind"],
        "feature_ids": list(spec["feature_ids"]),
        **model,
        "strict_prefix": True,
        "maximum_feature_source_position": model["walk_forward_fit"][
            "maximum_feature_source_position"
        ],
        "maximum_training_label_position": model["walk_forward_fit"][
            "max_training_label_position"
        ],
        "source": "phase4e3_walk_forward_fit_zone_distribution",
    }


def compute_selection_row(target: int) -> dict[str, object]:
    data = e16.e13.load(TARGET_GAME)
    source_rows = e16_rows(TARGET_GAME, "inner")
    outer_start = len(data) - OUTER_DRAWS
    selection_start = outer_start - SELECTION_DRAWS
    if target < selection_start or target >= outer_start:
        raise ValueError("FAIL_PHASE4E17_SELECTION_TARGET")
    source = source_rows[target - selection_start]
    if (str(source["issue"]), int(source["target_position"])) != (
        str(data[target].issue),
        target,
    ):
        raise ValueError("FAIL_PHASE4E17_E16_SELECTION_ROW_IDENTITY")
    candidate_outputs = {
        candidate_id: target_candidate_output(TARGET_GAME, target, source, candidate_id)
        for candidate_id in CANDIDATE_ORDER
    }
    # Labels are attached only after all candidate scores have been generated from
    # their strict prefixes.
    actual = {int(number) for number in source["zones"][TARGET_ZONE]["actual_numbers"]}
    for output in candidate_outputs.values():
        output["number_observations"] = [
            {
                "number": number,
                "candidate_score": float(output["scores"][number - 1]),
                "binary_hit": int(number in actual),
            }
            for number in range(1, len(output["scores"]) + 1)
        ]
        output["scores"] = [float(value) for value in output["scores"]]
    block_index = (target - selection_start) // SELECTION_BLOCK_DRAWS + 1
    result = {
        "game": TARGET_GAME,
        "zone": TARGET_ZONE,
        "issue": data[target].issue,
        "target_position": target,
        "maximum_feature_source_position": target - 1,
        "maximum_feature_source_issue": data[target - 1].issue,
        "strict_lag": True,
        "selection_block": f"chronological_block_{block_index}",
        "selection_block_index": block_index,
        "outer_label_used_for_candidate_selection": False,
        "target_label_available_to_candidate_fit": False,
        "phase4e16_source_row_sha256": digest(source),
        "actual_numbers": sorted(actual),
        "candidates": candidate_outputs,
    }
    if any(
        int(value["maximum_feature_source_position"]) != target - 1
        or int(value["maximum_training_label_position"]) > target - 1
        for value in candidate_outputs.values()
    ):
        raise ValueError("FAIL_PHASE4E17_SELECTION_ROW_STRICT_LAG")
    p4e3._TRAINING_SAMPLE_CACHE.clear()
    return result


def compute_selection_rows(workers: int) -> list[dict[str, object]]:
    source = e16_rows(TARGET_GAME, "inner")
    if len(source) != SELECTION_DRAWS:
        raise ValueError("FAIL_PHASE4E17_E16_SELECTION_LENGTH")
    targets = [int(row["target_position"]) for row in source]
    expected = e16.selection_targets(TARGET_GAME)
    if targets != expected:
        raise ValueError("FAIL_PHASE4E17_E16_SELECTION_TARGET_IDENTITY")
    if workers == 1:
        rows = [compute_selection_row(target) for target in targets]
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=workers) as pool:
            rows = pool.map(compute_selection_row, targets, chunksize=1)
    rows.sort(key=lambda row: int(row["target_position"]))
    if row_identity(rows) != row_identity(source):
        raise ValueError("FAIL_PHASE4E17_SELECTION_IDENTITY")
    return rows


def candidate_observations(
    rows: Sequence[dict[str, object]], candidate_id: str
) -> list[dict[str, object]]:
    return [
        {
            "issue": row["issue"],
            "target_position": row["target_position"],
            **observation,
        }
        for row in rows
        for observation in row["candidates"][candidate_id]["number_observations"]
    ]


def select_dlt_front_candidate(
    inner_rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    if len(inner_rows) != SELECTION_DRAWS:
        raise ValueError("FAIL_PHASE4E17_SELECTION_WINDOW")
    blocks = [
        inner_rows[index * SELECTION_BLOCK_DRAWS : (index + 1) * SELECTION_BLOCK_DRAWS]
        for index in range(SELECTION_BLOCKS)
    ]
    if any(len(block) != SELECTION_BLOCK_DRAWS for block in blocks):
        raise ValueError("FAIL_PHASE4E17_SELECTION_BLOCK_LENGTH")
    candidates = []
    for order, candidate_id in enumerate(CANDIDATE_ORDER, start=1):
        block_metrics = []
        for block_index, block in enumerate(blocks, start=1):
            metrics = e16.e15.association_metrics(
                candidate_observations(block, candidate_id), "candidate_score", "binary_hit"
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
        positive_count = sum(bool(block["positive_rho_and_slope"]) for block in block_metrics)
        median_rho = statistics.median(
            float(block["metrics"]["spearman_rho"]) for block in block_metrics
        )
        median_slope = statistics.median(
            float(block["metrics"]["descriptive_linear_association"]["slope"])
            for block in block_metrics
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_order": order,
                "candidate_kind": SPEC_BY_ID[candidate_id]["kind"],
                "feature_ids": list(SPEC_BY_ID[candidate_id]["feature_ids"]),
                "configuration": copy.deepcopy(SPEC_BY_ID[candidate_id]["configuration"]),
                "positive_block_count": positive_count,
                "required_positive_block_count": STABLE_POSITIVE_BLOCKS,
                "median_spearman_rho": median_rho,
                "median_descriptive_slope": median_slope,
                "stable_eligible": positive_count >= STABLE_POSITIVE_BLOCKS,
                "blocks": block_metrics,
            }
        )
    eligible = [candidate for candidate in candidates if bool(candidate["stable_eligible"])]
    if eligible:
        winner = max(
            eligible,
            key=lambda candidate: (
                float(candidate["median_spearman_rho"]),
                -int(candidate["candidate_order"]),
            ),
        )
        selected = str(winner["candidate_id"])
        stable = True
        status = "stable_selected"
    else:
        selected = str(
            e16_report(TARGET_GAME)["stable_orientation_experiment"]["selection_results"][
                TARGET_ZONE
            ]["selected_candidate"]
        )
        stable = False
        status = "unstable_phase4e16_fallback"
    selection_identity = digest(row_identity(inner_rows))
    selection_id = (
        f"p4e17-{status}-{selected}-"
        f"{digest({'candidate': selected, 'selection_identity': selection_identity, 'registry': digest(candidate_registry())})[:16]}"
    )
    return {
        "game": TARGET_GAME,
        "zone": TARGET_ZONE,
        "selection_rule": "candidate must have rho>0 and slope>0 in at least 3 of 4 chronological 60-target blocks; eligible candidates are ordered by median rho, then deterministic registered candidate order",
        "selection_uses_outer_labels": False,
        "selection_status": status,
        "stable": stable,
        "fallback_used": not stable,
        "selected_candidate": selected,
        "selected_candidate_kind": SPEC_BY_ID[selected]["kind"],
        "selected_feature_ids": list(SPEC_BY_ID[selected]["feature_ids"]),
        "selected_configuration": copy.deepcopy(SPEC_BY_ID[selected]["configuration"]),
        "selected_model_id": selection_id,
        "registered_phase4e16_fallback_candidate": e16_report(TARGET_GAME)[
            "stable_orientation_experiment"
        ]["selection_results"][TARGET_ZONE]["selected_candidate"],
        "candidates": candidates,
    }


def compact_inherited_inner_rows(game: str) -> list[dict[str, object]]:
    rows = []
    for source in e16_rows(game, "inner"):
        rows.append(
            {
                "game": game,
                "issue": source["issue"],
                "target_position": source["target_position"],
                "maximum_training_position": source["maximum_training_position"],
                "maximum_feature_source_position": source["maximum_training_position"],
                "strict_lag": source["strict_lag"],
                "selection_block": source["selection_block"],
                "selection_block_index": source["selection_block_index"],
                "outer_label_used_for_candidate_selection": False,
                "phase4e17_scope": "identity_only_all_game_zones_inherit_phase4e16",
                "phase4e16_source_row_sha256": digest(source),
            }
        )
    return rows


def validate_frozen_outer(game: str) -> list[dict[str, object]]:
    rows16 = e16_rows(game, "outer")
    if len(rows16) != OUTER_DRAWS:
        raise ValueError("FAIL_PHASE4E17_OUTER_LENGTH")
    identity_hash = outer_identity_digest(rows16)
    for phase in (E13, E14, E15, E16):
        rows = read_jsonl(phase / game / "outer-rolling-report.jsonl")
        report = read_json(phase / game / "report.json")
        if row_identity(rows) != row_identity(rows16):
            raise ValueError("FAIL_PHASE4E17_FROZEN_OUTER_IDENTITY")
        if identity_hash != str(report["outer_window"]["identity_sha256"]):
            raise ValueError("FAIL_PHASE4E17_FROZEN_OUTER_IDENTITY_HASH")
    for row in rows16:
        if not bool(row["strict_lag"]) or int(row["maximum_training_position"]) != int(
            row["target_position"]
        ) - 1:
            raise ValueError("FAIL_PHASE4E17_OUTER_STRICT_LAG")
    return rows16


def selected_outer_prediction(task: tuple[int, str]) -> dict[str, object]:
    target, candidate_id = task
    data = e16.e13.load(TARGET_GAME)
    outer_start = len(data) - OUTER_DRAWS
    if target < outer_start or target >= len(data):
        raise ValueError("FAIL_PHASE4E17_OUTER_TARGET")
    source = e16_rows(TARGET_GAME, "outer")[target - outer_start]
    output = target_candidate_output(TARGET_GAME, target, source, candidate_id)
    p4e3._TRAINING_SAMPLE_CACHE.clear()
    return {
        "issue": data[target].issue,
        "target_position": target,
        "candidate": output,
    }


def compute_selected_outer_predictions(
    selection: dict[str, object], workers: int
) -> dict[int, dict[str, object]]:
    candidate_id = str(selection["selected_candidate"])
    targets = [int(row["target_position"]) for row in e16_rows(TARGET_GAME, "outer")]
    tasks = [(target, candidate_id) for target in targets]
    if workers == 1:
        rows = [selected_outer_prediction(task) for task in tasks]
    else:
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=workers) as pool:
            rows = pool.map(selected_outer_prediction, tasks, chunksize=1)
    rows.sort(key=lambda row: int(row["target_position"]))
    if [int(row["target_position"]) for row in rows] != targets:
        raise ValueError("FAIL_PHASE4E17_OUTER_PREDICTION_IDENTITY")
    return {int(row["target_position"]): row["candidate"] for row in rows}


def confidence_set_from_scores(
    source: dict[str, object], scores: Sequence[float], ranking: Sequence[int], size: int
) -> dict[str, object]:
    actual = {int(number) for number in source["actual_numbers"]}
    raw_mass = {
        int(value["number"]): float(value["inclusion_mass"])
        for value in source["marginal_probabilities"]
    }
    ranked = list(ranking[:size])
    selected = sorted(ranked)
    overlap = len(actual.intersection(selected))
    score_sum = math.fsum(float(scores[number - 1]) for number in selected)
    raw_sum = math.fsum(raw_mass[number] for number in selected)
    zone_draw_count = int(source["zone_draw_count"])
    return {
        "set_size": size,
        "ranked_numbers": ranked,
        "selected_numbers": selected,
        "overlap_count": overlap,
        "single_group_hit_count": overlap,
        "single_group_number_count": size,
        "single_group_hit_rate": overlap / size,
        "exact_all_zone_numbers_hit": overlap == zone_draw_count,
        "raw_marginal_inclusion_mass_sum": raw_sum,
        "raw_marginal_confidence_mass": raw_sum / zone_draw_count,
        "candidate_score_sum": score_sum,
        "mean_candidate_score": score_sum / size,
        "orientation_score_sum": score_sum,
        "mean_orientation_score": score_sum / size,
        "score_is_true_lottery_probability": False,
    }


def selected_zone_output(
    source: dict[str, object], prediction: dict[str, object], selection: dict[str, object]
) -> dict[str, object]:
    scores = [float(value) for value in prediction["scores"]]
    ranking = [int(number) for number in prediction["ranking"]]
    actual = {int(number) for number in source["actual_numbers"]}
    raw_mass = {
        int(value["number"]): float(value["inclusion_mass"])
        for value in source["marginal_probabilities"]
    }
    selected_rank = {number: rank for rank, number in enumerate(ranking, start=1)}
    return {
        "decision_origin": "phase4e17_dlt_front_pre_outer_stability_selection",
        "selected_candidate": selection["selected_candidate"],
        "selected_candidate_kind": selection["selected_candidate_kind"],
        "selected_feature_ids": selection["selected_feature_ids"],
        "selected_configuration": selection["selected_configuration"],
        "selected_model_id": selection["selected_model_id"],
        "stable": selection["stable"],
        "fallback_used": selection["fallback_used"],
        "canonical_ascending_number_tie_break": True,
        "selected_orientation_ranking": ranking,
        "score_vector_sha256_float64_le": prediction["score_vector_sha256_float64_le"],
        "walk_forward_fit": prediction["walk_forward_fit"],
        "number_observations": [
            {
                "number": number,
                "raw_e13_marginal_inclusion_mass": raw_mass[number],
                "candidate_score": scores[number - 1],
                "orientation_score": scores[number - 1],
                "binary_hit": int(number in actual),
                "selected_rank": selected_rank[number],
            }
            for number in range(1, len(scores) + 1)
        ],
        "confidence_sets": {
            str(size): confidence_set_from_scores(source, scores, ranking, size)
            for size in ZONE_SIZES[TARGET_ZONE]
        },
        "score_is_true_lottery_probability": False,
    }


def inherited_zone_output(source: dict[str, object], zone: str) -> dict[str, object]:
    inherited = copy.deepcopy(source["phase4e16_stable_orientation"]["zones"][zone])
    inherited["decision_origin"] = "phase4e16_inherited_unchanged"
    inherited["selected_model_id"] = inherited.pop("selected_orientation_id")
    inherited["selected_candidate_kind"] = "phase4e16_inherited_orientation"
    inherited["selected_feature_ids"] = []
    inherited["selected_configuration"] = None
    inherited["score_is_true_lottery_probability"] = False
    inherited["walk_forward_fit"] = None
    for observation in inherited["number_observations"]:
        observation["raw_e13_marginal_inclusion_mass"] = observation.pop(
            "marginal_inclusion_mass"
        )
        observation["candidate_score"] = observation["orientation_score"]
        observation["selected_rank"] = observation.pop("selected_orientation_rank")
    for value in inherited["confidence_sets"].values():
        value["candidate_score_sum"] = value["orientation_score_sum"]
        value["mean_candidate_score"] = value["mean_orientation_score"]
        value["single_group_hit_count"] = int(value.pop("predicted_number_hits"))
        value["single_group_number_count"] = int(value.pop("predicted_number_trials"))
        value["single_group_hit_rate"] = float(value.pop("predicted_number_hit_rate"))
        value.pop("actual_number_coverage_rate", None)
        value.pop("any_number_hit", None)
    return inherited


def decorate_outer_rows(
    game: str,
    rows: Sequence[dict[str, object]],
    selection: dict[str, object],
    predictions: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    decorated = copy.deepcopy(list(rows))
    for row in decorated:
        zones = {}
        for zone in ("front", "back"):
            if game == TARGET_GAME and zone == TARGET_ZONE:
                prediction = predictions[int(row["target_position"])]
                zones[zone] = selected_zone_output(row["zones"][zone], prediction, selection)
            else:
                zones[zone] = inherited_zone_output(row, zone)
        row["phase4e17_per_number_feature_model"] = {
            "strict_lag": True,
            "maximum_feature_source_position": int(row["target_position"]) - 1,
            "outer_label_used_for_candidate_selection": False,
            "selection_completed_before_outer_rows_loaded": True,
            "zones": zones,
        }
    return decorated


def phase17_observations(
    rows: Sequence[dict[str, object]], zone: str
) -> list[dict[str, object]]:
    return [
        {
            "issue": row["issue"],
            "target_position": row["target_position"],
            "number": observation["number"],
            "candidate_score": observation["candidate_score"],
            "binary_hit": observation["binary_hit"],
        }
        for row in rows
        for observation in row["phase4e17_per_number_feature_model"]["zones"][zone][
            "number_observations"
        ]
    ]


def fixed_size_metrics(
    rows: Sequence[dict[str, object]], zone: str, size: int
) -> dict[str, object]:
    values = []
    selected_observations = []
    for row in rows:
        zone17 = row["phase4e17_per_number_feature_model"]["zones"][zone]
        value = zone17["confidence_sets"][str(size)]
        values.append({"issue": str(row["issue"]), **value})
        observations = {
            int(observation["number"]): observation
            for observation in zone17["number_observations"]
        }
        for number in value["ranked_numbers"]:
            observation = observations[int(number)]
            selected_observations.append(
                {
                    "issue": str(row["issue"]),
                    "number": int(number),
                    "candidate_score": float(observation["candidate_score"]),
                    "binary_hit": int(observation["binary_hit"]),
                }
            )
    draws = len(rows)
    group_rates = [
        {
            "issue": value["issue"],
            "hit_count": int(value["single_group_hit_count"]),
            "number_count": int(value["single_group_number_count"]),
            "hit_rate": float(value["single_group_hit_rate"]),
        }
        for value in values
    ]
    best_group = max(group_rates, key=lambda value: (value["hit_rate"], value["issue"]))
    overlap_total = sum(int(value["overlap_count"]) for value in values)
    exact_hits = sum(bool(value["exact_all_zone_numbers_hit"]) for value in values)
    return {
        "draws": draws,
        "set_size": size,
        "group_count": len(group_rates),
        "groups": group_rates,
        "best_single_group_hit_rate": float(best_group["hit_rate"]),
        "best_single_group_issue": best_group["issue"],
        "best_single_group_hit_count": int(best_group["hit_count"]),
        "best_single_group_number_count": int(best_group["number_count"]),
        "overlap_total_descriptive_only": overlap_total,
        "exact_all_zone_numbers_hit_count_descriptive_only": exact_hits,
        "selected_number_observation_association": e16.e15.association_metrics(
            selected_observations, "candidate_score", "binary_hit"
        ),
        "fixed_set_score_vs_overlap_association": e16.e15.set_level_association(
            values, size
        ),
        "wilson_intervals_are_descriptive": True,
    }


def per_canonical_number_metrics(
    rows: Sequence[dict[str, object]], zone: str, sizes: Sequence[int]
) -> list[dict[str, object]]:
    number_pool_size = int(rows[0]["zones"][zone]["number_pool_size"])
    result = []
    for number in range(1, number_pool_size + 1):
        observations = []
        selected_by_size = {size: [] for size in sizes}
        for row in rows:
            zone17 = row["phase4e17_per_number_feature_model"]["zones"][zone]
            observation = next(
                value
                for value in zone17["number_observations"]
                if int(value["number"]) == number
            )
            observations.append({"issue": row["issue"], **observation})
            for size in sizes:
                value = zone17["confidence_sets"][str(size)]
                if number in value["selected_numbers"]:
                    selected_by_size[size].append(int(observation["binary_hit"]))
        association = e16.e15.association_metrics(
            observations, "candidate_score", "binary_hit"
        )
        actual_hits = sum(int(value["binary_hit"]) for value in observations)
        fixed_sizes = {}
        for size in sizes:
            hits = sum(selected_by_size[size])
            trials = len(selected_by_size[size])
            fixed_sizes[str(size)] = {
                "selected_draws": trials,
                "selected_and_actual_overlap_count": hits,
                "conditional_predicted_number_hit_rate": hits / trials if trials else None,
                "conditional_predicted_number_hit_rate_wilson95": e16.e13.wilson(
                    hits, trials
                )
                if trials
                else None,
            }
        result.append(
            {
                "number": number,
                "draws": len(rows),
                "actual_appearance_count": actual_hits,
                "actual_appearance_rate": actual_hits / len(rows),
                "actual_appearance_rate_wilson95": e16.e13.wilson(
                    actual_hits, len(rows)
                ),
                "mean_candidate_score": math.fsum(
                    float(value["candidate_score"]) for value in observations
                )
                / len(observations),
                "spearman_rho": association["spearman_rho"],
                "descriptive_slope": association["descriptive_linear_association"][
                    "slope"
                ],
                "positive_association": association["positive_association"],
                "fixed_sizes": fixed_sizes,
            }
        )
    return result


def ticket_prize(
    game: str, issue: str, front_hits: int, back_hits: int,
    *, prize_rule_version: str | None = None, **metadata: object,
) -> dict[str, object]:
    """Return the rule-derived tier and known fixed amount for one complete ticket.

    First/second prizes use the configured fixed benchmark amounts requested for
    this experiment.
    """
    version = prize_rule_version or registered_rule_version(game, issue)
    return fixed_bonus(game, version, front_hits, back_hits, issue=issue, **metadata)


def _choose(n: int, k: int) -> int:
    return math.comb(n, k) if 0 <= k <= n else 0


def _ranked_zone_combinations(scores: Sequence[float], choose: int) -> list[tuple[float, tuple[int, ...]]]:
    import itertools
    ranked = [
        (math.fsum(float(scores[number - 1]) for number in combo), tuple(combo))
        for combo in itertools.combinations(range(1, len(scores) + 1), choose)
    ]
    ranked.sort(key=lambda value: (-value[0], value[1]))
    return ranked


def ranked_ticket_partition_prize_metrics(
    row: dict[str, object], game: str, partition_sizes: Sequence[int] = TICKET_PARTITION_SIZES
) -> dict[str, object]:
    """Evaluate top-N complete tickets ranked by additive front/back model score."""
    import heapq
    import itertools
    front_zone = row["phase4e17_per_number_feature_model"]["zones"]["front"]
    back_zone = row["phase4e17_per_number_feature_model"]["zones"]["back"]
    front_scores = [float(value["candidate_score"]) for value in front_zone["number_observations"]]
    back_scores = [float(value["candidate_score"]) for value in back_zone["number_observations"]]
    front_ranked = _ranked_zone_combinations(front_scores, 6 if game == "ssq" else 5)
    back_ranked = _ranked_zone_combinations(back_scores, 1 if game == "ssq" else 2)
    max_n = min(max(partition_sizes), len(front_ranked) * len(back_ranked))
    heap: list[tuple[float, tuple[int, ...], tuple[int, ...], int, int]] = []
    for front_index, (front_score, front_combo) in enumerate(front_ranked):
        back_score, back_combo = back_ranked[0]
        heapq.heappush(heap, (-(front_score + back_score), front_combo, back_combo, front_index, 0))
    actual_front = set(map(int, row["zones"]["front"]["actual_numbers"]))
    actual_back = set(map(int, row["zones"]["back"]["actual_numbers"]))
    results = {int(size): {"partition_size": int(size), "known_prize_total_yuan": 0.0, "winning_ticket_count": 0, "best_single_ticket_hit_rate": 0.0, "best_single_ticket": None} for size in partition_sizes}
    total = 0.0
    winners = 0
    best_rate = 0.0
    best_ticket = None
    for rank in range(1, max_n + 1):
        neg_score, front_combo, back_combo, front_index, back_index = heapq.heappop(heap)
        front_hits = len(set(front_combo) & actual_front)
        back_hits = len(set(back_combo) & actual_back)
        prize = ticket_prize(game, str(row["issue"]), front_hits, back_hits)
        amount = float(prize["fixed_prize_yuan"] or 0.0)
        total += amount
        if amount > 0:
            winners += 1
        hit_rate = (front_hits + back_hits) / (len(front_combo) + len(back_combo))
        if hit_rate > best_rate:
            best_rate = hit_rate
            best_ticket = {"rank": rank, "front": list(front_combo), "back": list(back_combo), "front_hits": front_hits, "back_hits": back_hits, "hit_rate": hit_rate}
        if rank in results:
            results[rank].update({
                "known_prize_total_yuan": total,
                "average_prize_yuan": total / rank,
                "winning_ticket_count": winners,
                "best_single_ticket_hit_rate": best_rate,
                "best_single_ticket": best_ticket,
                "ranking_score_is_true_lottery_probability": False,
            })
        next_back = back_index + 1
        if next_back < len(back_ranked):
            back_score, next_back_combo = back_ranked[next_back]
            heapq.heappush(heap, (-(front_ranked[front_index][0] + back_score), front_combo, next_back_combo, front_index, next_back))
    return {
        "ranking_definition": "complete legal tickets ranked by additive front/back candidate scores",
        "primary_metric": "known_prize_total_yuan / partition_size",
        "partitions": results,
        "score_is_true_lottery_probability": False,
    }


def ticket_group_prize_metrics(
    rows: Sequence[dict[str, object]], front_size: int, back_size: int
) -> dict[str, object]:
    """Score the Cartesian product of front/back confidence sets as full tickets."""
    groups = []
    for row in rows:
        front = row["phase4e17_per_number_feature_model"]["zones"]["front"]["confidence_sets"][str(front_size)]
        back = row["phase4e17_per_number_feature_model"]["zones"]["back"]["confidence_sets"][str(back_size)]
        actual_front = set(map(int, row["zones"]["front"]["actual_numbers"]))
        actual_back = set(map(int, row["zones"]["back"]["actual_numbers"]))
        chosen_front = set(map(int, front["selected_numbers"]))
        chosen_back = set(map(int, back["selected_numbers"]))
        required_front = 6 if str(row.get("game", "dlt")) == "ssq" else 5
        required_back = 1 if str(row.get("game", "dlt")) == "ssq" else 2
        front_hits_in_set = len(chosen_front & actual_front)
        back_hits_in_set = len(chosen_back & actual_back)
        ticket_count = _choose(front_size, required_front) * _choose(back_size, required_back)
        tier_counts: dict[str, int] = {}
        known_total = 0.0
        floating_count = 0
        for hf in range(required_front + 1):
            front_ways = _choose(front_hits_in_set, hf) * _choose(front_size - front_hits_in_set, required_front - hf)
            if not front_ways:
                continue
            for hb in range(required_back + 1):
                count = front_ways * _choose(back_hits_in_set, hb) * _choose(back_size - back_hits_in_set, required_back - hb)
                if not count:
                    continue
                prize = ticket_prize(str(row.get("game", "dlt")), str(row["issue"]), hf, hb)
                tier = prize["prize_tier"]
                if tier is not None:
                    tier_counts[str(tier)] = tier_counts.get(str(tier), 0) + count
                amount = prize["fixed_prize_yuan"]
                if prize["is_floating_prize"]:
                    floating_count += count
                elif amount is not None:
                    known_total += float(amount) * count
        groups.append({
            "issue": str(row["issue"]),
            "front_size": front_size,
            "back_size": back_size,
            "ticket_count": ticket_count,
            "front_hit_count_in_set": front_hits_in_set,
            "back_hit_count_in_set": back_hits_in_set,
            "known_prize_total_yuan": known_total,
            "valid_complete_ticket_group": ticket_count > 0,
            "average_known_prize_yuan": known_total / ticket_count if ticket_count else None,
            "floating_prize_ticket_count": floating_count,
            "floating_prize_amounts_excluded": floating_count > 0,
            "prize_tier_ticket_counts": tier_counts,
        })
    total_tickets = sum(int(group["ticket_count"]) for group in groups)
    total_known = math.fsum(float(group["known_prize_total_yuan"]) for group in groups)
    return {
        "front_size": front_size,
        "back_size": back_size,
        "group_count": len(groups),
        "groups": groups,
        "total_ticket_count": total_tickets,
        "known_prize_total_yuan": total_known,
        "pooled_average_known_prize_yuan": total_known / total_tickets if total_tickets else 0.0,
        "mean_group_average_known_prize_yuan": (
            statistics.fmean(
                float(group["average_known_prize_yuan"])
                for group in groups
                if group["average_known_prize_yuan"] is not None
            )
            if any(group["average_known_prize_yuan"] is not None for group in groups)
            else None
        ),
        "floating_prize_ticket_count": sum(int(group["floating_prize_ticket_count"]) for group in groups),
        "floating_prize_amounts_excluded": any(bool(group["floating_prize_amounts_excluded"]) for group in groups),
        "definition": "complete Cartesian product of selected front/back numbers; known fixed prize total divided by complete ticket count",
    }


def split_metrics(rows: Sequence[dict[str, object]], game: str) -> dict[str, object]:
    result: dict[str, object] = {"draws": len(rows)}
    for zone, sizes in ZONE_SIZES.items():
        zone17 = rows[0]["phase4e17_per_number_feature_model"]["zones"][zone]
        individual = e16.e15.association_metrics(
            phase17_observations(rows, zone), "candidate_score", "binary_hit"
        )
        result[zone] = {
            "selected_candidate": zone17["selected_candidate"],
            "selected_candidate_kind": zone17["selected_candidate_kind"],
            "selected_model_id": zone17["selected_model_id"],
            "decision_origin": zone17["decision_origin"],
            "individual_number_association": individual,
            "fixed_size_set_metrics": {
                str(size): fixed_size_metrics(rows, zone, size) for size in sizes
            },
            "per_canonical_number_association": per_canonical_number_metrics(
                rows, zone, sizes
            ),
            "acceptance_pass": bool(individual["positive_association"]),
            "acceptance_uses_fixed_set_or_pooled_set_association": False,
        }
    result["ticket_group_average_prize_metrics"] = {
        str(front_size): {
            str(back_size): ticket_group_prize_metrics(rows, front_size, back_size)
            for back_size in ZONE_SIZES["back"]
        }
        for front_size in ZONE_SIZES["front"]
    }
    result["ranked_ticket_partition_prize_metrics"] = {
        str(row["issue"]): ranked_ticket_partition_prize_metrics(row, game)
        for row in rows
    }
    return result


def selection_window_metadata(inner_rows: Sequence[dict[str, object]]) -> dict[str, object]:
    source = e16_report(TARGET_GAME)["candidate_selection_window"]
    blocks = []
    for index in range(SELECTION_BLOCKS):
        block = inner_rows[
            index * SELECTION_BLOCK_DRAWS : (index + 1) * SELECTION_BLOCK_DRAWS
        ]
        source_block = source["blocks"][index]
        identity = digest(row_identity(block))
        if identity != str(source_block["identity_sha256"]):
            raise ValueError("FAIL_PHASE4E17_E16_BLOCK_IDENTITY")
        blocks.append(
            {
                **source_block,
                "identity_sha256": identity,
                "outer_labels_used": False,
                "all_candidate_fits_strict_prefix": True,
            }
        )
    identity = digest(row_identity(inner_rows))
    if identity != str(source["identity_sha256"]):
        raise ValueError("FAIL_PHASE4E17_E16_SELECTION_IDENTITY_HASH")
    return {
        **source,
        "identity_sha256": identity,
        "blocks": blocks,
        "outer_labels_used_for_candidate_selection": False,
        "selection_scope": "dlt_front_only",
        "all_candidate_fits_strict_prefix": True,
        "all_maximum_feature_source_positions_equal_target_minus_one": all(
            int(row["maximum_feature_source_position"]) == int(row["target_position"]) - 1
            for row in inner_rows
        ),
    }


def lineage(game: str, inner_path: Path, outer_path: Path) -> dict[str, object]:
    source_path = SOURCE / f"{game}.jsonl"
    result: dict[str, object] = {
        "source_data_path": str(source_path.relative_to(ROOT)),
        "source_data_sha256": sha256(source_path),
        "registered_p4e2_oracle_path": str(ORACLE.relative_to(ROOT)),
        "registered_p4e2_oracle_sha256": sha256(ORACLE),
        "phase4e3_model_path": str(P4E3_MODEL.relative_to(ROOT)),
        "phase4e3_model_sha256": sha256(P4E3_MODEL),
        "phase4e3_dlt_selection_receipt_path": str(P4E3_DLT_SELECTION.relative_to(ROOT)),
        "phase4e3_dlt_selection_receipt_sha256": sha256(P4E3_DLT_SELECTION),
        "phase4e17_script_path": str(E17_SCRIPT.relative_to(ROOT)),
        "phase4e17_script_sha256": sha256(E17_SCRIPT),
        "phase4e17_candidate_registry_sha256": digest(candidate_registry()),
        "phase4e17_inner_rows_sha256": sha256(inner_path),
        "phase4e17_outer_rows_sha256": sha256(outer_path),
    }
    for phase_number, phase, script in (
        (13, E13, E13_SCRIPT),
        (14, E14, E14_SCRIPT),
        (15, E15, E15_SCRIPT),
        (16, E16, E16_SCRIPT),
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
        result[f"{prefix}_outer_identity_sha256"] = read_json(
            phase / game / "report.json"
        )["outer_window"]["identity_sha256"]
    return result


def run_game(
    game: str,
    inner_rows: Sequence[dict[str, object]],
    selection: dict[str, object],
    workers: int,
) -> dict[str, object]:
    # Selection has already completed before this function is allowed to load the
    # frozen outer rows.
    outer16 = validate_frozen_outer(game)
    predictions = (
        compute_selected_outer_predictions(selection, workers)
        if game == TARGET_GAME
        else {}
    )
    decorated = decorate_outer_rows(game, outer16, selection, predictions)
    for source, row in zip(outer16, decorated):
        projected = copy.deepcopy(row)
        projected.pop("phase4e17_per_number_feature_model")
        if projected != source:
            raise ValueError("FAIL_PHASE4E17_PHASE4E16_OUTER_MUTATION")
    metrics = {
        "calibration": split_metrics(decorated[:OUTER_CALIBRATION_DRAWS], game),
        "evaluation": split_metrics(decorated[OUTER_CALIBRATION_DRAWS:], game),
        "all_120_descriptive": split_metrics(decorated, game),
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
    outer_path.write_bytes(b"".join(canonical(row) for row in decorated))
    report16 = e16_report(game)
    outer_hash = outer_identity_digest(outer16)
    selection_window = selection_window_metadata(
        inner_rows if game == TARGET_GAME else compact_inherited_inner_rows(TARGET_GAME)
    )
    report = {
        "artifact_type": "phase4e17_bounded_per_number_feature_model_report",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "game": game,
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "diagnostic_claim": "per-number feature/model diagnostic; scores are not true lottery probabilities",
        "experiment_scope": "dlt_front_only" if game == TARGET_GAME else "phase4e16_inheritance_only",
        "outer_window": {
            **report16["outer_window"],
            "first_60_role": "frozen_outer_calibration_evaluation_only_not_selection",
            "last_60_role": "frozen_outer_evaluation",
            "identity_sha256": outer_hash,
            "identity_named_fields_sha256": digest(row_identity(outer16)),
            "identity_matches_exact_phase4e13_e14_e15_e16": True,
        },
        "candidate_selection_window": selection_window,
        "alternative_feature_model_experiment": {
            "target_game": TARGET_GAME,
            "target_zone": TARGET_ZONE,
            "applied_to_this_game": game == TARGET_GAME,
            "bounded_candidates": candidate_registry(),
            "candidate_order": list(CANDIDATE_ORDER),
            "candidate_count": len(CANDIDATE_ORDER),
            "available_feature_ids_used_once_across_model_subsets": list(
                AVAILABLE_FEATURE_IDS
            ),
            "configuration_grid_search_performed": False,
            "selection_scope": "dlt_front_only",
            "selection_uses_outer_labels": False,
            "selection_result": selection,
            "other_game_zones_inherit_phase4e16": True,
            "score_claim": "candidate scores rank individual numbers only and are not true lottery probabilities",
        },
        "outer_splits": metrics,
        "acceptance": {
            "rule": "every inherited or selected game/zone on the last-60 frozen outer evaluation requires individual-number Spearman rho > 0 and positive descriptive slope; fixed-size coverage is reported but is not a gate",
            "evaluation_zone_pass": evaluation_zone_pass,
            "failed_zones": [
                zone for zone, passed in evaluation_zone_pass.items() if not passed
            ],
            "accepted": all(evaluation_zone_pass.values()),
            "fixed_size_coverage_used_for_acceptance": False,
            "exact_ticket_gates_unchanged": True,
            "promotion_eligible": False,
        },
        "full_ticket_comparison": report16["full_ticket_comparison"],
        "full_ticket_comparison_unchanged_from_phase4e13_e14_e15_e16": True,
        "lineage": lineage(game, inner_path, outer_path),
        "strict_lag": {
            "target_t_uses_through_t_minus_1_only": True,
            "candidate_models_receive_only_draws_before_target_t": True,
            "phase4e3_model_training_labels_purged_by_two": True,
            "all_selection_rows_strict_lag": all(
                bool(row["strict_lag"]) for row in inner_rows
            ),
            "all_outer_rows_strict_lag": all(bool(row["strict_lag"]) for row in outer16),
            "all_maximum_feature_source_positions_equal_target_minus_one": all(
                int(row["phase4e17_per_number_feature_model"][
                    "maximum_feature_source_position"
                ])
                == int(row["target_position"]) - 1
                for row in decorated
            ),
            "selection_completed_before_outer_rows_loaded": True,
            "outer_labels_used_for_candidate_selection": False,
        },
    }
    report["lineage"]["phase4e17_outer_identity_sha256"] = outer_hash
    report["lineage"]["phase4e17_selection_identity_sha256"] = selection_window[
        "identity_sha256"
    ]
    report["lineage"]["phase4e17_block_identity_sha256"] = [
        block["identity_sha256"] for block in selection_window["blocks"]
    ]
    (path / "report.json").write_bytes(canonical(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run bounded Phase4E17 DLT-front per-number feature/model experiment"
    )
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    validate_registry()
    OUT.mkdir(exist_ok=True)

    # Candidate selection is deliberately completed before any E17 outer loading or
    # evaluation.  Only the frozen E16 pre-outer identities and labels enter here.
    dlt_inner = compute_selection_rows(args.workers)
    selection = select_dlt_front_candidate(dlt_inner)
    inherited_inner = compact_inherited_inner_rows("ssq")
    reports = {
        "ssq": run_game("ssq", inherited_inner, selection, args.workers),
        "dlt": run_game("dlt", dlt_inner, selection, args.workers),
    }
    failed = [
        {"game": game, "zone": zone}
        for game, report in reports.items()
        for zone, passed in report["acceptance"]["evaluation_zone_pass"].items()
        if not passed
    ]
    summary = {
        "artifact_type": "phase4e17_bounded_per_number_feature_model_summary",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "promotion_eligible": False,
        "p4e6_serving_unchanged": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "scores_are_true_lottery_probabilities": False,
        "experiment_scope": "dlt_front_only",
        "candidate_registry_sha256": digest(candidate_registry()),
        "candidate_order": list(CANDIDATE_ORDER),
        "selected_dlt_front": {
            "candidate": selection["selected_candidate"],
            "candidate_kind": selection["selected_candidate_kind"],
            "feature_ids": selection["selected_feature_ids"],
            "stable": selection["stable"],
            "fallback_used": selection["fallback_used"],
            "model_id": selection["selected_model_id"],
        },
        "inherited_phase4e16_game_zones": [
            {"game": "ssq", "zone": "front"},
            {"game": "ssq", "zone": "back"},
            {"game": "dlt", "zone": "back"},
        ],
        "games": reports,
        "acceptance_rule": "all game/zones require individual-number rho>0 and slope>0 on the frozen last-60 outer evaluation; fixed-size coverage and exact-ticket results are not changed into new gates",
        "failed_game_zones": failed,
        "accepted_all_games_zones": not failed,
        "exact_ticket_gates_unchanged": True,
        "phase4e17_complete": True,
    }
    (OUT / "summary.json").write_bytes(canonical(summary))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "selected_dlt_front": summary["selected_dlt_front"],
                "failed_game_zones": failed,
                "accepted_all_games_zones": summary["accepted_all_games_zones"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
