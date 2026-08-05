"""Offline P0-07 closeout orchestration.

This repair-layer slice implements only the fail-closed prepare boundary and a
machine-readable gate-input snapshot.  It deliberately does not emit replay,
attestation, handoff, acceptance, or any other terminal conclusion.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from p0_04_http import clock_check_from_json
from p0_04_pipeline import rebuild_captures
from p0_05_history import build_coverage
from p0_07_decision import build_per_game_gate_results, derive_per_game_outcome, derive_project_decision
from p0_07_handoff import DECISION_EVIDENCE_REF, PREVIOUS_REFS, finalize_handoff_fixed_point
from phase0lib import ValidationError, canonical_json_bytes, canonical_sha256, load_json, load_jsonl, sha256_bytes, sha256_file, validate_schema_instance
from verify_phase0 import (
    verify_observation, verify_p0_02, verify_p0_03, verify_p0_06_semantics,
    verify_p0_05_semantics, verify_provenance, verify_reviewer, verify_rule_bundles,
    verify_scope, verify_source_catalog,
)


REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / "artifacts" / "phase-0"
REVIEW_CANDIDATE = ARTIFACTS / "p0-07-candidate"
REVIEW_OUTPUT = ARTIFACTS / "p0-07-review-output"
GATE_INPUT_NAME = "p0-07-gate-inputs.json"
INPUT_MANIFEST_NAME = "p0-07-input-manifest.json"
DERIVED_MANIFEST_NAME = "p0-07-derived-manifest.json"
DERIVED_DIRECTORY_NAME = "derived"
HANDOFF_NAME = "stage1-handoff-fixture.json"
RECEIPT_NAME = "p0-07-stage1-consumer-receipt.json"
REPLAY_NAME = "replay-report.json"


class CloseoutHold(ValidationError):
    """A fail-closed P0-07 readiness hold with no output side effects."""


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise CloseoutHold("closeout clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_input_manifest(repo_root: Path, artifacts: Path, acceptance_cutoff_utc: str, recorded_at: datetime) -> dict[str, Any]:
    """Inventory only replay inputs; existing parsed/normalized/coverage are excluded."""
    fixed = [
        "docs/roadmap/phase-0-acceptance-contract.json",
        *[f"artifacts/phase-0/{name}" for name in (
            "scope-freeze.json", "source-catalog.json", "field-contract.json", "rule-bundles.json",
            "observation-plan.json", "reviewer-assignment.json", "environment-lock.json",
            "verification-command.json", "verification-command.json.sha256", "clock-check-p0-04.json",
            "evidence-manifest.jsonl", "soak-run-log.jsonl", "p0-06-runtime-plan.json",
            "p0-06-runtime-plan.json.sha256", "p0-06-scheduler-install-audit.json",
            "p0-04-evidence-migration-p0-20260801-b.json", "p0-05-work-plan.json",
        )],
    ]
    verification = load_json(artifacts / "verification-command.json")
    declared_hashes = {
        item["path"]: item["sha256"]
        for key in ("verifier_file_hashes", "schema_hashes")
        for item in verification[key]
    }
    required_closeout_tools = {"scripts/phase0/p0_05_history.py", "scripts/phase0/p0_07_closeout.py"}
    missing_closeout_tools = required_closeout_tools - set(declared_hashes)
    if missing_closeout_tools:
        raise CloseoutHold(f"verification-command does not freeze required closeout tools: {sorted(missing_closeout_tools)}")
    candidates = {repo_root / relative for relative in fixed}
    candidates.update(repo_root / relative for relative in declared_hashes)
    candidates.update(path for path in (artifacts / "raw").rglob("*") if path.is_file())
    candidates.update(path for path in artifacts.glob("repair-manifest-p0-20260801-c*.json") if path.is_file())
    anchor = artifacts / "batches" / "p0-20260801-c-pre"
    for relative in (
        "snapshot-manifest.json", "snapshot-manifest.json.sha256",
        "artifacts/phase-0/p0-06-scheduler-install-audit.json",
        "artifacts/phase-0/evidence-manifest.jsonl", "artifacts/phase-0/soak-run-log.jsonl",
    ):
        candidates.add(anchor / relative)
    records = []
    for path in sorted(candidates, key=lambda item: item.relative_to(repo_root).as_posix()):
        if not path.is_file():
            raise CloseoutHold(f"missing P0-07 replay input: {path.relative_to(repo_root).as_posix()}")
        stat = path.stat()
        relative = path.relative_to(repo_root).as_posix()
        actual_hash = sha256_file(path)
        if relative in declared_hashes and declared_hashes[relative] != actual_hash:
            raise CloseoutHold(f"verification-command frozen hash mismatch: {relative}")
        records.append({"path": relative, "size": stat.st_size, "sha256": actual_hash, "mtime_ns": stat.st_mtime_ns})
    return {"schema_version": "1.0.0", "artifact_type": "p0_07_input_manifest", "contract_version": "1.3", "recorded_at_utc": _utc_text(recorded_at), "acceptance_cutoff_utc": acceptance_cutoff_utc, "files": records}


def require_prepare_ready(
    repo_root: Path,
    artifacts: Path,
    *,
    utcnow_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], datetime]:
    """Validate cutoff and exact P0-06 completion before any output path exists."""
    observation = load_json(artifacts / "observation-plan.json")
    catalog = load_json(artifacts / "source-catalog.json")
    plan = load_json(artifacts / "p0-06-runtime-plan.json")
    soak = load_jsonl(artifacts / "soak-run-log.jsonl")
    evaluated_at = utcnow_fn()
    if evaluated_at.tzinfo is None:
        raise CloseoutHold("P0-07 prepare requires a timezone-aware real UTC clock")
    try:
        verify_p0_06_semantics(
            plan,
            soak,
            observation,
            catalog,
            repo_root,
            require_complete=True,
            verified_at_utc=evaluated_at,
        )
    except ValidationError as exc:
        raise CloseoutHold(str(exc)) from exc
    return observation, catalog, plan, soak, evaluated_at.astimezone(timezone.utc)


def build_gate_inputs(
    artifacts: Path,
    catalog: dict[str, Any],
    soak: list[dict[str, Any]],
    *,
    coverage: dict[str, Any],
    reconciliation: list[dict[str, Any]],
    recorded_at: datetime,
    input_manifest_sha256: str,
) -> dict[str, Any]:
    evidence = load_jsonl(artifacts / "evidence-manifest.jsonl")
    coverage_by_game = {item["game"]: item for item in coverage["games"]}
    catalog_by_game = {item["game"]: item for item in catalog["games"]}
    games = []
    for game in ("dlt", "ssq"):
        statuses = Counter(item["status"] for item in evidence if item["game"] == game)
        reconciled = [item for item in reconciliation if item["game"] == game]
        primary_use = catalog_by_game[game]["authoritative_primary"]["approved_use"]
        coverage_tier = coverage_by_game[game]["coverage_tier"]
        games.append({
            "game": game,
            "coverage_tier": coverage_tier,
            "evidence_status_counts": {key: int(statuses[key]) for key in ("verified", "unverified", "invalid")},
            "reconciliation_count": len(reconciled),
            "unresolved_conflicts": sum(item["resolution_status"] == "unresolved" for item in reconciled),
            "soak_request_count": sum(item["game"] == game for item in soak),
            "authoritative_primary_approved_use": primary_use,
            "compliant_corrective_action_available": coverage_tier == "none" or primary_use in {"blocked", "hold_pending"},
            "alternatives_exhausted_no_evidentiary_path": False,
        })
    return {
        "schema_version": "1.0.0",
        "artifact_type": "p0_07_gate_inputs",
        "contract_version": "1.3",
        "recorded_at_utc": _utc_text(recorded_at),
        "input_manifest_sha256": input_manifest_sha256,
        "games": games,
    }


def _same_source_family(candidate_url: str, source_url: str) -> bool:
    candidate = urlsplit(candidate_url)
    source = urlsplit(source_url)
    if candidate.scheme != "https" or source.scheme != "https" or candidate.hostname != source.hostname:
        return False
    source_path = source.path if source.path.endswith("/") else source.path.rsplit("/", 1)[0] + "/"
    return candidate.path.startswith(source_path)


def _core_draw(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record["front_numbers"], record["back_numbers"], record["draw_date_local"],
        record["number_space_version"], record["draw_process_version"],
        record["prize_rule_version"], record["active_promotion_ids"],
    )


def build_reconciliation(
    catalog: dict[str, Any],
    evidence: list[dict[str, Any]],
    normalized_by_evidence: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build rows only when an accepted authoritative-primary record exists."""
    games = {item["game"]: item for item in catalog["games"]}
    accepted = [
        item for item in evidence
        if item["status"] == "verified"
        and item["evidence_id"] in normalized_by_evidence
        and normalized_by_evidence[item["evidence_id"]]["status"] == "verified"
    ]
    rows: list[dict[str, Any]] = []
    for primary in accepted:
        game_catalog = games[primary["game"]]
        if not _same_source_family(primary["final_url"], game_catalog["authoritative_primary"]["url"]):
            continue
        primary_record = normalized_by_evidence[primary["evidence_id"]]
        corroborators = [
            item for item in accepted
            if item["game"] == primary["game"]
            and item["issue_id"] == primary["issue_id"]
            and item["evidence_id"] != primary["evidence_id"]
            and any(_same_source_family(item["final_url"], source["url"]) for source in game_catalog["official_corroborators"])
        ]
        matches = all(_core_draw(normalized_by_evidence[item["evidence_id"]]) == _core_draw(primary_record) for item in corroborators)
        rows.append({
            "schema_version": "1.0.0",
            "artifact_type": "reconciliation_entry",
            "game": primary["game"],
            "issue_id": primary["issue_id"],
            "primary_evidence_ref": primary["evidence_id"],
            "corroborating_evidence_refs": sorted(item["evidence_id"] for item in corroborators),
            "corroboration_tier": "shared_upstream" if corroborators else "primary_only",
            "core_fact_match": matches,
            "conflict_fields": [] if matches else ["core_draw"],
            "resolution_status": ("agreed" if corroborators else "primary_only") if matches else "unresolved",
            "resolved_record_ref": f"derived/normalized/{primary['evidence_id']}.json" if matches else None,
        })
    return sorted(rows, key=lambda item: (item["game"], item["issue_id"], item["primary_evidence_ref"]))


def build_derived_manifest(candidate: Path, input_manifest_sha256: str, recorded_at: datetime) -> dict[str, Any]:
    derived = candidate / DERIVED_DIRECTORY_NAME
    files = []
    for path in sorted((item for item in derived.rglob("*") if item.is_file()), key=lambda item: item.relative_to(candidate).as_posix()):
        relative = path.relative_to(candidate).as_posix()
        if relative.endswith(DERIVED_MANIFEST_NAME):
            raise CloseoutHold("derived manifest must not contain itself")
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return {
        "schema_version": "1.0.0",
        "artifact_type": "p0_07_derived_manifest",
        "contract_version": "1.3",
        "recorded_at_utc": _utc_text(recorded_at),
        "input_manifest_sha256": input_manifest_sha256,
        "files": files,
    }


def validate_derived_manifest(
    candidate: Path,
    manifest: dict[str, Any],
    *,
    input_manifest_sha256: str,
    schema: dict[str, Any],
) -> None:
    validate_schema_instance(manifest, schema)
    if manifest["input_manifest_sha256"] != input_manifest_sha256:
        raise CloseoutHold("derived manifest input-manifest hash mismatch")
    records = {item["path"]: item for item in manifest["files"]}
    if len(records) != len(manifest["files"]):
        raise CloseoutHold("derived manifest contains duplicate paths")
    if any(path.endswith(DERIVED_MANIFEST_NAME) for path in records):
        raise CloseoutHold("derived manifest must not contain itself")
    actual = {
        path.relative_to(candidate).as_posix(): path
        for path in (candidate / DERIVED_DIRECTORY_NAME).rglob("*")
        if path.is_file()
    }
    if set(records) != set(actual):
        raise CloseoutHold("derived manifest does not exactly enumerate derived files")
    required = {
        "derived/p0-07-gate-inputs.json", "derived/coverage-report.json", "derived/reconciliation.jsonl",
        "derived/revision-report.json",
    }
    if not required.issubset(records) or not any(path.startswith("derived/parsed/") for path in records) or not any(path.startswith("derived/normalized/") for path in records):
        raise CloseoutHold("derived manifest is missing a required derived category")
    for relative, path in actual.items():
        record = records[relative]
        if record["size"] != path.stat().st_size or record["sha256"] != sha256_file(path):
            raise CloseoutHold(f"derived manifest hash/size mismatch: {relative}")


def _transition_path(current: str, target: str, allowed: dict[str, list[str]]) -> list[str]:
    if current == target:
        return [target] if target in allowed.get(current, []) else []
    queue: list[tuple[str, list[str]]] = [(current, [])]
    visited = {current}
    while queue:
        state, path = queue.pop(0)
        for next_state in allowed.get(state, []):
            if next_state == target:
                return [*path, next_state]
            if next_state not in visited:
                visited.add(next_state)
                queue.append((next_state, [*path, next_state]))
    raise CloseoutHold(f"no contract-allowed status path from {current} to {target}")


def _correction_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record["record_id"], "game": record["game"], "issue_id": record["issue_id"],
        "front_numbers": record["front_numbers"], "back_numbers": record["back_numbers"],
        "draw_date_local": record["draw_date_local"], "status": record["status"], "supersedes": record["supersedes"],
    }


def _correction_core(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    return snapshot["front_numbers"], snapshot["back_numbers"], snapshot["draw_date_local"]


def _replay(scenario_id: str, before: dict[str, Any], after: dict[str, Any], evidence_refs: list[str]) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "algorithm": "phase0_correction_snapshot_canonical_sha256_v1",
        "before": before,
        "after": after,
        "before_hash": canonical_sha256(before),
        "after_hash": canonical_sha256(after),
        "reconstructed": True,
        "evidence_refs": evidence_refs,
    }


def _synthetic_correction() -> dict[str, Any]:
    before = {
        "record_id": "synthetic-dlt-2026001", "game": "dlt", "issue_id": "2026001",
        "front_numbers": ["01", "02", "03", "04", "05"], "back_numbers": ["01", "02"],
        "draw_date_local": "2026-01-01", "status": "verified", "supersedes": None,
    }
    after = {
        **before,
        "front_numbers": ["01", "02", "03", "04", "06"],
        "supersedes": canonical_sha256(before),
    }
    return _replay(
        "synthetic-public-dlt-correction-v1", before, after,
        ["embedded:synthetic-public-dlt-correction-v1"],
    )


def _observed_corrections(
    catalog: dict[str, Any],
    evidence: list[dict[str, Any]],
    normalized_by_evidence: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    official_urls = {
        game["game"]: [game["authoritative_primary"]["url"], *[item["url"] for item in game["official_corroborators"]]]
        for game in catalog["games"]
    }
    grouped: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    for item in evidence:
        if item["evidence_id"] not in normalized_by_evidence:
            continue
        if not any(_same_source_family(item["final_url"], url) for url in official_urls[item["game"]]):
            continue
        grouped.setdefault((item["game"], item["issue_id"]), []).append(
            (item["evidence_id"], _correction_snapshot(normalized_by_evidence[item["evidence_id"]]))
        )
    replays = []
    for (game, issue_id), snapshots in sorted(grouped.items()):
        correction_index = 0
        for (before_ref, before), (after_ref, after) in zip(snapshots, snapshots[1:]):
            if _correction_core(before) == _correction_core(after):
                continue
            correction_index += 1
            replays.append(_replay(
                f"observed-{game}-{issue_id}-{correction_index:03d}", before, after, [before_ref, after_ref],
            ))
    return replays


def build_revision_report(
    contract: dict[str, Any],
    catalog: dict[str, Any],
    evidence: list[dict[str, Any]],
    normalized_by_evidence: dict[str, dict[str, Any]],
    *,
    generated_at: datetime,
    input_manifest_sha256: str,
) -> dict[str, Any]:
    if not evidence:
        raise CloseoutHold("revision reconstruction requires non-empty append-only evidence")
    evidence_ids = [item["evidence_id"] for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise CloseoutHold("revision reconstruction requires unique append-only evidence IDs")
    allowed = contract["status_transition_policy"]["allowed_transitions"]
    events: list[dict[str, Any]] = []
    chains: dict[str, list[dict[str, Any]]] = {}
    current_status: dict[str, str] = {}
    for item in evidence:
        record_id = f"{item['game']}-{item['issue_id']}"
        current = current_status.get(record_id, "unavailable")
        for next_status in _transition_path(current, item["status"], allowed):
            chain = chains.setdefault(record_id, [])
            event = {
                "record_id": record_id,
                "from_status": current,
                "to_status": next_status,
                "evidence_ref": item["evidence_id"],
                "reason_code": f"project_evidence_status_{item['status']}",
                "actor_id": "p0-07-deterministic-revision-rebuilder",
                "transitioned_at_utc": item["retrieved_at"],
                "supersedes": chain[-1]["event_id"] if chain else None,
            }
            event["event_id"] = f"evt-{canonical_sha256(event)[:24]}"
            chain.append(event)
            events.append(event)
            current = next_status
        current_status[record_id] = current
    current_view = []
    for record_id, chain in sorted(chains.items()):
        last = chain[-1]
        current_view.append({
            "record_id": record_id, "event_id": last["event_id"], "status": last["to_status"],
            "evidence_ref": last["evidence_ref"], "record_history_sha256": canonical_sha256(chain),
        })
    return {
        "schema_version": "1.1.0", "artifact_type": "revision_report", "contract_version": "1.3",
        "generated_at_utc": _utc_text(generated_at), "input_manifest_sha256": input_manifest_sha256,
        "append_only_verified": True,
        "event_order_algorithm": "evidence_manifest_line_then_contract_shortest_path_v1",
        "current_view_algorithm": "last_event_per_record_chain_v1",
        "observed_correction_algorithm": "official_source_adjacent_distinct_core_snapshot_v1",
        "history_sha256": canonical_sha256(events), "events": events, "current_view": current_view,
        "synthetic_correction_replay": _synthetic_correction(),
        "observed_correction_replays": _observed_corrections(catalog, evidence, normalized_by_evidence),
    }


def validate_revision_report(
    report: dict[str, Any],
    *,
    schema: dict[str, Any],
    contract: dict[str, Any],
    catalog: dict[str, Any],
    evidence: list[dict[str, Any]],
    normalized_by_evidence: dict[str, dict[str, Any]],
    input_manifest_sha256: str,
) -> None:
    validate_schema_instance(report, schema)
    if report["input_manifest_sha256"] != input_manifest_sha256:
        raise CloseoutHold("revision report input-manifest hash mismatch")
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    if len(evidence_by_id) != len(evidence):
        raise CloseoutHold("revision evidence IDs are not unique")
    allowed = {
        (source, target)
        for source, targets in contract["status_transition_policy"]["allowed_transitions"].items()
        for target in targets
    }
    event_ids: set[str] = set()
    chains: dict[str, list[dict[str, Any]]] = {}
    previous_evidence_index = -1
    evidence_index = {item["evidence_id"]: index for index, item in enumerate(evidence)}
    for event in report["events"]:
        if event["event_id"] in event_ids:
            raise CloseoutHold("revision event IDs must be unique")
        event_ids.add(event["event_id"])
        if event["evidence_ref"] not in evidence_by_id:
            raise CloseoutHold("revision event has dangling evidence_ref")
        current_index = evidence_index[event["evidence_ref"]]
        if current_index < previous_evidence_index:
            raise CloseoutHold("revision events do not preserve append-only evidence order")
        previous_evidence_index = current_index
        if (event["from_status"], event["to_status"]) not in allowed:
            raise CloseoutHold("revision event uses a contract-forbidden status transition")
        chain = chains.setdefault(event["record_id"], [])
        if chain:
            if event["supersedes"] != chain[-1]["event_id"] or event["from_status"] != chain[-1]["to_status"]:
                raise CloseoutHold("revision supersedes chain is dangling, non-linear, or state-inconsistent")
        elif event["supersedes"] is not None or event["from_status"] != "unavailable":
            raise CloseoutHold("revision chain must begin at unavailable with no supersedes")
        expected_id = f"evt-{canonical_sha256({key: value for key, value in event.items() if key != 'event_id'})[:24]}"
        if event["event_id"] != expected_id:
            raise CloseoutHold("revision event ID is not deterministically reconstructable")
        chain.append(event)
    if report["history_sha256"] != canonical_sha256(report["events"]):
        raise CloseoutHold("revision history hash mismatch")
    expected_current = []
    for record_id, chain in sorted(chains.items()):
        last = chain[-1]
        expected_current.append({
            "record_id": record_id, "event_id": last["event_id"], "status": last["to_status"],
            "evidence_ref": last["evidence_ref"], "record_history_sha256": canonical_sha256(chain),
        })
    if report["current_view"] != expected_current:
        raise CloseoutHold("revision current view does not reconstruct from event history")
    synthetic = report["synthetic_correction_replay"]
    if synthetic["evidence_refs"] != ["embedded:synthetic-public-dlt-correction-v1"]:
        raise CloseoutHold("synthetic correction must use its exact controlled embedded reference")
    for replay in [synthetic, *report["observed_correction_replays"]]:
        if replay is not synthetic and any(reference not in evidence_by_id for reference in replay["evidence_refs"]):
            raise CloseoutHold("correction replay has dangling evidence_ref")
        if replay["before_hash"] != canonical_sha256(replay["before"]) or replay["after_hash"] != canonical_sha256(replay["after"]):
            raise CloseoutHold("correction replay hash is not reconstructable")
        if replay["before_hash"] == replay["after_hash"]:
            raise CloseoutHold("correction replay before/after hashes must differ")
    if report["synthetic_correction_replay"] != _synthetic_correction():
        raise CloseoutHold("synthetic correction replay differs from the public fixed fixture")
    expected_observed = _observed_corrections(catalog, evidence, normalized_by_evidence)
    if report["observed_correction_replays"] != expected_observed:
        raise CloseoutHold("observed correction replay set is incomplete or non-deterministic")


def prepare(
    repo_root: Path,
    artifacts: Path,
    output: Path,
    *,
    utcnow_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Path:
    """Atomically rebuild non-terminal derived artifacts from frozen replay inputs."""
    if output.exists() and any(output.iterdir()):
        raise CloseoutHold(f"P0-07 prepare output directory is not empty: {output}")
    if not output.parent.is_dir():
        raise CloseoutHold(f"P0-07 prepare output parent does not exist: {output.parent}")
    observation, catalog, _plan, soak, evaluated_at = require_prepare_ready(repo_root, artifacts, utcnow_fn=utcnow_fn)
    manifest = build_input_manifest(repo_root, artifacts, observation["acceptance_cutoff_utc"], evaluated_at)
    validate_schema_instance(manifest, load_json(artifacts / "schemas" / "p0-07-input-manifest.schema.json"))
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".p0-07-prepare-") as temporary:
        staging = Path(temporary) / "candidate"
        staging.mkdir()
        manifest_path = staging / INPUT_MANIFEST_NAME
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
        input_manifest_sha256 = sha256_file(manifest_path)
        derived = staging / DERIVED_DIRECTORY_NAME
        derived.mkdir()
        clock = clock_check_from_json(load_json(artifacts / "clock-check-p0-04.json"))
        rebuild_captures(artifacts, clock, derived)
        evidence = load_jsonl(artifacts / "evidence-manifest.jsonl")
        coverage = build_coverage(load_json(artifacts / "scope-freeze.json"), load_json(artifacts / "rule-bundles.json"), evidence)
        coverage["generated_at_utc"] = _utc_text(evaluated_at)
        validate_schema_instance(coverage, load_json(artifacts / "schemas" / "coverage-report.schema.json"))
        (derived / "coverage-report.json").write_bytes(canonical_json_bytes(coverage) + b"\n")
        normalized_by_evidence: dict[str, dict[str, Any]] = {}
        evidence_ids = {item["evidence_id"] for item in evidence}
        for normalized_path in sorted((derived / "normalized").glob("*.json")):
            record = load_json(normalized_path)
            for evidence_ref in record["evidence_refs"]:
                if evidence_ref not in evidence_ids:
                    continue
                if evidence_ref in normalized_by_evidence:
                    raise CloseoutHold(f"multiple normalized records claim evidence ref: {evidence_ref}")
                normalized_by_evidence[evidence_ref] = record
        reconciliation = build_reconciliation(catalog, evidence, normalized_by_evidence)
        reconciliation_schema = load_json(artifacts / "schemas" / "reconciliation.schema.json")
        for row in reconciliation:
            validate_schema_instance(row, reconciliation_schema)
        (derived / "reconciliation.jsonl").write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in reconciliation))
        contract = load_json(repo_root / "docs" / "roadmap" / "phase-0-acceptance-contract.json")
        revision = build_revision_report(
            contract, catalog, evidence, normalized_by_evidence,
            generated_at=evaluated_at, input_manifest_sha256=input_manifest_sha256,
        )
        revision_schema = load_json(artifacts / "schemas" / "revision-report.schema.json")
        validate_revision_report(
            revision, schema=revision_schema, contract=contract, catalog=catalog, evidence=evidence,
            normalized_by_evidence=normalized_by_evidence, input_manifest_sha256=input_manifest_sha256,
        )
        (derived / "revision-report.json").write_bytes(canonical_json_bytes(revision) + b"\n")
        value = build_gate_inputs(
            artifacts, catalog, soak, coverage=coverage, reconciliation=reconciliation,
            recorded_at=evaluated_at, input_manifest_sha256=input_manifest_sha256,
        )
        validate_schema_instance(value, load_json(artifacts / "schemas" / "p0-07-gate-inputs.schema.json"))
        gate_path = derived / GATE_INPUT_NAME
        gate_path.write_bytes(canonical_json_bytes(value) + b"\n")
        derived_manifest = build_derived_manifest(staging, input_manifest_sha256, evaluated_at)
        derived_manifest_schema = load_json(artifacts / "schemas" / "p0-07-derived-manifest.schema.json")
        validate_derived_manifest(
            staging, derived_manifest, input_manifest_sha256=input_manifest_sha256, schema=derived_manifest_schema,
        )
        (staging / DERIVED_MANIFEST_NAME).write_bytes(canonical_json_bytes(derived_manifest) + b"\n")
        if output.exists():
            output.rmdir()
        staging.replace(output)
    return output / DERIVED_DIRECTORY_NAME / GATE_INPUT_NAME


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CloseoutHold(f"invalid UTC timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise CloseoutHold(f"timestamp is not timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def _candidate_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def validate_candidate_snapshot(
    repo_root: Path,
    artifacts: Path,
    candidate: Path,
    *,
    now: datetime,
) -> tuple[dict[str, Any], datetime]:
    if not candidate.is_dir():
        raise CloseoutHold("review candidate directory is missing")
    input_path = candidate / INPUT_MANIFEST_NAME
    derived_manifest_path = candidate / DERIVED_MANIFEST_NAME
    if not input_path.is_file() or not derived_manifest_path.is_file():
        raise CloseoutHold("review candidate lacks prepare manifests")
    input_manifest = load_json(input_path)
    validate_schema_instance(input_manifest, load_json(artifacts / "schemas" / "p0-07-input-manifest.schema.json"))
    recorded_at = _parse_utc(input_manifest["recorded_at_utc"])
    cutoff = _parse_utc(input_manifest["acceptance_cutoff_utc"])
    if recorded_at < cutoff or recorded_at > now.astimezone(timezone.utc):
        raise CloseoutHold("candidate recorded_at must be between cutoff and the real review clock")
    expected_input = build_input_manifest(repo_root, artifacts, input_manifest["acceptance_cutoff_utc"], recorded_at)
    if input_manifest != expected_input:
        raise CloseoutHold("candidate input manifest differs from current input bytes/mtime or inventory")
    derived_manifest = load_json(derived_manifest_path)
    validate_derived_manifest(
        candidate, derived_manifest, input_manifest_sha256=sha256_file(input_path),
        schema=load_json(artifacts / "schemas" / "p0-07-derived-manifest.schema.json"),
    )
    expected_paths = {INPUT_MANIFEST_NAME, DERIVED_MANIFEST_NAME, *[item["path"] for item in derived_manifest["files"]]}
    if set(_candidate_bytes(candidate)) != expected_paths:
        raise CloseoutHold("candidate contains files not produced by prepare")
    return input_manifest, recorded_at


def _aggregate_global_gates(per_game: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregated = []
    for gate_id in (item["gate_id"] for item in per_game[0]["gate_results"]):
        gates = [next(item for item in game["gate_results"] if item["gate_id"] == gate_id) for game in per_game]
        passed = all(item["outcome"] == "PASS" for item in gates)
        remediation = "not_applicable" if passed else (
            "alternatives_exhausted_no_evidentiary_path"
            if any(item["remediation_status"] == "alternatives_exhausted_no_evidentiary_path" for item in gates)
            else "concrete_compliant_action_available"
        )
        aggregated.append({
            "gate_id": gate_id, "outcome": "PASS" if passed else "FAIL",
            "remediation_status": remediation,
            "reason_code": f"global_{gate_id[2:].lower().replace('-', '_')}_{'verified' if passed else 'failed'}",
            "evidence_refs": sorted({ref for item in gates for ref in item["evidence_refs"]}),
            "reason": f"Global {gate_id} is the mechanical conjunction of DLT and SSQ results.",
        })
    return aggregated


def review(*_args: Any, **_kwargs: Any) -> Path:
    """Removed legacy route: the fixed PowerShell launcher is the only replay entrypoint."""
    raise CloseoutHold("direct review is removed; use scripts/phase0/p0_07_replay_launcher.ps1")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare_parser = subparsers.add_parser("prepare", help="produce non-terminal gate inputs after the real cutoff and exact 24/24 completion")
    prepare_parser.add_argument("--repo-root", type=Path, default=REPO)
    prepare_parser.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    prepare_parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = prepare(args.repo_root.resolve(), args.artifacts.resolve(), args.output.resolve())
        print(json.dumps({"status":"PASS","action":args.action,"artifact":str(path),"terminal_conclusion_generated":False,"network_used":False}, separators=(",", ":")))
        return 0
    except (CloseoutHold, ValidationError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "HOLD", "action": args.action, "error": str(exc), "terminal_conclusion_generated": False, "network_used": False}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
