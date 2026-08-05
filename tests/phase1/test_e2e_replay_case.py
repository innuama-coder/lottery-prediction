from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import Any


REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
FORMAL_ROOT = REPO / "artifacts" / "phase-1"
sys.path.insert(0, str(REPO / "tests" / "phase1"))

import run_acceptance as acceptance  # noqa: E402
from lottery_data.steps.recovery import recover_stale_runs  # noqa: E402
from lottery_data.steps.replay import ReplayMutationError  # noqa: E402
from lottery_data.workflow import classify_failure, default_dependencies, execute_replay  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_state(root: Path) -> dict[str, tuple[str, str | None]]:
    state: dict[str, tuple[str, str | None]] = {}
    if not root.exists():
        return state
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            state[relative] = ("directory", None)
        elif path.is_file():
            state[relative] = ("file", _sha256(path))
        else:
            state[relative] = ("other", None)
    return state


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _run_cli(arguments: list[str], environment: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, "-m", "lottery_data", *arguments],
        cwd=REPO,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        raise AssertionError(
            f"CLI produced {len(lines)} stdout lines; exit={completed.returncode}; "
            f"stdout={completed.stdout!r}; stderr={completed.stderr!r}"
        )
    value = json.loads(lines[0])
    if not isinstance(value, dict):
        raise AssertionError(f"CLI stdout is not one JSON object: {value!r}")
    return completed, value


def _bootstrap(artifacts: Path, environment: dict[str, str], *, run_id: str, release_id: str) -> dict[str, Any]:
    completed, result = _run_cli(
        [
            "run", "--mode", "bootstrap", "--source-mode", "snapshot",
            "--phase0-snapshot", str(SNAPSHOT), "--run-id", run_id,
            "--release-id", release_id, "--artifacts-root", str(artifacts),
        ],
        environment,
    )
    if completed.returncode != 0:
        raise AssertionError(f"bootstrap failed: {completed.stderr}\n{completed.stdout}")
    if (result.get("status"), result.get("release_id")) != ("published", release_id):
        raise AssertionError(f"unexpected bootstrap result: {result!r}")
    return result


def _publication_state(artifacts: Path, release_id: str) -> dict[str, object]:
    pointer = artifacts / "current-release.json"
    lock = artifacts / ".publish.lock"
    releases = artifacts / "releases"
    return {
        "pointer": pointer.read_bytes() if pointer.is_file() else None,
        "publish_lock": lock.read_bytes() if lock.is_file() else None,
        "release_inventory": _tree_state(releases / release_id),
        "release_names": tuple(sorted(path.name for path in releases.iterdir() if path.is_dir())),
        "projection_inventory": _tree_state(artifacts / release_id),
        "journal_inventory": _tree_state(artifacts / ".publication-journals"),
    }


class OfflineReplayEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        formal_before = _tree_state(FORMAL_ROOT)
        self.addCleanup(lambda: self.assertEqual(formal_before, _tree_state(FORMAL_ROOT)))

    def test_real_cli_offline_replay_is_deterministic_and_non_publishing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase1-e2e06-replay-") as temporary:
            base = Path(temporary)
            artifacts = base / "artifacts"
            environment = acceptance._network_guard(base)
            source_id, replay_id, release_id = "e2e06-source", "e2e06-replay", "baseline-v1"
            _bootstrap(artifacts, environment, run_id=source_id, release_id=release_id)

            source = artifacts / "runs" / source_id
            source_before = _tree_state(source)
            publication_before = _publication_state(artifacts, release_id)

            completed, result = _run_cli(
                [
                    "replay", "--source-run-id", source_id, "--run-id", replay_id,
                    "--offline", "--artifacts-root", str(artifacts),
                ],
                environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual((result.get("exit_code"), result.get("status")), (0, "no_change"))

            replay = artifacts / "runs" / replay_id
            events = _jsonl(replay / "events.jsonl")
            counts = Counter(row["event_type"] for row in events)
            self.assertEqual(
                counts,
                Counter({
                    "request_started": 30, "request_succeeded": 30,
                    "run_planned": 1, "run_started": 1, "run_no_change": 1,
                }),
            )
            terminals = [
                row for row in events
                if row["event_type"] in {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
            ]
            self.assertEqual(len(terminals), 1)
            self.assertIs(terminals[0], events[-1])
            self.assertEqual(terminals[0]["event_type"], "run_no_change")

            for name in ("observations.jsonl", "reconciliation.jsonl", "candidate-draws.jsonl"):
                self.assertEqual((source / name).read_bytes(), (replay / name).read_bytes(), name)
            source_quality = json.loads((source / "quality-report.json").read_text(encoding="utf-8"))
            replay_quality = json.loads((replay / "quality-report.json").read_text(encoding="utf-8"))
            self.assertEqual(source_quality["deterministic"], replay_quality["deterministic"])

            replay_manifest = json.loads((replay / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(replay_manifest["replay_of_run_id"], source_id)
            self.assertEqual(replay_manifest["source_mode"], "snapshot")
            self.assertEqual({request["method"] for request in replay_manifest["request_plan"]}, {"SNAPSHOT"})
            self.assertEqual(_tree_state(source), source_before)
            self.assertEqual(_publication_state(artifacts, release_id), publication_before)
            self.assertFalse((artifacts / ".publication-journals" / f"{replay_id}.json").exists())
            self.assertFalse((artifacts / "releases" / replay_id).exists())
            self.assertFalse((artifacts / replay_id).exists())

    def test_concurrent_source_mutation_fails_closed_and_next_startup_recovers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase1-e2e06-mutation-") as temporary:
            base = Path(temporary)
            artifacts = base / "artifacts"
            environment = acceptance._network_guard(base)
            source_id, replay_id, release_id = "e2e06-source-negative", "e2e06-replay-mutation", "baseline-v1"
            _bootstrap(artifacts, environment, run_id=source_id, release_id=release_id)

            source = artifacts / "runs" / source_id
            source_before = _tree_state(source)
            publication_before = _publication_state(artifacts, release_id)
            manifest = json.loads((source / "run-manifest.json").read_text(encoding="utf-8"))
            second_raw = source / manifest["request_plan"][1]["input_ref"]
            original_raw = second_raw.read_bytes()
            rendezvous = Barrier(2)
            dependencies = default_dependencies()
            parse_calls = 0

            def parse_with_rendezvous(*args: object, **kwargs: object) -> list[dict[str, Any]]:
                nonlocal parse_calls
                parsed = dependencies.parse_raw(*args, **kwargs)
                parse_calls += 1
                if parse_calls == 1:
                    rendezvous.wait(timeout=10)
                    rendezvous.wait(timeout=10)
                return parsed

            def mutate_next_raw() -> None:
                rendezvous.wait(timeout=10)
                second_raw.write_bytes(original_raw + b"\n")
                rendezvous.wait(timeout=10)

            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    mutation = executor.submit(mutate_next_raw)
                    with self.assertRaises(ReplayMutationError) as caught:
                        execute_replay(
                            artifacts_root=artifacts,
                            source_run_id=source_id,
                            run_id=replay_id,
                            offline=True,
                            dependencies=replace(dependencies, parse_raw=parse_with_rendezvous),
                        )
                    mutation.result(timeout=10)
            finally:
                second_raw.write_bytes(original_raw)

            failure = caught.exception
            self.assertEqual(failure.exit_code, 5)
            self.assertTrue(failure.recovery_required)
            self.assertIsNotNone(failure.original_error)
            classified = classify_failure(failure)
            self.assertEqual((classified.exit_code, classified.error_code), (5, "REPLAY_MISMATCH"))
            self.assertEqual(_tree_state(source), source_before)
            self.assertEqual(_publication_state(artifacts, release_id), publication_before)

            replay = artifacts / "runs" / replay_id
            self.assertTrue(replay.is_dir())
            self.assertFalse((replay / "run-result.json").exists())
            self.assertNotIn(
                "run_no_change",
                {row["event_type"] for row in _jsonl(replay / "events.jsonl")},
            )

            startup_result = _bootstrap(
                artifacts,
                environment,
                run_id="e2e06-recovery-startup",
                release_id="recovery-v1",
            )
            self.assertEqual(startup_result["status"], "published")

            recovered_result = json.loads((replay / "run-result.json").read_text(encoding="utf-8"))
            recovered_events = _jsonl(replay / "events.jsonl")
            self.assertEqual(recovered_result["status"], "interrupted")
            self.assertIsNone(recovered_result["exit_code"])
            self.assertEqual(recovered_events[-1]["event_type"], "run_interrupted")
            self.assertEqual(
                sum(row["event_type"] in {"run_published", "run_no_change", "run_rejected", "run_interrupted"}
                    for row in recovered_events),
                1,
            )
            self.assertTrue((replay / "recovery" / "process-interrupted.json").is_file())
            self.assertEqual(
                recover_stale_runs(artifacts, clock=lambda: "2026-08-03T00:00:00Z"),
                (),
            )
            self.assertEqual(_tree_state(source), source_before)


if __name__ == "__main__":
    unittest.main()
