"""Transactional reset-only sampling; no planner, attachment or model edits.

Consumers must bind episode_scene_hash to reset/state/recordings. The compiled
scene identity is deliberately retained as a base model identity.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import hashlib
import json
import numbers

import mujoco
import numpy as np

from .collision import CollisionGeometry
from .contact import object_geom_ids
from .control import solve_ik, tcp_pose
from .layouts import layout_manifest
from .registry import FRUITS, digest, geometry_bounds, object_bounds

SCHEMA = "kitchen_research_initialization_v1"
ALGORITHM_VERSION = "local_xy80mm_yaw5deg_meal_grasp_clearance_v3"
LOCAL_XY_HALF_RANGE_M = .08
LOCAL_YAW_HALF_RANGE_RAD = np.pi / 36
MAX_ATTEMPTS_PER_GROUP = 64
OBSTACLE_CLEARANCE_M = .005
GROUP_CLEARANCE_M = .012
MEAL_GRASP_CLEARANCE_M = .060
SUPPORT_GAP_M = .0002
PENETRATION_TOLERANCE_M = 1e-6
GROUPS = (("burger_container", "burger"), ("hotdog_container", "hotdog"),
          ("banana",), ("apple",), ("pear",), ("tomato",))
RANDOMIZED = tuple(name for group in GROUPS for name in group)


def _meal_access_pair(first, second):
    meals = {"burger", "hotdog"}
    a, b = first.removesuffix("_container"), second.removesuffix("_container")
    return ((a in meals and b in meals and a != b) or
            (a in meals and second == "wood_tray") or
            (b in meals and first == "wood_tray"))


class ResearchInitializationError(RuntimeError):
    """Fail-closed initialization failure; metadata is JSON serializable."""

    def __init__(self, code, metadata=None):
        self.metadata = {"schema": SCHEMA, "status": "FAILED", "code": code,
                         "live_state_committed": False, **(metadata or {})}
        super().__init__(code)


def _geoms(scene, name):
    m = scene.model
    return sorted(g for g in object_geom_ids(scene, name)
                  if m.geom_contype[g] or m.geom_conaffinity[g])


def _free_address(scene, name):
    m, body = scene.model, scene.objects[name]
    if int(m.body_jntnum[body]) != 1:
        raise ResearchInitializationError("EXPECTED_ONE_FREE_JOINT", {"object": name})
    joint = int(m.body_jntadr[body])
    if m.jnt_type[joint] != mujoco.mjtJoint.mjJNT_FREE:
        raise ResearchInitializationError("EXPECTED_FREE_JOINT", {"object": name})
    return int(m.jnt_qposadr[joint]), int(m.jnt_dofadr[joint])


def _supports(scene, data):
    m = scene.model
    result = []
    for g in range(m.ngeom):
        if not m.geom(g).name.startswith("v3_counter_"):
            continue
        if m.geom_type[g] != mujoco.mjtGeom.mjGEOM_BOX:
            raise ResearchInitializationError("UNSUPPORTED_COUNTER_GEOMETRY")
        if not np.allclose(data.geom_xmat[g].reshape(3, 3), np.eye(3), atol=1e-12):
            raise ResearchInitializationError("NON_HORIZONTAL_COUNTER")
        lo, hi = geometry_bounds(m, data, g)
        result.append((g, lo, hi))
    if not result:
        raise ResearchInitializationError("NO_COUNTER_SUPPORT")
    return result


def _rectangle_covered(lo, hi, rectangles):
    """Exact union coverage for an AABB, including slab seams and sink holes."""
    axes = []
    for axis in range(2):
        cuts = [lo[axis], hi[axis]]
        cuts.extend(float(np.clip(p[axis], lo[axis], hi[axis]))
                    for a, b in rectangles for p in (a, b))
        axes.append(sorted(set(cuts)))
    for x0, x1 in zip(axes[0][:-1], axes[0][1:]):
        for y0, y1 in zip(axes[1][:-1], axes[1][1:]):
            if x1-x0 <= 1e-12 or y1-y0 <= 1e-12:
                continue
            p = np.array([(x0+x1)/2, (y0+y1)/2])
            if not any(np.all(p >= a[:2]-1e-10) and np.all(p <= b[:2]+1e-10)
                       for a, b in rectangles):
                return False
    return True


def _regions(scene, supports):
    layout = layout_manifest(scene.layout_id)
    lo = np.min([a for _, a, _ in supports], axis=0)
    hi = np.max([b for _, _, b in supports], axis=0)
    left = np.asarray(layout["base_poses"]["left"]["position"])
    right = np.asarray(layout["base_poses"]["right"]["position"])
    # Conservative front-of-base subregions, not reach-radius estimates.
    return {"left": ([float(lo[0]+.02), float(lo[1]+.02)],
                     [float(left[0]-.04), float(min(-.12, left[1]-.12))]),
            "right": ([float(right[0]+.04), float(lo[1]+.02)],
                      [float(hi[0]-.02), float(min(-.12, right[1]-.12))])}


def _distance(scene, data, a, b):
    return float(mujoco.mj_geomDistance(scene.model, data, a, b, 2., None))


def _group_check(scene, data, group, region, supports, obstacles, previous):
    m = scene.model
    bounds = {n: object_bounds(scene, n, data=data) for n in group}
    lo = np.min([a for a, _ in bounds.values()], axis=0)
    hi = np.max([b for _, b in bounds.values()], axis=0)
    if np.any(lo[:2] < np.asarray(region[0])-1e-9) or np.any(hi[:2] > np.asarray(region[1])+1e-9):
        return None, "FULL_FOOTPRINT_OUTSIDE_REGION"
    root = group[0]
    root_lo, root_hi = bounds[root]
    top = float(supports[0][2][2])
    if abs(root_lo[2]-top) > SUPPORT_GAP_M:
        return None, "COUNTER_SUPPORT_HEIGHT"
    slabs = [(a, b) for _, a, b in supports if abs(b[2]-top) < 1e-9]
    if not _rectangle_covered(root_lo, root_hi, slabs):
        return None, "FULL_FOOTPRINT_NOT_SUPPORTED"
    support_ids = {g for g, _, _ in supports}
    all_geoms = [g for name in group for g in _geoms(scene, name)]
    min_obstacle, min_group = 2., 2.
    closest_obstacle, closest_group = None, None
    for g in all_geoms:
        for h in obstacles:
            distance = _distance(scene, data, g, h)
            threshold = -PENETRATION_TOLERANCE_M if h in support_ids else OBSTACLE_CLEARANCE_M
            if _meal_access_pair(root, m.body(int(m.geom_bodyid[h])).name):
                threshold = MEAL_GRASP_CLEARANCE_M
            if distance < threshold:
                return None, "OBSTACLE_OR_SUPPORT_COLLISION"
            if h not in support_ids and distance < min_obstacle:
                min_obstacle, closest_obstacle = distance, [m.geom(g).name, m.geom(h).name]
        for h in previous:
            distance = _distance(scene, data, g, h)
            threshold = (MEAL_GRASP_CLEARANCE_M
                         if _meal_access_pair(root, m.body(int(m.geom_bodyid[h])).name)
                         else GROUP_CLEARANCE_M)
            if distance < threshold:
                return None, "INTER_GROUP_CLEARANCE"
            if distance < min_group:
                min_group, closest_group = distance, [m.geom(g).name, m.geom(h).name]
    root_support = min(_distance(scene, data, g, h)
                       for g in _geoms(scene, root) for h in support_ids)
    if root_support > SUPPORT_GAP_M or root_support < -PENETRATION_TOLERANCE_M:
        return None, "NO_ACTUAL_COUNTER_SUPPORT"
    carrier = None
    if len(group) == 2:
        meal = group[1]
        body = scene.objects[root]
        origin, rotation = data.xpos[body], data.xmat[body].reshape(3, 3)
        food_boxes = [geometry_bounds(m, data, g, origin, rotation) for g in _geoms(scene, meal)]
        food_lo = np.min([a for a, _ in food_boxes], axis=0)
        food_hi = np.max([b for _, b in food_boxes], axis=0)
        plate = m.geom(meal+"_plate").id
        plate_lo, plate_hi = geometry_bounds(m, data, plate, origin, rotation)
        if np.any(food_lo[:2] < plate_lo[:2]-1e-9) or np.any(food_hi[:2] > plate_hi[:2]+1e-9):
            return None, "FOOD_FULL_FOOTPRINT_OUTSIDE_PLATE"
        distances = [_distance(scene, data, g, h)
                     for g in _geoms(scene, meal) for h in _geoms(scene, root)]
        plate_gap = min(_distance(scene, data, g, plate) for g in _geoms(scene, meal))
        if min(distances) < -PENETRATION_TOLERANCE_M or plate_gap > SUPPORT_GAP_M:
            return None, "FOOD_CARRIER_SUPPORT_OR_PENETRATION"
        carrier = {"food": meal, "carrier": root, "plate_gap_m": plate_gap,
                   "full_food_footprint_inside_plate": True,
                   "relative_position_local_m": (rotation.T@(data.xpos[scene.objects[meal]]-origin)).tolist(),
                   "relative_rotation_matrix": (rotation.T@data.xmat[scene.objects[meal]].reshape(3, 3)).tolist()}
    return {"full_footprint_region_pass": True, "full_support_footprint_pass": True,
            "bounds_world_m": {n: [a.tolist(), b.tolist()] for n, (a, b) in bounds.items()},
            "counter_gap_m": root_support, "food_carrier": carrier,
            "min_obstacle_clearance_m": min_obstacle, "closest_obstacle_pair": closest_obstacle,
            "min_previous_group_clearance_m": min_group, "closest_previous_group_pair": closest_group}, None


def _approach_check(scene, data, root, checker):
    """Two independent static endpoint IK checks, never a path/grasp certificate."""
    side = "left" if root in FRUITS else "right"
    arm = scene.arms[side]
    view = replace(scene, data=data)
    bounds = object_bounds(scene, root, data=data)
    center = (bounds[0]+bounds[1])/2
    _, rotation = tcp_pose(data, arm)
    pairs = checker.pairs()
    rows = []
    scratch = mujoco.MjData(scene.model)
    for clearance in (.16, .10):
        target = center.copy()
        target[2] = bounds[1][2]+clearance
        result = solve_ik(view, side, target, rotation, max_iterations=180, scratch_data=scratch)
        row = {"target_world_m": target.tolist(), "rotation_matrix": rotation.tolist(),
               "q7_rad": result.q.tolist(), "ik_success": bool(result.success),
               "position_error_m": result.position_error, "rotation_error_rad": result.rotation_error}
        if result.success:
            q = data.qpos.copy()
            q[list(arm.qpos)] = result.q
            distances = checker.distances(q, pairs, cutoff=.3)
            minimum = int(np.argmin(distances))
            row.update(min_robot_clearance_m=float(distances[minimum]),
                       closest_pair=[scene.model.geom(int(g)).name for g in pairs[minimum]],
                       collision_free=bool(np.all(distances >= .002-1e-9)))
        else:
            row["collision_free"] = False
        rows.append(row)
        if not row["collision_free"]:
            return None
    return {"status": "FINITE_STATIC_APPROACH_IK_PASS", "side": side, "rows": rows,
            "joint_count": 7, "continuous_path_verified": False,
            "pinch_or_physical_grasp_verified": False}


def audit_object_separation(scene, *, data=None):
    """All free-object pairs, including the fixed-position tray and carriers.

    Only food/own-carrier pairs may touch; penetration is never intentional.
    This uses signed collision-geometry distances, not body-center distances.
    """
    data = scene.data if data is None else data
    group_by_name = {name: index for index, group in enumerate(GROUPS) for name in group}
    names = sorted(scene.objects)
    pairs, errors = [], []
    for i, name in enumerate(names):
        for other in names[i + 1:]:
            same_group = name in group_by_name and group_by_name.get(name) == group_by_name.get(other)
            distance = min(_distance(scene, data, a, b)
                           for a in _geoms(scene, name) for b in _geoms(scene, other))
            required = (-PENETRATION_TOLERANCE_M if same_group else
                        MEAL_GRASP_CLEARANCE_M if _meal_access_pair(name, other) else
                        GROUP_CLEARANCE_M if name in group_by_name and other in group_by_name else OBSTACLE_CLEARANCE_M)
            row = {"objects": [name, other], "signed_distance_m": distance,
                   "minimum_distance_m": required, "food_carrier_support_pair": same_group,
                   "pass": distance >= required}
            pairs.append(row)
            if not row["pass"]:
                errors.append(row)
    return {"pass": not errors, "pairs": pairs, "errors": errors,
            "method": "minimum signed MuJoCo collision-geom distance for every movable-object pair"}


def audit_research_initial_state(scene, *, data=None):
    """Read-only full-shape/support audit, also reusable after a settling witness."""
    data = scene.data if data is None else data
    supports = _supports(scene, data)
    regions = _regions(scene, supports)
    moving = {g for name in RANDOMIZED for g in _geoms(scene, name)}
    m = scene.model
    obstacles = [g for g in range(m.ngeom) if g not in moving
                 and (m.geom_contype[g] or m.geom_conaffinity[g])]
    previous, rows, errors = [], {}, []
    for group in GROUPS:
        side = "left" if group[0] in FRUITS else "right"
        result, error = _group_check(scene, data, group, regions[side], supports, obstacles, previous)
        rows[group[0]] = result
        if error:
            errors.append({"group": list(group), "code": error})
        previous.extend(g for name in group for g in _geoms(scene, name))
    separation = audit_object_separation(scene, data=data)
    errors.extend({"code": "OBJECT_PAIR_SEPARATION", **row} for row in separation["errors"])
    return {"pass": not errors, "errors": errors, "groups": rows, "regions_xy_m": regions,
            "object_separation": separation,
            "support_method": "full-shape AABB coverage by actual slab union + signed geom distance",
            "obstacle_clearance_m": OBSTACLE_CLEARANCE_M,
            "group_clearance_m": GROUP_CLEARANCE_M, "support_gap_tolerance_m": SUPPORT_GAP_M,
            "meal_grasp_clearance_m": MEAL_GRASP_CLEARANCE_M,
            "penetration_tolerance_m": PENETRATION_TOLERANCE_M}


def initialize_research_scene(scene, seed):
    """Sample at reset (time=0), validate in scratch, commit eight free bodies only.

    No settling occurs here. Model, robot/device/tray state, controls, identity
    fields and global RNG are unchanged. Caller must publish episode_scene_hash.
    """
    if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, numbers.Integral) or not 0 <= seed < 2**64:
        raise ResearchInitializationError("SEED_MUST_BE_UINT64_INTEGER")
    seed = int(seed)
    if scene.layout_id == "canonical" or layout_manifest(scene.layout_id).get("profile") != "kitchen_platform_v3":
        raise ResearchInitializationError("EXPLICIT_V3_LAYOUT_REQUIRED")
    if scene.data.time != 0. or not np.isfinite(scene.data.qpos).all() or not np.isfinite(scene.data.qvel).all():
        raise ResearchInitializationError("RESET_STATE_REQUIRED")
    if np.any(scene.data.qvel != 0.):
        raise ResearchInitializationError("ZERO_VELOCITY_RESET_REQUIRED")
    if not set(RANDOMIZED).issubset(scene.objects):
        raise ResearchInitializationError("REQUIRED_OBJECT_MISSING")
    m, live = scene.model, scene.data
    addresses = {n: _free_address(scene, n) for n in RANDOMIZED}
    scratch = mujoco.MjData(m)
    mujoco.mj_copyData(scratch, m, live)
    for adr, dof in addresses.values():
        scratch.qpos[adr:adr+7] = m.qpos0[adr:adr+7]
        scratch.qvel[dof:dof+6] = 0.
    mujoco.mj_forward(m, scratch)
    reference = scratch.qpos.copy()
    supports = _supports(scene, scratch)
    regions = _regions(scene, supports)
    moving = {g for n in RANDOMIZED for g in _geoms(scene, n)}
    obstacles = [g for g in range(m.ngeom) if g not in moving
                 and (m.geom_contype[g] or m.geom_conaffinity[g])]
    rng = np.random.Generator(np.random.PCG64(seed))
    checker = CollisionGeometry(scene)
    previous, attempts, approaches = [], {}, {}
    for group in GROUPS:
        root = group[0]
        side = "left" if root in FRUITS else "right"
        region = regions[side]
        rejected = {}
        root_adr = addresses[root][0]
        root_ref = reference[root_adr:root_adr+3]
        for attempt in range(1, MAX_ATTEMPTS_PER_GROUP+1):
            yaw = float(rng.uniform(-LOCAL_YAW_HALF_RANGE_RAD, LOCAL_YAW_HALF_RANGE_RAD))
            c, s = np.cos(yaw), np.sin(yaw)
            rotation = np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])
            yaw_quat = np.array([np.cos(yaw/2), 0., 0., np.sin(yaw/2)])
            for n in group:
                adr = addresses[n][0]
                scratch.qpos[adr:adr+3] = root_ref+rotation@(reference[adr:adr+3]-root_ref)
                mujoco.mju_mulQuat(scratch.qpos[adr+3:adr+7], yaw_quat, reference[adr+3:adr+7])
            mujoco.mj_kinematics(m, scratch)
            boxes = [object_bounds(scene, n, data=scratch) for n in group]
            lo, hi = np.min([a for a, _ in boxes], axis=0), np.max([b for _, b in boxes], axis=0)
            low = np.maximum(np.asarray(region[0])+(root_ref[:2]-lo[:2]),
                             root_ref[:2]-LOCAL_XY_HALF_RANGE_M)
            high = np.minimum(np.asarray(region[1])-(hi[:2]-root_ref[:2]),
                              root_ref[:2]+LOCAL_XY_HALF_RANGE_M)
            if np.any(high <= low):
                raise ResearchInitializationError("REGION_CANNOT_FIT_FULL_FOOTPRINT", {"seed": seed, "group": root})
            xy = rng.uniform(low, high)
            root_lo = object_bounds(scene, root, data=scratch)[0]
            shift = np.array([*(xy-root_ref[:2]), float(supports[0][2][2]-root_lo[2])])
            for n in group:
                adr = addresses[n][0]
                scratch.qpos[adr:adr+3] += shift
            mujoco.mj_forward(m, scratch)
            _, error = _group_check(scene, scratch, group, region, supports, obstacles, previous)
            approach = None
            if error is None:
                approach = _approach_check(scene, scratch, root, checker)
                if approach is None:
                    error = "FINITE_APPROACH_IK_OR_ENDPOINT_COLLISION"
            if error:
                rejected[error] = rejected.get(error, 0)+1
                continue
            attempts[root] = {"accepted_attempt": attempt, "rejected": rejected}
            approaches[root] = approach
            previous.extend(g for n in group for g in _geoms(scene, n))
            break
        else:
            raise ResearchInitializationError("BOUNDED_SAMPLING_EXHAUSTED",
                                              {"seed": seed, "failed_group": root, "attempts": attempts,
                                               "rejected": rejected, "limit_per_group": MAX_ATTEMPTS_PER_GROUP})
    audit = audit_research_initial_state(scene, data=scratch)
    if not audit["pass"]:
        raise ResearchInitializationError("FINAL_FULL_SCENE_AUDIT_FAILED", {"seed": seed, "audit": audit})
    # Later placements can invalidate an earlier endpoint; recheck the final world.
    for group in GROUPS:
        final_approach = _approach_check(scene, scratch, group[0], checker)
        if final_approach is None:
            raise ResearchInitializationError("FINAL_APPROACH_RECHECK_FAILED",
                                              {"seed": seed, "group": group[0], "attempts": attempts})
        approaches[group[0]] = final_approach
    poses = {n: {"position_world_m": scratch.xpos[b].tolist(),
                 "quaternion_wxyz": scratch.xquat[b].tolist()}
             for n, b in scene.objects.items()}
    state = {"time_s": float(scratch.time), "qpos": scratch.qpos.tolist(),
             "qvel": scratch.qvel.tolist(), "ctrl": scratch.ctrl.tolist(),
             "act": scratch.act.tolist(), "qacc_warmstart": scratch.qacc_warmstart.tolist()}
    spec = mujoco.mjtState.mjSTATE_INTEGRATION
    vector = np.empty(mujoco.mj_stateSize(m, spec))
    mujoco.mj_getState(m, scratch, vector, spec)
    state.update(mujoco_state_spec=int(spec), mujoco_integration_state=vector.tolist())
    initial_hash = digest(state)
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    dependencies = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in ("model.py", "registry.py", "collision.py", "control.py", "limits.py",
                                 "layouts.py", "layout_v3.py", "scene_identity.py")}
    identity = {"schema": SCHEMA, "base_scene_hash": scene.scene_sha256,
                "compiled_model_hash": scene.compiled_model_sha256,
                "layout_id": scene.layout_id, "layout_hash": scene.layout_sha256,
                "initializer_source_sha256": source_hash, "seed": seed,
                "initial_state_hash": initial_hash, "algorithm_version": ALGORITHM_VERSION,
                "dependency_sources_sha256": dependencies,
                "numpy_version": np.__version__, "mujoco_version": mujoco.__version__}
    metadata = {**identity, "status": "INITIALIZED_SOFTWARE_GEOMETRY_PASS",
                "episode_scene_hash": digest(identity), "initial_state": state,
                "initial_qpos": state["qpos"], "initial_qvel": state["qvel"],
                "object_poses": poses, "randomized_objects": list(RANDOMIZED),
                "preserved_objects": sorted(set(scene.objects)-set(RANDOMIZED)),
                "sampling": {"generator": "independent numpy.random.Generator(PCG64)",
                             "max_attempts_per_group": MAX_ATTEMPTS_PER_GROUP,
                             "attempts": attempts,
                             "xy_half_range_m": LOCAL_XY_HALF_RANGE_M,
                             "reference": "base model qpos0 group root; preserve left fruit/right meal layout",
                             "group_reference_xy_m": {group[0]: reference[addresses[group[0]][0]:addresses[group[0]][0]+2].tolist()
                                                       for group in GROUPS},
                             "yaw_range_rad": [-LOCAL_YAW_HALF_RANGE_RAD, LOCAL_YAW_HALF_RANGE_RAD]},
                "geometry": audit, "basic_operability": approaches,
                "dynamic_settling": {"occurred": False, "steps": 0, "duration_s": 0.},
                "evidence_scope": "reset geometry + finite seven-joint static approach endpoints only",
                "continuous_expert_operation": "NOT_VERIFIED", "live_state_committed": True,
                "shared_api_integrated": False, "fixed_demo_evidence_inherited": False}
    json.dumps(metadata, allow_nan=False)
    changed_q, changed_v = [], []
    for adr, dof in addresses.values():
        changed_q.extend(range(adr, adr+7))
        changed_v.extend(range(dof, dof+6))
    backup = mujoco.MjData(m)
    mujoco.mj_copyData(backup, m, live)
    try:
        live.qpos[changed_q] = scratch.qpos[changed_q]
        live.qvel[changed_v] = 0.
        mujoco.mj_forward(m, live)
    except Exception:
        mujoco.mj_copyData(live, m, backup)
        raise
    return metadata
