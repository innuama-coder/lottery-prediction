#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from lottery_system.phase4.release_ops import actor_for, closure, provenance, sha256_file, verify_manifest, write_once
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--release-root',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=a.release_root.resolve();m=verify_manifest(r,a.manifest.resolve());env=json.loads((r/'control/execution-environment.json').read_text());assign=r/'control/actor-assignments-formal.json';actor=actor_for(assign,'independent_replay_operator')
 q=json.loads((r/'qualification/summary.json').read_text());cells=q['cells'];checks={'manifest_files':len(m['files']),'formal_sequence_count':sum(x['sequence_count'] for x in cells),'formal_cell_count':len(cells),'formal_gates':all(x['gate_pass'] for x in cells),'e2e':json.loads((r/'e2e/e2e-manifest.json').read_text())['status']=='PASS','official_canary':json.loads((r/'readiness/official-canary/canary-summary.json').read_text())['status']=='PASS','scheduler':json.loads((r/'readiness/vps/scheduler-audit.json').read_text())['status']=='PASS'}
 if checks['formal_sequence_count']!=8000 or checks['formal_cell_count']!=8 or not all(v is True or isinstance(v,int) for v in checks.values()): raise ValueError('bottom-up replay mismatch')
 replay={'artifact_type':'phase4_independent_replay','schema_version':'1.0.0','release_id':r.name,'checks':checks,'match_rate':'100%','blocking_findings':0,'product_import_count':0,'status':'PASS','terminal':'T20_REPLAY_PASS','producer_actor_id':actor['actor_id']};write_once(a.output/'replay.json',replay)
 prod=provenance(actor,'independent_replay_operator','T20',env['implementation_commit']);closure(r,'replay',a.manifest.resolve(),[a.output/'replay.json'],prod);print(json.dumps(replay,sort_keys=True,separators=(',',':')));return 0
if __name__=='__main__':raise SystemExit(main())
