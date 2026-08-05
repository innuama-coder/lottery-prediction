from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lottery_data.artifacts import atomic_write_bytes, atomic_write_json
from lottery_data.models import validate_object
from lottery_data.serialization import canonical_json_bytes, make_event_id, sha256_file
from lottery_data.steps.events import EventLog
from lottery_data.steps.locking import LockUnavailable, OSFileLock
from lottery_data.steps.publication_journal import JournalError, PublicationJournal, STATES, tree_sha256
from lottery_data.steps.publish import PublishError, PublishLock
from lottery_data.steps.recovery import RecoveryConflict, recover_stale_publications
from lottery_data.steps.replay import (
    DisabledReplayPublication, OfflineReplayTransport, ReplayContractError, ReplayDeterminismError,
    ReplayMutationError, ReplayNetworkForbidden, ReplayPublicationForbidden, ReplayReadOnlyGuard,
    compare_deterministic_outputs, prepare_replay, replay_session,
)
from lottery_data.steps.report import RunCounters, build_run_result


NOW = "2026-08-02T15:00:00.000Z"


class LockingTests(unittest.TestCase):
    def test_nonblocking_lock_is_released_when_process_dies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "writer.lock"
            code = (
                "import sys; from pathlib import Path; "
                "from lottery_data.steps.locking import OSFileLock; "
                "guard=OSFileLock(Path(sys.argv[1])).acquire(); "
                "print('READY', flush=True); sys.stdin.read()"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", code, str(lock)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "READY")
                with self.assertRaises(LockUnavailable):
                    OSFileLock(lock).acquire()
                process.kill()
                process.communicate(timeout=10)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        with OSFileLock(lock):
                            break
                    except LockUnavailable:
                        if time.monotonic() >= deadline:
                            self.fail("terminated process did not release its OS lock")
                        time.sleep(0.05)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)


class JournalRecoveryTests(unittest.TestCase):
    def _run_fixture(
        self, root: Path, *, run_id: str = "run-1", request_plan: list[dict[str, object]] | None = None,
    ) -> tuple[Path, EventLog]:
        run = root / "runs" / run_id
        run.mkdir(parents=True)
        request_plan = request_plan or []
        atomic_write_json(run / "run-manifest.json", {
            "run_schema_version": "1.0.0", "run_id": run_id, "mode": "bootstrap",
            "source_mode": "snapshot", "started_at_utc": NOW, "artifacts_root": str(root),
            "previous_release_id": None, "games": ["ssq"], "request_plan": request_plan,
            "config_files": [{"ref": "config/source.json", "sha256": "1" * 64}],
            "schema_bundle_sha256": "2" * 64, "pipeline_bundle_sha256": "3" * 64,
            "python_version": "3.12.0",
            "bootstrap_snapshot": {
                "snapshot_id": "20260802T150000Z", "snapshot_root": "snapshot",
                "artifact_hashes_ref": "snapshot/artifact-hashes.json", "artifact_hashes_sha256": "4" * 64,
            },
            "incremental_watermark": None,
            "publish_policy": {
                "lock_ref": ".publish.lock", "compare_and_swap": True,
                "atomic_release_rename": True, "atomic_pointer_replace": True,
            },
            "replay_of_run_id": None,
        })
        atomic_write_json(run / "quality-report.json", {"decision": "PASS"})
        events = EventLog(run / "events.jsonl", run_id, lambda: NOW)
        events.append("run_planned")
        events.append("run_started")
        return run, events

    def _advance_to(self, journal: PublicationJournal, state: str) -> None:
        while journal.read()["state"] != state:
            current = STATES.index(journal.read()["state"])
            journal.advance(STATES[current + 1], updated_at_utc=NOW)

    def _write_published_result(self, run: Path, *, run_id: str = "run-1") -> None:
        result = build_run_result(
            run_id=run_id, mode="bootstrap", status="published", exit_code=0,
            started_at_utc=NOW, completed_at_utc=NOW, release_id="new",
            manifest_ref=f"runs/{run_id}/run-manifest.json", events_ref=f"runs/{run_id}/events.jsonl",
            quality_report_ref=f"runs/{run_id}/quality-report.json", error_refs=[],
            counters=RunCounters(artifact_hashes={
                "events": sha256_file(run / "events.jsonl"),
                "run_manifest": sha256_file(run / "run-manifest.json"),
            }),
        )
        atomic_write_json(run / "run-result.json", result)
        self._refresh_run_hashes(run)

    def _refresh_run_hashes(self, run: Path) -> None:
        entries = []
        root = run.parents[1]
        managed = [
            (run / "run-manifest.json", "manifest"), (run / "events.jsonl", "event"),
            (run / "quality-report.json", "quality"), (run / "run-result.json", "result"),
        ]
        managed.extend((path, "evidence") for path in sorted(run.glob("other*.json")))
        for path, role in managed:
            entries.append({
                "path": path.relative_to(root).as_posix(), "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size, "role": role,
            })
        atomic_write_json(run / "hashes.json", {
            "hash_manifest_schema_version": "1.0.0", "hash_profile": "sha256-file-manifest-v1",
            "generated_at_utc": NOW, "entries": entries,
        })

    def _install_published_trees(self, root: Path, run: Path, *, run_id: str = "run-1") -> None:
        release = root / "releases" / "new"
        release.mkdir(parents=True)
        (release / "draws.jsonl").write_bytes(b"")
        (release / "observations.jsonl").write_bytes(b"")
        (release / "quality-report.json").write_bytes((run / "quality-report.json").read_bytes())
        atomic_write_json(release / "manifest.json", {
            "release_schema_version": "1.0.0", "release_id": "new", "created_at_utc": NOW,
            "previous_release_id": None, "input_run_id": run_id,
            "record_count_by_game": {"ssq": 0, "dlt": 0}, "observation_count": 0,
            "input_manifest_sha256": sha256_file(run / "run-manifest.json"),
            "schema_bundle_sha256": "2" * 64, "pipeline_bundle_sha256": "3" * 64,
            "records_sha256": sha256_file(release / "draws.jsonl"),
            "observations_sha256": sha256_file(release / "observations.jsonl"),
            "quality_report_ref": "releases/new/quality-report.json", "status": "published",
        })
        atomic_write_json(release / "hashes.json", {"entries": []})
        projection = root / "new"
        projection.mkdir()
        for path in release.iterdir():
            (projection / path.name).write_bytes(path.read_bytes())

    def _journal(self, root: Path, *, run_id: str = "run-1") -> PublicationJournal:
        original = canonical_json_bytes({"release_id": "old"})
        committed = canonical_json_bytes({"release_id": "new"})
        atomic_write_bytes(root / "current-release.json", original)
        release = root / "releases" / "new"
        projection = root / "new"
        if release.is_dir() and projection.is_dir():
            release_tree = tree_sha256(release)
            projection_tree = tree_sha256(projection)
        else:
            identity = root / ".identity"
            identity.mkdir()
            release_tree = projection_tree = tree_sha256(identity)
            identity.rmdir()
        return PublicationJournal.create(
            artifacts_root=root, run_id=run_id, release_id="new",
            original_pointer_bytes=original, committed_pointer_bytes=committed,
            release_path="releases/new", projection_path="new",
            release_tree_sha256=release_tree, projection_tree_sha256=projection_tree,
            temporary_paths=[f"releases/.new.tmp-{run_id}", f".new.tmp-{run_id}"],
            updated_at_utc=NOW,
        )

    def test_journal_requires_contiguous_durable_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = self._journal(Path(directory))
            for state in STATES[1:]:
                value = journal.advance(state, updated_at_utc=NOW)
                self.assertEqual(value["state"], state)
            with self.assertRaises(JournalError):
                journal.advance("COMPLETED", updated_at_utc=NOW)

    def test_all_journal_state_and_side_effect_crash_windows_follow_disk_truth_matrix(self) -> None:
        cases = (
            # The same side effect appears on both sides of its journal write.
            ("before-release", "PREPARED", False, False, False, False, False, "rollback"),
            ("release-before-state", "PREPARED", True, False, False, False, False, "rollback"),
            ("release-after-state", "RELEASE_RENAMED", True, False, False, False, False, "rollback"),
            ("projection-before-state", "RELEASE_RENAMED", True, True, False, False, False, "rollback"),
            ("projection-after-state", "PROJECTION_RENAMED", True, True, False, False, False, "rollback"),
            ("pointer-before-state", "PROJECTION_RENAMED", True, True, True, False, False, "rollback"),
            ("pointer-after-state", "POINTER_COMMITTED", True, True, True, False, False, "rollback"),
            ("terminal-before-state", "POINTER_COMMITTED", True, True, True, True, False, "rollback"),
            ("terminal-after-state", "RUN_TERMINAL", True, True, True, True, False, "rollback"),
            ("result-before-state", "RUN_TERMINAL", True, True, True, True, True, "roll-forward"),
            ("result-after-state", "RESULT_WRITTEN", True, True, True, True, True, "roll-forward"),
            ("completed-before-state", "RESULT_WRITTEN", True, True, True, True, True, "roll-forward"),
            ("completed-after-state", "COMPLETED", True, True, True, True, True, "no-op"),
        )
        for name, state, release, projection, committed, terminal, result, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run, events = self._run_fixture(root)
                if terminal:
                    events.append("run_published", artifact_ref="releases/new/manifest.json")
                if result:
                    self._write_published_result(run)
                if expected in {"roll-forward", "no-op"}:
                    self._install_published_trees(root, run)
                journal = self._journal(root)
                self._advance_to(journal, state)
                if release and not (root / "releases" / "new").exists():
                    (root / "releases" / "new").mkdir(parents=True)
                if projection and not (root / "new").exists():
                    (root / "new").mkdir()
                if committed:
                    atomic_write_bytes(root / "current-release.json", canonical_json_bytes({"release_id": "new"}))

                report = recover_stale_publications(root, clock=lambda: NOW)

                if expected == "rollback":
                    self.assertEqual(report.recovered_run_ids, ("run-1",))
                    self.assertEqual(report.rolled_forward_run_ids, ())
                    self.assertEqual((root / "current-release.json").read_bytes(), canonical_json_bytes({"release_id": "old"}))
                    self.assertFalse((root / "releases" / "new").exists())
                    self.assertFalse((root / "new").exists())
                    repaired = json.loads((run / "run-result.json").read_text())
                    self.assertEqual((repaired["status"], repaired["release_id"]), ("interrupted", None))
                elif expected == "roll-forward":
                    self.assertEqual(report.rolled_forward_run_ids, ("run-1",))
                    self.assertTrue((root / "releases" / "new").is_dir())
                    self.assertTrue((root / "new").is_dir())
                    self.assertEqual(journal.read()["recovery"]["status"], "rolled_forward")
                else:
                    self.assertEqual((report.recovered_run_ids, report.rolled_forward_run_ids), ((), ()))
                    self.assertEqual(journal.read()["state"], "COMPLETED")

    def test_recovery_rolls_back_pointer_quarantines_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = [
                {"request_id": "request-1", "sequence": 1, "source_id": "ydniu", "publisher_id": "ydniu-publisher", "game": "ssq", "method": "SNAPSHOT", "url": "https://example.test/1", "input_ref": "raw/ydniu/ssq/request-1.html"},
                {"request_id": "request-1-retry", "sequence": 2, "source_id": "ydniu", "publisher_id": "ydniu-publisher", "game": "ssq", "method": "SNAPSHOT", "url": "https://example.test/2", "input_ref": "raw/ydniu/ssq/request-2.html"},
            ]
            run, events = self._run_fixture(root, request_plan=plan)
            events.append("request_started", request_id="request-1", attempt=1, source_id="ydniu", game="ssq")
            events.append(
                "request_succeeded", request_id="request-1", attempt=1,
                source_id="ydniu", game="ssq", artifact_ref="raw/ydniu/ssq/request-1.html",
            )
            events.append("request_started", request_id="request-1-retry", attempt=1, source_id="ydniu", game="ssq")
            journal = self._journal(root)
            (root / "releases" / "new").mkdir(parents=True)
            journal.advance("RELEASE_RENAMED", updated_at_utc=NOW)
            (root / "new").mkdir()
            journal.advance("PROJECTION_RENAMED", updated_at_utc=NOW)
            committed = canonical_json_bytes({"release_id": "new"})
            atomic_write_bytes(root / "current-release.json", committed)
            journal.advance("POINTER_COMMITTED", updated_at_utc=NOW)

            report = recover_stale_publications(root, clock=lambda: NOW)
            self.assertEqual(report.recovered_run_ids, ("run-1",))
            self.assertEqual(report.rolled_forward_run_ids, ())
            self.assertEqual(json.loads((root / "current-release.json").read_text()), {"release_id": "old"})
            self.assertFalse((root / "releases" / "new").exists())
            self.assertFalse((root / "new").exists())
            rows = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
            self.assertEqual([row["event_type"] for row in rows[-2:]], ["request_failed", "run_interrupted"])
            self.assertEqual((rows[-2]["request_id"], rows[-2]["attempt"]), ("request-1-retry", 1))
            result = json.loads((run / "run-result.json").read_text())
            validate_object("RunResult", result)
            self.assertEqual((result["status"], result["exit_code"], result["release_id"]), ("interrupted", None, None))
            before = (run / "events.jsonl").read_bytes()
            second = recover_stale_publications(root, clock=lambda: NOW)
            self.assertEqual(second.recovered_run_ids, ())
            self.assertEqual((run / "events.jsonl").read_bytes(), before)
            self.assertEqual(journal.read()["state"], "COMPLETED")

    def test_prepared_with_release_already_renamed_is_not_orphaned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, events = self._run_fixture(root)
            journal = self._journal(root)
            (root / "releases" / "new").mkdir(parents=True)
            report = recover_stale_publications(root, clock=lambda: NOW)
            self.assertEqual(report.recovered_run_ids, ("run-1",))
            self.assertFalse((root / "releases" / "new").exists())
            self.assertTrue((run / "recovery" / "quarantine" / "releases" / "new").is_dir())

    def test_complete_published_disk_truth_rolls_forward_even_if_journal_lags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, events = self._run_fixture(root)
            events.append("run_published", artifact_ref="releases/new/manifest.json")
            self._write_published_result(run)
            self._install_published_trees(root, run)
            journal = self._journal(root)
            atomic_write_bytes(root / "current-release.json", canonical_json_bytes({"release_id": "new"}))
            report = recover_stale_publications(root, clock=lambda: NOW)
            self.assertEqual(report.rolled_forward_run_ids, ("run-1",))
            self.assertTrue((root / "releases" / "new").is_dir())
            self.assertEqual(journal.read()["recovery"]["status"], "rolled_forward")

    def test_complete_looking_wrong_run_ref_or_hash_never_rolls_forward(self) -> None:
        for attack in ("wrong-run", "wrong-ref", "wrong-hash"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run, events = self._run_fixture(root)
                events.append("run_published", artifact_ref="releases/new/manifest.json")
                self._write_published_result(run)
                if attack == "wrong-run":
                    rows = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
                    rows[-1]["run_id"] = "other-run"
                    rows[-1]["event_id"] = make_event_id(
                        "other-run", rows[-1]["sequence"], rows[-1]["event_type"], None, None,
                    )
                    atomic_write_bytes(run / "events.jsonl", b"".join(canonical_json_bytes(row) for row in rows))
                    result = json.loads((run / "run-result.json").read_text())
                    result["deterministic_artifact_hashes"]["events"] = sha256_file(run / "events.jsonl")
                    atomic_write_json(run / "run-result.json", result)
                elif attack == "wrong-ref":
                    (run / "other-manifest.json").write_bytes((run / "run-manifest.json").read_bytes())
                    result = json.loads((run / "run-result.json").read_text())
                    result["manifest_ref"] = "runs/run-1/other-manifest.json"
                    atomic_write_json(run / "run-result.json", result)
                else:
                    result = json.loads((run / "run-result.json").read_text())
                    result["deterministic_artifact_hashes"]["events"] = "0" * 64
                    atomic_write_json(run / "run-result.json", result)
                self._refresh_run_hashes(run)
                self._install_published_trees(root, run)
                journal = self._journal(root)
                self._advance_to(journal, "RESULT_WRITTEN")
                atomic_write_bytes(root / "current-release.json", canonical_json_bytes({"release_id": "new"}))

                report = recover_stale_publications(root, clock=lambda: NOW)

                self.assertEqual((report.recovered_run_ids, report.rolled_forward_run_ids), (("run-1",), ()))
                repaired = json.loads((run / "run-result.json").read_text())
                self.assertEqual((repaired["run_id"], repaired["status"], repaired["release_id"]), ("run-1", "interrupted", None))
                self.assertEqual((root / "current-release.json").read_bytes(), canonical_json_bytes({"release_id": "old"}))

    def test_recovery_itself_is_crash_idempotent_at_every_durable_step(self) -> None:
        cases = (
            ("request_failed", "before"), ("request_failed", "after"),
            ("run_interrupted", "before"), ("run_interrupted", "after"),
            ("run-result", "before"), ("run-result", "after"),
            ("journal-completed", "before"), ("journal-completed", "after"),
        )
        for step, timing in cases:
            with self.subTest(step=step, timing=timing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plan = [{
                    "request_id": "request-1", "sequence": 1, "source_id": "ydniu",
                    "publisher_id": "ydniu-publisher", "game": "ssq", "method": "SNAPSHOT",
                    "url": "https://example.test/1", "input_ref": "raw/ydniu/ssq/request-1.html",
                }]
                run, events = self._run_fixture(root, request_plan=plan)
                events.append("request_started", request_id="request-1", attempt=1, source_id="ydniu", game="ssq")
                journal = self._journal(root)
                counter = {"value": 0}

                def clock() -> str:
                    counter["value"] += 1
                    return f"2026-08-02T15:00:{counter['value']:02d}.000Z"

                injected = {"done": False}
                original_append = EventLog.append
                original_write = __import__("lottery_data.steps.recovery", fromlist=["write_once_json"]).write_once_json
                original_complete = PublicationJournal.complete_recovery

                def append_hook(log: EventLog, event_type: str, **values: object) -> dict[str, object]:
                    if event_type == step and not injected["done"]:
                        injected["done"] = True
                        if timing == "before":
                            raise RuntimeError("injected recovery crash")
                        value = original_append(log, event_type, **values)
                        raise RuntimeError("injected recovery crash")
                    return original_append(log, event_type, **values)

                def write_hook(path: Path, value: object) -> None:
                    if step == "run-result" and path.name == "run-result.json" and not injected["done"]:
                        injected["done"] = True
                        if timing == "before":
                            raise RuntimeError("injected recovery crash")
                        original_write(path, value)
                        raise RuntimeError("injected recovery crash")
                    original_write(path, value)

                def complete_hook(journal_self: PublicationJournal, **values: object) -> dict[str, object]:
                    if step == "journal-completed" and not injected["done"]:
                        injected["done"] = True
                        if timing == "before":
                            raise RuntimeError("injected recovery crash")
                        value = original_complete(journal_self, **values)
                        raise RuntimeError("injected recovery crash")
                    return original_complete(journal_self, **values)

                with patch.object(EventLog, "append", new=append_hook), patch(
                    "lottery_data.steps.recovery.write_once_json", new=write_hook,
                ), patch.object(PublicationJournal, "complete_recovery", new=complete_hook):
                    with self.assertRaisesRegex(RuntimeError, "injected recovery crash"):
                        recover_stale_publications(root, clock=clock)

                pair_after_crash = {
                    name: (run / name).read_bytes() for name in ("events.jsonl", "run-result.json")
                    if (run / name).is_file()
                }
                recover_stale_publications(root, clock=clock)
                stable = {
                    name: (run / name).read_bytes() for name in (
                        "events.jsonl", "run-result.json", "recovery/process-interrupted.json",
                    )
                }
                stable_hashes = {name: sha256_file(run / name) for name in stable}
                journal_bytes = journal.path.read_bytes()
                if timing == "after" and step in {"run-result", "journal-completed"}:
                    self.assertEqual(pair_after_crash["events.jsonl"], stable["events.jsonl"])
                    self.assertEqual(pair_after_crash["run-result.json"], stable["run-result.json"])
                third = recover_stale_publications(root, clock=clock)
                self.assertEqual((third.recovered_run_ids, third.rolled_forward_run_ids), ((), ()))
                self.assertEqual(journal.path.read_bytes(), journal_bytes)
                for name, payload in stable.items():
                    self.assertEqual((run / name).read_bytes(), payload)
                    self.assertEqual(sha256_file(run / name), stable_hashes[name])

    def test_result_written_without_matching_published_pair_rolls_back_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, events = self._run_fixture(root)
            events.append("run_published", artifact_ref="releases/new/manifest.json")
            journal = self._journal(root)
            for state in STATES[1:6]:
                journal.advance(state, updated_at_utc=NOW)
            (root / "releases" / "new").mkdir(parents=True)
            (root / "new").mkdir()
            # Missing published result makes roll-forward unsafe despite RESULT_WRITTEN journal state.
            atomic_write_bytes(root / "current-release.json", canonical_json_bytes({"release_id": "new"}))
            recover_stale_publications(root, clock=lambda: NOW)
            repaired_events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
            self.assertNotIn("run_published", [row["event_type"] for row in repaired_events])
            self.assertEqual(repaired_events[-1]["event_type"], "run_interrupted")
            repaired_result = json.loads((run / "run-result.json").read_text())
            self.assertEqual((repaired_result["status"], repaired_result["release_id"]), ("interrupted", None))

    def test_recovery_and_publish_share_one_os_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with PublishLock(root / ".publish.lock", "publisher"):
                with self.assertRaises(PublishError):
                    recover_stale_publications(root, clock=lambda: NOW)

    def test_recovery_refuses_unknown_pointer_identity_with_exit_6(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = self._journal(root, run_id="conflict")
            journal.advance("RELEASE_RENAMED", updated_at_utc=NOW)
            journal.advance("PROJECTION_RENAMED", updated_at_utc=NOW)
            journal.advance("POINTER_COMMITTED", updated_at_utc=NOW)
            atomic_write_bytes(root / "current-release.json", b"other\n")
            with self.assertRaises(RecoveryConflict) as caught:
                recover_stale_publications(root, clock=lambda: NOW)
            self.assertEqual(caught.exception.exit_code, 6)

    def test_recovery_refuses_third_party_release_or_projection_content(self) -> None:
        for relative in ("releases/new", "new"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._run_fixture(root)
                self._journal(root)
                path = root / relative
                path.mkdir(parents=True)
                (path / "third-party.txt").write_text("not journal-owned", encoding="utf-8")
                with self.assertRaises(RecoveryConflict) as caught:
                    recover_stale_publications(root, clock=lambda: NOW)
                self.assertEqual(caught.exception.exit_code, 6)
                self.assertTrue(path.is_dir())


class ReplayTests(unittest.TestCase):
    def _source_run(self, root: Path) -> Path:
        run = root / "runs" / "source-1"
        raw = run / "raw" / "ydniu" / "ssq" / "page-001.html"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"saved raw")
        source_catalog = run / "config" / "phase1" / "source-catalog.json"
        collection_policy = run / "config" / "phase1" / "collection-policy.json"
        source_catalog.parent.mkdir(parents=True)
        atomic_write_json(source_catalog, {"source": "run-local"})
        atomic_write_json(collection_policy, {"policy": "run-local"})
        atomic_write_json(run / "run-manifest.json", {
            "run_schema_version": "1.0.0",
            "run_id": "source-1",
            "mode": "incremental", "source_mode": "snapshot",
            "started_at_utc": NOW, "artifacts_root": str(root),
            "previous_release_id": "baseline-v1", "games": ["ssq"],
            "config_files": [
                {"ref": "config/phase1/source-catalog.json", "sha256": sha256_file(source_catalog)},
                {"ref": "config/phase1/collection-policy.json", "sha256": sha256_file(collection_policy)},
            ],
            "request_plan": [{
                "request_id": "r1", "sequence": 1, "source_id": "ydniu", "publisher_id": "ydniu-publisher",
                "game": "ssq", "method": "SNAPSHOT", "url": "https://www.ydniu.com/open/ssq-500/1.html",
                "input_ref": "raw/ydniu/ssq/page-001.html",
            }],
            "schema_bundle_sha256": "0" * 64, "pipeline_bundle_sha256": "1" * 64,
            "python_version": "3.12.11", "bootstrap_snapshot": None,
            "incremental_watermark": {
                "current_release_id": "baseline-v1", "latest_issue_by_game": {"ssq": "2026001"},
                "recheck_published_issues": 20,
            },
            "publish_policy": {
                "lock_ref": ".publish.lock", "compare_and_swap": True,
                "atomic_release_rename": True, "atomic_pointer_replace": True,
            },
            "replay_of_run_id": None,
        })
        atomic_write_json(run / "run-result.json", {"run_id": "source-1", "status": "no_change"})
        atomic_write_json(run / "quality-report.json", {"decision": "PASS", "deterministic": {"counts": {"ssq": 1}}})
        (run / "events.jsonl").write_bytes(canonical_json_bytes({"event_type": "run_no_change"}))
        for name, payload in (
            ("observations.jsonl", b"observation\n"),
            ("reconciliation.jsonl", b"reconciliation\n"),
            ("candidate-draws.jsonl", b"draw\n"),
        ):
            (run / name).write_bytes(payload)
        hashed = [
            run / name for name in (
                "run-manifest.json", "run-result.json", "quality-report.json", "events.jsonl",
                "observations.jsonl", "reconciliation.jsonl", "candidate-draws.jsonl",
            )
        ]
        hashed.extend((raw, source_catalog, collection_policy))
        atomic_write_json(run / "hashes.json", {
            "entries": [{
                "path": path.relative_to(root).as_posix(), "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size, "role": "raw" if path == raw else "run",
            } for path in hashed],
        })
        return run

    def test_replay_plan_is_offline_hash_verified_and_reparse_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self._source_run(root)
            plan = prepare_replay(root, "source-1")
            self.assertFalse(plan.network_allowed)
            self.assertEqual(plan.requests[0]["method"], "SNAPSHOT")
            self.assertEqual(plan.requests[0]["source_raw_path"], run / "raw" / "ydniu" / "ssq" / "page-001.html")
            self.assertEqual(plan.requests[0]["source_raw_sha256"], sha256_file(plan.requests[0]["source_raw_path"]))
            self.assertEqual(plan.config_path("source-catalog.json"), run / "config" / "phase1" / "source-catalog.json")
            self.assertEqual(plan.config_path("collection-policy.json"), run / "config" / "phase1" / "collection-policy.json")

    def test_replay_source_id_reuses_stable_id_contract_before_touching_a_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._source_run(root)
            prospective = root / "runs" / "replay-new"
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

            for source_run_id in ("", ".", "..", "nested/source", r"nested\source", "-leading"):
                with self.subTest(source_run_id=source_run_id):
                    with self.assertRaisesRegex(ReplayContractError, "invalid replay source run id"):
                        prepare_replay(root, source_run_id)
                    self.assertFalse(prospective.exists())
                    self.assertEqual(before, sorted(path.relative_to(root).as_posix() for path in root.rglob("*")))

    def test_replay_rejects_complete_symlinked_or_escaping_runs_children_without_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "artifacts"
            source = self._source_run(root)
            external = self._source_run(base / "external")
            sibling_alias = root / "runs" / "source-alias"
            escaping_alias = root / "runs" / "external-alias"
            try:
                os.symlink(source, sibling_alias, target_is_directory=True)
                os.symlink(external, escaping_alias, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlink creation is unavailable")

            prospective = root / "runs" / "replay-new"
            for source_run_id in (sibling_alias.name, escaping_alias.name):
                with self.subTest(source_run_id=source_run_id):
                    with self.assertRaisesRegex(ReplayContractError, "symbolic link|direct, non-aliased"):
                        prepare_replay(root, source_run_id)
                    self.assertFalse(prospective.exists())

    def test_replay_rejects_raw_tamper_and_deterministic_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source_run(root)
            raw = source / "raw" / "ydniu" / "ssq" / "page-001.html"
            raw.write_bytes(b"tampered")
            with self.assertRaises(ReplayContractError):
                prepare_replay(root, "source-1")
            source = self._source_run(Path(tempfile.mkdtemp(dir=directory)))
            replay = source.parent / "replay"
            replay.mkdir()
            for name in ("observations.jsonl", "reconciliation.jsonl", "candidate-draws.jsonl", "quality-report.json"):
                (replay / name).write_bytes((source / name).read_bytes())
            compare_deterministic_outputs(source, replay)
            (replay / "candidate-draws.jsonl").write_bytes(b"drift\n")
            with self.assertRaises(ReplayDeterminismError):
                compare_deterministic_outputs(source, replay)

    def test_replay_guard_rejects_pointer_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_write_bytes(root / "current-release.json", b"old\n")
            with self.assertRaises(ReplayContractError):
                with ReplayReadOnlyGuard(root):
                    atomic_write_bytes(root / "current-release.json", b"new\n")

    def test_replay_capabilities_forbid_network_and_publication(self) -> None:
        transport = OfflineReplayTransport()
        publication = DisabledReplayPublication()
        for operation in (transport.request, transport.get, transport.open, transport.send):
            with self.assertRaises(ReplayNetworkForbidden):
                operation("https://example.test")
        for operation in (publication, publication.publish_release, publication.rollback_publication):
            with self.assertRaises(ReplayPublicationForbidden):
                operation()

    def test_replay_session_is_read_only_without_acquiring_publish_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._source_run(root)
            lock_path = root / ".publish.lock"
            self.assertFalse(lock_path.exists())
            with patch.object(OSFileLock, "acquire", side_effect=AssertionError("replay acquired an OS lock")):
                with replay_session(root, "source-1") as session:
                    with self.assertRaises(ReplayNetworkForbidden):
                        session.transport.get("https://example.test")
                    with self.assertRaises(ReplayPublicationForbidden):
                        session.publication.publish_release()
            self.assertFalse(lock_path.exists())

    def test_replay_session_succeeds_when_publication_surfaces_do_not_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._source_run(root)
            atomic_write_bytes(root / "current-release.json", b"stable\n")
            release_file = root / "releases" / "release-1" / "manifest.json"
            projection_file = root / "ssq" / "draws.jsonl"
            release_file.parent.mkdir(parents=True)
            projection_file.parent.mkdir(parents=True)
            release_file.write_bytes(b"stable release\n")
            projection_file.write_bytes(b"stable projection\n")
            before = (
                (root / "current-release.json").read_bytes(),
                release_file.read_bytes(),
                projection_file.read_bytes(),
            )

            with replay_session(root, "source-1"):
                pass

            self.assertEqual(before, (
                (root / "current-release.json").read_bytes(),
                release_file.read_bytes(),
                projection_file.read_bytes(),
            ))

    def test_replay_session_detects_source_raw_changed_after_it_was_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self._source_run(root)
            raw = run / "raw" / "ydniu" / "ssq" / "page-001.html"
            with self.assertRaises(ReplayMutationError) as caught:
                with replay_session(root, "source-1") as session:
                    self.assertEqual(session.plan.requests[0]["source_raw_path"].read_bytes(), b"saved raw")
                    raw.write_bytes(b"changed after copy")
            self.assertEqual(caught.exception.exit_code, 5)
            self.assertIn("source run inventory", str(caught.exception))

    def test_replay_source_mutation_preserves_body_exception_as_cause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self._source_run(root)
            with self.assertRaises(ReplayMutationError) as caught:
                with replay_session(root, "source-1"):
                    (run / "observations.jsonl").write_bytes(b"bypassed mutation\n")
                    raise ValueError("replay body failed")
            self.assertIsInstance(caught.exception.original_error, ValueError)
            self.assertIsInstance(caught.exception.__cause__, ValueError)

    def test_replay_missing_or_external_config_fails_before_new_run_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self._source_run(root)
            prospective = root / "runs" / "replay-new"
            (run / "config" / "phase1" / "source-catalog.json").unlink()
            with self.assertRaises(ReplayContractError):
                prepare_replay(root, "source-1")
            self.assertFalse(prospective.exists())

            second_root = root / "second"
            run = self._source_run(second_root)
            manifest_path = run / "run-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["config_files"][0]["ref"] = "../outside.json"
            atomic_write_json(manifest_path, manifest)
            with self.assertRaises(ReplayContractError):
                prepare_replay(second_root, "source-1")
            self.assertFalse(prospective.exists())

    def test_replay_source_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self._source_run(root)
            target = run / "config" / "phase1" / "source-catalog.json"
            link = run / "config" / "phase1" / "linked.json"
            try:
                os.symlink(target, link)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            with self.assertRaises(ReplayContractError):
                prepare_replay(root, "source-1")

    def test_replay_session_detects_concurrent_publisher_surface_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._source_run(root)
            atomic_write_bytes(root / "current-release.json", b"old\n")
            with self.assertRaises(ReplayMutationError) as caught:
                with replay_session(root, "source-1"):
                    with PublishLock(root / ".publish.lock", "concurrent-publisher"):
                        atomic_write_bytes(root / "current-release.json", b"new\n")
                        release_file = root / "releases" / "release-2" / "manifest.json"
                        projection_file = root / "ssq" / "draws.jsonl"
                        release_file.parent.mkdir(parents=True)
                        projection_file.parent.mkdir(parents=True)
                        release_file.write_bytes(b"new release\n")
                        projection_file.write_bytes(b"new projection\n")
            self.assertEqual(caught.exception.exit_code, 5)
            self.assertIsNone(caught.exception.original_error)
            self.assertIn("current-release.json", str(caught.exception))
            self.assertIn("releases inventory", str(caught.exception))
            self.assertIn("root projection inventory", str(caught.exception))

    def test_replay_guard_checks_all_surfaces_and_preserves_bypass_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_write_bytes(root / "current-release.json", b"old\n")
            release_file = root / "releases" / "release-1" / "manifest.json"
            projection_file = root / "ssq" / "draws.jsonl"
            release_file.parent.mkdir(parents=True)
            projection_file.parent.mkdir(parents=True)
            release_file.write_bytes(b"old release\n")
            projection_file.write_bytes(b"old projection\n")
            with self.assertRaises(ReplayMutationError) as caught:
                with ReplayReadOnlyGuard(root):
                    atomic_write_bytes(root / "current-release.json", b"new\n")
                    release_file.write_bytes(b"changed release content\n")
                    projection_file.write_bytes(b"changed projection content\n")
                    raise ValueError("inner replay failure")
            self.assertTrue(caught.exception.recovery_required)
            self.assertEqual(caught.exception.exit_code, 5)
            self.assertIsInstance(caught.exception.original_error, ValueError)
            self.assertIsInstance(caught.exception.__cause__, ValueError)
            self.assertIn("current-release.json", str(caught.exception))
            self.assertIn("releases inventory", str(caught.exception))
            self.assertIn("root projection inventory", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
