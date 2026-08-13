from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from lottery_system.phase4.ledger import AppendOnlyLedger
from lottery_system.phase4.research.alpha import (
    AlphaViolation,
    alpha_spend,
    make_spend_event,
    reduce_alpha_events,
    total_spend_by_game,
    validate_alpha_wealth,
)
from lottery_system.phase4.research.controller import (
    ResearchControllerViolation,
    derive_qualification_seed,
    execute_decision,
    execute_registered_development_fixture,
    execute_registered_scientific_controller_fixture,
    qualification_design,
    remediate_correction,
    select_development_design,
    scientific_controller_identity,
    validate_remediation,
)
from lottery_system.phase4.research.proposal import build_decision
from lottery_system.phase4.research.registry import (
    ResearchRegistryViolation,
    apply_registered_diff,
    build_candidate,
    canonical_diff,
    validate_candidate,
)
from lottery_system.phase4.research.sequential import SequentialViolation, reduce_e_process, resume_e_process, validate_lr_distribution
from lottery_system.phase4.serialization import canonical_json_bytes, load_json
from lottery_system.phase4.storage import write_once_json


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/phase4/fixtures/research/parameter-positive.json"
DEVELOPMENT_FIXTURE = ROOT / "tests/phase4/fixtures/research/development-small.json"
SCIENTIFIC_WORKER_FIXTURE = ROOT / "tests/phase4/fixtures/research/scientific-worker-small.json"
PREP = ROOT / "artifacts/phase-4-prep/p4-prep-phase4-mvp-20260813-r01-i01"
FEASIBILITY = PREP / "work-items/T10/attempts/T10-I01/feasibility/certificate.json"


def configs():
    return (
        load_json(ROOT / "config/phase4/model-registry.json", reject_floats=True),
        load_json(ROOT / "config/phase4/feature-registry.json", reject_floats=True),
        load_json(ROOT / "config/phase4/decision-contract.json", reject_floats=True),
        load_json(ROOT / "config/phase4/alpha-contract.json", reject_floats=True),
    )


def provenance(path="fixture"):
    return {"producer_actor_id":"p4-implementation-author-i01","task_id":"T07","session_id":"/root/implementation_author","source_commit":"f8a7a6abb46a55f8fa17e5ae3280c5c5432c363b","path":path,"role":"implementation_author"}


class ResearchControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_json(FIXTURE, reject_floats=True)
        self.model, self.feature, self.decision_contract, self.alpha_contract = configs()

    def execute(self, runtime: Path, fixture=None):
        return execute_decision(
            runtime, fixture or self.fixture, clock="2026-01-03T00:00:00Z", provenance=provenance(),
            model_registry=self.model, feature_registry=self.feature,
            decision_contract=self.decision_contract, alpha_contract=self.alpha_contract,
        )

    def scientific_fixture_request(self):
        fixture = load_json(SCIENTIFIC_WORKER_FIXTURE, reject_floats=True)
        identity = scientific_controller_identity()
        return {
            "schema_version":"1.0.0",
            "artifact_type":"phase4_registered_scientific_controller_test_request",
            "fixture_id":fixture["fixture_id"],
            "non_scientific":True,
            "qualification_seed_domain":None,
            "expected_controller_identity_id":identity["controller_identity_id"],
            "design":qualification_design(1536),
            "game":fixture["game"],
            "world":fixture["world"],
            "sequence_ordinal":fixture["sequence_ordinal"],
            "raw_draws":fixture["raw_draws"],
        }

    def test_parameter_positive_creates_one_shadow_without_champion_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            before = canonical_json_bytes([])
            result = self.execute(runtime)
            self.assertEqual(result["terminal"], "shadow_candidate_proposal")
            self.assertEqual(result["experiment_count"], 1)
            self.assertTrue(result["candidate_id"].startswith("candidate-v1:"))
            self.assertFalse((runtime / "champions").exists())
            shadow = load_json(runtime / "research/next-shadow/ssq.json", reject_floats=True)
            self.assertEqual(shadow["config"]["P01.shrinkage"], 5)
            self.assertNotEqual(canonical_json_bytes(shadow["config"]), canonical_json_bytes(self.fixture["parent_config"]))
            self.assertEqual(before, canonical_json_bytes([]))

    def test_idempotent_resume_does_not_duplicate_experiment_or_alpha(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            first = self.execute(runtime)
            second = self.execute(runtime)
            self.assertFalse(first["idempotent_resume"])
            self.assertTrue(second["idempotent_resume"])
            self.assertEqual(AppendOnlyLedger(runtime, "experiments").validate()["event_count"], 1)
            self.assertEqual(AppendOnlyLedger(runtime, "alpha-events").validate()["event_count"], 1)

    def test_parameter_and_feature_diff_are_canonical_and_content_derived(self):
        parameter = build_candidate(
            game="ssq", parent_model_id="M0", parent_config_id="m0", patches=[{"op":"replace","path":"/P01/shrinkage","value":5}],
            hypothesis_family="static_parameter", code_identity="code", data_release_id="data", feature_snapshot_id="feature",
            preregistration_id="pre", qualification_id="qualification", status="shadow_candidate",
            model_registry=self.model, feature_registry=self.feature,
        )
        feature = build_candidate(
            game="dlt", parent_model_id="M0", parent_config_id="m0", patches=[{"op":"replace","path":"/F01/enabled","value":True}],
            hypothesis_family="context_feature", code_identity="code", data_release_id="data", feature_snapshot_id="feature",
            preregistration_id="pre", qualification_id="qualification", status="shadow_candidate",
            model_registry=self.model, feature_registry=self.feature,
        )
        validate_candidate(parameter, model_registry=self.model, feature_registry=self.feature)
        validate_candidate(feature, model_registry=self.model, feature_registry=self.feature)
        self.assertNotEqual(parameter["candidate_id"], feature["candidate_id"])
        self.assertTrue(apply_registered_diff({"F01.enabled":False}, feature["canonical_diff"])["F01.enabled"])

    def test_all_registered_zero_experiment_reasons_create_no_experiment(self):
        for index, reason in enumerate(("no_eligible_hypothesis", "budget_exhausted", "guard_hold", "scheduled_no_change")):
            fixture = copy.deepcopy(self.fixture)
            fixture.update(cycle_action="zero_experiment", zero_experiment_reason=reason, decision_id=f"zero-decision-{index}")
            with tempfile.TemporaryDirectory() as directory:
                result = self.execute(Path(directory), fixture)
                self.assertEqual(result["experiment_count"], 0)
                self.assertIsNone(result["candidate_id"])
                self.assertFalse((Path(directory) / "ledgers/experiments").exists())

    def test_decimal_e_process_first_crossing_and_mean_one(self):
        self.assertEqual(validate_lr_distribution(["0.4", "0.6"], ["0.5", "0.5"]), Decimal(1))
        reduced = reduce_e_process(self.fixture["looks"], alpha_ordinal=1)
        self.assertEqual(reduced["first_crossing_look"], 30)
        self.assertEqual(reduced["terminal"], "shadow_candidate")
        self.assertEqual(reduced["alpha_spent"], "0.003")
        checkpoint = reduce_e_process(self.fixture["looks"][:10], alpha_ordinal=1)
        self.assertEqual(resume_e_process(checkpoint, self.fixture["looks"][10:]), reduced)

    def test_decimal80_threshold_boundary_and_adjacent_rounding(self):
        neutral = [
            {
                "look": look,
                "p0": ["0.5", "0.5"],
                "p1": ["0.5", "0.5"],
                "outcome_index": 0,
                "p1_frozen_at_utc": "2025-01-01T00:00:00Z",
                "outcome_observed_at_utc": "2025-01-02T00:00:00Z",
            }
            for look in range(1, 30)
        ]
        expected_threshold = (
            "333.33333333333333333333333333333333333333333333333333333333333333333333333333333"
        )
        boundary_cases = (
            ("0.3333333333333333333333333332", "0.6666666666666666666666666668", None),
            ("0.3333333333333333333333333333", "0.6666666666666666666666666667", None),
            ("0.3333333333333333333333333334", "0.6666666666666666666666666666", 30),
        )
        for p1_first, p1_second, expected_crossing in boundary_cases:
            last = {
                "look": 30,
                "p0": ["0.001", "0.999"],
                "p1": [p1_first, p1_second],
                "outcome_index": 0,
                "p1_frozen_at_utc": "2025-01-01T00:00:00Z",
                "outcome_observed_at_utc": "2025-01-02T00:00:00Z",
            }
            reduced = reduce_e_process([*neutral, last], alpha_ordinal=1)
            self.assertEqual(reduced["threshold"], expected_threshold)
            self.assertEqual(reduced["first_crossing_look"], expected_crossing)
            self.assertEqual(reduced["looks"][-1]["crossed"], expected_crossing == 30)

    def test_future_p1_and_look_after_stop_reject(self):
        future = copy.deepcopy(self.fixture["looks"])
        future[0]["p1_frozen_at_utc"] = future[0]["outcome_observed_at_utc"]
        with self.assertRaises(SequentialViolation):
            reduce_e_process(future, alpha_ordinal=1)
        after_stop = copy.deepcopy(self.fixture["looks"])
        row = copy.deepcopy(after_stop[-1])
        row["look"] = 31
        row["p1_frozen_at_utc"] = "2025-02-01T00:00:00Z"
        row["outcome_observed_at_utc"] = "2025-02-02T00:00:00Z"
        after_stop.append(row)
        with self.assertRaises(SequentialViolation):
            reduce_e_process(after_stop, alpha_ordinal=1)

    def test_alpha_formula_family_isolation_duplicate_and_negative_reject(self):
        self.assertEqual(alpha_spend(1), Decimal("0.003"))
        event = make_spend_event(game="ssq", hypothesis_family="static_parameter", experiment_id="experiment-a", ordinal=1, event_at_utc="2026-01-01T00:00:00Z")
        wealth = reduce_alpha_events("ssq", "static_parameter", [event])
        self.assertEqual(wealth["current_wealth"], "0.003")
        with self.assertRaises(AlphaViolation):
            reduce_alpha_events("ssq", "static_parameter", [event, event])
        cross = dict(event, hypothesis_family="context_feature")
        with self.assertRaises(AlphaViolation):
            reduce_alpha_events("ssq", "static_parameter", [cross])
        negative = dict(wealth, current_wealth="-0.001")
        with self.assertRaises(AlphaViolation):
            validate_alpha_wealth(negative, [event])

    def test_three_family_total_never_exceeds_point_zero_one_eight(self):
        rows = []
        for family in ("static_parameter", "slow_drift_parameter", "context_feature"):
            event = make_spend_event(game="dlt", hypothesis_family=family, experiment_id=f"experiment-{family}", ordinal=1, event_at_utc="2026-01-01T00:00:00Z")
            rows.append(reduce_alpha_events("dlt", family, [event]))
        self.assertEqual(total_spend_by_game(rows, "dlt"), Decimal("0.009"))

    def test_split_alpha_multi_family_unregistered_diff_and_config_no_output_reject(self):
        split = copy.deepcopy(self.fixture)
        split["alpha_ordinal"] = 2
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(AlphaViolation):
            self.execute(Path(directory), split)
        with self.assertRaises(ResearchRegistryViolation):
            canonical_diff([{"op":"replace","path":"/P99/search","value":1}], "static_parameter", model_registry=self.model, feature_registry=self.feature)
        with self.assertRaises(ResearchRegistryViolation):
            canonical_diff([{"op":"replace","path":"/F01/enabled","value":True}], "static_parameter", model_registry=self.model, feature_registry=self.feature)
        with self.assertRaises(ResearchRegistryViolation):
            apply_registered_diff({"P01.shrinkage":5}, [{"op":"replace","path":"/P01/shrinkage","value":5}])

    def test_refund_reset_and_direct_champion_surface_reject(self):
        event = make_spend_event(game="ssq", hypothesis_family="static_parameter", experiment_id="experiment-a", ordinal=1, event_at_utc="2026-01-01T00:00:00Z")
        for event_type in ("refund", "reset"):
            mutated = dict(event, event_type=event_type)
            with self.assertRaises(AlphaViolation):
                reduce_alpha_events("ssq", "static_parameter", [mutated])
        direct = copy.deepcopy(self.fixture)
        direct["champion_mutation"] = {"model_id":"P4E1"}
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ResearchControllerViolation):
            self.execute(Path(directory), direct)

    def test_one_experiment_contract_rejects_multiple(self):
        with self.assertRaises(ResearchRegistryViolation):
            build_decision(decision_id="decision", game="ssq", target_issue="issue", result_revision_id="revision", trigger="new_verified_result", experiment_ids=["a", "b"], terminal="rejected", zero_experiment_reason=None)

    def test_correction_remediation_archives_requalifies_and_preserves_alpha_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            alpha = AppendOnlyLedger(runtime, "alpha-events")
            alpha.append_event(object_id="alpha-event-a", event_type="alpha_event_recorded", event_at_utc="2026-01-01T00:00:00Z", payload={"artifact_type":"fixture_alpha_seed"}, producer_provenance=provenance(), expected_head_sha256=None)
            impact = {
                "schema_version":"1.0.0","artifact_type":"phase4_score_correction_impact",
                "correction_key":["ssq","issue","old","new"],"old_result_revision_id":"old","new_result_revision_id":"new",
                "corrected_score_ids":["score-new"],"corrected_aggregate_ids":["aggregate-new"],
                "pending_research_object_ids":["candidate-a"],"alpha_event_ids_before":["alpha-event-a"],"score_side_complete":True,
            }
            impact_path = runtime / "corrections/impact.json"
            write_once_json(impact_path, impact)
            before = (runtime / "ledgers/alpha-events/head.json").read_bytes()
            result = remediate_correction(runtime, impact_path, clock="2026-01-03T00:00:00Z", provenance=provenance(), decision_id="remediation-decision-a")
            self.assertEqual(result["terminal"], "remediation_completed")
            self.assertFalse(result["alpha_refund"])
            self.assertEqual(result["candidate_actions"], [{"candidate_id":"candidate-a","archive_status":"archived_pending_requalification","requalification_status":"required"}])
            self.assertEqual(len(result["candidate_action_ids"]), 1)
            self.assertEqual(AppendOnlyLedger(runtime, "candidate-requalifications").validate()["event_count"], 1)
            validate_remediation(result, impact)
            for mutation in (
                dict(result, candidate_actions=[]), dict(result, alpha_refund=True),
                dict(result, alpha_ledger_head_after="tampered"),
            ):
                with self.assertRaises(ResearchControllerViolation):
                    validate_remediation(mutation, impact)
            self.assertEqual(before, (runtime / "ledgers/alpha-events/head.json").read_bytes())
            resumed = remediate_correction(runtime, impact_path, clock="2026-01-03T00:00:00Z", provenance=provenance(), decision_id="remediation-decision-a")
            self.assertEqual(resumed["remediation_id"], result["remediation_id"])

    def test_correction_missing_alpha_history_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            impact = {
                "schema_version":"1.0.0","artifact_type":"phase4_score_correction_impact","correction_key":["ssq","issue","old","new"],
                "old_result_revision_id":"old","new_result_revision_id":"new","corrected_score_ids":[],"corrected_aggregate_ids":[],
                "pending_research_object_ids":["candidate-a"],"alpha_event_ids_before":["missing"],"score_side_complete":True,
            }
            path = runtime / "corrections/impact.json"
            write_once_json(path, impact)
            with self.assertRaises(ResearchControllerViolation):
                remediate_correction(runtime, path, clock="2026-01-03T00:00:00Z", provenance=provenance(), decision_id="remediation-a")

    def test_registered_small_development_fixture_runs_full_menu_without_qualification_seeds(self):
        fixture = load_json(DEVELOPMENT_FIXTURE, reject_floats=True)
        certificate = load_json(FEASIBILITY, reject_floats=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "development-fixture"
            manifest = execute_registered_development_fixture(output, fixture)
            self.assertEqual(manifest["design_count"], 3)
            self.assertEqual(manifest["cell_count"], 24)
            self.assertEqual(manifest["sequence_count"], 24)
            self.assertEqual(manifest["draw_observation_count"], 3600)
            self.assertEqual(manifest["lossless_shard_count"], 24)
            self.assertEqual(manifest["implementation_match_rate"], "1")
            self.assertIsNone(manifest["seed_domain"])
            self.assertTrue(manifest["descriptive_non_selection"])
            self.assertFalse(manifest["empirical_rate_in_selection_predicate"])
            control = load_json(output / "control.json", reject_floats=True)
            self.assertTrue(control["non_scientific_fixture"])
            self.assertIsNone(control["seed_domain"])
            selected, selection = select_development_design(
                manifest=manifest, certificate=certificate,
                designs=[qualification_design(q) for q in (1536, 1792, 2048)],
            )
            self.assertEqual(selected["q"], 1536)
            self.assertTrue(selection["analytic_only_effect_strength_selection"])
            self.assertFalse(selection["empirical_rate_in_predicate"])

    def test_development_fixture_checkpoint_resume_is_byte_identical(self):
        fixture = load_json(DEVELOPMENT_FIXTURE, reject_floats=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "resumed"
            held = execute_registered_development_fixture(output, fixture, stop_after_batches=3)
            self.assertEqual(held["terminal"], "DEVELOPMENT_CHECKPOINTED")
            manifest = execute_registered_development_fixture(output, fixture)
            before = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*") if path.is_file()
            }
            resumed = execute_registered_development_fixture(output, fixture)
            after = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*") if path.is_file()
            }
            self.assertEqual(resumed, manifest)
            self.assertEqual(after, before)

    def test_development_fixture_rejects_tampered_immutable_shard(self):
        fixture = load_json(DEVELOPMENT_FIXTURE, reject_floats=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "tampered"
            execute_registered_development_fixture(output, fixture, stop_after_batches=1)
            shard = next((output / "shards").rglob("*.json.gz"))
            shard.write_bytes(shard.read_bytes() + b"tamper")
            with self.assertRaises(FileExistsError):
                execute_registered_development_fixture(output, fixture)

    def test_development_fixture_cannot_open_qualification_seed_domain(self):
        fixture = load_json(DEVELOPMENT_FIXTURE, reject_floats=True)
        for mutation in (
            dict(fixture, qualification_seed_domain="development"),
            dict(fixture, non_scientific=False),
            dict(fixture, sequences_per_cell=21),
        ):
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(ResearchControllerViolation):
                execute_registered_development_fixture(Path(directory) / "output", mutation)
        with self.assertRaises(ResearchControllerViolation):
            derive_qualification_seed(qualification_design(1536)["design_id"], "fixture-development", "ssq", "uniform", 1)

    def test_selection_rejects_incomplete_menu_and_implementation_mismatch(self):
        fixture = load_json(DEVELOPMENT_FIXTURE, reject_floats=True)
        certificate = load_json(FEASIBILITY, reject_floats=True)
        with tempfile.TemporaryDirectory() as directory:
            manifest = execute_registered_development_fixture(Path(directory) / "output", fixture)
            with self.assertRaises(ResearchControllerViolation):
                select_development_design(
                    manifest=dict(manifest, implementation_match_rate="0.999"), certificate=certificate,
                    designs=[qualification_design(q) for q in (1536, 1792, 2048)],
                )
            with self.assertRaises(ResearchControllerViolation):
                select_development_design(
                    manifest=manifest, certificate=certificate,
                    designs=[qualification_design(q) for q in (1536, 2048)],
                )

    def test_selection_is_unchanged_by_descriptive_event_counts(self):
        fixture = load_json(DEVELOPMENT_FIXTURE, reject_floats=True)
        certificate = load_json(FEASIBILITY, reject_floats=True)
        with tempfile.TemporaryDirectory() as directory:
            manifest = execute_registered_development_fixture(Path(directory) / "output", fixture)
            designs = [qualification_design(q) for q in (1536, 1792, 2048)]
            selected, _ = select_development_design(manifest=manifest, certificate=certificate, designs=designs)
            mutated = copy.deepcopy(manifest)
            mutated["event_counts"] = {key: 999999 for key in mutated["event_counts"]}
            selected_mutated, _ = select_development_design(manifest=mutated, certificate=certificate, designs=designs)
            self.assertEqual(selected_mutated["design_id"], selected["design_id"])

    def test_scientific_controller_identity_changes_design_and_binds_command_and_code(self):
        identity = scientific_controller_identity()
        self.assertEqual(identity["argv"], ["python3", "-m", "lottery_system.phase4.research.worker"])
        self.assertEqual(identity["controller_source_sha256"], __import__("hashlib").sha256(
            (ROOT / identity["controller_source_path"]).read_bytes()
        ).hexdigest())
        self.assertEqual(identity["worker_source_sha256"], __import__("hashlib").sha256(
            (ROOT / identity["worker_source_path"]).read_bytes()
        ).hexdigest())
        prior = load_json(
            PREP / "qualification-design/development/selected-design.json",
            reject_floats=True,
        )
        current = qualification_design(1536)
        self.assertNotEqual(current["design_id"], prior["design_id"])
        self.assertEqual(current["controller_identity"], identity)

    def test_non_scientific_fixture_runs_real_black_box_worker_deterministically(self):
        request = self.scientific_fixture_request()
        encoded = canonical_json_bytes(request)
        environment = dict(os.environ, PYTHONPATH="src")
        argv = [sys.executable, "-m", "lottery_system.phase4.research.worker"]
        first = subprocess.run(argv, cwd=ROOT, env=environment, input=encoded, capture_output=True, check=False)
        second = subprocess.run(argv, cwd=ROOT, env=environment, input=encoded, capture_output=True, check=False)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stderr, b"")
        self.assertEqual(first.stdout, second.stdout)
        response = json.loads(first.stdout)
        self.assertEqual(response["status"], "PASS")
        self.assertTrue(response["non_scientific"])
        self.assertIsNone(response["qualification_seed_domain"])
        terminal = response["sequence_terminal"]
        self.assertEqual(terminal["draw_observation_count"], 150)
        self.assertEqual(terminal["input_mode"], "raw_draws")
        self.assertIsNone(terminal["seed_domain"])
        self.assertIsNone(terminal["seed_uint256"])
        self.assertEqual(response["guard_code"], "ALL_REGISTERED_GUARDS_PASS")
        self.assertEqual(response["champion_mutation_count"], 0)

    def test_scientific_worker_fixture_contract_mutations_fail_closed(self):
        request = self.scientific_fixture_request()
        mutations = []
        short = copy.deepcopy(request); short["raw_draws"] = short["raw_draws"][:-1]; mutations.append(short)
        domain = copy.deepcopy(request); domain["qualification_seed_domain"] = "power-confirmation"; mutations.append(domain)
        identity = copy.deepcopy(request); identity["expected_controller_identity_id"] = "scientific-controller-v1:" + "0" * 64; mutations.append(identity)
        design = copy.deepcopy(request); design["design"]["controller_identity"]["worker_source_sha256"] = "0" * 64; mutations.append(design)
        boolean_draw = copy.deepcopy(request); boolean_draw["raw_draws"][0] = True; mutations.append(boolean_draw)
        for mutation in mutations:
            with self.assertRaises(ResearchControllerViolation):
                execute_registered_scientific_controller_fixture(mutation)
        environment = dict(os.environ, PYTHONPATH="src")
        duplicate = canonical_json_bytes(request)[:-1] + b',"fixture_id":"duplicate"}'
        rejected = subprocess.run(
            [sys.executable, "-m", "lottery_system.phase4.research.worker"], cwd=ROOT,
            env=environment, input=duplicate, capture_output=True, check=False,
        )
        self.assertEqual(rejected.returncode, 5)
        self.assertEqual(json.loads(rejected.stdout)["guard_code"], "SCIENTIFIC_CONTROLLER_CONTRACT_MISMATCH")

    def test_scientific_worker_has_no_independent_script_or_network_dependency(self):
        worker = (ROOT / "src/lottery_system/phase4/research/worker.py").read_text(encoding="utf-8")
        controller = (ROOT / "src/lottery_system/phase4/research/controller.py").read_text(encoding="utf-8")
        for forbidden in ("scripts.phase4_independent", "scripts/phase4_independent", "urllib", "requests", "socket"):
            self.assertNotIn(forbidden, worker)
            self.assertNotIn(forbidden, controller)


if __name__ == "__main__":
    unittest.main()
