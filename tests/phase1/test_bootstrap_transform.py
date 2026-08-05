from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from lottery_data.models import ContractViolation
from lottery_data.serialization import canonical_json_bytes, sha256_file
from lottery_data.steps import EXPECTED_REPARSED_COUNTS, transform_bootstrap_snapshot
from lottery_data.steps.quality_gate import build_bootstrap_quality_report


REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "artifacts" / "phase-0-multisource" / "snapshots" / "20260802T025000Z"
CATALOG = REPO / "config" / "phase1" / "source-catalog.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_json(path: Path, value: dict) -> None:
    path.write_bytes(canonical_json_bytes(value))


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_bytes(b"".join(canonical_json_bytes(value) for value in values))


def build_quality_report(**overrides: object) -> dict:
    arguments = {
        "run_id": "quality-unit",
        "draws": [{"game": game} for game in ("ssq", "dlt") for _ in range(200)],
        "observations": [{}] * 800,
        "reconciliation": [{"decision": "verified"}] * 400,
        "audit": {
            "request_count": 30,
            "parsed_observations": 1042,
            "fallback_count": 2,
            "normal_pair_count": 398,
            "reparsed_counts": {"source": 1042},
            "expected_reparsed_counts": {"source": 1042},
        },
        "input_hashes": {"canonical": "a" * 64},
        "output_hashes": {
            "draws": "a" * 64,
            "release_observations": "b" * 64,
            "reconciliation": "c" * 64,
            "run_observations": "d" * 64,
        },
        "generated_at_utc": "2026-08-02T00:00:00Z",
    }
    arguments.update(overrides)
    return build_bootstrap_quality_report(**arguments)


class BootstrapQualityGateTests(unittest.TestCase):
    def test_counts_name_parsed_and_selected_observations_unambiguously(self) -> None:
        report = build_quality_report()
        deterministic = report["deterministic"]
        self.assertEqual(deterministic["counts"]["parsed_observations"], 1042)
        self.assertEqual(deterministic["counts"]["selected_observations"], 800)
        self.assertNotIn("observations", deterministic["counts"])
        self.assertEqual(
            list(deterministic["output_hashes"]),
            ["draws", "reconciliation", "release_observations", "run_observations"],
        )

    def test_output_hash_contract_fails_closed(self) -> None:
        valid = {
            "draws": "a" * 64,
            "release_observations": "b" * 64,
            "reconciliation": "c" * 64,
            "run_observations": "d" * 64,
        }
        invalid_cases = {
            "missing": {key: value for key, value in valid.items() if key != "run_observations"},
            "malformed": {**valid, "draws": "not-a-sha256"},
            "ambiguous_legacy": {**valid, "observations": "e" * 64},
        }
        for case, output_hashes in invalid_cases.items():
            with self.subTest(case=case), self.assertRaises(ContractViolation):
                build_quality_report(output_hashes=output_hashes)


class BootstrapTransformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = transform_bootstrap_snapshot(snapshot_root=SNAPSHOT, source_catalog_path=CATALOG)

    def copy_snapshot(self, temporary: str) -> Path:
        target = Path(temporary) / SNAPSHOT.name
        shutil.copytree(SNAPSHOT, target)
        return target

    def test_real_raw_reparses_to_frozen_400_800(self) -> None:
        result = self.baseline
        self.assertEqual(result["audit"]["request_count"], 30)
        self.assertEqual(result["audit"]["reparsed_counts"], dict(sorted(EXPECTED_REPARSED_COUNTS.items())))
        self.assertEqual(len(result["observations_all"]), 1042)
        self.assertEqual(len(result["observations_selected"]), 800)
        self.assertEqual(len(result["draws"]), 400)
        self.assertEqual(
            {game: sum(draw["game"] == game for draw in result["draws"]) for game in ("ssq", "dlt")},
            {"ssq": 200, "dlt": 200},
        )
        self.assertEqual(result["quality_report"]["decision"], "PASS")
        self.assertEqual(result["quality_report"]["deterministic"]["blocking_reason_codes"], [])

    def test_raw_issue_id_and_publishers_are_preserved(self) -> None:
        observations = self.baseline["observations_all"]
        by_key = {(item["source_id"], item["game"], item["issue_id"]): item for item in observations}
        self.assertEqual(by_key[("ydniu", "dlt", "2026026")]["raw_issue_id"], "2026026")
        self.assertEqual(by_key[("eastmoney", "dlt", "2026083")]["raw_issue_id"], "26083")
        self.assertEqual(by_key[("gdlottery", "dlt", "2026026")]["raw_issue_id"], "26026")
        self.assertEqual(by_key[("ydniu", "ssq", "2026085")]["publisher_id"], "ydniu-publisher")
        self.assertEqual(by_key[("eastmoney", "ssq", "2026085")]["publisher_id"], "eastmoney-publisher")
        self.assertEqual(by_key[("gdlottery", "dlt", "2026026")]["publisher_id"], "gdlottery-publisher")

    def test_source_pairs_and_four_layer_evidence_close(self) -> None:
        result = self.baseline
        fallback = [item for item in result["reconciliation"] if item["fallback_rule_id"] is not None]
        self.assertEqual([(item["game"], item["issue_id"]) for item in fallback], [("dlt", "2026026"), ("dlt", "2026027")])
        self.assertTrue(all(item["missing_source_ids"] == ["eastmoney"] for item in fallback))
        self.assertEqual(result["audit"]["normal_pair_count"], 398)
        self.assertEqual(result["audit"]["fallback_count"], 2)

        capture = {item["raw_ref"]: item for item in load_jsonl(SNAPSHOT / "capture-manifest.jsonl")}
        selected = {item["observation_id"]: item for item in result["observations_selected"]}
        for draw in result["draws"]:
            self.assertEqual(len({link["publisher_id"] for link in draw["evidence_links"]}), 2)
            for link in draw["evidence_links"]:
                observation = selected[link["observation_id"]]
                self.assertEqual(link["raw_sha256"], capture[link["raw_ref"]]["raw_sha256"])
                self.assertEqual(link["raw_sha256"], sha256_file(SNAPSHOT / link["raw_ref"]))
                self.assertEqual(draw["core_fact_sha256"], observation["core_fact_sha256"])

    def test_phase0_parsed_is_neither_required_nor_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self.copy_snapshot(temporary)
            shutil.rmtree(copied / "parsed")
            without_parsed = transform_bootstrap_snapshot(snapshot_root=copied, source_catalog_path=CATALOG)
            self.assertEqual(
                without_parsed["quality_report"]["deterministic"]["output_hashes"],
                self.baseline["quality_report"]["deterministic"]["output_hashes"],
            )
            (copied / "parsed").mkdir()
            (copied / "parsed" / "ydniu-ssq.jsonl").write_text("not json and deliberately poisoned", encoding="utf-8")
            poisoned = transform_bootstrap_snapshot(snapshot_root=copied, source_catalog_path=CATALOG)
            self.assertEqual(
                poisoned["quality_report"]["deterministic"]["output_hashes"],
                self.baseline["quality_report"]["deterministic"]["output_hashes"],
            )

    def test_missing_and_tampered_raw_are_rejected(self) -> None:
        for mode in ("missing", "tampered"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                copied = self.copy_snapshot(temporary)
                raw = copied / "raw" / "ydniu" / "ssq" / "page-001.html"
                if mode == "missing":
                    raw.unlink()
                else:
                    raw.write_bytes(raw.read_bytes() + b"tampered")
                with self.assertRaises(ContractViolation):
                    transform_bootstrap_snapshot(snapshot_root=copied, source_catalog_path=CATALOG)

    def test_validly_rehashed_source_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self.copy_snapshot(temporary)
            raw = copied / "raw" / "eastmoney" / "ssq" / "page-001.html"
            text = raw.read_text(encoding="utf-8-sig")
            issue_position = text.index("id=2026085")
            ball_position = text.index(">06</span>", issue_position)
            text = text[:ball_position] + ">07</span>" + text[ball_position + len(">06</span>"):]
            raw.write_text(text, encoding="utf-8")
            new_hash = sha256_file(raw)
            new_length = raw.stat().st_size

            manifest_path = copied / "capture-manifest.jsonl"
            manifest = load_jsonl(manifest_path)
            target_request_id = None
            for item in manifest:
                if item["raw_ref"] == "raw/eastmoney/ssq/page-001.html":
                    item["raw_sha256"] = new_hash
                    item["content_length"] = new_length
                    target_request_id = item["request_id"]
            self.assertIsNotNone(target_request_id)
            write_jsonl(manifest_path, manifest)

            events_path = copied / "request-events.jsonl"
            events = load_jsonl(events_path)
            for item in events:
                if item.get("event") == "request_succeeded" and item.get("request_id") == target_request_id:
                    item["raw_sha256"] = new_hash
                    item["content_length"] = new_length
            write_jsonl(events_path, events)

            hashes_path = copied / "artifact-hashes.json"
            hashes = load_json(hashes_path)
            hashes["capture-manifest.jsonl"] = sha256_file(manifest_path)
            hashes["request-events.jsonl"] = sha256_file(events_path)
            write_json(hashes_path, hashes)
            with self.assertRaisesRegex(ContractViolation, "conflict"):
                transform_bootstrap_snapshot(snapshot_root=copied, source_catalog_path=CATALOG)


if __name__ == "__main__":
    unittest.main()
