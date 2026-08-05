from __future__ import annotations

import io
import hashlib
import json
import multiprocessing
import tempfile
import time
import unittest
from datetime import date
from email.message import Message
from pathlib import Path

from lottery_data.parsers import get_parser, get_versioned_parser
from lottery_data.models import ContractViolation, make_live_child_authorization_sha256, validate_object
from lottery_data.steps.live import (
    HostThrottle,
    build_gd_announcement_request,
    fetch_to_raw,
    validate_gd_announcement_result,
    validate_live_request,
)
from lottery_data.steps.live_policy import LivePolicyError, build_live_request_plan, load_live_policy


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config" / "phase1" / "live-source-policy.json"
LEGACY_POLICY = ROOT / "tests" / "phase1" / "fixtures" / "live-policy" / "live-source-policy-v1.1.1.json"
GD_DISCOVERY_WITNESS = ROOT / "tests" / "phase1" / "fixtures" / "real" / "gd-discovery-witness-20260803.json"


def _legacy_policy() -> dict:
    return json.loads(LEGACY_POLICY.read_text(encoding="utf-8"))

# Representative structure recorded by A1 review; it is deliberately not labelled a captured live response.
SWLC_REPRESENTATIVE_STRUCTURE = b"""<!doctype html><meta charset=utf-8>
<table class="ssq-previous-table"><tbody>
<tr><td>2026088</td><td>2026-08-02(Sun)</td><td>06 07 11 18 22 33 05</td><td>442158322</td></tr>
<tr><td>2026087</td><td>2026-07-30(Thu)</td><td>04 06 10 18 23 31</td><td>11</td><td>473791046</td></tr>
</tbody></table>"""

SWLC_LIVE_MINIMAL_STRUCTURE = """<!doctype html><meta charset=utf-8>
<table class="ssq-previous-table"><tbody><tr>
<td>2026088</td><td>2026-08-02(日)</td>
<td class="drawNotice_shuangse"><p>06</p><p>07</p><p>11</p><p>18</p><p>22</p><p>33</p></td>
<td class="drawNotice_shuangse"><p class="blue">05</p></td>
<td>373747432</td><td>34</td><td>2941176</td><td>442158322</td><td></td>
</tr></tbody></table>""".encode("utf-8")

GD_REPRESENTATIVE_ANNOUNCEMENT = """<!doctype html><meta charset="utf-8">
<h2>第26086期开奖公告</h2><p>开奖日期：2026年8月1日</p>
<div class="dlt_Lottery"><ul><li>本期开奖号码：</li><li>10 11 18 22 35</li><li>06 12</li></ul></div>
""".encode()


def _throttle_process(root: str, start, output) -> None:
    start.wait()
    HostThrottle(Path(root), 2.05).wait("shared.example")
    output.put(time.time())


class _Response:
    def __init__(self, body: bytes, url: str, *, content_length: str | None = None, content_type: str = "text/html; charset=utf-8") -> None:
        self.body = io.BytesIO(body)
        self.status = 200
        self.headers = Message()
        self.headers["Content-Length"] = str(len(body)) if content_length is None else content_length
        self.headers["Content-Type"] = content_type
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self.body.read(amount)

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url


class _Opener:
    def __init__(self, body: bytes, *, response_url: str | None = None, content_length: str | None = None, content_type: str = "text/html; charset=utf-8") -> None:
        self.body = body
        self.response_url = response_url
        self.content_length = content_length
        self.content_type = content_type
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.method, timeout))
        return _Response(self.body, self.response_url or request.full_url, content_length=self.content_length, content_type=self.content_type)


class _MultiLengthOpener(_Opener):
    def open(self, request, timeout):
        response = super().open(request, timeout)
        response.headers["Content-Length"] = "1"
        return response


class LiveComponentTests(unittest.TestCase):
    def test_policy_hash_expiry_and_four_request_plan(self) -> None:
        policy = load_live_policy(POLICY, today=date(2026, 8, 16))
        plan = build_live_request_plan(policy, ("ssq", "dlt"))
        self.assertEqual(len(plan), 4)
        self.assertEqual([(item["source_id"], item["game"], item["request_kind"]) for item in plan], [
            ("ydniu", "ssq", "history"), ("swlc", "ssq", "history"),
            ("ydniu", "dlt", "history"), ("gdlottery", "dlt", "history"),
        ])
        with self.assertRaises(LivePolicyError) as caught:
            load_live_policy(POLICY, today=date(2026, 8, 17))
        self.assertEqual((caught.exception.stage, caught.exception.exit_code), ("preflight", 4))

    def test_swlc_and_gd_versioned_parsers(self) -> None:
        swlc = get_versioned_parser("phase1-swlc-live-parser", "1.0.0")(SWLC_REPRESENTATIVE_STRUCTURE, "ssq")
        self.assertEqual(swlc[0]["front_numbers"], [6, 7, 11, 18, 22, 33])
        self.assertEqual(swlc[1]["back_numbers"], [11])
        gd = get_versioned_parser("phase1-gdlottery-live-parser", "2.0.0")(GD_REPRESENTATIVE_ANNOUNCEMENT, "dlt")
        self.assertEqual((gd[0]["issue_id"], gd[0]["back_numbers"]), ("2026086", [6, 12]))

    def test_swlc_live_p_elements_preserve_ball_boundaries(self) -> None:
        rows = get_versioned_parser("phase1-swlc-live-parser", "1.0.0")(SWLC_LIVE_MINIMAL_STRUCTURE, "ssq")
        self.assertEqual(rows, [{
            "raw_issue_id": "2026088", "issue_id": "2026088", "draw_date_local": "2026-08-02",
            "front_numbers": [6, 7, 11, 18, 22, 33], "back_numbers": [5],
        }])

    def test_swlc_rejects_continuous_ambiguous_and_illegal_ball_structures(self) -> None:
        cases = {
            "continuous": "<td>060711182233</td><td>05</td>",
            "ambiguous": "<td>06 07 11 18 22 33 05</td><td>01 02 03 04 05 06 07</td>",
            "illegal": "<td><p>06</p><p>07</p><p>11</p><p>18</p><p>22</p><p>22</p></td><td><p>05</p></td>",
        }
        parser = get_versioned_parser("phase1-swlc-live-parser", "1.0.0")
        for name, number_cells in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "draw numbers are not uniquely identifiable"):
                parser((
                    '<table class="ssq-previous-table"><tr><td>2026088</td>'
                    '<td>2026-08-02</td>' + number_cells + '</tr></table>'
                ).encode("utf-8"), "ssq")

    def test_gd_discovery_uses_only_observed_link(self) -> None:
        policy = _legacy_policy()
        witness = json.loads(GD_DISCOVERY_WITNESS.read_text(encoding="utf-8"))
        body = witness["fragment_utf8"].encode("utf-8")
        request = build_gd_announcement_request(policy, body, sequence=5)
        self.assertEqual(request["request_id"], "live-gdlottery-dlt-announcement")
        self.assertEqual(request["expected_raw_issue_id"], "2026086")
        self.assertEqual(request["url"], "https://www.gdlottery.cn/f_html/kjgg/P085_26086.html")
        self.assertEqual(request["parent_request_id"], "live-gdlottery-dlt-discovery")
        self.assertEqual(request["discovery_request_id"], "live-gdlottery-dlt-discovery")
        self.assertEqual(
            request["discovery_raw_ref"],
            f"raw/gdlottery/dlt/sha256/{hashlib.sha256(body).hexdigest()}.raw",
        )
        self.assertEqual(request["authorization_sha256"], make_live_child_authorization_sha256(request))
        self.assertEqual(validate_live_request(request, policy, gd_discovery_body=body), request)

    def test_gd_discovery_witness_fragment_is_self_verifying(self) -> None:
        witness = json.loads(GD_DISCOVERY_WITNESS.read_text(encoding="utf-8"))
        fragment = witness["fragment_utf8"].encode("utf-8")
        self.assertEqual(len(fragment), witness["fragment_length_bytes"])
        self.assertEqual(hashlib.sha256(fragment).hexdigest(), witness["fragment_sha256"])
        self.assertRegex(witness["raw_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(witness["raw_size_bytes"], witness["fragment_length_bytes"])
        self.assertGreaterEqual(witness["fragment_offset_bytes"], 0)
        self.assertLessEqual(
            witness["fragment_offset_bytes"] + witness["fragment_length_bytes"],
            witness["raw_size_bytes"],
        )

    def test_gd_discovery_runtime_authorization_matches_manifest_validator(self) -> None:
        policy = _legacy_policy()
        manifest = json.loads((
            ROOT / "tests" / "phase1" / "fixtures" / "live-execution" / "valid-manifest-v1.1.json"
        ).read_text(encoding="utf-8"))
        validate_object("RunManifestV1.1", manifest)
        discovery = manifest["request_plan"][3]
        self.assertEqual(validate_live_request(discovery, policy), discovery)

        missing = dict(discovery)
        missing.pop("child_authorization")
        changed = {**discovery, "child_authorization": {**discovery["child_authorization"], "max_children": 2}}
        for name, request in (("missing", missing), ("changed", changed)):
            with self.subTest(name=name), self.assertRaises(LivePolicyError):
                validate_live_request(request, policy)
            invalid_manifest = {**manifest, "request_plan": [dict(row) for row in manifest["request_plan"]]}
            invalid_manifest["request_plan"][3] = request
            with self.subTest(name=f"manifest-{name}"), self.assertRaises(ContractViolation):
                validate_object("RunManifestV1.1", invalid_manifest)

    def test_dynamic_id_short_expected_issue_and_legacy_authorization_hash_are_rejected(self) -> None:
        policy = _legacy_policy()
        body = json.loads(GD_DISCOVERY_WITNESS.read_text(encoding="utf-8"))["fragment_utf8"].encode("utf-8")
        request = build_gd_announcement_request(policy, body, sequence=5)
        legacy_hash = hashlib.sha256(
            (
                "live-gdlottery-dlt-discovery\n"
                + hashlib.sha256(body).hexdigest()
                + "\nhttps://www.gdlottery.cn/f_html/kjgg/P085_26086.html\n26086\n"
            ).encode("utf-8")
        ).hexdigest()
        mutations = (
            dict(request, request_id="live-gdlottery-dlt-announcement-26086"),
            dict(request, expected_raw_issue_id="26086"),
            dict(request, authorization_sha256=legacy_hash),
            dict(request, discovery_raw_sha256="0" * 64),
            dict(request, discovery_raw_ref=f"raw/gdlottery/dlt/sha256/{'0' * 64}.raw"),
        )
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(LivePolicyError):
                validate_live_request(changed, policy, gd_discovery_body=body)

    def test_request_validation_rejects_arbitrary_urls_and_ssrf_forms(self) -> None:
        policy = _legacy_policy()
        original = build_live_request_plan(policy, ("ssq", "dlt"))[0]
        for unsafe in (
            "https://evil.example/x", "https://user:pass@www.ydniu.com/x",
            "https://www.ydniu.com:443/x", "https://127.0.0.1/x", "https://169.254.1.1/x",
        ):
            changed = dict(original, url=unsafe)
            with self.subTest(unsafe=unsafe), self.assertRaises(LivePolicyError):
                validate_live_request(changed, policy)
        mutated_policy = dict(policy)
        mutated_policy["sources"] = [dict(item) for item in policy["sources"]]
        mutated_policy["sources"][0]["endpoints"] = dict(mutated_policy["sources"][0]["endpoints"], ssq="https://evil.example/x")
        matching_mutation = dict(original, url="https://evil.example/x")
        with self.assertRaises(LivePolicyError):
            validate_live_request(matching_mutation, mutated_policy)

    def test_gd_selector_ambiguity_and_forged_mapping_are_rejected(self) -> None:
        policy = _legacy_policy()
        invalid = {
            "outside-btn": '<a href="/f_html/kjgg/P085_26086.html">x</a>',
            "zero-approved-path": '<div class="btn"><a href="/html/help/lskj.html">history</a></div>',
            "two-paths": ('<div class="btn"><a href="/f_html/kjgg/P085_26086.html">x</a>'
                          '<a href="/f_html/kjgg/P085_26087.html">y</a></div>'),
            "duplicate-elements": ('<div class="btn"><a href="/f_html/kjgg/P085_26086.html">x</a>'
                                   '<a href="/f_html/kjgg/P085_26086.html">x-again</a></div>'),
        }
        for name, body in invalid.items():
            with self.subTest(name=name), self.assertRaises(LivePolicyError) as caught:
                build_gd_announcement_request(policy, body.encode("utf-8"), sequence=5)
            self.assertEqual(caught.exception.exit_code, 2)
        old_title_shape = '<div class="btn"><a title="查看中奖详情" href="/f_html/kjgg/P085_26086.html">x</a></div>'.encode()
        self.assertEqual(build_gd_announcement_request(policy, old_title_shape, sequence=5)["expected_raw_issue_id"], "2026086")
        forged = {"request_kind": "announcement", "url": "https://www.gdlottery.cn/f_html/kjgg/P085_26086.html"}
        with self.assertRaises(LivePolicyError):
            validate_live_request(forged, policy)

    def test_gd_discovered_candidate_retains_origin_query_fragment_and_path_guards(self) -> None:
        policy = _legacy_policy()
        hrefs = (
            "https://evil.example/f_html/kjgg/P085_26086.html",
            "/f_html/kjgg/P085_26086.html?copy=1",
            "/f_html/kjgg/P085_26086.html#fragment",
        )
        for href in hrefs:
            body = f'<div class="btn"><a href="{href}">x</a><a href="/html/help/lskj.html">history</a></div>'.encode()
            with self.subTest(href=href), self.assertRaises(LivePolicyError) as caught:
                build_gd_announcement_request(policy, body, sequence=5)
            self.assertEqual(caught.exception.exit_code, 4)
        wrong_path = b'<div class="btn"><a href="/f_html/other/P085_26086.html">x</a></div>'
        with self.assertRaises(LivePolicyError) as caught:
            build_gd_announcement_request(policy, wrong_path, sequence=5)
        self.assertEqual(caught.exception.exit_code, 2)

    def test_gd_href_heading_and_normalized_issue_must_agree(self) -> None:
        policy = _legacy_policy()
        body = json.loads(GD_DISCOVERY_WITNESS.read_text(encoding="utf-8"))["fragment_utf8"].encode("utf-8")
        request = build_gd_announcement_request(policy, body, sequence=5)
        validate_gd_announcement_result(request, [{"raw_issue_id": "26086", "issue_id": "2026086"}])
        wrong = [{"raw_issue_id": "26087", "issue_id": "2026087"}]
        with self.assertRaises(LivePolicyError):
            validate_gd_announcement_result(request, wrong)

    def test_mock_fetch_persists_exact_raw_before_return(self) -> None:
        policy = load_live_policy(POLICY, today=date(2026, 8, 2))
        request = build_live_request_plan(policy, ("ssq",))[0]
        opener = _Opener(b"frozen mock response")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = fetch_to_raw(request, policy, root / "raw", root / "throttle", opener=opener)
            self.assertEqual(result["raw_path"].read_bytes(), b"frozen mock response")
            self.assertEqual(opener.calls[0][1:], ("GET", 30))

    def test_host_throttle_enforces_interval(self) -> None:
        class Clock:
            now = 10.0
            def __call__(self):
                return self.now
            def sleep(self, seconds):
                self.now += seconds
        clock = Clock()
        with tempfile.TemporaryDirectory() as folder:
            throttle = HostThrottle(Path(folder), 2.0, clock=clock, sleeper=clock.sleep)
            throttle.wait("example.test")
            clock.now += 1.0
            throttle.wait("example.test")
            self.assertEqual(clock.now, 12.0)

    def test_host_throttle_is_cross_process(self) -> None:
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as folder:
            start = context.Event()
            output = context.Queue()
            processes = [context.Process(target=_throttle_process, args=(folder, start, output)) for _ in range(2)]
            for process in processes:
                process.start()
            start.set()
            stamps = sorted(output.get(timeout=10) for _ in processes)
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertGreaterEqual(stamps[1] - stamps[0], 2.0)

    def test_malformed_response_metadata_is_stably_classified(self) -> None:
        policy = load_live_policy(POLICY, today=date(2026, 8, 2))
        request = build_live_request_plan(policy, ("ssq",))[0]
        cases = [
            (_Opener(b"x", content_length="-1"), "http_non_success_or_response_too_large", 3),
            (_Opener(b"x", content_length="abc"), "http_non_success_or_response_too_large", 3),
            (_Opener(b"x", content_type="text/html; charset=gbk"), "content_type_encoding_or_parse_failure", 2),
            (_Opener(b"x", response_url="https://evil.example/x"), "redirect_policy_violation", 4),
            (_MultiLengthOpener(b"x"), "http_non_success_or_response_too_large", 3),
        ]
        for opener, category, exit_code in cases:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(LivePolicyError) as caught:
                    fetch_to_raw(request, policy, Path(folder) / "raw", Path(folder) / "throttle", opener=opener)
                self.assertEqual((caught.exception.category, caught.exception.exit_code), (category, exit_code))

    def test_old_gd_snapshot_parser_is_unchanged_and_registered(self) -> None:
        raw = ROOT / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z" / "raw" / "gdlottery" / "dlt" / "page-001.html"
        records = get_parser("gdlottery")(raw.read_bytes(), "dlt")
        self.assertEqual(records[0]["issue_id"], "2026026")


if __name__ == "__main__":
    unittest.main()
