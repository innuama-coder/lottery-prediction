from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


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
from p0_04_pipeline import (  # noqa: E402
    build_environment_lock,
    dlt_issue_url,
    load_existing_environment,
    main as pipeline_main,
    process_capture,
    require_collection_approved,
    write_run_artifacts,
)
from phase0lib import ValidationError, canonical_json_bytes, load_json, load_jsonl, validate_schema_instance  # noqa: E402


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


def build_fixture_capture(root: Path) -> Path:
    environment = build_environment_lock("2026-08-01T00:01:00Z")
    outcome = process_capture(
        game="dlt", issue_id="2026014",
        fetch=saved_fetch("dlt", fixture_bytes("p0_04_dlt_valid.html")),
        clock_check=passing_clock(), output_root=root, environment_lock=environment,
    )
    write_run_artifacts(root, [outcome])
    clock_path = root / "fixture-clock.json"
    clock_path.write_text(json.dumps({
        "checked_at_utc": "2026-08-01T00:00:00Z", "source": "fixture-clock",
        "offset_seconds": 0, "maximum_offset_seconds": 5, "passed": True,
        "raw_result_sha256": "0" * 64,
    }), encoding="utf-8")
    return clock_path


def run_fixture_verifier(root: Path, clock_path: Path) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = pipeline_main([
            "verify-captures", "--artifacts", str(root), "--clock-check", str(clock_path),
        ])
    return result, stdout.getvalue(), stderr.getvalue()


def rewrite_manifest(root: Path, transform) -> None:  # noqa: ANN001
    entries = load_jsonl(root / "evidence-manifest.jsonl")
    transform(entries[0])
    (root / "evidence-manifest.jsonl").write_bytes(
        b"".join(canonical_json_bytes(entry) + b"\n" for entry in entries)
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

    def test_dlt_catalog_gate_accepts_only_exact_scheduled_host_path(self) -> None:
        source = require_collection_approved("dlt", dlt_issue_url("2026014"))
        self.assertEqual(source["source_id"], "dlt-gd-official-issue-pages")
        for url in (
            "https://gdlottery.cn/f_html/kjgg/P085_26014.html",
            "https://www.gdlottery.cn/f_html/kjgg/P085_26014.html?copy=1",
            "https://www.gdlottery.cn/other/P085_26014.html",
        ):
            with self.subTest(url=url), self.assertRaises(AcquisitionError):
                require_collection_approved("dlt", url)

    def test_ssq_hold_fails_before_collector_is_constructed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            clock_path = Path(temp) / "clock.json"
            clock_path.write_text(json.dumps({
                "checked_at_utc": "2026-08-01T00:00:00Z", "source": "fixture-clock",
                "offset_seconds": 0, "maximum_offset_seconds": 5, "passed": True,
                "raw_result_sha256": "0" * 64,
            }), encoding="utf-8")
            stderr = io.StringIO()
            with patch("p0_04_pipeline.PublicHttpCollector") as collector, redirect_stderr(stderr):
                result = pipeline_main([
                    "collect", "--game", "ssq", "--issue", "2026014",
                    "--clock-check", str(clock_path), "--output-root", temp,
                ])
            self.assertNotEqual(result, 0)
            collector.assert_not_called()
            self.assertIn("operational readiness is not ready", stderr.getvalue())


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
        cls.normalized_schema = load_json(REPO / "artifacts/phase-0/schemas/normalized-records.schema.json")

    def test_successful_parse_normalizes_without_inventing_actual_draw_at(self) -> None:
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
            self.assertIsNotNone(outcome.normalized)
            self.assertEqual(outcome.normalized["schema_version"], "1.1.0")
            self.assertEqual(outcome.normalized["draw_date_local"], "2026-02-02")
            self.assertIsNone(outcome.normalized["draw_at"])
            self.assertIsNone(outcome.normalized["available_at"])
            self.assertIsNone(outcome.normalized["corroboration_tier"])
            self.assertEqual(outcome.evidence["status"], "unverified")
            self.assertIsNone(outcome.evidence["corroboration_tier"])
            self.assertNotEqual(outcome.evidence["normalized_record_sha256"], "0" * 64)
            self.assertTrue((root / "normalized/p0-04-dlt-2026014.json").exists())
            self.assertEqual(outcome.parse_result["normalization_blockers"], [])
            validate_schema_instance(environment, self.environment_schema)
            validate_schema_instance(outcome.evidence, self.evidence_schema)
            validate_schema_instance(outcome.normalized, self.normalized_schema)

    def test_evidence_verified_requires_nonnull_corroboration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            outcome = process_capture(
                game="dlt", issue_id="2026014",
                fetch=saved_fetch("dlt", fixture_bytes("p0_04_dlt_valid.html")),
                clock_check=passing_clock(), output_root=Path(temp),
            )
            verified = dict(outcome.evidence, status="verified")
            with self.assertRaises(ValidationError):
                validate_schema_instance(verified, self.evidence_schema)
            verified["corroboration_tier"] = "shared_upstream"
            validate_schema_instance(verified, self.evidence_schema)

    def test_evidence_nonverified_statuses_reject_nonnull_corroboration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            outcome = process_capture(
                game="dlt", issue_id="2026014",
                fetch=saved_fetch("dlt", fixture_bytes("p0_04_dlt_valid.html")),
                clock_check=passing_clock(), output_root=Path(temp),
            )
            for status in ("unavailable", "unverified", "conflicted", "invalid"):
                with self.subTest(status=status):
                    tampered = dict(outcome.evidence, status=status, corroboration_tier="primary_only")
                    with self.assertRaises(ValidationError):
                        validate_schema_instance(tampered, self.evidence_schema)
            verified = dict(outcome.evidence, status="verified", corroboration_tier="primary_only")
            validate_schema_instance(verified, self.evidence_schema)

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
            self.assertIsNone(outcome.evidence["corroboration_tier"])
            self.assertNotEqual(outcome.evidence["status"], "verified")
            self.assertFalse((root / "normalized/p0-04-dlt-2026014.json").exists())
            validate_schema_instance(outcome.evidence, self.evidence_schema)

    def test_nonidentity_content_encoding_never_reaches_character_or_field_parsing(self) -> None:
        raw = fixture_bytes("p0_04_dlt_valid.html")
        base = saved_fetch("dlt", raw)
        encoded = FetchResult(
            requested_url=base.requested_url, redirect_chain=base.redirect_chain, final_url=base.final_url,
            retrieved_at=base.retrieved_at, raw_body=base.raw_body,
            response_headers=(*base.response_headers, {"name": "Content-Encoding", "value": "gzip"}),
            redacted_header_names=base.redacted_header_names,
        )
        with tempfile.TemporaryDirectory() as temp:
            outcome = process_capture(
                game="dlt", issue_id="2026014", fetch=encoded,
                clock_check=passing_clock(), output_root=Path(temp),
            )
            self.assertIsNone(outcome.parse_result)
            self.assertIsNone(outcome.normalized)
            self.assertFalse(outcome.evidence["content_decoding_applied"])
            self.assertFalse(outcome.evidence["character_decoding_applied"])
            self.assertIsNone(outcome.evidence["character_codec"])
            self.assertFalse(outcome.evidence["field_parsing_applied"])
            self.assertFalse(outcome.evidence["field_parsing_succeeded"])
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
            manifest_path = root / "evidence-manifest.jsonl"
            manifest = load_jsonl(manifest_path)
            parsed = load_json(root / "parsed/p0-04-ssq-2026014.json")
            normalized = load_json(root / "normalized/p0-04-ssq-2026014.json")
            environment_path = root / "environment-lock.json"
            self.assertFalse((root / "p0-04-environment-lock.json").exists())
            self.assertFalse((root / "p0-04-evidence-manifest.jsonl").exists())
            self.assertEqual(manifest, [outcome.evidence])
            self.assertEqual(parsed, outcome.parse_result)
            self.assertEqual(normalized, outcome.normalized)
            self.assertEqual(
                hashlib.sha256(environment_path.read_bytes()).hexdigest(),
                outcome.evidence["environment_lock_sha256"],
            )
            self.assertEqual(load_existing_environment(root), outcome.environment_lock)
            validate_schema_instance(manifest[0], self.evidence_schema)
            validate_schema_instance(normalized, self.normalized_schema)
            write_run_artifacts(root, [outcome])
            self.assertEqual(load_jsonl(manifest_path), [outcome.evidence])

    def test_existing_payload_and_manifest_conflicts_are_never_overwritten(self) -> None:
        raw = fixture_bytes("p0_04_dlt_valid.html")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outcome = process_capture(
                game="dlt", issue_id="2026014", fetch=saved_fetch("dlt", raw),
                clock_check=passing_clock(), output_root=root,
            )
            write_run_artifacts(root, [outcome])
            conflicting_fetch = saved_fetch("dlt", raw + b"\n")
            with self.assertRaisesRegex(AcquisitionError, "raw payload conflict"):
                process_capture(
                    game="dlt", issue_id="2026014", fetch=conflicting_fetch,
                    clock_check=passing_clock(), output_root=root,
                )
            conflicting_evidence = dict(outcome.evidence)
            conflicting_evidence["clock_offset_seconds"] = 1
            conflicting_outcome = type(outcome)(conflicting_evidence, outcome.normalized, outcome.parse_result, outcome.environment_lock)
            with self.assertRaisesRegex(AcquisitionError, "evidence_id conflict"):
                write_run_artifacts(root, [conflicting_outcome])


class OfflineCaptureVerificationTests(unittest.TestCase):
    def test_fixture_capture_verifies_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clock_path = build_fixture_capture(root)
            result, stdout, stderr = run_fixture_verifier(root, clock_path)
            self.assertEqual(result, 0, stderr)
            self.assertIn('"verified_entries":1', stdout)
            self.assertIn('"network_used":false', stdout)

    def test_raw_tamper_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clock_path = build_fixture_capture(root)
            raw_path = root / "raw/p0-04-dlt-2026014.html"
            raw_path.write_bytes(raw_path.read_bytes() + b"tamper")
            result, _stdout, stderr = run_fixture_verifier(root, clock_path)
            self.assertNotEqual(result, 0)
            self.assertIn("raw payload SHA-256 mismatch", stderr)

    def test_normalized_tamper_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clock_path = build_fixture_capture(root)
            normalized_path = root / "normalized/p0-04-dlt-2026014.json"
            normalized = load_json(normalized_path)
            normalized["record_id"] = "tampered-record"
            normalized_path.write_bytes(canonical_json_bytes(normalized) + b"\n")
            result, _stdout, stderr = run_fixture_verifier(root, clock_path)
            self.assertNotEqual(result, 0)
            self.assertIn("normalized canonical hash mismatch", stderr)

    def test_manifest_hash_tamper_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clock_path = build_fixture_capture(root)
            rewrite_manifest(root, lambda entry: entry.__setitem__("normalized_record_sha256", "0" * 64))
            result, _stdout, stderr = run_fixture_verifier(root, clock_path)
            self.assertNotEqual(result, 0)
            self.assertIn("normalized canonical hash mismatch", stderr)

    def test_forbidden_header_name_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clock_path = build_fixture_capture(root)
            rewrite_manifest(root, lambda entry: entry["redacted_header_names"].append("Authorization"))
            result, _stdout, stderr = run_fixture_verifier(root, clock_path)
            self.assertNotEqual(result, 0)
            self.assertIn("sensitive redacted header names", stderr)

    def test_request_codec_and_processing_stage_tampering_returns_nonzero(self) -> None:
        mutations = {
            "request_method": lambda entry: entry.__setitem__("request_method", "POST"),
            "character_codec": lambda entry: entry.__setitem__("character_codec", "gb18030"),
            "content_stage": lambda entry: entry.__setitem__("content_decoding_applied", True),
            "character_stage": lambda entry: entry.update(character_decoding_applied=False, character_codec=None, field_parsing_applied=False, field_parsing_succeeded=False),
            "field_applied_stage": lambda entry: entry.update(field_parsing_applied=False, field_parsing_succeeded=False),
            "field_success_stage": lambda entry: entry.__setitem__("field_parsing_succeeded", False),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                clock_path = build_fixture_capture(root)
                rewrite_manifest(root, mutation)
                result, _stdout, _stderr = run_fixture_verifier(root, clock_path)
                self.assertNotEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
