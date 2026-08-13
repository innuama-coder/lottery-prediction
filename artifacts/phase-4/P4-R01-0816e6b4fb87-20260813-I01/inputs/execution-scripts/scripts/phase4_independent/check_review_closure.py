#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
from lottery_system.phase4.release_ops import actor_for,closure,provenance,write_once
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--release-root',type=Path,required=True);p.add_argument('--validator-closure',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=a.release_root.resolve();v=json.loads((r/'validator/final-validator.json').read_text());assign=r/'control/actor-assignments-formal.json';actors=json.loads(assign.read_text());actor=actor_for(assign,'independent_reviewer');prior=set(actors.get('prior_producer_actor_ids',[]))
 if actor['actor_id'] in prior or v.get('status')!='PASS' or v.get('blocking_findings')!=0: raise ValueError('review independence or validator gate failed')
 review={'artifact_type':'phase4_release_review','schema_version':'1.0.0','release_id':r.name,'reviewer_actor_id':actor['actor_id'],'assertion_dispositions':v['assertions'],'blocking_findings':0,'independence_review':'PASS','scientific_wording':'Synthetic capability only; no real predictive improvement is claimed.','status':'PASS','terminal':'T22_REVIEW_PASS'};write_once(a.output/'review.json',review);env=json.loads((r/'control/execution-environment.json').read_text());closure(r,'review',a.validator_closure.resolve(),[a.output/'review.json'],provenance(actor,'independent_reviewer','T22',env['implementation_commit']));print(json.dumps(review,sort_keys=True,separators=(',',':')));return 0
if __name__=='__main__':raise SystemExit(main())
