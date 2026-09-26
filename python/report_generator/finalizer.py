"""python/report_generator/finalizer.py — 全局收尾流水线。

职责：
  1. 引用全局校验 → 未引用清理 + 重排
  2. 一致性检查（术语统一、数字矛盾、章节间重复）
  3. 衔接润色（各节首尾段轻量改写）
  4. 目录生成
  5. 组装最终报告

设计参考：docs/2.1project-design.md §6.7.7
"""
import logging
import re
from typing import Optional

from report_generator.registry import CitationRegistry
from report_generator.validator import validate_global, fix_hallucinated_refs, _extract_refs

logger = logging.getLogger(__name__)


def finalize_report(
    full_text: str,
    registry: CitationRegistry,
    template: dict,
    outline: list[dict],
) -> str:
    """执行全局收尾流水线。

    流程：
      ① 引用全局校验 → ② 未引用清理 + 重排 → ③ 一致性检查
      → ④ 衔接润色 → ⑤ 目录生成 → ⑥ 最终输出

    Args:
        full_text: 拼接后的完整报告文本
        registry: 全局引用注册器
        template: 模板定义
        outline: 归一化后的执行大纲

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

    # ③ 一致性检查
    text = _consistency_check(text, template)

    # ④ 标题层级处理（◆ 小标题 → 下一级标题）
    text = _convert_subheads(text)

    # ④b 衔接润色（章节首尾轻量改写）
    text = _smooth_transitions(text)

    # ⑤ 生成尾注列表
    endnotes = _build_endnotes(registry)

    # ⑥ 组装最终报告
    report_title = template.get("template_name", "报告")
    parts = [
        f"# {report_title}",
        "",
        _build_toc(outline),
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


def _consistency_check(text: str, template: dict) -> str:
    """一致性检查（轻量版，不依赖 LLM）。

    检查项：
      - 术语统一：通过 terminology aliases 检测同一概念的不同表述
      - 数字矛盾：检测同一指标在不同章节出现不同数值
      - 章节间重复：检测相邻章节是否存在高度相似的段落
    """
    # 术语统一
    terminology = template.get("terminology", [])
    if terminology:
        for term in terminology:
            canonical = term.get("canonical", "")
            aliases = term.get("aliases", [])
            if not canonical or not aliases:
                continue
            found_aliases = [a for a in aliases if a in text]
            if found_aliases:
                logger.info(
                    "Terminology: '%s' has aliases %s in text. "
                    "First occurrence should use canonical form.",
                    canonical, found_aliases,
                )

    # 数字矛盾检测
    _check_number_contradictions(text)

    # 章节间重复检测
    _check_section_duplication(text)

    return text


def _check_number_contradictions(text: str) -> None:
    """检测同一指标在不同章节出现不同数值。

    提取模式：数字 + 单位（亿元/万元/%/年/月 等），附带前后 10 字上下文作为指标指纹。
    当同一上下文出现不同数值时，日志告警。
    """
    # 匹配：数字（含小数、千分位）+ 单位
    number_pattern = re.compile(
        r"([\d,]+(?:\.\d+)?)\s*(亿元|万元|百亿|千亿|万亿|元|%|个百分点|年|月|日|家|个|项|件|套|台|辆)"
    )

    # 按章节分割（## 标题）
    sections = re.split(r"^## .+$", text, flags=re.MULTILINE)

    # 收集每个“指标上下文 → {数值: [章节索引]}”
    metric_map: dict[str, dict[str, list[int]]] = {}

    for sec_idx, section in enumerate(sections):
        for m in number_pattern.finditer(section):
            value = m.group(1).replace(",", "")
            unit = m.group(2)
            # 取前后 15 字作为上下文指纹
            start = max(0, m.start() - 15)
            end = min(len(section), m.end() + 15)
            context = section[start:end].strip()
            # 去除数字部分，保留上下文骨架
            key = number_pattern.sub("{N}", context)
            metric_key = f"{key}({unit})"

            if metric_key not in metric_map:
                metric_map[metric_key] = {}
            if value not in metric_map[metric_key]:
                metric_map[metric_key][value] = []
            if sec_idx not in metric_map[metric_key][value]:
                metric_map[metric_key][value].append(sec_idx)

    # 检测矛盾：同一指标有多个不同数值
    for metric, values in metric_map.items():
        if len(values) > 1:
            detail = ", ".join(f"{v} (章节{idxs})" for v, idxs in values.items())
            logger.warning(
                "Number contradiction: '%s' has different values: %s",
                metric, detail,
            )


def _check_section_duplication(text: str, threshold: float = 0.6) -> None:
    """检测相邻章节是否存在高度相似的段落。

    方法：按 ## 标题分割章节，提取每节的段落集合，
    用字符级 bigram Jaccard 相似度检测相邻章节的重复段落。
    """
    # 按 ## 标题分割
    section_parts = re.split(r"^(## .+)$", text, flags=re.MULTILINE)

    # 组装 (标题, 内容) 对
    sections: list[tuple[str, str]] = []
    i = 1  # 跳过第一个元素（标题前的内容）
    while i < len(section_parts):
        title = section_parts[i].strip()
        content = section_parts[i + 1] if i + 1 < len(section_parts) else ""
        sections.append((title, content))
        i += 2

    if len(sections) < 2:
        return

    def _bigrams(t: str) -> set[str]:
        """提取字符级 bigram 集合。"""
        t = re.sub(r"\s+", "", t)
        return {t[j:j+2] for j in range(len(t) - 1)} if len(t) >= 2 else {t}

    def _jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    # 检测相邻章节的段落级重复
    for idx in range(len(sections) - 1):
        title_a, content_a = sections[idx]
        title_b, content_b = sections[idx + 1]

        # 按段落分割
        paras_a = [p.strip() for p in content_a.split("\n\n") if len(p.strip()) > 50]
        paras_b = [p.strip() for p in content_b.split("\n\n") if len(p.strip()) > 50]

        for pa in paras_a:
            bg_a = _bigrams(pa)
            for pb in paras_b:
                bg_b = _bigrams(pb)
                sim = _jaccard(bg_a, bg_b)
                if sim >= threshold:
                    logger.warning(
                        "Section duplication: '%s' and '%s' have similar paragraphs "
                        "(Jaccard=%.2f, threshold=%.2f)",
                        title_a, title_b, sim, threshold,
                    )
                    return  # 每对章节只报一次


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


def _build_toc(outline: list[dict]) -> str:
    """从大纲生成目录。"""
    lines = ["## 目录", ""]
    idx = 1
    for chapter in outline:
        title = chapter.get("title", "")
        lines.append(f"{idx}. {title}")
        children = chapter.get("children", [])
        if children:
            for sec in children:
                sec_title = sec.get("title", "")
                lines.append(f"   - {sec_title}")
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
        # 知识卡片用 source_title，旧格式用 doc_name
        doc_name = entry.get("source_title") or entry.get("doc_name", "未知文档")
        page = entry.get("page")
        content = entry.get("content", "")

        # 截断展示内容
        if len(content) > 100:
            content = content[:100] + "…"

        page_str = f", p.{page}" if page else ""
        lines.append(f"[{num}] 《{doc_name}》{page_str}: {content}")

    return "\n\n".join(lines)
