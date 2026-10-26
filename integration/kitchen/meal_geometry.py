"""Small measured-handle planner; no recorded poses or runtime attachments.

Candidates follow the compiled handle frame, including its tilt. Planning only
uses detached MuJoCo data. Endpoint clearance is not a dynamic grasp proof.
"""
from dataclasses import dataclass, field, replace
import math

import mujoco
import numpy as np

from robot_arm.kitchen.control import solve_ik, tcp_pose
from robot_arm.kitchen.collision import CollisionGeometry
from robot_arm.kitchen.payload_geometry import CarriedGeometry
from robot_arm.kitchen.registry import geometry_bounds, object_bounds


def handle_frames(scene, name):
    """Query both compiled bars, in world coordinates at the supplied state."""
    if name not in ("burger_container", "hotdog_container"):
        raise ValueError("MEAL_CARRIER_REQUIRED")
    meal = name.removesuffix("_container")
    return [{"geom_name": meal + "_grasp_" + side,
             "position_world_m": scene.data.geom_xpos[scene.model.geom(meal + "_grasp_" + side).id].tolist(),
             "rotation_world": scene.data.geom_xmat[scene.model.geom(meal + "_grasp_" + side).id].reshape(3, 3).tolist(),
             "half_size_m": scene.model.geom_size[scene.model.geom(meal + "_grasp_" + side).id].tolist()}
            for side in ("left", "right")]


def grasp_candidates(scene, name):
    """Pinch the bar's thin axis, approach along its outboard tangent.

    Bounded pitch alternatives and both jaw rolls cover IK alternatives;
    the object's measured translation, yaw and bar tilt are never hard-coded.
    """
    for frame in handle_frames(scene, name):
        bar = np.asarray(frame["rotation_world"])
        sign = 1. if frame["geom_name"].endswith("left") else -1.
        for pitch in (0., -15., -30., 15.):
            t = math.radians(pitch)
            for yaw in (0., -25., 25., -45., 45.):
                a = math.radians(yaw)
                tangent = sign * (bar[:, 0] * math.cos(a) + bar[:, 1] * math.sin(a))
                z = tangent * math.cos(t) + bar[:, 2] * math.sin(t)
                for roll in (-1., 1.):
                    x = roll * (bar[:, 2] - np.dot(bar[:, 2], z) * z)
                    x /= np.linalg.norm(x)
                    rotation = np.column_stack((x, np.cross(z, x), z))
                    point = np.asarray(frame["position_world_m"]) - z * .006
                    yield frame, point, rotation


@dataclass
class MealGrasp:
    handle: str
    point: np.ndarray
    rotation: np.ndarray
    pre: np.ndarray
    pre_joints: np.ndarray
    clearance_m: float
    approach_joints: list = field(default_factory=list)


def plan_grasp(scene, name, side="right", *, plan_approach=False, destination=None,
               approach_distance=.12, lift_height=.045):
    """Choose a reachable pre/grasp/lift triplet with actual robot clearance.

    Checks the full robot against compiled geometry, including the parked arm,
    open door, food and handle posts. It never advances the live simulation.
    The approach path and measured bilateral grasp are checked separately.
    """
    geometry = CollisionGeometry(scene)
    pairs = geometry.pairs()
    arm = scene.arms[side]
    data = mujoco.MjData(scene.model)
    view = replace(scene, data=data)
    rejected = {"ik": 0, "clearance": 0, "approach": 0}
    for frame, point, rotation in grasp_candidates(scene, name):
        pre = point - rotation[:, 2] * approach_distance
        data.qpos[:] = scene.data.qpos
        solutions = []
        minimum = float("inf")
        grasp_geometry = None
        targets = [pre + (point-pre)*t for t in np.linspace(0., 1., 9)]
        targets.append(point + [0., 0., lift_height])
        for index, target in enumerate(targets):
            result = solve_ik(view, side, target, rotation, max_iterations=400,
                              position_tolerance=.001, rotation_tolerance=.01)
            if not result.success:
                rejected["ik"] += 1
                break
            data.qpos[list(arm.qpos)] = result.q
            checker = geometry if index < len(targets)-1 else grasp_geometry
            checked_pairs = pairs if index < len(targets)-1 else np.asarray([
                p for p in checker.pairs((name, name.removesuffix("_container")))
                if not all(int(g) in checker.geoms for g in p)], dtype=int)
            # During the hypothetical lift the carrier follows the TCP. Its
            # original bar must not be mistaken for a stationary obstacle.
            distances = checker.distances(data.qpos, checked_pairs)
            minimum = min(minimum, float(distances.min()))
            if minimum < .001:
                rejected["clearance"] += 1
                break
            solutions.append(result.q.copy())
            if index == len(targets)-2:
                grasp_geometry = CarriedGeometry(scene, data.qpos, side,
                                                (name, name.removesuffix("_container")))
        if len(solutions) == len(targets):
            grasp = MealGrasp(frame["geom_name"], point, rotation, pre, solutions[0], minimum)
            if destination == "tray":
                # A reachable pickup can still leave an impossible wrist pose
                # at the tray. Screen that end pose before touching the bar.
                root = scene.data.xpos[scene.objects[name]]
                support = scene.model.geom("tray_bottom").id
                _, top = geometry_bounds(scene.model, scene.data, support)
                lo, _ = object_bounds(scene, name)
                desired = scene.data.geom_xpos[support].copy()
                desired[0] += .11
                desired[2] = top[2] + root[2]-lo[2] + .001
                final = desired + point-root
                reachable = True
                for goal in (final+[0., 0., .10], final):
                    result = solve_ik(view, side, goal, rotation, max_iterations=400,
                                      position_tolerance=.001, rotation_tolerance=.01)
                    if not result.success:
                        reachable = False; break
                    data.qpos[list(arm.qpos)] = result.q
                if not reachable:
                    rejected["ik"] += 1
                    continue
            if plan_approach:
                try: grasp.approach_joints = approach_path(scene, grasp, side)
                except ValueError:
                    rejected["approach"] += 1
                    continue
            return grasp
    raise ValueError(f"NO_GEOMETRIC_MEAL_GRASP: {name}: {rejected}")


def approach_path(scene, grasp, side="right"):
    """Try a direct approach, then two raised doglegs; no graph search.

    Cartesian samples use 20mm/nominal 5deg divisions; joint-edge samples <=0.035rad are
    checked against the actual full robot geometry. This is a sampled plan,
    not a continuous collision certificate or a physics success claim.
    """
    arm = scene.arms[side]
    geometry = CollisionGeometry(scene)
    # The fingers stay fixed during this arm-only route. Opposing pads may
    # intentionally touch when the empty jaws are closed for button pressing;
    # their invariant self-contact is not a scene obstacle.
    finger_bodies = {int(scene.model.geom_bodyid[g]) for g in arm.pad_geoms}
    pads = {g for g in geometry.active if int(scene.model.geom_bodyid[g]) in finger_bodies}
    pairs = np.asarray([p for p in geometry.pairs()
                        if not all(int(g) in pads for g in p)], dtype=int)
    start, start_rotation = tcp_pose(scene.data, arm)
    routes = [(grasp.pre,)]
    for lift in (.12, .24):
        z = max(start[2], grasp.pre[2]) + lift
        routes.append((np.array([*start[:2], z]),
                       np.array([*grasp.pre[:2], z]), grasp.pre))
    for route in routes:
        data = mujoco.MjData(scene.model)
        data.qpos[:] = scene.data.qpos
        view = replace(scene, data=data)
        path = []
        position, rotation = start.copy(), start_rotation.copy()
        good = True
        for end in route:
            qa, qb = np.empty(4), np.empty(4)
            mujoco.mju_mat2Quat(qa, rotation.ravel())
            mujoco.mju_mat2Quat(qb, grasp.rotation.ravel())
            if qa @ qb < 0: qb *= -1
            angle = 2 * math.acos(float(np.clip(qa @ qb, -1., 1.)))
            count = max(1, math.ceil(np.linalg.norm(end-position)/.02), math.ceil(angle/math.radians(5)))
            for t in np.linspace(0., 1., count+1)[1:]:
                quat = (1-t)*qa+t*qb; quat /= np.linalg.norm(quat)
                mat = np.empty(9); mujoco.mju_quat2Mat(mat, quat)
                result = solve_ik(view, side, position+(end-position)*t, mat.reshape(3, 3),
                                  max_iterations=250, position_tolerance=.001, rotation_tolerance=.01)
                if not result.success:
                    good = False; break
                previous = data.qpos.copy()
                candidate = previous.copy(); candidate[list(arm.qpos)] = result.q
                samples = max(1, math.ceil(np.max(np.abs(candidate-previous))/.035))
                if any(np.min(geometry.distances(previous+(candidate-previous)*f, pairs)) < .001
                       for f in np.linspace(0., 1., samples+1)):
                    good = False; break
                data.qpos[:] = candidate
                path.append(result.q.copy())
            if not good: break
            position, rotation = np.asarray(end).copy(), grasp.rotation.copy()
        if good:
            return path
    raise ValueError("NO_SIMPLE_MEAL_APPROACH: " + grasp.handle)
