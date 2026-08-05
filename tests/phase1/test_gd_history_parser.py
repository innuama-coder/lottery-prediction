from __future__ import annotations

import json
import random
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from lottery_data.parsers.gdlottery_history import parse
from lottery_data.parsers.registry import get_versioned_parser
from lottery_data.serialization import core_fact_sha256, sha256_file


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "phase1" / "fixtures" / "real" / "gd-game-number-history-20260803.json"
METADATA = FIXTURE.with_name("gd-game-number-history-20260803.metadata.json")
FIXTURE_SHA256 = "dae5c9e0f33cfc09e8b245e9f093bfeaf115ed9383c673dd78ef08f34c98b5ac"
FIXTURE_SIZE = 1_828_447


def _record(draw_id: str, index: int = 1) -> dict[str, object]:
    drawn = date(2026, 1, 1) + timedelta(days=index)
    return {
        "id": index + 1,
        "gameId": "085",
        "drawId": draw_id,
        "createTime": drawn.isoformat(),
        "cashTime": (drawn + timedelta(days=60)).isoformat(),
        "kjhm": "01+02+03+04+05 06+07",
    }


def _payload(count: int = 20) -> bytes:
    values = {}
    for offset in range(count):
        draw_id = f"{26_001 + offset:05d}"
        values[f"085_{draw_id}"] = _record(draw_id, offset)
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class GuangdongHistoryParserTests(unittest.TestCase):
    def test_real_fixture_identity_selection_and_targets(self) -> None:
        metadata = json.loads(METADATA.read_text(encoding="utf-8"))
        self.assertEqual(FIXTURE.stat().st_size, FIXTURE_SIZE)
        self.assertEqual(sha256_file(FIXTURE), FIXTURE_SHA256)
        self.assertEqual(metadata["size_bytes"], FIXTURE_SIZE)
        self.assertEqual(metadata["sha256"], FIXTURE_SHA256)
        self.assertEqual(metadata["parser_id"], "phase1-gdlottery-history-parser")
        self.assertEqual(metadata["parser_version"], "1.0.0")
        self.assertIs(get_versioned_parser(metadata["parser_id"], metadata["parser_version"]), parse)

        facts = parse(FIXTURE.read_bytes(), "dlt")
        self.assertEqual(len(facts), 20)
        self.assertEqual([item["raw_issue_id"] for item in facts], [f"{issue:05d}" for issue in range(26_067, 26_087)])
        self.assertEqual([item["issue_id"] for item in facts], [f"20{issue:05d}" for issue in range(26_067, 26_087)])
        self.assertEqual(facts, sorted(facts, key=lambda item: int(item["issue_id"])))

        expected = {
            "2026084": ("2026-07-27", [13, 25, 30, 32, 33], [4, 5], "4325114f50f20d55517e5a5a7ef145f030b57bd03b7dacab7d86f064a69973b7"),
            "2026085": ("2026-07-29", [3, 4, 14, 28, 31], [5, 7], "292ab7f1314b884d87c100808ff7d6f339a3656957cbed8b6ee08976a1c78dcc"),
            "2026086": ("2026-08-01", [10, 11, 18, 22, 35], [6, 12], "82df2d2f5e8427a7d5df6373e2f569df412c8f0668c3e35f52717d283d1773e0"),
        }
        by_issue = {item["issue_id"]: item for item in facts}
        for issue_id, (drawn, front, back, digest) in expected.items():
            fact = by_issue[issue_id]
            self.assertEqual((fact["draw_date_local"], fact["front_numbers"], fact["back_numbers"]), (drawn, front, back))
            self.assertEqual(core_fact_sha256({"game": "dlt", **fact}), digest)

    def test_input_order_does_not_change_latest20_or_output_order(self) -> None:
        records = json.loads(_payload(25))
        items = list(records.items())
        random.Random(20260803).shuffle(items)
        facts = parse(json.dumps(dict(items), separators=(",", ":")).encode(), "dlt")
        self.assertEqual([item["raw_issue_id"] for item in facts], [f"{issue:05d}" for issue in range(26_006, 26_026)])

        boundary = parse(_payload(21), "dlt")
        self.assertEqual(
            [item["raw_issue_id"] for item in boundary],
            [f"{issue:05d}" for issue in range(26_002, 26_022)],
        )

    def test_duplicate_keys_and_nonfinite_constants_are_rejected(self) -> None:
        record = json.dumps(_record("26001"), separators=(",", ":"))
        duplicate = ("{" + f'"085_26001":{record},"085_26001":{record}' + "}").encode()
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            parse(duplicate, "dlt")
        with self.assertRaisesRegex(ValueError, "non-finite JSON constant"):
            parse(b'{"085_26001":NaN}', "dlt")
        with self.assertRaisesRegex(ValueError, "non-finite JSON constant"):
            parse(b'{"085_26001":Infinity}', "dlt")

        payload = _payload().decode("utf-8")
        nested_duplicate = (
            payload[:-1] + ',"001_26001":{"nested":{"value":1,"value":2}}}'
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            parse(nested_duplicate, "dlt")

    def test_wrong_root_game_and_too_few_dlt_records_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "supports only dlt"):
            parse(_payload(), "ssq")
        with self.assertRaisesRegex(ValueError, "root must be an object"):
            parse(b"[]", "dlt")
        with self.assertRaisesRegex(ValueError, "fewer than 20"):
            parse(_payload(19), "dlt")

    def test_record_identity_fields_and_types_are_strict(self) -> None:
        mutations = {
            "extra": lambda row: row.__setitem__("extra", 1),
            "missing": lambda row: row.pop("createTime"),
            "bool-id": lambda row: row.__setitem__("id", True),
            "float-id": lambda row: row.__setitem__("id", 1.5),
            "numeric-issue": lambda row: row.__setitem__("drawId", 26001),
            "bad-game": lambda row: row.__setitem__("gameId", "086"),
            "bad-date": lambda row: row.__setitem__("createTime", "2026-02-30"),
            "bad-cash-date": lambda row: row.__setitem__("cashTime", "2026-2-01"),
            "bool-draw-number": lambda row: row.__setitem__("drawNumber", False),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                root = json.loads(_payload())
                mutate(root["085_26001"])
                with self.assertRaises(ValueError):
                    parse(json.dumps(root, separators=(",", ":")).encode(), "dlt")

        other_game = json.loads(_payload())
        other_game["001_26001"] = {
            **_record("26001"), "gameId": "001", "extra": "allowed-outside-dlt",
        }
        self.assertEqual(
            parse(json.dumps(other_game, separators=(",", ":")).encode(), "dlt"),
            parse(_payload(), "dlt"),
        )

        malformed_old = json.loads(_payload(21))
        malformed_old["085_26001"]["extra"] = "still-forbidden-for-old-085"
        with self.assertRaisesRegex(ValueError, "record fields differ"):
            parse(json.dumps(malformed_old, separators=(",", ":")).encode(), "dlt")

        collision = json.loads(_payload())
        collision["085_26001"]["drawId"] = "26002"
        with self.assertRaisesRegex(ValueError, "key and record identity disagree"):
            parse(json.dumps(collision, separators=(",", ":")).encode(), "dlt")

    def test_dlt_number_format_order_uniqueness_and_ranges_are_strict(self) -> None:
        bad_values = (
            "01 02 03 04 05 06 07",
            "02+01+03+04+05 06+07",
            "01+01+03+04+05 06+07",
            "01+02+03+04+36 06+07",
            "01+02+03+04+05 07+06",
            "01+02+03+04+05 06+13",
        )
        for value in bad_values:
            with self.subTest(value=value):
                root = json.loads(_payload())
                root["085_26001"]["kjhm"] = value
                with self.assertRaises(ValueError):
                    parse(json.dumps(root, separators=(",", ":")).encode(), "dlt")

    def test_root_count_cap_and_safe_exception_mapping(self) -> None:
        oversized = {f"001_{index:05d}": {"invalid": True} for index in range(20_001)}
        with self.assertRaisesRegex(ValueError, "outside 1..20000"):
            parse(json.dumps(oversized, separators=(",", ":")).encode(), "dlt")
        with self.assertRaisesRegex(ValueError, "decoded safely"):
            parse(b"\xff", "dlt")
        for failure in (RecursionError("deep"), MemoryError("large")):
            with self.subTest(failure=type(failure).__name__):
                with patch("lottery_data.parsers.gdlottery_history.json.loads", side_effect=failure):
                    with self.assertRaisesRegex(ValueError, "decoded safely"):
                        parse(b"{}", "dlt")


if __name__ == "__main__":
    unittest.main()
