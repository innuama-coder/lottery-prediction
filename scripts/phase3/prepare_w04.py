from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lottery_research.phase3.formal import run_component_benchmarks
from lottery_research.phase3.serialization import canonical_json_bytes, sha256_file, write_new_json


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prep-root", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root, prep = args.project_root.resolve(), args.prep_root.resolve()
    wheelhouse = prep / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=False)
    completed = run([sys.executable, "-m", "pip", "wheel", "--wheel-dir", str(wheelhouse), "-r", "requirements/phase3.lock"], root)
    (prep / "logs").mkdir(parents=True, exist_ok=True)
    (prep / "logs/wheel-build.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (prep / "logs/wheel-build.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode:
        return completed.returncode
    wheels = [{"filename": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size} for path in sorted(wheelhouse.glob("*.whl"))]
    if not wheels:
        raise ValueError("wheelhouse is empty")
    manifest = {
        "schema_version": "3.0.0", "artifact_type": "phase3_wheelhouse_manifest",
        "lock_path": "requirements/phase3.lock", "lock_sha256": sha256_file(root / "requirements/phase3.lock"),
        "build_command": [sys.executable, "-m", "pip", "wheel", "--wheel-dir", str(wheelhouse), "-r", "requirements/phase3.lock"],
        "wheels": wheels, "inventory_sha256": hashlib.sha256(canonical_json_bytes(wheels)).hexdigest(),
    }
    write_new_json(prep / "wheelhouse-manifest.json", manifest)
    temporary = Path(tempfile.mkdtemp(prefix="phase3-offline-rebuild-"))
    try:
        create = run([sys.executable, "-m", "venv", str(temporary / "venv")], root)
        if create.returncode:
            raise RuntimeError(create.stderr)
        python = temporary / "venv/bin/python"
        install = run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), "-r", "requirements/phase3.lock"], root)
        editable = run([str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), "--no-deps", "--no-build-isolation", "-e", "."], root)
        smoke = run([str(python), "-c", "from lottery_research.phase3.probability import FixedCardinalityDistribution as D; assert D.uniform(5,2).normalization_audit()==1.0"], root)
        (prep / "logs/offline-install.stdout.log").write_text(install.stdout + editable.stdout + smoke.stdout, encoding="utf-8")
        (prep / "logs/offline-install.stderr.log").write_text(install.stderr + editable.stderr + smoke.stderr, encoding="utf-8")
        status = "PASS" if install.returncode == editable.returncode == smoke.returncode == 0 else "HOLD"
        receipt = {
            "schema_version": "3.0.0", "artifact_type": "phase3_offline_rebuild_receipt", "status": status,
            "network_used_during_rebuild": False,
            "install_command": [str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), "-r", "requirements/phase3.lock"],
            "editable_command": [str(python), "-m", "pip", "install", "--no-index", "--find-links", str(wheelhouse), "--no-deps", "--no-build-isolation", "-e", "."],
            "exit_codes": {"create_venv": create.returncode, "locked_install": install.returncode, "editable_install": editable.returncode, "smoke": smoke.returncode},
            "wheelhouse_manifest_sha256": sha256_file(prep / "wheelhouse-manifest.json"),
        }
        write_new_json(prep / "offline-rebuild-receipt.json", receipt)
        if status != "PASS":
            return 3
    finally:
        shutil.rmtree(temporary)
    run_component_benchmarks(root, prep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
