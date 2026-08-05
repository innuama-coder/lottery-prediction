from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
sys.path.insert(0, str(SCRIPTS))

from migrate_p0_04_evidence_v12 import MigrationHold, migrate  # noqa: E402
from phase0lib import load_json, load_jsonl, validate_schema_instance  # noqa: E402


ARCHIVE = REPO / "artifacts/phase-0/batches/p0-04-20260801-a"
ARCHIVED_ARTIFACTS = ARCHIVE / "artifacts/phase-0"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class P004EvidenceMigrationTests(unittest.TestCase):
    def test_archive_migration_preserves_derived_bytes_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = Path(temp) / "artifacts/phase-0"
            shutil.copytree(ARCHIVED_ARTIFACTS, artifacts)
            protected = [
                artifacts / "clock-check-p0-04.json", artifacts / "environment-lock.json",
                *list((artifacts / "raw").glob("*")), *list((artifacts / "parsed").glob("*")),
                *list((artifacts / "normalized").glob("*")),
            ]
            before = {path.relative_to(artifacts).as_posix(): file_hash(path) for path in protected}
            output = artifacts / "p0-04-evidence-migration-p0-20260801-b.json"
            migration = migrate(artifacts, ARCHIVE, output)
            self.assertEqual(migration["status"], "PASS")
            self.assertEqual(migration["replay"]["status"], "PASS")
            self.assertEqual(before, {path.relative_to(artifacts).as_posix(): file_hash(path) for path in protected})
            entries = load_jsonl(artifacts / "evidence-manifest.jsonl")
            self.assertTrue(all(entry["schema_version"] == "1.2.0" for entry in entries))
            self.assertTrue(all(entry["request_method"] == "GET" for entry in entries))
            self.assertTrue(all(not entry["content_decoding_applied"] for entry in entries))
            self.assertTrue(all(entry["character_codec"] == "utf-8" for entry in entries))
            validate_schema_instance(
                load_json(output),
                load_json(REPO / "artifacts/phase-0/schemas/p0-04-evidence-migration.schema.json"),
            )
            manifest_path = artifacts / "evidence-manifest.jsonl"
            manifest_bytes = manifest_path.read_bytes()
            migration_bytes = output.read_bytes()
            manifest_mtime = manifest_path.stat().st_mtime_ns
            migration_mtime = output.stat().st_mtime_ns
            replayed = migrate(artifacts, ARCHIVE, output)
            self.assertEqual(replayed, migration)
            self.assertEqual(manifest_path.read_bytes(), manifest_bytes)
            self.assertEqual(output.read_bytes(), migration_bytes)
            self.assertEqual(manifest_path.stat().st_mtime_ns, manifest_mtime)
            self.assertEqual(output.stat().st_mtime_ns, migration_mtime)

    def test_tampered_migrated_manifest_holds_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = Path(temp) / "artifacts/phase-0"
            shutil.copytree(ARCHIVED_ARTIFACTS, artifacts)
            output = artifacts / "p0-04-evidence-migration-p0-20260801-b.json"
            migrate(artifacts, ARCHIVE, output)
            manifest = artifacts / "evidence-manifest.jsonl"
            manifest.write_bytes(manifest.read_bytes() + b"\n")
            tampered = manifest.read_bytes()
            artifact_bytes = output.read_bytes()
            with self.assertRaisesRegex(MigrationHold, "differ from deterministic archive reconstruction"):
                migrate(artifacts, ARCHIVE, output)
            self.assertEqual(manifest.read_bytes(), tampered)
            self.assertEqual(output.read_bytes(), artifact_bytes)

    def test_old_hash_mismatch_holds_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = Path(temp) / "artifacts/phase-0"
            shutil.copytree(ARCHIVED_ARTIFACTS, artifacts)
            manifest = artifacts / "evidence-manifest.jsonl"
            manifest.write_bytes(manifest.read_bytes() + b"\n")
            before = manifest.read_bytes()
            output = artifacts / "p0-04-evidence-migration-p0-20260801-b.json"
            with self.assertRaisesRegex(MigrationHold, "neither archived old bytes nor a recorded migrated state"):
                migrate(artifacts, ARCHIVE, output)
            self.assertEqual(manifest.read_bytes(), before)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
