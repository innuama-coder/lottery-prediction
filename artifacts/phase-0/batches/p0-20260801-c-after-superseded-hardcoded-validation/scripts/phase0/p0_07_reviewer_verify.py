"""Fixed-path read-only reviewer technical verification; makes no identity claim."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Any

from p0_07_closeout import (
    DERIVED_MANIFEST_NAME, INPUT_MANIFEST_NAME, _candidate_bytes, _utc_text, prepare,
    validate_candidate_snapshot,
)
from p0_07_replay_driver import validate_bundle
from phase0lib import canonical_json_bytes, load_json, sha256_file, validate_schema_instance


REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / "artifacts" / "phase-0"
CANDIDATE = ARTIFACTS / "p0-07-candidate"
BUNDLE = ARTIFACTS / "p0-07-review-bundle"
OUTPUT = ARTIFACTS / "p0-07-reviewer-verification-receipt.json"


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


def _state(input_manifest: dict[str, Any]) -> tuple[dict[str, tuple[str, int]], str]:
    records = {}
    serial = []
    for item in input_manifest["files"]:
        path = REPO / item["path"]
        record = (sha256_file(path), path.stat().st_mtime_ns)
        records[item["path"]] = record
        serial.append({"path": item["path"], "sha256": record[0], "mtime_ns": record[1]})
    digest = hashlib.sha256(canonical_json_bytes(serial)).hexdigest()
    return records, digest


def verify(*, utcnow_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Path:
    if OUTPUT.exists():
        raise ValueError("reviewer verification receipt is copy-once")
    started = utcnow_fn()
    if started.tzinfo is None:
        raise ValueError("reviewer verification clock must be timezone-aware")
    input_manifest, snapshot_at = validate_candidate_snapshot(REPO, ARTIFACTS, CANDIDATE, now=started)
    before, before_digest = _state(input_manifest)
    validate_bundle(BUNDLE)
    persistent_rebuilt = BUNDLE / "worker" / "rebuilt"
    if _candidate_bytes(CANDIDATE) != _candidate_bytes(persistent_rebuilt):
        raise ValueError("persistent rebuilt bytes differ from canonical candidate")
    with tempfile.TemporaryDirectory(dir=ARTIFACTS, prefix=".p0-07-reviewer-clean-") as temporary:
        rebuilt = Path(temporary) / "rebuilt"
        prepare(REPO, ARTIFACTS, rebuilt, utcnow_fn=lambda: snapshot_at)
        if _candidate_bytes(rebuilt) != _candidate_bytes(CANDIDATE):
            raise ValueError("reviewer clean rebuild differs from canonical candidate")
    after, after_digest = _state(input_manifest)
    if before != after:
        raise ValueError("review inputs changed during reviewer verification")
    completed = utcnow_fn()
    if completed.tzinfo is None or completed < started:
        raise ValueError("reviewer verification completion clock invalid")
    receipt = {
        "schema_version":"1.0.0","artifact_type":"p0_07_reviewer_verification_receipt","contract_version":"1.3",
        "verifier_role":"independent_technical_verifier_no_identity_claim",
        "started_at_utc":_utc_text(started),"completed_at_utc":_utc_text(completed),
        "assignment_sha256":sha256_file(ARTIFACTS/"reviewer-assignment.json"),
        "review_bundle_root_manifest_sha256":sha256_file(BUNDLE/"bundle-manifest.json"),
        "content_manifest_sha256":sha256_file(BUNDLE/"content-manifest.json"),
        "execution_receipt_sha256":sha256_file(BUNDLE/"execution-receipt.json"),
        "technical_report_sha256":sha256_file(BUNDLE/"technical-replay-report.json"),
        "candidate_input_manifest_sha256":sha256_file(CANDIDATE/INPUT_MANIFEST_NAME),
        "rebuilt_input_manifest_sha256":sha256_file(persistent_rebuilt/INPUT_MANIFEST_NAME),
        "candidate_output_manifest_sha256":sha256_file(CANDIDATE/DERIVED_MANIFEST_NAME),
        "rebuilt_output_manifest_sha256":sha256_file(persistent_rebuilt/DERIVED_MANIFEST_NAME),
        "input_state_before_sha256":before_digest,"input_state_after_sha256":after_digest,
        "strict_bundle_validated":True,"clean_rebuild_exact_match":True,"evidence_unmodified":True,"outcome":"PASS",
    }
    validate_schema_instance(receipt, load_json(ARTIFACTS/"schemas"/"p0-07-reviewer-verification-receipt.schema.json"))
    _atomic_copy_once(OUTPUT,canonical_json_bytes(receipt)+b"\n")
    return OUTPUT


def main() -> int:
    if len(sys.argv) != 1:
        sys.stderr.write('{"status":"FAIL","error":"arguments are forbidden"}\n'); return 2
    try:
        path=verify(); sys.stdout.buffer.write(canonical_json_bytes({"status":"PASS","receipt":path.relative_to(REPO).as_posix()})+b"\n"); return 0
    except Exception as exc:
        sys.stderr.buffer.write(canonical_json_bytes({"status":"FAIL","error":str(exc)})+b"\n"); return 2


if __name__ == "__main__": raise SystemExit(main())
