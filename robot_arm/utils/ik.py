#!/usr/bin/env python3
"""ik.py —— fr3 的简易数值逆运动学(阻尼最小二乘), 用于把腕部(法兰/attachment_site)放到目标点。

这是任务 1 的剩余项「把抓取平移接到臂 IK」的轻量实现: 只解位置(3 DOF), 姿态自由
(模型本就不预测手部朝向)。7 自由度的 fr3 对 3 DOF 目标冗余, DLS 取最小范数解。

用法:
    from utils.ik import ik_position
    arm_q = ik_position(model, data, target_world, site_name="attachment_site",
                        q_init=np.array([0,-0.5,0,-1.6,0,2.5,0]))
"""
import numpy as np
import mujoco

# fr3 前伸姿态(也作为 IK 的初始猜测)
ARM_REACH = np.array([0.0, -0.5, 0.0, -1.6, 0.0, 2.5, 0.0])

# 多起点 IK 的额外初始猜测: 单一起点(ARM_REACH)会陷入局部极小值
# (实测 side_y-_30 等目标从 ARM_REACH 解不出, 换起点即可收敛)
IK_EXTRA_STARTS = [
    np.array([1.2, -0.5, 0.0, -1.6, 0.0, 2.5, 0.0]),   # 肩关节 j1 偏 +x
    np.array([-1.2, -0.5, 0.0, -1.6, 0.0, 2.5, 0.0]),  # 偏 -x
    np.array([0.0, -0.5, 1.2, -1.6, 0.0, 2.5, 0.0]),   # 偏 +z 转
    np.array([0.0, -0.5, -1.2, -1.6, 0.0, 2.5, 0.0]),  # 偏 -z 转
    np.array([0.0, -0.8, 0.0, -2.2, 0.0, 2.2, 0.0]),   # 更下探
    np.array([0.0, -0.5, 0.0, -1.6, 0.0, 1.5, 0.0]),   # 腕关节 5 不同
]

# mink 是否可用(用户本机已装; 沙箱可能没有)
try:
    import mink as _mink
    _HAS_MINK = True
except ImportError:
    _HAS_MINK = False


def _frame_pose(model, data, frame_name, frame_type):
    """返回 frame 的 (位置, 旋转矩阵)。"""
    if frame_type == "body":
        fid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, frame_name)
        return data.xpos[fid], data.xmat[fid].reshape(3, 3)
    fid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, frame_name)
    return data.site_xpos[fid], data.site_xmat[fid].reshape(3, 3)


def mink_ik(model, data, frame_name, frame_type, target_pos, target_rot=None,
            q_init=ARM_REACH, max_iters=300):
    """mink 逆解(Levenberg-Marquardt + 关节限位)。只解臂(前 7 关节), 其余冻结。

    target_rot 为 None 时只解位置(朝向保持初始猜测的自然姿态)。
    返回 (arm_q(7,), 最终位置(3,))。
    """
    q = data.qpos.copy()
    q[:7] = np.asarray(q_init, dtype=float)
    config = _mink.Configuration(model)
    config.update(q)

    oc = 1.0 if target_rot is not None else 0.0
    rot = np.asarray(target_rot, dtype=float) if target_rot is not None else np.eye(3)
    task = _mink.FrameTask(frame_name, frame_type, position_cost=1.0,
                           orientation_cost=oc, lm_damping=1e-4)
    task.set_target(_mink.SE3.from_rotation_and_translation(
        _mink.SO3.from_matrix(rot), np.asarray(target_pos, dtype=float)))
    freeze = _mink.DofFreezingTask(model, list(range(7, model.nv)))
    limits = [_mink.ConfigurationLimit(model)]

    pos = None
    for _ in range(max_iters):
        vel = _mink.solve_ik(config, [task, freeze], dt=0.05, solver="daqp", limits=limits)
        config.integrate_inplace(vel, 0.05)
        data.qpos[:] = config.q
        mujoco.mj_forward(model, data)
        pos, R = _frame_pose(model, data, frame_name, frame_type)
        if np.linalg.norm(pos - target_pos) < 1e-3 and (
                target_rot is None or np.linalg.norm(R - rot) < 1e-2):
            break
    data.qpos[:7] = config.q[:7]
    mujoco.mj_forward(model, data)
    pos, _ = _frame_pose(model, data, frame_name, frame_type)
    return config.q[:7].copy(), pos


def _jac_site(model, data, site_id):
    jacp = np.zeros((3, model.nv))
    jacr = np.zeros((3, model.nv))
    mujoco.mj_jacSite(model, data, jacp, jacr, site_id)
    return jacp[:, :7]


def _jac_body(model, data, body_id):
    jacp = np.zeros((3, model.nv))
    jacr = np.zeros((3, model.nv))
    mujoco.mj_jacBody(model, data, jacp, jacr, body_id)
    return jacp[:, :7]


def ik_multi(model, data, constraints, q_init=ARM_REACH,
             max_iter=500, tol=1e-3, damp=0.05):
    """多点约束逆运动学(阻尼最小二乘), 解 fr3 的 7 关节角(qpos[0:7])。

    constraints: list of (kind, name, target_world_pos)
        kind='site' 用 mj_jacSite, kind='body' 用 mj_jacBody。
    用多个点约束(如"腕部到位 + 掌心指向物体")即可同时解出位置与姿态。
    返回 (q(7,), 各点最终误差列表)。
    注意: 会原地修改 data.qpos。
    """
    ids = []
    kinds = []
    targets = []
    for kind, name, t in constraints:
        if kind == "site":
            i = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, name)
            assert i >= 0, f"找不到 site: {name}"
        elif kind == "body":
            i = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
            assert i >= 0, f"找不到 body: {name}"
        else:
            raise ValueError(kind)
        ids.append(i)
        kinds.append(kind)
        targets.append(np.asarray(t, dtype=np.float64))

    K = len(ids)
    q = np.array(q_init, dtype=np.float64).copy()
    qlo = model.jnt_range[:7, 0]
    qhi = model.jnt_range[:7, 1]

    def cur_pos(kind, i):
        return data.site_xpos[i] if kind == "site" else data.xpos[i]

    for _ in range(max_iter):
        data.qpos[:7] = q
        mujoco.mj_forward(model, data)
        errs, Js = [], []
        for kind, i, t in zip(kinds, ids, targets):
            errs.append(t - cur_pos(kind, i))
            Js.append(_jac_site(model, data, i) if kind == "site" else _jac_body(model, data, i))
        err = np.concatenate(errs)          # (3K,)
        J = np.concatenate(Js, axis=0)      # (3K,7)
        if np.linalg.norm(err) < tol:
            break
        A = J @ J.T + damp * damp * np.eye(3 * K)
        dq = J.T @ np.linalg.solve(A, err)
        step = 1.0 / (1.0 + np.linalg.norm(dq) * 0.5)
        q += step * dq
        q = np.clip(q, qlo, qhi)

    data.qpos[:7] = q
    mujoco.mj_forward(model, data)
    finals = [cur_pos(kind, i) for kind, i in zip(kinds, ids)]
    return q, finals


def ik_position(model, data, target, site_name="attachment_site", q_init=ARM_REACH,
                max_iter=500, tol=1e-3, damp=0.05):
    """解 fr3 的 7 个关节角(qpos[0:7]), 使 site_name 的位置到达 target(世界系)。

    只解位置, 姿态保持"就近"的冗余解。优先用 mink(稳), 否则回退手写 DLS。
    返回 (q(7,), 最终位置)。
    """
    if _HAS_MINK:
        try:
            return mink_ik(model, data, site_name, "site", target, None, q_init=q_init)
        except Exception:
            pass
    q, finals = ik_multi(model, data, [("site", site_name, target)],
                         q_init=q_init, max_iter=max_iter, tol=tol, damp=damp)
    return q, finals[0]


def ik_pose_6dof(model, data, body_name, target_pos, target_rot, q_init=ARM_REACH,
                 max_iter=800, tol=1e-3, damp=0.05):
    """6 自由度 IK: 使 body 到位(target_pos) + 朝向(target_rot, 3x3 body->world)。

    位置误差 = target_pos - xpos; 朝向误差 = R_err = target_rot @ R_cur^T 转轴角。
    用 mj_jacBody 的 jacp(3x7) + jacr(3x7) 拼 6x7 雅可比做阻尼最小二乘。
    返回 q(7,)。会原地修改 data.qpos。
    """
    if _HAS_MINK:
        try:
            q, _ = mink_ik(model, data, body_name, "body", target_pos, target_rot,
                           q_init=q_init, max_iters=max_iter)
            return q
        except Exception:
            pass
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
    assert bid >= 0, f"找不到 body: {body_name}"
    target_pos = np.asarray(target_pos, dtype=np.float64)
    target_rot = np.asarray(target_rot, dtype=np.float64)

    q = np.array(q_init, dtype=np.float64).copy()
    qlo = model.jnt_range[:7, 0]
    qhi = model.jnt_range[:7, 1]

    for _ in range(max_iter):
        data.qpos[:7] = q
        mujoco.mj_forward(model, data)
        pos_err = target_pos - data.xpos[bid]
        R_cur = data.xmat[bid].reshape(3, 3)
        R_err = target_rot @ R_cur.T
        # 旋转矩阵 -> 轴角
        cosang = np.clip((np.trace(R_err) - 1.0) / 2.0, -1.0, 1.0)
        angle = np.arccos(cosang)
        if angle < 1e-9:
            rot_err = np.zeros(3)
        else:
            axis = np.array([R_err[2, 1] - R_err[1, 2],
                             R_err[0, 2] - R_err[2, 0],
                             R_err[1, 0] - R_err[0, 1]]) / (2.0 * np.sin(angle))
            rot_err = axis * angle
        err = np.concatenate([pos_err, rot_err])
        if np.linalg.norm(err) < tol:
            break
        jacp = np.zeros((3, model.nv))
        jacr = np.zeros((3, model.nv))
        mujoco.mj_jacBody(model, data, jacp, jacr, bid)
        J = np.concatenate([jacp[:, :7], jacr[:, :7]], axis=0)   # 6x7
        A = J @ J.T + damp * damp * np.eye(6)
        dq = J.T @ np.linalg.solve(A, err)
        step = 1.0 / (1.0 + np.linalg.norm(dq) * 0.5)
        q += step * dq
        q = np.clip(q, qlo, qhi)

    data.qpos[:7] = q
    mujoco.mj_forward(model, data)
    return q


def ik_pose_6dof_best(model, data, body_name, target_pos, target_rot,
                      q_init=ARM_REACH, extra_starts=(), max_iter=1500,
                      tol=1e-3, damp=0.05):
    """多起点 6DOF IK: 从多个初始猜测出发, 取最终误差最小的解。

    单起点 DLS/mink 会陷入局部极小值(实测多目标从 ARM_REACH 解不出, 换起点即收敛)。
    起点 = [q_init] + extra_starts + IK_EXTRA_STARTS(去重)。
    返回 (q(7,), pos_err, rot_err)。会原地修改 data.qpos。
    """
    starts = [np.asarray(q_init, dtype=float)]
    for s in list(extra_starts) + list(IK_EXTRA_STARTS):
        s = np.asarray(s, dtype=float)
        if not any(np.allclose(s, st) for st in starts):
            starts.append(s)
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
    best = (np.inf, np.inf, None)
    for q0 in starts:
        try:
            q = ik_pose_6dof(model, data, body_name, target_pos, target_rot,
                             q_init=q0, max_iter=max_iter, tol=tol, damp=damp)
            data.qpos[:7] = q
            mujoco.mj_forward(model, data)
            pe = float(np.linalg.norm(data.xpos[bid] - target_pos))
            re = float(np.linalg.norm(data.xmat[bid].reshape(3, 3) - target_rot))
        except Exception:
            continue
        if pe < best[0]:
            best = (pe, re, q)
        if pe < tol:
            break
    data.qpos[:7] = best[2]
    mujoco.mj_forward(model, data)
    return best[2], best[0], best[1]


def ik_position_body_best(model, data, body_name, target_pos,
                          q_init=ARM_REACH, extra_starts=(), max_iter=1500,
                          tol=1e-3, damp=0.05):
    """多起点**位置-only** IK: 只约束 body 位置, 朝向自由(自然保持在初始猜测附近)。

    用于抬升等"不需要精确朝向"的场景(如抓取后抬臂: 只要物体不掉, 朝向不必锁死)。
    返回 (q(7,), pos_err); 全部起点都失败返回 (None, inf)。
    """
    starts = [np.asarray(q_init, dtype=float)]
    for s in list(extra_starts) + list(IK_EXTRA_STARTS):
        s = np.asarray(s, dtype=float)
        if not any(np.allclose(s, st) for st in starts):
            starts.append(s)
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
    best = (np.inf, None)
    for q0 in starts:
        try:
            if _HAS_MINK:
                q, pos = mink_ik(model, data, body_name, "body", target_pos, None,
                                 q_init=q0, max_iters=max_iter)
                pe = float(np.linalg.norm(pos - target_pos))
            else:
                q, finals = ik_multi(model, data, [("body", body_name, target_pos)],
                                     q_init=q0, max_iter=max_iter, tol=tol, damp=damp)
                pe = float(np.linalg.norm(finals[0] - target_pos))
        except Exception:
            continue
        if pe < best[0]:
            best = (pe, q)
    if best[1] is None:
        return None, np.inf
    data.qpos[:7] = best[1]
    mujoco.mj_forward(model, data)
    return best[1], best[0]
