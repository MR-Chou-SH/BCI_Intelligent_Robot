#!/usr/bin/env python3
"""gripper_scene.py —— 构建「桌子 + fr3+UMI夹爪 + 小型物体 + 放置箱」的 MuJoCo 场景。

- 统一场景源是 assets/fr3_umi_merged.xml(fr3 + UMI 夹爪, 由
  scripts/build_merged_xml_umi.py 生成);
- 物体尺寸按 UMI 夹爪的最大开度选(实测约 8.6cm): 全部 ≤ 7cm 宽;
- 固定布局(不随机), 便于复现与 GUI 观察: obj_0 = 竖着的细高长方体
  (4×4×18cm, "竖着的高的长方体"的夹爪版);
- 桌子靠近机械臂的一角放一个平底开口放置箱(pick-and-place 用)。

夹爪标定(scripts/build_merged_xml_umi.py 生成后实测):
- 夹爪根 body("umi_umi_gripper_base")原点 = 法兰原点(attachment_site), 根 quat 为单位,
  G 系 = 法兰系: **手指沿法兰 +z(末端轴), 开合轴沿法兰 +x**;
- 手指垫中心在法兰系 (0, 0.003, 0.1244), 即沿法兰 +z 向下 12.4cm;
- 手指垫最大内面开度 ≈ 8.6cm。

用法:
    from utils.gripper_scene import build_gripper_scene
    model, data = build_gripper_scene()
"""
import os
import numpy as np
import mujoco

from utils.camera_utils import look_at_quat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRIPPER_XML = os.path.join(ROOT, "assets", "fr3_umi_merged.xml")

TABLE_TOP_Z = 0.75          # 桌面高度(m)
TABLE_HALF_X = 0.60
TABLE_HALF_Y = 0.40
ARM_BASE_XY = [0.0, 0.40]   # fr3 底座在桌子一边的中心(后方 y=+0.4)

# 固定物体布局: (名字, geom类型, size半尺寸, 水平位置(x,y)) —— 全部在夹爪开度内
# box: size=[半x, 半y, 半z]; cylinder: size=[半径, 半高, 0]
OBJECTS = [
    ("obj_0", mujoco.mjtGeom.mjGEOM_BOX, [0.020, 0.020, 0.090], (0.10, -0.12)),   # 竖着的高长方体 4x4x18cm
    ("obj_1", mujoco.mjtGeom.mjGEOM_BOX, [0.025, 0.015, 0.050], (0.16, 0.02)),    # 扁长方体 5x3x10cm
    ("obj_2", mujoco.mjtGeom.mjGEOM_CYLINDER, [0.020, 0.050, 0.0], (-0.05, -0.08)),  # 圆柱 直径4cm 高10cm
    ("obj_3", mujoco.mjtGeom.mjGEOM_BOX, [0.018, 0.018, 0.060], (-0.11, 0.03)),   # 小方柱 3.6x3.6x12cm
]

# 放置箱: 平底开口盒子, 放在桌子靠近机械臂的一角(机械臂底座在 y=+0.4 后边中心)
PLACE_BOX = dict(
    center=(0.35, 0.30),     # 盒中心水平位置(桌面系)
    inner_half=0.10,         # 内腔半宽(x/y, 底面 20x20cm, 加大便于接住落下的物体)
    wall_h=0.06,             # 壁高(低壁, 便于放入/观察)
    wall_t=0.01,             # 壁厚
)

# 夹爪标定常量(在法兰系): 手指垫中心相对法兰原点的偏移
PAD_OFFSET_IN_FLANGE = np.array([0.0, 0.003, 0.1244])
MAX_OPEN = 0.086            # 最大内面开度(m, 实测)


def _add_table(arm):
    wb = arm.worldbody
    wb.add_geom(name="tabletop", type=mujoco.mjtGeom.mjGEOM_BOX,
                size=[TABLE_HALF_X, TABLE_HALF_Y, 0.02],
                pos=[0, 0, TABLE_TOP_Z - 0.02], rgba=[0.55, 0.40, 0.25, 1])
    for sx in (-1, 1):
        for sy in (-1, 1):
            wb.add_geom(name=f"leg{sx:+d}{sy:+d}", type=mujoco.mjtGeom.mjGEOM_BOX,
                        size=[0.03, 0.03, TABLE_TOP_Z / 2],
                        pos=[sx * (TABLE_HALF_X - 0.04), sy * (TABLE_HALF_Y - 0.04), TABLE_TOP_Z / 2],
                        rgba=[0.30, 0.22, 0.14, 1])


def _add_objects(arm):
    wb = arm.worldbody
    for name, kind, size, (x, y) in OBJECTS:
        half_h = size[1] if kind == mujoco.mjtGeom.mjGEOM_CYLINDER else size[2]
        body = wb.add_body(name=name, pos=[x, y, TABLE_TOP_Z + half_h])
        g = body.add_geom(type=kind, size=list(size), rgba=[0.62, 0.78, 0.5, 1])
        g.density = 150.0   # 轻质(约 0.04~0.1kg)
        body.add_freejoint()


def _add_place_box(arm):
    """平底开口盒子(固定, 无关节), 放桌子靠近机械臂的一角。"""
    cx, cy = PLACE_BOX["center"]
    ih, wh, wt = PLACE_BOX["inner_half"], PLACE_BOX["wall_h"], PLACE_BOX["wall_t"]
    outer = ih + wt                      # 外半宽
    box = arm.worldbody.add_body(name="place_box", pos=[cx, cy, TABLE_TOP_Z])
    rgba = [0.35, 0.45, 0.75, 1.0]
    # 底板
    box.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[outer, outer, wt / 2],
                 pos=[0, 0, wt / 2], rgba=rgba)
    # 四面壁
    box.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[wt / 2, outer, wh / 2],
                 pos=[ih + wt / 2, 0, wh / 2], rgba=rgba)
    box.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[wt / 2, outer, wh / 2],
                 pos=[-(ih + wt / 2), 0, wh / 2], rgba=rgba)
    box.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[outer, wt / 2, wh / 2],
                 pos=[0, ih + wt / 2, wh / 2], rgba=rgba)
    box.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[outer, wt / 2, wh / 2],
                 pos=[0, -(ih + wt / 2), wh / 2], rgba=rgba)


def _add_lighting(arm):
    wb = arm.worldbody
    arm.add_texture(name="skybox", type=mujoco.mjtTexture.mjTEXTURE_SKYBOX,
                    builtin=mujoco.mjtBuiltin.mjBUILTIN_GRADIENT,
                    rgb1=[0.35, 0.5, 0.7], rgb2=[0.1, 0.1, 0.1],
                    width=512, height=3072)
    wb.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE,
                size=[0, 0, 0.05], pos=[0, 0, -0.01], rgba=[0.85, 0.85, 0.85, 1])
    wb.add_light(name="light_key", pos=[0, 0, 2.2], dir=[0, 0, -1],
                 intensity=2.0, diffuse=[1, 1, 1], ambient=[0.4, 0.4, 0.4], specular=[0, 0, 0])
    wb.add_light(name="light_fill", pos=[0.8, -0.8, 1.5], dir=[-0.8, 0.8, -1],
                 intensity=1.5, diffuse=[1, 1, 1], specular=[0, 0, 0])


def _add_camera(arm):
    cam = arm.worldbody.add_camera(name="front", pos=[0.9, -0.9, 1.3], fovy=50)
    cam.quat = look_at_quat([0.9, -0.9, 1.3], [0.0, 0.0, 0.7]).tolist()


def build_gripper_scene():
    """返回 (model, data)。加载统一夹爪 XML + 桌/物体/放置箱/灯光/相机。"""
    assert os.path.exists(GRIPPER_XML), f"缺少统一夹爪 XML: {GRIPPER_XML}, 请先跑 python scripts/build_merged_xml_umi.py"
    arm = mujoco.MjSpec.from_file(GRIPPER_XML)
    arm.body("base").pos = [ARM_BASE_XY[0], ARM_BASE_XY[1], TABLE_TOP_Z]
    _add_table(arm)
    _add_objects(arm)
    _add_place_box(arm)
    _add_lighting(arm)
    _add_camera(arm)
    model = arm.compile()
    data = mujoco.MjData(model)
    return model, data


def object_half_extents(model, obj_body):
    """返回物体的 (hx, hy, hz) 半尺寸(m): box 取 s[0..2], cylinder 取 (r, r, h)。"""
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, obj_body)
    gid = model.body_geomadr[bid]
    s = model.geom_size[gid]
    t = model.geom_type[gid]
    if t == mujoco.mjtGeom.mjGEOM_BOX:
        return float(s[0]), float(s[1]), float(s[2])
    if t == mujoco.mjtGeom.mjGEOM_CYLINDER:
        return float(s[0]), float(s[0]), float(s[1])
    if t == mujoco.mjtGeom.mjGEOM_SPHERE:
        return float(s[0]), float(s[0]), float(s[0])
    raise ValueError(f"不支持 geom 类型 {t}")


def obj_geom_ids(model, obj_body):
    """返回某物体 body 名下所有 geom 的 id 集合(接触检测用)。"""
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, obj_body)
    assert bid >= 0, f"找不到 body: {obj_body}"
    adr = model.body_geomadr[bid]
    return set(range(adr, adr + model.body_geomnum[bid]))


if __name__ == "__main__":
    model, data = build_gripper_scene()
    print(f"gripper scene: nq={model.nq} nu={model.nu} nbody={model.nbody}")
    for name, _, _, _ in OBJECTS:
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        print(f"  {name}: {np.round(data.xpos[bid], 3)}  half={object_half_extents(model, name)}")
