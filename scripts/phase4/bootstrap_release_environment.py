#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, shutil, subprocess
from pathlib import Path
def sha(p:Path)->str:
 h=hashlib.sha256()
 if p.is_dir():
  for f in sorted(x for x in p.rglob('*') if x.is_file()): h.update(f.relative_to(p).as_posix().encode()); h.update(f.read_bytes())
 else: h.update(p.read_bytes())
 return h.hexdigest()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--prep-root',type=Path,required=True);p.add_argument('--release-root',type=Path,required=True);p.add_argument('--wheelhouse',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.release_root.exists(): raise SystemExit('release identity already exists')
 a.release_root.mkdir(parents=True); (a.release_root/'control').mkdir(); (a.release_root/'inputs').mkdir()
 shutil.copytree(a.wheelhouse,a.release_root/'inputs/wheelhouse')
 shutil.copytree(a.prep_root/'qualification-design',a.release_root/'inputs/preparation-evidence/qualification-design')
 shutil.copy2(a.prep_root/'work-items/T10/feasibility/certificate.json',a.release_root/'inputs/preparation-evidence/T10-feasibility.json')
 (a.release_root/'inputs/config').mkdir(parents=True)
 shutil.copy2(Path('config/phase4/scientific-power-controller-command.json'),a.release_root/'inputs/config/scientific-power-controller-command.json')
 env={'artifact_type':'phase4_execution_environment','schema_version':'1.0.0','implementation_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'wheelhouse_manifest_sha256':sha(a.release_root/'inputs/wheelhouse'),'release_root':str(a.release_root)}
 (a.release_root/'control/execution-environment.json').write_text(json.dumps(env,sort_keys=True,separators=(',',':')),encoding='utf-8')
 a.output.mkdir(parents=True,exist_ok=True); (a.output/'receipt.json').write_text(json.dumps({'status':'PASS','terminal':'T15_FORMAL_RELEASE_FROZEN','release_root':str(a.release_root),'execution_environment_sha256':sha(a.release_root/'control/execution-environment.json')},sort_keys=True,separators=(',',':')),encoding='utf-8'); return 0
if __name__=='__main__':raise SystemExit(main())
