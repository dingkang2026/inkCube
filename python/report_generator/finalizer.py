"""python/report_generator/finalizer.py — 全局收尾流水线。

职责（四步，均确定性程序修改，不依赖 LLM）：
  1. 引用全局校验 → 幻觉修复 → 未引用清理 + 重排
  2. ◆ 小标题转 Markdown 四级标题
  3. 衔接润色（规则驱动，各节首尾段轻量改写）
  4. 目录 + 尾注生成，组装最终报告

原「一致性检查（数字矛盾/章节重复）」已删除：跨节重复防护前移至
生成期全量 digests 注入（§3.5）；数字矛盾规则无法定真值、LLM 仲裁
成本与不确定性高，不划算（§3.7 设计决策注记）。

设计参考：docs/2.5report-design.md §3.7
"""
import logging
import re

from report_generator.registry import CitationRegistry
from report_generator.validator import validate_global, fix_hallucinated_refs, _extract_refs

logger = logging.getLogger(__name__)


def finalize_report(
    full_text: str,
    registry: CitationRegistry,
    template: dict,
) -> str:
    """执行全局收尾流水线。

    流程：
      ① 引用全局校验 → ② 幻觉修复 → 未引用清理 + 重排 → ③ ◆ 小标题转换
      → ④ 衔接润色 → ⑤ 尾注生成 → ⑥ 目录 + 组装最终输出

    Args:
        full_text: 拼接后的完整报告文本
        registry: 全局引用注册器
        template: 模板定义

    Returns:
        最终报告文本
    """
    text = full_text

    # ① 引用全局校验
    issues = validate_global(text, registry)
    for issue in issues:
        logger.info("Global issue: %s", issue.get("message", ""))

    # 修复幻觉引用
    hallucinated = [i for i in issues if i["type"] == "hallucinated_refs"]
    if hallucinated:
        valid_refs = set(registry.all_ref_numbers())
        text = fix_hallucinated_refs(text, valid_refs)
        logger.info("Fixed hallucinated references")

    # ② 未引用清理 + 重排
    # 先标记哪些被引用了
    cited_in_text = set(_extract_refs(text))
    registry.mark_cited(list(cited_in_text))
    removed = registry.remove_uncited()
    if removed:
        logger.info("Removed %d uncited references", len(removed))
        # 重排编号
        text = registry.renumber_and_remap(text)

    # ③ ◆ 小标题转换（◆ 小标题 → 下一级标题）
    text = _convert_subheads(text)

    # ④ 衔接润色（章节首尾轻量改写）
    text = _smooth_transitions(text)

    # ⑤ 生成尾注列表
    endnotes = _build_endnotes(registry)

    # ⑥ 组装最终报告
    report_title = template.get("template_name", "报告")
    parts = [
        f"# {report_title}",
        "",
        _build_toc(text),
        "",
        text,
        "",
        "---",
        "",
        "## 参考来源",
        "",
        endnotes,
    ]

    return "\n".join(parts)


def _convert_subheads(text: str) -> str:
    """将 ◆ 小标题转换为 Markdown 四级标题。"""
    def replace_subhead(m):
        title = m.group(1).strip()
        return f"#### {title}"

    return re.sub(r"^\s*◆\s*(.+?)\s*$", replace_subhead, text, flags=re.MULTILINE)


def _smooth_transitions(text: str) -> str:
    """章节首尾衔接润色（轻量版，规则驱动，不依赖 LLM）。

    处理项：
      - 清除悬空过渡词：节尾出现“如下：”“包括：”等但后面没有内容
      - 清除节首重复标题：节首重复了章节标题
      - 去除连续空行超过 2 行的情况
    """
    # 按章节分割
    section_parts = re.split(r"^(## .+)$", text, flags=re.MULTILINE)

    smoothed: list[str] = []
    for i, part in enumerate(section_parts):
        if i == 0:
            # 标题前的内容（如报告标题），原样保留
            smoothed.append(part)
            continue

        if i % 2 == 1:
            # 章节标题行
            smoothed.append(part)
        else:
            # 章节内容
            content = part

            # 规则 1：清除悬空过渡词（节尾“如下：”“包括：”等后面无内容）
            content = re.sub(
                r"[，,]?\s*(如下|包括|详述|展开|如下所示)\s*[：:]\s*$",
                "。",
                content.rstrip(),
            )

            # 规则 2：清除节首重复标题（节首第一行与上一节标题相同）
            lines = content.split("\n", 1)
            if lines:
                first_line = lines[0].strip()
                # 如果节首是重复的标题（去掉 ## 后与当前标题相同），跳过
                prev_title = section_parts[i - 1].replace("## ", "").strip() if i > 0 else ""
                if first_line == prev_title:
                    content = lines[1] if len(lines) > 1 else ""

            smoothed.append(content)

    text = "".join(smoothed)

    # 规则 3：连续空行压缩为最多 2 行
    text = re.sub(r"\n{3,}", "\n\n\n", text)

    return text


def _build_toc(text: str) -> str:
    """扫描装配后正文的 ## 章节标题，按出现顺序生成目录。

    目录顺序与正文严格一致：正文经 _assemble_full_text 重排
    （post_body(front) 前置），故不能按大纲原序生成。
    """
    lines = ["## 目录", ""]
    idx = 1
    for m in re.finditer(r"^## (.+)$", text, flags=re.MULTILINE):
        lines.append(f"{idx}. {m.group(1).strip()}")
        idx += 1
    return "\n".join(lines)


def _build_endnotes(registry: CitationRegistry) -> str:
    """构建尾注列表。"""
    entries = registry.all_entries()
    if not entries:
        return "（无引用来源）"

    lines = []
    for entry in entries:
        num = entry["ref_number"]
        source_title = entry.get("source_title", "未知来源")
        content = entry.get("content", "")

        # 截断展示内容
        if len(content) > 100:
            content = content[:100] + "…"

        lines.append(f"[{num}] 《{source_title}》: {content}")

    return "\n\n".join(lines)
