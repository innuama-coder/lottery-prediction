"""Fixed-path declared reviewer attestation, explicitly not cryptographic authentication."""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from p0_07_closeout import _utc_text
from p0_07_replay_driver import CANONICAL_COMMAND
from phase0lib import canonical_json_bytes, load_json, sha256_file, validate_schema_instance


REPO=Path(__file__).resolve().parents[2]
ARTIFACTS=REPO/"artifacts"/"phase-0"
BUNDLE=ARTIFACTS/"p0-07-review-bundle"
RECEIPT=ARTIFACTS/"p0-07-reviewer-verification-receipt.json"
OUTPUT=ARTIFACTS/"reviewer-attestation.json"


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


def attest(*, utcnow_fn:Callable[[],datetime]=lambda:datetime.now(timezone.utc))->Path:
    if OUTPUT.exists(): raise ValueError("reviewer attestation is copy-once")
    receipt=load_json(RECEIPT)
    validate_schema_instance(receipt,load_json(ARTIFACTS/"schemas"/"p0-07-reviewer-verification-receipt.schema.json"))
    assignment=load_json(ARTIFACTS/"reviewer-assignment.json")
    reviewers=assignment.get("reviewers",[])
    if len(reviewers)!=1: raise ValueError("attestation requires exactly one assigned reviewer")
    reviewer=reviewers[0]
    if reviewer.get("authored_parser") is not False or reviewer.get("will_modify_evidence") is not False:
        raise ValueError("assigned reviewer independence declaration is not acceptable")
    bindings={
        "assignment_sha256":ARTIFACTS/"reviewer-assignment.json",
        "review_bundle_root_manifest_sha256":BUNDLE/"bundle-manifest.json",
        "execution_receipt_sha256":BUNDLE/"execution-receipt.json",
        "technical_report_sha256":BUNDLE/"technical-replay-report.json",
    }
    for field,path in bindings.items():
        if receipt[field]!=sha256_file(path): raise ValueError(f"reviewer verification binding mismatch: {field}")
    execution=load_json(BUNDLE/"execution-receipt.json")
    if execution["canonical_command"]!=CANONICAL_COMMAND: raise ValueError("execution did not use canonical replay command")
    signed_at=utcnow_fn()
    if signed_at.tzinfo is None: raise ValueError("attestation clock must be timezone-aware")
    value={
        "schema_version":"2.0.0","artifact_type":"reviewer_attestation","contract_version":"1.3",
        "reviewer_id":reviewer["reviewer_id"],"identity_assurance":"declared_not_cryptographically_authenticated",
        "assignment_sha256":sha256_file(ARTIFACTS/"reviewer-assignment.json"),
        "reviewer_verification_receipt_sha256":sha256_file(RECEIPT),
        "review_bundle_root_manifest_sha256":sha256_file(BUNDLE/"bundle-manifest.json"),
        "execution_receipt_sha256":sha256_file(BUNDLE/"execution-receipt.json"),
        "technical_report_sha256":sha256_file(BUNDLE/"technical-replay-report.json"),
        "canonical_command":CANONICAL_COMMAND,"canonical_command_sha256":hashlib.sha256(CANONICAL_COMMAND.encode()).hexdigest(),
        "observed_launcher_exit_code":0,"evidence_unmodified_declared":True,"independence_declared":True,
        "conclusion":"PASS","signed_at_utc":_utc_text(signed_at),"signature_type":"declared_agent_identity_v1",
    }
    signature_hash=hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    value["signature"]=f"declared-agent-identity:{reviewer['reviewer_id']}:{signature_hash}"
    validate_schema_instance(value,load_json(ARTIFACTS/"schemas"/"reviewer-attestation.schema.json"))
    _atomic_copy_once(OUTPUT,canonical_json_bytes(value)+b"\n")
    return OUTPUT


def main()->int:
    if len(sys.argv)!=1: sys.stderr.write('{"status":"FAIL","error":"arguments are forbidden"}\n'); return 2
    try:
        path=attest(); sys.stdout.buffer.write(canonical_json_bytes({"status":"PASS","attestation":path.relative_to(REPO).as_posix()})+b"\n"); return 0
    except Exception as exc:
        sys.stderr.buffer.write(canonical_json_bytes({"status":"FAIL","error":str(exc)})+b"\n"); return 2


if __name__=="__main__": raise SystemExit(main())
