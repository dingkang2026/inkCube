"""python/report_generator/template_extractor.py — LLM-driven template extraction.

Parses Word/PDF sample documents to extract full text,
then uses LLM to analyze the content and generate a complete template JSON draft
including outline, prompt_hint, audience, role_hint, etc.

设计参考：docs/2.5report-design.md §2.3.2 上传样例 LLM 提取流程
"""
import json
import logging
import re
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum text length to send to LLM (characters). Truncate if longer.
MAX_TEXT_LENGTH = 30000

# 隐式项目上下文变量：运行时恒可用，无需在 variables 声明（与前端 IMPLICIT_VARS 一致）。
# derived 变量的 depends_on 可指向这些名字，清洗时视为已知。
IMPLICIT_VARS = ("company_name", "credit_code", "project_name", "project_type", "stage")


def extract_text_from_docx(filepath: str) -> tuple[str, list[dict]]:
    """Extract full text and headings from a Word document.

    Returns:
        (full_text, headings) where headings is [{level, title}, ...]
    """
    try:
        from docx import Document
    except ImportError:
        logger.error("python-docx not installed. Cannot parse Word documents.")
        return "", []

    try:
        doc = Document(filepath)
    except Exception as e:
        logger.error("Failed to open docx file %s: %s", filepath, e)
        return "", []

    # Extract full text
    paragraphs = []
    headings = []
    heading_styles = {"Heading 1": 1, "Heading 2": 2, "Heading 3": 3, "Heading 4": 4}

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        paragraphs.append(text)

        style_name = para.style.name if para.style else ""
        if style_name in heading_styles:
            headings.append({"level": heading_styles[style_name], "title": text})

    full_text = "\n".join(paragraphs)
    return full_text, headings


def extract_text_from_pdf(filepath: str) -> tuple[str, list[dict]]:
    """Extract full text and headings from a PDF file.

    Returns:
        (full_text, headings) where headings is [{level, title}, ...]
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF not installed. Cannot parse PDF documents.")
        return "", []

    try:
        doc = fitz.open(filepath)
    except Exception as e:
        logger.error("Failed to open PDF file %s: %s", filepath, e)
        return "", []

    paragraphs = []
    headings = []
    chapter_pattern = re.compile(r"^(第[一二三四五六七八九十百千\d]+[章节篇部])\s*(.*)")
    section_pattern = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)")

    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]

        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                text = ""
                max_font_size = 0
                for span in line.get("spans", []):
                    text += span.get("text", "")
                    font_size = span.get("size", 0)
                    max_font_size = max(max_font_size, font_size)

                text = text.strip()
                if not text:
                    continue

                paragraphs.append(text)

                # Detect headings by font size or pattern
                if len(text) <= 200:
                    level = None
                    if max_font_size >= 18:
                        level = 1
                    elif max_font_size >= 15:
                        level = 2
                    elif max_font_size >= 13:
                        level = 3

                    if level is None:
                        m = chapter_pattern.match(text)
                        if m:
                            level = 1
                        else:
                            m = section_pattern.match(text)
                            if m:
                                parts = m.group(1).split(".")
                                level = min(len(parts), 3)

                    if level is not None:
                        if not headings or headings[-1]["title"] != text:
                            headings.append({"level": level, "title": text})

    doc.close()
    full_text = "\n".join(paragraphs)
    return full_text, headings


EXTRACTION_SYSTEM_PROMPT = """你是一位专业的报告模板分析师。用户将提供一份报告样例的全文内容，你需要分析这份报告并生成一个结构化的报告模板 JSON。

请严格按照以下 JSON Schema 输出，不要输出任何其他内容：

```json
{
  "template_name": "报告模板名称",
  "project_types": ["investment" 或 "recruitment" 或 "general"],
  "stage": "before" 或 "during" 或 "after" 或 null,
  "role_hint": "生成角色定位，如'资深行业分析师'",
  "audience": "目标读者/汇报对象",
  "report_depth": "full" 或 "brief",
  "global_requirements": ["全局写作要求1", "全局写作要求2"],
  "variables": [
    {
      "name": "变量名（正文以 {{name}} 引用）",
      "source": "user 或 derived",
      "label": "UI 展示名",
      "required": true,
      "placeholder": "user 变量的输入提示",
      "depends_on": ["依赖的变量名"],
      "derive_hint": "derived 变量的自然语言解析指令",
      "data_sources": ["knowledge", "internet"],
      "cardinality": "scalar 或 list"
    }
  ],
  "outline": [
    {
      "section_id": "ch1",
      "title": "章节标题",
      "children": [
        {
          "section_id": "ch1.1",
          "title": "子节标题",
          "description": "本节内容说明（给人看：检索素材与展示用）",
          "writing_brief": "行文任务书：编号小节清单，每条写明「写什么 + 怎么写 + 用什么形式」，\n如：\n1. 先给出……，引用注明口径；\n2. 以 Markdown 表格对比……；\n3. 给出……判断，说明依据与不确定性",
          "prompt_hint": "写作指引（任务书之外的补充约束，可选）"
        }
      ]
    }
  ]
}
```

分析要求：
1. **大纲结构**：根据报告的实际章节划分，提取完整的层级结构
2. **每节说明**：为每个章节生成 description（内容说明，给人看）和 **writing_brief（行文任务书，重点）**：根据样例中该节的实际内容，反推出 2-5 条编号写作指令（每条=写什么+怎么写+用什么形式，如表格/mermaid 图/引用块/公式），任务书决定行文结构与篇幅，务必具体可执行；prompt_hint 仅放任务书之外的补充约束，无则省略
3. **角色定位**：根据报告的专业领域和文风推断 role_hint
4. **目标读者**：根据报告的用语和深度推断 audience
5. **项目类型**：推断报告属于投资分析(investment)、招商引资(recruitment)还是通用(general)
6. **所属阶段**：推断报告属于投前/招商前(before)、投中/招商中(during)、投后/招商后(after)
7. **变量 DAG 草稿**：识别报告中需要动态填充的「实体锚点」，产出 variables 数组（草稿，供人在编辑器 review 冻结）：
   - **user 变量**：需用户在向导手填的输入项（如报告期、目标区域、行业），声明 source="user" + label + required + placeholder
   - **derived 变量**：可由系统研究得出的实体锚点（如产业领域、竞争对手名单），声明 source="derived" + depends_on（依赖链，可指向 company_name 等隐式变量或其他变量）+ derive_hint（自然语言解析指令）+ data_sources（检索通道，恒非空，["knowledge"] 或 ["knowledge","internet"]）+ cardinality（scalar 单个值 / list 名单数组）
   - **隐式项目变量**（company_name / credit_code / project_name / project_type / stage）无需声明，可直接在 depends_on 或正文 {{}} 中引用
   - 变量值通过正文 {{name}} 注入到相关章节的 description / writing_brief / prompt_hint，**不改变大纲结构**；list 变量注入时以顿号连接
   - 若报告无明显动态变量，variables 输出空数组 []

直接输出 JSON，不要包含 markdown 代码块标记。"""


def _sanitize_variables(variables) -> list[dict]:
    """清洗 LLM 产出的 variables 草稿，确保符合 _schema.json（草稿定位：尽量治愈而非丢弃）。

    两遍处理：先收集合法 name 集合，再逐个清洗（depends_on 仅保留指向已声明变量或隐式变量的引用，
    剔除悬空依赖，使草稿能直接通过编辑器校验）。

    - 仅保留带合法 name 的对象；name 去重（后者丢弃）
    - source 非 user/derived → 默认 user
    - user：required 默认 True；placeholder 仅保留非空字符串
    - derived：depends_on 过滤为已知名字；derive_hint 缺失补空串；
      data_sources 非法/为空补 ["knowledge"]；cardinality 非 scalar/list 补 scalar
    - 仅输出 schema 允许的字段（additionalProperties=false）
    """
    if not isinstance(variables, list):
        return []

    # 第一遍：收集合法且去重的 name（保留首次出现顺序）
    valid_names: list[str] = []
    seen: set[str] = set()
    for v in variables:
        if not isinstance(v, dict):
            continue
        name = v.get("name")
        if isinstance(name, str) and name.strip() and name.strip() not in seen:
            seen.add(name.strip())
            valid_names.append(name.strip())
    known = set(valid_names) | set(IMPLICIT_VARS)

    # 第二遍：逐个清洗
    out: list[dict] = []
    emitted: set[str] = set()
    for v in variables:
        if not isinstance(v, dict):
            continue
        name = v.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        if name in emitted:
            continue
        emitted.add(name)
        label = v.get("label")
        clean: dict = {"name": name, "label": label.strip() if isinstance(label, str) and label.strip() else name}
        source = v.get("source")
        if source not in ("user", "derived"):
            source = "user"
        clean["source"] = source
        if source == "user":
            clean["required"] = bool(v.get("required", True))
            placeholder = v.get("placeholder")
            if isinstance(placeholder, str) and placeholder.strip():
                clean["placeholder"] = placeholder
        else:
            depends_on = v.get("depends_on")
            deps = (
                [d.strip() for d in depends_on if isinstance(d, str) and d.strip()]
                if isinstance(depends_on, list) else []
            )
            # 剔除悬空依赖（仅保留已声明变量或隐式变量）+ 去重保序 + 排除自依赖
            clean["depends_on"] = list(dict.fromkeys(d for d in deps if d in known and d != name))
            derive_hint = v.get("derive_hint")
            clean["derive_hint"] = derive_hint if isinstance(derive_hint, str) else ""
            data_sources = v.get("data_sources")
            valid_sources = (
                [s for s in data_sources if s in ("knowledge", "internet")]
                if isinstance(data_sources, list) else []
            )
            # 去重保序；恒非空（schema minItems=1）
            clean["data_sources"] = list(dict.fromkeys(valid_sources)) or ["knowledge"]
            cardinality = v.get("cardinality")
            clean["cardinality"] = cardinality if cardinality in ("scalar", "list") else "scalar"
        out.append(clean)
    return out


async def extract_template_with_llm(
    full_text: str,
    headings: list[dict],
    llm_client,
    llm_settings: dict,
) -> dict:
    """Use LLM to analyze full text and generate template JSON draft.

    Args:
        full_text: Full text content of the sample document.
        headings: Extracted headings [{level, title}, ...] (used as hints).
        llm_client: LLMClient instance.
        llm_settings: Application settings dict.

    Returns:
        Template JSON dict (draft).
    """
    # Truncate text if too long
    text_for_llm = full_text[:MAX_TEXT_LENGTH]
    if len(full_text) > MAX_TEXT_LENGTH:
        text_for_llm += "\n\n[... 文本过长，已截断 ...]"

    # Build headings hint
    headings_hint = ""
    if headings:
        lines = []
        for h in headings:
            indent = "  " * (h["level"] - 1)
            lines.append(f"{indent}- {h['title']}")
        headings_hint = "\n\n文档标题结构（供参考）：\n" + "\n".join(lines)

    user_message = f"以下是报告样例的全文内容：\n\n{text_for_llm}{headings_hint}\n\n请分析这份报告并生成模板 JSON。"

    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    response = await llm_client.chat(messages, llm_settings)

    # Parse JSON from response
    text = response.strip()
    # Remove markdown code block if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        template = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("LLM returned invalid JSON: %s\nResponse: %s", e, text[:500])
        raise ValueError(f"LLM 返回的内容无法解析为 JSON: {e}")

    # Ensure required fields
    template.setdefault("template_id", f"custom_{uuid.uuid4().hex[:8]}")
    template.setdefault("template_name", "自定义模板")
    template.setdefault("version", "1.0")
    template.setdefault("project_types", ["general"])
    template.setdefault("stage", None)
    template.setdefault("output_format", "docx")
    template.setdefault("role_hint", "")
    template.setdefault("audience", "")
    template.setdefault("report_depth", "full" if len(template.get("outline", [])) >= 5 else "brief")
    template.setdefault("global_requirements", [])
    template.setdefault("outline", [])
    template["variables"] = _sanitize_variables(template.get("variables"))

    # Ensure outline nodes have section_id
    for i, ch in enumerate(template["outline"], 1):
        ch.setdefault("section_id", f"ch{i}")
        for j, sec in enumerate(ch.get("children", []), 1):
            sec.setdefault("section_id", f"ch{i}.{j}")

    return template


async def extract_template_from_sample(
    filepath: str,
    llm_client,
    llm_settings: dict,
) -> dict:
    """Extract a template draft from an uploaded sample document using LLM.

    Parses the document to extract full text, then sends to LLM for
    comprehensive analysis and template generation.

    Args:
        filepath: Path to the uploaded Word/PDF file.
        llm_client: LLMClient instance.
        llm_settings: Application settings dict.

    Returns:
        Template JSON dict (draft, needs user confirmation in editor).
    """
    ext = Path(filepath).suffix.lower()

    if ext in (".docx", ".doc"):
        full_text, headings = extract_text_from_docx(filepath)
    elif ext == ".pdf":
        full_text, headings = extract_text_from_pdf(filepath)
    else:
        raise ValueError(f"不支持的文件格式: {ext}。仅支持 Word (.docx) 和 PDF。")

    if not full_text or len(full_text.strip()) < 100:
        raise ValueError("文档内容过少，无法提取模板结构。请确认文件包含有效内容。")

    logger.info(
        "Extracted text from %s: %d chars, %d headings",
        filepath, len(full_text), len(headings),
    )

    template = await extract_template_with_llm(full_text, headings, llm_client, llm_settings)

    logger.info(
        "Extracted template: name=%s, %d chapters",
        template.get("template_name"),
        len(template.get("outline", [])),
    )

    return template
