"""python/report_generator/runner.py — 报告生成执行主流程。

职责：
  1. 两遍执行（standard 先，post_body 后）
  2. 每节 checkpoint（outputs/digests/registry/order/token_tracker）
  3. 断点恢复
  4. 精准 Token 统计追踪
  5. 智能重试分类
  6. 报告级暂停（章节边界协作生效，2.5 §3.1 修订）

设计参考：docs/2.5report-design.md §3.8 ~ §3.9
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from report_generator.normalizer import (
    instantiate, normalize_outline,
    validate_execution_outline, get_execution_sections,
)
from report_generator.registry import CitationRegistry
from report_generator.retrieval import retrieve_for_section
from report_generator.variable_resolver import resolve_variables
from report_generator.prompts import (
    build_system_prompt, build_standard_prompt, build_post_body_prompt,
    build_digest_prompt, build_extract_numbers_prompt,
)
from report_generator.validator import validate_section, format_retry_feedback
from report_generator.finalizer import finalize_report
from report_generator.token_tracker import TokenTracker
from errors import (
    actionable,
    LLMInvalidKey, LLMRateLimited, LLMQuotaExhausted, LLMModelNotFound,
    LLMNetworkError,
)

logger = logging.getLogger(__name__)

# 最大重试次数
MAX_RETRIES = 3


# ── 智能重试分类 ──────────────────────────────────────────


def _classify_error(error_str: str) -> tuple[bool, str]:
    """根据错误信息分类重试策略。

    Args:
        error_str: 错误消息（小写）

    Returns:
        (retryable, action):
          - retryable: 是否可重试
          - action: "retry" | "degrade" | "fatal"
    """
    # 内容安全过滤 → 不重试，直接降级
    if any(kw in error_str for kw in [
        "content_filter", "content_policy", "safety", "moderation",
        "content management", "responsible_ai",
    ]):
        return False, "degrade"

    # Token 超限 → 截断上下文后重试（一次机会）
    if any(kw in error_str for kw in [
        "context_length", "max_tokens", "token_limit",
        "maximum context length", "too many tokens",
    ]):
        return True, "truncate"

    # 无效 API Key → 不重试
    if "401" in error_str or ("invalid" in error_str and "key" in error_str):
        return False, "fatal"

    # 频率限制 / 超时 / 网络错误 → 重试（指数退避）
    if any(kw in error_str for kw in [
        "429", "rate_limit", "rate limit", "too many requests",
        "timeout", "timed out", "connection", "network",
    ]):
        return True, "retry"

    # 空输出 → 重试
    if "empty" in error_str or "null content" in error_str:
        return True, "retry"

    # 默认：重试
    return True, "retry"


class ReportGenerationError(Exception):
    """报告生成过程中的致命错误。"""
    pass


class ProgressEvent:
    """进度事件（用于进度轮询）。"""

    def __init__(self, event_type: str, data: dict):
        self.event_type = event_type  # "section_start" | "section_done" | "progress" | "warning" | "paused" | "complete" | "error"
        self.data = data


class ReportRunner:
    """报告生成执行器。

    Usage:
        runner = ReportRunner(
            template=template,
            variables={"公司名": "XX科技"},
            project_id="proj_xxx",
            search_tool=search_tool,
            llm_client=llm_client,
            llm_settings=settings,
            output_dir="data/reports/report_xxx",
        )
        async for event in runner.run():
            # event: ProgressEvent → 推送给前端
            ...
    """

    def __init__(
        self,
        template: dict,
        variables: dict[str, str],
        project_id: str,
        search_tool,
        llm_client,
        llm_settings: dict,
        output_dir: str,
        base_project_ids: Optional[list[str]] = None,
        db=None,
        section_internet: Optional[dict] = None,
        internet_enabled: bool = False,
        pause_check: Optional[Callable[[], bool]] = None,
    ):
        self.template = template
        self.variables = variables
        self.project_id = project_id
        self.search_tool = search_tool
        self.llm_client = llm_client
        self.llm_settings = llm_settings
        self.output_dir = Path(output_dir)
        self.base_project_ids = base_project_ids or []
        self.db = db
        # 报告级互联网总开关（总闸，§1.1）：OFF 时全程仅用知识库，章节开关失效
        self.internet_enabled = internet_enabled
        # 章节级互联网开关 {section_id: bool}（§1.1）；report_depth 贯穿检索/生成/校验
        self.section_internet = section_internet or {}
        self.report_depth = template.get("report_depth", "full")
        # Wiki 通道可用性（_prepare 中按项目已完成 Wiki 页面检测；无则仅知识库/互联网）
        self.wiki_available = False
        # 报告级暂停探针（2.5 §3.1 修订）：pause 端点写 DB 标志，路由层注入本
        # callable；run() 在章节边界轮询（当前章节完成→checkpoint 已落盘→安全停）
        self.pause_check = pause_check

        # 状态
        self.outputs: dict[str, str] = {}
        self.digests: dict[str, str] = {}
        self.order: list[str] = []
        self.registry = CitationRegistry()
        self.token_tracker = TokenTracker()
        self.system_prompt = ""
        self.outline: list[dict] = []
        self.sections: list[dict] = []
        self.report_title = ""
        self.resolved_variables: dict = {}
        self.resolution_evidence: dict = {}

    async def run(self):
        """执行报告生成主流程。

        Yields:
            ProgressEvent 实例
        """
        try:
            # ── 尝试从 checkpoint 恢复（含 resolved_variables，供 _prepare 短路）──
            self._load_checkpoint()

            # ── 准备阶段（解析变量 → 实例化 → 归一化 → 校验）──
            await self._prepare()

            # 降级显式引导（0.4 §7.3 v3.6）：互联网总开关开启但 Tavily Key 缺失，
            # 全程实际仅用项目内材料——作为 warning 写入报告日志，而非静默降级
            if self.internet_enabled and not str(self.llm_settings.get("tavily_key") or "").strip():
                yield ProgressEvent("warning", {
                    "message": (
                        "互联网检索未生效：已开启互联网检索但未配置 Tavily API Key，"
                        "本次报告仅使用项目内材料。请前往 设置 > 外部模型 配置 Tavily Key 后重新生成。"
                    ),
                })

            # ── 第一遍：standard 章节 ──
            standard_sections = [
                s for s in self.sections if s.get("compose_mode", "standard") == "standard"
            ]
            total = len(self.sections)

            for sec in standard_sections:
                sid = sec["section_id"]
                if sid in self.outputs:
                    continue  # 已恢复

                # 报告级暂停检查点（2.5 §3.1 修订）：请求到达后当前章节完成后生效，
                # 上节 checkpoint 已落盘；恢复走 retry(resume) 断点续跑
                if self._pause_requested():
                    yield ProgressEvent("paused", {"completed": len(self.outputs), "total": total})
                    return

                yield ProgressEvent("section_start", {
                    "section_id": sid,
                    "title": sec.get("title", ""),
                    "index": self.order.index(sid) + 1 if sid in self.order else len(self.outputs) + 1,
                    "total": total,
                })

                # 构建 digests 列表（供检索策略使用）
                digests_list = [
                    {"section_id": s, "title": self._get_section_title(s), "digest": self.digests[s]}
                    for s in self.order if s in self.digests
                ]

                # 检索（多通道并行 + LLM 策略 + 卡片构建）
                retrieval, retrieval_usage = await retrieve_for_section(
                    section=sec,
                    search_tool=self.search_tool,
                    registry=self.registry,
                    project_id=self.project_id,
                    llm_client=self.llm_client,
                    llm_settings=self.llm_settings,
                    digests=digests_list,
                    tavily_api_key=self.llm_settings.get("tavily_key", ""),
                    base_project_ids=self.base_project_ids,
                    query_parallelism=self.llm_settings.get("report_channel_parallelism", 4),
                    db=self.db,
                    data_sources=(
                        ["knowledge"]
                        + (["wiki"] if self.wiki_available else [])
                        + (["internet"] if (self.internet_enabled and self.section_internet.get(sid)) else [])
                    ),
                    report_depth=self.report_depth,
                )
                self.token_tracker.record(sid, "retrieval", retrieval_usage)

                # 检索通道降级警告（如 Tavily Key 无效/额度用尽/网络不通，0.4 §7.3 v3.6）
                # → 报告日志显式呈现，用户在报告详情页可见
                for warning in retrieval.warnings:
                    yield ProgressEvent("warning", {"message": warning})

                if retrieval.skip:
                    self.outputs[sid] = "（本节因材料不足已跳过）"
                    self.digests[sid] = ""  # 空摘要，避免污染后续章节的检索策略
                    self.order.append(sid)
                    yield ProgressEvent("section_done", {
                        "section_id": sid,
                        "title": sec.get("title", ""),
                        "skipped": True,
                        "completed": len(self.outputs),
                        "total": total,
                    })
                    self._save_checkpoint()
                    continue

                # 构建 Prompt
                chapter_title = sec.get("_chapter_title", "")
                ref_numbers = [c.get("ref_number", 0) for c in retrieval.cards if c.get("ref_number")]
                ref_text = self.registry.format_for_prompt(ref_numbers)

                user_prompt = build_standard_prompt(
                    section=sec,
                    chapter_title=chapter_title,
                    report_title=self.report_title,
                    completed_digests=self.digests,
                    section_order=self.order,
                    ref_text=ref_text,
                    ref_numbers=ref_numbers,
                    global_requirements=self.template.get("global_requirements"),
                    is_degraded=retrieval.is_degraded,
                    sufficiency=retrieval.sufficiency,
                )

                # 生成 + 重试
                output, finish_reason = await self._generate_with_retry(
                    section=sec,
                    user_prompt=user_prompt,
                    given_refs=set(ref_numbers),
                )

                # 输出截断用户可见（§3.9：finish_reason=length 不抛错）
                if finish_reason == "length":
                    yield ProgressEvent("warning", {
                        "message": f"章节「{sec.get('title', sid)}」输出因模型长度限制被截断，内容可能不完整",
                    })

                self.outputs[sid] = output
                self.order.append(sid)

                # 生成摘要
                digest = await self._summarize(output)
                self.digests[sid] = digest

                yield ProgressEvent("section_done", {
                    "section_id": sid,
                    "title": sec.get("title", ""),
                    "degraded": retrieval.is_degraded,
                    "completed": len(self.outputs),
                    "total": total,
                })

                self._save_checkpoint()

            # ── 第二遍：post_body 章节 ──
            post_body_sections = [
                s for s in self.sections if s.get("compose_mode") == "post_body"
            ]

            for sec in post_body_sections:
                sid = sec["section_id"]
                if sid in self.outputs:
                    continue

                # 报告级暂停检查点（同第一遍，2.5 §3.1 修订）
                if self._pause_requested():
                    yield ProgressEvent("paused", {"completed": len(self.outputs), "total": total})
                    return

                yield ProgressEvent("section_start", {
                    "section_id": sid,
                    "title": sec.get("title", ""),
                    "index": len(self.outputs) + 1,
                    "total": total,
                })

                # 提取关键数据
                key_data = await self._extract_numbers()

                user_prompt = build_post_body_prompt(
                    section=sec,
                    digests=self.digests,
                    section_order=self.order,
                    key_data_text=key_data,
                    registry=self.registry,
                    global_requirements=self.template.get("global_requirements"),
                )

                output, finish_reason = await self._generate_with_retry(
                    section=sec,
                    user_prompt=user_prompt,
                    given_refs=set(self.registry.all_ref_numbers()),
                )

                if finish_reason == "length":
                    yield ProgressEvent("warning", {
                        "message": f"章节「{sec.get('title', sid)}」输出因模型长度限制被截断，内容可能不完整",
                    })

                self.outputs[sid] = output
                self.order.append(sid)

                yield ProgressEvent("section_done", {
                    "section_id": sid,
                    "title": sec.get("title", ""),
                    "completed": len(self.outputs),
                    "total": total,
                })

                self._save_checkpoint()

            # ── 全局收尾 ──
            yield ProgressEvent("progress", {"phase": "finalizing", "message": "全局校验与收尾..."})

            content_path = self._write_final_artifacts()

            yield ProgressEvent("complete", {
                "content_path": str(content_path),
                "total_sections": sum(
                    1 for v in self.outputs.values()
                    if not v.startswith("（本节因材料不足已跳过）")
                ),
                "total_words": sum(
                    len(v) for v in self.outputs.values()
                    if not v.startswith("（本节因材料不足已跳过）")
                ),
                "registry_size": len(self.registry),
                "token_usage": self.token_tracker.summary(),
            })

        except Exception as e:
            logger.exception("Report generation failed: %s", e)
            yield ProgressEvent("error", {"message": actionable(e)})
            raise

    def _pause_requested(self) -> bool:
        """报告级暂停请求是否待消费（无探针/探针异常均视为不暂停，不阻断生成）。"""
        if self.pause_check is None:
            return False
        try:
            return bool(self.pause_check())
        except Exception:
            logger.warning("pause_check probe failed", exc_info=True)
            return False

    async def _prepare(self):
        """准备阶段（§3.9 管线）：解析变量 → 实例化 → 归一化 → 校验。"""
        # 变量解析（研究 DAG）。checkpoint 已含 resolved_variables 则短路，
        # 避免重复解析（确定性 + 已付 token）。
        if not self.resolved_variables:
            project = self.db.get_project(self.project_id) if self.db else {}
            resolved, evidence, resolve_usage = await resolve_variables(
                self.template, project, self.variables,
                search_tool=self.search_tool,
                llm_client=self.llm_client,
                llm_settings=self.llm_settings,
                db=self.db,
                base_project_ids=self.base_project_ids,
                tavily_api_key=self.llm_settings.get("tavily_key", ""),
                internet_enabled=self.internet_enabled,
            )
            self.token_tracker.record("__resolve__", "variable_resolution", resolve_usage)
            self.resolved_variables = resolved
            self.resolution_evidence = evidence

        # 实例化变量（用 resolved 替换，非 self.variables；{{}} 注入后大纲结构不变）
        tpl = instantiate(self.template, self.resolved_variables)
        outline = tpl.get("outline", [])

        # 归一化大纲
        self.outline = normalize_outline(outline)

        # 校验
        errors = validate_execution_outline(self.outline)
        if errors:
            raise ReportGenerationError(f"大纲校验失败: {'; '.join(errors)}")

        # 获取执行节列表
        self.sections = get_execution_sections(self.outline)

        # 报告标题
        self.report_title = tpl.get("template_name", "报告")

        # System Prompt（注入报告主体 + 报告深度）
        self.system_prompt = build_system_prompt(
            role_hint=tpl.get("role_hint", "资深研究分析师"),
            audience=tpl.get("audience", "机构客户"),
            company_name=self.resolved_variables.get("company_name", ""),
            report_depth=self.report_depth,
        )

        # 确保输出目录
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Wiki 通道可用性检测：项目存在已完成 Wiki 页面则报告检索自动纳入 wiki 通道
        self.wiki_available = self._detect_wiki_available()

        logger.info("Runner prepared: %d sections, depth=%s, wiki=%s, title='%s'",
                    len(self.sections), self.report_depth, self.wiki_available, self.report_title)

    def _detect_wiki_available(self) -> bool:
        """检测项目是否有已完成（compiled / user_edited）的 Wiki 页面。

        有则报告检索自动纳入 wiki 通道（无用户开关，§2.6 §8.2）；无 db、
        查询失败或无已完成页面时返回 False，优雅降级为仅知识库 / 互联网检索。
        """
        if self.db is None:
            return False
        try:
            pages = self.db.list_wiki_pages(self.project_id)
        except Exception as e:
            logger.warning("Wiki availability check failed for project=%s: %s",
                           self.project_id, e)
            return False
        return any(p.get("status") in ("compiled", "user_edited") for p in pages)

    def _load_checkpoint(self):
        """从 checkpoint 恢复状态（如果存在）。"""
        ckpt_path = self.output_dir / "checkpoint.json"
        if not ckpt_path.exists():
            return

        try:
            data = json.loads(ckpt_path.read_text(encoding="utf-8"))
            self.outputs = data.get("outputs", {})
            self.digests = data.get("digests", {})
            self.order = data.get("order", [])
            self.resolved_variables = data.get("resolved_variables", {})
            self.resolution_evidence = data.get("resolution_evidence", {})
            if "registry" in data:
                self.registry = CitationRegistry.from_dict(data["registry"])
            if "token_tracker" in data:
                self.token_tracker = TokenTracker.from_dict(data["token_tracker"])
            logger.info("Checkpoint loaded: %d sections done, %d refs, %d token records",
                        len(self.outputs), len(self.registry), len(self.token_tracker))
        except Exception as e:
            logger.warning("Failed to load checkpoint: %s", e)

    def _save_checkpoint(self):
        """保存 checkpoint。"""
        ckpt_path = self.output_dir / "checkpoint.json"
        data = {
            "outputs": self.outputs,
            "digests": self.digests,
            "order": self.order,
            "registry": self.registry.to_dict(),
            "token_tracker": self.token_tracker.to_dict(),
            "resolved_variables": self.resolved_variables,
            "resolution_evidence": self.resolution_evidence,
        }
        ckpt_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_final_artifacts(self) -> Path:
        """组装全文 → 全局收尾 → 写 content.md + registry.json + token_usage.json。

        run() 收尾时调用，确保全文组装 → 全局收尾 → 落盘产物一致。

        Returns:
            content.md 路径
        """
        full_text = self._assemble_full_text()
        final_text = finalize_report(full_text, self.registry, self.template)

        content_path = self.output_dir / "content.md"
        content_path.parent.mkdir(parents=True, exist_ok=True)
        content_path.write_text(final_text, encoding="utf-8")

        self.registry.save(self.output_dir / "registry.json")
        self.token_tracker.save(self.output_dir / "token_usage.json")
        return content_path

    async def _generate_with_retry(
        self,
        section: dict,
        user_prompt: str,
        given_refs: set[int],
    ) -> tuple[str, str]:
        """生成单节内容，智能重试 + 精准 token 追踪。

        Returns:
            (output, finish_reason)：finish_reason 为最后一次成功调用的
            结束原因；"length" 表示输出被 max_tokens 截断（reasoning 模型
            思考 token 亦计入输出预算，§3.9），由调用方告警可见。
        """
        sid = section.get("section_id", "unknown")
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        for attempt in range(MAX_RETRIES):
            try:
                result = await self.llm_client.chat_full(messages, self.llm_settings)
                output = result["content"]
                usage = result["usage"]
                finish_reason = result.get("finish_reason", "stop")
                self.token_tracker.record(sid, "generation", usage)

                # 输出截断可观测（chat_full 不抛错，仅 finish_reason=length）
                if finish_reason == "length":
                    logger.warning(
                        "Section '%s' attempt %d: output truncated (finish_reason=length), "
                        "completion_tokens=%s", sid, attempt + 1, usage.get("completion_tokens"),
                    )

                # 校验
                violations = validate_section(output, given_refs, section, report_depth=self.report_depth)
                if not violations:
                    return output, finish_reason

                # 有违规，构建重试反馈
                if attempt < MAX_RETRIES - 1:
                    feedback = format_retry_feedback(violations)
                    logger.info("Section '%s' attempt %d: %d violations, retrying...",
                                sid, attempt + 1, len(violations))
                    messages = [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_prompt},
                        {"role": "assistant", "content": output},
                        {"role": "user", "content": feedback},
                    ]
                else:
                    logger.warning("Section '%s': %d violations after %d retries, accepting",
                                   sid, len(violations), MAX_RETRIES)
                    return output, finish_reason

            except Exception as e:
                # 结构化错误类优先（llm/client 分类产物，0.4 §7.3 v3.6）：
                # 配置/凭据类 fatal 不重试；限流/网络 retry。字符串规则兜底（历史/未知异常）
                if isinstance(e, (LLMInvalidKey, LLMQuotaExhausted, LLMModelNotFound)):
                    retryable, action = False, "fatal"
                elif isinstance(e, (LLMRateLimited, LLMNetworkError)):
                    retryable, action = True, "retry"
                else:
                    error_str = str(e).lower()
                    retryable, action = _classify_error(error_str)

                if not retryable:
                    logger.error("Section '%s' non-retryable error (%s): %s", sid, action, e)
                    if action == "degrade":
                        return "", "error"  # 降级：返回空内容
                    raise ReportGenerationError(f"Section '{sid}' fatal error: {e}") from e

                # 上下文溢出：保尾截断后重试（一次机会）。写作任务与参考资料
                # 位于 prompt 尾部必须保住；digests 段在头部被优先裁剪，
                # 从段落边界切割避免断行
                if action == "truncate":
                    logger.warning("Section '%s' context overflow, truncating prompt head and retrying", sid)
                    if len(user_prompt) > 4000:
                        tail = user_prompt[-4000:]
                        nl = tail.find("\n")
                        user_prompt = (
                            "（注：前文摘要因上下文长度限制已截断）\n"
                            + (tail[nl + 1:] if nl != -1 else tail)
                        )
                    # 重置 messages 以使用截断后的 prompt
                    messages = [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_prompt},
                    ]
                    # 等待后重试
                    await asyncio.sleep(1)
                    continue

                logger.error("Section '%s' attempt %d failed (%s): %s",
                             sid, attempt + 1, action, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)  # 指数退避
                else:
                    raise ReportGenerationError(
                        f"Section '{sid}' generation failed after {MAX_RETRIES} attempts: {e}"
                    ) from e

        return "", "error"  # unreachable

    async def _summarize(self, text: str, max_words: Optional[int] = None) -> str:
        """为生成的文本生成主题纲要（纲要式 digest，长度按 report_depth：
        full 120 / brief 60 字，§3.5；不搬运具体数值与引用编号）。"""
        if max_words is None:
            max_words = 120 if self.report_depth == "full" else 60
        try:
            messages = build_digest_prompt(text, max_words)
            result = await self.llm_client.chat_full(messages, self.llm_settings)
            self.token_tracker.record("global", "digest", result["usage"])
            return result["content"]
        except Exception as e:
            logger.warning("Failed to generate digest: %s", e)
            return text[:200] + "..." if len(text) > 200 else text

    async def _extract_numbers(self) -> str:
        """从已生成的 standard 章节中提取关键数据。"""
        combined = "\n\n".join(
            self.outputs.get(sid, "") for sid in self.order
            if self.outputs.get(sid) and not self.outputs[sid].startswith("（")
        )
        if not combined:
            return ""

        try:
            messages = build_extract_numbers_prompt(combined[:8000])
            result = await self.llm_client.chat_full(messages, self.llm_settings)
            self.token_tracker.record("global", "extract_numbers", result["usage"])
            return result["content"]
        except Exception as e:
            logger.warning("Failed to extract numbers: %s", e)
            return ""

    def _get_section_title(self, section_id: str) -> str:
        """根据 section_id 查找标题。"""
        for sec in self.sections:
            if sec.get("section_id") == section_id:
                return sec.get("title", "")
        return section_id

    def _assemble_full_text(self) -> str:
        """按执行顺序拼接所有章节内容为完整文本。"""
        parts = []
        post_body_front = []
        post_body_end = []

        for sec in self.sections:
            sid = sec.get("section_id", "")
            content = self.outputs.get(sid, "")
            if not content:
                continue

            title = sec.get("title", "")
            if sec.get("compose_mode") == "post_body":
                position = sec.get("position", "end")
                section_text = f"## {title}\n\n{content}"
                if position == "front":
                    post_body_front.append(section_text)
                else:
                    post_body_end.append(section_text)
            else:
                parts.append(f"## {title}\n\n{content}")

        full_parts = post_body_front + parts + post_body_end
        return "\n\n---\n\n".join(full_parts)
