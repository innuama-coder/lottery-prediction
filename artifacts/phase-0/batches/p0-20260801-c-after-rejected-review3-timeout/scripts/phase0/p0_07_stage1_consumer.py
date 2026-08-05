"""Standalone fixed-path Stage 1 fixture consumer (standard library only)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
STAGING = REPO / "artifacts" / "phase-0" / ".p0-07-replay-staging"
FIXTURE = STAGING / "worker" / "proposed-stage1-handoff-fixture.json"
REF_INDEX = STAGING / "worker" / "logical-ref-index.json"
OUTPUT = STAGING / "consumer" / "p0-07-stage1-consumer-receipt.json"
GAMES = ("dlt", "ssq")
PASS = {"PASS_FULL", "PASS_LIMITED"}
TIERS = ("corroborated_official", "shared_upstream", "primary_only")
OUTCOMES = {"PASS_FULL", "PASS_LIMITED", "HOLD", "STOP"}
COVERAGE_TIERS = {"target", "minimum_viable", "none"}
TOP_KEYS = {
    "schema_version", "artifact_type", "contract_version", "project_decision", "active_games",
    "excluded_games", "game_results", "field_contract_ref", "rule_bundles_ref",
    "environment_lock_ref", "decision_evidence_ref",
}
GAME_KEYS = {"game", "per_game_outcome", "coverage_tier", "corroboration_tier", "corroboration_counts", "evidence_ref"}


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fail(message: str) -> None:
    raise ValueError(message)


def derive_project_decision(outcomes: list[str]) -> str:
    if len(outcomes) != 2 or any(outcome not in OUTCOMES for outcome in outcomes):
        fail("per-game outcomes are not exact")
    if all(outcome == "PASS_FULL" for outcome in outcomes):
        return "GO"
    if any(outcome in PASS for outcome in outcomes):
        return "LIMITED_GO"
    if any(outcome == "HOLD" for outcome in outcomes):
        return "HOLD"
    if all(outcome == "STOP" for outcome in outcomes):
        return "STOP"
    fail("per-game outcomes have no project decision")


def derive_corroboration_tier(counts: list[dict[str, object]]) -> str:
    if [entry.get("tier") for entry in counts] != list(TIERS):
        fail("corroboration counts are not in the exact tier order")
    if any(type(entry.get("count")) is not int or entry["count"] < 0 for entry in counts):
        fail("corroboration counts invalid")
    by_tier = {entry["tier"]: entry["count"] for entry in counts}
    if sum(by_tier.values()) == 0:
        return "none"
    if by_tier["primary_only"] > 0:
        return "primary_only"
    if by_tier["shared_upstream"] > 0:
        return "shared_upstream"
    return "corroborated_official"


def validate_outcome_coverage(outcome: str, coverage_tier: str) -> None:
    if outcome not in OUTCOMES or coverage_tier not in COVERAGE_TIERS:
        fail("per-game outcome or coverage tier invalid")
    if outcome == "PASS_FULL" and coverage_tier != "target":
        fail("PASS_FULL requires target coverage")
    if outcome == "PASS_LIMITED" and coverage_tier != "minimum_viable":
        fail("PASS_LIMITED requires minimum_viable coverage")


def main() -> int:
    if len(sys.argv) != 1:
        sys.stderr.write('{"status":"FAIL","error":"arguments are forbidden"}\n')
        return 2
    try:
        raw = FIXTURE.read_bytes()
        fixture = json.loads(raw.decode("utf-8"))
        if raw != canonical(fixture) + b"\n": fail("fixture bytes are not canonical JSON plus newline")
        if set(fixture) != TOP_KEYS: fail("fixture contains missing or hidden fields")
        if fixture["schema_version"] != "1.1.0" or fixture["artifact_type"] != "stage1_handoff_fixture": fail("fixture identity mismatch")
        if fixture["decision_evidence_ref"] != "derived/p0-07-gate-inputs.json": fail("decision evidence is not the fixed prior ref")
        if any("replay-report" in str(value) or "attestation" in str(value) or "acceptance-report" in str(value) for value in fixture.values()): fail("fixture depends on a future terminal artifact")
        games = fixture["game_results"]
        if len(games) != 2 or [item.get("game") for item in games] != list(GAMES): fail("fixture game declarations are not exact")
        index_raw = REF_INDEX.read_bytes(); index = json.loads(index_raw.decode("utf-8"))
        if index_raw != canonical(index) + b"\n" or not isinstance(index, dict): fail("logical ref index is not canonical")
        refs = [fixture["field_contract_ref"], fixture["rule_bundles_ref"], fixture["environment_lock_ref"], fixture["decision_evidence_ref"]]
        for item in games:
            if set(item) != GAME_KEYS: fail("game fixture contains missing or hidden fields")
            validate_outcome_coverage(item["per_game_outcome"], item["coverage_tier"])
            counts = item["corroboration_counts"]
            derived_tier = derive_corroboration_tier(counts)
            if item["corroboration_tier"] != derived_tier: fail("corroboration tier differs from counts")
            refs.extend(item["evidence_ref"])
        outcomes = [item["per_game_outcome"] for item in games]
        project_decision = derive_project_decision(outcomes)
        active = [item["game"] for item in games if item["per_game_outcome"] in PASS]
        excluded = [item["game"] for item in games if item["per_game_outcome"] not in PASS]
        if fixture["project_decision"] != project_decision: fail("project decision differs from per-game outcomes")
        if fixture["active_games"] != active or fixture["excluded_games"] != excluded: fail("active/excluded partition mismatch")
        for logical_ref in refs:
            record = index.get(logical_ref)
            if not isinstance(record, dict): fail(f"unresolved logical ref: {logical_ref}")
            bundle_path = STAGING / "worker" / record["bundle_path"]
            payload = bundle_path.read_bytes()
            if len(payload) != record["size"] or digest(payload) != record["sha256"]: fail(f"content-addressed ref mismatch: {logical_ref}")
        receipt = {
            "schema_version":"1.0.0","artifact_type":"p0_07_stage1_consumer_receipt","contract_version":"1.3",
            "fixture_sha256":digest(canonical(fixture)),"consumed_fixture_file_bytes_sha256":digest(raw),
            "project_decision":project_decision,"active_games":active,
            "excluded_games":excluded,"consumed_game_count":2,"declared_fields_only":True,
            "hidden_manual_transformations":False,"resolved_evidence_ref_count":len(set(refs)),
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=False)
        OUTPUT.write_bytes(canonical(receipt) + b"\n")
        sys.stdout.buffer.write(canonical({"status":"PASS","receipt":str(OUTPUT.relative_to(REPO)).replace("\\","/")}) + b"\n")
        return 0
    except Exception as exc:
        sys.stderr.buffer.write(canonical({"status":"FAIL","error":str(exc)}) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
