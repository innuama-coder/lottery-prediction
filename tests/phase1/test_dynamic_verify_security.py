from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.phase1.test_high_level_workflow_unit as live_fixture
from lottery_data.models import make_revision_id
from lottery_data.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_file
from lottery_data.steps.verify import VerifyContractError, verify_release


RUN_ID = "live-publish"
RELEASE_ID = "live-release"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: dict) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _write_jsonl(path: Path, rows: list[dict], keys: tuple[str, ...]) -> None:
    path.write_bytes(canonical_jsonl_bytes(rows, sort_keys=keys))


def _resign(root: Path) -> None:
    run = root / "runs" / RUN_ID
    formal = root / RELEASE_ID
    published = root / "releases" / RELEASE_ID
    quality = _json(run / "quality-report.json")
    deterministic = quality["deterministic"]
    deterministic["input_hashes"]["run_manifest"] = sha256_file(run / "run-manifest.json")
    deterministic["input_hashes"]["current_release_manifest"] = sha256_file(
        root / "releases" / "baseline-v1" / "manifest.json"
    )
    deterministic["output_hashes"] = {
        "draws": sha256_file(run / "candidate-draws.jsonl"),
        "reconciliation": sha256_file(run / "reconciliation.jsonl"),
        "release_observations": sha256_file(formal / "observations.jsonl"),
        "run_observations": sha256_file(run / "observations.jsonl"),
    }
    _write_json(run / "quality-report.json", quality)
    for release in (formal, published):
        shutil.copyfile(run / "quality-report.json", release / "quality-report.json")
        manifest = _json(release / "manifest.json")
        manifest["input_manifest_sha256"] = sha256_file(run / "run-manifest.json")
        manifest["records_sha256"] = sha256_file(formal / "draws.jsonl")
        manifest["observations_sha256"] = sha256_file(formal / "observations.jsonl")
        _write_json(release / "manifest.json", manifest)
    shutil.copyfile(formal / "draws.jsonl", published / "draws.jsonl")
    shutil.copyfile(formal / "observations.jsonl", published / "observations.jsonl")
    shutil.copyfile(formal / "manifest.json", published / "manifest.json")
    result = _json(run / "run-result.json")
    result["deterministic_artifact_hashes"] = {
        "candidate_draws": sha256_file(run / "candidate-draws.jsonl"),
        "events": sha256_file(run / "events.jsonl"),
        "observations": sha256_file(run / "observations.jsonl"),
        "quality_report": sha256_file(run / "quality-report.json"),
        "reconciliation": sha256_file(run / "reconciliation.jsonl"),
        "run_manifest": sha256_file(run / "run-manifest.json"),
    }
    _write_json(run / "run-result.json", result)
    for release in (formal, published):
        hashes = _json(release / "hashes.json")
        for entry in hashes["entries"]:
            path = release / entry["path"]
            entry["sha256"] = sha256_file(path)
            entry["size_bytes"] = path.stat().st_size
        _write_json(release / "hashes.json", hashes)
    _refresh_run_hashes(root)


def _refresh_run_hashes(root: Path) -> None:
    run = root / "runs" / RUN_ID
    hashes = _json(run / "hashes.json")
    entries = []
    for path in sorted(item for item in run.rglob("*") if item.is_file() and item.name != "hashes.json"):
        relative = path.relative_to(root).as_posix()
        entries.append({
            "path": relative, "sha256": sha256_file(path), "size_bytes": path.stat().st_size,
            "role": "raw" if "/raw/" in relative else "managed",
        })
    hashes["entries"] = entries
    _write_json(run / "hashes.json", hashes)


def _state(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


class DynamicVerifySecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._base_temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._base_temp.name)

        class KeepDirectory:
            def __enter__(self) -> str:
                return str(cls.base)

            def __exit__(self, *_: object) -> bool:
                return False

        case = live_fixture.HighLevelWorkflowUnitTests(
            "test_live_publish_is_accepted_by_dynamic_verify"
        )
        with patch.object(live_fixture.tempfile, "TemporaryDirectory", return_value=KeepDirectory()):
            case.test_live_publish_is_accepted_by_dynamic_verify()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._base_temp.cleanup()

    def copy_root(self, directory: str) -> Path:
        root = Path(directory) / "root"
        shutil.copytree(self.base, root)
        return root

    def test_valid_dynamic_verification_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_root(directory)
            run = root / "runs" / RUN_ID
            result = _json(run / "run-result.json")
            self.assertEqual(result["deterministic_artifact_hashes"], {
                "candidate_draws": sha256_file(run / "candidate-draws.jsonl"),
                "events": sha256_file(run / "events.jsonl"),
                "observations": sha256_file(run / "observations.jsonl"),
                "quality_report": sha256_file(run / "quality-report.json"),
                "reconciliation": sha256_file(run / "reconciliation.jsonl"),
                "run_manifest": sha256_file(run / "run-manifest.json"),
            })
            before = _state(root)
            report = verify_release(root, RELEASE_ID)
            self.assertEqual((report["status"], report["profile"]), ("PASS", "incremental-dynamic"))
            self.assertEqual(_state(root), before)

    def test_result_hash_collection_rejects_missing_extra_and_wrong_quality_hash(self) -> None:
        mutations = {
            "missing": lambda hashes: hashes.pop("quality_report"),
            "managed-path": lambda hashes: hashes.__setitem__(
                f"runs/{RUN_ID}/quality-report.json", hashes["quality_report"]
            ),
            "wrong-quality": lambda hashes: hashes.__setitem__("quality_report", "0" * 64),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = self.copy_root(directory)
                _resign(root)
                result_path = root / "runs" / RUN_ID / "run-result.json"
                result = _json(result_path)
                mutate(result["deterministic_artifact_hashes"])
                _write_json(result_path, result)
                _refresh_run_hashes(root)
                with self.assertRaisesRegex(VerifyContractError, "RunResult hashes"):
                    verify_release(root, RELEASE_ID)

    def test_resigned_run_evidence_mutations_are_rejected(self) -> None:
        mutations = {
            "observation": lambda root: self._mutate_observation(root),
            "candidate": lambda root: self._mutate_candidate(root),
            "reconciliation": lambda root: self._mutate_reconciliation(root),
            "quality": lambda root: self._mutate_quality(root),
            "result": lambda root: self._mutate_result(root),
            "config": lambda root: self._mutate_config(root),
            "raw": lambda root: self._mutate_raw(root),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = self.copy_root(directory)
                mutate(root)
                _resign(root)
                with self.assertRaises(VerifyContractError):
                    verify_release(root, RELEASE_ID)

    def test_false_supersedes_is_rejected_after_full_resign(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_root(directory)
            formal = root / RELEASE_ID
            rows = _jsonl(formal / "draws.jsonl")
            row = next(item for item in rows if item["issue_id"] in {"2026084", "2026086"})
            predecessor = _jsonl(root / "baseline-v1" / "draws.jsonl")[0]["revision_id"]
            row["supersedes_revision_id"] = predecessor
            row["revision_id"] = make_revision_id(
                row["game"], row["issue_id"], row["core_fact_sha256"], predecessor
            )
            _write_jsonl(formal / "draws.jsonl", rows, ("game", "issue_id", "revision_id"))
            shutil.copyfile(formal / "draws.jsonl", root / "runs" / RUN_ID / "candidate-draws.jsonl")
            _resign(root)
            with self.assertRaisesRegex(VerifyContractError, "false predecessor"):
                verify_release(root, RELEASE_ID)

    @staticmethod
    def _mutate_observation(root: Path) -> None:
        path = root / "runs" / RUN_ID / "observations.jsonl"
        rows = _jsonl(path)
        rows[0]["source_url"] += "?attacker=1"
        _write_jsonl(path, rows, ("game", "issue_id", "publisher_id", "source_id", "observation_id"))

    @staticmethod
    def _mutate_candidate(root: Path) -> None:
        path = root / "runs" / RUN_ID / "candidate-draws.jsonl"
        rows = _jsonl(path)
        rows.pop()
        _write_jsonl(path, rows, ("game", "issue_id", "revision_id"))

    @staticmethod
    def _mutate_reconciliation(root: Path) -> None:
        path = root / "runs" / RUN_ID / "reconciliation.jsonl"
        rows = _jsonl(path)
        rows[0]["decision"] = "conflict"
        _write_jsonl(path, rows, ("game", "issue_id"))

    @staticmethod
    def _mutate_quality(root: Path) -> None:
        path = root / "runs" / RUN_ID / "quality-report.json"
        value = _json(path)
        value["deterministic"]["counts"]["added"] += 1
        _write_json(path, value)

    @staticmethod
    def _mutate_result(root: Path) -> None:
        path = root / "runs" / RUN_ID / "run-result.json"
        value = _json(path)
        value["candidate_stats"]["eligible"] -= 1
        _write_json(path, value)

    @staticmethod
    def _mutate_config(root: Path) -> None:
        path = root / "runs" / RUN_ID / "config" / "live-source-policy.json"
        path.write_bytes(path.read_bytes() + b"\n")
        manifest_path = root / "runs" / RUN_ID / "run-manifest.json"
        manifest = _json(manifest_path)
        manifest["config_files"][0]["sha256"] = sha256_file(path)
        _write_json(manifest_path, manifest)
        quality_path = root / "runs" / RUN_ID / "quality-report.json"
        quality = _json(quality_path)
        quality["deterministic"]["input_hashes"]["live_source_policy"] = sha256_file(path)
        _write_json(quality_path, quality)

    @staticmethod
    def _mutate_raw(root: Path) -> None:
        path = next((root / "runs" / RUN_ID / "raw").rglob("*.raw"))
        path.write_bytes(path.read_bytes() + b"attacker")


if __name__ == "__main__":
    unittest.main()
