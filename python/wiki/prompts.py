"""python/wiki/prompts.py — Prompt templates for Wiki two-stage ingest.

Per §4.3 of 2.6wiki-design.md:
  Stage 1 (Analysis): Read full source text, plan pages, identify entities/concepts.
  Stage 2 (Generation): Produce PAGE blocks with YAML frontmatter + REVIEW blocks.

Domain role injection (E2-2026-10): system prompts are prefixed with a
project-type-specific role profile (recruitment / investment) so that
analysis and generation anchor on the recruitment / investment domain
instead of generic knowledge-compilation. general keeps the original
generic role.
"""


# Domain role profiles per project type (§4.3 of 2.6wiki-design.md).
# Injected at the start of both stages' system prompt. general → no override.
_ROLE_PROFILES: dict[str, str] = {
    "recruitment": (
        "你是园区招商引资顾问，服务企业全生命周期：线索评估 → 落地推进 → 投产后"
        "监管服务。分析材料时始终围绕：企业与本项目的匹配度、政策匹配与兑现、"
        "落地可行性、投产后经营与履约风险。"
    ),
    "investment": (
        "你是投资机构分析师，覆盖尽调 → 投决 → 投后 → 退出全流程。分析材料时"
        "始终围绕：投资价值与风险定价、财务真实性与健康度、条款保护（对赌/"
        "优先权）、投后监控信号与退出路径。"
    ),
}

# Append-only writing rule for ledger-style pages (监控页，§2.2 页面生命周期语义)
_LEDGER_RULE = (
    "7. compile_hint 中标注为「监控页/台账式」的页面：新事件追加到时间线末尾，"
    "禁止改写或删除历史条目；每条记录必须带日期/期间标注"
)

# Profile-page timeline rule (画像页状态变迁，§2.2/§4.3): time-varying facts in
# profile pages are kept per-period with explicit dates instead of being
# silently replaced by newer values — "last year's revenue vs this year's
# revenue" / "former vs current GM" must coexist with period labels.
_PROFILE_TIMELINE_RULE = (
    "8. 画像页中会随时间变化的信息（营收等经营/财务指标、实控人与股权结构、"
    "管理层任职、主营业务、资质荣誉等）：更新时按时期分段保留历史值、在其后"
    "补充新值，新旧值各标注数据时点或来源期间（如「2024 年营收 X（2024 年报）」）；"
    "禁止用新值静默替换旧值，人员/股权变动记录沿革与变更时间"
)


def _system_prefix(project_type: str) -> str:
    """Return the domain role prefix for a project type ('' for general/unknown)."""
    return _ROLE_PROFILES.get(project_type, "")


def build_ingest_analysis_prompt(
    source_text: str,
    filename: str,
    purpose: str,
    overview: str,
    index_summary: str,
    schema_hints: str,
    base_lib_context: str,
    project_type: str = "general",
) -> list[dict]:
    """Build messages for Stage 1 (Analysis) per §4.3.

    Stage 1 reads the full source text and outputs a structured analysis:
    key entities, concepts, recommended pages, contradictions, etc.
    Does NOT generate page content.
    """
    system = (
        _system_prefix(project_type)
        + "你是知识分析 Agent。你的任务是深入阅读一篇原始资料，输出结构化分析。\n\n"
        "## Wiki 目标（purpose.md）\n"
        f"{purpose or '（未设定）'}\n\n"
        "## Wiki 全局摘要（overview.md）\n"
        f"{overview or '（Wiki 刚建立，尚无摘要）'}\n\n"
        "## 页面类型规则\n"
        "- source: 为该文件创建来源摘要页，记录关键要点\n"
        "- entity: 识别文中的人、组织、产品、技术等具名实体\n"
        "- concept: 识别文中的概念、方法、理论\n"
        "- topic: 更新相关的综合分析主题\n"
        "- structure: 补充与项目结构页相关的内容\n\n"
        "## 输出要求\n"
        "以结构化文本输出分析结果，包含以下部分：\n"
        "1. key_entities: 关键实体列表（名称、类型、描述）\n"
        "2. key_concepts: 关键概念列表（名称、描述）\n"
        "3. recommended_pages: 推荐创建/更新的页面（page_key、type、action: create/update、reason）\n"
        "4. cross_source_findings: 跨源发现（与已有知识的关联或矛盾）\n"
        "5. contradictions: 矛盾信息（描述、涉及来源）\n"
        "6. suggested_base_lib_queries: 建议的基础库检索方向\n"
    )

    context_parts = [f"## 原始资料（{filename}）\n{source_text}"]
    if index_summary:
        context_parts.append(f"## 当前 Wiki 索引\n{index_summary}")
    if schema_hints:
        context_parts.append(f"## 项目结构页编译指引\n{schema_hints}")
    if base_lib_context:
        context_parts.append(f"## 基础库参考\n{base_lib_context}")

    user_content = "\n\n".join(context_parts)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def build_ingest_generation_prompt(
    source_text: str,
    filename: str,
    analysis_report: str,
    purpose: str,
    overview: str,
    existing_content: str,
    schema_hints: str,
    base_lib_context: str,
    project_type: str = "general",
    open_gaps: list[dict] | None = None,
) -> list[dict]:
    """Build messages for Stage 2 (Generation) per §4.3.

    Stage 2 generates actual Wiki page content based on Stage 1 analysis.
    Each page includes YAML frontmatter and uses [[wiki-links]] for cross-references.

    open_gaps: 未解决缺口清单（E2-2026-10 缺口闭环，§4.3）——每项
    {index, title, description}；非空时注入编号清单与 RESOLVES 契约，
    LLM 认定本文件补上某缺口则输出 ---RESOLVES: {编号}---。
    """
    system = (
        _system_prefix(project_type)
        + "你是知识编译 Agent。基于分析结果，生成实际 Wiki 页面。\n\n"
        "## Wiki 目标\n"
        f"{purpose or '（未设定）'}\n\n"
        "## 输出格式\n\n"
        "### 页面内容\n"
        "每个页面用 PAGE block 包裹，必须包含 YAML frontmatter：\n"
        "---PAGE: {page_key}---\n"
        "type: {entity|concept|topic|structure|source}\n"
        "title: {页面标题}\n"
        "refs: [{相关页面 page_key 列表}]\n"
        "tags: [{标签列表}]\n"
        "\n"
        "{Markdown 正文，使用 [[page_key]] 语法引用其他 Wiki 页面}\n"
        "---END PAGE---\n\n"
        "### 来源摘要页\n"
        "必须为当前文件创建一个来源摘要页：\n"
        "---PAGE: src_{file_slug}---\n"
        "type: source\n"
        "title: {文件名} 摘要\n"
        "refs: [{本次摄入触碰的所有页面 page_key}]\n"
        "tags: [{来源标签}]\n"
        "\n"
        "## 关键要点\n"
        "{要点列表}\n"
        "---END PAGE---\n\n"
        "### 发现项\n"
        "用 REVIEW block 包裹：\n"
        "---REVIEW: {type} | {title}---\n"
        "{description}\n"
        "---END REVIEW---\n"
        "REVIEW type 可选：missing-page / contradiction / suggestion / confirm / data-gap\n"
        "contradiction 类型的 REVIEW 必须在 description 开头为每一侧写一行证据：\n"
        "EVIDENCE: [[{page_key}]] 「{原文中最小对立表述}」\n"
        "（两侧各一行；引用原文照抄、尽量短，供后续人工裁决时自动替换被否决侧页面）\n\n"
        "### 全局摘要更新\n"
        "在末尾输出更新后的 Wiki 全局摘要：\n"
        "---OVERVIEW---\n"
        "{更新后的 overview 摘要，1-2 段浓缩所有知识}\n"
        "---END OVERVIEW---\n\n"
        "## 规则\n"
        "1. 必须创建来源摘要页（type: source）\n"
        "2. 正文中必须使用 [[page_key]] 语法引用其他 Wiki 页面\n"
        "3. 增量更新时，在已有内容基础上补充，不覆盖已有正确信息\n"
        "4. 信息冲突时，保留各方说法并标注来源和时间\n"
        "5. 无法确认的信息标注为「待核实」\n"
        "6. 如果发现缺失页面、矛盾、建议，输出 REVIEW block\n"
        f"{_LEDGER_RULE}\n"
        f"{_PROFILE_TIMELINE_RULE}\n"
    )

    # 缺口闭环（§4.3/§4.4，E2-2026-10）：编号清单注入 + RESOLVES 契约。
    # 仅在有未解决缺口时追加，避免空清单噪音；编号由后端确定性映射 review_id。
    if open_gaps:
        gap_lines = "\n".join(
            f"【缺口{g['index']}】{g['title']}: {g['description']}"
            for g in open_gaps
        )
        system += (
            "### 待补缺口\n"
            "以下是此前积累的未解决缺口，请留意本次材料是否恰好补上了其中某个：\n"
            f"{gap_lines}\n"
            "若本次材料确实提供了补上某缺口的关键信息，在输出末尾输出对应行：\n"
            "---RESOLVES: {缺口编号}---\n"
            "（可多行或逗号并列；未补上的缺口不要输出；不确定时宁可不输出）\n\n"
        )

    context_parts = [f"## 原始资料（{filename}）\n{source_text}"]
    if analysis_report:
        context_parts.append(f"## 分析结果（Stage 1 输出）\n{analysis_report}")
    if existing_content:
        context_parts.append(f"## 已有页面内容（增量更新参考）\n{existing_content}")
    if schema_hints:
        context_parts.append(f"## 编译指引\n{schema_hints}")
    if base_lib_context:
        context_parts.append(f"## 基础库补充\n{base_lib_context}")

    user_content = "\n\n".join(context_parts)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
