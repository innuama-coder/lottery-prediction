from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lottery_data.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_file
from lottery_data.steps.preflight import BootstrapArguments, IncrementalArguments
from lottery_data.workflow import execute_bootstrap, execute_incremental, execute_verify
from lottery_data.steps.verify import _dynamic_raw_ref_matches_profile


REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
CONFIG = REPO / "config" / "phase1"
FIXTURE = Path(__file__).parent / "fixtures" / "real" / "e2e03-seed.json"
SEED_RUN = "e2e03-seed-run"
FIXTURE_DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
SEED_RELEASE = FIXTURE_DATA["source_release_id"]
DELTA_RELEASE = "e2e03-delta"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def _write_jsonl(path: Path, rows: list[dict], keys: tuple[str, ...]) -> None:
    path.write_bytes(canonical_jsonl_bytes(rows, sort_keys=keys))


def _tree_state(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in root.rglob("*") if path.is_file()
    }


def _validate_fixture(fixture: dict) -> None:
    required = {
        "fixture_schema_version", "recipe", "source_release_id", "removed_game", "removed_issue_id",
        "removed_revision_id", "removed_core_fact_sha256", "removed_observation_ids",
        "expected_draws_sha256", "expected_observations_sha256", "expected_counts", "raw_mutation_allowed",
    }
    if set(fixture) != required or fixture["source_release_id"] != SEED_RELEASE:
        raise ValueError("E2E03 fixture fields/release identity are incomplete")
    if fixture["raw_mutation_allowed"] is not False or fixture["recipe"] != "current-bootstrap-minus-one-normalized-draw-v1":
        raise ValueError("E2E03 fixture recipe/raw policy mismatch")
    if set(fixture["expected_counts"]) != {"draws", "release_observations", "run_observations", "ssq", "dlt"}:
        raise ValueError("E2E03 expected_counts are incomplete")


def _refresh_release(release: Path) -> None:
    hashes = _json(release / "hashes.json")
    for item in hashes["entries"]:
        disk = release / item["path"]
        item["sha256"], item["size_bytes"] = sha256_file(disk), disk.stat().st_size
    _write_json(release / "hashes.json", hashes)


def _refresh_run(root: Path, run_id: str) -> None:
    run = root / "runs" / run_id
    roles = {
        "run-manifest.json": "manifest", "events.jsonl": "event", "observations.jsonl": "observation",
        "reconciliation.jsonl": "reconciliation", "candidate-draws.jsonl": "candidate",
        "quality-report.json": "quality", "run-result.json": "result",
    }
    entries = []
    for disk in sorted(path for path in run.rglob("*") if path.is_file() and path.name != "hashes.json"):
        relative = disk.relative_to(run).as_posix()
        role = "raw" if relative.startswith("raw/") else "config" if relative.startswith("config/") else roles[relative]
        entries.append({
            "path": disk.relative_to(root).as_posix(), "sha256": sha256_file(disk),
            "size_bytes": disk.stat().st_size, "role": role,
        })
    hashes = _json(run / "hashes.json")
    hashes["entries"] = entries
    _write_json(run / "hashes.json", hashes)


def _derive_seed(root: Path, fixture: dict, *, enforce_frozen_hashes: bool = True) -> None:
    _validate_fixture(fixture)
    run = root / "runs" / SEED_RUN
    releases = (root / "releases" / SEED_RELEASE, root / SEED_RELEASE)
    raw_before = {
        path.relative_to(run).as_posix(): sha256_file(path)
        for path in (run / "raw").rglob("*") if path.is_file()
    }
    source_draws = _jsonl(run / "candidate-draws.jsonl")
    removed_draw = next((row for row in source_draws if
        row["game"] == fixture["removed_game"] and row["issue_id"] == fixture["removed_issue_id"]), None)
    if removed_draw is None or removed_draw["revision_id"] != fixture["removed_revision_id"] or removed_draw["core_fact_sha256"] != fixture["removed_core_fact_sha256"]:
        raise ValueError("E2E03 removed draw identity/core does not match the frozen recipe")
    draws = [row for row in source_draws if not (
        row["game"] == fixture["removed_game"] and row["issue_id"] == fixture["removed_issue_id"]
    )]
    source_observations = _jsonl(releases[0] / "observations.jsonl")
    removed = [row for row in source_observations if row["observation_id"] in fixture["removed_observation_ids"]]
    if len(removed) != 2 or {(row["game"], row["issue_id"]) for row in removed} != {(fixture["removed_game"], fixture["removed_issue_id"])}:
        raise ValueError("E2E03 removed observations do not exactly close to the removed draw")
    observations = [row for row in source_observations if row["observation_id"] not in fixture["removed_observation_ids"]]
    _write_jsonl(run / "candidate-draws.jsonl", draws, ("game", "issue_id", "revision_id"))
    for release in releases:
        _write_jsonl(release / "draws.jsonl", draws, ("game", "issue_id", "revision_id"))
        _write_jsonl(release / "observations.jsonl", observations, ("game", "issue_id", "publisher_id", "source_id", "observation_id"))
        manifest = _json(release / "manifest.json")
        manifest["records_sha256"] = sha256_file(release / "draws.jsonl")
        manifest["observations_sha256"] = sha256_file(release / "observations.jsonl")
        manifest["record_count_by_game"] = {"ssq": fixture["expected_counts"]["ssq"], "dlt": fixture["expected_counts"]["dlt"]}
        manifest["observation_count"] = fixture["expected_counts"]["release_observations"]
        _write_json(release / "manifest.json", manifest)
    self_hashes = (sha256_file(releases[0] / "draws.jsonl"), sha256_file(releases[0] / "observations.jsonl"))
    if enforce_frozen_hashes and self_hashes != (fixture["expected_draws_sha256"], fixture["expected_observations_sha256"]):
        raise ValueError("E2E03 frozen fixture hashes do not match the derived seed")
    quality = _json(run / "quality-report.json")
    quality["deterministic"]["counts"].update({
        "draws": fixture["expected_counts"]["draws"],
        "selected_observations": fixture["expected_counts"]["release_observations"],
        "ssq": fixture["expected_counts"]["ssq"], "dlt": fixture["expected_counts"]["dlt"],
    })
    quality["deterministic"]["output_hashes"].update({
        "draws": sha256_file(run / "candidate-draws.jsonl"),
        "release_observations": sha256_file(releases[0] / "observations.jsonl"),
    })
    for path in (run / "quality-report.json", releases[0] / "quality-report.json", releases[1] / "quality-report.json"):
        _write_json(path, quality)
    result = _json(run / "run-result.json")
    result["deterministic_artifact_hashes"].update({
        "candidate_draws": sha256_file(run / "candidate-draws.jsonl"),
        "quality_report": sha256_file(run / "quality-report.json"),
    })
    _write_json(run / "run-result.json", result)
    for release in releases:
        _refresh_release(release)
    pointer = _json(root / "current-release.json")
    pointer["manifest_sha256"] = sha256_file(releases[0] / "manifest.json")
    _write_json(root / "current-release.json", pointer)
    _refresh_run(root, SEED_RUN)
    if len(_jsonl(run / "observations.jsonl")) != fixture["expected_counts"]["run_observations"]:
        raise ValueError("E2E03 run observations count changed")
    assert raw_before == {
        path.relative_to(run).as_posix(): sha256_file(path)
        for path in (run / "raw").rglob("*") if path.is_file()
    }


class SnapshotDeltaE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._base_temp = tempfile.TemporaryDirectory(prefix="snapshot-e2e03-base-")
        cls.base = Path(cls._base_temp.name) / "artifacts"
        code, result = execute_bootstrap(BootstrapArguments(
            mode="bootstrap", source_mode="snapshot", phase0_snapshot=SNAPSHOT,
            artifacts_root=cls.base, config_root=CONFIG, run_id=SEED_RUN, release_id=SEED_RELEASE,
        ))
        if (code, result["status"]) != (0, "published"):
            raise AssertionError(result)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._base_temp.cleanup()

    def _copy_root(self, temporary: str) -> Path:
        root = Path(temporary) / "artifacts"
        shutil.copytree(self.base, root)
        return root

    def test_trusted_seed_adds_exactly_one_then_second_run_is_no_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snapshot-e2e03-") as temporary:
            root = self._copy_root(temporary)
            run = root / "runs" / SEED_RUN
            run_observations_before = (run / "observations.jsonl").read_bytes()
            raw_before = _tree_state(run / "raw")
            _derive_seed(root, FIXTURE_DATA)
            code, report = execute_verify(artifacts_root=root, release_id=SEED_RELEASE)
            self.assertEqual((code, report.get("status")), (0, "PASS"), report)
            self.assertEqual((run / "observations.jsonl").read_bytes(), run_observations_before)
            self.assertEqual(_tree_state(run / "raw"), raw_before)
            self.assertEqual(_tree_state(root / SEED_RELEASE), _tree_state(root / "releases" / SEED_RELEASE))
            seed_tree = _tree_state(root / "releases" / SEED_RELEASE)

            code, result = execute_incremental(IncrementalArguments(
                mode="incremental", source_mode="snapshot", snapshot_root=SNAPSHOT,
                artifacts_root=root, config_root=CONFIG, run_id="e2e03-add", release_id=DELTA_RELEASE,
            ))
            if code != 0:
                self.fail({
                    "result": result,
                    "errors": [_json(path) for path in (root / "runs" / "e2e03-add" / "errors").glob("*.json")],
                })
            self.assertEqual((code, result["status"], result["change_stats"]["added"]), (0, "published", 1), result)
            self.assertEqual(result["change_stats"]["revised"], 0)
            add_run = root / "runs" / "e2e03-add"
            add_quality_counts = _json(add_run / "quality-report.json")["deterministic"]["counts"]
            self.assertEqual(result["candidate_stats"]["observed"], len(_jsonl(add_run / "reconciliation.jsonl")))
            self.assertEqual(result["candidate_stats"]["eligible"], add_quality_counts["draws"])
            self.assertEqual(result["candidate_stats"]["unresolved"], 0)
            self.assertEqual(_tree_state(root / "releases" / SEED_RELEASE), seed_tree)
            self.assertEqual(_tree_state(root / SEED_RELEASE), seed_tree)
            self.assertEqual((run / "observations.jsonl").read_bytes(), run_observations_before)
            self.assertEqual(_tree_state(run / "raw"), raw_before)
            code, report = execute_verify(artifacts_root=root, release_id=DELTA_RELEASE)
            self.assertEqual((code, report.get("status")), (0, "PASS"), report)

            pointer_before = (root / "current-release.json").read_bytes()
            code, result = execute_incremental(IncrementalArguments(
                mode="incremental", source_mode="snapshot", snapshot_root=SNAPSHOT,
                artifacts_root=root, config_root=CONFIG, run_id="e2e03-no-change",
            ))
            self.assertEqual((code, result["status"]), (0, "no_change"), result)
            no_change_run = root / "runs" / "e2e03-no-change"
            no_change_counts = _json(no_change_run / "quality-report.json")["deterministic"]["counts"]
            self.assertEqual(result["candidate_stats"]["observed"], len(_jsonl(no_change_run / "reconciliation.jsonl")))
            self.assertEqual(result["candidate_stats"]["eligible"], no_change_counts["draws"])
            self.assertEqual(result["candidate_stats"]["unresolved"], 0)
            self.assertEqual((root / "current-release.json").read_bytes(), pointer_before)
            self.assertEqual(_tree_state(root / "releases" / SEED_RELEASE), seed_tree)
            self.assertEqual(_tree_state(root / SEED_RELEASE), seed_tree)
            self.assertEqual((run / "observations.jsonl").read_bytes(), run_observations_before)
            self.assertEqual(_tree_state(run / "raw"), raw_before)

    def test_only_frozen_399_798_seed_recipe_is_accepted(self) -> None:
        attacks = (("ssq", 10), ("dlt", -10))
        for game, position in attacks:
            with self.subTest(game=game, position=position), tempfile.TemporaryDirectory() as temporary:
                root = self._copy_root(temporary)
                draws = [row for row in _jsonl(root / SEED_RELEASE / "draws.jsonl") if row["game"] == game]
                target = draws[position]
                fixture = dict(FIXTURE_DATA)
                fixture.update({
                    "removed_game": game, "removed_issue_id": target["issue_id"],
                    "removed_revision_id": target["revision_id"],
                    "removed_core_fact_sha256": target["core_fact_sha256"],
                    "removed_observation_ids": [item["observation_id"] for item in target["evidence_links"]],
                    "expected_draws_sha256": "0" * 64, "expected_observations_sha256": "0" * 64,
                    "expected_counts": {
                        "draws": 399, "release_observations": 798, "run_observations": 1042,
                        "ssq": 199 if game == "ssq" else 200, "dlt": 199 if game == "dlt" else 200,
                    },
                })
                _derive_seed(root, fixture, enforce_frozen_hashes=False)
                code, report = execute_verify(artifacts_root=root, release_id=SEED_RELEASE)
                self.assertNotEqual(code, 0, report)

    def test_missing_or_wrong_fixture_hash_fails_before_seed_is_signed(self) -> None:
        for mutation in ("missing", "wrong"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = self._copy_root(temporary)
                fixture = dict(FIXTURE_DATA)
                if mutation == "missing":
                    fixture.pop("expected_draws_sha256")
                else:
                    fixture["expected_draws_sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    _derive_seed(root, fixture)

    def test_snapshot_and_live_raw_profiles_reject_each_other(self) -> None:
        digest = "a" * 64
        page = "raw/ydniu/ssq/page-001.html"
        addressed = f"raw/ydniu/ssq/sha256/{digest}.raw"
        self.assertTrue(_dynamic_raw_ref_matches_profile(page, source_id="ydniu", game="ssq", raw_sha256=None, snapshot=True))
        self.assertFalse(_dynamic_raw_ref_matches_profile(addressed, source_id="ydniu", game="ssq", raw_sha256=digest, snapshot=True))
        self.assertTrue(_dynamic_raw_ref_matches_profile(addressed, source_id="ydniu", game="ssq", raw_sha256=digest, snapshot=False))
        self.assertFalse(_dynamic_raw_ref_matches_profile(page, source_id="ydniu", game="ssq", raw_sha256=digest, snapshot=False))


if __name__ == "__main__":
    unittest.main()
