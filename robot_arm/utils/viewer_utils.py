#!/usr/bin/env python3
"""viewer_utils.py —— launch_passive 的安全封装，修复退出时的段错误。

根因(已用 faulthandler 定位，3.4.0 / 3.9.0 均复现):
    mujoco 的 `launch_passive` 在 daemon 线程里跑渲染循环(render_loop)。脚本退出时，
    主线程的 atexit 处理器 `glfw.terminate()` 与 daemon 线程的 `simulate.destroy()`
    并发清理 GLFW，产生竞争 → 段错误(glfw/__init__.py:832 terminate 附近)。

修复: 在 launch_passive 返回后，把 `os._exit(0)` 注册为"最后一个"atexit 处理器。
    atexit 按 LIFO 执行，因此它最先运行，直接干净退出、跳过 `glfw.terminate()`。
"""
import atexit
import os
import sys

import mujoco.viewer


def launch_passive_safe(model, data, **kwargs):
    """等价于 mujoco.viewer.launch_passive，但退出时不再段错误。

    用法:
        h = launch_passive_safe(model, data)
        while h.is_running():
            mujoco.mj_step(model, data)   # 你的控制循环
            h.sync()
        h.close()
        # 脚本结束时会干净退出(无段错误)
    """
    h = mujoco.viewer.launch_passive(model, data, **kwargs)

    def _clean_exit():
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    atexit.register(_clean_exit)   # LIFO: 最先执行 → 跳过 glfw.terminate
    return h
