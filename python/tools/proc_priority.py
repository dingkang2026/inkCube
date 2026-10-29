"""任务子进程低优先级 + 无控制台窗口（2.11 §5.6 R3，E3 T11）。

任务执行链 spawn 的 venv shell/python/uv 子进程统一以低优先级、无窗口启动，
OS 前台调度天然优先——定时采集或装包运行中，前台操作无可感知卡顿。全局生效
（人工任务同样受益）。执行链覆盖：shell 工具（Popen）、code_executor、
package_manager、venv 创建/预装（uv/pip 子进程）、外部 Skill 脚本（asyncio
create_subprocess_exec），以及任务链之外的瞬时 CLI 子进程（git/pandoc/soffice/ripgrep/pyright/mypy/tsc
等，仅隐藏窗口不降级，见 hidden_window_kwargs）。

窗口语义（2026-10-28 PPT 生成闪窗复盘）：Python 后端运行于无控制台环境
（PyCharm 启动 / Electron spawn），Windows 上每个控制台子进程都会被分配
新的可见控制台窗口——agent 每执行一条命令闪一次窗。CREATE_NO_WINDOW 让
子进程获得隐藏控制台（stdio 管道不受影响；SetConsoleOutputCP 在隐藏控制台
上仍有效，见 shell_tool._ensure_utf8_console）。

- Windows：BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW（creationflags，
  class 随进程树继承，shell=True 派生的 python 同级降级）。
- POSIX：nice 10（preexec_fn）。CPython 警告多线程进程使用 preexec_fn
  有 fork 死锁风险（fork 后子进程仅存调用线程，Python 层回调若遇另一
  线程持锁可卡死）——本回调仅 os.nice 单调用、spawn 后立即 exec，
  窗口极小且执行链为低频长任务非高并发热路径，工程取舍接受；Windows
  为主要目标平台，POSIX 分支仅保障可用性（R2-P2-5 更正：旧注释
  「不触 malloc」声明不成立，回调本身即 Python 闭包）。

用户前台主动操作（如 Skill 安装的 git clone）不降级——用户正在等待。
"""
import os
import subprocess


def low_priority_kwargs() -> dict:
    """Popen / subprocess.run / asyncio.create_subprocess_exec 通用的
    低优先级 + 无窗口 kwargs（按平台返回不同键，随 spawn 调用一并传入）。"""
    if os.name == "nt":
        return {"creationflags": getattr(
            subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}

    def _nice10() -> None:
        os.nice(10)

    return {"preexec_fn": _nice10}


def hidden_window_kwargs() -> dict:
    """仅隐藏控制台窗口、不降优先级的 kwargs（Windows 专属；POSIX 空dict）。

    供任务链之外的瞬时 CLI 子进程使用（git 探测/clone、pandoc/soffice 转换、
    ripgrep/pyright/mypy/tsc 分析、taskkill 清理、confirm_ui 关停等）：这些调用要么是用户正在等待的
    前台路径（降级无益），要么是系统清理动作（降级有害），统一只解决
    无控制台父进程下的窗口闪现。"""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
