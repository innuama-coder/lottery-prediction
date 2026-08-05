import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("batch_migration", ROOT / "scripts/phase0/batch_migration.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class BatchMigrationTests(unittest.TestCase):
    def test_archive_files_match_snapshot(self):
        ok, bad = MODULE.archived_files_ok()
        self.assertTrue(ok, bad)

    def test_frozen_inputs_are_byte_identical(self):
        manifest = MODULE.build_manifest()
        self.assertTrue(all(item["identical"] for item in manifest["inherited_frozen_inputs"].values()))

    def test_decision_surface_is_unchanged(self):
        manifest = MODULE.build_manifest()
        self.assertTrue(manifest["decision_surface"]["identical"])
        self.assertTrue(manifest["migration_gate_pass"])

    def test_prior_observation_is_disclosed(self):
        manifest = MODULE.build_manifest()
        self.assertTrue(manifest["prior_source_observation_disclosed"])
        self.assertGreaterEqual(len(manifest["prior_observations"]), 3)


if __name__ == "__main__":
    unittest.main()
