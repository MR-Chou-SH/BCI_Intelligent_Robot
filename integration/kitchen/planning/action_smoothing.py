"""Small scratch-checked corner blends near the recorded joint path."""
import numpy as np
import mujoco
from .action_retiming import ARM_COLUMNS
from robot_arm.kitchen.control import tcp_pose


def carry_pause_clearance(data,platform):
    """Only remove longer lift/turn pauses with >=20mm measured clearance.

    Exclude intentional hand/payload and internal payload contacts. Check the
    whole active arm plus carrier/food against other objects and fixtures at
    the beginning, middle and end of the recorded hold. Tight oven/sink routes
    retain their original pauses; explicit event boundaries are never crossed.
    """
    from robot_arm.kitchen.contact import object_geom_ids
    geometry=platform.geometry;m=platform.scene.model;cache={}
    def clear(first,last,mode):
        contract=mode['contact_contract'];side=contract['arm'];payload=tuple(contract['payload_ids'])
        key=(side,payload)
        if key not in cache:
            sides=('left','right') if side=='both' else (side,)
            active={g for g in geometry.robot if m.body(int(m.geom_bodyid[g])).name.startswith(tuple(s+'_' for s in sides))}
            hand={g for g in active if m.body(int(m.geom_bodyid[g])).name.startswith(tuple(s+'_umi' for s in sides))}
            hand_groups=[{g for g in hand if m.body(int(m.geom_bodyid[g])).name.startswith(s+'_umi')} for s in sides]
            carried=set().union(*(object_geom_ids(platform.scene,n) for n in payload)) if payload else set()
            cache[key]=np.asarray([p for p in geometry.pairs(payload)
                if any(int(g) in active|carried for g in p)
                and not all(int(g) in carried for g in p)
                and not any(all(int(g) in group for g in p) for group in hand_groups)
                and not (any(int(g) in hand for g in p) and any(int(g) in carried for g in p))],dtype=int).reshape(-1,2)
        pairs=cache[key]
        if not len(pairs):return False
        for frame in (first,(first+last)//2,last):
            tick=data['start_tick']+20*frame
            i=int(np.searchsorted(data['states/tick'],tick))
            if i>=len(data['states/tick']) or data['states/tick'][i]!=tick:return False
            if float(np.min(geometry.distances(data['states/qpos'][i],pairs)))<.020:return False
        return True
    return clear


def blend_source(commands,data,cuts,modes,platform):
    """Preserve phase endpoints, grippers, and device/acquire paths.

    Two binomial passes, <=0.008rad joint and <=2mm TCP change. Static fixture
    distance is bounded using the existing per-geom motion bound; if a sample
    cannot prove clearance, keep its original command. Not a dynamic grasp or
    continuous whole-route collision certificate.
    """
    source=np.asarray(commands);result=source.copy();n=len(source)
    points=np.vstack([data['controller']['reference'],source]);proposed=points.copy()
    protected=set(cuts)|{0,n}
    # Don't blend within five frames of gripper motion or explicit events.
    changes=np.flatnonzero(np.max(np.abs(np.diff(points[:,[7,15]],axis=0)),axis=1)>1e-12)+1
    for index in list(protected)+changes.tolist():
        protected.update(range(max(0,index-5),min(n,index+5)+1))
    for _ in range(2):
        next_points=proposed.copy()
        next_points[1:-1,ARM_COLUMNS]=(proposed[:-2,ARM_COLUMNS]+2*proposed[1:-1,ARM_COLUMNS]+proposed[2:,ARM_COLUMNS])/4
        proposed=next_points
    geometry=platform.geometry;m=platform.scene.model
    # Fixed fixtures, including sink rim/counter; articulated microwave parts
    # are included too using their recorded positions at each source tick.
    pairs=geometry.pairs();movable=set()
    from robot_arm.kitchen.contact import object_geom_ids
    object_geoms={name:object_geom_ids(platform.scene,name) for name in platform.scene.objects}
    for geoms in object_geoms.values():movable.update(geoms)
    fixture_pairs=np.array([p for p in pairs if
        (int(p[0]) in geometry.robot and int(p[1]) not in geometry.robot|movable) or
        (int(p[1]) in geometry.robot and int(p[0]) not in geometry.robot|movable)],dtype=int).reshape(-1,2)
    scratch=mujoco.MjData(m);joint_ids=data['metadata']['arm_qpos_indices']
    fixtures=set(geometry.active)-geometry.robot-movable
    payload_pairs={name:np.array([(a,b) for a in geoms for b in fixtures if geometry.eligible(a,b)],dtype=int).reshape(-1,2)
                   for name,geoms in object_geoms.items()}
    accepted=0;rejected=0;mode_index=0;max_tcp=0.;payload_checked=0
    for index in range(1,n):
        while mode_index+1<len(modes) and modes[mode_index+1][0]<index:mode_index+=1
        mode=modes[mode_index][1];contract=mode.get('contact_contract') or {}
        if index in protected or mode.get('guarded_gripper') or contract.get('phase') in ('device','acquire'):continue
        delta=proposed[index,ARM_COLUMNS]-points[index,ARM_COLUMNS]
        peak=np.max(np.abs(delta))
        if peak<1e-8:continue
        delta*=min(1.,.008/peak)
        state_index=min(index-1,len(data['states/qpos'])-1)
        baseline=data['states/qpos'][state_index].copy();baseline[joint_ids]=points[index,ARM_COLUMNS]
        distance=geometry.distances(baseline,fixture_pairs) if len(fixture_pairs) else np.array([])
        candidate=baseline.copy();candidate[joint_ids]+=delta
        bounds=geometry.motion_bound(baseline,candidate)
        if len(fixture_pairs) and np.any(distance-bounds[fixture_pairs[:,0]]-bounds[fixture_pairs[:,1]]<np.minimum(.002,distance-.00005)):
            rejected+=1;continue
        scratch.qpos[:]=baseline;mujoco.mj_kinematics(m,scratch)
        poses={s:tcp_pose(scratch,platform.scene.arms[s]) for s in ('left','right')}
        poses={s:(p.copy(),r.copy()) for s,(p,r) in poses.items()}
        frames={name:(scratch.xpos[platform.scene.objects[name]].copy(),scratch.xmat[platform.scene.objects[name]].reshape(3,3).copy())
                for name in contract.get('payload_ids',[]) if name in object_geoms}
        scratch.qpos[:]=candidate;mujoco.mj_kinematics(m,scratch)
        tcp=max(float(np.linalg.norm(tcp_pose(scratch,platform.scene.arms[s])[0]-poses[s][0])) for s in poses)
        if tcp>.002:rejected+=1;continue
        # Scratch-only rigid carry hypothesis includes fruit/carrier/food
        # against fixtures; never attaches or corrects runtime objects.
        side=contract.get('arm')
        if contract.get('phase')=='carry' and side in poses and frames:
            old_p,old_r=poses[side];new_p,new_r=tcp_pose(scratch,platform.scene.arms[side])
            carried=candidate.copy();payload_ok=True
            for name,(position,rotation) in frames.items():
                body=platform.scene.objects[name];joint=int(m.body_jntadr[body])
                if m.jnt_type[joint]!=mujoco.mjtJoint.mjJNT_FREE:continue
                adr=int(m.jnt_qposadr[joint]);carried[adr:adr+3]=new_p+new_r@old_r.T@(position-old_p)
                mujoco.mju_mat2Quat(carried[adr+3:adr+7],(new_r@old_r.T@rotation).ravel())
            for name in frames:
                pairs=payload_pairs[name]
                if not len(pairs):continue
                original=geometry.distances(baseline,pairs)
                for hypothesis in (geometry.midpoint(baseline,carried),carried):
                    if np.any(geometry.distances(hypothesis,pairs)<np.minimum(.002,original-.00005)):
                        payload_ok=False;break
                if not payload_ok:break
            payload_checked+=1
            if not payload_ok:rejected+=1;continue
        result[index-1,ARM_COLUMNS]=candidate[joint_ids];accepted+=1;max_tcp=max(max_tcp,tcp)
    return result,{'blended_samples':accepted,'clearance_or_tcp_rejections':rejected,
        'maximum_tcp_blend_m':max_tcp,'joint_blend_limit_rad':.008,'tcp_blend_limit_m':.002,
        'rigid_payload_fixture_samples':payload_checked}
