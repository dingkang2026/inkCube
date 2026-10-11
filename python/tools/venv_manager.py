"""Per-task virtual environment management.

Each task gets an isolated Python venv so that pip install in one task
does not pollute another. Uses stdlib venv as the default backend;
when `uv` is available it will be preferred for speed.

Lazy creation: tasks only record the deterministic venv path (db.create_task);
the venv directory itself is materialized on first code-execution need via
`ensure_venv`, so chat-only tasks never cost any disk space.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time

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
]


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
        )
        logger.info("venv created with stdlib: %s", venv_path)
        return venv_path
    except Exception as e:
        logger.error("venv creation failed entirely: %s", e)
        raise RuntimeError(f"无法创建任务虚拟环境: {e}") from e


# uv 预装失败后的自愈重试（2.8 §5.5）——三级链，各管一层：
# 1) cached：常态路径，缓存热则秒级完成。
# 2) cached-retry：uv 缓存「解压 wheel → rename 进 archive-v0」可被实时防护
#    的扫描句柄稳定阻断（os error 5，与 uv 版本无关，见 2.8 §12 F2）。失败后
#    智能体进程内自愈：从 stderr 提取 uv 报出的 (tmp → dest) 改名对，等待
#    扫描句柄释放后亲手完成改名（实验证明延时后必成），重试直接命中缓存。
# 3) mirror：官方源不可达/极慢时的可达性兑底（国内实测 resolve 3.1s/
#    全装 31.9s vs 官方源 resolve 28s~1m37s 且慢网时 300s 超时），同时带
#    --no-cache 绕开 archive 路径（镜像快下载仍会写缓存再撞 rename）。
#    pip 分支无 archive 层，镜像兑底用 -i。
_PREINSTALL_RETRY_DELAY = 5.0    # 重试前等待秒数（含实时防护句柄释放窗口）
_PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
# 按错误关键字切段后逐段配对（段内行为同单对正则）：全局 findall 的
# dest 组贪婪到全文最后一个冒号，≥2 个改名错误时会把后续对整个吞进前
# 一条 dest（实测失配，修复静默降级到镜像层）；段内 dest 仍贪婪到最后
# 一个冒号——路径含盘符冒号（C:\），非贪婪会在盘符处截断（实测踩坑）
_RENAME_SEG_SPLIT_RE = re.compile(r"failed to rename file from", re.IGNORECASE)
# dest 组贪婪到段内最后一个冒号——路径含盘符冒号（C:\），非贪婪会在
# 盘符处截断（实测踩坑：review 建议的 (.+?): 形态即裁在此处，测试拦截）
_RENAME_SEG_PAIR_RE = re.compile(r"(.+?) to (.+):")


def _repair_uv_archive_rename(stderr: str) -> int:
    """完成 uv 被阻断的缓存改名（2.8 §12 F2 进程内自愈）。

    uv 解压 wheel 到缓存临时目录后立即 rename 进 archive-v0，实时防护的
    扫描句柄尚未释放时被拒；uv 退出后句柄已释放，由本进程延时后补上改名，
    缓存即完整（下次 uv 直接命中）。返回实际修复条数；单对失败不中断
    （剩余对继续，全部失败也不抛错——后续 --no-cache 兕底）。

    注意：uv 管道输出下按固定宽度对长路径折行（word-wrap，在空格处断），
    须先折叠全部空白再匹配，否则正则在「from」后即失配（实测踩坑）。
    多个改名错误按关键字切段逐段配对——全局单正则的贪婪 dest 会把后续
    错误对吞并（实测失配）。
    """
    text = " ".join((stderr or "").split())
    repaired = 0
    pairs = [m.groups() for seg in _RENAME_SEG_SPLIT_RE.split(text)[1:]
             if (m := _RENAME_SEG_PAIR_RE.match(seg))]
    if not pairs and "failed to rename" in text:
        logger.warning("archive repair: rename error present but no pair parsed")
    for tmp, dest in pairs:
        tmp, dest = tmp.strip(), dest.strip()
        if not os.path.isdir(tmp):
            logger.warning("archive repair: tmp missing, skip: %s", tmp)
            continue
        if os.path.exists(dest):
            continue  # 已有同名条目（可能并发写入），不覆盖
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.rename(tmp, dest)
            repaired += 1
            logger.info("archive repair: renamed %s -> %s", tmp, dest)
        except OSError as e:
            logger.warning("archive repair failed for %s: %s", dest, e)
    return repaired


def _run_preinstall(cmd: list[str]) -> tuple[bool, str]:
    """执行一次预装命令，返回 (成功?, 错误全文供 rename 修复解析)。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8", errors="replace",
            timeout=300,  # playwright ~40MB wheel，冷缓存下载需较长时间
        )
    except Exception as e:
        return False, str(e)
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or "") + "\n" + (proc.stdout or "")


def preinstall_packages(venv_python: str) -> bool:
    """Preinstall common packages into a new task venv (best-effort).

    Uses uv if available (faster), falls back to pip. Both uv and pip
    have global caches, so repeated installs are fast. Three-tier
    self-heal (2.8 §12 F3): in-process repair of uv's blocked cache
    rename then retry; finally a domestic-mirror + --no-cache attempt
    for slow/unreachable official index. Returns True when any attempt
    succeeded.
    """
    uv = resolve_tool("uv")
    if uv:
        base_cmd = [uv, "pip", "install", "--python", venv_python,
                    *PREINSTALLED_PACKAGES]
        fallbacks = [
            ("mirror", [*base_cmd, "--no-cache",
                        "--default-index", _PYPI_MIRROR]),
        ]
    else:
        base_cmd = [venv_python, "-m", "pip", "install", "--quiet",
                    *PREINSTALLED_PACKAGES]
        fallbacks = [
            ("mirror", [venv_python, "-m", "pip", "install", "--quiet",
                        "-i", _PYPI_MIRROR, *PREINSTALLED_PACKAGES]),
        ]

    attempts: list[tuple[str, list[str]]] = [("cached", base_cmd),
                                             ("cached-retry", base_cmd),
                                             *fallbacks]

    last_err = ""
    for name, cmd in attempts:
        if last_err:
            # 上一轮失败：等待实时防护释放扫描句柄后，进程内修复被阻断的
            # 缓存改名，再进入下一轮尝试（修复成功则重试直接命中缓存）
            time.sleep(_PREINSTALL_RETRY_DELAY)
            _repair_uv_archive_rename(last_err)
        ok, err = _run_preinstall(cmd)
        if ok:
            logger.info("Package preinstall in %s (%s attempt)",
                        venv_python, name)
            return True
        last_err = err
        logger.warning("Package preinstall failed on %s attempt: %s",
                       name, err[:500])
    return False


# 信号计数与加白清理共用的陈旧阈值：健康安装解压 wheel 期间 cache 根会
# 短暂存在瞬态 .tmp（写完即改名进 archive），在途条目不计信号、加白成功
# 后的清理也不删（Electron addUvCacheExclusion 用同一 10 分钟值，2.8 §12 F3）
_RESIDUE_STALE_SECS = 600


def get_uv_cache_tmp_residue() -> int:
    """uv-cache 下陈旧 .tmp* 残留条目数（2.8 §12 F3 阻断信号）。

    每次 rename 被实时防护阻断 / 安装被中断，uv 都会在缓存目录留下一个
    .tmp 解压残骸且不会自行清理——它是「阻断发生过」的持久物证，天然
    跨重启、零状态维护。加白后不再新增；一次性清理可让信号归零。

    只计陈旧条目（mtime 距今超过 _RESIDUE_STALE_SECS）：健康冷装的瞬态
    解压目录被排除，避免把正常安装误报为杀软阻断；被阻断/中断的残骸
    mtime 冻结，超时后即计入。
    UV_CACHE_DIR 未设置（理论不发生，main.py 有 setdefault）时返回 0。
    """
    cache_dir = os.environ.get("UV_CACHE_DIR")
    if not cache_dir or not os.path.isdir(cache_dir):
        return 0
    now = time.time()
    count = 0
    try:
        for name in os.listdir(cache_dir):
            if not name.startswith(".tmp"):
                continue
            try:
                mtime = os.stat(os.path.join(cache_dir, name)).st_mtime
            except OSError:
                continue  # 扫描窗口内被清理/占用，视为不存在
            if now - mtime > _RESIDUE_STALE_SECS:
                count += 1
    except OSError as e:
        logger.warning("uv-cache residue scan failed: %s", e)
        return 0
    return count


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
    拿预装锁后同步执行预装（与后台线程互斥：线程正在装则等待其完成后
    双检 marker 受益），成功写 marker 返回 True，失败返回 False——调用方
    向 LLM 返回 retryable 错误，下次调用重试。不依赖后台线程存活：预装
    线程曾失败退出的 venv，首次调用仍会在此自愈补装。
    """
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
    """
    venv_path = create_venv(task_dir)
    venv_python = get_venv_python(venv_path)
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
