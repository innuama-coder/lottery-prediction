from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from lottery_data.models import ContractViolation, schema_path, validate_live_event_stream, validate_object
from lottery_data.serialization import make_event_id


REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "phase1" / "fixtures" / "live-execution-v1.2"
V11_FIXTURES = REPO / "tests" / "phase1" / "fixtures" / "live-execution"
V11_SCHEMA_HASHES = {
    "run-manifest-v1.1.schema.json": "b5cc83086b4c3dbbce35ebd20373302fac36f7b3e3800c4759d04610baebd673",
    "run-event-v1.1.schema.json": "cff590d5b0dec8a4bc28ad3eb6c2b86bc56ca903978b362d56524ce2b7353c73",
}


def load_manifest() -> dict:
    return json.loads((FIXTURES / "valid-manifest-v1.2.json").read_text(encoding="utf-8"))


def event(sequence: int, event_type: str, request: dict | None = None, **extra: object) -> dict:
    request_id = request["request_id"] if request else None
    attempt = 1 if event_type in {"request_started", "request_succeeded", "request_failed"} else None
    value = {
        "event_schema_version": "1.2.0",
        "event_id": make_event_id("live-v12-contract-fixture", sequence, event_type, request_id, attempt),
        "run_id": "live-v12-contract-fixture",
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


def success_stream(manifest: dict, terminal: str = "run_no_change") -> list[dict]:
    events = [event(1, "run_planned"), event(2, "run_started")]
    sequence = 3
    for request, digest in zip(manifest["request_plan"], ("a" * 64, "b" * 64, "c" * 64, "d" * 64)):
        events.append(event(sequence, "request_started", request))
        sequence += 1
        events.append(event(
            sequence,
            "request_succeeded",
            request,
            artifact_ref=f"raw/{request['source_id']}/{request['game']}/sha256/{digest}.raw",
        ))
        sequence += 1
    events.append(event(sequence, terminal))
    return events


def reindex(events: list[dict]) -> list[dict]:
    for sequence, item in enumerate(events, 1):
        item["sequence"] = sequence
        item["occurred_at_utc"] = f"2026-08-03T00:00:{sequence:02d}Z"
        item["event_id"] = make_event_id(
            item["run_id"], sequence, item["event_type"], item["request_id"], item["attempt"],
        )
    return events


def run_result(stats: dict[str, int]) -> dict:
    return {
        "result_schema_version": "1.0.0", "run_id": "live-v12-contract-fixture", "mode": "incremental",
        "status": "no_change", "started_at_utc": "2026-08-03T00:00:00Z", "completed_at_utc": "2026-08-03T00:01:00Z",
        "request_stats": stats,
        "observation_stats": {"parsed": 0, "valid": 0, "invalid": 0, "missing": 0, "duplicate": 0, "conflict": 0},
        "candidate_stats": {"observed": 0, "eligible": 0, "unresolved": 0},
        "change_stats": {"added": 0, "revised": 0, "unchanged": 0, "conflict": 0, "invalid": 0, "duplicate": 0, "manual_core_edit": 0},
        "exit_code": 0, "release_id": None,
        "manifest_ref": "runs/live-v12-contract-fixture/run-manifest.json",
        "events_ref": "runs/live-v12-contract-fixture/events.jsonl",
        "quality_report_ref": "runs/live-v12-contract-fixture/quality-report.json",
        "error_refs": [], "deterministic_artifact_hashes": {"events": "f" * 64},
    }


class LiveExecutionV12SpecTests(unittest.TestCase):
    def test_v12_schemas_and_aliases_accept_only_the_four_static_profile(self) -> None:
        for name in ("run-manifest-v1.2.schema.json", "run-event-v1.2.schema.json"):
            Draft202012Validator.check_schema(json.loads(schema_path(name).read_text(encoding="utf-8")))
        manifest = load_manifest()
        validate_object("RunManifest", manifest)
        validate_object("RunManifestV1.2", manifest)
        self.assertEqual([row["sequence"] for row in manifest["request_plan"]], [1, 2, 3, 4])
        self.assertTrue(all(row["request_kind"] == "history" for row in manifest["request_plan"]))
        self.assertTrue(all("input_ref" not in row and "child_authorization" not in row for row in manifest["request_plan"]))
        self.assertIsNone(manifest["replay_of_run_id"])

    def test_response_profiles_and_gd_identity_are_frozen(self) -> None:
        manifest = load_manifest()
        self.assertEqual(
            [row["response_profile"] for row in manifest["request_plan"][:3]],
            [{"expected_media_type": "text/html", "max_response_bytes": 1048576}] * 3,
        )
        self.assertEqual(manifest["request_plan"][3], {
            "request_id": "live-gdlottery-dlt-history", "sequence": 4,
            "source_id": "gdlottery", "publisher_id": "gdlottery-publisher", "game": "dlt",
            "method": "GET", "url": "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json",
            "request_kind": "history", "parser_id": "phase1-gdlottery-history-parser",
            "parser_version": "1.0.0",
            "response_profile": {"expected_media_type": "application/json", "max_response_bytes": 2097152},
        })
        for index, mutation in (
            (0, lambda row: row["response_profile"].__setitem__("max_response_bytes", 2097152)),
            (3, lambda row: row["response_profile"].__setitem__("max_response_bytes", 2097153)),
            (3, lambda row: row.__setitem__("url", row["url"] + "?game=dlt")),
        ):
            candidate = load_manifest()
            mutation(candidate["request_plan"][index])
            with self.assertRaises(ContractViolation):
                validate_object("RunManifest", candidate)

    def test_child_discovery_announcement_and_input_ref_are_not_representable(self) -> None:
        invalid_manifest = json.loads((FIXTURES / "invalid-manifest-child-v1.2.json").read_text(encoding="utf-8"))
        with self.assertRaises(ContractViolation):
            validate_object("RunManifestV1.2", invalid_manifest)
        invalid_event = json.loads((FIXTURES / "invalid-request-discovered-event-v1.2.json").read_text(encoding="utf-8"))
        with self.assertRaises(ContractViolation):
            validate_object("RunEvent", invalid_event)
        candidate = load_manifest()
        candidate["request_plan"][0]["input_ref"] = "raw/forbidden.raw"
        with self.assertRaises(ContractViolation):
            validate_object("RunManifest", candidate)

    def test_success_requires_exact_ordered_four_request_closure_and_one_terminal(self) -> None:
        manifest = load_manifest()
        expected = {"planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0}
        validated = validate_live_event_stream(manifest, success_stream(manifest), run_result(expected))
        self.assertEqual(validated["request_stats"], expected)

        missing = success_stream(manifest)
        del missing[4:6]
        with self.assertRaisesRegex(ContractViolation, "exact ordered four-request success stream"):
            validate_live_event_stream(manifest, reindex(missing), run_result({
                "planned": 4, "started": 3, "succeeded": 3, "failed": 0, "not_started": 1,
            }))

        duplicate_terminal = success_stream(manifest)
        duplicate_terminal.append(event(len(duplicate_terminal) + 1, "run_no_change"))
        with self.assertRaisesRegex(ContractViolation, "at most one final run terminal"):
            validate_live_event_stream(manifest, duplicate_terminal, run_result(expected))

    def test_v11_and_v12_profiles_are_disjoint_and_v11_bytes_stay_frozen(self) -> None:
        v11_manifest = json.loads((V11_FIXTURES / "valid-manifest-v1.1.json").read_text(encoding="utf-8"))
        validate_object("RunManifestV1.1", v11_manifest)
        for name, expected in V11_SCHEMA_HASHES.items():
            self.assertEqual(hashlib.sha256(schema_path(name).read_bytes()).hexdigest(), expected)
        with self.assertRaises(ContractViolation):
            validate_object("RunManifestV1.1", load_manifest())
        with self.assertRaises(ContractViolation):
            validate_object("RunManifestV1.2", v11_manifest)

        from tests.phase1.test_live_execution_spec import run_result as v11_result
        from tests.phase1.test_live_execution_spec import valid_stream as v11_stream

        expected = {"planned": 5, "started": 5, "succeeded": 5, "failed": 0, "not_started": 0}
        self.assertEqual(
            validate_live_event_stream(v11_manifest, v11_stream(v11_manifest), v11_result("no_change", expected))["request_stats"],
            expected,
        )

    def test_unplanned_request_and_wrong_result_counts_fail_closed(self) -> None:
        manifest = load_manifest()
        events = success_stream(manifest)
        forged = copy.deepcopy(manifest["request_plan"][0])
        forged["request_id"] = "live-unplanned-ssq-history"
        events[2] = event(3, "request_started", forged)
        with self.assertRaisesRegex(ContractViolation, "unplanned request_started"):
            validate_live_event_stream(manifest, events, run_result({
                "planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0,
            }))

        with self.assertRaisesRegex(ContractViolation, "request_stats differ"):
            validate_live_event_stream(manifest, success_stream(manifest), run_result({
                "planned": 5, "started": 4, "succeeded": 4, "failed": 0, "not_started": 1,
            }))


if __name__ == "__main__":
    unittest.main()
