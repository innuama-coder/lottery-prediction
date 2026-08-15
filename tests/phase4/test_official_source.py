from __future__ import annotations

import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver

from lottery_system.phase4.identity import content_id
from lottery_system.phase4.official_adapter import (
    EXPECTED_ENDPOINTS,
    SourcePolicyError,
    SourceReadinessError,
    capture_endpoint,
    load_source_policy,
    parse_source_response,
    run_readonly_canary,
)
from lottery_system.phase4.serialization import load_json, sha256_bytes, sha256_file
from lottery_system.phase4.verification import (
    SourceVerificationError,
    deduplicate_facts,
    verify_result_revision,
    verify_revision_successor,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/phase4/fixtures/source"
POLICY = ROOT / "config/phase4/source-policy.json"
PROVENANCE = {
    "producer_actor_id": "p4-implementation-author-i01", "task_id": "T03",
    "session_id": "/root/implementation_author", "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b",
    "path": "artifacts/phase-4-prep/unit", "role": "implementation_author",
}
VALID_CLOCK = "2026-08-11T12:00:00Z"
LEGACY_PREP_INSTALLED = (ROOT / "artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T03/data-custodian-source-review-I01/source-review.json").is_file()
RAW = {
    ("ssq", "swlc"): (FIXTURES / "swlc-ssq.html").read_bytes(),
    ("ssq", "ydniu"): (FIXTURES / "ydniu-ssq.html").read_bytes(),
    ("dlt", "gdlottery"): (FIXTURES / "gdlottery-dlt.json").read_bytes(),
    ("dlt", "ydniu"): (FIXTURES / "ydniu-dlt.html").read_bytes(),
}


class _Response:
    def __init__(self, body: bytes, url: str, *, status: int = 200) -> None:
        self.body, self.url, self.status = io.BytesIO(body), url, status
        self.headers = Message()
        content_type = "application/json" if "gdlottery" in url else "text/html"
        self.headers["Content-Type"] = f"{content_type}; charset=utf-8"

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def getcode(self) -> int:
        return self.status

    def read(self, amount: int = -1) -> bytes:
        return self.body.read(amount)


def _observation(game: str, source_id: str, raw: bytes) -> dict:
    body = {
        "schema_version": "1.0.0", "artifact_type": "phase4_source_observation",
        "game": game, "source_id": source_id, "request_url": EXPECTED_ENDPOINTS[(game, source_id)],
        "method": "GET", "observed_at_utc": VALID_CLOCK, "http_status": 200,
        "raw_sha256": sha256_bytes(raw), "raw_bytes": len(raw), "terminal": "observed",
    }
    body["observation_id"] = content_id("observation", body)
    return body


class OfficialSourceTests(unittest.TestCase):
    @unittest.skipUnless(LEGACY_PREP_INSTALLED, "superseded T00-T24 source-review evidence is not installed")
    def test_frozen_policy_yields_exact_four_source_plan(self) -> None:
        self.assertEqual(sha256_file(POLICY), "ab70c9abc440ad8180db70321578e4a569fbfda802b0e698597fd5ae3df7417f")
        self.assertEqual(sha256_file(ROOT / "config/phase4/calendar-policy.json"), "79ae43a35b5f83d1c424994c9b7ac34b812faa6d619d34ac9e3000afb44c8bbf")
        review_root = ROOT / "artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T03/data-custodian-source-review-I01"
        self.assertEqual(sha256_file(review_root / "source-review.json"), "66022e1a2d24bb648cacd4ac13be6afb581e6b0f4648fde31bc946c667badeac")
        self.assertEqual(sha256_file(review_root / "receipt.json"), "b28839d64eb94f384095b7243e833cf33be5cb6c9184c82a9033a38c7bf55a17")
        policy, endpoints = load_source_policy(POLICY, at_utc=VALID_CLOCK)
        self.assertEqual(policy["policy_id"], "p4-source-policy-v1-20260811-i01")
        self.assertEqual({(row.game, row.source_id) for row in endpoints}, set(EXPECTED_ENDPOINTS))
        self.assertEqual(len({row.publisher for row in endpoints}), 3)

    def test_policy_expiry_method_endpoint_and_purpose_mutations_fail_closed(self) -> None:
        with self.assertRaises(SourcePolicyError):
            load_source_policy(POLICY, at_utc="2027-01-01T00:00:00Z")
        original = load_json(POLICY, reject_floats=True)
        mutations = (
            lambda value: value.__setitem__("purpose", "research_only"),
            lambda value: value["sources"][0].__setitem__("method", "POST"),
            lambda value: value["sources"][0].__setitem__("endpoint", value["sources"][0]["endpoint"] + "&next=https://evil.example"),
            lambda value: value["sources"][1].__setitem__("publisher", value["sources"][0]["publisher"]),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as raw:
                changed = json.loads(json.dumps(original))
                mutate(changed)
                path = Path(raw) / "policy.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(SourcePolicyError):
                    load_source_policy(path, at_utc=VALID_CLOCK)

    def test_all_fixed_responses_parse_and_two_source_core_facts_verify(self) -> None:
        facts = {}
        observations = []
        for (game, source_id), raw in RAW.items():
            observation = _observation(game, source_id, raw)
            observations.append(observation)
            for fact in parse_source_response(observation, raw):
                facts[(game, source_id, fact["issue_id"])] = fact
        ssq = verify_result_revision(facts[("ssq", "swlc", "2026088")], facts[("ssq", "ydniu", "2026088")], verified_at_utc=VALID_CLOCK)
        dlt = verify_result_revision(facts[("dlt", "gdlottery", "2026086")], facts[("dlt", "ydniu", "2026086")], verified_at_utc=VALID_CLOCK)
        self.assertEqual(ssq["numbers"], {"front": [6, 7, 11, 18, 22, 33], "back": [5]})
        self.assertEqual(dlt["numbers"], {"front": [10, 11, 18, 22, 35], "back": [6, 12]})
        schemas = {}
        for path in (ROOT / "schemas/phase4").glob("*.schema.json"):
            schema = json.loads(path.read_text())
            schemas[schema["$id"]] = schema
        for schema_name, values in (("source-observation", observations), ("result-revision", [ssq, dlt])):
            schema = schemas[f"https://lottery.local/schemas/phase4/{schema_name}.schema.json"]
            validator = Draft202012Validator(schema, resolver=RefResolver.from_schema(schema, store=schemas))
            self.assertEqual([error.message for value in values for error in validator.iter_errors(value)], [])

    def test_conflict_duplicate_revision_and_parser_drift_fail_closed(self) -> None:
        swlc = parse_source_response(_observation("ssq", "swlc", RAW[("ssq", "swlc")]), RAW[("ssq", "swlc")])[0]
        ydniu = parse_source_response(_observation("ssq", "ydniu", RAW[("ssq", "ydniu")]), RAW[("ssq", "ydniu")])[0]
        conflict = json.loads(json.dumps(ydniu))
        conflict["numbers"]["back"] = [6]
        conflict.pop("parsed_fact_id")
        conflict["parsed_fact_id"] = content_id("parsed-fact", conflict)
        with self.assertRaises(SourceVerificationError):
            verify_result_revision(swlc, conflict, verified_at_utc="2026-08-11T00:00:00Z")
        with self.assertRaises(SourceVerificationError):
            deduplicate_facts([ydniu, conflict])
        first = verify_result_revision(swlc, ydniu, verified_at_utc=VALID_CLOCK)
        corrected = verify_result_revision(swlc, ydniu, verified_at_utc="2026-08-12T00:00:00Z", supersedes_revision_id=first["result_revision_id"])
        with self.assertRaises(SourceVerificationError):
            verify_revision_successor(first, corrected)
        bad_raw = RAW[("ssq", "ydniu")].replace(b'open_number', b'changed_markup')
        with self.assertRaises(SourceReadinessError):
            parse_source_response(_observation("ssq", "ydniu", bad_raw), bad_raw)

    def test_transport_size_and_final_authority_are_enforced(self) -> None:
        _, endpoints = load_source_policy(POLICY, at_utc=VALID_CLOCK)
        endpoint = next(row for row in endpoints if (row.game, row.source_id) == ("ssq", "swlc"))
        with self.assertRaises(SourcePolicyError):
            capture_endpoint(endpoint, observed_at_utc=VALID_CLOCK, open_response=lambda _: _Response(b"x", "https://evil.example/x"))
        too_large = b"x" * (endpoint.maximum_response_bytes + 1)
        with self.assertRaises(SourceReadinessError):
            capture_endpoint(endpoint, observed_at_utc=VALID_CLOCK, open_response=lambda _: _Response(too_large, endpoint.endpoint))

    def test_mocked_four_endpoint_canary_writes_only_isolated_roots(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            policy_path = project / "config/phase4/source-policy.json"
            policy_path.parent.mkdir(parents=True)
            policy_path.write_bytes(POLICY.read_bytes())
            for protected in ("phase-0", "phase-0-multisource", "phase-1", "phase-2", "phase-2.1", "phase-3"):
                (project / "artifacts" / protected).mkdir(parents=True)
            staging = project / "artifacts/phase-4-staging/unit"
            output = project / "artifacts/phase-4-prep/unit/work-items/T03/canary"
            pauses = []
            summary = run_readonly_canary(
                project_root=project, source_policy_path=policy_path, staging_root=staging,
                output_root=output, mode="early-readonly-canary", observed_at_utc=VALID_CLOCK,
                producer_provenance=PROVENANCE,
                open_response=lambda endpoint: _Response(RAW[(endpoint.game, endpoint.source_id)], endpoint.endpoint),
                pause=pauses.append,
            )
            self.assertEqual((summary["successful_endpoint_count"], summary["verified_game_count"]), (4, 2))
            self.assertEqual(len(list((staging / "raw").glob("*/*/*.raw"))), 4)
            self.assertEqual(len(pauses), 1)
            self.assertGreater(pauses[0], 0)
            self.assertTrue((output / "protected-inventory-before.json").is_file())
            self.assertTrue((output / "protected-inventory-after.json").is_file())


if __name__ == "__main__":
    unittest.main()
