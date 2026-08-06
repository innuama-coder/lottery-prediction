"""Resource observation for Phase 2.1.

The snapshot deliberately has no capacity thresholds.  The task initiator owns
capacity sizing for an approved workload; an executor records facts and real
benchmark/runtime failures only.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .serialization import sha256


def _memory_bytes() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemTotal is unavailable")


def resource_facts(root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(root)
    return {
        "policy": "facts_only_no_generic_capacity_or_architecture_thresholds",
        "capacity_owner": "task_initiator",
        "error_rule": "classify only reproducible failures from an actual command, benchmark, wheelhouse operation, test, or formal run",
        "system": platform.system(),
        "architecture": platform.machine(),
        "logical_cpu_count": os.cpu_count() or 1,
        "total_memory_bytes": _memory_bytes(),
        "available_disk_bytes": usage.free,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
    }


def locked_requirements(lock_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            name, version = line.split("==", 1)
            result[name] = version
    return result


def dependency_facts(lock_path: Path) -> dict[str, Any]:
    installed = {}
    for name, expected in locked_requirements(lock_path).items():
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"installed {name}=={actual}, lock requires {expected}")
        installed[name] = actual
    return {"lock_sha256": sha256(lock_path), "installed": installed}


def wheelhouse_facts(lock_path: Path, wheelhouse: Path) -> dict[str, Any]:
    if not wheelhouse.is_dir():
        raise RuntimeError(f"wheelhouse does not exist: {wheelhouse}")
    wheels = sorted(wheelhouse.glob("*.whl"))
    normalized = {path.name.lower().replace("-", "_"): path for path in wheels}
    missing = []
    for name, version in locked_requirements(lock_path).items():
        prefix = f"{name.lower().replace('-', '_')}_{version.lower().replace('-', '_')}"
        if not any(key.startswith(prefix) for key in normalized):
            missing.append(f"{name}=={version}")
    if missing:
        raise RuntimeError(f"wheelhouse is incomplete: {missing}")
    return {
        "path": wheelhouse.resolve().as_posix(),
        "wheel_count": len(wheels),
        "missing_locked_requirements": [],
        "wheels": [{"filename": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)} for path in wheels],
    }
