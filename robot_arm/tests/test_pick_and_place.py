#!/usr/bin/env python3
"""tests/test_pick_and_place.py —— fr3 + UMI 夹爪: 竖直下压抓取 + 抬升(+放置)测试。

流程(纯几何规划, 不用 dexgrasp 模型):
    1. 建夹爪桌面场景(utils/gripper_scene.py, 固定布局 4 个物体 + 放置箱);
    2. 对每个物体: 取 x-y 平面最短轴 ŝ → 法兰朝向 R_flange(开合轴 ∥ ŝ, 法兰 z 竖直向下)
       → 夹持点(顶面下 2.5cm, 且不低于桌面+垫尖余量) → 法兰位置 = 夹持点 − R_flange @ 垫偏移;
    3. 臂 IK: 预抓(正上方 15cm) / 抓取 / 抬升轨迹(8 段局部 6DOF IK 热启动, 竖直直线);
    4. 状态机: HOME → APPROACH(夹爪开) → CLOSE(命令 0/1, 底层 rate 平滑 ramp)
       → SETTLE → LIFT → HOLD → [--place: PLACE(L 形: 水平横移不降 + 小段下降)
       → PLACE_HOLD → RELEASE(张开落入箱内)] → DONE;
    5. 判定: place_ok / lift_ok / hold_only / dropped / lost / grasp_fail / ik_fail。

用法(在 loco_mujoco 环境):
    python tests/test_pick_and_place.py                 # 全物体 headless(抓-抬, 不放置)
    python tests/test_pick_and_place.py --place         # 全物体 headless(抓-抬-放)
    python tests/test_pick_and_place.py --obj 0         # 只测 obj_0
    python tests/test_pick_and_place.py --view 2        # GUI 看某个物体的解算抓取姿态
    python tests/test_pick_and_place.py --replay 2 --place  # GUI 实时回放完整流程
输出: outputs/pick_and_place_results.json / .csv, 终端统计表。
"""
import argparse
import csv
import json
import os
import sys

import numpy as np
import mujoco
import mujoco.viewer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils.gripper_scene import build_gripper_scene, OBJECTS
from control.gripper_planner import GripperGraspPlanner


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--obj", type=int, default=None, help="只测某个物体索引(默认全部)")
    ap.add_argument("--place", action="store_true",
                    help="抓取抬升后送到放置箱中心上方并释放(默认: 只抓-抬-保持)")
    ap.add_argument("--view", nargs="?", const=0, type=int,
                    help="GUI 查看某个物体的解算抓取姿态(默认 0); 空格播放")
    ap.add_argument("--replay", nargs="?", const=0, type=int,
                    help="GUI 实时回放某个物体的完整流程")
    ap.add_argument("--out", default=os.path.join(ROOT, "outputs", "pick_and_place_results.json"))
    args = ap.parse_args()

    idx = [args.obj] if args.obj is not None else list(range(len(OBJECTS)))

    if args.view is not None:
        i = args.view
        name = OBJECTS[i][0]
        model, data = build_gripper_scene()
        mujoco.mj_forward(model, data)
        p = GripperGraspPlanner(model, data, name)
        data.qpos[:7] = p.arm_grasp
        data.ctrl[:7] = p.arm_grasp
        data.ctrl[7] = 0.0                      # 夹爪张开
        mujoco.mj_forward(model, data)
        print(f"[view] {name}: 法兰位={np.round(p.flange_pos, 3)} 最短轴 ŝ={np.round(p.s_hat, 3)} "
              f"ik_ok={p.ik_ok} lift_ok={p.lift_ok}", flush=True)
        print(f"[view] 已摆到抓取姿态(夹爪张开)。按空格运行物理, 观察夹爪是否包住物体。", flush=True)
        try:
            mujoco.viewer.launch(model, data)
        except Exception as e:
            print(f"[view] 无法打开 GUI(无显示器?): {e}", flush=True)
        return

    if args.replay is not None:
        i = args.replay
        name = OBJECTS[i][0]
        model, data = build_gripper_scene()
        mujoco.mj_forward(model, data)
        p = GripperGraspPlanner(model, data, name, place=args.place)
        if not p.ik_ok:
            print(f"[{name}] ik_fail", flush=True)
            return
        import time
        from utils.viewer_utils import launch_passive_safe
        what = "抓取 → 抬升 → 放置箱释放" if args.place else "下压 → 平滑闭合 → 竖直抬升"
        print(f"[replay] {name}: {what}。关窗退出。", flush=True)
        try:
            h = launch_passive_safe(model, data)
        except Exception as e:
            print(f"[replay] 无法打开 GUI(无显示器?): {e}", flush=True)
            return
        while h.is_running() and p.step():
            mujoco.mj_step(model, data)
            h.sync()
            time.sleep(0.005)
        while h.is_running():
            h.sync()
            time.sleep(0.02)
        h.close()
        p.report()
        return

    # ---- headless 统计 ----
    mode = "抓取-抬升-放置箱" if args.place else "抓取-抬升(不放置)"
    print(f"{len(idx)} 个物体, 竖直下压抓取 + 抬升 28cm, 模式: {mode}\n", flush=True)
    rows = []
    for i in idx:
        name, kind, size, xy = OBJECTS[i]
        model, data = build_gripper_scene()
        mujoco.mj_forward(model, data)
        p = GripperGraspPlanner(model, data, name, place=args.place)
        p.run_headless(max_steps=9000, quiet=True)
        s = p.summary()
        s.update(obj=name, idx=i)
        rows.append(s)
        extra = f" 在箱内={s['in_box']}" if args.place else ""
        print(f"  [{name}] -> {s['result']:10s} 上移={s['up']*100:5.1f}cm "
              f"水平位移={s['moved']*100:4.1f}cm 接触={s['contacts']}{extra}", flush=True)

    print("\n=== 结果表 ===")
    for r in rows:
        print(f"  {r['obj']:6s} -> {r['result']}")
    ok_kind = "place_ok" if args.place else "lift_ok"
    n_ok = sum(1 for r in rows if r["result"] == ok_kind)
    print(f"\n成功({ok_kind}): {n_ok}/{len(idx)}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=2)
    with open(args.out.replace(".json", ".csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["obj", "idx", "result", "contacts", "up", "moved", "grabbed", "lift_ik_ok", "in_box"])
        for r in rows:
            w.writerow([r["obj"], r["idx"], r["result"], r["contacts"],
                        round(r["up"], 4), round(r["moved"], 4),
                        r["grabbed"], r["lift_ik_ok"], r["in_box"]])
    print(f"\n已保存: {args.out} / {args.out.replace('.json', '.csv')}")


if __name__ == "__main__":
    main()
