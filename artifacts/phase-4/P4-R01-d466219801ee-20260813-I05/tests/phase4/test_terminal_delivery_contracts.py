from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator, RefResolver

from lottery_system.phase4.commands.release import accept
from lottery_system.phase4.release_ops import canonical, closure, provenance, sha256_file, write_once


ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str):
    path = ROOT / "scripts/phase4_independent" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def validate_schema(name: str, value: dict) -> None:
    path = ROOT / "schemas/phase4" / name
    schema = json.loads(path.read_text())
    store = {}
    for candidate in (ROOT / "schemas/phase4").glob("*.schema.json"):
        candidate_schema = json.loads(candidate.read_text())
        store[candidate.as_uri()] = candidate_schema
        if "$id" in candidate_schema:
            store[candidate_schema["$id"]] = candidate_schema
    Draft202012Validator(
        schema,
        resolver=RefResolver(base_uri=path.as_uri(), referrer=schema, store=store),
    ).validate(value)


class TerminalDeliveryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.release = Path(self.temporary.name) / "P4-R01-0123456789ab-20260813-I05"
        for relative in ("control", "manifest", "validator", "replay", "review", "delivery", "acceptance/I01"):
            (self.release / relative).mkdir(parents=True, exist_ok=True)
        self.commit = "a" * 40
        assignments = {
            "assignments": [
                {"actor_id":"controller","actor_type":"codex_session","roles":["release_controller"],"session_id":"/controller","task_ids":["T00","T19"]},
                {"actor_id":"validator","actor_type":"codex_session","roles":["acceptance_engineer"],"session_id":"/validator","task_ids":["T21"]},
                {"actor_id":"replay","actor_type":"codex_session","roles":["independent_replay_operator"],"session_id":"/replay","task_ids":["T20"]},
                {"actor_id":"reviewer","actor_type":"codex_session","roles":["independent_reviewer"],"session_id":"/reviewer","task_ids":["T22"]},
                {"actor_id":"delivery","actor_type":"codex_session","roles":["machine_delivery_statement"],"session_id":"/delivery","task_ids":["T23"]},
                {"actor_id":"approver","actor_type":"codex_session","roles":["acceptance_approver"],"session_id":"/approver","task_ids":["T24"]},
            ]
        }
        write_once(self.release / "control/actor-assignments-formal.json", assignments)
        write_once(self.release / "control/execution-environment.json", {"implementation_commit":self.commit})
        evidence = {"files":[{"producer_provenance":{"producer_actor_id":"controller"}}]}
        write_once(self.release / "manifest/evidence-manifest.json", evidence)
        write_once(self.release / "replay/replay.json", {"status":"PASS"})
        closure(self.release,"replay",self.release / "manifest/evidence-manifest.json",[self.release / "replay/replay.json"],provenance(assignments["assignments"][2],"independent_replay_operator","T20",self.commit))
        assertions = [{"assertion_id":f"P4-MVP-A{i:02d}","status":"PASS"} for i in range(1,22)]
        write_once(self.release / "validator/final-validator.json", {"status":"PASS","blocking_findings":0,"assertions":assertions})
        closure(self.release,"validator",self.release / "manifest/replay-closure.json",[self.release / "validator/final-validator.json"],provenance(assignments["assignments"][1],"acceptance_engineer","T21",self.commit))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_review_delivery_and_acceptance_match_strict_schemas(self) -> None:
        review_script = load_script("check_review_closure.py")
        review = review_script.build_review(self.release, self.release / "manifest/validator-closure.json")
        validate_schema("review.schema.json", review)
        write_once(self.release / "review/review.json", review)
        assignments = json.loads((self.release / "control/actor-assignments-formal.json").read_text())
        closure(self.release,"review",self.release / "manifest/validator-closure.json",[self.release / "review/review.json"],provenance(assignments["assignments"][3],"independent_reviewer","T22",self.commit))

        delivery_script = load_script("validate_machine_delivery_statement.py")
        old_argv = __import__("sys").argv
        try:
            __import__("sys").argv = ["delivery","--delivery-statement",str(self.release / "delivery/machine-delivery-statement.json"),"--review-closure",str(self.release / "manifest/review-closure.json"),"--actor-assignments",str(self.release / "control/actor-assignments-formal.json")]
            self.assertEqual(delivery_script.main(), 0)
        finally:
            __import__("sys").argv = old_argv
        statement = json.loads((self.release / "delivery/machine-delivery-statement.json").read_text())
        validate_schema("signature.schema.json", statement)
        self.assertEqual(statement["scientific_wording_sha256"], hashlib.sha256(review["scientific_wording"].encode()).hexdigest())

        result = accept(SimpleNamespace(
            release_root=self.release,
            iteration="I01",
            validator=self.release / "validator/final-validator.json",
            review=self.release / "review/review.json",
            delivery_statement=self.release / "delivery/machine-delivery-statement.json",
            actor_assignments=self.release / "control/actor-assignments-formal.json",
            output=self.release / "acceptance/I01",
        ))
        self.assertEqual(result["engineering_status"], "READY_FOR_HUMAN_ACCEPTANCE")
        acceptance = json.loads((self.release / "acceptance/I01/acceptance.json").read_text())
        validate_schema("acceptance.schema.json", acceptance)
        self.assertEqual(len(acceptance["model_status"]), 2)
        self.assertEqual(len(acceptance["top_k_status"]), 8)

    def test_acceptance_rejects_a_changed_review_closure_hash(self) -> None:
        review_script = load_script("check_review_closure.py")
        review = review_script.build_review(self.release, self.release / "manifest/validator-closure.json")
        write_once(self.release / "review/review.json", review)
        assignments = json.loads((self.release / "control/actor-assignments-formal.json").read_text())
        closure(self.release,"review",self.release / "manifest/validator-closure.json",[self.release / "review/review.json"],provenance(assignments["assignments"][3],"independent_reviewer","T22",self.commit))
        statement = {
            "decision":"PASS",
            "review_closure_sha256":"0" * 64,
            "validator_closure_sha256":sha256_file(self.release / "manifest/validator-closure.json"),
        }
        write_once(self.release / "delivery/machine-delivery-statement.json", statement)
        closure(self.release,"delivery",self.release / "manifest/review-closure.json",[self.release / "delivery/machine-delivery-statement.json"],provenance(assignments["assignments"][4],"machine_delivery_statement","T23",self.commit))
        with self.assertRaisesRegex(Exception, "closure hash chain mismatch"):
            accept(SimpleNamespace(release_root=self.release,iteration="I01",validator=self.release / "validator/final-validator.json",review=self.release / "review/review.json",delivery_statement=self.release / "delivery/machine-delivery-statement.json",actor_assignments=self.release / "control/actor-assignments-formal.json",output=self.release / "acceptance/I01"))
