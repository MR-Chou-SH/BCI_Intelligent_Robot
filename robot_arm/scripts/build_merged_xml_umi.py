#!/usr/bin/env python3
"""build_merged_xml_umi.py —— 生成 fr3 + UMI 夹爪的统一 XML。

输出: assets/fr3_umi_merged.xml(单一 XML) + assets/meshes/(fr3 mesh + umi mesh/贴图)。

与 build_merged_xml.py(手)的区别:
- 夹爪替换灵巧手: fr3 全部 7 个旋转关节保留, umi_gripper 挂在 fr3_joint7 的
  法兰 attachment_site 之后;
- **去掉 umi 的 6 个浮动关节**(gripper_joint_x/y/z/rx/ry/rz, 遥操作用的自由浮动
  控制)及对应 6 个执行器 —— 夹爪焊死在法兰上, 只保留手指 2 个滑动关节 + split 腱
  + 1 个 fingers_actuator;
- 夹爪根 body 原点归零(原 pos 0 0 0.11 是"竖立在桌面"的摆放), 根随法兰;
  **根 body quat 置单位**(去掉原版 -90° 绕 x): 使 G 系 = 法兰系,
  手指指向沿 法兰 +z(= fr3 末端圆柱轴), 开合轴 +x 沿 法兰 +x。
  于是「竖直向下抓取」时 法兰 z 轴竖直向下、手指朝下、法兰 x 轴 ∥ 物体最短水平轴;
- 机身(base_link)绕自身中心缩小 ×0.55(原 18.5cm 太长, 法兰到物体顶仅 ~10cm);
- mesh/贴图平铺拷入 assets/meshes/(umi 的 6 个 STL + 2 个 PNG 与手 mesh 不重名)。

IK 末端点约定: 夹爪根 body("umi_umi_gripper_base") 原点 = 法兰原点(attachment_site),
故臂 IK 目标 = (物体上方某点, R_flange); 手指垫在根下方约 PAD_PINCH_Y 处(见
utils/gripper_scene.py 校准注释)。

用法(在 loco_mujoco 环境):
    python scripts/build_merged_xml_umi.py
"""
import os
import re
import shutil
import sys

import mujoco

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ARM = os.path.join(ROOT, "vendor/mujoco_menagerie/franka_fr3/fr3.xml")
GRIPPER = os.path.join(ROOT, "vendor/mujoco_menagerie/umi_gripper/umi_gripper.xml")
FR3_ASSETS = os.path.join(os.path.dirname(ARM), "assets")
UMI_ASSETS = os.path.join(os.path.dirname(GRIPPER), "assets")

OUT_XML = os.path.join(ROOT, "assets", "fr3_umi_merged.xml")
OUT_ASSETS = os.path.join(ROOT, "assets", "meshes")

# 去掉的浮动关节/执行器(遥操作用, 夹爪焊在法兰上后无用)
FLOAT_JOINTS = ["gripper_joint_x", "gripper_joint_y", "gripper_joint_z",
                "gripper_joint_rx", "gripper_joint_ry", "gripper_joint_rz"]
FLOAT_ACTS = [f"{j}_act" for j in FLOAT_JOINTS]


def strip_floating(g):
    """删除 umi 的 6 个浮动关节 + 6 个执行器; 根 body 命名 + 原点归零。"""
    for jn in FLOAT_JOINTS:
        j = g.joint(jn)
        if j is not None:
            g.delete(j)
    for an in FLOAT_ACTS:
        a = g.actuator(an)
        if a is not None:
            g.delete(a)
    root = list(g.worldbody.bodies)[0]
    root.name = "umi_gripper_base"
    root.pos = [0.0, 0.0, 0.0]        # 根随法兰(原 0.11 是桌面竖立摆放偏移)
    # 根 body quat 置单位: 原版 -90°绕x 会把手指方向(G z)转到法兰 y(水平),
    # 使手指与 fr3 末端圆柱轴垂直。置单位后 G 系 = 法兰系:
    #   手指(G z)沿末端轴, 机身长轴也沿末端轴, 开合轴 = G x = 法兰 x。
    root.quat = [1.0, 0.0, 0.0, 0.0]
    # 手指关节去掉弹簧刚度(原 stiffness=100 与位置执行器对抗, 导致夹持力≈0)
    for jn in ("left_finger_joint", "right_finger_joint"):
        j = g.joint(jn)
        if j is not None:
            j.stiffness = [0.0, 0.0, 0.0]


# 机身几何缩小的中心: 机身(base_link 网格)在夹爪系的包围盒中心,
# 实测 AABB x[-0.086,0.086] y[-0.068,0.027] z[-0.076,0.109] -> 中心 (0,-0.0205,0.0165)
CHASSIS_CENTER = (0.0, -0.0205, 0.0165)
# 机身缩小倍率: 竖直后机身 18.5cm 太长(法兰到物体顶只有 ~10cm), 缩到 ~10cm
CHASSIS_SCALE = 0.55


def make_chassis_vertical(g):
    """缩小夹爪机身(根 body 的直接几何), 绕机身中心 ×CHASSIS_SCALE:

    根 body quat 已是单位(见 strip_floating), 机身长轴天然沿夹爪系 z =
    法兰 z(末端轴)——不需要旋转。机身原长 18.5cm, 法兰到物体顶只有 ~10cm,
    不缩会顶到物体; 缩小到 ~10cm 长, 底部正好悬在物体上方。
    只动机身几何, 不动手指子 body(手指位置/朝向不变, 抓取几何不变)。
    """
    import numpy as np
    root = list(g.worldbody.bodies)[0]
    c = np.array(CHASSIS_CENTER)
    # 缩放: mesh 几何缩 mesh 资产(机身/相机), 其余几何缩 pos + size
    for geom in list(root.geoms):
        p = np.asarray(geom.pos, dtype=float)
        geom.pos = (c + CHASSIS_SCALE * (p - c)).tolist()
        if geom.meshname:
            m = g.mesh(geom.meshname)
            if m is not None and not any(abs(v - 1.0) > 1e-9 for v in m.scale):
                m.scale = [CHASSIS_SCALE] * 3
        elif len(geom.size) and np.any(np.asarray(geom.size) != 0.0):
            geom.size = [float(v * CHASSIS_SCALE) for v in geom.size]


def main():
    arm = mujoco.MjSpec.from_file(ARM)
    gripper = mujoco.MjSpec.from_file(GRIPPER)
    strip_floating(gripper)
    make_chassis_vertical(gripper)

    # 挂到法兰: 站点不做额外旋转(夹爪根 quat 已置单位, 手指沿法兰 +z)
    site = arm.site("attachment_site")
    arm.attach(gripper, prefix="umi_", site=site)

    xml = arm.to_xml()
    # mesh/贴图归到 assets/meshes/: meshdir/texturedir 指向 "meshes", 相对 XML 所在目录(assets/)解析
    xml = re.sub(r'meshdir="assets/?"', 'meshdir="meshes"', xml)
    xml = re.sub(r'texturedir="assets/?"', 'texturedir="meshes"', xml)
    # to_xml 不会带出 texturedir(编译器属性), 手动补上, 否则贴图按 XML 所在目录解析找不到
    xml = re.sub(r'<compiler[^>]*/>', '<compiler angle="radian" meshdir="meshes" texturedir="meshes"/>',
                 xml, count=1)
    # 摩擦锥/迭代(umi 原 xml 的 option, 防手指打滑); fr3 的 option 是自闭合 <option .../>
    xml = re.sub(r'<option[^>]*/>',
                 '<option integrator="implicitfast" impratio="10" cone="elliptic" '
                 'noslip_iterations="2"><flag multiccd="enable"/></option>',
                 xml, count=1)

    os.makedirs(OUT_ASSETS, exist_ok=True)
    with open(OUT_XML, "w") as f:
        f.write(xml)

    # 平铺拷贝两套 mesh + umi 贴图到 assets/meshes/(重名跳过)
    copied = []
    for src_dir in (FR3_ASSETS, UMI_ASSETS):
        for fn in sorted(os.listdir(src_dir)):
            src = os.path.join(src_dir, fn)
            if os.path.isfile(src) and not os.path.exists(os.path.join(OUT_ASSETS, fn)):
                shutil.copy(src, os.path.join(OUT_ASSETS, fn))
                copied.append(fn)
    print(f"已生成: {OUT_XML} (+ {len(copied)} 个文件拷入 {OUT_ASSETS}/)", flush=True)

    # 验证: 直接加载
    model = mujoco.MjModel.from_xml_path(OUT_XML)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    jnames = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, j) for j in range(model.njnt)]
    anames = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, a) for a in range(model.nu)]
    bnames = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b) for b in range(model.nbody)]
    print(f"验证: nq={model.nq} njnt={model.njnt} nbody={model.nbody} nu={model.nu}", flush=True)
    print(f"  关节: {[n for n in jnames if n and n.startswith('fr3_')]} + "
          f"{[n for n in jnames if n and n.startswith('umi_')]}", flush=True)
    print(f"  执行器: {anames}", flush=True)
    assert model.nq == 9 and model.njnt == 9, "期望 7(臂) + 2(手指)"
    assert "fr3_joint7" in jnames and "umi_left_finger_joint" in jnames
    root = [n for n in bnames if n and "umi_gripper_base" in n]
    print(f"  夹爪根 body: {root}", flush=True)
    fb = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "umi_umi_gripper_base")
    print(f"  夹爪根 pos(法兰处)= {np_round(data.xpos[fb])}", flush=True)


def np_round(x):
    return "[" + " ".join(f"{v:.3f}" for v in x) + "]"


if __name__ == "__main__":
    main()
