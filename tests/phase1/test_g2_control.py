from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from lottery_data.steps.preflight import IncrementalArguments  # noqa: E402
from lottery_data.steps.incremental import DeltaOutsideG2Scope  # noqa: E402
from lottery_data.steps.snapshot import load_source_catalog  # noqa: E402
from lottery_data.serialization import sha256_file  # noqa: E402
from lottery_data.workflow import execute_incremental, execute_verify  # noqa: E402
from tests.phase1.test_bootstrap_e2e import NOW, _dependencies  # noqa: E402


def _g2_plan(
    _: Path,
    __: Sequence[str],
    ___: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "request_id": f"g2-{source_id}-ssq-2026085",
            "sequence": sequence,
            "source_id": source_id,
            "publisher_id": publisher_id,
            "game": "ssq",
            "method": "SNAPSHOT",
            "url": f"snapshot://{source_id}/ssq/2026085",
            "input_ref": f"raw/g2-recheck/{source_id}/ssq-2026085.html",
        }
        for sequence, (source_id, publisher_id) in enumerate(
            (("ydniu", "ydniu-publisher"), ("eastmoney", "eastmoney-publisher")),
            start=1,
        )
    ]


def _g2_materialize(
    request: dict[str, Any],
    _: Path,
    raw_root: Path,
) -> dict[str, Any]:
    relative = Path(request["input_ref"]).relative_to("raw")
    raw_path = raw_root / relative
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(request["source_id"].encode("ascii"))
    return {
        "raw_path": raw_path,
        "raw_ref": request["input_ref"],
        "raw_sha256": sha256_file(raw_path),
        "source_id": request["source_id"],
        "publisher_id": request["publisher_id"],
        "game": request["game"],
        "url": request["url"],
        "captured_at_utc": NOW,
        "request_id": request["request_id"],
    }


def _g2_dependencies():
    return replace(
        _dependencies(),
        build_request_plan=_g2_plan,
        load_source_catalog=load_source_catalog,
        materialize_request=_g2_materialize,
        expected_reparsed_counts={"ydniu:ssq": 1, "eastmoney:ssq": 1},
    )


class G2ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.snapshot = self.root / "20260802T025000Z"
        self.snapshot.mkdir()
        (self.snapshot / "artifact-hashes.json").write_text("{}\n", encoding="utf-8")
        self.config = self.root / "config"
        self.config.mkdir()
        shutil.copyfile(REPO / "config" / "phase1" / "source-catalog.json", self.config / "source-catalog.json")
        shutil.copyfile(REPO / "config" / "phase1" / "collection-policy.json", self.config / "collection-policy.json")
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        formal = REPO / "artifacts" / "phase-1"
        self.pointer_bytes = (formal / "current-release.json").read_bytes()
        pointer = json.loads(self.pointer_bytes)
        self.release_id = pointer["release_id"]
        self.predecessor_run_id = pointer["updated_by_run_id"]
        shutil.copyfile(formal / "current-release.json", self.artifacts / "current-release.json")
        shutil.copytree(
            formal / "releases" / self.release_id,
            self.artifacts / "releases" / self.release_id,
        )
        shutil.copytree(formal / self.release_id, self.artifacts / self.release_id)
        shutil.copytree(
            formal / "runs" / self.predecessor_run_id,
            self.artifacts / "runs" / self.predecessor_run_id,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_snapshot_incremental_no_change_preserves_release_and_pointer(self) -> None:
        arguments = IncrementalArguments(
            "incremental", "snapshot", self.snapshot, self.artifacts, self.config, "incremental-001",
        )
        code, result = execute_incremental(arguments, dependencies=_g2_dependencies())

        self.assertEqual((code, result["mode"], result["status"], result["release_id"]), (0, "incremental", "no_change", None))
        self.assertEqual((result["change_stats"]["added"], result["change_stats"]["revised"], result["change_stats"]["unchanged"]), (0, 0, 1))
        self.assertEqual((self.artifacts / "current-release.json").read_bytes(), self.pointer_bytes)
        self.assertEqual(sorted(path.name for path in (self.artifacts / "releases").iterdir()), [self.release_id])
        self.assertEqual(sorted(path.name for path in self.artifacts.iterdir() if path.is_dir() and path.name not in {"runs", "releases"}), [self.release_id])
        run = self.artifacts / "runs" / "incremental-001"
        events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["event_type"], "run_no_change")
        hashes = json.loads((run / "hashes.json").read_text(encoding="utf-8"))
        actual = {entry["path"] for entry in hashes["entries"]}
        expected = {
            path.relative_to(self.artifacts).as_posix()
            for path in run.rglob("*") if path.is_file() and path.name != "hashes.json" and "recovery" not in path.parts
        }
        self.assertEqual(actual, expected)

    def test_verify_is_read_only_and_returns_structured_result(self) -> None:
        before = sorted((path.relative_to(self.artifacts).as_posix(), path.read_bytes()) for path in self.artifacts.rglob("*") if path.is_file())
        code, report = execute_verify(
            artifacts_root=self.artifacts, release_id=self.release_id, snapshot_root_override=self.snapshot,
            dependencies=_g2_dependencies(),
        )
        after = sorted((path.relative_to(self.artifacts).as_posix(), path.read_bytes()) for path in self.artifacts.rglob("*") if path.is_file())
        self.assertEqual((code, report["status"]), (0, "PASS"))
        self.assertEqual(before, after)

    def test_incremental_engine_delta_fails_closed_without_release_or_pointer_change(self) -> None:
        arguments = IncrementalArguments(
            "incremental", "snapshot", self.snapshot, self.artifacts, self.config, "incremental-delta",
        )

        with patch(
            "lottery_data.steps.incremental_engine.build_incremental_release",
            side_effect=DeltaOutsideG2Scope("injected candidate delta"),
        ):
            code, result = execute_incremental(arguments, dependencies=_g2_dependencies())
        self.assertEqual((code, result["status"], result["exit_code"]), (4, "rejected", 4))
        self.assertEqual((self.artifacts / "current-release.json").read_bytes(), self.pointer_bytes)
        self.assertEqual(sorted(path.name for path in (self.artifacts / "releases").iterdir()), [self.release_id])
        self.assertEqual(sorted(path.name for path in self.artifacts.iterdir() if path.is_dir() and path.name not in {"runs", "releases"}), [self.release_id])


if __name__ == "__main__":
    unittest.main()
