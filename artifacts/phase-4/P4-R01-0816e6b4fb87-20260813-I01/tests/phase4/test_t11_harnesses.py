from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


BOTTOM_UP = load_module("phase4_t11_validate_bottom_up", ROOT / "scripts/phase4/validate_bottom_up.py")
BENCHMARK = load_module("phase4_t11_benchmark", ROOT / "scripts/phase4/benchmark_prequalification.py")


class T11HarnessTests(unittest.TestCase):
    def test_registry_guard_map_bidirectional_and_fail_closed_mutations(self) -> None:
        registry = json.loads((ROOT / "config/phase4/e2e-registry.json").read_text())
        guard_map = json.loads((ROOT / "tests/phase4/fixtures/e2e/guard-map.json").read_text())
        rows = BOTTOM_UP.validate_registry(registry, guard_map)
        self.assertEqual((len(rows), sum(row["polarity"] == "positive" for row in rows), sum(row["polarity"] == "negative" for row in rows)), (53, 10, 43))
        mutations = []
        changed = copy.deepcopy(guard_map); changed["cases"].pop(); mutations.append(changed)
        changed = copy.deepcopy(guard_map); changed["cases"][10]["guard_code"] = "PASS"; mutations.append(changed)
        changed = copy.deepcopy(guard_map); changed["cases"][10]["guard_exit_code"] = 0; mutations.append(changed)
        changed = copy.deepcopy(guard_map); changed["cases"][0]["selectors"] = []; mutations.append(changed)
        changed_registry = copy.deepcopy(registry); changed_registry["unrelated_exception_counts_as_pass"] = True
        with self.assertRaises(BOTTOM_UP.HarnessViolation):
            BOTTOM_UP.validate_registry(changed_registry, guard_map)
        for changed in mutations:
            with self.assertRaises(BOTTOM_UP.HarnessViolation):
                BOTTOM_UP.validate_registry(registry, changed)

    def test_a01_a21_readiness_map_has_exact_ids_and_registered_cases(self) -> None:
        assertions = json.loads((ROOT / "config/phase4/acceptance-assertions.json").read_text())
        readiness = json.loads((ROOT / "tests/phase4/fixtures/e2e/acceptance-readiness-map.json").read_text())
        registry = json.loads((ROOT / "config/phase4/e2e-registry.json").read_text())
        expected = {row["acceptance_id"] for row in assertions["assertions"]}
        rows = readiness["entries"]
        self.assertEqual(len(rows), 21)
        self.assertEqual({row["acceptance_id"] for row in rows}, expected)
        cases = set(registry["positive_cases"] + registry["negative_cases"])
        for row in rows:
            self.assertTrue(row["e2e_cases"])
            self.assertTrue(set(row["e2e_cases"]).issubset(cases))
            self.assertTrue(row["remaining_closure_tasks"])
        self.assertIn("cannot be inferred PASS", readiness["readiness_semantics"])

    def test_benchmark_fixture_identity_command_and_one_black_box_sample(self) -> None:
        registry_path = ROOT / "tests/phase4/fixtures/benchmark/registry.json"
        command_path = ROOT / "tests/phase4/fixtures/benchmark/controller-command.json"
        registry, command, supplied = BENCHMARK.validate_inputs(registry_path, command_path)
        self.assertTrue(registry["non_scientific"])
        self.assertIsNone(registry["qualification_seed_domain"])
        worker_source = (ROOT / registry["controller_source"]["path"]).read_text()
        self.assertIn("from lottery_system.phase4.research.sequential import reduce_e_process", worker_source)
        self.assertIn("reduced = reduce_e_process(looks, alpha_ordinal=1)", worker_source)
        with tempfile.TemporaryDirectory(dir=ROOT / "artifacts/phase-4-runtime", prefix="t11-benchmark-unit-") as raw:
            sample = BENCHMARK.execute_sample(
                registry=registry, command=command, supplied=supplied, cycles=150,
                root=Path(raw), run_id="unit-sample",
            )
            self.assertEqual((sample["sequence_count"], sample["observation_count"]), (20, 3000))
            self.assertEqual(len(sample["manifest_sha256"]), 64)
            self.assertEqual(len(sample["evidence_return_sha256"]), 64)
        changed = copy.deepcopy(registry)
        changed["qualification_seed_domain"] = "development"
        with tempfile.TemporaryDirectory() as raw:
            changed_path = Path(raw) / "registry.json"
            changed_path.write_text(json.dumps(changed))
            with self.assertRaises(BENCHMARK.BenchmarkViolation):
                BENCHMARK.validate_inputs(changed_path, command_path)


if __name__ == "__main__":
    unittest.main()
