"""Frozen Phase 1 workflow E2E contract entry point."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


E2E_MODULE_COUNTS = {
    "test_high_level_workflow_unit": 10,
    "test_bootstrap_verify_profiles": 6,
    "test_snapshot_delta_e2e": 4,
    "test_e2e_fallback_case": 1,
    "test_e2e_replay_case": 2,
    "test_e2e_fault_case": 6,
}
DEFERRED_FIXED_MODULES: dict[str, str] = {}
EXPECTED_SELECTED_TESTS = sum(E2E_MODULE_COUNTS.values())
EXPECTED_ENTRY_TESTS = EXPECTED_SELECTED_TESTS + 1


def _selected_suite(loader: unittest.TestLoader) -> tuple[unittest.TestSuite, dict[str, int]]:
    suite = unittest.TestSuite()
    actual_counts: dict[str, int] = {}
    for module_name, expected_count in E2E_MODULE_COUNTS.items():
        module = importlib.import_module(module_name)
        module_path = Path(module.__file__).resolve()
        if module_path.parent != HERE:
            raise AssertionError(f"contract module resolved outside tests/phase1: {module_name} -> {module_path}")
        module_suite = loader.loadTestsFromModule(module)
        actual_count = module_suite.countTestCases()
        if actual_count != expected_count:
            raise AssertionError(
                f"contract count changed for {module_name}: expected={expected_count}, actual={actual_count}"
            )
        actual_counts[module_name] = actual_count
        suite.addTests(module_suite)
    return suite, actual_counts


def _test_ids(suite: unittest.TestSuite) -> list[str]:
    values: list[str] = []
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            values.extend(_test_ids(test))
        else:
            values.append(test.id())
    return values


class WorkflowE2EEntryContractTests(unittest.TestCase):
    def test_explicit_e2e_suite_identity_count_and_uniqueness(self) -> None:
        selected, counts = _selected_suite(unittest.TestLoader())
        ids = _test_ids(selected)
        self.assertEqual(counts, E2E_MODULE_COUNTS)
        self.assertEqual(len(ids), EXPECTED_SELECTED_TESTS)
        self.assertEqual(len(ids), len(set(ids)), "contract suite contains duplicate test IDs")
        self.assertEqual(
            {test_id.split(".", 1)[0] for test_id in ids},
            set(E2E_MODULE_COUNTS),
        )
        self.assertEqual(DEFERRED_FIXED_MODULES, {})
        self.assertTrue(set(DEFERRED_FIXED_MODULES.values()).isdisjoint(E2E_MODULE_COUNTS))


def load_tests(
    loader: unittest.TestLoader,
    standard_tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    del standard_tests, pattern
    selected, _ = _selected_suite(loader)
    ids = _test_ids(selected)
    if len(ids) != EXPECTED_SELECTED_TESTS or len(ids) != len(set(ids)):
        raise AssertionError("E2E workflow contract suite count/uniqueness check failed")
    result = unittest.TestSuite()
    result.addTests(loader.loadTestsFromTestCase(WorkflowE2EEntryContractTests))
    result.addTests(selected)
    if result.countTestCases() != EXPECTED_ENTRY_TESTS:
        raise AssertionError("E2E workflow entry test count changed")
    return result


if __name__ == "__main__":
    unittest.main(verbosity=2)
