from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
FORMAL = REPO / "artifacts" / "phase-1"
FIXTURE = REPO / "tests" / "phase1" / "fixtures" / "real" / "e2e04-fallback.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def tree_state(root: Path) -> dict[str, tuple[str, str | None]]:
    state: dict[str, tuple[str, str | None]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            state[relative] = ("directory", None)
        elif path.is_file():
            state[relative] = ("file", sha256(path))
    return state


class RealFallbackCaseTests(unittest.TestCase):
    def test_real_snapshot_bootstrap_closes_both_dlt_fallbacks(self) -> None:
        frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))
        formal_before = tree_state(FORMAL)
        self.addCleanup(self.assertEqual, formal_before, tree_state(FORMAL))
        self.assertEqual(sha256(SNAPSHOT / "artifact-hashes.json"), frozen["snapshot_artifact_hashes_sha256"])
        self.assertEqual(sha256(SNAPSHOT / "capture-manifest.jsonl"), frozen["snapshot_capture_manifest_sha256"])

        with tempfile.TemporaryDirectory(prefix="phase1-e2e04-fallback-") as temporary:
            base = Path(temporary)
            artifacts = base / "artifacts"
            guard = base / "network-guard"
            guard.mkdir()
            (guard / "sitecustomize.py").write_text(
                "import socket\n"
                "def deny(*args, **kwargs): raise RuntimeError('network forbidden in E2E-04')\n"
                "socket.create_connection=deny\n"
                "socket.getaddrinfo=deny\n"
                "socket.socket.connect=deny\n"
                "socket.socket.connect_ex=deny\n"
                "socket.socket.sendto=deny\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join((str(guard), str(REPO / "src")))
            environment["LOTTERY_DATA_NETWORK_DISABLED"] = "1"
            command = [
                sys.executable, "-m", "lottery_data", "run", "--mode", "bootstrap",
                "--source-mode", "snapshot", "--phase0-snapshot", str(SNAPSHOT),
                "--run-id", "e2e04-bootstrap", "--release-id", "e2e04-release",
                "--artifacts-root", str(artifacts),
            ]
            completed = subprocess.run(command, cwd=REPO, env=environment, text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            stdout_lines = completed.stdout.splitlines()
            self.assertEqual(len(stdout_lines), 1)
            result = json.loads(stdout_lines[0])
            self.assertEqual((result["status"], result["release_id"]), ("published", "e2e04-release"))

            run = artifacts / "runs" / "e2e04-bootstrap"
            release = artifacts / "releases" / "e2e04-release"
            run_observations = jsonl(run / "observations.jsonl")
            reconciliation = jsonl(run / "reconciliation.jsonl")
            draws = jsonl(release / "draws.jsonl")
            release_observations = jsonl(release / "observations.jsonl")
            captures = {row["raw_ref"]: row for row in jsonl(SNAPSHOT / "capture-manifest.jsonl")}
            self.assertEqual(sha256(release / "draws.jsonl"), frozen["expected_release_draws_sha256"])
            self.assertEqual(sha256(release / "observations.jsonl"), frozen["expected_release_observations_sha256"])

            for expected in frozen["issues"]:
                key = (expected["game"], expected["issue_id"])
                rows = [row for row in run_observations if (row["game"], row["issue_id"]) == key]
                expected_sources = {row["source_id"]: row for row in expected["sources"]}
                actual_sources = {row["source_id"]: row for row in rows}
                self.assertNotIn(expected["missing_source_id"], actual_sources, key)
                self.assertEqual(set(actual_sources), set(expected_sources), key)
                self.assertEqual(len({row["publisher_id"] for row in rows}), 2, key)
                self.assertEqual({row["core_fact_sha256"] for row in rows}, {expected["core_fact_sha256"]}, key)

                for source_id, expected_source in expected_sources.items():
                    actual = actual_sources[source_id]
                    for field in ("publisher_id", "observation_id", "raw_ref", "raw_sha256"):
                        self.assertEqual(actual[field], expected_source[field], (key, source_id, field))
                    self.assertEqual(actual["source_url"], expected_source["url"], (key, source_id, "source_url"))
                    capture = captures[expected_source["raw_ref"]]
                    self.assertEqual((capture["url"], capture["raw_sha256"]), (expected_source["url"], expected_source["raw_sha256"]), (key, source_id, "capture"))
                    self.assertEqual(sha256(SNAPSHOT / expected_source["raw_ref"]), expected_source["raw_sha256"])
                    self.assertEqual(sha256(run / expected_source["raw_ref"]), expected_source["raw_sha256"])

                recs = [row for row in reconciliation if (row["game"], row["issue_id"]) == key]
                self.assertEqual(len(recs), 1, key)
                rec = recs[0]
                observation_ids = {row["observation_id"] for row in rows}
                self.assertEqual(rec["fallback_rule_id"], expected["fallback_rule_id"], key)
                self.assertEqual(rec["missing_source_ids"], [expected["missing_source_id"]], key)
                self.assertEqual(rec["decision"], "verified", key)
                self.assertEqual(rec["dissenting_observation_ids"], [], key)
                self.assertEqual(set(rec["selected_observation_ids"]), observation_ids, key)
                self.assertEqual(set(rec["agreeing_observation_ids"]), observation_ids, key)

                matching_draws = [row for row in draws if (row["game"], row["issue_id"]) == key]
                self.assertEqual(len(matching_draws), 1, key)
                draw = matching_draws[0]
                self.assertEqual(draw["core_fact_sha256"], expected["core_fact_sha256"], key)
                links = {link["source_id"]: link for link in draw["evidence_links"]}
                self.assertEqual(set(links), set(expected_sources), key)
                for source_id, actual in actual_sources.items():
                    self.assertEqual(
                        links[source_id],
                        {field: actual[field] for field in ("source_id", "publisher_id", "observation_id", "raw_ref", "raw_sha256")},
                        (key, source_id, "evidence_link"),
                    )
                selected = {row["observation_id"]: row for row in release_observations if row["observation_id"] in observation_ids}
                self.assertEqual(set(selected), observation_ids, key)
                self.assertTrue(all(selected[row["observation_id"]] == row for row in rows), key)


if __name__ == "__main__":
    unittest.main()
