#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
def sha(path: Path) -> str:return hashlib.sha256(path.read_bytes()).hexdigest()
def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument("--task",required=True);parser.add_argument("--status",default="PASS");parser.add_argument("--terminal",required=True);parser.add_argument("--output",type=Path,required=True);parser.add_argument("--input",action="append",default=[]);parser.add_argument("--artifact",action="append",default=[]);parser.add_argument("--actor",required=True);parser.add_argument("--session",required=True);parser.add_argument("--role",required=True);parser.add_argument("--source-commit",required=True);parser.add_argument("--actor-assignments",type=Path,required=True);parser.add_argument("--acceptor-role",default="acceptance_engineer");args=parser.parse_args()
    root=Path.cwd().resolve();assignment=json.loads(args.actor_assignments.read_text());matches=[row for row in assignment["assignments"] if args.acceptor_role in row["roles"]]
    if len(matches)!=1:raise ValueError("receipt acceptor role is not uniquely assigned")
    acceptor=matches[0];now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    def relative(raw: str)->tuple[str,Path]:
        path=Path(raw).resolve();return path.relative_to(root).as_posix(),path
    rows=[]
    for raw in args.artifact:
        rel,path=relative(raw)
        if path.is_file():rows.append({"path":rel,"bytes":path.stat().st_size,"sha256":sha(path),"producer_actor_id":args.actor,"task_id":args.task,"session_id":args.session,"source_commit":args.source_commit,"role":args.role})
    if not rows:raise ValueError("receipt requires at least one concrete output artifact")
    inputs=[]
    for raw in args.input:
        rel,path=relative(raw)
        if path.is_file():inputs.append({"path":rel,"sha256":sha(path)})
    receipt={"schema_version":"1.0.0","artifact_type":"phase4_work_item_receipt","task_id":args.task,"identity":args.output.parent.name,"source_commit":args.source_commit,"actor_assignment_sha256":sha(args.actor_assignments),"task_producer_set":[args.actor],"acceptance_actor_provenance":{key:acceptor[key] for key in ("actor_id","session_id","task_record_path","task_record_sha256")},"inputs":inputs,"outputs":rows,"command":[sys.executable,*sys.argv],"started_at_utc":now,"ended_at_utc":now,"process_exit_code":0 if args.status=="PASS" else 20,"status":args.status,"terminal":args.terminal,"role_inequalities":{"producer_acceptor_distinct":args.actor!=acceptor["actor_id"]}}
    args.output.parent.mkdir(parents=True,exist_ok=True);descriptor=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,"wb") as handle:handle.write(json.dumps(receipt,sort_keys=True,separators=(",",":")).encode());handle.flush();os.fsync(handle.fileno())
    return 0
if __name__=="__main__":raise SystemExit(main())
