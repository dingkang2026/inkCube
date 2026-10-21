"""任务子进程低优先级（2.11 §5.6 R3，E3 T11）。

任务执行链 spawn 的 venv shell/python/uv 子进程统一以低优先级启动，
OS 前台调度天然优先——定时采集或装包运行中，前台操作无可感知卡顿；
全局生效（人工任务同样受益）。执行链覆盖：shell 工具（Popen）、
code_executor、package_manager、venv 创建/预装（uv/pip 子进程）、
外部 Skill 脚本（asyncio create_subprocess_exec）。

- Windows：BELOW_NORMAL_PRIORITY_CLASS（Popen creationflags；优先级
  class 随进程树继承，shell=True 派生的 python 同级降级）。
- POSIX：nice 10（preexec_fn）。CPython 警告多线程进程使用 preexec_fn
  有 fork 死锁风险（fork 后子进程仅存调用线程，Python 层回调若遇另
  一线程持锁可卡死）——本回调仅 os.nice 单调用、spawn 后立即 exec，
  窗口极小且执行链为低频长任务非高并发热路径，工程取舍接受；Windows
  为主要目标平台，POSIX 分支仅保障可用性（R2-P2-5 更正：旧注释
  「不触 malloc」声明不成立，回调本身即 Python 闭包）。

用户前台主动操作（如 Skill 安装的 git clone）不降级——用户正在等待。
"""
import os
import subprocess


def low_priority_kwargs() -> dict:
    """Popen / subprocess.run / asyncio.create_subprocess_exec 通用的
    低优先级 kwargs（按平台返回不同键，随 spawn 调用一并传入）。"""
    if os.name == "nt":
        return {"creationflags": getattr(
            subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)}

    def _nice10() -> None:
        os.nice(10)

    return {"preexec_fn": _nice10}
