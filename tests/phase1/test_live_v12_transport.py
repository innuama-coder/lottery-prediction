from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from datetime import date
from email.message import Message
from pathlib import Path

from lottery_data.steps.live import build_gd_announcement_request, fetch_to_raw, validate_live_request
from lottery_data.steps.live_policy import (
    LIVE_POLICY_SHA256,
    LivePolicyError,
    build_live_request_plan,
    load_live_policy,
)


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "tests" / "phase1" / "fixtures" / "live-policy" / "live-source-policy-v1.2.0.json"
LEGACY_POLICY = ROOT / "tests" / "phase1" / "fixtures" / "live-policy" / "live-source-policy-v1.1.1.json"
LEGACY_MANIFEST = ROOT / "tests" / "phase1" / "fixtures" / "live-execution" / "valid-manifest-v1.1.json"
LEGACY_SHA256 = "442eac435a16bc5ea9d521b227bd5ad87f3592ba47af67f8eead64c1f2c14fb1"


class _Response:
    def __init__(
        self, body: bytes, url: str, *, content_type: str,
        content_length: str | None = None, content_encoding: str | None = None,
        duplicate_content_length: bool = False, duplicate_content_type: bool = False,
    ) -> None:
        self.status = 200
        self._body = io.BytesIO(body)
        self._url = url
        self.headers = Message()
        self.headers["Content-Length"] = str(len(body)) if content_length is None else content_length
        if duplicate_content_length:
            self.headers["Content-Length"] = str(len(body))
        self.headers["Content-Type"] = content_type
        if duplicate_content_type:
            self.headers["Content-Type"] = content_type
        if content_encoding is not None:
            self.headers["Content-Encoding"] = content_encoding

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._url

    def read(self, amount: int = -1) -> bytes:
        return self._body.read(amount)


class _Opener:
    def __init__(self, body: bytes, **response_options) -> None:
        self.body = body
        self.response_options = response_options
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        url = self.response_options.pop("response_url", request.full_url)
        return _Response(self.body, url, **self.response_options)


class LiveV12TransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_live_policy(POLICY, today=date(2026, 8, 3))
        self.legacy_policy = json.loads(LEGACY_POLICY.read_text(encoding="utf-8"))
        self.plan = build_live_request_plan(self.policy, ("ssq", "dlt"))
        self.gd = self.plan[3]

    def _fetch(self, request: dict, opener: _Opener):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        return fetch_to_raw(request, self.policy, root / "raw", root / "throttle", opener=opener)

    def test_legacy_policy_is_byte_frozen_and_current_plan_is_four_static_histories(self) -> None:
        self.assertEqual(hashlib.sha256(LEGACY_POLICY.read_bytes()).hexdigest(), LEGACY_SHA256)
        self.assertEqual(hashlib.sha256(POLICY.read_bytes()).hexdigest(), LIVE_POLICY_SHA256)
        self.assertEqual(self.policy["live_policy_schema_version"], "1.2.0")
        self.assertFalse(self.policy["production_collection_approved"])
        self.assertFalse(self.policy["redistribution_approved"])
        self.assertEqual([row["request_id"] for row in self.plan], [
            "live-ydniu-ssq-history", "live-swlc-ssq-history",
            "live-ydniu-dlt-history", "live-gdlottery-dlt-history",
        ])
        self.assertTrue(all(row["request_kind"] == "history" for row in self.plan))
        self.assertTrue(all("child_authorization" not in row for row in self.plan))
        self.assertEqual(self.gd, {
            "request_id": "live-gdlottery-dlt-history", "sequence": 4,
            "source_id": "gdlottery", "publisher_id": "gdlottery-publisher", "game": "dlt",
            "method": "GET", "url": "https://www.gdlottery.cn/f_html/kjgg/gameNumber.json",
            "request_kind": "history", "parser_id": "phase1-gdlottery-history-parser",
            "parser_version": "1.0.0",
            "response_profile": {"expected_media_type": "application/json", "max_response_bytes": 2097152},
        })
        self.assertEqual(
            [row["response_profile"] for row in self.plan[:3]],
            [{"expected_media_type": "text/html", "max_response_bytes": 1048576}] * 3,
        )

    def test_legacy_discovery_helper_and_request_validation_remain_available(self) -> None:
        body = b'<div class="btn"><a href="/f_html/kjgg/P085_26086.html">draw</a></div>'
        request = build_gd_announcement_request(self.legacy_policy, body, sequence=5)
        self.assertEqual(request["request_id"], "live-gdlottery-dlt-announcement")
        self.assertEqual(validate_live_request(request, self.legacy_policy, gd_discovery_body=body), request)

    def test_policy_version_not_request_shape_selects_the_profile(self) -> None:
        legacy_plan = build_live_request_plan(self.legacy_policy, ("ssq", "dlt"))
        self.assertEqual([(row["request_id"], row["request_kind"]) for row in legacy_plan], [
            ("live-ydniu-ssq-history", "history"),
            ("live-swlc-ssq-history", "history"),
            ("live-ydniu-dlt-history", "history"),
            ("live-gdlottery-dlt-discovery", "discovery"),
        ])
        self.assertTrue(all("response_profile" not in row for row in legacy_plan))
        legacy_discovery = json.loads(LEGACY_MANIFEST.read_text(encoding="utf-8"))["request_plan"][3]
        self.assertEqual(validate_live_request(legacy_discovery, self.legacy_policy), legacy_discovery)
        with self.assertRaises(LivePolicyError):
            validate_live_request(legacy_discovery, self.policy)
        with self.assertRaises(LivePolicyError):
            validate_live_request(self.gd, self.legacy_policy)

        body = b'<div class="btn"><a href="/f_html/kjgg/P085_26086.html">draw</a></div>'
        legacy_child = build_gd_announcement_request(self.legacy_policy, body, sequence=5)
        with self.assertRaises(LivePolicyError):
            validate_live_request(legacy_child, self.policy, gd_discovery_body=body)

        unknown = {**self.policy, "live_policy_schema_version": "9.9.9"}
        with self.assertRaises(LivePolicyError):
            build_live_request_plan(unknown, ("ssq", "dlt"))
        with self.assertRaises(LivePolicyError):
            validate_live_request(self.gd, unknown)

    def test_legacy_transport_never_inherits_the_v12_two_mibibyte_cap(self) -> None:
        legacy_request = build_live_request_plan(self.legacy_policy, ("ssq",))[0]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        with self.assertRaises(LivePolicyError) as caught:
            fetch_to_raw(
                legacy_request, self.legacy_policy, root / "raw", root / "throttle",
                opener=_Opener(b"x" * (1048576 + 1), content_type="text/html"),
            )
        self.assertEqual((caught.exception.category, caught.exception.exit_code), ("http_non_success_or_response_too_large", 3))

    def test_request_validation_rejects_query_profile_mutation_and_dynamic_child(self) -> None:
        self.assertEqual(validate_live_request(self.gd, self.policy), self.gd)
        mutations = [
            {**self.gd, "url": self.gd["url"] + "?1722600000000"},
            {**self.gd, "response_profile": {"expected_media_type": "application/json", "max_response_bytes": 1048577}},
            {**self.gd, "request_kind": "discovery"},
            {**self.gd, "child_authorization": {}},
        ]
        for request in mutations:
            with self.subTest(request=request), self.assertRaises(LivePolicyError):
                validate_live_request(request, self.policy)
        with self.assertRaises(LivePolicyError):
            validate_live_request({"request_kind": "announcement", "request_id": "live-gdlottery-dlt-announcement"}, self.policy)

    def test_endpoint_specific_cap_and_identity_accept_encoding(self) -> None:
        gd_body = b" " * (1048576 + 1)
        gd_opener = _Opener(gd_body, content_type="application/json")
        result = self._fetch(self.gd, gd_opener)
        self.assertEqual(result["content_length"], len(gd_body))
        sent = gd_opener.requests[0][0]
        self.assertEqual(sent.get_header("Accept"), "application/json")
        self.assertEqual(sent.get_header("Accept-encoding"), "identity")

        html = self.plan[0]
        with self.assertRaises(LivePolicyError) as caught:
            self._fetch(html, _Opener(b"x" * (1048576 + 1), content_type="text/html"))
        self.assertEqual((caught.exception.category, caught.exception.exit_code), ("http_non_success_or_response_too_large", 3))

        with self.assertRaises(LivePolicyError) as caught:
            self._fetch(self.gd, _Opener(b"x" * (2097152 + 1), content_type="application/json"))
        self.assertEqual((caught.exception.category, caught.exception.exit_code), ("http_non_success_or_response_too_large", 3))

    def test_json_metadata_length_encoding_and_redirect_are_fail_closed(self) -> None:
        cases = {
            "wrong-media": _Opener(b"{}", content_type="text/html"),
            "wrong-charset": _Opener(b"{}", content_type="application/json; charset=gbk"),
            "duplicate-media": _Opener(b"{}", content_type="application/json", duplicate_content_type=True),
            "gzip": _Opener(b"{}", content_type="application/json", content_encoding="gzip"),
            "duplicate-length": _Opener(b"{}", content_type="application/json", duplicate_content_length=True),
            "malformed-length": _Opener(b"{}", content_type="application/json", content_length="2, 2"),
            "declared-over-cap": _Opener(b"{}", content_type="application/json", content_length="2097153"),
            "changed-url": _Opener(b"{}", content_type="application/json", response_url=self.gd["url"] + "?cache=1"),
        }
        for name, opener in cases.items():
            with self.subTest(name=name), self.assertRaises(LivePolicyError):
                self._fetch(self.gd, opener)

    def test_json_media_type_allows_absent_or_utf8_charset_only(self) -> None:
        for content_type in ("application/json", "application/json; charset=utf-8", 'application/json; charset="UTF-8"'):
            with self.subTest(content_type=content_type):
                result = self._fetch(self.gd, _Opener(b"{}", content_type=content_type))
                self.assertEqual(result["content_length"], 2)


if __name__ == "__main__":
    unittest.main()
