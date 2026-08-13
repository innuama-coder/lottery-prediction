#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, sys
from pathlib import Path
def sha(path:Path)->str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--expected-commit',required=True);a=p.parse_args();m=json.loads(a.manifest.read_text());root=Path(m['release_root'])
 if m['implementation_commit']!=a.expected_commit or Path(sys.executable).resolve()!=Path(m['release_python']).resolve(): raise ValueError('execution identity mismatch')
 for group,prefix in ((m['wheelhouse_files'],'inputs/wheelhouse'),(m['execution_script_files'],'inputs/execution-scripts')):
  for row in group:
   path=root/prefix/row['path']
   if not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']: raise ValueError(f'execution file mismatch: {path}')
 print(json.dumps({'status':'PASS','terminal':'EXECUTION_ENVIRONMENT_PASS','implementation_commit':a.expected_commit,'python':str(Path(sys.executable).resolve())},sort_keys=True,separators=(',',':')));return 0
if __name__=='__main__':raise SystemExit(main())
