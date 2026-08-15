#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, os, platform, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--release",type=Path,required=True); parser.add_argument("--attempt-id",required=True); parser.add_argument("command",nargs=argparse.REMAINDER); args=parser.parse_args()
    release=args.release.resolve(); release.relative_to(ROOT.resolve())
    command=args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command: raise SystemExit("missing command")
    process=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,env={**os.environ,"PYTHONPATH":"src"})
    receipt={"artifact_type":"phase4_acceptance_command_receipt","attempt_id":args.attempt_id,"command":command,"exit_code":process.returncode,"interpreter_realpath":str(Path(sys.executable).resolve()),"python_version":platform.python_version(),"requirements_lock_sha256":sha(ROOT/"requirements/phase4.lock"),"stdout":process.stdout,"stderr":process.stderr,"status":"PASS" if process.returncode==0 else "HOLD"}
    output=release/f"validation/attempts/{args.attempt_id}/receipt.json"; output.parent.mkdir(parents=True,exist_ok=False); output.write_text(json.dumps(receipt,sort_keys=True,separators=(",",":"))+"\n")
    print(json.dumps({"attempt_id":args.attempt_id,"exit_code":process.returncode,"receipt":str(output),"status":receipt["status"]},sort_keys=True))
    return process.returncode
if __name__=="__main__": raise SystemExit(main())
