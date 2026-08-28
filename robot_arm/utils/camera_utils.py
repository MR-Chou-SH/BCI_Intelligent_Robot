#!/usr/bin/env python3
"""camera_utils.py —— 相机/朝向最小工具(从 DexGrasp 的 utils/camera.py 摘出)。

只保留 pick-and-place 需要的部分:
- mat2quat: 旋转矩阵 -> mujoco 四元数(w,x,y,z)
- look_at_quat: 让 mujoco 相机(看向 -z)从 pos 瞄准 target 的四元数
"""
import numpy as np
import mujoco


def normalize(v):
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def mat2quat(R: np.ndarray) -> np.ndarray:
    """3x3 旋转矩阵 -> mujoco 四元数 (w,x,y,z)。直接用 mju_mat2Quat, 避免自实现误差。"""
    R = np.asarray(R, dtype=np.float64)
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, R.ravel())  # 行主序 9 元素
    return quat


def look_at_quat(pos, target, up=(0.0, 0.0, 1.0)) -> np.ndarray:
    """返回让 mujoco 相机(看向 -z)从 pos 瞄准 target 的四元数 (w,x,y,z)。"""
    pos = np.asarray(pos, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    up = np.asarray(up, dtype=np.float64)
    fwd = normalize(target - pos)
    z = -fwd
    x = normalize(np.cross(up, z))
    y = np.cross(z, x)
    R = np.stack([x, y, z], axis=1)  # 列 = 相机系坐标轴(相机系->世界系)
    return mat2quat(R)
