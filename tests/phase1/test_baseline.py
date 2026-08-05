from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "phase1"))
import run_acceptance as acceptance  # noqa: E402


class BaselineAcceptanceRunnerTests(unittest.TestCase):
    def test_stdout_parser_fails_closed_on_multiple_values(self) -> None:
        with self.assertRaises(ValueError):
            acceptance._parse_single_json("{}\n{}\n")

    def test_unrecognized_cli_is_fail_not_hold(self) -> None:
        contract_path = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
        contract = acceptance.load_contract(contract_path)
        command = {"actual_exit_code": 4, "stderr": "unrecognized arguments", "result": {"status": "rejected"}}
        with patch.object(acceptance, "_run", return_value=(command, command["result"])):
            code, report = acceptance._case_report("E2E-01", contract, contract_path)
        self.assertEqual((code, report["status"]), (acceptance.FAIL, "FAIL"))

    def test_temp_root_guard_rejects_repository_and_accepts_system_temp(self) -> None:
        with self.assertRaises(ValueError):
            acceptance._safe_temp_root(REPO / "artifacts" / "phase-1" / "unsafe")
        with tempfile.TemporaryDirectory(prefix="phase1-safe-") as temporary:
            acceptance._safe_temp_root(Path(temporary))

    def test_assertion_always_records_expected_actual_and_evidence_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence.json"
            evidence.write_text("{}\n", encoding="utf-8")
            output: list[dict[str, object]] = []
            self.assertTrue(acceptance._assertion(output, "example", 1, 1, [evidence]))
            self.assertEqual(output[0]["expected"], 1)
            self.assertEqual(output[0]["actual"], 1)
            self.assertRegex(next(iter(output[0]["evidence_sha256"].values())), r"^[0-9a-f]{64}$")

    def test_hash_manifest_attack_matrix_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            managed = root / "data.jsonl"
            managed.write_text("{}\n", encoding="utf-8")
            manifest = root / "hashes.json"
            entry = {"path": "data.jsonl", "sha256": acceptance.sha256_file(managed), "size_bytes": managed.stat().st_size}
            valid = {"hash_manifest_schema_version": "1.0.0", "hash_profile": "sha256-file-manifest-v1", "entries": [entry]}
            manifest.write_text(json.dumps(valid) + "\n", encoding="utf-8")
            self.assertTrue(acceptance._hash_manifest_valid(manifest, root, frozenset({"data.jsonl"})))
            managed.write_text("{\"changed\":true}\n", encoding="utf-8")
            self.assertFalse(acceptance._hash_manifest_valid(manifest, root, frozenset({"data.jsonl"})))
            managed.write_text("{}\n", encoding="utf-8")
            attacks = [
                {**valid, "entries": []},
                {**valid, "hash_profile": "md5"},
                {**valid, "entries": [entry, entry]},
                {**valid, "entries": [{**entry, "path": "../data.jsonl"}]},
            ]
            for attack in attacks:
                manifest.write_text(json.dumps(attack) + "\n", encoding="utf-8")
                self.assertFalse(acceptance._hash_manifest_valid(manifest, root, frozenset({"data.jsonl"})))
            manifest.write_text(json.dumps(valid) + "\n", encoding="utf-8")
            (root / "unmanaged.txt").write_text("x", encoding="utf-8")
            self.assertFalse(acceptance._hash_manifest_valid(manifest, root, frozenset({"data.jsonl"})))
            (root / "unmanaged.txt").unlink()
            with patch.object(Path, "is_symlink", return_value=True):
                self.assertFalse(acceptance._hash_manifest_valid(manifest, root, frozenset({"data.jsonl"})))

    def test_network_guard_blocks_connect_ex_and_udp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = acceptance._network_guard(Path(temporary))
            for expression in (
                "socket.socket().connect_ex(('127.0.0.1', 9))",
                "socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('127.0.0.1', 9))",
            ):
                completed = subprocess.run(
                    [sys.executable, "-c", f"import socket; {expression}"], env=environment,
                    text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("network forbidden by Phase 1 snapshot acceptance", completed.stderr)

    def test_evidence_and_event_attack_helpers_reject_broken_bijections(self) -> None:
        observations = [
            {"observation_id": f"o{i}", "publisher_id": f"p{i}", "source_id": f"s{i}", "raw_ref": f"raw/{i}", "raw_sha256": str(i) * 64, "game": "ssq", "issue_id": "2026001", "core_fact_sha256": "a" * 64}
            for i in (1, 2)
        ]
        draw = {"game": "ssq", "issue_id": "2026001", "core_fact_sha256": "a" * 64, "evidence_links": [
            {key: row[key] for key in ("observation_id", "publisher_id", "source_id", "raw_ref", "raw_sha256")} for row in observations
        ]}
        self.assertFalse(acceptance._evidence_bijection_valid([draw] * 400, observations, observations))
        plan = [{"request_id": "r1", "source_id": "s1", "game": "ssq"}]
        events = [{"sequence": i, "run_id": "wrong", "event_type": "run_started", "request_id": None} for i in range(1, 64)]
        self.assertFalse(acceptance._events_valid(events, {"request_plan": plan}, "expected"))

    def test_full_oracle_rejects_real_resigned_semantic_attacks(self) -> None:
        run_id, release_id = "attack-bootstrap", "attack-release"

        def write_json(path: Path, value: object) -> None:
            path.write_bytes(acceptance.canonical_bytes(value))

        def resign(root: Path) -> None:
            for manifest_path, base in (
                (root / "runs" / run_id / "hashes.json", root),
                (root / "releases" / release_id / "hashes.json", root / "releases" / release_id),
                (root / release_id / "hashes.json", root / release_id),
            ):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path == root / "runs" / run_id / "hashes.json":
                    run = root / "runs" / run_id
                    entries = []
                    for target in sorted(path for path in run.rglob("*") if path.is_file() and path.name != "hashes.json"):
                        local = target.relative_to(run).as_posix()
                        role = (
                            "raw" if local.startswith("raw/") else
                            "config" if local.startswith("config/") else "managed"
                        )
                        entries.append({
                            "path": target.relative_to(root).as_posix(),
                            "sha256": acceptance.sha256_file(target),
                            "size_bytes": target.stat().st_size,
                            "role": role,
                        })
                    manifest["entries"] = entries
                else:
                    for entry in manifest["entries"]:
                        target = base / entry["path"]
                        entry["sha256"] = acceptance.sha256_file(target)
                        entry["size_bytes"] = target.stat().st_size
                write_json(manifest_path, manifest)
            pointer_path = root / "current-release.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            pointer["manifest_sha256"] = acceptance.sha256_file(root / "releases" / release_id / "manifest.json")
            write_json(pointer_path, pointer)

        def resign_run_metadata(root: Path) -> dict:
            run = root / "runs" / run_id
            manifest_sha = acceptance.sha256_file(run / "run-manifest.json")
            quality = json.loads((run / "quality-report.json").read_text(encoding="utf-8"))
            quality["deterministic"]["input_hashes"]["run_manifest"] = manifest_sha
            for path in (
                run / "quality-report.json",
                root / "releases" / release_id / "quality-report.json",
                root / release_id / "quality-report.json",
            ):
                write_json(path, quality)
            for path in (root / "releases" / release_id / "manifest.json", root / release_id / "manifest.json"):
                value = json.loads(path.read_text(encoding="utf-8"))
                value["input_manifest_sha256"] = manifest_sha
                write_json(path, value)
            result_path = run / "run-result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["deterministic_artifact_hashes"] = {
                "candidate_draws": acceptance.sha256_file(run / "candidate-draws.jsonl"),
                "events": acceptance.sha256_file(run / "events.jsonl"),
                "observations": acceptance.sha256_file(run / "observations.jsonl"),
                "quality_report": acceptance.sha256_file(run / "quality-report.json"),
                "reconciliation": acceptance.sha256_file(run / "reconciliation.jsonl"),
                "run_manifest": manifest_sha,
            }
            write_json(result_path, result)
            return result

        with tempfile.TemporaryDirectory(prefix="phase1-resigned-oracle-") as temporary:
            fixture = Path(temporary) / "fixture"
            environment = acceptance._network_guard(Path(temporary))
            argv = [sys.executable, "-m", "lottery_data", "run", "--mode", "bootstrap", "--source-mode", "snapshot", "--phase0-snapshot", str(acceptance.SNAPSHOT), "--run-id", run_id, "--release-id", release_id, "--artifacts-root", str(fixture)]
            command, stdout_result = acceptance._run(argv, environment)
            self.assertEqual(command["actual_exit_code"], 0)
            self.assertIsNotNone(stdout_result)
            ok, _, _ = acceptance._release_oracle(fixture, release_id, run_id, acceptance.SNAPSHOT, stdout_result)
            self.assertTrue(ok)

            for name in (
                "run-quality", "events-resigned", "pointer", "release-manifest", "run-result",
                "result-quality-missing", "result-quality-wrong", "result-extra",
                "raw-inventory-swap", "config-missing", "config-extra", "config-external",
                "config-mixed", "config-duplicate", "config-unknown", "config-bytes",
            ):
                root = Path(temporary) / name
                shutil.copytree(fixture, root)
                attacked_stdout = json.loads((root / "runs" / run_id / "run-result.json").read_text(encoding="utf-8"))
                if name == "run-quality":
                    path = root / "runs" / run_id / "quality-report.json"
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["deterministic"]["counts"]["draws"] = 399
                    write_json(path, value)
                    attacked_stdout = resign_run_metadata(root)
                elif name == "events-resigned":
                    path = root / "runs" / run_id / "events.jsonl"
                    events = acceptance._json_lines(path)
                    next(row for row in events if row["event_type"] == "request_succeeded")["artifact_ref"] = "raw/attacker/replacement.html"
                    path.write_bytes(b"".join(acceptance.canonical_bytes(row) for row in events))
                    attacked_stdout = resign_run_metadata(root)
                elif name == "pointer":
                    path = root / "current-release.json"
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["manifest_ref"] = "releases/wrong/manifest.json"
                    value["updated_by_run_id"] = "resigned-wrong-run"
                    write_json(path, value)
                elif name == "release-manifest":
                    for path in (root / "releases" / release_id / "manifest.json", root / release_id / "manifest.json"):
                        value = json.loads(path.read_text(encoding="utf-8"))
                        value["input_run_id"] = "resigned-wrong-run"
                        value["records_sha256"] = "0" * 64
                        write_json(path, value)
                elif name == "run-result":
                    path = root / "runs" / run_id / "run-result.json"
                    attacked_stdout["deterministic_artifact_hashes"]["observations"] = "0" * 64
                    write_json(path, attacked_stdout)
                elif name in {"result-quality-missing", "result-quality-wrong"}:
                    path = root / "runs" / run_id / "run-result.json"
                    if name == "result-quality-missing":
                        attacked_stdout["deterministic_artifact_hashes"].pop("quality_report")
                    else:
                        attacked_stdout["deterministic_artifact_hashes"]["quality_report"] = "0" * 64
                    write_json(path, attacked_stdout)
                elif name == "result-extra":
                    path = root / "runs" / run_id / "run-result.json"
                    attacked_stdout["deterministic_artifact_hashes"]["attacker"] = "0" * 64
                    write_json(path, attacked_stdout)
                elif name == "raw-inventory-swap":
                    run = root / "runs" / run_id
                    victim = sorted(path for path in (run / "raw").rglob("*") if path.is_file())[0]
                    payload = victim.read_bytes()
                    victim.unlink()
                    replacement = run / "raw" / "attacker" / "replacement.html"
                    replacement.parent.mkdir(parents=True)
                    replacement.write_bytes(payload)
                else:
                    run = root / "runs" / run_id
                    manifest_path = run / "run-manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if name == "config-missing":
                        manifest["config_files"].pop()
                    elif name == "config-extra":
                        extra = run / "config" / "extra.json"
                        write_json(extra, {"attacker": True})
                        manifest["config_files"].append({
                            "ref": "config/extra.json", "sha256": acceptance.sha256_file(extra),
                        })
                    elif name == "config-external":
                        external = root / "runs" / "external-config.json"
                        write_json(external, {"attacker": True})
                        manifest["config_files"][-1] = {
                            "ref": "../external-config.json", "sha256": acceptance.sha256_file(external),
                        }
                    elif name == "config-mixed":
                        legacy = REPO / "config" / "phase1" / "source-catalog.json"
                        manifest["config_files"][-1] = {
                            "ref": "config/phase1/source-catalog.json", "sha256": acceptance.sha256_file(legacy),
                        }
                    elif name == "config-duplicate":
                        manifest["config_files"][-1] = dict(manifest["config_files"][0])
                    elif name == "config-unknown":
                        unknown = run / "config" / "unknown.json"
                        write_json(unknown, {"attacker": True})
                        manifest["config_files"][-1] = {
                            "ref": "config/unknown.json", "sha256": acceptance.sha256_file(unknown),
                        }
                    else:
                        changed = run / "config" / "source-catalog.json"
                        changed.write_bytes(changed.read_bytes() + b" ")
                        manifest["config_files"][-1]["sha256"] = acceptance.sha256_file(changed)
                    write_json(manifest_path, manifest)
                    attacked_stdout = resign_run_metadata(root)
                resign(root)
                ok, _, _ = acceptance._release_oracle(root, release_id, run_id, acceptance.SNAPSHOT, attacked_stdout)
                self.assertFalse(ok, name)

    def test_formal_legacy_oracle_is_read_only_and_rejects_profile_upgrade(self) -> None:
        formal = acceptance.FORMAL_ROOT
        pointer = json.loads((formal / "current-release.json").read_text(encoding="utf-8"))
        run_id, release_id = pointer["updated_by_run_id"], pointer["release_id"]
        before = acceptance._tree_hashes(formal)
        ok, _, _ = acceptance._release_oracle(formal, release_id, run_id, acceptance.SNAPSHOT)
        self.assertTrue(ok)
        self.assertEqual(before, acceptance._tree_hashes(formal))

        with tempfile.TemporaryDirectory(prefix="phase1-legacy-profile-") as temporary:
            for name in ("sixth-result-key", "run-local-config"):
                root = Path(temporary) / name
                shutil.copytree(formal, root)
                run = root / "runs" / run_id
                if name == "sixth-result-key":
                    result_path = run / "run-result.json"
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    result["deterministic_artifact_hashes"]["quality_report"] = acceptance.sha256_file(run / "quality-report.json")
                    result_path.write_bytes(acceptance.canonical_bytes(result))
                else:
                    config = run / "config" / "collection-policy.json"
                    config.parent.mkdir(parents=True)
                    shutil.copyfile(REPO / "config" / "phase1" / "collection-policy.json", config)
                hashes_path = run / "hashes.json"
                hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
                hashes["entries"] = [
                    {
                        "path": path.relative_to(root).as_posix(),
                        "sha256": acceptance.sha256_file(path),
                        "size_bytes": path.stat().st_size,
                        "role": "config" if path.relative_to(run).as_posix().startswith("config/") else
                                "raw" if path.relative_to(run).as_posix().startswith("raw/") else "managed",
                    }
                    for path in sorted(run.rglob("*"), key=lambda item: item.as_posix())
                    if path.is_file() and path != hashes_path
                ]
                hashes_path.write_bytes(acceptance.canonical_bytes(hashes))
                ok, _, _ = acceptance._release_oracle(root, release_id, run_id, acceptance.SNAPSHOT)
                self.assertFalse(ok, name)

    def test_formal_state_detects_any_tree_change_but_excludes_g2_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(acceptance, "FORMAL_ROOT", Path(temporary)):
            root = Path(temporary)
            before = acceptance._formal_state()
            (root / "nested").mkdir()
            (root / "nested" / "x").write_text("x", encoding="utf-8")
            self.assertNotEqual(before, acceptance._formal_state())
            (root / "acceptance").mkdir()
            (root / "acceptance" / "g2.json").write_text("first", encoding="utf-8")
            state = acceptance._formal_state()
            (root / "acceptance" / "g2.json").write_text("second", encoding="utf-8")
            self.assertEqual(state, acceptance._formal_state())

    def test_g2_contract_argv_whitelist_and_recursion_guard_fail_closed(self) -> None:
        contract_path = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
        contract = acceptance.load_contract(contract_path)
        tampered = json.loads(json.dumps(contract))
        next(g for g in tampered["gates"] if g["id"] == "G2")["verification"][0]["argv"].append("--extra")
        with self.assertRaises(ValueError):
            acceptance.run_g2(tampered, contract_path)
        with patch.dict(os.environ, {"LOTTERY_ACCEPTANCE_DEPTH": "1"}):
            with self.assertRaises(RuntimeError):
                acceptance.run_g2(contract, contract_path)

    def test_g2_contract_assertions_are_exact_and_oracles_are_separate(self) -> None:
        contract_path = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
        contract = acceptance.load_contract(contract_path)
        hashes = {"draws": "a" * 64, "observations": "b" * 64}
        e2e02 = {
            "status": "PASS",
            "assertions": [
                {"id": "independent_bootstrap_hash_match", "status": "PASS", "expected": hashes,
                 "actual": hashes, "evidence_sha256": {"a/draws": hashes["draws"], "b/draws": hashes["draws"], "a/observations": hashes["observations"], "b/observations": hashes["observations"]}},
                {"id": "snapshot_incremental_status", "status": "PASS",
                 "expected": {"exit_code": 0, "status": "no_change", "release_id": None},
                 "actual": {"exit_code": 0, "status": "no_change", "release_id": None},
                 "evidence_sha256": {"run-result.json": "c" * 64}},
            ],
        }
        command = {"actual_exit_code": 0, "status": "PASS", "stdout_sha256": "d" * 64}
        oracle = [{"id": f"oracle-{index}", "status": "PASS"} for index in range(17)]
        with (
            patch.object(acceptance, "run_g1", return_value=(acceptance.PASS, {"status": "PASS"})),
            patch.object(acceptance, "_run", side_effect=[(command, {"status": "PASS"}), (command, e2e02), (command, {"status": "verified"})]),
            patch.object(acceptance, "_release_oracle", return_value=(True, oracle, {})),
        ):
            code, report = acceptance.run_g2(contract, contract_path)
        self.assertEqual((code, report["status"]), (acceptance.PASS, "PASS"))
        self.assertEqual([item["id"] for item in report["assertions"]], acceptance.EXPECTED_G2_ASSERTIONS)
        self.assertEqual(len(report["oracle_assertions"]), 18)
        self.assertTrue(all(set(item) >= {"id", "status", "expected", "actual", "evidence_sha256"} for item in report["assertions"]))

        tampered = json.loads(json.dumps(e2e02))
        tampered["assertions"][0]["actual"] = {"draws": "e" * 64, "observations": "b" * 64}
        with (
            patch.object(acceptance, "run_g1", return_value=(acceptance.PASS, {"status": "PASS"})),
            patch.object(acceptance, "_run", side_effect=[(command, {"status": "PASS"}), (command, tampered), (command, {"status": "verified"})]),
            patch.object(acceptance, "_release_oracle", return_value=(True, oracle, {})),
        ):
            code, report = acceptance.run_g2(contract, contract_path)
        self.assertEqual((code, report["status"]), (acceptance.FAIL, "FAIL"))

    def test_g2_unknown_duplicate_or_missing_contract_assertion_fails_closed(self) -> None:
        contract_path = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
        contract = acceptance.load_contract(contract_path)
        for mutation in ("unknown", "duplicate", "missing"):
            attacked = json.loads(json.dumps(contract))
            assertions = next(gate for gate in attacked["gates"] if gate["id"] == "G2")["assertions"]
            if mutation == "unknown":
                assertions[-1] = "attacker=true"
            elif mutation == "duplicate":
                assertions[-1] = assertions[0]
            else:
                assertions.pop()
            code, report = acceptance.run_g2(attacked, contract_path)
            self.assertEqual((code, report["status"]), (acceptance.FAIL, "FAIL"), mutation)
            self.assertEqual(report["commands"], [], mutation)

    def test_g2_missing_formal_baseline_is_hold_without_running_cases(self) -> None:
        contract_path = REPO / "docs" / "roadmap" / "phase-1-acceptance-contract.json"
        contract = acceptance.load_contract(contract_path)
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(acceptance, "FORMAL_ROOT", Path(temporary)),
            patch.object(acceptance, "run_g1", return_value=(acceptance.PASS, {"status": "PASS"})),
            patch.object(acceptance, "_run") as run_command,
        ):
            code, report = acceptance.run_g2(contract, contract_path)
        self.assertEqual((code, report["status"]), (acceptance.HOLD, "HOLD"))
        run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
