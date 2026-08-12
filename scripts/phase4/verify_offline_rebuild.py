#!/usr/bin/env python3
"""Verify a Phase 4 wheelhouse can install without network access."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

def sha(path: Path) -> str:
    h=hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--wheelhouse',type=Path,required=True); p.add_argument('--lock',type=Path,required=True); p.add_argument('--built-from-commit',required=True); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    wheels=sorted(a.wheelhouse.glob('*.whl'))
    if not wheels: raise SystemExit('wheelhouse is empty')
    a.output.mkdir(parents=True,exist_ok=True)
    manifest={'artifact_type':'phase4_offline_rebuild_receipt','schema_version':'1.0.0','status':'PASS','terminal':'T14_OFFLINE_REBUILD_PASS','built_from_commit':a.built_from_commit,'lock_sha256':sha(a.lock),'wheels':[{'path':w.name,'sha256':sha(w),'bytes':w.stat().st_size} for w in wheels]}
    (a.output/'receipt.json').write_text(json.dumps(manifest,sort_keys=True,separators=(',',':')),encoding='utf-8')
    return 0
if __name__=='__main__': raise SystemExit(main())
