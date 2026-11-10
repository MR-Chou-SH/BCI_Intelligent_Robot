"""Fixed physical normalization and task-relevant METRA features."""
import numpy as np
from robot_arm.kitchen.limits import joint_control_limits
from robot_arm.kitchen.action_codec import encode
from integration.kitchen.data.lerobot_export import pack16


class ObservationLayout:
    def __init__(self, reader, scene, case):
        self.reader, self.case = reader, case
        self.ids = reader.ids
        self.target_id = case.get('fruit') or (case['meal']+'_container' if case.get('meal') else 'wood_tray')
        self.target_index = self.ids.index(self.target_id)
        self.tray_index = self.ids.index('wood_tray')
        lows, highs = [], []
        for side in ('left','right'):
            lo,hi=joint_control_limits(scene.model,scene.arms[side])
            lows.extend([*lo, *([-3.]*7), 0., -2.,-2.,0., *([-1.]*4)])
            highs.extend([*hi, *([3.]*7), .1, 2.,2.,2., *([1.]*4)])
        for _ in self.ids:
            lows.extend([-2.,-2.,0., *([-1.]*4), *([-2.]*3), *([-10.]*3)])
            highs.extend([2.,2.,2., *([1.]*4), *([2.]*3), *([10.]*3)])
        self.reference_slice = slice(len(lows),len(lows)+16)
        for side in ('left','right'):
            lo,hi=joint_control_limits(scene.model,scene.arms[side])
            lows.extend([*lo,0.]);highs.extend([*hi,1.])
        self.device_indices=[i for i,j in enumerate(reader.joints) if j is not None]
        for i in self.device_indices:
            j=reader.joints[i];lo,hi=scene.model.jnt_range[j]
            lows.extend([lo,-5.]);highs.extend([hi,5.])
        # cleaned/heated flags; wash/heat counters; goal one-hot; task hold progress.
        n=2*len(self.ids)+4+2+len(self.ids)+1
        lows.extend([0.]*n);highs.extend([1.]*n)
        self.diversity_slice=slice(len(lows),len(lows)+6)
        lows.extend([-2.]*6);highs.extend([2.]*6)
        self.low=np.asarray(lows,dtype=np.float32);self.high=np.asarray(highs,dtype=np.float32)

    def raw(self, state, monitor):
        q,g=encode(state['reference'])
        goal=np.zeros(len(self.ids));goal[self.target_index]=1
        progress=monitor.quality.current.get(monitor.quality.phase+':'+self.target_id+':tray',0.)/2
        return np.concatenate([state['robot'].ravel(),state['objects'].ravel(),pack16(q,g),
            state['object_joint'][self.device_indices].ravel(),state['object_flags'].ravel().astype(float),
            np.minimum(state['clean_seconds']/2,1),[min(state['heat_seconds']/5,1),float(state['button_latched'])],
            goal,[min(progress,1)],self.diversity(state)]).astype(np.float32)

    def normalize(self, values):
        values=np.asarray(values,dtype=np.float32)
        if values.shape[-1:]!=self.low.shape or not np.isfinite(values).all():
            raise ValueError('Invalid privileged observation vector')
        return np.clip(2*(values-self.low)/(self.high-self.low)-1,-1,1).astype(np.float32)

    def diversity(self, state):
        # A fixed scene's relative target/TCP motion; no clock, UID, action or idle arm.
        target=state['objects'][self.target_index,:3]
        tray=state['objects'][self.tray_index,:3]
        tcp=state['robot'][0,15:18]
        return np.r_[tcp-target,target-tray].astype(np.float32)

    def schema(self):
        return {'schema':'kitchen_rl_observation_v1','dimension':len(self.low),
                'low':self.low.tolist(),'high':self.high.tolist(),'target_id':self.target_id,
                'object_ids':self.ids,'diversity_dimension':6,
                'tcp_quaternion_convention':'native_body_xquat_wxyz_v1',
                'normalization':'fixed physical bounds; [-1,1]; independent of demonstration moments'}
