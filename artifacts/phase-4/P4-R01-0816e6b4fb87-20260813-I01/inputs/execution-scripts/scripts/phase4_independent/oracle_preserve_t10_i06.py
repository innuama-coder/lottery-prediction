from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ATTEMPT = Path("artifacts/phase-4-prep/p4-prep-controller-issued-i01/work-items/T10/attempts/T10-I06")
TARGET = ATTEMPT / "preserved-shared-outputs"
RECEIPT = ATTEMPT / "receipt.json"
HOLD = ATTEMPT / "independent-validation.json"
FILES = (
    Path("scripts/phase4_independent/check_qualification_feasibility.py"),
    Path("scripts/phase4_independent/oracle_finalize_t10.py"),
    Path("scripts/phase4_independent/oracle_mutation_audit.py"),
    Path("tests/phase4_oracle/test_feasibility.py"),
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    receipt = json.loads(RECEIPT.read_text())
    expected = {Path(row["path"]): row for row in receipt["outputs"]}
    rows = []
    payloads: list[tuple[Path, bytes]] = []
    for source in FILES:
        row = expected.get(source)
        data = source.read_bytes()
        if row is None or _sha(data) != row["sha256"] or len(data) != row["bytes"]:
            raise SystemExit(f"I06 receipt binding mismatch: {source}")
        destination = TARGET / source
        if destination.exists():
            raise SystemExit(f"preservation target already exists: {destination}")
        payloads.append((destination, data))
        rows.append({
            "source_path": str(source), "preserved_path": str(destination),
            "sha256": row["sha256"], "bytes": row["bytes"],
            "producer_actor_id": row["producer_actor_id"], "task_id": row["task_id"],
            "session_id": row["session_id"], "source_commit": row["source_commit"], "role": row["role"],
        })
    if _sha(RECEIPT.read_bytes()) != "1b72368a065c48e36bb512e32e71ff8efda9e52fd2ad16b1b876ebf519e3c2c1":
        raise SystemExit("I06 receipt hash mismatch")
    if _sha(HOLD.read_bytes()) != "310f788848a203b068587854de79554e9ee3ceec47b8b36b5e637acab8dff181":
        raise SystemExit("I06 HOLD hash mismatch")
    mapping = {
        "schema_version": "1.0.0", "artifact_type": "phase4_preserved_shared_outputs",
        "attempt_id": "T10-I06", "reason": "preservation_before_T10-I07_analytic_fail_closed_fix",
        "receipt_path": str(RECEIPT), "receipt_sha256": _sha(RECEIPT.read_bytes()),
        "hold_path": str(HOLD), "hold_sha256": _sha(HOLD.read_bytes()),
        "files": rows, "status": "PRESERVED",
    }
    map_data = _canonical(mapping)
    if args.write:
        for destination, data in payloads:
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        map_path = TARGET / "preservation-map.json"
        descriptor = os.open(map_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(map_data)
            handle.flush()
            os.fsync(handle.fileno())
    print(json.dumps({"status": "PASS", "mode": "write" if args.write else "dry-run", "files": len(rows), "map_sha256": _sha(map_data)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
