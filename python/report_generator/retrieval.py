"""python/report_generator/retrieval.py — 多通道检索层。

职责：
  1. LLM 驱动检索策略（query_hints → 多组搜索关键词）
  2. 多通道并行检索：
     - knowledge 通道：KnowledgeSearchTool（Vector + BM25 + RRF）
     - wiki 通道：项目已编译 Wiki 页面（关键词 + 图谱扩展，高权重骨架上下文）
     - internet 通道：Tavily API 网络搜索
  3. 知识卡片构建（LLM 提取 + 去重 + 归因）
  4. 注册到全局 CitationRegistry
  5. 降级策略（候选池为空时的处理）

设计参考：docs/2.5report-design.md §3.3
"""
import asyncio
import logging
from typing import Any, Optional

import httpx

from knowledge.knowledge_search import KnowledgeSearchTool
from report_generator.card_builder import build_knowledge_cards
from report_generator.registry import CitationRegistry
from report_generator.search_strategy import generate_search_queries

logger = logging.getLogger(__name__)


class RetrievalResult:
    """单次检索结果。"""

    def __init__(
        self,
        cards: list[dict],
        is_degraded: bool = False,
        skip: bool = False,
        sufficiency: Optional[dict] = None,
        raw_results: Optional[list[dict]] = None,
        warnings: Optional[list[str]] = None,
    ):
        self.cards = cards  # 知识卡片列表
        self.is_degraded = is_degraded  # 降级为"分析推理模式"
        self.skip = skip  # 跳过该节（optional + 无材料）
        self.sufficiency = sufficiency or {"level": "unknown", "reason": ""}
        self.raw_results = raw_results or []  # 原始检索结果（调试用）
        # 检索通道降级/失败显式警告（如 Tavily Key 缺失/无效/额度用尽/网络不通），
        # runner 转为 ProgressEvent("warning") → 报告日志，用户在报告详情页可见（0.4 §7.3 v3.6）
        self.warnings = warnings or []


async def retrieve_for_section(
    section: dict,
    search_tool: KnowledgeSearchTool,
    registry: CitationRegistry,
    project_id: str,
    llm_client: Any,
    llm_settings: dict,
    digests: list[dict],
    tavily_api_key: str = "",
    base_project_ids: Optional[list[str]] = None,
    top_k: Optional[int] = None,
    query_parallelism: int = 4,
    db: Any = None,
    data_sources: Optional[list[str]] = None,
    report_depth: str = "full",
) -> tuple[RetrievalResult, dict]:
    """为一个章节执行多通道并行检索。

    流程：
      ① LLM 检索策略：生成搜索关键词
      ② 知识库通道检索（默认启用）
      ③ 各通道独立检索，结果合并
      ④ LLM 构建知识卡片
      ⑤ 注册到全局 Registry

    Args:
        section: 归一化后的节定义（含 prompt_hint、optional 等）
        search_tool: 知识检索工具实例
        registry: 全局引用注册器
        project_id: 当前项目 ID
        llm_client: LLMClient 实例
        llm_settings: 应用设置 dict
        digests: 前文摘要列表
        tavily_api_key: Tavily API Key（internet 通道）
        base_project_ids: 关联基础库项目 ID 列表（可选）
        top_k: 每通道返回片段数（None → 按 report_depth 推算：full 20 / brief 10）
        query_parallelism: 通道内多 query 并行数（默认 4）
        db: 数据库句柄（wiki 通道检索项目已编译 Wiki 页面时使用）
        data_sources: 检索通道子集 {"knowledge","internet","wiki"}（None → ["knowledge"]）
        report_depth: 报告深度 "full" | "brief"，决定知识库检索量

    Returns:
        (RetrievalResult, total_usage): 检索结果 + 累计 API usage
    """
    section_id = section.get("section_id", "unknown")
    if data_sources is None:
        data_sources = ["knowledge"]
    if top_k is None:
        top_k = 20 if report_depth == "full" else 10
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _add_usage(usage: dict):
        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        total_usage["total_tokens"] += usage.get("total_tokens", 0)

    # ① LLM 检索策略：生成搜索关键词
    queries, strategy_usage = await generate_search_queries(
        section, digests, llm_client, llm_settings,
    )
    _add_usage(strategy_usage)
    logger.info("Retrieval: section=%s, queries=%d", section_id, len(queries))

    # ② 多通道检索装配（§3.3：knowledge 恒可用；wiki 按 data_sources + db；internet 按 data_sources + tavily_key）
    raw_results: list[dict] = []
    channel_tasks = []
    channel_warnings: list[str] = []

    if "knowledge" in data_sources:
        channel_tasks.append(
            _search_knowledge_channel(
                queries, search_tool, project_id, base_project_ids, top_k,
                query_parallelism,
            )
        )

    if "wiki" in data_sources and db is not None:
        channel_tasks.append(
            _search_wiki_channel(queries, db, project_id, top_k)
        )

    if "internet" in data_sources:
        if tavily_api_key:
            channel_tasks.append(
                _search_internet_channel(
                    queries, tavily_api_key, top_k, query_parallelism,
                )
            )
        else:
            logger.info(
                "Retrieval: section=%s internet 通道已请求但未配置 tavily_key，跳过",
                section_id,
            )
            # 显式降级警告（0.4 §7.3 v3.6）：请求了互联网通道但 Key 缺失，不能静默跳过
            channel_warnings.append(
                "互联网检索未生效：未配置 Tavily API Key，本次报告仅使用项目内材料。"
                "请前往 设置 > 外部模型 配置后重试。"
            )

    # 并行执行所有通道
    if channel_tasks:
        channel_results = await asyncio.gather(*channel_tasks, return_exceptions=True)
        for result in channel_results:
            if isinstance(result, Exception):
                logger.warning("Channel execution failed: %s", result)
                continue
            channel_data, channel_usage, warnings = result
            raw_results.extend(channel_data)
            channel_warnings.extend(warnings)
            _add_usage(channel_usage)

    # ③ 降级判断
    if not raw_results:
        if section.get("optional"):
            logger.info("Section '%s': no results, optional=true → skip", section_id)
            return RetrievalResult(cards=[], skip=True, warnings=channel_warnings), total_usage
        else:
            logger.info("Section '%s': no results, optional=false → degraded mode", section_id)
            return RetrievalResult(cards=[], is_degraded=True, warnings=channel_warnings), total_usage

    # ④ LLM 构建知识卡片
    cards, sufficiency, card_usage = await build_knowledge_cards(
        raw_results, section, llm_client, llm_settings,
    )
    _add_usage(card_usage)

    # ⑤ 注册到全局 Registry
    ref_numbers = []
    for card in cards:
        num = registry.register_card(card)
        card["ref_number"] = num
        ref_numbers.append(num)

    logger.info("Section '%s': %d raw results → %d cards, refs=%s, sufficiency=%s",
                section_id, len(raw_results), len(cards), ref_numbers,
                sufficiency.get("level", "unknown"))

    return RetrievalResult(
        cards=cards,
        sufficiency=sufficiency,
        raw_results=raw_results,
        warnings=channel_warnings,
    ), total_usage


# ── knowledge 通道 ──────────────────────────────────────────


async def _search_knowledge_channel(
    queries: list[str],
    search_tool: KnowledgeSearchTool,
    project_id: str,
    base_project_ids: Optional[list[str]],
    top_k: int,
    query_parallelism: int = 4,
) -> tuple[list[dict], dict]:
    """知识库通道：对每个 query 执行向量 + BM25 检索，RRF 融合去重。
    
    支持多 query 并行执行，通过 semaphore 控制并发数。
    """
    search_project_ids = [project_id]
    if base_project_ids:
        search_project_ids.extend(base_project_ids)

    all_chunks: dict[str, dict] = {}  # chunk_id -> chunk dict
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # 使用 semaphore 控制并发
    sem = asyncio.Semaphore(query_parallelism)

    async def search_one(query: str, pid: str):
        """执行单次搜索并收集结果。"""
        async with sem:
            try:
                results = await search_tool.search(
                    query=query, project_id=pid, top_k=top_k,
                )
                return results
            except Exception as e:
                logger.warning("Knowledge search failed for query='%s' in project=%s: %s",
                               query, pid, e)
                return []

    # 并行执行所有 query × project_id 组合
    tasks = [search_one(query, pid) for query in queries for pid in search_project_ids]
    results_list = await asyncio.gather(*tasks)

    for results in results_list:
        for r in results:
            cid = r.get("id", "")
            if cid and cid not in all_chunks:
                metadata = r.get("metadata", {})
                all_chunks[cid] = {
                    "source_type": "knowledge",
                    "source_title": metadata.get("filename", "未知文档"),
                    "source_from": _format_knowledge_source(metadata),
                    "source_date": "",
                    "content": _truncate_content(r.get("content", ""), 500),
                    "score": r.get("_score", 0),
                }

    # 按分数排序取 top_k
    sorted_chunks = sorted(all_chunks.values(), key=lambda c: c["score"], reverse=True)
    return sorted_chunks[:top_k], empty_usage, []


def _format_knowledge_source(metadata: dict) -> str:
    """格式化知识库来源（文件路径 + 页码）。"""
    filename = metadata.get("filename", "")
    page_no = metadata.get("page_no")
    if page_no:
        return f"{filename} p.{page_no}"
    return filename


# ── internet 通道 ──────────────────────────────────────────


def _tavily_failure_warning(e: Exception) -> str:
    """Tavily 检索失败 → 面向用户的显式警告文案（自带修正指引，0.4 §7.3 v3.6）。

    降级显式呈现：互联网通道失败时报告仍会用项目内材料完成，但用户必须被告知
    「本次未使用互联网材料」及修正路径，不能静默降级。
    """
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403):
        return ("互联网检索未生效：Tavily API Key 无效或已失效，本次报告仅使用项目内材料。"
                "请前往 设置 > 外部模型 更新 Tavily Key。")
    if status == 402:
        return ("互联网检索未生效：Tavily 账户额度已用尽，本次报告仅使用项目内材料。"
                "请检查 Tavily 用量，或在 设置 > 外部模型 更换 API Key。")
    if status == 429:
        return ("互联网检索未生效：Tavily 调用频率或用量超限，本次报告仅使用项目内材料。"
                "请稍后重试或检查 Tavily 账户用量限额。")
    if isinstance(e, (httpx.ConnectError, httpx.TimeoutException)):
        return "互联网检索未生效：无法连接 Tavily 服务，本次报告仅使用项目内材料。请检查网络连接。"
    return ("互联网检索未生效：Tavily 服务暂时异常，本次报告仅使用项目内材料。可稍后重新生成。")


async def _search_internet_channel(
    queries: list[str],
    tavily_api_key: str,
    top_k: int,
    query_parallelism: int = 4,
) -> tuple[list[dict], dict, list[str]]:
    """互联网通道：使用 Tavily API 搜索。
    
    支持多 query 并行执行，通过 semaphore 控制并发数。
    复用 httpx 连接池，内置 429 速率限制退避。
    返回 (results, usage, warnings)：warnings 为显式降级警告（去重后，0.4 §7.3 v3.6）。
    """
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    if not tavily_api_key:
        logger.warning("Internet channel: tavily_api_key not configured, skipping")
        return [], empty_usage, [
            "互联网检索未生效：未配置 Tavily API Key，本次报告仅使用项目内材料。"
            "请前往 设置 > 外部模型 配置后重试。"
        ]

    all_results: dict[str, dict] = {}  # url -> result dict
    warning_set: set[str] = set()  # 多 query 失败去重

    # 使用 semaphore 控制并发
    sem = asyncio.Semaphore(query_parallelism)

    # 复用连接池
    async with httpx.AsyncClient(timeout=30.0) as client:
        async def search_one(query: str):
            """执行单次搜索并收集结果（含 429 退避）。"""
            async with sem:
                try:
                    results = await _tavily_search(query, tavily_api_key, client, max_results=5)
                    return results
                except Exception as e:
                    warning = _tavily_failure_warning(e)
                    if warning:
                        warning_set.add(warning)
                    logger.warning("Internet search failed for query='%s': %s", query, e)
                    return []

        # 并行执行所有 query
        tasks = [search_one(query) for query in queries]
        results_list = await asyncio.gather(*tasks)

    for results in results_list:
        for r in results:
            url = r.get("url", "")
            if url and url not in all_results:
                all_results[url] = {
                    "source_type": "internet",
                    "source_title": r.get("title", "未知网页"),
                    "source_from": url,
                    "source_date": r.get("published_date", ""),
                    "content": _truncate_content(r.get("content", ""), 500),
                    "score": r.get("score", 0),
                }

    # 按分数排序取 top_k
    sorted_results = sorted(all_results.values(), key=lambda r: r["score"], reverse=True)
    return sorted_results[:top_k], empty_usage, list(warning_set)


async def _tavily_search(
    query: str, api_key: str, client: httpx.AsyncClient,
    max_results: int = 5, max_retries: int = 2,
) -> list[dict]:
    """直接调用 Tavily API 获取结构化搜索结果。
    
    内置 429 速率限制退避（指数等待），复用传入的 httpx 客户端连接池。
    """
    for attempt in range(max_retries + 1):
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "include_answer": True,
            },
        )
        if response.status_code == 429 and attempt < max_retries:
            wait = 2 ** (attempt + 1)  # 2s, 4s
            logger.warning("Tavily rate limited, retrying in %ds (attempt %d/%d)",
                           wait, attempt + 1, max_retries)
            await asyncio.sleep(wait)
            continue
        response.raise_for_status()
        break  # 成功响应，退出重试循环

    data = response.json()
    results = []

    # 如果有 answer，作为第一条结果
    if data.get("answer"):
        results.append({
            "title": "Tavily AI Answer",
            "content": data["answer"],
            "url": f"tavily://answer/{query[:50]}",
            "score": 1.0,
            "published_date": "",
        })

    for r in data.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "content": r.get("content", ""),
            "url": r.get("url", ""),
            "score": r.get("score", 0),
            "published_date": r.get("published_date", ""),
        })

    return results


# ── 工具函数 ──────────────────────────────────────────────


def _truncate_content(text: str, max_len: int = 500) -> str:
    """截断过长片段。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "……"


# ── wiki 通道 ──────────────────────────────────────────────


async def _search_wiki_channel(
    queries: list[str],
    db: Any,
    project_id: str,
    top_k: int,
) -> tuple[list[dict], dict, list[str]]:
    """Wiki 通道：从项目已编译的 Wiki 页面中检索相关页面。

    设计参考 docs/2.6wiki-design.md §8.1/§8.2：Wiki 页面是已编译的结构化“骨架”
    知识，作为报告生成的最高权重上下文（采信优先级 Wiki > 知识库 > 互联网）。

    仅纳入已完成状态（compiled / user_edited）的页面，排除 draft 半成品页。
    页面集只拉取一次并在多 query 间复用，避免重复全量加载。
    使用 wiki_search 引擎（关键词 + 图谱扩展），合并去重后按相关性排序。
    """
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    from wiki.wiki_search import search_wiki

    # 只检索已完成的 Wiki 页面（compiled / user_edited），排除 draft 半成品。
    # index/log/overview/purpose 不作为 wiki_pages 行存储，无需额外排除元页。
    try:
        all_pages = db.list_wiki_pages(project_id)
    except Exception as e:
        logger.warning("Wiki channel: failed to list pages for project=%s: %s", project_id, e)
        return [], empty_usage, []

    finished_pages = [
        p for p in all_pages
        if p.get("status") in ("compiled", "user_edited")
    ]
    if not finished_pages:
        return [], empty_usage

    all_results: dict[str, dict] = {}  # page_key -> result dict

    for query in queries:
        try:
            results = search_wiki(
                query=query,
                project_id=project_id,
                db=db,
                top_k=top_k,
                enable_graph=True,
                pages=finished_pages,
            )
        except Exception as e:
            logger.warning("Wiki search failed for query='%s': %s", query, e)
            continue

        for page in results:
            pk = page.get("page_key", "")
            if pk and pk not in all_results:
                content = page.get("content", "")
                all_results[pk] = {
                    "source_type": "wiki",
                    "source_title": page.get("title", pk),
                    "source_from": f"wiki:{pk}",
                    "source_date": page.get("updated_at", ""),
                    "content": _truncate_content(content, 800),
                    "score": page.get("_score", 1.0),
                    "page_key": pk,
                }

    sorted_pages = sorted(all_results.values(), key=lambda r: r["score"], reverse=True)
    return sorted_pages[:top_k], empty_usage, []
