"""python/report_generator/prompts.py — Prompt 构建。

职责：
  1. System Prompt（全文共享，注入报告主体 / 报告深度）
  2. standard 章节 User Prompt
  3. post_body 章节 User Prompt
  4. 重试反馈注入
  5. 派生变量抽值 Prompt（extract_value，one-shot resolver 用）

设计参考：docs/2.5report-design.md §3.5 / §3.1.1
"""
import logging
from typing import Optional

from report_generator.registry import CitationRegistry

logger = logging.getLogger(__name__)


# ── System Prompt ────────────────────────────────────────────


SYSTEM_PROMPT_TEMPLATE = """\
你是一位{role_hint}，为机构客户撰写正式研究报告。

【报告场景】
本报告面向：{audience}
据此调整：深度、用语、结构偏好

【报告主体】
本报告为：{company_name}

【报告深度】
{depth_instruction}

【角色与文风】
- 专业、客观、克制；观点须有证据支撑，不使用夸张修辞
- 书面语，段落 150-300 字；分析性语言优先于描述性语言
- 给出判断时说明依据与不确定性
- 数字规范：千分位、单位统一、不确定数据用区间；数据须注明口径
- 行文用"本报告""我们认为"

【输出契约】（严格遵守）
1. 不输出本节章节标题，首行直接开始正文
2. 引用标注紧跟被支撑句末，格式 [n]，句内可多个如 [3][7]
3. 只能引用参考资料中列出的编号；无材料支撑的具体数据禁止出现
4. 列表仅用于并列枚举，连续不超过 5 项；论证性内容必须成段
5. 末尾不写总结套话，不以"综上所述""总而言之"收尾
6. 直接输出正文，不要任何前言说明
"""


# 报告深度 → 深度指令（§3.5）
_DEPTH_INSTRUCTIONS = {
    "full": "撰写详尽的长篇研究报告，每节充分展开，多角度分析，数据详实",
    "brief": "撰写精炼的短篇报告，每节 200-500 字，结论先行，言简意赅",
}


def build_system_prompt(
    role_hint: str = "资深研究分析师",
    audience: str = "机构客户",
    company_name: str = "",
    report_depth: str = "full",
) -> str:
    """构建全文共享的 System Prompt（§3.5）。

    Args:
        role_hint: 角色定位（如"投资分析师"）
        audience: 报告场景/读者
        company_name: 报告主体（隐式项目变量，来自 project.company_name）
        report_depth: 报告深度 "full" | "brief"，决定 depth_instruction

    Returns:
        System Prompt 文本
    """
    depth_instruction = _DEPTH_INSTRUCTIONS.get(report_depth, _DEPTH_INSTRUCTIONS["full"])
    return SYSTEM_PROMPT_TEMPLATE.format(
        role_hint=role_hint,
        audience=audience,
        company_name=company_name or "（未指定报告主体）",
        depth_instruction=depth_instruction,
    )


# ── standard 章节 User Prompt ────────────────────────────────


def build_standard_prompt(
    section: dict,
    chapter_title: str,
    report_title: str,
    completed_digests: dict[str, str],
    section_order: list[str],
    ref_text: str,
    ref_numbers: list[int],
    global_requirements: Optional[list[str]] = None,
    is_degraded: bool = False,
    sufficiency: Optional[dict] = None,
) -> str:
    """构建 standard 章节的 User Prompt。

    Args:
        section: 归一化后的节定义
        chapter_title: 所属章标题
        report_title: 报告题目
        completed_digests: 已完成节的摘要 {section_id: digest_text}
        section_order: 节的执行顺序（用于展示前文摘要）
        ref_text: 格式化后的参考资料文本（知识卡片或原始片段）
        ref_numbers: 本节给定的引用编号列表
        global_requirements: 全局写作要求
        is_degraded: 是否为降级模式（无检索材料）
        sufficiency: 充足性评估 {"level": "sufficient"|"insufficient", "reason": str}

    Returns:
        User Prompt 文本
    """
    # 前文摘要（最近 3 节 + 全局一页纸摘要）
    digest_lines = []
    recent = [sid for sid in section_order if sid in completed_digests][-3:]
    for sid in recent:
        digest_lines.append(f"- {sid}: {completed_digests[sid]}")
    digest_text = "\n".join(digest_lines) if digest_lines else "（无前文）"

    # 写作要求（§3.5：prompt_hint + effective_requirements）
    requirements = []
    prompt_hint = section.get("prompt_hint", "")
    if prompt_hint:
        requirements.append(prompt_hint)
    desc = section.get("description", "")
    if desc:
        requirements.append(desc)
    # 多级大纲折叠产生的写作要求（§3.2 归一化注入）
    for wr in section.get("writing_requirements", []):
        if wr and wr not in requirements:
            requirements.append(wr)
    if global_requirements:
        requirements.extend(global_requirements)
    req_text = "\n".join(f"- {r}" for r in requirements) if requirements else "无特殊要求"

    # 降级模式
    degraded_hint = ""
    if is_degraded:
        degraded_hint = """
⚠️ 【分析推理模式】当前无检索材料，请基于专业知识论述。
- 禁止虚构具体数据与引用
- 禁止编造引用编号
- 报告中将标注数据缺口
"""

    # 充足性提示
    sufficiency_hint = ""
    if sufficiency and sufficiency.get("level") == "insufficient":
        reason = sufficiency.get("reason", "")
        sufficiency_hint = (
            "\n⚠️ 【信息充足性】本节检索材料有限"
            + (f"（{reason}）" if reason else "")
            + "，侧重分析推理，禁止虚构具体数据与引用"
        )

    # 组装
    parts = [
        f"【全局上下文】",
        f"报告题目：{report_title}",
        f"本节位置：第 {section.get('section_id', '?')} 节「{section.get('title', '')}」（隶属 {chapter_title}）",
        f"前文各节摘要（供衔接，勿重复）：",
        digest_text,
        "",
        f"【本节写作任务】",
        f"写作要求：",
        req_text,
        degraded_hint,
        sufficiency_hint,
    ]

    if not is_degraded and ref_text:
        parts.extend([
            "",
            f"【参考资料】",
            ref_text,
        ])

    return "\n".join(parts)


# ── post_body 章节 User Prompt ──────────────────────────────


def build_post_body_prompt(
    section: dict,
    digests: dict[str, str],
    section_order: list[str],
    key_data_text: str,
    registry: CitationRegistry,
    global_requirements: Optional[list[str]] = None,
) -> str:
    """构建 post_body 章节的 User Prompt。

    Args:
        section: 归一化后的节定义
        digests: 各节摘要 {section_id: digest_text}
        section_order: 节的执行顺序
        key_data_text: extract_numbers 提取的关键数据表
        registry: 全局引用注册器
        global_requirements: 全局写作要求

    Returns:
        User Prompt 文本
    """
    # 全文摘要视图
    digest_lines = []
    for sid in section_order:
        if sid in digests:
            digest_lines.append(f"- {sid}: {digests[sid]}")
    digest_text = "\n".join(digest_lines) if digest_lines else "（无正文内容）"

    # 写作要求（§3.5：prompt_hint + effective_requirements）
    requirements = []
    prompt_hint = section.get("prompt_hint", "")
    if prompt_hint:
        requirements.append(prompt_hint)
    for wr in section.get("writing_requirements", []):
        if wr and wr not in requirements:
            requirements.append(wr)
    if global_requirements:
        requirements.extend(global_requirements)
    req_text = "\n".join(f"- {r}" for r in requirements) if requirements else "无特殊要求"

    # 可用引用
    source_list = registry.format_source_list()

    parts = [
        f"【全文摘要视图】",
        digest_text,
        "",
        f"【全文关键数据表】",
        key_data_text or "（无提取数据）",
        "",
        f"【本节写作任务】",
        f"写作要求：",
        req_text,
        f"附加要求：不得简单重复正文内容，须给出跨章节的综合判断",
        "",
        f"【可用引用编号】",
        source_list or "（无可用引用）",
    ]

    return "\n".join(parts)


# ── 辅助：摘要生成 Prompt ───────────────────────────────────


def build_digest_prompt(text: str, max_words: int = 200) -> list[dict]:
    """构建用于生成节摘要的 messages。

    Args:
        text: 章节全文
        max_words: 摘要目标字数

    Returns:
        OpenAI messages 列表
    """
    return [
        {"role": "system", "content": "你是一位专业的文本摘要助手。请为以下报告章节生成简洁的摘要。"},
        {"role": "user", "content": f"请用不超过 {max_words} 字概括以下内容的核心要点和关键数据：\n\n{text}"},
    ]


def build_extract_numbers_prompt(text: str) -> list[dict]:
    """构建用于提取关键数据的 messages。

    Args:
        text: 章节全文

    Returns:
        OpenAI Messages 列表
    """
    return [
        {"role": "system", "content": "你是一位专业的数据提取助手。请从文本中提取关键数据点。"},
        {"role": "user", "content": f"""请从以下报告文本中提取所有关键数据点，格式为：
- 指标名称: 数值（来源引用编号）

只提取有具体数值的数据点，忽略定性描述。

{text}"""},
    ]


def build_extract_value_prompt(
    derive_hint: str,
    deps: dict,
    evidence_text: str,
    cardinality: str = "scalar",
) -> list[dict]:
    """构建派生变量抽值的 messages（one-shot resolver，§3.1.1）。

    复用 extract_numbers 的「LLM → 结构化值」机制。抽值契约：
      - scalar：输出一个简洁的名词短语字符串（单个值）
      - list：输出 JSON 字符串数组，如 ["甲", "乙"]

    Args:
        derive_hint: 派生变量的自然语言解析指令
        deps: 已解析的上游依赖 {name: value}
        evidence_text: 检索证据（知识卡片拼接文本）
        cardinality: "scalar" | "list"

    Returns:
        OpenAI messages 列表
    """
    deps_lines = "\n".join(
        f"- {k}: {v}" for k, v in deps.items() if v is not None
    )
    deps_text = deps_lines or "（无上游依赖）"

    if cardinality == "list":
        system = "你是一位专业的信息抽取助手。请从证据中抽取满足要求的列表型结果。"
        output_contract = (
            '输出一个 JSON 字符串数组，列出所有符合条件的项，形如 ["项1", "项2", "项3"]。'
            "只输出 JSON 数组本身，不要任何解释或多余文本。若证据不足则输出空数组 []。"
        )
    else:
        system = "你是一位专业的信息抽取助手。请从证据中抽取满足要求的单个值。"
        output_contract = (
            "输出一个简洁的名词短语字符串（单个值），只输出该值本身，"
            "不要任何解释、标点包裹或前后缀。若证据不足则输出空字符串。"
        )

    user = f"""## 抽取目标
{derive_hint}

## 已知上游变量
{deps_text}

## 检索证据
{evidence_text or "（无证据）"}

## 输出要求
{output_contract}"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
