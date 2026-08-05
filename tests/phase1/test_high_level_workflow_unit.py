from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
import hashlib
from pathlib import Path
from unittest.mock import patch

from lottery_data import cli
from lottery_data.artifacts import atomic_write_json, write_once_json
from lottery_data.steps.events import EventLog
from lottery_data.steps.locking import RunLock
from lottery_data.steps.preflight import IncrementalArguments, PreflightError, prepare_incremental
from lottery_data.steps.recovery import recover_stale_publications, recover_stale_runs
from lottery_data.serialization import core_fact_sha256, make_observation_id
from lottery_data.workflow import default_dependencies
from lottery_data.workflow import execute_incremental, execute_replay, execute_verify


REPO = Path(__file__).resolve().parents[2]
NOW = "2026-08-03T00:00:00Z"


class HighLevelWorkflowUnitTests(unittest.TestCase):
    def _publication_fixture(self, root: Path) -> None:
        source = REPO / "artifacts" / "phase-1"
        shutil.copyfile(source / "current-release.json", root / "current-release.json")
        shutil.copytree(source / "releases" / "baseline-v1", root / "releases" / "baseline-v1")
        shutil.copytree(source / "baseline-v1", root / "baseline-v1")

    def _assert_live_incremental_count_parity(self, root: Path, run_id: str, result: dict) -> None:
        run_root = root / "runs" / run_id
        quality = json.loads((run_root / "quality-report.json").read_text(encoding="utf-8"))
        counts = quality["deterministic"]["counts"]
        reconciliation = [
            json.loads(line)
            for line in (run_root / "reconciliation.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertEqual(result["candidate_stats"]["observed"], len(reconciliation))
        self.assertEqual(result["candidate_stats"]["eligible"], counts["draws"])
        self.assertEqual(result["candidate_stats"]["unresolved"], counts["unresolved"])
        # RunResult counts every successfully parsed live response; the engine
        # quality count contains only observations selected for delta evaluation.
        self.assertGreaterEqual(result["observation_stats"]["parsed"], counts["run_observations"])
        self.assertEqual(result["observation_stats"]["valid"], result["observation_stats"]["parsed"])
        for key in ("added", "revised", "unchanged", "conflict"):
            self.assertEqual(result["change_stats"][key], counts[key])

    def test_live_policy_hash_failure_precedes_every_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / "config"
            config.mkdir()
            policy = (REPO / "config" / "phase1" / "live-source-policy.json").read_bytes()
            (config / "live-source-policy.json").write_bytes(policy + b"\n")
            artifacts = base / "never-created"
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main([
                    "run", "--mode", "incremental", "--source-mode", "live",
                    "--artifacts-root", str(artifacts), "--config-root", str(config),
                ])
            result = json.loads(stdout.getvalue())
            self.assertEqual((code, result["exit_code"], result["mode"]), (4, 4, "incremental"))
            self.assertNotIn("manifest_ref", result)
            self.assertFalse(artifacts.exists())

    def test_invalid_api_identifier_has_zero_filesystem_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory) / "sentinel"
            arguments = IncrementalArguments(
                mode="incremental", source_mode="live", snapshot_root=None,
                artifacts_root=artifacts, config_root=REPO / "config" / "phase1",
                run_id="../escape", release_id="valid-release",
            )
            with self.assertRaises(PreflightError):
                execute_incremental(arguments)
            self.assertFalse(artifacts.exists())

    def test_lock_contention_is_structured_exit6_without_started_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = RunLock(root, "contended-run").acquire()
            stdout, stderr = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = cli.main([
                        "run", "--mode", "incremental", "--source-mode", "live",
                        "--artifacts-root", str(root), "--config-root", str(REPO / "config" / "phase1"),
                        "--run-id", "contended-run", "--release-id", "contended-release",
                    ])
            finally:
                lock.release()
            result = json.loads(stdout.getvalue())
            self.assertEqual((code, result["exit_code"], result["status"]), (6, 6, "interrupted"))
            self.assertFalse((root / "runs" / "contended-run").exists())

    def test_init_crash_windows_are_quarantined_and_active_owner_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for run_id, window in (("empty-init", "empty"), ("manifest-init", "manifest"), ("events-empty-init", "events"), ("active-init", "manifest")):
                run = root / "runs" / run_id
                (run / "raw").mkdir(parents=True)
                if window in {"manifest", "events"}:
                    (run / "run-manifest.json").write_text("{}\n", encoding="utf-8")
                if window == "events":
                    (run / "events.jsonl").touch()
            lock = RunLock(root, "active-init").acquire()
            try:
                self.assertEqual(
                    recover_stale_runs(root, clock=lambda: NOW),
                    ("empty-init", "events-empty-init", "manifest-init"),
                )
                self.assertTrue((root / "runs" / "active-init").is_dir())
            finally:
                lock.release()
            self.assertEqual(recover_stale_runs(root, clock=lambda: NOW), ("active-init",))
            self.assertEqual(recover_stale_runs(root, clock=lambda: NOW), ())
            for run_id in ("empty-init", "events-empty-init", "manifest-init", "active-init"):
                self.assertTrue((root / ".run-recovery" / run_id).is_dir())
                self.assertFalse((root / "runs" / run_id).exists())

    def test_live_preflight_freezes_exact_static_v12_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root)
            deps = default_dependencies()
            arguments = IncrementalArguments(
                mode="incremental", source_mode="live", snapshot_root=None,
                artifacts_root=root, config_root=REPO / "config" / "phase1",
                run_id="live-unit", release_id="live-unit-release",
            )
            prepared = prepare_incremental(
                arguments, clock=lambda: NOW, build_request_plan=deps.build_request_plan,
                load_source_catalog=deps.load_source_catalog,
            )
            self.assertEqual(prepared.manifest["run_schema_version"], "1.3.0")
            self.assertEqual(len(prepared.manifest["request_plan"]), 4)
            self.assertEqual([row["method"] for row in prepared.manifest["request_plan"]], ["GET"] * 4)
            self.assertEqual([row["request_kind"] for row in prepared.manifest["request_plan"]], ["history"] * 4)
            self.assertTrue(all("child_authorization" not in row for row in prepared.manifest["request_plan"]))

    def test_stale_run_recovery_skips_active_owner_then_interrupts_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id = "stale-unit"
            run = root / "runs" / run_id
            run.mkdir(parents=True)
            manifest = json.loads((REPO / "tests" / "phase1" / "fixtures" / "spec" / "valid" / "run-manifest.json").read_text(encoding="utf-8"))
            manifest.update({"run_id": run_id, "artifacts_root": str(root)})
            for index, request in enumerate(manifest["request_plan"], 1):
                request["request_id"] = f"request-unit-{index}"
            write_once_json(run / "run-manifest.json", manifest)
            events = EventLog(run / "events.jsonl", run_id, lambda: NOW)
            events.append("run_planned")
            events.append("run_started")
            lock = RunLock(root, run_id).acquire()
            try:
                self.assertEqual(recover_stale_runs(root, clock=lambda: NOW), ())
            finally:
                lock.release()
            self.assertEqual(recover_stale_runs(root, clock=lambda: NOW), (run_id,))
            rows = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[-1]["event_type"], "run_interrupted")
            self.assertEqual(json.loads((run / "run-result.json").read_text(encoding="utf-8"))["status"], "interrupted")
            frozen = {path.relative_to(run).as_posix(): path.read_bytes() for path in run.rglob("*") if path.is_file()}
            self.assertEqual(recover_stale_runs(root, clock=lambda: NOW), ())
            self.assertEqual(recover_stale_runs(root, clock=lambda: NOW), ())
            self.assertEqual(frozen, {path.relative_to(run).as_posix(): path.read_bytes() for path in run.rglob("*") if path.is_file()})

    def test_live_no_change_executes_exact_four_request_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root)
            source_run = REPO / "artifacts" / "phase-1" / "runs" / "p1-baseline-v1"
            shutil.copytree(source_run, root / "runs" / "p1-baseline-v1")
            draws = [json.loads(line) for line in (root / "releases" / "baseline-v1" / "draws.jsonl").read_text(encoding="utf-8").splitlines()]
            latest = {game: max((row for row in draws if row["game"] == game), key=lambda row: row["issue_id"]) for game in ("ssq", "dlt")}

            def fake_fetch(request, policy, raw_root, throttle_root, **kwargs):
                body = request["request_id"].encode()
                path = raw_root / f"{request['request_id']}.raw"
                path.write_bytes(body)
                return {"raw_path": path, "raw_sha256": hashlib.sha256(body).hexdigest(), "url": request["url"]}

            def fake_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
                draw = latest[request["game"]]
                observation = {
                    "observation_schema_version": "1.0.0", "observation_id": "",
                    "source_id": request["source_id"], "publisher_id": publisher_id,
                    "game": request["game"], "raw_issue_id": draw["issue_id"], "issue_id": draw["issue_id"],
                    "draw_date_local": draw["draw_date_local"], "front_numbers": draw["front_numbers"],
                    "back_numbers": draw["back_numbers"], "source_url": request["url"],
                    "captured_at_utc": request["provenance"]["captured_at_utc"],
                    "raw_ref": request["provenance"]["raw_ref"], "raw_sha256": request["provenance"]["raw_sha256"],
                    "parser_id": parser_id, "parser_version": parser_version,
                    "core_fact_profile": "phase0-core-fact-v1", "core_fact_sha256": "", "parse_status": "parsed",
                }
                observation["core_fact_sha256"] = core_fact_sha256(observation)
                observation["observation_id"] = make_observation_id(
                    observation["source_id"], observation["game"], observation["issue_id"],
                    observation["raw_sha256"], parser_version,
                )
                return [observation]

            arguments = IncrementalArguments(
                mode="incremental", source_mode="live", snapshot_root=None, artifacts_root=root,
                config_root=REPO / "config" / "phase1", run_id="live-no-change", release_id="unused-release",
            )
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=fake_fetch), \
                 patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=fake_parse):
                code, result = execute_incremental(arguments)
            self.assertEqual((code, result["status"]), (0, "no_change"), result)
            self.assertEqual(set(result["deterministic_artifact_hashes"]), {
                "run_manifest", "events", "observations", "reconciliation",
                "candidate_draws", "quality_report",
            })
            run_root = root / "runs" / "live-no-change"
            inventory = json.loads((run_root / "hashes.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {item["path"] for item in inventory["entries"]},
                {path.relative_to(root).as_posix() for path in run_root.rglob("*") if path.is_file() and path.name != "hashes.json"},
            )
            events = [json.loads(line) for line in (root / "runs" / "live-no-change" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(sum(row["event_type"] == "request_started" for row in events), 4)
            self.assertNotIn("request_discovered", {row["event_type"] for row in events})

    def test_offline_replay_reparses_without_publication_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root)
            shutil.copytree(
                REPO / "artifacts" / "phase-1" / "runs" / "p1-baseline-v1",
                root / "runs" / "p1-baseline-v1",
            )
            source_run = root / "runs" / "p1-baseline-v1"
            local_config = source_run / "config" / "phase1"
            local_config.mkdir(parents=True)
            for name in ("source-catalog.json", "collection-policy.json"):
                shutil.copyfile(REPO / "config" / "phase1" / name, local_config / name)
            hashes_path = source_run / "hashes.json"
            hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
            for path in sorted(local_config.iterdir()):
                hashes["entries"].append({
                    "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size_bytes": path.stat().st_size,
                    "role": "config",
                })
            hashes["entries"].sort(key=lambda item: item["path"])
            atomic_write_json(hashes_path, hashes)
            pointer_before = (root / "current-release.json").read_bytes()
            release_before = (root / "releases" / "baseline-v1" / "manifest.json").read_bytes()
            code, result = execute_replay(
                artifacts_root=root, source_run_id="p1-baseline-v1",
                run_id="replay-unit", offline=True,
            )
            self.assertEqual((code, result["status"]), (0, "no_change"), result)
            self.assertEqual((root / "current-release.json").read_bytes(), pointer_before)
            self.assertEqual((root / "releases" / "baseline-v1" / "manifest.json").read_bytes(), release_before)
            self.assertFalse((root / "releases" / "replay-unit").exists())

    def test_live_quality_blocked_reports_authoritative_unresolved_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root)
            shutil.copytree(
                REPO / "artifacts" / "phase-1" / "runs" / "p1-baseline-v1",
                root / "runs" / "p1-baseline-v1",
            )
            draws = [
                json.loads(line)
                for line in (root / "releases" / "baseline-v1" / "draws.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            latest_dlt = max((row for row in draws if row["game"] == "dlt"), key=lambda row: row["issue_id"])
            new_facts = {
                "ssq": {"issue_id": "2026086", "draw_date_local": "2026-07-28", "front_numbers": [1, 2, 3, 4, 5, 6], "back_numbers": [1]},
                "dlt": {"issue_id": "2026084", "draw_date_local": "2026-07-29", "front_numbers": [1, 2, 3, 4, 5], "back_numbers": [1, 2]},
            }

            def fake_fetch(request, policy, raw_root, throttle_root, **kwargs):
                body = request["request_id"].encode()
                path = raw_root / f"{request['request_id']}.raw"
                path.write_bytes(body)
                return {"raw_path": path, "raw_sha256": hashlib.sha256(body).hexdigest(), "url": request["url"]}

            def fake_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
                fact = (
                    latest_dlt
                    if request["game"] == "dlt" and request["source_id"] == "ydniu"
                    else new_facts[request["game"]]
                )
                observation = {
                    "observation_schema_version": "1.0.0", "observation_id": "",
                    "source_id": request["source_id"], "publisher_id": publisher_id, "game": request["game"],
                    "raw_issue_id": fact["issue_id"], "issue_id": fact["issue_id"],
                    "draw_date_local": fact["draw_date_local"], "front_numbers": fact["front_numbers"],
                    "back_numbers": fact["back_numbers"], "source_url": request["url"],
                    "captured_at_utc": request["provenance"]["captured_at_utc"],
                    "raw_ref": request["provenance"]["raw_ref"], "raw_sha256": request["provenance"]["raw_sha256"],
                    "parser_id": parser_id, "parser_version": parser_version,
                    "core_fact_profile": "phase0-core-fact-v1", "core_fact_sha256": "", "parse_status": "parsed",
                }
                observation["core_fact_sha256"] = core_fact_sha256(observation)
                observation["observation_id"] = make_observation_id(
                    observation["source_id"], observation["game"], observation["issue_id"],
                    observation["raw_sha256"], parser_version,
                )
                return [observation]

            pointer_before = (root / "current-release.json").read_bytes()
            release_before = (root / "releases" / "baseline-v1" / "manifest.json").read_bytes()
            arguments = IncrementalArguments(
                mode="incremental", source_mode="live", snapshot_root=None, artifacts_root=root,
                config_root=REPO / "config" / "phase1", run_id="live-quality-blocked", release_id="must-not-publish",
            )
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=fake_fetch), \
                 patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=fake_parse):
                code, result = execute_incremental(arguments)
            self.assertEqual((code, result["status"]), (2, "rejected"), result)
            self.assertTrue(
                any("incremental_quality_blocked.json" in ref for ref in result["error_refs"]),
                result,
            )
            self._assert_live_incremental_count_parity(root, "live-quality-blocked", result)
            self.assertGreater(result["candidate_stats"]["unresolved"], 0)
            self.assertEqual(result["request_stats"]["succeeded"], 4)
            self.assertEqual((root / "current-release.json").read_bytes(), pointer_before)
            self.assertEqual((root / "releases" / "baseline-v1" / "manifest.json").read_bytes(), release_before)
            self.assertFalse((root / "releases" / "must-not-publish").exists())

    def test_live_publish_is_accepted_by_dynamic_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root)
            shutil.copytree(
                REPO / "artifacts" / "phase-1" / "runs" / "p1-baseline-v1",
                root / "runs" / "p1-baseline-v1",
            )
            facts = {
                "ssq": {"issue_id": "2026086", "draw_date_local": "2026-07-28", "front_numbers": [1, 2, 3, 4, 5, 6], "back_numbers": [1]},
                "dlt": {"issue_id": "2026084", "draw_date_local": "2026-07-29", "front_numbers": [1, 2, 3, 4, 5], "back_numbers": [1, 2]},
            }

            def fake_fetch(request, policy, raw_root, throttle_root, **kwargs):
                body = request["request_id"].encode()
                path = raw_root / f"{request['request_id']}.raw"
                path.write_bytes(body)
                return {"raw_path": path, "raw_sha256": hashlib.sha256(body).hexdigest(), "url": request["url"]}

            def fake_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
                fact = facts[request["game"]]
                observation = {
                    "observation_schema_version": "1.0.0", "observation_id": "",
                    "source_id": request["source_id"], "publisher_id": publisher_id, "game": request["game"],
                    "raw_issue_id": fact["issue_id"], "issue_id": fact["issue_id"],
                    "draw_date_local": fact["draw_date_local"], "front_numbers": fact["front_numbers"],
                    "back_numbers": fact["back_numbers"], "source_url": request["url"],
                    "captured_at_utc": request["provenance"]["captured_at_utc"],
                    "raw_ref": request["provenance"]["raw_ref"], "raw_sha256": request["provenance"]["raw_sha256"],
                    "parser_id": parser_id, "parser_version": parser_version,
                    "core_fact_profile": "phase0-core-fact-v1", "core_fact_sha256": "", "parse_status": "parsed",
                }
                observation["core_fact_sha256"] = core_fact_sha256(observation)
                observation["observation_id"] = make_observation_id(
                    observation["source_id"], observation["game"], observation["issue_id"],
                    observation["raw_sha256"], parser_version,
                )
                return [observation]

            arguments = IncrementalArguments(
                mode="incremental", source_mode="live", snapshot_root=None, artifacts_root=root,
                config_root=REPO / "config" / "phase1", run_id="live-publish", release_id="live-release",
            )
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=fake_fetch), \
                 patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=fake_parse):
                code, result = execute_incremental(arguments)
            self.assertEqual((code, result["status"], result["release_id"]), (0, "published", "live-release"), result)
            self._assert_live_incremental_count_parity(root, "live-publish", result)
            verify_code, report = execute_verify(artifacts_root=root, release_id="live-release")
            self.assertEqual((verify_code, report.get("status"), report.get("profile")), (0, "PASS", "incremental-dynamic"), report)
            journal_path = root / ".publication-journals" / "live-publish.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            journal["state"] = "RESULT_WRITTEN"
            journal["recovery"] = None
            atomic_write_json(journal_path, journal)
            recovered = recover_stale_publications(root, clock=lambda: NOW)
            self.assertEqual(recovered.rolled_forward_run_ids, ("live-publish",))
            self.assertEqual(json.loads(journal_path.read_text(encoding="utf-8"))["state"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
