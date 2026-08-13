#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os
from datetime import datetime, timezone
from pathlib import Path
def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument("--stream",type=Path,required=True);parser.add_argument("--task",required=True);parser.add_argument("--status",required=True);parser.add_argument("--message",required=True);args=parser.parse_args()
    args.stream.parent.mkdir(parents=True,exist_ok=True)
    row={"at_utc":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"task_id":args.task,"status":args.status,"message":args.message}
    descriptor=os.open(args.stream,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(descriptor,"ab") as handle: handle.write(json.dumps(row,sort_keys=True,separators=(",",":")).encode()+b"\n");handle.flush();os.fsync(handle.fileno())
    return 0
if __name__=="__main__":raise SystemExit(main())
