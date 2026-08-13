from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .serialization import load_json


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_once(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(payload)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())


def file_row(root: Path, path: Path, *, role: str, producer: dict[str, Any]) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    return {"path":relative,"role":role,"sha256":sha256_file(path),"bytes":path.stat().st_size,"parents":[],"producer_provenance":{**producer,"path":relative}}


def actor_for(assignments: Path, role: str) -> dict[str, Any]:
    payload = load_json(assignments, reject_floats=True)
    matches = [row for row in payload["assignments"] if role in row["roles"]]
    if len(matches) != 1:
        raise ValueError(f"expected one actor for {role}")
    return matches[0]


def provenance(actor: dict[str, Any], role: str, task: str, commit: str) -> dict[str, Any]:
    return {"producer_actor_id":actor["actor_id"],"task_id":task,"session_id":actor["session_id"],"source_commit":commit,"role":role}


POST_PREFIXES = ("replay/", "validator/", "review/", "delivery/", "acceptance/", "manifest/replay-", "manifest/validator-", "manifest/review-", "manifest/delivery-")


def evidence_files(release_root: Path) -> Iterable[Path]:
    for path in sorted(item for item in release_root.rglob("*") if item.is_file()):
        relative = path.relative_to(release_root).as_posix()
        if relative == "manifest/evidence-manifest.json" or relative.startswith(POST_PREFIXES):
            continue
        yield path


def assemble_evidence(release_root: Path, assignments: Path, implementation_commit: str) -> dict[str, Any]:
    actor = actor_for(assignments, "release_controller")
    producer = provenance(actor, "release_controller", "T19", implementation_commit)
    rows = [file_row(release_root, path, role="formal_evidence", producer=producer) for path in evidence_files(release_root)]
    required = ("contracts/", "inputs/", "qualification/", "e2e/", "readiness/", "work-items/")
    coverage = {prefix.rstrip("/"):any(row["path"].startswith(prefix) for row in rows) for prefix in required}
    if not all(coverage.values()):
        raise ValueError(f"formal evidence coverage incomplete: {coverage}")
    manifest = {"schema_version":"1.0.0","artifact_type":"phase4_evidence_manifest","release_id":release_root.name,"generated_at_utc":utc_now(),"implementation_commit":implementation_commit,"files":rows,"file_count":len(rows),"total_bytes":sum(row["bytes"] for row in rows),"coverage":coverage,"status":"PASS","terminal":"T19_EVIDENCE_MANIFEST_PASS","producer_provenance":{**producer,"path":"manifest/evidence-manifest.json"}}
    write_once(release_root / "manifest/evidence-manifest.json", manifest)
    return manifest


def verify_manifest(release_root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path, reject_floats=True)
    seen: set[str] = set()
    for row in manifest["files"]:
        relative = row["path"]
        if relative in seen or relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("manifest path set is unsafe or duplicated")
        path = release_root / relative
        if not path.is_file() or path.stat().st_size != row["bytes"] or sha256_file(path) != row["sha256"]:
            raise ValueError(f"manifest mismatch: {relative}")
        seen.add(relative)
    if len(seen) != manifest["file_count"] or sum((release_root / p).stat().st_size for p in seen) != manifest["total_bytes"]:
        raise ValueError("manifest aggregate mismatch")
    return manifest


def closure(release_root: Path, name: str, parent_path: Path, new_files: list[Path], producer: dict[str, Any]) -> dict[str, Any]:
    parent_sha = sha256_file(parent_path)
    rows = [file_row(release_root, path, role=name, producer=producer) for path in sorted(new_files)]
    payload = {"schema_version":"1.0.0","artifact_type":f"phase4_{name}_closure","release_id":release_root.name,"parent_path":parent_path.relative_to(release_root).as_posix(),"parent_sha256":parent_sha,"files":rows,"status":"PASS","producer_provenance":{**producer,"path":f"manifest/{name}-closure.json"}}
    write_once(release_root / f"manifest/{name}-closure.json", payload)
    return payload
