"""python/report_generator/normalizer.py — 模板归一化层。

职责：
  1. 模板变量实例化（{{variable}} → 实际值；list 顿号连接注入）
  2. 多级大纲归一化（任意层级折叠为两级执行大纲：章 → 节）
  3. 执行大纲入口校验

设计参考：docs/2.5report-design.md §3.1.1 / §3.2
"""
import json
import logging
import re
from copy import deepcopy

logger = logging.getLogger(__name__)


# ── 变量实例化 ─────────────────────────────────────────────


def instantiate(template: dict, variables: dict) -> dict:
    """将模板中的 {{variable}} 占位符替换为实际值（§3.9 管线第 2 步）。

    variables 为 resolve_variables 产出的 resolved_variables（含隐式项目变量、
    user 输入、derived 解析值）；UNRESOLVED 值以 None 表示，替换为空串；
    list 值以顿号连接为字符串注入（如竞对名单「A、B、C」）。

    大纲节点数量恒定，不因 list 长度增删节（不做 foreach 展开）；1/2 级结构
    由模板静态定义，三级及更深由 normalize_outline 折叠为写作要求。

    Args:
        template: 完整模板 dict（含 outline）
        variables: 已解析变量值 {name: scalar | list | None}

    Returns:
        替换后的模板 dict（深拷贝，不修改原对象）

    Raises:
        ValueError: 模板引用了未声明的变量
    """
    result = deepcopy(template)

    # declared 集合 = resolved 变量名（隐式 + user + derived）
    declared = set(variables.keys())

    # 校验：变量引用必须已声明
    text = json.dumps(result, ensure_ascii=False)
    referenced = set(re.findall(r"\{\{(\w+)\}\}", text))
    undeclared = referenced - declared
    if undeclared:
        raise ValueError(
            f"模板引用了未声明的变量: {', '.join(sorted(undeclared))}。"
            f"请在模板 variables 中声明，或在用户输入中提供。"
        )

    # 执行替换（list → 顿号连接为字符串；dict 跳过；None → 空串，避免正文残留 {{}}）
    for key, value in variables.items():
        if isinstance(value, dict):
            continue
        placeholder = "{{" + key + "}}"
        if placeholder not in text:
            continue
        if isinstance(value, list):
            # list 型派生变量（如竞对名单）以顿号连接注入正文/提示词
            replacement = "、".join(str(x) for x in value if x is not None and str(x) != "")
        else:
            replacement = "" if value is None else str(value)
        # 转义为 JSON 字符串上下文安全值，防止引号/换行破坏 round-trip
        safe = json.dumps(replacement, ensure_ascii=False)[1:-1]
        text = text.replace(placeholder, safe)

    return json.loads(text)


# ── 大纲归一化 ─────────────────────────────────────────────


def normalize_outline(outline: list[dict]) -> list[dict]:
    """将多级大纲折叠为两级执行大纲（章 → 节，§3.2）。

    折叠规则（整枝合并，不拆散）：
      - 深层节点标题 → "须覆盖：标题——子孙标题"
      - 深层节点 prompt_hint → 括号附加
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
    """就地折叠一个节的 children（三级及更深 → 二级，§3.2）。

    v2 节点已无 word_count/must_cite/notes/query_hints/data_sources/promote_subhead
    （schema additionalProperties:false），仅折叠 children 标题链 + prompt_hint。
    """
    reqs = sec.setdefault("writing_requirements", [])
    sub_children = sec.pop("children", [])

    for sub in sub_children:
        # 递归折叠整枝为一条写作要求
        r = _fold_node(sub)
        if r and r not in reqs:
            reqs.append(r)

    # 如果没有任何 writing_requirements，添加默认
    if not reqs and sec.get("title"):
        reqs.append(f"须覆盖：{sec['title']}")

    # 告警：requirements 过多
    if len(reqs) > 12:
        logger.warning(
            "Section '%s' has %d writing requirements (>12), "
            "consider simplifying the outline.",
            sec.get("section_id", "?"), len(reqs),
        )


def _fold_node(node: dict) -> str:
    """递归折叠一个子树为一条写作要求字符串（§3.2）。"""
    chain = [node.get("title", "")]
    chain.extend(_collect_titles(node.get("children", [])))
    r = f"须覆盖：{'——'.join(chain)}"

    # v2 深层节点的 writing_brief 任务书整段并入（换行压缩为分号，
    # 保持 writing_requirements 单条单行形态；行尾已带分号时剥除，
    # 避免压缩出「；；」，空行过滤）
    brief = (node.get("writing_brief") or "").strip()
    if brief:
        lines = [ln.strip().rstrip("；;")
                 for ln in brief.replace("\r\n", "\n").split("\n")]
        r += "：" + "；".join(ln for ln in lines if ln)

    # v2 用 prompt_hint 作为附加说明（替代 v1 的 notes / promote_subhead）
    prompt_hint = node.get("prompt_hint", "")
    if prompt_hint:
        r += f"（{prompt_hint}）"

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
        ch_id = chapter.get("section_id", "")
        if not ch_id:
            errors.append("Chapter missing 'section_id'")

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
    sid = sec.get("section_id", "")
    if not sid:
        errors.append("Section missing 'section_id'")
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
