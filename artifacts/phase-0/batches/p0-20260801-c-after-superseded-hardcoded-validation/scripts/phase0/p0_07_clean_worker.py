"""Fixed-path clean replay worker launched only by the P0-07 replay driver."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Any

from p0_05_history import GENERATED_AT
from p0_07_closeout import (
    DERIVED_DIRECTORY_NAME, DERIVED_MANIFEST_NAME, GATE_INPUT_NAME, INPUT_MANIFEST_NAME,
    _aggregate_global_gates, _candidate_bytes, _utc_text, prepare, require_prepare_ready,
    validate_candidate_snapshot,
)
from p0_07_decision import build_per_game_gate_results, derive_per_game_outcome, derive_project_decision
from p0_07_handoff import build_handoff_fixture, project_handoff_pass
from phase0lib import canonical_json_bytes, load_json, load_jsonl, sha256_bytes, sha256_file
from verify_phase0 import (
    verify_observation, verify_p0_02, verify_p0_03, verify_p0_05_semantics, verify_provenance,
    verify_reviewer, verify_rule_bundles, verify_scope, verify_source_catalog,
)


REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / "artifacts" / "phase-0"
CANDIDATE = ARTIFACTS / "p0-07-candidate"
STAGING = ARTIFACTS / ".p0-07-replay-staging"
WORKER = STAGING / "worker"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _technical_outcome(gates: list[dict[str, Any]], tier: str) -> str:
    noncoverage = all(item["outcome"] == "PASS" for item in gates if item["gate_id"] != "G-COVERAGE")
    coverage = next(item for item in gates if item["gate_id"] == "G-COVERAGE")
    if noncoverage and coverage["outcome"] == "PASS" and tier == "target": return "PASS_FULL"
    if noncoverage and coverage["outcome"] == "PASS" and tier == "minimum_viable": return "PASS_LIMITED"
    failures = [item for item in gates if item["outcome"] == "FAIL"]
    if any(item["remediation_status"] == "alternatives_exhausted_no_evidentiary_path" for item in failures): return "STOP"
    if any(item["remediation_status"] == "concrete_compliant_action_available" for item in failures): return "HOLD"
    raise ValueError("technical first-12 gates have no unique outcome")


def run_worker(*, utcnow_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    now = utcnow_fn()
    if WORKER.exists(): raise ValueError("worker output is not an empty clean directory")
    WORKER.mkdir()
    input_manifest, snapshot_at = validate_candidate_snapshot(REPO, ARTIFACTS, CANDIDATE, now=now)
    before = {item["path"]:(sha256_file(REPO/item["path"]),(REPO/item["path"]).stat().st_mtime_ns) for item in input_manifest["files"]}
    rebuilt = WORKER / "rebuilt"
    prepare(REPO, ARTIFACTS, rebuilt, utcnow_fn=lambda: snapshot_at)
    if _candidate_bytes(CANDIDATE) != _candidate_bytes(rebuilt): raise ValueError("clean rebuilt bytes differ from candidate")
    contract_path = REPO / "docs" / "roadmap" / "phase-0-acceptance-contract.json"
    contract = load_json(contract_path); schemas = ARTIFACTS / "schemas"
    scope=load_json(ARTIFACTS/"scope-freeze.json"); observation=load_json(ARTIFACTS/"observation-plan.json")
    assignment=load_json(ARTIFACTS/"reviewer-assignment.json")
    verify_scope(scope,contract_path); verify_observation(scope,observation); verify_reviewer(scope,assignment)
    verify_p0_02(contract_path,ARTIFACTS,schemas); verify_p0_03(contract_path,ARTIFACTS,schemas)
    catalog=load_json(ARTIFACTS/"source-catalog.json"); verify_source_catalog(catalog)
    rules=load_json(ARTIFACTS/"rule-bundles.json"); verify_rule_bundles(scope,rules)
    _o,_c,_p,soak,_t=require_prepare_ready(REPO,ARTIFACTS,utcnow_fn=lambda:now)
    evidence=load_jsonl(ARTIFACTS/"evidence-manifest.jsonl")
    coverage=load_json(rebuilt/DERIVED_DIRECTORY_NAME/"coverage-report.json")
    reconciliation=load_jsonl(rebuilt/DERIVED_DIRECTORY_NAME/"reconciliation.jsonl")
    p005=copy.deepcopy(coverage); p005["generated_at_utc"]=GENERATED_AT
    verify_p0_05_semantics(scope,rules,evidence,catalog,load_json(ARTIFACTS/"p0-05-work-plan.json"),p005,reconciliation)
    provenance_root=WORKER/"provenance-root"; pa=provenance_root/"artifacts"/"phase-0"
    (pa/"raw").mkdir(parents=True); (pa/"normalized").mkdir()
    env=pa/"environment-lock.json"; env.write_bytes((ARTIFACTS/"environment-lock.json").read_bytes())
    for item in evidence:
        raw=pa/Path(*item["stored_payload_path"].split("/")[2:]); raw.parent.mkdir(parents=True,exist_ok=True); raw.write_bytes((REPO/item["stored_payload_path"]).read_bytes())
        if item["normalized_record_sha256"]!="0"*64:
            name=Path(item["normalized_record_ref"]).name; (pa/"normalized"/name).write_bytes((rebuilt/DERIVED_DIRECTORY_NAME/"normalized"/name).read_bytes())
    verify_provenance(provenance_root,env,evidence)
    revision=load_json(rebuilt/DERIVED_DIRECTORY_NAME/"revision-report.json")
    gate_inputs=load_json(rebuilt/DERIVED_DIRECTORY_NAME/GATE_INPUT_NAME)
    repairs=sorted(ARTIFACTS.glob("repair-manifest-p0-20260801-c*.json"))
    if len(repairs)!=1: raise ValueError("expected exactly one P0-07 repair evidence file")
    repair=load_json(repairs[0]); base=[]
    for game in ("dlt","ssq"):
        gates=build_per_game_gate_results(game,gate_inputs=gate_inputs,coverage=coverage,revision=revision,exact24=soak,source_catalog=catalog,contract=contract,repair_evidence=repair,clean_replay_match=True,handoff_consumer_match=False)
        tier=next(item["coverage_tier"] for item in coverage["games"] if item["game"]==game)
        base.append({"game":game,"gate_results":gates,"coverage_tier":tier,"per_game_outcome":derive_per_game_outcome(gates,tier)})
    proposed_results=project_handoff_pass(base,contract=contract)
    fixture=build_handoff_fixture(proposed_results,reconciliation=reconciliation,evidence=evidence,contract=contract,schema=load_json(schemas/"stage1-handoff-fixture.schema.json"))
    _write(WORKER/"proposed-stage1-handoff-fixture.json",fixture)
    technical=[]
    for result in proposed_results:
        gates=result["gate_results"][:12]
        technical.append({"game":result["game"],"gate_results":gates,"technical_outcome":_technical_outcome(gates,result["coverage_tier"]),"coverage_tier":result["coverage_tier"]})
    content=WORKER/"content"/"sha256"; content.mkdir(parents=True)
    logical:dict[str,dict[str,Any]]={}
    def add_ref(logical_ref:str,source:str,payload:bytes)->None:
        sha=sha256_bytes(payload); target=content/sha
        if not target.exists(): target.write_bytes(payload)
        logical[logical_ref]={"logical_ref":logical_ref,"source":source,"size":len(payload),"sha256":sha,"bundle_path":f"content/sha256/{sha}"}
    ref_paths={
        "docs/roadmap/phase-0-acceptance-contract.json":contract_path,
        "artifacts/phase-0/repair-manifest-p0-20260801-c-draft.json":repairs[0],
        "artifacts/phase-0/source-catalog.json":ARTIFACTS/"source-catalog.json",
        "artifacts/phase-0/soak-run-log.jsonl":ARTIFACTS/"soak-run-log.jsonl",
        "artifacts/phase-0/field-contract.json":ARTIFACTS/"field-contract.json",
        "artifacts/phase-0/rule-bundles.json":ARTIFACTS/"rule-bundles.json",
        "artifacts/phase-0/environment-lock.json":ARTIFACTS/"environment-lock.json",
        "derived/p0-07-gate-inputs.json":rebuilt/"derived"/GATE_INPUT_NAME,
        "derived/coverage-report.json":rebuilt/"derived"/"coverage-report.json",
        "derived/revision-report.json":rebuilt/"derived"/"revision-report.json",
    }
    for ref,path in ref_paths.items(): add_ref(ref,str(path.relative_to(REPO)).replace("\\","/"),path.read_bytes())
    for item in evidence: add_ref(item["evidence_id"],f"artifacts/phase-0/evidence-manifest.jsonl#{item['evidence_id']}",canonical_json_bytes(item)+b"\n")
    _write(WORKER/"logical-ref-index.json",logical)
    facts={
        "schema_version":"1.0.0","artifact_type":"p0_07_clean_replay_facts","snapshot_at_utc":_utc_text(snapshot_at),
        "input_manifest_sha256":sha256_file(CANDIDATE/INPUT_MANIFEST_NAME),"rebuilt_input_manifest_sha256":sha256_file(rebuilt/INPUT_MANIFEST_NAME),
        "candidate_output_manifest_sha256":sha256_file(CANDIDATE/DERIVED_MANIFEST_NAME),"rebuilt_output_manifest_sha256":sha256_file(rebuilt/DERIVED_MANIFEST_NAME),
        "per_game_results":technical,"gate_results":_aggregate_global_gates([{"game":x["game"],"gate_results":x["gate_results"]} for x in technical]),
        "project_decision":derive_project_decision([{"game":x["game"],"per_game_outcome":x["technical_outcome"]} for x in technical]),
        "proposed_handoff_file_bytes_sha256":sha256_file(WORKER/"proposed-stage1-handoff-fixture.json"),
    }
    _write(WORKER/"facts.json",facts)
    final={item["path"]:(sha256_file(REPO/item["path"]),(REPO/item["path"]).stat().st_mtime_ns) for item in input_manifest["files"]}
    if before!=final: raise ValueError("input bytes/mtime changed during clean worker")
    return facts


def main()->int:
    if len(sys.argv) != 1:
        sys.stderr.write('{"status":"FAIL","error":"arguments are forbidden"}\n')
        return 2
    try:
        facts=run_worker(); sys.stdout.buffer.write(canonical_json_bytes({"status":"PASS","facts_sha256":sha256_file(WORKER/"facts.json")})+b"\n"); return 0
    except Exception as exc:
        sys.stderr.buffer.write(canonical_json_bytes({"status":"FAIL","error":str(exc)})+b"\n"); return 2


if __name__=="__main__": raise SystemExit(main())
