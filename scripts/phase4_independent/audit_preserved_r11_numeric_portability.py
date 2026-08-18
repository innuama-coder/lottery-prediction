#!/usr/bin/env python3
"""Full, non-mutating numeric migration preflight for immutable P4E2 r11."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
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
FULL_MATRIX_FIXTURE = ROOT / "tests/phase4/fixtures/local-verifier-r11-macos-full-replay.json"


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


def route(path: str, contract: dict[str, object]) -> tuple[str, str]:
    matches = [
        (row["profile_id"], pattern)
        for row in contract["path_numeric_profiles"]
        for pattern in row["paths"]
        if replay._path_matches(path, pattern)
    ]
    if len(matches) != 1:
        raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:route:{path}:{matches}")
    return matches[0]


def empty_maximum() -> dict[str, object]:
    return {"value": 0.0, "path": None}


def update_maximum(target: dict[str, object], value: float | int, path: str) -> None:
    if value > target["value"]:
        target.update(value=value, path=path)


class MatrixCollector:
    def __init__(self, contract: dict[str, object]) -> None:
        self.contract = contract
        self.patterns: dict[tuple[str, str], dict[str, object]] = {}

    def __call__(self, path: str, observed: object, expected: object, result: dict[str, object]) -> None:
        profile_id, pattern = route(path, self.contract)
        if result["profile_id"] != profile_id:
            raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:profile_route:{path}")
        row = self.patterns.setdefault((profile_id, pattern), {
            "profile_id": profile_id,
            "pattern": pattern,
            "comparisons": 0,
            "differing_leaves": 0,
            "bound_failures": 0,
            "max_absolute": empty_maximum(),
            "max_relative": empty_maximum(),
            "max_ulps": empty_maximum(),
        })
        row["comparisons"] += 1
        if result["absolute_error"] or result["ulp_distance"]:
            row["differing_leaves"] += 1
        if not result["passed"]:
            row["bound_failures"] += 1
        update_maximum(row["max_absolute"], result["absolute_error"], path)
        update_maximum(row["max_relative"], result["relative_error"], path)
        update_maximum(row["max_ulps"], result["ulp_distance"], path)

    def result(self) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        rows = [self.patterns[key] for key in sorted(self.patterns)]
        profiles = []
        for profile_id in sorted(self.contract["numeric_profiles"]):
            selected = [row for row in rows if row["profile_id"] == profile_id]
            profile = {
                "profile_id": profile_id,
                "patterns_observed": len(selected),
                "comparisons": sum(row["comparisons"] for row in selected),
                "differing_leaves": sum(row["differing_leaves"] for row in selected),
                "bound_failures": sum(row["bound_failures"] for row in selected),
                "max_absolute": empty_maximum(),
                "max_relative": empty_maximum(),
                "max_ulps": empty_maximum(),
            }
            for row in selected:
                for axis in ("max_absolute", "max_relative", "max_ulps"):
                    update_maximum(profile[axis], row[axis]["value"], row[axis]["path"])
            profiles.append(profile)
        return rows, profiles


def validate_controller_matrix_and_classification(contract: dict[str, object]) -> dict[str, object]:
    fixture = replay.load(FULL_MATRIX_FIXTURE)
    if (fixture.get("release_id") != EXPECTED_RELEASE_ID
            or fixture.get("legacy_numeric_bound_failures") != 163
            or fixture.get("exact_identity_mismatches") != 0
            or fixture.get("semantic_comparisons_by_game") != {"ssq": 54807, "dlt": 54865}):
        raise ValueError("HOLD_R11_NUMERIC_PREFLIGHT:controller_fixture")
    expected_routes = {
        "feature_snapshot.634.feature_values.F04": "derived_feature_context_v2",
        "model.zones.1.context.number_features.F04.7": "derived_feature_context_v2",
        "model.zones.1.context.normalization.F04.mean": "derived_feature_context_v2",
        "model.zones.1.coefficients.F04": "derived_coefficient_v1",
        "model.objective_trace.gradient_at_zero_by_zone.1.F04": "derived_coefficient_v1",
        "model.report_only_metrics.0.model_joint_log_loss": "tight_recomputed_v1",
        "shadow_top1000.968.joint_probability": "top1000_derived_probability_display_v3",
    }
    for path, expected in expected_routes.items():
        if route(path, contract)[0] != expected:
            raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:known_route:{path}")

    profiles = contract["numeric_profiles"]
    feature = fixture["legacy_profiles"]["derived_feature_snapshot_v1"]
    probability = fixture["legacy_profiles"]["top1000_derived_probability_display_v1"]
    tight = fixture["legacy_profiles"]["tight_recomputed_v1"]
    if not (feature["max_absolute"] <= profiles["derived_feature_context_v2"]["max_absolute"]
            and feature["max_relative"] <= profiles["derived_feature_context_v2"]["max_relative"]
            and feature["max_ulps"] <= profiles["derived_feature_context_v2"]["max_ulps"]
            and tight["coefficient_example_ulps"] <= profiles["derived_coefficient_v1"]["max_ulps"]
            and tight["max_absolute"] <= profiles["tight_recomputed_v1"]["max_absolute"]
            and probability["max_absolute"] <= profiles["top1000_derived_probability_display_v3"]["max_absolute"]
            and probability["max_relative"] <= profiles["top1000_derived_probability_display_v3"]["max_relative"]
            and probability["max_ulps"] <= profiles["top1000_derived_probability_display_v3"]["max_ulps"]):
        raise ValueError("HOLD_R11_NUMERIC_PREFLIGHT:controller_maximum_not_covered")
    configured_patterns = sum(len(row["paths"]) for row in contract["path_numeric_profiles"])
    if configured_patterns != 86:
        raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:path_count:{configured_patterns}")
    return {
        "fixture_sha256": sha(FULL_MATRIX_FIXTURE),
        "legacy_bound_failures_preserved": 163,
        "legacy_exact_identity_mismatches": 0,
        "known_failure_paths_reclassified": expected_routes,
        "configured_pattern_count": configured_patterns,
        "all_legacy_profile_maxima_covered_by_source_class": True,
    }


def step(value: float, count: int) -> float:
    for _ in range(count):
        value = math.nextafter(value, math.inf)
    return value


def boundary_audit(contract: dict[str, object]) -> list[dict[str, object]]:
    bases = {
        "tight_recomputed_v1": 1.0,
        "derived_feature_context_v2": 0.0099312201839453045,
        "derived_coefficient_v1": 0.02098171210825526,
        "top1000_derived_probability_display_v3": float("5.75e-08"),
    }
    results = []
    for profile_id, policy in contract["numeric_profiles"].items():
        base = bases[profile_id]
        at_ulp = step(base, policy["max_ulps"])
        outside_ulp = math.nextafter(at_ulp, math.inf)
        if not replay.numeric_comparison(base, at_ulp, contract=contract, profile_id=profile_id)["passed"]:
            raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:{profile_id}:ulp_boundary")
        if replay.numeric_comparison(base, outside_ulp, contract=contract, profile_id=profile_id)["passed"]:
            raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:{profile_id}:ulp_outside")

        outside_absolute = base
        while abs(outside_absolute - base) <= policy["max_absolute"]:
            outside_absolute = math.nextafter(outside_absolute, math.inf)
        if replay.numeric_comparison(base, outside_absolute, contract=contract, profile_id=profile_id)["passed"]:
            raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:{profile_id}:absolute_outside")

        subnormal = math.ulp(0.0)
        relative_outside = math.nextafter(subnormal, math.inf)
        if replay.numeric_comparison(subnormal, relative_outside, contract=contract, profile_id=profile_id)["passed"]:
            raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:{profile_id}:relative_outside")
        for nonfinite in (math.nan, math.inf, -math.inf):
            try:
                replay.numeric_comparison(base, nonfinite, contract=contract, profile_id=profile_id)
            except ValueError as exc:
                if "FAIL_NON_FINITE" not in str(exc):
                    raise
            else:
                raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:{profile_id}:nonfinite")
        results.append({
            "profile_id": profile_id,
            "at_ulp_boundary_passes": True,
            "next_ulp_fails": True,
            "just_outside_absolute_fails": True,
            "relative_subnormal_negative_fails": True,
            "nonfinite_values_fail": True,
        })
    return results


def audit(release: Path, draws_path: Path, *, require_zero_new_bound_failures: bool) -> dict[str, object]:
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:python:{sys.implementation.name}:{sys.version_info.major}.{sys.version_info.minor}")
    release = release.resolve()
    if release.name != EXPECTED_RELEASE_ID:
        raise ValueError("HOLD_R11_NUMERIC_PREFLIGHT:release_id")
    before = inventory(release)
    if before != (EXPECTED_FILE_COUNT, EXPECTED_INVENTORY_SHA256):
        raise ValueError("HOLD_R11_NUMERIC_PREFLIGHT:inventory")
    if any(sha(release / relative) != expected for relative, expected in EXPECTED_CLOSURE_HASHES.items()):
        raise ValueError("HOLD_R11_NUMERIC_PREFLIGHT:closure_hash")

    contract = replay.local_contract()
    controller = validate_controller_matrix_and_classification(contract)
    collector = MatrixCollector(contract)
    with replay.collect_numeric_comparisons(collector, suppress_bounds=True):
        replay_results = [replay.replay_game(release, draws_path, game) for game in ("ssq", "dlt")]
    pattern_results, profile_results = collector.result()
    observed_patterns = {(row["profile_id"], row["pattern"]) for row in pattern_results}
    configured_patterns = {(row["profile_id"], pattern)
                           for row in contract["path_numeric_profiles"] for pattern in row["paths"]}
    if observed_patterns != configured_patterns:
        raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:pattern_coverage:{sorted(configured_patterns - observed_patterns)}")
    new_failures = sum(row["bound_failures"] for row in profile_results)
    if [row["semantic_numeric_comparisons"] for row in replay_results] != [54807, 54865]:
        raise ValueError("HOLD_R11_NUMERIC_PREFLIGHT:semantic_comparison_count")
    if before != inventory(release):
        raise ValueError("FAIL_R11_NUMERIC_PREFLIGHT_WROTE_RELEASE")
    product_imports = sorted(name for name in sys.modules if name.startswith("lottery_system"))
    if product_imports:
        raise ValueError(f"HOLD_R11_NUMERIC_PREFLIGHT:product_imports:{product_imports}")
    return {
        "artifact_type": "phase4_preserved_r11_full_numeric_migration_preflight",
        "status": "PASS" if not new_failures else "HOLD",
        "release_id": EXPECTED_RELEASE_ID,
        "release_file_count": EXPECTED_FILE_COUNT,
        "release_inventory_sha256": EXPECTED_INVENTORY_SHA256,
        "runtime": {
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "numeric_contract_id": contract["contract_id"],
        "controller_matrix_migration": controller,
        "profile_results": profile_results,
        "pattern_results": pattern_results,
        "boundary_audit": boundary_audit(contract),
        "replay_results": replay_results,
        "new_bound_failures": new_failures,
        "zero_new_bound_failures_required": require_zero_new_bound_failures,
        "exact_structure_identity_checks_retained": True,
        "product_core_import_count": 0,
        "release_unchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--draws", type=Path, default=ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-zero-new-bound-failures", action="store_true")
    args = parser.parse_args()
    result = audit(args.release, args.draws.resolve(),
                   require_zero_new_bound_failures=args.require_zero_new_bound_failures)
    encoded = replay.canon(result)
    if args.output:
        output = args.output.resolve()
        output.relative_to(ROOT.resolve())
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(output)
        output.write_bytes(encoded)
    print(encoded.decode(), end="")
    return 0 if result["status"] == "PASS" else 20


if __name__ == "__main__":
    raise SystemExit(main())
