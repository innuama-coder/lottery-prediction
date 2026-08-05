"""Read-only audit and staging of the frozen Phase 0 snapshot."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lottery_data.models import ContractViolation as _ContractViolation
from lottery_data.serialization import sha256_file


def ContractViolation(message: str) -> _ContractViolation:
    """Create a transform-scoped violation using the shared contract error."""
    return _ContractViolation("bootstrap-transform", message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractViolation(f"invalid JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise ContractViolation(f"expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ContractViolation(f"cannot read JSONL: {path}") from exc
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line:
            raise ContractViolation(f"blank JSONL line: {path}:{line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractViolation(f"invalid JSONL: {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ContractViolation(f"expected JSON object: {path}:{line_number}")
        values.append(value)
    return values


def load_source_catalog(path: Path) -> dict[str, Any]:
    catalog = load_json(path)
    if catalog.get("applicability") != "Phase 1 internal research using the frozen Phase 0 snapshot only":
        raise ContractViolation("source catalog applicability is not frozen Phase 1 snapshot research")
    if catalog.get("production_collection_approved") is not False or catalog.get("redistribution_approved") is not False:
        raise ContractViolation("source catalog must fail closed for production and redistribution")
    sources = catalog.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ContractViolation("source catalog has no sources")
    ids: set[str] = set()
    publishers: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ContractViolation("invalid source catalog entry")
        source_id = source.get("source_id")
        publisher_id = source.get("publisher_id")
        if not isinstance(source_id, str) or source_id in ids:
            raise ContractViolation(f"duplicate or invalid source id: {source_id}")
        if not isinstance(publisher_id, str):
            raise ContractViolation(f"missing publisher id for {source_id}")
        if source.get("approved_for_phase1_internal_snapshot_research") is not True:
            raise ContractViolation(f"source is not approved for snapshot research: {source_id}")
        ids.add(source_id)
        publishers.add(publisher_id)
    if len(publishers) != len(ids):
        raise ContractViolation("Phase 1 sources must map to distinct stable publisher ids")
    return catalog


def source_index(source_catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    sources = source_catalog.get("sources")
    if not isinstance(sources, list):
        raise ContractViolation("source catalog sources must be an array")
    return {str(item["source_id"]): dict(item) for item in sources}


def audit_snapshot(snapshot_root: Path) -> dict[str, Any]:
    snapshot_root = snapshot_root.resolve()
    required = {
        "capture_manifest": snapshot_root / "capture-manifest.jsonl",
        "request_events": snapshot_root / "request-events.jsonl",
        "collection_summary": snapshot_root / "collection-summary.json",
        "artifact_hashes": snapshot_root / "artifact-hashes.json",
        "canonical": snapshot_root / "consensus" / "canonical-records.jsonl",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise ContractViolation(f"snapshot required files missing: {missing}")

    artifact_hashes = load_json(required["artifact_hashes"])
    for relative, path in (
        ("capture-manifest.jsonl", required["capture_manifest"]),
        ("request-events.jsonl", required["request_events"]),
        ("collection-summary.json", required["collection_summary"]),
        ("consensus/canonical-records.jsonl", required["canonical"]),
    ):
        expected = artifact_hashes.get(relative)
        if expected != sha256_file(path):
            raise ContractViolation(f"snapshot artifact SHA-256 mismatch: {relative}")

    summary = load_json(required["collection_summary"])
    entries = load_jsonl(required["capture_manifest"])
    events = load_jsonl(required["request_events"])
    if summary.get("status") != "PASS" or summary.get("successful_requests") != 30:
        raise ContractViolation("snapshot collection summary is not the frozen 30-request success")
    if len(entries) != 30:
        raise ContractViolation(f"capture manifest must contain 30 requests, found {len(entries)}")
    request_ids = [entry.get("request_id") for entry in entries]
    if len(set(request_ids)) != 30:
        raise ContractViolation("capture manifest request ids are not unique")
    started = [event.get("request_id") for event in events if event.get("event") == "request_started"]
    succeeded_events = [event for event in events if event.get("event") == "request_succeeded"]
    succeeded = [event.get("request_id") for event in succeeded_events]
    failed = [event.get("request_id") for event in events if event.get("event") == "request_failed"]
    if failed or sorted(started) != sorted(request_ids) or sorted(succeeded) != sorted(request_ids):
        raise ContractViolation("request-event audit does not close over capture manifest")
    terminal_by_id = {str(event["request_id"]): event for event in succeeded_events}
    if len(terminal_by_id) != 30:
        raise ContractViolation("request success events are not unique")

    for entry in entries:
        if entry.get("outcome") != "success" or entry.get("http_status") != 200:
            raise ContractViolation(f"unsuccessful captured request: {entry.get('request_id')}")
        raw_ref = entry.get("raw_ref")
        if not isinstance(raw_ref, str):
            raise ContractViolation(f"missing raw_ref: {entry.get('request_id')}")
        raw_path = snapshot_root / raw_ref
        if not raw_path.is_file() or sha256_file(raw_path) != entry.get("raw_sha256"):
            raise ContractViolation(f"raw evidence SHA-256 mismatch: {raw_ref}")
        if raw_path.stat().st_size != entry.get("content_length"):
            raise ContractViolation(f"raw evidence length mismatch: {raw_ref}")
        terminal = terminal_by_id[str(entry["request_id"])]
        for field in (
            "source_id", "game", "url", "http_status", "captured_at_utc", "raw_ref", "raw_sha256", "content_length",
        ):
            if terminal.get(field) != entry.get(field):
                raise ContractViolation(f"capture/request-event mismatch for {field}: {entry['request_id']}")

    return {
        "snapshot_id": snapshot_root.name,
        "snapshot_root": snapshot_root,
        "entries": entries,
        "entry_by_request_id": {str(entry["request_id"]): entry for entry in entries},
        "canonical_path": required["canonical"],
        "canonical_sha256": artifact_hashes["consensus/canonical-records.jsonl"],
        "capture_manifest_sha256": artifact_hashes["capture-manifest.jsonl"],
        "request_events_sha256": artifact_hashes["request-events.jsonl"],
        "collection_summary": summary,
    }


def build_bootstrap_request_plan(
    snapshot_root: Path,
    games: Sequence[str],
    source_catalog: Mapping[str, Any],
) -> list[dict[str, Any]]:
    audit = audit_snapshot(snapshot_root)
    wanted = set(games)
    if not wanted or not wanted <= {"ssq", "dlt"}:
        raise ContractViolation(f"unsupported game selection: {sorted(wanted)}")
    sources = source_index(source_catalog)
    result: list[dict[str, Any]] = []
    for entry in audit["entries"]:
        if entry["game"] not in wanted:
            continue
        source = sources.get(entry["source_id"])
        if source is None:
            raise ContractViolation(f"captured source absent from Phase 1 catalog: {entry['source_id']}")
        if entry["game"] not in source.get("games", []):
            raise ContractViolation(f"source/game not approved: {entry['source_id']}/{entry['game']}")
        result.append({
            "request_id": entry["request_id"],
            "sequence": len(result) + 1,
            "source_id": entry["source_id"],
            "publisher_id": source["publisher_id"],
            "game": entry["game"],
            "method": "SNAPSHOT",
            "url": entry["url"],
            "input_ref": entry["raw_ref"],
        })
    if wanted == {"ssq", "dlt"} and len(result) != 30:
        raise ContractViolation(f"full bootstrap plan must contain 30 requests, found {len(result)}")
    return result


def materialize_snapshot_request(
    request: Mapping[str, Any],
    snapshot_root: Path,
    run_raw_root: Path | None,
) -> dict[str, Any]:
    audit = audit_snapshot(snapshot_root)
    entry = audit["entry_by_request_id"].get(str(request.get("request_id")))
    if entry is None:
        raise ContractViolation(f"request not present in capture manifest: {request.get('request_id')}")
    for field in ("source_id", "game", "url"):
        if request.get(field) != entry.get(field):
            raise ContractViolation(f"request/capture mismatch for {field}: {request.get('request_id')}")
    if request.get("input_ref") != entry.get("raw_ref") or request.get("method") != "SNAPSHOT":
        raise ContractViolation(f"request is not the captured snapshot input: {request.get('request_id')}")

    source_path = snapshot_root.resolve() / entry["raw_ref"]
    if sha256_file(source_path) != entry["raw_sha256"]:
        raise ContractViolation(f"raw evidence SHA-256 mismatch: {entry['raw_ref']}")
    raw_path = source_path
    if run_raw_root is not None:
        relative_inside_raw = Path(entry["raw_ref"]).relative_to("raw")
        raw_path = run_raw_root / relative_inside_raw
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, raw_path)
        if sha256_file(raw_path) != entry["raw_sha256"]:
            raise ContractViolation(f"staged raw SHA-256 mismatch: {entry['raw_ref']}")

    return {
        "request_id": entry["request_id"],
        "source_id": entry["source_id"],
        "publisher_id": request["publisher_id"],
        "game": entry["game"],
        "url": entry["url"],
        "captured_at_utc": entry["captured_at_utc"],
        "raw_ref": entry["raw_ref"],
        "raw_sha256": entry["raw_sha256"],
        "raw_path": raw_path,
    }
