#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lottery_system.phase4.release_ops import (
    actor_for,
    closure,
    provenance,
    sha256_file,
    utc_now,
    write_once,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery-statement", type=Path, required=True)
    parser.add_argument("--review-closure", type=Path, required=True)
    parser.add_argument("--actor-assignments", type=Path, required=True)
    args = parser.parse_args()
    release_root = args.review_closure.resolve().parents[1]
    expected_review_closure = release_root / "manifest/review-closure.json"
    if args.review_closure.resolve() != expected_review_closure.resolve():
        raise ValueError("machine delivery review closure path mismatch")

    assignments = json.loads(args.actor_assignments.read_text(encoding="utf-8"))
    actor = actor_for(args.actor_assignments, "machine_delivery_statement")
    prior_actor_ids = {
        row["actor_id"]
        for row in assignments["assignments"]
        if any(task_id != "T23" and int(task_id[1:]) <= 22 for task_id in row.get("task_ids", []))
    }
    if actor["actor_id"] in prior_actor_ids or actor.get("actor_type") != "codex_session":
        raise ValueError("machine delivery actor conflicts with prior producer")

    review_path = release_root / "review/review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    validator_closure = release_root / "manifest/validator-closure.json"
    review_closure_value = json.loads(args.review_closure.read_text(encoding="utf-8"))
    if (
        review.get("status") != "PASS"
        or review.get("blocking_findings") != []
        or review_closure_value.get("parent_path") != "manifest/validator-closure.json"
        or review_closure_value.get("parent_sha256") != sha256_file(validator_closure)
        or review.get("validator_closure_sha256") != sha256_file(validator_closure)
    ):
        raise ValueError("machine delivery review gate failed")

    wording = review["scientific_wording"]
    statement = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_machine_delivery_statement",
        "release_id": release_root.name,
        "signer_actor_id": actor["actor_id"],
        "signer_actor_type": "codex_session",
        "signed_at_utc": utc_now(),
        "decision": "PASS",
        "delivery_matrix_sha256": sha256_file(review_path),
        "scientific_wording_sha256": hashlib.sha256(wording.encode("utf-8")).hexdigest(),
        "review_closure_sha256": sha256_file(args.review_closure),
        "validator_closure_sha256": sha256_file(validator_closure),
        "comment": "Machine-only closure: delivery matrix is complete and scientific wording makes no real predictive-improvement claim.",
    }
    write_once(args.delivery_statement, statement)
    environment = json.loads((release_root / "control/execution-environment.json").read_text())
    closure(
        release_root,
        "delivery",
        args.review_closure.resolve(),
        [args.delivery_statement],
        provenance(actor, "machine_delivery_statement", "T23", environment["implementation_commit"]),
    )
    print(json.dumps({"status": "PASS", "terminal": "T23_MACHINE_DELIVERY_PASS", "statement": statement}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
