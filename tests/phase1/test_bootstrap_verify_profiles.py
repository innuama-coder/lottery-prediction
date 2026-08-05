from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lottery_data.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_file
from lottery_data.steps.preflight import BootstrapArguments, IncrementalArguments
from lottery_data.workflow import execute_bootstrap, execute_incremental, execute_verify


REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
FORMAL = REPO / "artifacts" / "phase-1"
RUN_ID = "current-bootstrap"
RELEASE_ID = "baseline-v1"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _write_jsonl(path: Path, rows: list[dict], keys: tuple[str, ...]) -> None:
    path.write_bytes(canonical_jsonl_bytes(rows, sort_keys=keys))


def _files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in root.rglob("*") if path.is_file()
    }


def _role(relative: str) -> str:
    if relative.startswith("raw/"):
        return "raw"
    if relative.startswith("config/"):
        return "config"
    if relative.startswith("errors/"):
        return "error"
    return {
        "run-manifest.json": "manifest", "events.jsonl": "event",
        "observations.jsonl": "observation", "reconciliation.jsonl": "reconciliation",
        "candidate-draws.jsonl": "candidate", "quality-report.json": "quality",
        "run-result.json": "result",
    }.get(relative, "managed")


def _rebuild_run_hashes(root: Path, run_id: str) -> None:
    run = root / "runs" / run_id
    hashes = _json(run / "hashes.json")
    hashes["entries"] = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "role": _role(path.relative_to(run).as_posix()),
        }
        for path in sorted(item for item in run.rglob("*") if item.is_file() and item.name != "hashes.json")
    ]
    _write_json(run / "hashes.json", hashes)


def _refresh_release_hashes(root: Path, release: Path) -> None:
    hashes = _json(release / "hashes.json")
    for entry in hashes["entries"]:
        path = release / entry["path"]
        entry["sha256"] = sha256_file(path)
        entry["size_bytes"] = path.stat().st_size
    _write_json(release / "hashes.json", hashes)


def _resign_current(root: Path) -> dict:
    run = root / "runs" / RUN_ID
    manifest_sha = sha256_file(run / "run-manifest.json")
    quality = _json(run / "quality-report.json")
    quality["deterministic"]["input_hashes"]["run_manifest"] = manifest_sha
    for path in (
        run / "quality-report.json",
        root / "releases" / RELEASE_ID / "quality-report.json",
        root / RELEASE_ID / "quality-report.json",
    ):
        _write_json(path, quality)
    for path in (
        root / "releases" / RELEASE_ID / "manifest.json",
        root / RELEASE_ID / "manifest.json",
    ):
        manifest = _json(path)
        manifest["input_manifest_sha256"] = manifest_sha
        _write_json(path, manifest)
    result_path = run / "run-result.json"
    result = _json(result_path)
    result["deterministic_artifact_hashes"] = {
        "candidate_draws": sha256_file(run / "candidate-draws.jsonl"),
        "events": sha256_file(run / "events.jsonl"),
        "observations": sha256_file(run / "observations.jsonl"),
        "quality_report": sha256_file(run / "quality-report.json"),
        "reconciliation": sha256_file(run / "reconciliation.jsonl"),
        "run_manifest": manifest_sha,
    }
    _write_json(result_path, result)
    for release in (root / "releases" / RELEASE_ID, root / RELEASE_ID):
        _refresh_release_hashes(root, release)
    pointer_path = root / "current-release.json"
    pointer = _json(pointer_path)
    pointer["manifest_sha256"] = sha256_file(root / "releases" / RELEASE_ID / "manifest.json")
    _write_json(pointer_path, pointer)
    _rebuild_run_hashes(root, RUN_ID)
    return result


class BootstrapVerifyProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._base_temp = tempfile.TemporaryDirectory(prefix="bootstrap-current-profile-")
        cls.base = Path(cls._base_temp.name) / "artifacts"
        code, result = execute_bootstrap(BootstrapArguments(
            mode="bootstrap", source_mode="snapshot", phase0_snapshot=SNAPSHOT,
            artifacts_root=cls.base, config_root=REPO / "config" / "phase1",
            run_id=RUN_ID, release_id=RELEASE_ID,
        ))
        if (code, result.get("status")) != (0, "published"):
            raise AssertionError(result)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._base_temp.cleanup()

    def copy_current(self, directory: str) -> Path:
        root = Path(directory) / "artifacts"
        shutil.copytree(self.base, root)
        return root

    def test_current_cli_bootstrap_verifies_and_snapshot_incremental_is_no_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_current(directory)
            verify_code, report = execute_verify(
                artifacts_root=root, release_id=RELEASE_ID, snapshot_root_override=SNAPSHOT,
            )
            self.assertEqual((verify_code, report.get("status")), (0, "PASS"), report)
            pointer_before = (root / "current-release.json").read_bytes()
            release_before = _files(root / "releases" / RELEASE_ID)
            projection_before = _files(root / RELEASE_ID)
            code, result = execute_incremental(IncrementalArguments(
                mode="incremental", source_mode="snapshot", snapshot_root=SNAPSHOT,
                artifacts_root=root, config_root=REPO / "config" / "phase1",
                run_id="snapshot-no-change",
            ))
            self.assertEqual((code, result.get("status"), result.get("exit_code")), (0, "no_change", 0), result)
            self.assertEqual(result["change_stats"]["unchanged"], 400)
            self.assertEqual(result["request_stats"], {
                "planned": 30, "started": 30, "succeeded": 30, "failed": 0, "not_started": 0,
            })
            self.assertEqual((root / "current-release.json").read_bytes(), pointer_before)
            self.assertEqual(_files(root / "releases" / RELEASE_ID), release_before)
            self.assertEqual(_files(root / RELEASE_ID), projection_before)

    def test_formal_legacy_baseline_verifies_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "legacy"
            shutil.copytree(FORMAL, root)
            before = _files(root)
            code, report = execute_verify(
                artifacts_root=root, release_id=RELEASE_ID, snapshot_root_override=SNAPSHOT,
            )
            self.assertEqual((code, report.get("status")), (0, "PASS"), report)
            self.assertEqual(_files(root), before)

    def test_current_config_profile_attacks_fail_after_full_resign(self) -> None:
        for attack in ("missing", "extra", "mixed", "traversal", "bytes"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                root = self.copy_current(directory)
                run = root / "runs" / RUN_ID
                manifest_path = run / "run-manifest.json"
                manifest = _json(manifest_path)
                configs = {item["ref"]: item for item in manifest["config_files"]}
                if attack == "missing":
                    victim = "config/source-catalog.json"
                    (run / victim).unlink()
                    manifest["config_files"] = [item for item in manifest["config_files"] if item["ref"] != victim]
                elif attack == "extra":
                    path = run / "config" / "extra.json"
                    _write_json(path, {"attacker": True})
                    manifest["config_files"].append({"ref": "config/extra.json", "sha256": sha256_file(path)})
                elif attack == "mixed":
                    old = run / "config" / "source-catalog.json"
                    path = run / "config" / "phase1" / "source-catalog.json"
                    path.parent.mkdir(parents=True)
                    shutil.copyfile(old, path)
                    old.unlink()
                    configs["config/source-catalog.json"]["ref"] = "config/phase1/source-catalog.json"
                elif attack == "traversal":
                    old = run / "config" / "source-catalog.json"
                    path = root / "runs" / "outside-config.json"
                    shutil.copyfile(old, path)
                    old.unlink()
                    configs["config/source-catalog.json"]["ref"] = "../outside-config.json"
                else:
                    path = run / "config" / "collection-policy.json"
                    path.write_bytes(path.read_bytes() + b"\n")
                    configs["config/collection-policy.json"]["sha256"] = sha256_file(path)
                _write_json(manifest_path, manifest)
                _resign_current(root)
                code, report = execute_verify(
                    artifacts_root=root, release_id=RELEASE_ID, snapshot_root_override=SNAPSHOT,
                )
                self.assertNotEqual(code, 0, (attack, report))

    def test_current_result_event_and_quality_attacks_fail_after_resign(self) -> None:
        for attack in ("result-missing", "result-extra", "result-quality", "events", "quality"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                root = self.copy_current(directory)
                run = root / "runs" / RUN_ID
                if attack == "events":
                    events = _jsonl(run / "events.jsonl")
                    succeeded = next(row for row in events if row["event_type"] == "request_succeeded")
                    succeeded["artifact_ref"] = "raw/attacker/rebound.html"
                    _write_jsonl(run / "events.jsonl", events, ("sequence",))
                    _resign_current(root)
                elif attack == "quality":
                    quality = _json(run / "quality-report.json")
                    quality["deterministic"]["counts"]["parsed_observations"] -= 1
                    for path in (
                        run / "quality-report.json",
                        root / "releases" / RELEASE_ID / "quality-report.json",
                        root / RELEASE_ID / "quality-report.json",
                    ):
                        _write_json(path, quality)
                    _resign_current(root)
                else:
                    _resign_current(root)
                    result_path = run / "run-result.json"
                    result = _json(result_path)
                    hashes = result["deterministic_artifact_hashes"]
                    if attack == "result-missing":
                        hashes.pop("quality_report")
                    elif attack == "result-extra":
                        hashes["runs/current-bootstrap/quality-report.json"] = hashes["quality_report"]
                    else:
                        hashes["quality_report"] = "0" * 64
                    _write_json(result_path, result)
                    _rebuild_run_hashes(root, RUN_ID)
                code, report = execute_verify(
                    artifacts_root=root, release_id=RELEASE_ID, snapshot_root_override=SNAPSHOT,
                )
                self.assertNotEqual(code, 0, (attack, report))

    def test_legacy_profile_rejects_sixth_hash_and_run_local_config(self) -> None:
        for attack in ("sixth-hash", "run-local-config"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "legacy"
                shutil.copytree(FORMAL, root)
                manifest = _json(root / "releases" / RELEASE_ID / "manifest.json")
                run_id = manifest["input_run_id"]
                run = root / "runs" / run_id
                if attack == "sixth-hash":
                    result_path = run / "run-result.json"
                    result = _json(result_path)
                    result["deterministic_artifact_hashes"]["quality_report"] = sha256_file(run / "quality-report.json")
                    _write_json(result_path, result)
                else:
                    path = run / "config" / "source-catalog.json"
                    path.parent.mkdir()
                    shutil.copyfile(REPO / "config" / "phase1" / "source-catalog.json", path)
                _rebuild_run_hashes(root, run_id)
                code, report = execute_verify(
                    artifacts_root=root, release_id=RELEASE_ID, snapshot_root_override=SNAPSHOT,
                )
                self.assertNotEqual(code, 0, (attack, report))

    def test_invalid_current_profile_blocks_incremental_before_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_current(directory)
            run = root / "runs" / RUN_ID
            config = run / "config" / "collection-policy.json"
            config.write_bytes(config.read_bytes() + b"\n")
            manifest = _json(run / "run-manifest.json")
            next(item for item in manifest["config_files"] if item["ref"] == "config/collection-policy.json")["sha256"] = sha256_file(config)
            _write_json(run / "run-manifest.json", manifest)
            _resign_current(root)
            pointer_before = (root / "current-release.json").read_bytes()
            release_before = _files(root / "releases" / RELEASE_ID)
            projection_before = _files(root / RELEASE_ID)
            code, result = execute_incremental(IncrementalArguments(
                mode="incremental", source_mode="snapshot", snapshot_root=SNAPSHOT,
                artifacts_root=root, config_root=REPO / "config" / "phase1",
                run_id="blocked-incremental",
            ))
            self.assertEqual((code, result.get("status"), result["request_stats"]["started"]), (4, "rejected", 0), result)
            self.assertEqual((root / "current-release.json").read_bytes(), pointer_before)
            self.assertEqual(_files(root / "releases" / RELEASE_ID), release_before)
            self.assertEqual(_files(root / RELEASE_ID), projection_before)


if __name__ == "__main__":
    unittest.main()
