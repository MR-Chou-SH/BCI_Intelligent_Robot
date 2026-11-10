"""L1-apple vector environment on an explicitly tagged Warp training variant."""
import numpy as np
import mujoco
from robot_arm.kitchen.warp_backend import WarpBackend
from robot_arm.kitchen.warp_control import WarpControl
from robot_arm.kitchen.gripper import fingertip_gap
from robot_arm.kitchen.contact import object_geom_ids
from integration.kitchen.data.replay import restore
from .environment import PrivilegedEnvironment


class WarpAppleEnvironment:
    def __init__(self, *, nworld=16, seed=0, horizon=750, allow_noslip_variant=False, box_foot_proxy=False):
        import torch
        import warp as wp
        self.t,self.wp=torch,wp
        self.prototype=PrivilegedEnvironment(seed=seed,horizon=horizon)
        self.case=self.prototype.case;self.reader=self.prototype.reader;self.layout=self.prototype.layout
        self.rng=np.random.default_rng(seed);self.horizon=horizon;self.nworld=nworld
        self.b=WarpBackend(self.prototype.platform.scene,nworld=nworld,allow_noslip_variant=allow_noslip_variant,
                           box_foot_proxy=box_foot_proxy)
        self.control=WarpControl(self.b,self.prototype.platform.controller.reference)
        self.control.build_graph()
        self.device=self.b.device
        self.steps=torch.zeros(nworld,dtype=torch.long,device=self.device)
        self.time_limits=torch.full((nworld,),int(horizon),dtype=torch.long,device=self.device)
        self.holds=torch.zeros(nworld,device=self.device)
        self.done=torch.zeros(nworld,dtype=torch.bool,device=self.device)
        self.low=torch.as_tensor(self.layout.low,device=self.device);self.high=torch.as_tensor(self.layout.high,device=self.device)
        self.seeds=np.zeros(nworld,dtype=np.uint32)
        self.width_calibration={}
        m=self.prototype.platform.scene.model
        for side,arm in self.prototype.platform.scene.arms.items():
            d=mujoco.MjData(m);mujoco.mj_copyData(d,m,self.prototype.platform.scene.data)
            mujoco.mj_kinematics(m,d)
            start=fingertip_gap(m,d,arm)['inner_gap_m'];base=d.qpos[list(arm.fingers)].copy();slopes=[]
            for finger in arm.fingers:
                d.qpos[finger]+=.001;mujoco.mj_kinematics(m,d)
                slopes.append((fingertip_gap(m,d,arm)['inner_gap_m']-start)/.001)
                d.qpos[finger]-=.001;mujoco.mj_kinematics(m,d)
            self.width_calibration[side]=(start,torch.as_tensor(base,dtype=torch.float32,device=self.device),
                                          torch.as_tensor(slopes,dtype=torch.float32,device=self.device))
        self.target_body=m.body('apple').id;self.tray_body=m.body('wood_tray').id
        self.apple_geoms=sorted(object_geom_ids(self.prototype.platform.scene,'apple'))
        self.all_pads=[g for arm in self.prototype.platform.scene.arms.values() for g in arm.pad_geoms]
        self.tray_geom=m.geom('tray_bottom').id
        self.contact_ids=wp.array(np.arange(self.b.data.naconmax,dtype=np.int32),device=self.device)
        self.contact_forces=wp.empty(self.b.data.naconmax,dtype=wp.spatial_vector,device=self.device)
        self.robot_geoms=torch.tensor([i for i in range(m.ngeom) if m.body(int(m.geom_bodyid[i])).name.startswith(('left_','right_'))],device=self.device)
        self.base_geoms=torch.tensor([i for i in range(m.ngeom) if m.body(int(m.geom_bodyid[i])).name.endswith('fr3_link0')],device=self.device)
        self.reset()

    def _state(self):
        t=self.t;d=self.control.scratch;m=self.b.cpu_model
        self.control.scratch_qpos.copy_(self.b.qpos)
        self.wp.to_torch(d.qvel).copy_(self.b.qvel)
        with self.wp.ScopedDevice(self.device):
            self.b.mjw.kinematics(self.b.model,d);self.b.mjw.com_pos(self.b.model,d);self.b.mjw.com_vel(self.b.model,d)
        pos=self.wp.to_torch(d.xpos);quat=self.wp.to_torch(d.xquat);rot=self.wp.to_torch(d.xmat)
        cv=self.wp.to_torch(d.cvel);com=self.wp.to_torch(d.subtree_com)
        ids=self.reader.bodies;roots=m.body_rootid[ids].tolist()
        linear=cv[:,ids,3:]+t.linalg.cross(cv[:,ids,:3],pos[:,ids]-com[:,roots])
        objects=t.cat([pos[:,ids],quat[:,ids],linear,cv[:,ids,:3]],dim=-1)
        robots=[]
        for side,arm in self.prototype.platform.scene.arms.items():
            start,base,slopes=self.width_calibration[side]
            width=(start+((self.b.qpos[:,list(arm.fingers)]-base)*slopes).sum(-1)).clamp_min(0)
            tcp=pos[:,arm.tcp_body]+rot[:,arm.tcp_body]@t.tensor(arm.tcp_offset,device=self.device)
            robots.append(t.cat([self.b.qpos[:,list(arm.qpos)],self.b.qvel[:,list(arm.dofs)],width[:,None],tcp,quat[:,arm.tcp_body]],-1))
        reference=self.control.reference.clone();reference[:,[7,15]]=1-reference[:,[7,15]]/.05
        device=[]
        for i in self.layout.device_indices:
            joint=self.reader.joints[i]
            device.extend([self.b.qpos[:,int(m.jnt_qposadr[joint])],self.b.qvel[:,int(m.jnt_dofadr[joint])]])
        device=t.stack(device,-1)
        flags=t.zeros((self.nworld,len(self.reader.ids)*2+4+2),device=self.device)
        goal=t.zeros((self.nworld,len(self.reader.ids)),device=self.device);goal[:,self.layout.target_index]=1
        target=objects[:,self.layout.target_index,:3];tray=objects[:,self.layout.tray_index,:3]
        relative=t.cat([robots[0][:,15:18]-target,target-tray],-1)
        raw=t.cat([*robots,objects.flatten(1),reference,device,flags,goal,
                   (self.holds/2).clamp(0,1)[:,None],relative],-1)
        self.latest_objects=objects
        return (2*(raw-self.low)/(self.high-self.low)-1).clamp(-1,1)

    def _task(self):
        t=self.t;d=self.b.data;m=self.b.cpu_model
        # Contact facts use the same native post-step caches as ExpertQuality.
        with self.wp.ScopedDevice(self.device):
            self.b.mjw.contact_force(self.b.model,d,self.contact_ids,False,self.contact_forces)
        geom=self.wp.to_torch(d.contact.geom);world=self.wp.to_torch(d.contact.worldid).long().clamp(0,self.nworld-1)
        valid=t.arange(d.naconmax,device=self.device)<self.wp.to_torch(d.nacon)[0]
        force=self.wp.to_torch(self.contact_forces)[:,0].clamp_min(0)
        apple=t.tensor(self.apple_geoms,device=self.device);pads=t.tensor(self.all_pads,device=self.device)
        a0=t.isin(geom[:,0],apple);a1=t.isin(geom[:,1],apple)
        normal=self.wp.to_torch(d.contact.frame)[:,0,:]
        sign=t.where(a0,-1.,1.);up=normal[:,2]*sign
        pos=self.wp.to_torch(d.xpos);contact_pos=self.wp.to_torch(d.contact.pos)
        pair=(a0 & (geom[:,1]==self.tray_geom))|(a1 & (geom[:,0]==self.tray_geom))
        support=t.zeros(self.nworld,device=self.device)
        qualifies=valid & pair & (up>.5) & (contact_pos[:,2]<pos[world,self.target_body,2]+.005)
        support.scatter_add_(0,world,t.where(qualifies,force*up,0.))
        touching=t.zeros(self.nworld,device=self.device)
        touch=valid & ((a0 & t.isin(geom[:,1],pads))|(a1 & t.isin(geom[:,0],pads)))
        touching.scatter_add_(0,world,touch.float())
        cv=self.wp.to_torch(d.cvel)[:,self.target_body];root=int(m.body_rootid[self.target_body])
        linear=cv[:,3:]+t.linalg.cross(cv[:,:3],pos[:,self.target_body]-self.wp.to_torch(d.subtree_com)[:,root])
        quiet=(linear.norm(dim=-1)<=.02)&(cv[:,:3].norm(dim=-1)<=.05)
        rot=self.wp.to_torch(d.xmat)[:,self.tray_body].transpose(-1,-2)
        footprint=t.ones(self.nworld,dtype=t.bool,device=self.device)
        gp=self.wp.to_torch(d.geom_xpos);gr=self.wp.to_torch(d.geom_xmat)
        for g in self.apple_geoms:
            center=(rot@(gp[:,g]-pos[:,self.tray_body])[:,:,None]).squeeze(-1)
            local=rot@gr[:,g];size=m.geom_size[g];kind=int(m.geom_type[g])
            if kind==mujoco.mjtGeom.mjGEOM_SPHERE:extent=t.full_like(center,float(size[0]))
            elif kind==mujoco.mjtGeom.mjGEOM_BOX:extent=local.abs()@t.tensor(size,dtype=local.dtype,device=self.device)
            elif kind==mujoco.mjtGeom.mjGEOM_CYLINDER:
                axis=local[:,:,2];extent=axis.abs()*float(size[1])+float(size[0])*(1-axis.square()).clamp_min(0).sqrt()
            else:raise NotImplementedError('Unsupported GPU apple collision envelope')
            lower=center-extent;upper=center+extent
            footprint &= (lower[:,0]>=-.233)&(lower[:,1]>=-.153)&(upper[:,0]<=.233)&(upper[:,1]<=.153)
        predicate=(support>.25)&quiet&(touching==0)
        self.holds=t.where(predicate,self.holds+.04,0.)
        success=(self.holds>=2.-1e-6)&footprint
        self.task_evidence={'support_force_n':support,'pad_contacts':touching,'linear_speed_m_s':linear.norm(dim=-1),
            'angular_speed_rad_s':cv[:,:3].norm(dim=-1),'footprint_inside':footprint,'stable_seconds':self.holds,
            'target_position':pos[:,self.target_body]}
        robot0=t.isin(geom[:,0],self.robot_geoms);robot1=t.isin(geom[:,1],self.robot_geoms)
        bases=(t.isin(geom[:,0],self.base_geoms)&~robot1)|(t.isin(geom[:,1],self.base_geoms)&~robot0)
        allowed=touch # pads/apple interactions are allowed forL1.
        illegal=valid & (robot0|robot1) & ~bases & ~allowed
        contact_sum=t.zeros(self.nworld,device=self.device)
        contact_sum.scatter_add_(0,world,t.where(illegal,force,0.))
        return success,contact_sum.clamp_max(100.)

    def observation(self):return self._state().detach().cpu().numpy()

    def reset(self, worlds=None, snapshots=None, seeds=None):
        worlds=list(range(self.nworld)) if worlds is None else list(map(int,worlds))
        states=[];references=[]
        if snapshots is not None and len(snapshots)!=len(worlds):raise ValueError('One snapshot per reset world')
        for offset,world in enumerate(worlds):
            if snapshots is None:
                seed=int(seeds[offset]) if seeds is not None else int(self.rng.integers(2**32))
                self.prototype.reset(seed=seed);saved=self.prototype.capture();self.seeds[world]=seed
            else:saved=snapshots[offset]
            data=mujoco.MjData(self.b.cpu_model)
            restore(type('View',(),{'model':self.b.cpu_model,'data':data})(),saved)
            states.append(data);references.append(saved['controller']['reference'])
        self.b.restore_worlds(worlds,states)
        ids=self.t.tensor(worlds,dtype=self.t.long,device=self.device)
        refs=self.t.tensor(np.asarray(references),dtype=self.t.float32,device=self.device)
        self.control.reference[ids]=refs;self.control.previous[ids]=refs;self.control.accepted[ids]=refs
        self.steps[ids]=0;self.holds[ids]=0;self.done[ids]=False
        self.time_limits[ids]=self.horizon
        return self.observation()

    def step(self, actions):
        if self.done.any():raise RuntimeError('Reset ended worlds before stepping again')
        a=self.t.as_tensor(actions,dtype=self.t.float32,device=self.device)
        if a.shape!=(self.nworld,8) or not self.t.isfinite(a).all():raise ValueError('Expected finite(nworld,8) actions')
        a=a.clamp(-1,1);target=self.control.reference.clone()
        target[:,:7]+=a[:,:7]*(.7/25);target[:,7]=.025*(1-a[:,7])
        self.control.advance(target);self.steps+=1
        self.b.check_buffers()
        success,contact=self._task();truncated=(self.steps>=self.time_limits)&~success
        self.done=success|truncated
        obs=self.observation()
        # Sparse task reward and separately reported safety cost; no hidden
        # distance shaping or skill reward before the episode's outcome is known.
        reward=success.float()-1e-4*contact
        return obs,reward.cpu().numpy(),success.cpu().numpy(),truncated.cpu().numpy(),{
            'success':success.cpu().numpy(),'contact_cost':contact.cpu().numpy(),
            'final_obs':obs.copy(),'accepted_native_targets':self.control.accepted.detach().cpu().numpy()}

    def close(self):self.prototype.close()
