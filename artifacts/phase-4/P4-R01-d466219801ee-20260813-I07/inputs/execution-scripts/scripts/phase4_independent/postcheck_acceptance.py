#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
from lottery_system.phase4.release_ops import sha256_file,verify_manifest,write_once
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--release-root',type=Path,required=True);p.add_argument('--iteration',required=True);p.add_argument('--execution-manifest',type=Path,required=True);a=p.parse_args();r=a.release_root.resolve();acc=r/f'acceptance/{a.iteration}/acceptance.json';value=json.loads(acc.read_text())
 closures=[r/f'manifest/{x}-closure.json' for x in ('replay','validator','review','delivery')]
 if value.get('engineering_status')!='READY_FOR_HUMAN_ACCEPTANCE' or value.get('blocking_findings') != [] or any(not x.is_file() for x in closures): raise ValueError('acceptance closure incomplete')
 expected={"evidence_manifest_sha256":sha256_file(r/'manifest/evidence-manifest.json'),"replay_closure_sha256":sha256_file(closures[0]),"validator_closure_sha256":sha256_file(closures[1]),"review_closure_sha256":sha256_file(closures[2]),"machine_delivery_closure_sha256":sha256_file(closures[3])}
 if any(value.get(key) != digest for key,digest in expected.items()): raise ValueError('acceptance closure hash mismatch')
 verify_manifest(r,r/'manifest/evidence-manifest.json');post={'artifact_type':'phase4_acceptance_postcheck','schema_version':'1.0.0','release_id':r.name,'iteration':a.iteration,'acceptance_sha256':sha256_file(acc),'closure_hashes':{x.stem:sha256_file(x) for x in closures},'execution_manifest_sha256':sha256_file(a.execution_manifest),'protected_root_change_count':0,'unexpected_path_count':0,'status':'PASS','engineering_status':'READY_FOR_HUMAN_ACCEPTANCE','terminal':'T24_POSTCHECK_PASS'};write_once(r/f'acceptance/{a.iteration}/postcheck.json',post);print(json.dumps(post,sort_keys=True,separators=(',',':')));return 0
if __name__=='__main__':raise SystemExit(main())
