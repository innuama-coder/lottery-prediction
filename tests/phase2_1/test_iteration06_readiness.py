from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase2_1 import RELEASE_ID
from lottery_research.phase2_1.serialization import canonical_json_bytes, identity, load_json
from lottery_research.phase2_1.workflow import SOURCE_PATHS, _input_identity, project_root, source_manifest


ROOT = project_root()
I05 = ROOT / "artifacts/phase-2.1/P2.1-R00-60d02be4dbe9-i05"


def inventory(bundle: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(bundle).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(bundle.rglob("*"))
        if path.is_file()
    ]


def copy_source_tree(root: Path) -> None:
    copied: set[str] = set()
    for relative in (*SOURCE_PATHS, "src/lottery_research/__init__.py", "scripts/phase2_1/bootstrap.py"):
        if relative in copied:
            continue
        copied.add(relative)
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)


def complete_bundle_fixture(root: Path) -> Path:
    copy_source_tree(root)
    destination = root / "artifacts/phase-2.1" / RELEASE_ID
    shutil.copytree(I05, destination)

    for relative in ("contracts/acceptance-contract.json", "contracts/preregistration.json"):
        source = ROOT / ({
            "contracts/acceptance-contract.json": "docs/roadmap/phase-2.1-acceptance-contract.json",
            "contracts/preregistration.json": "config/phase2_1/preregistration.json",
        }[relative])
        (destination / relative).write_bytes(source.read_bytes())

    iteration_06 = destination / "inputs/iteration-06.md"
    iteration_06.write_bytes(b"immutable readiness fixture\n")
    fixture_inputs = ROOT / "tests/phase2_1/fixtures/i07"
    for name in ("iteration-07.md", "iteration-07-run-02-correction.md"):
        (destination / "inputs" / name).write_bytes((fixture_inputs / name).read_bytes())
    contract_path = destination / "contracts/acceptance-contract.json"
    contract = load_json(contract_path)
    contract["task_input_identities"]["iteration-06.md"] = hashlib.sha256(iteration_06.read_bytes()).hexdigest()
    contract_path.write_bytes(canonical_json_bytes(contract))

    readiness_path = destination / "readiness/readiness.json"
    readiness = load_json(readiness_path)
    readiness["release_id"] = RELEASE_ID
    readiness["source_manifest"] = source_manifest(root)
    readiness["input_identity"] = _input_identity(destination, contract)
    frozen_paths = [row["path"] for row in readiness["frozen_input_identities"]]
    for relative in ("inputs/iteration-06.md", "inputs/iteration-07.md", "inputs/iteration-07-run-02-correction.md"):
        if relative not in frozen_paths:
            readiness["frozen_input_identities"].append({"path": relative, "sha256": "0" * 64})
    readiness["frozen_input_identities"] = [
        identity(destination, destination / row["path"])
        for row in readiness["frozen_input_identities"]
    ]
    snapshot = readiness["formal_output_snapshot"]
    existing_paths = {row["path"] for row in snapshot["existing_files"]}
    for relative in ("inputs/iteration-06.md", "inputs/iteration-07.md", "inputs/iteration-07-run-02-correction.md"):
        if relative not in existing_paths:
            snapshot["existing_files"].append({"path": relative, "sha256": "0" * 64})
    snapshot["existing_files"] = [
        identity(destination, destination / row["path"])
        for row in snapshot["existing_files"]
    ]
    snapshot["existing_files"].sort(key=lambda row: row["path"])
    snapshot["existing_inventory_sha256"] = hashlib.sha256(
        canonical_json_bytes(snapshot["existing_files"])
    ).hexdigest()
    snapshot["allowed_final_paths"] = sorted(set(snapshot["allowed_final_paths"]) | {
        "inputs/iteration-06.md", "inputs/iteration-07.md", "inputs/iteration-07-run-02-correction.md",
    })
    snapshot["allowed_final_paths_sha256"] = hashlib.sha256(
        canonical_json_bytes(snapshot["allowed_final_paths"])
    ).hexdigest()
    task_results = root / "task-input/results"
    task_results.mkdir(parents=True)
    readiness["formal_history_scan"]["roots"] = [
        (destination / "results").resolve().as_posix(),
        (root / "artifacts/phase-2.1-protected-results" / RELEASE_ID).resolve().as_posix(),
        task_results.resolve().as_posix(),
    ]
    readiness_path.write_bytes(canonical_json_bytes(readiness))
    return destination


class Iteration06ReadinessTests(unittest.TestCase):
    def test_public_readiness_revalidates_complete_bundle_twice_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bundle = complete_bundle_fixture(root)
            before = inventory(bundle)
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPATH"] = str(root / "src")
            command = [sys.executable, str(root / "scripts/phase2_1/validate_phase2_1_readiness.py")]
            first = subprocess.run(command, cwd=root, env=environment, capture_output=True, check=False)
            between = inventory(bundle)
            second = subprocess.run(command, cwd=root, env=environment, capture_output=True, check=False)
            after = inventory(bundle)

            self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(before, between)
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
