"""Shared read-only helpers for the Phase 3 formal workflow.

Formal command implementations live in :mod:`lottery_research.phase3.formal`.
This module deliberately contains no synthetic substitute for ``run``,
``evaluate``, ``replay``, ``verify-e2e``, or ``accept``.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any

from .prerun_contract import FROZEN_INPUTS, validate_prerun_contract
from .serialization import load_json, sha256_file


HOLD_TERMINAL = "HOLD"


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _lock_versions(root: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in (root / "requirements/phase3.lock").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            package, version = line.split("==", 1)
            expected[package] = version
    return expected


def validate_offline_dependencies(root: Path) -> dict[str, Any]:
    expected = _lock_versions(root)
    observed: dict[str, str | None] = {}
    for package in expected:
        try:
            observed[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            observed[package] = None
    mismatches = {package: {"expected": expected[package], "observed": observed[package]} for package in expected if observed[package] != expected[package]}
    return {"expected": expected, "observed": observed, "mismatches": mismatches, "status": "PASS" if not mismatches else "HOLD"}


def validate_frozen_inputs(root: Path) -> dict[str, Any]:
    receipt = validate_prerun_contract(root)
    release = "P2.1-R00-61a99a2c3732-i07-r02"
    bundle = root / "artifacts/phase-2.1" / release
    recursive = load_json(bundle / "acceptance/manifest.json")
    acceptance = load_json(bundle / "acceptance/acceptance.json")
    if recursive["release_id"] != release or recursive["file_count"] != 56:
        raise ValueError("Phase 2.1 recursive manifest identity mismatch")
    if (acceptance["status"], acceptance["delivery_status"], acceptance["scientific_classification"], acceptance["blocking_findings"]) != ("PASS", "GO", "indeterminate", 0):
        raise ValueError("Phase 2.1 acceptance boundary mismatch")
    for row in recursive["files"]:
        path = bundle / row["path"]
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise ValueError(f"Phase 2.1 recursive manifest mismatch: {row['path']}")
    return {"prerun": receipt, "frozen_input_count": len(FROZEN_INPUTS), "phase2_1_recursive_file_count": len(recursive["files"]), "status": "PASS"}
