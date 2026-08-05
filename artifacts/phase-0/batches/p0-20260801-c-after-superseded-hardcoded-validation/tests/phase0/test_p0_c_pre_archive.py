import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase0"))
SPEC = importlib.util.spec_from_file_location("archive_p0_c_pre", ROOT / "scripts/phase0/archive_p0_c_pre.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class P0CPreArchiveTests(unittest.TestCase):
    def test_archive_manifest_sidecar_and_pending_draft_verify(self):
        self.assertFalse((ROOT / MODULE.REPAIR_DRAFT_RELATIVE).exists())
        self.assertTrue((ROOT / "artifacts/phase-0/repair-manifest-p0-20260801-c-review-pending.json").is_file())
        snapshot = MODULE.verify_archive(ROOT, check_current=False)
        self.assertEqual(snapshot["inventory_contract"]["schema_count"], 21)
        self.assertEqual(snapshot["inventory_contract"]["frozen_tool_count"], 9)
        self.assertEqual(snapshot["file_count"], len(snapshot["files"]))

    def test_required_before_surfaces_are_archived(self):
        snapshot = MODULE.verify_archive(ROOT, check_current=False)
        paths = {item["path"] for item in snapshot["files"]}
        for required in MODULE.FIXED_PATHS:
            self.assertIn(required, paths)
        self.assertEqual(len([path for path in paths if path.startswith("artifacts/phase-0/schemas/")]), 21)
        command = json.loads((MODULE.ARCHIVE / "artifacts/phase-0/verification-command.json").read_text(encoding="utf-8"))
        self.assertEqual({item["path"] for item in command["verifier_file_hashes"]}, {path for path in paths if path.startswith("scripts/phase0/")})

    def test_repair_draft_contains_only_captured_before_and_pending_later_states(self):
        snapshot = MODULE.verify_archive(ROOT, check_current=False)
        draft = MODULE.verify_archived_repair_draft(
            ROOT,
            MODULE.historical_repair_draft_path(ROOT),
            pre_manifest_sha256=MODULE.sha256_file(MODULE.SNAPSHOT_MANIFEST),
            pre_snapshot=snapshot,
        )
        self.assertEqual([item["id"] for item in draft["root_causes"]], [f"P0-07-0{number}" for number in range(1, 6)])
        self.assertEqual(draft["before"]["status"], "captured")
        self.assertEqual(draft["after"]["status"], "pending")
        self.assertEqual(draft["tests"]["status"], "pending")
        self.assertEqual(draft["reviewer"]["status"], "pending")
        self.assertIsNone(draft["after"]["snapshot_manifest_sha256"])
        self.assertIsNone(draft["tests"]["exit_code"])
        self.assertIsNone(draft["reviewer"]["conclusion"])

    def test_tampered_archived_file_is_detected(self):
        snapshot = MODULE.verify_archive(ROOT, check_current=False)
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "archive"
            shutil.copytree(MODULE.ARCHIVE, copied)
            target_record = next(item for item in snapshot["files"] if item["size"] > 0)
            target = copied / target_record["path"]
            target.write_bytes(target.read_bytes() + b"tamper")
            with self.assertRaisesRegex(MODULE.ArchiveError, "differs from manifest"):
                MODULE.verify_snapshot_tree(snapshot, copied)

    def test_tampered_archived_repair_draft_is_detected(self):
        snapshot = MODULE.verify_archive(ROOT, check_current=False)
        with tempfile.TemporaryDirectory() as temporary:
            copied_draft = Path(temporary) / "repair-draft.json"
            shutil.copy2(MODULE.historical_repair_draft_path(ROOT), copied_draft)
            copied_draft.write_bytes(copied_draft.read_bytes() + b"tamper")
            with self.assertRaisesRegex(MODULE.ArchiveError, "differs from C-after manifest"):
                MODULE.verify_archived_repair_draft(
                    ROOT,
                    copied_draft,
                    pre_manifest_sha256=MODULE.sha256_file(MODULE.SNAPSHOT_MANIFEST),
                    pre_snapshot=snapshot,
                )


if __name__ == "__main__":
    unittest.main()
