"""python/report_generator/card_builder.py — 知识卡片构建。

职责：
  - 将多通道原始检索结果（wiki 页面 + knowledge chunks + internet results）转化为结构化知识卡片
  - LLM 驱动：信息提取、来源归因、语义去重、交叉验证
  - 充足性评估（充足/不足）+ 补搜 queries 建议（迭代检索循环轮 2+ 的查询来源，§3.3）
  - 增量建卡：补搜轮接受 existing_cards（原样保留）与 used_queries（硬约束避重）
  - 降级：LLM 失败时将 raw results 直接转为简单卡片

Card Schema：
  {
    "card_id": "C001",
    "section_id": "sec_xxx",
    "content": "结构化知识内容",
    "source_type": "wiki" | "knowledge" | "internet",
    "source_title": "文档名/网页标题",
    "source_from": "文件路径+页码 / URL",
    "source_date": "文档日期/网页日期（可选）"
  }

设计参考：docs/2.5report-design.md §3.3
"""
import json
import logging
from typing import Any, Optional

from errors import actionable

logger = logging.getLogger(__name__)

# 来源类型 → 展示标签（用于在用户消息中标注每条原始检索结果的来源）
_SOURCE_LABELS = {"knowledge": "知识库", "wiki": "项目Wiki", "internet": "互联网"}

CARD_BUILD_SYSTEM_PROMPT = """\
你是一个知识管理专家。根据检索到的原始材料，构建结构化知识卡片。

## 输入
- 章节标题和写作要求
- 原始检索结果（来自项目 Wiki、知识库和互联网）
- 已有知识卡片与已执行的检索清单（补搜轮才有：评估与补搜针对全部累计材料）

## 任务
1. **信息提取**：从每个原始结果中提取与章节主题相关的核心知识点
2. **来源归因**：准确标注每条信息的来源（项目 Wiki 页面 / 知识库文档 / 互联网网页）
3. **语义去重**：合并来自不同来源但内容重复的知识点，保留更权威的版本；已有卡片中已有的知识点不重复提取
4. **采信优先级**：项目 Wiki > 知识库 > 互联网。Wiki 页面是已编译的结构化结论（权威骨架），知识库是原始文档细节，互联网仅作补充；信息冲突时以更高优先级来源为准
5. **充足性评估**：针对全部材料（已有卡片 + 本轮新增结果）判断是否足以支撑章节写作
6. **补搜建议**：仅当评估为 insufficient 时，针对缺口生成 2-4 组新的搜索关键词

## 输出格式
输出 JSON：
```json
{
  "cards": [
    {
      "content": "提取的核心知识点（简洁、可直接用于写作）",
      "source_type": "wiki、knowledge 或 internet",
      "source_title": "来源文档名或网页标题",
      "source_from": "文件路径+页码 或 URL",
      "source_date": "日期（如有，否则留空）"
    }
  ],
  "sufficiency": {
    "level": "sufficient 或 insufficient",
    "reason": "判断理由（一句话）",
    "follow_up_queries": ["补搜关键词1", "补搜关键词2"]
  }
}
```

## 规则
- 每张卡片聚焦一个知识点，内容简洁（100-300字）
- 保留关键数据和事实，不要添加推测
- 来源信息必须准确，不可编造
- 如果材料不足以支撑写作，sufficiency.level 标记为 "insufficient"，并在 follow_up_queries 中给出 2-4 组针对缺口的搜索关键词（不得与已执行的检索语义重复）；材料足够时 level 为 "sufficient"，follow_up_queries 为空数组
- 只输出 JSON，不要其他内容
"""


async def build_knowledge_cards(
    raw_results: list[dict],
    section: dict,
    llm_client: Any,
    llm_settings: dict,
    existing_cards: Optional[list[dict]] = None,
    used_queries: Optional[list[str]] = None,
) -> tuple[list[dict], dict, dict]:
    """将原始检索结果转化为知识卡片（迭代检索循环的单轮建卡，§3.3）。

    Args:
        raw_results: 本轮新增的多通道原始结果列表，每项包含：
            {source_type, source_title, source_from, source_date, content, score?}
        section: 归一化后的章节 dict
        llm_client: LLMClient 实例
        llm_settings: 应用设置 dict
        existing_cards: 已有卡片（补搜轮传入）——原样保留返回，LLM 仅为新增
            raw 提卡，充足性评估针对累计材料整体判断
        used_queries: 已执行的检索关键词全量清单（补搜轮传入）——prompt 硬约束
            follow_up_queries 不得语义重复

    Returns:
        (cards, sufficiency, usage):
          - cards: 全量卡片（existing_cards 原样 + 新增，新增项待注册发 ref_number）
          - sufficiency: {"level", "reason", "follow_up_queries"} 三字段，
            insufficient 时 follow_up_queries 为 2-4 组补搜关键词
          - usage: API usage dict
    """
    section_id = section.get("section_id", "unknown")
    title = section.get("title", "")
    writing_reqs = section.get("writing_requirements", [])
    existing_cards = existing_cards or []
    used_queries = used_queries or []

    if not raw_results:
        return list(existing_cards), \
            {"level": "insufficient", "reason": "无检索结果", "follow_up_queries": []}, \
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # 构建用户消息
    user_parts = [
        "## 章节信息",
        f"- 标题：{title}",
    ]
    if writing_reqs:
        # writing_requirements 是 list[str]，拼接为可读文本
        req_text = "、".join(writing_reqs) if isinstance(writing_reqs, list) else str(writing_reqs)
        user_parts.append(f"- 写作要求：{req_text}")

    # 补搜轮上下文（§3.3 迭代循环）：已有卡片轻量清单 + 已用 queries 硬约束避重
    if existing_cards:
        user_parts.append(f"\n## 已有知识卡片（共 {len(existing_cards)} 张，勿重复提取）")
        for c in existing_cards:
            source_label = _SOURCE_LABELS.get(c.get("source_type"), "互联网")
            user_parts.append(f"- [{source_label}] {str(c.get('content', ''))[:100]}")
    if used_queries:
        user_parts.append("\n## 已执行的检索（follow_up_queries 不得与之语义重复）")
        for q in used_queries:
            user_parts.append(f"- {q}")

    user_parts.append(f"\n## 原始检索结果（本轮新增，共 {len(raw_results)} 条）")
    for i, r in enumerate(raw_results, 1):
        source_label = _SOURCE_LABELS.get(r.get("source_type"), "互联网")
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

        parsed = _parse_cards_response(content, section_id, start_index=len(existing_cards))
        if parsed:
            new_cards, sufficiency = parsed
            cards = list(existing_cards) + new_cards
            logger.info(
                "CardBuilder: section=%s, built %d new cards (total %d), sufficiency=%s",
                section_id, len(new_cards), len(cards), sufficiency.get("level", "unknown"),
            )
            return cards, sufficiency, usage
        else:
            logger.warning(
                "CardBuilder: LLM returned unparseable response for section=%s, falling back",
                section_id,
            )
            return list(existing_cards) + _fallback_cards(
                raw_results, section_id, start_index=len(existing_cards)), \
                {"level": "insufficient", "reason": "INKCUBE_CODE:CARD_PARSE_FAILED", "follow_up_queries": []}, usage

    except Exception as e:
        logger.error(
            "CardBuilder: LLM call failed for section=%s: %s, falling back",
            section_id, str(e),
        )
        return list(existing_cards) + _fallback_cards(
            raw_results, section_id, start_index=len(existing_cards)), \
            {"level": "insufficient", "reason": actionable(e), "follow_up_queries": []}, \
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _parse_cards_response(
    content: str, section_id: str, start_index: int = 0,
) -> tuple[list[dict], dict] | None:
    """从 LLM 响应中解析 cards 和 sufficiency（follow_up_queries 经 _normalize_sufficiency 归一）。"""
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

    sufficiency = _normalize_sufficiency(data.get("sufficiency"))

    cards = []
    for i, rc in enumerate(raw_cards, start_index + 1):
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


def _normalize_sufficiency(sufficiency: Any) -> dict:
    """归一化 sufficiency 为三字段 {level, reason, follow_up_queries}（§3.3）。

    防御归一：level 非法值回退 insufficient；follow_up_queries 非字符串项剔除、
    截断至 4 组；sufficient 强制清空 queries（prompt 已约束，此处双保险）。
    """
    if not isinstance(sufficiency, dict):
        return {"level": "insufficient", "reason": "未评估", "follow_up_queries": []}
    level = sufficiency.get("level")
    if level not in ("sufficient", "insufficient"):
        level = "insufficient"
    reason = str(sufficiency.get("reason", "")).strip()
    queries = sufficiency.get("follow_up_queries")
    if not isinstance(queries, list):
        queries = []
    queries = [str(q).strip() for q in queries if isinstance(q, str) and q.strip()][:4]
    if level == "sufficient":
        queries = []
    return {"level": level, "reason": reason, "follow_up_queries": queries}


def _fallback_cards(
    raw_results: list[dict], section_id: str, start_index: int = 0,
) -> list[dict]:
    """降级：将 raw results 直接转为简单卡片（内容截断）。"""
    cards = []
    for i, r in enumerate(raw_results, start_index + 1):
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
