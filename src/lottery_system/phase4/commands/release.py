from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cli_kernel import ContractEvidenceMismatch, ProviderRegistry
from ..release_ops import actor_for, assemble_evidence, provenance, sha256_file, write_once
from ..serialization import load_json
from ..provider_registry import register_delivered_provider


def assemble(args: Any) -> dict[str, Any]:
    release = Path(args.release_root).resolve()
    if args.phase == "evidence":
        assignments = release / "control/actor-assignments-formal.json"
        environment = load_json(release / "control/execution-environment.json", reject_floats=True)
        manifest = assemble_evidence(release, assignments, environment["implementation_commit"])
        return {"status":"PASS","terminal":"T19_EVIDENCE_MANIFEST_PASS","exit_code":0,"manifest_sha256":sha256_file(release / "manifest/evidence-manifest.json"),"file_count":manifest["file_count"]}
    if args.phase == "prepare-formal":
        environment = release / "control/execution-environment.json"
        if not environment.is_file():
            raise ContractEvidenceMismatch("T15 execution environment is absent")
        return {"status":"PASS","terminal":"T15_FORMAL_RELEASE_FROZEN","exit_code":0,"execution_environment_sha256":sha256_file(environment)}
    raise ContractEvidenceMismatch("release assemble phase is not registered")


def accept(args: Any) -> dict[str, Any]:
    release = Path(args.release_root).resolve()
    validator = load_json(Path(args.validator), reject_floats=True)
    review = load_json(Path(args.review), reject_floats=True)
    delivery = load_json(Path(args.delivery_statement), reject_floats=True)
    if validator.get("status") != "PASS" or review.get("status") != "PASS" or delivery.get("decision") != "PASS":
        raise ContractEvidenceMismatch("T24 upstream closure is not PASS")
    assertions = validator.get("assertions", [])
    expected = [f"P4-MVP-A{i:02d}" for i in range(1, 22)]
    if [row.get("assertion_id") for row in assertions] != expected or any(row.get("status") != "PASS" for row in assertions) or validator.get("blocking_findings") != 0:
        raise ContractEvidenceMismatch("T24 A01-A21 or blocker gate failed")
    dispositions = {row["assertion_id"]: row["status"] for row in assertions}
    if review.get("a01_a21_disposition") != dispositions or review.get("blocking_findings") != []:
        raise ContractEvidenceMismatch("T24 review disposition or blocker gate failed")
    assignments = load_json(Path(args.actor_assignments), reject_floats=True)
    actor = actor_for(Path(args.actor_assignments), "acceptance_approver")
    prior_actors = {
        row["actor_id"]
        for row in assignments["assignments"]
        if any(int(task_id[1:]) <= 23 for task_id in row.get("task_ids", []))
    }
    if actor["actor_id"] in prior_actors:
        raise ContractEvidenceMismatch("T24 acceptance approver conflicts with prior producer")
    evidence_manifest = release / "manifest/evidence-manifest.json"
    replay_closure = release / "manifest/replay-closure.json"
    validator_closure = release / "manifest/validator-closure.json"
    review_closure = release / "manifest/review-closure.json"
    delivery_closure = release / "manifest/delivery-closure.json"
    review_closure_value = load_json(review_closure, reject_floats=True)
    delivery_closure_value = load_json(delivery_closure, reject_floats=True)
    if (
        review_closure_value.get("parent_sha256") != sha256_file(validator_closure)
        or delivery_closure_value.get("parent_sha256") != sha256_file(review_closure)
        or delivery.get("review_closure_sha256") != sha256_file(review_closure)
        or delivery.get("validator_closure_sha256") != sha256_file(validator_closure)
    ):
        raise ContractEvidenceMismatch("T24 closure hash chain mismatch")
    model_status = [
        {"schema_version":"1.0.0","artifact_type":"phase4_model_status","game":game,"model_id":"M0","comparator_champion_id":"M0","model_release_id":release.name,"window_id":"formal-qualification","status":"baseline_only"}
        for game in ("ssq", "dlt")
    ]
    top_k_status = [
        {"schema_version":"1.0.0","artifact_type":"phase4_top_k_status","game":game,"K":k,"model_id":"M0","comparator_champion_id":"M0","model_release_id":release.name,"window_id":"formal-qualification","status":"insufficient_observation"}
        for game in ("ssq", "dlt") for k in (10, 100, 200, 1000)
    ]
    output = Path(args.output)
    acceptance = {
        "schema_version":"1.0.0",
        "artifact_type":"phase4_acceptance",
        "release_id":release.name,
        "iteration":args.iteration,
        "status":"PASS",
        "engineering_status":"READY_FOR_HUMAN_ACCEPTANCE",
        "a01_a21":dispositions,
        "blocking_findings":[],
        "delivery_coverage":"100%",
        "champion_by_game":{"ssq":"M0","dlt":"M0"},
        "model_status":model_status,
        "top_k_status":top_k_status,
        "evidence_manifest_sha256":sha256_file(evidence_manifest),
        "replay_closure_sha256":sha256_file(replay_closure),
        "validator_closure_sha256":sha256_file(validator_closure),
        "review_closure_sha256":sha256_file(review_closure),
        "machine_delivery_closure_sha256":sha256_file(delivery_closure),
        "approver_provenance":provenance(actor,"acceptance_approver","T24",load_json(release / "control/execution-environment.json",reject_floats=True)["implementation_commit"]) | {"path":f"acceptance/{args.iteration}/acceptance.json"},
    }
    write_once(output / "acceptance.json", acceptance)
    return {"status":"PASS","engineering_status":"READY_FOR_HUMAN_ACCEPTANCE","terminal":"T24_READY_FOR_HUMAN_ACCEPTANCE","exit_code":0,"acceptance_sha256":sha256_file(output / "acceptance.json")}


def register(registry: ProviderRegistry) -> None:
    register_delivered_provider(registry, "release", "assemble", assemble)
    register_delivered_provider(registry, "release", "accept", accept)
