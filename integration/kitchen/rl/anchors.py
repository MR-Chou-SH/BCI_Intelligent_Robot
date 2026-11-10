"""Prepare immutable L1 seed experience and sparse full-reset anchors from H5."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from robot_arm.kitchen.state_tensor import StateTensor
from robot_arm.kitchen.registry import control_identity
from integration.kitchen.data.replay import read_compact, verify
from integration.kitchen.data.lerobot_export import policy_episode
from integration.kitchen.data.files import digest, replace_json, read
from integration.kitchen.vla.dataset import trajectory_paths
from .tasks import TaskMonitor
from .observations import ObservationLayout
from .snapshots import capture


def prepare_one(path, output, stride):
    episode=policy_episode(path);data=read_compact(path)
    if (data['task']['task']!='L1' or data['task']['fruit']!='apple'
            or np.any(data['actions/interpolation_reset'])
            or not np.array_equal(data['actions/tick'],episode['ticks'])):
        raise ValueError('First seed bank requires uniform25Hz L1 apple records')
    directory=output/episode['source_sha256'];directory.mkdir()
    observations=[];actions=[];outcomes=[];anchors=[]
    rng=np.random.default_rng(read(path.with_name('report.json'))['seed'])
    holder={}
    selected=set(range(0,len(episode['ticks']),stride))|{len(episode['ticks'])-1}
    def observe(scene,controller,index):
        if not holder:
            p=SimpleNamespace(scene=scene,controller=controller,post_step_observers=[],queue=[],control=control_identity())
            monitor=TaskMonitor(p,data['task']);reader=StateTensor(scene);layout=ObservationLayout(reader,scene,data['task'])
            holder.update(platform=p,monitor=monitor,reader=reader,layout=layout)
        monitor=holder['monitor']
        if index:monitor.quality.sample(controller)
        outcome=monitor.update();state=holder['reader'].query(scene,controller)
        observations.append(holder['layout'].normalize(holder['layout'].raw(state,monitor)))
        outcomes.append(outcome['success'])
        if index in selected:
            saved=capture(holder['platform'],monitor,rng)
            saved['environment']={'steps':0,'horizon':750,'done':False}
            anchors.append({'frame':index,'snapshot':saved})
        if index<len(episode['ticks']):
            q=data['actions/joint_pos'][index,:7]
            action=np.r_[(q-controller.reference[:7])/(controller.velocity_limit/25),
                         2*data['actions/gripper_opening'][index,0]-1]
            if np.abs(action).max()>1.0001:raise ValueError('Expert control not representable in boundedRL action space')
            actions.append(np.clip(action,-1,1))
    report=verify(path,observer=observe)
    if report['status']!='CONTROL_RESIMULATION_PASS' or not report['task_success']:
        raise ValueError('Seed independent replay failed')
    indices=np.flatnonzero(outcomes)
    if not len(indices):raise ValueError('Autonomous task monitor did not recognize expert success')
    end=int(indices[0]);anchors=[a for a in anchors if a['frame']<end]
    if not anchors:raise ValueError('No pre-terminal curriculum anchors')
    # Include the last pre-success state, without ever labeling it already done.
    rewards=np.zeros(end,dtype='f4');rewards[-1]=1
    terminated=np.zeros(end,dtype=bool);terminated[-1]=True
    array=directory/'experience.npz'
    np.savez_compressed(array,observation=np.asarray(observations[:end],dtype='f4'),
        next_observation=np.asarray(observations[1:end+1],dtype='f4'),action=np.asarray(actions[:end],dtype='f4'),
        reward=rewards,terminated=terminated,truncated=np.zeros(end,dtype=bool))
    anchor_path=directory/'anchors.json.gz'
    with gzip.open(anchor_path,'xt',encoding='utf-8') as stream:json.dump(anchors,stream,separators=(',',':'))
    row={'source':str(path),'source_sha256':episode['source_sha256'],'source_group':episode['source_group'],
         'seed':read(path.with_name('report.json'))['seed'],'frames':end,'anchor_frames':[a['frame'] for a in anchors],
         'experience':str(array.relative_to(output)),'experience_sha256':digest(array),
         'anchors':str(anchor_path.relative_to(output)),'anchors_sha256':digest(anchor_path),
         'layout':holder['layout'].schema(),'initial_xy':data['states/object_pose'][0,data['metadata']['object_ids'].index('apple'),:2].tolist()}
    replace_json(directory/'receipt.json',row)
    return row


def prepare(manifest, output, count=20, stride=16):
    paths=trajectory_paths(manifest=manifest)
    if count<1 or stride<1:raise ValueError('Positive count/stride required')
    # Spread seed demos across the sorted initial x/y distribution, not repeated
    # optimized/backup copies. Exact same initial states keep one source group.
    coordinates=[]
    for path in paths:
        d=read_compact(path);i=d['metadata']['object_ids'].index('apple')
        coordinates.append(d['states/object_pose'][0,i,:2])
    selected=[int(np.argmin(np.asarray(coordinates)[:,0]))]
    while len(selected)<min(count,len(paths)):
        distance=np.min(np.linalg.norm(np.asarray(coordinates)[:,None]-np.asarray(coordinates)[selected][None],axis=-1),axis=1)
        distance[selected]=-1;selected.append(int(distance.argmax()))
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    rows=[]
    for index in selected:
        row=prepare_one(paths[index],output,stride);rows.append(row)
        print(json.dumps({'prepared':len(rows),'seed':row['seed'],'frames':row['frames'],'anchors':len(row['anchor_frames'])}),flush=True)
    result={'schema':'kitchen_discodemo_seed_bank_v1','anchor_stride':stride,'demos':rows,
            'control_hash':control_identity()['control_hash'],
            'scope':'Full physical reset anchors at sparse demonstration frames; new curriculum episodes reset task hold memory.'}
    replace_json(output/'bank.json',result)
    return result


def align_legacy_tcp_quaternions(transitions, layout, scene):
    """Correct only the signs of legacy matrix-derived TCP quats, in memory.

    FK of recorded joints selects the native branch; keep the recorded values
    and all other channels. Original bank files and their receipts stay intact.
    """
    import mujoco
    scratch=mujoco.MjData(scene.model)
    low=np.asarray(layout['low']);high=np.asarray(layout['high'])
    changed=0
    for key in ('observation','next_observation'):
        for observation in transitions[key]:
            for offset,side in ((0,'left'),(22,'right')):
                arm=scene.arms[side]
                scratch.qpos[list(arm.qpos)]=(observation[offset:offset+7]+1)/2*(high[offset:offset+7]-low[offset:offset+7])+low[offset:offset+7]
            mujoco.mj_kinematics(scene.model,scratch)
            for offset,side in ((0,'left'),(22,'right')):
                quat=observation[offset+18:offset+22]
                if np.dot(quat,scratch.xquat[scene.arms[side].tcp_body])<0:
                    quat*=-1;changed+=1
    return changed


def load_bank(path):
    path=Path(path);bank=read(path);base=path.parent
    if bank['schema']!='kitchen_discodemo_seed_bank_v1':raise ValueError('Unsupported seed bank')
    if bank['control_hash']!=control_identity()['control_hash']:raise ValueError('Seed bank control changed')
    compatibility_scene=None;changed=0;observation_hash=hashlib.sha256()
    for row in bank['demos']:
        for key in ('experience','anchors'):
            item=(base/row[key]).resolve()
            if not item.is_relative_to(base.resolve()) or digest(item)!=row[key+'_sha256']:
                raise ValueError('Seed bank file/path/hash mismatch')
        with gzip.open(base/row['anchors'],'rt',encoding='utf-8') as stream:row['snapshots']=json.load(stream)
        with np.load(base/row['experience'],allow_pickle=False) as data:row['transitions']={k:data[k].copy() for k in data.files}
        if row['layout'].get('tcp_quaternion_convention')!='native_body_xquat_wxyz_v1':
            if compatibility_scene is None:
                from robot_arm.kitchen.model import build_scene
                compatibility_scene=build_scene(seed=0,layout_id=row['snapshots'][0]['snapshot']['layout'])
            changed+=align_legacy_tcp_quaternions(row['transitions'],row['layout'],compatibility_scene)
        for key in ('observation','next_observation'):observation_hash.update(row['transitions'][key].tobytes())
    bank['observation_adapter']={'name':'native_body_xquat_wxyz_v1','legacy_quaternions_flipped':changed,
                                'derived_observations_sha256':observation_hash.hexdigest()}
    return bank


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--demos',type=int,default=20);parser.add_argument('--stride',type=int,default=16)
    args=parser.parse_args();prepare(args.manifest,args.output,args.demos,args.stride)


if __name__=='__main__':main()
