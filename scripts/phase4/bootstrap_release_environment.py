#!/usr/bin/env python3
"""Create the immutable, wheel-only T15 Phase 4 execution environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical(value)); handle.flush(); os.fsync(handle.fileno())


def files(root: Path) -> list[dict[str, Any]]:
    return [{"path":p.relative_to(root).as_posix(),"bytes":p.stat().st_size,"sha256":sha(p)} for p in sorted(x for x in root.rglob("*") if x.is_file())]


def export_git(commit: str, destination: Path, paths: list[str]) -> None:
    with tempfile.TemporaryDirectory() as raw:
        archive = Path(raw) / "snapshot.tar"
        with archive.open("wb") as handle:
            subprocess.run(["git","archive","--format=tar",commit,"--",*paths],check=True,stdout=handle)
        subprocess.run(["tar","-xf",str(archive),"-C",str(destination)],check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prep-root", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--release-venv", required=True, type=Path)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--authority-commit", required=True)
    parser.add_argument("--required-ancestor", required=True)
    parser.add_argument("--implementation-commit", required=True)
    parser.add_argument("--actor-assignments", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if args.release_root.exists() or args.release_venv.exists():
        raise ValueError("release or release venv identity already exists")
    if subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip() != args.implementation_commit:
        raise ValueError("implementation commit does not equal clean HEAD")
    if subprocess.run(["git","diff","--quiet"],check=False).returncode or subprocess.run(["git","diff","--cached","--quiet"],check=False).returncode:
        raise ValueError("T15 requires a clean implementation worktree")
    for ancestor in (args.authority_commit, args.required_ancestor):
        if subprocess.run(["git","merge-base","--is-ancestor",ancestor,args.implementation_commit],check=False).returncode:
            raise ValueError("authority continuity failed")
    release = args.release_root.resolve(); release.mkdir(parents=True)
    for name in ("control","contracts","inputs","qualification-design","readiness","work-items/T15","manifest"):
        (release / name).mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.wheelhouse, release / "inputs/wheelhouse", dirs_exist_ok=False)
    shutil.copytree(args.prep_root, release / "inputs/preparation-evidence", dirs_exist_ok=False)
    export_git(args.implementation_commit, release, ["config/phase4","schemas/phase4","schemas/phase1/draw-record.schema.json","qualification-design","deploy/systemd-user","tests/phase4","scripts/phase4","scripts/phase4_independent"])
    shutil.copytree(release / "config/phase4", release / "contracts", dirs_exist_ok=True)
    shutil.copy2(release / "schemas/phase1/draw-record.schema.json", release / "contracts/phase1-draw-record.schema.json")
    power_design = args.prep_root / "qualification-design/power/qualification-design.json"
    if not power_design.is_file():
        raise ValueError("T13 signed qualification design is absent")
    shutil.copy2(power_design, release / "qualification-design/qualification-design.json")
    scripts_root = release / "inputs/execution-scripts"
    shutil.copytree(release / "scripts", scripts_root / "scripts")
    shutil.rmtree(release / "scripts")
    shutil.copy2(args.actor_assignments, release / "control/actor-assignments-formal.json")
    actor_records = args.actor_assignments.parent / "actor-records"
    if actor_records.is_dir():
        shutil.copytree(actor_records, release / "control/actor-records")
    venv.EnvBuilder(with_pip=True, clear=False).create(args.release_venv)
    python = args.release_venv / "bin/python"
    product = sorted((release / "inputs/wheelhouse").glob("autoresearch_lotte-*.whl"))
    if len(product) != 1:
        raise ValueError("wheelhouse must contain exactly one product wheel")
    completed = subprocess.run([str(python),"-m","pip","install","--no-index","--find-links",str(release / "inputs/wheelhouse"),"--require-hashes","-r",str(root / "requirements/phase4.lock")],capture_output=True,text=True,check=False)
    if completed.returncode:
        raise ValueError(f"offline dependency install failed: {completed.stderr}")
    completed = subprocess.run([str(python),"-m","pip","install","--no-index","--no-deps",str(product[0])],capture_output=True,text=True,check=False)
    if completed.returncode:
        raise ValueError(f"offline wheel install failed: {completed.stderr}")
    smoke_env = {**os.environ,"P4_PROJECT_ROOT":str(release)}; smoke_env.pop("PYTHONPATH",None)
    smoke = subprocess.run([str(python),"-m","lottery_system.phase4","--help"],cwd=release,env=smoke_env,capture_output=True,check=False)
    if smoke.returncode != 0:
        raise ValueError("installed wheel CLI smoke failed")
    compatibility = release / "artifacts/phase-4-prep" / args.prep_root.name
    for relative in ("control", "work-items/T10", "qualification-design/development"):
        source = args.prep_root / relative
        if source.exists():
            shutil.copytree(source, compatibility / relative, dirs_exist_ok=True)
    wheel_rows = files(release / "inputs/wheelhouse")
    script_rows = files(scripts_root)
    prep_rows = files(release / "inputs/preparation-evidence")
    environment = {"schema_version":"1.0.0","artifact_type":"phase4_execution_environment","authority_commit":args.authority_commit,"required_ancestor_commit":args.required_ancestor,"implementation_commit":args.implementation_commit,"release_id":release.name,"release_root":str(release),"release_python":str(python.resolve()),"python_version":platform.python_version(),"platform":platform.platform(),"wheelhouse_files":wheel_rows,"execution_script_files":script_rows,"preparation_evidence_file_count":len(prep_rows),"preparation_evidence_bytes":sum(row["bytes"] for row in prep_rows),"formal_terminal_count_at_freeze":0,"offline_install":True,"worktree_execution_allowed":False,"status":"PASS"}
    write_once(release / "control/execution-environment.json", environment)
    whitelist = {"schema_version":"1.0.0","artifact_type":"phase4_artifact_whitelist","pre_manifest_prefixes":["control/","contracts/","config/","schemas/","qualification-design/","inputs/","tests/","deploy/","qualification/","e2e/","readiness/","runs/","work-items/"],"post_manifest_prefixes":["replay/","validator/","review/","delivery/","acceptance/","manifest/replay-closure.json","manifest/validator-closure.json","manifest/review-closure.json","manifest/delivery-closure.json"],"status":"FROZEN"}
    write_once(release / "control/artifact-whitelist.json", whitelist)
    receipt = {"schema_version":"1.0.0","artifact_type":"phase4_work_item_receipt","task_id":"T15","release_id":release.name,"status":"PASS","terminal":"T15_FORMAL_RELEASE_FROZEN","process_exit_code":0,"implementation_commit":args.implementation_commit,"execution_environment_sha256":sha(release / "control/execution-environment.json"),"wheelhouse_file_count":len(wheel_rows),"preparation_evidence_file_count":len(prep_rows),"formal_terminal_count":0}
    write_once(args.output / "receipt.json", receipt)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
