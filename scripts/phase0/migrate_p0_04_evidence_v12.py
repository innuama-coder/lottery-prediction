"""One-shot, fail-closed migration of archived P0-04 evidence to schema 1.2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from p0_04_http import clock_check_from_json
from p0_04_pipeline import verify_captures
from phase0lib import ValidationError, canonical_json_bytes, load_json, load_jsonl, validate_schema_instance


REPO = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACTS = REPO / "artifacts" / "phase-0"
DEFAULT_ARCHIVE = DEFAULT_ARTIFACTS / "batches" / "p0-04-20260801-a"
DEFAULT_OUTPUT = DEFAULT_ARTIFACTS / "p0-04-evidence-migration-p0-20260801-b.json"
MIGRATION_SCHEMA = DEFAULT_ARTIFACTS / "schemas" / "p0-04-evidence-migration.schema.json"
EVIDENCE_SCHEMA = DEFAULT_ARTIFACTS / "schemas" / "evidence-manifest.schema.json"


class MigrationHold(RuntimeError):
    """Raised before any canonical write when immutable preconditions fail."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def migrate(artifacts: Path, archive: Path, output: Path) -> dict[str, Any]:
    artifacts, archive, output = artifacts.resolve(), archive.resolve(), output.resolve()
    archived_artifacts = archive / "artifacts" / "phase-0"
    snapshot_path = archive / "snapshot-manifest.json"
    snapshot = load_json(snapshot_path)
    snapshot_records = {item["source_path"]: item for item in snapshot["files"]}
    archive_manifest = archived_artifacts / "evidence-manifest.jsonl"
    canonical_manifest = artifacts / "evidence-manifest.jsonl"
    if not archive_manifest.is_file() or not canonical_manifest.is_file():
        raise MigrationHold("archive or canonical evidence manifest is missing")
    archive_old_bytes = archive_manifest.read_bytes()
    canonical_old_bytes = canonical_manifest.read_bytes()
    archive_old_hash = sha256_bytes(archive_old_bytes)
    canonical_old_hash = sha256_bytes(canonical_old_bytes)
    manifest_record = snapshot_records.get("artifacts/phase-0/evidence-manifest.jsonl")
    if manifest_record is None or manifest_record["sha256"] != archive_old_hash:
        raise MigrationHold("archive manifest hash differs from immutable snapshot manifest")
    canonical_is_old = canonical_old_bytes == archive_old_bytes

    protected = [
        item for item in snapshot["files"]
        if item["source_path"] == "artifacts/phase-0/clock-check-p0-04.json"
        or item["source_path"] == "artifacts/phase-0/environment-lock.json"
        or item["source_path"].startswith("artifacts/phase-0/raw/")
        or item["source_path"].startswith("artifacts/phase-0/parsed/")
        or item["source_path"].startswith("artifacts/phase-0/normalized/")
    ]
    invariants: list[dict[str, Any]] = []
    for item in protected:
        relative = Path(*Path(item["source_path"]).parts[2:])
        canonical_path = artifacts / relative
        archive_path = archived_artifacts / relative
        if not canonical_path.is_file() or not archive_path.is_file():
            raise MigrationHold(f"protected invariant file is missing: {item['source_path']}")
        canonical_hash = sha256_bytes(canonical_path.read_bytes())
        archive_hash = sha256_bytes(archive_path.read_bytes())
        if canonical_hash != archive_hash or archive_hash != item["sha256"]:
            raise MigrationHold(f"protected invariant differs from archive: {item['source_path']}")
        invariants.append({
            "path": item["source_path"], "archive_path": item["archive_path"],
            "before_sha256": canonical_hash, "after_sha256": canonical_hash,
            "archive_sha256": archive_hash, "unchanged": True,
        })

    old_entries = load_jsonl(archive_manifest)
    new_entries: list[dict[str, Any]] = []
    evidence_schema = load_json(EVIDENCE_SCHEMA)
    for old in old_entries:
        if old.get("schema_version") != "1.1.0":
            raise MigrationHold("archive contains an unexpected evidence schema version")
        migrated = dict(old)
        migrated.update({
            "schema_version": "1.2.0", "request_method": "GET",
            "content_decoding_applied": False,
            "character_decoding_applied": True, "character_codec": "utf-8",
            "field_parsing_applied": True, "field_parsing_succeeded": True,
        })
        validate_schema_instance(migrated, evidence_schema)
        new_entries.append(migrated)
    new_manifest_bytes = b"".join(canonical_json_bytes(entry) + b"\n" for entry in new_entries)
    new_manifest_hash = sha256_bytes(new_manifest_bytes)

    if not canonical_is_old:
        if not output.is_file():
            raise MigrationHold("canonical manifest is neither archived old bytes nor a recorded migrated state")
        migration = load_json(output)
        validate_schema_instance(migration, load_json(MIGRATION_SCHEMA))
        if canonical_old_bytes != new_manifest_bytes:
            raise MigrationHold("canonical migrated manifest bytes differ from deterministic archive reconstruction")
        if migration["archive_manifest_sha256"] != archive_old_hash or migration["canonical_old_manifest_sha256"] != archive_old_hash:
            raise MigrationHold("migration artifact old/archive manifest hashes are inconsistent")
        if migration["canonical_new_manifest_sha256"] != canonical_old_hash or canonical_old_hash != new_manifest_hash:
            raise MigrationHold("migration artifact new manifest hash is inconsistent")
        if migration["old_line_count"] != len(old_entries) or migration["new_line_count"] != len(new_entries):
            raise MigrationHold("migration artifact line counts are inconsistent")
        if migration["invariants"] != invariants:
            raise MigrationHold("migration artifact invariants differ from current/archive protected bytes")
        if migration["status"] != "PASS" or migration["replay"]["status"] != "PASS" or migration["replay"]["network_used"] is not False:
            raise MigrationHold("migration artifact does not record a successful offline replay")
        return migration

    with tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary) / "artifacts" / "phase-0"
        staged.mkdir(parents=True)
        for item in protected:
            relative = Path(*Path(item["source_path"]).parts[2:])
            destination = staged / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifacts / relative, destination)
        (staged / "evidence-manifest.jsonl").write_bytes(new_manifest_bytes)
        clock = clock_check_from_json(load_json(staged / "clock-check-p0-04.json"))
        replay_count = verify_captures(staged, clock)

    for invariant in invariants:
        relative = Path(*Path(invariant["path"]).parts[2:])
        after_hash = sha256_bytes((artifacts / relative).read_bytes())
        if after_hash != invariant["before_sha256"]:
            raise MigrationHold(f"protected invariant changed during staging: {invariant['path']}")
        invariant["after_sha256"] = after_hash

    migration = {
        "schema_version": "1.0.0", "artifact_type": "p0_04_evidence_migration",
        "migration_id": "p0-04-evidence-migration-p0-20260801-b", "archive_id": "p0-04-20260801-a",
        "source_schema_version": "1.1.0", "target_schema_version": "1.2.0",
        "archive_manifest_path": "artifacts/phase-0/batches/p0-04-20260801-a/artifacts/phase-0/evidence-manifest.jsonl",
        "archive_manifest_sha256": archive_old_hash, "canonical_old_manifest_sha256": canonical_old_hash,
        "canonical_new_manifest_sha256": new_manifest_hash,
        "old_line_count": len(old_entries), "new_line_count": len(new_entries),
        "blockers_resolved": ["P0-04-REQUEST-METHOD-MISSING", "P0-04-DECODE-LAYERS-CONFLATED"],
        "invariants": invariants,
        "replay": {"status": "PASS", "verified_entries": replay_count, "network_used": False},
        "status": "PASS",
    }
    validate_schema_instance(migration, load_json(MIGRATION_SCHEMA))
    _atomic_replace(canonical_manifest, new_manifest_bytes)
    _atomic_replace(output, canonical_json_bytes(migration) + b"\n")
    return migration


def _atomic_replace(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(value)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        migration = migrate(args.artifacts, args.archive, args.output)
        print(json.dumps({"status": "PASS", "migration_id": migration["migration_id"], "network_used": False}, separators=(",", ":")))
        return 0
    except (MigrationHold, ValidationError, OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(json.dumps({"status": "HOLD", "error": str(exc), "network_used": False}, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
