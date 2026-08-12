from __future__ import annotations

import contextlib
import concurrent.futures
import io
import json
import os
import threading
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from lottery_system.phase4.cli_kernel import ContractEvidenceMismatch, build_parser, main, producer_provenance
from lottery_system.phase4.data_chain import (
    DataChainMismatch,
    StaleDataChainHead,
    append_data_release,
    create_genesis,
    current_data_release,
    proposed_data_release_id,
)
from lottery_system.phase4.storage import resolve_inside
from lottery_system.phase4.ledger import AppendOnlyLedger
from lottery_system.phase4.serialization import load_json


ROOT = Path(__file__).resolve().parents[2]
GENESIS = ROOT / "config/phase4/genesis.json"
ACTOR_ASSIGNMENTS = "artifacts/phase-4-prep/p4-prep-controller-issued-i01/control/actor-assignments-preparation.json"
PROVENANCE = {
    "producer_actor_id": "p4-implementation-author-i01", "task_id": "T02",
    "session_id": "/root/implementation_author", "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b",
    "path": "artifacts/phase-4-runtime/unit", "role": "implementation_author",
}


class DataChainTests(unittest.TestCase):
    def test_genesis_recomputes_phase1_content_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            first = create_genesis(ROOT, runtime, GENESIS, clock="2026-01-01T00:00:00Z", producer_provenance=PROVENANCE)
            second = create_genesis(ROOT, runtime, GENESIS, clock="2026-01-01T00:00:00Z", producer_provenance=PROVENANCE)
            self.assertFalse(first["idempotent_resume"])
            self.assertTrue(second["idempotent_resume"])
            release_id = first["release"]["data_release_id"]
            self.assertTrue(current_data_release(runtime, release_id)["is_current"])
            release_root = resolve_inside(runtime, f"data-releases/{release_id}")
            self.assertEqual((release_root / "baseline/draws.jsonl").stat().st_size, (ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl").stat().st_size)

    def test_three_release_chain_is_direct_and_cannot_be_spliced(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            genesis = create_genesis(ROOT, runtime, GENESIS, clock="2026-01-01T00:00:00Z", producer_provenance=PROVENANCE)["release"]
            first_id = proposed_data_release_id(ROOT, runtime, previous_phase4_release_id=genesis["data_release_id"], result_revision_ids=["revision-01"])
            first = append_data_release(
                ROOT, runtime, data_release_id=first_id, previous_phase4_release_id=genesis["data_release_id"],
                result_revision_ids=["revision-01"], clock="2026-01-02T00:00:00Z", producer_provenance=PROVENANCE,
            )["release"]
            second_id = proposed_data_release_id(ROOT, runtime, previous_phase4_release_id=first_id, result_revision_ids=["revision-02"])
            second = append_data_release(
                ROOT, runtime, data_release_id=second_id, previous_phase4_release_id=first_id,
                result_revision_ids=["revision-02"], clock="2026-01-03T00:00:00Z", producer_provenance=PROVENANCE,
            )["release"]
            self.assertEqual(second["previous_phase4_release_id"], first["data_release_id"])
            branch_id = proposed_data_release_id(ROOT, runtime, previous_phase4_release_id=genesis["data_release_id"], result_revision_ids=["revision-branch"])
            with self.assertRaises(DataChainMismatch):
                append_data_release(
                    ROOT, runtime, data_release_id=branch_id, previous_phase4_release_id=genesis["data_release_id"],
                    result_revision_ids=["revision-branch"], clock="2026-01-04T00:00:00Z", producer_provenance=PROVENANCE,
                )

    def test_genesis_resume_after_successor_preserves_current_release_and_head(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            genesis = create_genesis(
                ROOT, runtime, GENESIS, clock="2026-01-01T00:00:00Z", producer_provenance=PROVENANCE,
            )["release"]
            successor_id = proposed_data_release_id(
                ROOT, runtime, previous_phase4_release_id=genesis["data_release_id"],
                result_revision_ids=["revision-01"],
            )
            append_data_release(
                ROOT, runtime, data_release_id=successor_id,
                previous_phase4_release_id=genesis["data_release_id"],
                result_revision_ids=["revision-01"], clock="2026-01-02T00:00:00Z",
                producer_provenance=PROVENANCE,
            )
            before = (runtime / "data-releases/current-view.json").read_bytes()
            resumed = create_genesis(
                ROOT, runtime, GENESIS, clock="2026-01-01T00:00:00Z", producer_provenance=PROVENANCE,
            )
            self.assertTrue(resumed["idempotent_resume"])
            self.assertEqual(resumed["current_view"]["data_release_id"], successor_id)
            self.assertEqual((runtime / "data-releases/current-view.json").read_bytes(), before)

    def test_barrier_synchronized_sibling_successors_only_one_commits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            genesis = create_genesis(
                ROOT, runtime, GENESIS, clock="2026-01-01T00:00:00Z", producer_provenance=PROVENANCE,
            )["release"]
            predecessor = genesis["data_release_id"]
            proposals = [
                (
                    proposed_data_release_id(
                        ROOT, runtime, previous_phase4_release_id=predecessor,
                        result_revision_ids=[revision],
                    ),
                    revision,
                )
                for revision in ("revision-ssq", "revision-dlt")
            ]
            barrier = threading.Barrier(2)

            def submit(proposal: tuple[str, str]) -> str:
                release_id, revision = proposal
                barrier.wait()
                try:
                    append_data_release(
                        ROOT, runtime, data_release_id=release_id,
                        previous_phase4_release_id=predecessor,
                        result_revision_ids=[revision], clock="2026-01-02T00:00:00Z",
                        producer_provenance=PROVENANCE,
                    )
                    return "PASS"
                except StaleDataChainHead:
                    return "STALE"

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(submit, proposals))
            self.assertEqual(sorted(outcomes), ["PASS", "STALE"])
            self.assertEqual(StaleDataChainHead.exit_code, 30)
            self.assertEqual(StaleDataChainHead.terminal, "STALE_DATA_CHAIN_HEAD")
            release_dirs = [path for path in (runtime / "data-releases").iterdir() if path.is_dir()]
            self.assertEqual(len(release_dirs), 2)
            self.assertEqual(AppendOnlyLedger(runtime, "data-chain").validate()["event_count"], 2)

    def test_identity_mismatch_empty_batch_and_changed_genesis_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw) / "runtime"
            genesis = create_genesis(ROOT, runtime, GENESIS, clock="2026-01-01T00:00:00Z", producer_provenance=PROVENANCE)["release"]
            for release_id, revisions in (("forged-release", ["revision-01"]), ("forged-empty", [])):
                with self.subTest(release_id=release_id), self.assertRaises(DataChainMismatch):
                    append_data_release(
                        ROOT, runtime, data_release_id=release_id,
                        previous_phase4_release_id=genesis["data_release_id"], result_revision_ids=revisions,
                        clock="2026-01-02T00:00:00Z", producer_provenance=PROVENANCE,
                    )
            changed = Path(raw) / "changed-genesis.json"
            payload = load_json(GENESIS, reject_floats=True)
            payload["base_phase1_records_sha256"] = "0" * 64
            changed.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(DataChainMismatch):
                create_genesis(ROOT, Path(raw) / "other", changed, clock="2026-01-01T00:00:00Z", producer_provenance=PROVENANCE)

    def test_cli_contract_registry_and_unimplemented_provider_hold(self) -> None:
        parser, specifications = build_parser(ROOT)
        expected = {tuple(row["verb"].split(" ", 1)) for row in load_json(ROOT / "config/phase4/cli-contract.json")["commands"]}
        self.assertEqual(set(specifications), expected)
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = main([
                "replay", "release", "--release-root", "x", "--manifest", "y", "--output", "z",
            ])
        self.assertEqual(code, 20)

    def test_invocation_provenance_is_complete_and_matches_actual_actor(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ContractEvidenceMismatch):
                producer_provenance(ROOT, "artifacts/phase-4-runtime/unit")
        with mock.patch.dict(os.environ, {"P4_ACTOR_ID": "p4-implementation-author-i01"}, clear=True):
            with self.assertRaises(ContractEvidenceMismatch):
                producer_provenance(ROOT, "artifacts/phase-4-runtime/unit")
        contexts = (
            {
                "P4_ACTOR_ID": "p4-implementation-author-i01", "P4_SESSION_ID": "/root/implementation_author",
                "P4_TASK_ID": "T02", "P4_ROLE": "implementation_author", "P4_ACTOR_ASSIGNMENTS": ACTOR_ASSIGNMENTS,
            },
            {
                "P4_ACTOR_ID": "p4-acceptance-engineer-i01", "P4_SESSION_ID": "/root/acceptance_engineer",
                "P4_TASK_ID": "T02", "P4_ROLE": "acceptance_engineer", "P4_ACTOR_ASSIGNMENTS": ACTOR_ASSIGNMENTS,
            },
        )
        for context in contexts:
            with self.subTest(actor=context["P4_ACTOR_ID"]), mock.patch.dict(os.environ, context, clear=True):
                provenance = producer_provenance(ROOT, "artifacts/phase-4-runtime/unit")
                self.assertEqual(provenance["producer_actor_id"], context["P4_ACTOR_ID"])
                self.assertEqual(provenance["session_id"], context["P4_SESSION_ID"])
                self.assertEqual(provenance["role"], context["P4_ROLE"])
        invalid = dict(contexts[0], P4_SESSION_ID="/root/acceptance_engineer")
        with mock.patch.dict(os.environ, invalid, clear=True), self.assertRaises(ContractEvidenceMismatch):
            producer_provenance(ROOT, "artifacts/phase-4-runtime/unit")


if __name__ == "__main__":
    unittest.main()
