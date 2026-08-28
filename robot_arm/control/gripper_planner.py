#!/usr/bin/env python3
"""control/gripper_planner.py —— UMI 夹爪抓取-抬升-放置规划控制器(fr3 + umi_gripper)。

夹爪是**纯几何规划**(不用 dexgrasp 模型):
    1) 取物体在 x-y 平面的最短轴 ŝ(水平), 夹爪开合轴与它平行;
    2) 夹爪(开)在物体正上方, 竖直下压到「最高点下一小段距离」;
    3) 闭合(夹爪命令 0/1, 底层按最大速率平滑 ramp, 不猛烈开闭);
    4) 抓住后竖直抬升;
    5) place=True 时: 抬升后水平横移到放置箱中心上方(不降), 再小段下降, 张开释放。

阶段状态机:
    HOME      臂从 ARM_REACH 关节空间 ramp 到预抓位(抓取位正上方 pre_h)
    APPROACH  臂 ramp 到抓取位(法兰在物体上方, 手指垫对准夹持点)
    CLOSE     夹爪目标=1(闭合), 底层 rate 限制平滑 ramp; 等接触/超时
    SETTLE    保持, 让握力稳定
    LIFT      臂沿竖直抬升轨迹(lift_h), 夹爪保持闭合
    HOLD      保持验证(place=False 时结束后 DONE)
    PLACE     L 形轨迹: 抬升高度上水平横移到盒上方(不降) -> 小段下降
    PLACE_HOLD 保持到位, 等机械臂收敛(夹爪仍闭合)
    RELEASE   夹爪目标=0(平滑张开), 物体落入箱内

坐标系/标定(见 utils/gripper_scene.py):
- 夹爪根 body("umi_umi_gripper_base") 原点 = 法兰原点, 根 quat 单位 → G 系 = 法兰系;
- **手指指向沿法兰 +z = fr3 末端圆柱轴**; 开合轴 = 法兰 +x;
- 手指垫中心在法兰系 PAD_OFFSET_IN_FLANGE=(0, 0.003, 0.1244)(沿法兰 +z 向下);
- 法兰朝向 R_flange(列=法兰轴): x = ŝ(开合轴), z = ẑ(竖直向下, 手指朝下), y = z×x;
- 法兰位置 = 夹持点 − R_flange @ PAD_OFFSET_IN_FLANGE。

用法:
    from utils.gripper_scene import build_gripper_scene
    from control.gripper_planner import GripperGraspPlanner
    model, data = build_gripper_scene()
    p = GripperGraspPlanner(model, data, "obj_0", place=True)   # 抓-抬-放
    p.run_headless()        # 终端打印各阶段 + 结果
    p.run_gui()             # 实时回放
"""
import os
import sys

import numpy as np
import mujoco

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils.ik import ik_pose_6dof, ik_pose_6dof_best, ARM_REACH
from utils.gripper_scene import (PAD_OFFSET_IN_FLANGE, TABLE_TOP_Z, PLACE_BOX,
                                 obj_geom_ids)

# 垫子尖端在夹持平面下方的长度(实测: 夹爪垫是长条, 尖端伸出夹持面 7.66cm)。
# 夹持点太低时垫尖会刮桌面, 把闭合卡在 q≈0.01 而夹不到物体 —— 夹持点必须高于
# 桌面 + 垫尖长度 + 余量。
PAD_BELOW_PINCH = 0.0766
PINCH_MIN_Z = TABLE_TOP_Z + PAD_BELOW_PINCH + 0.01

PRE_H = 0.15                # 预抓高度(抓取位正上方, m)
LIFT_H = 0.28               # 抬升高度(m, 略低: 运输中底部只需高过桌面上最高的物体)
RAMP_STEPS = 200            # 每段关节空间 ramp 步数
LIFT_SEG_STEPS = 90         # 抬升轨迹每段步数
PLACE_H_N = 12              # 放置水平段(抬升高度上横移)分段数
PLACE_D_N = 4               # 放置下降段(盒子上方小段下降)分段数
PLACE_SEG_STEPS = 90        # 放置轨迹每段步数
PLACE_HOLD_STEPS = 150      # 到位后先保持闭合, 等机械臂稳定再释放
DROP_CLEAR = 0.25           # 释放时垫子高出箱口的高度(m, 高一点让物体落进加大的箱)
RELEASE_STEPS = 300         # 张开释放的等待步数
IK_POS_TOL = 0.04
IK_ROT_TOL = 0.10

# ---- 夹爪 0/1 命令的底层平滑控制器 -------------------------------- #
CTRL_OPEN = 0.0             # 夹爪命令 0 = 张开
CTRL_CLOSE = 0.05           # 夹爪命令 1 = 闭合(腱长 0.05 = 手指合拢到限位)
CTRL_RATE = 0.05 / 300.0    # 每步最大 ctrl 变化量(全行程约 300 步 ≈ 0.6s, 不猛烈)

GRIPPER_BODY = "umi_umi_gripper_base"


class GripperServo:
    """夹爪底层: 输入 0/1 目标, 输出带 rate 限制的平滑 ctrl 差值。"""

    def __init__(self, rate=CTRL_RATE):
        self.rate = rate
        self.ctrl = CTRL_OPEN

    def target_ctrl(self, cmd):
        return CTRL_CLOSE if cmd else CTRL_OPEN

    def step(self, cmd):
        """cmd: 0(张开) 或 1(闭合)。返回当前 ctrl 值。"""
        t = self.target_ctrl(cmd)
        self.ctrl += np.clip(t - self.ctrl, -self.rate, self.rate)
        return self.ctrl


class GripperGraspPlanner:
    """竖直向下夹爪抓取规划: 最短轴对齐 → 下压 → 平滑闭合 → 抬升 → (放置)。"""

    def __init__(self, model, data, obj, pre_h=PRE_H, lift_h=LIFT_H,
                 pinch_from_top=0.025, close_steps=340, settle_steps=100,
                 place=False, drop_clear=DROP_CLEAR):
        self.model = model
        self.data = data
        self.obj = obj
        self.pre_h = pre_h
        self.lift_h = lift_h
        self.pinch_from_top = pinch_from_top
        self.close_steps = close_steps
        self.settle_steps = settle_steps
        self.place = place
        self.drop_clear = drop_clear
        self.quiet = False

        self.obj_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, obj)
        self.og = obj_geom_ids(model, obj)
        self.gripper_bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, GRIPPER_BODY)
        assert self.obj_bid >= 0 and self.gripper_bid >= 0, "找不到物体/夹爪根 body"

        # 物体几何: 中心 + 半尺寸 + 最短水平轴 ŝ(在物体 geom 系, 场景初始与水平面平行)
        from utils.gripper_scene import object_half_extents
        hx, hy, hz = object_half_extents(model, obj)
        self.hz = hz
        xmat = data.xmat[self.obj_bid].reshape(3, 3) if hasattr(data, "xmat") else np.eye(3)
        # 物体 geom 系的 x/y 轴在世界的水平投影, 取较短的那条为开合方向
        ax = xmat[:, 0]; ax[2] = 0.0
        ay = xmat[:, 1]; ay[2] = 0.0
        na, nb = np.linalg.norm(ax), np.linalg.norm(ay)
        if hx <= hy:
            s = ax / na if na > 1e-9 else np.array([1.0, 0.0, 0.0])
        else:
            s = ay / nb if nb > 1e-9 else np.array([0.0, 1.0, 0.0])
        self.s_hat = s
        self.center = data.xpos[self.obj_bid].copy()

        # 法兰朝向: 列 = [x_S, y_S, z_S]。手指垫沿法兰 +z 伸出, 要竖直向下抓取则
        # 法兰 +z 必须指向世界 −ẑ(法兰 z 轴朝下, 垫在法兰下方); 开合轴 x_S = ŝ。
        # 右手系: x×y = z → y_S = (sy, −sx, 0), z_S = −ẑ。
        s = self.s_hat
        self.R_flange = np.column_stack([s, np.array([s[1], -s[0], 0.0]),
                                         np.array([0.0, 0.0, -1.0])])
        assert abs(np.linalg.det(self.R_flange) - 1.0) < 1e-6, "法兰朝向必须右手系"

        # 夹持点: 物体顶面下方 pinch_from_top(手指垫夹住上段);
        # 圆柱/球等圆截面物体改为夹质心高度(圆面夹上段容易滚出);
        # 再强制夹持点不低于 PINCH_MIN_Z(垫尖不能刮桌面)
        gid = model.body_geomadr[self.obj_bid]
        is_round = model.geom_type[gid] in (mujoco.mjtGeom.mjGEOM_CYLINDER,
                                            mujoco.mjtGeom.mjGEOM_SPHERE)
        pinch_z = self.center[2] + (0.0 if is_round else hz - pinch_from_top)
        pinch_z = max(pinch_z, PINCH_MIN_Z)
        pinch = np.array([self.center[0], self.center[1], pinch_z])
        self.pinch = pinch
        flange_pos = pinch - self.R_flange @ PAD_OFFSET_IN_FLANGE
        self.flange_pos = flange_pos
        self.flange_pre = flange_pos + np.array([0.0, 0.0, pre_h])

        # ---- 臂 IK(多起点; 预抓/抓取 6DOF) ----
        self.arm_pre, pe1, re1 = ik_pose_6dof_best(model, data, GRIPPER_BODY,
                                                    self.flange_pre, self.R_flange,
                                                    q_init=ARM_REACH)
        self.arm_grasp, pe2, re2 = ik_pose_6dof_best(model, data, GRIPPER_BODY,
                                                      self.flange_pos, self.R_flange,
                                                      q_init=self.arm_pre)
        self.ik_ok = (pe1 <= IK_POS_TOL and pe2 <= IK_POS_TOL
                      and re1 <= IK_ROT_TOL and re2 <= IK_ROT_TOL)

        # ---- 抬升轨迹: 竖直向上, 分 N 段局部 6DOF IK, 每段从前一段热启动 ----
        # (单次 IK 的 arm_lift 是"折叠"位形, 关节空间直线 ramp 会甩一个大弧;
        #  分段热启动保持近似竖直直线; 若某段局部解离前一段太远(求解器逃逸到别的
        #  分支), 用更高阻尼重解, 仍不行就跳过该段)
        self.lift_traj = self._build_traj(self.arm_grasp, self.flange_pos,
                                          self.flange_pos + np.array([0.0, 0.0, lift_h]),
                                          N=8)
        self.lift_ok = True
        data.qpos[:7] = self.lift_traj[-1]
        mujoco.mj_forward(model, data)
        self.lift_end_pos = data.xpos[self.gripper_bid].copy()

        # ---- 放置轨迹(place=True): L 形 —— 抬升高度上水平横移到盒子上方(不降),
        #      再在盒子上方小段下降(PLACE_D_N 段)到释放位 ----
        self.place_traj = None
        self.place_ok_target = False
        if place:
            pb = PLACE_BOX
            cx, cy = pb["center"]
            box_top = TABLE_TOP_Z + pb["wall_h"]
            place_pos = np.array([cx, cy, box_top + PAD_OFFSET_IN_FLANGE[2] + drop_clear])
            self.place_pos = place_pos
            # 水平段: 抬升高度不变, 横移到盒中心正上方
            corner = np.array([cx, cy, self.lift_end_pos[2]])
            traj_h = self._build_traj(self.lift_traj[-1], self.lift_end_pos, corner,
                                      N=PLACE_H_N)
            # 下降段: 盒上方竖直下降一小段到释放位
            traj_d = self._build_traj(traj_h[-1], corner, place_pos, N=PLACE_D_N)
            self.place_traj = traj_h[:-1] + traj_d
            self.place_box_center = np.array([cx, cy])
            self.place_box_inner = pb["inner_half"]
            self.place_box_bottom = TABLE_TOP_Z

        # ---- 执行器/接触增强 ----
        for a in range(7):                        # 臂: 只放大 forcerange(kp 不能动)
            model.actuator_forcerange[a] *= 3.0
        for g in range(model.ngeom):              # 摩擦: 夹爪/物体/桌面 mu=2.0
            bn = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, model.geom_bodyid[g]) or ""
            if bn.startswith("umi_") or bn == obj or bn == "tabletop":
                model.geom_friction[g][0] = 2.0
        # 夹爪执行器: kp 1000 原值(2000 会让闭合太暴力, 把圆物块挤飞), forcerange ±200
        model.actuator_forcerange[7] = [-200.0, 200.0]

        # ---- 初始状态: 臂在 ARM_REACH, 夹爪张开 ----
        data.qpos[:7] = ARM_REACH
        data.qpos[7:9] = 0.0
        mujoco.mj_forward(model, data)
        self.obj_start = data.xpos[self.obj_bid].copy()
        self.reset()

    # ------------------------------------------------------------------ #
    def reset(self):
        self.phase = "HOME"
        self.n = 0
        self.grabbed = False
        self.result = "运行中"
        self.servo = GripperServo()

    def _next(self, ph):
        if not self.quiet:
            print(f"  [{self.phase}] -> [{ph}]", flush=True)
        self.phase = ph
        self.n = 0

    def _contacts(self):
        """夹爪与物体的接触数(非物体侧的几何属于 umi_* body, 排除桌面接触)。"""
        m = self.model
        n = 0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            if c.geom1 in self.og or c.geom2 in self.og:
                hg = c.geom2 if c.geom1 in self.og else c.geom1
                bn = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, m.geom_bodyid[hg]) or ""
                if bn.startswith("umi_"):
                    n += 1
        return n

    def _build_traj(self, start_q, start_pos, end_pos, N=8):
        """从 start_pos 到 end_pos 的局部 6DOF IK 轨迹(N 段, 逐段热启动)。

        返回关节角轨迹 [q0, q1, ..., qN]; 某段局部解失败/逃逸太远则沿用前一段。
        保持朝向 R_flange 不变(手指始终竖直向下)。
        """
        m, d = self.model, self.data
        traj = [np.asarray(start_q, dtype=float)]
        for k in range(1, N + 1):
            target = np.asarray(start_pos, dtype=float) + \
                (np.asarray(end_pos, dtype=float) - np.asarray(start_pos, dtype=float)) * (k / N)
            q_prev = traj[-1]
            q = ik_pose_6dof(m, d, GRIPPER_BODY, target, self.R_flange,
                             q_init=q_prev, max_iter=1500, tol=1e-3)
            far = float(np.max(np.abs(q - q_prev)))
            if far > 0.35:                          # 逃逸到远分支 → 高阻尼重解
                q = ik_pose_6dof(m, d, GRIPPER_BODY, target, self.R_flange,
                                 q_init=q_prev, max_iter=2000, tol=1e-3, damp=0.3)
                far = float(np.max(np.abs(q - q_prev)))
            d.qpos[:7] = q
            mujoco.mj_forward(m, d)
            pe = float(np.linalg.norm(d.xpos[self.gripper_bid] - target))
            if pe > IK_POS_TOL or far > 0.35:       # 仍不行 → 跳过该段(沿用前一段)
                q = q_prev
            traj.append(q)
        d.qpos[:7] = traj[0]
        mujoco.mj_forward(m, d)
        return traj

    def _ramp_through(self, traj, seg_steps):
        """沿轨迹 traj 逐段关节空间 ramp; 返回 (是否走完, 当前 ctrl 值)。"""
        nseg = len(traj) - 1
        seg = min(self.n // seg_steps, nseg - 1)
        t = min(1.0, (self.n % seg_steps) / seg_steps)
        ctrl = traj[seg] + t * (traj[seg + 1] - traj[seg])
        done = self.n >= nseg * seg_steps
        return ctrl, done

    def step(self):
        """推进一帧控制(状态机), 返回 False 表示已到 DONE。"""
        m, d = self.model, self.data
        ph = self.phase
        # 夹爪 0/1 命令: 打开(0)于 HOME/APPROACH/RELEASE; 闭合(1)其余
        cmd = 0 if ph in ("HOME", "APPROACH", "RELEASE") else 1
        d.ctrl[7] = self.servo.step(cmd)

        if ph == "HOME":
            t = min(1.0, self.n / RAMP_STEPS)
            d.ctrl[:7] = ARM_REACH + t * (self.arm_pre - ARM_REACH)
            if t >= 1.0:
                self._next("APPROACH")
        elif ph == "APPROACH":
            t = min(1.0, self.n / RAMP_STEPS)
            d.ctrl[:7] = self.arm_pre + t * (self.arm_grasp - self.arm_pre)
            if t >= 1.0:
                if not self.ik_ok:
                    self.result = "ik_fail"
                    self._next("DONE")
                else:
                    self._next("CLOSE")
        elif ph == "CLOSE":
            d.ctrl[:7] = self.arm_grasp
            if self.n > self.close_steps:            # 平滑闭合完成后
                if self._contacts() >= 1:
                    self.grabbed = True
                    self._next("SETTLE")
                else:
                    self.result = "grasp_fail"
                    self._next("DONE")
        elif ph == "SETTLE":
            d.ctrl[:7] = self.arm_grasp
            if self.n > self.settle_steps:           # 让握力稳定
                if self._contacts() >= 1:
                    self._next("LIFT")
                else:
                    self.result = "grasp_fail"
                    self._next("DONE")
        elif ph == "LIFT":
            if not self.lift_ok:
                self.result = "lift_ik_fail"
                self._next("DONE")
                return True
            ctrl, done = self._ramp_through(self.lift_traj, LIFT_SEG_STEPS)
            d.ctrl[:7] = ctrl
            if done:
                self._next("HOLD")
        elif ph == "HOLD":
            d.ctrl[:7] = self.lift_traj[-1]
            hold_steps = 60 if self.place else 150
            if self.n > hold_steps:
                self._next("PLACE" if self.place else "DONE")
        elif ph == "PLACE":
            if self.place_traj is None:
                self.result = "place_ik_fail"
                self._next("DONE")
                return True
            ctrl, done = self._ramp_through(self.place_traj, PLACE_SEG_STEPS)
            d.ctrl[:7] = ctrl
            if done:
                self._next("PLACE_HOLD")
        elif ph == "PLACE_HOLD":
            d.ctrl[:7] = self.place_traj[-1]      # 保持到位, 夹爪仍闭合,
            if self.n > PLACE_HOLD_STEPS:          # 等机械臂收敛后再释放
                self._next("RELEASE")
        elif ph == "RELEASE":
            d.ctrl[:7] = self.place_traj[-1]
            if self.n > RELEASE_STEPS:               # 平滑张开, 物体落入箱内
                self._next("DONE")
        else:  # DONE
            return False

        self.n += 1
        return True

    # ------------------------------------------------------------------ #
    def _in_place_box(self):
        """物体是否在放置箱内(水平在口内, 且没掉到桌面)。"""
        if self.place_traj is None:
            return False
        p = self.data.xpos[self.obj_bid]
        c = self.place_box_center
        ih = self.place_box_inner
        return (abs(p[0] - c[0]) <= ih and abs(p[1] - c[1]) <= ih
                and p[2] > self.place_box_bottom - 0.02)

    def summary(self):
        """结构化结果(枚举值)。"""
        m, d = self.model, self.data
        dp = d.xpos[self.obj_bid] - self.obj_start
        ncon = self._contacts()
        if self.result == "运行中":          # 被 max_steps 截断时兜底分类
            if self.place:
                if self._in_place_box():
                    self.result = "place_ok"
                elif self.grabbed:
                    self.result = "dropped"      # 中途掉了或没送进箱
                elif ncon >= 1:
                    self.result = "hold_only"
                else:
                    self.result = "lost"
            else:
                if self.grabbed and ncon >= 1 and dp[2] > 0.03:
                    self.result = "lift_ok"
                elif self.grabbed and ncon >= 1:
                    self.result = "hold_only"
                else:
                    self.result = "lost"
        return dict(result=self.result, up=float(dp[2]), moved=float(np.linalg.norm(dp[:2])),
                    contacts=ncon, grabbed=bool(self.grabbed), lift_ik_ok=bool(self.lift_ok),
                    in_box=bool(self._in_place_box()))

    def report(self):
        m, d = self.model, self.data
        dp = d.xpos[self.obj_bid] - self.obj_start
        self.summary()
        print(f"\n结果: {self.result}")
        print(f"  物块位移: 上(Δz)={dp[2]*100:.1f}cm 水平(Δxy)={np.linalg.norm(dp[:2])*100:.1f}cm "
              f"接触对数={self._contacts()}")
        if self.place:
            print(f"  在箱内={self._in_place_box()} "
                  f"物块位={np.round(d.xpos[self.obj_bid], 3)} "
                  f"箱中心={np.round(self.place_box_center, 3)}")
        print(f"  抓稳={self.grabbed}  抬升位IK={'OK' if self.lift_ok else 'FAIL'}")

    def run_headless(self, max_steps=9000, quiet=False):
        self.quiet = quiet
        if not self.ik_ok:
            print("抓取位 IK 不收敛, 中止。", flush=True)
            self.result = "ik_fail"
            if not quiet:
                self.report()
            return self
        steps = 0
        while self.step() and steps < max_steps:
            mujoco.mj_step(self.model, self.data)
            steps += 1
        if not quiet:
            self.report()
        return self

    def run_gui(self, speed=0.006):
        import time
        from utils.viewer_utils import launch_passive_safe
        h = launch_passive_safe(self.model, self.data)
        try:
            while h.is_running() and self.step():
                mujoco.mj_step(self.model, self.data)
                h.sync()
                time.sleep(speed)
            while h.is_running():
                h.sync()
                time.sleep(0.02)
        finally:
            h.close()
        self.report()
        return self
