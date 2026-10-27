"""python/report_generator/finalizer.py — 全局收尾流水线。

职责（四步，均确定性程序修改，不依赖 LLM）：
  1. 引用全局校验 → 幻觉修复 → 未引用清理 + 重排
  2. ◆ 小标题转下一级标题（上下文感知：无子节章内→H3，子节内→H4）
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
    """将 ◆ 小标题转换为下一级标题（上下文感知，§3.5，2026-10-27）。

    装配层两级化后标题形态：`## n 章题`（无子节章）与
    `### n.m 节题`（有子节章的子节）。◆ 行按最近标题上下文转换：
      - 章上下文（无子节章，旧形态兼容）→ `### n.k 节题`
      - 子节上下文 → `#### n.m.k 小节题`（避免与装配注入的子节
        编号冲突）；子节号遇新章/新子节重置
    """
    pattern = re.compile(r"^(## .+|### .+|[ \t\u3000]*◆\s*.+)$", re.MULTILINE)
    state = {"prefix": "", "sub": 0}

    def repl(m: re.Match) -> str:
        line = m.group(1)
        if line.startswith("## "):
            num = re.match(r"^## (\d+)[\s\u3000]", line)
            state["prefix"] = num.group(1) if num else ""
            state["sub"] = 0
            return line
        if line.startswith("### "):
            num = re.match(r"^### (\d+\.\d+)[\s\u3000]", line)
            if num:
                state["prefix"] = num.group(1)
                state["sub"] = 0
            return line
        state["sub"] += 1
        title = line.strip().lstrip("◆").strip()
        prefix = state["prefix"] or "0"
        if "." in prefix:
            return f"#### {prefix}.{state['sub']} {title}"
        return f"### {prefix}.{state['sub']} {title}"

    return pattern.sub(repl, text)


def _smooth_transitions(text: str) -> str:
    """章节首尾衔接润色（轻量版，规则驱动，不依赖 LLM）。

    处理项：
      - 清除悬空过渡词：节尾出现“如下：”“包括：”等但后面没有内容
      - 去除连续空行超过 2 行的情况

    （节首重复标题剥离已上移至装配层 _assemble_full_text：该处持有
    节题上下文逐节执行，2026-10-27 两级装配时迁移。）

    结构不变量：## 标题必须独立成行。re.split 的标题捕获组（$）不含
    行尾换行（由下一内容块开头的换行提供），内容块节尾的 --- 分隔符
    剥离后必须按标准形态（前后各一空行）重建，否则分隔符会与下一节
    标题粘成一行（---## xxx），连带破坏 _build_toc 的标题扫描
    （目录只剩首节）与 Markdown 渲染。
    """
    # 按章节分割：奇数位为标题捕获组（不含行尾换行），偶数位为章节内容
    # （以标题行尾换行开头；非末内容块以 “---” 分隔符结尾）
    section_parts = re.split(r"^(## .+)$", text, flags=re.MULTILINE)

    smoothed: list[str] = []
    for i, part in enumerate(section_parts):
        if i == 0:
            # 标题前的内容（如报告标题），原样保留
            smoothed.append(part)
            continue

        if i % 2 == 1:
            # 章节标题行：行尾换行由下一内容块开头提供，原样保留
            smoothed.append(part)
        else:
            # 章节内容：拆 头部换行 + 正文 + 尾部分隔符 三段处理
            head_nl = len(part) - len(part.lstrip("\n"))
            head = part[:head_nl]
            rest = part[head_nl:]

            # 剥离节尾 --- 分隔符（_assemble_full_text 拼接产物，
            # 非末内容块才有）：rstrip 只能剥到 --- 为止，若不剥离，
            # 规则 1 的 $ 永远锚在分隔符上，对中间节从不生效
            trailing = ""
            if rest.rstrip().endswith("---"):
                rest = rest.rstrip()[:-3].rstrip("\n")
                if i < len(section_parts) - 1:
                    trailing = "\n\n---\n\n"

            body = rest.rstrip("\n")

            # 规则 1：清除悬空过渡词（节尾“如下：”“包括：”等后面无内容）
            body = re.sub(
                r"[，,]?\s*(如下|包括|详述|展开|如下所示)\s*[：:]\s*$",
                "。",
                body,
            )

            smoothed.append(head + body + trailing)

    text = "".join(smoothed)

    # 规则 2：连续空行压缩为最多 2 行
    text = re.sub(r"\n{3,}", "\n\n\n", text)

    return text


def _build_toc(text: str) -> str:
    """扫描装配后正文的 ## 章节标题与 ### 子节标题，按出现顺序生成两级目录。

    目录顺序与正文严格一致：正文经 _assemble_full_text 重排
    （post_body(front) 前置），故不能按大纲原序生成。

    章条目保留 `idx.` 列表序号（Markdown 有序列表语法），标题文本剥去
    装配层注入的前导序号（`1 标题` → `标题`）避免双号，序号语义与正文
    标题编号同源一致；子节条目缩进挂靠章条目（`   - n.m 标题`），n.m
    直接采用装配层注入的编号（§3.5，2026-10-27 两级装配）。
    """
    lines = ["## 目录", ""]
    idx = 1
    for m in re.finditer(r"^(##|###) (.+)$", text, flags=re.MULTILINE):
        level, raw = m.group(1), m.group(2).strip()
        if level == "##":
            bare = re.sub(r"^\d+\s+", "", raw)
            lines.append(f"{idx}. {bare}")
            idx += 1
        else:
            num = re.match(r"^(\d+\.\d+)\s+", raw)
            if num:
                lines.append(f"   - {num.group(1)} {raw[num.end():]}")
            else:
                lines.append(f"   - {raw}")
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
