"""python/report_generator/card_builder.py — 知识卡片构建。

职责：
  - 将多通道原始检索结果（knowledge chunks + internet results）转化为结构化知识卡片
  - LLM 驱动：信息提取、来源归因、语义去重、交叉验证
  - 充足性评估（充足/不足）
  - 降级：LLM 失败时将 raw results 直接转为简单卡片

Card Schema：
  {
    "card_id": "C001",
    "section_id": "sec_xxx",
    "content": "结构化知识内容",
    "source_type": "knowledge" | "internet",
    "source_title": "文档名/网页标题",
    "source_from": "文件路径+页码 / URL",
    "source_date": "文档日期/网页日期（可选）"
  }

设计参考：docs/2.1project-design.md §6.7.4
"""
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

CARD_BUILD_SYSTEM_PROMPT = """\
你是一个知识管理专家。根据检索到的原始材料，构建结构化知识卡片。

## 输入
- 章节标题和写作要求
- 原始检索结果（来自知识库和互联网）

## 任务
1. **信息提取**：从每个原始结果中提取与章节主题相关的核心知识点
2. **来源归因**：准确标注每条信息的来源（知识库文档 or 互联网网页）
3. **语义去重**：合并来自不同来源但内容重复的知识点，保留更权威的版本
4. **采信优先级**：知识库来源 > 互联网来源（当信息冲突时以知识库为准）
5. **充足性评估**：判断当前材料是否足以支撑章节写作

## 输出格式
输出 JSON：
```json
{
  "cards": [
    {
      "content": "提取的核心知识点（简洁、可直接用于写作）",
      "source_type": "knowledge 或 internet",
      "source_title": "来源文档名或网页标题",
      "source_from": "文件路径+页码 或 URL",
      "source_date": "日期（如有，否则留空）"
    }
  ],
  "sufficiency": {
    "level": "sufficient 或 insufficient",
    "reason": "判断理由（一句话）"
  }
}
```

## 规则
- 每张卡片聚焦一个知识点，内容简洁（100-300字）
- 保留关键数据和事实，不要添加推测
- 来源信息必须准确，不可编造
- 如果材料不足以支撑写作，sufficiency.level 标记为 "insufficient"
- 只输出 JSON，不要其他内容
"""


async def build_knowledge_cards(
    raw_results: list[dict],
    section: dict,
    llm_client: Any,
    llm_settings: dict,
) -> tuple[list[dict], dict, dict]:
    """将原始检索结果转化为知识卡片。

    Args:
        raw_results: 多通道原始结果列表，每项包含：
            {source_type, source_title, source_from, source_date, content, score?}
        section: 归一化后的章节 dict
        llm_client: LLMClient 实例
        llm_settings: 应用设置 dict

    Returns:
        (cards, sufficiency, usage):
          - cards: 知识卡片列表
          - sufficiency: {"level": "sufficient"|"insufficient", "reason": str}
          - usage: API usage dict
    """
    section_id = section.get("section_id", "unknown")
    title = section.get("title", "")
    writing_reqs = section.get("writing_requirements", [])

    if not raw_results:
        return [], {"level": "insufficient", "reason": "无检索结果"}, \
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # 构建用户消息
    user_parts = [
        f"## 章节信息",
        f"- 标题：{title}",
    ]
    if writing_reqs:
        # writing_requirements 是 list[str]，拼接为可读文本
        req_text = "、".join(writing_reqs) if isinstance(writing_reqs, list) else str(writing_reqs)
        user_parts.append(f"- 写作要求：{req_text}")

    user_parts.append(f"\n## 原始检索结果（共 {len(raw_results)} 条）")
    for i, r in enumerate(raw_results, 1):
        source_label = "知识库" if r.get("source_type") == "knowledge" else "互联网"
        user_parts.append(f"### [{i}] [{source_label}] {r.get('source_title', '未知')}")
        user_parts.append(f"来源：{r.get('source_from', '')}")
        if r.get("source_date"):
            user_parts.append(f"日期：{r['source_date']}")
        user_parts.append(f"内容：\n{r.get('content', '')}")
        user_parts.append("")

    user_message = "\n".join(user_parts)

    messages = [
        {"role": "system", "content": CARD_BUILD_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    try:
        result = await llm_client.chat_full(messages, llm_settings)
        content = result["content"]
        usage = result["usage"]

        parsed = _parse_cards_response(content, section_id)
        if parsed:
            cards, sufficiency = parsed
            logger.info(
                "CardBuilder: section=%s, built %d cards, sufficiency=%s",
                section_id, len(cards), sufficiency.get("level", "unknown"),
            )
            return cards, sufficiency, usage
        else:
            logger.warning(
                "CardBuilder: LLM returned unparseable response for section=%s, falling back",
                section_id,
            )
            return _fallback_cards(raw_results, section_id), \
                {"level": "insufficient", "reason": "LLM 响应无法解析"}, usage

    except Exception as e:
        logger.error(
            "CardBuilder: LLM call failed for section=%s: %s, falling back",
            section_id, str(e),
        )
        return _fallback_cards(raw_results, section_id), \
            {"level": "insufficient", "reason": f"LLM 调用失败: {e}"}, \
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _parse_cards_response(
    content: str, section_id: str,
) -> tuple[list[dict], dict] | None:
    """从 LLM 响应中解析 cards 和 sufficiency。"""
    data = None

    # 尝试直接解析
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass

    # 尝试从 markdown 代码块中提取
    if data is None:
        import re
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
            except (json.JSONDecodeError, TypeError):
                pass

    if not data or not isinstance(data, dict):
        return None

    raw_cards = data.get("cards", [])
    if not raw_cards:
        return None

    sufficiency = data.get("sufficiency", {"level": "insufficient", "reason": "未评估"})

    cards = []
    for i, rc in enumerate(raw_cards, 1):
        card = {
            "card_id": f"C{section_id[:4]}_{i:03d}",
            "section_id": section_id,
            "content": str(rc.get("content", "")).strip(),
            "source_type": rc.get("source_type", "knowledge"),
            "source_title": str(rc.get("source_title", "")).strip(),
            "source_from": str(rc.get("source_from", "")).strip(),
            "source_date": str(rc.get("source_date", "")).strip(),
        }
        if card["content"]:
            cards.append(card)

    if not cards:
        return None

    return cards, sufficiency


def _fallback_cards(
    raw_results: list[dict], section_id: str,
) -> list[dict]:
    """降级：将 raw results 直接转为简单卡片（内容截断）。"""
    cards = []
    for i, r in enumerate(raw_results, 1):
        content = r.get("content", "")
        if len(content) > 500:
            content = content[:500] + "……"

        card = {
            "card_id": f"C{section_id[:4]}_{i:03d}",
            "section_id": section_id,
            "content": content,
            "source_type": r.get("source_type", "knowledge"),
            "source_title": r.get("source_title", "未知"),
            "source_from": r.get("source_from", ""),
            "source_date": r.get("source_date", ""),
        }
        cards.append(card)

    return cards
