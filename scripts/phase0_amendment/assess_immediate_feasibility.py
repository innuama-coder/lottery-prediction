"""Build the non-network Phase 0 v1.4 immediate feasibility assessment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
PHASE0 = REPO / "artifacts" / "phase-0"
DEFAULT_OUTPUT = REPO / "artifacts" / "phase-0-amendment-1" / "immediate-feasibility-report.json"
SCHEMA = REPO / "artifacts" / "phase-0-amendment-1" / "schemas" / "immediate-feasibility-report.schema.json"
CORRECTIVE_SCHEMA = REPO / "artifacts" / "phase-0-amendment-1" / "schemas" / "corrective-paths.schema.json"
CORRECTIVE_PATHS = REPO / "artifacts" / "phase-0-amendment-1" / "corrective-paths.json"
CONTRACT = REPO / "docs" / "roadmap" / "phase-0-acceptance-contract-v1.4.json"
INPUTS = {
    "contract_v1_4": CONTRACT,
    "plan_amendment_v1_4": REPO / "docs" / "roadmap" / "phase-0-data-feasibility-amendment-v1.4.md",
    "assessment_schema": SCHEMA,
    "corrective_paths_schema": CORRECTIVE_SCHEMA,
    "corrective_paths": CORRECTIVE_PATHS,
    "assessment_evaluator": Path(__file__).resolve(),
    "pipeline_test_suite": REPO / "tests" / "phase0" / "test_p0_04.py",
    "coverage_test_suite": REPO / "tests" / "phase0" / "test_p0_05.py",
    "source_catalog": PHASE0 / "source-catalog.json",
    "scope_freeze": PHASE0 / "scope-freeze.json",
    "coverage_report": PHASE0 / "coverage-report.json",
    "p0_05_work_plan": PHASE0 / "p0-05-work-plan.json",
    "evidence_manifest": PHASE0 / "evidence-manifest.jsonl",
}
TEST_PATTERNS = ("test_p0_04.py", "test_p0_05.py")


class AssessmentError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssessmentError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    result = []
    for line_number, raw in enumerate(path.read_bytes().splitlines(), 1):
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AssessmentError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise AssessmentError(f"expected JSON object at {path}:{line_number}")
        result.append(value)
    return result


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _source_summary(source: dict[str, Any]) -> dict[str, Any]:
    observed = source["observed_access"]
    return {
        "source_id": source["source_id"],
        "publisher": source["publisher"],
        "url": source["url"],
        "observed_at_utc": observed["observed_at_utc"],
        "http_status": observed.get("http_status"),
        "access_outcome": observed["outcome"],
        "approved_use": source["approved_use"],
        "collection_usable_now": observed["outcome"] == "accessible"
        and source["approved_use"] == "scheduled_low_rate_fetch",
    }


def _run_test(pattern: str, output_dir: Path) -> dict[str, Any]:
    command = [
        str(Path(sys.executable).resolve()), "-B", "-m", "unittest", "discover",
        "-s", "tests/phase0", "-p", pattern, "-q",
    ]
    completed = subprocess.run(command, cwd=REPO, capture_output=True, timeout=300, check=False)
    stem = Path(pattern).stem
    stdout_ref = f"artifacts/phase-0-amendment-1/test-outputs/{stem}.stdout.bin"
    stderr_ref = f"artifacts/phase-0-amendment-1/test-outputs/{stem}.stderr.bin"
    _atomic_write(output_dir / "test-outputs" / f"{stem}.stdout.bin", completed.stdout)
    _atomic_write(output_dir / "test-outputs" / f"{stem}.stderr.bin", completed.stderr)
    combined = completed.stdout + b"\n" + completed.stderr
    match = re.search(rb"Ran ([0-9]+) tests?", combined)
    passed = completed.returncode == 0 and re.search(rb"(?:^|\r?\n)OK(?:\r?\n|$)", combined) is not None
    return {
        "command": command,
        "exit_code": completed.returncode,
        "observed_test_count": int(match.group(1)) if match else 0,
        "result": "PASS" if passed else "FAIL",
        "stdout_ref": stdout_ref,
        "stderr_ref": stderr_ref,
        "stdout_sha256": _sha256_bytes(completed.stdout),
        "stderr_sha256": _sha256_bytes(completed.stderr),
    }


def _evidence_issue_audit(evidence: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for game in ("dlt", "ssq"):
        observed = [
            item.get("issue_id") for item in evidence
            if item.get("game") == game and item.get("status") in {"verified", "unverified"}
        ]
        verified = [
            item.get("issue_id") for item in evidence
            if item.get("game") == game and item.get("status") == "verified"
        ]
        valid_observed = [issue for issue in observed if isinstance(issue, str)]
        valid_verified = [issue for issue in verified if isinstance(issue, str)]
        result[game] = {
            "observed": set(valid_observed), "verified": set(valid_verified),
            "invalid_issue_id": len(valid_observed) != len(observed) or len(valid_verified) != len(verified),
            "duplicate_verified": len(valid_verified) != len(set(valid_verified)),
        }
    return result


def _validated_evidence_ref(reference: str) -> Path:
    if not isinstance(reference, str) or not reference or "\\" in reference:
        raise AssessmentError(f"invalid corrective evidence reference: {reference!r}")
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts:
        raise AssessmentError(f"corrective evidence reference escapes repository: {reference}")
    resolved = (REPO / relative).resolve()
    try:
        resolved.relative_to(REPO.resolve())
    except ValueError as exc:
        raise AssessmentError(f"corrective evidence reference escapes repository: {reference}") from exc
    if not resolved.is_file():
        raise AssessmentError(f"corrective evidence reference does not exist: {reference}")
    return resolved


def _corrective_evidence_refs(value: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for game in value.get("games", []):
        for action in game.get("actions", []):
            references.update(action.get("evidence_refs", []))
        for alternative in game.get("audited_alternatives", []):
            references.update(alternative.get("evidence_refs", []))
        references.update(game.get("exhaustion_evidence_refs", []))
    for reference in references:
        _validated_evidence_ref(reference)
    return references


def _corrective_paths_by_game(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    games = value.get("games", [])
    result = {item.get("game"): item for item in games if isinstance(item, dict)}
    if len(games) != 2 or set(result) != {"dlt", "ssq"}:
        raise AssessmentError("corrective-path assessment must contain dlt and ssq exactly once")
    for game, item in result.items():
        available = item["compliant_corrective_action_available"]
        exhausted = item["alternatives_exhausted_no_evidentiary_path"]
        if available == exhausted:
            raise AssessmentError(f"{game}: corrective path classification must be exactly one of available or exhausted")
        if available and not item["actions"]:
            raise AssessmentError(f"{game}: HOLD classification requires at least one concrete corrective action")
        if available and item["exhaustion_evidence_refs"]:
            raise AssessmentError(f"{game}: HOLD classification cannot claim exhaustion evidence")
        if exhausted:
            if item["actions"]:
                raise AssessmentError(f"{game}: STOP classification cannot retain available corrective actions")
            if not item["exhaustion_evidence_refs"]:
                raise AssessmentError(f"{game}: STOP classification requires exhaustion evidence")
            if not item["audited_alternatives"] or any(
                alternative["status"] != "exhausted" for alternative in item["audited_alternatives"]
            ):
                raise AssessmentError(f"{game}: STOP classification requires every audited alternative to be exhausted")
    _corrective_evidence_refs(value)
    return result


def build_report(
    *, evaluated_at_utc: str, test_runs: list[dict[str, Any]], catalog: dict[str, Any],
    scope: dict[str, Any], coverage: dict[str, Any], evidence: list[dict[str, Any]],
    corrective_paths: dict[str, Any],
) -> dict[str, Any]:
    if len(test_runs) != 2:
        raise AssessmentError("exactly two targeted test runs are required")
    tests_pass = all(item["result"] == "PASS" for item in test_runs)
    coverage_by_game = {item["game"]: item for item in coverage["games"]}
    scope_games = {item["game"]: item for item in scope["games"]}
    sample_games = {item["game"]: item for item in scope["corroboration_sample"]["games"]}
    evidence_audit = _evidence_issue_audit(evidence)
    readiness_by_game = {item["game"]: item for item in catalog["operational_readiness"]}
    corrective_by_game = _corrective_paths_by_game(corrective_paths)
    games = []
    for item in catalog["games"]:
        game = item["game"]
        coverage_item = coverage_by_game[game]
        frozen_target = sorted(issue for stratum in sample_games[game]["strata"] for issue in stratum["candidate_issue_ids"])
        minimum_interval = scope_games[game]["minimum_viable_interval"]
        frozen_minimum = [
            issue for issue in frozen_target
            if int(minimum_interval["start_issue"]) <= int(issue) <= int(minimum_interval["end_issue"])
        ]
        target_expected = len(frozen_target)
        minimum_expected = len(frozen_minimum)
        declared_tier = coverage_item["coverage_tier"]
        target_set = set(frozen_target)
        minimum_set = set(frozen_minimum)
        declared_target_observed = set(coverage_item["target_observed_issues"])
        declared_minimum_observed = set(coverage_item["minimum_observed_issues"])
        observed_set = evidence_audit[game]["observed"]
        verified_set = evidence_audit[game]["verified"]
        target_observed_set = observed_set & target_set
        minimum_observed_set = observed_set & minimum_set
        declared_missing = {entry.get("issue_id") for entry in coverage_item["missing"]}
        frozen_universe_valid = (
            coverage_item["target_expected_issues"] == frozen_target
            and coverage_item["minimum_expected_issues"] == frozen_minimum
            and len(target_set) == len(frozen_target)
            and len(minimum_set) == len(frozen_minimum)
            and minimum_set <= target_set
        )
        coverage_consistent = (
            frozen_universe_valid
            and declared_target_observed == target_observed_set
            and declared_minimum_observed == minimum_observed_set
            and declared_missing == target_set - target_observed_set
        )
        evidence_valid = (
            not evidence_audit[game]["invalid_issue_id"]
            and not evidence_audit[game]["duplicate_verified"]
            and verified_set <= target_set
            and coverage_consistent
        )
        acquisition_ready = readiness_by_game[game]["acquisition_ready"] is True
        corrective = corrective_by_game[game]
        full = tests_pass and evidence_valid and acquisition_ready and verified_set == target_set
        limited = tests_pass and evidence_valid and acquisition_ready and minimum_set <= verified_set
        sufficient = full or limited
        computed_tier = "target" if full else "minimum_viable" if limited else "none"
        primary = _source_summary(item["authoritative_primary"])
        corroborators = [_source_summary(source) for source in item["official_corroborators"]]
        shared = item["shared_upstream_assessment"]
        independent_assessable = (
            primary["collection_usable_now"]
            and any(source["collection_usable_now"] for source in corroborators)
            and "not an independent" not in shared.lower()
        )
        reasons = []
        if not primary["collection_usable_now"]:
            reasons.append("authoritative primary is not approved and accessible for collection")
        if declared_tier == "none":
            reasons.append("historical coverage is below the frozen minimum interval")
        if not acquisition_ready:
            reasons.append("no approved acquisition path is currently ready")
        if len(verified_set) == 0:
            reasons.append("there are no verified historical records")
        if evidence_audit[game]["duplicate_verified"]:
            reasons.append("verified evidence contains duplicate issue IDs")
        if not coverage_consistent:
            reasons.append("coverage observed sets do not match the evidence manifest")
        if not frozen_universe_valid:
            reasons.append("coverage expected sets do not match the frozen target and minimum universes")
        if not verified_set <= target_set:
            reasons.append("verified evidence contains issue IDs outside the frozen target interval")
        if not independent_assessable:
            reasons.append("no two accessible independent sources exist for real conflict testing")
        if not sufficient and corrective["compliant_corrective_action_available"]:
            reasons.append("compliant corrective actions remain available: " + ", ".join(action["action_id"] for action in corrective["actions"]))
        if not sufficient and corrective["alternatives_exhausted_no_evidentiary_path"]:
            reasons.append("audited compliant alternatives are exhausted with no official evidentiary path")
        games.append({
            "game": game,
            "authoritative_primary": primary,
            "official_corroborators": corroborators,
            "source_topology": shared,
            "independent_conflict_test": "ASSESSABLE" if independent_assessable else "NOT_ASSESSABLE",
            "coverage": {
                "target_expected": target_expected, "target_observed": len(target_observed_set),
                "minimum_expected": minimum_expected, "minimum_observed": len(minimum_observed_set),
                "verified_records": len(verified_set & target_set), "missing": len(target_set - target_observed_set),
                "declared_coverage_tier": declared_tier, "coverage_tier": computed_tier,
            },
            "modeling_data_sufficient": sufficient,
            "compliant_corrective_action_available": corrective["compliant_corrective_action_available"],
            "alternatives_exhausted_no_evidentiary_path": corrective["alternatives_exhausted_no_evidentiary_path"],
            "corrective_actions": [action["action_id"] for action in corrective["actions"]],
            "outcome": "PASS_FULL" if full else "PASS_LIMITED" if limited else "HOLD" if corrective["compliant_corrective_action_available"] else "STOP",
            "blocking_reasons": reasons,
        })
    all_sources_dated = all(
        source["observed_at_utc"] for game in games
        for source in [game["authoritative_primary"], *game["official_corroborators"]]
    )
    passing_outcomes = [game["outcome"] for game in games if game["outcome"] in {"PASS_FULL", "PASS_LIMITED"}]
    immediate_gates = [
        {"id": "I-SOURCE-STATUS", "status": "PASS" if all_sources_dated else "FAIL", "finding": "All declared sources have dated access and policy observations; observations are point-in-time, not permanence claims."},
        {"id": "I-REPLAY", "status": "PASS" if tests_pass else "FAIL", "finding": "Saved fixtures and the canonical capture path are covered by deterministic offline replay tests."},
        {"id": "I-FAILURE-AUDIT", "status": "PASS" if tests_pass else "FAIL", "finding": "Malformed inputs, tampering, duplicate IDs, and write conflicts fail closed in targeted tests."},
        {"id": "I-SOURCE-TOPOLOGY", "status": "LIMITATION_RECORDED", "finding": "Both provincial channels are shared-upstream publications; no real independent dual-source conflict test is currently possible."},
        {"id": "I-DATA-SUFFICIENCY", "status": "PASS" if passing_outcomes else "FAIL", "finding": "; ".join(
            f"{game['game'].upper()} has {game['coverage']['target_observed']} observed and {game['coverage']['verified_records']} unique verified records against a {game['coverage']['minimum_expected']}-issue minimum; modeling_ready={str(game['modeling_data_sufficient']).lower()}"
            for game in games
        ) + "."},
        {"id": "I-CORRECTIVE-CLASSIFICATION", "status": "PASS", "finding": "; ".join(
            f"{game['game'].upper()} corrective_action_available={str(game['compliant_corrective_action_available']).lower()}, alternatives_exhausted={str(game['alternatives_exhausted_no_evidentiary_path']).lower()}, outcome={game['outcome']}"
            for game in games
        ) + "."},
    ]
    project_decision = (
        "GO" if len(passing_outcomes) == 2 and all(outcome == "PASS_FULL" for outcome in passing_outcomes)
        else "LIMITED_GO" if passing_outcomes else "STOP" if all(game["outcome"] == "STOP" for game in games) else "HOLD"
    )
    return {
        "schema_version": "1.0.0", "artifact_type": "phase0_immediate_feasibility_report",
        "contract_version": "1.4", "evaluated_at_utc": evaluated_at_utc,
        "input_hashes": {
            **{name: _sha256_file(path) for name, path in INPUTS.items()},
            **{
                f"corrective_evidence::{reference}": _sha256_file(_validated_evidence_ref(reference))
                for reference in sorted(_corrective_evidence_refs(corrective_paths))
            },
        },
        "test_runs": test_runs, "games": games, "immediate_gates": immediate_gates,
        "prospective_observation": {
            "classification": "supplementary_non_blocking", "blocking": False,
            "evidence_ref": "artifacts/phase-0/p0-06-runtime-plan.json",
        },
        "project_decision": project_decision,
        "phase0_assessment_status": "COMPLETE" if project_decision in {"GO", "LIMITED_GO"} else "COMPLETE_WITH_STOP" if project_decision == "STOP" else "COMPLETE_WITH_HOLD",
    }


def _validate_instance(value: dict[str, Any], schema_path: Path) -> None:
    scripts = REPO / "scripts" / "phase0"
    sys.path.insert(0, str(scripts))
    try:
        from phase0lib import validate_schema_instance
        validate_schema_instance(value, _load_json(schema_path))
    finally:
        sys.path.pop(0)


def _validate_report(report: dict[str, Any]) -> None:
    _validate_instance(report, SCHEMA)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--evaluated-at-utc")
    args = parser.parse_args(argv)
    evaluated = args.evaluated_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    output = args.output.resolve()
    corrective_paths = _load_json(INPUTS["corrective_paths"])
    _validate_instance(corrective_paths, CORRECTIVE_SCHEMA)
    _corrective_paths_by_game(corrective_paths)
    test_runs = [_run_test(pattern, output.parent) for pattern in TEST_PATTERNS]
    report = build_report(
        evaluated_at_utc=evaluated, test_runs=test_runs,
        catalog=_load_json(INPUTS["source_catalog"]), coverage=_load_json(INPUTS["coverage_report"]),
        scope=_load_json(INPUTS["scope_freeze"]), evidence=_load_jsonl(INPUTS["evidence_manifest"]),
        corrective_paths=corrective_paths,
    )
    _validate_report(report)
    report_bytes = _canonical_bytes(report)
    _atomic_write(output, report_bytes)
    _atomic_write(output.with_suffix(output.suffix + ".sha256"), (_sha256_bytes(report_bytes) + "\n").encode("ascii"))
    print(json.dumps({
        "status": "PASS", "project_decision": report["project_decision"],
        "phase0_assessment_status": report["phase0_assessment_status"],
        "network_used": False, "output": str(output),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
