"""python/tools/mermaid_bridge.py — Mermaid → PNG 离线渲染桥（公共模块）。

doc_generator（任务智能体导出）与 report_generator.renderer（报告导出）共用，
避免双实现。链路：```mermaid 代码块 → 无头 Electron 子进程（electron/mermaid-renderer.js，
注入项目内置 mermaid.min.js）→ 白底 2× 光栅化 PNG（base64 经 stdout 回传）→
以 ![diagram](png路径) 替换原代码块 → pandoc。

为何落成 PNG 而非 SVG：Word 的 SVG 引擎丢弃 mermaid SVG 内嵌 <style> 的类样式
（填充回退黑块）且按自身字体度量重排文字（中文叠压成乱码），PNG 位图在
各查看器下渲染一致，且 pandoc 嵌 PNG 不再依赖 rsvg-convert。

降级纪律（§3.7 渲染规则）：单块渲染失败保留原代码块；全部失败返回原文件，
调用方无需感知 Electron 是否可用。
"""
import base64
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ```mermaid 围栏块（MULTILINE+DOTALL：块内任意内容，以行首 ``` 收尾）
MERMAID_RE = re.compile(r'^```mermaid\s*\n(.*?)^```\s*$', re.MULTILINE | re.DOTALL)

_MERMAID_RENDER_TIMEOUT = 30  # 单块渲染超时（秒）


def find_electron() -> str | None:
    """Locate the Electron binary for spawning a mermaid renderer.

    Resolution order: INKCUBE_ELECTRON_PATH env → dev node_modules → PATH.
    """
    # 1. Environment variable override
    env = os.environ.get('INKCUBE_ELECTRON_PATH')
    if env and os.path.isfile(env):
        return env

    project_root = Path(__file__).resolve().parent.parent.parent

    # 2. Development: node_modules/electron/dist/
    if sys.platform == 'win32':
        candidates = [
            project_root / 'node_modules' / 'electron' / 'dist' / 'electron.exe',
        ]
    else:
        candidates = [
            project_root / 'node_modules' / 'electron' / 'dist' / 'electron',
        ]
    for c in candidates:
        if c.is_file():
            return str(c)

    # 3. System PATH
    found = shutil.which('electron')
    if found:
        return found

    logger.warning('Electron binary not found — Mermaid diagrams will be skipped')
    return None


def _resolve_renderer_script() -> str:
    """Locate mermaid-renderer.js.

    Packaged builds get the explicit path via INKCUBE_MERMAID_RENDERER_SCRIPT
    (injected by the Electron main process when spawning Python — the script
    ships in extraResources because app.asar is opaque to Python's isfile);
    dev falls back to the repo-relative path.
    """
    env_path = os.environ.get('INKCUBE_MERMAID_RENDERER_SCRIPT', '')
    if env_path and os.path.isfile(env_path):
        return env_path
    return str(
        Path(__file__).resolve().parent.parent.parent / 'electron' / 'mermaid-renderer.js'
    )


def render_mermaid_to_png(electron: str, code: str) -> bytes | None:
    """Render a single Mermaid diagram to a white-background PNG via headless Electron.

    Mermaid code is written to a temp file passed via argv: a piped stdin
    yields 0 bytes on Windows Electron (GUI subsystem) child processes,
    so file input is the only reliable channel. The renderer returns the
    PNG as base64 on stdout (text-safe on any platform).
    """
    renderer_script = _resolve_renderer_script()
    if not os.path.isfile(renderer_script):
        logger.warning('mermaid-renderer.js not found at %s', renderer_script)
        return None

    fd, input_path = tempfile.mkstemp(prefix='inkcube_mmd_input_', suffix='.mmd')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(code)
        result = subprocess.run(
            [electron, renderer_script, input_path],
            capture_output=True,
            encoding='utf-8',
            stdin=subprocess.DEVNULL,
            timeout=_MERMAID_RENDER_TIMEOUT,
            env={**os.environ, 'INKCUBE_MERMAID_RENDERER': '1'},
        )
    except subprocess.TimeoutExpired:
        logger.warning('Mermaid render timed out')
        return None
    except Exception as e:
        logger.warning('Mermaid render error: %s', e)
        return None
    finally:
        try:
            os.remove(input_path)
        except OSError:
            pass

    if result.returncode != 0:
        logger.warning(
            'Mermaid render failed (exit %d): %s',
            result.returncode, result.stderr.strip(),
        )
        return None
    try:
        png = base64.b64decode(result.stdout.strip(), validate=True)
    except ValueError:
        logger.warning('Mermaid render output is not valid base64')
        return None
    return png if png.startswith(b'\x89PNG') else None


def preprocess_mermaid(input_file: Path,
                       image_dir: Path | None = None) -> tuple[Path, str | None]:
    """Replace ```mermaid blocks with image references.

    Args:
        input_file: 待预处理的 Markdown 文件路径
        image_dir: 重绘 PNG 落点（可选）。缺省落临时目录——md 内引用为临时
            目录绝对路径，仅供「用完即弃」的导出链（doc_generator，用后
            cleanup_temp_dir）；指定时 PNG 持久化到该目录且 md 原地改写，
            引用随所在工作区长期有效（ppt 素材就位：staged md 被 agent
            持续读取，临时目录被系统清理后引用会断裂）。

    Returns:
        (actual_input_path, temp_dir_or_None)。
        无 mermaid 块 / Electron 不可用 / 全部渲染失败时返回原 input_file 与
        None——调用方直接使用 input_file；缺省模式成功时 pandoc 应使用
        actual_input，并在结束后调用 cleanup_temp_dir(temp_dir)。
    """
    content = input_file.read_text(encoding='utf-8')
    if not MERMAID_RE.search(content):
        return input_file, None

    electron = find_electron()
    if not electron:
        return input_file, None

    if image_dir is not None:
        try:
            image_dir.mkdir(parents=True, exist_ok=True)
            modified, count = _render_blocks(content, image_dir, electron)
            if count:
                input_file.write_text(modified, encoding='utf-8')
                logger.info('Mermaid: rendered %d diagram(s) to PNG', count)
        except OSError:
            # 写盘异常（磁盘满/权限/杀软锁文件）：降级保留原文件，
            # 不让预处理异常阻断调用方主链路（降级纪律）
            logger.warning('Mermaid preprocess IO failed, keeping original markdown',
                           exc_info=True)
        return input_file, None

    tmp_dir = tempfile.mkdtemp(prefix='inkcube_mmd_')
    try:
        return _render_blocks_to_file(input_file, content, Path(tmp_dir), electron)
    except OSError:
        # 写盘异常（磁盘满/权限/杀软锁文件）：清理后降级返回原文件，
        # 不让预处理异常阻断调用方的导出主链路（降级纪律）
        logger.warning('Mermaid preprocess IO failed, keeping original markdown',
                       exc_info=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return input_file, None


def _render_blocks(content: str, image_dir: Path,
                   electron: str) -> tuple[str, int]:
    """逐块重绘 mermaid 为 PNG（落 image_dir）并替换引用；返回 (新内容, 成功数)。"""
    success_count = 0

    def _replace_block(m: re.Match) -> str:
        nonlocal success_count
        code = m.group(1)
        png = render_mermaid_to_png(electron, code)
        if png:
            png_path = image_dir / f'diagram_{success_count}.png'
            png_path.write_bytes(png)
            success_count += 1
            return f'![diagram]({png_path})'
        logger.warning('Mermaid block render failed, keeping as code')
        return m.group(0)  # leave block unchanged on failure

    return MERMAID_RE.sub(_replace_block, content), success_count


def _render_blocks_to_file(input_file: Path, content: str,
                           tmp_dir: Path, electron: str) -> tuple[Path, str | None]:
    image_dir = tmp_dir / 'images'
    image_dir.mkdir()
    modified, success_count = _render_blocks(content, image_dir, electron)

    if success_count == 0:
        # All renders failed — no change
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return input_file, None

    # Write pre-processed markdown to a temp file
    preprocessed = tmp_dir / 'preprocessed.md'
    preprocessed.write_text(modified, encoding='utf-8')
    logger.info('Mermaid: rendered %d diagram(s) to PNG', success_count)
    return preprocessed, str(tmp_dir)


def cleanup_temp_dir(tmp_dir: str | None) -> None:
    """Remove the temp dir returned by preprocess_mermaid（统一清理入口）。"""
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)
