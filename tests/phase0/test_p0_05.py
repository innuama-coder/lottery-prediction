from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "phase0"
ARTIFACTS = REPO / "artifacts" / "phase-0"
sys.path.insert(0, str(SCRIPTS))

from phase0lib import ValidationError, load_json, load_jsonl  # noqa: E402
from verify_phase0 import verify_p0_05_semantics  # noqa: E402


class P005SemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scope = load_json(ARTIFACTS / "scope-freeze.json")
        cls.rules = load_json(ARTIFACTS / "rule-bundles.json")
        cls.evidence = load_jsonl(ARTIFACTS / "evidence-manifest.jsonl")
        cls.catalog = load_json(ARTIFACTS / "source-catalog.json")
        cls.work = load_json(ARTIFACTS / "p0-05-work-plan.json")
        cls.coverage = load_json(ARTIFACTS / "coverage-report.json")

    def verify(self, work=None, coverage=None) -> None:
        verify_p0_05_semantics(
            self.scope, self.rules, self.evidence, self.catalog,
            self.work if work is None else work,
            self.coverage if coverage is None else coverage,
            [],
        )

    def test_baseline_replays(self) -> None:
        self.verify()

    def test_deleted_work_item_fails(self) -> None:
        value = copy.deepcopy(self.work); value["games"][0]["work_issue_ids"].pop()
        with self.assertRaises(ValidationError): self.verify(work=value)

    def test_post_hoc_sample_shrink_fails(self) -> None:
        value = copy.deepcopy(self.work); value["games"][0]["sample_issue_ids"].pop()
        with self.assertRaises(ValidationError): self.verify(work=value)

    def test_budget_release_fails(self) -> None:
        value = copy.deepcopy(self.work); value["budget_audit"]["certified_authorized_new_requests"] = 1
        with self.assertRaises(ValidationError): self.verify(work=value)

    def test_false_coverage_claim_fails(self) -> None:
        value = copy.deepcopy(self.coverage); value["games"][0]["coverage_tier"] = "target"
        with self.assertRaises(ValidationError): self.verify(coverage=value)

    def test_ssq_request_authorization_fails(self) -> None:
        value = copy.deepcopy(self.work); value["games"][1]["certified_authorized_new_requests"] = 1
        with self.assertRaises(ValidationError): self.verify(work=value)


if __name__ == "__main__":
    unittest.main()
