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
import subprocess
import sys
import threading

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


def preinstall_packages(venv_python: str) -> bool:
    """Preinstall common packages into a new task venv (best-effort).

    Uses uv if available (faster), falls back to pip.
    Both uv and pip have global caches, so repeated installs are fast.
    Returns True when the install command succeeded.
    """
    uv = resolve_tool("uv")
    try:
        if uv:
            cmd = [uv, "pip", "install", "--python", venv_python,
                   *PREINSTALLED_PACKAGES]
        else:
            cmd = [venv_python, "-m", "pip", "install", "--quiet",
                   *PREINSTALLED_PACKAGES]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8", errors="replace",
            timeout=120,
        )
        if proc.returncode == 0:
            logger.info("Preinstalled packages in %s", venv_python)
            return True
        logger.warning("Package preinstall failed (exit %d): %s",
                       proc.returncode, (proc.stderr or "")[:500])
        return False
    except Exception as e:
        logger.warning("Package preinstall failed (best-effort): %s", e)
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
                    try:
                        with open(os.path.join(venv_path, _PREINSTALL_MARKER), "w",
                                  encoding="utf-8") as f:
                            f.write("ok")
                    except OSError as e:
                        logger.warning("preinstall marker write failed: %s", e)
            finally:
                lock.release()
        threading.Thread(target=_preinstall, daemon=True,
                         name="venv-preinstall").start()
    return venv_path
