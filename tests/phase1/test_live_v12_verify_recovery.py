from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from lottery_data.models import ContractViolation, validate_live_event_stream
from lottery_data.steps.recovery import _manifest_profile
from lottery_data.steps.replay import ReplayContractError, _live_replay_requests, _replay_profile
from lottery_data.steps.verify import VerifyContractError, _live_manifest_profile
from tests.phase1.test_live_execution_spec import run_result as v11_result, valid_stream
from tests.phase1.test_live_execution_v12_spec import event, run_result, success_stream


ROOT = Path(__file__).resolve().parents[2]
V11 = ROOT / "tests/phase1/fixtures/live-execution/valid-manifest-v1.1.json"
V12 = ROOT / "tests/phase1/fixtures/live-execution-v1.2/valid-manifest-v1.2.json"
LEGACY_SHA = "442eac435a16bc5ea9d521b227bd5ad87f3592ba47af67f8eead64c1f2c14fb1"
CURRENT_SHA = "23b7fc1bd1d5d7518b345ee92dd8fd7a3172305b7478fe82175d7af38aa80a1b"


def manifest(path: Path, policy_sha: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["config_files"] = [{"ref": "config/live-source-policy.json", "sha256": policy_sha}]
    return value


class LiveV12VerifyRecoveryTests(unittest.TestCase):
    def test_all_consumers_dispatch_v11_and_v12_from_version_and_policy_identity(self) -> None:
        v11 = manifest(V11, LEGACY_SHA)
        v12 = manifest(V12, CURRENT_SHA)
        self.assertEqual(_live_manifest_profile(v11), ("1.1.0", LEGACY_SHA))
        self.assertEqual(_live_manifest_profile(v12), ("1.2.0", CURRENT_SHA))
        self.assertEqual((_manifest_profile(v11), _manifest_profile(v12)), ("live-v11", "live-v12"))
        self.assertEqual((_replay_profile(v11), _replay_profile(v12)), ("live-v11", "live-v12"))

    def test_unknown_mixed_and_policy_swaps_fail_closed(self) -> None:
        candidates = []
        wrong_policy = manifest(V12, LEGACY_SHA)
        candidates.append(wrong_policy)
        mixed = manifest(V12, CURRENT_SHA)
        mixed["run_schema_version"] = "1.1.0"
        candidates.append(mixed)
        unknown = manifest(V12, CURRENT_SHA)
        unknown["run_schema_version"] = "9.9.9"
        candidates.append(unknown)
        for candidate in candidates:
            with self.subTest(version=candidate["run_schema_version"], policy=candidate["config_files"][0]["sha256"]):
                with self.assertRaises(VerifyContractError):
                    _live_manifest_profile(candidate)
                with self.assertRaises((ValueError, ContractViolation)):
                    _manifest_profile(candidate)
                with self.assertRaises(ReplayContractError):
                    _replay_profile(candidate)

    def test_v12_success_terminal_counts_and_stream_closure(self) -> None:
        v12 = manifest(V12, CURRENT_SHA)
        expected = {"planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0}
        no_change = run_result(expected)
        validate_live_event_stream(v12, success_stream(v12), no_change)

        published_events = success_stream(v12, "run_published")
        published = copy.deepcopy(no_change)
        published.update(status="published", release_id="release-v12")
        validate_live_event_stream(v12, published_events, published)

        for terminal, status, exit_code in (
            ("run_rejected", "rejected", 4), ("run_interrupted", "interrupted", None),
        ):
            events = [
                event(1, "run_planned"), event(2, "run_started"),
                event(3, terminal, error_code="PROFILE_TEST", error_detail_ref="runs/live-v12-contract-fixture/error.json"),
            ]
            result = run_result({"planned": 4, "started": 0, "succeeded": 0, "failed": 0, "not_started": 4})
            result.update(status=status, exit_code=exit_code)
            validate_live_event_stream(v12, events, result)

    def test_replay_derives_live_raws_without_cross_profile_interpretation(self) -> None:
        v12 = manifest(V12, CURRENT_SHA)
        v12_stats = {"planned": 4, "started": 4, "succeeded": 4, "failed": 0, "not_started": 0}
        self.assertEqual(len(_live_replay_requests(v12, success_stream(v12), run_result(v12_stats), "live-v12")), 4)

        v11 = manifest(V11, LEGACY_SHA)
        v11_stats = {"planned": 5, "started": 5, "succeeded": 5, "failed": 0, "not_started": 0}
        derived = _live_replay_requests(v11, valid_stream(v11), v11_result("no_change", v11_stats), "live-v11")
        self.assertEqual(len(derived), 5)
        self.assertEqual(derived[-1][0]["request_kind"], "announcement")


if __name__ == "__main__":
    unittest.main()
