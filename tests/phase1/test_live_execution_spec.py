from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from lottery_data.models import (
    ContractViolation,
    make_live_child_authorization_sha256,
    schema_path,
    validate_live_event_stream,
    validate_object,
)
from lottery_data.serialization import make_event_id


REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "phase1" / "fixtures" / "live-execution"
V1_HASHES = {
    "dataset-release.schema.json": "05526d4f81ccce548e97c216b9aaa1dff0e87bd1e426955721c968041a108e4e",
    "draw-record.schema.json": "6e977acb866c3310f3bd53c33f425a7d206cdef1fab6b5335e7ebea6fc01bf5a",
    "run-event.schema.json": "df9c784d0c6216fbbf54d67ea61f9e5140ad1e5d4a780a23d5425c969cb61ad2",
    "run-manifest.schema.json": "a686052b84f69faee8f8a37b6f5792c5891fd859bb45b8a66e4dff0a8e421cd8",
    "run-result.schema.json": "2b828d75344d643d95fbd444a1928e7877aa237bda806414a4ede9f61c5cfb55",
    "source-observation.schema.json": "f4a8de948312b423b79ea6f38c0d8f6b15e047011476ab865c7f238660dd5a04",
}


def load_manifest() -> dict:
    return json.loads((FIXTURES / "valid-manifest-v1.1.json").read_text(encoding="utf-8"))


def event(sequence: int, event_type: str, request: dict | None = None, **extra: object) -> dict:
    request_id = request["request_id"] if request else None
    attempt = 1 if event_type in {"request_started", "request_succeeded", "request_failed"} else None
    value = {
        "event_schema_version": "1.1.0",
        "event_id": make_event_id("live-contract-fixture", sequence, event_type, request_id, attempt),
        "run_id": "live-contract-fixture",
        "sequence": sequence,
        "event_type": event_type,
        "occurred_at_utc": f"2026-08-03T00:00:{sequence:02d}Z",
        "request_id": request_id,
        "attempt": attempt,
        "source_id": request["source_id"] if request else None,
        "game": request["game"] if request else None,
        "artifact_ref": None,
        "error_code": None,
        "error_detail_ref": None,
    }
    value.update(extra)
    return value


def valid_stream(manifest: dict) -> list[dict]:
    events = [event(1, "run_planned"), event(2, "run_started")]
    sequence = 3
    raw_digests = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
    for request, digest in zip(manifest["request_plan"], raw_digests):
        events.append(event(sequence, "request_started", request))
        sequence += 1
        raw_ref = f"raw/{request['source_id']}/{request['game']}/sha256/{digest}.raw"
        events.append(event(sequence, "request_succeeded", request, artifact_ref=raw_ref))
        sequence += 1
    child = {
        "request_id": "live-gdlottery-dlt-announcement",
        "source_id": "gdlottery", "publisher_id": "gdlottery-publisher", "game": "dlt",
        "method": "GET", "request_kind": "announcement", "parser_id": "phase1-gdlottery-live-parser",
        "parser_version": "2.0.0", "url": "https://www.gdlottery.cn/f_html/kjgg/P085_26086.html",
        "expected_raw_issue_id": "2026086", "parent_request_id": "live-gdlottery-dlt-discovery",
        "discovery_request_id": "live-gdlottery-dlt-discovery",
        "discovery_raw_ref": f"raw/gdlottery/dlt/sha256/{'d' * 64}.raw",
        "discovery_raw_sha256": "d" * 64,
    }
    child["authorization_sha256"] = make_live_child_authorization_sha256(child)
    events.append(event(sequence, "request_discovered", child, **{key: value for key, value in child.items() if key != "request_id"}))
    sequence += 1
    events.append(event(sequence, "request_started", child))
    sequence += 1
    events.append(event(sequence, "request_succeeded", child, artifact_ref=f"raw/gdlottery/dlt/sha256/{'e' * 64}.raw"))
    sequence += 1
    events.append(event(sequence, "run_no_change"))
    return events


def reindex(events: list[dict]) -> list[dict]:
    for sequence, item in enumerate(events, 1):
        item["sequence"] = sequence
        item["occurred_at_utc"] = f"2026-08-03T00:00:{sequence:02d}Z"
        item["event_id"] = make_event_id(item["run_id"], sequence, item["event_type"], item["request_id"], item["attempt"])
    return events


def run_result(status: str, stats: dict[str, int], *, exit_code: int | None = 0, release_id: str | None = None) -> dict:
    return {
        "result_schema_version": "1.0.0", "run_id": "live-contract-fixture", "mode": "incremental",
        "status": status, "started_at_utc": "2026-08-03T00:00:00Z", "completed_at_utc": "2026-08-03T00:01:00Z",
        "request_stats": stats,
        "observation_stats": {"parsed": 0, "valid": 0, "invalid": 0, "missing": 0, "duplicate": 0, "conflict": 0},
        "candidate_stats": {"observed": 0, "eligible": 0, "unresolved": 0},
        "change_stats": {"added": 0, "revised": 0, "unchanged": 0, "conflict": 0, "invalid": 0, "duplicate": 0, "manual_core_edit": 0},
        "exit_code": exit_code, "release_id": release_id,
        "manifest_ref": "runs/live-contract-fixture/run-manifest.json",
        "events_ref": "runs/live-contract-fixture/events.jsonl",
        "quality_report_ref": "runs/live-contract-fixture/quality-report.json",
        "error_refs": [], "deterministic_artifact_hashes": {"events": "f" * 64},
    }


def rejected_stream(manifest: dict, terminal: str = "run_rejected") -> list[dict]:
    request = manifest["request_plan"][0]
    return [
        event(1, "run_planned"), event(2, "run_started"), event(3, "request_started", request),
        event(4, "request_failed", request, error_code="NETWORK_UNAVAILABLE", error_detail_ref="errors/request-1.json"),
        event(5, terminal, error_code="RUN_REJECTED", error_detail_ref="errors/run.json"),
    ]


class LiveExecutionProfileTests(unittest.TestCase):
    def test_v11_schemas_are_meta_valid_and_alias_dispatch_accepts_fixture(self) -> None:
        for name in ("run-manifest-v1.1.schema.json", "run-event-v1.1.schema.json"):
            Draft202012Validator.check_schema(json.loads(schema_path(name).read_text(encoding="utf-8")))
        validate_object("RunManifest", load_manifest())
        invalid_manifest = json.loads((FIXTURES / "invalid-manifest-v1.1.json").read_text(encoding="utf-8"))
        with self.assertRaises(ContractViolation):
            validate_object("RunManifestV1.1", invalid_manifest)
        validate_object("RunEvent", json.loads((FIXTURES / "valid-discovered-event-v1.1.json").read_text(encoding="utf-8")))
        invalid_event = json.loads((FIXTURES / "invalid-discovered-event-v1.1.json").read_text(encoding="utf-8"))
        with self.assertRaises(ContractViolation):
            validate_object("RunEvent", invalid_event)

    def test_v1_and_v11_profiles_cannot_be_cross_used_in_either_direction(self) -> None:
        v1_manifest = json.loads((REPO / "tests/phase1/fixtures/spec/valid/run-manifest.json").read_text(encoding="utf-8"))
        v1_event = json.loads((REPO / "tests/phase1/fixtures/spec/valid/run-event-request-started.json").read_text(encoding="utf-8"))
        v11_event = json.loads((FIXTURES / "valid-discovered-event-v1.1.json").read_text(encoding="utf-8"))
        with self.assertRaises(ContractViolation):
            validate_object("run-manifest.schema.json", load_manifest())
        with self.assertRaises(ContractViolation):
            validate_object("RunManifestV1.1", v1_manifest)
        with self.assertRaises(ContractViolation):
            validate_object("run-event.schema.json", v11_event)
        with self.assertRaises(ContractViolation):
            validate_object("RunEventV1.1", v1_event)

    def test_manifest_authorization_is_strict_and_static_plan_is_write_once(self) -> None:
        candidate = load_manifest()
        candidate["request_plan"][3]["child_authorization"]["max_children"] = 2
        with self.assertRaises(ContractViolation):
            validate_object("RunManifest", candidate)
        candidate = load_manifest()
        candidate["request_plan"][0]["url"] += "?changed=true"
        with self.assertRaises(ContractViolation):
            validate_object("RunManifest", candidate)

    def test_valid_discovery_closes_effective_five_request_plan(self) -> None:
        manifest = load_manifest()
        expected = {"planned": 5, "started": 5, "succeeded": 5, "failed": 0, "not_started": 0}
        result = validate_live_event_stream(manifest, valid_stream(manifest), run_result("no_change", expected))
        self.assertEqual(result["request_stats"], {"planned": 5, "started": 5, "succeeded": 5, "failed": 0, "not_started": 0})

    def test_success_terminals_require_the_complete_five_request_success_closure(self) -> None:
        cases = json.loads((FIXTURES / "invalid-stream-cases-v1.1.json").read_text(encoding="utf-8"))["cases"]
        self.assertEqual(len(cases), 3)
        manifest = load_manifest()
        events = valid_stream(manifest)
        events = [item for item in events if item["event_type"] != "request_discovered" and item["request_id"] != "live-gdlottery-dlt-announcement"]
        with self.assertRaisesRegex(ContractViolation, cases[0]["expected_violation"]):
            validate_live_event_stream(manifest, reindex(events))

        events = valid_stream(manifest)
        first_id = manifest["request_plan"][0]["request_id"]
        events = [item for item in events if item["request_id"] != first_id]
        events[-1]["event_type"] = "run_published"
        with self.assertRaisesRegex(ContractViolation, cases[1]["expected_violation"]):
            validate_live_event_stream(manifest, reindex(events))

    def test_rejected_and_interrupted_allow_undiscovered_child_but_close_every_started_request(self) -> None:
        manifest = load_manifest()
        stats = {"planned": 4, "started": 1, "succeeded": 0, "failed": 1, "not_started": 3}
        result = validate_live_event_stream(manifest, rejected_stream(manifest), run_result("rejected", stats, exit_code=3))
        self.assertEqual(result["request_stats"], stats)
        result = validate_live_event_stream(
            manifest, rejected_stream(manifest, "run_interrupted"), run_result("interrupted", stats, exit_code=None)
        )
        self.assertEqual(result["request_stats"], stats)

        dangling = rejected_stream(manifest)[:-2] + [rejected_stream(manifest)[-1]]
        with self.assertRaisesRegex(ContractViolation, "started requests lack one terminal event"):
            validate_live_event_stream(manifest, reindex(dangling))

    def test_terminal_event_and_run_result_are_one_to_one(self) -> None:
        cases = json.loads((FIXTURES / "invalid-stream-cases-v1.1.json").read_text(encoding="utf-8"))["cases"]
        manifest = load_manifest()
        stats = {"planned": 5, "started": 5, "succeeded": 5, "failed": 0, "not_started": 0}
        events = valid_stream(manifest)
        mismatched = run_result("published", stats, release_id="release-1")
        with self.assertRaisesRegex(ContractViolation, cases[2]["expected_violation"]):
            validate_live_event_stream(manifest, events, mismatched)

        published_events = copy.deepcopy(events)
        published_events[-1]["event_type"] = "run_published"
        validate_live_event_stream(manifest, reindex(published_events), run_result("published", stats, release_id="release-1"))

        rejected_stats = {"planned": 4, "started": 1, "succeeded": 0, "failed": 1, "not_started": 3}
        with self.assertRaises(ContractViolation):
            validate_live_event_stream(
                manifest, rejected_stream(manifest, "run_interrupted"),
                run_result("interrupted", rejected_stats, exit_code=10),
            )

    def test_all_four_terminal_events_require_a_run_result(self) -> None:
        manifest = load_manifest()
        successful = valid_stream(manifest)
        published = copy.deepcopy(successful)
        published[-1]["event_type"] = "run_published"
        rejected = rejected_stream(manifest, "run_rejected")
        interrupted = rejected_stream(manifest, "run_interrupted")
        cases = {
            "run_published": reindex(published),
            "run_no_change": successful,
            "run_rejected": rejected,
            "run_interrupted": interrupted,
        }
        for terminal, events in cases.items():
            with self.subTest(terminal=terminal):
                with self.assertRaisesRegex(ContractViolation, "requires one matching RunResult"):
                    validate_live_event_stream(manifest, events)

    def test_non_terminal_online_prefix_allows_no_result_and_one_started_request(self) -> None:
        manifest = load_manifest()
        request = manifest["request_plan"][0]
        prefix = [event(1, "run_planned"), event(2, "run_started"), event(3, "request_started", request)]
        validated = validate_live_event_stream(manifest, prefix)
        self.assertEqual(
            validated["request_stats"],
            {"planned": 4, "started": 1, "succeeded": 0, "failed": 0, "not_started": 3},
        )

    def test_authorization_hash_is_deterministic_and_tamper_evident(self) -> None:
        manifest = load_manifest()
        events = valid_stream(manifest)
        discovered = next(item for item in events if item["event_type"] == "request_discovered")
        self.assertEqual(discovered["authorization_sha256"], "2b95d3ee3ac15c305a30f569e899ed65f3e01f567e5ec36f34dd54e320fc6b71")
        self.assertEqual(make_live_child_authorization_sha256(discovered), discovered["authorization_sha256"])
        discovered["url"] = "https://www.gdlottery.cn/f_html/kjgg/P085_26087.html"
        with self.assertRaises(ContractViolation):
            validate_live_event_stream(manifest, events)

    def test_stream_rejects_unplanned_started_and_discovery_before_raw_success(self) -> None:
        manifest = load_manifest()
        events = valid_stream(manifest)
        started = next(item for item in events if item["event_type"] == "request_started")
        started["request_id"] = "unplanned-request"
        started["event_id"] = make_event_id(started["run_id"], started["sequence"], started["event_type"], started["request_id"], 1)
        with self.assertRaises(ContractViolation):
            validate_live_event_stream(manifest, events)

        events = valid_stream(manifest)
        discovered_index = next(index for index, item in enumerate(events) if item["event_type"] == "request_discovered")
        discovery_success_index = next(index for index, item in enumerate(events) if item["event_type"] == "request_succeeded" and item["request_id"] == "live-gdlottery-dlt-discovery")
        events[discovered_index], events[discovery_success_index] = events[discovery_success_index], events[discovered_index]
        for sequence, item in enumerate(events, 1):
            item["sequence"] = sequence
            item["event_id"] = make_event_id(item["run_id"], sequence, item["event_type"], item["request_id"], item["attempt"])
        with self.assertRaises(ContractViolation):
            validate_live_event_stream(manifest, events)

    def test_stream_rejects_duplicate_discovery_and_non_content_addressed_success(self) -> None:
        manifest = load_manifest()
        events = valid_stream(manifest)
        discovered = next(item for item in events if item["event_type"] == "request_discovered")
        duplicate = copy.deepcopy(discovered)
        events.insert(-1, duplicate)
        for sequence, item in enumerate(events, 1):
            item["sequence"] = sequence
            item["event_id"] = make_event_id(item["run_id"], sequence, item["event_type"], item["request_id"], item["attempt"])
        with self.assertRaises(ContractViolation):
            validate_live_event_stream(manifest, events)

    def test_discovery_parent_raw_origin_path_and_issue_checks_fail_closed_independently(self) -> None:
        manifest = load_manifest()
        mutations = {
            "parent-ref": lambda item: item.__setitem__("discovery_raw_ref", f"raw/gdlottery/dlt/sha256/{'f' * 64}.raw"),
            "raw-hash": lambda item: item.__setitem__("discovery_raw_sha256", "e" * 64),
            "cross-origin": lambda item: item.__setitem__("url", "https://evil.example/f_html/kjgg/P085_26086.html"),
            "path": lambda item: item.__setitem__("url", "https://www.gdlottery.cn/f_html/other/P085_26086.html"),
            "issue": lambda item: item.__setitem__("expected_raw_issue_id", "2026087"),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                events = valid_stream(manifest)
                discovered = next(item for item in events if item["event_type"] == "request_discovered")
                mutate(discovered)
                if label == "parent-ref":
                    discovered["discovery_raw_sha256"] = "f" * 64
                discovered["authorization_sha256"] = make_live_child_authorization_sha256(discovered)
                with self.assertRaises(ContractViolation):
                    validate_live_event_stream(manifest, events)

        events = valid_stream(manifest)
        next(item for item in events if item["event_type"] == "request_succeeded")["artifact_ref"] = "raw/current.html"
        with self.assertRaises(ContractViolation):
            validate_live_event_stream(manifest, events)

    def test_original_six_v1_schema_bytes_are_unchanged(self) -> None:
        for name, expected in V1_HASHES.items():
            self.assertEqual(hashlib.sha256((REPO / "schemas" / "phase1" / name).read_bytes()).hexdigest(), expected, name)


if __name__ == "__main__":
    unittest.main()
