"""python/report_generator/retrieval.py — 多通道检索层。

职责：
  1. LLM 驱动检索策略（轮 1 query 生成）
  2. 迭代检索循环（§3.3 多轮搜索-卡片积累）：轮 N 补搜 queries 由建卡调用的
     sufficiency.follow_up_queries 直接产出，轮次上限 settings report_retrieval_max_rounds
  3. 多通道并行检索（每轮）：
     - knowledge 通道：KnowledgeSearchTool（Vector + BM25 + RRF）
     - wiki 通道：项目已编译 Wiki 页面（关键词 + 图谱扩展，高权重骨架上下文）
     - internet 通道：网络搜索（Tavily / 智谱二选一，按 web_search_cfg 分发；
       Bearer 认证 / language 加权 / 轮 2+ Tavily search_depth 可配）
  4. 知识卡片构建（LLM 提取 + 去重 + 归因；补搜轮增量建卡 + 跨轮两级去重）
  5. 注册到全局 CitationRegistry
  6. 降级策略（候选池为空时的处理）

设计参考：docs/2.5report-design.md §3.3
"""
import asyncio
import logging
from typing import Any, Optional

import httpx

from knowledge.knowledge_search import KnowledgeSearchTool
from tools.web_search import DEFAULT_ZHIPU_ENGINE, ZHIPU_ENGINES, has_cjk
from report_generator.card_builder import build_knowledge_cards
from report_generator.registry import CitationRegistry
from report_generator.search_strategy import generate_search_queries

logger = logging.getLogger(__name__)


class RetrievalResult:
    """单节检索结果（迭代循环终态）。"""

    def __init__(
        self,
        cards: list[dict],
        is_degraded: bool = False,
        skip: bool = False,
        sufficiency: Optional[dict] = None,
        raw_results: Optional[list[dict]] = None,
        warnings: Optional[list[str]] = None,
        rounds: Optional[list[dict]] = None,
        queries: Optional[list[str]] = None,
    ):
        self.cards = cards  # 全量知识卡片（含 ref_number）
        self.is_degraded = is_degraded  # 降级为"分析推理模式"
        self.skip = skip  # 跳过该节（optional + 无材料）
        # 三字段 {level, reason, follow_up_queries}（§3.3 迭代循环）
        self.sufficiency = sufficiency or {"level": "unknown", "reason": "", "follow_up_queries": []}
        self.raw_results = raw_results or []  # 各轮累计原始结果（调试用）
        # 检索通道降级/失败显式警告（如网络搜索 Key 缺失/无效/额度用尽/网络不通），
        # runner 转为 ProgressEvent("warning") → 报告日志，用户在报告详情页可见（0.4 §7.3 v3.6）
        self.warnings = warnings or []
        # 每轮明细（§3.8 检索摘要落库用）：{round_no, queries, channel_stats,
        # raw_count, new_raw_count, card_count, sufficiency_level, sufficiency_reason}
        self.rounds = rounds or []
        # 全部已执行 queries（轮 1 策略产物 + 各轮 follow_up）
        self.queries = queries or []


async def retrieve_for_section(
    section: dict,
    search_tool: KnowledgeSearchTool,
    registry: CitationRegistry,
    project_id: str,
    llm_client: Any,
    llm_settings: dict,
    digests: list[dict],
    web_search_cfg: dict | None = None,
    base_project_ids: Optional[list[str]] = None,
    top_k: Optional[int] = None,
    query_parallelism: int = 4,
    db: Any = None,
    data_sources: Optional[list[str]] = None,
    include_project_kb: bool = True,
    report_depth: str = "full",
) -> tuple[RetrievalResult, dict]:
    """为一个章节执行迭代检索循环（多轮搜索-卡片积累，§3.3）。

    流程（每轮「三通道检索 → 建卡 + 充足性评估」）：
      轮 1：LLM 检索策略生成 queries → 三通道检索 → 建卡 + 评估
      轮 N：上一轮 sufficiency.follow_up_queries 补搜 → 增量建卡 + 评估（累计材料）
      退出：sufficient / follow_up_queries 为空 / 补搜无新料 / 达轮次上限
      （settings report_retrieval_max_rounds，默认 3，钾制 1-5）

    Args:
        section: 归一化后的节定义（含 prompt_hint、optional 等）
        search_tool: 知识检索工具实例
        registry: 全局引用注册器
        project_id: 当前项目 ID
        llm_client: LLMClient 实例
        llm_settings: 应用设置 dict
        digests: 前文摘要列表
        web_search_cfg: 网络搜索配置 {"provider","key","engine"}（internet 通道，web_search_cfg_from_settings 归一产物）
        base_project_ids: 关联基础库项目 ID 列表（可选）
        top_k: 每通道返回片段数（None → 按 report_depth 推算：full 20 / brief 10）
        query_parallelism: 通道内多 query 并行数（默认 4）
        db: 数据库句柄（wiki 通道检索项目已编译 Wiki 页面时使用）
        data_sources: 检索通道子集 {"knowledge","internet","wiki"}（None → ["knowledge"]）
        include_project_kb: 是否检索本项目内材料（§1.2，2026-10-27）；False 时
            knowledge 通道仅检索勾选的基础库，wiki 通道由调用方不传入 "wiki"
            而排除（runner 侧随同一开关门控）。支撑通用行业/区域研究不携带
            项目主体材料场景。章节路径的 knowledge 剥离由 runner 条件化组装
            保证；未按约定剥离时（如变量抽值路径直传声明通道），由通道内
            空范围早返回兜底（0 卡片 → 降级，结果等价）
        report_depth: 报告深度 "full" | "brief"，决定知识库检索量

    Returns:
        (RetrievalResult, total_usage): 检索结果（含每轮明细 rounds）+ 累计 API usage
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

    # 轮次上限（§3.3）：settings 可配，默认 3，钳制 1-5
    try:
        max_rounds = int(llm_settings.get("report_retrieval_max_rounds", 3))
    except (TypeError, ValueError):
        max_rounds = 3
    max_rounds = max(1, min(max_rounds, 5))

    # 补搜轮（2+）Tavily search_depth 可配（Tavily 专属设置，智谱引擎档位由
    # web_search_cfg.engine 承载；默认 basic；轮 1 恒 basic，§3.3）
    followup_search_depth = str(
        llm_settings.get("report_tavily_search_depth", "basic") or "basic"
    )

    # ① LLM 检索策略：生成轮 1 搜索关键词
    queries, strategy_usage = await generate_search_queries(
        section, digests, llm_client,
    )
    _add_usage(strategy_usage)
    used_queries: list[str] = list(queries)
    logger.info("Retrieval: section=%s round=1 queries=%d, max_rounds=%d",
                section_id, len(queries), max_rounds)

    channel_warnings: list[str] = []
    seen_sources: set[tuple[str, str]] = set()  # (source_type, source_from) 跨轮粗滤
    all_raw: list[dict] = []  # 各轮累计原始结果（调试用）
    cards: list[dict] = []
    round_summaries: list[dict] = []
    sufficiency: dict = {"level": "unknown", "reason": "", "follow_up_queries": []}

    for round_no in range(1, max_rounds + 1):
        # ② 单轮三通道并行检索（补搜轮 search_depth 可配）
        raw_results, channel_stats, round_warnings, round_usage = await _run_retrieval_round(
            queries,
            search_tool=search_tool,
            project_id=project_id,
            base_project_ids=base_project_ids,
            top_k=top_k,
            query_parallelism=query_parallelism,
            db=db,
            data_sources=data_sources,
            web_search_cfg=web_search_cfg,
            search_depth="basic" if round_no == 1 else followup_search_depth,
            section_id=section_id,
            include_project_kb=include_project_kb,
        )
        _add_usage(round_usage)
        channel_warnings.extend(round_warnings)
        all_raw.extend(raw_results)

        # ③ 跨轮粗滤：剔除与此前轮次同来源的结果（补搜常撞回已搜页面）
        new_results = [
            r for r in raw_results
            if (r.get("source_type", ""), r.get("source_from", "")) not in seen_sources
        ]
        for r in raw_results:
            seen_sources.add((r.get("source_type", ""), r.get("source_from", "")))

        round_summaries.append({
            "round_no": round_no,
            "queries": list(queries),
            "channel_stats": channel_stats,
            "raw_count": len(raw_results),
            "new_raw_count": len(new_results),
        })

        # 轮 1 空结果 → 降级/跳过判定（§3.3 降级表）
        if round_no == 1 and not raw_results:
            if section.get("optional"):
                logger.info("Section '%s': no results, optional=true → skip", section_id)
                return RetrievalResult(
                    cards=[], skip=True, warnings=channel_warnings,
                    rounds=round_summaries, queries=used_queries,
                ), total_usage
            logger.info("Section '%s': no results, optional=false → degraded mode", section_id)
            return RetrievalResult(
                cards=[], is_degraded=True, warnings=channel_warnings,
                rounds=round_summaries, queries=used_queries,
            ), total_usage

        # 补搜轮无新料 → 不空转建卡，以现有材料收尾（避免重复 token 消耗）
        if round_no > 1 and not new_results:
            logger.info("Retrieval: section=%s round=%d no new sources, stop",
                        section_id, round_no)
            break

        # ④ 建卡 + 充足性评估（补搜轮针对累计材料，同时产出 follow_up_queries）
        cards, sufficiency, card_usage = await build_knowledge_cards(
            new_results, section, llm_client,
            existing_cards=cards if round_no > 1 else None,
            used_queries=used_queries if round_no > 1 else None,
        )
        _add_usage(card_usage)

        round_summaries[-1].update({
            "card_count": len(cards),
            "sufficiency_level": sufficiency.get("level"),
            "sufficiency_reason": sufficiency.get("reason", ""),
            "follow_up_queries": sufficiency.get("follow_up_queries", []),
        })

        # ⑤ 注册本轮新增卡片（registry 三元组精确去重，重复内容返回已有编号）
        for card in cards:
            if not card.get("ref_number"):
                card["ref_number"] = registry.register_card(card)

        logger.info(
            "Section '%s' round %d: %d raw (%d new) → %d cards, sufficiency=%s (%s)",
            section_id, round_no, len(raw_results), len(new_results), len(cards),
            sufficiency.get("level"), str(sufficiency.get("reason", ""))[:60],
        )

        if sufficiency.get("level") == "sufficient":
            break
        follow_up = sufficiency.get("follow_up_queries") or []
        if not follow_up:
            break
        queries = follow_up
        used_queries.extend(follow_up)

    return RetrievalResult(
        cards=cards,
        sufficiency=sufficiency,
        raw_results=all_raw,
        warnings=channel_warnings,
        rounds=round_summaries,
        queries=used_queries,
    ), total_usage


async def _run_retrieval_round(
    queries: list[str],
    *,
    search_tool: KnowledgeSearchTool,
    project_id: str,
    base_project_ids: Optional[list[str]],
    top_k: int,
    query_parallelism: int,
    db: Any,
    data_sources: list[str],
    web_search_cfg: dict,
    search_depth: str,
    section_id: str = "unknown",
    include_project_kb: bool = True,
) -> tuple[list[dict], dict, list[str], dict]:
    """执行单轮三通道并行检索（§3.3 迭代循环的单轮）。

    Returns:
        (raw_results, channel_stats, warnings, usage)：
          channel_stats 形如 {"knowledge": n, "wiki": n, "internet": n}（未启用通道缺省 0）
    """
    raw_results: list[dict] = []
    channel_warnings: list[str] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    stats: dict[str, int] = {}

    channel_tasks = []
    task_names = []

    if "knowledge" in data_sources:
        channel_tasks.append(
            _search_knowledge_channel(
                queries, search_tool, project_id, base_project_ids, top_k,
                query_parallelism, include_project_kb,
            )
        )
        task_names.append("knowledge")

    if "wiki" in data_sources and db is not None:
        channel_tasks.append(
            _search_wiki_channel(queries, db, project_id, top_k)
        )
        task_names.append("wiki")

    if "internet" in data_sources:
        if web_search_cfg and web_search_cfg.get("key"):
            channel_tasks.append(
                _search_internet_channel(
                    queries, web_search_cfg, top_k, query_parallelism,
                    search_depth=search_depth,
                )
            )
            task_names.append("internet")
        else:
            logger.info(
                "Retrieval: section=%s internet 通道已请求但网络搜索未配置，跳过",
                section_id,
            )
            # 显式降级警告（0.4 §7.3 v3.6）：请求了互联网通道但网络搜索未配置，不能静默跳过
            channel_warnings.append(
                "互联网检索未生效：未配置网络搜索，本次报告仅使用项目内材料。"
                "请前往 设置 > 基础配置 > 网络搜索 配置后重试。"
            )

    # 并行执行所有通道
    if channel_tasks:
        channel_results = await asyncio.gather(*channel_tasks, return_exceptions=True)
        for name, result in zip(task_names, channel_results):
            if isinstance(result, Exception):
                logger.warning("Channel '%s' execution failed: %s", name, result)
                stats[name] = 0
                continue
            channel_data, channel_usage, warnings = result
            raw_results.extend(channel_data)
            channel_warnings.extend(warnings)
            stats[name] = len(channel_data)
            usage["prompt_tokens"] += channel_usage.get("prompt_tokens", 0)
            usage["completion_tokens"] += channel_usage.get("completion_tokens", 0)
            usage["total_tokens"] += channel_usage.get("total_tokens", 0)

    return raw_results, stats, channel_warnings, usage


# ── knowledge 通道 ──────────────────────────────────────────


async def _search_knowledge_channel(
    queries: list[str],
    search_tool: KnowledgeSearchTool,
    project_id: str,
    base_project_ids: Optional[list[str]],
    top_k: int,
    query_parallelism: int = 4,
    include_project_kb: bool = True,
) -> tuple[list[dict], dict, list[str]]:
    """知识库通道：对每个 query 执行向量 + BM25 检索，RRF 融合去重。
    
    支持多 query 并行执行，通过 semaphore 控制并发数。
    include_project_kb=False 时检索范围仅含勾选的基础库（§1.2）；
    空范围（False 且无基础库）早返回空三元组，不发起任何检索。
    """
    search_project_ids: list[str] = []
    if include_project_kb and project_id:
        search_project_ids.append(project_id)
    if base_project_ids:
        search_project_ids.extend(base_project_ids)
    if not search_project_ids:
        return [], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, []

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


def web_search_cfg_from_settings(llm_settings: dict) -> dict:
    """从应用设置归一网络搜索配置（provider 二选一，两把 Key 并存，2026-10-19）。

    未选供应商时 key 为空——消费方（_search_internet_channel / 工具层）按
    未配置处理，不猜意图。runner / variable_resolver 共用唯一归一入口。
    """
    provider = str(llm_settings.get("web_search_provider") or "").strip()
    if provider == "zhipu":
        key = str(llm_settings.get("zhipu_search_key") or "").strip()
    elif provider == "tavily":
        key = str(llm_settings.get("tavily_key") or "").strip()
    else:
        key = ""
    return {
        "provider": provider,
        "key": key,
        "engine": str(llm_settings.get("zhipu_search_engine") or "search_pro"),
    }


def _search_failure_warning(e: Exception, provider: str) -> str:
    """网络搜索失败 → 面向用户的显式警告文案（自带修正指引，0.4 §7.3 v3.6）。

    降级显式呈现：互联网通道失败时报告仍会用项目内材料完成，但用户必须被告知
    「本次未使用互联网材料」及修正路径，不能静默降级。文案按供应商区分。
    """
    name = "智谱" if provider == "zhipu" else "Tavily"
    goto = "设置 > 基础配置 > 网络搜索"
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403):
        return (f"互联网检索未生效：{name} API Key 无效或已失效，本次报告仅使用项目内材料。"
                f"请前往 {goto} 更新 Key。")
    if status == 402:
        return (f"互联网检索未生效：{name} 账户额度已用尽，本次报告仅使用项目内材料。"
                f"请检查 {name} 用量，或在 {goto} 更换 API Key。")
    if status == 429:
        return (f"互联网检索未生效：{name} 调用频率或用量超限，本次报告仅使用项目内材料。"
                f"请稍后重试或检查 {name} 账户用量限额。")
    if isinstance(e, (httpx.ConnectError, httpx.TimeoutException)):
        return f"互联网检索未生效：无法连接 {name} 服务，本次报告仅使用项目内材料。请检查网络连接。"
    return (f"互联网检索未生效：{name} 服务暂时异常，本次报告仅使用项目内材料。可稍后重新生成。")


async def _search_internet_channel(
    queries: list[str],
    web_search_cfg: dict,
    top_k: int,
    query_parallelism: int = 4,
    search_depth: str = "basic",
) -> tuple[list[dict], dict, list[str]]:
    """互联网通道：网络搜索（Tavily / 智谱，按 web_search_cfg.provider 分发）。"""
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    provider = web_search_cfg.get("provider", "")

    if not web_search_cfg.get("key"):
        logger.warning("Internet channel: web_search not configured, skipping")
        return [], empty_usage, [
            "互联网检索未生效：未配置网络搜索，本次报告仅使用项目内材料。"
            "请前往 设置 > 基础配置 > 网络搜索 配置后重试。"
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
                    results = await _web_search_api(
                        query, web_search_cfg, client,
                        max_results=5, search_depth=search_depth,
                    )
                    return results
                except Exception as e:
                    warning = _search_failure_warning(e, provider)
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


async def _web_search_api(
    query: str, cfg: dict, client: httpx.AsyncClient,
    max_results: int = 5, search_depth: str = "basic",
) -> list[dict]:
    """按 provider 分发的单次网络搜索，返回统一结构 [{title, content, url, score, published_date}]。"""
    if cfg.get("provider") == "zhipu":
        return await _zhipu_search(query, cfg, client, max_results=max_results)
    return await _tavily_search(
        query, cfg.get("key", ""), client,
        max_results=max_results, search_depth=search_depth,
    )


async def _zhipu_search(
    query: str, cfg: dict, client: httpx.AsyncClient,
    max_results: int = 5, max_retries: int = 2,
) -> list[dict]:
    """直接调用智谱 Web Search API（open.bigmodel.cn /paas/v4/web_search）。

    Bearer 认证与智谱 LLM 同平台（同一个 API Key 体系）；search_query 上限
    70 字符（API 硬限，超长截断）；search_intent=False（工具场景显式搜索，
    必填字段）；content_size=high（摘要详细档，信息密度优先）。
    响应无 score——以返回序号为相关性权重（引擎已排序，倒序计分保序）；
    无 answer 段（无 Tavily 中文摘要翻译失真问题，c2f24e5c）。
    内置 429 速率限制退避（指数等待），复用传入的 httpx 客户端连接池。
    """
    for attempt in range(max_retries + 1):
        response = await client.post(
            "https://open.bigmodel.cn/api/paas/v4/web_search",
            json={
                "search_query": query[:70],
                "search_engine": (
                    cfg.get("engine") if cfg.get("engine") in ZHIPU_ENGINES
                    else DEFAULT_ZHIPU_ENGINE
                ),
                "search_intent": False,
                "count": max_results,
                "content_size": "high",
            },
            headers={"Authorization": f"Bearer {cfg.get('key', '')}"},
        )
        if response.status_code == 429 and attempt < max_retries:
            wait = 2 ** (attempt + 1)  # 2s, 4s
            logger.warning("Zhipu web_search rate limited, retrying in %ds (attempt %d/%d)",
                           wait, attempt + 1, max_retries)
            await asyncio.sleep(wait)
            continue
        response.raise_for_status()
        break  # 成功响应，退出重试循环

    data = response.json()
    raw = data.get("search_result") or []
    results = []
    for i, r in enumerate(raw):
        results.append({
            "title": r.get("title", ""),
            "content": r.get("content", ""),
            "url": r.get("link", ""),
            "score": float(len(raw) - i),  # 序号即权重（引擎已排序）
            "published_date": r.get("publish_date", ""),
        })
    return results


async def _tavily_search(
    query: str, api_key: str, client: httpx.AsyncClient,
    max_results: int = 5, search_depth: str = "basic", max_retries: int = 2,
) -> list[dict]:
    """直接调用 Tavily API 获取结构化搜索结果。

    认证走 Authorization: Bearer header（官方推荐，Key 不进请求体）；
    language=zh-cn 中文命中加权（纯加权不丢结果）；include_answer 仅非 CJK
    query 开启（与工具层 tools/web_search.py 同判，c2f24e5c 中文摘要失真回归）；
    search_depth 白名单归一（advanced 2 credits，轮 2+ 补搜经 settings
    report_tavily_search_depth 传入）。
    内置 429 速率限制退避（指数等待），复用传入的 httpx 客户端连接池。
    """
    if search_depth not in ("advanced", "basic", "fast", "ultra-fast"):
        search_depth = "basic"
    for attempt in range(max_retries + 1):
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "query": query,
                "max_results": max_results,
                "include_answer": not has_cjk(query),
                "language": "zh-cn",
                "search_depth": search_depth,
            },
            headers={"Authorization": f"Bearer {api_key}"},
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
        return [], empty_usage, []

    all_results: dict[str, dict] = {}  # page_key -> result dict

    for query in queries:
        try:
            # wiki_search 为同步 CPU 密集调用（关键词+图谱扩展）：to_thread 下放，
            # 避免 async 通道内阻塞事件循环（与导出链路 to_thread 同纪律）
            results = await asyncio.to_thread(
                search_wiki,
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
