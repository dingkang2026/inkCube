"""python/report_generator/prompts.py — Prompt 构建。

职责：
  1. System Prompt（全文共享）
  2. standard 章节 User Prompt
  3. post_body 章节 User Prompt
  4. 重试反馈注入

设计参考：docs/2.1project-design.md §6.7.5
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

【角色与文风】
- 专业、客观、克制；观点须有证据支撑，不使用夸张修辞
- 书面语，段落 150-300 字；分析性语言优先于描述性语言
- 给出判断时说明依据与不确定性
- 术语首次出现用全称，此后用简称：{terminology_text}
- 数字规范：千分位、单位统一、不确定数据用区间；数据须注明口径
- 行文用"本报告""我们认为"

{subhead_rules}

【输出契约】（严格遵守）
1. 不输出本节章节标题，首行直接开始正文
2. 引用标注紧跟被支撑句末，格式 [n]，句内可多个如 [3][7]
3. 只能引用参考资料中列出的编号；无材料支撑的具体数据禁止出现
4. 列表仅用于并列枚举，连续不超过 5 项；论证性内容必须成段
5. 末尾不写总结套话，不以"综上所述""总而言之"收尾
6. 目标字数 ±20% 以内；直接输出正文，不要任何前言说明
"""


def build_system_prompt(
    role_hint: str = "资深研究分析师",
    audience: str = "机构客户",
) -> str:
    """构建全文共享的 System Prompt。

    Args:
        role_hint: 角色定位（如"投资分析师"）
        audience: 报告场景/读者

    Returns:
        System Prompt 文本
    """
    return SYSTEM_PROMPT_TEMPLATE.format(
        role_hint=role_hint,
        audience=audience,
        terminology_text="无特殊术语规范",
        subhead_rules="",
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
    retry_feedback: Optional[str] = None,
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
        retry_feedback: 重试反馈（上次违规信息）
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

    # 写作要求
    requirements = []
    prompt_hint = section.get("prompt_hint", "")
    if prompt_hint:
        requirements.append(prompt_hint)
    desc = section.get("description", "")
    if desc:
        requirements.append(desc)
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

    if retry_feedback:
        parts.extend([
            "",
            retry_feedback,
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
    retry_feedback: Optional[str] = None,
) -> str:
    """构建 post_body 章节的 User Prompt。

    Args:
        section: 归一化后的节定义
        digests: 各节摘要 {section_id: digest_text}
        section_order: 节的执行顺序
        key_data_text: extract_numbers 提取的关键数据表
        registry: 全局引用注册器
        global_requirements: 全局写作要求
        retry_feedback: 重试反馈

    Returns:
        User Prompt 文本
    """
    # 全文摘要视图
    digest_lines = []
    for sid in section_order:
        if sid in digests:
            digest_lines.append(f"- {sid}: {digests[sid]}")
    digest_text = "\n".join(digest_lines) if digest_lines else "（无正文内容）"

    # 写作要求
    requirements = []
    prompt_hint = section.get("prompt_hint", "")
    if prompt_hint:
        requirements.append(prompt_hint)
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

    if retry_feedback:
        parts.extend([
            "",
            retry_feedback,
        ])

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
