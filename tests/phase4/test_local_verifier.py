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
        fixture_path = ROOT / self.contract["numeric_profile_evidence"]["derived_feature_snapshot_v1"]["fixture_path"]
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["difference_count"], 32)
        self.assertEqual(len(fixture["differences"]), 32)
        self.assertEqual(sum(row["game"] == "ssq" for row in fixture["differences"]), 6)
        self.assertEqual(sum(row["game"] == "dlt" for row in fixture["differences"]), 26)
        for row in fixture["differences"]:
            result = verifier.numeric_comparison(
                row["release_value"], row["macos_value"], contract=self.contract,
                profile_id="derived_feature_snapshot_v1",
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
            verifier.compare_value(base, nine, "model.zones.0.coefficients.F04", contract=self.contract)

    def test_observed_top1000_macos_17_ulp_fixture_passes_and_is_narrowly_routed(self) -> None:
        evidence = self.contract["numeric_profile_evidence"]["top1000_derived_probability_display_v1"]
        fixture = json.loads((ROOT / evidence["fixture_path"]).read_text(encoding="utf-8"))
        released_rows = verifier.load_jsonl(next((ROOT / f"artifacts/phase-4/{fixture['release_id']}/forecasts/{fixture['game']}").glob("*/top1000.jsonl")))
        released = released_rows[fixture["rank"] - 1]
        self.assertEqual(released["rank"], fixture["rank"])
        self.assertEqual(released["canonical_ticket_key"], fixture["canonical_ticket_key"])
        self.assertEqual(released["joint_probability"], fixture["release_value"])

        result = verifier.numeric_comparison(
            fixture["release_value"], fixture["macos_value"], contract=self.contract,
            profile_id=fixture["profile_id"],
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["absolute_error"], fixture["absolute_error"])
        self.assertEqual(result["relative_error"], fixture["relative_error"])
        self.assertEqual(result["ulp_distance"], 17)
        verifier.compare_value(fixture["release_value"], fixture["macos_value"], fixture["path"], contract=self.contract)

        for scope in ("top1000", "historical_top1000", "shadow_top1000"):
            self.assertEqual(
                verifier._path_profile(f"{scope}.622.joint_probability", self.contract),
                "top1000_derived_probability_display_v1",
            )
            self.assertEqual(verifier._path_profile(f"{scope}.622.log_joint_score", self.contract), "tight_recomputed_v1")
            self.assertIsNone(verifier._path_profile(f"{scope}.622.rank", self.contract))

    def test_top1000_probability_18_ulp_and_just_outside_bounds_fail(self) -> None:
        profile = "top1000_derived_probability_display_v1"
        base = float("6.358672953029994052e-08")
        eighteen = base
        for _ in range(18):
            eighteen = math.nextafter(eighteen, -math.inf)
        ulp = verifier.numeric_comparison(base, eighteen, contract=self.contract, profile_id=profile)
        self.assertEqual(ulp["ulp_distance"], 18)
        self.assertFalse(ulp["passed"])

        absolute_base = 1.0
        absolute_outside = math.nextafter(absolute_base, math.inf)
        absolute = verifier.numeric_comparison(absolute_base, absolute_outside, contract=self.contract, profile_id=profile)
        self.assertGreater(absolute["absolute_error"], 2.2499312661442353e-22)
        self.assertLessEqual(absolute["relative_error"], 3.5383660753807325e-15)
        self.assertEqual(absolute["ulp_distance"], 1)
        self.assertFalse(absolute["passed"])

        relative_base = 2.0 ** -24
        relative_outside = relative_base
        for _ in range(16):
            relative_outside = math.nextafter(relative_outside, math.inf)
        relative = verifier.numeric_comparison(relative_base, relative_outside, contract=self.contract, profile_id=profile)
        self.assertLessEqual(relative["absolute_error"], 2.2499312661442353e-22)
        self.assertGreater(relative["relative_error"], 3.5383660753807325e-15)
        self.assertEqual(relative["ulp_distance"], 16)
        self.assertFalse(relative["passed"])

        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaisesRegex(ValueError, "FAIL_NON_FINITE"):
                verifier.numeric_comparison(base, value, contract=self.contract, profile_id=profile)

        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(base, eighteen, "top1000.622.joint_probability", contract=self.contract)

    def test_derived_feature_boundary_immediately_above_each_maximum_fails(self) -> None:
        profile = "derived_feature_snapshot_v1"
        path = "feature_snapshot.0.feature_values.F04"

        absolute_base, absolute_outside = 1.0, 1.0
        while abs(absolute_outside - absolute_base) <= 3e-16:
            absolute_outside = math.nextafter(absolute_outside, math.inf)
        absolute = verifier.numeric_comparison(absolute_base, absolute_outside, contract=self.contract, profile_id=profile)
        self.assertGreater(absolute["absolute_error"], 3e-16)
        self.assertFalse(absolute["passed"])

        relative_base, relative_outside, relative_steps = 0.0078125, 0.0078125, 0
        while abs(relative_outside - relative_base) / relative_outside <= 3e-14:
            relative_outside = math.nextafter(relative_outside, math.inf)
            relative_steps += 1
        relative = verifier.numeric_comparison(relative_base, relative_outside, contract=self.contract, profile_id=profile)
        self.assertEqual(relative_steps, 136)
        self.assertLessEqual(relative["absolute_error"], 3e-16)
        self.assertLessEqual(relative["ulp_distance"], 151)
        self.assertGreater(relative["relative_error"], 3e-14)
        self.assertFalse(relative["passed"])

        ulp_base, ulp_outside = 0.0099312201839453045, 0.0099312201839453045
        for _ in range(152):
            ulp_outside = math.nextafter(ulp_outside, -math.inf)
        ulp = verifier.numeric_comparison(ulp_base, ulp_outside, contract=self.contract, profile_id=profile)
        self.assertEqual(ulp["ulp_distance"], 152)
        self.assertLessEqual(ulp["absolute_error"], 3e-16)
        self.assertLessEqual(ulp["relative_error"], 3e-14)
        self.assertFalse(ulp["passed"])
        with self.assertRaisesRegex(ValueError, "HOLD_REPLAY_NUMERIC_BOUND"):
            verifier.compare_value(ulp_base, ulp_outside, path, contract=self.contract)

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
