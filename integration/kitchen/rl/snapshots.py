"""Full training reset state; never used as an intermediate policy correction."""
import copy
import mujoco
import numpy as np
from integration.kitchen.data.replay import restore


def capture(platform, monitor, rng):
    c=platform.controller
    if c.frame_step or platform.queue:
        raise ValueError('Snapshots require an idle25Hz control boundary')
    spec=mujoco.mjtState.mjSTATE_INTEGRATION
    state=np.empty(mujoco.mj_stateSize(platform.scene.model,spec))
    mujoco.mj_getState(platform.scene.model,platform.scene.data,state,spec)
    return {'schema':'kitchen_rl_reset_state_v1','layout':platform.scene.layout_id,
        'compiled_model_sha256':platform.scene.compiled_model_sha256,'control_hash':platform.control['control_hash'],
        'state_spec':int(spec),'integration_state':state.tolist(),
        'qfrc_bias':platform.scene.data.qfrc_bias.tolist(),
        'controller':{**{n:getattr(c,n).tolist() for n in ('previous','accepted','reference')},
            'tick':c.tick,'frame_step':c.frame_step,'stopped':c.stopped,'stop_reason':c.stop_reason},
        'devices':copy.deepcopy(c.devices.snapshot()),'case':monitor.case,
        'monitor':{n:list(getattr(monitor,n)) if isinstance(getattr(monitor,n),set) else copy.deepcopy(getattr(monitor,n)) for n in ('index','completed','success','failure_reason',
            'pending_reason','initial_heated','initial_heating','initial_tray_support','initial_meal_on_tray','payloads')},
        'quality':copy.deepcopy(monitor.quality.report()),'rng':copy.deepcopy(rng.bit_generator.state)}


def apply(platform, monitor, rng, snapshot, *, new_task_episode=False):
    if (snapshot['schema']!='kitchen_rl_reset_state_v1' or snapshot['layout']!=platform.scene.layout_id
            or snapshot['compiled_model_sha256']!=platform.scene.compiled_model_sha256
            or snapshot['control_hash']!=platform.control['control_hash'] or snapshot['case']!=monitor.case):
        raise ValueError('Reset state model/control/task mismatch')
    restore(platform.scene,snapshot)
    platform.scene.data.qfrc_bias[:]=snapshot['qfrc_bias']
    c=platform.controller
    for n in ('previous','accepted','reference'):setattr(c,n,np.asarray(snapshot['controller'][n],dtype=float).copy())
    for n in ('tick','frame_step','stopped','stop_reason'):setattr(c,n,snapshot['controller'][n])
    c.devices.restore(snapshot['devices']);platform.queue=[];platform.execution=None
    for n,v in snapshot['monitor'].items():setattr(monitor,n,set(v) if n=='initial_heated' else copy.deepcopy(v))
    q=monitor.quality;s=snapshot['quality']
    q.phase=monitor.skills[min(monitor.index,len(monitor.skills)-1)]
    q.last_tick=s['last_tick'];q.samples=s['samples'];q.current=copy.deepcopy(s['current_windows_s'])
    q.maximum=copy.deepcopy(s['maximum_windows_s']);q.failures=copy.deepcopy(s['controller_failures'])
    q.tray_tilt_peak=s['tray_tilt_peak_rad']
    rng.bit_generator.state=copy.deepcopy(snapshot['rng'])
    if new_task_episode:
        # Curriculum starts a NEW task episode at the old physical state. Do not
        # inherit already-earned success hold seconds and terminate immediately.
        monitor.index=0;monitor.completed=[];monitor.success=False;monitor.failure_reason=None;monitor.pending_reason=None
        q.phase=monitor.skills[0];q.samples=0;q.last_tick=c.tick;q.current={};q.maximum={};q.failures=[];q.tray_tilt_peak=0.
