from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3.ledger import AppendOnlyLedger, CheckpointStore
from lottery_research.phase3.registry import load_and_validate_registries


ROOT = Path(__file__).resolve().parents[2]


class LedgerTests(unittest.TestCase):
    def test_terminal_state_is_append_only_and_cannot_be_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = AppendOnlyLedger(Path(raw) / "events.jsonl", "qualification-i01")
            ledger.start("experiment-1", {"model_id": "M1"})
            ledger.finish("experiment-1", "failed", {"reason": "injected"})
            with self.assertRaises(ValueError):
                ledger.finish("experiment-1", "succeeded", {})
            with self.assertRaises(FileExistsError):
                AppendOnlyLedger(Path(raw) / "events.jsonl", "qualification-i01")
            rows = [json.loads(line) for line in (Path(raw) / "events.jsonl").read_text().splitlines()]
            self.assertEqual([row["state"] for row in rows], ["started", "failed"])

    def test_checkpoint_resume_requires_same_identity_and_payload_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "checkpoint.json"
            store = CheckpointStore(path, "run-a")
            store.write_new({"completed": [1, 2]})
            self.assertEqual(store.load()["payload"], {"completed": [1, 2]})
            with self.assertRaises(FileExistsError):
                store.write_new({"completed": [1, 2, 3]})
            with self.assertRaises(ValueError):
                CheckpointStore(path, "run-b").load()


class RegistryTests(unittest.TestCase):
    def test_registry_has_explicit_model_opening_decisions(self) -> None:
        model_registry, feature_registry = load_and_validate_registries(ROOT)
        self.assertEqual(model_registry["models"]["M0"]["role"], "permanent_champion")
        self.assertEqual(model_registry["models"]["M1"]["role"], "mandatory_challenger")
        self.assertTrue(all(model_registry["models"][key]["opening_decision"] == "not_opened" for key in ("M2", "M3", "M4")))
        self.assertEqual(feature_registry["features"]["prior_draw_result"]["status"], "blocked_pending_pit_evidence")


if __name__ == "__main__":
    unittest.main()
