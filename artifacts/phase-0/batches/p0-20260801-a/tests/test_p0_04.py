from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
FIXTURES = Path(__file__).with_name("fixtures")
sys.path.insert(0, str(SCRIPTS))

from p0_04_http import (  # noqa: E402
    AcquisitionError,
    ClockCheck,
    FetchResult,
    PublicHttpCollector,
    parse_w32tm_offsets,
    validate_public_request_headers,
    validate_source_url,
    whitelist_response_headers,
)
from p0_04_parser import ParseError, decode_html, parse_dlt_html, parse_ssq_history_html  # noqa: E402
from p0_04_pipeline import build_environment_lock, process_capture, write_run_artifacts  # noqa: E402
from phase0lib import load_json, load_jsonl, validate_schema_instance  # noqa: E402


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def passing_clock() -> ClockCheck:
    return ClockCheck(
        checked_at_utc="2026-08-01T00:00:00Z",
        source="fixture-clock",
        offset_seconds=0,
        maximum_offset_seconds=5,
        passed=True,
        raw_result_sha256="0" * 64,
    )


def saved_fetch(game: str, raw: bytes) -> FetchResult:
    if game == "dlt":
        url = "https://www.gdlottery.cn/f_html/kjgg/P085_26014.html"
    else:
        url = "https://www.gdfc.org.cn/sjfx/ssq_200.html"
    return FetchResult(
        requested_url=url,
        redirect_chain=({"url": url, "status": 200},),
        final_url=url,
        retrieved_at="2026-08-01T00:01:00Z",
        raw_body=raw,
        response_headers=(
            {"name": "Content-Type", "value": "text/html; charset=UTF-8"},
            {"name": "Date", "value": "Sat, 01 Aug 2026 00:01:00 GMT"},
        ),
        redacted_header_names=(),
    )


class AcquisitionPolicyTests(unittest.TestCase):
    def test_sensitive_request_headers_are_forbidden_case_insensitively(self) -> None:
        for header in ("Authorization", "cookie", "PROXY-AUTHORIZATION"):
            with self.subTest(header=header), self.assertRaisesRegex(AcquisitionError, "forbidden"):
                validate_public_request_headers({header: "secret"})

    def test_only_allowlisted_https_sources_are_accepted(self) -> None:
        validate_source_url("https://www.gdlottery.cn/f_html/kjgg/P085_26014.html")
        validate_source_url("https://www.gdfc.org.cn/sjfx/ssq_200.html")
        for url in (
            "http://www.gdlottery.cn/f_html/kjgg/P085_26014.html",
            "https://example.com/result",
            "https://user:password@www.gdlottery.cn/result",
        ):
            with self.subTest(url=url), self.assertRaises(AcquisitionError):
                validate_source_url(url)

    def test_rate_limit_cannot_be_configured_above_two_requests_per_minute(self) -> None:
        with self.assertRaisesRegex(AcquisitionError, "30 seconds"):
            PublicHttpCollector(minimum_interval_seconds=29.9)

    def test_response_header_allowlist_redacts_cookie_and_server(self) -> None:
        allowed, redacted = whitelist_response_headers(
            {
                "Content-Type": "text/html; charset=UTF-8",
                "Date": "Sat, 01 Aug 2026 00:01:00 GMT",
                "Set-Cookie": "session=secret",
                "Server": "example",
            }
        )
        self.assertEqual([item["name"] for item in allowed], ["Content-Type", "Date"])
        self.assertEqual(redacted, ["Server", "Set-Cookie"])
        self.assertNotIn("secret", json.dumps(allowed))

    def test_clock_output_is_parsed_deterministically(self) -> None:
        output = "10:00:00, +0.0123456s\n10:00:01, -0.1000000s\n10:00:02, +1.2500000s\n"
        self.assertEqual(parse_w32tm_offsets(output), (0.0123456, -0.1, 1.25))
        with self.assertRaisesRegex(AcquisitionError, "no parseable offsets"):
            parse_w32tm_offsets("clock failed")


class ParserTests(unittest.TestCase):
    def test_dlt_valid_fixture_is_deterministic(self) -> None:
        text, codec = decode_html(fixture_bytes("p0_04_dlt_valid.html"), "text/html; charset=UTF-8")
        parsed = parse_dlt_html(text, "2026014")
        self.assertEqual(codec, "utf-8")
        self.assertEqual(parsed.issue_id, "2026014")
        self.assertEqual(parsed.front_numbers, ("16", "18", "23", "34", "35"))
        self.assertEqual(parsed.back_numbers, ("01", "06"))
        self.assertEqual(parsed.draw_date.isoformat(), "2026-02-02")

    def test_ssq_valid_fixture_is_deterministic(self) -> None:
        text, _ = decode_html(fixture_bytes("p0_04_ssq_valid.html"), "text/html; charset=UTF-8")
        parsed = parse_ssq_history_html(text, "2026014")
        self.assertEqual(parsed.front_numbers, ("02", "06", "09", "18", "25", "33"))
        self.assertEqual(parsed.back_numbers, ("12",))
        self.assertEqual(parsed.draw_date.isoformat(), "2026-02-01")

    def test_dlt_missing_number_field_is_rejected(self) -> None:
        html = fixture_bytes("p0_04_dlt_valid.html").decode("utf-8").replace("本期开奖号码：", "号码：")
        with self.assertRaisesRegex(ParseError, "marker"):
            parse_dlt_html(html, "2026014")

    def test_dlt_out_of_range_number_is_rejected(self) -> None:
        html = fixture_bytes("p0_04_dlt_valid.html").decode("utf-8").replace(
            "16 18 23 34 35", "16 18 23 34 36"
        )
        with self.assertRaisesRegex(ParseError, "outside"):
            parse_dlt_html(html, "2026014")

    def test_dlt_duplicate_number_is_rejected(self) -> None:
        html = fixture_bytes("p0_04_dlt_valid.html").decode("utf-8").replace(
            "16 18 23 34 35", "16 18 23 34 34"
        )
        with self.assertRaisesRegex(ParseError, "duplicate"):
            parse_dlt_html(html, "2026014")

    def test_ssq_missing_blue_field_is_rejected(self) -> None:
        html = fixture_bytes("p0_04_ssq_valid.html").decode("utf-8").replace(
            "<td>12</td><td>386000000</td>", "<td></td><td>386000000</td>"
        )
        with self.assertRaisesRegex(ParseError, "blue-number cell missing"):
            parse_ssq_history_html(html, "2026014")

    def test_ssq_out_of_range_number_is_rejected(self) -> None:
        html = fixture_bytes("p0_04_ssq_valid.html").decode("utf-8").replace(
            "02 06 09 18 25 33", "02 06 09 18 25 34"
        )
        with self.assertRaisesRegex(ParseError, "outside"):
            parse_ssq_history_html(html, "2026014")

    def test_ssq_duplicate_number_is_rejected(self) -> None:
        html = fixture_bytes("p0_04_ssq_valid.html").decode("utf-8").replace(
            "02 06 09 18 25 33", "02 06 09 18 25 25"
        )
        with self.assertRaisesRegex(ParseError, "duplicate"):
            parse_ssq_history_html(html, "2026014")

    def test_issue_mismatch_is_rejected(self) -> None:
        html = fixture_bytes("p0_04_dlt_valid.html").decode("utf-8")
        with self.assertRaisesRegex(ParseError, "issue mismatch"):
            parse_dlt_html(html, "2026013")

    def test_corrupt_encoding_is_rejected_without_replacement(self) -> None:
        with self.assertRaisesRegex(ParseError, "not valid"):
            decode_html(b"<html>\xff\xff</html>", "text/html; charset=UTF-8")


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.environment_schema = load_json(REPO / "artifacts/phase-0/schemas/environment-lock.schema.json")
        cls.evidence_schema = load_json(REPO / "artifacts/phase-0/schemas/evidence-manifest.schema.json")

    def test_successful_parse_does_not_invent_actual_draw_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            environment = build_environment_lock("2026-08-01T00:01:00Z")
            outcome = process_capture(
                game="dlt",
                issue_id="2026014",
                fetch=saved_fetch("dlt", fixture_bytes("p0_04_dlt_valid.html")),
                clock_check=passing_clock(),
                output_root=root,
                environment_lock=environment,
            )
            self.assertIsNotNone(outcome.parse_result)
            self.assertIsNone(outcome.normalized)
            self.assertEqual(outcome.evidence["status"], "unverified")
            self.assertEqual(outcome.evidence["normalized_record_sha256"], "0" * 64)
            self.assertFalse((root / "normalized/p0-04-dlt-2026014.json").exists())
            self.assertEqual(
                outcome.parse_result["normalization_blockers"], ["actual_draw_at_not_evidenced"]
            )
            validate_schema_instance(environment, self.environment_schema)
            validate_schema_instance(outcome.evidence, self.evidence_schema)

    def test_parse_failure_emits_no_verified_or_normalized_record(self) -> None:
        broken = fixture_bytes("p0_04_dlt_valid.html").replace(b"16 18 23 34 35", b"16 18 23 34 36")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outcome = process_capture(
                game="dlt",
                issue_id="2026014",
                fetch=saved_fetch("dlt", broken),
                clock_check=passing_clock(),
                output_root=root,
            )
            self.assertIsNone(outcome.parse_result)
            self.assertIsNone(outcome.normalized)
            self.assertEqual(outcome.evidence["status"], "invalid")
            self.assertNotEqual(outcome.evidence["status"], "verified")
            self.assertFalse((root / "normalized/p0-04-dlt-2026014.json").exists())
            validate_schema_instance(outcome.evidence, self.evidence_schema)

    def test_written_raw_hash_manifest_and_outputs_are_self_consistent(self) -> None:
        raw = fixture_bytes("p0_04_ssq_valid.html")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            environment = build_environment_lock("2026-08-01T00:01:00Z")
            outcome = process_capture(
                game="ssq",
                issue_id="2026014",
                fetch=saved_fetch("ssq", raw),
                clock_check=passing_clock(),
                output_root=root,
                environment_lock=environment,
            )
            write_run_artifacts(root, [outcome])
            stored = root / "raw/p0-04-ssq-2026014.html"
            self.assertEqual(hashlib.sha256(stored.read_bytes()).hexdigest(), outcome.evidence["stored_payload_sha256"])
            manifest = load_jsonl(root / "p0-04-evidence-manifest.jsonl")
            parsed = load_jsonl(root / "parsed/p0-04-parse-results.jsonl")
            normalized = (root / "normalized/p0-04-normalized-records.jsonl").read_bytes()
            self.assertEqual(manifest, [outcome.evidence])
            self.assertEqual(parsed, [outcome.parse_result])
            self.assertEqual(normalized, b"")
            validate_schema_instance(manifest[0], self.evidence_schema)


if __name__ == "__main__":
    unittest.main()
