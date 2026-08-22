#!/usr/bin/env python3
"""Generate append-only Phase4E21 bonus-hardening evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import heapq
import importlib.util
import itertools
import json
import sys
import tempfile
import math
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "artifacts/phase4e21_bonus_hardening"
sys.path.insert(0, str(ROOT / "scripts/phase4e17"))

import run_per_number_feature_model as e17

from lottery_system.phase4.bonus import (
    DLT_FIXED_PRIZES,
    DLT_FIXED_RULE,
    DLT_TIER_STATES,
    SSQ_FIXED_PRIZES,
    SSQ_FIXED_RULE,
    SSQ_TIER_STATES,
    BONUS_CONTRACT_FINGERPRINT,
    fixed_bonus,
    registered_rule_version,
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


E19 = load_module("phase4e21_e19", ROOT / "scripts/phase4e19/ssq_prize_aware.py")
E20 = load_module("phase4e21_e20", ROOT / "scripts/phase4e20/ssq_supervised_compression.py")

FROZEN_PATHS = tuple(E19.DLT_FROZEN_HASHES)
OLD_REPORTS = {
    "phase4e17_ssq": ROOT / "artifacts/phase4e17/ssq/report.json",
    "phase4e17_dlt": ROOT / "artifacts/phase4e17/dlt/report.json",
    "phase4e18_ssq": ROOT / "artifacts/phase4e18/ssq/report.json",
    "phase4e19_ssq": ROOT / "artifacts/phase4e19/report.json",
    "phase4e20_ssq": ROOT / "artifacts/phase4e20/report.json",
}
P4E6_DECISION = ROOT / "artifacts/phase4e6/delivery/decision.json"


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")


def write_jsonl(path: Path, values: Sequence[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_bytes(value) for value in values))


def frozen_hashes() -> dict[str, str]:
    return {relative: sha256_file(ROOT / relative) for relative in FROZEN_PATHS}


OFFICIAL_STATE_ORACLE: dict[tuple[str, str], dict[tuple[int, int], int]] = {
    ("ssq", SSQ_FIXED_RULE): {
        (6, 1): 1, (6, 0): 2, (5, 1): 3, (5, 0): 4, (4, 1): 4,
        (4, 0): 5, (3, 1): 5, (2, 1): 6, (1, 1): 6, (0, 1): 6,
    },
    ("dlt", DLT_FIXED_RULE): {
        (5, 2): 1, (5, 1): 2, (5, 0): 3, (4, 2): 3, (4, 1): 4,
        (4, 0): 5, (3, 2): 5, (3, 1): 6, (2, 2): 6,
        (3, 0): 7, (2, 1): 7, (1, 2): 7, (0, 2): 7,
    },
}


def full_space_oracle() -> dict[str, object]:
    """Independent combinatorial audit for the two current routine tables."""
    cases = (
        ("ssq", SSQ_FIXED_RULE, 33, 6, 16, 1, 17_721_088, 1_188_988, 15_117_950),
        ("dlt", DLT_FIXED_RULE, 35, 5, 12, 2, 21_425_712, 1_429_197, 18_890_405),
    )
    checks = []
    for game, version, front_n, front_k, back_n, back_k, total_expected, winners_expected, payout_expected in cases:
        total = winners = payout = 0
        for front_hits in range(front_k + 1):
            for back_hits in range(back_k + 1):
                ways_count = (
                    math.comb(front_k, front_hits)
                    * math.comb(front_n - front_k, front_k - front_hits)
                    * math.comb(back_k, back_hits)
                    * math.comb(back_n - back_k, back_k - back_hits)
                )
                total += ways_count
                amount = int(fixed_bonus(game, version, front_hits, back_hits)["fixed_prize_yuan"])
                winners += ways_count * int(amount > 0)
                payout += ways_count * amount
        checks.append({
            "game": game,
            "total_ticket_count": total,
            "expected_ticket_count": total_expected,
            "winning_ticket_count": winners,
            "expected_winning_ticket_count": winners_expected,
            "fixed_prize_total_yuan": payout,
            "expected_fixed_prize_total_yuan": payout_expected,
            "passed": (total, winners, payout) == (total_expected, winners_expected, payout_expected),
        })
    return {"checks": checks, "all_checks_pass": all(item["passed"] for item in checks)}


def state_space_audit() -> dict[str, object]:
    configurations = (
        ("ssq", SSQ_FIXED_RULE, 7, 2, SSQ_TIER_STATES, SSQ_FIXED_PRIZES),
        ("dlt", DLT_FIXED_RULE, 6, 3, DLT_TIER_STATES, DLT_FIXED_PRIZES),
    )
    results = []
    for game, version, front_count, back_count, states, prizes in configurations:
        rows = []
        assigned: list[tuple[int, int]] = []
        for front_hits in range(front_count):
            for back_hits in range(back_count):
                result = fixed_bonus(
                    game,
                    version,
                    front_hits,
                    back_hits,
                    issue="MUTATED_ISSUE",
                    special_payout=999_999_999,
                    fuyun_prize=888_888_888,
                    promotion_ids=["IGNORED"],
                )
                if result["prize_tier"] is not None:
                    assigned.append((front_hits, back_hits))
                rows.append({"front_hits": front_hits, "back_hits": back_hits, **result})
        flattened = [state for tier_states in states.values() for state in tier_states]
        oracle = OFFICIAL_STATE_ORACLE[(game, version)]
        actual = {
            (front_hits, back_hits): result["prize_tier"]
            for front_hits in range(front_count)
            for back_hits in range(back_count)
            for result in [fixed_bonus(game, version, front_hits, back_hits)]
            if result["prize_tier"] is not None
        }
        states_match_oracle = actual == oracle
        results.append(
            {
                "game": game,
                "rule_version": version,
                "state_count": front_count * back_count,
                "assigned_state_count": len(assigned),
                "unmatched_state_count": front_count * back_count - len(assigned),
                "mutually_exclusive": len(flattened) == len(set(flattened)),
                "states_match_official_oracle": states_match_oracle,
                "all_amounts_integer_yuan": all(type(row["fixed_prize_yuan"]) is int for row in rows),
                "first_prize_yuan": prizes[1],
                "second_prize_yuan": prizes[2],
                "states": rows,
            }
        )
    full_space = full_space_oracle()
    return {
        "artifact_type": "phase4e21_bonus_state_space_audit",
        "routine_fixed_prizes_only": True,
        "metadata_inputs_ignored": [
            "issue", "special_payout", "fuyun_prize", "promotion_ids",
            "floating_prize", "issue_specific_branch",
        ],
        "configurations": results,
        "all_checks_pass": all(
            row["mutually_exclusive"]
            and row["states_match_official_oracle"]
            and row["all_amounts_integer_yuan"]
            for row in results
        ) and full_space["all_checks_pass"],
        "full_space_oracle": full_space,
    }


_COMBINATIONS: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def legal_combinations(game: str) -> tuple[np.ndarray, np.ndarray]:
    cached = _COMBINATIONS.get(game)
    if cached is not None:
        return cached
    if game == "ssq":
        dimensions = (33, 6, 16, 1)
    elif game == "dlt":
        dimensions = (35, 5, 12, 2)
    else:
        raise ValueError(f"unsupported game: {game}")
    front_n, front_k, back_n, back_k = dimensions
    result = (
        np.asarray(list(itertools.combinations(range(1, front_n + 1), front_k)), dtype=np.int16),
        np.asarray(list(itertools.combinations(range(1, back_n + 1), back_k)), dtype=np.int16),
    )
    _COMBINATIONS[game] = result
    return result


def fast_ranked_ticket_partition_prize_metrics(
    row: Mapping[str, object], game: str,
    partition_sizes: Sequence[int] = e17.TICKET_PARTITION_SIZES,
) -> dict[str, object]:
    """Vectorized equivalent of E17's complete-ticket ranking and prize replay."""
    front_combos, back_combos = legal_combinations(game)
    zones = row["phase4e17_per_number_feature_model"]["zones"]
    front_scores = np.asarray(
        [float(value["candidate_score"]) for value in zones["front"]["number_observations"]]
    )
    back_scores = np.asarray(
        [float(value["candidate_score"]) for value in zones["back"]["number_observations"]]
    )
    front_values = front_scores[front_combos - 1].sum(axis=1)
    back_values = back_scores[back_combos - 1].sum(axis=1)
    # Combination arrays are lexicographic; stable score sorting reproduces
    # E17's (-score, tuple) ordering for exact score ties.
    front_order = np.argsort(-front_values, kind="stable")
    back_order = np.argsort(-back_values, kind="stable")
    max_count = min(max(partition_sizes), len(front_order) * len(back_order))
    front_limit = min(len(front_order), max_count)

    while True:
        heap: list[tuple[float, tuple[int, ...], tuple[int, ...], int, int]] = []
        for front_position in range(front_limit):
            front_index = int(front_order[front_position])
            back_index = int(back_order[0])
            front_tuple = tuple(map(int, front_combos[front_index]))
            back_tuple = tuple(map(int, back_combos[back_index]))
            heapq.heappush(
                heap,
                (
                    -float(front_values[front_index] + back_values[back_index]),
                    front_tuple,
                    back_tuple,
                    front_position,
                    0,
                ),
            )
        selected_front = np.empty(max_count, dtype=np.int64)
        selected_back = np.empty(max_count, dtype=np.int64)
        last_negative_score = 0.0
        for rank in range(max_count):
            negative_score, _front_tuple, _back_tuple, front_position, back_position = heapq.heappop(heap)
            last_negative_score = negative_score
            selected_front[rank] = front_order[front_position]
            selected_back[rank] = back_order[back_position]
            next_back_position = back_position + 1
            if next_back_position < len(back_order):
                front_index = int(front_order[front_position])
                back_index = int(back_order[next_back_position])
                heapq.heappush(
                    heap,
                    (
                        -float(front_values[front_index] + back_values[back_index]),
                        tuple(map(int, front_combos[front_index])),
                        tuple(map(int, back_combos[back_index])),
                        front_position,
                        next_back_position,
                    ),
                )
        if front_limit == len(front_order):
            break
        omitted_best = float(front_values[front_order[front_limit]] + back_values[back_order[0]])
        if -last_negative_score > omitted_best:
            break
        front_limit = min(len(front_order), front_limit * 2)

    actual_front = np.zeros(front_scores.size + 1, dtype=np.uint8)
    actual_back = np.zeros(back_scores.size + 1, dtype=np.uint8)
    actual_front[np.asarray(row["zones"]["front"]["actual_numbers"], dtype=int)] = 1
    actual_back[np.asarray(row["zones"]["back"]["actual_numbers"], dtype=int)] = 1
    front_hits = actual_front[front_combos[selected_front]].sum(axis=1)
    back_hits = actual_back[back_combos[selected_back]].sum(axis=1)
    version = registered_rule_version(game, str(row["issue"]))
    front_dimension = 7 if game == "ssq" else 6
    back_dimension = 2 if game == "ssq" else 3
    amount_lookup = np.zeros((front_dimension, back_dimension), dtype=np.int64)
    tier_lookup = np.zeros((front_dimension, back_dimension), dtype=np.uint8)
    tier_max = 6 if game == "ssq" else 7
    for front_hit_count in range(front_dimension):
        for back_hit_count in range(back_dimension):
            prize = fixed_bonus(game, version, front_hit_count, back_hit_count)
            amount_lookup[front_hit_count, back_hit_count] = int(prize["fixed_prize_yuan"])
            tier_lookup[front_hit_count, back_hit_count] = int(prize["prize_tier"] or 0)
    amounts = amount_lookup[front_hits, back_hits]
    tiers = tier_lookup[front_hits, back_hits]
    cumulative = np.cumsum(amounts, dtype=np.int64)
    result: dict[int, dict[str, object]] = {}
    for size in partition_sizes:
        if size > max_count:
            continue
        tier_counts = np.bincount(tiers[:size], minlength=tier_max + 1)
        total = int(cumulative[size - 1])
        result[int(size)] = {
            "partition_size": int(size),
            "known_prize_total_yuan": total,
            "average_prize_yuan": total / int(size),
            "winning_ticket_count": int(size - tier_counts[0]),
            "prize_tier_ticket_counts": {
                str(tier): int(tier_counts[tier]) for tier in range(1, tier_max + 1)
            },
        }
    return {
        "ranking_definition": "complete legal tickets ranked by additive front/back candidate scores",
        "primary_metric": "known_prize_total_yuan / partition_size",
        "partitions": result,
        "score_is_true_lottery_probability": False,
        "evidence_recalculation": "vectorized exact hit-state replay of the registered E17 ticket ordering",
    }


def recalculate_e17_game(game: str, output_dir: Path) -> Path:
    rows = read_jsonl(ROOT / f"artifacts/phase4e17/{game}/outer-rolling-report.jsonl")
    ranked = {
        str(row["issue"]): fast_ranked_ticket_partition_prize_metrics(row, game)
        for row in rows
    }
    split_ranges = {
        "calibration": rows[:60],
        "evaluation": rows[60:],
        "all_120": rows,
    }
    report = {
        "artifact_type": "phase4e21_phase4e17_bonus_recalculation",
        "game": game,
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "source_outer_rows": f"artifacts/phase4e17/{game}/outer-rolling-report.jsonl",
        "source_outer_rows_sha256": sha256_file(ROOT / f"artifacts/phase4e17/{game}/outer-rolling-report.jsonl"),
        "routine_fixed_prizes_only": True,
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
        "splits": {},
    }
    for split, split_rows in split_ranges.items():
        report["splits"][split] = {
            "draws": len(split_rows),
            "first_issue": str(split_rows[0]["issue"]),
            "last_issue": str(split_rows[-1]["issue"]),
            "rule_versions": sorted(
                {registered_rule_version(game, str(row["issue"])) for row in split_rows}
            ),
            "ticket_group_average_prize_metrics": {
                str(front_size): {
                    str(back_size): e17.ticket_group_prize_metrics(split_rows, front_size, back_size)
                    for back_size in e17.ZONE_SIZES["back"]
                }
                for front_size in e17.ZONE_SIZES["front"]
            },
            "ranked_ticket_partition_prize_metrics": {
                str(row["issue"]): ranked[str(row["issue"])] for row in split_rows
            },
        }
    path = output_dir / "recalculated/phase4e17" / game / "report.json"
    write_json(path, report)
    return path


def aggregate_e18(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    totals: dict[int, dict[str, object]] = {}
    for row in rows:
        metrics = fast_ranked_ticket_partition_prize_metrics(row, "ssq")
        for size, value in metrics["partitions"].items():
            item = totals.setdefault(
                int(size), {"known_prize_total_yuan": 0, "winning_ticket_count": 0}
            )
            item["known_prize_total_yuan"] = int(item["known_prize_total_yuan"]) + int(value["known_prize_total_yuan"])
            item["winning_ticket_count"] = int(item["winning_ticket_count"]) + int(value["winning_ticket_count"])
    for size, item in totals.items():
        item["draws"] = len(rows)
        item["partition_size"] = size
        item["average_prize_yuan"] = int(item["known_prize_total_yuan"]) / (len(rows) * size)
    return {str(size): value for size, value in totals.items()}


def recalculate_e18(output_dir: Path) -> Path:
    rows = read_jsonl(ROOT / "artifacts/phase4e18/ssq/outer-rolling-report.jsonl")
    report = {
        "artifact_type": "phase4e21_phase4e18_bonus_recalculation",
        "game": "ssq",
        "status": "RETROSPECTIVE_BACKTEST_ONLY",
        "source_outer_rows": "artifacts/phase4e18/ssq/outer-rolling-report.jsonl",
        "source_outer_rows_sha256": sha256_file(ROOT / "artifacts/phase4e18/ssq/outer-rolling-report.jsonl"),
        "routine_fixed_prizes_only": True,
        "partitions": {
            "calibration": aggregate_e18(rows[:60]),
            "evaluation": aggregate_e18(rows[60:]),
            "all_120": aggregate_e18(rows),
        },
        "gate_status": "NO_PROMOTION",
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
    }
    path = output_dir / "recalculated/phase4e18/ssq/report.json"
    write_json(path, report)
    return path


def normalized_e20_report(path: Path) -> dict[str, object]:
    report = copy.deepcopy(read_json(path))
    report.pop("runtime_seconds", None)
    return report


def compare_directories(primary: Path, replay: Path, phase: str) -> dict[str, object]:
    if phase == "phase4e20":
        names = (
            "candidate-registry.json", "model-feature-lineage.json", "coefficients.json",
            "portfolio-hashes.json", "inner-rolling-report.jsonl", "outer-rolling-report.jsonl",
            "calibration-summary.json", "evaluation-summary.json", "all-120-summary.json",
            "replay-evidence.json", "dlt-isolation-baseline.sha256", "delivery/decision.json",
        )
        normalized_match = normalized_e20_report(primary / "report.json") == normalized_e20_report(replay / "report.json")
    else:
        names = (
            "candidate-registry.json", "feature-lineage.json", "strict-lag-hashes.json",
            "inner-rolling-report.jsonl", "outer-rolling-report.jsonl", "calibration-summary.json",
            "evaluation-summary.json", "all-120-summary.json", "replay-evidence.json",
            "report.json", "delivery/decision.json", "delivery/manifest.json",
        )
        normalized_match = True
    rows = [
        {
            "path": name,
            "primary_sha256": sha256_file(primary / name),
            "replay_sha256": sha256_file(replay / name),
            "matches": sha256_file(primary / name) == sha256_file(replay / name),
        }
        for name in names
    ]
    return {
        "phase": phase,
        "byte_identical_payloads": all(row["matches"] for row in rows),
        "normalized_report_match": normalized_match,
        "files": rows,
    }


def manifest(output_dir: Path, paths: Sequence[Path]) -> dict[str, object]:
    rows = []
    for path in sorted(set(paths)):
        rows.append(
            {
                "path": str(path.relative_to(output_dir)),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return {"artifact_type": "phase4e21_bonus_hardening_manifest", "files": rows}


def run(
    output_dir: Path,
    independent_replay: bool = True,
    resume_existing: bool = False,
) -> dict[str, object]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    contract_marker = output_dir / ".bonus-contract-fingerprint"
    if resume_existing:
        if not contract_marker.exists() or contract_marker.read_text().strip() != BONUS_CONTRACT_FINGERPRINT:
            resume_existing = False
    contract_marker.write_text(BONUS_CONTRACT_FINGERPRINT + "\n")
    frozen_before = frozen_hashes()
    p4e6_before = sha256_file(P4E6_DECISION)
    old_hashes = {name: sha256_file(path) for name, path in OLD_REPORTS.items()}

    state_audit = state_space_audit()
    state_path = output_dir / "state-space-audit.json"
    write_json(state_path, state_audit)

    e17_ssq_path = output_dir / "recalculated/phase4e17/ssq/report.json"
    e17_dlt_path = output_dir / "recalculated/phase4e17/dlt/report.json"
    e18_path = output_dir / "recalculated/phase4e18/ssq/report.json"
    if not (resume_existing and e17_ssq_path.exists()):
        recalculate_e17_game("ssq", output_dir)
    if not (resume_existing and e17_dlt_path.exists()):
        recalculate_e17_game("dlt", output_dir)
    if not (resume_existing and e18_path.exists()):
        recalculate_e18(output_dir)

    e19_dir = output_dir / "recalculated/phase4e19"
    e20_dir = output_dir / "recalculated/phase4e20"
    if resume_existing and (e19_dir / "report.json").exists():
        e19_report = read_json(e19_dir / "report.json")
        e19_result = {
            "decision": e19_report["decision"],
            "hard_gate_passed": e19_report["hard_acceptance_gate"]["passed"],
        }
    else:
        e19_result = E19.run_pipeline(e19_dir)
    if resume_existing and (e20_dir / "report.json").exists():
        e20_report = read_json(e20_dir / "report.json")
        e20_result = {
            "decision": e20_report["decision"],
            "hard_gate_passed": e20_report["hard_acceptance_gate"]["passed"],
        }
    else:
        e20_result = E20.run_pipeline(e20_dir)

    replay_evidence: dict[str, object] = {
        "artifact_type": "phase4e21_independent_replay",
        "performed": independent_replay,
    }
    if independent_replay:
        with tempfile.TemporaryDirectory(prefix="phase4e21-replay-") as temporary:
            replay_root = Path(temporary)
            replay_e19 = replay_root / "phase4e19"
            replay_e20 = replay_root / "phase4e20"
            E19.run_pipeline(replay_e19)
            E20.run_pipeline(replay_e20)
            comparisons = [
                compare_directories(e19_dir, replay_e19, "phase4e19"),
                compare_directories(e20_dir, replay_e20, "phase4e20"),
            ]
        replay_evidence["comparisons"] = comparisons
        replay_evidence["all_deterministic_payloads_match"] = all(
            row["byte_identical_payloads"] and row["normalized_report_match"]
            for row in comparisons
        )
    replay_path = output_dir / "independent-replay.json"
    write_json(replay_path, replay_evidence)

    frozen_after = frozen_hashes()
    p4e6_after = sha256_file(P4E6_DECISION)
    serving = read_json(P4E6_DECISION)
    isolation = {
        "artifact_type": "phase4e21_dlt_serving_isolation",
        "frozen_dlt_hashes_before": frozen_before,
        "frozen_dlt_hashes_after": frozen_after,
        "frozen_dlt_hashes_match_registered": {
            path: frozen_after[path] == E19.DLT_FROZEN_HASHES[path] for path in FROZEN_PATHS
        },
        "p4e6_decision_sha256_before": p4e6_before,
        "p4e6_decision_sha256_after": p4e6_after,
        "p4e6_serving_release": serving.get("serving_release"),
        "p4e6_terminal_status": serving.get("terminal_status"),
        "all_isolation_checks_pass": (
            frozen_before == frozen_after
            and all(frozen_after[path] == E19.DLT_FROZEN_HASHES[path] for path in FROZEN_PATHS)
            and p4e6_before == p4e6_after
            and serving.get("serving_release") == "P4-P4E2-20260815-r12"
            and serving.get("terminal_status") == "PROSPECTIVE_ONLY"
        ),
    }
    isolation_path = output_dir / "dlt-serving-isolation.json"
    write_json(isolation_path, isolation)

    new_reports = {
        "phase4e17_ssq": e17_ssq_path,
        "phase4e17_dlt": e17_dlt_path,
        "phase4e18_ssq": e18_path,
        "phase4e19_ssq": e19_dir / "report.json",
        "phase4e20_ssq": e20_dir / "report.json",
    }
    hash_comparison = {
        "artifact_type": "phase4e21_old_new_report_hashes",
        "reports": [
            {
                "report": name,
                "old_path": str(OLD_REPORTS[name].relative_to(ROOT)),
                "old_sha256": old_hashes[name],
                "new_path": str(new_reports[name].relative_to(ROOT)),
                "new_sha256": sha256_file(new_reports[name]),
            }
            for name in OLD_REPORTS
        ],
    }
    hashes_path = output_dir / "old-new-report-hashes.json"
    write_json(hashes_path, hash_comparison)

    decision = {
        "artifact_type": "phase4e21_bonus_hardening_decision",
        "status": "NO_PROMOTION",
        "acceptance_conclusion": "ACCEPT_BONUS_HARDENING_NO_PROMOTION",
        "routine_fixed_prize_rule_checks_pass": state_audit["all_checks_pass"],
        "phase4e19_gate_status": e19_result["decision"],
        "phase4e19_hard_gate_passed": e19_result["hard_gate_passed"],
        "phase4e20_gate_status": e20_result["decision"],
        "phase4e20_hard_gate_passed": e20_result["hard_gate_passed"],
        "thresholds_changed": False,
        "models_features_parameters_partitions_changed": False,
        "historical_inputs_changed": False,
        "prior_phase4e18_e19_e20_artifacts_overwritten": False,
        "dlt_and_serving_isolation_passed": isolation["all_isolation_checks_pass"],
        "independent_replay_passed": replay_evidence.get("all_deterministic_payloads_match", False),
        "p4e6_serving_release": "P4-P4E2-20260815-r12",
        "p4e6_terminal_status": "PROSPECTIVE_ONLY",
    }
    decision_path = output_dir / "delivery/decision.json"
    write_json(decision_path, decision)

    evidence_paths = [
        state_path, e17_ssq_path, e17_dlt_path, e18_path, replay_path,
        isolation_path, hashes_path, decision_path,
    ]
    evidence_paths.extend(path for path in e19_dir.rglob("*") if path.is_file())
    evidence_paths.extend(path for path in e20_dir.rglob("*") if path.is_file())
    manifest_path = output_dir / "delivery/manifest.json"
    write_json(manifest_path, manifest(output_dir, evidence_paths))
    return {
        "status": decision["status"],
        "acceptance_conclusion": decision["acceptance_conclusion"],
        "phase4e19_gate_status": decision["phase4e19_gate_status"],
        "phase4e20_gate_status": decision["phase4e20_gate_status"],
        "dlt_and_serving_isolation_passed": decision["dlt_and_serving_isolation_passed"],
        "independent_replay_passed": decision["independent_replay_passed"],
        "manifest_sha256": sha256_file(manifest_path),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--skip-independent-replay", action="store_true")
    parser.add_argument("--resume-existing", action="store_true")
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            run(
                arguments.output_dir,
                independent_replay=not arguments.skip_independent_replay,
                resume_existing=arguments.resume_existing,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
