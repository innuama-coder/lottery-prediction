from __future__ import annotations

import concurrent.futures
import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from lottery_system.phase4.checkpoint import CheckpointMismatch, create_checkpoint, load_checkpoint
from lottery_system.phase4.ledger import AppendOnlyLedger, LedgerMismatch, StaleLedgerHead
from lottery_system.phase4.serialization import sha256_file
from lottery_system.phase4.storage import (
    AdvisoryFileLock,
    IdentityReuseError,
    LockUnavailable,
    atomic_replace_bytes,
    atomic_replace_json,
    write_once_bytes,
    write_once_json,
)


PROVENANCE = {
    "producer_actor_id": "p4-implementation-author-i01",
    "task_id": "T02",
    "session_id": "/root/implementation_author",
    "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b",
    "path": "artifacts/phase-4-runtime/unit",
    "role": "implementation_author",
}


def _concurrent_append(arguments: tuple[str, str | None, str]) -> str:
    raw, expected, object_id = arguments
    try:
        AppendOnlyLedger(Path(raw), "shared").append_event(
            object_id=object_id,
            event_type="succeeded",
            event_at_utc="2026-01-01T00:00:00Z",
            payload={"game": object_id},
            producer_provenance=PROVENANCE,
            expected_head_sha256=expected,
        )
        return "PASS"
    except StaleLedgerHead:
        return "STALE"


class StorageLedgerTests(unittest.TestCase):
    def test_write_once_and_atomic_projection_have_distinct_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "nested/value.json"
            write_once_json(path, {"value": 1})
            self.assertEqual(path.read_bytes(), b'{"value":1}')
            with self.assertRaises(IdentityReuseError):
                write_once_json(path, {"value": 2})
            atomic_replace_json(path, {"value": 2})
            self.assertEqual(json.loads(path.read_text()), {"value": 2})

    def test_file_fsync_publish_rename_and_directory_fsync_fault_boundaries(self) -> None:
        for stage, target_exists in (
            ("after_file_fsync", False), ("after_publish", True), ("after_directory_fsync", True),
        ):
            with self.subTest(operation="write_once", stage=stage), tempfile.TemporaryDirectory() as raw:
                target = Path(raw) / "object"

                def fail(observed: str) -> None:
                    if observed == stage:
                        raise RuntimeError(f"injected:{stage}")

                with self.assertRaisesRegex(RuntimeError, "injected"):
                    write_once_bytes(target, b"new-complete", fault=fail)
                self.assertEqual(target.exists(), target_exists)
                if target_exists:
                    self.assertEqual(target.read_bytes(), b"new-complete")
                self.assertEqual(list(Path(raw).glob(".*.tmp-*")), [])

        for stage, expected in (
            ("after_file_fsync", b"old-complete"),
            ("after_rename", b"new-complete"),
            ("after_directory_fsync", b"new-complete"),
        ):
            with self.subTest(operation="replace", stage=stage), tempfile.TemporaryDirectory() as raw:
                target = Path(raw) / "projection"
                target.write_bytes(b"old-complete")

                def fail(observed: str) -> None:
                    if observed == stage:
                        raise RuntimeError(f"injected:{stage}")

                with self.assertRaisesRegex(RuntimeError, "injected"):
                    atomic_replace_bytes(target, b"new-complete", fault=fail)
                self.assertEqual(target.read_bytes(), expected)
                self.assertEqual(list(Path(raw).glob(".*.tmp-*")), [])

    def test_lock_bypass_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            lock_path = Path(raw) / "shared.lock"
            with AdvisoryFileLock(lock_path):
                with self.assertRaises(LockUnavailable):
                    AdvisoryFileLock(lock_path).acquire(blocking=False)

    def test_hash_chain_expected_head_and_current_view_recompute(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = AppendOnlyLedger(Path(raw), "main")
            first = ledger.append_event(
                object_id="ssq-object", event_type="started", event_at_utc="2026-01-01T00:00:00Z",
                payload={"stage": 1}, producer_provenance=PROVENANCE, expected_head_sha256=None,
            )
            with self.assertRaises(StaleLedgerHead):
                ledger.append_event(
                    object_id="dlt-object", event_type="started", event_at_utc="2026-01-01T00:00:01Z",
                    payload={"stage": 1}, producer_provenance=PROVENANCE, expected_head_sha256=None,
                )
            second = ledger.append_event(
                object_id="dlt-object", event_type="started", event_at_utc="2026-01-01T00:00:01Z",
                payload={"stage": 1}, producer_provenance=PROVENANCE,
                expected_head_sha256=first["event_sha256"],
            )
            self.assertEqual(ledger.validate()["event_count"], 2)
            self.assertEqual(ledger.read_head().event_sha256, second["event_sha256"])
            view = json.loads(ledger.current_view_path.read_text())
            self.assertEqual(set(view["objects"]), {"ssq-object", "dlt-object"})

    def test_advisory_lock_serializes_same_expected_head_without_lost_update(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            AppendOnlyLedger(runtime, "shared").validate()
            context = multiprocessing.get_context("spawn")
            with concurrent.futures.ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
                results = list(pool.map(_concurrent_append, [(raw, None, "ssq"), (raw, None, "dlt")]))
            self.assertEqual(sorted(results), ["PASS", "STALE"])
            self.assertEqual(AppendOnlyLedger(runtime, "shared").validate()["event_count"], 1)

    def test_fault_recovery_resolves_to_old_or_new_complete_head(self) -> None:
        for stage, expected_count in (
            ("after_payload", 0), ("after_journal", 0), ("after_event", 1),
            ("after_head", 1), ("after_view", 1), ("after_commit", 1),
        ):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as raw:
                ledger = AppendOnlyLedger(Path(raw), "faults")

                def fail(observed: str) -> None:
                    if observed == stage:
                        raise RuntimeError(f"injected:{stage}")

                with self.assertRaisesRegex(RuntimeError, "injected"):
                    ledger.append_event(
                        object_id="object", event_type="started", event_at_utc="2026-01-01T00:00:00Z",
                        payload={"stage": stage}, producer_provenance=PROVENANCE,
                        expected_head_sha256=None, fault=fail,
                    )
                self.assertEqual(AppendOnlyLedger(Path(raw), "faults").validate()["event_count"], expected_count)

    def test_event_or_payload_tamper_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = AppendOnlyLedger(Path(raw), "tamper")
            result = ledger.append_event(
                object_id="object", event_type="started", event_at_utc="2026-01-01T00:00:00Z",
                payload={"value": 1}, producer_provenance=PROVENANCE, expected_head_sha256=None,
            )
            event = Path(raw) / result["head"]["event_path"]
            event.write_bytes(event.read_bytes() + b" ")
            with self.assertRaises(LedgerMismatch):
                ledger.validate()

    def test_event_filename_is_bound_to_ordinal_and_event_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = AppendOnlyLedger(Path(raw), "filename")
            result = ledger.append_event(
                object_id="object", event_type="started", event_at_utc="2026-01-01T00:00:00Z",
                payload={"value": 1}, producer_provenance=PROVENANCE, expected_head_sha256=None,
            )
            original = Path(raw) / result["head"]["event_path"]
            renamed = original.with_name(f"000000000001-{'0' * 64}.json")
            original.rename(renamed)
            head = result["head"] | {"event_path": renamed.relative_to(Path(raw)).as_posix()}
            atomic_replace_json(ledger.head_path, head)
            with self.assertRaises(LedgerMismatch):
                ledger.validate()

    def test_checkpoint_binds_identity_plan_head_and_file_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            evidence = runtime / "evidence.bin"
            evidence.write_bytes(b"evidence")
            digest = sha256_file(evidence)
            plan = ["ssq", "2026001", "predict_lock", "2026-01-01T09:00:00Z", "schedule-v1"]
            create_checkpoint(
                runtime, checkpoint_id="checkpoint-01", run_id="run-01", plan_key=plan,
                ledger_head_sha256="1" * 64, input_hashes=[digest], output_hashes=[],
                stage="generated", next_ordinal=2, rng_counter=7, created_at_utc="2026-01-01T00:00:00Z",
            )
            loaded = load_checkpoint(
                runtime, "checkpoint-01", expected_run_id="run-01", expected_plan_key=plan,
                expected_ledger_head_sha256="1" * 64, verify_paths=[evidence],
            )
            self.assertEqual(loaded["rng_counter"], 7)
            with self.assertRaises(CheckpointMismatch):
                load_checkpoint(runtime, "checkpoint-01", expected_run_id="wrong")
            with self.assertRaises(CheckpointMismatch):
                load_checkpoint(runtime, "checkpoint-01", expected_ledger_head_sha256="2" * 64)


if __name__ == "__main__":
    unittest.main()
