from __future__ import annotations

import copy
import hashlib
import unittest

from lottery_data.models import ContractViolation
from lottery_data.serialization import core_fact_sha256, make_observation_id, make_revision_id
from lottery_data.steps.incremental_engine import build_incremental_release


POLICY = {
    "game_source_pairs": {
        "ssq": {"source_ids": ["ydniu", "swlc"]},
        "dlt": {"source_ids": ["ydniu", "gdlottery"]},
    },
    "sources": [
        {"source_id": "ydniu", "publisher_id": "publisher-a"},
        {"source_id": "swlc", "publisher_id": "publisher-b"},
        {"source_id": "gdlottery", "publisher_id": "publisher-c"},
        {"source_id": "eastmoney", "publisher_id": "publisher-d"},
        {"source_id": "extra", "publisher_id": "publisher-extra"},
    ],
}


def observation(
    source: str,
    publisher: str,
    issue: str,
    *,
    front: list[int] | None = None,
    suffix: str = "",
    game: str = "ssq",
) -> dict:
    front = front or ([1, 2, 3, 4, 5, 6] if game == "ssq" else [1, 2, 3, 4, 5])
    back = [1] if game == "ssq" else [1, 2]
    raw_ref = f"raw/{source}/{game}/{issue}{suffix}.html"
    raw_sha = hashlib.sha256(raw_ref.encode()).hexdigest()
    value = {
        "observation_schema_version": "1.0.0",
        "source_id": source,
        "publisher_id": publisher,
        "game": game,
        "raw_issue_id": issue,
        "issue_id": issue,
        "draw_date_local": "2026-01-01",
        "front_numbers": front,
        "back_numbers": back,
        "source_url": f"https://example.com/{source}/{issue}",
        "captured_at_utc": "2026-01-01T12:00:00.000Z",
        "raw_ref": raw_ref,
        "raw_sha256": raw_sha,
        "parser_id": f"{source}-parser",
        "parser_version": "1.0.0",
        "core_fact_profile": "phase0-core-fact-v1",
        "parse_status": "parsed",
    }
    value["core_fact_sha256"] = core_fact_sha256(value)
    value["observation_id"] = make_observation_id(source, game, issue, raw_sha, "1.0.0")
    return value


def draw(left: dict, right: dict) -> dict:
    core = left["core_fact_sha256"]
    return {
        "record_schema_version": "1.0.0",
        "game": left["game"],
        "issue_id": left["issue_id"],
        "draw_date_local": left["draw_date_local"],
        "front_numbers": list(left["front_numbers"]),
        "back_numbers": list(left["back_numbers"]),
        "status": "verified",
        "core_fact_profile": "phase0-core-fact-v1",
        "core_fact_sha256": core,
        "evidence_links": [
            {key: item[key] for key in ("source_id", "publisher_id", "observation_id", "raw_ref", "raw_sha256")}
            for item in (left, right)
        ],
        "revision_id": make_revision_id(left["game"], left["issue_id"], core, None),
        "supersedes_revision_id": None,
        "knowledge_class": "prospective_as_observed",
        "available_at_utc": "2026-01-01T12:00:00.000Z",
    }


def raws(rows: list[dict]) -> dict[str, str]:
    return {row["raw_ref"]: row["raw_sha256"] for row in rows}


class IncrementalEngineTests(unittest.TestCase):
    def baseline(self, issue: str = "2026001") -> tuple[list[dict], list[dict]]:
        rows = [
            observation("ydniu", "publisher-a", issue, suffix="-old"),
            observation("swlc", "publisher-b", issue, suffix="-old"),
        ]
        return [draw(*rows)], rows

    def execute(self, current_draws: list[dict], current_rows: list[dict], new_rows: list[dict], **kwargs):
        return build_incremental_release(
            current_draws=current_draws,
            current_selected_observations=current_rows,
            new_observations=new_rows,
            policy=POLICY,
            current_raw_hashes=raws(current_rows),
            new_raw_hashes=raws(new_rows),
            **kwargs,
        )

    def history(self, game: str, count: int, pair: tuple[str, str]) -> tuple[list[dict], list[dict]]:
        publishers = {item["source_id"]: item["publisher_id"] for item in POLICY["sources"]}
        draws: list[dict] = []
        rows: list[dict] = []
        for sequence in range(1, count + 1):
            issue = f"2026{sequence:03d}"
            selected = [
                observation(source, publishers[source], issue, game=game, suffix="-old")
                for source in pair
            ]
            draws.append(draw(*selected))
            rows.extend(selected)
        return draws, rows

    def test_unchanged_preserves_old_record_and_evidence_byte_surface(self) -> None:
        current_draws, current_rows = self.baseline()
        new_rows = [
            observation("ydniu", "publisher-a", "2026001", suffix="-new"),
            observation("swlc", "publisher-b", "2026001", suffix="-new"),
        ]
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertTrue(result.publishable)
        self.assertEqual(result.changes, {"added": 0, "revised": 0, "unchanged": 1, "conflict": 0, "unresolved": 0})
        self.assertEqual(result.draws, tuple(current_draws))
        self.assertEqual(result.release_observations, tuple(current_rows))
        self.assertEqual(len(result.run_observations), 2)

    def test_added_is_dynamic_and_has_null_supersedes_and_two_evidence(self) -> None:
        current_draws, current_rows = self.baseline()
        new_rows = [
            observation("ydniu", "publisher-a", "2026002"),
            observation("swlc", "publisher-b", "2026002"),
        ]
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertTrue(result.publishable)
        self.assertEqual((len(result.draws), len(result.release_observations)), (2, 4))
        added = next(row for row in result.draws if row["issue_id"] == "2026002")
        self.assertIsNone(added["supersedes_revision_id"])
        self.assertEqual(len(added["evidence_links"]), 2)
        self.assertEqual(result.changes["added"], 1)

    def test_revised_supersedes_old_revision_without_mutating_old_release(self) -> None:
        current_draws, current_rows = self.baseline()
        before = copy.deepcopy((current_draws, current_rows))
        changed = [1, 2, 3, 4, 5, 7]
        new_rows = [
            observation("ydniu", "publisher-a", "2026001", front=changed, suffix="-new"),
            observation("swlc", "publisher-b", "2026001", front=changed, suffix="-new"),
        ]
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertEqual(result.draws[0]["supersedes_revision_id"], current_draws[0]["revision_id"])
        self.assertNotEqual(result.draws[0]["revision_id"], current_draws[0]["revision_id"])
        self.assertEqual((current_draws, current_rows), before)
        self.assertEqual(result.changes["revised"], 1)

    def test_extra_dissent_blocks_even_when_required_pair_agrees(self) -> None:
        current_draws, current_rows = self.baseline()
        agreed = [1, 2, 3, 4, 5, 7]
        new_rows = [
            observation("ydniu", "publisher-a", "2026001", front=agreed, suffix="-new"),
            observation("swlc", "publisher-b", "2026001", front=agreed, suffix="-new"),
            observation("extra", "publisher-extra", "2026001", front=[1, 2, 3, 4, 5, 8]),
        ]
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertFalse(result.publishable)
        self.assertEqual(result.changes["conflict"], 1)
        self.assertIn("PUBLISHER_CORE_FACT_CONFLICT", result.quality["deterministic"]["blocking_reason_codes"])

    def test_missing_pair_and_bounded_gap_are_unresolved_without_guessing_tail(self) -> None:
        current_draws, current_rows = self.baseline("2026001")
        new_rows = [observation("ydniu", "publisher-a", "2026003")]
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertFalse(result.publishable)
        self.assertEqual(result.changes["unresolved"], 2)  # missing pair plus observed bounded gap 002
        keys = {(row["issue_id"], row["decision"]) for row in result.reconciliation}
        self.assertIn(("2026002", "unresolved"), keys)
        self.assertNotIn(("2026004", "unresolved"), keys)

    def test_old_issue_outside_recheck_is_not_revised(self) -> None:
        first_draws, first_rows = self.baseline("2026001")
        second_draws, second_rows = self.baseline("2026002")
        changed = [1, 2, 3, 4, 5, 7]
        new_rows = [
            observation("ydniu", "publisher-a", "2026001", front=changed, suffix="-new"),
            observation("swlc", "publisher-b", "2026001", front=changed, suffix="-new"),
        ]
        result = self.execute(first_draws + second_draws, first_rows + second_rows, new_rows, recheck_limit=1)
        self.assertTrue(result.publishable)
        self.assertEqual(result.changes, {"added": 0, "revised": 0, "unchanged": 0, "conflict": 0, "unresolved": 0})
        self.assertEqual(result.draws, tuple(first_draws + second_draws))

    def test_verified_old_raw_hash_is_required(self) -> None:
        current_draws, current_rows = self.baseline()
        with self.assertRaises(ContractViolation):
            build_incremental_release(
                current_draws=current_draws,
                current_selected_observations=current_rows,
                new_observations=[], policy=POLICY,
                current_raw_hashes={}, new_raw_hashes={},
            )

    def test_every_run_observation_raw_is_closed_even_when_not_selected(self) -> None:
        current_draws, current_rows = self.baseline()
        new_rows = [
            observation("ydniu", "publisher-a", "2026001", suffix="-new"),
            observation("swlc", "publisher-b", "2026001", suffix="-new"),
            observation("extra", "publisher-extra", "2026001", suffix="-extra"),
        ]
        incomplete = raws(new_rows)
        incomplete.pop(new_rows[-1]["raw_ref"])
        with self.assertRaises(ContractViolation):
            build_incremental_release(
                current_draws=current_draws,
                current_selected_observations=current_rows,
                new_observations=new_rows, policy=POLICY,
                current_raw_hashes=raws(current_rows), new_raw_hashes=incomplete,
            )

    def test_identical_old_and_new_observations_still_require_new_raw_hash(self) -> None:
        current_draws, current_rows = self.baseline()
        identical_run_rows = copy.deepcopy(current_rows)
        with self.assertRaises(ContractViolation):
            build_incremental_release(
                current_draws=current_draws,
                current_selected_observations=current_rows,
                new_observations=identical_run_rows, policy=POLICY,
                current_raw_hashes=raws(current_rows), new_raw_hashes={},
            )
        wrong = raws(identical_run_rows)
        wrong[identical_run_rows[0]["raw_ref"]] = "0" * 64
        with self.assertRaises(ContractViolation):
            build_incremental_release(
                current_draws=current_draws,
                current_selected_observations=current_rows,
                new_observations=identical_run_rows, policy=POLICY,
                current_raw_hashes=raws(current_rows), new_raw_hashes=wrong,
            )

    def test_identical_old_and_new_raws_verify_both_sides_then_deduplicate_stably(self) -> None:
        current_draws, current_rows = self.baseline()
        identical_run_rows = copy.deepcopy(current_rows)
        result = self.execute(current_draws, current_rows, identical_run_rows)
        plan = list(result.raw_lineage_copy_plan)
        self.assertEqual([item["raw_ref"] for item in plan], sorted(item["raw_ref"] for item in plan))
        self.assertEqual(len(plan), 2)
        self.assertTrue(all(item["origin"] == "both" for item in plan))
        self.assertTrue(all(item["origins"] == ["current_release", "current_run"] for item in plan))
        self.assertTrue(all(item["action"] == "copy_verified" for item in plan))

    def test_issue_pair_resolver_supports_mixed_snapshot_fallback_without_engine_hardcoding(self) -> None:
        rows = []
        expected_pairs = {
            "2026026": ("ydniu", "gdlottery"),
            "2026027": ("ydniu", "gdlottery"),
            "2026028": ("ydniu", "eastmoney"),
        }
        publishers = {item["source_id"]: item["publisher_id"] for item in POLICY["sources"]}
        for issue, pair in expected_pairs.items():
            rows.extend(observation(source, publishers[source], issue, game="dlt") for source in pair)
        calls: list[tuple[str, str]] = []

        def resolve(game: str, issue_id: str):
            calls.append((game, issue_id))
            return expected_pairs[issue_id]

        result = build_incremental_release(
            current_draws=[], current_selected_observations=[], new_observations=rows,
            policy=POLICY, current_raw_hashes={}, new_raw_hashes=raws(rows), pair_resolver=resolve,
        )
        self.assertTrue(result.publishable)
        self.assertEqual(calls, [("dlt", "2026026"), ("dlt", "2026027"), ("dlt", "2026028")])
        selected = {
            draw_row["issue_id"]: {link["source_id"] for link in draw_row["evidence_links"]}
            for draw_row in result.draws
        }
        self.assertEqual(selected, {issue: set(pair) for issue, pair in expected_pairs.items()})

    def test_pair_resolver_missing_pair_and_same_publisher_fail_closed(self) -> None:
        rows = [
            observation("ydniu", "publisher-a", "2026026", game="dlt"),
            observation("gdlottery", "publisher-c", "2026026", game="dlt"),
        ]
        common = dict(
            current_draws=[], current_selected_observations=[], new_observations=rows,
            policy=POLICY, current_raw_hashes={}, new_raw_hashes=raws(rows),
        )
        with self.assertRaises(ContractViolation):
            build_incremental_release(**common, pair_resolver=lambda game, issue: None)
        with self.assertRaisesRegex(ContractViolation, "publishers are not distinct"):
            build_incremental_release(
                **common,
                source_identities={"evil-twin": "publisher-a"},
                pair_resolver=lambda game, issue: ("ydniu", "evil-twin"),
            )

    def test_resolver_pair_order_is_canonical_and_extra_dissent_still_blocks(self) -> None:
        agreed = [1, 2, 3, 4, 5]
        rows = [
            observation("ydniu", "publisher-a", "2026026", game="dlt", front=agreed),
            observation("gdlottery", "publisher-c", "2026026", game="dlt", front=agreed),
        ]
        arguments = dict(
            current_draws=[], current_selected_observations=[], new_observations=rows,
            policy=POLICY, current_raw_hashes={}, new_raw_hashes=raws(rows),
        )
        forward = build_incremental_release(**arguments, pair_resolver=lambda game, issue: ("ydniu", "gdlottery"))
        reverse = build_incremental_release(**arguments, pair_resolver=lambda game, issue: ("gdlottery", "ydniu"))
        self.assertEqual(forward.draws, reverse.draws)
        self.assertEqual(forward.quality["deterministic"]["output_hashes"], reverse.quality["deterministic"]["output_hashes"])

        dissent = rows + [observation("extra", "publisher-extra", "2026026", game="dlt", front=[1, 2, 3, 4, 6])]
        blocked = build_incremental_release(
            current_draws=[], current_selected_observations=[], new_observations=dissent,
            policy=POLICY, current_raw_hashes={}, new_raw_hashes=raws(dissent),
            pair_resolver=lambda game, issue: ("ydniu", "gdlottery"),
        )
        self.assertFalse(blocked.publishable)
        self.assertIn("PUBLISHER_CORE_FACT_CONFLICT", blocked.quality["deterministic"]["blocking_reason_codes"])

    def test_live_dlt_recheck_defers_nineteen_unchanged_single_side_old_issues(self) -> None:
        current_draws, current_rows = self.history("dlt", 20, ("ydniu", "eastmoney"))
        before = copy.deepcopy((current_draws, current_rows))
        new_rows = [
            observation("ydniu", "publisher-a", f"2026{sequence:03d}", game="dlt", suffix="-recheck")
            for sequence in range(1, 21)
        ]
        new_rows.extend([
            observation("ydniu", "publisher-a", "2026021", game="dlt", suffix="-new"),
            observation("gdlottery", "publisher-c", "2026021", game="dlt", suffix="-new"),
        ])
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertTrue(result.publishable)
        self.assertEqual((current_draws, current_rows), before)
        counts = result.quality["deterministic"]["counts"]
        self.assertEqual(
            {key: counts[key] for key in ("recheck_attempted", "recheck_complete", "recheck_deferred")},
            {"recheck_attempted": 19, "recheck_complete": 0, "recheck_deferred": 19},
        )
        deferred = [row for row in result.reconciliation if row["reason_code"] == "RECHECK_DEFERRED_MISSING_PARTNER"]
        self.assertEqual(len(deferred), 19)
        self.assertNotIn("2026001", {row["issue_id"] for row in result.reconciliation})
        self.assertEqual(result.changes, {"added": 1, "revised": 0, "unchanged": 0, "conflict": 0, "unresolved": 0})
        self.assertTrue(all(len(row["selected_observation_ids"]) == 2 for row in deferred))

    def test_live_dlt_single_side_old_change_is_unconfirmed_and_blocks(self) -> None:
        current_draws, current_rows = self.history("dlt", 20, ("ydniu", "eastmoney"))
        new_rows = [
            observation("ydniu", "publisher-a", f"2026{sequence:03d}", game="dlt", suffix="-recheck")
            for sequence in range(1, 21)
        ]
        new_rows[-1] = observation(
            "ydniu", "publisher-a", "2026020", game="dlt", suffix="-changed", front=[1, 2, 3, 4, 6],
        )
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertFalse(result.publishable)
        changed = next(row for row in result.reconciliation if row["issue_id"] == "2026020")
        self.assertEqual((changed["decision"], changed["reason_code"]), ("unresolved", "RECHECK_UNCONFIRMED_CHANGE"))
        self.assertIn("RECHECK_UNCONFIRMED_CHANGE", result.quality["deterministic"]["blocking_reason_codes"])

    def test_live_dlt_new_issue_missing_gd_partner_remains_blocking(self) -> None:
        current_draws, current_rows = self.history("dlt", 20, ("ydniu", "eastmoney"))
        new_rows = [observation("ydniu", "publisher-a", "2026021", game="dlt", suffix="-new")]
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertFalse(result.publishable)
        new_issue = next(row for row in result.reconciliation if row["issue_id"] == "2026021")
        self.assertEqual((new_issue["decision"], new_issue["reason_code"]), ("unresolved", "REQUIRED_SOURCE_PAIR_MISSING"))
        self.assertEqual(result.changes["unresolved"], 1)

    def test_live_ssq_two_history_sources_complete_twenty_rechecks(self) -> None:
        current_draws, current_rows = self.history("ssq", 20, ("ydniu", "swlc"))
        new_rows = []
        for sequence in range(1, 21):
            issue = f"2026{sequence:03d}"
            new_rows.extend([
                observation("ydniu", "publisher-a", issue, suffix="-recheck"),
                observation("swlc", "publisher-b", issue, suffix="-recheck"),
            ])
        result = self.execute(current_draws, current_rows, new_rows)
        self.assertTrue(result.publishable)
        counts = result.quality["deterministic"]["counts"]
        self.assertEqual(
            {key: counts[key] for key in ("recheck_attempted", "recheck_complete", "recheck_deferred")},
            {"recheck_attempted": 20, "recheck_complete": 20, "recheck_deferred": 0},
        )
        self.assertEqual(result.changes["unchanged"], 20)


if __name__ == "__main__":
    unittest.main()
