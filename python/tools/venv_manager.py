"""Per-task virtual environment management.

Each task gets an isolated Python venv so that pip install in one task
does not pollute another. Uses stdlib venv as the default backend;
when `uv` is available it will be preferred for speed.

Lazy creation: tasks only record the deterministic venv path (db.create_task);
the venv directory itself is materialized on first code-execution need via
`ensure_venv`, so chat-only tasks never cost any disk space.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from tools.proc_priority import low_priority_kwargs
from tools.tool_resolver import resolve_python, resolve_tool

logger = logging.getLogger(__name__)

# Common packages preinstalled in every task venv (Phase 13).
# Covers ~80% of Skill dependencies so most skills skip pip install.
PREINSTALLED_PACKAGES = [
    "requests", "httpx",     # HTTP
    "pyyaml",                # YAML
    "beautifulsoup4",        # HTML parsing
    "openpyxl",              # Excel
    "pillow",                # Image processing
    "html2text",             # HTML→Markdown（browser-fetch 内置 skill，纯 Python）
    "playwright",            # browser-fetch CDP 客户端（2.8 §5.5，~40MB wheel）
    # ppt-master 内置 skill 运行依赖（2.3.1 §8.5 + 包内 requirements.txt）
    "flask",                 # confirm_ui 本地服务
    "python-pptx", "lxml",   # svg_to_pptx 生成与 XML 处理
    "XlsxWriter",            # svg_to_pptx --native-charts-and-tables 原生图表
    "skia-pathops", "uharfbuzz",  # finalize_svg 路径/文字整形
    "defusedxml",            # 安全 XML 解析
]


class UvExclusionRequired(RuntimeError):
    """杀毒软件白名单未验证，预装被前置阻断（2.8 §12 F4）。

    前置阻断语义：未验证加白前任何 uv/pip 安装不允许发生——缓存体系要么
    健康运行，要么明确停机，不存在「带病装了再说」的中间态。首用同步路径
    （ensure_venv_blocking）抛出本异常，由 loader 转为结构化 JSON 交还 LLM。
    """


# 提权会话（Electron addUvCacheExclusion）在 Add-MpPreference 后同会话回读
# ExclusionPath 验证成功，落盘的裁决文件（普通权限下 Get-MpPreference 读
# ExclusionPath 返回 N/A，回读必须放进提权上下文，2.8 §12 F4）。位置在
# UV_CACHE_DIR 同级（data 目录），Python 与 Electron 两侧约定一致。
_VERIFIED_MARKER_NAME = "uv-exclusion-verified.json"


def _norm_path(p: str) -> str:
    """路径归一（abspath + 分隔符 + Windows 大小写不敏感），供跨进程比对。"""
    return os.path.abspath(p or "").replace("/", "\\").lower()


def _verified_marker_path() -> str:
    cache_dir = os.environ.get("UV_CACHE_DIR", "")
    if not cache_dir:
        return ""
    return os.path.join(os.path.dirname(cache_dir), _VERIFIED_MARKER_NAME)


def is_uv_exclusion_verified() -> bool:
    """杀毒软件白名单是否已验证（前置阻断闸门，2.8 §12 F4）。

    非 Windows 恒 True（无 Defender 排除概念）；UV_CACHE_DIR 未设置视为
    True（理论不发生，main.py 有 setdefault，测试环境可能触达）；文件缺失
    /解析失败/ok 非 true/记录的 cache_dir 与当前不一致（data 目录迁移）
    均判未验证。
    """
    if sys.platform != "win32":
        return True
    path = _verified_marker_path()
    if not path:
        return True
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if not (isinstance(data, dict) and data.get("ok") is True):
        return False
    cache_dir = os.environ.get("UV_CACHE_DIR", "")
    return _norm_path(data.get("cache_dir", "")) == _norm_path(cache_dir)


def _find_uv() -> str | None:
    """Return path to uv binary if available, else None."""
    return resolve_tool("uv")


def get_venv_python(venv_path: str) -> str:
    """Return the path to the Python executable inside a venv."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts", "python.exe")
    return os.path.join(venv_path, "bin", "python")


def get_venv_bin_dir(venv_path: str) -> str:
    """Return the bin/Scripts directory of a venv."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts")
    return os.path.join(venv_path, "bin")


def get_venv_path_from_python(venv_python: str) -> str:
    """从 venv 解释器路径反推 venv 根目录（Scripts|bin 的上一级）。"""
    return os.path.dirname(os.path.dirname(venv_python))


def create_venv(task_dir: str) -> str:
    """Create a venv for a task. Returns the venv path.

    Tries uv first (faster), falls back to stdlib venv.
    """
    venv_path = os.path.join(task_dir, ".venv")
    if os.path.isfile(get_venv_python(venv_path)):
        logger.info("venv already exists: %s", venv_path)
        return venv_path

    os.makedirs(task_dir, exist_ok=True)
    uv_path = _find_uv()

    if uv_path:
        try:
            subprocess.run(
                [uv_path, "venv", venv_path, "--python", resolve_python()],
                capture_output=True,
                encoding="utf-8", errors="replace",
                timeout=60,
                check=True,
                **low_priority_kwargs(),   # 2.11 §5.6 R3
            )
            logger.info("venv created with uv: %s", venv_path)
            return venv_path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("uv venv failed, falling back to stdlib: %s", e)

    # Fallback: stdlib venv — run as a subprocess so a hung ensurepip can't
    # block forever (in-process venv.create() has no timeout support)
    try:
        subprocess.run(
            [resolve_python(), "-m", "venv", venv_path],
            capture_output=True,
            encoding="utf-8", errors="replace",
            timeout=300,
            check=True,
            **low_priority_kwargs(),   # 2.11 §5.6 R3
        )
        logger.info("venv created with stdlib: %s", venv_path)
        return venv_path
    except Exception as e:
        logger.error("venv creation failed entirely: %s", e)
        raise RuntimeError(f"无法创建任务虚拟环境: {e}") from e


# 预装安装链（2.8 §12 F4）——cached（镜像+缓存）重试收敛 → mirror（--no-cache）兑底：
# 1) cached：镜像源 + 带缓存，rename 竞态失败自动重试（共 2 尝试）。
#    前置条件：白名单已验证（is_uv_exclusion_verified）。
#    - 重试收敛：rename 竞态（os error 5）死包随机（certifi/pillow 各死
#      过）、每轮死 1~2 但缓存增量推进 2~4 个（实测 archive 3→16→18）。
#      保留 2 次重试：全冷机器每任务渐进推进缓存；稳态（小包全缓存后）
#      剩余大包（pillow/playwright，>5MB 下载写入期扫描锁 100% 必死）
#      零推进，重试即纯浪费——由 mirror 层兼做绕竞态快速通道（本机实测
#      稳态下 6 次全败零推进，2 次即足够）。
# 2) mirror：--no-cache 完全绕开 archive rename 路径——既是 cached 链
#    全败时的永成功兑底，也是大包必死场景的快速通道（4s vs 必败重试）。
#
# 跨模块共享的装包镜像源：预装链（下方）与 package_manager 工具（LLM 主动
# 装包，tools/package_manager.py）共用同一常量，保证「推荐 uv + 镜像」从
# 意图变成事实（2026-10-16 装包路径统一）。
# 源选镜像：官方源慢网下 resolve 28s~1m37s + 冷缓存下载 300s 超时
# （2026-10-12 三轮实测），腾讯云源 resolve+全装 ~15-32s。
# 曾用清华源，实测其对 packages 路径 wheel 下载间歇性 403
# （index 可达、下载被拒），换腾讯云源规避。
PYPI_MIRROR = "https://mirrors.cloud.tencent.com/pypi/simple"

# rename 竞态瞬态错误特征（uv#1491）：单包概率性失败，重试可收敛；
# 超时/网络类错误不重试（增量无益且烧 300s 预算），直接降级 mirror。
_TRANSIENT_MARKERS = ("os error 5", "拒绝访问", "access is denied",
                      "failed to read from the distribution cache")


def _is_transient_rename_error(err: str) -> bool:
    """错误是否为 rename 竞态瞬态（可重试收敛）。"""
    low = err.lower()
    return any(marker in low for marker in _TRANSIENT_MARKERS)


def _run_preinstall(cmd: list[str]) -> tuple[bool, str]:
    """执行一次预装命令，返回 (成功?, 错误全文供重试判定)。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8", errors="replace",
            timeout=300,  # playwright ~40MB wheel，冷缓存下载需较长时间
            **low_priority_kwargs(),   # 2.11 §5.6 R3
        )
    except Exception as e:
        return False, str(e)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or "") + "\n" + (proc.stdout or "")


# .tmp 孤儿陈旧阈值：rename 竞态失败留下的死目录。仅清 mtime 超过 1 小时
# 的——在途解压的 .tmp 可能属于其他 venv 的并发预装进程（锁是 per-venv
# 的），不可触碰；超时后仍存在的必为本进程或已退出进程的孤儿。
_TMP_ORPHAN_STALE_SECS = 3600


def _cleanup_stale_tmp_orphans() -> None:
    """清扫 uv-cache 中陈旧的 .tmp 孤儿（重试链失败现场，防持续累积）。"""
    cache_dir = os.environ.get("UV_CACHE_DIR", "")
    if not cache_dir:
        return
    now = time.time()
    try:
        for entry in Path(cache_dir).glob(".tmp*"):
            try:
                if now - entry.stat().st_mtime > _TMP_ORPHAN_STALE_SECS:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue
    except OSError as e:
        logger.debug(".tmp orphan cleanup skipped: %s", e)


def preinstall_packages(venv_python: str) -> bool:
    """Preinstall common packages into a new task venv (best-effort).

    Uses uv if available (faster), falls back to pip. Caller must gate
    on is_uv_exclusion_verified() first (前置阻断，2.8 §12 F4)。

    链路：cached（镜像+缓存）重试收敛 → mirror（--no-cache）兑底，
    详见模块内注释。Returns True when any attempt succeeded.
    """
    uv = resolve_tool("uv")
    if uv:
        cached_cmd = [uv, "pip", "install", "--python", venv_python,
                      "--default-index", PYPI_MIRROR,
                      *PREINSTALLED_PACKAGES]
        mirror_cmd = [*cached_cmd, "--no-cache"]
    else:
        cached_cmd = [venv_python, "-m", "pip", "install", "--quiet",
                      "-i", PYPI_MIRROR, *PREINSTALLED_PACKAGES]
        mirror_cmd = [venv_python, "-m", "pip", "install", "--quiet",
                      "-i", PYPI_MIRROR, *PREINSTALLED_PACKAGES]

    for attempt in range(2):  # 稳态下大包必死零推进，2 次足够；mirror 兼做绕行
        ok, err = _run_preinstall(cached_cmd)
        if ok:
            _cleanup_stale_tmp_orphans()
            logger.info("Package preinstall in %s (cached attempt #%d)",
                        venv_python, attempt + 1)
            return True
        logger.warning("Package preinstall failed on cached attempt #%d: %s",
                       attempt + 1, err[:2000])
        if not _is_transient_rename_error(err):
            break  # 非瞬态（超时/网络）——重试无益，直接降级
    ok, err = _run_preinstall(mirror_cmd)
    if ok:
        _cleanup_stale_tmp_orphans()
        logger.info("Package preinstall in %s (mirror attempt)", venv_python)
        return True
    logger.warning("Package preinstall failed on mirror attempt: %s",
                   err[:500])
    return False


_PREINSTALL_MARKER = ".inkcube-preinstalled"

# 每 venv 一把预装锁：同一任务连续调用 venv 工具时，ensure 会在预装线程
# 未完成前再次被调，锁保证不重复起线程（两个 uv/pip 并发写同一 venv 会
# 互相破坏）。任务数有配额上限，锁字典生命周期内常驻、内存可忽略。
_preinstall_locks: dict[str, threading.Lock] = {}
_preinstall_locks_guard = threading.Lock()


def _get_preinstall_lock(venv_path: str) -> threading.Lock:
    with _preinstall_locks_guard:
        return _preinstall_locks.setdefault(venv_path, threading.Lock())


def is_preinstall_done(venv_path: str) -> bool:
    """Whether the preinstall marker exists for this venv."""
    return os.path.isfile(os.path.join(venv_path, _PREINSTALL_MARKER))


def _write_preinstall_marker(venv_path: str) -> None:
    try:
        with open(os.path.join(venv_path, _PREINSTALL_MARKER), "w",
                  encoding="utf-8") as f:
            f.write("ok")
    except OSError as e:
        logger.warning("preinstall marker write failed: %s", e)


def ensure_venv_blocking(venv_path: str) -> bool:
    """同步确保 venv 依赖就绪（skill CLI 首用路径，2.8 §5.5）。

    「缺就装、装完继续」：marker 在位 → 立即 True（零开销快路径）；缺失 →
    白名单前置检查（未验证抛 UvExclusionRequired，由 loader 转结构化
    JSON 交还 LLM——重试无用，须用户先完成授权），然后拿预装锁后同步
    执行预装（与后台线程互斥：线程正在装则等待其完成后双检 marker 受益），
    成功写 marker 返回 True，失败返回 False——调用方向 LLM 返回 retryable
    错误，下次调用重试。不依赖后台线程存活：预装线程曾失败退出的 venv，
    首次调用仍会在此自愈补装。
    """
    if not is_uv_exclusion_verified():
        raise UvExclusionRequired(
            "杀毒软件白名单未确认，依赖安装被前置阻断；"
            "请用户在应用顶部横幅完成一次系统授权后重试")
    venv_python = get_venv_python(venv_path)
    lock = _get_preinstall_lock(venv_path)
    with lock:
        if is_preinstall_done(venv_path):
            return True
        if preinstall_packages(venv_python):
            _write_preinstall_marker(venv_path)
            return True
        return False


def ensure_venv(task_dir: str) -> str:
    """Lazily ensure a task venv exists, then preinstall packages once.

    create_venv is idempotent (existing venv → no-op). Preinstall runs at
    most once per venv: the marker file guards across restarts/repeat runs,
    the per-venv lock guards concurrent ensures within a process (second
    caller skips while the first thread is installing); a failed install
    leaves the marker absent so the next ensure retries. Preinstall itself
    runs in a daemon thread — first code execution must not block on a
    cold package cache.

    白名单前置阻断（2.8 §12 F4）：未验证时后台线程跳过预装（不跑 uv），
    marker 保持缺失，首用同步路径（ensure_venv_blocking）会给 LLM 明确
    的授权指引。
    """
    venv_path = create_venv(task_dir)
    venv_python = get_venv_python(venv_path)
    if not is_uv_exclusion_verified():
        logger.warning(
            "Preinstall skipped for %s: antivirus whitelist not verified "
            "(front-blocked, 2.8 §12 F4)", venv_path)
        return venv_path
    lock = _get_preinstall_lock(venv_path)
    if not is_preinstall_done(venv_path) and lock.acquire(blocking=False):
        def _preinstall():
            try:
                # 双检：拿锁前的窗口内上一轮预装可能已完成并写入 marker
                if is_preinstall_done(venv_path):
                    return
                if preinstall_packages(venv_python):
                    _write_preinstall_marker(venv_path)
            finally:
                lock.release()
        threading.Thread(target=_preinstall, daemon=True,
                         name="venv-preinstall").start()
    return venv_path
