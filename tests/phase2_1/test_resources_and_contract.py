from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lottery_research.phase2_1 import BASELINE_SHA, ITERATION, RELEASE_ID, RUN_LABEL
from lottery_research.phase2_1.resources import resource_facts, wheelhouse_facts
from lottery_research.phase2_1.schema import validate
from lottery_research.phase2_1.workflow import SOURCE_PATHS, project_root, source_manifest


ROOT = project_root()


class ResourceAndContractTests(unittest.TestCase):
    def test_release_and_iteration_baseline_identities_are_explicit(self) -> None:
        self.assertEqual(RELEASE_ID, "P2.1-R00-60d02be4dbe9-i06")
        self.assertEqual(BASELINE_SHA, "5e1aa705c2e0b9f33fb3ef2698e8af55301919dd")
        self.assertEqual((RUN_LABEL, ITERATION), ("P2.1-R00", "i06"))

    def test_contract_has_no_generic_resource_thresholds(self) -> None:
        contract = json.loads((ROOT / "docs/roadmap/phase-2.1-acceptance-contract.json").read_text(encoding="utf-8"))
        validate("contract", contract)
        self.assertEqual(contract["resource_policy"]["generic_thresholds"], [])
        self.assertEqual(contract["resource_policy"]["preflight_mode"], "facts_only")
        self.assertEqual(contract["resource_policy"]["capacity_owner"], "task_initiator")

    def test_low_resource_values_are_recorded_not_rejected(self) -> None:
        with patch("lottery_research.phase2_1.resources.os.cpu_count", return_value=1), patch(
            "lottery_research.phase2_1.resources._memory_bytes", return_value=1
        ), patch("lottery_research.phase2_1.resources.shutil.disk_usage") as usage:
            usage.return_value = type("Usage", (), {"free": 0})()
            facts = resource_facts(ROOT)
        self.assertEqual(facts["logical_cpu_count"], 1)
        self.assertEqual(facts["total_memory_bytes"], 1)
        self.assertEqual(facts["available_disk_bytes"], 0)
        self.assertEqual(facts["policy"], "facts_only_no_generic_capacity_or_architecture_thresholds")

    def test_missing_wheel_is_a_real_operation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                wheelhouse_facts(ROOT / "requirements/phase2_1.lock", Path(raw))

    def test_source_manifest_is_closed_and_sorted(self) -> None:
        manifest = source_manifest(ROOT)
        paths = [row["path"] for row in manifest["files"]]
        self.assertEqual(paths, sorted(paths))
        self.assertGreater(manifest["file_count"], 10)
        self.assertRegex(manifest["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(any(path.startswith("src/lottery_research/phase2/") for path in paths))

    def test_source_manifest_rejects_missing_phase2_runtime(self) -> None:
        from lottery_research.phase2_1.workflow import _verify_source

        manifest = source_manifest(ROOT)
        manifest["files"] = [row for row in manifest["files"] if not row["path"].startswith("src/lottery_research/phase2/")]
        manifest["file_count"] = len(manifest["files"])
        import hashlib
        from lottery_research.phase2_1.serialization import canonical_json_bytes
        manifest["sha256"] = hashlib.sha256(canonical_json_bytes(manifest["files"])).hexdigest()
        with self.assertRaisesRegex(ValueError, "complete registered runtime closure"):
            _verify_source(ROOT, manifest)

    def test_source_manifest_rejects_modified_phase2_runtime_file(self) -> None:
        from lottery_research.phase2_1.workflow import _verify_source

        with tempfile.TemporaryDirectory() as raw:
            clone = Path(raw)
            for relative in SOURCE_PATHS:
                source = ROOT / relative
                target = clone / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copyfile(source, target)
            manifest = source_manifest(clone)
            victim = next((clone / "src/lottery_research/phase2").glob("*.py"))
            victim.write_bytes(victim.read_bytes() + b"\n# isolated tamper\n")
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                _verify_source(clone, manifest)


if __name__ == "__main__":
    unittest.main()
