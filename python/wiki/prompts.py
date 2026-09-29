"""python/wiki/prompts.py — Prompt templates for Wiki two-stage ingest.

Per §4.3 of 2.6wiki-design.md:
  Stage 1 (Analysis): Read full source text, plan pages, identify entities/concepts.
  Stage 2 (Generation): Produce PAGE blocks with YAML frontmatter + REVIEW blocks.
"""


def build_ingest_analysis_prompt(
    source_text: str,
    filename: str,
    purpose: str,
    overview: str,
    index_summary: str,
    schema_hints: str,
    base_lib_context: str,
) -> list[dict]:
    """Build messages for Stage 1 (Analysis) per §4.3.

    Stage 1 reads the full source text and outputs a structured analysis:
    key entities, concepts, recommended pages, contradictions, etc.
    Does NOT generate page content.
    """
    system = (
        "你是知识分析 Agent。你的任务是深入阅读一篇原始资料，输出结构化分析。\n\n"
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
) -> list[dict]:
    """Build messages for Stage 2 (Generation) per §4.3.

    Stage 2 generates actual Wiki page content based on Stage 1 analysis.
    Each page includes YAML frontmatter and uses [[wiki-links]] for cross-references.
    """
    system = (
        "你是知识编译 Agent。基于分析结果，生成实际 Wiki 页面。\n\n"
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
        "REVIEW type 可选：missing-page / contradiction / suggestion / confirm / data-gap\n\n"
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


# ── Legacy (kept for backward compat with compiler.py) ──


def build_stage1_analysis_prompt(
    raw_materials: str,
    existing_pages_summary: str,
    base_lib_context: str,
    schema_hints: str,
) -> list[dict]:
    """Build messages for Stage 1 (Analysis).

    Stage 1 reads materials and plans page structure, identifies concepts,
    flags contradictions/gaps. Does NOT generate page content.
    """
    system = (
        "你是知识编译 Agent。分析以下原始资料，规划 Wiki 页面结构。\n"
        "你的任务是：\n"
        "1. 识别资料中的关键概念、实体、数据\n"
        "2. 建议哪些 page_key 需要创建或更新\n"
        "3. 标注发现的矛盾、缺失信息、建议\n"
        "4. 不要生成页面内容，只做分析和规划\n\n"
        "以 JSON 格式输出分析报告。"
    )

    context_parts = []
    if raw_materials:
        context_parts.append(f"## 原始资料\n{raw_materials}")
    if existing_pages_summary:
        context_parts.append(f"## 已有 Wiki 页面\n{existing_pages_summary}")
    if base_lib_context:
        context_parts.append(f"## 基础库参考\n{base_lib_context}")
    if schema_hints:
        context_parts.append(f"## 编译指引\n{schema_hints}")

    user_content = "\n\n".join(context_parts) if context_parts else "（无额外资料）"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def build_stage2_generation_prompt(
    raw_materials: str,
    analysis_report: str,
    existing_pages_content: str,
    base_lib_context: str,
    compile_hints: dict[str, str],
) -> list[dict]:
    """Build messages for Stage 2 (Generation).

    Stage 2 generates actual page content based on Stage 1 analysis.
    Output uses PAGE blocks and REVIEW blocks.
    """
    # Build compile hints section
    hints_text = ""
    if compile_hints:
        hints_lines = []
        for page_key, hint in compile_hints.items():
            hints_lines.append(f"- **{page_key}**: {hint}")
        hints_text = "\n".join(hints_lines)

    system = (
        "你是知识编译 Agent。根据分析结果生成 Wiki 页面内容。\n\n"
        f"## 编译指引\n{hints_text}\n\n"
        "## 输出格式\n"
        "页面内容用 PAGE block 包裹：\n"
        "---PAGE: {page_key}---\n"
        "(Markdown 内容)\n"
        "---END PAGE---\n\n"
        "发现项用 REVIEW block 包裹：\n"
        "---REVIEW: {type} | {title}---\n"
        "{description}\n"
        "---END REVIEW---\n\n"
        "REVIEW type 可选：missing-page / contradiction / suggestion / confirm / data-gap\n\n"
        "## 规则\n"
        "1. 为每个需要创建/更新的页面生成 Markdown 内容\n"
        "2. 增量更新时，在已有内容基础上补充，不覆盖已有正确信息\n"
        "3. 信息冲突时，保留各方说法并标注来源和时间\n"
        "4. 无法确认的信息标注为「待核实」\n"
        "5. 使用 Markdown 格式\n"
        "6. 如果发现缺失页面、矛盾、建议，输出 REVIEW block"
    )

    context_parts = []
    if raw_materials:
        context_parts.append(f"## 原始资料\n{raw_materials}")
    if analysis_report:
        context_parts.append(f"## 分析结果（Stage 1 输出）\n{analysis_report}")
    if existing_pages_content:
        context_parts.append(f"## 已有页面内容（增量更新参考）\n{existing_pages_content}")
    if base_lib_context:
        context_parts.append(f"## 基础库参考\n{base_lib_context}")

    user_content = "\n\n".join(context_parts) if context_parts else "（无额外资料）"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
