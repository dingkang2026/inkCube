"""python/report_generator/search_strategy.py — LLM 驱动检索策略。

职责：
  - 根据章节上下文（标题、描述、生成指引、前文摘要）生成多组搜索关键词
  - 使用 LLM 理解信息需求，输出针对性不同的搜索查询
  - 降级：LLM 调用失败时回退到标题作为关键词

设计参考：docs/2.5report-design.md §3.3
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SEARCH_STRATEGY_SYSTEM_PROMPT = """\
你是一个检索策略专家。根据章节信息和前文摘要，生成精准的搜索关键词组。

## 输入
- 章节标题和描述
- 章节的生成指引（prompt_hint）
- 章节的写作要求
- 前文摘要（已完成章节的内容摘要）

## 输出要求
输出 JSON 格式：
```json
{
  "queries": [
    "关键词组1：针对某个具体信息点的搜索词",
    "关键词组2：针对另一个信息点的搜索词",
    ...
  ]
}
```

## 规则
1. 每组关键词应针对不同的信息点或角度
2. 关键词要具体、可检索，避免过于宽泛
3. 结合 prompt_hint 的提示方向
4. 如果前文摘要提供了上下文，利用它来细化搜索方向
5. 输出 3-6 组关键词
6. 只输出 JSON，不要其他内容
"""


async def generate_search_queries(
    section: dict,
    digests: list[dict],
    llm_client: Any,
) -> tuple[list[str], dict]:
    """为章节生成搜索关键词。

    Args:
        section: 归一化后的章节 dict，包含 title, description, prompt_hint
        digests: 前文摘要列表 [{"section_id": str, "title": str, "digest": str}, ...]
        llm_client: LLMClient 实例

    Returns:
        (queries, usage): 搜索关键词列表 + API usage dict
    """
    section_id = section.get("section_id", "unknown")
    title = section.get("title", "")
    description = section.get("description", "")
    prompt_hint = section.get("prompt_hint", "")

    # 构建用户消息
    user_parts = [
        f"## 章节信息",
        f"- 标题：{title}",
    ]
    if description:
        user_parts.append(f"- 描述：{description}")
    if prompt_hint:
        user_parts.append(f"- 生成指引：{prompt_hint}")

    if digests:
        user_parts.append(f"\n## 前文摘要（共 {len(digests)} 节）")
        for d in digests[-5:]:  # 最多取最近 5 节摘要，避免过长
            user_parts.append(f"### {d.get('title', '')}")
            user_parts.append(d.get("digest", "")[:500])

    user_message = "\n".join(user_parts)

    messages = [
        {"role": "system", "content": SEARCH_STRATEGY_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    # 调用 LLM
    try:
        result = await llm_client.chat_full(messages)
        content = result["content"]
        usage = result["usage"]

        # 解析 JSON 响应
        parsed = _parse_queries_json(content)
        if parsed:
            logger.info(
                "SearchStrategy: section=%s, generated %d queries",
                section_id, len(parsed),
            )
            return parsed, usage
        else:
            logger.warning(
                "SearchStrategy: LLM returned unparseable response for section=%s, falling back",
                section_id,
            )
            return _fallback_queries(section), usage

    except Exception as e:
        logger.error(
            "SearchStrategy: LLM call failed for section=%s: %s, falling back to title",
            section_id, str(e),
        )
        # 降级：返回空 usage，使用标题作为关键词
        return _fallback_queries(section), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _parse_queries_json(content: str) -> list[str]:
    """从 LLM 响应中解析 queries 列表。"""
    # 尝试直接解析
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "queries" in data:
            queries = data["queries"]
            if isinstance(queries, list) and queries:
                return [str(q).strip() for q in queries if str(q).strip()]
    except (json.JSONDecodeError, TypeError):
        pass

    # 尝试从 markdown 代码块中提取
    import re
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, dict) and "queries" in data:
                queries = data["queries"]
                if isinstance(queries, list) and queries:
                    return [str(q).strip() for q in queries if str(q).strip()]
        except (json.JSONDecodeError, TypeError):
            pass

    return []


def _fallback_queries(section: dict) -> list[str]:
    """降级策略：使用标题作为搜索关键词。"""
    title = section.get("title", "")
    return [title] if title else []
