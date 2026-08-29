from __future__ import annotations

import copy
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase4_independent"))
import replay_real_model_release as verifier  # noqa: E402


class LocalVerifierNumericContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = verifier.local_contract()

    def test_observed_macos_python_31211_four_ulp_fixture_passes(self) -> None:
        fixtures = (
            (-0.3853463719541539, -0.38534637195415367),
            (0.02098171210825526, 0.020981712108255272),
        )
        for linux_value, macos_value in fixtures:
            result = verifier.numeric_comparison(linux_value, macos_value, contract=self.contract)
            self.assertTrue(result["passed"])
            self.assertEqual(result["ulp_distance"], 4)

    def test_all_32_feature_snapshot_cross_runtime_vectors_pass_derived_profile(self) -> None:
        fixture_path = ROOT / self.contract["numeric_profile_evidence"]["derived_feature_context_v2"]["prior_fixture_path"]
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["difference_count"], 32)
        self.assertEqual(len(fixture["differences"]), 32)
        self.assertEqual(sum(row["game"] == "ssq" for row in fixture["differences"]), 6)
        self.assertEqual(sum(row["game"] == "dlt" for row in fixture["differences"]), 26)
        for row in fixture["differences"]:
            result = verifier.numeric_comparison(
                row["release_value"], row["macos_value"], contract=self.contract,
                profile_id="derived_feature_context_v2",
            )
            self.assertTrue(result["passed"], row["path"])
            self.assertEqual(result["ulp_distance"], row["ulp_distance"])
            verifier.compare_value(row["release_value"], row["macos_value"], row["path"], contract=self.contract)
        worst = max(fixture["differences"], key=lambda row: row["ulp_distance"])
        self.assertEqual(worst["ulp_distance"], 151)
        self.assertEqual(worst["release_value"], "0.0099312201839453045")
        self.assertEqual(worst["macos_value"], "0.0099312201839450425")

    def test_exact_eight_ulp_boundary_passes_and_nine_ulp_fails(self) -> None:
        base = 1.0
        eight, nine = base, base
        for _ in range(8):
            eight = math.nextafter(eight, math.inf)
        for _ in range(9):
            nine = math.nextafter(nine, math.inf)
        self.assertTrue(verifier.numeric_comparison(base, eight, contract=self.contract)["passed"])
        self.assertFalse(verifier.numeric_comparison(base, nine, contract=self.contract)["passed"])
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(base, nine, "model.zones.0.log_normalizer", contract=self.contract)

    def test_observed_top1000_macos_fixtures_pass_derived_v3_and_are_narrowly_routed(self) -> None:
        profile = "top1000_derived_probability_display_v3"
        evidence = self.contract["numeric_profile_evidence"][profile]
        fixture = json.loads((ROOT / evidence["prior_fixture_path"]).read_text(encoding="utf-8"))
        released_rows = verifier.load_jsonl(next((ROOT / f"artifacts/phase-4/{fixture['release_id']}/forecasts/{fixture['game']}").glob("*/top1000.jsonl")))
        released = released_rows[fixture["rank"] - 1]
        self.assertEqual(released["rank"], fixture["rank"])
        self.assertEqual(released["canonical_ticket_key"], fixture["canonical_ticket_key"])
        self.assertEqual(released["joint_probability"], fixture["release_value"])

        result = verifier.numeric_comparison(
            fixture["release_value"], fixture["macos_value"], contract=self.contract,
            profile_id=profile,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["absolute_error"], fixture["absolute_error"])
        self.assertEqual(result["relative_error"], fixture["relative_error"])
        self.assertEqual(result["ulp_distance"], 17)
        verifier.compare_value(fixture["release_value"], fixture["macos_value"], fixture["path"], contract=self.contract)

        failure = json.loads((ROOT / evidence["controller_failure_fixture_path"]).read_text(encoding="utf-8"))
        r11_rows = verifier.load_jsonl(next((ROOT / f"artifacts/phase-4/{failure['release_id']}/forecasts/{failure['game']}").glob("*/top1000.jsonl")))
        released = r11_rows[failure["rank"] - 1]
        self.assertEqual(released["canonical_ticket_key"], failure["canonical_ticket_key"])
        self.assertEqual(released["joint_probability"], failure["release_value"])
        replayed = float(failure["release_value"])
        for _ in range(failure["ulp_distance"]):
            replayed = math.nextafter(replayed, -math.inf)
        result = verifier.numeric_comparison(failure["release_value"], replayed, contract=self.contract, profile_id=profile)
        self.assertTrue(result["passed"])
        self.assertEqual(result["absolute_error"], failure["absolute_error"])
        self.assertEqual(result["relative_error"], failure["relative_error"])
        self.assertEqual(result["ulp_distance"], failure["ulp_distance"])

        for scope in ("top1000", "historical_top1000", "shadow_top1000"):
            self.assertEqual(
                verifier._path_profile(f"{scope}.622.joint_probability", self.contract),
                profile,
            )
            self.assertEqual(verifier._path_profile(f"{scope}.622.log_joint_score", self.contract), "tight_recomputed_v1")
            self.assertIsNone(verifier._path_profile(f"{scope}.622.rank", self.contract))

    def test_top1000_probability_32_ulp_and_just_outside_bounds_fail(self) -> None:
        profile = "top1000_derived_probability_display_v3"
        base = float("5.75e-08")
        thirty_two, thirty_three = base, base
        for _ in range(32):
            thirty_two = math.nextafter(thirty_two, -math.inf)
        for _ in range(33):
            thirty_three = math.nextafter(thirty_three, -math.inf)
        self.assertTrue(verifier.numeric_comparison(base, thirty_two, contract=self.contract, profile_id=profile)["passed"])
        ulp = verifier.numeric_comparison(base, thirty_three, contract=self.contract, profile_id=profile)
        self.assertEqual(ulp["ulp_distance"], 33)
        self.assertFalse(ulp["passed"])

        absolute_base = 1.0
        absolute_outside = math.nextafter(absolute_base, math.inf)
        absolute = verifier.numeric_comparison(absolute_base, absolute_outside, contract=self.contract, profile_id=profile)
        self.assertGreater(absolute["absolute_error"], 4.235164736271502e-22)
        self.assertLessEqual(absolute["relative_error"], 3.774758283725532e-15)
        self.assertEqual(absolute["ulp_distance"], 1)
        self.assertFalse(absolute["passed"])

        relative_base = math.ulp(0.0)
        relative_outside = math.nextafter(relative_base, math.inf)
        relative = verifier.numeric_comparison(relative_base, relative_outside, contract=self.contract, profile_id=profile)
        self.assertLessEqual(relative["absolute_error"], 4.235164736271502e-22)
        self.assertGreater(relative["relative_error"], 3.774758283725532e-15)
        self.assertEqual(relative["ulp_distance"], 1)
        self.assertFalse(relative["passed"])

        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ValueError, "FAIL_NON_FINITE"):
                verifier.numeric_comparison(base, value, contract=self.contract, profile_id=profile)

        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(base, thirty_three, "top1000.622.joint_probability", contract=self.contract)

    def test_full_matrix_is_classified_into_narrow_source_profiles(self) -> None:
        profile = self.contract["numeric_profiles"]["top1000_derived_probability_display_v3"]
        evidence = self.contract["numeric_profile_evidence"]["top1000_derived_probability_display_v3"]
        self.assertEqual(profile["max_relative"], 17 / 2**52)
        self.assertEqual(profile["max_absolute"], 2**-71)
        self.assertEqual(profile["max_ulps"], 32)
        self.assertEqual(evidence["relative_derivation"], "17 / 2^52")

        frozen_r11 = json.loads((ROOT / "artifacts/phase-4/P4-P4E2-20260815-r11/contracts/local-verifier-contract.json").read_text())
        self.assertEqual(self.contract["numeric_profiles"]["tight_recomputed_v1"], frozen_r11["numeric_profiles"]["tight_recomputed_v1"])
        old_paths = next(row["paths"] for row in frozen_r11["path_numeric_profiles"]
                         if row["profile_id"] == "top1000_derived_probability_display_v1")
        new_paths = next(row["paths"] for row in self.contract["path_numeric_profiles"]
                         if row["profile_id"] == "top1000_derived_probability_display_v3")
        self.assertEqual(new_paths, old_paths)
        self.assertEqual(self.contract["exact_invariants"], frozen_r11["exact_invariants"])
        self.assertEqual(verifier._path_profile("model.zones.1.context.number_features.F04.7", self.contract), "derived_number_feature_context_v1")
        self.assertEqual(verifier._path_profile("model.zones.1.coefficients.F04", self.contract), "derived_coefficient_v1")
        self.assertEqual(verifier._path_profile("model.zones.1.top_zone_rows.33.0", self.contract), "propagated_zone_score_v1")
        self.assertEqual(verifier._path_profile("model.zones.1.maximum_score", self.contract), "tight_recomputed_v1")
        fixture = json.loads((ROOT / evidence["full_replay_fixture_path"]).read_text())
        self.assertEqual(fixture["legacy_numeric_bound_failures"], 163)
        self.assertEqual(fixture["exact_identity_mismatches"], 0)

    def test_derived_feature_boundary_immediately_above_each_maximum_fails(self) -> None:
        profile = "derived_feature_context_v2"
        path = "feature_snapshot.0.feature_values.F04"

        absolute_base, absolute_outside = 1.0, 1.0
        while abs(absolute_outside - absolute_base) <= 3.3306690738754696e-16:
            absolute_outside = math.nextafter(absolute_outside, math.inf)
        absolute = verifier.numeric_comparison(absolute_base, absolute_outside, contract=self.contract, profile_id=profile)
        self.assertGreater(absolute["absolute_error"], 3.3306690738754696e-16)
        self.assertFalse(absolute["passed"])

        relative_base, relative_outside, relative_steps = 0.0078125, 0.0078125, 0
        while abs(relative_outside - relative_base) / relative_outside <= 3e-14:
            relative_outside = math.nextafter(relative_outside, math.inf)
            relative_steps += 1
        relative = verifier.numeric_comparison(relative_base, relative_outside, contract=self.contract, profile_id=profile)
        self.assertEqual(relative_steps, 136)
        self.assertLessEqual(relative["absolute_error"], 3.3306690738754696e-16)
        self.assertLessEqual(relative["ulp_distance"], 151)
        self.assertGreater(relative["relative_error"], 3e-14)
        self.assertFalse(relative["passed"])

        ulp_base, ulp_outside = 0.0099312201839453045, 0.0099312201839453045
        for _ in range(152):
            ulp_outside = math.nextafter(ulp_outside, -math.inf)
        ulp = verifier.numeric_comparison(ulp_base, ulp_outside, contract=self.contract, profile_id=profile)
        self.assertEqual(ulp["ulp_distance"], 152)
        self.assertLessEqual(ulp["absolute_error"], 3.3306690738754696e-16)
        self.assertLessEqual(ulp["relative_error"], 3e-14)
        self.assertFalse(ulp["passed"])
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(ulp_base, ulp_outside, path, contract=self.contract)

    def test_c5a9_remaining_paths_are_isolated_and_observed_maxima_are_covered(self) -> None:
        fixture_path = ROOT / "tests/phase4/fixtures/local-verifier-r11-macos-path-pattern-replay.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["tested_commit"], "c5a9b3a307ae45e04a72c318f0ddb6e1ae1e069f")
        self.assertEqual(fixture["terminal"]["new_bound_failures"], 17)
        self.assertEqual(fixture["terminal"]["exact_identity_mismatches"], 0)
        self.assertTrue(fixture["terminal"]["release_unchanged"])

        expected = {
            "model.zones.*.context.number_features.F04.*": "derived_number_feature_context_v1",
            "model.zones.*.top_zone_rows.*.0": "propagated_zone_score_v1",
        }
        for row in fixture["offending_patterns"]:
            profile_id = expected[row["pattern"]]
            profile = self.contract["numeric_profiles"][profile_id]
            self.assertLessEqual(row["max_absolute"]["value"], profile["max_absolute"])
            self.assertLessEqual(row["max_relative"]["value"], profile["max_relative"])
            self.assertLessEqual(row["max_ulps"]["value"], profile["max_ulps"])
        self.assertEqual(fixture["corrected_profiles"]["derived_coefficient_v1"]["bound_failures"], 0)
        self.assertEqual(fixture["corrected_profiles"]["top1000_derived_probability_display_v3"]["bound_failures"], 0)

    def test_number_feature_and_zone_score_conjunctive_boundaries(self) -> None:
        nested = "derived_number_feature_context_v1"
        nested_base = 1.0
        nested_at_absolute = nested_base
        for _ in range(2):
            nested_at_absolute = math.nextafter(nested_at_absolute, math.inf)
        self.assertEqual(abs(nested_at_absolute - nested_base), 4 * 2**-53)
        self.assertTrue(verifier.numeric_comparison(
            nested_base, nested_at_absolute, contract=self.contract, profile_id=nested,
        )["passed"])
        nested_outside = math.nextafter(nested_at_absolute, math.inf)
        self.assertFalse(verifier.numeric_comparison(
            nested_base, nested_outside, contract=self.contract, profile_id=nested,
        )["passed"])
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(
                nested_base, nested_outside,
                "model.zones.0.context.number_features.F04.30", contract=self.contract,
            )

        zone = "propagated_zone_score_v1"
        zone_base = 0.04
        zone_at_ulp = zone_base
        for _ in range(64):
            zone_at_ulp = math.nextafter(zone_at_ulp, math.inf)
        zone_result = verifier.numeric_comparison(
            zone_base, zone_at_ulp, contract=self.contract, profile_id=zone,
        )
        self.assertEqual(zone_result["ulp_distance"], 64)
        self.assertTrue(zone_result["passed"])
        zone_outside = math.nextafter(zone_at_ulp, math.inf)
        self.assertFalse(verifier.numeric_comparison(
            zone_base, zone_outside, contract=self.contract, profile_id=zone,
        )["passed"])
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(
                zone_base, zone_outside, "model.zones.1.top_zone_rows.33.0", contract=self.contract,
            )

    def test_coefficient_profile_16_ulp_boundary_and_17_ulp_negative(self) -> None:
        profile = "derived_coefficient_v1"
        path = "model.zones.1.coefficients.F04"
        base = 0.02098171210825526
        at_boundary, outside = base, base
        for _ in range(16):
            at_boundary = math.nextafter(at_boundary, math.inf)
        for _ in range(17):
            outside = math.nextafter(outside, math.inf)
        self.assertTrue(verifier.numeric_comparison(base, at_boundary, contract=self.contract, profile_id=profile)["passed"])
        self.assertFalse(verifier.numeric_comparison(base, outside, contract=self.contract, profile_id=profile)["passed"])
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(base, outside, path, contract=self.contract)

    def test_full_matrix_observer_suppresses_only_numeric_bounds(self) -> None:
        events = []
        base = 1.0
        outside = base
        for _ in range(9):
            outside = math.nextafter(outside, math.inf)
        with verifier.collect_numeric_comparisons(lambda *row: events.append(row), suppress_bounds=True):
            verifier.compare_value(base, outside, "model.zones.0.log_normalizer", contract=self.contract)
            with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_MISMATCH"):
                verifier.compare_value("exact-left", "exact-right", "model.model_release_id", contract=self.contract)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0][3]["passed"])
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(base, outside, "model.zones.0.log_normalizer", contract=self.contract)

    def test_non_finite_and_unlisted_paths_fail_closed(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ValueError, "FAIL_NON_FINITE"):
                verifier.numeric_comparison(value, 1.0, contract=self.contract)
        with self.assertRaisesRegex(ValueError, "HOLD_SEMANTIC_NUMERIC_TYPE"):
            verifier.numeric_comparison(True, 1.0, contract=self.contract)
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_MISMATCH"):
            verifier.compare_value(1.0, math.nextafter(1.0, 2.0), "model.training_count", contract=self.contract)

    def test_contract_release_copy_uses_canonical_json_not_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pretty = Path(raw) / "pretty.json"
            compact = Path(raw) / "compact.json"
            pretty.write_text(json.dumps(self.contract, indent=2) + "\n", encoding="utf-8")
            compact.write_bytes(verifier.canon(self.contract))
            self.assertNotEqual(pretty.read_bytes(), compact.read_bytes())
            self.assertTrue(verifier.same_json_document(pretty, compact))
            changed = copy.deepcopy(self.contract)
            changed["numeric_profiles"]["tight_recomputed_v1"]["max_ulps"] += 1
            compact.write_bytes(verifier.canon(changed))
            self.assertFalse(verifier.same_json_document(pretty, compact))


class LocalVerifierFeatureSnapshotContractTests(unittest.TestCase):
    snapshot = next((ROOT / "artifacts/phase-4/P4-P4E2-20260815-r08/features/dlt").glob("*/feature-snapshot.jsonl"))

    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = verifier.load_jsonl(cls.snapshot)

    def assert_snapshot_mutation_fails(self, rows: list[object], reason: str = "HOLD_REPLAY") -> None:
        with self.assertRaisesRegex(ValueError, reason):
            verifier.compare_feature_snapshot(rows, self.rows)

    def test_structure_identity_order_and_cardinality_are_exact(self) -> None:
        verifier.compare_feature_snapshot(copy.deepcopy(self.rows), self.rows)

        non_numeric = copy.deepcopy(self.rows)
        non_numeric[0]["game"] = "ssq"
        self.assert_snapshot_mutation_fails(non_numeric, "HOLD_REPLAY_MISMATCH")

        feature_id = copy.deepcopy(self.rows)
        feature_id[0]["feature_values"]["F99"] = feature_id[0]["feature_values"].pop("F04")
        self.assert_snapshot_mutation_fails(feature_id, "HOLD_REPLAY_MISMATCH")

        cutoff = copy.deepcopy(self.rows)
        cutoff[0]["cutoff_position"] += 1
        self.assert_snapshot_mutation_fails(cutoff, "HOLD_REPLAY_MISMATCH")

        fact_hash = copy.deepcopy(self.rows)
        fact_hash[0]["input_prefix_sha256"] = "0" * 64
        self.assert_snapshot_mutation_fails(fact_hash, "HOLD_REPLAY_MISMATCH")

        reordered = copy.deepcopy(self.rows)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        self.assert_snapshot_mutation_fails(reordered, "HOLD_REPLAY_MISMATCH")
        self.assert_snapshot_mutation_fails(copy.deepcopy(self.rows[:-1]), "length")
        self.assert_snapshot_mutation_fails(copy.deepcopy(self.rows + [self.rows[-1]]), "length")

    def test_numeric_type_nonfinite_and_just_outside_profile_fail(self) -> None:
        non_numeric = copy.deepcopy(self.rows)
        non_numeric[0]["feature_values"]["F04"] = "not-a-number"
        self.assert_snapshot_mutation_fails(non_numeric, "HOLD_SEMANTIC_NUMERIC_TYPE")

        for value in (math.nan, math.inf, -math.inf):
            non_finite = copy.deepcopy(self.rows)
            non_finite[0]["feature_values"]["F04"] = value
            self.assert_snapshot_mutation_fails(non_finite, "FAIL_NON_FINITE")

        outside = copy.deepcopy(self.rows)
        value = 0.0099312201839453045
        for _ in range(152):
            value = math.nextafter(value, -math.inf)
        outside[638]["feature_values"]["F04"] = format(value, ".17g")
        self.assert_snapshot_mutation_fails(outside, "HOLD_REPLAY_NUMERIC_BOUND")


class LocalVerifierIntegrityTests(unittest.TestCase):
    release = ROOT / "artifacts/phase-4/P4-P4E2-20260815-r04"

    @staticmethod
    def stable_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        migrated = copy.deepcopy(rows)
        identities = [verifier.oracle.score_identity(float(row["log_joint_score"])) for row in migrated]
        positions = {identity: [index for index, value in enumerate(identities, 1) if value == identity]
                     for identity in set(identities)}
        layer, previous = 0, None
        for row, identity in zip(migrated, identities):
            score = float(row["log_joint_score"])
            if identity != previous:
                layer += 1
                previous = identity
            peers = positions[identity]
            row.update(
                score_order_key=verifier.oracle.score_order_key(score),
                score_identity=identity,
                probability_representation=verifier.oracle.PROBABILITY_REPRESENTATION_ID,
                probability_layer=layer,
                tie_group_id=verifier.oracle.tie_group_id_for_score(score),
                tie_group_size=len(peers),
                tie_rank_lower=min(peers),
                tie_rank_upper=max(peers),
                tie_midrank=format((min(peers) + max(peers)) / 2, ".1f"),
                tie_key=verifier.oracle.tie_key_for_score(score),
                ranking_algorithm_id=verifier.oracle.RANKING_ALGORITHM_ID,
            )
        return migrated

    def test_missing_and_tampered_final_closure_fail(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / self.release.name
            shutil.copytree(self.release, copied)
            verifier._validate_final_closure(copied)
            closure = copied / "acceptance/final-closure.json"
            original = closure.read_bytes()
            closure.unlink()
            with self.assertRaises((FileNotFoundError, ValueError)):
                verifier._validate_final_closure(copied)
            closure.write_bytes(original)
            value = json.loads(closure.read_text())
            value["manifest_sha256"] = "0" * 64
            closure.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
            with self.assertRaisesRegex(ValueError, "HOLD_FINAL_CLOSURE_MISMATCH"):
                verifier._validate_final_closure(copied)

    def test_top1000_order_tie_identity_and_lineage_are_exact(self) -> None:
        top_path = next((self.release / "forecasts/ssq").glob("*/top1000.jsonl"))
        rows = self.stable_rows([json.loads(line) for line in top_path.read_text().splitlines()])
        verifier._compare_top(rows, copy.deepcopy(rows), "top1000")
        reordered = copy.deepcopy(rows)
        reordered[0], reordered[1] = reordered[1], reordered[0]
        with self.assertRaisesRegex(ValueError, "HOLD_TIE_IDENTITY|HOLD_TOP1000_ORDER|HOLD_REPLAY_MISMATCH"):
            verifier._compare_top(reordered, rows, "top1000")
        changed_tie = copy.deepcopy(rows)
        changed_tie[0]["score_identity"] = changed_tie[1]["score_identity"]
        with self.assertRaisesRegex(ValueError, "HOLD_TIE_IDENTITY"):
            verifier._compare_top(changed_tie, rows, "top1000")
        changed_lineage = copy.deepcopy(rows)
        changed_lineage[0]["lineage"]["model_release_id"] += "-mutated"
        with self.assertRaisesRegex(ValueError, "lineage"):
            verifier._compare_top(changed_lineage, rows, "top1000")

    def test_top1000_exact_invariants_fail_before_tolerated_probability_is_compared(self) -> None:
        top_path = next((self.release / "forecasts/ssq").glob("*/top1000.jsonl"))
        expected = self.stable_rows(verifier.load_jsonl(top_path))
        observed = copy.deepcopy(expected)
        value = float(observed[622]["joint_probability"])
        for _ in range(17):
            value = math.nextafter(value, -math.inf)
        observed[622]["joint_probability"] = format(value, ".18g")
        verifier._compare_top(observed, expected, "top1000")

        mutations = {}
        mutations["ticket"] = copy.deepcopy(observed)
        mutations["ticket"][622]["front_numbers"][0] += 1
        mutations["order"] = copy.deepcopy(observed)
        mutations["order"][0], mutations["order"][1] = mutations["order"][1], mutations["order"][0]
        mutations["rank"] = copy.deepcopy(observed)
        mutations["rank"][622]["rank"] += 1
        mutations["tie_key"] = copy.deepcopy(observed)
        mutations["tie_key"][622]["tie_key"] = "tie-score-order-key-v1:P4S10HE1:0"
        mutations["score_order_key"] = copy.deepcopy(observed)
        mutations["score_order_key"][622]["score_order_key"] = "P4S10HE1:0"
        mutations["score_identity"] = copy.deepcopy(observed)
        mutations["score_identity"][622]["score_identity"] = observed[621]["score_identity"]
        mutations["lineage"] = copy.deepcopy(observed)
        mutations["lineage"][622]["lineage"]["model_release_id"] += "-mutated"

        for name, changed in mutations.items():
            with self.subTest(name=name), mock.patch.object(
                verifier, "numeric_comparison", side_effect=AssertionError("numeric comparison ran before exact rejection")
            ):
                with self.assertRaises((ValueError, KeyError)):
                    verifier._compare_top(changed, expected, "top1000")

    def test_local_entry_point_contains_no_vps_only_path(self) -> None:
        for relative in ("scripts/phase4/local-accept-release", "scripts/phase4/local_accept_release.py"):
            text = (ROOT / relative).read_text()
            self.assertNotIn("/home/", text)
            self.assertNotIn("/usr/bin/python", text)
            self.assertNotIn("acceptance-venv", text)
        finalizer = (ROOT / "scripts/phase4/finalize_real_model_release.py").read_text()
        checklist_template = finalizer[finalizer.index("checklist = f\"\"\""):finalizer.index("checklist_path =", finalizer.index("checklist = f\"\"\""))]
        self.assertNotIn("/home/", checklist_template)
        self.assertNotIn("/usr/bin/", checklist_template)
        self.assertNotIn("acceptance-venv", checklist_template)


if __name__ == "__main__":
    unittest.main()
