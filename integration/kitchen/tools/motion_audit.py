"""Read-only measured stop audit for qualified state/action trajectories."""
import argparse
import json
from pathlib import Path
import numpy as np
from integration.kitchen.data.files import digest,read
from integration.kitchen.data.collect_experts import save
from integration.kitchen.data.replay import read_compact


def quiet_intervals(ticks,quiet,physics_hz=500,minimum_s=.2):
    ticks=np.asarray(ticks);quiet=np.asarray(quiet,dtype=bool)
    if len(ticks)!=len(quiet)+1 or np.any(np.diff(ticks)<=0):raise ValueError('Invalid measured clock')
    intervals=[];start=None
    for i,flag in enumerate(np.r_[quiet,False]):
        if flag and start is None:start=i
        if not flag and start is not None:
            duration=(int(ticks[i])-int(ticks[start]))/physics_hz
            if duration+1e-9>=minimum_s:
                intervals.append({'start_tick':int(ticks[start]),'end_tick':int(ticks[i]),'duration_s':duration})
            start=None
    return intervals


def audit(path):
    path=Path(path);data=read_compact(path)
    if data['schema']!='kitchen_state_actions_v3':raise ValueError('Audit requires V3 measured state/action data')
    dt=np.diff(data['states/tick'])/500
    q=data['states/qpos'][:,data['metadata']['arm_qpos_indices']]
    speed=np.max(np.abs(np.diff(q,axis=0)),axis=1)/dt
    jaws=np.max(np.abs(np.diff(data['states/gripper_width_m'],axis=0)),axis=1)/dt
    quiet=(speed<.01)&(jaws<.002)
    intervals=quiet_intervals(data['states/tick'],quiet)
    for item in intervals:
        item['start_s']=(item['start_tick']-data['start_tick'])/500
        item['end_s']=(item['end_tick']-data['start_tick'])/500
        item['skills']=sorted({s['skill'] for s in data['segments']
            if s['start_tick']<item['end_tick'] and s['end_tick']>item['start_tick']})
    duration=(data['end_tick']-data['start_tick'])/500
    return {'trajectory':str(path),'sha256':digest(path),'case_id':data['task']['case_id'],
        'simulated_seconds':duration,'quiet_seconds':sum(x['duration_s'] for x in intervals),
        'quiet_bouts':len(intervals),'longest_quiet_s':max((x['duration_s'] for x in intervals),default=0.),
        'intervals':intervals,'criterion':{'joint_max_rad_s':.01,'jaw_max_m_s':.002,'minimum_bout_s':.2},
        'scope':'Measured25Hz states, both arms/jaws; includes required grip/device/stability waits, not an automatic unnecessary-wait classification'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--manifest',type=Path)
    group.add_argument('--trajectories',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    paths=[Path(r['trajectory']) for r in read(args.manifest)] if args.manifest else args.trajectories
    result={'schema':'kitchen_motion_audit_v1','trajectories':[audit(p) for p in paths]}
    save(args.output,result)
    for row in result['trajectories']:
        print(json.dumps({k:row[k] for k in ('case_id','simulated_seconds','quiet_seconds','quiet_bouts','longest_quiet_s')}))


if __name__=='__main__':main()
