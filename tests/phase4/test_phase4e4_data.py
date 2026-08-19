from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from lottery_system.phase4e4.data import load_jsonl, parse_500_ssq, parse_gdlottery_dlt


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "artifacts/phase-4e4/data-20260819"
ORIGINAL = ROOT / "artifacts/phase-1/baseline-v1/draws.jsonl"
EXPECTED = {
    "ssq": {
        "canonical_count": 3482,
        "prebaseline_count": 3282,
        "selection_count": 3222,
        "report_count": 60,
        "canonical_sha256": "0a753969d6a23d4fa7b55cd7f115990603908c575203c1626a713f305c83a7b5",
        "selection_sha256": "222e35c97f9b2cbf45f0cdcf1662f6c8e9198c2c65c074ed6fcbbc916891678b",
        "sealed_report_sha256": "7aa8178e98d00f53a066d790deeafef8dc966a27c2b4df39c0c6e53634ed3572",
        "source_authority": "provenance_tracked_nonofficial_fallback",
        "promotion_authority": False,
    },
    "dlt": {
        "canonical_count": 1283,
        "prebaseline_count": 1073,
        "selection_count": 1013,
        "report_count": 60,
        "canonical_sha256": "1d77e3a8fcd8f407144981c88122c40120f592815b62748ae69d244774277ef8",
        "selection_sha256": "053726ac2b5777a987687eda77e182c6f9ed21090e87f817e5641bafd1b8ca5a",
        "sealed_report_sha256": "75b8555d0814c92371e9688f7aad79c72a7abf3eac6a3287b919a57b60c9d44f",
        "source_authority": "official",
        "promotion_authority": True,
    },
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payloads(rows: list[object]) -> list[dict[str, object]]:
    return [row.payload() for row in rows]  # type: ignore[attr-defined]


class Phase4E4DataTests(unittest.TestCase):
    def test_parses_provenance_html(self) -> None:
        body = b'<table><tr class="t_tr1"><td>25036</td><td>05</td><td>11</td><td>13</td><td>16</td><td>19</td><td>32</td><td>07</td><td>&nbsp;</td><td>x</td><td>3</td><td>x</td><td>82</td><td>x</td><td>x</td><td>2025-04-03</td></tr></table>'
        rows = parse_500_ssq(body)
        self.assertEqual((rows[0].issue, rows[0].front, rows[0].back), ("2025036", (5, 11, 13, 16, 19, 32), (7,)))

    def test_parses_official_dlt_and_rejects_bad_set(self) -> None:
        good = {"085_24001": {"id": 1, "gameId": "085", "drawId": "24001", "createTime": "2024-01-01", "kjhm": "01+02+03+04+05 06+07"}}
        rows = parse_gdlottery_dlt(json.dumps(good).encode())
        self.assertEqual((rows[0].issue, rows[0].front, rows[0].back), ("2024001", (1, 2, 3, 4, 5), (6, 7)))
        good["085_24001"]["kjhm"] = "01+01+03+04+05 06+07"
        with self.assertRaises(ValueError):
            parse_gdlottery_dlt(json.dumps(good).encode())

    def test_inventory_and_artifact_hashes_are_frozen(self) -> None:
        inventory = json.loads((DATA / "provenance/inventory.json").read_text(encoding="utf-8"))
        self.assertEqual(inventory["authority_commit"], "c6621d14c13cda36b010f6d13fd3c636cdfb0a2e")
        self.assertTrue(inventory["authority_commit_on_remote_before_capture"])
        self.assertEqual(inventory["original_200_sha256"], "f2e34a88fbd43a378d7fb6255d39deee1354216b918934f355a06ef986be60c1")
        self.assertEqual(inventory["synthetic_rows"], 0)
        self.assertEqual(inventory["silent_imputations"], 0)
        for game, expected in EXPECTED.items():
            actual = inventory["games"][game]
            for key, value in expected.items():
                self.assertEqual(actual[key], value, f"{game}/{key}")
            self.assertEqual(actual["duplicate_issue_count"], 0)
            self.assertEqual(actual["overlap_conflicts"], [])
            self.assertEqual(actual["overlap_with_original_count"], 200)
            self.assertGreaterEqual(actual["selection_count"], 480)
            self.assertEqual(digest(DATA / f"canonical/{game}.jsonl"), expected["canonical_sha256"])
            self.assertEqual(digest(DATA / f"selection-prefix/{game}.jsonl"), expected["selection_sha256"])
            self.assertEqual(digest(DATA / f"sealed-report/{game}.jsonl"), expected["sealed_report_sha256"])

    def test_raw_sources_replay_canonical_bytes_deterministically(self) -> None:
        replayed = {
            "ssq": parse_500_ssq((DATA / "raw/ssq_provenance.html").read_bytes()),
            "dlt": parse_gdlottery_dlt((DATA / "raw/dlt_official.json").read_bytes()),
        }
        for game, rows in replayed.items():
            self.assertEqual(payloads(rows), payloads(load_jsonl(DATA / f"canonical/{game}.jsonl", game)))

    def test_original_200_are_exactly_included_without_conflict(self) -> None:
        originals: dict[str, dict[str, dict[str, object]]] = {"ssq": {}, "dlt": {}}
        for line in ORIGINAL.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            originals[row["game"]][row["issue_id"]] = row
        self.assertEqual({game: len(rows) for game, rows in originals.items()}, {"ssq": 200, "dlt": 200})
        for game in ("ssq", "dlt"):
            canonical = {row.issue: row for row in load_jsonl(DATA / f"canonical/{game}.jsonl", game)}
            self.assertEqual(set(originals[game]), set(originals[game]).intersection(canonical))
            for issue, old in originals[game].items():
                current = canonical[issue]
                self.assertEqual(
                    (current.draw_date, list(current.front), list(current.back)),
                    (old["draw_date_local"], old["front_numbers"], old["back_numbers"]),
                    f"core-fact conflict at {game}/{issue}",
                )

    def test_selection_and_sealed_report_are_physical_chronological_partitions(self) -> None:
        boundaries = {"ssq": "2025-04-06", "dlt": "2025-03-31"}
        for game in ("ssq", "dlt"):
            canonical = load_jsonl(DATA / f"canonical/{game}.jsonl", game)
            selection = load_jsonl(DATA / f"selection-prefix/{game}.jsonl", game)
            report = load_jsonl(DATA / f"sealed-report/{game}.jsonl", game)
            eligible = [row for row in canonical if row.draw_date < boundaries[game]]
            self.assertEqual(selection, eligible[:-60])
            self.assertEqual(report, eligible[-60:])
            self.assertEqual(len({row.issue for row in canonical}), len(canonical))
            self.assertTrue(set(row.issue for row in selection).isdisjoint(row.issue for row in report))
            self.assertLess((selection[-1].draw_date, int(selection[-1].issue)), (report[0].draw_date, int(report[0].issue)))
            self.assertTrue(all(
                (left.draw_date, int(left.issue)) < (right.draw_date, int(right.issue))
                for left, right in zip(canonical, canonical[1:])
            ))

    def test_source_status_permanently_blocks_ssq_promotion(self) -> None:
        inventory = json.loads((DATA / "provenance/inventory.json").read_text(encoding="utf-8"))
        requests = {row["source_id"]: row for row in inventory["requests"]}
        self.assertEqual(requests["cwl_attempt"]["http_status"], 403)
        self.assertEqual(requests["ssq_provenance"]["http_status"], 200)
        self.assertEqual(requests["dlt_official"]["http_status"], 200)
        self.assertTrue(inventory["ssq_official_access_blocked"])
        self.assertFalse(inventory["games"]["ssq"]["promotion_authority"])
        self.assertTrue(inventory["games"]["dlt"]["promotion_authority"])
        self.assertIn("cannot authorize promotion", inventory["ssq_fallback_disclosure"])


if __name__ == "__main__":
    unittest.main()
