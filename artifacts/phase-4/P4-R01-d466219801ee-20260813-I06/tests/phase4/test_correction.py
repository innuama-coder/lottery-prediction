from __future__ import annotations

import copy
import json
import os
import pickle
import shutil
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from lottery_system.phase4.correction import (
    CORRECTION_POLICY_SHA256, CORRECTION_POLICY_VERSION, CorrectionViolation, apply_current_replacements, build_score_correction_impact,
    correction_impact_id, validate_correction_policy, validate_correction_policy_object,
)
from lottery_system.phase4.commands.score import _seed_fixture_runtime, derive_runtime_correction_graph
from lottery_system.phase4.ledger import AppendOnlyLedger
from lottery_system.phase4.metrics import derive_score_id
from lottery_system.phase4.serialization import load_json, sha256_file
from lottery_system.phase4.storage import atomic_replace_json, write_once_json
from lottery_system.phase4.windows import (
    TrustedWindowAnchor, WindowViolation, build_window_metric, canonical_window_anchor,
    resolve_trusted_window_inputs,
)
from lottery_system.phase4 import windows as window_module


ROOT = Path(__file__).resolve().parents[2]


class CorrectionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = load_json(ROOT / "tests/phase4/fixtures/correction/valid.json", reject_floats=True)
        self.graph = {
            "game": "ssq", "issue_id": "fixture-issue-001",
            "old_result_revision_id": "fixture-result-r1", "new_result_revision_id": "fixture-result-r2",
            "new_supersedes_revision_id": "fixture-result-r1", "new_data_release_id": "fixture-data-release-r2",
            "new_data_release_result_revision_ids": ["fixture-result-r2"],
            "current_scores": {"fixture-forecast-a": "score-old-a", "fixture-forecast-b": "score-old-b"},
            "current_aggregates": {"fixture-window-a": "aggregate-old-a"},
            "score_replacements": {"score-old-a": "score-new-a", "score-old-b": "score-new-b"},
            "aggregate_replacements": {"aggregate-old-a": "aggregate-new-a"},
            "pending_research_object_ids": ["research-candidate-a"],
            "alpha_event_ids_before": ["alpha-event-a"],
        }

    def _impact(self):
        return build_score_correction_impact(canonical_graph=self.graph)

    def test_frozen_policy_and_impact_schema(self):
        policy = validate_correction_policy(
            ROOT / "config/phase4/correction-policy-v1.json",
            expected_sha256=CORRECTION_POLICY_SHA256, expected_version=CORRECTION_POLICY_VERSION,
        )
        self.assertEqual(policy["partial_terminal"], "HOLD_CORRECTION_INCOMPLETE")
        impact = self._impact()
        schema = load_json(ROOT / "schemas/phase4/correction-impact.schema.json", reject_floats=True)
        Draft202012Validator(schema).validate(impact)
        self.assertTrue(correction_impact_id(impact).startswith("score-correction-impact-v1:"))

    def test_policy_works_from_explicit_installed_release_path(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / "release-contracts" / "correction-policy-v1.json"
            installed.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "config/phase4/correction-policy-v1.json", installed)
            policy = validate_correction_policy(
                installed, expected_sha256=CORRECTION_POLICY_SHA256,
                expected_version=CORRECTION_POLICY_VERSION,
            )
            self.assertEqual(policy["correction_policy_version"], CORRECTION_POLICY_VERSION)

    def test_runtime_graph_is_derived_from_all_canonical_domains(self):
        runtime_parent = ROOT / "artifacts/phase-4-runtime"
        runtime_parent.mkdir(parents=True, exist_ok=True)
        fixture_path = ROOT / "tests/phase4/fixtures/correction/valid.json"
        provenance = {"producer_actor_id": "p4-implementation-author-i01", "role": "implementation_author",
                      "task_id": "T06", "session_id": "/root/implementation_author",
                      "source_commit": "f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b"}
        with tempfile.TemporaryDirectory(prefix="t06-i04-unit-", dir=runtime_parent) as directory:
            runtime = Path(directory)
            new_revision = _seed_fixture_runtime(
                project=ROOT, runtime=runtime, fixture_path=fixture_path, supplied=self.fixture,
                event_at="2026-01-02T00:00:00Z", provenance=provenance,
            )
            derived = derive_runtime_correction_graph(runtime, new_revision)
            self.assertEqual(derived["graph"]["new_result_revision_id"], new_revision)
            self.assertTrue(derived["graph"]["score_replacements"])
            self.assertTrue(derived["graph"]["aggregate_replacements"])
            self.assertEqual(derived["graph"]["pending_research_object_ids"], ["research-candidate-a"])
            self.assertEqual(derived["graph"]["alpha_event_ids_before"], ["alpha-event-a"])
            self.assertEqual(len(derived["bindings"]["graph_sha256"]), 64)
            score_ids = list(derived["graph"]["current_scores"].values())
            packages, trust, window_contract = resolve_trusted_window_inputs(
                runtime_root=runtime, window_id="fixture-window-a",
            )
            self.assertEqual(window_contract["score_ids"], score_ids)
            arguments = {"packages": packages, "game": "ssq", "model_id": "P4E1",
                         "comparator_champion_id": "M0", "model_release_id": "fixture-release-v1",
                         "window_id": "fixture-window-a", "metric_contract_id": "phase4-metric-v1"}
            self.assertEqual(build_window_metric(**arguments, trusted_anchor=trust)["observation_count"], 1)

            with self.subTest(case_id="capability-documented-constructor"), self.assertRaises(TypeError):
                TrustedWindowAnchor()
            with self.subTest(case_id="capability-copy"), self.assertRaises(TypeError):
                copy.copy(trust)
            with self.subTest(case_id="capability-deepcopy"), self.assertRaises(TypeError):
                copy.deepcopy(trust)
            with self.subTest(case_id="capability-pickle"), self.assertRaises(TypeError):
                pickle.dumps(trust)
            clone = object.__new__(TrustedWindowAnchor)
            with self.subTest(case_id="capability-object-new-clone"), self.assertRaises(WindowViolation):
                build_window_metric(**arguments, trusted_anchor=clone)
            with self.assertRaises(AttributeError):
                setattr(clone, "_token", object())
            alternate_resolve, _alternate_validate = window_module._closed_window_authority_boundary()
            _alternate_packages, alternate_capability, _alternate_contract = alternate_resolve(
                runtime_root=runtime, window_id="fixture-window-a")
            with self.subTest(case_id="capability-direct-helper-import"), self.assertRaises(WindowViolation):
                build_window_metric(**arguments, trusted_anchor=alternate_capability)
            _resolved_packages, exposed_payload, _resolved_contract = window_module._resolve_trusted_window_state(
                runtime_root=runtime, window_id="fixture-window-a")
            exposed_payload["window_id"] = "caller-window"
            exposed_payload["anchor"]["window_id"] = "caller-window"
            with self.subTest(case_id="capability-payload-mutation-is-detached"):
                self.assertEqual(build_window_metric(**arguments, trusted_anchor=trust)["observation_count"], 1)
            if not hasattr(os, "fork"):
                return
            read_fd, write_fd = os.pipe()
            child_pid = os.fork()
            if child_pid == 0:
                os.close(read_fd)
                slot_rewrite_rejected = False
                accepted = False
                try:
                    setattr(trust, "_owner_pid", os.getpid())
                except (AttributeError, TypeError):
                    slot_rewrite_rejected = True
                try:
                    build_window_metric(**arguments, trusted_anchor=trust)
                    accepted = True
                except WindowViolation:
                    pass
                os.write(write_fd, json.dumps({"slot_rewrite_rejected": slot_rewrite_rejected,
                                               "accepted": accepted}).encode("ascii"))
                os.close(write_fd)
                os._exit(0)
            os.close(write_fd)
            child_report = json.loads(os.read(read_fd, 4096).decode("ascii"))
            os.close(read_fd)
            _, child_status = os.waitpid(child_pid, 0)
            with self.subTest(case_id="capability-cross-pid-slot-rewrite"):
                self.assertEqual(child_status, 0)
                self.assertTrue(child_report["slot_rewrite_rejected"])
                self.assertFalse(child_report["accepted"])

            with self.subTest(case_id="missing-external-trust-root"), self.assertRaises(WindowViolation):
                build_window_metric(**arguments, trusted_anchor=None)
            substitutions = {}
            for case_id in ("model", "result", "comparator", "forecast-score-id", "revision-score-id"):
                rows = copy.deepcopy(packages)
                if case_id == "model":
                    rows[0]["detail"]["recomputation"]["model_front"]["ticks"][1] = 1
                elif case_id == "result":
                    rows[0]["detail"]["recomputation"]["result"]["back"] = [2]
                    rows[0]["detail"]["observed_back"] = [2]
                elif case_id == "comparator":
                    rows[0]["score"]["comparator_forecast_id"] = "substitute-m0"
                    rows[0]["detail"]["recomputation"]["comparator_forecast_id"] = "substitute-m0"
                elif case_id == "forecast-score-id":
                    rows[0]["score"]["forecast_id"] = "substitute-forecast"
                    rows[0]["detail"]["recomputation"]["forecast_id"] = "substitute-forecast"
                    rows[0]["score"]["score_id"] = derive_score_id("substitute-forecast", rows[0]["score"]["result_revision_id"], "phase4-metric-v1")
                    rows[0]["detail"]["score_id"] = rows[0]["score"]["score_id"]
                else:
                    rows[0]["score"]["result_revision_id"] = "substitute-revision"
                    rows[0]["detail"]["recomputation"]["result_revision_id"] = "substitute-revision"
                    rows[0]["score"]["score_id"] = derive_score_id(rows[0]["score"]["forecast_id"], "substitute-revision", "phase4-metric-v1")
                    rows[0]["detail"]["score_id"] = rows[0]["score"]["score_id"]
                substitutions[case_id] = rows
            for case_id, rows in substitutions.items():
                fresh = canonical_window_anchor(
                    packages=rows, window_id="fixture-window-a", anchor_source_sha256="0" * 64,
                    current_projection_sha256="1" * 64,
                )
                with self.subTest(case_id=f"coordinated-{case_id}-fresh-anchor"), self.assertRaises(WindowViolation):
                    build_window_metric(**{**arguments, "packages": rows}, trusted_anchor=fresh)
            caller_anchor = canonical_window_anchor(
                packages=packages, window_id="fixture-window-a", anchor_source_sha256="0" * 64,
                current_projection_sha256="1" * 64,
            )
            with self.subTest(case_id="caller-anchor-and-self-hash"), self.assertRaises(WindowViolation):
                build_window_metric(**arguments, trusted_anchor=caller_anchor)
            with self.subTest(case_id="replayed-other-window"), self.assertRaises(WindowViolation):
                build_window_metric(**{**arguments, "window_id": "other-window"}, trusted_anchor=trust)
            with self.subTest(case_id="replayed-other-game"), self.assertRaises(WindowViolation):
                build_window_metric(**{**arguments, "game": "dlt"}, trusted_anchor=trust)
            with self.subTest(case_id="replayed-other-revision"), self.assertRaises(WindowViolation):
                build_window_metric(**{**arguments, "packages": substitutions["revision-score-id"]}, trusted_anchor=trust)
            current_path = runtime / "scores/current/fixture-forecast-a.json"
            current = load_json(current_path, reject_floats=True)
            atomic_replace_json(current_path, {**current, "result_revision_id": "wrong-revision"})
            with self.subTest(case_id="wrong-current-projection-hash"), self.assertRaises(WindowViolation):
                build_window_metric(**arguments, trusted_anchor=trust)
            atomic_replace_json(current_path, current)
            window_current_path = runtime / "window-metrics/current/fixture-window-a.json"
            window_current = load_json(window_current_path, reject_floats=True)
            window_current_path.unlink()
            with self.assertRaisesRegex(CorrectionViolation, "omit"):
                derive_runtime_correction_graph(runtime, new_revision)
            atomic_replace_json(window_current_path, window_current)
            score_ledger = AppendOnlyLedger(runtime, "scores")
            validation = score_ledger.validate()
            score_ledger.append_event(
                object_id="trust-root-staleness-probe", event_type="score_trust_probe",
                event_at_utc="2026-01-03T00:00:00Z", payload={"probe": True},
                producer_provenance=provenance, expected_head_sha256=validation["head_sha256"],
            )
            with self.subTest(case_id="wrong-or-replayed-score-ledger-head"), self.assertRaises(WindowViolation):
                build_window_metric(**arguments, trusted_anchor=trust)
            alternate = copy.deepcopy(packages[0])
            alternate["score"]["forecast_id"] = "alternate-current-forecast"
            alternate["detail"]["recomputation"]["forecast_id"] = "alternate-current-forecast"
            alternate_id = derive_score_id(
                "alternate-current-forecast", alternate["score"]["result_revision_id"], "phase4-metric-v1")
            alternate["score"]["score_id"] = alternate_id
            alternate["detail"]["score_id"] = alternate_id
            alternate_root = runtime / "scores" / alternate_id
            score_path, detail_path, receipt_path = alternate_root / "score.json", alternate_root / "window-detail.json", alternate_root / "score-receipt.json"
            write_once_json(score_path, alternate["score"])
            write_once_json(detail_path, alternate["detail"])
            write_once_json(receipt_path, {"schema_version": "1.0.0", "artifact_type": "phase4_score_receipt",
                                           "score_id": alternate_id, "score_sha256": sha256_file(score_path),
                                           "window_detail_sha256": sha256_file(detail_path)})
            validation = score_ledger.validate()
            score_ledger.append_event(
                object_id=alternate_id, event_type="score_recorded", event_at_utc="2026-01-03T00:00:01Z",
                payload={"score_id": alternate_id, "score_sha256": sha256_file(score_path),
                         "window_detail_sha256": sha256_file(detail_path), "score_receipt_sha256": sha256_file(receipt_path)},
                producer_provenance=provenance, expected_head_sha256=validation["head_sha256"],
            )
            atomic_replace_json(runtime / "scores/current/alternate-current-forecast.json", {
                "schema_version": "1.0.0", "artifact_type": "phase4_score_current_view",
                "forecast_id": "alternate-current-forecast", "score_id": alternate_id,
                "result_revision_id": alternate["score"]["result_revision_id"],
            })
            with self.subTest(case_id="alternate-valid-current-row-cannot-be-caller-selected"), self.assertRaisesRegex(WindowViolation, "membership/order"):
                resolve_trusted_window_inputs(runtime_root=runtime, window_id="fixture-window-a")
            with self.assertRaises(Exception):
                derive_runtime_correction_graph(runtime, "result-revision-v1:" + "0" * 64)

    def test_exhaustive_current_replacement(self):
        current = apply_current_replacements(
            current_scores=self.graph["current_scores"], current_aggregates=self.graph["current_aggregates"],
            score_replacements=self.graph["score_replacements"], aggregate_replacements=self.graph["aggregate_replacements"],
            expected_old_score_ids=list(self.graph["current_scores"].values()), expected_old_aggregate_ids=list(self.graph["current_aggregates"].values()),
        )
        self.assertEqual(set(current["scores"].values()), {"score-new-a", "score-new-b"})
        self.assertEqual(set(current["aggregates"].values()), {"aggregate-new-a"})

    def test_partial_propagation_rejected(self):
        replacements = dict(self.graph["score_replacements"])
        replacements.pop("score-old-b")
        with self.assertRaisesRegex(CorrectionViolation, "incomplete"):
            apply_current_replacements(current_scores=self.graph["current_scores"], current_aggregates=self.graph["current_aggregates"],
                                       score_replacements=replacements, aggregate_replacements=self.graph["aggregate_replacements"],
                                       expected_old_score_ids=list(self.graph["current_scores"].values()), expected_old_aggregate_ids=list(self.graph["current_aggregates"].values()))

    def test_stale_head_and_duplicate_current_rejected(self):
        current = dict(self.graph["current_scores"])
        current["fork"] = "score-old-a"
        with self.assertRaisesRegex(CorrectionViolation, "duplicate|uniquely current"):
            apply_current_replacements(current_scores=current, current_aggregates=self.graph["current_aggregates"],
                                       score_replacements=self.graph["score_replacements"], aggregate_replacements=self.graph["aggregate_replacements"],
                                       expected_old_score_ids=list(current.values()), expected_old_aggregate_ids=list(self.graph["current_aggregates"].values()))

    def test_same_revision_and_duplicate_outputs_rejected(self):
        with self.assertRaisesRegex(CorrectionViolation, "must change"):
            graph = copy.deepcopy(self.graph)
            graph["old_result_revision_id"] = graph["new_result_revision_id"]
            build_score_correction_impact(canonical_graph=graph)
        with self.assertRaisesRegex(CorrectionViolation, "duplicate|already"):
            graph = copy.deepcopy(self.graph)
            graph["score_replacements"]["score-old-b"] = "score-new-a"
            build_score_correction_impact(canonical_graph=graph)

    def test_impact_contains_no_research_or_alpha_writes(self):
        impact = self._impact()
        forbidden = {"decision_id", "candidate_id", "remediation_decision", "alpha_event", "alpha_refund"}
        self.assertTrue(forbidden.isdisjoint(impact))
        self.assertEqual(impact["alpha_event_ids_before"], self.graph["alpha_event_ids_before"])

    def test_independent_15_case_policy_and_completeness_matrix_rejects(self):
        policy = validate_correction_policy(
            ROOT / "config/phase4/correction-policy-v1.json",
            expected_sha256=CORRECTION_POLICY_SHA256, expected_version=CORRECTION_POLICY_VERSION,
        )
        policy_mutations = {
            "schema_version": "2.0.0",
            "artifact_type": "wrong",
            "correction_policy_version": "wrong",
            "idempotence_key": ["wrong"],
            "score_side": ["wrong"],
            "research_side": ["wrong"],
            "closure_requires_both_sides": False,
            "preserve": ["wrong"],
            "alpha_refund": True,
            "duplicate_observation_credit": True,
            "partial_terminal": "PASS",
        }
        for field, value in policy_mutations.items():
            with self.subTest(case_id=f"policy-{field}"):
                mutated = copy.deepcopy(policy)
                mutated[field] = value
                with self.assertRaises(CorrectionViolation):
                    validate_correction_policy_object(mutated)
        graph = copy.deepcopy(self.graph)
        graph["score_replacements"].pop("score-old-b")
        with self.subTest(case_id="caller-selected-score-omission"), self.assertRaises(CorrectionViolation):
            build_score_correction_impact(canonical_graph=graph)
        graph = copy.deepcopy(self.graph)
        graph["aggregate_replacements"].clear()
        with self.subTest(case_id="caller-selected-aggregate-omission"), self.assertRaises(CorrectionViolation):
            build_score_correction_impact(canonical_graph=graph)
        graph = copy.deepcopy(self.graph)
        graph["new_result_revision_id"] = graph["old_result_revision_id"]
        with self.subTest(case_id="same-revision-control"), self.assertRaises(CorrectionViolation):
            build_score_correction_impact(canonical_graph=graph)
        graph = copy.deepcopy(self.graph)
        graph["score_replacements"]["score-old-b"] = "score-new-a"
        with self.subTest(case_id="duplicate-replacement-control"), self.assertRaises(CorrectionViolation):
            build_score_correction_impact(canonical_graph=graph)


if __name__ == "__main__":
    unittest.main()
