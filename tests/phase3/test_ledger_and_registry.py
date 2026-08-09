from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3.ledger import AppendOnlyLedger, CheckpointStore, canonical_attempts
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

    def test_retry_attempts_are_preserved_and_one_canonical_success_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "events.jsonl"
            ledger = AppendOnlyLedger(path, "formal-i01")
            ledger.start("experiment-1", {}, attempt_id="attempt-01")
            ledger.finish("experiment-1", "failed", {}, attempt_id="attempt-01")
            ledger.start("experiment-1", {}, attempt_id="attempt-02", parent_attempt_id="attempt-01")
            ledger.finish("experiment-1", "succeeded", {}, attempt_id="attempt-02")
            ledger.close()
            self.assertEqual(canonical_attempts(path), {"experiment-1": "attempt-02"})


class RegistryTests(unittest.TestCase):
    def test_registry_has_explicit_model_opening_decisions(self) -> None:
        model_registry, feature_registry = load_and_validate_registries(ROOT)
        self.assertEqual(model_registry["models"]["M0"]["role"], "permanent_champion")
        self.assertEqual(model_registry["models"]["M1"]["role"], "mandatory_challenger")
        self.assertTrue(all(model_registry["models"][key]["opening_decision"] == "not_opened" for key in ("M2", "M3", "M4")))
        self.assertEqual(feature_registry["features"]["prior_draw_result"]["status"], "eligible")
        self.assertEqual(feature_registry["features"]["prior_draw_result"]["availability_proof"]["mode"], "retrospective_sequence_safe")


if __name__ == "__main__":
    unittest.main()
