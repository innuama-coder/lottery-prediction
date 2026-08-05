"""Fixed-path terminal finalizer and strict expected-byte verifier."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from p0_07_closeout import DERIVED_MANIFEST_NAME, INPUT_MANIFEST_NAME
from p0_07_final_model import build_acceptance_markdown, build_final_replay, build_machine_decision, canonical_line, signature_payload
from p0_07_replay_driver import CANONICAL_COMMAND, validate_bundle
from phase0lib import load_json, sha256_file, validate_schema_instance


REPO=Path(__file__).resolve().parents[2]
ARTIFACTS=REPO/"artifacts"/"phase-0"
BUNDLE=ARTIFACTS/"p0-07-review-bundle"
REVIEWER_RECEIPT=ARTIFACTS/"p0-07-reviewer-verification-receipt.json"
ATTESTATION=ARTIFACTS/"reviewer-attestation.json"
MANIFEST=ARTIFACTS/"p0-07-terminal-manifest.json"
SIDECAR=ARTIFACTS/"p0-07-terminal-manifest.json.sha256"
FINAL_NAMES=("revision-report.json","replay-report.json","stage1-handoff-fixture.json","machine-acceptance-decision.json","phase-0-acceptance-report.md")
MANIFEST_NAMES=("p0-07-reviewer-verification-receipt.json","reviewer-attestation.json",*FINAL_NAMES)


def _hash(payload:bytes)->str: return hashlib.sha256(payload).hexdigest()


def _atomic_copy_once(path:Path,payload:bytes)->None:
    temporary=path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if path.exists() or temporary.exists(): raise ValueError(f"copy-once target is not empty: {path}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        if path.exists(): raise ValueError(f"copy-once target appeared during write: {path}")
        os.replace(temporary,path)
    finally:
        if temporary.exists(): temporary.unlink()


def _validate_upstream(*,repo_root:Path|None=None,artifacts:Path|None=None)->tuple[dict[str,Any],bytes,bytes,bytes,bytes,bytes,dict[str,Any]]:
    repo_root=REPO if repo_root is None else repo_root.resolve(); artifacts=ARTIFACTS if artifacts is None else artifacts.resolve()
    bundle=artifacts/"p0-07-review-bundle"; reviewer_receipt=artifacts/"p0-07-reviewer-verification-receipt.json"; attestation_path=artifacts/"reviewer-attestation.json"
    validate_bundle(bundle,repo_root=repo_root,artifacts=artifacts,staging=artifacts/".p0-07-replay-staging")
    technical_bytes=(bundle/"technical-replay-report.json").read_bytes(); technical=load_json(bundle/"technical-replay-report.json")
    validate_schema_instance(technical,load_json(artifacts/"schemas"/"technical-replay-report.schema.json"))
    if technical_bytes!=canonical_line(technical): raise ValueError("technical report bytes are not canonical")
    reviewer_bytes=reviewer_receipt.read_bytes(); reviewer=load_json(reviewer_receipt)
    validate_schema_instance(reviewer,load_json(artifacts/"schemas"/"p0-07-reviewer-verification-receipt.schema.json"))
    if reviewer_bytes!=canonical_line(reviewer) or reviewer["outcome"]!="PASS": raise ValueError("reviewer verification receipt is not an exact PASS receipt")
    expected_bindings={"assignment_sha256":artifacts/"reviewer-assignment.json","review_bundle_root_manifest_sha256":bundle/"bundle-manifest.json","content_manifest_sha256":bundle/"content-manifest.json","execution_receipt_sha256":bundle/"execution-receipt.json","technical_report_sha256":bundle/"technical-replay-report.json"}
    for field,path in expected_bindings.items():
        if reviewer[field]!=sha256_file(path): raise ValueError(f"reviewer receipt binding mismatch: {field}")
    reviewer_snapshot_bindings={
        "candidate_input_manifest_sha256":artifacts/"p0-07-candidate"/INPUT_MANIFEST_NAME,
        "rebuilt_input_manifest_sha256":bundle/"worker"/"rebuilt"/INPUT_MANIFEST_NAME,
        "candidate_output_manifest_sha256":artifacts/"p0-07-candidate"/DERIVED_MANIFEST_NAME,
        "rebuilt_output_manifest_sha256":bundle/"worker"/"rebuilt"/DERIVED_MANIFEST_NAME,
    }
    for field,path in reviewer_snapshot_bindings.items():
        if reviewer[field]!=sha256_file(path): raise ValueError(f"reviewer snapshot binding mismatch: {field}")
    if reviewer["input_state_before_sha256"]!=reviewer["input_state_after_sha256"]:
        raise ValueError("reviewer receipt records input mutation during verification")
    candidate_revision=artifacts/"p0-07-candidate"/"derived"/"revision-report.json"
    rebuilt_revision=bundle/"worker"/"rebuilt"/"derived"/"revision-report.json"
    revision_bytes=candidate_revision.read_bytes()
    if revision_bytes!=rebuilt_revision.read_bytes(): raise ValueError("candidate and persistent rebuilt revision report bytes differ")
    revision_record={"path":"derived/revision-report.json","size":len(revision_bytes),"sha256":_hash(revision_bytes)}
    for manifest_path in (artifacts/"p0-07-candidate"/DERIVED_MANIFEST_NAME,bundle/"worker"/"rebuilt"/DERIVED_MANIFEST_NAME):
        if revision_record not in load_json(manifest_path)["files"]: raise ValueError("revision report is absent from a bound derived manifest")
    content_record={"path":"worker/rebuilt/derived/revision-report.json","size":len(revision_bytes),"sha256":_hash(revision_bytes)}
    if content_record not in load_json(bundle/"content-manifest.json")["files"]: raise ValueError("revision report is absent from review-bundle content closure")
    attestation_bytes=attestation_path.read_bytes(); attestation=load_json(attestation_path)
    validate_schema_instance(attestation,load_json(artifacts/"schemas"/"reviewer-attestation.schema.json"))
    if attestation_bytes!=canonical_line(attestation): raise ValueError("attestation bytes are not canonical")
    assignment=load_json(artifacts/"reviewer-assignment.json"); reviewers=assignment["reviewers"]
    if len(reviewers)!=1 or attestation["reviewer_id"]!=reviewers[0]["reviewer_id"]: raise ValueError("attestation identity differs from unique assignment")
    if attestation["identity_assurance"]!="declared_not_cryptographically_authenticated": raise ValueError("attestation overstates identity assurance")
    if attestation["conclusion"]!="PASS" or attestation["observed_launcher_exit_code"]!=0:
        raise ValueError("attestation does not declare a successful canonical replay")
    expected_signature=f"declared-agent-identity:{attestation['reviewer_id']}:{_hash(signature_payload(attestation))}"
    if attestation["signature"]!=expected_signature: raise ValueError("declared identity signature payload mismatch")
    if attestation["reviewer_verification_receipt_sha256"]!=sha256_file(reviewer_receipt): raise ValueError("attestation receipt binding mismatch")
    for field,path in (("assignment_sha256",artifacts/"reviewer-assignment.json"),("review_bundle_root_manifest_sha256",bundle/"bundle-manifest.json"),("execution_receipt_sha256",bundle/"execution-receipt.json"),("technical_report_sha256",bundle/"technical-replay-report.json")):
        if attestation[field]!=sha256_file(path): raise ValueError(f"attestation binding mismatch: {field}")
    if attestation["canonical_command"]!=CANONICAL_COMMAND or attestation["canonical_command_sha256"]!=_hash(CANONICAL_COMMAND.encode()): raise ValueError("attestation canonical command mismatch")
    if datetime.fromisoformat(attestation["signed_at_utc"].replace("Z","+00:00")) < datetime.fromisoformat(reviewer["completed_at_utc"].replace("Z","+00:00")): raise ValueError("attestation predates reviewer verification")
    execution_bytes=(bundle/"execution-receipt.json").read_bytes(); consumer_bytes=(bundle/"p0-07-stage1-consumer-receipt.json").read_bytes(); fixture_bytes=(bundle/"proposed-stage1-handoff-fixture.json").read_bytes()
    return technical,execution_bytes,consumer_bytes,fixture_bytes,revision_bytes,reviewer_bytes,attestation


def expected_terminal_bytes(*,repo_root:Path|None=None,artifacts:Path|None=None)->dict[str,bytes]:
    artifacts=ARTIFACTS if artifacts is None else artifacts.resolve()
    technical,execution_bytes,consumer_bytes,fixture_bytes,revision_bytes,reviewer_bytes,attestation=_validate_upstream(repo_root=repo_root,artifacts=artifacts)
    attestation_bytes=(artifacts/"reviewer-attestation.json").read_bytes()
    final=build_final_replay(technical,execution_bytes=execution_bytes,consumer_bytes=consumer_bytes,fixture_bytes=fixture_bytes,reviewer_receipt_bytes=reviewer_bytes,attestation_bytes=attestation_bytes)
    validate_schema_instance(final,load_json(artifacts/"schemas"/"replay-report.schema.json")); final_bytes=canonical_line(final)
    decision=build_machine_decision(final,final_bytes,fixture_bytes,attestation_bytes)
    validate_schema_instance(decision,load_json(artifacts/"schemas"/"machine-acceptance-decision.schema.json")); decision_bytes=canonical_line(decision)
    return {"revision-report.json":revision_bytes,"replay-report.json":final_bytes,"stage1-handoff-fixture.json":fixture_bytes,"machine-acceptance-decision.json":decision_bytes,"phase-0-acceptance-report.md":build_acceptance_markdown(decision)}


def _manifest(expected:dict[str,bytes],recorded_at:str,*,artifacts:Path|None=None)->dict[str,Any]:
    artifacts=ARTIFACTS if artifacts is None else artifacts.resolve(); bundle=artifacts/"p0-07-review-bundle"; reviewer_receipt=artifacts/"p0-07-reviewer-verification-receipt.json"; attestation=artifacts/"reviewer-attestation.json"
    payloads={"p0-07-reviewer-verification-receipt.json":reviewer_receipt.read_bytes(),"reviewer-attestation.json":attestation.read_bytes(),**expected}
    files=[{"path":name,"size":len(payloads[name]),"sha256":_hash(payloads[name])} for name in MANIFEST_NAMES]
    return {"schema_version":"2.0.0","artifact_type":"p0_07_terminal_manifest","contract_version":"1.3","recorded_at_utc":recorded_at,
            "review_bundle_root_manifest_sha256":sha256_file(bundle/"bundle-manifest.json"),"reviewer_verification_receipt_sha256":sha256_file(reviewer_receipt),"reviewer_attestation_sha256":sha256_file(attestation),
            "manifest_excludes":["p0-07-terminal-manifest.json","p0-07-terminal-manifest.json.sha256"],"commit_marker":"p0-07-terminal-manifest.json.sha256","files":files}


def verify_terminal(*,repo_root:Path|None=None,artifacts:Path|None=None,require_sidecar:bool=True)->None:
    repo_root=REPO if repo_root is None else repo_root.resolve(); artifacts=ARTIFACTS if artifacts is None else artifacts.resolve(); manifest_path=artifacts/MANIFEST.name; sidecar=artifacts/SIDECAR.name
    stale=[path for path in artifacts.iterdir() if path.name.startswith(".p0-07-terminal-") or (path.name.startswith(".") and path.name.endswith(".tmp") and "terminal" in path.name)]
    if stale: raise ValueError(f"stale terminal staging/temp paths exist: {[path.name for path in stale]}")
    expected=expected_terminal_bytes(repo_root=repo_root,artifacts=artifacts)
    for name,payload in expected.items():
        path=artifacts/name
        if not path.is_file() or path.read_bytes()!=payload: raise ValueError(f"terminal artifact differs from pure expected bytes: {name}")
    manifest=load_json(manifest_path); attestation=load_json(artifacts/"reviewer-attestation.json")
    expected_manifest=_manifest(expected,attestation["signed_at_utc"],artifacts=artifacts)
    if manifest!=expected_manifest or manifest_path.read_bytes()!=canonical_line(expected_manifest): raise ValueError("terminal manifest differs from exact expected closure")
    validate_schema_instance(manifest,load_json(artifacts/"schemas"/"p0-07-terminal-manifest.schema.json"))
    if require_sidecar:
        if not sidecar.is_file() or sidecar.read_bytes()!=(_hash(manifest_path.read_bytes())+"\n").encode("ascii"): raise ValueError("terminal commit marker is absent or invalid")


def finalize()->Path:
    terminal_paths=[ARTIFACTS/name for name in FINAL_NAMES]+[MANIFEST,SIDECAR]
    if any(path.exists() for path in terminal_paths): raise ValueError("terminal publication is copy-once and target is not empty")
    expected=expected_terminal_bytes(repo_root=REPO,artifacts=ARTIFACTS); attestation=load_json(ATTESTATION); manifest=_manifest(expected,attestation["signed_at_utc"],artifacts=ARTIFACTS); validate_schema_instance(manifest,load_json(ARTIFACTS/"schemas"/"p0-07-terminal-manifest.schema.json"))
    created:list[Path]=[]
    try:
        with tempfile.TemporaryDirectory(dir=ARTIFACTS,prefix=".p0-07-terminal-") as temporary:
            staging=Path(temporary)
            for name,payload in expected.items(): (staging/name).write_bytes(payload)
            (staging/MANIFEST.name).write_bytes(canonical_line(manifest))
            staged={**expected,MANIFEST.name:canonical_line(manifest)}
            for name,payload in staged.items():
                if (staging/name).read_bytes()!=payload:
                    raise ValueError(f"terminal staging bytes differ from pure expected bytes: {name}")
            for name in (*FINAL_NAMES,MANIFEST.name):
                target=ARTIFACTS/name
                if target.exists(): raise ValueError(f"terminal target appeared during publication: {name}")
                (staging/name).replace(target); created.append(target)
        verify_terminal(repo_root=REPO,artifacts=ARTIFACTS,require_sidecar=False)
        _atomic_copy_once(SIDECAR,(_hash(MANIFEST.read_bytes())+"\n").encode("ascii"))
        verify_terminal(repo_root=REPO,artifacts=ARTIFACTS,require_sidecar=True)
    except Exception:
        if SIDECAR.exists(): SIDECAR.unlink()
        for path in reversed(created):
            if path.exists(): path.unlink()
        raise
    return SIDECAR


def main()->int:
    if len(sys.argv)!=1: sys.stderr.write('{"status":"FAIL","error":"arguments are forbidden"}\n'); return 2
    try:
        path=finalize(); sys.stdout.buffer.write(canonical_line({"status":"PASS","commit_marker":path.relative_to(REPO).as_posix()})); return 0
    except Exception as exc:
        sys.stderr.buffer.write(canonical_line({"status":"FAIL","error":str(exc)})); return 2


if __name__=="__main__": raise SystemExit(main())
