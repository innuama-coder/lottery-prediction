"""Build a reproducible Phase 2 environment identity from a benchmark result."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .input_validation import sha256
from .schema import validate_payload
from .serialization import canonical_json_bytes


def _bundle_files(root: Path) -> list[Path]:
    files = list((root / "src" / "lottery_research" / "phase2").glob("*.py"))
    files.extend((root / "schemas" / "phase2").glob("*.json"))
    files.append(root / "pyproject.toml")
    return sorted(path for path in files if path.is_file())


def source_schema_bundle(root: Path) -> dict[str, Any]:
    entries = [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)}
        for path in _bundle_files(root)
    ]
    digest = hashlib.sha256(canonical_json_bytes(entries)).hexdigest()
    return {
        "algorithm": "sha256",
        "profile": "canonical JSON array of sorted {path,sha256} entries with one trailing LF",
        "file_count": len(entries),
        "sha256": digest,
    }


def _locked_packages(lock_path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==", 1)
        packages[name] = version
    return packages


def installed_packages_for_lock(lock_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, expected in _locked_packages(lock_path).items():
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"installed {name}=={actual}, lock requires {expected}")
        result[name] = actual
    return result


def build_environment_lock(root: Path, benchmark: dict[str, Any], *, created_at_utc: str | None = None) -> dict[str, Any]:
    lock_path = root / "requirements" / "phase2.lock"
    start_path = root / "artifacts" / "phase-2" / "readiness" / "p2-01-start-authorization.json"
    result = {
        "schema_version": "1.0.0",
        "artifact_type": "phase2_environment_lock",
        "created_at_utc": created_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "python": {"version": platform.python_version(), "implementation": platform.python_implementation(), "executable": sys.executable},
        "platform": {"system": platform.system(), "release": platform.release(), "version": platform.version(), "machine": platform.machine()},
        "hardware": {"logical_processors": os.cpu_count() or 1, "processor": platform.processor() or "not_reported_by_platform"},
        "dependency_lock": {"path": lock_path.relative_to(root).as_posix(), "sha256": sha256(lock_path)},
        "installed_packages": installed_packages_for_lock(lock_path),
        "p2_01_start_authorization": {"path": start_path.relative_to(root).as_posix(), "sha256": sha256(start_path)},
        "source_schema_bundle": source_schema_bundle(root),
        "benchmark": benchmark,
    }
    validate_payload("environment_lock", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--benchmark-json", type=Path, required=True)
    args = parser.parse_args()
    benchmark = json.loads(args.benchmark_json.read_text(encoding="utf-8"))
    result = build_environment_lock(args.project_root.resolve(), benchmark)
    sys.stdout.buffer.write(canonical_json_bytes(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
