"""python/report_generator/renderer.py — Markdown → docx 渲染。

职责：
  - Markdown → docx 渲染（标题三层分离、引用转上标、尾注列表）

本阶段渲染目标格式为 Word（docx）。

设计参考：docs/2.5report-design.md §3.7 渲染规则
"""
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def render_markdown_to_docx(
    markdown_text: str,
    output_path: str,
    title: str = "报告",
) -> str:
    """将 Markdown 报告渲染为 Word 文档。

    渲染规则：
      - 一级标题 (#) → Heading 1
      - 二级标题 (##) → Heading 2
      - 三级标题 (###) → Heading 3
      - 四级标题 (####) → Heading 4（◆ 小标题转换后）
      - 引用 [n] → 上标格式
      - 尾注列表 → 脚注样式
      - 段落 → Normal 样式
      - 列表 → List 样式

    Args:
        markdown_text: Markdown 格式的报告文本
        output_path: 输出 docx 文件路径
        title: 文档标题

    Returns:
        输出文件路径
    """
    try:
        from docx import Document
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        logger.error("python-docx not installed. Install with: pip install python-docx")
        raise

    doc = Document()

    # 设置默认字体
    style = doc.styles["Normal"]
    font = style.font
    font.name = "宋体"
    font.size = Pt(11)

    # 设置标题样式
    for i in range(1, 5):
        heading_style = doc.styles[f"Heading {i}"]
        heading_style.font.name = "黑体"
        heading_style.font.size = Pt(18 - i * 2)  # 16, 14, 12, 10

    # 解析 Markdown 并构建 docx
    lines = markdown_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # 空行跳过
        if not line:
            i += 1
            continue

        # 分隔线
        if line.strip() == "---":
            i += 1
            continue

        # 标题
        heading_match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            p = doc.add_heading(text, level=level)
            i += 1
            continue

        # 列表项
        list_match = re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if list_match:
            text = list_match.group(2)
            p = doc.add_paragraph(style="List Bullet")
            _add_text_with_refs(p, text)
            i += 1
            continue

        # 有序列表
        ordered_match = re.match(r"^(\s*)\d+\.\s+(.+)$", line)
        if ordered_match:
            text = ordered_match.group(2)
            p = doc.add_paragraph(style="List Number")
            _add_text_with_refs(p, text)
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        _add_text_with_refs(p, line)
        i += 1

    # 保存
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output))
    logger.info("Rendered docx: %s", output)
    return str(output)


def _add_text_with_refs(paragraph, text: str):
    """向段落中添加文本，将 [n] 引用转为上标格式。"""
    from docx.shared import Pt

    # 分割文本：普通文本和单个引用标记交替
    parts = re.split(r"(\[\d+\])", text)
    for part in parts:
        if not part:
            continue
        ref_match = re.match(r"^\[(\d+)\]$", part)
        if ref_match:
            # 上标引用
            run = paragraph.add_run(f"[{ref_match.group(1)}]")
            run.font.superscript = True
            run.font.size = Pt(8)
        else:
            # 普通文本
            paragraph.add_run(part)
