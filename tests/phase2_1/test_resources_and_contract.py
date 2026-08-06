from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lottery_research.phase2_1 import BASELINE_SHA, RELEASE_ID, RUN_LABEL
from lottery_research.phase2_1.resources import resource_facts, wheelhouse_facts
from lottery_research.phase2_1.schema import validate
from lottery_research.phase2_1.workflow import project_root, source_manifest


ROOT = project_root()


class ResourceAndContractTests(unittest.TestCase):
    def test_release_identity_is_baseline_derived(self) -> None:
        self.assertEqual(RELEASE_ID, f"{RUN_LABEL}-{BASELINE_SHA[:12]}")

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


if __name__ == "__main__":
    unittest.main()
