from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
from typing import Any

from oracle_math import canonical_bytes, sha256_file


FORBIDDEN_PREFIXES = ("lottery_system", "src.lottery_system")


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Phase 4 oracle imports without importing oracle targets")
    parser.add_argument("--scripts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite immutable audit output")
    targets = sorted({*args.scripts.glob("oracle_*.py"), args.scripts / "run_known_answers.py", args.scripts / "check_qualification_feasibility.py"})
    findings = []
    rows = []
    for path in targets:
        if not path.is_file():
            findings.append({"path": str(path), "reason": "missing"})
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        forbidden = [name for name in imports if name.startswith(FORBIDDEN_PREFIXES)]
        if forbidden:
            findings.append({"path": str(path), "reason": "forbidden_product_import", "imports": forbidden})
        rows.append({"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size, "imports": sorted(imports)})
    report = {
        "schema_version": "1.0.0",
        "artifact_type": "phase4_independent_oracle_import_audit",
        "audited_files": rows,
        "product_import_count": sum(1 for finding in findings if finding["reason"] == "forbidden_product_import"),
        "findings": findings,
        "status": "PASS" if not findings else "HOLD",
    }
    _write_new(args.output, report)
    print(json.dumps({"status": report["status"], "files": len(rows), "findings": len(findings)}, sort_keys=True))
    return 0 if not findings else 20


if __name__ == "__main__":
    raise SystemExit(main())
