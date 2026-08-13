#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,shutil,subprocess
from pathlib import Path
def run(argv):
 c=subprocess.run(argv,capture_output=True,text=True,check=False);return {'argv':argv,'exit_code':c.returncode,'stdout':c.stdout,'stderr':c.stderr}
def main()->int:
 p=argparse.ArgumentParser();p.add_argument('--release-root',type=Path,required=True);p.add_argument('--runtime-root',type=Path,required=True);p.add_argument('--python',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=a.release_root.resolve();runtime=a.runtime_root.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);snap=out/'installed-units';snap.mkdir()
 actor=r/'control/actor-assignments-formal.json';assignment=json.loads(actor.read_text());vps=[x for x in assignment['assignments'] if 'vps_operator' in x['roles']][0];envfile=runtime/'runtime-provenance.env';runtime.mkdir(parents=True,exist_ok=True);schedule=runtime/'runtime-schedule.json';schedule.write_text((r/'tests/phase4/fixtures/schedule/dual-game.json').read_text(),encoding='utf-8')
 envfile.write_text(f"P4_PROJECT_ROOT={r}\nP4_ACTOR_ID={vps['actor_id']}\nP4_SESSION_ID={vps['session_id']}\nP4_TASK_ID=T18\nP4_ROLE=vps_operator\nP4_ACTOR_ASSIGNMENTS={actor.relative_to(r)}\n",encoding='utf-8')
 service=f'''[Unit]\nDescription=Lottery Phase 4 deterministic scheduler tick\nAfter=network-online.target\n\n[Service]\nType=oneshot\nUMask=0077\nWorkingDirectory={r}\nEnvironmentFile={envfile}\nExecStart={a.python.resolve()} -m lottery_system.phase4 schedule tick --schedule {schedule} --runtime-root {runtime} --clock system\n'''
 timer=(r/'deploy/systemd-user/lottery-phase4.timer').read_text();(snap/'lottery-phase4.service').write_text(service);(snap/'lottery-phase4.timer').write_text(timer)
 user_units=Path.home()/'.config/systemd/user';user_units.mkdir(parents=True,exist_ok=True);shutil.copy2(snap/'lottery-phase4.service',user_units/'lottery-phase4.service');shutil.copy2(snap/'lottery-phase4.timer',user_units/'lottery-phase4.timer')
 commands=[run(['systemctl','--user','daemon-reload']),run(['systemctl','--user','enable','--now','lottery-phase4.timer']),run(['systemctl','--user','cat','lottery-phase4.service']),run(['systemctl','--user','show','lottery-phase4.timer','-p','NextElapseUSecRealtime','-p','Persistent','-p','RandomizedDelayUSec']),run(['systemctl','--user','list-timers','lottery-phase4.timer','--no-pager'])]
 passed=all(x['exit_code']==0 for x in commands);receipt={'artifact_type':'phase4_user_systemd_install','schema_version':'1.0.0','release_id':r.name,'commands':commands,'sudo_used':False,'installed_unit_root':str(user_units),'status':'PASS' if passed else 'HOLD','terminal':'T18_USER_SYSTEMD_READY' if passed else 'HOLD_SCHEDULER_UNAVAILABLE'};(out/'install-receipt.json').write_text(json.dumps(receipt,sort_keys=True,separators=(',',':')),encoding='utf-8');print(json.dumps(receipt,sort_keys=True,separators=(',',':')));return 0 if passed else 20
if __name__=='__main__':raise SystemExit(main())
