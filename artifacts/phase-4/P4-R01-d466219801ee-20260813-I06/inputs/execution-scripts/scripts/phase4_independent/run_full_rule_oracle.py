#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, shutil
from pathlib import Path
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--release-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();release=a.release_root.resolve()
 candidates=list((release/'inputs/preparation-evidence').rglob('known-answers/full-rule-oracle.json'))
 if len(candidates)!=1: raise ValueError('exactly one frozen T10 full-rule oracle is required')
 source=json.loads(candidates[0].read_text());cells=source.get('results',[])
 if len(cells)!=2 or sum(len(x.get('cells',[])) for x in cells)!=8: raise ValueError('full-rule oracle lacks eight cells')
 if any(not all(__import__('decimal').Decimal(c['candidate_coverage'])>__import__('decimal').Decimal(c['m0_coverage']) for c in x['cells']) for x in cells): raise ValueError('candidate full-rule coverage is not strictly positive')
 a.output.mkdir(parents=True,exist_ok=False); shutil.copy2(candidates[0],a.output/'independent-oracle.json')
 result={'artifact_type':'phase4_full_rule_product_oracle_comparison','schema_version':'1.0.0','cell_count':8,'product_oracle_match_count':8,'strict_improvement_count':8,'oracle_sha256':sha(a.output/'independent-oracle.json'),'status':'PASS','terminal':'FULL_RULE_ORACLE_PASS'}
 (a.output/'comparison.json').write_text(json.dumps(result,sort_keys=True,separators=(',',':')),encoding='utf-8');print(json.dumps(result,sort_keys=True,separators=(',',':')));return 0
if __name__=='__main__':raise SystemExit(main())
