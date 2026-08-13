#!/usr/bin/env python3
"""Independently verify the T14 product wheel and offline runtime closure."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""): digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--wheelhouse",type=Path,required=True); parser.add_argument("--lock",type=Path,required=True); parser.add_argument("--built-from-commit",required=True); parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    if subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()!=args.built_from_commit:
        raise ValueError("built-from commit is not HEAD")
    product=sorted(args.wheelhouse.glob("autoresearch_lotte-*.whl"))
    if len(product)!=1: raise ValueError("exactly one product wheel is required")
    source_rows=[]
    for relative in subprocess.check_output(["git","ls-tree","-r","--name-only",args.built_from_commit,"--","src/lottery_system/phase4"],text=True).splitlines():
        if not relative.endswith(".py"): continue
        content=subprocess.check_output(["git","show",f"{args.built_from_commit}:{relative}"])
        source_rows.append({"path":relative,"sha256":hashlib.sha256(content).hexdigest(),"bytes":len(content)})
    with zipfile.ZipFile(product[0]) as archive:
        names=set(archive.namelist())
        for row in source_rows:
            wheel_name=row["path"].removeprefix("src/")
            if wheel_name not in names or hashlib.sha256(archive.read(wheel_name)).hexdigest()!=row["sha256"]:
                raise ValueError(f"wheel source differs from Git object: {row['path']}")
    with tempfile.TemporaryDirectory() as raw:
        environment=Path(raw)/"venv"; venv.EnvBuilder(with_pip=True).create(environment); python=environment/"bin/python"
        install=subprocess.run([str(python),"-m","pip","install","--no-index","--find-links",str(args.wheelhouse),"--require-hashes","-r",str(args.lock)],capture_output=True,text=True,check=False)
        if install.returncode: raise ValueError(f"offline dependency install failed: {install.stderr}")
        install_product=subprocess.run([str(python),"-m","pip","install","--no-index","--no-deps",str(product[0])],capture_output=True,text=True,check=False)
        if install_product.returncode: raise ValueError("offline product install failed")
        env={**os.environ,"P4_PROJECT_ROOT":str(Path.cwd().resolve())}; env.pop("PYTHONPATH",None)
        smoke=subprocess.run(
            [str(python),"-m","lottery_system.phase4","--help"],
            env=env,capture_output=True,text=True,check=False,
        )
        if smoke.returncode:
            raise ValueError(
                "installed product CLI smoke failed "
                f"(exit={smoke.returncode}, stdout={smoke.stdout!r}, stderr={smoke.stderr!r})"
            )
        record_files=sorted(environment.rglob("*.dist-info/RECORD")); record_hash=hashlib.sha256(b"".join(path.read_bytes() for path in record_files)).hexdigest()
    wheels=[{"path":path.name,"sha256":sha(path),"bytes":path.stat().st_size} for path in sorted(args.wheelhouse.glob("*.whl"))]
    receipt={"artifact_type":"phase4_offline_rebuild_receipt","schema_version":"1.0.0","task_id":"T14","status":"PASS","terminal":"T14_OFFLINE_REBUILD_PASS","process_exit_code":0,"built_from_commit":args.built_from_commit,"lock_sha256":sha(args.lock),"product_wheel_sha256":sha(product[0]),"wheels":wheels,"source_files":source_rows,"distribution_record_tree_sha256":record_hash,"offline_install":True,"cli_smoke_exit_code":0}
    args.output.mkdir(parents=True,exist_ok=False); (args.output/"receipt.json").write_bytes(canonical(receipt)); print(json.dumps(receipt,sort_keys=True,separators=(",",":"))); return 0


if __name__=="__main__": raise SystemExit(main())
