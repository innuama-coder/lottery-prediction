#!/usr/bin/env python3
"""Independent, read-only stable-score migration replay for immutable r10."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

import p4e2_oracle as oracle
import replay_real_model_release as replay


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_RELEASE_ID = "P4-P4E2-20260815-r10"
EXPECTED_INVENTORY_SHA256 = "e3b65e2ef7c7ab12ee7fe21c68d9847858661446f1a5242528db0dc46ba19d5c"
EXPECTED_FILE_COUNT = 178
EXPECTED_CLOSURE_HASHES = {
    "manifest/delivery-manifest.json": "a26bab8b91e6ed357762871af7c31ecf60d626167ac78ef1ae5adc093713274b",
    "acceptance/final-closure.json": "c51fba1b56db6e38614eb83ba859edb4760f48d9bbd7c540fbd0cf4c0e9d493a",
    "replay/replay-report.json": "17253cf55191531de96182c6989a89180ca3e25d08c0919c5ff348438660630a",
    "contracts/local-verifier-contract.json": "44109116d3921fba3b033b20e3eab165f46f1250a933f799d6d3b23a1c864b76",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(release: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    files = sorted(path for path in release.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(release).as_posix()
        hasher.update(relative.encode() + b"\0" + str(path.stat().st_size).encode() + b"\0" + sha(path).encode() + b"\n")
    return len(files), hasher.hexdigest()


def stable_expected_model(model: dict[str, object], zones: list[dict[str, object]]) -> dict[str, object]:
    return {**model, "zones": zones}


def independently_enumerated_zones(game: str, draws: list[oracle.Draw], coefficients: list[dict[str, float]]) -> list[dict[str, object]]:
    contexts = [oracle.feature_context(game, draws, zone) for zone in (0, 1)]
    enumerations = [oracle.enumerate_zone(contexts[zone], coefficients[zone], True, collect_layers=True) for zone in (0, 1)]
    return [
        {
            "n": oracle.RULES[game][zone][0], "k": oracle.RULES[game][zone][1],
            "coefficients": coefficients[zone], "context": contexts[zone],
            "top_zone_rows": [[score, list(combo)] for score, combo in enumerations[zone]["rows"]],
            **{key: value for key, value in enumerations[zone].items() if key != "rows"},
        }
        for zone in (0, 1)
    ]


def compare_scope(game: str, scope: str, observed: list[dict[str, object]], expected: list[dict[str, object]]) -> dict[str, object]:
    if len(observed) != 1000 or len(expected) != 1000:
        raise ValueError(f"HOLD_R10_MIGRATION_REPLAY:{scope}:length")
    observed_tickets = [(tuple(row["front_numbers"]), tuple(row["back_numbers"])) for row in observed]
    expected_tickets = [(tuple(row["front_numbers"]), tuple(row["back_numbers"])) for row in expected]
    if observed_tickets != expected_tickets or [row["rank"] for row in observed] != list(range(1, 1001)):
        raise ValueError(f"HOLD_R10_MIGRATION_REPLAY:{scope}:membership_order_rank")
    observed_scores = [float(row["log_joint_score"]) for row in observed]
    expected_scores = [float(row["log_joint_score"]) for row in expected]
    observed_keys = [oracle.score_order_key(score) for score in observed_scores]
    expected_keys = [row["score_order_key"] for row in expected]
    if observed_keys != expected_keys or len(set(observed_keys)) != 1000:
        raise ValueError(f"HOLD_R10_MIGRATION_REPLAY:{scope}:stable_key")
    semantic = 0
    for index, (observed_row, expected_row, observed_score, key) in enumerate(zip(observed, expected, observed_scores, observed_keys)):
        if (observed_row.get("canonical_ticket_key") != expected_row.get("canonical_ticket_key")
                or oracle.score_identity(observed_score) != expected_row["score_identity"]
                or oracle.tie_key_for_score(observed_score) != expected_row["tie_key"]
                or oracle.tie_group_id_for_score(observed_score) != expected_row["tie_group_id"]
                or oracle.score_order_key(math.nextafter(observed_score, -math.inf)) != key
                or oracle.score_order_key(math.nextafter(observed_score, math.inf)) != key):
            raise ValueError(f"HOLD_R10_MIGRATION_REPLAY:{scope}:{index}:identity")
        semantic += replay.compare_value(observed_row["log_joint_score"], expected_row["log_joint_score"], f"{scope}.{index}.log_joint_score")["semantic"]
        semantic += replay.compare_value(observed_row["joint_probability"], expected_row["joint_probability"], f"{scope}.{index}.joint_probability")["semantic"]
    gaps = [abs(left - right) for left, right in zip(observed_scores, observed_scores[1:]) if left != right]
    for left, right in zip(observed_scores, observed_scores[1:]):
        if left != right and oracle.score_order_key(left) == oracle.score_order_key(right):
            raise ValueError(f"HOLD_R10_MIGRATION_REPLAY:{scope}:adjacent_distinct_merged")
    return {
        "game": game, "scope": scope, "ticket_count": 1000, "stable_key_count": len(set(observed_keys)),
        "minimum_adjacent_distinct_gap": format(min(gaps), ".18e"),
        "membership_order_rank_unchanged": True, "one_ulp_invariant": True,
        "product_independent_identity_match": True, "semantic_numeric_comparisons": semantic,
    }


def audit(release: Path, draws_path: Path) -> dict[str, object]:
    release = release.resolve()
    if release.name != EXPECTED_RELEASE_ID:
        raise ValueError("HOLD_R10_MIGRATION_REPLAY:release_id")
    before = inventory(release)
    if before != (EXPECTED_FILE_COUNT, EXPECTED_INVENTORY_SHA256):
        raise ValueError("HOLD_R10_MIGRATION_REPLAY:inventory")
    if any(sha(release / relative) != expected for relative, expected in EXPECTED_CLOSURE_HASHES.items()):
        raise ValueError("HOLD_R10_MIGRATION_REPLAY:closure_hash")
    scope_results = []
    for game in ("ssq", "dlt"):
        draws = replay.load_draws(draws_path, game)
        serving = replay.load(release / "selection/serving-selection.json")["serving_model_by_game"][game]
        model = replay.load(release / serving["model_path"])
        formal_coefficients = oracle.fit_coefficients(game, draws, len(draws), float(model["regularization"]["selected"]))
        expected_formal = stable_expected_model(model, independently_enumerated_zones(game, draws, formal_coefficients))
        formal_path = replay._single(release / f"forecasts/{game}", "*/top1000.jsonl")
        scope_results.append(compare_scope(game, "top1000", replay.load_jsonl(formal_path), oracle.top_tickets(expected_formal)))

        lifecycle = release / f"runtime/lifecycle/{game}/historical-cycle-v1"
        parent = replay.load(lifecycle / "parent-model.json")
        parent_coefficients = oracle.fit_coefficients(game, draws, len(draws) - 1, float(parent["regularization"]["selected"]))
        expected_parent = stable_expected_model(parent, independently_enumerated_zones(game, draws[:-1], parent_coefficients))
        scope_results.append(compare_scope(game, "historical_top1000", replay.load_jsonl(lifecycle / "top1000.jsonl"), oracle.top_tickets(expected_parent)))

        research = release / f"research/{game}"
        child = replay.load(research / "child-model.json")
        proposal = replay.load(research / "diff.json")["change"]
        coefficients = oracle.fit_coefficients(game, draws, len(draws), float(proposal["child_l2"]))
        expected_child = stable_expected_model(child, independently_enumerated_zones(game, draws, coefficients))
        scope_results.append(compare_scope(game, "shadow_top1000", replay.load_jsonl(research / "shadow-top1000.jsonl"), oracle.top_tickets(expected_child)))
    minimum = min(Decimal(row["minimum_adjacent_distinct_gap"]) for row in scope_results)
    if format(minimum, "e") != "4.326295779955025012e-10":
        raise ValueError("HOLD_R10_MIGRATION_REPLAY:minimum_gap")
    if before != inventory(release):
        raise ValueError("FAIL_R10_MIGRATION_REPLAY_WROTE_RELEASE")
    return {
        "artifact_type": "phase4_preserved_r10_stable_score_migration_replay",
        "release_id": EXPECTED_RELEASE_ID, "release_file_count": EXPECTED_FILE_COUNT,
        "release_inventory_sha256": EXPECTED_INVENTORY_SHA256,
        "score_order_key_id": oracle.SCORE_ORDER_KEY_ID,
        "score_order_quantum": format(oracle.SCORE_ORDER_QUANTUM, "f"),
        "rounding": "ROUND_HALF_EVEN", "scope_count": len(scope_results), "row_count": 6000,
        "minimum_adjacent_distinct_gap": "4.326295779955025012e-10",
        "scope_results": scope_results, "release_unchanged": True,
        "product_core_import_count": len([name for name in sys.modules if name.startswith("lottery_system")]),
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--draws", type=Path, default=ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.release, args.draws.resolve())
    encoded = replay.canon(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists() and args.output.read_bytes() != encoded:
            raise FileExistsError(args.output)
        args.output.write_bytes(encoded)
    print(encoded.decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
