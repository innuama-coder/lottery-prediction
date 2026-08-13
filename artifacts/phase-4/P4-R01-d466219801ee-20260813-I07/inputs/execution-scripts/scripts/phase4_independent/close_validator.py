#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
from lottery_system.phase4.release_ops import actor_for,closure,provenance
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--release-root',type=Path,required=True);p.add_argument('--validator',type=Path,required=True);p.add_argument('--replay-closure',type=Path,required=True);a=p.parse_args();r=a.release_root.resolve();v=json.loads(a.validator.read_text());
 if v.get('status')!='PASS' or v.get('blocking_findings')!=0:raise ValueError('validator is not PASS')
 assign=r/'control/actor-assignments-formal.json';actor=actor_for(assign,'acceptance_engineer');env=json.loads((r/'control/execution-environment.json').read_text());closure(r,'validator',a.replay_closure.resolve(),[a.validator.resolve()],provenance(actor,'acceptance_engineer','T21',env['implementation_commit']));return 0
if __name__=='__main__':raise SystemExit(main())
