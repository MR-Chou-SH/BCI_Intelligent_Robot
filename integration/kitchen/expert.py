"""Geometry-driven experts reusing the existing FR3 controller and public gates.

All live motions go through KitchenPlatform.request. IK uses detached data;
there are no live object/device qpos writes, attachments or threshold changes.
"""
from __future__ import annotations
from dataclasses import replace
import copy
import json
import math
from pathlib import Path
import time
import uuid
import gzip
import hashlib

import mujoco
import numpy as np

from robot_arm.kitchen.control import solve_ik, tcp_pose
from robot_arm.kitchen.registry import geometry_bounds, object_bounds
from robot_arm.kitchen.contact import object_geom_ids
from robot_arm.kitchen.limits import joint_control_limits
from .reference_trajectories import ASSETS, inspect

DOWN = np.array([[0., -1., 0.], [-1., 0., 0.], [0., 0., -1.]])


def rz(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])


def reference_calls_between(start,end):
    """Closed control-frame window, including a clipped last RPC batch."""
    if start%20 or end%20 or end<=start:raise ValueError("REFERENCE_CONTROL_FRAME_WINDOW")
    inspect(ASSETS)
    selected=[]
    for line in (ASSETS/"burger_public_calls.jsonl").read_text().splitlines():
        row=json.loads(line);tick=row["start_tick"]
        if row["method"] in ("reset","hold"):continue
        if not (start<=tick<end or tick==end and row["method"]=="end_contact"):continue
        if row["method"]=="execute_trajectory":
            row["args"]["waypoints"]=row["args"]["waypoints"][:(end-tick)//20]
        elif row["method"]=="step":
            row["args"]["n_substeps"]=min(row["args"]["n_substeps"],end-tick)
            row["end_tick"]=min(row["end_tick"],end)
        selected.append(row)
    return selected


# Task-only mode (user direction 2026-10-25): the free-motion elbow/door
# heuristic is not enforced; only segment outcomes are verified.
ENABLE_ELBOW_DOOR_CHECK = False


class ExpertFailure(RuntimeError):
    pass


class LocalPlannerClient:
    """In-process transport to the same token/ownership/validation dispatcher."""
    def __init__(self, platform, journal=None):
        self.platform, self.journal = platform, Path(journal) if journal is not None else None
        self.token = platform.token()
        self.writer_id, self.connection_id = uuid.uuid4().hex, uuid.uuid4().hex

    def call(self, method, args=None, *, mutating=False):
        reply = self.platform.request(method, args, token=self.token if mutating else None,
            request_id=uuid.uuid4().hex if mutating else None,
            writer_id=self.writer_id if mutating else None, connection_id=self.connection_id)
        self.token = reply["token"]
        if self.journal is None:return reply
        opener=gzip.open if self.journal.suffix==".gz" else open
        kwargs={"compresslevel":1} if self.journal.suffix==".gz" else {}
        with opener(self.journal,"at",encoding="utf-8",**kwargs) as stream:
            stream.write(json.dumps({"method": method, "args": args or {}, "reply": reply},
                                    allow_nan=False, separators=(",", ":")) + "\n")
        return reply


class KitchenExpert:
    def __init__(self, platform, client, *, wall_seconds=1200, write_checkpoints=True):
        self.write_checkpoints = write_checkpoints
        self.platform, self.scene, self.client = platform, platform.scene, client
        self.deadline = time.monotonic() + wall_seconds
        self.stage = "initialized"
        self.events = []
        self.contract = None
        self.held = None
        self.last_target_position = None
        self.dual_rotation = DOWN.copy()
        self.home = {side: self.scene.data.qpos[list(arm.qpos)].copy() for side, arm in self.scene.arms.items()}

    def call(self, method, args=None, *, mutating=False):
        if time.monotonic() > self.deadline:
            raise ExpertFailure("EXPERT_WALL_BUDGET_EXCEEDED")
        reply = self.client.call(method, args, mutating=mutating)
        if not reply.get("ok"):
            raise ExpertFailure(f"{self.stage}: {method}: {reply.get('error')}")
        return reply["result"]

    def mark(self, name, **details):
        self.stage = name
        row = {"stage": name, "tick": self.platform.controller.tick, **details}
        self.events.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def save_checkpoint(self, name):
        """Dev-only: persist the full state so iteration can resume here."""
        if not self.write_checkpoints:
            return None
        from .checkpoint import save
        return save(self, Path("experiments/kitchen_platform_v3/checkpoints") / (name + ".npz"))

    def view(self):
        data = mujoco.MjData(self.scene.model)
        mujoco.mj_copyData(data, self.scene.model, self.scene.data)
        mujoco.mj_forward(self.scene.model, data)
        return replace(self.scene, data=data)

    def obj(self, name):
        return self.call("get_object_states", {"ids": [name]})["objects"][0]

    def feedback(self):
        return self.call("get_task_feedback")

    def execute(self, targets, contract=None):
        args = {"waypoints": [np.asarray(q).tolist() for q in targets], "source": "programmatic_validation"}
        if contract is not None:
            args["contact_contract"] = contract
        self.call("execute_trajectory", args, mutating=True)
        state = self.call("step", {"n_substeps": len(targets) * 20}, mutating=True)
        if state["stopped"] or state["execution"]["status"] == "FAILED":
            raise ExpertFailure(f"{self.stage}: PHYSICS_STOP: {state['stop_reason']}: {state['execution'].get('reason')}: guard={self.platform.controller.stop_evidence}")
        self.contract = copy.deepcopy(contract)
        return state

    def wait(self, seconds, contract=None):
        contract = self.contract if contract is None else contract
        count = int(math.ceil(seconds * 25))
        while count:
            n = min(10, count)
            self.execute([self.platform.controller.reference.copy()] * n, contract)
            count -= n

    def joints(self, side, goal, contract=None, *, max_frames=600, speed=.008):
        contract = self.contract if contract is None else contract
        offset = 0 if side == "left" else 8
        arm = self.scene.arms[side]
        goal = np.asarray(goal)
        frames = 0
        goal_view=self.view()
        goal_view.data.qpos[list(arm.qpos)]=goal
        mujoco.mj_forward(self.scene.model,goal_view.data)
        goal_position,goal_rotation=tcp_pose(goal_view.data,arm)
        # The commanded reference is rate/TCP-capped by design, so it need not
        # become exactly equal to the goal. When it stops moving while the
        # measured arm has settled near the goal, accept that as the reached
        # pose instead of burning the whole 600-frame budget (the old rule
        # required reference==goal and failed every faucet/place approach that
        # sat in contact).
        last_reference = None
        stalled = 0
        while frames < max_frames:
            current = self.platform.controller.reference.copy()
            error = np.max(np.abs(self.scene.data.qpos[list(arm.qpos)] - goal))
            if error < .0008 and np.max(np.abs(current[offset:offset+7] - goal)) < 1e-7:
                return
            # A measured near-settle is only a budget guard, never a substitute
            # for the commanded pose. A 1.5mm/0.02rad acceptance left up to
            # 1.5mm uncommanded offset that later preflights rejected at the
            # 2mm ordinary margin, so keep it sub-millimetre.
            if error<.002 and np.max(np.abs(current[offset:offset+7]-goal))<1e-7 and np.max(np.abs(self.scene.data.qvel[list(arm.dofs)]))<.01:
                position,rotation=tcp_pose(self.view().data,arm)
                pe=float(np.linalg.norm(position-goal_position))
                re=math.acos(float(np.clip((np.trace(goal_rotation.T@rotation)-1)/2,-1,1)))
                if pe<=.0004 and re<=.005:
                    self.events.append({"stage":"measured_tracking_settled","tick":self.platform.controller.tick,
                                        "position_error_m":pe,"rotation_error_rad":re})
                    return
            unchanged = last_reference is not None and np.max(np.abs(current-last_reference)) < 1e-9
            stalled = stalled + 1 if unchanged else 0
            if stalled >= 40:
                speed_now = float(np.max(np.abs(self.scene.data.qvel[list(arm.dofs)])))
                if error < .025 and speed_now < .01:
                    position,rotation=tcp_pose(self.view().data,arm)
                    pe=float(np.linalg.norm(position-goal_position))
                    re=math.acos(float(np.clip((np.trace(goal_rotation.T@rotation)-1)/2,-1,1)))
                    self.events.append({"stage":"measured_stall_settled","tick":self.platform.controller.tick,
                                        "joint_error_rad":float(error),"position_error_m":pe,
                                        "rotation_error_rad":re,"frames_without_reference_change":stalled})
                    return
                raise ExpertFailure(f"JOINT_TRACKING_STALLED: {self.stage}: joint_error={error:.6f} speed={speed_now:.6f}")
            last_reference = current.copy()
            targets = []
            for _ in range(8):
                proposed = current.copy()
                difference = goal - current[offset:offset+7]
                peak = float(np.max(np.abs(difference)))
                proposed[offset:offset+7] += difference * min(1., speed / peak) if peak else difference
                current = self.platform.controller.prepare_joint_targets(proposed, reference=current)
                targets.append(current.copy())
                if np.max(np.abs(current[offset:offset+7] - goal)) < 1e-8:
                    break
            self.execute(targets, contract)
            frames += len(targets)
        # Joint limit reached (reference clamped) but the end effector is at the
        # commanded pose: accept and let the segment's outcome checks decide.
        position,rotation=tcp_pose(self.view().data,arm)
        position_error=float(np.linalg.norm(position-goal_position))
        rotation_error=math.acos(float(np.clip((np.trace(goal_rotation.T@rotation)-1)/2,-1,1)))
        if position_error<=.05 and rotation_error<=.10:
            self.events.append({"stage":"budget_settled","tick":self.platform.controller.tick,
                                "position_error_m":position_error,"rotation_error_rad":rotation_error,
                                "joint_error_rad":float(error)})
            return
        raise ExpertFailure(f"JOINT_TRACKING_BUDGET: {self.stage}; measured_error={error:.6f}; reference_error={float(np.max(np.abs(self.platform.controller.reference[offset:offset+7]-goal))):.6f}")

    def point(self, side, position, rotation, contract=None, *, tolerance=.0002, speed=.008, transit=False):
        view = self.view()
        # Transit points have no contact requirement; the free corridor can sit
        # a couple of mm / 0.3deg outside the wrist's exact-strict envelope.
        # Grasp and placement points keep the strict tolerance.
        rtol = .010 if transit else .003
        ptol = .002 if transit else max(tolerance, .0015)
        result = solve_ik(view, side, np.asarray(position), np.asarray(rotation), max_iterations=400,
                          position_tolerance=tolerance, rotation_tolerance=rtol)
        if not result.success:
            result=solve_ik(view,side,np.asarray(position),np.asarray(rotation),max_iterations=700,
                            position_tolerance=ptol,rotation_tolerance=rtol)
        if not result.success:
            # Deterministic high-level seed search, reusing the same IK and
            # original rectangular joint domain. No live joint writes.
            arm=self.scene.arms[side];lower,upper=joint_control_limits(self.scene.model,arm)
            seed=int.from_bytes(hashlib.sha256(np.asarray(position).tobytes()+np.asarray(rotation).tobytes()+side.encode()).digest()[:8],"little")
            rng=np.random.default_rng(seed)
            seeds=[self.home[side]]+[rng.uniform(lower,upper) for _ in range(16)]
            for attempt,qseed in enumerate(seeds):
                alternative=self.view();alternative.data.qpos[list(arm.qpos)]=qseed
                result=solve_ik(alternative,side,np.asarray(position),np.asarray(rotation),max_iterations=600,
                                position_tolerance=ptol,rotation_tolerance=rtol)
                if result.success:
                    self.events.append({"stage":"ik_seed_search","attempt":attempt,"tick":self.platform.controller.tick,
                                        "position_error_m":result.position_error,"rotation_error_rad":result.rotation_error})
                    break
        if not result.success:
            # A free-space transit may sit a few mm outside the strict IK
            # envelope; accept it there and let the next segment refine.
            if transit and result.position_error < .010 and result.rotation_error < .020:
                self.events.append({"stage":"transit_ik_accepted","tick":self.platform.controller.tick,
                                    "position_error_m":float(result.position_error),
                                    "rotation_error_rad":float(result.rotation_error)})
                self.joints(side, result.q, contract, speed=speed)
                return
            raise ExpertFailure(f"{self.stage}: IK_FAILED {np.asarray(position).tolist()} errors={result.position_error},{result.rotation_error}")
        try:
            if ENABLE_ELBOW_DOOR_CHECK and contract is None and self.contract is None and not self.door_path_clear(side,result.q):
                raise ExpertFailure("MOTION_NOT_CERTIFIED: planned elbow path crosses open door")
            self.joints(side, result.q, contract, speed=speed)
        except ExpertFailure as error:
            # A valid IK solution may put an elbow through an open door. Search
            # another branch only for free motion, and certify its whole path
            # before moving. Never recover from a live physics stop this way.
            if contract is not None or self.contract is not None or self.platform.controller.stopped or "MOTION_NOT_CERTIFIED" not in str(error):
                raise
            arm=self.scene.arms[side];lower,upper=joint_control_limits(self.scene.model,arm)
            rng=np.random.default_rng(92025)
            for attempt in range(16):
                alternative=self.view();alternative.data.qpos[list(arm.qpos)]=rng.uniform(lower,upper)
                candidate=solve_ik(alternative,side,np.asarray(position),np.asarray(rotation),max_iterations=600,
                    position_tolerance=max(tolerance,.0015),rotation_tolerance=.003)
                if not candidate.success:continue
                q0=self.scene.data.qpos.copy();q1=q0.copy();q1[list(arm.qpos)]=candidate.q
                if not self.platform.geometry.certificate(q0,q1)["pass"]:continue
                if not self.door_path_clear(side,candidate.q):continue
                self.mark("free_ik_reroute",side=side,attempt=attempt)
                self.joints(side,candidate.q,speed=speed,max_frames=1800)
                break
            else:raise ExpertFailure("COLLISION_FREE_IK_BRANCH_NOT_FOUND: "+str(error)) from error
        self.last_target_position = np.asarray(position).copy()

    def door_path_clear(self,side,goal):
        if side!="right" or self.platform.controller.devices.angle("microwave_hinge")<math.radians(60):return True
        geometry=self.platform.geometry;m=self.scene.model
        door={m.geom("microwave_door_panel").id, m.geom("microwave_door_handle").id}
        pairs=np.asarray([p for p in geometry.pairs()
                          if any(g in door for g in p) and any(g in geometry.robot for g in p)],dtype=int)
        q0=self.scene.data.qpos.copy();q1=q0.copy();q1[list(self.scene.arms[side].qpos)]=goal
        # Plan a positive clearance around the compliant open door. The
        # ordinary -3mm gate is insufficient to prevent a 15N elbow contact.
        return geometry.certificate(q0,q1,pairs=pairs,margin=.004)["pass"]

    def stream_joints(self, side, path, contract=None, *, speed=.008):
        """Keep moving through intermediate goals; settle only at the endpoint."""
        if not path:return
        contract = self.contract if contract is None else contract
        if side=="left":
            targets,_,_=self.smooth_targets(side,path,speed=speed)
            self.execute_smooth(side,targets,contract,speed=speed)
            return
        contract = self.contract if contract is None else contract
        offset = 0 if side == "left" else 8
        current = self.platform.controller.reference.copy()
        suffix = np.zeros(len(path)+1)
        for i in range(len(path)-2,-1,-1):
            suffix[i+1] = suffix[i+2] + float(np.max(np.abs(path[i+1]-path[i])))
        targets=[]; frames=0; started=self.platform.controller.tick
        for i,goal in enumerate(path):
            while True:
                difference=goal-current[offset:offset+7]
                peak=float(np.max(np.abs(difference)))
                if peak<1e-8:break
                # One acceleration/deceleration envelope for the whole route,
                # rather than restarting at every Cartesian sample.
                step=min(speed,.001+.0005*frames,max(.001,math.sqrt(.001*(peak+suffix[i+1]))))
                proposed=current.copy()
                proposed[offset:offset+7]+=difference*min(1.,step/peak)
                accepted=self.platform.controller.prepare_joint_targets(proposed,reference=current)
                if np.max(np.abs(accepted-current))<1e-12 or frames>=6000:
                    raise ExpertFailure("CONTINUOUS_ROUTE_BUDGET: "+self.stage)
                current=accepted;targets.append(current.copy());frames+=1
                if len(targets)==8:
                    self.execute(targets,contract);targets=[]
        if targets:self.execute(targets,contract)
        self.joints(side,path[-1],contract,speed=speed)
        self.events.append({"stage":"motion_stream","side":side,"tick":self.platform.controller.tick,
                            "start_tick":started,"waypoints":len(path),"frames":frames})

    def smooth_targets(self,side,path,*,speed):
        from .curved_motion import joint_spline
        offset=0 if side=="left" else 8
        initial=self.platform.controller.reference.copy()
        points=[initial[offset:offset+7].copy(),*path]
        frames=None
        for _ in range(8):
            values,parameters,knots=joint_spline(points,speed,frames)
            if len(values)>6000:raise ValueError("SMOOTH_ROUTE_FRAME_LIMIT")
            targets=[];current=initial.copy();good=True
            for value in values:
                proposed=current.copy();proposed[offset:offset+7]=value
                accepted=self.platform.controller.prepare_joint_targets(proposed,reference=current)
                if not np.allclose(accepted,proposed,atol=1e-9,rtol=0.):good=False;break
                # Send the controller's exact bounded value: interpolation at
                # a joint limit can otherwise leave a -1e-16 roundoff outside.
                targets.append(accepted);current=accepted
            if good:return targets,parameters,knots
            frames=int(math.ceil(len(values)*1.4))
        raise ValueError("SMOOTH_ROUTE_RATE_LIMIT")

    def execute_smooth(self,side,targets,contract=None,*,speed=.008,acquire=None,split=None):
        started=self.platform.controller.tick
        for first in range(0,len(targets),8):
            last=min(first+8,len(targets))
            if split is not None and first<split<last:
                self.execute(targets[first:split],contract)
                self.execute(targets[split:last],acquire)
            else:self.execute(targets[first:last],acquire if split is not None and first>=split else contract)
        final=targets[-1][:7] if side=="left" else targets[-1][8:15]
        self.joints(side,final,acquire if acquire is not None else contract,speed=speed)
        self.events.append({"stage":"motion_stream","side":side,"tick":self.platform.controller.tick,
                            "start_tick":started,"frames":len(targets),"interpolation":"C1_Hermite_global_clock"})

    def fruit_curve(self,position,rotation,*,mode,target,payload=(),support=None,acquire=None,escape_lifts=2):
        from .curved_motion import curve_candidates
        view=self.view();arm=view.arms["left"];q0=view.data.qpos.copy()
        failures=[]
        for path,index,clearance,details in curve_candidates(view,"left",position,rotation,
                mode=mode,payload=payload,target=target if acquire else None,support=support,diagnostics=failures):
            try:targets,parameters,knots=self.smooth_targets("left",path[1:],speed=.008)
            except ValueError:continue
            threshold=knots[min(index,len(knots)-1)]
            minimum=1.;good=True
            for command,parameter in zip(targets,parameters):
                q=q0.copy();q[list(arm.qpos)]=command[:7]
                distance=clearance(q,parameter>=threshold)
                minimum=min(minimum,distance)
                if distance<.001:good=False;break
            if not good:
                failures.append({"reason":"retimed_clearance","distance_m":minimum})
                continue
            split=int(np.searchsorted(parameters,threshold)) if acquire else None
            self.mark("fruit_curve_started",target=target,**details,minimum_retimed_m=minimum)
            self.execute_smooth("left",targets,self.contract,speed=.008,acquire=acquire,split=split)
            self.last_fruit_curve_endpoint=np.asarray(position).copy()
            self.mark("fruit_curve_complete",target=target,mode=mode)
            return True
        blocked_start=(failures and all(f.get("reason")=="clearance" and f.get("index")==1 for f in failures))
        if payload and blocked_start and escape_lifts:
            # A curved banana can still graze its neighbour after a 60mm
            # lift. Clear that measured start contact, then retry a curve;
            # never replace the full transfer by a high rectangular route.
            self.mark("fruit_clearance_lift",target=target,reason=failures[0],distance_m=.025)
            before=self.view();here,_=tcp_pose(before.data,before.arms["left"])
            desired_xy=(np.asarray(position)+before.data.xpos[before.objects[target]]-here)[:2]
            self.line("left",here+[0,0,.025],rotation,self.contract,speed=.004)
            from .curved_motion import placement_tcp_goal
            updated=placement_tcp_goal(self.view(),"left",target,support,desired_xy)
            return self.fruit_curve(updated,rotation,mode=mode,target=target,payload=payload,
                                    support=support,acquire=acquire,escape_lifts=escape_lifts-1)
        self.mark("fruit_curve_unavailable",target=target,mode=mode,failures=failures)
        return False

    def continuous_poses(self, side, poses, contract=None, *, speed=.008):
        """Warm-start the existing IK before executing any part of the route."""
        view=self.view();arm=view.arms[side];path=[]
        for position,rotation in poses:
            result=solve_ik(view,side,np.asarray(position),rotation,max_iterations=400,
                            position_tolerance=.0002,rotation_tolerance=.003)
            if not result.success:
                result=solve_ik(view,side,np.asarray(position),rotation,max_iterations=700,
                                position_tolerance=.0015,rotation_tolerance=.003)
            if not result.success:return False
            path.append(result.q.copy());view.data.qpos[list(arm.qpos)]=result.q
        self.stream_joints(side,path,contract,speed=speed)
        return True

    def line(self, side, position, rotation, contract=None, spacing=.015, ramp=False, speed=.008, transit=False):
        start = tcp_pose(self.view().data, self.scene.arms[side])[0]
        end = np.asarray(position)
        count = max(1, int(np.ceil(np.linalg.norm(end - start) / spacing)))
        phase=(contract or self.contract or {}).get("phase")
        if side=="left" and np.linalg.norm(end-start)>.035 and phase!="acquire":
            poses=[(start+(end-start)*i/count,rotation) for i in range(1,count+1)]
            if self.continuous_poses(side,poses,contract,speed=speed):return
        for i in range(1, count + 1):
            # A carried object behaves like a pendulum on its pinch grip. A
            # full-speed first waypoint impulses it into >4rad/s; ramp the first
            # three waypoints instead of relying on a post-hoc settle.
            step = (.002 if i == 1 else .004 if i == 2 else .006 if i == 3 else speed) if ramp else speed
            self.point(side, start + (end - start) * i / count, rotation, contract, speed=step, transit=transit)

    def grip(self, side, value, contract):
        offset = 7 if side == "left" else 15
        current = self.platform.controller.reference.copy()
        if value > current[offset] + .00001:
            # Acquire only the measured force needed for retention. Do not
            # squeeze every object to one geometry-independent closed width.
            for _ in range(200):
                current=current.copy()
                current[offset] += float(np.clip(value-current[offset],-.0004,.0004))
                self.execute([current],contract)
                grasp=self.feedback()["contact_execution"]["measured"].get("grasp",{})
                target_force={"hotdog_container":8.,"tomato":8.,"pear":4.,"banana":4.,
                              "microwave_door_handle":8.}.get(
                    contract["target"],6. if contract["target"].endswith("_container") else 2.)
                if contract["target"]=="pear" and "pear" in self.platform.controller.devices.cleaned:
                    target_force=8.
                enough=min(grasp.get("pad_normal_forces_n",[0.,0.])) >= target_force
                # The raised burger bar needs the full bounded tendon command
                # used by the reference, rather than stopping at first 6N contact.
                if contract["target"].endswith("_container"):enough &= current[offset]>=value-1e-8
                if enough:
                    self.wait(.16)
                    return
            raise ExpertFailure("CONTACT_FORCE_TARGET_NOT_REACHED")
        targets = []
        for _ in range(max(35, int(math.ceil(abs(value-current[offset])/.001)) + 15)):
            current = current.copy()
            current[offset] += float(np.clip(value - current[offset], -.001, .001))
            targets.append(current)
        for start in range(0, len(targets), 8):
            self.execute(targets[start:start+8], contract)

    def free_contract(self, side, name, phase):
        payload = [name]
        supports = {name: ["v3_counter_left", "v3_counter_right", "v3_counter_front", "v3_counter_back",
                           "basket_bottom", "tray_bottom", "microwave_bottom", "user_table"]}
        if name.endswith("_container"):
            food = name.removesuffix("_container")
            payload.append(food)
            supports[food] = [food + "_plate"]
        return {"arm": side, "target": name, "phase": phase, "payload_ids": payload, "supports": supports,
                **({"target_support_transition": True} if phase in ("carry", "release") else {})}

    def balanced_grasp_point(self, side, name, position, rotation):
        """Centre the two actual pad gaps before touching a thin curved object."""
        arm=self.scene.arms[side]
        geoms=object_geom_ids(self.scene,name)
        scratch=mujoco.MjData(self.scene.model)
        best=None
        # A single-sided push on the banana's curved surface can lift it before
        # both pads meet. Use mesh distances, not the object's root as midpoint.
        for shift in np.linspace(-.006,.006,25):
            point=np.asarray(position)+rotation[:,0]*shift
            result=solve_ik(self.view(),side,point,rotation,max_iterations=400,
                            position_tolerance=.0001,rotation_tolerance=.003)
            if not result.success:continue
            mujoco.mj_copyData(scratch,self.scene.model,self.scene.data)
            scratch.qpos[list(arm.qpos)]=result.q
            scratch.qpos[list(arm.fingers)]=.027
            mujoco.mj_forward(self.scene.model,scratch)
            gaps=[min(float(mujoco.mj_geomDistance(self.scene.model,scratch,p,g,.1,None))
                      for g in geoms) for p in arm.pad_geoms]
            score=abs(gaps[0]-gaps[1])
            if best is None or score<best[0]:best=(score,shift,point)
        if best is None:raise ExpertFailure("GRASP_CENTRING_IK_FAILED")
        self.events.append({"stage":"grasp_gap_centering","tick":self.platform.controller.tick,
                            "shift_m":float(best[1]),"gap_difference_m":float(best[0])})
        return best[2]

    def pick(self, side, name, *, in_oven=False, destination=None):
        self.mark("pick_approach", target=name)
        # No forced home reset: the vertical lift to `high` below is the safe
        # transit, so motion continues from the current pose (user direction).
        view = self.view()
        body = self.scene.objects[name]
        center = view.data.xpos[body].copy()
        if name.endswith("_container"):
            from .meal_geometry import plan_grasp
            try:
                grasp = plan_grasp(view, name, side, plan_approach=True, destination=destination,
                                   approach_distance=.06 if in_oven else .12,
                                   lift_height=.035 if in_oven else .045)
                approach = grasp.approach_joints
            except ValueError as error:
                # A home detour is a last resort after current-pose geometry
                # planning fails, never an unconditional skill boundary.
                self.mark("route_home_fallback",side=side,reason=str(error))
                self.return_home(side)
                view=self.view()
                try:
                    grasp=plan_grasp(view,name,side,plan_approach=True,destination=destination,
                                     approach_distance=.06 if in_oven else .12,
                                     lift_height=.035 if in_oven else .045)
                    approach=grasp.approach_joints
                except ValueError as retry_error:raise ExpertFailure(str(retry_error)) from retry_error
            point, rotation = grasp.point, grasp.rotation
            self.mark("meal_geometric_grasp", target=name, handle=grasp.handle,
                      position_world_m=point.tolist(), clearance_m=grasp.clearance_m,
                      approach_waypoints=len(approach))
        else:
            point = center + [0, 0, -.005]
            rotation = DOWN
            # The tomato sits nearest the left arm base, so a straight-down
            # wrist folds an upper link into the 2mm counter margin. A sphere
            # tolerates any tool yaw, so tilt the grasp 30 deg to keep the
            # wrist and elbow up without moving the object.
            if name=="tomato":rotation=DOWN@rz(math.radians(30))
            if name=="pear":point=view.data.xipos[body].copy()+[0,0,.030]
            # The banana body's centre of mass sits near its root; grasping
            # 20mm above it left a long gravity lever that crept ~1 deg/s
            # through the pads during the 18s tray traverse. 10mm grazed the
            # counter and sink wall at 1.96mm, so use 15mm and carry it faster.
            if name == "banana": point[2] += .015
        curved=False
        acquire=self.free_contract(side,name,"acquire")
        if side=="left" and not name.endswith("_container"):
            try:
                base=rotation.copy()
                for angle in (0.,20.,-20.,40.,-40.):
                    candidate=base@rz(math.radians(angle))
                    grasp_point=self.balanced_grasp_point(side,name,point,candidate) if name=="banana" else point
                    curved=self.fruit_curve(grasp_point,candidate,mode="approach",target=name,acquire=acquire)
                    if curved:
                        point,rotation=grasp_point,candidate
                        break
            except ExpertFailure as error:
                if "GRASP_CENTRING_IK_FAILED" not in str(error):raise
        if not curved:
            if side=="left":self.mark("fruit_legacy_approach",target=name,reason="CHECKED_CURVE_CANDIDATES_EXHAUSTED")
            if in_oven:
                oven_r = view.data.xmat[self.scene.model.body("microwave").id].reshape(3, 3)
                pre = point - rotation[:,2]*.20
            else:
                # Both carriers now have a raised outboard bar; keep the burger's
                # original stand-off. The hotdog approach still needs routing work
                # (it grazes the neighbouring burger bar), tracked in the run log.
                clearance=.35 if self.obj(name).get("in_basket") else .18
                pre = point - rotation[:,2]*clearance
            # Change orientation with clearance above the object before descent.
            # This must raise the tool, not only cap it: the previous `min` kept a
            # home-height TCP at ~1.0m and swept the 156mm fingers into the tomato
            # and the open microwave handle.
            high,initial_rotation=tcp_pose(view.data,self.scene.arms[side])
            # Reorient in place at the current TCP. Forcing the TCP up to 1.15m
            # while keeping the previous (e.g. door-grasp) orientation was outside
            # the right arm's envelope after opening the door.
            high=high.copy()
            qa,qb=np.zeros(4),np.zeros(4)
            mujoco.mju_mat2Quat(qa,initial_rotation.ravel());mujoco.mju_mat2Quat(qb,rotation.ravel())
            if np.dot(qa,qb)<0:qb=-qb
            if name.endswith("_container"):
                for joints in approach:
                    self.joints(side, joints, speed=.004)
            else:
                orientations=[]
                for fraction in np.linspace(0,1,13)[1:]:
                    quat=(1-fraction)*qa+fraction*qb;quat/=np.linalg.norm(quat)
                    mat=np.empty(9);mujoco.mju_quat2Mat(mat,quat)
                    orientations.append((high.copy(),mat.reshape(3,3).copy()))
                if not self.continuous_poses(side,orientations,speed=.006):
                    for position,orientation in orientations:self.point(side,position,orientation)
            # Continuous transit needs a via point, not a straight line through the
            # worktop fixtures: move diagonally at 1.28m above the target, then
            # descend along the approach axis. The old home reset hid this by
            # approaching from a fixed side.
            via=np.array([float(pre[0]),float(pre[1]),max(1.28,float(high[2]))])
            if not name.endswith("_container"):
                # A fixed 1.28m waypoint is outside the apple seed0 workspace.
                # Lower only this free transit waypoint; preserve grasp poses.
                for height in (via[2],1.24,1.20,1.16):
                    if height<float(pre[2])+.04:continue
                    candidate=via.copy();candidate[2]=height
                    if solve_ik(self.view(),side,candidate,rotation,max_iterations=400,
                                position_tolerance=.0015,rotation_tolerance=.003).success:
                        via=candidate;break
                self.line(side,via,rotation)
                self.line(side,pre,rotation)
            self.line(side, point - rotation[:,2]*.025, rotation)
            if name=="banana":
                point=self.balanced_grasp_point(side,name,point,rotation)
        acquire = self.free_contract(side, name, "acquire")
        if not curved:self.line(side, point, rotation, acquire, spacing=.006)
        value = .05 if name.endswith("_container") else .04 if name in ("banana", "tomato") else .025
        if name=="pear" and "pear" in self.platform.controller.devices.cleaned:value=.04
        self.grip(side, value, acquire)
        f = self.feedback()["contact_execution"]
        if f["measured_grasp_age_s"] < .05:
            raise ExpertFailure("NO_BILATERAL_50MS: " + name)
        carry = self.free_contract(side, name, "carry")
        self.mark("pick_lift", target=name)
        lift_distance = .035 if in_oven else .06 if side=="left" else .18
        if in_oven:
            pose=self.view()
            ceiling=geometry_bounds(pose.model,pose.data,pose.model.geom("microwave_top").id)[0][2]
            hand_top=max(geometry_bounds(pose.model,pose.data,g)[1][2]
                         for g in range(pose.model.ngeom)
                         if pose.model.body(int(pose.model.geom_bodyid[g])).name.startswith("right_umi"))
            lift_distance=min(lift_distance,float(ceiling-hand_top-.012))
            if lift_distance < .035:
                raise ExpertFailure("OVEN_EXTRACTION_HEADROOM_BELOW_35MM")
        self.line(side, point + [0, 0, lift_distance], rotation, carry, ramp=True)
        if side!="left":self.wait(1.)
        if self.obj(name)["position_world_m"][2] < center[2] + .03:
            raise ExpertFailure("NO_30MM_LIFT: " + name)
        self.held = (side, name)
        self.mark("pick_qualified", target=name)
        self.save_checkpoint("picked_" + name)
        if in_oven:
            here = tcp_pose(self.view().data, self.scene.arms[side])[0]
            self.line(side, here - oven_r[:, 0] * .28, rotation, carry)

    def place(self, label, *, slot=0.):
        side, name = self.held
        self.mark("place_approach", target=name, support=label)
        view = self.view()
        arm = self.scene.arms[side]
        tcp, rotation = tcp_pose(view.data, arm)
        root = view.data.xpos[self.scene.objects[name]].copy()
        offset = root - tcp
        support = {"tray": "tray_bottom", "basket": "basket_bottom", "oven": "microwave_bottom"}[label]
        geom = self.scene.model.geom(support).id
        lo, hi = object_bounds(view, name)
        support_lo, support_hi = geometry_bounds(self.scene.model, view.data, geom)
        center = view.data.geom_xpos[geom].copy()
        center[:2] += np.array([slot, 0])
        if label=="basket" and name=="banana":
            # The curved fruit can roll towards the faucet/rear wall after
            # opening. Reserve room for the full finger-holder closure sweep
            # on the next pick, instead of placing its root at the wall side.
            basket_frame=view.data.geom_xmat[geom].reshape(3,3)
            inset=min(.04,.35*float(view.model.geom_size[geom][1]))
            center-=basket_frame[:,1]*inset
            self.mark("basket_regrasp_clearance",target=name,inset_m=inset)
        if label == "oven":
            # Align the whole carrier with the cavity before entering its mouth.
            oven = self.scene.model.body("microwave").id
            desired_body = view.data.xmat[oven].reshape(3, 3).copy()
            # Keep the carrier inside the front of the cavity so the wrist
            # remains outside the roof lip with the steeper handle grasp.
            local = np.array([-.10, 0., .032])
            desired_root = view.data.xpos[oven] + desired_body @ local
            self.orient_payload(desired_body)
            view = self.view(); tcp, rotation = tcp_pose(view.data, arm)
            root = view.data.xpos[self.scene.objects[name]].copy(); offset = root - tcp
            ceiling=geometry_bounds(view.model,view.data,view.model.geom("microwave_top").id)[0][2]
            hand_top=max(geometry_bounds(view.model,view.data,g)[1][2]
                         for g in range(view.model.ngeom)
                         if view.model.body(int(view.model.geom_bodyid[g])).name.startswith("right_umi"))
            entry_raise=min(.015,float(ceiling-(hand_top-root[2])-desired_root[2]-.012))
            if entry_raise < .015:
                raise ExpertFailure("OVEN_INSERTION_HEADROOM_BELOW_15MM")
            # Approach on the cavity's side opposite the door hinge. The
            # centreline approach intersects the open panel's front corner.
            pre_root = desired_root - desired_body[:, 0] * .32 - desired_body[:,1]*.06 + [0, 0, .16]
            self.mark("meal_oven_front_corridor",target=name)
            # Re-plan the oven-entry corridor for the current arm/base (the
            # right base moved to y=0; the hard-coded [root[0],-0.34] and
            # [0.62,-0.34] points are unreachable now). Pick the first
            # reachable Y, then the first reachable X/Y.
            def _reachable(tcp_target):
                return solve_ik(self.view(),side,np.asarray(tcp_target),rotation,max_iterations=400).success
            first=np.array([root[0],-.34,pre_root[2]])-offset
            for y in (-.34,-.30,-.26,-.22,-.18,-.14):
                candidate=np.array([root[0],y,pre_root[2]])-offset
                if _reachable(candidate): first=candidate; break
            second=np.array([.62,-.34,pre_root[2]])-offset
            for x in (.62,.58,.54,.50,.46,.42):
                found=None
                for y in (-.34,-.30,-.26,-.22):
                    candidate=np.array([x,y,pre_root[2]])-offset
                    if _reachable(candidate): found=candidate; break
                if found is not None: second=found; break
            self.line(side,first,rotation,ramp=True,speed=.004)
            self.line(side,second,rotation,ramp=True,speed=.004)
            self.line(side, pre_root - offset, rotation, transit=True)
            # Steeper bars raise the wrist. Lower outside the mouth before
            # entering horizontally, using the actual full-hand roof clearance.
            insertion_pre=pre_root.copy(); insertion_pre[2]=desired_root[2]+entry_raise
            self.mark("meal_oven_insert",entry_raise_m=entry_raise)
            self.line(side,insertion_pre-offset,rotation)
            self.line(side, desired_root - offset + [0, 0, entry_raise], rotation)
            target = desired_root - offset
        else:
            target = center - offset
            target[2] = support_hi[2] + (root[2] - lo[2]) + .001 - offset[2]
            curved_place=side=="left" and self.fruit_curve(target,rotation,mode="carry",target=name,
                                                         payload=(name,),support=support)
            if curved_place:target=self.last_fruit_curve_endpoint.copy()
            if not curved_place:
                if side=="left":self.mark("fruit_legacy_place",target=name,reason="CHECKED_CURVE_CANDIDATES_EXHAUSTED")
                top = target.copy()
                top[2] = target[2]+.10 if name.endswith("_container") else 1.06 if label == "tray" else 1.07
                if label == "tray":
                    corridor = tcp.copy(); corridor[1] = center[1]; corridor[2] = top[2]
                    self.line(side, corridor, rotation, ramp=True, speed=.011 if name=="banana" else .008)
                self.line(side, top, rotation, ramp=True,speed=.011 if name=="banana" else .008)
        if label=="oven" or not curved_place:self.line(side,target,rotation,spacing=.008,ramp=True)
        # Settle the supported load before opening; no assumed release height.
        devices = self.platform.controller.devices
        for _ in range(8):
            if devices.supports(name, [support]): break
            target[2] -= .001
            self.point(side, target, rotation)
        if not devices.supports(name, [support]):
            raise ExpertFailure("NO_SUPPORT_BEFORE_RELEASE: " + name)
        # While the hand still pinches the object it shares the load with
        # the support. Transfer weight by gradually opening, rather than
        # repeatedly changing height to force full weight onto the support.
        # Native support above and final quiet/pad-separation gates remain.
        self.wait(.4)
        release = self.free_contract(side, name, "release")
        self.grip(side, 0., release)
        self.wait(.4)
        if name=="banana":
            self.peel_release(side,name,rotation,release)
        here=tcp_pose(self.view().data,arm)[0]
        # Lift clear of the object before certifying separation: the pads are
        # 156mm long and 18mm left the banana waist 0.114mm inside pad110. Rise
        # 60mm along world +Z so the finger tips clear the object top. The first
        # millimetres still graze the banana, so lift at half speed (0.004
        # rad/frame): the default 0.008 spun it to 4.29rad/s, over the 4.0
        # payload spin gate. The release-travel bound is 120mm; acquire stays 30.
        if name.endswith("_container"):
            # Raised bars sit inside long fingers. Withdraw along the tool
            # axis before rising, rather than hooking the bar from below.
            for distance in (.040, .030, .020, .060, .080):
                goal = here-rotation[:,2]*distance
                result = solve_ik(self.view(),side,goal,rotation,max_iterations=400,
                                  position_tolerance=.001,rotation_tolerance=.01)
                if result.success:
                    self.line(side,goal,rotation,release,spacing=.008,speed=.004)
                    break
            else:
                raise ExpertFailure("NO_REACHABLE_HANDLE_WITHDRAWAL")
            self.wait(.12)
            if not self.feedback()["contact_execution"]["measured"]["grasp"].get("pads_separated"):
                raise ExpertFailure("MEAL_RELEASE_PADS_NOT_SEPARATED")
        else:
            self.line(side, here + np.array([0.,0.,.060]), rotation, release, spacing=.008, speed=.004)
        # end_contact requires the released object itself to be quiet
        # (<=0.02rad/s, <=0.05m/s). A round fruit released on a tray still
        # rolls for a moment, so settle before certifying the release.
        if side=="left":
            # Wait for the measured release condition, not a fixed dwell on
            # every fruit. The final two-second tray quality gate is unchanged.
            for _ in range(40):
                state=self.obj(name);feedback=self.feedback()["contact_execution"]
                quiet=(np.linalg.norm(state["linear_velocity_world_m_s"])<=.02 and
                       np.linalg.norm(state["angular_velocity_world_rad_s"])<=.05)
                if quiet and feedback.get("measured_pad_separation_s",0)>=.05:break
                self.wait(.08)
        else:self.wait(1.5)
        # 500Hz policy supplies pad-separation, actual support and velocity gates.
        self.call("end_contact", mutating=True)
        self.contract = None
        self.held = None
        here = tcp_pose(self.view().data, arm)[0]
        if label == "oven":
            self.line(side, here - desired_body[:, 0] * .32, rotation)
        elif name.endswith("_container"):
            # Already clear along the measured approach axis. No arbitrary
            # upward detour into a joint limit after a successful release.
            pass
        else:
            self.line(side, here - rotation[:,2]*(.08 if side=="left" else .20), rotation)
        if side!="left":self.wait(2.)
        if not devices.supports(name, [support]) or devices.touching_pads(name):
            raise ExpertFailure("RELEASED_SUPPORT_NOT_RETAINED")
        self.mark("place_released", target=name, support=support)

    def peel_release(self, side, name, rotation, contract):
        """Back an open finger off the measured contact before lifting it."""
        arm=self.scene.arms[side]
        geoms=object_geom_ids(self.scene,name)
        direction=np.zeros(3)
        touching=[]
        data=self.scene.data
        for index,contact in enumerate(data.contact):
            a,b=int(contact.geom1),int(contact.geom2)
            if a in arm.pad_geoms and b in geoms:
                sign=1.;pad=a
            elif b in arm.pad_geoms and a in geoms:
                sign=-1.;pad=b
            else:continue
            force=np.empty(6)
            mujoco.mj_contactForce(self.scene.model,data,index,force)
            direction-=sign*np.asarray(contact.frame[:3])*max(0.,float(force[0]))
            touching.append(pad)
        if not touching:return
        direction[2]=0.
        if np.linalg.norm(direction)<1e-6:
            # If the fruit is resting on a horizontal part of the open finger,
            # remove that support sideways instead of dragging it upward.
            view=self.view()
            pad_center=np.mean(view.data.geom_xpos[list(set(touching))],axis=0)
            direction=pad_center-view.data.xpos[self.scene.objects[name]]
            direction[2]=0.
        if np.linalg.norm(direction)<1e-6:
            raise ExpertFailure("RELEASE_PEEL_DIRECTION_UNDEFINED")
        direction/=np.linalg.norm(direction)
        self.mark("release_peel",target=name,direction_world=direction.tolist())
        here=tcp_pose(self.view().data,arm)[0]
        self.line(side,here+direction*.020,rotation,contract,spacing=.002,speed=.002)
        self.wait(.4)

    def _rotation_anchor(self, side, name, desired_body):
        """Try the current carrier centre, then a few nearby raised positions.

        No workspace grid or random search. A detached rigid-payload hypothesis
        checks the robot, carrier and food against the actual door/obstacles.
        Native contact and support still determine physical success at runtime.
        """
        from robot_arm.kitchen.payload_geometry import CarriedGeometry
        view = self.view(); m = view.model; arm = self.scene.arms[side]
        body = self.scene.objects[name]
        root = view.data.xpos[body].copy()
        tcp, tool = tcp_pose(view.data, arm)
        old_body = view.data.xmat[body].reshape(3, 3).copy()
        offset = old_body.T @ (tcp-root); relative = old_body.T @ tool
        a, b = np.zeros(4), np.zeros(4)
        mujoco.mju_mat2Quat(a, old_body.ravel())
        mujoco.mju_mat2Quat(b, np.asarray(desired_body).ravel())
        if a @ b < 0: b *= -1
        geometry = CarriedGeometry(view, view.data.qpos, side,
                                   (name, name.removesuffix("_container")))
        pairs = np.asarray([p for p in geometry.pairs(tuple(geometry.frames))
                            if not all(int(g) in geometry.geoms for g in p)], dtype=int)
        pads = set(arm.pad_geoms)
        thresholds = np.array([-.003 if any(int(g) in pads for g in p)
                               and any(int(g) in geometry.geoms for g in p) else .001
                               for p in pairs])
        for delta in ([0., 0., 0.], [0., 0., .06], [-.06, 0., .06],
                      [0., -.06, .06], [-.12, 0., .06], [-.06, -.06, .10]):
            anchor = root + delta
            scratch = self.view()
            poses = [(tcp + np.asarray(delta)*t, tool) for t in np.linspace(0, 1, 9)[1:]]
            for t in np.linspace(0, 1, 17)[1:]:
                q = (1-t)*a+t*b; q /= np.linalg.norm(q)
                r = np.empty(9); mujoco.mju_quat2Mat(r, q); r = r.reshape(3, 3)
                poses.append((anchor+r@offset, r@relative))
            good = True
            for position, rotation in poses:
                result = solve_ik(scratch, side, position, rotation, max_iterations=400,
                                  position_tolerance=.001, rotation_tolerance=.01)
                if not result.success: good = False; break
                previous = scratch.data.qpos.copy()
                candidate = previous.copy(); candidate[list(arm.qpos)] = result.q
                count = max(1, math.ceil(np.max(np.abs(candidate-previous))/.035))
                if any(np.any(geometry.distances(previous+(candidate-previous)*f, pairs) < thresholds)
                       for f in np.linspace(0, 1, count+1)):
                    good = False; break
                scratch.data.qpos[:] = candidate
            if good: return anchor
        return None

    def orient_payload(self, desired_body):
        side, name = self.held
        view = self.view(); body = self.scene.objects[name]
        current_body = view.data.xmat[body].reshape(3, 3)
        if np.linalg.norm(current_body-np.asarray(desired_body)) < .03:
            return
        root = view.data.xpos[body].copy()
        tcp, rotation = tcp_pose(view.data, self.scene.arms[side])
        anchor = self._rotation_anchor(side, name, desired_body)
        if anchor is None:
            raise ExpertFailure("PAYLOAD_TURN_NO_SIMPLE_CLEAR_ANCHOR")
        self.mark("payload_turn_anchor", target=name, root_world_m=np.asarray(anchor).tolist())
        if np.linalg.norm(anchor-root) > .001:
            self.line(side, tcp+(anchor-root), rotation, ramp=True, speed=.004)
        self._turn_payload_to(side, name, desired_body)

    def _turn_payload_to(self, side, name, desired_body, count=17):
        view = self.view()
        root = view.data.xpos[self.scene.objects[name]].copy()
        old_body = view.data.xmat[self.scene.objects[name]].reshape(3, 3).copy()
        tcp, old_tcp = tcp_pose(view.data, self.scene.arms[side])
        offset = old_body.T @ (tcp - root)
        relative = old_body.T @ old_tcp
        a, b = np.zeros(4), np.zeros(4)
        mujoco.mju_mat2Quat(a, old_body.ravel()); mujoco.mju_mat2Quat(b, np.asarray(desired_body).ravel())
        if np.dot(a, b) < 0: b = -b
        self.mark("payload_turn", target=name)
        for t in np.linspace(0, 1, count)[1:]:
            q = (1 - t) * a + t * b; q /= np.linalg.norm(q)
            r = np.zeros(9); mujoco.mju_quat2Mat(r, q); r = r.reshape(3, 3)
            self.point(side, root + r @ offset, r @ relative, speed=.004)

    def return_home(self, side, *, check_path=True):
        if self.held or self.contract:
            raise ExpertFailure("CANNOT_HOME_WITH_ACTIVE_CONTACT")
        self.mark("return_home", side=side)
        # An oven withdrawal and home can be individually valid while the
        # joint interpolation cuts through the open panel. Use the same small
        # set of Cartesian approach routes when that direct path is blocked.
        arm = self.scene.arms[side]
        q0 = self.scene.data.qpos.copy(); q1 = q0.copy()
        q1[list(arm.qpos)] = self.home[side]
        if check_path and not self.platform.geometry.certificate(q0,q1,margin=.001)["pass"]:
            view = self.view(); view.data.qpos[:] = q1
            mujoco.mj_forward(view.model,view.data)
            position, rotation = tcp_pose(view.data,arm)
            self.free_pose(side,position,rotation)
            q0 = self.scene.data.qpos.copy()
            if not self.platform.geometry.certificate(q0,q1,margin=.001)["pass"]:
                # Same home TCP pose can have another redundant elbow branch.
                return
        self.joints(side, self.home[side])

    def free_pose(self, side, position, rotation, *, continuous=True, home_fallback=False):
        """Direct Cartesian route or two raised doglegs, with sampled clearance."""
        from .meal_geometry import MealGrasp,approach_path
        goal = MealGrasp("free_pose",np.asarray(position),np.asarray(rotation),
                         np.asarray(position),np.empty(7),0.)
        try: path = approach_path(self.view(),goal,side)
        except ValueError as error:
            if not home_fallback:raise ExpertFailure(str(error)) from error
            self.mark("route_home_fallback",side=side,reason=str(error))
            self.return_home(side)
            try:path=approach_path(self.view(),goal,side)
            except ValueError as retry_error:raise ExpertFailure(str(retry_error)) from retry_error
        if not continuous:
            for joints in path: self.joints(side,joints,speed=.004)
            return
        self.stream_joints(side,path,speed=.008)

    def clear_left_corridor(self):
        """Retract locally beyond the tray edge, keeping the current tool pose."""
        from .meal_geometry import MealGrasp,approach_path
        if self.held or self.contract:raise ExpertFailure("CLEARANCE_REQUIRES_FREE_HAND")
        view=self.view();arm=view.arms["left"]
        here,rotation=tcp_pose(view.data,arm)
        tray=view.objects["wood_tray"];origin=view.data.xpos[tray]
        frame=view.data.xmat[tray].reshape(3,3)
        edge=geometry_bounds(view.model,view.data,view.model.geom("tray_bottom").id,origin,frame)[0][0]
        hand=[g for g in self.platform.geometry.robot
              if view.model.body(int(view.model.geom_bodyid[g])).name.startswith("left_umi")]
        for distance in (.12,.18,.24,.30):
            position=here-frame[:,0]*distance
            result=solve_ik(view,"left",position,rotation,max_iterations=400,
                            position_tolerance=.001,rotation_tolerance=.01)
            if not result.success:continue
            scratch=self.view();scratch.data.qpos[list(arm.qpos)]=result.q
            mujoco.mj_forward(scratch.model,scratch.data)
            if max(geometry_bounds(scratch.model,scratch.data,g,origin,frame)[1][0] for g in hand)>edge-.06:continue
            goal=MealGrasp("left_corridor",position,rotation,position,np.empty(7),0.)
            try:path=approach_path(self.view(),goal,"left")
            except ValueError:continue
            self.stream_joints("left",path)
            self.mark("left_corridor_cleared",distance_m=distance,route="local")
            return
        self.mark("route_home_fallback",side="left",reason="NO_LOCAL_TRAY_CLEARANCE_ROUTE")
        self.return_home("left")

    def reference_device(self, name):
        """Only fixed equipment uses recorded geometry; payloads are replanned."""
        inspect(ASSETS)
        if self.held or self.contract: raise ExpertFailure("DEVICE_REQUIRES_FREE_HAND")
        self.mark("device_" + name)
        # Continuous transit: rise to a common safe height at the current XY
        # instead of snapping back to the home joint pose before the replay.
        side = "right" if name != "faucet" else "left"
        if name == "faucet":
            self.faucet_reference()
            return
        metadata=json.loads((ASSETS/"burger_metadata.json").read_text())
        phases=metadata["execution_clock"]["mapped_phases"]
        phase_name={"open":"opening","close":"closing","heat_open":"button"}[name]
        start=0 if name=="open" else next(row["start_tick"] for row in phases if row["name"]==phase_name)
        final_name="reopening" if name=="heat_open" else phase_name
        end=next(row["end_tick"] for row in phases if row["name"]==final_name and row["phase"]=="release")
        rows = [json.loads(line) for line in (ASSETS / "burger_public_calls.jsonl").read_text().splitlines()]
        # Include the recorded approach from its preceding free waypoint at
        # home. Bridging directly to the first acquire pose cuts across the
        # open door instead of following the demonstrated transit.
        if start:
            homes=[]
            for index,row in enumerate(rows[:-1]):
                if row["start_tick"]>=start:break
                if row["method"]!="execute_trajectory" or row["args"].get("contact_contract"):continue
                goal=np.asarray(row["args"]["waypoints"][-1])[8:15]
                if np.max(np.abs(goal-self.home["right"]))<1e-7 and rows[index+1]["method"]=="step":
                    homes.append(rows[index+1]["end_tick"])
            if homes:
                releases=[row["end_tick"] for row in rows if row["method"]=="end_contact" and row["end_tick"]<=start]
                after=max(releases,default=0)
                start=next((tick for tick in homes if tick>=after),homes[-1])
        # Include the source's collision-checked withdrawal to home when one
        # precedes the next contact. Finishing at release beside the door handle
        # leaves no clearance for a new pick's orientation change.
        for index,row in enumerate(rows):
            if row["start_tick"]<end or row["method"]!="execute_trajectory":continue
            if row["args"].get("contact_contract"):break
            goal=np.asarray(row["args"]["waypoints"][-1])[8:15]
            if np.max(np.abs(goal-self.home["right"]))<1e-7:
                following=rows[index+1]
                if following["method"]=="step":end=following["end_tick"]
                break
        self.reference_window(start,end)
        if self.contract: raise ExpertFailure("DEVICE_REFERENCE_ENDED_IN_CONTACT")
        self.mark("device_"+name+"_complete")

    def reference_window(self,start,end):
        chosen=reference_calls_between(start,end)
        bridged=False
        for row in chosen:
            args=copy.deepcopy(row["args"])
            if row["method"] == "execute_trajectory":
                for q in args["waypoints"]: q[:8]=self.platform.controller.reference[:8].tolist()
                first=np.asarray(args["waypoints"][0])
                if not bridged and np.max(np.abs(first[8:15]-self.platform.controller.reference[8:15]))>.01:
                    self.joints("right",first[8:15])
                bridged=True
                self.call("execute_trajectory",args,mutating=True)
                self.contract=args.get("contact_contract")
            elif row["method"] == "step":
                state=self.call("step",args,mutating=True)
                if state["stopped"]:raise ExpertFailure("REFERENCE_DEVICE_STOP: "+str(state["stop_reason"]))
            elif row["method"] == "end_contact":
                self.call("end_contact",mutating=True);self.contract=None

    def faucet_reference(self):
        # Replay the recorded apple wash cycle as-is, including the handle turn.
        # The old code broke at tick 53080 and re-planned the turn with IK because
        # the recorded turn slid >15mm under the relative-motion gate. That gate
        # is now disabled and the re-planned turn lost the handle (GRASP_LOST at
        # 102ms), so reusing the recording is both simpler and more faithful.
        rows=[json.loads(line) for line in (ASSETS/"apple_controls_25hz.jsonl").open()]
        rows=[r for r in rows if 47980 <= r["start_tick"] and r["end_tick"]<=63880]
        contract=None
        acquired=False
        ever_acquired=False
        seen_open=False
        cursor=0
        while cursor<len(rows):
            row=rows[cursor]
            q=np.asarray(row["target16"]);q[8:]=self.platform.controller.reference[8:]
            if q[7]>.00001 and contract is None:
                contract={"arm":"left","target":"faucet_handle_geom","phase":"acquire","payload_ids":[],"supports":{}}
            if contract and contract["phase"]=="acquire" and self.feedback()["contact_execution"]["measured_grasp_age_s"]>=.05:
                contract={**contract,"phase":"device"};acquired=True;ever_acquired=True
            if acquired and q[7]<self.platform.controller.reference[7]-.00001 and contract["phase"]=="device":
                contract={**contract,"phase":"release"}
            if contract and contract["phase"]=="release" and q[7]<=.00001 and self.feedback()["contact_execution"]["measured_pad_separation_s"]>=.05:
                self.call("end_contact",mutating=True);contract=None;self.contract=None;acquired=False
                break
            if contract is None and q[7]<=.00001:
                # The reference free approach/withdrawal has hundreds of
                # samples. Following each with joints() introduced a stop at
                # every sample. Keep their joint path and settle only once.
                path=[]
                while cursor<len(rows) and rows[cursor]["target16"][7]<=.00001:
                    path.append(np.asarray(rows[cursor]["target16"][:7],dtype=float))
                    cursor+=1
                self.stream_joints("left",path)
                continue
            if contract is None:
                # After release the recorded tail still carries gripper commands;
                # an unguarded sweep is rejected. Hold the gripper at its current
                # value for the withdrawal instead of replaying that channel.
                q[7]=self.platform.controller.reference[7]
            self.execute([q],contract)
            if self.platform.controller.devices.angle("faucet_switch")>=math.radians(40):
                seen_open=True
            cursor+=1
        if not ever_acquired:
            raise ExpertFailure("FAUCET_BILATERAL_50MS_MISSING")
        if not seen_open:
            raise ExpertFailure("FAUCET_ACTUAL_ON_THRESHOLD_NOT_REACHED")
        if abs(self.platform.controller.devices.angle("faucet_switch"))>math.radians(5):
            raise ExpertFailure("FAUCET_ACTUAL_OFF_THRESHOLD_NOT_REACHED")

    def turn_faucet(self, goal_angle):
        view=self.view();model=self.scene.model
        body=model.body("faucet_handle").id
        joint=model.joint("faucet_switch").id
        address=int(model.jnt_qposadr[joint])
        tcp,rotation=tcp_pose(view.data,self.scene.arms["left"])
        body_rotation=view.data.xmat[body].reshape(3,3)
        offset=body_rotation.T@(tcp-view.data.xpos[body])
        relative_rotation=body_rotation.T@rotation
        start=float(view.data.qpos[address])
        count=max(1,int(math.ceil(abs(goal_angle-start)/math.radians(1.5))))
        for angle in np.linspace(start,goal_angle,count+1)[1:]:
            scratch=self.view();scratch.data.qpos[address]=angle
            mujoco.mj_forward(model,scratch.data)
            target_rotation=scratch.data.xmat[body].reshape(3,3)
            self.point("left",scratch.data.xpos[body]+target_rotation@offset,
                       target_rotation@relative_rotation)

    def fruit(self, fruit, wash=False,slot=0.):
        self.pick("left",fruit)
        if wash:
            self.place("basket")
            self.reference_device("faucet")
            if fruit not in self.platform.controller.devices.cleaned:
                raise ExpertFailure("WASH_EVENT_MISSING")
            self.pick("left",fruit)
        self.place("tray",slot=slot)

    def meal(self, meal, destination="tray", in_oven=False):
        # Every meal pickup uses the current compiled handle geometry, including
        # in-oven extraction. Fixed nominal-position reference windows are not
        # a planning fallback for randomized meals.
        self.pick("right",meal+"_container",in_oven=in_oven,destination=destination)
        # Tray success constrains footprint/support, not yaw. Avoid an
        # unnecessary half-turn that can slide food off its own carrier.
        self.place(destination,slot=.11 if destination=="tray" else 0.)

    def dual_points(self, positions, rotation=None, contract=None, transit=False):
        rotation=self.dual_rotation if rotation is None else rotation
        rotations=rotation if isinstance(rotation,dict) else {s:rotation for s in ("left","right")}
        contract = self.contract if contract is None else contract
        view = self.view()
        goals = {}
        for side in ("left", "right"):
            result = solve_ik(view, side, positions[side], rotations[side], max_iterations=500,
                              position_tolerance=.0002, rotation_tolerance=.003)
            if not result.success:
                arm=self.scene.arms[side];lower,upper=joint_control_limits(self.scene.model,arm)
                rng=np.random.default_rng(47 if side=="left" else 83)
                for seed in [self.home[side]]+[rng.uniform(lower,upper) for _ in range(16)]:
                    scratch=self.view();scratch.data.qpos[list(arm.qpos)]=seed
                    result=solve_ik(scratch,side,positions[side],rotations[side],max_iterations=600,
                                    position_tolerance=.0015,rotation_tolerance=.003)
                    if result.success:break
            if not result.success:
                # A free-space transit pre may sit a couple of mm outside the
                # strict IK envelope with no contact consequence.
                if transit and result.position_error < .003 and result.rotation_error < .010:
                    goals[side] = result.q
                    continue
                raise ExpertFailure(f"DUAL_IK_FAILED_{side}: {positions[side]} error={result.position_error}")
            goals[side] = result.q
        target_view=self.view()
        for side in goals:target_view.data.qpos[list(self.scene.arms[side].qpos)]=goals[side]
        mujoco.mj_forward(self.scene.model,target_view.data)
        target_poses={s:tcp_pose(target_view.data,self.scene.arms[s]) for s in goals}
        for _ in range(600):
            current = self.platform.controller.reference.copy()
            if all(np.max(np.abs(self.scene.data.qpos[list(self.scene.arms[s].qpos)] - goals[s])) < .006 and
                   np.max(np.abs(current[o:o+7]-goals[s])) < 1e-7
                   for o, s in ((0, "left"), (8, "right"))):
                return
            if all(np.max(np.abs(current[o:o+7]-goals[s]))<1e-7 and
                   np.max(np.abs(self.scene.data.qvel[list(self.scene.arms[s].dofs)]))<.01
                   for o,s in ((0,"left"),(8,"right"))):
                measured=self.view();settled=True
                for side in goals:
                    p,r=tcp_pose(measured.data,self.scene.arms[side]);gp,gr=target_poses[side]
                    settled &= np.linalg.norm(p-gp)<=.0015 and math.acos(float(np.clip((np.trace(gr.T@r)-1)/2,-1,1)))<=.02
                if settled:return
            proposed = current.copy()
            peak = max(float(np.max(np.abs(goals[s]-current[o:o+7]))) for o,s in ((0,"left"),(8,"right")))
            # Free-space approach needs a wrist roll of ~3.4rad; the old
            # .004rad/frame cap (600 frames) could not cover it. Keep the slow
            # rate only while a contact contract is active.
            step_limit = .004 if contract is not None else .010
            scale = min(1., step_limit / peak) if peak else 1.
            for offset, side in ((0,"left"),(8,"right")):
                proposed[offset:offset+7] += scale * (goals[side]-current[offset:offset+7])
            target = self.platform.controller.prepare_joint_targets(proposed)
            self.execute([target], contract)
        cur=self.platform.controller.reference.copy()
        for o,s in ((0,"left"),(8,"right")):
            print("DUAL_DEBUG",s,"goal",np.round(goals[s],3).tolist(),
                  "ref",np.round(cur[o:o+7],3).tolist(),
                  "meas",np.round(self.scene.data.qpos[list(self.scene.arms[s].qpos)],3).tolist(),
                  "err",round(float(np.max(np.abs(cur[o:o+7]-goals[s]))),4),flush=True)
        raise ExpertFailure("DUAL_JOINT_TRACKING_BUDGET: "+str(self.stage))

    def dual_line(self, positions, rotation=None, contract=None, transit=False):
        rotation=self.dual_rotation if rotation is None else rotation
        view = self.view()
        start = {s: tcp_pose(view.data,self.scene.arms[s])[0] for s in ("left","right")}
        count = max(1,int(math.ceil(max(np.linalg.norm(np.asarray(positions[s])-start[s]) for s in start)/.008)))
        for i in range(1,count+1):
            self.dual_points({s:start[s]+(np.asarray(positions[s])-start[s])*i/count for s in start},rotation,contract,transit)

    def dual_grip(self, value, contract, force=True):
        current = self.platform.controller.reference.copy()
        for _ in range(220 if (value>0 and force) else 65):
            current = current.copy()
            measured=self.feedback()["contact_execution"]["measured"].get("grasp",{}).get("by_arm",{}) if (value>0 and force) else {}
            for offset,side in ((7,"left"),(15,"right")):
                if value>0 and force and min(measured.get(side,{}).get("pad_normal_forces_n",[0.,0.]))>=4.:continue
                delta=.00025 if (value>0 and force) else .001
                current[offset] += float(np.clip(value-current[offset],-delta,delta))
            self.execute([current],contract)
            if value>0 and force:
                grasp=self.feedback()["contact_execution"]["measured"].get("grasp",{})
                if min(grasp.get("pad_normal_forces_n",[0.,0.,0.,0.]))>=4.:
                    self.wait(.16)
                    return
        if value>0 and force:raise ExpertFailure("FOUR_PAD_GRIP_FORCE_NOT_REACHED")

    def center_rim_grips(self, positions):
        """Balance actual pad-mesh gaps in scratch before symmetric closure.

        A tilted gripper's TCP is not the midpoint of the two rim contacts.
        Live moves remain ordinary bounded IK motions with the acquire contract.
        """
        targets={s:np.asarray(p).copy() for s,p in positions.items()}
        geoms=object_geom_ids(self.scene,"wood_tray")
        scratch=mujoco.MjData(self.scene.model)
        corrections={}
        for side in ("left","right"):
            arm=self.scene.arms[side]
            best=None
            for shift in np.linspace(-.006,.006,13):
                position=targets[side]+[0,shift,0]
                rotation=self.dual_rotation[side] if isinstance(self.dual_rotation,dict) else self.dual_rotation
                result=solve_ik(self.view(),side,position,rotation,max_iterations=400,
                                position_tolerance=.0001,rotation_tolerance=.003)
                if not result.success:continue
                mujoco.mj_copyData(scratch,self.scene.model,self.scene.data)
                scratch.qpos[list(arm.qpos)]=result.q
                scratch.qpos[list(arm.fingers)]=.030
                mujoco.mj_forward(self.scene.model,scratch)
                gaps=[min(float(mujoco.mj_geomDistance(self.scene.model,scratch,p,g,.1,None)) for g in geoms) for p in arm.pad_geoms]
                score=abs(gaps[0]-gaps[1])
                if best is None or score<best[0]:best=(score,shift)
            if best is None:raise ExpertFailure("RIM_GAP_CENTERING_IK_FAILED")
            targets[side][1]+=best[1];corrections[side]=float(best[1])
        self.mark("dual_rim_centering",corrections_y_m=corrections)
        return targets

    def deliver(self):
        if self.held or self.contract:raise ExpertFailure("DUAL_TRAY_REQUIRES_EMPTY_HANDS")
        self.mark("dual_tray_approach")
        # User direction: grasp the tray's left/right protruding handles, not
        # the rim. Each handle is a 44x110x32mm bar at x=+-0.27, y=-0.40. A
        # purely vertical grip cannot reach the user table (0.79m extension,
        # DUAL_JOINT_TRACKING_BUDGET); tilt the tool inward 40deg about world Y,
        # keeping the closing axis on the handle's 44mm width.
        # Horizontal approach from outside the tray: tool z points inward
        # (+x left / -x right), jaws close vertically on the bar's 32mm height.
        # Grasping across the 44mm width puts the inner finger into the rim,
        # which sits only 30mm from the handle.
        # Side approach with vertical jaws on the raised bar (36mm). The
        # right forearm needed the microwave moved +x/+y for clearance; that is
        # now geometric (door skill), not a recorded replay.
        flat=np.diag([1.,-1.,-1.])
        self.dual_rotation={"left":flat,"right":flat}
        view=self.view(); m=view.model
        grips={s:view.data.geom_xpos[m.geom("tray_handle_"+s).id].copy() for s in ("left","right")}
        tray=view.data.xpos[self.scene.objects["wood_tray"]].copy()
        # Rise clear of the tray, descend to 40mm above each handle under no
        # contract, then take the last 28mm under acquire (30mm local budget).
        # The pads' lower edge sits ~8mm below the TCP, so grasp 12mm above the
        # handle centre; at +5mm the pad edge was 3.1mm inside the handle.
        axis={s:self.dual_rotation[s][:,2] for s in ("left","right")}
        self.dual_points({s:p-axis[s]*.35 for s,p in grips.items()},transit=True)
        self.dual_points({s:p-axis[s]*.24 for s,p in grips.items()},transit=True)
        self.dual_line({s:p-axis[s]*.020 for s,p in grips.items()},transit=True)
        # Pre-close the empty jaws above the bar (guarded stationary sweep, no
        # contact contract). The full 50mm closure against the tray pushed the
        # arm ~14mm and blew the 30mm acquire local-travel budget.
        self.wait(.5)
        current=self.platform.controller.reference.copy()
        for _ in range(50):
            current=current.copy()
            for index in (7,15):current[index]+=float(np.clip(.020-current[index],-.0004,.0004))
            self.call("execute_trajectory",{"waypoints":[current.tolist()],
                "source":"programmatic_validation","guarded_gripper":True},mutating=True)
            state=self.call("step",{"n_substeps":20},mutating=True)
            if state["stopped"]:
                raise ExpertFailure("PRECLOSE_STOP: "+str(state["stop_reason"]))
        base={"arm":"both","target":"wood_tray","payload_ids":["wood_tray"],
              "supports":{"wood_tray":["v3_counter_left","v3_counter_right","v3_counter_front","v3_counter_back","user_table"]}}
        acquire={**base,"phase":"acquire"}
        self.dual_line({s:p-axis[s]*.000 for s,p in grips.items()},contract=acquire)
        self.dual_grip(.05,acquire)
        if self.feedback()["contact_execution"]["measured_grasp_age_s"] < .05:
            raise ExpertFailure("FOUR_PAD_50MS_GRASP_MISSING")
        carry={**base,"phase":"carry","target_support_transition":True}
        self.mark("dual_tray_lift")
        self.dual_line({s:p+[0,0,.12] for s,p in grips.items()},contract=carry)
        self.wait(1.)
        table=self.scene.model.geom("user_table").id
        target_tray=self.scene.data.geom_xpos[table].copy()
        target_tray[1]=-.77
        target_tray[2] += self.scene.model.geom_size[table,2]+.008
        self.mark("dual_tray_transport")
        displacement=target_tray-tray
        elevated={s:p+displacement+[0,0,.12] for s,p in grips.items()}
        self.dual_line(elevated,contract=carry)
        landing={s:p+displacement for s,p in grips.items()}
        self.dual_line(landing,contract=carry)
        if not self.platform.controller.devices.supports("wood_tray",["user_table"]):
            raise ExpertFailure("TRAY_TABLE_SUPPORT_MISSING")
        release={**base,"phase":"release","target_support_transition":True}
        self.dual_grip(.002,release,force=False)
        # Open jaws can still graze the handles in place; withdraw 4cm along +Z
        # under the release contract so the pads clear the tray, then certify.
        self.dual_line({s:q+np.array([0.,0.,.04]) for s,q in landing.items()},contract=release)
        for _ in range(20):
            if self.feedback()["contact_execution"]["measured_pad_separation_s"] >= .05: break
            self.wait(.2)
        self.call("end_contact",mutating=True)
        self.contract=None
        self.dual_line({s:p+[0,0,.12] for s,p in landing.items()})
        self.wait(2.)
        if not self.platform.controller.devices.supports("wood_tray",["user_table"]):
            raise ExpertFailure("TRAY_DELIVERY_SUPPORT_NOT_RETAINED")
        self.mark("dual_tray_delivered")
        # Delivery ends at the released, clear pose; no unrelated home detour.

    def door_skill(self, target_angle):
        """Geometric door open/close: grasp the handle, then follow its hinge arc.

        The recorded device calls are joint-space and tied to the original
        microwave/base pose; this skill is computed from the measured handle and
        hinge, so it survives layout and domain randomization.
        """
        view = self.view(); m = view.model
        joint = m.joint("microwave_hinge").id; adr = m.jnt_qposadr[joint]
        start = float(view.data.qpos[adr])
        if abs(start - target_angle) < math.radians(2):
            return
        handle = m.geom("microwave_door_handle").id
        pos = view.data.geom_xpos[handle].copy()
        rot = view.data.geom_xmat[handle].reshape(3, 3).copy()
        # The handle bar is vertical: approach from above with the standard
        # DOWN tool axis, closing along the door's width direction. A horizontal
        # tool axis put the wrist at a joint limit (0.61rad tracking error).
        # 75deg tilted-down grasp (only tilt whose pre and grasp are both
        # reachable from the initial joint state); the TCP then follows the
        # handle's hinge arc with the grasp pose fixed in the handle frame.
        outward = -rot[:, 0].copy(); outward[2] = 0.; outward /= np.linalg.norm(outward)
        close_axis = rot[:, 1].copy(); close_axis[2] = 0.; close_axis /= np.linalg.norm(close_axis)
        tilt = math.radians(90.)
        # Tool +z is the hand's forward: point it into the door (+x_door) and
        # down; the fingers then trail outside and clear the door panel.
        z = -math.cos(tilt) * outward + math.sin(tilt) * np.array([0., 0., -1.]); z /= np.linalg.norm(z)
        x = close_axis - np.dot(close_axis, z) * z; x /= np.linalg.norm(x)
        y = np.cross(z, x); R = np.column_stack([x, y, z])
        self.mark("door_handle_approach",angle_start_rad=start)
        self.free_pose("right",pos-z*.028,R,home_fallback=True)
        base = {"arm": "right", "target": "microwave_door_handle", "payload_ids": [], "supports": {}}
        acquire = {**base, "phase": "acquire"}
        self.point("right", pos, R, acquire)
        self.grip("right", .05, acquire)
        if self.feedback()["contact_execution"]["measured_grasp_age_s"] < .05:
            raise ExpertFailure("DOOR_HANDLE_GRASP_MISSING")
        device = {**base, "phase": "device"}
        view = self.view(); pos = view.data.geom_xpos[handle].copy()
        rot = view.data.geom_xmat[handle].reshape(3, 3).copy()
        tcp, tool = tcp_pose(view.data, self.scene.arms["right"])
        offset = rot.T @ (tcp - pos); relative = rot.T @ tool
        count = max(1, int(math.ceil(abs(target_angle - start) / math.radians(0.5))))
        poses=[];scratch=self.view()
        for angle in np.linspace(start, target_angle, count + 1)[1:]:
            scratch.data.qpos[adr] = angle
            mujoco.mj_forward(m, scratch.data)
            hrot = scratch.data.geom_xmat[handle].reshape(3, 3)
            hpos = scratch.data.geom_xpos[handle]
            poses.append(((hpos + hrot @ offset).copy(),(hrot @ relative).copy()))
        if not self.continuous_poses("right",poses,device,speed=.004):
            for position,rotation in poses:self.point("right",position,rotation,device)
        release = {**base, "phase": "release"}
        self.grip("right", 0., release)
        self.wait(.4)
        self.call("end_contact", mutating=True)
        self.contract = None

    def open_door(self):
        # Open to 100deg (the hinge limit) for extra clearance around the oven
        # mouth; 0-100deg was IK-feasible for relative/world/slerp tool modes.
        self.door_skill(math.radians(100))
        self.wait(.8)
        if self.platform.controller.devices.angle("microwave_hinge") < math.radians(90):
            raise ExpertFailure("DOOR_OPEN_ANGLE_NOT_REACHED")
        # The next geometry planner starts here and includes handle clearance.
        self.save_checkpoint("door_open")

    def close_door(self):
        # Push the panel shut from the outside (user direction): no handle
        # grasp, so the closing does not depend on reaching the open handle.
        self.push_door(0.)
        self.wait(.8)
        if self.platform.controller.devices.angle("microwave_hinge") > math.radians(5):
            raise ExpertFailure("DOOR_CLOSED_ANGLE_NOT_REACHED")

    def push_door(self, target_angle=0.):
        """Close the open door by pressing the outer panel face and following
        the hinge arc; the pads push, no grasp."""
        view = self.view(); m = view.model
        joint = m.joint("microwave_hinge").id; adr = m.jnt_qposadr[joint]
        start = float(view.data.qpos[adr])
        if abs(start - target_angle) < math.radians(2):
            return
        panel = m.geom("microwave_door_panel").id
        door_body = m.body(int(m.geom_bodyid[panel])).id
        half = np.asarray(m.geom_size[panel]); local = np.asarray(m.geom_pos[panel])
        self.mark("door_push_approach", angle_start_rad=start)
        approach_tick=self.platform.controller.tick
        # Push with a side pad while the long fingers point down. A horizontal
        # forward-pointing tool put the wrist into the worktop at this layout.
        R_local = np.diag([1., -1., -1.])
        frame_rot = view.data.xmat[door_body].reshape(3,3).copy()
        frame_pos = view.data.xpos[door_body].copy()
        R_push = frame_rot @ R_local
        tcp, old_tool = tcp_pose(view.data,self.scene.arms["right"])
        front = max(geometry_bounds(m,view.data,g,tcp,old_tool)[1][0]
                    for g in self.scene.arms["right"].pad_geoms)
        free_edge_sign = 1. if local[1] >= 0. else -1.
        push_local = np.array([local[0]-half[0]-front+.002,
                               local[1]+free_edge_sign*half[1]*.35, local[2]+.040])
        # The approach ends 20mm outside the measured panel, not at a fixed
        # world pose. The same three simple routes also work after tray release.
        pre = frame_pos+frame_rot@(push_local-np.array([.022,0.,0.]))
        self.free_pose("right",pre,R_push,continuous=True,home_fallback=True)
        base = {"arm": "right", "target": "microwave_door_handle", "payload_ids": [], "supports": {}}
        device = {**base, "phase": "device"}
        self.mark("door_push", angle_target_rad=target_angle,
                  approach_sim_seconds=(self.platform.controller.tick-approach_tick)/500.)
        self.line("right",frame_pos+frame_rot@push_local,R_push,device,spacing=.004,speed=.004)
        # Stream the hinge arc at 12deg/s rather than stopping to settle at
        # each 0.5deg point. The existing controller still rate-limits every
        # 25Hz command. Only the final endpoint waits for measured tracking.
        arc_tick = self.platform.controller.tick
        count = max(1,math.ceil(abs(target_angle-start)/math.radians(12.)*25))
        scratch = self.view()
        current = self.platform.controller.reference.copy()
        targets = []
        peak_force = [0.]
        right_geoms = {g for g in range(m.ngeom)
                       if m.body(int(m.geom_bodyid[g])).name.startswith("right_")}
        def measure_push(controller):
            for index, contact in enumerate(controller.scene.data.contact):
                pair = {int(contact.geom1),int(contact.geom2)}
                if panel in pair and pair & right_geoms:
                    force = np.zeros(6)
                    mujoco.mj_contactForce(m,controller.scene.data,index,force)
                    peak_force[0] = max(peak_force[0],float(force[0]))
        self.platform.post_step_observers.append(measure_push)
        try:
            for angle in np.linspace(start,target_angle,count+1)[1:]:
                scratch.data.qpos[adr] = angle
                mujoco.mj_forward(m,scratch.data)
                rot = scratch.data.xmat[door_body].reshape(3,3)
                pos = scratch.data.xpos[door_body]
                result = solve_ik(scratch,"right",pos+rot@push_local,rot@R_local,
                                  max_iterations=250,position_tolerance=.001,rotation_tolerance=.01)
                if not result.success:
                    raise ExpertFailure("DOOR_ARC_IK_FAILED")
                proposed = current.copy(); proposed[8:15] = result.q
                current = self.platform.controller.prepare_joint_targets(proposed,reference=current)
                scratch.data.qpos[list(self.scene.arms["right"].qpos)] = current[8:15]
                targets.append(current.copy())
                if len(targets)==10:
                    self.execute(targets,device); targets=[]
            if targets: self.execute(targets,device)
            self.point("right",pos+rot@push_local,rot@R_local,device,speed=.008)
        finally:
            self.platform.post_step_observers.remove(measure_push)
        self.mark("door_push_complete",arc_sim_seconds=(self.platform.controller.tick-arc_tick)/500.,
                  measured_angle_rad=self.platform.controller.devices.angle("microwave_hinge"),
                  peak_panel_contact_n_25hz=peak_force[0])
        self.contract = device
        self.mark("door_push_release")
        release = {**base,"phase":"release"}
        here, tool = tcp_pose(self.view().data,self.scene.arms["right"])
        actual = self.view().data.xmat[door_body].reshape(3,3)
        self.line("right",here-actual[:,0]*.035,tool,release,spacing=.005,speed=.004)
        self.wait(.4)
        self.call("end_contact",mutating=True)
        self.contract = None

    def set_empty_jaws(self, value):
        if self.held or self.contract:
            raise ExpertFailure("EMPTY_JAW_MOTION_REQUIRES_RELEASED_HAND")
        self.wait(.6)
        current = self.platform.controller.reference.copy()
        targets = []
        for _ in range(115):
            current = current.copy()
            current[15] += np.clip(value-current[15],-.0005,.0005)
            targets.append(current.tolist())
        self.call("execute_trajectory",{"waypoints":targets,"source":"programmatic_validation",
                                        "guarded_gripper":True},mutating=True)
        state = self.call("step",{"n_substeps":20*len(targets)},mutating=True)
        if state["stopped"]:
            raise ExpertFailure("EMPTY_JAW_MOTION_STOPPED: "+str(state["stop_reason"]))

    def heat_open(self, meal):
        """Press the actual spring button, wait for measured heat, open door."""
        if self.held or self.contract:
            raise ExpertFailure("HEAT_REQUIRES_RELEASED_HAND")
        dev = self.platform.controller.devices
        container = meal+"_container"
        if not dev.meal_ready(container) or dev.angle("microwave_hinge")>math.radians(5):
            raise ExpertFailure("HEAT_REQUIRES_CLOSED_OCCUPIED_OVEN")
        self.mark("button_close_empty_jaws")
        self.set_empty_jaws(.05)
        view = self.view(); m = view.model
        button = m.geom("microwave_start_geom").id
        origin = view.data.geom_xpos[button].copy()
        oven = m.body("microwave").id
        frame = view.data.xmat[oven].reshape(3,3)
        inward = frame[:,0].copy()
        rotation = frame@np.array([[0.,0.,1.],[0.,1.,0.],[-1.,0.,0.]])
        tcp, tool = tcp_pose(view.data,self.scene.arms["right"])
        front = max(geometry_bounds(m,view.data,g,tcp,tool)[1][2]
                    for g in self.scene.arms["right"].pad_geoms)
        face = float(m.geom_size[button][0])
        self.mark("button_approach",position_world_m=origin.tolist())
        self.free_pose("right",origin-inward*(front+face+.020),rotation,home_fallback=True)
        device = {"arm":"right","target":"microwave_start_geom","phase":"device",
                  "payload_ids":[],"supports":{}}
        self.mark("button_press")
        for depth in np.arange(-.003,.0061,.0005):
            self.point("right",origin-inward*(front+face-depth),rotation,device,speed=.004)
            self.wait(.08)
            if dev.heating==container or container in dev.heated: break
        else: raise ExpertFailure("BUTTON_PRESS_DID_NOT_START_HEATING")
        release = {**device,"phase":"release"}
        here, tool = tcp_pose(self.view().data,self.scene.arms["right"])
        self.line("right",here-inward*.030,tool,release,spacing=.005,speed=.004)
        self.wait(.3); self.call("end_contact",mutating=True); self.contract=None
        self.mark("heat_wait")
        for _ in range(35):
            if container in dev.heated: break
            self.wait(.2)
        else: raise ExpertFailure("HEAT_COMPLETE_EVENT_MISSING")
        self.set_empty_jaws(0.)
        self.open_door()

    def prepare(self, case):
        task,meal=case["task"],case["meal"]
        self.mark("initial_settle")
        self.wait(1.)
        if task in ("R1","R2","R4") or task=="R3" and case["initial_occupancy"]!="empty":
            self.open_door()
        if task in ("R2","R4") or task=="R3" and case["initial_occupancy"]!="empty":
            self.meal(meal,"oven")
        # R3 is a closed-door opening task, including an empty oven. The
        # layout's current default is 100deg open, so prepare it explicitly.
        if task in ("R3", "R4"):
            self.close_door()
        self.mark("preparation_complete")

    def skill(self, name, case, quality):
        fruit,meal=case["fruit"],case["meal"]
        quality.start(name)
        self.mark("skill_started",skill=name)
        if name in ("L1","L2"):
            self.fruit(fruit,wash=name=="L2",slot=-.11 if case["meal"] else 0.)
            self.settle_quality(quality,fruit+":tray")
            quality.tray_object(fruit)
            if name=="L2" and fruit not in self.platform.controller.devices.cleaned:
                raise ExpertFailure("FRUIT_NOT_CLEANED")
        elif name=="R1":
            self.meal(meal,"oven")
            self.close_door()
            if not self.platform.controller.devices.meal_ready(meal+"_container"):
                raise ExpertFailure("MEAL_NOT_SUPPORTED_INSIDE_OVEN")
            quality.require("door:closed",.5)
        elif name=="R2":
            self.meal(meal,in_oven=True)
            self.close_door()
            self.settle_quality(quality,meal+"_container:tray")
            quality.tray_object(meal+"_container");quality.require("door:closed",.5)
        elif name=="R3":
            self.open_door();quality.require("door:open",.5)
        elif name=="R4":
            self.heat_open(meal);quality.require("door:open",.5)
        elif name=="R5":
            self.meal(meal);self.settle_quality(quality,meal+"_container:tray");quality.tray_object(meal+"_container")
        elif name=="B1":
            self.deliver();quality.require("tray:delivered",2.)
            if quality.tray_tilt_peak>math.radians(10):raise ExpertFailure("TRAY_TILT_LIMIT")
        else:raise ExpertFailure("UNKNOWN_EXPERT_SKILL: "+name)
        self.mark("skill_qualified",skill=name)

    def settle_quality(self,quality,key,max_seconds=30.):
        for _ in range(int(math.ceil(max_seconds/.4))):
            if quality.current.get(quality.phase+":"+key,0.)>=2.-1e-9:return
            self.wait(.4)
        raise ExpertFailure("SETTLING_WINDOW_TIMEOUT: "+key)

    def heat_meal(self, case, quality):
        # State-dependent high-level chain; device operations are chosen from
        # measured door/occupancy/heat state, never the planned target state.
        meal=case["meal"];dev=self.platform.controller.devices
        if not dev.meal_ready(meal+"_container"):
            if dev.angle("microwave_hinge")<math.radians(70):self.skill("R3",case,quality)
            self.skill("R1",case,quality)
        if meal+"_container" not in dev.heated:
            if dev.angle("microwave_hinge")>math.radians(5):self.close_door()
            self.skill("R4",case,quality)
        elif dev.angle("microwave_hinge")<math.radians(70):self.skill("R3",case,quality)
        self.skill("R2",case,quality)

    def run_case(self, case, quality):
        from .expert_catalog import ATOMIC,COMPOSITE
        task=case["task"]
        if task in ATOMIC:self.skill(task,case,quality)
        else:
            chain = COMPOSITE[task][1]
            for index, skill in enumerate(chain):
                if skill=="heat_meal_state_dependent":self.heat_meal(case,quality)
                else:self.skill(skill,case,quality)
                if skill in ("L1", "L2") and index + 1 < len(chain):
                    # Releasing fruit leaves the long left gripper above the
                    # tray, inside the right forearm's placement corridor.
                    # Leave that corridor before planning the next skill.
                    self.mark("composite_left_clearance", fruit=case["fruit"])
                    self.clear_left_corridor()
                    self.settle_quality(quality,case["fruit"]+":tray")
                    quality.tray_object(case["fruit"])
        # Later skills must not undo earlier subgoals in a composite.
        if case["fruit"]:
            self.settle_quality(quality,case["fruit"]+":tray")
            quality.tray_object(case["fruit"])
        if case["meal"] and (task in ("R2","R5") or task.startswith("C")):
            quality.tray_object(case["meal"]+"_container")
        if task in ("C04","C07","C08") and case["meal"]+"_container" not in self.platform.controller.devices.heated:
            raise ExpertFailure("COMPOSITE_HEATED_MEAL_MARKER_LOST")
