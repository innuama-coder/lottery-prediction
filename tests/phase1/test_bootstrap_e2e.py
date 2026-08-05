from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from lottery_data.serialization import canonical_jsonl_bytes, core_fact_sha256, make_observation_id, make_revision_id, sha256_bytes, sha256_file  # noqa: E402
from lottery_data.steps.preflight import BootstrapArguments, prepare_bootstrap  # noqa: E402
from lottery_data.workflow import WorkflowDependencies, execute_bootstrap  # noqa: E402
from lottery_data.steps.events import EventLog  # noqa: E402
import lottery_data.workflow as workflow_module  # noqa: E402


NOW = "2026-08-02T03:00:00Z"


def _catalog(_: Path) -> dict[str, Any]:
    return {"sources": [
        {"source_id": "s1", "publisher_id": "publisher-one"},
        {"source_id": "s2", "publisher_id": "publisher-two"},
    ]}


def _plan(_: Path, __: Sequence[str], ___: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"request_id": "s1-ssq-p001", "sequence": 1, "source_id": "s1", "publisher_id": "publisher-one", "game": "ssq", "method": "SNAPSHOT", "url": "snapshot://s1/ssq/1", "input_ref": "raw/s1/ssq/page-001.html"},
        {"request_id": "s2-ssq-p001", "sequence": 2, "source_id": "s2", "publisher_id": "publisher-two", "game": "ssq", "method": "SNAPSHOT", "url": "snapshot://s2/ssq/1", "input_ref": "raw/s2/ssq/page-001.html"},
    ]


def _materialize(request: dict[str, Any], _: Path, raw_root: Path) -> dict[str, Any]:
    relative = Path(request["input_ref"]).relative_to("raw")
    raw_path = raw_root / relative
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(request["source_id"].encode("ascii"))
    return {
        "raw_path": raw_path, "raw_ref": request["input_ref"], "raw_sha256": ("a" if request["source_id"] == "s1" else "b") * 64,
        "source_id": request["source_id"], "publisher_id": request["publisher_id"], "game": "ssq",
        "url": "https://example.test/ssq", "captured_at_utc": NOW, "request_id": request["request_id"],
    }


def _parse(request: dict[str, Any], _: Path, *, publisher_id: str) -> list[dict[str, Any]]:
    raw_sha = request["provenance"]["raw_sha256"]
    value = {
        "observation_schema_version": "1.0.0", "observation_id": "", "source_id": request["source_id"],
        "publisher_id": publisher_id, "game": "ssq", "raw_issue_id": "2026085", "issue_id": "2026085",
        "draw_date_local": "2026-07-26", "front_numbers": [6, 9, 13, 17, 24, 28], "back_numbers": [15],
        "source_url": "https://example.test/ssq", "captured_at_utc": NOW, "raw_ref": request["input_ref"],
        "raw_sha256": raw_sha, "parser_id": f"phase1-{request['source_id']}-parser", "parser_version": "1.0.0",
        "core_fact_profile": "phase0-core-fact-v1", "core_fact_sha256": "", "parse_status": "parsed",
    }
    value["core_fact_sha256"] = core_fact_sha256(value)
    value["observation_id"] = make_observation_id(value["source_id"], "ssq", "2026085", raw_sha, "1.0.0")
    return [value]


def _reconcile(_: Path, observations: Sequence[Mapping[str, Any]], __: Mapping[str, Any], ___: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected = [dict(value) for value in observations]
    return [{
        "reconciliation_schema_version": "1.0.0", "game": "ssq", "issue_id": "2026085", "decision": "verified",
        "core_fact_sha256": selected[0]["core_fact_sha256"], "selected_observation_ids": [value["observation_id"] for value in selected],
        "agreeing_observation_ids": sorted(value["observation_id"] for value in selected), "missing_source_ids": [],
        "dissenting_observation_ids": [], "fallback_rule_id": None, "reason_codes": ["TEST_TWO_PUBLISHER"],
    }], selected


def _draws(_: Sequence[Mapping[str, Any]], observations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    first = observations[0]
    revision = make_revision_id("ssq", "2026085", first["core_fact_sha256"], None)
    return [{
        "record_schema_version": "1.0.0", "game": "ssq", "issue_id": "2026085", "draw_date_local": "2026-07-26",
        "front_numbers": [6, 9, 13, 17, 24, 28], "back_numbers": [15], "status": "verified",
        "core_fact_profile": "phase0-core-fact-v1", "core_fact_sha256": first["core_fact_sha256"],
        "evidence_links": [{"source_id": value["source_id"], "publisher_id": value["publisher_id"], "observation_id": value["observation_id"], "raw_ref": value["raw_ref"], "raw_sha256": value["raw_sha256"]} for value in observations],
        "revision_id": revision, "supersedes_revision_id": None, "knowledge_class": "retrospective_current_view", "available_at_utc": None,
    }]


def _quality(**values: Any) -> dict[str, Any]:
    return {"quality_schema_version": "1.0.0", "run_id": values["run_id"], "decision": "PASS", "deterministic": {"counts": {"parsed_observations": values["audit"]["parsed_observations"], "selected_observations": len(values["observations"])}, "checks": [], "input_hashes": dict(values["input_hashes"]), "output_hashes": dict(values["output_hashes"]), "blocking_reason_codes": []}, "generated_at_utc": values["generated_at_utc"]}


def _dependencies(reconcile_func: Any = _reconcile) -> WorkflowDependencies:
    def transform(**values: Any) -> SimpleNamespace:
        observations_all = [dict(value) for value in values["observations_all"]]
        reconciliation, selected = reconcile_func(values["snapshot_root"], observations_all, values["source_catalog"], values["collection_policy"])
        draws = _draws(reconciliation, selected)
        output_hashes = {
            "draws": sha256_bytes(canonical_jsonl_bytes(draws, sort_keys=("game", "issue_id", "revision_id"))),
            "run_observations": sha256_bytes(canonical_jsonl_bytes(observations_all, sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"))),
            "release_observations": sha256_bytes(canonical_jsonl_bytes(selected, sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"))),
            "reconciliation": sha256_bytes(canonical_jsonl_bytes(reconciliation, sort_keys=("game", "issue_id"))),
        }
        audit = {"parsed_observations": len(observations_all)}
        quality = _quality(
            run_id=values["run_id"], observations=selected, audit=audit,
            input_hashes=values["input_hashes"], output_hashes=output_hashes,
            generated_at_utc=values["generated_at_utc"],
        )
        return SimpleNamespace(
            observations_all=tuple(observations_all), observations_selected=tuple(selected),
            reconciliation=tuple(reconciliation), draws=tuple(draws),
            quality_report=quality, audit=audit, output_hashes=output_hashes,
        )

    return WorkflowDependencies(
        build_request_plan=_plan, load_source_catalog=_catalog,
        audit_snapshot=lambda _: {"canonical_sha256": "c" * 64, "capture_manifest_sha256": "d" * 64, "request_events_sha256": "e" * 64},
        materialize_request=_materialize, parse_raw=_parse,
        deduplicate_observations=lambda values: [dict(value) for value in values],
        validate_observations=lambda _: None, reconcile=reconcile_func, build_draw_records=_draws,
        build_quality_report=_quality, expected_reparsed_counts={"s1:ssq": 1, "s2:ssq": 1},
        transform_observations=transform, compare_no_change=lambda **_: {"status": "no_change"},
        verify_release=lambda **_: {"status": "PASS"}, clock=lambda: NOW,
    )


class BootstrapControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.snapshot = self.base / "20260802T025000Z"
        self.snapshot.mkdir()
        (self.snapshot / "artifact-hashes.json").write_text("{}\n", encoding="utf-8")
        self.config = self.base / "config"
        self.config.mkdir()
        shutil.copyfile(REPO / "config" / "phase1" / "source-catalog.json", self.config / "source-catalog.json")
        shutil.copyfile(REPO / "config" / "phase1" / "collection-policy.json", self.config / "collection-policy.json")
        self.artifacts = self.base / "artifacts"
        self.arguments = BootstrapArguments("bootstrap", "snapshot", self.snapshot, self.artifacts, self.config, "run-001", "release-001")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_atomic_bootstrap_publishes_release_and_pointer(self) -> None:
        code, result = execute_bootstrap(self.arguments, dependencies=_dependencies())
        self.assertEqual((code, result["status"], result["release_id"]), (0, "published", "release-001"))
        run = self.artifacts / "runs" / "run-001"
        release = self.artifacts / "releases" / "release-001"
        projection = self.artifacts / "release-001"
        self.assertTrue((run / "run-manifest.json").is_file())
        self.assertTrue((run / "run-result.json").is_file())
        self.assertTrue((run / "hashes.json").is_file())
        self.assertTrue((release / "manifest.json").is_file())
        for name in ("draws.jsonl", "observations.jsonl", "manifest.json", "quality-report.json", "hashes.json"):
            self.assertEqual((release / name).read_bytes(), (projection / name).read_bytes())
        pointer = json.loads((self.artifacts / "current-release.json").read_text(encoding="utf-8"))
        self.assertEqual(pointer["release_id"], "release-001")
        events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
        for source in ("s1", "s2"):
            start = next(i for i, event in enumerate(events) if event["event_type"] == "request_started" and event["source_id"] == source)
            terminal = next(i for i, event in enumerate(events) if event["event_type"] == "request_succeeded" and event["source_id"] == source)
            self.assertLess(start, terminal)
        self.assertEqual(events[-1]["event_type"], "run_published")

    def test_run_preserves_all_observations_while_release_contains_selected_evidence(self) -> None:
        base_parse = _parse

        def parse_with_unselected(request: dict[str, Any], raw: Path, *, publisher_id: str) -> list[dict[str, Any]]:
            rows = base_parse(request, raw, publisher_id=publisher_id)
            if request["source_id"] == "s1":
                extra = dict(rows[0])
                extra["parser_version"] = "1.0.1"
                extra["observation_id"] = make_observation_id(
                    extra["source_id"], extra["game"], extra["issue_id"], extra["raw_sha256"], extra["parser_version"]
                )
                rows.append(extra)
            return rows

        def reconcile_selected(
            snapshot: Path,
            observations: Sequence[Mapping[str, Any]],
            catalog: Mapping[str, Any],
            policy: Mapping[str, Any],
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            selected = [next(row for row in observations if row["source_id"] == source) for source in ("s1", "s2")]
            return _reconcile(snapshot, selected, catalog, policy)

        dependencies = replace(_dependencies(reconcile_selected), parse_raw=parse_with_unselected)
        code, result = execute_bootstrap(self.arguments, dependencies=dependencies)

        run_observations = (self.artifacts / "runs" / "run-001" / "observations.jsonl").read_text(encoding="utf-8").splitlines()
        release_observations_path = self.artifacts / "releases" / "release-001" / "observations.jsonl"
        release_observations = release_observations_path.read_text(encoding="utf-8").splitlines()
        release_manifest = json.loads((self.artifacts / "releases" / "release-001" / "manifest.json").read_text(encoding="utf-8"))
        run_quality_path = self.artifacts / "runs" / "run-001" / "quality-report.json"
        release_quality_path = self.artifacts / "releases" / "release-001" / "quality-report.json"
        quality = json.loads(run_quality_path.read_text(encoding="utf-8"))

        self.assertEqual((code, result["observation_stats"]["parsed"], result["observation_stats"]["valid"]), (0, 3, 2))
        self.assertEqual((len(run_observations), len(release_observations)), (3, 2))
        self.assertEqual(result["deterministic_artifact_hashes"]["observations"], sha256_file(self.artifacts / "runs" / "run-001" / "observations.jsonl"))
        self.assertEqual(release_manifest["observations_sha256"], sha256_file(release_observations_path))
        self.assertNotEqual(result["deterministic_artifact_hashes"]["observations"], release_manifest["observations_sha256"])
        self.assertEqual(quality["deterministic"]["counts"], {"parsed_observations": 3, "selected_observations": 2})
        self.assertEqual(set(quality["deterministic"]["output_hashes"]), {"draws", "reconciliation", "release_observations", "run_observations"})
        self.assertEqual(quality["deterministic"]["output_hashes"]["run_observations"], sha256_file(self.artifacts / "runs" / "run-001" / "observations.jsonl"))
        self.assertEqual(quality["deterministic"]["output_hashes"]["release_observations"], sha256_file(release_observations_path))
        self.assertEqual(run_quality_path.read_bytes(), release_quality_path.read_bytes())

    def test_preflight_default_resources_are_independent_of_cwd(self) -> None:
        outside = self.base / "outside-cwd"
        outside.mkdir()
        arguments = BootstrapArguments("bootstrap", "snapshot", self.snapshot, self.artifacts, None, "run-cwd", "release-cwd")
        original = Path.cwd()
        try:
            os.chdir(outside)
            prepared = prepare_bootstrap(
                arguments, clock=lambda: NOW, build_request_plan=_plan, load_source_catalog=_catalog,
            )
        finally:
            os.chdir(original)
        self.assertEqual(prepared.source_catalog_path, REPO / "config" / "phase1" / "source-catalog.json")
        self.assertRegex(prepared.manifest["pipeline_bundle_sha256"], r"^[0-9a-f]{64}$")

    def test_quality_failure_preserves_run_but_does_not_publish(self) -> None:
        dependencies = _dependencies()
        transform = dependencies.transform_observations

        def transform_with_failed_quality(**values: Any) -> SimpleNamespace:
            transformed = transform(**values)
            return SimpleNamespace(**{
                **transformed.__dict__,
                "quality_report": {**transformed.quality_report, "decision": "FAIL"},
            })

        dependencies = replace(dependencies, transform_observations=transform_with_failed_quality)
        code, result = execute_bootstrap(self.arguments, dependencies=dependencies)
        self.assertEqual((code, result["status"], result["release_id"]), (2, "rejected", None))
        self.assertTrue((self.artifacts / "runs" / "run-001" / "run-result.json").is_file())
        self.assertFalse((self.artifacts / "releases" / "release-001").exists())
        self.assertFalse((self.artifacts / "release-001").exists())
        self.assertFalse((self.artifacts / "current-release.json").exists())

    def test_lock_contention_returns_nonpersistent_interrupted_envelope(self) -> None:
        self.artifacts.mkdir()
        (self.artifacts / ".publish.lock").write_text("held", encoding="utf-8")
        code, result = execute_bootstrap(self.arguments, dependencies=_dependencies())
        self.assertEqual((code, result["exit_code"], result["status"]), (6, 6, "interrupted"))
        self.assertEqual(result["request_stats"]["started"], 0)
        for field in ("manifest_ref", "events_ref", "quality_report_ref"):
            self.assertNotIn(field, result)
        self.assertFalse((self.artifacts / "runs" / "run-001").exists())
        self.assertFalse((self.artifacts / "releases" / "release-001").exists())
        self.assertFalse((self.artifacts / "release-001").exists())
        self.assertFalse((self.artifacts / "current-release.json").exists())

    def test_request_file_failure_has_durable_terminal_and_no_publication(self) -> None:
        def missing(request: dict[str, Any], snapshot: Path, raw_root: Path) -> dict[str, Any]:
            raise FileNotFoundError(request["input_ref"])

        code, result = execute_bootstrap(self.arguments, dependencies=replace(_dependencies(), materialize_request=missing))
        self.assertEqual((code, result["status"], result["request_stats"]["failed"]), (5, "rejected", 1))
        events_path = self.artifacts / "runs" / "run-001" / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["event_type"] for event in events[-2:]], ["request_failed", "run_rejected"])
        self.assertFalse((self.artifacts / "releases" / "release-001").exists())
        self.assertFalse((self.artifacts / "release-001").exists())
        self.assertFalse((self.artifacts / "current-release.json").exists())

    def assert_post_publish_rolled_back(self, code: int, result: Mapping[str, Any]) -> None:
        self.assertNotEqual(code, 0)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse((self.artifacts / "current-release.json").exists())
        self.assertFalse((self.artifacts / "releases" / "release-001").exists())
        self.assertFalse((self.artifacts / "release-001").exists())
        run = self.artifacts / "runs" / "run-001"
        self.assertTrue((run / "recovery" / "published-release").is_dir())
        self.assertTrue((run / "recovery" / "published-projection").is_dir())
        events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        terminals = [event["event_type"] for event in events if event["event_type"].startswith("run_") and event["event_type"] not in {"run_planned", "run_started"}]
        self.assertEqual(terminals, ["run_rejected"])
        self.assertNotIn("run_published", [event["event_type"] for event in events])

    def test_run_published_event_failure_rolls_back_commit(self) -> None:
        original = EventLog.append

        def append_with_fault(log: EventLog, event_type: str, **kwargs: Any) -> dict[str, Any]:
            if event_type == "run_published":
                raise OSError("injected run_published failure")
            return original(log, event_type, **kwargs)

        with patch.object(EventLog, "append", new=append_with_fault):
            code, result = execute_bootstrap(self.arguments, dependencies=_dependencies())
        self.assert_post_publish_rolled_back(code, result)

    def test_result_write_failure_rolls_back_commit(self) -> None:
        original = workflow_module.write_once_json
        failed = False

        def write_with_fault(path: Path, value: Any) -> None:
            nonlocal failed
            if path.name == "run-result.json" and not failed:
                failed = True
                raise OSError("injected result write failure")
            original(path, value)

        with patch.object(workflow_module, "write_once_json", new=write_with_fault):
            code, result = execute_bootstrap(self.arguments, dependencies=_dependencies())
        self.assert_post_publish_rolled_back(code, result)

    def test_hash_finalize_failure_rolls_back_commit(self) -> None:
        original = workflow_module._finalize_run_hashes
        failed = False

        def finalize_with_fault(layout: Any, clock: Any) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("injected hash finalize failure")
            original(layout, clock)

        with patch.object(workflow_module, "_finalize_run_hashes", new=finalize_with_fault):
            code, result = execute_bootstrap(self.arguments, dependencies=_dependencies())
        self.assert_post_publish_rolled_back(code, result)
        self.assertTrue((self.artifacts / "runs" / "run-001" / "recovery" / "uncommitted-run-result.json").is_file())

    def test_rollback_precondition_failure_is_interrupted_not_rejected(self) -> None:
        original = EventLog.append

        def append_with_fault(log: EventLog, event_type: str, **kwargs: Any) -> dict[str, Any]:
            if event_type == "run_published":
                raise OSError("injected post-publish failure")
            return original(log, event_type, **kwargs)

        with (
            patch.object(EventLog, "append", new=append_with_fault),
            patch.object(workflow_module, "rollback_publication", side_effect=RuntimeError("pointer changed")),
        ):
            code, result = execute_bootstrap(self.arguments, dependencies=_dependencies())

        self.assertEqual((code, result["status"], result["exit_code"]), (10, "interrupted", 10))
        self.assertTrue((self.artifacts / "current-release.json").is_file())
        self.assertTrue((self.artifacts / "releases" / "release-001").is_dir())
        events = [
            json.loads(line)
            for line in (self.artifacts / "runs" / "run-001" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        terminals = [
            event["event_type"]
            for event in events
            if event["event_type"].startswith("run_") and event["event_type"] not in {"run_planned", "run_started"}
        ]
        self.assertEqual(terminals, ["run_interrupted"])
        self.assertNotIn("run_rejected", [event["event_type"] for event in events])


if __name__ == "__main__":
    unittest.main()
