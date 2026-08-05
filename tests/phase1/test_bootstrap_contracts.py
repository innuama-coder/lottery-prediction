from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lottery_data.models as contract_models
from lottery_data.models import ContractViolation, distribution_file_by_suffix, schema_path, validate_object, validate_schema
from lottery_data.serialization import (
    bundle_sha256,
    canonical_json_bytes,
    canonical_jsonl_bytes,
    core_fact_sha256,
    make_event_id,
    make_observation_id,
    make_revision_id,
    sha256_bytes,
    sha256_file,
)


REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "phase1" / "fixtures" / "spec"
SCHEMAS = (
    "source-observation.schema.json",
    "draw-record.schema.json",
    "dataset-release.schema.json",
    "run-manifest.schema.json",
    "run-event.schema.json",
    "run-result.schema.json",
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class BootstrapContractTests(unittest.TestCase):
    def test_six_schemas_load_and_validate_their_w1_examples(self) -> None:
        expectations = load_json(FIXTURES / "fixture-expectations.json")["fixtures"]
        valid_by_schema = {
            metadata["schema"]: relative
            for relative, metadata in expectations.items()
            if metadata["valid"]
        }
        self.assertEqual(set(valid_by_schema), set(SCHEMAS))
        for name in SCHEMAS:
            with self.subTest(schema=name):
                self.assertTrue(schema_path(name).is_file())
                value = load_json(FIXTURES / valid_by_schema[name])
                self.assertIs(validate_object(name, value), value)
        with tempfile.TemporaryDirectory() as directory:
            install_root = Path(directory)
            installed_schema = install_root / "share" / "autoresearch-lotte" / "schemas" / "phase1" / SCHEMAS[0]
            installed_schema.parent.mkdir(parents=True)
            installed_schema.write_bytes(schema_path(SCHEMAS[0]).read_bytes())

            class FakePackagePath:
                def __init__(self, record_path: str, located: Path) -> None:
                    self.record_path = record_path
                    self.located = located

                def __str__(self) -> str:
                    return self.record_path

                def locate(self) -> Path:
                    return self.located

            class FakeDistribution:
                def __init__(self, files: list[FakePackagePath]) -> None:
                    self.files = files

            suffix = f"share/autoresearch-lotte/schemas/phase1/{SCHEMAS[0]}"
            installed_entry = FakePackagePath(f"../{suffix}", installed_schema)

            missing_source = install_root / "no-source-tree"
            contract_models._validator.cache_clear()
            try:
                with patch.object(contract_models, "_SCHEMA_ROOT", missing_source), patch.object(
                    contract_models, "distribution", return_value=FakeDistribution([installed_entry])
                ):
                    self.assertEqual(schema_path(SCHEMAS[0]), installed_schema)
                    self.assertEqual(distribution_file_by_suffix(suffix), installed_schema)
                    value = load_json(FIXTURES / valid_by_schema[SCHEMAS[0]])
                    self.assertIs(validate_schema(SCHEMAS[0], value), value)
                with patch.object(contract_models, "distribution", return_value=FakeDistribution([])):
                    with self.assertRaisesRegex(ContractViolation, "found 0"):
                        distribution_file_by_suffix(suffix)
                duplicate = FakePackagePath(f"duplicate/{suffix}", installed_schema)
                with patch.object(contract_models, "distribution", return_value=FakeDistribution([installed_entry, duplicate])):
                    with self.assertRaisesRegex(ContractViolation, "found 2"):
                        distribution_file_by_suffix(suffix)
            finally:
                contract_models._validator.cache_clear()

    def test_three_real_phase0_core_hashes_remain_byte_compatible(self) -> None:
        vectors = load_json(FIXTURES / "hash-vectors.json")["vectors"]
        self.assertEqual(len(vectors), 3)
        for vector in vectors:
            with self.subTest(case=vector["case_id"]):
                self.assertEqual(core_fact_sha256(vector["phase0_fact"]), vector["expected_core_fact_sha256"])

    def test_three_deterministic_ids_recompute(self) -> None:
        vectors = load_json(FIXTURES / "hash-vectors.json")["deterministic_id_vectors"]
        self.assertEqual({vector["kind"] for vector in vectors}, {"observation", "revision", "event"})
        for vector in vectors:
            identity = vector["identity"]
            if vector["kind"] == "observation":
                actual = make_observation_id(
                    identity["source_id"], identity["game"], identity["issue_id"],
                    identity["raw_sha256"], identity["parser_version"],
                )
            elif vector["kind"] == "revision":
                actual = make_revision_id(
                    identity["game"], identity["issue_id"], identity["core_fact_sha256"],
                    identity["supersedes_revision_id"],
                )
            else:
                actual = make_event_id(
                    identity["run_id"], identity["sequence"], identity["event_type"],
                    identity["request_id"], identity["attempt"],
                )
            with self.subTest(case=vector["case_id"]):
                self.assertEqual(actual, vector["expected_id"])

    def test_canonical_json_is_utf8_sorted_compact_and_lf_terminated(self) -> None:
        encoded = canonical_json_bytes({"z": 1, "中文": "值", "a": 2})
        self.assertEqual(encoded, '{"a":2,"z":1,"中文":"值"}\n'.encode("utf-8"))
        self.assertFalse(encoded.startswith(b"\xef\xbb\xbf"))
        with self.assertRaises(ValueError):
            canonical_json_bytes({"bad": float("nan")})

    def test_jsonl_uses_three_phase1_sort_profiles_and_is_stable(self) -> None:
        cases = (
            (("game", "issue_id", "publisher_id", "source_id", "observation_id"), [
                {"game":"ssq","issue_id":"2","publisher_id":"p","source_id":"s","observation_id":"b","marker":2},
                {"game":"ssq","issue_id":"1","publisher_id":"p","source_id":"s","observation_id":"a","marker":1},
            ]),
            (("game", "issue_id"), [
                {"game":"ssq","issue_id":"2","marker":2}, {"game":"dlt","issue_id":"1","marker":1},
            ]),
            (("game", "issue_id", "revision_id"), [
                {"game":"ssq","issue_id":"1","revision_id":"r","marker":1},
                {"game":"dlt","issue_id":"2","revision_id":"r","marker":2},
            ]),
        )
        for sort_keys, rows in cases:
            with self.subTest(sort_keys=sort_keys):
                forward = canonical_jsonl_bytes(rows, sort_keys=sort_keys)
                reverse = canonical_jsonl_bytes(reversed(rows), sort_keys=sort_keys)
                self.assertEqual(forward, reverse)
        tied = [{"game":"ssq","issue_id":"1","marker":1}, {"game":"ssq","issue_id":"1","marker":2}]
        stable = canonical_jsonl_bytes(tied, sort_keys=("game", "issue_id")).decode("utf-8").splitlines()
        self.assertEqual([json.loads(line)["marker"] for line in stable], [1, 2])
        with self.assertRaises(ValueError):
            canonical_jsonl_bytes([{"game":"ssq"}], sort_keys=("game", "issue_id"))

    def test_hash_helpers_and_bundle_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.txt"
            second = root / "b.txt"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            self.assertEqual(sha256_file(first), sha256_bytes(b"a"))
            forward = bundle_sha256(["a.txt", "b.txt"], root=root)
            reverse = bundle_sha256(["b.txt", "a.txt"], root=root)
            self.assertEqual(forward, reverse)

    def test_dataset_release_single_object_boundary_is_honest(self) -> None:
        release = load_json(FIXTURES / "valid" / "dataset-release.json")
        self.assertIs(validate_object("dataset-release.schema.json", release), release)
        invalid = copy.deepcopy(release)
        invalid["record_count_by_game"]["ssq"] = -1
        with self.assertRaises(ContractViolation):
            validate_object("dataset-release.schema.json", invalid)

    def test_observation_semantics_fail_closed(self) -> None:
        value = load_json(FIXTURES / "valid" / "source-observation.json")
        self.assertIs(validate_object("source-observation.schema.json", value), value)
        for mutation in ("numbers", "core_hash", "observation_id"):
            invalid = copy.deepcopy(value)
            if mutation == "numbers":
                invalid["front_numbers"][0], invalid["front_numbers"][1] = invalid["front_numbers"][1], invalid["front_numbers"][0]
            elif mutation == "core_hash":
                invalid["core_fact_sha256"] = "0" * 64
            else:
                invalid["observation_id"] = "obs-v1:" + "0" * 64
            with self.subTest(mutation=mutation), self.assertRaises(ContractViolation):
                validate_object("source-observation.schema.json", invalid)

    def test_draw_revision_and_publisher_semantics_fail_closed(self) -> None:
        value = load_json(FIXTURES / "valid" / "draw-record.json")
        value["revision_id"] = make_revision_id(
            value["game"], value["issue_id"], value["core_fact_sha256"], value["supersedes_revision_id"]
        )
        self.assertIs(validate_object("draw-record.schema.json", value), value)
        invalid = copy.deepcopy(value)
        invalid["evidence_links"][1]["publisher_id"] = invalid["evidence_links"][0]["publisher_id"]
        with self.assertRaises(ContractViolation):
            validate_object("draw-record.schema.json", invalid)

    def test_manifest_event_and_result_semantics_fail_closed(self) -> None:
        manifest = load_json(FIXTURES / "valid" / "run-manifest.json")
        manifest["request_plan"][1]["request_id"] = manifest["request_plan"][0]["request_id"]
        with self.assertRaises(ContractViolation):
            validate_object("run-manifest.schema.json", manifest)
        manifest = load_json(FIXTURES / "valid" / "run-manifest.json")
        manifest["request_plan"][1]["sequence"] = 3
        with self.assertRaises(ContractViolation):
            validate_object("run-manifest.schema.json", manifest)

        event = load_json(FIXTURES / "valid" / "run-event-request-started.json")
        event["event_id"] = make_event_id(
            event["run_id"], event["sequence"], event["event_type"], event["request_id"], event["attempt"]
        )
        self.assertIs(validate_object("run-event.schema.json", event), event)
        event["attempt"] = 2
        with self.assertRaises(ContractViolation):
            validate_object("run-event.schema.json", event)

        result = load_json(FIXTURES / "valid" / "run-result-published.json")
        result["request_stats"]["succeeded"] -= 1
        with self.assertRaises(ContractViolation):
            validate_object("run-result.schema.json", result)

    def test_unknown_schema_fails_closed(self) -> None:
        with self.assertRaises(ContractViolation):
            validate_schema("future.schema.json", {})


if __name__ == "__main__":
    unittest.main()
