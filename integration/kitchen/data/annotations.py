"""English coarse/fine labels indexed by action rows, never by physics ticks.

CLI exports labels for an existing qualified trajectory to a fresh JSON file;
original trajectories, reports and dataset indexes are never modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from integration.kitchen.tasks.catalog import chain,HEAT
from integration.kitchen.tasks.language import SCHEMA,coarse_instruction,instruction
from integration.kitchen.validation.success import validate_timeline
from .replay import read_compact


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def build_annotations(path):
    data=read_compact(path)
    if data['schema']!='kitchen_state_actions_v3':raise ValueError('LANGUAGE_REQUIRES_POLICY_ACTIONS_V3')
    case=data.get('task');raw=data.get('segments',[])
    if not case:raise ValueError('NO_TASK_TIMELINE')
    validate_timeline(case,raw,data['start_tick'],data['end_tick'])
    skills=list(chain(case));groups=[];cursor=0
    for skill in skills:
        count=len(raw)-(len(skills)-1) if skill==HEAT else 1
        groups.append(raw[cursor:cursor+count]);cursor+=count
    starts=data.get('language_stages',[])
    if starts:
        if [s['skill'] for s in starts]!=skills:raise ValueError('LANGUAGE_STAGE_CHAIN_MISMATCH')
        boundaries=[s['start_tick'] for s in starts]
    else:
        # Read-only historical export: combine actual atomic H stages and
        # assign between-skill withdrawal to the preceding semantic stage.
        boundaries=[group[0]['start_tick'] for group in groups]
        boundaries[0]=data['start_tick']
    if (boundaries[0]!=data['start_tick'] or any(type(t) is not int for t in boundaries)
        or any(a>=b for a,b in zip(boundaries,boundaries[1:]))):
        raise ValueError('INVALID_LANGUAGE_STAGE_CLOCK')
    boundaries.append(data['end_tick']);ticks=data['actions/tick'];n=len(ticks)
    if not n:raise ValueError('NO_POLICY_ACTIONS')
    indices=[int(np.searchsorted(ticks,t,side='left')) for t in boundaries]
    fine=[]
    for i,(skill,group) in enumerate(zip(skills,groups)):
        a,b=boundaries[i:i+2];first,last=indices[i:i+2]
        if not (a<=group[0]['start_tick']<group[-1]['end_tick']<=b and 0<=first<last<=n):
            raise ValueError('LANGUAGE_RANGE_DOES_NOT_COVER_SKILLS')
        if a%20 or b%20:raise ValueError('LANGUAGE_STAGE_NOT_ON_CONTROL_BOUNDARY')
        fine.append({'skill':skill,'instruction':instruction(skill,case),'start_step':first,'end_step':last})
    if indices[0]!=0 or indices[-1]!=n:raise ValueError('LANGUAGE_RANGE_DOES_NOT_COVER_ACTIONS')
    return {'schema':SCHEMA,'case_id':case['case_id'],'language':'en',
        'targets':{slot:case[key] for slot,key in [('fruit','fruit'),('food','meal')] if case.get(key)},
        'coarse_instruction':coarse_instruction(case),'num_steps':n,
        'step_indexing':'zero_based_half_open_action_rows','fine_segments':fine,
        'trajectory_sha256':digest(path)}


def validate_sidecar(trajectory,path,expected_sha256):
    path=Path(path)
    if digest(path)!=expected_sha256:raise ValueError('Language annotation hash mismatch')
    if json.loads(path.read_text())!=build_annotations(trajectory):
        raise ValueError('Language annotation does not match trajectory')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trajectory',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    report=json.loads(args.trajectory.with_name('report.json').read_text())
    if (report.get('status')!='QUALIFIED_EXPERT' or report.get('task_success') is not True
        or report.get('verification',{}).get('sha256')!=digest(args.trajectory)):
        parser.error('Export requires the matching sealed qualified report')
    annotation=build_annotations(args.trajectory)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(annotation,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({'annotation':str(args.output),'num_steps':annotation['num_steps'],
                      'fine_segments':len(annotation['fine_segments'])}))
    return 0

if __name__=='__main__':raise SystemExit(main())
