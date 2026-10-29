"""python/report_generator/renderer.py — Markdown → docx 渲染（pandoc）。

职责：
  - Markdown → docx 导出（经应用内置 pandoc，resolve_tool 统一解析 bundled > PATH）
  - 引用上标转换（视觉尾注，2026-10-27）：正文 [n] → pandoc 上标 ^[n]^，
    导出前由报告导出端点调用（预览端 <sup> 转换与之同契约）
  - 图形块 → PNG 预处理（mermaid 块 / 内联 <svg> 块 / 本地 .svg 引用，
    与任务智能体 doc_generator 共用 tools/mermaid_bridge；Electron 不可用
    或渲染失败时降级保留原样导出）

表格 / 加粗 / 嵌套列表等由 pandoc 原生支持（此前手写 python-docx 逐行映射
不支持表格与加粗，已删除——与 doc_generator 工具链路共用同一 pandoc 通道，
避免双实现）。

设计参考：docs/2.5report-design.md §3.7 渲染规则
"""
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from tools.doc_template import resolve_reference_doc
from tools.mermaid_bridge import cleanup_temp_dir, preprocess_mermaid
from tools.proc_priority import hidden_window_kwargs
from tools.tool_resolver import resolve_tool

logger = logging.getLogger(__name__)

_PANDOC_TIMEOUT = 60  # 与 doc_generator 工具链路一致

# 视觉尾注引用上标（2026-10-27）：正文 [n] → pandoc 上标 ^[n]^。
# 契约：content.md 保持语义 [n] 单一事实源；预览端（ReportPreview）转
# <sup>[n]</sup>、导出端转 ^[n]^，两端各自按渲染引擎转换、排除规则一致：
#   - 行首 [n]：文末「参考来源」列表条目自身，不上标
#   - [n]( ：Markdown 链接形态（[text](url)），非引用
#   - [[n]] ：嵌套方括号（wiki 链接 key 形态），非引用
# 已知边界：代码块内 [n] 同样转换（报告正文极少含代码块，接受）；
# 相邻引用 [40][41] 逐个转换，pandoc 渲染为连续上标。
_CITE_SUP_RE = re.compile(r"(?<!^)(?<!\[)\[(\d{1,3})\](?!\()(?!\])", re.MULTILINE)


def apply_endnote_superscript(markdown_text: str) -> str:
    """导出前把正文引用标记 [n] 转为 pandoc 原生上标 ^[n]^（视觉尾注）。

    GFM 无上标语法、pandoc docx writer 丢弃内联 HTML <sup>，故两端
    各按引擎转换；本函数仅用于导出链路，不改写 content.md 本体。
    """
    return _CITE_SUP_RE.sub(lambda m: f"^[{m.group(1)}]^", markdown_text)


def render_markdown_to_docx(
    markdown_text: str,
    output_path: str,
    title: str = "报告",
    orientation: str = "portrait",
    timeout: float | None = None,
) -> str:
    """将 Markdown 报告渲染为 Word 文档（pandoc）。

    Args:
        markdown_text: Markdown 格式的报告文本（含 # 标题层级）
        output_path: 输出 docx 文件路径
        title: 文档标题（仅用于日志观测，pandoc 不注入正文——报告自身
               已含 `# {报告标题}` 一级标题，注入元数据标题会重复）
        orientation: 页面方向 portrait | landscape（导出面板选择，决定
                     reference-doc 样式模板；非法值回退 portrait）
        timeout: pandoc 子进程超时秒数（None = _PANDOC_TIMEOUT 默认；
                 知识册导出传更大值——整册拼接页数多，CodeReview M-3）

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

    # markdown_text 写随机名临时文件（pandoc CLI 接受文件输入；固定名在并发
    # 导出下互踩——to_thread 下放后允许并发）。图形块（mermaid / 内联 svg）
    # 先经公共桥转 PNG（失败保留原样，不阻断导出）
    reference_doc = resolve_reference_doc(orientation)
    fd, tmp_md_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp.md", dir=str(output.parent))
    os.close(fd)
    tmp_md = Path(tmp_md_name)
    mmd_tmp = None
    # pandoc 先写 .tmp 隔离产物：超时/失败时 zip 半写必损坏，不能留最终路径
    final_output = output.with_suffix(output.suffix + ".tmp")
    try:
        tmp_md.write_text(markdown_text, encoding="utf-8")
        actual_md, mmd_tmp = preprocess_mermaid(tmp_md)
        cmd = [pandoc, str(actual_md), "-o", str(final_output),
               "--from", "markdown", "--to", "docx"]
        if reference_doc:
            cmd += ["--reference-doc", reference_doc]
        result = subprocess.run(
            cmd,
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=_PANDOC_TIMEOUT if timeout is None else timeout,
            **hidden_window_kwargs(),  # hidden_window_kwargs：无控制台父进程下 pandoc 闪窗（2026-10-28）
        )
        if result.returncode != 0:
            logger.error("pandoc md→docx failed (title=%s): %s", title, result.stderr.strip())
            raise RuntimeError(f"pandoc 转换失败: {result.stderr.strip()}")
        os.replace(final_output, output)
    except Exception:
        # 超时（TimeoutExpired，pandoc 已被 kill）或失败：清理半写产物
        if final_output.exists():
            try:
                final_output.unlink()
            except OSError:
                pass
        raise
    finally:
        if tmp_md.exists():
            try:
                tmp_md.unlink()
            except OSError:
                pass
        cleanup_temp_dir(mmd_tmp)

    logger.info("Rendered docx via pandoc (title=%s): %s", title, output)
    return str(output)
