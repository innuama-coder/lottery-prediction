from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/phase4_independent/{name}.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class PowerToolTests(unittest.TestCase):
    def test_seed_domains_are_separate_and_deterministic(self):
        module = load("confirm_power")
        a = module.seed("design-v1:" + "a" * 64, "power-confirmation", "ssq", "uniform", 1)
        self.assertEqual(a, module.seed("design-v1:" + "a" * 64, "power-confirmation", "ssq", "uniform", 1))
        self.assertNotEqual(a, module.seed("design-v1:" + "a" * 64, "development", "ssq", "uniform", 1))

    def test_aggregate_interval_direction_and_thresholds(self):
        module = load("reduce_power")
        cells = []
        for game in ("dlt", "ssq"):
            cells.append({"game":game,"world":"uniform","sequence_count":20000,"sequence_rate_estimate":"0.01","sequence_rate_simultaneous_interval":["0.005","0.02"]})
            for world in ("static_bias","slow_drift","useful_feature"):
                cells.append({"game":game,"world":world,"sequence_count":20000,"sequence_rate_estimate":"0.98","sequence_rate_simultaneous_interval":["0.96","0.99"]})
        result = module.reduce({"artifact_type":"phase4_power_confirmation_summary","design_id":"d","cells":cells},1000,50,900)
        self.assertEqual(result["status"], "PASS")
        for row in result["cells"]:
            lo, hi = map(Decimal, row["formal_1000_gate_pass_probability_interval"])
            self.assertLessEqual(lo, hi)

    def test_exact_plan_selector_resolves_scientific_controller(self):
        module = load("confirm_power")
        command, binding = module.load_scientific_command(ROOT / "config/phase4/power-controller-command.json")
        self.assertEqual(command["artifact_type"], "phase4_scientific_controller_command")
        self.assertEqual(command["protocol"], "phase4_scientific_single_sequence_json_v1")
        self.assertEqual(len(binding["command_selector_sha256"]), 64)
        self.assertEqual(len(binding["scientific_command_sha256"]), 64)

    def test_parallel_cells_preserve_canonical_order_and_hashes(self):
        module = load("confirm_power")
        identity = {"controller_identity_id": "scientific-controller-v1:" + "1" * 64}
        design = {"design_id": "qualification-design-v1:" + "2" * 64}

        class FakeInvoker:
            def __init__(self, argv):
                self.argv = argv

            def invoke(self, request):
                event = request["world"] != "uniform"
                return {"sequence_terminal": {
                    "request_id": request["request_id"],
                    "sequence_event": event,
                }}

            def close(self):
                return None

        original = module.StreamInvoker
        module.StreamInvoker = FakeInvoker
        try:
            with tempfile.TemporaryDirectory() as serial_dir, tempfile.TemporaryDirectory() as parallel_dir:
                serial = module.collect_cells(
                    design=design, identity=identity, argv=["fake"], seed_domain="power-confirmation",
                    sequences_per_cell=3, output=Path(serial_dir), cell_workers=1,
                )
                parallel = module.collect_cells(
                    design=design, identity=identity, argv=["fake"], seed_domain="power-confirmation",
                    sequences_per_cell=3, output=Path(parallel_dir), cell_workers=8,
                )
                self.assertEqual(serial, parallel)
                expected = [(game, world) for game in module.GAMES for world in module.WORLDS]
                self.assertEqual(expected, [(cell["game"], cell["world"]) for cell in parallel[0]])
                for cell in parallel[0]:
                    path = Path(parallel_dir) / cell["terminals_path"]
                    terminals = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual([1, 2, 3], [int(row["request_id"].rsplit("-", 1)[1]) for row in terminals])
        finally:
            module.StreamInvoker = original

    def test_cell_worker_failure_is_fail_closed(self):
        module = load("confirm_power")

        class FailingInvoker:
            def __init__(self, argv):
                pass

            def invoke(self, request):
                if request["game"] == "dlt" and request["world"] == "uniform":
                    raise RuntimeError("injected worker failure")
                return {"sequence_terminal": {"sequence_event": "proposal"}}

            def close(self):
                return None

        original = module.StreamInvoker
        module.StreamInvoker = FailingInvoker
        try:
            with tempfile.TemporaryDirectory() as output:
                with self.assertRaisesRegex(RuntimeError, "injected worker failure"):
                    module.collect_cells(
                        design={"design_id": "d"}, identity={"controller_identity_id": "i"}, argv=["fake"],
                        seed_domain="power-confirmation", sequences_per_cell=1,
                        output=Path(output), cell_workers=8,
                    )
                self.assertFalse((Path(output) / "raw-control.json").exists())
                self.assertFalse((Path(output) / "summary.json").exists())
        finally:
            module.StreamInvoker = original


if __name__ == "__main__": unittest.main()
