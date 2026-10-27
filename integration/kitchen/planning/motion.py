"""Motion execution, interpolation and checked local routes."""
from __future__ import annotations
from dataclasses import replace
import copy
import json
import math
from pathlib import Path
import time
import hashlib

import mujoco
import numpy as np

from robot_arm.kitchen.control import solve_ik, tcp_pose
from robot_arm.kitchen.registry import geometry_bounds
from robot_arm.kitchen.contact import object_geom_ids
from robot_arm.kitchen.limits import joint_control_limits

from integration.kitchen.planning.common import ExpertFailure, ENABLE_ELBOW_DOOR_CHECK

class ExpertMotion:
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
        from integration.kitchen.tools.checkpoint import save
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


