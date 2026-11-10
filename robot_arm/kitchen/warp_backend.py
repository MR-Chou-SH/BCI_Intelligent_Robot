"""Optional batched MuJoCo Warp physics; original scene/model is never modified."""
import copy
from pathlib import Path
import numpy as np
import mujoco
from .scene_identity import compiled_model_sha256


class WarpBackend:
    def __init__(self, scene, *, nworld=16, device='cuda:0', nconmax=256, njmax=512,
                 allow_noslip_variant=False, box_foot_proxy=False):
        original=int(scene.model.opt.noslip_iterations)
        if original and not allow_noslip_variant:
            raise ValueError('MuJoCo Warp3.12 does not implement noslip. '
                'Original model unchanged; --allow-noslip-variant is required for the separate training-only copy.')
        import torch
        import warp as wp
        import mujoco_warp as mjw
        if not device.startswith('cuda') or not torch.cuda.is_available():
            raise RuntimeError('Warp RL requires CUDA; no silent CPU fallback')
        self.wp,self.mjw,self.torch=wp,mjw,torch
        wp.config.kernel_cache_dir=str(Path(__file__).resolve().parents[2]/'.cache/kitchen_rl/warp')
        wp.init()
        self.device=device;self.nworld=int(nworld);self.scene=scene
        if self.nworld<1:raise ValueError('nworld must be positive')
        self.cpu_model=copy.copy(scene.model)
        if original:
            self.cpu_model.opt.noslip_iterations=0
        if box_foot_proxy:
            if not allow_noslip_variant:raise ValueError('Contact proxy requires explicit training-variant opt-in')
            # Inscribed collision proxy only in the training copy. Preserve
            # original mass/inertia and bottom height; original MJCF stays intact.
            foot=self.cpu_model.geom('apple_foot').id
            radius,half_height=self.cpu_model.geom_size[foot,:2]
            self.cpu_model.geom_type[foot]=mujoco.mjtGeom.mjGEOM_BOX
            self.cpu_model.geom_size[foot]=[radius/np.sqrt(2),radius/np.sqrt(2),half_height]
        self.identity={'backend':'mujoco_warp','mujoco_version':mujoco.__version__,
            'mujoco_warp_version':mjw.__version__,'warp_version':wp.__version__,
            'original_compiled_model_sha256':scene.compiled_model_sha256,
            'training_compiled_model_sha256':compiled_model_sha256(self.cpu_model),
            'physics_variant':('noslip_disabled_box_foot_training_only_v1' if box_foot_proxy
                               else 'noslip_disabled_training_only_v1' if original else 'original'),
            'apple_foot_box_proxy':bool(box_foot_proxy),
            'original_noslip_iterations':original,'training_noslip_iterations':int(self.cpu_model.opt.noslip_iterations),
            'nworld':self.nworld,'nconmax':nconmax,'njmax':njmax,
            'export_requirement':'Re-execute and qualify on original CPU model; GPU trajectory is not CPU replay evidence'}
        data=mujoco.MjData(self.cpu_model);mujoco.mj_copyData(data,self.cpu_model,scene.data)
        with wp.ScopedDevice(device):
            self.model=mjw.put_model(self.cpu_model)
            self.data=mjw.put_data(self.cpu_model,data,nworld=self.nworld,nconmax=nconmax,njmax=njmax)
        self.qpos=wp.to_torch(self.data.qpos);self.qvel=wp.to_torch(self.data.qvel)
        self.ctrl=wp.to_torch(self.data.ctrl);self.tick=torch.zeros(self.nworld,dtype=torch.int64,device=device)

    def step_controls(self, controls, *, substeps=1):
        """Native actuator ctrl; policy targets must first pass the bounded adapter."""
        controls=self.torch.as_tensor(controls,dtype=self.ctrl.dtype,device=self.device)
        if controls.shape!=self.ctrl.shape or not self.torch.isfinite(controls).all():
            raise ValueError('Expected finite batched actuator controls')
        self.ctrl.copy_(controls)
        with self.wp.ScopedDevice(self.device):
            for _ in range(int(substeps)):self.mjw.step(self.model,self.data)
        self.tick+=int(substeps)

    def download(self, world=0):
        if not 0<=world<self.nworld:raise ValueError('Invalid world index')
        result=mujoco.MjData(self.cpu_model)
        with self.wp.ScopedDevice(self.device):
            self.mjw.get_data_into(result,self.cpu_model,self.data,world_id=world)
        return result

    def restore_worlds(self, worlds, states):
        """Reset only: copy native initial state fields, then update kinematics."""
        ids=self.torch.as_tensor(worlds,dtype=self.torch.long,device=self.device)
        if len(ids)!=len(states):raise ValueError('One reset state per world required')
        for name in ('qpos','qvel','qacc_warmstart','ctrl','qfrc_applied','xfrc_applied'):
            destination=self.wp.to_torch(getattr(self.data,name))
            values=np.stack([getattr(s,name) for s in states])
            destination[ids]=self.torch.as_tensor(values,dtype=destination.dtype,device=self.device)
        self.wp.to_torch(self.data.time)[ids]=self.torch.as_tensor([s.time for s in states],device=self.device)
        self.tick[ids]=self.torch.as_tensor([round(s.time/.002) for s in states],device=self.device)
        with self.wp.ScopedDevice(self.device):self.mjw.forward(self.model,self.data)

    def check_buffers(self):
        count=int(self.data.nacon.numpy()[0]);constraints=self.data.nefc.numpy()
        if count>self.data.naconmax or np.any(constraints>self.data.njmax):
            raise RuntimeError('Warp contact/constraint buffer overflow; increase capacities explicitly')
        if not self.torch.isfinite(self.qpos).all() or not self.torch.isfinite(self.qvel).all():
            raise RuntimeError('Nonfinite GPU physics')
        return {'contacts':count,'maximum_constraints':int(constraints.max())}
