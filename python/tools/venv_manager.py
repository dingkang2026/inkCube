"""Per-task virtual environment management.

Each task gets an isolated Python venv so that pip install in one task
does not pollute another. Uses stdlib venv as the default backend;
when `uv` is available it will be preferred for speed.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

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

    # Fallback: stdlib venv
    try:
        import venv
        venv.create(venv_path, with_pip=True)
        logger.info("venv created with stdlib: %s", venv_path)
        return venv_path
    except Exception as e:
        logger.error("venv creation failed entirely: %s", e)
        raise RuntimeError(f"无法创建任务虚拟环境: {e}") from e


def preinstall_packages(venv_python: str) -> None:
    """Preinstall common packages into a new task venv (best-effort).

    Uses uv if available (faster), falls back to pip.
    Both uv and pip have global caches, so repeated installs are fast.
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
        else:
            logger.warning("Package preinstall failed (exit %d): %s",
                           proc.returncode, (proc.stderr or "")[:500])
    except Exception as e:
        logger.warning("Package preinstall failed (best-effort): %s", e)
