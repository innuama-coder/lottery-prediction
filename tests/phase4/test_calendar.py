from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from lottery_system.phase4.calendar import (
    CALENDAR_CONTRACT_ID,
    CalendarAmbiguous,
    build_calendar_release,
    canonical_entries,
    derive_calendar_release_id,
    load_calendar_build_fixture,
    load_calendar_policy,
    validate_calendar_release,
)
from lottery_system.phase4.cli_kernel import main


ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "config/phase4/calendar-policy.json"
FIXTURE = ROOT / "tests/phase4/fixtures/calendar/build-input.json"
LEGACY_PREP_INSTALLED = (ROOT / "artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T03/data-custodian-source-review-I01/receipt.json").is_file()


class CalendarTests(unittest.TestCase):
    @unittest.skipUnless(LEGACY_PREP_INSTALLED, "superseded T00-T24 calendar evidence is not installed")
    def test_explicit_release_is_content_derived_schema_valid_and_zoneinfo_bound(self) -> None:
        policy, entries = load_calendar_build_fixture(ROOT, FIXTURE)
        release_id = derive_calendar_release_id(policy, entries)
        release = build_calendar_release(policy, entries, calendar_release_id=release_id)
        self.assertEqual(validate_calendar_release(release, contract_id=CALENDAR_CONTRACT_ID), release)
        schema = json.loads((ROOT / "schemas/phase4/calendar.schema.json").read_text())
        self.assertEqual([error.message for error in Draft202012Validator(schema).iter_errors(release)], [])
        if os.name == "nt":
            self.assertTrue(release["tzdata_identity"].startswith(("tzdata-package:Asia/Shanghai:", "iana-asia-shanghai-post-1991-fixed-utc+08:")))
        else:
            self.assertIn("zoneinfo:Asia/Shanghai:sha256:", release["tzdata_identity"])

    @unittest.skipUnless(LEGACY_PREP_INSTALLED, "superseded T00-T24 calendar evidence is not installed")
    def test_duplicate_rollback_wrong_rule_and_identity_fail_closed(self) -> None:
        policy, entries = load_calendar_build_fixture(ROOT, FIXTURE)
        cases = []
        duplicate = [dict(row) for row in entries]
        duplicate.append(dict(entries[-1]))
        cases.append(duplicate)
        rollback = [dict(row) for row in entries]
        rollback[2]["target_issue"] = "2026087"
        cases.append(rollback)
        wrong_rule = [dict(row) for row in entries]
        wrong_rule[0]["rule_id"] = "dlt-ns-35c5-12c2-v1"
        cases.append(wrong_rule)
        for index, changed in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(CalendarAmbiguous):
                canonical_entries(changed)
        release_id = derive_calendar_release_id(policy, entries)
        with self.assertRaises(CalendarAmbiguous):
            build_calendar_release(policy, entries, calendar_release_id=release_id + "x")

    def test_policy_mutations_and_hardcoded_conversion_are_rejected(self) -> None:
        original = json.loads(POLICY.read_text())
        mutations = (
            lambda value: value.__setitem__("timezone", "UTC"),
            lambda value: value["actions"]["predict_lock"].__setitem__("planned_at_utc_time", "17:00:00Z"),
            lambda value: value["actions"]["prepare"].__setitem__("contract_expression", "weekly_guess"),
            lambda value: value["validation_rules"].__setitem__("server_local_timezone_allowed", True),
            lambda value: value["validation_rules"].__setitem__("utc_offset_hardcoding_as_conversion_allowed", True),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as raw:
                changed = json.loads(json.dumps(original))
                mutate(changed)
                path = Path(raw) / "policy.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(CalendarAmbiguous):
                    load_calendar_policy(path, draw_dates=[])

    @unittest.skipUnless(LEGACY_PREP_INSTALLED, "superseded T00-T24 calendar evidence is not installed")
    def test_server_timezone_does_not_change_zoneinfo_release(self) -> None:
        policy, entries = load_calendar_build_fixture(ROOT, FIXTURE)
        baseline = derive_calendar_release_id(policy, entries)
        if not hasattr(time, "tzset"):
            self.skipTest("tzset unavailable")
        with mock.patch.dict(os.environ, {"TZ": "America/Los_Angeles"}):
            time.tzset()
            try:
                self.assertEqual(derive_calendar_release_id(policy, entries), baseline)
            finally:
                os.environ.pop("TZ", None)
                time.tzset()

    @unittest.skipUnless(LEGACY_PREP_INSTALLED, "superseded T00-T24 calendar evidence is not installed")
    def test_calendar_validate_cli_provider_and_wrong_contract_terminal(self) -> None:
        policy, entries = load_calendar_build_fixture(ROOT, FIXTURE)
        release = build_calendar_release(policy, entries, calendar_release_id=derive_calendar_release_id(policy, entries))
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "calendar.json"
            path.write_text(json.dumps(release), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(["calendar", "validate", "--calendar", str(path), "--contract-id", CALENDAR_CONTRACT_ID])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "PASS")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(["calendar", "validate", "--calendar", str(path), "--contract-id", "wrong"])
            self.assertEqual(code, 20)
            self.assertEqual(json.loads(output.getvalue())["terminal"], "HOLD_CALENDAR_AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
