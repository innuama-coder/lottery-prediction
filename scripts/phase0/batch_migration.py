"""Build and verify the immutable Phase-0 batch-A to repair-layer-B migration."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
A_ROOT = ROOT / "artifacts/phase-0/batches/p0-20260801-a"
CURRENT = ROOT / "artifacts/phase-0"
MANIFEST = CURRENT / "batch-migration-p0-20260801-b.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decision_surface(base: Path) -> dict[str, Any]:
    scope = load(base / "scope-freeze.json")
    observation = load(base / "observation-plan.json")
    reviewer = load(base / "reviewer-assignment.json")
    contract_path = (A_ROOT / "docs/phase-0-acceptance-contract.json") if base == A_ROOT / "artifacts" else ROOT / "docs/roadmap/phase-0-acceptance-contract.json"
    contract = load(contract_path)
    return {
        "contract_version": contract["version"],
        "scope_games": scope["games"],
        "corroboration_sample": scope["corroboration_sample"],
        "status_machine": scope["status_machine"],
        "acceptance_cutoff_utc": observation["acceptance_cutoff_utc"],
        "clock": observation["clock"],
        "request_schedule": observation["request_schedule"],
        "retry_policy": observation["retry_policy"],
        "budgets": observation["budgets"],
        "observation_games": observation["games"],
        "reviewers": reviewer["reviewers"],
        "role_separation": reviewer["role_separation"],
        "independence_declaration": reviewer["independence_declaration"],
        "hard_gates": contract["hard_gates"],
        "decision_logic": contract["decision_logic"],
    }


def projection_hash(base: Path) -> str:
    return hashlib.sha256(canonical_bytes(decision_surface(base))).hexdigest()


def archived_files_ok() -> tuple[bool, list[str]]:
    snapshot = load(A_ROOT / "snapshot-manifest.json")
    bad = []
    for item in snapshot["files"]:
        path = A_ROOT / item["path"]
        if not path.is_file() or path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            bad.append(item["path"])
    return not bad, bad


def build_manifest() -> dict[str, Any]:
    a_artifacts = A_ROOT / "artifacts"
    inherited = {}
    for name in ("scope-freeze.json", "observation-plan.json", "reviewer-assignment.json"):
        a_path = a_artifacts / name
        b_path = CURRENT / name
        inherited[name] = {
            "batch_a_sha256": sha256_file(a_path),
            "repair_layer_b_sha256": sha256_file(b_path),
            "identical": a_path.read_bytes() == b_path.read_bytes(),
        }
    a_surface = projection_hash(a_artifacts)
    b_surface = projection_hash(CURRENT)
    archive_ok, archive_bad = archived_files_ok()
    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase0_batch_migration",
        "from_batch_id": "p0-20260801-a",
        "to_repair_layer_id": "p0-20260801-b",
        "recorded_at_utc": "2026-08-01T03:11:36.9055470Z",
        "migration_kind": "machine_contract_repair_without_decision_surface_change",
        "prior_source_observation_disclosed": True,
        "prior_observations": [
            "DLT national official endpoints returned HTTP 567 in the batch-A execution environment.",
            "SSQ national official endpoints returned HTTP 403 in the batch-A execution environment.",
            "Guangdong official provincial DLT and SSQ pages returned HTTP 200 in low-frequency probes.",
        ],
        "archive": {
            "manifest_path": "artifacts/phase-0/batches/p0-20260801-a/snapshot-manifest.json",
            "manifest_sha256": sha256_file(A_ROOT / "snapshot-manifest.json"),
            "all_archived_files_match": archive_ok,
            "mismatches": archive_bad,
        },
        "inherited_frozen_inputs": inherited,
        "decision_surface": {
            "algorithm": "SHA-256 over UTF-8 sorted-key compact JSON projection",
            "batch_a_sha256": a_surface,
            "repair_layer_b_sha256": b_surface,
            "identical": a_surface == b_surface,
        },
        "allowed_change_classes": [
            "normalized time nullability and verified-state constraints",
            "source-level access and permission evidence structure",
            "rule evidence/version/state-machine registries",
            "semantic verifier and failure-injection tests",
            "repair-layer verification command hashes",
            "P0-02/P0-03 artifacts migrated to the corrected schemas",
        ],
        "forbidden_changes": [
            "target or minimum interval",
            "sample seed, universe, selected issues or sample size",
            "request schedule, retry/rate/resource budget or cutoff",
            "reviewer identity or role separation",
            "hard-gate standards, fail conditions or decision order",
            "backfilling a missed scheduled request as on-time",
        ],
        "defects": [
            {
                "defect_id": "P0-A-TIME-001",
                "root_cause": "The normalized schema required a non-null actual draw timestamp even when official history only evidenced a local draw date.",
                "required_fix": "Represent draw_date_local explicitly, make draw_at nullable, and reject fabricated actual times.",
            },
            {
                "defect_id": "P0-A-SOURCE-001",
                "root_cause": "The source schema represented an HTTP method but not observed reachability, approved use, or source-level compliance evidence.",
                "required_fix": "Add source-level observed_access, approved_use, evidence references, rate policy and owner/review fields.",
            },
            {
                "defect_id": "P0-A-RULE-001",
                "root_cause": "Rule labels and bare URLs lacked a stable semantic registry and state-machine evidence path.",
                "required_fix": "Add version, promotion/state-machine and stable evidence registries plus semantic verification.",
            },
        ],
        "migration_gate_pass": archive_ok and all(item["identical"] for item in inherited.values()) and a_surface == b_surface,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    expected = build_manifest()
    if args.write:
        MANIFEST.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not MANIFEST.is_file():
        raise SystemExit("migration manifest is missing; run with --write")
    actual = load(MANIFEST)
    if actual != expected:
        raise SystemExit("migration manifest differs from deterministic rebuild")
    if not actual["migration_gate_pass"]:
        raise SystemExit("migration gate failed")
    print(json.dumps({"status": "PASS", "migration_gate": True, "decision_surface_unchanged": True}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
