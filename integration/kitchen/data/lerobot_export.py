"""Read-only H5 -> LeRobot policy data. Images remain a separate future stage.

The plan command needs only existing simulation dependencies. Export calls the
real LeRobot writer in a conversion environment; it never writes a fake dataset
or installs dependencies. Fine-label export splits episodes at task boundaries.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from .files import read,digest
from .annotations import validate_sidecar
from .replay import read_compact

FPS=25
JOINT_NAMES=[f'{side}_q{i}' for side in ('left','right') for i in range(1,8)]
ACTION_NAMES=JOINT_NAMES[:7]+['left_gripper_opening_ratio']+JOINT_NAMES[7:]+['right_gripper_opening_ratio']
STATE_NAMES=JOINT_NAMES[:7]+['left_gripper_width_m']+JOINT_NAMES[7:]+['right_gripper_width_m']


def pack16(joints,grippers):
    joints=np.asarray(joints);grippers=np.asarray(grippers)
    if joints.shape[-1:]!=(14,) or grippers.shape!=joints.shape[:-1]+(2,):raise ValueError('Expected14 joints and2 grippers')
    return np.concatenate([joints[...,:7],grippers[...,:1],joints[...,7:],grippers[...,1:]],axis=-1).astype(np.float32)


def unpack16(action):
    action=np.asarray(action)
    if action.shape[-1:]!=(16,) or not np.isfinite(action).all():raise ValueError('Expected finite16D policy action')
    return np.concatenate([action[...,:7],action[...,8:15]],axis=-1),action[...,[7,15]]


def policy_episode(path):
    path=Path(path).resolve();report=read(path.with_name('report.json'))
    if report.get('status')!='QUALIFIED_EXPERT' or report.get('task_success') is not True:
        raise ValueError('Export only qualified episodes')
    if digest(path)!=report['verification']['sha256']:raise ValueError('Trajectory hash mismatch')
    annotation_path=path.with_name('annotations.json')
    validate_sidecar(path,annotation_path,report['annotations_sha256'])
    annotation=read(annotation_path);data=read_compact(path)
    if data['metadata']['action_hz']!=FPS or data['metadata']['physics_hz']!=500:
        raise ValueError('Unsupported policy/physics clock')
    if data['start_tick']%20:raise ValueError('Export requires a control-boundary episode start')
    ticks=np.arange(data['start_tick'],data['end_tick'],20,dtype=np.int64)
    raw_indices=np.searchsorted(data['actions/tick'],ticks,side='right')-1
    state_indices=np.searchsorted(data['states/tick'],ticks)
    if not len(ticks) or np.any(raw_indices<0) or not np.array_equal(data['states/tick'][state_indices],ticks):
        raise ValueError('Missing exact measured states on the25Hz export grid')
    qpos_indices=data['metadata']['arm_qpos_indices']
    if len(qpos_indices)!=14:raise ValueError('Expected two7-joint arms')
    state=pack16(data['states/qpos'][state_indices][:,qpos_indices],data['states/gripper_width_m'][state_indices])
    action=pack16(data['actions/joint_pos'][raw_indices],data['actions/gripper_opening'][raw_indices])
    if not np.isfinite(state).all() or not np.isfinite(action).all():raise ValueError('Nonfinite policy data')
    segments=[]
    for segment in annotation['fine_segments']:
        first,last=np.searchsorted(raw_indices,[segment['start_step'],segment['end_step']],side='left')
        if first<last:segments.append({**segment,'export_start':int(first),'export_end':int(last)})
    if not segments or segments[0]['export_start']!=0 or segments[-1]['export_end']!=len(ticks):
        raise ValueError('Language does not cover export grid')
    return {'state':state,'action':action,'ticks':ticks,'segments':segments,'annotation':annotation,
            'source':str(path),'source_sha256':annotation['trajectory_sha256']}


def episode_views(episode,level):
    if level=='coarse':
        yield 0,len(episode['action']),episode['annotation']['coarse_instruction']
    elif level=='fine':
        for s in episode['segments']:yield s['export_start'],s['export_end'],s['instruction']
    else:raise ValueError('Unknown instruction level')


def features():
    return {'observation.state':{'dtype':'float32','shape':(16,),'names':STATE_NAMES},
            'action':{'dtype':'float32','shape':(16,),'names':ACTION_NAMES}}


def plan(paths,level):
    episodes=[]
    for path in paths:
        episode=policy_episode(path)
        episodes.append({'source':episode['source'],'source_sha256':episode['source_sha256'],
            'case_id':episode['annotation']['case_id'],'targets':episode['annotation']['targets'],
            'coarse_instruction':episode['annotation']['coarse_instruction'],
            'frames':len(episode['action']),'exported_episodes':sum(1 for _ in episode_views(episode,level)),
            'segments':episode['segments']})
    return {'schema':'kitchen_lerobot_export_v1','fps':FPS,'instruction_level':level,
        'features':features(),'vision_ready':False,'source_episodes':episodes,
        'action_semantics':'absolute joint radians and normalized gripper targets; left7/opening/right7/opening',
        'state_semantics':'measured joint radians and gripper width metres; left7/width/right7/width'}


def export(paths,output,level,repo_id='local/kitchen_fr3_umi',*,writer_class=None):
    output=Path(output)
    if output.exists():raise FileExistsError('Use a new export directory')
    if writer_class is None:
        try:from lerobot.datasets.lerobot_dataset import LeRobotDataset
        except ImportError as error:raise RuntimeError('LeRobot is required in the conversion environment; use plan for dependency-free validation') from error
        writer_class=LeRobotDataset
    # Validate all source receipts before creating a destination dataset.
    description=plan(paths,level)
    dataset=writer_class.create(repo_id=repo_id,root=output,fps=FPS,features=features())
    exported=[]
    for path in paths:
        episode=policy_episode(path)
        for first,last,prompt in episode_views(episode,level):
            for i in range(first,last):
                dataset.add_frame({'observation.state':episode['state'][i],
                                   'action':episode['action'][i],'task':prompt})
            dataset.save_episode()
            exported.append({'episode_index':len(exported),'source':episode['source'],
                'source_group':episode['source_sha256'],'export_start':first,'export_end':last,
                'source_start_tick':int(episode['ticks'][first]),'task':prompt})
    if hasattr(dataset,'finalize'):dataset.finalize()
    description['exported_episodes']=exported
    description['writer']=writer_class.__module__+'.'+writer_class.__name__
    if writer_class.__module__.startswith('lerobot.'):
        from importlib.metadata import version,PackageNotFoundError
        try:description['writer_package_version']=version('lerobot')
        except PackageNotFoundError:description['writer_package_version']='unknown'
    with (output/'kitchen_export.json').open('x',encoding='utf-8') as stream:
        json.dump(description,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return description


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','export'))
    sources=parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--trajectories',nargs='+',type=Path)
    sources.add_argument('--manifest',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--instruction-level',choices=('coarse','fine'),default='coarse')
    parser.add_argument('--repo-id',default='local/kitchen_fr3_umi')
    args=parser.parse_args(argv)
    paths=args.trajectories or [Path(r['trajectory']) for r in read(args.manifest)]
    if not paths or len({p.resolve() for p in paths})!=len(paths):parser.error('Empty or duplicate source episodes')
    if args.action=='plan':
        result=plan(paths,args.instruction_level);args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    else:result=export(paths,args.output,args.instruction_level,args.repo_id)
    print(json.dumps({'output':str(args.output),'source_episodes':len(result['source_episodes']),
                      'fps':FPS,'vision_ready':False}))
    return 0

if __name__=='__main__':raise SystemExit(main())
