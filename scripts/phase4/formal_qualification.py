#!/usr/bin/env python3
"""Run the frozen scientific controller for the formal 1,000-sequence cells."""
from __future__ import annotations
import argparse, json, subprocess, hashlib
from pathlib import Path
GAMES=('dlt','ssq'); WORLDS=('uniform','static_bias','slow_drift','useful_feature')
def canon(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def sha(b):return hashlib.sha256(b).hexdigest()
def derive(d,dom,g,w,o):return int.from_bytes(hashlib.sha256(f'P4-SEED-v2|{d}|{dom}|{g}|{w}|{o}'.encode()).digest(),'big')
def main():
 p=argparse.ArgumentParser();p.add_argument('--design',type=Path,required=True);p.add_argument('--command',type=Path,required=True);p.add_argument('--release-root',type=Path,required=True);p.add_argument('--sequences',type=int,default=1000);a=p.parse_args(); d=json.loads(a.design.read_text()); c=json.loads(a.command.read_text()); out=a.release_root/'qualification'; out.mkdir(parents=True,exist_ok=False)
 cells=[]
 for g in GAMES:
  for w in WORLDS:
   proc=subprocess.Popen(c['argv']+['--stream'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,env={**__import__('os').environ,'PYTHONPATH':'src'}); terms=[]; succ=0
   for o in range(1,a.sequences+1):
    n=derive(d['design_id'],'formal-qualification',g,w,o); st=str(n); req={'schema_version':'1.0.0','artifact_type':'phase4_scientific_controller_request','request_id':f'formal-{g}-{w}-{o}','expected_controller_identity_id':c['controller_identity']['controller_identity_id'],'design':d,'game':g,'world':w,'sequence_ordinal':o,'seed_domain':'formal-qualification','input_mode':'seed','seed_uint256':st,'seed_commitment_sha256':sha(st.encode()),'raw_draws':None}; proc.stdin.write(canon(req)+b'\n');proc.stdin.flush(); r=json.loads(proc.stdout.readline()); t=r['sequence_terminal'];succ+=int(t['sequence_event'] is True);terms.append(t)
   proc.terminate();proc.wait(); sh=out/f'{g}-{w}-terminals.json';sh.write_bytes(canon(terms));cells.append({'game':g,'world':w,'sequence_count':a.sequences,'success_count':succ,'terminals_path':sh.name,'terminals_sha256':sha(sh.read_bytes())})
 control={'artifact_type':'phase4_formal_qualification_summary','schema_version':'1.0.0','design_id':d['design_id'],'sequence_count':sum(x['sequence_count'] for x in cells),'cells':cells,'status':'PASS' if all((x['success_count']<=50 if x['world']=='uniform' else x['success_count']>=900) for x in cells) else 'FAIL','terminal':'FORMAL_QUALIFICATION_PASS' if all((x['success_count']<=50 if x['world']=='uniform' else x['success_count']>=900) for x in cells) else 'FAIL_FORMAL_QUALIFICATION'}; (out/'summary.json').write_bytes(canon(control)); return 0 if control['status']=='PASS' else 2
if __name__=='__main__':raise SystemExit(main())
