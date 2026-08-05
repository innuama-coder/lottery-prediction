"""Deterministic, network-free P0-05 work-plan and coverage generator."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from phase0lib import canonical_json_bytes, canonical_sha256, load_json, load_jsonl


REPO = Path(__file__).resolve().parents[2]
GENERATED_AT = "2026-08-01T05:00:00Z"
FIELDS = ["bundle_id", "number_space_version", "draw_process_version", "prize_rule_version", "active_promotion_ids"]
SELECTION_ALGORITHM_VERSION = "p0-05-mechanical-union-v1"
REASON_ORDER = {"frozen_sample": 0, "transition_previous": 1, "transition_at": 2, "transition_next": 3}
EXPECTED = {
    "dlt": {"sample": "699bf06c64404343727e7e70e837f6783769d4bef80bd1dcb6c93d9d60ae2f64", "transition": "defc8f620a12ab80dcc356587d31630b04754f9c9fb80ea62cd91693a9b45892", "union": "27b6d6f66679c04a3bc9e3763b6b3996f0ea9418a5fd9f4d7581efa08936c007", "records": "c055b914887e60de28a723fa31ba01abcf0d48c467f336777b99716c0a12449d"},
    "ssq": {"sample": "088a0831c56b02b051dd0ff09c1a59e9816b98d52a608354a6a07cab4c64009a", "transition": "52400b99a0d8b82761d087f781dc2046b78cb32bb31bf06b1fa5439fe5b87ba9", "union": "bf961be839bdfd792d4236ee3e09ca68801ccd82f26dc58a63f802e473dcd30a", "records": "8ba285352bbe90f79941868080a8a7279aa0fe3c46c3aeb21756eef90dea69a2"},
}


def build_work_plan(scope: dict[str, Any], rules: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    bundles = {item["bundle_id"]: item for item in rules["bundles"]}
    mappings = {game: {} for game in ("dlt", "ssq")}
    for item in rules["issue_mappings"]:
        mappings[item["game"]][item["issue_id"]] = item["bundle_id"]
    sample_by_game = {item["game"]: item["final_selected_issue_ids"] for item in scope["corroboration_sample"]["games"]}
    evidence_issues = {game: sorted({item["issue_id"] for item in evidence if item["game"] == game and item["status"] in {"verified", "unverified"}}) for game in ("dlt", "ssq")}
    games = []
    for game in ("dlt", "ssq"):
        ids = sorted(mappings[game])
        transitions = []
        work_reasons: dict[str, set[str]] = {issue: {"frozen_sample"} for issue in sample_by_game[game]}
        for index in range(1, len(ids)):
            previous_issue, issue = ids[index - 1], ids[index]
            previous_id, current_id = mappings[game][previous_issue], mappings[game][issue]
            if previous_id == current_id:
                continue
            previous, current = bundles[previous_id], bundles[current_id]
            changed = [field for field in FIELDS if field == "bundle_id" or previous[field] != current[field]]
            transitions.append({"previous_issue_id": previous_issue, "issue_id": issue, "previous_bundle_id": previous_id, "bundle_id": current_id, "changed_fields": changed})
            transition_window = [
                (previous_issue, "transition_previous"),
                (issue, "transition_at"),
            ]
            if index + 1 < len(ids):
                transition_window.append((ids[index + 1], "transition_next"))
            for candidate, reason in transition_window:
                work_reasons.setdefault(candidate, set()).add(reason)
        transition_ids = [item["issue_id"] for item in transitions]
        work_ids = sorted(work_reasons)
        work_records = [
            {
                "issue_id": issue,
                "bundle_id": mappings[game][issue],
                "inclusion_reasons": sorted(work_reasons[issue], key=REASON_ORDER.__getitem__),
            }
            for issue in work_ids
        ]
        existing = evidence_issues[game]
        planned_new = [issue for issue in work_ids if issue not in set(existing)]
        games.append({
            "game": game, "sample_issue_ids": sample_by_game[game], "sample_issue_ids_sha256": canonical_sha256(sample_by_game[game]), "parent_expected_sample_sha256": EXPECTED[game]["sample"],
            "transition_records": transitions, "transition_issue_ids": transition_ids, "transition_records_sha256": canonical_sha256(transitions),
            "work_issue_ids": work_ids, "work_issue_ids_sha256": canonical_sha256(work_ids), "parent_expected_work_issue_ids_sha256": EXPECTED[game]["union"],
            "work_records": work_records, "work_records_sha256": canonical_sha256(work_records),
            "external_audit_hashes": {"supplied_transition_sha256": EXPECTED[game]["transition"], "supplied_work_records_sha256": EXPECTED[game]["records"], "comparison_status": "not_comparable_canonical_shape_not_recorded"},
            "counts": {"sample": len(sample_by_game[game]), "transition": len(transitions), "work_union": len(work_ids)},
            "existing_reusable_evidence_issue_ids": existing, "planned_new_issue_ids": planned_new, "certified_authorized_new_requests": 0,
        })
    return {
        "schema_version": "1.0.0", "artifact_type": "p0_05_work_plan", "contract_version": "1.3", "generated_at_utc": GENERATED_AT,
        "status": "completed_hold_budget_reconciliation_required", "selection_algorithm_version": SELECTION_ALGORITHM_VERSION, "changed_field_order": FIELDS,
        "budget_audit": {"total_request_budget": 96, "p0_06_reserved_requests": 24, "p0_04_actual_requests": 1, "p0_02_direct_request_lower_bound": 8, "p0_02_direct_request_exact": None, "remaining_formula": "71-N_p0_02_direct_requests", "dlt_required_new_requests": 51, "dlt_feasible_only_if_p0_02_requests_lte": 20, "certified_authorized_new_requests": 0},
        "games": games, "network_runner_authorized": False,
    }


def build_coverage(scope: dict[str, Any], rules: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = {item["game"]: sorted(issue for stratum in item["strata"] for issue in stratum["candidate_issue_ids"]) for item in scope["corroboration_sample"]["games"]}
    minimum_ranges = {item["game"]: (int(item["minimum_viable_interval"]["start_issue"]), int(item["minimum_viable_interval"]["end_issue"])) for item in scope["games"]}
    observed_lists = {game: [item["issue_id"] for item in evidence if item["game"] == game and item["status"] in {"verified", "unverified"}] for game in ("dlt", "ssq")}
    mapping = {game: {} for game in ("dlt", "ssq")}
    for item in rules["issue_mappings"]: mapping[item["game"]][item["issue_id"]] = item["bundle_id"]
    games = []
    for game in ("dlt", "ssq"):
        expected = candidates[game]; minimum_start, minimum_end = minimum_ranges[game]; minimum = [issue for issue in expected if minimum_start <= int(issue) <= minimum_end]
        counts = Counter(observed_lists[game]); observed = sorted(counts)
        reason = "authoritative_primary_blocked_and_request_budget_unreconciled" if game == "dlt" else "compliance_hold_no_approved_source"
        transitions = [ids for i, ids in enumerate(expected[1:], 1) if mapping[game][ids] != mapping[game][expected[i-1]]]
        games.append({
            "game": game, "target_expected_issues": expected, "target_observed_issues": [issue for issue in observed if issue in set(expected)],
            "minimum_expected_issues": minimum, "minimum_observed_issues": [issue for issue in observed if issue in set(minimum)],
            "missing": [{"issue_id": issue, "classification": reason, "evidence_ref": "artifacts/phase-0/p0-05-work-plan.json"} for issue in expected if issue not in counts],
            "duplicate": [{"issue_id": issue, "classification": "duplicate_canonical_evidence", "evidence_ref": "artifacts/phase-0/evidence-manifest.jsonl"} for issue, count in sorted(counts.items()) if count > 1],
            "extra": [{"issue_id": issue, "classification": "outside_frozen_target", "evidence_ref": "artifacts/phase-0/evidence-manifest.jsonl"} for issue in observed if issue not in set(expected)],
            "holiday": [], "rule_boundary": [{"issue_id": issue, "classification": "rule_bundle_transition_at_issue", "evidence_ref": "artifacts/phase-0/rule-bundles.json"} for issue in transitions],
            "coverage_tier": "none", "evidence_refs": ["artifacts/phase-0/scope-freeze.json", "artifacts/phase-0/rule-bundles.json", "artifacts/phase-0/source-catalog.json", "artifacts/phase-0/evidence-manifest.jsonl", "artifacts/phase-0/p0-05-work-plan.json"],
        })
    return {"schema_version": "1.0.0", "artifact_type": "coverage_report", "contract_version": "1.3", "generated_at_utc": GENERATED_AT, "games": games}


def generate(artifacts: Path) -> None:
    scope=load_json(artifacts/"scope-freeze.json"); rules=load_json(artifacts/"rule-bundles.json"); evidence=load_jsonl(artifacts/"evidence-manifest.jsonl")
    (artifacts/"p0-05-work-plan.json").write_bytes(canonical_json_bytes(build_work_plan(scope,rules,evidence))+b"\n")
    (artifacts/"coverage-report.json").write_bytes(canonical_json_bytes(build_coverage(scope,rules,evidence))+b"\n")
    (artifacts/"reconciliation.jsonl").write_bytes(b"")


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--artifacts",type=Path,default=REPO/"artifacts/phase-0"); args=parser.parse_args(argv); generate(args.artifacts); print(json.dumps({"status":"PASS","network_used":False},separators=(",",":"))); return 0


if __name__ == "__main__": raise SystemExit(main())
