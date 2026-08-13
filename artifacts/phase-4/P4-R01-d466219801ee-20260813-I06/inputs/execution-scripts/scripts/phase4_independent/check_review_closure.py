#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lottery_system.phase4.release_ops import (
    actor_for,
    closure,
    provenance,
    sha256_file,
    write_once,
)


def _task_number(task_id: str) -> int:
    return int(task_id[1:]) if task_id.startswith("T") and task_id[1:].isdigit() else 99


def build_review(release_root: Path, validator_closure: Path) -> dict[str, Any]:
    validator_path = release_root / "validator/final-validator.json"
    validator = json.loads(validator_path.read_text(encoding="utf-8"))
    assignments_path = release_root / "control/actor-assignments-formal.json"
    assignments = json.loads(assignments_path.read_text(encoding="utf-8"))
    reviewer = actor_for(assignments_path, "independent_reviewer")

    expected_ids = [f"P4-MVP-A{i:02d}" for i in range(1, 22)]
    assertions = validator.get("assertions", [])
    dispositions = {row.get("assertion_id"): row.get("status") for row in assertions}
    if list(dispositions) != expected_ids or any(value != "PASS" for value in dispositions.values()):
        raise ValueError("review A01-A21 gate failed")
    if validator.get("status") != "PASS" or validator.get("blocking_findings") != 0:
        raise ValueError("review validator gate failed")

    prior_assignments = [
        row
        for row in assignments["assignments"]
        if any(_task_number(task_id) <= 21 for task_id in row.get("task_ids", []))
    ]
    reviewed_actor_ids = sorted({row["actor_id"] for row in prior_assignments})
    operator_roles = {
        "release_controller",
        "run_operator",
        "vps_operator",
        "acceptance_engineer",
        "independent_replay_operator",
    }
    controller_operator_validator_ids = sorted(
        {
            row["actor_id"]
            for row in prior_assignments
            if operator_roles.intersection(row.get("roles", []))
        }
    )
    intersection = sorted({reviewer["actor_id"]}.intersection(reviewed_actor_ids))
    if intersection:
        raise ValueError("reviewer actor conflicts with a T00-T21 actor")

    manifest = json.loads((release_root / "manifest/evidence-manifest.json").read_text(encoding="utf-8"))
    provenance_actor_ids = {
        row["producer_provenance"]["producer_actor_id"]
        for row in manifest["files"]
        if row.get("producer_provenance", {}).get("producer_actor_id")
    }
    for name in ("replay", "validator"):
        payload = json.loads((release_root / f"manifest/{name}-closure.json").read_text(encoding="utf-8"))
        provenance_actor_ids.add(payload["producer_provenance"]["producer_actor_id"])
        provenance_actor_ids.update(
            row["producer_provenance"]["producer_actor_id"] for row in payload["files"]
        )
    if not provenance_actor_ids.issubset(set(reviewed_actor_ids)):
        raise ValueError("manifest provenance includes an unassigned T00-T21 actor")

    replay_closure = release_root / "manifest/replay-closure.json"
    evidence_manifest = release_root / "manifest/evidence-manifest.json"
    expected_validator_closure = release_root / "manifest/validator-closure.json"
    if validator_closure.resolve() != expected_validator_closure.resolve():
        raise ValueError("review validator closure path mismatch")
    validator_closure_value = json.loads(validator_closure.read_text(encoding="utf-8"))
    if (
        validator_closure_value.get("parent_path") != "manifest/replay-closure.json"
        or validator_closure_value.get("parent_sha256") != sha256_file(replay_closure)
        or validator_closure_value.get("status") != "PASS"
    ):
        raise ValueError("review validator closure chain mismatch")

    return {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_review",
        "review_id": f"{release_root.name}-T22-review",
        "release_id": release_root.name,
        "reviewer_actor_id": reviewer["actor_id"],
        "reviewer_task_id": "T22",
        "reviewer_session_id": reviewer["session_id"],
        "validator_closure_sha256": sha256_file(validator_closure),
        "replay_closure_sha256": sha256_file(replay_closure),
        "evidence_manifest_sha256": sha256_file(evidence_manifest),
        "a01_a21_disposition": dispositions,
        "blocking_findings": [],
        "independence_audit": {
            "reviewer_actor_id": reviewer["actor_id"],
            "reviewed_producer_actor_ids": reviewed_actor_ids,
            "reviewed_controller_operator_validator_actor_ids": controller_operator_validator_ids,
            "actor_intersection": intersection,
            "role_conflict_count": 0,
            "derived_from_manifest_provenance": True,
        },
        "scientific_wording": "Synthetic capability only; no real predictive improvement is claimed.",
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--validator-closure", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    release_root = args.release_root.resolve()
    review = build_review(release_root, args.validator_closure.resolve())
    write_once(args.output / "review.json", review)
    assignments_path = release_root / "control/actor-assignments-formal.json"
    reviewer = actor_for(assignments_path, "independent_reviewer")
    environment = json.loads((release_root / "control/execution-environment.json").read_text())
    closure(
        release_root,
        "review",
        args.validator_closure.resolve(),
        [args.output / "review.json"],
        provenance(reviewer, "independent_reviewer", "T22", environment["implementation_commit"]),
    )
    print(json.dumps({"status": "PASS", "terminal": "T22_REVIEW_PASS", "review": review}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
