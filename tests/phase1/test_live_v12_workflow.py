from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lottery_data.models import ContractViolation
from lottery_data.parsers.gdlottery_history import parse as parse_gd_history
from lottery_data.serialization import core_fact_sha256, make_observation_id, sha256_file
from lottery_data.steps.incremental_engine import build_incremental_release as PRODUCT_BUILD_INCREMENTAL_RELEASE
from lottery_data.steps.live_policy import LIVE_POLICY_V13_SHA256, LivePolicyError
from lottery_data.steps.parse import parse_versioned_raw as PRODUCT_PARSE_VERSIONED_RAW
from lottery_data.steps.preflight import IncrementalArguments, prepare_incremental
from lottery_data.steps.verify import verify_release
from lottery_data.workflow import default_dependencies, execute_incremental


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-08-03T00:00:00Z"
GD_FIXTURE = ROOT / "tests" / "phase1" / "fixtures" / "real" / "gd-game-number-history-20260803.json"


class LiveV12WorkflowTests(unittest.TestCase):
    def _publication_fixture(self, root: Path, *, with_predecessor_run: bool = False) -> None:
        source = ROOT / "artifacts" / "phase-1"
        shutil.copyfile(source / "current-release.json", root / "current-release.json")
        shutil.copytree(source / "releases" / "baseline-v1", root / "releases" / "baseline-v1")
        shutil.copytree(source / "baseline-v1", root / "baseline-v1")
        if with_predecessor_run:
            shutil.copytree(source / "runs" / "p1-baseline-v1", root / "runs" / "p1-baseline-v1")

    def _arguments(self, root: Path, run_id: str, release_id: str = "unused-release") -> IncrementalArguments:
        return IncrementalArguments(
            mode="incremental", source_mode="live", snapshot_root=None,
            artifacts_root=root, config_root=ROOT / "config" / "phase1",
            run_id=run_id, release_id=release_id,
        )

    @staticmethod
    def _observation(request: dict, fact: dict, raw_path: Path, publisher_id: str,
                     parser_id: str, parser_version: str) -> dict:
        observation = {
            "observation_schema_version": "1.0.0", "observation_id": "",
            "source_id": request["source_id"], "publisher_id": publisher_id,
            "game": request["game"], "raw_issue_id": fact.get("raw_issue_id", fact["issue_id"]),
            "issue_id": fact["issue_id"], "draw_date_local": fact["draw_date_local"],
            "front_numbers": fact["front_numbers"], "back_numbers": fact["back_numbers"],
            "source_url": request["url"], "captured_at_utc": request["provenance"]["captured_at_utc"],
            "raw_ref": request["provenance"]["raw_ref"], "raw_sha256": request["provenance"]["raw_sha256"],
            "parser_id": parser_id, "parser_version": parser_version,
            "core_fact_profile": "phase0-core-fact-v1", "core_fact_sha256": "", "parse_status": "parsed",
        }
        observation["core_fact_sha256"] = core_fact_sha256(observation)
        observation["observation_id"] = make_observation_id(
            observation["source_id"], observation["game"], observation["issue_id"],
            observation["raw_sha256"], parser_version,
        )
        return observation

    def _fake_fetch(self, request, policy, raw_root, throttle_root, **kwargs):
        body = GD_FIXTURE.read_bytes() if request["source_id"] == "gdlottery" else request["request_id"].encode()
        path = raw_root / f"{request['request_id']}.raw"
        path.write_bytes(body)
        return {"raw_path": path, "raw_sha256": hashlib.sha256(body).hexdigest(), "url": request["url"]}

    def test_preflight_and_success_use_exact_v12_static_plan_and_persist_raw_before_parse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root, with_predecessor_run=True)
            arguments = self._arguments(root, "live-v12-success", "live-v12-release")
            deps = default_dependencies()
            prepared = prepare_incremental(
                arguments, clock=lambda: NOW, build_request_plan=deps.build_request_plan,
                load_source_catalog=deps.load_source_catalog,
            )
            self.assertEqual(prepared.manifest["run_schema_version"], "1.3.0")
            self.assertEqual(prepared.manifest["config_files"][0]["sha256"], LIVE_POLICY_V13_SHA256)
            self.assertEqual(len(prepared.request_plan), 4)
            self.assertTrue(all(row["request_kind"] == "history" for row in prepared.request_plan))
            self.assertTrue(all("child_authorization" not in row for row in prepared.request_plan))

            gd_facts = parse_gd_history(GD_FIXTURE.read_bytes(), "dlt")
            baseline_draws = [
                json.loads(line) for line in (root / "releases" / "baseline-v1" / "draws.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            latest_ssq = max((row for row in baseline_draws if row["game"] == "ssq"), key=lambda row: row["issue_id"])
            latest_dlt = max((row for row in baseline_draws if row["game"] == "dlt"), key=lambda row: row["issue_id"])
            ssq_history = sorted(
                (row for row in baseline_draws if row["game"] == "ssq"),
                key=lambda row: row["issue_id"],
            )[-30:]
            parsed_paths: list[Path] = []

            def fake_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
                parsed_paths.append(raw_path)
                self.assertTrue(raw_path.is_file())
                self.assertEqual(sha256_file(raw_path), request["provenance"]["raw_sha256"])
                self.assertIn("/sha256/", "/" + raw_path.relative_to(root / "runs" / "live-v12-success").as_posix())
                if request["source_id"] == "gdlottery":
                    return PRODUCT_PARSE_VERSIONED_RAW(
                        request, raw_path, publisher_id=publisher_id,
                        parser_id=parser_id, parser_version=parser_version,
                    )
                facts = gd_facts if request["game"] == "dlt" else ssq_history
                return [self._observation(request, fact, raw_path, publisher_id, parser_id, parser_version) for fact in facts]

            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=self._fake_fetch), \
                 patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=fake_parse), \
                 patch("lottery_data.steps.incremental_engine.build_incremental_release",
                       wraps=PRODUCT_BUILD_INCREMENTAL_RELEASE) as engine:
                code, result = execute_incremental(arguments)

            self.assertEqual((code, result["status"], result["release_id"]), (0, "published", "live-v12-release"), result)
            self.assertEqual(result["request_stats"], {"planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0})
            self.assertEqual(len(parsed_paths), 4)
            events = [json.loads(line) for line in (root / "runs" / "live-v12-success" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["event_schema_version"] for row in events], ["1.3.0"] * len(events))
            self.assertNotIn("request_discovered", {row["event_type"] for row in events})
            self.assertEqual(sum(row["event_type"] == "request_started" for row in events), 4)
            self.assertEqual(sum(row["event_type"] == "request_succeeded" for row in events), 4)
            quality = json.loads((root / "runs" / "live-v12-success" / "quality-report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["decision"], "PASS")
            counts = quality["deterministic"]["counts"]
            self.assertEqual(counts, {
                "draws": 403, "release_observations": 806, "run_observations": 100,
                "added": 3, "revised": 0, "unchanged": 37, "conflict": 0,
                "unresolved": 0, "recheck_attempted": 37, "recheck_complete": 37,
                "recheck_deferred": 0,
            })

            supplied = list(engine.call_args.kwargs["new_observations"])
            self.assertEqual(len(supplied), 100)
            current_core = {
                (row["game"], row["issue_id"]): row["core_fact_sha256"] for row in baseline_draws
            }
            expected_pairs = {"ssq": {"ydniu", "swlc"}, "dlt": {"ydniu", "gdlottery"}}
            for draw in (latest_ssq, latest_dlt):
                pair = [row for row in supplied if (row["game"], row["issue_id"]) == (draw["game"], draw["issue_id"])]
                self.assertEqual({row["source_id"] for row in pair}, expected_pairs[draw["game"]])
                self.assertEqual(len(pair), 2)
                self.assertTrue(all(row["core_fact_sha256"] == current_core[(draw["game"], draw["issue_id"])] for row in pair))

            run_root = root / "runs" / "live-v12-success"
            run_observations = [
                json.loads(line) for line in (run_root / "observations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(run_observations), 100)
            self.assertEqual(
                {row["observation_id"] for row in run_observations},
                {row["observation_id"] for row in supplied},
            )
            self.assertEqual(
                sha256_file(run_root / "observations.jsonl"),
                quality["deterministic"]["output_hashes"]["run_observations"],
            )
            reconciliation = [
                json.loads(line) for line in (run_root / "reconciliation.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(reconciliation), 40)
            existing_complete = [
                row for row in reconciliation if (row["game"], row["issue_id"]) in current_core
            ]
            self.assertEqual(len(existing_complete), 37)
            self.assertTrue(all(
                row["decision"] == "verified" and len(row["selected_observation_ids"]) == 2
                for row in existing_complete
            ))
            self.assertEqual(
                {(row["game"], row["issue_id"]) for row in reconciliation if (row["game"], row["issue_id"]) not in current_core},
                {("dlt", "2026084"), ("dlt", "2026085"), ("dlt", "2026086")},
            )
            published = [
                json.loads(line) for line in (root / "releases" / "live-v12-release" / "draws.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue({("dlt", issue) for issue in ("2026084", "2026085", "2026086")} <= {
                (row["game"], row["issue_id"]) for row in published
            })
            verification = verify_release(root, "live-v12-release")
            self.assertEqual((verification["status"], verification["profile"]), ("PASS", "incremental-dynamic"))

    def test_complete_same_core_pairs_without_new_issue_are_truthful_no_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root, with_predecessor_run=True)
            baseline_draws = [
                json.loads(line) for line in (root / "releases" / "baseline-v1" / "draws.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            latest = {
                game: max((row for row in baseline_draws if row["game"] == game), key=lambda row: row["issue_id"])
                for game in ("ssq", "dlt")
            }

            def fake_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
                return [self._observation(
                    request, latest[request["game"]], raw_path, publisher_id, parser_id, parser_version,
                )]

            pointer_before = (root / "current-release.json").read_bytes()
            releases_before = {
                path.relative_to(root / "releases").as_posix(): sha256_file(path)
                for path in (root / "releases").rglob("*") if path.is_file()
            }
            arguments = self._arguments(root, "live-v12-same-core", "must-not-publish")
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=self._fake_fetch), \
                 patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=fake_parse), \
                 patch("lottery_data.steps.incremental_engine.build_incremental_release",
                       wraps=PRODUCT_BUILD_INCREMENTAL_RELEASE) as engine:
                code, result = execute_incremental(arguments)

            self.assertEqual((code, result["status"], result["release_id"]), (0, "no_change", None), result)
            supplied = list(engine.call_args.kwargs["new_observations"])
            self.assertEqual(len(supplied), 4)
            quality = json.loads((root / "runs" / "live-v12-same-core" / "quality-report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["deterministic"]["counts"], {
                "draws": 400, "release_observations": 800, "run_observations": 4,
                "added": 0, "revised": 0, "unchanged": 2, "conflict": 0,
                "unresolved": 0, "recheck_attempted": 2, "recheck_complete": 2,
                "recheck_deferred": 0,
            })
            run_root = root / "runs" / "live-v12-same-core"
            run_observations = [
                json.loads(line) for line in (run_root / "observations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(run_observations), 4)
            self.assertEqual(
                sha256_file(run_root / "observations.jsonl"),
                quality["deterministic"]["output_hashes"]["run_observations"],
            )
            reconciliation = [
                json.loads(line) for line in (run_root / "reconciliation.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(reconciliation), 2)
            self.assertTrue(all(
                row["decision"] == "verified" and len(row["selected_observation_ids"]) == 2
                for row in reconciliation
            ))
            self.assertEqual((root / "current-release.json").read_bytes(), pointer_before)
            self.assertEqual({
                path.relative_to(root / "releases").as_posix(): sha256_file(path)
                for path in (root / "releases").rglob("*") if path.is_file()
            }, releases_before)
            self.assertFalse((root / "releases" / "must-not-publish").exists())

    def test_network_rejection_closes_started_request_and_keeps_four_planned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root)
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=LivePolicyError(
                "authentication_cookie_or_challenge_required", "forced non-retryable failure",
                stage="runtime", exit_code=3,
            )):
                code, result = execute_incremental(self._arguments(root, "live-v12-network"))
            self.assertEqual((code, result["status"]), (3, "rejected"), result)
            self.assertEqual(result["request_stats"], {"planned": 4, "started": 1, "succeeded": 0, "failed": 1, "not_started": 3})
            self.assertFalse((root / "releases" / "unused-release").exists())

    def test_retryable_transport_failure_is_audited_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root, with_predecessor_run=True)
            baseline_draws = [
                json.loads(line) for line in (root / "releases" / "baseline-v1" / "draws.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            latest = {
                game: max((row for row in baseline_draws if row["game"] == game), key=lambda row: row["issue_id"])
                for game in ("ssq", "dlt")
            }
            calls = 0

            def flaky_fetch(request, policy, raw_root, throttle_root, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise LivePolicyError(
                        "dns_timeout_tls_or_required_source_unavailable", "temporary unavailable",
                        stage="runtime", exit_code=3, retryable=True,
                    )
                return self._fake_fetch(request, policy, raw_root, throttle_root, **kwargs)

            def fake_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
                return [self._observation(
                    request, latest[request["game"]], raw_path, publisher_id, parser_id, parser_version,
                )]

            arguments = self._arguments(root, "live-v13-retry-success", "must-not-publish")
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=flaky_fetch), \
                 patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=fake_parse), \
                 patch("lottery_data.workflow.time.sleep") as sleeper:
                code, result = execute_incremental(arguments)

            self.assertEqual((code, result["status"]), (0, "no_change"), result)
            self.assertEqual(result["request_stats"], {"planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0})
            sleeper.assert_called_once_with(2.0)
            events = [json.loads(line) for line in (root / "runs" / "live-v13-retry-success" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            first = [row for row in events if row.get("request_id") == "live-ydniu-ssq-history"]
            self.assertEqual([(row["event_type"], row["attempt"]) for row in first], [
                ("request_started", 1), ("request_failed", 1),
                ("request_started", 2), ("request_succeeded", 2),
            ])
            error_ref = first[1]["error_detail_ref"]
            self.assertEqual(error_ref, "runs/live-v13-retry-success/errors/live-ydniu-ssq-history/attempt-1.json")
            self.assertTrue((root / error_ref).is_file())

    def test_retryable_transport_failure_stops_after_two_audited_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root)
            failure = LivePolicyError(
                "dns_timeout_tls_or_required_source_unavailable", "temporary unavailable",
                stage="runtime", exit_code=3, retryable=True,
            )
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=failure), \
                 patch("lottery_data.workflow.time.sleep") as sleeper:
                code, result = execute_incremental(self._arguments(root, "live-v13-retry-exhausted"))
            self.assertEqual((code, result["status"]), (3, "rejected"), result)
            self.assertEqual(result["request_stats"], {"planned": 4, "started": 1, "succeeded": 0, "failed": 1, "not_started": 3})
            sleeper.assert_called_once_with(2.0)
            events = [json.loads(line) for line in (root / "runs" / "live-v13-retry-exhausted" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            attempts = [
                (row["event_type"], row["attempt"]) for row in events
                if row.get("request_id") == "live-ydniu-ssq-history"
            ]
            self.assertEqual(attempts, [
                ("request_started", 1), ("request_failed", 1),
                ("request_started", 2), ("request_failed", 2),
            ])
            for attempt in (1, 2):
                self.assertTrue((root / "runs" / "live-v13-retry-exhausted" / "errors" /
                                 "live-ydniu-ssq-history" / f"attempt-{attempt}.json").is_file())

    def test_parser_rejection_preserves_content_addressed_raw_and_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root)
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=self._fake_fetch), \
                 patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=ContractViolation(
                     "incremental-transform", "forced parser failure",
                 )):
                code, result = execute_incremental(self._arguments(root, "live-v12-parser"))
            self.assertEqual((code, result["status"]), (2, "rejected"), result)
            self.assertEqual(result["request_stats"], {"planned": 4, "started": 1, "succeeded": 0, "failed": 1, "not_started": 3})
            raw_files = list((root / "runs" / "live-v12-parser" / "raw").rglob("*.raw"))
            self.assertEqual(len(raw_files), 1)
            self.assertEqual(raw_files[0].stem, sha256_file(raw_files[0]))
            self.assertFalse((root / "releases" / "unused-release").exists())

    def test_quality_rejection_occurs_after_all_four_static_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._publication_fixture(root, with_predecessor_run=True)
            facts = {
                "ssq": {"issue_id": "2026086", "draw_date_local": "2026-07-28", "front_numbers": [1, 2, 3, 4, 5, 6], "back_numbers": [1]},
                "dlt": {"issue_id": "2026084", "draw_date_local": "2026-07-29", "front_numbers": [1, 2, 3, 4, 5], "back_numbers": [1, 2]},
            }

            def fake_parse(request, raw_path, *, publisher_id, parser_id, parser_version):
                fact = dict(facts[request["game"]])
                if request["source_id"] == "ydniu" and request["game"] == "dlt":
                    fact["front_numbers"] = [6, 7, 8, 9, 10]
                return [self._observation(request, fact, raw_path, publisher_id, parser_id, parser_version)]

            pointer_before = (root / "current-release.json").read_bytes()
            with patch("lottery_data.steps.live.fetch_to_raw", side_effect=self._fake_fetch), \
                 patch("lottery_data.steps.parse.parse_versioned_raw", side_effect=fake_parse):
                code, result = execute_incremental(self._arguments(root, "live-v12-quality", "must-not-publish"))
            self.assertEqual((code, result["status"]), (2, "rejected"), result)
            self.assertEqual(result["request_stats"], {"planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0})
            self.assertGreater(result["change_stats"]["conflict"] + result["candidate_stats"]["unresolved"], 0)
            self.assertEqual((root / "current-release.json").read_bytes(), pointer_before)
            self.assertFalse((root / "releases" / "must-not-publish").exists())


if __name__ == "__main__":
    unittest.main()
