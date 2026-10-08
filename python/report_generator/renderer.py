"""python/report_generator/renderer.py — Markdown → docx 渲染（pandoc）。

职责：
  - Markdown → docx 导出（经应用内置 pandoc，resolve_tool 统一解析 bundled > PATH）
  - Mermaid 块 → SVG 预处理（与任务智能体 doc_generator 共用 tools/mermaid_bridge；
    Electron 不可用或渲染失败时降级保留代码块原样导出）

表格 / 加粗 / 嵌套列表等由 pandoc 原生支持（此前手写 python-docx 逐行映射
不支持表格与加粗，已删除——与 doc_generator 工具链路共用同一 pandoc 通道，
避免双实现）。

设计参考：docs/2.5report-design.md §3.7 渲染规则
"""
import logging
import subprocess
from pathlib import Path

from tools.mermaid_bridge import cleanup_temp_dir, preprocess_mermaid
from tools.tool_resolver import resolve_tool

logger = logging.getLogger(__name__)

_PANDOC_TIMEOUT = 60  # 与 doc_generator 工具链路一致


def render_markdown_to_docx(
    markdown_text: str,
    output_path: str,
    title: str = "报告",
) -> str:
    """将 Markdown 报告渲染为 Word 文档（pandoc）。

    Args:
        markdown_text: Markdown 格式的报告文本（含 # 标题层级）
        output_path: 输出 docx 文件路径
        title: 文档标题（仅用于日志观测，pandoc 不注入正文——报告自身
               已含 `# {报告标题}` 一级标题，注入元数据标题会重复）

    Returns:
        输出文件路径

    Raises:
        RuntimeError: pandoc 不可用（安装损坏/缺失）或转换失败
    """
    pandoc = resolve_tool("pandoc")
    if not pandoc:
        logger.error("pandoc not found for report export")
        raise RuntimeError(
            "pandoc 未找到（报告导出依赖应用内置 pandoc）。"
            "请检查应用安装是否完整，或安装 pandoc 后重启应用。"
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    # markdown_text 写临时文件（pandoc CLI 接受文件输入）；
    # mermaid 块先经公共桥转 SVG（失败保留代码块，不阻断导出）
    tmp_md = output.with_suffix(".tmp.md")
    mmd_tmp = None
    try:
        tmp_md.write_text(markdown_text, encoding="utf-8")
        actual_md, mmd_tmp = preprocess_mermaid(tmp_md)
        result = subprocess.run(
            [pandoc, str(actual_md), "-o", str(output),
             "--from", "markdown", "--to", "docx"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=_PANDOC_TIMEOUT,
        )
    finally:
        if tmp_md.exists():
            try:
                tmp_md.unlink()
            except OSError:
                pass
        cleanup_temp_dir(mmd_tmp)

    if result.returncode != 0:
        logger.error("pandoc md→docx failed (title=%s): %s", title, result.stderr.strip())
        raise RuntimeError(f"pandoc 转换失败: {result.stderr.strip()}")

    logger.info("Rendered docx via pandoc (title=%s): %s", title, output)
    return str(output)
