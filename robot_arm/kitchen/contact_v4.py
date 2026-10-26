"""Declared rigid support/handle proxies for the resumed V3 candidate only."""
from __future__ import annotations

import math
import mujoco


def _hull(spec, body_name, geom_name, rings, mass, color):
    vertices = []
    for height, rx, ry in rings:
        for index in range(32):
            angle = 2*math.pi*index/32
            vertices.extend((rx*math.cos(angle), ry*math.sin(angle), height))
    mesh_name = body_name+"_flat_support_hull_v4"
    spec.add_mesh(name=mesh_name, uservert=vertices)
    spec.delete(spec.geom(geom_name))
    spec.body(body_name).add_geom(name=geom_name, type=mujoco.mjtGeom.mjGEOM_MESH,
        meshname=mesh_name, mass=mass, rgba=color, friction=[.8,.01,.001], solref=[.02,1])


def _flat_foot(spec, body_name, radius, sphere_radius, color):
    """A small cylindrical foot whose bottom face is flush with the sphere.

    Turns the sphere's point contact into a small flat support polygon so a
    round fruit cannot roll freely on the tray/basket, without changing its
    resting height (foot bottom == sphere bottom).
    """
    half = .004
    spec.body(body_name).add_geom(name=body_name + "_foot",
        type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[radius, half],
        pos=[0., 0., -(sphere_radius - half)], mass=.01, rgba=color,
        friction=[.8,.01,.001], solref=[.02,1])


def apply_contact_v4(spec, layout):
    from .mechanics import add_jaw_coupling
    add_jaw_coupling(spec, layout)
    if layout.get("contact_model") != "rigid_support_and_standoff_v4":
        return
    if layout.get('apple_primitive') == 'equal_shape_sphere_v1':
        apple=spec.geom('apple_collision')
        if max(abs(float(r)-.035) for r in apple.size)>1e-12:
            raise ValueError('Equal-shape sphere requires the original35mm spherical envelope')
        apple.type=mujoco.mjtGeom.mjGEOM_SPHERE
        apple.size=[.035,0.,0.]
        _flat_foot(spec, 'apple', .020, .035, [.78,.04,.02,1])
    if layout.get('tomato_primitive') == 'equal_shape_sphere_v1':
        # User direction 2026-10-24: the 32x32x30 tomato becomes an equal-radius
        # 32mm sphere, the same contact-model precedent as the apple. This keeps
        # the 30 degree rotation gate and ADR-004 untouched; a sphere whose
        # rotation does not change its collision shape now satisfies it exactly.
        # Raise the body by 2mm so the sphere keeps the ellipsoid's resting
        # bottom on the 0.800m worktop instead of starting 2mm penetrated.
        tomato=spec.geom('tomato_collision')
        if (max(abs(float(r)-.032) for r in tomato.size[:2])>1e-12
                or abs(float(tomato.size[2])-.030)>1e-12):
            raise ValueError('Equal-shape tomato sphere requires the original 32x32x30 envelope')
        tomato.type=mujoco.mjtGeom.mjGEOM_SPHERE
        tomato.size=[.032,0.,0.]
        _flat_foot(spec, 'tomato', .018, .032, [.9,.05,.02,1])
        body=spec.body('tomato')
        body.pos=[float(body.pos[0]),float(body.pos[1]),float(body.pos[2])+.002]
    if layout.get('button_mount') == 'recessed_sidewall_pocket_v1':
        # A6mm front-edge pocket provides travel clearance for the5mm spring key.
        side='front' if spec.body('microwave_button').pos[1]<0 else 'back'
        wall=spec.geom('microwave_side_'+side)
        y=float(wall.pos[1]);color=list(wall.rgba)
        wall.pos=[.003,y,.17];wall.size=[.197,.012,.17]
        for label,z,half_z in (('lower',.071,.071),('upper',.259,.081)):
            spec.body('microwave').add_geom(name='microwave_button_pocket_'+label,
                type=mujoco.mjtGeom.mjGEOM_BOX,pos=[-.197,y,z],size=[.003,.012,half_z],
                rgba=color,friction=[.8,.01,.001],solref=[.02,1])
    # Finite flat bottoms represent a pear calyx and the supporting hotdog bun,
    # rather than an upright prolate ellipsoid and a nearly circular rolling sausage.
    _hull(spec, "pear", "pear_collision", [(-.045,.012,.012),(-.035,.025,.025),
        (-.018,.032,.032),(.005,.027,.027),(.028,.015,.015),(.045,.003,.003)],
        .18, [.75,.75,.13,1])
    _hull(spec, "hotdog", "hotdog_food", [(-.022,.047,.014),(-.015,.057,.021),
        (0.,.060,.023),(.014,.054,.021),(.022,.040,.011)], .15, [.70,.32,.12,1])
    # User direction: give the round-bodied hotdog a rectangular foot flush with
    # its lowest point (like the fruit feet). The bun's local y is world +z and
    # its bottom sits at body z=-0.0234; the 8mm skid turns the rolling contact
    # into a flat support so the food stops spinning on its plate during carry.
    spec.body("hotdog").add_geom(name="hotdog_foot", type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[.060,.020,.004], pos=[0.,0.,-.018], mass=.01, rgba=[.70,.32,.12,1],
        friction=[.8,.01,.001], solref=[.02,1])
    if layout.get('burger_support') == 'flat_bun_v1':
        _hull(spec, 'burger', 'burger_food', [(-.028,.032,.030),(-.020,.044,.041),
            (0.,.047,.044),(.018,.041,.038),(.028,.025,.024)], .20, [.68,.35,.11,1])
    # A real stand-off handle leaves 43 mm behind the bar, not the old 2 mm slit.
    handle = spec.geom("microwave_door_handle")
    handle.pos = [-.060, -.25 if layout["door_variant"] == "mirrored_left_hinge" else .25, .145]
    handle.size = [.009,.010,.060]
    door = spec.body("microwave_door")
    for index, z in enumerate((.085,.205)):
        door.add_geom(name="microwave_handle_post_"+str(index), type=mujoco.mjtGeom.mjGEOM_CAPSULE,
            fromto=[-.010,handle.pos[1],z,-.060,handle.pos[1],z], size=[.006],
            mass=.012, rgba=[.55,.56,.57,1], friction=[.8,.01,.001], solref=[.02,1])
    if layout.get("meal_carrier") in ("raised_burger_carry_bar_v1", "raised_burger_carry_bar_v2", "raised_burger_carry_bar_v3"):
        carrier = spec.body("burger_container")
        for side, x in (("left", -.073), ("right", .073)):
            bar = spec.geom("burger_grasp_"+side)
            bar.pos = [x, 0, .055]
            bar.size = [.018, .025, .010]
            bar.mass = .005
            if layout["meal_carrier"].endswith("v3"):
                bar.size = [.025,.025,.010]
                tilt = math.radians(float(layout.get("burger_handle_tilt_deg", 30.)))
                bar.quat = [math.cos(tilt/2),0.,math.sin(tilt/2),0.]
            for index, y in enumerate((-.024, .024)):
                foot_x = x if layout["meal_carrier"].endswith("v1") else math.copysign(.063, x)
                carrier.add_geom(name=f"burger_handle_post_{side}_{index}",
                    type=mujoco.mjtGeom.mjGEOM_CAPSULE,
                    fromto=[foot_x,y,.006,x,y,.055], size=[.003], mass=.0025,
                    rgba=[.85,.84,.80,1], friction=[.8,.01,.001], solref=[.02,1])
    if layout.get("hotdog_carrier") == "raised_hotdog_carry_bar_v1":
        # User direction 2026-10-24: mirror the burger's raised carry bar on the
        # hotdog carrier. Grasping the low side handle left the pads 1.9902mm
        # from hotdog_food (2mm margin); a 45mm-raised, outboard bar lets the
        # pads close above and outside the food instead.
        carrier = spec.body("hotdog_container")
        for side, sign in (("left", -1.), ("right", 1.)):
            x = sign * .082
            bar = spec.geom("hotdog_grasp_" + side)
            bar.pos = [x, 0, .060]
            bar.size = [.018, .025, .010]
            bar.mass = .005
            # Adjustable carrier geometry, independent of robot/world poses.
            # Candidate 013 uses a tilted thin pinch face like the burger bar;
            # the planner reads the compiled frame rather than this angle.
            tilt = math.radians(float(layout.get("hotdog_handle_tilt_deg", 0.)))
            bar.quat = [math.cos(tilt/2), 0., math.sin(tilt/2), 0.]
            for index, y in enumerate((-.024, .024)):
                carrier.add_geom(name=f"hotdog_handle_post_{side}_{index}",
                    type=mujoco.mjtGeom.mjGEOM_CAPSULE,
                    fromto=[sign * .062, y, .006, x, y, .060], size=[.003], mass=.0025,
                    rgba=[.85,.84,.80,1], friction=[.8,.01,.001], solref=[.02,1])
    # Scoped meal friction: fruit, tray, door and robot surfaces are unchanged.
    for meal in ("burger", "hotdog"):
        for side in ("left", "right"):
            if "meal_handle_friction" in layout:
                spec.geom(meal+"_grasp_"+side).friction = [float(layout["meal_handle_friction"]), .02, .002]
        for geom in spec.geoms:
            if "meal_support_friction" in layout and geom.name in (meal+"_plate", meal+"_food", meal+"_foot"):
                geom.friction = [float(layout["meal_support_friction"]), .02, .002]
