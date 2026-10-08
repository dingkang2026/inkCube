"""python/tools/mermaid_bridge.py — Mermaid → SVG 离线渲染桥（公共模块）。

doc_generator（任务智能体导出）与 report_generator.renderer（报告导出）共用，
避免双实现。链路：```mermaid 代码块 → 无头 Electron 子进程（electron/mermaid-renderer.js，
注入项目内置 mermaid.min.js）→ SVG → 以 ![diagram](svg路径) 替换原代码块 → pandoc。

降级纪律（§3.7 渲染规则）：单块渲染失败保留原代码块；全部失败返回原文件，
调用方无需感知 Electron 是否可用。
"""
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


def render_mermaid_to_svg(electron: str, code: str) -> str | None:
    """Render a single Mermaid diagram to SVG via a headless Electron process."""
    renderer_script = str(
        Path(__file__).resolve().parent.parent.parent / 'electron' / 'mermaid-renderer.js'
    )
    if not os.path.isfile(renderer_script):
        logger.warning('mermaid-renderer.js not found at %s', renderer_script)
        return None

    try:
        result = subprocess.run(
            [electron, renderer_script],
            input=code,
            capture_output=True,
            encoding='utf-8',
            timeout=_MERMAID_RENDER_TIMEOUT,
            env={**os.environ, 'INKCUBE_MERMAID_RENDERER': '1'},
        )
        if result.returncode != 0:
            logger.warning(
                'Mermaid render failed (exit %d): %s',
                result.returncode, result.stderr.strip(),
            )
            return None
        svg = result.stdout.strip()
        return svg if svg.startswith('<svg') or svg.startswith('<?xml') else None
    except subprocess.TimeoutExpired:
        logger.warning('Mermaid render timed out')
        return None
    except Exception as e:
        logger.warning('Mermaid render error: %s', e)
        return None


def preprocess_mermaid(input_file: Path) -> tuple[Path, str | None]:
    """Replace ```mermaid blocks with SVG image references.

    Args:
        input_file: 待预处理的 Markdown 文件路径

    Returns:
        (actual_input_path, temp_dir_or_None)。
        无 mermaid 块 / Electron 不可用 / 全部渲染失败时返回原 input_file 与
        None——调用方直接使用 input_file；成功时 pandoc 应使用 actual_input，
        并在结束后调用 cleanup_temp_dir(temp_dir)。
    """
    content = input_file.read_text(encoding='utf-8')
    if not MERMAID_RE.search(content):
        return input_file, None

    electron = find_electron()
    if not electron:
        return input_file, None

    tmp_dir = tempfile.mkdtemp(prefix='inkcube_mmd_')
    svg_dir = Path(tmp_dir) / 'svgs'
    svg_dir.mkdir()

    success_count = 0

    def _replace_block(m: re.Match) -> str:
        nonlocal success_count
        code = m.group(1)
        svg = render_mermaid_to_svg(electron, code)
        if svg:
            svg_path = svg_dir / f'diagram_{success_count}.svg'
            svg_path.write_text(svg, encoding='utf-8')
            success_count += 1
            return f'![diagram]({svg_path})'
        logger.warning('Mermaid block render failed, keeping as code')
        return m.group(0)  # leave block unchanged on failure

    modified = MERMAID_RE.sub(_replace_block, content)

    if success_count == 0:
        # All renders failed — no change
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return input_file, None

    # Write pre-processed markdown to a temp file
    preprocessed = Path(tmp_dir) / 'preprocessed.md'
    preprocessed.write_text(modified, encoding='utf-8')
    logger.info('Mermaid: rendered %d diagram(s) to SVG', success_count)
    return preprocessed, tmp_dir


def cleanup_temp_dir(tmp_dir: str | None) -> None:
    """Remove the temp dir returned by preprocess_mermaid（统一清理入口）。"""
    if tmp_dir:
        shutil.rmtree(tmp_dir, ignore_errors=True)
