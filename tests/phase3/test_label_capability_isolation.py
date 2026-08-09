from __future__ import annotations

import concurrent.futures
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path

from lottery_research.phase3.data_access import (
    GuardedLabelStore,
    LABEL_FIELDS,
    TRAINER_FORBIDDEN_FIELDS,
    activate_scoring_capability,
    load_target_catalog,
    read_training_prefix,
    trainer_input_payload,
)
from lottery_research.phase3.ledger import AppendOnlyLedger
from lottery_research.phase3.formal import _trainer_fit_target, _trainer_label_store_access_probe
from lottery_research.phase3.serialization import load_json, sha256_file, write_new_json


ROOT = Path(__file__).resolve().parents[2]


class LabelCapabilityIsolationTests(unittest.TestCase):
    def test_target_catalog_and_training_prefix_exclude_target_and_future_labels(self) -> None:
        catalog = load_target_catalog(ROOT)
        self.assertEqual(len(catalog), 300)
        self.assertFalse(any(LABEL_FIELDS.intersection(row.as_dict()) for row in catalog))
        for target in (catalog[0], catalog[149], catalog[150], catalog[-1]):
            prefix = read_training_prefix(ROOT, target)
            payload = trainer_input_payload(target, prefix)
            self.assertEqual(len(prefix), target.source_count)
            self.assertTrue(all(row["game"] == target.game and row["issue_id"] < target.target_issue for row in prefix))
            self.assertFalse(TRAINER_FORBIDDEN_FIELDS.intersection(payload))
            self.assertTrue(all(not TRAINER_FORBIDDEN_FIELDS.intersection(row) for row in payload["prefix"]))

    def test_training_role_cannot_open_any_scoring_or_guarded_label_interface(self) -> None:
        with self.assertRaisesRegex(ValueError, "LABEL_STORE_CAPABILITY_DENIED"):
            GuardedLabelStore(ROOT, capability="scorer")
        with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
            probe = pool.submit(_trainer_label_store_access_probe, ROOT.as_posix()).result(timeout=30)
        self.assertNotEqual(probe["pid"], os.getpid())
        self.assertTrue(probe["denied"])
        self.assertTrue(probe["direct_artifact_denied"])
        self.assertTrue(probe["subprocess_denied"] and probe["fork_denied"] and probe["exec_denied"])
        self.assertEqual(probe["number_read_count"], 0)

    def test_trainer_fits_in_spawned_process_from_prefix_only_payload(self) -> None:
        target = load_target_catalog(ROOT)[0]
        payload = trainer_input_payload(target, read_training_prefix(ROOT, target))
        with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
            probe = pool.submit(_trainer_label_store_access_probe, ROOT.as_posix()).result(timeout=30)
            trained = pool.submit(_trainer_fit_target, payload).result(timeout=30)
        self.assertEqual(trained["trainer_pid"], probe["pid"])
        self.assertTrue(
            probe["denied"] and probe["direct_artifact_denied"]
            and probe["subprocess_denied"] and probe["fork_denied"] and probe["exec_denied"]
        )
        self.assertEqual(trained["training_count"], target.source_count)
        self.assertEqual(set(trained["models"]), {"M0", "M1"})

    def _fixture(self, base: Path) -> tuple[Path, AppendOnlyLedger, GuardedLabelStore, dict[str, str]]:
        release = base / "release-r03"
        forecast = release / "runs/forecasts/dlt/2025084/M0.json"
        values = {
            "release_id": release.name,
            "run_id": "run-r03",
            "game": "dlt",
            "target_issue": "2025084",
            "model_id": "M0",
        }
        write_new_json(forecast, values)
        experiment = "dlt-2025084-M0"
        attempt = f"{experiment}-attempt-01"
        ledger = AppendOnlyLedger(release / "runs/experiment-ledger.jsonl", values["run_id"])
        ledger.start(experiment, {**{key: values[key] for key in ("release_id", "game", "target_issue", "model_id")}}, attempt_id=attempt)
        store = GuardedLabelStore(ROOT, capability=activate_scoring_capability())
        values.update({
            "experiment_id": experiment,
            "attempt_id": attempt,
            "forecast_path": forecast.as_posix(),
            "receipt_path": (release / "runs/label-unlocks/dlt/2025084/M0.json").as_posix(),
        })
        return release, ledger, store, values

    @staticmethod
    def _lock(release: Path, ledger: AppendOnlyLedger, values: dict[str, str], *, sha: str | None = None) -> None:
        forecast = Path(values["forecast_path"])
        ledger.progress(values["experiment_id"], "forecast_locked", {
            "release_id": values["release_id"], "run_id": values["run_id"],
            "experiment_id": values["experiment_id"], "attempt_id": values["attempt_id"],
            "game": values["game"], "target_issue": values["target_issue"], "model_id": values["model_id"],
            "forecast_path": forecast.relative_to(release).as_posix(),
            "forecast_sha256": sha or sha256_file(forecast),
            "prediction_locked_at": "2026-08-09T00:00:00Z",
        }, attempt_id=values["attempt_id"])

    @staticmethod
    def _unlock(release: Path, default_ledger: AppendOnlyLedger, store: GuardedLabelStore, values: dict[str, str], **overrides: object):
        arguments = {
            "release_root": release,
            "ledger": default_ledger,
            "experiment_id": values["experiment_id"],
            "attempt_id": values["attempt_id"],
            "release_id": values["release_id"],
            "run_id": values["run_id"],
            "game": values["game"],
            "target_issue": values["target_issue"],
            "model_id": values["model_id"],
            "forecast_path": Path(values["forecast_path"]),
            "receipt_path": Path(values["receipt_path"]),
        }
        arguments.update(overrides)
        return store.guarded_unlock(**arguments)

    def test_guarded_unlock_positive_receipt_binds_all_identities_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            release, ledger, store, values = self._fixture(Path(raw))
            self._lock(release, ledger, values)
            unlocked = self._unlock(release, ledger, store, values)
            receipt = load_json(Path(values["receipt_path"]))
            self.assertEqual(store.number_read_count, 1)
            self.assertEqual(unlocked.receipt_sha256, sha256_file(Path(values["receipt_path"])))
            self.assertEqual(
                (receipt["release_id"], receipt["experiment_id"], receipt["attempt_id"], receipt["target_issue"], receipt["forecast_sha256"], receipt["label_store_identity"]),
                (values["release_id"], values["experiment_id"], values["attempt_id"], values["target_issue"], sha256_file(Path(values["forecast_path"])), store.identity),
            )
            ledger.progress(values["experiment_id"], "scored", {}, attempt_id=values["attempt_id"])
            ledger.finish(values["experiment_id"], "succeeded", {}, attempt_id=values["attempt_id"])
            ledger.close()

    def test_all_unlock_rejections_happen_before_label_numbers_are_read(self) -> None:
        cases = (
            "pre_lock", "wrong_hash", "forecast_rewritten", "wrong_release", "wrong_experiment",
            "wrong_attempt", "wrong_target", "wrong_ledger", "interleaved_ledger", "trainer_access",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as raw:
                release, ledger, store, values = self._fixture(Path(raw))
                if case != "pre_lock":
                    self._lock(release, ledger, values, sha="0" * 64 if case == "wrong_hash" else None)
                overrides: dict[str, str] = {}
                if case == "forecast_rewritten":
                    forecast = Path(values["forecast_path"])
                    forecast.unlink()
                    write_new_json(forecast, {**{key: values[key] for key in ("release_id", "run_id", "game", "target_issue", "model_id")}, "mutated": True})
                elif case == "wrong_release":
                    overrides["release_id"] = "wrong-release"
                elif case == "wrong_experiment":
                    overrides["experiment_id"] = "dlt-2025084-M1"
                elif case == "wrong_attempt":
                    overrides["attempt_id"] = f"{values['experiment_id']}-attempt-02"
                elif case == "wrong_target":
                    overrides["target_issue"] = "2025085"
                    overrides["experiment_id"] = "dlt-2025085-M0"
                    overrides["attempt_id"] = "dlt-2025085-M0-attempt-01"
                elif case == "trainer_access":
                    with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as pool:
                        probe = pool.submit(_trainer_label_store_access_probe, ROOT.as_posix()).result(timeout=30)
                    self.assertTrue(probe["denied"])
                    self.assertTrue(probe["direct_artifact_denied"])
                    self.assertTrue(probe["subprocess_denied"] and probe["fork_denied"] and probe["exec_denied"])
                    self.assertEqual(probe["number_read_count"], 0)
                    self.assertEqual(store.number_read_count, 0)
                    ledger.close()
                    continue
                elif case == "wrong_ledger":
                    forged = AppendOnlyLedger(Path(raw) / "forged-ledger.jsonl", values["run_id"])
                    forged.start(values["experiment_id"], {
                        key: values[key] for key in ("release_id", "game", "target_issue", "model_id")
                    }, attempt_id=values["attempt_id"])
                    self._lock(release, forged, values)
                    overrides["ledger"] = forged
                elif case == "interleaved_ledger":
                    other = "dlt-2025084-M1"
                    ledger.start(other, {
                        "release_id": values["release_id"], "game": "dlt",
                        "target_issue": "2025084", "model_id": "M1",
                    }, attempt_id=f"{other}-attempt-01")
                with self.assertRaises(ValueError):
                    self._unlock(release, ledger, store, values, **overrides)
                self.assertEqual(store.number_read_count, 0)
                self.assertFalse(Path(values["receipt_path"]).exists())
                ledger.close()
                if case == "wrong_ledger":
                    forged.close()


if __name__ == "__main__":
    unittest.main()
