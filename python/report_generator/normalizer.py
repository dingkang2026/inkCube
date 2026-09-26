"""python/report_generator/normalizer.py — 模板归一化层。

职责：
  1. 模板变量实例化（{{variable}} → 实际值）
  2. 多级大纲归一化（任意层级折叠为两级执行大纲：章 → 节）
  3. 执行大纲入口校验

设计参考：docs/2.1project-design.md §6.7.2 / §6.7.11
"""
import json
import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


# ── 变量实例化 ─────────────────────────────────────────────


def instantiate(template: dict, variables: dict[str, str]) -> dict:
    """将模板中的 {{variable}} 占位符替换为实际值。

    Args:
        template: 完整模板 dict（含 outline）
        variables: 用户填写的变量值，如 {"公司名": "XX科技"}

    Returns:
        替换后的模板 dict（深拷贝，不修改原对象）

    Raises:
        ValueError: 模板引用了 variables 中未声明的变量
    """
    result = deepcopy(template)
    declared = set(result.get("variables", []))

    # 校验：变量引用必须在 variables 中声明
    text = json.dumps(result, ensure_ascii=False)
    import re
    referenced = set(re.findall(r"\{\{(\w+)\}\}", text))
    undeclared = referenced - declared
    if undeclared:
        raise ValueError(
            f"模板引用了未声明的变量: {', '.join(sorted(undeclared))}。"
            f"请在模板 variables 中声明，或在用户输入中提供。"
        )

    # 执行替换
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        text = text.replace(placeholder, value)

    return json.loads(text)


# ── 大纲归一化 ─────────────────────────────────────────────


def normalize_outline(outline: list[dict]) -> list[dict]:
    """将多级大纲折叠为两级执行大纲（章 → 节）。

    折叠规则（整枝合并，不拆散）：
      - 深层节点标题 → "须覆盖：标题——子孙标题"
      - notes → 括号附加
      - query_hints → 上浮合并去重
      - word_count → 累加
      - must_cite → 上浮 OR 合并
      - promote_subhead: true → 附加"并以内部小标题形式呈现"
      - 折叠后 requirements > 12 条 → 告警日志

    Args:
        outline: 原始多级大纲（支持任意层级嵌套）

    Returns:
        两级执行大纲（每章只有 children 作为节，不再有更深嵌套）
    """
    result = deepcopy(outline)
    for chapter in result:
        children = chapter.get("children", [])
        if not children:
            # 章本身作为唯一节（无子节时）
            continue
        for sec in children:
            _flatten_section(sec)
    return result


def _flatten_section(sec: dict) -> None:
    """就地折叠一个节的 children（三级及更深 → 二级）。"""
    reqs = sec.setdefault("writing_requirements", [])
    hints = sec.setdefault("query_hints", [])
    sub_children = sec.pop("children", [])

    for sub in sub_children:
        # 递归折叠整枝为一条写作要求
        r = _fold_node(sub)
        if r and r not in reqs:
            reqs.append(r)

        # query_hints 上浮去重
        for h in sub.get("query_hints", []):
            if h not in hints:
                hints.append(h)

        # word_count 累加
        sec["word_count"] = sec.get("word_count", 1200) + sub.get("word_count", 0)

        # must_cite 上浮 OR
        if sub.get("must_cite"):
            sec["must_cite"] = True

        # data_sources 上浮合并去重
        sub_sources = sub.get("data_sources", [])
        if sub_sources:
            sec_sources = sec.setdefault("data_sources", [])
            for ds in sub_sources:
                if ds not in sec_sources:
                    sec_sources.append(ds)

        # notes 上浮
        for n in sub.get("notes", []):
            if n not in reqs:
                reqs.append(n)

    # 如果没有任何 writing_requirements，添加默认
    if not reqs and sec.get("title"):
        reqs.append(f"须覆盖：{sec['title']}")

    # 告警：requirements 过多
    if len(reqs) > 12:
        logger.warning(
            "Section '%s' has %d writing requirements (>12), "
            "consider simplifying the outline.",
            sec.get("id", "?"), len(reqs),
        )


def _fold_node(node: dict) -> str:
    """递归折叠一个子树为一条写作要求字符串。"""
    chain = [node.get("title", "")]
    chain.extend(_collect_titles(node.get("children", [])))
    r = f"须覆盖：{'——'.join(chain)}"

    notes = node.get("notes", [])
    if notes:
        r += f"（{'；'.join(notes)}）"

    if node.get("promote_subhead"):
        r += "，并以内部小标题形式呈现"

    return r


def _collect_titles(nodes: list[dict]) -> list[str]:
    """递归收集所有层级的标题。"""
    titles = []
    for n in nodes:
        titles.append(n.get("title", ""))
        titles.extend(_collect_titles(n.get("children", [])))
    return titles


# ── 执行大纲校验 ────────────────────────────────────────────


def validate_execution_outline(outline: list[dict]) -> list[str]:
    """校验归一化后的执行大纲是否可执行。

    Returns:
        错误信息列表。空列表表示通过。
    """
    errors: list[str] = []
    seen_ids: set[str] = set()

    for chapter in outline:
        ch_id = chapter.get("id", "")
        if not ch_id:
            errors.append("Chapter missing 'id'")

        children = chapter.get("children", [])
        if not children:
            # 章本身作为节
            _validate_section_fields(chapter, seen_ids, errors)
        else:
            for sec in children:
                _validate_section_fields(sec, seen_ids, errors)

    return errors


def _validate_section_fields(sec: dict, seen_ids: set[str], errors: list[str]):
    """校验单个节的必填字段。"""
    sid = sec.get("id", "")
    if not sid:
        errors.append("Section missing 'id'")
    elif sid in seen_ids:
        errors.append(f"Duplicate section id: {sid}")
    else:
        seen_ids.add(sid)

    if not sec.get("title"):
        errors.append(f"Section '{sid}' missing 'title'")

    mode = sec.get("compose_mode", "standard")
    if mode not in ("standard", "post_body"):
        errors.append(f"Section '{sid}' invalid compose_mode: {mode}")

    # post_body 不得有子章节
    if mode == "post_body" and sec.get("children"):
        errors.append(f"post_body section '{sid}' must not have children")


# ── 辅助：获取执行大纲的节列表 ──────────────────────────────


def get_execution_sections(outline: list[dict]) -> list[dict]:
    """从归一化大纲中提取所有可执行节（扁平列表）。

    对于有 children 的章，返回其 children；
    对于无 children 的章，返回章本身。
    """
    sections = []
    for chapter in outline:
        children = chapter.get("children", [])
        if children:
            for sec in children:
                sec["_chapter_title"] = chapter.get("title", "")
                sections.append(sec)
        else:
            chapter["_chapter_title"] = chapter.get("title", "")
            sections.append(chapter)
    return sections
