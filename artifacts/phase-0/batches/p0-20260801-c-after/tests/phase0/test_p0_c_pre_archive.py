import base64
import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase0"))
SPEC = importlib.util.spec_from_file_location("archive_p0_c_pre", ROOT / "scripts/phase0/archive_p0_c_pre.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
import validate_p0_c_freeze as VALIDATION


class P0CPreArchiveTests(unittest.TestCase):
    def _scheduler_fixture(self):
        plan = json.loads((ROOT / "artifacts/phase-0/p0-06-runtime-plan.json").read_text(encoding="utf-8"))
        verification = json.loads((ROOT / "artifacts/phase-0/verification-command.json").read_text(encoding="utf-8"))
        runner = str((ROOT / "scripts/phase0/p0_06_runner.py").resolve())
        artifacts = str((ROOT / "artifacts/phase-0").resolve())
        plan_sha = (ROOT / "artifacts/phase-0/p0-06-runtime-plan.json.sha256").read_text(encoding="ascii").strip()
        return {
            "powershell": {"executable": VALIDATION.POWERSHELL, "version": "5.1.26100.7462", "edition": "Desktop"},
            "task_name": VALIDATION.TASK_NAME,
            "state": "Ready",
            "trigger_count": 24,
            "actions": [{
                "execute": str(Path(verification["interpreter_path"]).resolve()),
                "arguments": f'"{runner}" --action execute-due --artifacts "{artifacts}" --allow-network --expected-plan-sha256 {plan_sha}',
                "working_directory": str(ROOT.resolve()),
            }],
            "triggers": [{"start_boundary": item["local_at"], "enabled": True} for item in plan["scheduler"]["triggers"]],
            "settings": {"start_when_available": True, "execution_time_limit": "PT15M", "execution_time_limit_minutes": 15, "multiple_instances": "IgnoreNew"},
            "principal": {"user_id": "fixture", "resolved_sid": "S-1-fixture", "current_sid": "S-1-fixture", "logon_type": "Interactive", "run_level": "Limited"},
            "next_run_local": "2026-08-02T22:00:00+08:00",
            "last_run_local": "1999-11-30T00:00:00+08:00",
            "last_task_result": 267011,
            "missed_runs": 0,
        }

    def _fake_validation_run(self, argv, **_kwargs):
        command = tuple(argv)
        if command == VALIDATION.SCHEDULER_COMMAND:
            stdout = VALIDATION._canonical_bytes(self._scheduler_fixture())
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")
        if command == VALIDATION.UNITTEST_COMMAND:
            stderr = b"----------------------------------------------------------------------\nRan 160 tests in 1.234s\n\nOK\n"
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=stderr)
        if command == VALIDATION.REPLAY_COMMAND:
            return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=VALIDATION.REPLAY_HOLD_BYTES)
        if "--stage" in command:
            stage = command[command.index("--stage") + 1]
            stdout = VALIDATION._canonical_bytes({"status": "PASS", "stage": stage, "contract_version": "1.3", "network_used": False})
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")
        raise AssertionError(f"unexpected validation subprocess: {argv}")

    def test_archive_manifest_sidecar_and_pending_draft_verify(self):
        self.assertFalse((ROOT / MODULE.REPAIR_DRAFT_RELATIVE).exists())
        # The suite runs both immediately before C-after creation (no root
        # repair manifest) and after creation (one review-pending manifest).
        # Never accept a draft, an unexpected name, or more than one root
        # repair manifest.
        root_repairs = sorted((ROOT / "artifacts/phase-0").glob("repair-manifest*.json"))
        self.assertLessEqual(len(root_repairs), 1)
        if root_repairs:
            self.assertEqual(root_repairs[0].name, "repair-manifest-p0-20260801-c-review-pending.json")
            self.assertTrue(root_repairs[0].with_suffix(root_repairs[0].suffix + ".sha256").is_file())
        snapshot = MODULE.verify_archive(ROOT, check_current=False)
        self.assertEqual(snapshot["inventory_contract"]["schema_count"], 21)
        self.assertEqual(snapshot["inventory_contract"]["frozen_tool_count"], 9)
        self.assertEqual(snapshot["file_count"], len(snapshot["files"]))

        # Keep these governance failure injections inside an existing test case
        # so the frozen full-suite observation remains exactly Ran 160.
        with patch.object(VALIDATION, "_utc_now", return_value="2026-08-01T12:00:00.000000Z"):
            valid = VALIDATION.collect_validation(run_fn=self._fake_validation_run)
        VALIDATION.validate_record(valid)
        cases = {}

        hardcoded = copy.deepcopy(valid)
        hardcoded["processes"][1]["parsed_observation"]["status"] = "FAIL"
        cases["hardcoded parsed gate result"] = hardcoded

        wrong_count = copy.deepcopy(valid)
        wrong_count["full_unittest"]["observed_test_count"] = 159
        cases["wrong unittest count"] = wrong_count

        stale_tree = copy.deepcopy(valid)
        stale_tree["operational_tree"]["before"]["root_sha256"] = "0" * 64
        cases["stale operational tree hash"] = stale_tree

        tampered_stdout = copy.deepcopy(valid)
        stream = tampered_stdout["processes"][1]["stdout"]
        changed = b'{"network_used":false,"stage":"p0-01","status":"FAIL"}\n'
        stream.update(bytes_base64=base64.b64encode(changed).decode("ascii"), size=len(changed), sha256=hashlib.sha256(changed).hexdigest())
        cases["tampered exact stdout with recomputed stream hash"] = tampered_stdout

        extra_unittest_text = copy.deepcopy(valid)
        unit = next(item for item in extra_unittest_text["processes"] if item["role"] == "full_unittest_discover")
        changed_summary = base64.b64decode(unit["stderr"]["bytes_base64"]) + b"untrusted trailing text\n"
        unit["stderr"].update(bytes_base64=base64.b64encode(changed_summary).decode("ascii"), size=len(changed_summary), sha256=hashlib.sha256(changed_summary).hexdigest())
        cases["unittest summary with extra text"] = extra_unittest_text

        for label, candidate in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(VALIDATION.FreezeValidationError):
                    VALIDATION.validate_record(candidate)

        resealed = copy.deepcopy(valid)
        resealed["status"] = "FAIL"
        with tempfile.TemporaryDirectory() as temporary:
            record_path = Path(temporary) / "record.json"
            sidecar_path = Path(temporary) / "record.json.sha256"
            payload = VALIDATION._canonical_bytes(resealed)
            record_path.write_bytes(payload)
            sidecar_path.write_text(hashlib.sha256(payload).hexdigest() + "\n", encoding="ascii")
            with self.assertRaisesRegex(VALIDATION.FreezeValidationError, "identity/status"):
                VALIDATION.load_and_validate(record_path, sidecar_path)

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
