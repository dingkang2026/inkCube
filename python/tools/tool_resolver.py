"""统一工具路径解析器。

为所有外部工具（uv / rg / ruff / pandoc / python-build-standalone）提供统一的路径查找逻辑。

三级查找策略：
1. 环境变量覆盖（开发/测试用）
2. 安装包捆绑路径（生产环境 %APPDATA%/InkCube/tools/ 或 resources/tools/）
3. 系统 PATH 查找（兜底 / 开发环境 conda）

使用方式：
    from tools.tool_resolver import resolve_tool, resolve_python

    uv_path = resolve_tool("uv")
    python_path = resolve_python()
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 工具注册表：工具名 → (环境变量名, 二进制文件名列表[按优先级])
_TOOL_REGISTRY: dict[str, tuple[str, list[str]]] = {
    "uv":     ("INKCUBE_UV_PATH",     ["uv.exe", "uv"]),
    "rg":     ("INKCUBE_RG_PATH",     ["rg.exe", "rg"]),
    "ruff":   ("INKCUBE_RUFF_PATH",   ["ruff.exe", "ruff"]),
    "pandoc": ("INKCUBE_PANDOC_PATH", ["pandoc.exe", "pandoc"]),
}


def _get_project_root() -> Path:
    """获取项目根目录（python/ 的父目录）。"""
    return Path(__file__).resolve().parent.parent.parent


def _get_app_data_dir() -> Path:
    """获取 %APPDATA%/InkCube/ 路径（跨平台）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "InkCube"


def _get_bundled_tools_dir() -> Path:
    """获取捆绑工具目录。

    开发环境：{项目根}/resources/tools/
    生产环境（PyInstaller）：exe 同级目录 resources/tools/
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "resources" / "tools"
    return _get_project_root() / "resources" / "tools"


def _get_app_data_tools_dir() -> Path:
    """获取 %APPDATA%/InkCube/tools/ 路径。"""
    return _get_app_data_dir() / "tools"


def resolve_tool(name: str) -> str | None:
    """解析工具路径，返回绝对路径或 None。

    查找优先级：
    1. 环境变量 INKCUBE_{NAME}_PATH（开发/测试覆盖）
    2. 捆绑目录（resources/tools/ 或 %APPDATA%/InkCube/tools/）
    3. 系统 PATH（shutil.which）

    Args:
        name: 工具名，必须是已注册的工具（uv / rg / ruff / pandoc）

    Returns:
        工具的绝对路径，未找到返回 None

    Raises:
        ValueError: 工具名未注册
    """
    if name not in _TOOL_REGISTRY:
        raise ValueError(f"未知工具: {name}，已知工具: {list(_TOOL_REGISTRY.keys())}")

    env_var, bin_names = _TOOL_REGISTRY[name]

    # 1. 环境变量覆盖
    env_path = os.environ.get(env_var)
    if env_path and os.path.isfile(env_path):
        logger.debug("resolve_tool(%s): found via env %s = %s", name, env_var, env_path)
        return env_path

    # 2a. 捆绑工具目录（开发: resources/tools/，生产: exe旁 resources/tools/）
    bundled_dir = _get_bundled_tools_dir()
    for bin_name in bin_names:
        candidate = bundled_dir / bin_name
        if candidate.is_file():
            logger.debug("resolve_tool(%s): found bundled at %s", name, candidate)
            return str(candidate)

    # 2b. %APPDATA%/InkCube/tools/（生产环境解压后的位置）
    app_data_tools = _get_app_data_tools_dir()
    if app_data_tools != bundled_dir:
        for bin_name in bin_names:
            candidate = app_data_tools / bin_name
            if candidate.is_file():
                logger.debug("resolve_tool(%s): found in appdata at %s", name, candidate)
                return str(candidate)

    # 3. 系统 PATH
    for bin_name in bin_names:
        found = shutil.which(bin_name)
        if found:
            logger.debug("resolve_tool(%s): found in PATH: %s", name, found)
            return found

    logger.debug("resolve_tool(%s): not found", name)
    return None


def resolve_python() -> str | None:
    """解析 python-build-standalone 的 Python 路径。

    用于创建任务 venv 时作为 --python 参数，确保预装 Python 不被污染。
    注意：这不是后端运行时的 Python（后端用 PyInstaller 内嵌的 Python）。

    查找优先级：
    1. 环境变量 INKCUBE_PYTHON_PATH
    2. %APPDATA%/InkCube/python/python.exe（生产环境解压后）
    3. resources/tools/python-build-standalone/python.exe（开发环境）
    4. sys.executable（兜底：使用当前 Python）

    Returns:
        Python 可执行文件的绝对路径
    """
    exe_name = "python.exe" if sys.platform == "win32" else "python3"

    # 1. 环境变量
    env_path = os.environ.get("INKCUBE_PYTHON_PATH")
    if env_path and os.path.isfile(env_path):
        logger.debug("resolve_python: found via env INKCUBE_PYTHON_PATH = %s", env_path)
        return env_path

    # 2. 生产环境：%APPDATA%/InkCube/python/
    app_data_python = _get_app_data_dir() / "python" / exe_name
    if app_data_python.is_file():
        logger.debug("resolve_python: found in appdata at %s", app_data_python)
        return str(app_data_python)

    # 3. 开发环境：resources/tools/python-build-standalone/
    project_root = _get_project_root()
    bundled = project_root / "resources" / "tools" / "python-build-standalone" / exe_name
    if bundled.is_file():
        logger.debug("resolve_python: found bundled at %s", bundled)
        return str(bundled)

    # 4. 兜底：当前 Python
    logger.debug("resolve_python: falling back to sys.executable = %s", sys.executable)
    return sys.executable


def get_tool_versions_path() -> Path:
    """获取 tool-versions.json 的路径。"""
    return _get_app_data_dir() / "tool-versions.json"


def get_all_tool_paths() -> dict[str, str | None]:
    """获取所有已注册工具的路径，用于诊断和状态展示。

    Returns:
        {工具名: 路径或None}
    """
    result = {}
    for name in _TOOL_REGISTRY:
        result[name] = resolve_tool(name)
    result["python"] = resolve_python()
    return result
