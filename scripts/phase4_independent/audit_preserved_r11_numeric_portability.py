#!/usr/bin/env python3
"""Independent, read-only probability portability audit for immutable r11."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import replay_real_model_release as replay


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_RELEASE_ID = "P4-P4E2-20260815-r11"
EXPECTED_FILE_COUNT = 178
EXPECTED_INVENTORY_SHA256 = "b01b69df6f5a39fab7b2b2215f6a89306606d6f96f354711aceaf894464357d9"
EXPECTED_CLOSURE_HASHES = {
    "manifest/delivery-manifest.json": "dac2de9bec8602e2580308791356f62e8e34fed9fd26f8c3ee9251b640ed3568",
    "acceptance/final-closure.json": "f43b8234312a0ba478f066ab28b94758946913010d45dc79fbc765154a5793b9",
    "replay/replay-report.json": "b4720b440fba596c98c621987305e0a0641f2a8ffbdfaa4ba1c83f670366bb7e",
    "contracts/local-verifier-contract.json": "e584a691f52c782b869ea5f0b0c4833d5dcbab281581b2ea636b702ca65e6d04",
}
PROFILE_ID = "top1000_derived_probability_display_v2"
DERIVED_RELATIVE_BOUND = 17 / 2**52
EXPECTED_EXHAUSTIVE_MAX_RELATIVE = 3.774410052595433e-15


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(release: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    files = sorted(path for path in release.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(release).as_posix()
        hasher.update(relative.encode() + b"\0" + str(path.stat().st_size).encode()
                      + b"\0" + sha(path).encode() + b"\n")
    return len(files), hasher.hexdigest()


def scope_paths(release: Path) -> list[tuple[str, str, Path]]:
    rows: list[tuple[str, str, Path]] = []
    for game in ("ssq", "dlt"):
        rows.extend([
            (game, "top1000", replay._single(release / f"forecasts/{game}", "*/top1000.jsonl")),
            (game, "historical_top1000", release / f"runtime/lifecycle/{game}/historical-cycle-v1/top1000.jsonl"),
            (game, "shadow_top1000", release / f"research/{game}/shadow-top1000.jsonl"),
        ])
    return rows


def audit_contract(release: Path) -> dict[str, object]:
    current = replay.local_contract()
    frozen = replay.load(release / "contracts/local-verifier-contract.json")
    if current.get("contract_id") != "P4-LOCAL-STABLE-SCORE-KEY-3":
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:contract_id")
    for unchanged in ("tight_recomputed_v1", "derived_feature_snapshot_v1"):
        if current["numeric_profiles"][unchanged] != frozen["numeric_profiles"][unchanged]:
            raise ValueError(f"HOLD_R11_PORTABILITY_AUDIT:unrelated_profile:{unchanged}")
    old = frozen["numeric_profiles"]["top1000_derived_probability_display_v1"]
    new = current["numeric_profiles"][PROFILE_ID]
    for unchanged in ("finite_required", "require_all_bounds", "max_absolute", "max_ulps"):
        if old[unchanged] != new[unchanged]:
            raise ValueError(f"HOLD_R11_PORTABILITY_AUDIT:probability_profile:{unchanged}")
    if new["max_relative"] != DERIVED_RELATIVE_BOUND:
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:relative_derivation")
    old_paths = next(row["paths"] for row in frozen["path_numeric_profiles"]
                     if row["profile_id"] == "top1000_derived_probability_display_v1")
    new_paths = next(row["paths"] for row in current["path_numeric_profiles"]
                     if row["profile_id"] == PROFILE_ID)
    if old_paths != new_paths or new_paths != [
        "top1000.*.joint_probability",
        "historical_top1000.*.joint_probability",
        "shadow_top1000.*.joint_probability",
    ]:
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:path_scope")
    if current["exact_invariants"] != frozen["exact_invariants"]:
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:exact_invariants")
    path_counts = {row["profile_id"]: len(row["paths"]) for row in current["path_numeric_profiles"]}
    if path_counts != {
        "tight_recomputed_v1": 41,
        "derived_feature_snapshot_v1": 42,
        PROFILE_ID: 3,
    }:
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:path_counts")
    return {
        "contract_id": current["contract_id"],
        "schema_version": current["schema_version"],
        "profiles_audited": sorted(current["numeric_profiles"]),
        "path_counts": path_counts,
        "unrelated_profiles_unchanged": True,
        "probability_absolute_unchanged": True,
        "probability_ulps_unchanged": True,
        "probability_paths_unchanged": True,
        "exact_invariants_unchanged": True,
        "relative_derivation": "17 / 2^52",
        "derived_relative_bound": DERIVED_RELATIVE_BOUND,
    }


def audit_probability_envelope(release: Path) -> dict[str, object]:
    contract = replay.local_contract()
    profile = contract["numeric_profiles"][PROFILE_ID]
    maximum: tuple[float, str, int, str, int] | None = None
    audited_pairs = 0
    conjunctive_passes = 0
    absolute_limited = 0
    scope_results = []
    for game, scope, path in scope_paths(release):
        rows = replay.load_jsonl(path)
        replay._validate_frozen_top(rows, f"{game}:{scope}")
        scope_pairs = 0
        scope_passes = 0
        for index, row in enumerate(rows):
            base = float(row["joint_probability"])
            if not math.isfinite(base) or not 0 < base < 1:
                raise ValueError(f"HOLD_R11_PORTABILITY_AUDIT:{game}:{scope}:{index}:probability")
            for direction in (-math.inf, math.inf):
                candidate = base
                for steps in range(1, 18):
                    candidate = math.nextafter(candidate, direction)
                    result = replay.numeric_comparison(base, candidate, contract=contract, profile_id=PROFILE_ID)
                    audited_pairs += 1
                    scope_pairs += 1
                    eligible = result["absolute_error"] <= profile["max_absolute"]
                    if result["relative_error"] > profile["max_relative"]:
                        raise ValueError(f"HOLD_R11_PORTABILITY_AUDIT:{game}:{scope}:{index}:relative")
                    if result["passed"] != eligible:
                        raise ValueError(f"HOLD_R11_PORTABILITY_AUDIT:{game}:{scope}:{index}:conjunction")
                    if eligible:
                        conjunctive_passes += 1
                        scope_passes += 1
                    else:
                        absolute_limited += 1
                    observed = (result["relative_error"], game, index, scope, steps)
                    if maximum is None or observed > maximum:
                        maximum = observed
                outside = math.nextafter(candidate, direction)
                if replay.numeric_comparison(base, outside, contract=contract, profile_id=PROFILE_ID)["passed"]:
                    raise ValueError(f"HOLD_R11_PORTABILITY_AUDIT:{game}:{scope}:{index}:18_ulp")
        scope_results.append({
            "game": game,
            "scope": scope,
            "row_count": len(rows),
            "bidirectional_1_to_17_ulp_pairs": scope_pairs,
            "conjunctive_pass_pairs": scope_passes,
            "identity_order_rank_exact": True,
        })
    assert maximum is not None
    if maximum[0] != EXPECTED_EXHAUSTIVE_MAX_RELATIVE:
        raise ValueError(f"HOLD_R11_PORTABILITY_AUDIT:max_relative:{maximum[0]}")
    return {
        "scope_count": len(scope_results),
        "row_count": sum(row["row_count"] for row in scope_results),
        "bidirectional_1_to_17_ulp_pairs": audited_pairs,
        "conjunctive_pass_pairs": conjunctive_passes,
        "absolute_limited_pairs": absolute_limited,
        "eighteen_ulp_negative_count": 12_000,
        "exhaustive_max_relative_envelope": maximum[0],
        "exhaustive_max_relative_location": {
            "game": maximum[1], "zero_based_index": maximum[2],
            "scope": maximum[3], "ulp_steps": maximum[4],
        },
        "derived_relative_bound": DERIVED_RELATIVE_BOUND,
        "scope_results": scope_results,
    }


def audit_controller_failure() -> dict[str, object]:
    path = ROOT / "tests/phase4/fixtures/local-verifier-top1000-probability-r11-macos-31211.json"
    fixture = replay.load(path)
    base = float(fixture["release_value"])
    candidate = base
    for _ in range(fixture["ulp_distance"]):
        candidate = math.nextafter(candidate, -math.inf)
    current = replay.numeric_comparison(base, candidate, profile_id=PROFILE_ID)
    frozen = replay.load(ROOT / "artifacts/phase-4/P4-P4E2-20260815-r11/contracts/local-verifier-contract.json")
    old = replay.numeric_comparison(base, candidate, contract=frozen,
                                    profile_id="top1000_derived_probability_display_v1")
    if (current["passed"] is not True or old["passed"] is not False
            or current["absolute_error"] != fixture["absolute_error"]
            or current["relative_error"] != fixture["relative_error"]
            or current["ulp_distance"] != fixture["ulp_distance"]):
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:controller_failure")
    return {
        "fixture_sha256": sha(path),
        "failed_release_id": fixture["release_id"],
        "failed_controller_profile": fixture["failed_profile_id"],
        "failure_reason": fixture["reason"],
        "old_profile_reproduces_fail": True,
        "formula_derived_profile_accepts_bound": True,
    }


def audit(release: Path, draws_path: Path) -> dict[str, object]:
    release = release.resolve()
    if release.name != EXPECTED_RELEASE_ID:
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:release_id")
    before = inventory(release)
    if before != (EXPECTED_FILE_COUNT, EXPECTED_INVENTORY_SHA256):
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:inventory")
    if any(sha(release / relative) != expected for relative, expected in EXPECTED_CLOSURE_HASHES.items()):
        raise ValueError("HOLD_R11_PORTABILITY_AUDIT:closure_hash")
    contract_audit = audit_contract(release)
    replay_results = [replay.replay_game(release, draws_path, game) for game in ("ssq", "dlt")]
    envelope = audit_probability_envelope(release)
    controller_failure = audit_controller_failure()
    if before != inventory(release):
        raise ValueError("FAIL_R11_PORTABILITY_AUDIT_WROTE_RELEASE")
    product_imports = [name for name in sys.modules if name.startswith("lottery_system")]
    if product_imports:
        raise ValueError(f"HOLD_R11_PORTABILITY_AUDIT:product_imports:{product_imports}")
    return {
        "artifact_type": "phase4_preserved_r11_numeric_portability_audit",
        "release_id": EXPECTED_RELEASE_ID,
        "release_file_count": EXPECTED_FILE_COUNT,
        "release_inventory_sha256": EXPECTED_INVENTORY_SHA256,
        "preserved_failure": controller_failure,
        "contract_audit": contract_audit,
        "probability_envelope": envelope,
        "independent_replay_results": replay_results,
        "semantic_numeric_comparisons": sum(row["semantic_numeric_comparisons"] for row in replay_results),
        "product_core_import_count": 0,
        "release_unchanged": True,
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
