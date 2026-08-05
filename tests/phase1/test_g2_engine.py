from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lottery_data.serialization import canonical_json_bytes, canonical_jsonl_bytes, make_event_id, sha256_file
from lottery_data.steps import transform_bootstrap_snapshot
from lottery_data.steps.incremental import DeltaOutsideG2Scope, compare_no_change
from lottery_data.steps.transform import TransformResult, transform_observations
from lottery_data.steps.verify import RawHashMismatchError, VerifyContractError, verify_release


REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
CATALOG_PATH = REPO / "config" / "phase1" / "source-catalog.json"
POLICY_PATH = REPO / "config" / "phase1" / "collection-policy.json"
INTEGRATION = REPO / "artifacts" / "phase-1-integration-root-20260802-2126"
RELEASE_ID = "p1-root-integration-003"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def file_state(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in root.rglob("*") if path.is_file()
    }


def complete_run_hashes(root: Path) -> None:
    run = root / "runs" / RELEASE_ID
    entries = []
    for path in sorted((item for item in run.rglob("*") if item.is_file() and item.name != "hashes.json")):
        relative = path.relative_to(root).as_posix()
        entries.append({
            "path": relative,
            "role": "raw" if "/raw/" in relative else "managed",
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        })
    value = load_json(run / "hashes.json")
    value["entries"] = entries
    (run / "hashes.json").write_bytes(canonical_json_bytes(value))


def refresh_release_hashes(root: Path) -> None:
    for release in (root / RELEASE_ID, root / "releases" / RELEASE_ID):
        value = load_json(release / "hashes.json")
        for entry in value["entries"]:
            path = release / entry["path"]
            entry["sha256"] = sha256_file(path)
            entry["size_bytes"] = path.stat().st_size
        (release / "hashes.json").write_bytes(canonical_json_bytes(value))


def build_verifiable_root(temporary: str) -> Path:
    root = Path(temporary) / "phase-1"
    shutil.copytree(INTEGRATION / "runs", root / "runs")
    shutil.copytree(INTEGRATION / "releases", root / "releases")
    shutil.copytree(root / "releases" / RELEASE_ID, root / RELEASE_ID)
    complete_run_hashes(root)
    return root


class G2TransformAndIncrementalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        wrapper = transform_bootstrap_snapshot(snapshot_root=SNAPSHOT, source_catalog_path=CATALOG_PATH)
        cls.wrapper = wrapper
        cls.transformed = transform_observations(
            wrapper["observations_all"],
            SNAPSHOT,
            load_json(CATALOG_PATH),
            load_json(POLICY_PATH),
            "g2-engine-test",
            {
                "canonical": wrapper["audit"]["canonical_sha256"],
                "capture_manifest": wrapper["audit"]["capture_manifest_sha256"],
                "request_events": wrapper["audit"]["request_events_sha256"],
            },
            "2026-08-02T03:00:00Z",
        )

    def test_transform_result_is_controlled_and_frozen_1042_800_400(self) -> None:
        result = self.transformed
        self.assertIsInstance(result, TransformResult)
        self.assertEqual((len(result.observations_all), len(result.observations_selected), len(result.draws)), (1042, 800, 400))
        self.assertEqual(
            set(result.output_hashes),
            {"draws", "run_observations", "release_observations", "reconciliation"},
        )
        self.assertEqual(result.audit["parsed_observations"], 1042)

    def test_compare_no_change_requires_hash_count_and_keys_to_match(self) -> None:
        current = {
            "draws": list(self.transformed.draws),
            "observations": list(self.transformed.observations_selected),
        }
        report = compare_no_change(self.transformed, current)
        self.assertEqual(report["status"], "no_change")
        changed = {"draws": [dict(row) for row in current["draws"]], "observations": current["observations"]}
        changed["draws"][0]["revision_id"] = "rev-v1:" + "0" * 64
        with self.assertRaises(DeltaOutsideG2Scope):
            compare_no_change(self.transformed, changed)


class G2VerifyTests(unittest.TestCase):
    def assert_read_only_failure(self, root: Path, expected: type[Exception]) -> None:
        before = file_state(root)
        with self.assertRaises(expected):
            verify_release(root, RELEASE_ID, snapshot_root_override=SNAPSHOT)
        self.assertEqual(file_state(root), before)

    def test_real_integration_copy_closes_all_read_only_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = build_verifiable_root(temporary)
            before = file_state(root)
            report = verify_release(root, RELEASE_ID, snapshot_root_override=SNAPSHOT)
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(all(report["checks"].values()))
            self.assertEqual(report["actual"]["run_observations"], 1042)
            self.assertEqual(file_state(root), before)

    def test_release_tamper_matrix_fails_closed_without_verifier_writes(self) -> None:
        cases = ("formal_divergence", "release_hash")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = build_verifiable_root(temporary)
                if case == "formal_divergence":
                    (root / RELEASE_ID / "quality-report.json").write_bytes(b"{}\n")
                    expected = VerifyContractError
                else:
                    path = root / RELEASE_ID / "draws.jsonl"
                    payload = path.read_bytes().replace(b'"status":"verified"', b'"status":"rejected"', 1)
                    path.write_bytes(payload)
                    (root / "releases" / RELEASE_ID / "draws.jsonl").write_bytes(payload)
                    expected = VerifyContractError
                self.assert_read_only_failure(root, expected)

    def test_extra_run_file_is_rejected_even_when_hash_inventory_is_resigned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = build_verifiable_root(temporary)
            (root / "runs" / RELEASE_ID / "extra.json").write_bytes(b"{}\n")
            complete_run_hashes(root)
            self.assert_read_only_failure(root, VerifyContractError)

    def test_event_bad_id_is_rejected_even_when_run_hashes_are_resigned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = build_verifiable_root(temporary)
            path = root / "runs" / RELEASE_ID / "events.jsonl"
            events = load_jsonl(path)
            events[10]["event_id"] = "evt-v1:" + "0" * 64
            path.write_bytes(canonical_jsonl_bytes(events, sort_keys=("run_id", "sequence", "event_id")))
            complete_run_hashes(root)
            self.assert_read_only_failure(root, VerifyContractError)

    def test_unselected_run_observation_bad_core_is_rejected_when_resigned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = build_verifiable_root(temporary)
            release_ids = {
                row["observation_id"]
                for row in load_jsonl(root / RELEASE_ID / "observations.jsonl")
            }
            path = root / "runs" / RELEASE_ID / "observations.jsonl"
            observations = load_jsonl(path)
            target = next(row for row in observations if row["observation_id"] not in release_ids)
            target["core_fact_sha256"] = "0" * 64
            path.write_bytes(canonical_jsonl_bytes(
                observations,
                sort_keys=("game", "issue_id", "publisher_id", "source_id", "observation_id"),
            ))
            complete_run_hashes(root)
            self.assert_read_only_failure(root, VerifyContractError)

    def test_raw_tamper_is_classified_as_raw_hash_mismatch_before_generic_hash_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = build_verifiable_root(temporary)
            raw = root / "runs" / RELEASE_ID / "raw" / "ydniu" / "ssq" / "page-001.html"
            raw.write_bytes(raw.read_bytes() + b"tamper")
            self.assert_read_only_failure(root, RawHashMismatchError)

    def test_foreign_event_run_identity_is_rejected_after_ids_and_hashes_are_resigned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = build_verifiable_root(temporary)
            path = root / "runs" / RELEASE_ID / "events.jsonl"
            events = load_jsonl(path)
            for event in events:
                event["run_id"] = "foreign-run"
                event["event_id"] = make_event_id(
                    event["run_id"], event["sequence"], event["event_type"],
                    event["request_id"], event["attempt"],
                )
            path.write_bytes(canonical_jsonl_bytes(events, sort_keys=("run_id", "sequence", "event_id")))
            complete_run_hashes(root)
            self.assert_read_only_failure(root, VerifyContractError)

    def test_run_result_events_digest_is_bound_after_run_hashes_are_resigned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = build_verifiable_root(temporary)
            path = root / "runs" / RELEASE_ID / "run-result.json"
            result = load_json(path)
            result["deterministic_artifact_hashes"]["events"] = "0" * 64
            path.write_bytes(canonical_json_bytes(result))
            complete_run_hashes(root)
            self.assert_read_only_failure(root, VerifyContractError)

    def test_foreign_quality_run_id_is_rejected_after_all_file_hashes_are_resigned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = build_verifiable_root(temporary)
            quality = load_json(root / RELEASE_ID / "quality-report.json")
            quality["run_id"] = "foreign-run"
            payload = canonical_json_bytes(quality)
            for path in (
                root / RELEASE_ID / "quality-report.json",
                root / "releases" / RELEASE_ID / "quality-report.json",
                root / "runs" / RELEASE_ID / "quality-report.json",
            ):
                path.write_bytes(payload)
            refresh_release_hashes(root)
            complete_run_hashes(root)
            self.assert_read_only_failure(root, VerifyContractError)


if __name__ == "__main__":
    unittest.main()
