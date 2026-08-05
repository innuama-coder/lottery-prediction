from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from lottery_data import cli
from lottery_data.steps.preflight import (
    BootstrapArguments,
    IncrementalArguments,
    PreflightError,
    _load_snapshot_configuration,
    prepare_bootstrap,
    prepare_incremental,
)
from lottery_data.workflow import default_dependencies


REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
CONFIG = REPO / "config" / "phase1"
NOW = "2026-08-03T00:00:00.000Z"


class SnapshotConfigurationFreezeTests(unittest.TestCase):
    def _copy_config(self, root: Path) -> Path:
        config = root / "config"
        config.mkdir(parents=True)
        for name in ("source-catalog.json", "collection-policy.json", "live-source-policy.json"):
            shutil.copyfile(CONFIG / name, config / name)
        return config

    def _publication_fixture(self, root: Path) -> None:
        root.mkdir(parents=True)
        shutil.copyfile(REPO / "artifacts/phase-1/current-release.json", root / "current-release.json")
        shutil.copytree(REPO / "artifacts/phase-1/releases/baseline-v1", root / "releases/baseline-v1")
        shutil.copytree(REPO / "artifacts/phase-1/baseline-v1", root / "baseline-v1")

    def _arguments(self, mode: str, artifacts: Path, config: Path | None):
        if mode == "bootstrap":
            return BootstrapArguments("bootstrap", "snapshot", SNAPSHOT, artifacts, config, "freeze-bootstrap", "freeze-release")
        return IncrementalArguments("incremental", "snapshot", SNAPSHOT, artifacts, config, "freeze-incremental", "freeze-release")

    def _prepare(self, arguments, builder):
        deps = default_dependencies()
        if arguments.mode == "bootstrap":
            return prepare_bootstrap(arguments, clock=lambda: NOW, build_request_plan=builder, load_source_catalog=deps.load_source_catalog)
        return prepare_incremental(arguments, clock=lambda: NOW, build_request_plan=builder, load_source_catalog=deps.load_source_catalog)

    def _assert_rejected_before_plan(self, mode: str, config: Path) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory) / "artifacts"
            if mode == "incremental":
                self._publication_fixture(artifacts)
            builder = Mock(side_effect=AssertionError("request plan must not be built"))
            with self.assertRaises(PreflightError):
                self._prepare(self._arguments(mode, artifacts, config), builder)
            builder.assert_not_called()
            self.assertFalse((artifacts / "runs" / f"freeze-{mode}").exists())

    def test_exact_default_and_explicit_snapshot_configuration_pass_both_modes(self) -> None:
        deps = default_dependencies()
        default_loaded = _load_snapshot_configuration(None, deps.load_source_catalog)
        explicit_loaded = _load_snapshot_configuration(CONFIG, deps.load_source_catalog)
        self.assertEqual(default_loaded[2:4], explicit_loaded[2:4])
        self.assertEqual(default_loaded[4:], explicit_loaded[4:])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._copy_config(root)
            for mode in ("bootstrap", "incremental"):
                with self.subTest(mode=mode):
                    artifacts = root / mode
                    if mode == "incremental":
                        self._publication_fixture(artifacts)
                    result = self._prepare(self._arguments(mode, artifacts, config), deps.build_request_plan)
                    self.assertGreater(len(result.request_plan), 0)

    def test_valid_json_source_catalog_tamper_is_rejected_in_both_snapshot_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._copy_config(Path(directory))
            value = json.loads((config / "source-catalog.json").read_text(encoding="utf-8"))
            value["tampered_but_valid_json"] = True
            (config / "source-catalog.json").write_text(json.dumps(value), encoding="utf-8")
            for mode in ("bootstrap", "incremental"):
                self._assert_rejected_before_plan(mode, config)

    def test_valid_json_collection_policy_tamper_is_rejected_in_both_snapshot_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._copy_config(Path(directory))
            value = json.loads((config / "collection-policy.json").read_text(encoding="utf-8"))
            value["tampered_but_valid_json"] = True
            (config / "collection-policy.json").write_text(json.dumps(value), encoding="utf-8")
            for mode in ("bootstrap", "incremental"):
                self._assert_rejected_before_plan(mode, config)

    def test_missing_snapshot_configuration_is_rejected_before_plan_in_both_modes(self) -> None:
        for missing in ("source-catalog.json", "collection-policy.json"):
            with tempfile.TemporaryDirectory() as directory:
                config = self._copy_config(Path(directory))
                (config / missing).unlink()
                for mode in ("bootstrap", "incremental"):
                    with self.subTest(missing=missing, mode=mode):
                        self._assert_rejected_before_plan(mode, config)

    def test_source_catalog_toctou_is_rejected_before_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = self._copy_config(Path(directory))
            deps = default_dependencies()

            def mutate_after_load(path: Path):
                value = deps.load_source_catalog(path)
                path.write_bytes(path.read_bytes() + b"\n")
                return value

            builder = Mock()
            arguments = self._arguments("bootstrap", Path(directory) / "artifacts", config)
            with self.assertRaisesRegex(PreflightError, "changed during preflight"):
                prepare_bootstrap(arguments, clock=lambda: NOW, build_request_plan=builder, load_source_catalog=mutate_after_load)
            builder.assert_not_called()

    def test_cli_snapshot_rejection_is_exit4_without_run_release_pointer_or_fake_refs(self) -> None:
        for tampered_name in ("source-catalog.json", "collection-policy.json"):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                config = self._copy_config(base)
                value = json.loads((config / tampered_name).read_text(encoding="utf-8"))
                value["attack"] = "valid-json"
                (config / tampered_name).write_text(json.dumps(value), encoding="utf-8")
                for mode in ("bootstrap", "incremental"):
                    with self.subTest(tampered=tampered_name, mode=mode):
                        artifacts = base / mode
                        if mode == "incremental":
                            self._publication_fixture(artifacts)
                        argv = ["run", "--mode", mode, "--source-mode", "snapshot", "--artifacts-root", str(artifacts),
                                "--config-root", str(config), "--run-id", f"cli-{mode}", "--release-id", f"cli-{mode}-release"]
                        argv.extend(["--phase0-snapshot", str(SNAPSHOT)] if mode == "bootstrap" else ["--snapshot-root", str(SNAPSHOT)])
                        stdout, stderr = io.StringIO(), io.StringIO()
                        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = cli.main(argv)
                        result = json.loads(stdout.getvalue())
                        self.assertEqual((code, result["exit_code"], result["status"]), (4, 4, "rejected"))
                        self.assertNotIn("manifest_ref", result)
                        self.assertFalse((artifacts / "runs" / f"cli-{mode}").exists())
                        self.assertFalse((artifacts / "releases" / f"cli-{mode}-release").exists())
                        self.assertFalse((artifacts / f"cli-{mode}-release").exists())
                        if mode == "bootstrap":
                            self.assertFalse((artifacts / "current-release.json").exists())

    def test_current_live_preflight_does_not_use_snapshot_configuration_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory) / "artifacts"
            self._publication_fixture(artifacts)
            deps = default_dependencies()
            arguments = IncrementalArguments("incremental", "live", None, artifacts, CONFIG, "live-freeze-control", "live-release")
            preflight = prepare_incremental(
                arguments, clock=lambda: NOW, build_request_plan=Mock(side_effect=AssertionError("snapshot builder called")),
                load_source_catalog=Mock(side_effect=AssertionError("snapshot loader called")),
            )
            self.assertEqual(preflight.manifest["run_schema_version"], "1.3.0")
            self.assertEqual(len(preflight.request_plan), 4)


if __name__ == "__main__":
    unittest.main()
