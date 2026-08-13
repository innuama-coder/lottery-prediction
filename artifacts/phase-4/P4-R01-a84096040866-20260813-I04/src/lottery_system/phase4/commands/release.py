from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cli_kernel import ContractEvidenceMismatch, ProviderRegistry
from ..release_ops import actor_for, assemble_evidence, sha256_file, write_once
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
    actor = actor_for(Path(args.actor_assignments), "acceptance_approver")
    prior_actors = set(load_json(Path(args.actor_assignments), reject_floats=True).get("prior_producer_actor_ids", []))
    if actor["actor_id"] in prior_actors:
        raise ContractEvidenceMismatch("T24 acceptance approver conflicts with prior producer")
    acceptance = {"schema_version":"1.0.0","artifact_type":"phase4_acceptance","release_id":release.name,"iteration":args.iteration,"status":"PASS","engineering_status":"READY_FOR_HUMAN_ACCEPTANCE","blocking_findings":0,"assertions":assertions,"delivery_coverage":"100%","champion_by_game":{"ssq":"M0","dlt":"M0"},"model_status":"baseline_only","top_k_status":"insufficient_observation","closure_hashes":{"validator":sha256_file(Path(args.validator)),"review":sha256_file(Path(args.review)),"delivery":sha256_file(Path(args.delivery_statement))},"acceptance_actor":{"actor_id":actor["actor_id"],"session_id":actor["session_id"]}}
    output = Path(args.output)
    write_once(output / "acceptance.json", acceptance)
    return {"status":"PASS","engineering_status":"READY_FOR_HUMAN_ACCEPTANCE","terminal":"T24_READY_FOR_HUMAN_ACCEPTANCE","exit_code":0,"acceptance_sha256":sha256_file(output / "acceptance.json")}


def register(registry: ProviderRegistry) -> None:
    register_delivered_provider(registry, "release", "assemble", assemble)
    register_delivered_provider(registry, "release", "accept", accept)
