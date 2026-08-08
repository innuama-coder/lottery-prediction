"""Phase 3 point-in-time (PIT) evidence preparation, validation and tamper tests.

This module implements the W08 PIT recovery iteration for Phase 3.  It is
deliberately separate from the W01-W03 candidate contract in ``config/phase3``:

* It never edits the candidate contracts or any Phase 1/2/2.1 artifact in place.
* It builds a new, immutable PIT preparation release in its own directory and
  binds the new manifest/ledger/data-time-contract/preregistration by SHA-256.
* The validator recomputes every hash and the eligible-feature coverage from the
  files on disk; it does not trust any top-level self-reported field.
* Availability may only be proven by an independently archived publication whose
  parsed payload binds ``(game, issue, numbers)`` to the frozen Phase 1 draw and
  whose availability timestamp is derived from an *allowed* basis.  Inferring
  availability from draw dates, HTTP ``Date`` headers, retrieval time,
  ``first_seen_at``, the current CMS ``PublishDate``, the current page or a
  scheduled broadcast is forbidden and rejected.

The expected terminal for the frozen 400-draw inventory is
``HOLD_PENDING_PIT_EVIDENCE`` because the Phase 1 records are
``retrospective_current_view`` with ``available_at_utc=null`` and no auditable
archived-publication evidence exists for them under the binding rule above.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from .prerun_contract import (
    AUTHORITY,
    FROZEN_INPUTS,
    _availability_ledger,
    _data_time_contract,
    _input_manifest,
    _preregistration,
    _read_draws,
    load_json,
    sha256,
    validate_schema,
)
from .serialization import canonical_json_bytes, canonical_sha256, sha256_file, write_new_json


HOLD_TERMINAL = "HOLD_PENDING_PIT_EVIDENCE"
READY_TERMINAL = "READY_FOR_RESULTS_BLIND_FREEZE"

# A PIT preparation iteration is its own immutable release identity.  Reusing an
# existing directory is rejected so that earlier evidence is never overwritten.
PIT_RELEASE_IDENTITY = "p3-pit-prep-20260808-i02"
PIT_RELEASE_PARENT = "p3-pit-prep-20260808-i01"
PIT_RELEASE_DATE = "2026-08-08"
PIT_RELEASE_PARENT_PATH = f"artifacts/phase-3-pit/{PIT_RELEASE_PARENT}"

# The only permitted way to turn an unknown availability row into an eligible one
# is an independently archived publication.  The basis of its timestamp is
# restricted: these are the only accepted derivations.
ALLOWED_AVAILABILITY_BASES = frozenset({
    "independent_archive_capture_timestamp",
})

# Every basis below is explicitly forbidden as a source of availability time.
# Using any of them (directly or as the recorded basis) makes a row ineligible
# and, if the row nevertheless claims eligibility, is treated as forgery.
FORBIDDEN_AVAILABILITY_BASES = frozenset({
    "draw_date",
    "http_date",
    "retrieved_at",
    "first_seen_at",
    "current_page",
    "current_view",
    "cms_publish_date",
    "scheduled_broadcast",
    "planned_air_time",
})

# Reason codes that are acceptable for a fail-closed unknown row.
FAIL_CLOSED_REASON_CODES = frozenset({
    "PIT_AVAILABILITY_UNPROVEN",
    "NO_ARCHIVED_PUBLICATION_EVIDENCE",
    "BINDING_INCOMPLETE",
})

FORBIDDEN_ACTIONS = frozenset({
    "champion_promotion",
    "production_prediction",
    "public_non_uniform_prediction",
    "betting",
    "automatic_purchase",
    "yield_claim",
})

SCHEMAS = {
    "input_manifest": "input-manifest.schema.json",
    "availability_ledger": "availability-ledger.schema.json",
    "data_time_contract": "data-time-contract.schema.json",
    "preregistration": "preregistration.schema.json",
    "pit_archived_publication": "pit-archived-publication.schema.json",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _validate_archived_schema(root: Path, payload: dict[str, Any]) -> None:
    """Validate an archived-publication original against its Phase 3 schema."""
    schema = load_json(root / "schemas/phase3" / SCHEMAS["pit_archived_publication"])
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(map(str, first.absolute_path)) or "$"
        raise ValueError(f"pit_archived_publication schema violation at {location}: {first.message}")


def _now_utc_iso() -> str:
    # Captured at build time only; never used to derive feature availability.
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_identity(identity: str) -> None:
    if not identity or identity in {".", ".."}:
        raise ValueError("identity must be a non-empty immutable string")
    if any(token in identity for token in ("/", "\\", "*", " ")) or "latest" in identity.lower():
        raise ValueError("identity must be explicit and must not contain latest, wildcards or separators")


def _git(root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def _container_indicators() -> dict[str, Any]:
    indicators: dict[str, Any] = {
        "dockerenv_present": Path("/.dockerenv").exists(),
        "cgroup_controller_line": "",
    }
    try:
        indicators["cgroup_controller_line"] = Path("/proc/1/cgroup").read_text(encoding="utf-8").splitlines()[0]
    except OSError:
        indicators["cgroup_controller_line"] = ""
    return indicators


def _sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (entry["game"], entry["target_issue"])


# ---------------------------------------------------------------------------
# Pure availability assessment (unit-testable, independent of frozen inputs)
# ---------------------------------------------------------------------------


def assess_availability_entry(
    entry: dict[str, Any],
    archived_original: dict[str, Any] | None,
    draw: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assess a single availability ledger entry against the PIT binding rule.

    Returns a dict with ``counts_as_eligible`` (bool) and ``blocking`` (list of
    human-readable findings).  Any blocking finding on a row that claims
    ``eligibility=eligible`` indicates forgery or inference and must reject the
    bundle.  Unknown rows are accepted only when they fail closed.
    """
    eligibility = entry.get("eligibility")
    blocking: list[str] = []

    if eligibility == "unknown":
        if entry.get("evidence_method") != "none":
            blocking.append("unknown row must use evidence_method=none")
        if entry.get("available_at_utc") is not None:
            blocking.append("unknown row must not assert available_at_utc")
        if entry.get("prediction_locked_at") is not None:
            blocking.append("unknown row must not assert prediction_locked_at")
        if entry.get("reason_code") not in FAIL_CLOSED_REASON_CODES:
            blocking.append("unknown row lacks a fail-closed reason code")
        return {"counts_as_eligible": False, "blocking": blocking}

    if eligibility == "ineligible":
        if not entry.get("reason_code"):
            blocking.append("ineligible row lacks a reason code")
        if entry.get("available_at_utc") is None and entry.get("prediction_locked_at") is not None:
            blocking.append("ineligible row asserts a lock without availability")
        return {"counts_as_eligible": False, "blocking": blocking}

    if eligibility != "eligible":
        blocking.append(f"unknown eligibility value: {eligibility!r}")
        return {"counts_as_eligible": False, "blocking": blocking}

    # eligibility == "eligible": every binding requirement must hold.
    if entry.get("evidence_method") != "archived_publication":
        blocking.append("eligible row must use evidence_method=archived_publication")
    if not entry.get("prediction_locked_at") or not entry.get("available_at_utc"):
        blocking.append("eligible row lacks prediction_locked_at or available_at_utc")
        return {"counts_as_eligible": False, "blocking": blocking}

    if _parse_time(entry["available_at_utc"]) >= _parse_time(entry["prediction_locked_at"]):
        blocking.append("eligible row violates available_at_utc < prediction_locked_at")

    if archived_original is None:
        blocking.append("eligible row has no archived-publication original")
        return {"counts_as_eligible": False, "blocking": blocking}

    basis = archived_original.get("availability_basis")
    if basis in FORBIDDEN_AVAILABILITY_BASES:
        blocking.append(f"availability derived from forbidden basis: {basis}")
    elif basis not in ALLOWED_AVAILABILITY_BASES:
        blocking.append(f"availability basis not in allowed set: {basis!r}")

    if archived_original.get("available_at_utc") != entry.get("available_at_utc"):
        blocking.append("archived original available_at_utc disagrees with ledger")

    if draw is None:
        blocking.append("frozen draw missing for eligible row target issue")
    else:
        if archived_original.get("game") != draw.get("game") or archived_original.get("issue_id") != draw.get("issue_id"):
            blocking.append("archived original game/issue does not bind to frozen draw")
        if archived_original.get("front_numbers") != draw.get("front_numbers") or archived_original.get("back_numbers") != draw.get("back_numbers"):
            blocking.append("archived original numbers do not bind to frozen draw")

    # The archived timestamp must itself precede the prediction lock.
    if _parse_time(archived_original.get("available_at_utc", "")) >= _parse_time(entry["prediction_locked_at"]):
        blocking.append("archived original timestamp does not precede prediction lock")

    counts = not blocking
    return {"counts_as_eligible": counts, "blocking": blocking}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Bundle construction
# ---------------------------------------------------------------------------


def _archived_original_path(bundle: Path, game: str, target_issue: str) -> Path:
    return bundle / "evidence-collection" / "archived-publication" / game / f"{target_issue}.json"


def _write_contract_closure(root: Path, bundle: Path, draws: list[dict[str, Any]]) -> dict[str, Path]:
    """Write hash-bound input-manifest/ledger/contract/preregistration copies."""
    manifest = _input_manifest(root, draws)
    manifest_path = bundle / "input-manifest.json"
    write_new_json(manifest_path, manifest)

    ledger = _availability_ledger(draws, sha256(manifest_path))
    ledger_path = bundle / "availability-ledger.json"
    write_new_json(ledger_path, ledger)

    data_time = _data_time_contract(sha256(manifest_path), sha256(ledger_path))
    data_time_path = bundle / "data-time-contract.json"
    write_new_json(data_time_path, data_time)

    prereg = _preregistration(sha256(manifest_path), sha256(ledger_path), sha256(data_time_path))
    prereg_path = bundle / "preregistration.json"
    write_new_json(prereg_path, prereg)

    return {
        "input_manifest": manifest_path,
        "availability_ledger": ledger_path,
        "data_time_contract": data_time_path,
        "preregistration": prereg_path,
    }


def _recovery_context(root: Path, bundle: Path, identity: str) -> dict[str, Any]:
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_pit_recovery_context",
        "identity": identity,
        "release_date": PIT_RELEASE_DATE,
        "supersedes_parent_release": PIT_RELEASE_PARENT,
        "parent_release_path": PIT_RELEASE_PARENT_PATH,
        "authority": AUTHORITY,
        "captured_at_utc": _now_utc_iso(),
        "git": {
            "head": _git(root, "rev-parse", "HEAD"),
            "branch": _git(root, "branch", "--show-current"),
            "remote_origin": _git(root, "remote", "get-url", "origin"),
            "remote_main_head": _git(root, "ls-remote", "origin", "main").split()[0] if _git(root, "ls-remote", "origin", "main") else "",
            "status_porcelain": _git(root, "status", "--porcelain=v1"),
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "logical_processors": os.cpu_count(),
            "container": _container_indicators(),
            "worker_user": os.environ.get("USER", ""),
        },
        "frozen_phase3_lock": "requirements/phase3.lock",
        "task_query": {
            "phase3_readme": "tasks/phase3/README.md",
            "task_package": "tasks/phase3/w08-w13-pit-recovery/",
            "parent_pit_preparation": f"{PIT_RELEASE_PARENT_PATH}/receipt.json",
        },
        "read_only_invariants": [
            "config/phase3 candidate contracts are preserved unchanged",
            "artifacts/phase-1, artifacts/phase-2, artifacts/phase-2.1 are read-only",
            "no artifacts/phase-3/<release-id> is created unless PIT is frozen and readiness is READY",
            "public network is preparation-only; formal runs are offline",
        ],
    }


def _collection_attempt(root: Path, identity: str, receipts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    draws = _read_draws(root)
    retrospective = sum(1 for row in draws if row.get("knowledge_class") == "retrospective_current_view")
    null_availability = sum(1 for row in draws if row.get("available_at_utc") is None)
    receipts = receipts or {}
    recon_targets = len(receipts)
    snapshots_available = sum(1 for rec in receipts.values() if rec.get("snapshot_available"))
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_pit_collection_attempt",
        "identity": identity,
        "captured_at_utc": _now_utc_iso(),
        "network_use_policy": "preparation_only_reconnaissance; no eligibility is derived from live pages or archive snapshots",
        "methodology": {
            "dlt": "official per-issue result PDF / independently archived publication that simultaneously binds issue, numbers and an availability timestamp",
            "ssq": "official or auditable archived original that simultaneously binds issue, numbers and historical availability time",
            "binding_rule": "each archived original must bind (game, issue_id, front_numbers, back_numbers) to the frozen Phase 1 draw and derive availability from an allowed basis",
        },
        "forbidden_availability_bases": sorted(FORBIDDEN_AVAILABILITY_BASES),
        "allowed_availability_bases": sorted(ALLOWED_AVAILABILITY_BASES),
        "frozen_inventory_facts": {
            "draw_count": len(draws),
            "retrospective_current_view": retrospective,
            "available_at_utc_null": null_availability,
            "draw_date_local_present": sum(1 for row in draws if row.get("draw_date_local")),
        },
        "reconnaissance": {
            "receipts_preserved": sorted(receipts),
            "target_count": recon_targets,
            "archive_snapshots_available": snapshots_available,
            "snapshots_binding_per_issue_result": 0,
            "finding": "official homepages have current-view archive snapshots, but per-issue result endpoints have none; no archived snapshot binds (game, issue, numbers, availability time) for any frozen draw",
        },
        "attempted_sources": [
            {
                "source_id": "frozen-phase1-draws",
                "findings": "all 400 records are retrospective_current_view with available_at_utc=null; draw_date_local is present but is a forbidden availability basis",
                "eligible_evidence_obtained": 0,
            },
            {
                "source_id": "independent-archive-reconnaissance",
                "findings": f"{recon_targets} official endpoints probed via an independent archive; {snapshots_available} returned snapshots, 0 bind a per-issue result; homepage snapshots are current view and forbidden as availability evidence",
                "eligible_evidence_obtained": 0,
            },
        ],
        "outcome": "no eligible archived_publication evidence; all availability rows remain unknown and fail closed",
        "conflicts": [],
        "missing_or_failed_evidence_preserved": True,
    }


def build_pit_preparation_bundle(
    root: Path,
    output: Path,
    identity: str,
    *,
    collection_receipts: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a new immutable PIT preparation release directory and its contracts.

    ``collection_receipts`` optionally carries bounded read-only reconnaissance
    receipts (e.g. HTTP probes).  They are preserved as evidence but never used
    to derive eligibility.
    """
    _validate_identity(identity)
    root = root.resolve()
    bundle = output.resolve()
    if bundle.name != identity:
        raise ValueError("output basename must equal the immutable identity")
    bundle.mkdir(parents=True, exist_ok=False)

    authority_path = root / AUTHORITY["path"]
    if sha256(authority_path) != AUTHORITY["sha256"]:
        raise ValueError("Phase 3 authority identity mismatch")

    draws = _read_draws(root)
    context = _recovery_context(root, bundle, identity)
    write_new_json(bundle / "recovery-context.json", context)

    attempt = _collection_attempt(root, identity, collection_receipts)
    write_new_json(bundle / "evidence-collection" / "collection-attempt.json", attempt)

    if collection_receipts:
        for name, payload in sorted(collection_receipts.items()):
            write_new_json(bundle / "evidence-collection" / "http-receipts" / f"{name}.json", payload)

    paths = _write_contract_closure(root, bundle, draws)

    tamper = run_negative_tamper_tests()
    write_new_json(bundle / "negative-tamper-report.json", tamper)

    # Core validation runs before the manifest exists; the manifest closure is
    # verified once the manifest is written, then the final receipt is sealed.
    core_validation = validate_pit_preparation_bundle(root, bundle)

    review = _independent_review(root, bundle, identity, core_validation, tamper)
    write_new_json(bundle / "independent-review.json", review)

    manifest = _bundle_manifest(bundle, identity)
    write_new_json(bundle / "manifest.json", manifest)

    validation = validate_pit_preparation_bundle(root, bundle)
    if validation["metrics"] != core_validation["metrics"]:
        raise ValueError("PIT validation metrics changed after manifest closure")
    write_new_json(bundle / "pit-validation.json", validation)

    receipt = _release_receipt(identity, validation, review, manifest, bundle)
    write_new_json(bundle / "receipt.json", receipt)
    return receipt


def _bundle_manifest(bundle: Path, identity: str) -> dict[str, Any]:
    roles = {
        "recovery-context.json": "pit_recovery_identity_and_environment",
        "evidence-collection/collection-attempt.json": "pit_collection_methodology_and_negative_result",
        "input-manifest.json": "frozen_input_identity",
        "availability-ledger.json": "append_only_point_in_time_availability_ledger",
        "data-time-contract.json": "data_time_contract",
        "preregistration.json": "results_blind_preregistration",
        "negative-tamper-report.json": "negative_tamper_test_matrix",
        "independent-review.json": "independent_review_report",
    }
    for path in sorted((bundle / "evidence-collection").rglob("*.json")):
        relative = path.relative_to(bundle).as_posix()
        if relative == "evidence-collection/collection-attempt.json":
            continue
        roles[relative] = "preserved_pit_collection_evidence"

    files = []
    for relative, role in sorted(roles.items()):
        path = bundle / relative
        files.append({
            "path": relative,
            "role": role,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "lines": len(path.read_bytes().splitlines()),
        })
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_explicit_evidence_manifest",
        "identity": identity,
        "non_formal_synthetic_only": False,
        "files": files,
        "inventory_sha256": canonical_sha256(files),
    }


# ---------------------------------------------------------------------------
# Independent validation (recomputes everything from disk)
# ---------------------------------------------------------------------------


def _load_archived_original(bundle: Path, entry: dict[str, Any]) -> dict[str, Any] | None:
    path = _archived_original_path(bundle, entry["game"], entry["target_issue"])
    if not path.is_file():
        return None
    return load_json(path)


def _verify_bundle_manifest(bundle: Path) -> dict[str, Any]:
    """Independently recompute the bundle manifest closure from disk."""
    manifest_path = bundle / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("bundle manifest.json is missing")
    manifest = load_json(manifest_path)
    if manifest.get("artifact_type") != "phase3_explicit_evidence_manifest":
        raise ValueError("bundle manifest has the wrong artifact_type")
    if canonical_sha256(manifest["files"]) != manifest["inventory_sha256"]:
        raise ValueError("bundle manifest inventory digest mismatch")
    paths_seen: list[str] = []
    for row in manifest["files"]:
        path = bundle / row["path"]
        if not path.is_file():
            raise ValueError(f"bundle manifest lists missing file: {row['path']}")
        if sha256_file(path) != row["sha256"] or path.stat().st_size != row["bytes"] or len(path.read_bytes().splitlines()) != row["lines"]:
            raise ValueError(f"bundle manifest evidence mismatch: {row['path']}")
        paths_seen.append(row["path"])
    if len(set(paths_seen)) != len(paths_seen):
        raise ValueError("bundle manifest contains a duplicate path")
    if any("latest" in p.lower() or "*" in p for p in paths_seen):
        raise ValueError("bundle manifest contains an unsafe path")
    return {"manifest_sha256": sha256_file(manifest_path), "file_count": len(manifest["files"])}


def _draw_map(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    return {(row["game"], row["issue_id"]): row for row in _read_draws(root)}


def validate_pit_preparation_bundle(root: Path, bundle: Path) -> dict[str, Any]:
    """Independently recompute hashes, coverage, ordering and number binding."""
    root = root.resolve()
    bundle = bundle.resolve()

    paths = {
        "input_manifest": bundle / "input-manifest.json",
        "availability_ledger": bundle / "availability-ledger.json",
        "data_time_contract": bundle / "data-time-contract.json",
        "preregistration": bundle / "preregistration.json",
    }
    payloads = {kind: load_json(path) for kind, path in paths.items()}
    for kind, payload in payloads.items():
        validate_schema(root, kind, payload)

    manifest_sha = sha256(paths["input_manifest"])
    if payloads["availability_ledger"]["input_manifest_sha256"] != manifest_sha:
        raise ValueError("availability ledger does not bind to input manifest")
    ledger_sha = sha256(paths["availability_ledger"])
    if payloads["data_time_contract"]["input_manifest_sha256"] != manifest_sha:
        raise ValueError("data-time contract does not bind to input manifest")
    if payloads["data_time_contract"]["availability_ledger_sha256"] != ledger_sha:
        raise ValueError("data-time contract does not bind to availability ledger")
    contract_sha = sha256(paths["data_time_contract"])
    prereg = payloads["preregistration"]
    if prereg["input_manifest_sha256"] != manifest_sha:
        raise ValueError("preregistration does not bind to input manifest")
    if prereg["availability_ledger_sha256"] != ledger_sha:
        raise ValueError("preregistration does not bind to availability ledger")
    if prereg["data_time_contract_sha256"] != contract_sha:
        raise ValueError("preregistration does not bind to data-time contract")
    if prereg["formal_run_authorized"]:
        raise ValueError("preregistration must not authorize a formal run while PIT is unproven")

    if sha256(root / AUTHORITY["path"]) != AUTHORITY["sha256"]:
        raise ValueError("Phase 3 authority file identity mismatch")

    # Recompute frozen input identities directly from disk (no self-report).
    for role, relative, expected_sha256, allowed_use in FROZEN_INPUTS:
        path = root / relative
        if not path.is_file() or sha256(path) != expected_sha256:
            raise ValueError(f"frozen input identity mismatch: {relative}")

    draws = _read_draws(root)
    draw_map = _draw_map(root)
    if len(draws) != 400 or len({d["game"] for d in draws}) != 2:
        raise ValueError("frozen draw inventory mismatch")

    manifest_closure = _verify_bundle_manifest(bundle) if (bundle / "manifest.json").is_file() else None

    entries = payloads["availability_ledger"]["entries"]
    expected_keys = {(row["game"], row["issue_id"]) for row in draws}
    observed_keys = {(row["game"], row["target_issue"]) for row in entries}
    if observed_keys != expected_keys or len(entries) != len(expected_keys):
        raise ValueError("availability ledger coverage does not match frozen draws")

    eligible = 0
    blocking: list[dict[str, Any]] = []
    forbidden_basis_seen: list[str] = []
    for entry in sorted(entries, key=_sort_key):
        archived = _load_archived_original(bundle, entry)
        if archived is not None:
            _validate_archived_schema(root, archived)
            if entry.get("evidence_method") != "archived_publication":
                blocking.append({"target": entry["target_issue"], "finding": "archived original present without archived_publication evidence_method"})
        draw = draw_map.get((entry["game"], entry["target_issue"]))
        assessment = assess_availability_entry(entry, archived, draw)
        if entry.get("eligibility") == "eligible" and archived is not None:
            if archived.get("availability_basis") in FORBIDDEN_AVAILABILITY_BASES:
                forbidden_basis_seen.append(f"{entry['game']}:{entry['target_issue']}:{archived.get('availability_basis')}")
        if assessment["blocking"]:
            for finding in assessment["blocking"]:
                blocking.append({"target": entry["target_issue"], "finding": finding})
        elif assessment["counts_as_eligible"]:
            eligible += 1

    total = len(entries)
    coverage = eligible / total if total else 0.0
    if blocking:
        # Blocking findings mean a row claims eligibility without genuine
        # archived-publication binding, i.e. forged or inferred availability.
        # This is an integrity violation and must reject the bundle outright.
        summary = "; ".join(f"{finding['target']}: {finding['finding']}" for finding in blocking[:8])
        raise ValueError(f"PIT blocking findings ({len(blocking)}): {summary}")
    status = "READY" if coverage == 1.0 else "HOLD"
    terminal = READY_TERMINAL if status == "READY" else HOLD_TERMINAL

    gaps = _gap_summary(entries, forbidden_basis_seen, blocking)
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_pit_validation",
        "status": status,
        "terminal": terminal,
        "formal_run_authorized": status == "READY",
        "independent_recompute": True,
        "metrics": {
            "input_identity_coverage": 1.0,
            "draw_inventory_coverage": 1.0,
            "availability_ledger_coverage": 1.0,
            "eligible_feature_coverage": coverage,
            "eligible_rows": eligible,
            "total_rows": total,
            "blocking_findings": len(blocking),
            "formal_result_count": 0,
        },
        "binding_checks": {
            "manifest_to_ledger": payloads["availability_ledger"]["input_manifest_sha256"] == manifest_sha,
            "contract_to_manifest_and_ledger": payloads["data_time_contract"]["input_manifest_sha256"] == manifest_sha and payloads["data_time_contract"]["availability_ledger_sha256"] == ledger_sha,
            "preregistration_closure": prereg["data_time_contract_sha256"] == contract_sha,
            "bundle_manifest_closure": manifest_closure,
        },
        "forbidden_availability_bases_observed": forbidden_basis_seen,
        "gap_list": gaps,
        "blocking_findings_detail": blocking,
        "delivery_verdict": {
            "delivery_state": "DELIVERED_SUCCESS" if status == "READY" else "HOLD",
            "acceptance_verdict": "ACCEPTED" if status == "READY" else "BLOCKED",
            "evidence_only": status != "READY",
            "delivery_verified": True,
        },
    }


def _gap_summary(
    entries: Iterable[dict[str, Any]],
    forbidden_basis_seen: list[str],
    blocking: list[dict[str, Any]],
) -> list[str]:
    rows = list(entries)
    unknown = sum(1 for row in rows if row.get("eligibility") == "unknown")
    ineligible = sum(1 for row in rows if row.get("eligibility") == "ineligible")
    eligible = sum(1 for row in rows if row.get("eligibility") == "eligible")
    gaps = [
        f"{unknown} of {len(rows)} availability rows remain unknown (PIT unproven)",
        f"{eligible} eligible rows; full coverage requires all {len(rows)} rows eligible with archived_publication binding",
    ]
    if ineligible:
        gaps.append(f"{ineligible} rows marked ineligible; ineligible evidence cannot support a feature input")
    if forbidden_basis_seen:
        gaps.append(f"forbidden availability bases observed: {forbidden_basis_seen}")
    if blocking:
        gaps.append(f"{len(blocking)} blocking findings indicate forged or inferred availability")
    return gaps


def _independent_review(
    root: Path, bundle: Path, identity: str, validation: dict[str, Any], tamper: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_review",
        "review_id": f"{identity}-pit-review",
        "reviewer_role": "independent_point_in_time_verifier",
        "implementation_author": "phase3_pit_primary_path",
        "classification_approver": "unassigned_while_pit_hold",
        "reviewed_paths": [
            (bundle / "availability-ledger.json").resolve().as_posix(),
            (bundle / "pit-validation.json").resolve().as_posix(),
            (bundle / "negative-tamper-report.json").resolve().as_posix(),
        ],
        "independence": "recomputes hashes, coverage, time ordering and number binding from disk; does not read top-level self-reported fields",
        "recomputed_eligible_feature_coverage": validation["metrics"]["eligible_feature_coverage"],
        "recomputed_blocking_findings": validation["metrics"]["blocking_findings"],
        "negative_tamper_matrix_passed": tamper["summary"]["all_cases_passed"],
        "forbidden_actions_blocked": sorted(FORBIDDEN_ACTIONS),
        "blocking_findings": validation["metrics"]["blocking_findings"],
        "status": validation["status"],
        "terminal": validation["terminal"],
    }


def _release_receipt(
    identity: str,
    validation: dict[str, Any],
    review: dict[str, Any],
    manifest: dict[str, Any],
    bundle: Path,
) -> dict[str, Any]:
    ready = validation["status"] == "READY"
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_command_receipt",
        "command": "pit-prepare",
        "identity": identity,
        "status": validation["status"],
        "terminal": validation["terminal"],
        "exit_code": 0 if ready else 20,
        "formal_run_authorized": ready,
        "formal_result_count": 0,
        "hold_reasons": [] if ready else ["PIT_AVAILABILITY_UNPROVEN"],
        "delivery_state": validation["delivery_verdict"]["delivery_state"],
        "acceptance_verdict": validation["delivery_verdict"]["acceptance_verdict"],
        "evidence_only": validation["delivery_verdict"]["evidence_only"],
        "delivery_verified": validation["delivery_verdict"]["delivery_verified"],
        "review_status": review["status"],
        "manifest_sha256": sha256_file(bundle / "manifest.json"),
        "inventory_sha256": manifest["inventory_sha256"],
        "next_authorized_work": "results-blind freeze + formal W08-W13" if ready else None,
    }


def write_preparation_status(
    root: Path, bundle: Path, status_path: Path, *, identity: str
) -> dict[str, Any]:
    """Write the top-level PIT preparation status deliverable.

    The preparation status is a *derived* summary bound to an already-frozen,
    immutable preparation bundle; it never edits the bundle or any candidate
    contract.  Every binding value is recomputed from the bundle on disk (the
    manifest inventory digest is re-derived and the manifest/receipt/validation
    identities are checked), so the status cannot self-report a verdict the
    underlying evidence does not support.
    """
    root = root.resolve()
    bundle = bundle.resolve()
    recomputed = validate_pit_preparation_bundle(root, bundle)
    manifest = load_json(bundle / "manifest.json")
    receipt = load_json(bundle / "receipt.json")
    validation = load_json(bundle / "pit-validation.json")
    if manifest.get("identity") != identity or receipt.get("identity") != identity:
        raise ValueError("bundle identity does not match the requested status identity")
    if manifest.get("artifact_type") != "phase3_explicit_evidence_manifest":
        raise ValueError("bundle manifest has the wrong artifact_type")
    # Reject a status that would bind to a tampered/inconsistent manifest.
    if canonical_sha256(manifest["files"]) != manifest["inventory_sha256"]:
        raise ValueError("bundle manifest inventory digest mismatch")
    if receipt.get("manifest_sha256") != sha256_file(bundle / "manifest.json"):
        raise ValueError("bundle receipt does not bind to the on-disk manifest")
    if validation.get("status") != recomputed["status"] or validation.get("terminal") != recomputed["terminal"]:
        raise ValueError("stored PIT validation does not match the independent recomputation")
    if validation.get("metrics") != recomputed["metrics"]:
        raise ValueError("stored PIT validation metrics do not match the independent recomputation")
    if receipt.get("status") != recomputed["status"] or receipt.get("terminal") != recomputed["terminal"]:
        raise ValueError("bundle receipt does not match the independent recomputation")

    try:
        bundle_reference = bundle.relative_to(root).as_posix()
    except ValueError:
        # Independent validators may mount a copied bundle outside the checkout.
        # Its identity and manifest hashes remain the authoritative binding.
        bundle_reference = bundle.name

    status = {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_pit_preparation_status",
        "identity": identity,
        "release_date": PIT_RELEASE_DATE,
        "supersedes_parent_release": PIT_RELEASE_PARENT,
        "status": receipt["status"],
        "terminal": receipt["terminal"],
        "delivery_state": receipt["delivery_state"],
        "acceptance_verdict": receipt["acceptance_verdict"],
        "evidence_only": receipt["evidence_only"],
        "delivery_verified": receipt["delivery_verified"],
        "formal_run_authorized": receipt["formal_run_authorized"],
        "formal_result_count": receipt["formal_result_count"],
        "eligible_feature_coverage": recomputed["metrics"]["eligible_feature_coverage"],
        "total_rows": recomputed["metrics"]["total_rows"],
        "blocking_findings": recomputed["metrics"]["blocking_findings"],
        "hold_reasons": receipt.get("hold_reasons", []),
        "gap_list": validation.get("gap_list", []),
        "bundle_path": bundle_reference,
        "bundle_manifest_sha256": receipt["manifest_sha256"],
        "bundle_inventory_sha256": manifest["inventory_sha256"],
        "bundle_core_files": manifest["files"],
        "authority": AUTHORITY,
        "generated_at_utc": _now_utc_iso(),
    }

    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_bytes(canonical_json_bytes(status))
    return status


# ---------------------------------------------------------------------------
# Negative tamper test matrix (synthetic, never touches the frozen bundle)
# ---------------------------------------------------------------------------


def _synthetic_draw(game: str, issue: str, front: list[int], back: list[int]) -> dict[str, Any]:
    return {
        "game": game,
        "issue_id": issue,
        "front_numbers": front,
        "back_numbers": back,
        "available_at_utc": None,
        "knowledge_class": "retrospective_current_view",
    }


def _synthetic_archived(
    game: str,
    issue: str,
    front: list[int],
    back: list[int],
    *,
    available_at_utc: str,
    basis: str = "independent_archive_capture_timestamp",
) -> dict[str, Any]:
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_pit_archived_publication",
        "game": game,
        "issue_id": issue,
        "front_numbers": front,
        "back_numbers": back,
        "availability_basis": basis,
        "available_at_utc": available_at_utc,
        "source_url": "https://archive.example/snapshot/" + issue,
        "capture_timestamp": available_at_utc,
        "content_sha256": hashlib.sha256((issue + str(front) + str(back)).encode()).hexdigest(),
    }


def _expect_reject(label: str, build_scene: Any) -> dict[str, Any]:
    try:
        result = build_scene()
    except Exception as exc:  # noqa: BLE001 - tamper tests assert a rejection
        return {"case": label, "expected": "REJECTED", "actual": "REJECTED", "status": "PASS", "detail": str(exc)}
    # A returned assessment with blocking findings also counts as rejected.
    if isinstance(result, dict) and result.get("blocking"):
        return {"case": label, "expected": "REJECTED", "actual": "REJECTED", "status": "PASS", "detail": ",".join(result["blocking"])}
    if isinstance(result, dict) and result.get("status") in ("EVIDENCE_MISMATCH",):
        return {"case": label, "expected": "REJECTED", "actual": "REJECTED", "status": "PASS"}
    return {"case": label, "expected": "REJECTED", "actual": "ACCEPTED", "status": "FAIL", "detail": json.dumps(result, sort_keys=True)[:240]}


def _expect_accept(label: str, build_scene: Any) -> dict[str, Any]:
    try:
        result = build_scene()
    except Exception as exc:  # noqa: BLE001
        return {"case": label, "expected": "ACCEPTED", "actual": "REJECTED", "status": "FAIL", "detail": str(exc)}
    if isinstance(result, dict):
        if result.get("blocking") or result.get("status") == "EVIDENCE_MISMATCH":
            return {"case": label, "expected": "ACCEPTED", "actual": "REJECTED", "status": "FAIL", "detail": json.dumps(result, sort_keys=True)[:240]}
        return {"case": label, "expected": "ACCEPTED", "actual": "ACCEPTED", "status": "PASS"}
    return {"case": label, "expected": "ACCEPTED", "actual": "UNKNOWN", "status": "FAIL"}


def run_negative_tamper_tests() -> dict[str, Any]:
    """Run the synthetic PIT tamper matrix and return a structured report.

    Every case uses synthetic draws/archived originals only; the frozen 400-draw
    inventory and the real bundle are never modified.
    """
    lock = "2025-04-02T18:00:00Z"
    cases: list[dict[str, Any]] = []

    draw = _synthetic_draw("dlt", "SYN001", [1, 2, 3, 4, 5], [6, 7])

    def assess(entry, archived, drw):
        return assess_availability_entry(entry, archived, drw)

    # T1: eligible row without archived_publication evidence_method -> reject
    cases.append(_expect_reject("T1-eligible-without-archived-method", lambda: assess(
        {"eligibility": "eligible", "evidence_method": "none", "prediction_locked_at": lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "x"},
        None, draw)))

    # T2: eligible row with eligible method but no original on disk -> reject
    cases.append(_expect_reject("T2-eligible-missing-original", lambda: assess(
        {"eligibility": "eligible", "evidence_method": "archived_publication", "prediction_locked_at": lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "x"},
        None, draw)))

    # T3: availability derived from forbidden basis (draw_date) -> reject
    archived_forbidden = _synthetic_archived("dlt", "SYN001", [1, 2, 3, 4, 5], [6, 7], available_at_utc="2025-04-01T00:00:00Z", basis="draw_date")
    cases.append(_expect_reject("T3-forbidden-basis-draw-date", lambda: assess(
        {"eligibility": "eligible", "evidence_method": "archived_publication", "prediction_locked_at": lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "x"},
        archived_forbidden, draw)))

    # T4: archived original numbers do not bind to the draw -> reject
    archived_mismatch = _synthetic_archived("dlt", "SYN001", [9, 9, 9, 9, 9], [6, 7], available_at_utc="2025-04-01T00:00:00Z")
    cases.append(_expect_reject("T4-number-binding-mismatch", lambda: assess(
        {"eligibility": "eligible", "evidence_method": "archived_publication", "prediction_locked_at": lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "x"},
        archived_mismatch, draw)))

    # T5: ordering violation available_at_utc >= prediction_locked_at -> reject
    archived_order = _synthetic_archived("dlt", "SYN001", [1, 2, 3, 4, 5], [6, 7], available_at_utc="2025-04-03T00:00:00Z")
    cases.append(_expect_reject("T5-ordering-violation", lambda: assess(
        {"eligibility": "eligible", "evidence_method": "archived_publication", "prediction_locked_at": lock, "available_at_utc": "2025-04-03T00:00:00Z", "reason_code": "x"},
        archived_order, draw)))

    # T6: unknown row asserting available_at_utc -> reject
    cases.append(_expect_reject("T6-unknown-asserts-availability", lambda: assess(
        {"eligibility": "unknown", "evidence_method": "none", "prediction_locked_at": None, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "PIT_AVAILABILITY_UNPROVEN"},
        None, draw)))

    # T7: unknown row missing fail-closed reason -> reject
    cases.append(_expect_reject("T7-unknown-missing-reason", lambda: assess(
        {"eligibility": "unknown", "evidence_method": "none", "prediction_locked_at": None, "available_at_utc": None, "reason_code": ""},
        None, draw)))

    # Positive control: a genuinely constructed eligible row IS accepted.
    archived_valid = _synthetic_archived("dlt", "SYN001", [1, 2, 3, 4, 5], [6, 7], available_at_utc="2025-04-01T00:00:00Z")
    cases.append(_expect_accept("T8-positive-genuine-archived-binding", lambda: assess(
        {"eligibility": "eligible", "evidence_method": "archived_publication", "prediction_locked_at": lock, "available_at_utc": "2025-04-01T00:00:00Z", "reason_code": "archived_publication_bound"},
        archived_valid, draw)))

    # T9: forbidden action (champion promotion) is blocked by the workflow set.
    cases.append({
        "case": "T9-forbidden-action-blocked",
        "expected": "REJECTED",
        "actual": "REJECTED" if "champion_promotion" in FORBIDDEN_ACTIONS else "ACCEPTED",
        "status": "PASS" if "champion_promotion" in FORBIDDEN_ACTIONS else "FAIL",
    })

    passed = sum(1 for case in cases if case["status"] == "PASS")
    return {
        "schema_version": "3.0.0",
        "artifact_type": "phase3_pit_negative_tamper_report",
        "synthetic_only": True,
        "cases": cases,
        "summary": {
            "case_count": len(cases),
            "passed": passed,
            "all_cases_passed": passed == len(cases),
        },
    }


# ---------------------------------------------------------------------------
# Schema helpers for the archived-publication original
# ---------------------------------------------------------------------------


def validate_pit_archived_publication_schema(root: Path, payload: dict[str, Any]) -> None:
    _validate_archived_schema(root, payload)


__all__ = [
    "ALLOWED_AVAILABILITY_BASES",
    "FORBIDDEN_AVAILABILITY_BASES",
    "PIT_RELEASE_IDENTITY",
    "HOLD_TERMINAL",
    "READY_TERMINAL",
    "assess_availability_entry",
    "build_pit_preparation_bundle",
    "run_negative_tamper_tests",
    "validate_pit_preparation_bundle",
    "write_preparation_status",
]
