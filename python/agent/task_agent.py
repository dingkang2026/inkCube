import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator

import activity_events
from llm.client import LLMClient
from llm.budget import count_tokens, context_window, output_tokens
from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境
from db.database import Database
from tools.code_executor import CodeExecutorTool, check_code_danger
from tools.shell_tool import ShellTool, check_safe_command, check_domain_whitelist
from tools.file_ops import FileOpsTool
from tools.code_search import CodeSearchTool
from tools.code_lint import CodeLintTool
from tools.code_format import CodeFormatTool
from tools.package_manager import PackageManagerTool
from tools.project_scaffold import ProjectScaffoldTool
from tools.task_knowledge_search import TaskKnowledgeSearchTool
from tools.doc_generator import DocGeneratorTool
from tools.vision import VisionTool
from tools.web_search import WebSearchTool
from tools.repo_map import RepoMapTool
from tools.skill_runner import SkillRunnerTool
from tools.git_ops import GitOpsTool
from tools.lsp_client import LSPClientTool
from tools.venv_manager import (
    UvExclusionRequired,
    ensure_venv,
    ensure_venv_blocking,
    get_venv_python,
    is_preinstall_done,
    is_uv_exclusion_verified,
)
from skills.registry import SkillRegistry
from skills.context import SkillContext
from skills.loader import AgentSkillAdapter, SKILL_GUIDE_MAX_CHARS
from agent.context_manager import (
    ContextManager, build_git_context, detect_project_context,
    summarize_tool_result_line,
)
from errors import LLMNetworkError, InkCubeError, actionable
from paths import get_data_dir

logger = logging.getLogger(__name__)


def _llm_error_msg(e: Exception) -> str:
    """LLM 调用失败的用户可行动文案（存为助手消息 + 流式推送，0.4 §7.3 v3.6）。

    D1 错误契约：网络类区分超时/连接两个 code、配置/凭据类经 errors.actionable()
    产出 `INKCUBE_CODE:<CODE>|<detail>` 协议串，前端渲染前经 translateStoredError
    还原为当前语言；未知异常透传 detail（第三方异常串原文展示，自动降级）。
    """
    if isinstance(e, LLMNetworkError):
        detail = getattr(e, "detail", "") or str(e)
        lowered = detail.lower()
        # httpx 超时惯用表述为 "timed out"（带空格），与 "timeout" 一并匹配
        if "超时" in detail or "timed out" in lowered or "timeout" in lowered:
            return "INKCUBE_CODE:LLM_NETWORK_TIMEOUT"
        return "INKCUBE_CODE:LLM_NETWORK_ERROR"
    if isinstance(e, InkCubeError):
        return f"❌ {actionable(e)}"
    return f"调用模型出错: {getattr(e, 'detail', '') or str(e)}"


def _venv_env_needs_setup(venv_path: str) -> bool:
    """任务 venv 首用环境预检（2.8 §5.5 step_note 可见性）。

    venv python 与预装 marker 均在位才算就绪（与 _ensure_task_venv 快路径
    同一判据）；venv_path 为空（任务无 venv，chat-only）不会发生预装，
    返回 False。预检为真时马上要发生同步预装（3~60s+ 黑盒），调用方推
    step_note 呈现给用户。判据本为通用 venv 判据，2026-10-13 随 tool 链
    （code_executor/shell/package_manager 首用可见性）接入由 skill 专属
    升为 tool/skill 共用，更名（原名 _skill_env_needs_setup）。
    """
    if not venv_path:
        return False
    return not (
        os.path.isfile(get_venv_python(venv_path))
        and is_preinstall_done(venv_path)
    )


def _skill_env_setup_failed(venv_python: str, result_str: str) -> bool:
    """环境终态判定（2.8 §5.5）：venv 未就绪（创建失败回退空串）或 loader
    报 retryable 预装失败 → 环境失败；脚本自身业务失败（exit_code≠0 等）
    不算——那说明环境已就绪，只是脚本没跑成（业务错误在执行结果里可见）。"""
    if not venv_python:
        return True
    try:
        rj = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(rj, dict) and bool(rj.get("retryable"))

# ── Modular System Prompt Components ──
# Each section is independently maintained and dynamically assembled.

_PROMPT_IDENTITY = """你是一个编程助手“墨方-任务”。你可以使用工具来编写和执行代码、操作文件。
始终使用用户提问的语言回复。"""

_PROMPT_IDENTITY_GENERAL = """你是一个智能助手"墨方-任务"。你可以使用工具来搜索信息、获取网页内容、生成文档、操作文件。
专注于帮助用户完成研究、写作、文档生成等通用任务。
始终使用用户提问的语言回复。

💡 提示：当前为通用模式，已有文件操作和 Shell 命令能力。如需执行代码、代码搜索、代码检查、包管理等高级编程功能，可建议用户切换到编程模式（🛠）。"""

_PROMPT_EFFICIENCY = """⚠️ 效率原则：
- 一旦获得了回答用户问题所需的信息，立即给出最终回答（回答正文就是交付物）
- 不要输出 Final Answer 之类的标记词，不要重复总结已完成的内容
- 不要在已有结果的情况下继续探索额外细节
- 通常 3-5 次工具调用就足以完成一个任务
- 如果工具执行失败，根据返回的 error_type 和 suggestion 修正，最多重试 3 次"""

_PROMPT_VERIFICATION = """✅ 验证原则：
- 创建或修改代码后，用 code_executor 执行验证（至少运行一次确认无语法错误）
- 执行失败时，根据 error_type/error_line/suggestion 定位并修正问题
- 不要假设代码正确——必须实际执行验证
- 修改已有文件优先使用 file_ops 的 search_replace 操作，不要整文件重写
- write/search_replace 会自动创建 .bak 备份，.py 文件编辑后自动语法检查
- 编辑失败时可用 file_ops 的 restore_backup 操作从备份恢复"""

_PROMPT_EDIT_FORMAT = """📝 代码编辑指导：
- 修改已有文件时，优先使用 search_replace（比整文件重写更高效）
- search_replace 的 old_content 应包含足够的上下文以唯一定位（建议包含函数签名或前后几行）
- 创建新文件用 write/create
- 编辑后建议用 code_executor 运行验证（至少一次）
- 如果需要了解项目结构，先调用 repo_map 获取全局视图
- 如果需要搜索特定代码，使用 code_search 而非逐个文件 read"""

_PROMPT_SELF_REVIEW = """🔍 自我审查原则：
- 在给出最终回答之前，检查所有修改的文件是否已保存且语法正确
- 如果编辑了代码，至少执行一次验证（code_executor 或 code_lint）
- 如果有遗漏的需求点，继续执行而不是急于总结
- 如果一切就绪，直接给出最终回答"""

_PROMPT_RICH_TEXT = """📊 富文本支持：
你可以在最终回答中使用 Markdown 格式：
- Mermaid 图表：用 ```mermaid 代码块绘制流程图、时序图、甘特图、状态图、类图等结构化图表
- SVG 图形：直接嵌入 <svg> 标签绘制自定义图形（仅图形元素：rect/circle/text/path/g 等）
- 图表选型：结构化图表（流程/时序/甘特/状态/类图）一律优先 Mermaid（代码可维护、
  渲染稳定、便于增量修改）；SVG 仅用于 Mermaid 表达不了的自由布局示意图、带图标/
  装饰效果的视觉设计，禁止用 SVG 手绘流程图等结构化图表
- 尺寸约束：图形最终会嵌入 A4 文档或 PPT，按目标介质设计——SVG 用显式宽度
  （如 width="640"）+ 等比 viewBox，纵横比控制在 4:3~16:10（竖版不超过 3:4），
  文字最小 14px（超宽图缩到页宽后不可读）；PPT 配图用 16:9（viewBox 1280×720）；
  Mermaid 节点较多时优先纵向布局（graph TD），避免超宽横向图，节点过多拆分多图或用 subgraph 分组
- 行内样式：用 <span style="color:#663399">标注颜色/强调，style 仅支持视觉属性
  （color、background、font-weight、font-size、padding、border-radius 等）；
  不要在表格单元格内使用 HTML 标签
- 代码块、表格、列表等标准 Markdown 语法"""

_PROMPT_INCREMENTAL = """🔁 增量修改原则：
- 修改类请求必须基于前面的结果与 workspace 现有产物：先 read 确认现状再动手
  （历史消息可能被上下文压缩，文件内容才是最终权威）
- 除非用户明确要求重做，禁止推倒重来，禁止重新生成已有且仍正确的内容
- 用户明确指出修改点时（某一段/某一章/某个函数），只改该处：
  优先 file_ops 的 search_replace / apply_diff 定点修改，不要整文件重写
- 长文档/报告按章节逐个写入独立文件，全部完成后组装成稿；
  之后修改某一章时只重写该章文件并重新组装，禁止全文重生成"""

# ── Planner / Editor dual-model prompts ──
_PLANNER_PROMPT = """你是“架构师”，负责分析用户需求并制定执行计划。
你不需要执行任何工具，只需输出清晰的执行计划。

请按照以下格式输出：

Plan:
1. [步骤描述] — 涉及文件: [文件路径]
2. [步骤描述] — 涉及文件: [文件路径]
...

Strategy: [整体策略说明]

注意：
- 只分析需求和规划步骤，不要执行任何操作
- 明确指出需要修改的文件
- 如果任务简单（1-2步），直接给出计划即可"""

# ── Context compression constants ──
# Snip: truncate individual tool outputs longer than this
SNIP_THRESHOLD = 32000  # chars (~8K tokens, ~6% of 128K context)
# After collapse, keep this many recent messages intact
KEEP_RECENT = 10

# Confirmation wait windows (1.1 §7.1/§7.2): on timeout the action is skipped
# (fail-safe — never auto-approve third-party code), the dialog is dismissed
# via confirm_expired, and the skip is traced in the action's observation.
SHELL_CONFIRM_TIMEOUT = 120
SKILL_CONFIRM_TIMEOUT = 180

# 预算软着陆（1.1 §5.6，2026-10-16）：剩 BUDGET_WARNING_LEFT 轮时注入一次
# 收束提醒，把「末轮硬切断、断点落在半成品中间」变为 LLM 自主选择安全断
# 点软着陆。max_iter < 3 时预算过小不注入；恢复分支过滤旧提醒（新预算下
# 旧轮数提示失效）。
BUDGET_WARNING_LEFT = 3
_BUDGET_WARNING_PREFIX = "[系统提示] 迭代预算"

# PPT 任务参数预设（2.3 §3.2）：settings 专用键（task_ppt_*，defaults 默认
# None）未设置时生效，显式设置即覆盖。必须用专用键而非通用键承载——
# get_settings() 白名单恒含 task_max_iterations/task_timeout（60/30），以
# 「get 为 None」判定未设置对通用键永不成立，用通用键则预设整体失效
#（2026-10-17 发现：E1 起预设从未生效，ppt 实际以 60 轮/300s 运行）。
# 实测依据：ppt-master 真实生成 169 轮/41 分钟（60 轮上限被切断 3 次），
# svg_to_pptx 大页数转换单命令远超 30s；预算收束提前量同步放大
#（240 轮任务末 3 轮来不及保存，剩 24 轮即提醒收束）。
ORIGIN_PRESETS: dict[str, dict] = {
    "ppt": {
        "task_ppt_max_iterations": 240,
        "task_ppt_timeout": 600,
    },
}

# Tools carrying a task-venv interpreter config. Lazy venv creation triggers
# only when one of these actually executes with a venv path set — general-mode
# tools are created without venv config (see _create_task_tools), so chat-only
# tasks never materialize a venv on disk.
_VENV_TOOL_ATTRS = {
    "code_executor": "venv_python",
    "shell": "venv_path",
    "package_manager": "venv_path",
    "skill_runner": "task_venv_python",
}


class TaskAgent:
    MAX_CONCURRENT = 3  # max simultaneous running tasks

    def __init__(self, db: Database):
        self.db = db
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._running_task_ids: set[str] = set()
        self._task_iterations: dict[str, int] = {}
        self._run_outcomes: dict[str, str] = {}  # task_id -> completed/failed（终态通知用）
        self._task_messages: dict[str, list[dict]] = {}  # in-memory LLM messages for pause
        self._paused_tasks: set[str] = set()  # paused via pause_task — state must survive error exit

        # Shell confirmation: pending confirmations waiting for user response
        self._pending_confirms: dict[str, dict] = {}
        # Session-level auto-allow: task_id -> True when user clicks "allow all for this session"
        self._session_auto_allow: set[str] = set()
        # Skill confirmation: pending skill confirmations
        self._pending_skill_confirms: dict[str, dict] = {}
        # Skill scan cache: install_path → (mtime, parsed_dict) (Phase 15.4)
        self._skill_scan_cache: dict[str, tuple[float, dict]] = {}

        # Tool instances for prompt description generation (used by _get_tools_description)
        _knowledge_search = TaskKnowledgeSearchTool()
        self.knowledge_search = _knowledge_search  # also referenced by _create_task_tools
        self._tools = {
            "code_executor": CodeExecutorTool(),
            "shell": ShellTool(),
            "file_ops": FileOpsTool(),
            "code_search": CodeSearchTool(),
            "web_search": WebSearchTool(),
            "code_lint": CodeLintTool(),
            "code_format": CodeFormatTool(),
            "package_manager": PackageManagerTool(),
            "project_scaffold": ProjectScaffoldTool(),
            "knowledge_search": _knowledge_search,
            "doc_generator": DocGeneratorTool(),
            "vision": VisionTool(db=db),
            "repo_map": RepoMapTool(),
            "skill_runner": SkillRunnerTool(db=db),
            "git_ops": GitOpsTool(),
            "lsp_client": LSPClientTool(),
        }

    @staticmethod
    def _apply_task_routing(settings: dict, origin: str) -> dict:
        """按 origin 解析主对话模型档位（llm/routing.py 档位制）。

        PPT 工作流在任务对话内执行（2.3 §3.2）：origin='ppt' 任务按
        ppt_model 档位解析（唯一默认 high 的业务线，走 get_task_settings
        统一入口留 routing 日志）；其余任务按 task_model 档位。解析结果
        写入 settings["model"]，LLMClient 契约不变。
        """
        if origin == "ppt":
            from llm.routing import get_task_settings
            return get_task_settings(settings, "ppt")
        from llm.routing import resolve_model
        settings["model"] = resolve_model(
            settings, str(settings.get("task_model") or "standard"))
        return settings

    @staticmethod
    def _apply_origin_presets(settings: dict, origin: str) -> dict:
        """origin 参数预设统一应用（ORIGIN_PRESETS，2.3 §3.2）。

        PPT 参数走专用键（task_ppt_*）：get 到 None 即未设置 → 填预设值，
        显式设置即覆盖。专用键与通用键（task_max_iterations / task_timeout，
        chat 设置页字段）完全解耦——chat 的通用设置不渗透 ppt 任务，反之亦然。
        预设值落地到通用键（task_ppt_X → task_X），下游消费点无需感知 origin。
        独立成纯函数以便单测（run() 内调用，行为不变）。
        """
        for key, val in ORIGIN_PRESETS.get(origin, {}).items():
            if settings.get(key) is None:
                settings[key] = val
            settings["task_" + key.removeprefix("task_ppt_")] = settings[key]
        return settings

    def request_cancel(self, task_id: str | None = None):
        """Request cancellation for a specific task, or all tasks if task_id is None."""
        if task_id:
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
        else:
            for event in self._cancel_events.values():
                event.set()

    def respond_confirm(self, task_id: str, confirmed: bool, allow_session: bool = False):
        """Respond to a pending shell confirmation from the frontend.
        
        Args:
            confirmed: Whether the user allowed the command.
            allow_session: If True and confirmed, auto-allow all subsequent
                          shell commands for this task session.
        """
        pending = self._pending_confirms.get(task_id)
        if pending:
            # 暂停已请求（cancel_event set）：用户意图是停止，此后的「允许」
            # 一律视为拒绝——否则暂停后残留的确认弹窗仍会放行命令执行。
            cancel_event = self._cancel_events.get(task_id)
            if cancel_event is not None and cancel_event.is_set():
                confirmed = False
                allow_session = False
            pending["confirmed"] = confirmed
            if confirmed and allow_session:
                self._session_auto_allow.add(task_id)
            pending["event"].set()

    def respond_skill_confirm(self, task_id: str, confirmed: bool) -> bool:
        """Respond to a pending skill confirmation from the frontend.

        Confirming also persists permanent trust (needs_confirm=0, 1.1 §7.2):
        the task dialog IS the first-use acknowledgement point, so the skill
        never prompts again (settings-page toggle can still disable it).
        Returns False when no pending confirmation exists (e.g. already timed
        out) so the frontend can tell the user the click had no effect.
        """
        pending = self._pending_skill_confirms.get(task_id)
        if not pending:
            return False
        # 暂停已请求：同 respond_confirm，此后的「允许」一律视为拒绝
        cancel_event = self._cancel_events.get(task_id)
        if cancel_event is not None and cancel_event.is_set():
            confirmed = False
        pending["confirmed"] = confirmed
        if confirmed:
            try:
                self.db.confirm_skill_by_name(pending.get("skill_name", ""))
            except Exception as e:
                # Degraded to this-run-only allow on DB failure; next run
                # re-prompts (logged for diagnosis, 1.1 §7.2)
                logger.warning("Failed to persist skill confirmation: %s", e)
        pending["event"].set()
        return True

    @staticmethod
    def _prune_unpaired_tool_messages(messages: list[dict]) -> list[dict]:
        """移除未配对的 tool_calls 消息段，保证保存的状态可安全恢复。

        暂停可能落在工具执行中途：快照尾部是 assistant(tool_calls) 而其
        tool result 缺失或不全。直接保存后，恢复时该消息序列会被
        OpenAI 兼容 API 以 400 拒绝（tool_calls 必须与 tool 消息一一配对），
        任务恢复必然失败。此处按段校验：assistant(tool_calls) 之后直到
        下一条非 tool 消息前的 tool result 覆盖全部 tool_call_id 才保留，
        否则整段丢弃（回到上一条完整迭代边界，代价是重做当轮工具调用）。
        """
        pruned: list[dict] = []
        i = 0
        n = len(messages)
        while i < n:
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                expected = {tc.get("id") for tc in msg["tool_calls"] if tc.get("id")}
                j = i + 1
                got: set = set()
                while j < n and messages[j].get("role") == "tool":
                    got.add(messages[j].get("tool_call_id"))
                    j += 1
                if expected and expected <= got:
                    pruned.append(msg)
                    pruned.extend(messages[i + 1:j])
                # 未配对（或缺 tool_call_id）：整段丢弃，不保留
                i = j
            else:
                pruned.append(msg)
                i += 1
        return pruned

    def pause_task(self, task_id: str, messages: list[dict], iteration: int, recent_errors: list[str]) -> bool:
        """Pause a running task: save state and request cancellation."""
        state = {
            "messages": self._prune_unpaired_tool_messages(messages),
            "iteration": iteration,
            "recent_errors": recent_errors,
            "auto_saved": True,  # Mark as pausable; also enables startup detection
        }
        ok = self.db.save_agent_state(task_id, state)
        if ok:
            self._paused_tasks.add(task_id)
            self.request_cancel(task_id)
        return ok

    def _reset_cancel(self, task_id: str):
        """Create a fresh cancel event for a specific task."""
        self._cancel_events[task_id] = asyncio.Event()

    @property
    def running_count(self) -> int:
        return len(self._running_task_ids)

    def can_start_task(self) -> bool:
        return len(self._running_task_ids) < self.MAX_CONCURRENT

    def _register_external_skills(self, skill_registry: SkillRegistry, tools: dict | None = None):
        """Load external skills from DB and register them into SkillRegistry.

        Also registers each skill directory as a file_ops read-only root so
        the LLM can read references/ on demand (Agent Skills L3 progressive
        disclosure, 1.1 §4.4).
        Supports Agent Skills standard format (SKILL.md).
        Uses mtime-based cache to avoid re-parsing unchanged SKILL.md files (Phase 15.4).
        """
        try:
            from skills.parser import parse_skill_md, discover_skill_type, validate_skill_md
            all_skills = self.db.list_skills()
            file_ops = tools.get("file_ops") if tools else None
            fc_skill_names: list[str] = []
            for skill_info in all_skills:
                if not skill_info.get("enabled", 1):
                    continue
                install_path = skill_info.get("install_path", "")
                if not install_path or not os.path.isdir(install_path):
                    continue

                skill_type = discover_skill_type(install_path)

                if skill_type == "agent_skill":
                    skill_md_path = os.path.join(install_path, "SKILL.md")

                    # Check cache: skip re-parse if SKILL.md hasn't changed
                    cached = self._skill_scan_cache.get(install_path)
                    parsed = None
                    if cached:
                        try:
                            current_mtime = os.path.getmtime(skill_md_path)
                            if current_mtime == cached[0]:
                                parsed = cached[1]
                                logger.debug("Skill cache hit: %s", install_path)
                        except OSError:
                            pass

                    if parsed is None:
                        parsed = parse_skill_md(skill_md_path)
                        if not parsed or not parsed.get("frontmatter"):
                            logger.warning("Invalid SKILL.md in %s, skipping", install_path)
                            continue
                        errors = validate_skill_md(parsed)
                        if errors:
                            logger.warning("Invalid SKILL.md in %s: %s", install_path, "; ".join(errors))
                            continue
                        # Update cache
                        try:
                            mtime = os.path.getmtime(skill_md_path)
                            self._skill_scan_cache[install_path] = (mtime, parsed)
                        except OSError:
                            pass

                    adapter = AgentSkillAdapter(install_path, parsed, skill_info)
                    skill_registry.register(adapter, {"type": "agent_skill", "enabled": True})
                    fc_skill_names.append(adapter.name)
                    # L3: keep skill files readable (read/list only) for this task
                    if file_ops is not None and hasattr(file_ops, "add_readonly_root"):
                        file_ops.add_readonly_root(install_path)
                    logger.info("Registered Agent Skill: %s (from %s)", adapter.name, install_path)

                else:
                    logger.debug("No valid skill manifest found in %s, skipping", install_path)

            # 双通道去冗（2026-10-16 天眼查任务实证）：CLI 型 Agent Skill 已
            # 注册为独立 FC 工具，skill_runner 的 L1 清单再列一遍同名 Skill
            # 会诱导模型先经 skill_runner「激活」（参数约定不同，首调必错）。
            # 两个实例都要标记：执行走 per-task 实例，系统提示工具清单读
            # __init__ 实例（_build_system_prompt → _get_tools_description）
            if fc_skill_names:
                for runner in (tools.get("skill_runner") if tools else None,
                               self._tools.get("skill_runner")):
                    if runner is not None:
                        runner.mark_fc_registered(fc_skill_names)
        except Exception as e:
            logger.warning("Failed to load external skills: %s", e)

    def _get_tools_description(self, mode: str = "coding") -> str:
        """Generate rich tool descriptions for the system prompt."""
        # In general mode, show file_ops + shell + web/knowledge/doc tools
        if mode == "general":
            general_tools = ["file_ops", "shell", "web_search", "knowledge_search", "vision", "doc_generator", "repo_map", "skill_runner", "git_ops", "lsp_client"]
            sections = []
            for name in general_tools:
                if name in self._tools:
                    sections.append(self._tools[name].prompt_description)
            return "\n\n".join(sections)
        # Coding mode: show all tools
        sections = []
        for name, tool in self._tools.items():
            sections.append(tool.prompt_description)
        return "\n\n".join(sections)

    def _build_system_prompt(self, workspace_path: str, mode: str = "coding") -> str:
        """Dynamically assemble the system prompt from modular components.

        Enforces a token budget (Phase 15.2): if the prompt exceeds the budget,
        low-priority sections (project config, project context, git context)
        are dropped.
        """
        PROMPT_BUDGET = 4000  # tokens
        identity = _PROMPT_IDENTITY_GENERAL if mode == "general" else _PROMPT_IDENTITY
        # Inject current date/time so the agent understands "today", "yesterday", etc.
        now = datetime.now()
        _WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday_cn = _WEEKDAY_CN[now.weekday()]
        date_time_line = f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} ({weekday_cn})"
        parts = [
            identity,
            date_time_line,
            "",
            "可用工具:",
            self._get_tools_description(mode),
            "",
            f"工作目录: {workspace_path}",
            "运行环境: 任务代码在用户本机的桌面会话中执行，GUI 程序的窗口会直接显示在用户屏幕上。",
            "所有文件操作的路径都是相对于工作目录的。",
        ]
        if mode == "coding":
            parts.append("每个任务有独立的 Python 虚拟环境，pip install 的包只在此任务中可用。")
        parts.extend([
            "",
            _PROMPT_EFFICIENCY,
        ])
        # 增量修改原则两模式共用：file_ops（含 search_replace）coding/general 均可用；
        # general（研究/写作/文档生成）恰是「长报告分章、不要整体重生成」的高发场景
        parts.extend(["", _PROMPT_INCREMENTAL])
        if mode == "coding":
            parts.extend(["", _PROMPT_VERIFICATION])
            parts.extend(["", _PROMPT_EDIT_FORMAT])
            parts.extend(["", _PROMPT_SELF_REVIEW])
        parts.extend(["", _PROMPT_RICH_TEXT])

        # ── Optional sections (dropped if over budget) ──
        optional_sections: list[tuple[str, list[str]]] = []

        # Inject git context (branch, recent commits, uncommitted changes)
        git_ctx = build_git_context(workspace_path)
        if git_ctx:
            optional_sections.append(("git", ["", git_ctx]))

        # Inject auto-detected project context (tech stack)
        project_ctx = detect_project_context(workspace_path)
        if project_ctx:
            optional_sections.append(("project_ctx", ["", project_ctx]))

        # Inject project-level config if present
        project_config = self._load_project_config(workspace_path)
        if project_config:
            optional_sections.append(("project_config", ["", "── 项目配置 ──", project_config]))

        # Inject repo-map summary (PageRank-ranked, token-budgeted) — Phase 17
        try:
            _repo_map_tmp = RepoMapTool(workspace_dir=workspace_path)
            _repo_summary = _repo_map_tmp.generate_summary_for_prompt(token_budget=512)
            if _repo_summary:
                optional_sections.append(("repo_map", ["", "── 项目结构 (PageRank 排序) ──", _repo_summary]))
        except Exception:
            pass  # repo-map generation is best-effort

        # Budget check: drop low-priority sections if over budget
        included_sections = list(optional_sections)  # copy
        # 每个可选 section 的值是片段列表（list[str]），需展平后才能 join
        full_parts = parts + [seg for _, s in included_sections for seg in s]
        full_prompt = "\n".join(full_parts)
        prompt_tokens = count_tokens(full_prompt)

        if prompt_tokens > PROMPT_BUDGET:
            # Drop sections from lowest priority first
            for name, _ in reversed(list(included_sections)):
                included_sections = [(n, s) for n, s in included_sections if n != name]
                full_parts = parts + [seg for _, s in included_sections for seg in s]
                test_prompt = "\n".join(full_parts)
                test_tokens = count_tokens(test_prompt)
                if test_tokens <= PROMPT_BUDGET:
                    logger.warning(
                        "System prompt truncated (dropped %s): %d tokens (budget: %d)",
                        name, test_tokens, PROMPT_BUDGET,
                    )
                    return test_prompt
            full_prompt = "\n".join(full_parts)
            logger.warning(
                "System prompt truncated (dropped all optional): %d tokens (budget: %d)",
                count_tokens(full_prompt), PROMPT_BUDGET,
            )

        return full_prompt

    @staticmethod
    def _load_project_config(workspace_path: str) -> str | None:
        """Load project-level agent config from workspace root if it exists.

        Checks multiple filenames for cross-tool compatibility:
        - inkcube.md / INKCUBE.md (native)
        - .cursorrules (Cursor compatibility)
        - AGENTS.md (Aider / Codex compatibility)
        """
        config_names = ("inkcube.md", "INKCUBE.md", ".cursorrules", "AGENTS.md")
        for name in config_names:
            config_path = os.path.join(workspace_path, name)
            if os.path.isfile(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # Limit to 4000 chars to avoid bloating the prompt
                    if len(content) > 4000:
                        content = content[:4000] + "\n\n[...truncated, max 4000 chars]"
                    return content
                except Exception:
                    return None
        return None

    async def _ensure_task_venv(self, task_id: str, venv_path: str) -> str:
        """懒创建任务 venv：任务创建时仅登记路径，首次执行代码/装包/跑 Skill 时落盘。

        快路径要求 venv 与预装 marker 均在位（零创建开销）——marker 缺失时
        落入 ensure_venv 补跑预装（create_venv 幂等 no-op），兑现「预装失败
        不写 marker、下次 ensure 重试」的承诺（含跨重启的中断预装补装）。
        marker 缺失后区分两种原因（2026-10-13 误报修复）：白名单未验证 →
        抛 UvExclusionRequired（真阻断）；已验证 → ensure_venv_blocking
        同步等待后台预装完成或自愈补装——此前统一按白名单误报，首次任务
        必吃误导性授权指引（实测 cb44ad52：预装 ~20s 窗口内首用必中）。
        创建后校验任务仍存在：delete_task 在 cancel 等待超时后直接 rmtree
        任务目录，若落在 create_venv 的 makedirs 窗口内目录会被重建，此处
        回收刚落盘的 venv 避免孤儿目录。创建失败/任务已删返回 ""，后续执行
        自动回退系统 Python（code_executor/shell 对不存在的 venv 路径自带
        降级），与旧「创建期失败 → venv_path=''」行为一致。同一任务经
        try_acquire_task 互斥，无并发创建竞态。
        """
        if not venv_path:
            return ""
        if os.path.isfile(get_venv_python(venv_path)) and is_preinstall_done(venv_path):
            return venv_path
        try:
            await asyncio.to_thread(ensure_venv, os.path.dirname(venv_path))
            if self.db.get_task(task_id) is None:
                shutil.rmtree(os.path.dirname(venv_path), ignore_errors=True)
                logger.info("Task %s deleted during venv creation, cleaned %s", task_id, venv_path)
                return ""
            if not is_preinstall_done(venv_path):
                if not is_uv_exclusion_verified():
                    # 前置阻断（2.8 §12 F4）：白名单未验证时后台预装被跳过，
                    # marker 不会出现——不交付空 venv（裸环境 import 必败），
                    # 也不降级系统 Python（污染主环境），抛出给调用方向 LLM
                    # 给出明确的授权指引
                    raise UvExclusionRequired(
                        "杀毒软件白名单未确认，任务运行环境无法就绪；"
                        "请用户在应用顶部横幅完成一次系统授权后重试")
                # 白名单已验证：marker 缺失 = 预装进行中（等后台完成）或曾
                # 失败（自愈补装）。同步等待装完再继续任务；blocking 内部
                # per-venv 锁 + 双检与后台线程互斥，不重复下载。装失败 →
                # venv 在位仅包不全，交由 LLM 按需 package_manager 补装
                #（不降级系统 Python，与 fire-and-forget 失败时的实际行为一致）
                if not await asyncio.to_thread(ensure_venv_blocking, venv_path):
                    logger.warning(
                        "Task %s preinstall failed after blocking wait, "
                        "continuing with partial venv: %s", task_id, venv_path)
            logger.info("Task venv lazily created: %s", venv_path)
            return venv_path
        except UvExclusionRequired:
            raise
        except Exception as e:
            logger.warning("Lazy venv creation failed (%s): %s", venv_path, e)
            return ""

    def _create_task_tools(self, workspace_path: str, venv_path: str = "", settings: dict | None = None, mode: str = "coding", task_id: str = "") -> dict:
        """Create independent tool instances for a single task.
        
        Each concurrent task gets its own tools to avoid workspace/venv conflicts.
        In 'general' mode, returns the general tool subset (file_ops, shell,
        web_search, knowledge_search, vision, doc_generator, repo_map,
        skill_runner, git_ops, lsp_client); coding mode returns the full set.
        """
        doc_generator = DocGeneratorTool()
        doc_generator.set_workspace(workspace_path)

        # Create independent instances for web/knowledge tools (safe for concurrent use)
        web_search = WebSearchTool(
            provider=settings.get("web_search_provider", "") if settings else "",
            tavily_key=settings.get("tavily_key", "") if settings else "",
            zhipu_key=settings.get("zhipu_search_key", "") if settings else "",
            zhipu_engine=settings.get("zhipu_search_engine", "search_pro") if settings else "search_pro",
        )
        knowledge_search = TaskKnowledgeSearchTool()
        if self.knowledge_search.db:
            knowledge_search.configure(
                db=self.knowledge_search.db,
                chroma_persist_dir=self.knowledge_search.chroma_persist_dir,
                models_dir=self.knowledge_search.models_dir,
            )

        # Repo-map tool: per-task workspace-aware instance
        repo_map = RepoMapTool(workspace_dir=workspace_path)

        # Skill runner: per-task instance with DB reference；task_id 供 SkillContext
        # → loader 注入 INKCUBE_TASK_ID → 租约锁同 task 重入判据（2.8 §3.3，缺则
        # 进程异常退出后 60s 内同任务全部被拒）
        skill_runner = SkillRunnerTool(db=self.db)
        skill_runner.task_workspace_path = workspace_path
        skill_runner.task_id = task_id

        # Git ops: per-task workspace-aware instance
        git_ops = GitOpsTool()
        git_ops.set_workspace(workspace_path)

        # LSP client: per-task workspace-aware instance
        lsp_client = LSPClientTool(workspace_dir=workspace_path)

        # VLM 图片理解：per-task workspace-aware instance（无 venv 依赖，
        # 两种模式同构；fetched 只读根与 file_ops 同源）
        vision = VisionTool(db=self.db)
        vision.set_workspace(workspace_path)
        vision.add_readonly_root(os.path.join(get_data_dir(), "fetched"))

        if mode == "general":
            # General mode: file_ops + shell + web/knowledge/doc tools
            file_ops = FileOpsTool()
            shell = ShellTool()
            file_ops.set_workspace(workspace_path)
            shell.set_workspace(workspace_path)
            # Apply workspace quota from settings
            if settings:
                max_mb = settings.get("task_workspace_max_mb", 1024)
                if max_mb > 0:
                    file_ops.max_workspace_bytes = max_mb * 1024 * 1024
            # browser-fetch extract 产物锚 data_dir/fetched（B7）：注册只读根
            # 供 file_ops read/list 用绝对路径回读（写操作永不解析到该根）
            file_ops.add_readonly_root(os.path.join(get_data_dir(), "fetched"))
            return {
                "file_ops": file_ops,
                "shell": shell,
                "web_search": web_search,
                "knowledge_search": knowledge_search,
                "vision": vision,
                "doc_generator": doc_generator,
                "repo_map": repo_map,
                "skill_runner": skill_runner,
                "git_ops": git_ops,
                "lsp_client": lsp_client,
            }

        # Coding mode: full tool set
        code_executor = CodeExecutorTool()
        shell = ShellTool()
        file_ops = FileOpsTool()
        code_search = CodeSearchTool()
        code_lint = CodeLintTool()
        code_format = CodeFormatTool()
        package_manager = PackageManagerTool()
        project_scaffold = ProjectScaffoldTool()

        code_executor.set_workspace(workspace_path)
        shell.set_workspace(workspace_path)
        file_ops.set_workspace(workspace_path)
        code_search.set_workspace(workspace_path)
        code_lint.set_workspace(workspace_path)
        code_format.set_workspace(workspace_path)
        package_manager.set_workspace(workspace_path)
        project_scaffold.set_workspace(workspace_path)

        # Apply workspace quota from settings
        if settings:
            max_mb = settings.get("task_workspace_max_mb", 1024)
            if max_mb > 0:
                file_ops.max_workspace_bytes = max_mb * 1024 * 1024
        # browser-fetch extract 产物锚 data_dir/fetched（B7）：注册只读根
        # 供 file_ops read/list 用绝对路径回读（写操作永不解析到该根）
        file_ops.add_readonly_root(os.path.join(get_data_dir(), "fetched"))

        if venv_path:
            venv_python = get_venv_python(venv_path)
            code_executor.set_venv_python(venv_python)
            shell.set_venv_path(venv_path)
            package_manager.set_venv_path(venv_path)
            skill_runner.task_venv_python = venv_python

        return {
            "code_executor": code_executor,
            "shell": shell,
            "file_ops": file_ops,
            "code_search": code_search,
            "web_search": web_search,
            "code_lint": code_lint,
            "code_format": code_format,
            "package_manager": package_manager,
            "project_scaffold": project_scaffold,
            "knowledge_search": knowledge_search,
            "doc_generator": doc_generator,
            "vision": vision,
            "repo_map": repo_map,
            "skill_runner": skill_runner,
            "git_ops": git_ops,
            "lsp_client": lsp_client,
        }


    # ── Edit-Verify: Lint Hint Enrichment ──
    # After file edit, if post_execute reports lint_warning, enrich the result
    # so the LLM sees the warning prominently. The LLM fixes it in the next iteration.
    # (Real auto-fix loop is in Phase 11 — _edit_verify_loop)

    @staticmethod
    def _enrich_result_with_lint_hint(result_str: str, tool_params: dict) -> str:
        """If a file edit produced a lint_warning, add a hint for the LLM.

        This does NOT auto-fix — it enriches the result so the LLM sees
        the warning prominently and can fix it in the next iteration.
        """
        try:
            result_obj = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            return result_str

        if not result_obj.get("lint_warning"):
            return result_str

        fix_path = tool_params.get("path", "")
        lint_warning = result_obj["lint_warning"]
        result_obj["fix_hint"] = (
            f"⚠️ 编辑后检测到问题: {lint_warning}\n"
            f"请使用 search_replace 或 apply_diff 修复 {fix_path} 中的此问题。"
        )
        logger.info("Lint hint added for %s: %s", fix_path, lint_warning[:100])
        return json.dumps(result_obj, ensure_ascii=False)

    # ── Edit-Verify Loop (Phase 11) ──
    # Real auto-fix: if lint check fails after edit, ask LLM to fix it.

    async def _edit_verify_loop(
        self,
        tool: FileOpsTool,
        tool_params: dict,
        result_str: str,
        task_llm: LLMClient,
        settings: dict,
        max_retries: int = 3,
    ) -> str:
        """Edit-verify-fix loop: if lint check fails, auto-fix up to N times.

        Returns the (possibly updated) result_str.
        """
        fix_path = tool_params.get("path", "")
        logger.info(
            "Edit-verify loop entered: path=%s action=%s",
            fix_path, tool_params.get("action", ""),
        )
        for attempt in range(max_retries):
            # Check if there's a lint error to fix
            try:
                result_obj = json.loads(result_str)
            except (json.JSONDecodeError, TypeError):
                break

            if not result_obj.get("lint_warning"):
                break  # No error, done

            lint_warning = result_obj["lint_warning"]
            logger.info(
                "Edit-verify loop: lint error on %s: %s (attempt %d/%d)",
                fix_path, lint_warning[:200], attempt + 1, max_retries,
            )

            # Read current file content
            try:
                read_result = await tool.execute(action="read", path=fix_path)
                read_obj = json.loads(read_result)
                current_content = read_obj.get("content", "")
            except (json.JSONDecodeError, TypeError, Exception):
                break

            if not current_content:
                break

            # Ask LLM to fix the error
            fix_prompt = [
                {"role": "system", "content": "你是一个代码修复专家。修复语法/lint 错误，不要改变功能。"},
                {"role": "user", "content": (
                    f"文件 {fix_path} 编辑后出现以下错误：\n"
                    f"{lint_warning}\n\n"
                    f"当前文件内容：\n```\n{current_content[:5000]}\n```\n\n"
                    f"请输出修复后的完整文件内容。只输出代码，不要解释。"
                )},
            ]

            try:
                fixed_content = (await task_llm.chat_full(fix_prompt))["content"]
                if not fixed_content:
                    logger.warning(
                        "Edit-verify loop: LLM returned empty fix for %s (attempt %d/%d)",
                        fix_path, attempt + 1, max_retries,
                    )
                    break
                logger.info(
                    "Edit-verify loop: LLM fix response len=%d for %s (attempt %d/%d)",
                    len(fixed_content), fix_path, attempt + 1, max_retries,
                )

                # Strip markdown code fences if present
                fixed_content = fixed_content.strip()
                if fixed_content.startswith("```"):
                    lines = fixed_content.split("\n")
                    lines = lines[1:]  # remove opening fence
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    fixed_content = "\n".join(lines)

                # Guard: never overwrite a file with empty content
                if not fixed_content.strip():
                    logger.warning(
                        "Edit-verify loop: LLM returned empty fix for %s, skipping (attempt %d/%d)",
                        fix_path, attempt + 1, max_retries,
                    )
                    break

                # Apply the fix
                fix_result = await tool.execute(
                    action="write", path=fix_path, content=fixed_content,
                )
                result_str = fix_result

                # Re-validate via post_execute
                result_str = await tool.post_execute(result_str, action="write", path=fix_path)

                logger.info(
                    "Edit-verify loop: auto-fix applied for %s (attempt %d/%d)",
                    fix_path, attempt + 1, max_retries,
                )
            except Exception as e:
                logger.warning("Edit-verify: auto-fix failed: %s", e)
                break

        return result_str

    # ── Execution Logging ──

    @staticmethod
    def _log_execution(task_id: str, tool_name: str, tool_input: dict, result: str, duration_ms: int):
        """Write execution log entry to task's execution.log file."""
        try:
            log_dir = os.path.join(get_data_dir(), "tasks", task_id)
            if not os.path.isdir(log_dir):
                return
            log_path = os.path.join(log_dir, "execution.log")
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            # Truncate result for log (increased from 500 to 2000)
            result_preview = result[:2000] + "..." if len(result) > 2000 else result
            # For input, show keys + short value previews (not truncated blob)
            input_parts = []
            for k, v in tool_input.items():
                if isinstance(v, str) and len(v) > 100:
                    input_parts.append(f"{k}=<{len(v)}chars>{v[:80]}...")
                else:
                    input_parts.append(f"{k}={v}")
            input_preview = ", ".join(input_parts)
            if len(input_preview) > 500:
                input_preview = input_preview[:500] + "..."
            entry = f"[{ts}] [INFO] [{tool_name}] input={{{input_preview}}} | duration={duration_ms}ms | result={result_preview}\n"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass  # logging failure should not break the agent

    # ── Slash Command Handling ──

    def _is_slash_command(self, user_message: str) -> tuple[str, str] | None:
        """Check if user_message is a slash command.

        Returns (cmd, params_str) or None.
        """
        text = user_message.strip()
        if not text.startswith("/"):
            return None
        parts = text.split(None, 1)
        cmd = parts[0][1:]  # strip leading /
        if not cmd:
            return None
        params_str = parts[1] if len(parts) > 1 else ""
        return (cmd, params_str)

    def _slash_routing(self, user_message: str, tools: dict) -> tuple[tuple[str, str] | None, bool]:
        """Route a slash command: returns (parsed, direct_execute).

        parsed is (cmd, params) when the message is a slash command, else None.
        direct_execute=True means _execute_slash_command handles it directly
        (bypassing the LLM): /help, unknown skill names, or no skill_runner.
        Installed Agent Skills always return direct_execute=False — the input
        (often natural language like「回声测试」) is translated into CLI args
        by the LLM in the normal agent loop. A static CLI-vs-NL pre-check over
        SKILL.md prose is unreliable:「何时使用」bullet lines can false-match
        as CLI syntax.
        """
        parsed = self._is_slash_command(user_message)
        if not parsed:
            return None, False
        cmd, _params = parsed
        if cmd == "help":
            return parsed, True
        sr = tools.get("skill_runner")
        if not sr:
            return parsed, True
        sr._refresh_cache()
        return parsed, sr._find_skill(cmd) is None

    @staticmethod
    def _build_slash_hint(cmd: str, params: str, guide: str = "", refs_note: str = "") -> str:
        """Build the user-role hint injected when a slash-invoked Agent Skill
        falls through to the agent loop.

        ``guide`` is the skill's SKILL.md body (usage_instructions): without
        it the LLM has no subcommand/flag documentation on its first call and
        must guess (one error + self-correct round); with it the first call
        is usually right.
        ``refs_note`` is the skill's references/ pointer (L3): when present,
        the LLM knows where to read detailed references on demand instead of
        having everything preloaded.
        """
        input_line = (
            f"用户的完整输入: `{params}`\n" if params
            else "用户未提供具体参数，请根据 Skill 能力选择合适的子命令。\n"
        )
        hint = (
            f"[系统提示] 用户通过 `/{cmd}` 调用了名为 `{cmd}` 的 Skill。\n"
            f"你 **必须** 使用工具来执行此 Skill，不要自己直接回答。\n\n"
            f"可用工具（任选其一）：\n"
            f"1. **优先**: 调用 `{cmd}` 函数（将需求翻译为 CLI 参数传入 `command` 字段）\n"
            f"2. **备选**: 调用 `skill_runner` 工具，参数 skill=\"{cmd}\", input={{\"command\": \"CLI参数\"}}\n\n"
            f"示例：用户说「统计字数 Hello」→ command=\"wordcount --text Hello\"\n"
            f"{input_line}"
        )
        if guide:
            hint += (
                f"\n`{cmd}` 使用指南（首次调用即应据此构造正确的 CLI 参数）：\n"
                f"<guide>\n{guide[:SKILL_GUIDE_MAX_CHARS]}\n</guide>\n"
            )
        if refs_note:
            hint += f"\n{refs_note}\n"
        hint += f"请参考 `{cmd}` 的使用指南构造正确的 CLI 参数。"
        return hint

    async def _execute_slash_command(
        self, task_id: str, cmd: str, tools: dict,
    ) -> AsyncGenerator[dict, None]:
        """Execute a non-skill slash command directly, bypassing the LLM.

        Handles /help (usage listing) and unknown skill names (availability
        report). Installed Agent Skills never reach here — _slash_routing
        routes them through the normal agent loop so the LLM translates the
        input into CLI args (1.1 §4.5).
        """

        # ── /help ──
        if cmd == "help":
            help_lines = ["📋 **可用命令:**\n"]
            help_lines.append("- `/help` — 显示此帮助信息")
            # List built-in tools
            help_lines.append("\n**内置工具:**")
            for name, tool in sorted(tools.items()):
                if name != "skill_runner":
                    desc = getattr(tool, "description", "") or name
                    help_lines.append(f"- `{name}` — {desc}")
            # List installed skills
            skill_runner = tools.get("skill_runner")
            if skill_runner:
                skill_runner._refresh_cache()
                if skill_runner._skill_cache:
                    help_lines.append("\n**已安装 Skill (斜杠调用):**")
                    for s in skill_runner._skill_cache:
                        desc = s.get("description", "") or "无描述"
                        help_lines.append(f"- `/{s['name']}` — {desc}")
                else:
                    help_lines.append("\n*当前无已安装的第三方 Skill。*")
            answer = "\n".join(help_lines)
            # NOTE: user message already saved by routers/tasks.py task_chat()
            self.db.save_task_message(task_id, "assistant", answer)
            yield {"type": "answer_chunk", "data": {"content": answer}}
            yield {"type": "done", "data": {}}
            return

        # ── /skill-name [params] ──
        skill_runner = tools.get("skill_runner")
        if not skill_runner:
            yield {"type": "answer_chunk", "data": {"content": "当前模式不支持 Skill 调用"}}
            yield {"type": "done", "data": {}}
            return

        # Only unknown names arrive here — installed skills fall through to
        # the agent loop (see _slash_routing). Defensive: an installed name
        # that slips through is never direct-run; point the user at the agent
        # path instead.
        skill_runner._refresh_cache()
        available = [s["name"] for s in skill_runner._skill_cache]
        if skill_runner._find_skill(cmd):
            answer = f"Skill `{cmd}` 已安装，请以「/{cmd} 需求描述」方式调用，由 Agent 负责执行。"
        elif available:
            answer = f"未找到 Skill: `{cmd}`\n\n可用 Skill: {', '.join(f'`{s}`' for s in available)}"
        else:
            answer = "当前无已安装的第三方 Skill。请在设置页安装。"
        # NOTE: user message already saved by routers/tasks.py task_chat()
        self.db.save_task_message(task_id, "assistant", answer)
        yield {"type": "answer_chunk", "data": {"content": answer}}
        yield {"type": "done", "data": {}}

    # ── Main Run Loop ──

    def get_iteration(self, task_id: str) -> int:
        """Return the current iteration for a running task (0 if not found)."""
        return self._task_iterations.get(task_id, 0)

    def get_messages(self, task_id: str) -> list[dict] | None:
        """Return the current in-memory LLM messages for a running task."""
        return self._task_messages.get(task_id)

    def is_task_running(self, task_id: str) -> bool:
        """Return True if the given task is currently running."""
        return task_id in self._running_task_ids

    def try_acquire_task(self, task_id: str) -> bool:
        """Atomically reserve a run slot for a task (same-task mutual exclusion).

        Returns False if this task is already running — two concurrent chats
        on one task would run two agent loops over the same history and
        corrupt messages/state (H4) — or the global concurrency limit is
        reached. run()'s finally block releases the reservation.
        """
        if task_id in self._running_task_ids or not self.can_start_task():
            return False
        self._running_task_ids.add(task_id)
        return True

    async def run(self, task_id: str, user_message: str) -> AsyncGenerator[dict, None]:
        self._paused_tasks.discard(task_id)  # fresh run — a previous pause no longer applies
        self._reset_cancel(task_id)
        self._running_task_ids.add(task_id)
        self._task_iterations[task_id] = 0
        self._run_outcomes[task_id] = "completed"
        # 2.10 §4.3: 用量环境（source=task + 轮次 round_id）——覆盖本 run 的
        # 全部 LLM 调用（FC 迭代/planner/compact/fix）；round_id 是「每次对话」
        # 气泡行与轮次聚合的归属键。contextvar 随 async 原生隔离，并发任务互不可见
        round_id = uuid.uuid4().hex
        with push_context(UsageContext(
                source="task", task_id=task_id, round_id=round_id)):
            try:
                async for event in self._run_inner(task_id, user_message):
                    if event.get("type") == "done":
                        # 2.10 §4.5: SSE usage 事件（气泡行实时数据源）——本轮
                        # 全部调用按 round_id 聚合，先 usage 后 done；无记录
                        # （流式端点无 usage）不发送。data 即 usage 字段集
                        # （与其它事件同构：载荷本身，不再包一层）——前端
                        # parsed 直接作 UsageSummary 消费
                        round_usage = self.db.get_usage_by_round(round_id)
                        if round_usage:
                            yield {"type": "usage", "data": round_usage}
                    yield event
            except Exception:
                self._run_outcomes[task_id] = "failed"
                raise
            finally:
                # 用户取消/暂停（cancel_event 被 set，含 pause_task 路径）静默（0.4 §6.5）
                cancel_event = self._cancel_events.get(task_id)
                user_stopped = cancel_event is not None and cancel_event.is_set()
                self._running_task_ids.discard(task_id)
                self._cancel_events.pop(task_id, None)
                self._task_iterations.pop(task_id, None)
                self._task_messages.pop(task_id, None)
                outcome = self._run_outcomes.pop(task_id, "completed")
                # 2.10 §4.5: 轮次收尾——本轮 usage 写最后一条 assistant 消息
                # metadata.usage（气泡历史回放数据源）；无记录跳过，失败仅日志
                try:
                    round_usage = self.db.get_usage_by_round(round_id)
                    if round_usage:
                        self.db.attach_usage_to_last_assistant(task_id, round_usage)
                except Exception:
                    logger.warning("attach round usage failed: task=%s", task_id,
                                   exc_info=True)
                if not user_stopped:
                    # 经 activity_events 统一入口（E2 收尾，v3.9）：内部查 title + E3 事件投递，
                    # 异常自行兜底不影响业务主流程（任务不存在静默跳过，与旧直调一致）
                    # ppt 任务跳过（origin='ppt'）：回合结束 ≠ 任务终态（门禁回合自然
                    # 结束、迭代耗尽均为中间态），ppt 侧通知由 ppt_service 统一声明。
                    # 通知是收尾副链路：失败仅日志，不得掩盖上方主链路的原始异常/结果
                    try:
                        task_row = self.db.get_task(task_id)
                        if task_row and task_row.get("origin") == "ppt":
                            pass
                        elif task_row and task_row.get("schedule_id"):
                            # 影子任务（定时订阅执行，2.11 D2）：终态通知由调度器
                            # schedule_run 负责，此处跳过防双通知
                            pass
                        elif outcome == "completed":
                            activity_events.complete_task(self.db, task_id)
                        else:
                            activity_events.fail_task(self.db, task_id)
                    except Exception:
                        logger.warning("task terminal notify failed: task=%s",
                                       task_id, exc_info=True)

    async def _run_planner(
        self, planner_llm: LLMClient,
        user_message: str, editor_messages: list[dict],
    ) -> str | None:
        """Phase 1: Call planner model to generate an execution plan.
        
        Returns the plan text, or None if planning failed.
        """
        # Build planner messages: system prompt + relevant context
        planner_msgs = [
            {"role": "system", "content": _PLANNER_PROMPT},
        ]
        # Include recent conversation context (last 6 messages max)
        context_msgs = [m for m in editor_messages if m["role"] != "system"][-6:]
        planner_msgs.extend(context_msgs)
        # Add the current user request
        planner_msgs.append({"role": "user", "content": user_message})

        plan = (await planner_llm.chat_full(planner_msgs))["content"]
        if plan and plan.strip():
            return plan.strip()
        return None

    @staticmethod
    def _parse_plan_steps(plan_text: str) -> list[dict]:
        """Parse planner output into structured steps for frontend display.

        Expected format:
            Plan:
            1. [步骤描述] — 涉及文件: [文件路径]
            2. ...

        Returns list of {"id": int, "desc": str, "files": list[str]}.
        """
        steps = []
        step_pattern = re.compile(
            r'^\s*(\d+)\.\s*(.+?)(?:\s*[—\-]\s*涉及文件:\s*(.+))?$',
            re.MULTILINE,
        )
        for m in step_pattern.finditer(plan_text):
            step_id = int(m.group(1))
            desc = m.group(2).strip()
            files_str = m.group(3) or ""
            files = [f.strip() for f in files_str.split(",") if f.strip()]
            steps.append({"id": step_id, "desc": desc, "files": files})
        return steps

    async def _run_inner(self, task_id: str, user_message: str) -> AsyncGenerator[dict, None]:
        settings = self.db.get_settings()

        # Get task info（提前：origin 决定档位路由键，见 _apply_task_routing）
        task = self.db.get_task(task_id)
        if not task:
            yield {"type": "answer_chunk", "data": {"content": "任务不存在"}}
            yield {"type": "done", "data": {}}
            return
        task_origin = (task.get("origin") or "chat")

        # 档位制任务路由：按 origin 分流解析最终模型名写入 settings["model"]
        settings = self._apply_task_routing(settings, task_origin)

        # Apply task-specific timeout to tools
        # ppt-master 等工作流型 Skill 的长脚本链（SVG 转换/旁白合成）单命令可达数分钟，
        # 30s 会把合法长任务误杀（超时建议语引导换非阻塞验证是第二道防线）。
        # origin 预设在此统一应用（ORIGIN_PRESETS，2.3 §3.2）：专用键
        # （task_ppt_*）显式设置优先，未设置时用预设默认值（chat 任务预设表为空，行为不变）

        # Create per-task LLM client to avoid model cross-contamination in concurrent mode
        task_llm = LLMClient(settings)

        # ── Planner model setup (dual-model architecture) ──
        # task_planner_model 空串 = 不启用规划阶段（单模型模式，语义保留）；
        # 配置档位时解析为对应模型，与主模型同名则不重复建 client
        planner_level = str(settings.get("task_planner_model") or "")
        planner_llm = None
        if planner_level:
            planner_model_name = resolve_model(settings, planner_level)
            if planner_model_name and planner_model_name != settings.get("model"):
                planner_settings = dict(settings)
                planner_settings["model"] = planner_model_name
                planner_llm = LLMClient(planner_settings)

        if not settings.get("api_key"):
            async for event in self._demo_mode(task_id, user_message):
                yield event
            return

        workspace_path = task["workspace_path"]
        venv_path = task.get("venv_path", "")
        task_mode = task.get("mode", "coding") or "coding"
        # origin 参数预设统一应用（ORIGIN_PRESETS，2.3 §3.2，语义见 _apply_origin_presets；
        # task_origin 已在上方读取）
        self._apply_origin_presets(settings, task_origin)
        task_timeout = settings.get("task_timeout", 30)
        # Create per-task tool instances to avoid workspace/venv conflicts in concurrent mode
        tools = self._create_task_tools(workspace_path, venv_path, settings, mode=task_mode, task_id=task_id)
        # Apply task-specific timeout to shell and code_executor
        if "shell" in tools:
            tools["shell"].timeout = task_timeout
        if "code_executor" in tools:
            tools["code_executor"].timeout = task_timeout

        # ── V2: Skill Registry + Function Calling detection ──
        skill_registry = SkillRegistry()
        skill_registry.register_builtin_tools(tools)

        # ── Load external skills from DB and register them ──
        self._register_external_skills(skill_registry, tools)

        model_name = settings.get("model", "")
        logger.info("Using Function Calling mode for task %s (model=%s)", task_id, model_name)

        # ── V2: Context Manager (token-aware) ──
        # 预算经 budget 唯一入口（D1/D2）；reserve_for_response 必须跟随输出下限——
        # 输出下限升 32K 后旧硬编码 4096 预留不再匹配，压缩触发时机会失准
        ctx_mgr = ContextManager(
            max_tokens=context_window(settings),
            reserve_for_response=output_tokens(settings),
            snip_threshold=SNIP_THRESHOLD,
            keep_recent=KEEP_RECENT,
        )

        # ── Slash command routing: installed skills → agent loop ──
        slash_parsed, _slash_direct = self._slash_routing(user_message, tools)
        if slash_parsed and _slash_direct:
            async for event in self._execute_slash_command(task_id, slash_parsed[0], tools):
                yield event
            return
        # else: fall through to the normal agent loop below — an installed
        # Agent Skill (any params form) or a plain message

        # Build message history
        history = self.db.get_task_messages(task_id)
        # Prompt building runs git commands (3×5s timeout), scans workspace
        # files for project detection and repo-map — keep it off the event
        # loop so other tasks/SSE stay responsive (H1)
        system_prompt = await asyncio.to_thread(
            self._build_system_prompt, workspace_path, mode=task_mode,
        )
        if settings.get("system_prompt"):
            system_prompt = settings["system_prompt"] + "\n\n" + system_prompt

        # 60 轮覆盖工作流型 Skill（如 ppt-master Default 路线 ≈40-60 次操作）
        # 的单回合执行；仍不足时由「恢复」横幅续跑获新一轮预算（§恢复语义）
        max_iter = settings.get("task_max_iterations", 60)
        if not isinstance(max_iter, int) or max_iter < 1:
            max_iter = 60

        # ── Check for paused state (resume) ──
        paused_state = self.db.load_agent_state(task_id)
        start_iteration = 0
        if paused_state:
            # 过滤上一轮预算注入的收束提醒：新一轮预算下旧「仅剩 N 轮」
            # 提示已失效，保留会误导 LLM 提前收束（1.1 §5.3/§5.6）
            messages = [
                m for m in paused_state.get("messages", [])
                if not (m.get("role") == "user"
                        and str(m.get("content", "")).startswith(_BUDGET_WARNING_PREFIX))
            ]
            saved_iteration = paused_state.get("iteration", 0)
            recent_errors_saved = paused_state.get("recent_errors", [])
            self.db.delete_agent_state(task_id)
            # 恢复 = 新一轮运行、全新预算（对齐 Gemini/OpenClaw 续跑语义）：
            # 迭代上限是单次运行护栏而非任务总护栏。若沿用已耗尽的计数，
            # range(saved, max_iter) 为空 —— 恢复后立即再次耗尽「恢复空转」
            #（2026-10-14 天眼查 25 轮耗尽任务实证：state 保留但点恢复无效）
            logger.info(
                "Task %s resuming from iteration %d with fresh budget %d",
                task_id, saved_iteration, max_iter,
            )
            yield {"type": "thought", "data": {
                "content": (f"▶️ 从第 {saved_iteration} 轮恢复执行"
                            f"（新一轮 {max_iter} 轮预算）...")},
            }
        else:
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(self._rebuild_history_from_db(history))
            recent_errors_saved = None

        # ── Slash command fallthrough hint ──
        # An installed Agent Skill invoked via /skill-name is handled by the
        # normal agent loop: guide the LLM to call the skill tool instead of
        # answering directly, with the skill's SKILL.md instructions attached
        # so the first tool call already carries correct CLI args.
        if slash_parsed and not _slash_direct:
            _fc, _p = slash_parsed
            _guide = ""
            _refs_note = ""
            _adapter = skill_registry.get(_fc)
            if _adapter is not None:
                _guide = getattr(_adapter, "usage_instructions", "") or ""
                _note_fn = getattr(_adapter, "reference_note", None)
                if callable(_note_fn):
                    _refs_note = _note_fn() or ""
                # The hint below already carries the guide — suppress the
                # duplicate copy that would attach to the first tool result
                _mark = getattr(_adapter, "mark_guide_sent", None)
                if callable(_mark):
                    _mark()
            messages.append({
                "role": "user",
                "content": self._build_slash_hint(_fc, _p, _guide, _refs_note),
            })
            logger.info(
                "Injected slash fallthrough hint for skill '%s' (guide=%d chars)",
                _fc, len(_guide),
            )

        # ── Phase 1: Planner (if configured) ──
        # Skip planner when resuming from saved state (user_message is empty/irrelevant)
        if planner_llm and not paused_state:
            yield {"type": "thought", "data": {"content": "🏗️ 架构师正在制定执行计划..."}}
            try:
                plan = await self._run_planner(planner_llm, user_message, messages)
                if plan:
                    # Parse into structured steps for frontend (Phase 18)
                    plan_steps = self._parse_plan_steps(plan)
                    # Inject plan as a system-level context for the editor
                    plan_msg = f"[架构师执行计划]\n{plan}\n[请按照以上计划执行]"
                    messages.append({"role": "user", "content": plan_msg})
                    # Send structured plan to frontend
                    yield {"type": "plan", "data": {
                        "text": plan,
                        "steps": plan_steps,
                        "total": len(plan_steps),
                    }}
                    logger.info("Planner plan for task %s: %d steps", task_id, len(plan_steps))
            except Exception as e:
                logger.warning("Planner failed for task %s: %s, falling back to single-model", task_id, e)
                yield {"type": "thought", "data": {"content": "⚠️ 规划阶段出错，回退到单模型模式"}}

        logger.info(
            "TaskAgent run start: task=%s model=%s workspace=%s mode=%s msg_count=%d",
            task_id, model_name, workspace_path, task_mode, len(messages),
        )

        # Stuck detection state
        recent_errors: list[str] = recent_errors_saved if recent_errors_saved is not None else []
        STUCK_THRESHOLD = 3  # consecutive same errors → stuck

        # Per-task cancel event for this execution
        cancel_event = self._cancel_events.get(task_id) or asyncio.Event()

        # Track in-memory messages so pause endpoint can save complete state
        self._task_messages[task_id] = messages

        for iteration in range(start_iteration, max_iter):
            self._task_iterations[task_id] = iteration
            if cancel_event.is_set():
                break
            # 显式迭代边界（1.2 §2）：前端据此推进迭代编号，与 content 是否为空解耦；
            # 后续该轮的 reasoning_chunk / answer_chunk / action / observation 均归属此轮号
            yield {"type": "iteration", "data": {"iteration": iteration}}

            # ── 预算软着陆：剩余阈值到时一次性注入收束提醒（1.1 §5.6）──
            # 阈值到达天然只触发一次；max_iter < 阈值时预算过小不注入。
            # 在 compress 之前追加，提醒计入压缩预算且下一轮 LLM 立即可见。
            # chat 维持剩 3 轮；ppt 长工作流放大到 max(3, max_iter//10)
            #（240 轮任务剩 3 轮来不及保存，2.3 §3.2）。
            budget_warning_left = (
                max(BUDGET_WARNING_LEFT, max_iter // 10)
                if task_origin == "ppt" else BUDGET_WARNING_LEFT
            )
            if max_iter - iteration == budget_warning_left:
                messages.append({
                    "role": "user",
                    "content": (
                        f"{_BUDGET_WARNING_PREFIX}仅剩 {budget_warning_left} 轮。请："
                        "1) 立即将所有中间产物保存到 workspace；"
                        "2) 收束当前工作到安全断点；"
                        "3) 简述已完成进度与剩余工作，便于恢复后续跑。"
                    ),
                })
                yield {"type": "thought", "data": {
                    "content": (f"⚠️ 接近迭代预算上限（剩 {budget_warning_left} 轮），"
                                "已提醒 Agent 收束保存")}}
                logger.info(
                    "Budget soft-landing warning injected: task=%s remaining=%d",
                    task_id, budget_warning_left,
                )

            logger.info(
                "TaskAgent iteration %d start: msg_count=%d recent_errors=%d task=%s",
                iteration, len(messages), len(recent_errors), task_id,
            )

            # Context Collapse: compress if too many messages (token-aware)
            messages = await ctx_mgr.compress(messages, task_llm)

            # ── Function Calling path ──
            fc_completed = False
            async for fc_event in self._handle_fc_iteration(
                task_id, task_llm, settings, messages,
                tools, skill_registry, cancel_event,
                recent_errors, iteration,
                stuck_threshold=STUCK_THRESHOLD,
                venv_path=venv_path,
                ctx_mgr=ctx_mgr,
                workspace_path=workspace_path,
                task_origin=task_origin,
            ):
                if fc_event.get("type") == "_fc_done":
                    fc_completed = True
                    fc_status = fc_event["data"].get("status", "continue")
                    break
                yield fc_event
            if fc_completed:
                logger.info(
                    "TaskAgent iteration %d done: status=%s task=%s",
                    iteration, fc_status, task_id,
                )
                if fc_status == "continue":
                    # ── Auto-save full state for crash recovery ──
                    # Save complete messages (including tool_calls / tool_call_id)
                    # so the LLM can correctly pair tool calls with results on resume.
                    try:
                        self.db.save_agent_state(task_id, {
                            "messages": messages,
                            "iteration": iteration + 1,
                            "recent_errors": list(recent_errors),
                            "auto_saved": True,
                        })
                    except Exception:
                        logger.warning("Auto-save state failed for task %s", task_id, exc_info=True)
                elif fc_status in ("done", "error"):
                    # Task finished or errored — clear auto-saved state.
                    # Exception: an error exit caused by a user pause — keep
                    # the state so "resume" can continue from where it stopped.
                    # 注意：迭代预算耗尽的 fallback 路径不经此处（循环自然退出），
                    # state 有意保留 —— 前端「恢复」横幅据此显示，续跑从断点继续
                    if fc_status == "done" or task_id not in self._paused_tasks:
                        self.db.delete_agent_state(task_id)
                    self._run_outcomes[task_id] = "completed" if fc_status == "done" else "failed"
                    yield {"type": "done", "data": {}}
                    return

        if cancel_event.is_set():
            fallback = "已停止生成。"
        else:
            # 迭代预算耗尽：此刻库中仍留着最后一轮 auto-save 的 agent_state
            #（上方循环 return 前只对 done/error 清理），前端 tasks:hasState
            # 会显示「恢复」横幅 —— 文案必须指引续跑而非误导改需求描述
            #（2026-10-14 天眼查 25 轮耗尽任务实证：旧文案让用户以为是
            # 需求问题，实际是预算耗尽且进展可续）
            fallback = (
                f"已连续执行 {max_iter} 轮迭代仍未完成任务，已有进展已保存。"
                "可点击「恢复」从断点继续（将获得新一轮执行预算），"
                "或在 设置→任务设置 中调大迭代上限后重试。"
            )
        self.db.save_task_message(task_id, "assistant", fallback)
        yield {"type": "answer_chunk", "data": {"content": fallback}}
        yield {"type": "done", "data": {}}

    # ── Cross-run history rebuild（1.1 §5.3 同任务重入）──

    @staticmethod
    def _rebuild_history_from_db(history: list[dict]) -> list[dict]:
        """从 DB 历史重建 LLM 上下文（非 resume 的重入路径）。

        DB 历史原本只回放 user/assistant 文本——工具结果全部蒸发，恢复后
        的 LLM 需多轮盲读文件重建上下文（2026-10-16 实测 3-4 轮）。现将
        observation 压为单行摘要路标（summarize_tool_result_line，与回合内
        Micro Compact 共用格式）：metadata 里的 tool_name/tool_output 已在
        落库时保存（task_messages.metadata），无需表迁移。「文件即状态」
        仍是最终权威，摘要只提供活动线索。上一轮预算的收束提醒一并过滤。
        """
        rebuilt: list[dict] = []
        pending_obs: list[str] = []

        def _flush_obs():
            if pending_obs:
                rebuilt.append({
                    "role": "user",
                    "content": ("[上次运行工具活动摘要]\n"
                                + "\n".join(pending_obs)
                                + "\n[摘要结束]"),
                })
                pending_obs.clear()

        for msg in history:
            role = msg.get("role")
            if role == "observation":
                meta = msg.get("metadata") or {}
                pending_obs.append(summarize_tool_result_line(
                    meta.get("tool_name", "tool"),
                    meta.get("tool_output") or msg.get("content", ""),
                ))
                continue
            _flush_obs()
            if role == "user":
                content = str(msg.get("content", ""))
                if content.startswith(_BUDGET_WARNING_PREFIX):
                    continue  # 旧预算收束提醒：与新回合无关，不回放
                rebuilt.append({"role": "user", "content": content})
            elif role == "assistant":
                rebuilt.append({"role": "assistant", "content": str(msg.get("content", ""))})
        _flush_obs()
        return rebuilt

    # ── FC Iteration Logging Helpers ──

    @staticmethod
    def _preview(text: str, limit: int = 200) -> str:
        """Truncate text for logging, preserving newlines as \\n."""
        if not text:
            return "<empty>"
        one_line = text.replace("\n", "\\n")
        return one_line[:limit] + ("..." if len(one_line) > limit else "")

    @staticmethod
    def _summarize_tool_call(tc: dict) -> str:
        """One-line summary of a tool call: name + argument keys + value previews."""
        name = tc.get("function", {}).get("name", "?")
        try:
            args = json.loads(tc.get("function", {}).get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            return f"{name}(<parse_error>)"
        parts = []
        for k, v in args.items():
            if isinstance(v, str):
                parts.append(f"{k}={TaskAgent._preview(v, 80)}")
            else:
                parts.append(f"{k}={v}")
        return f"{name}({', '.join(parts)})"

    # ── Function Calling Iteration Handler ──

    async def _handle_fc_iteration(
        self,
        task_id: str,
        task_llm: LLMClient,
        settings: dict,
        messages: list[dict],
        tools: dict,
        skill_registry: SkillRegistry,
        cancel_event: asyncio.Event,
        recent_errors: list[str],
        iteration: int,
        stuck_threshold: int = 3,
        venv_path: str = "",
        ctx_mgr: "ContextManager | None" = None,
        workspace_path: str = "",
        task_origin: str = "chat",
    ) -> AsyncGenerator[dict, None]:
        """Handle one iteration of the function calling loop.

        Yields frontend events (action, observation, thought, answer_chunk).
        Terminates by yielding ``{"type": "_fc_done", "data": {"status": ...}}``
        where status is "done", "error", or "continue".
        """
        tool_defs = skill_registry.to_function_definitions()

        # ── LLM call with tools (function calling) — streaming ──
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_flushed = False  # Track if reasoning has been persisted to DB
        _reasoning_chunks_sent = False  # Track if reasoning_chunk events were yielded (retry needs reset)
        _content_chunks_sent = False  # Track if answer_chunk events were yielded (retry/error needs reset)
        tc_accum: dict[int, dict] = {}  # index -> {id, function: {name, arguments}}
        finish_reason = "unknown"

        # ── Retry logic for transient LLM errors (timeout, connection) ──
        _LLM_MAX_RETRIES = 2
        _llm_last_error: Exception | None = None

        for _llm_attempt in range(_LLM_MAX_RETRIES + 1):
            if _llm_attempt > 0:
                if cancel_event.is_set():
                    yield {"type": "_fc_done", "data": {"status": "error"}}
                    return
                _backoff = 2 ** _llm_attempt  # 2s, 4s
                logger.info(
                    "LLM retry %d/%d for task %s (backoff %ds)",
                    _llm_attempt, _LLM_MAX_RETRIES, task_id, _backoff,
                )
                await asyncio.sleep(_backoff)
                # Reset accumulators for retry (keep reasoning_flushed to avoid duplicate DB writes)
                content_parts.clear()
                reasoning_parts.clear()
                tc_accum.clear()
                finish_reason = "unknown"
                # Frontend already rendered partial reasoning chunks — reset it
                # so the retried stream does not append onto stale text
                if _reasoning_chunks_sent:
                    yield {"type": "reasoning_reset", "data": {}}
                    _reasoning_chunks_sent = False
                if _content_chunks_sent:
                    # Frontend already rendered partial content chunks — reset it
                    # so the retried stream does not append onto stale text
                    yield {"type": "answer_reset", "data": {}}
                    _content_chunks_sent = False

            try:
                async for chunk in task_llm.chat_stream(messages, tools=tool_defs):
                    if cancel_event.is_set():
                        yield {"type": "_fc_done", "data": {"status": "error"}}
                        return
                    if chunk["type"] == "reasoning":
                        delta = chunk["delta"]
                        reasoning_parts.append(delta)
                        # Stream reasoning to frontend as it arrives (1.2 §2);
                        # DB persistence happens once at the flush points below
                        if delta:
                            yield {"type": "reasoning_chunk", "data": {"delta": delta}}
                            _reasoning_chunks_sent = True
                    elif chunk["type"] == "content":
                        # Persist reasoning before first content chunk (ensure DB order:
                        # reasoning precedes content); frontend already received it
                        # via reasoning_chunk events
                        if not reasoning_flushed and reasoning_parts:
                            rc = "".join(reasoning_parts)
                            if rc.strip():
                                self.db.save_task_message(task_id, "reasoning", rc)
                            reasoning_flushed = True
                        
                        delta = chunk["delta"]
                        content_parts.append(delta)
                        # Stream text content to frontend
                        yield {"type": "answer_chunk", "data": {"content": delta}}
                        _content_chunks_sent = True
                    elif chunk["type"] == "tool_calls":
                        # Streaming complete — tool_calls accumulated
                        for i, tc in enumerate(chunk["tool_calls"]):
                            tc_accum[i] = tc
                    elif chunk["type"] == "finish":
                        finish_reason = chunk.get("finish_reason", "unknown")
                    elif chunk["type"] == "usage":
                        # D5: 流式 usage 对账（端点支持 stream_options 时才有）——
                        # 真实 token 消耗落日志，供 CHARS_PER_TOKEN 校准与配额追踪
                        usage = chunk.get("usage", {})
                        logger.info(
                            "TaskAgent FC token usage [%d] prompt=%s completion=%s total=%s for task %s",
                            iteration,
                            usage.get("prompt_tokens"), usage.get("completion_tokens"),
                            usage.get("total_tokens"), task_id,
                        )

                # Detailed summary of LLM response (use raw accumulators here)
                _cp = "".join(content_parts) if content_parts else ""
                _tc_list = [tc_accum[i] for i in sorted(tc_accum.keys())] if tc_accum else []
                _content_preview = self._preview(_cp, 300) if _cp else "<none>"
                _tc_summaries = [self._summarize_tool_call(tc) for tc in _tc_list]
                logger.info(
                    "TaskAgent FC response [%d] finish=%s content=%s tool_calls=[%s] for task %s",
                    iteration, finish_reason, _content_preview,
                    "; ".join(_tc_summaries) if _tc_summaries else "none",
                    task_id,
                )
                _llm_last_error = None
                break  # Success — exit retry loop

            except LLMNetworkError as e:
                # Transient error (timeout / connection) — eligible for retry
                _llm_last_error = e
                detail = getattr(e, 'detail', '') or str(e)
                logger.warning(
                    "LLM transient error for task %s (attempt %d/%d): %s",
                    task_id, _llm_attempt + 1, _LLM_MAX_RETRIES + 1, detail,
                )
                if _llm_attempt >= _LLM_MAX_RETRIES:
                    break  # Exhausted retries

            except Exception as e:
                # Non-transient error (auth, API error) — do not retry
                _llm_last_error = e
                break

        # ── Check if LLM call ultimately failed ──
        if _llm_last_error is not None:
            e = _llm_last_error
            detail = getattr(e, 'detail', '') or str(e)
            error_msg = _llm_error_msg(e)
            logger.error("TaskAgent FC LLM call failed for task %s: %s", task_id, detail)
            # Drop any partially streamed content so the error renders as a
            # clean message instead of appending onto stale partial text
            if _content_chunks_sent:
                yield {"type": "answer_reset", "data": {}}
                _content_chunks_sent = False
            self.db.save_task_message(task_id, "assistant", error_msg)
            yield {"type": "answer_chunk", "data": {"content": error_msg}}
            yield {"type": "_fc_done", "data": {"status": "error"}}
            return

        content = "".join(content_parts) if content_parts else None
        reasoning_content = "".join(reasoning_parts) if reasoning_parts else None
        tool_calls = [tc_accum[i] for i in sorted(tc_accum.keys())] if tc_accum else None

        # ── Case 1: Only text (no tool calls) ──
        if not tool_calls:
            text = content or ""
            if not text.strip():
                # Empty response — treat as thought, continue
                logger.warning("TaskAgent FC empty response for task %s", task_id)
                yield {"type": "_fc_done", "data": {"status": "continue"}}
                return

            # In FC mode, finish_reason="stop" means the model considers itself done.
            # Treat the entire text as the final answer.
            if finish_reason == "stop":
                self.db.save_task_message(task_id, "assistant", text)
                # Content already streamed via answer_chunk events
                yield {"type": "_fc_done", "data": {"status": "done"}}
                return

            # Plain text — save as assistant message, continue
            messages.append({"role": "assistant", "content": text})
            self.db.save_task_message(task_id, "assistant", text)
            yield {"type": "_fc_done", "data": {"status": "continue"}}
            return

        # ── Case 2: Tool calls present ──
        # Persist reasoning if not already flushed during streaming (content-less
        # iterations flush here); frontend received it via reasoning_chunk events
        if not reasoning_flushed and reasoning_content and reasoning_content.strip():
            self.db.save_task_message(task_id, "reasoning", reasoning_content)

        # Extract and yield thought before action (so frontend can clear streamContent)
        if content:
            thought_text = content.strip()
            if thought_text.startswith("Thought:"):
                thought_text = thought_text[len("Thought:"):].strip()
            if thought_text:
                yield {"type": "thought", "data": {"content": thought_text}}
                self.db.save_task_message(task_id, "thought", thought_text)

        # Build assistant message with tool_calls for the conversation history
        assistant_msg: dict = {"role": "assistant"}
        if content:
            assistant_msg["content"] = content
        assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        # ── Execute each tool call ──
        _edits_made = False  # Track if any file edits were made (for self-reflection)
        # PPT 任务命令静默（2.3 §3.3）：工作流命令谱系 = 内置 skill 固定脚本
        # 清单 + 工作区文件操作，来源可信可预期；逐条确认在百轮级任务下不可用。
        # 固定 auto 语义，覆盖用户三档设置与 task_auto_confirm_shell
        confirm_mode = settings.get("task_shell_confirm_mode", "smart")
        if settings.get("task_auto_confirm_shell", False):
            confirm_mode = "auto"
        if task_origin == "ppt":
            confirm_mode = "auto"

        for tc in tool_calls:
            if cancel_event.is_set():
                yield {"type": "_fc_done", "data": {"status": "error"}}
                return

            tc_id = tc["id"]
            tool_name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments", "")
            try:
                tool_params = json.loads(raw_args)
            except (json.JSONDecodeError, KeyError):
                logger.warning(
                    "TaskAgent FC tool call JSON parse error: tool=%s raw_args=%s task=%s",
                    tool_name, self._preview(raw_args, 500), task_id,
                )
                tool_params = {}

            # Yield action event for frontend
            yield {"type": "action", "data": {"tool": tool_name, "input": tool_params}}
            action_msg_id = self.db.save_task_message(
                task_id, "action", "",
                metadata={"tool_name": tool_name, "tool_input": tool_params},
            )

            exec_start = time.time()
            result_str = None

            # ── Shell confirmation check ──
            if tool_name == "shell":
                needs_confirm = False
                if confirm_mode == "auto":
                    pass
                elif confirm_mode == "smart":
                    command = tool_params.get("command", "")
                    domain_whitelist = settings.get("task_shell_domain_whitelist", "")
                    if task_id not in self._session_auto_allow and not check_safe_command(command) and not check_domain_whitelist(command, domain_whitelist):
                        needs_confirm = True
                else:  # strict
                    needs_confirm = True

                if needs_confirm:
                    command = tool_params.get("command", "")
                    confirm_event = asyncio.Event()
                    self._pending_confirms[task_id] = {
                        "command": command,
                        "confirmed": False,
                        "event": confirm_event,
                    }
                    # Confirm dialogs are transient stream events only (same
                    # as skill confirm): persisting them writes ghost "action"
                    # rows that replay as empty tool cards in task history.
                    yield {"type": "confirm_shell", "data": {
                        "command": command,
                        "task_id": task_id,
                    }}
                    timed_out = False
                    try:
                        await asyncio.wait_for(confirm_event.wait(), timeout=SHELL_CONFIRM_TIMEOUT)
                    except asyncio.TimeoutExpired:
                        timed_out = True
                    pending = self._pending_confirms.pop(task_id, {})
                    if not pending.get("confirmed"):
                        if timed_out:
                            # Nobody answered in time: dismiss the stale dialog;
                            # the skip itself is traced via the action's observation
                            yield {"type": "confirm_expired", "data": {"kind": "shell", "task_id": task_id}}
                        result_str = json.dumps({
                            "exit_code": -1, "stdout": "",
                            "stderr": "确认超时，已跳过此 shell 命令的执行。" if timed_out else "用户拒绝了此 shell 命令的执行。",
                            "error_type": "UserDenied",
                        }, ensure_ascii=False)

            # ── code_executor dangerous-call confirmation (S12) ──
            # Workspace boundary: in-workspace deletions run free, outside/
            # undecidable targets and non-whitelisted commands ask the user
            # (same confirm_shell dialog, same 3-mode semantics as shell).
            if tool_name == "code_executor" and result_str is None:
                needs_confirm = False
                confirm_topic = ""
                if confirm_mode == "auto":
                    pass
                elif confirm_mode == "smart":
                    guard = check_code_danger(tool_params.get("code", ""), workspace_path)
                    if guard and guard["action"] == "confirm":
                        needs_confirm = True
                        confirm_topic = guard["reason"]
                else:  # strict
                    needs_confirm = True
                    confirm_topic = f"执行 Python 代码（strict 模式）: {tool_params.get('code', '')[:120]}"

                if needs_confirm:
                    confirm_event = asyncio.Event()
                    self._pending_confirms[task_id] = {
                        "command": confirm_topic,
                        "confirmed": False,
                        "event": confirm_event,
                    }
                    # Reuse the shell confirm dialog (stream-only, S6).
                    yield {"type": "confirm_shell", "data": {
                        "command": confirm_topic,
                        "task_id": task_id,
                    }}
                    timed_out = False
                    try:
                        await asyncio.wait_for(confirm_event.wait(), timeout=SHELL_CONFIRM_TIMEOUT)
                    except asyncio.TimeoutExpired:
                        timed_out = True
                    pending = self._pending_confirms.pop(task_id, {})
                    if not pending.get("confirmed"):
                        if timed_out:
                            yield {"type": "confirm_expired", "data": {"kind": "shell", "task_id": task_id}}
                        result_str = json.dumps({
                            "exit_code": -1, "stdout": "",
                            "stderr": "确认超时，已跳过此代码执行。" if timed_out else "用户拒绝了此代码执行。",
                            "error_type": "UserDenied",
                        }, ensure_ascii=False)

            # ── Skill confirmation check ──
            # origin=ppt 直行（与 shell 静默同语义，2.3 §7.3 PPT 任务零确认打断；
            # 内置 skill 已由 seed 置信，此处是第三方 skill 未置信时的兑底）
            if tool_name == "skill_runner" and result_str is None:
                _skill_name = tool_params.get("skill", "")
                _skill_runner_inst = tools.get("skill_runner")
                if _skill_runner_inst:
                    _skill_runner_inst._refresh_cache()
                    _sinfo = _skill_runner_inst._find_skill(_skill_name)
                    if _sinfo and _sinfo.get("needs_confirm") and task_origin != "ppt":
                        _confirm_event = asyncio.Event()
                        self._pending_skill_confirms[task_id] = {
                            "skill_name": _skill_name,
                            "confirmed": False,
                            "event": _confirm_event,
                        }
                        # Yield confirm event to frontend so the dialog appears
                        yield {"type": "confirm_skill", "data": {
                            "skill_name": _skill_name,
                            "task_id": task_id,
                        }}
                        _timed_out = False
                        try:
                            await asyncio.wait_for(_confirm_event.wait(), timeout=SKILL_CONFIRM_TIMEOUT)
                        except asyncio.TimeoutExpired:
                            _timed_out = True
                        _pending = self._pending_skill_confirms.pop(task_id, {})
                        if not _pending.get("confirmed"):
                            if _timed_out:
                                yield {"type": "confirm_expired", "data": {"kind": "skill", "task_id": task_id}}
                            result_str = json.dumps({
                                "error": (f"确认超时，已跳过 Skill '{_skill_name}' 的执行" if _timed_out
                                          else f"用户未确认 Skill '{_skill_name}' 的执行权限"),
                                "error_type": "UserDenied",
                            }, ensure_ascii=False)

            # ── git_ops commit confirmation ──
            # （origin=ppt 直行：临时工作区无 commit 价值，零打断优先）
            if tool_name == "git_ops" and result_str is None:
                if tool_params.get("action") == "commit" and task_origin != "ppt":
                    _commit_msg = tool_params.get("args", "")
                    _confirm_event = asyncio.Event()
                    self._pending_confirms[task_id] = {
                        "command": f"git commit {_commit_msg}",
                        "confirmed": False,
                        "event": _confirm_event,
                    }
                    # Same as shell confirm: stream-only, no ghost action row.
                    yield {"type": "confirm_shell", "data": {
                        "command": f"git commit {_commit_msg}",
                        "task_id": task_id,
                    }}
                    _commit_timed_out = False
                    try:
                        await asyncio.wait_for(_confirm_event.wait(), timeout=SHELL_CONFIRM_TIMEOUT)
                    except asyncio.TimeoutExpired:
                        _commit_timed_out = True
                    _pending = self._pending_confirms.pop(task_id, {})
                    if not _pending.get("confirmed"):
                        if _commit_timed_out:
                            yield {"type": "confirm_expired", "data": {"kind": "shell", "task_id": task_id}}
                        result_str = json.dumps({
                            "exit_code": -1, "stdout": "",
                            "stderr": "确认超时，已跳过此 git commit 操作。" if _commit_timed_out else "用户拒绝了此 git commit 操作。",
                            "error_type": "UserDenied",
                        }, ensure_ascii=False)

            # ── Tool execution ──
            if result_str is None:
                tool = tools.get(tool_name)
                if tool:
                    # Lazy venv: materialize on first venv-configured tool use
                    # (失败时 venv_path 置 ""，同轮后续外部 Skill 上下文一并降级)。
                    # 首用环境准备 UI 可见（2.8 §5.5，2026-10-13 从 skill 链
                    # 补齐到 tool 链）：预检 marker 缺失才推 step_note（快路径
                    # 零噪音），终态按 venv 物化结果 + marker 落定。
                    if tool_name in _VENV_TOOL_ATTRS and getattr(
                            tool, _VENV_TOOL_ATTRS[tool_name], ""):
                        _tool_env_setup = _venv_env_needs_setup(venv_path)
                        if _tool_env_setup:
                            yield {"type": "step_note", "data": {
                                "text": ("⏳ 首次执行需要 Python 环境，正在准备"
                                         "运行环境（创建虚拟环境并安装依赖，约 10~60 秒）..."),
                            }}
                        _env_t0 = time.time()
                        try:
                            venv_path = await self._ensure_task_venv(task_id, venv_path)
                            if _tool_env_setup:
                                if venv_path and is_preinstall_done(venv_path):
                                    yield {"type": "step_note", "data": {
                                        "text": f"✅ Python 运行环境就绪（{time.time() - _env_t0:.0f}s）",
                                    }}
                                elif venv_path:
                                    yield {"type": "step_note", "data": {
                                        "text": "⚠️ Python 环境部分就绪，执行所需依赖将按需安装",
                                    }}
                                else:
                                    yield {"type": "step_note", "data": {
                                        "text": "❌ Python 环境创建失败，本次执行将回退系统 Python",
                                    }}
                        except UvExclusionRequired as e:
                            # 前置阻断（2.8 §12 F4）：白名单未验证，不降级
                            # 裸跑，给 LLM 明确的授权指引
                            if _tool_env_setup:
                                yield {"type": "step_note", "data": {
                                    "text": "❌ 运行环境准备被阻断：请先在应用顶部横幅完成系统授权",
                                }}
                            result_str = json.dumps(
                                {"error": str(e), "error_type": "UvExclusionRequired"},
                                ensure_ascii=False)
                    if result_str is None:
                        # Log key param details for diagnostics
                        _param_summary = {k: (f"len={len(v)}" if isinstance(v, str) and len(v) > 100 else v)
                                          for k, v in tool_params.items()}
                        logger.info(
                            "TaskAgent FC exec tool=%s params=%s task=%s",
                            tool_name, json.dumps(_param_summary, ensure_ascii=False), task_id,
                        )
                        # Builtin tool execution
                        try:
                            override_params = await tool.pre_execute(**tool_params)
                            if override_params is not None:
                                tool_params = override_params
                            result_str = await tool.execute(**tool_params)
                            result_str = await tool.post_execute(result_str, **tool_params)
                            # ── Edit-verify: enrich result with lint hint ──
                            if tool_name in ("file_ops",):
                                result_str = self._enrich_result_with_lint_hint(result_str, tool_params)
                                # Phase 11: real auto-fix loop for lint errors
                                action_type = tool_params.get("action", "")
                                if action_type in ("write", "search_replace", "apply_diff"):
                                    result_str = await self._edit_verify_loop(
                                        tool, tool_params, result_str,
                                        task_llm, settings,
                                    )
                        except Exception as e:
                            logger.warning(
                                "TaskAgent FC tool exception: tool=%s error=%s task=%s",
                                tool_name, e, task_id,
                            )
                            result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                else:
                    # Check if it's an external skill registered in skill_registry
                    skill = skill_registry.get(tool_name)
                    if skill:
                        # ── First-use confirmation for external skills (1.1 §7.2) ──
                        # Checked via skill_runner's live DB cache (not the
                        # adapter's startup snapshot) so settings-page
                        # confirmation takes effect within the running task.
                        if result_str is None:
                            _sr_chk = tools.get("skill_runner")
                            _needs_confirm = False
                            if _sr_chk:
                                _sr_chk._refresh_cache()
                                _si_chk = _sr_chk._find_skill(tool_name)
                                _needs_confirm = bool(_si_chk and _si_chk.get("needs_confirm"))
                            if _needs_confirm and task_origin != "ppt":
                                # origin=ppt 直行（与 shell 静默同语义，2.3 §7.3）
                                _skill_event = asyncio.Event()
                                self._pending_skill_confirms[task_id] = {
                                    "skill_name": tool_name,
                                    "confirmed": False,
                                    "event": _skill_event,
                                }
                                # Yield confirm event so the dialog appears
                                yield {"type": "confirm_skill", "data": {
                                    "skill_name": tool_name,
                                    "task_id": task_id,
                                }}
                                _skill_timed_out = False
                                try:
                                    await asyncio.wait_for(_skill_event.wait(), timeout=SKILL_CONFIRM_TIMEOUT)
                                except asyncio.TimeoutExpired:
                                    _skill_timed_out = True
                                _pending = self._pending_skill_confirms.pop(task_id, {})
                                if not _pending.get("confirmed"):
                                    if _skill_timed_out:
                                        yield {"type": "confirm_expired", "data": {"kind": "skill", "task_id": task_id}}
                                    result_str = json.dumps({
                                        "error": (f"确认超时，已跳过 Skill '{tool_name}' 的执行" if _skill_timed_out
                                                  else f"用户未确认 Skill '{tool_name}' 的执行权限"),
                                        "error_type": "UserDenied",
                                    }, ensure_ascii=False)
                        # External skill execution via SkillRegistry
                        if result_str is None:
                            # ── 首用依赖安装可见性（2.8 §5.5）──
                            # 预检判据与终态判定见模块级纯函数；marker 在位则
                            # 零开销跳过。粗粒度两条（开始/终态）：预装一次性
                            # （venv 常驻任务目录），不做逐行下载进度。
                            _need_setup = _venv_env_needs_setup(venv_path)
                            if _need_setup:
                                yield {"type": "step_note", "data": {
                                    "text": (f"⏳ 首次使用 {tool_name}，正在准备运行环境"
                                             f"（创建虚拟环境并安装依赖，约 10~60 秒）..."),
                                }}
                            _setup_t0 = time.time()
                            try:
                                venv_path = await self._ensure_task_venv(task_id, venv_path)
                                _venv_py = ""
                                if venv_path:
                                    _venv_py = get_venv_python(venv_path)
                                context = SkillContext(
                                    workspace_path=workspace_path,
                                    venv_python=_venv_py,
                                    task_id=task_id,
                                    settings=settings,
                                    cancel_event=cancel_event,
                                )
                                result_str = await skill_registry.execute(tool_name, tool_params, context)
                                if _need_setup:
                                    _setup_secs = time.time() - _setup_t0
                                    if _skill_env_setup_failed(_venv_py, result_str):
                                        yield {"type": "step_note", "data": {
                                            "text": f"❌ {tool_name} 运行环境准备失败"
                                                    f"（耗时 {_setup_secs:.0f}s），详见执行结果",
                                        }}
                                    else:
                                        yield {"type": "step_note", "data": {
                                            "text": f"✅ {tool_name} 运行环境就绪（{_setup_secs:.0f}s）",
                                        }}
                            except Exception as e:
                                if _need_setup:
                                    yield {"type": "step_note", "data": {
                                        "text": f"❌ {tool_name} 运行环境准备失败：{e}",
                                    }}
                                result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                    else:
                        result_str = f"未知工具: {tool_name}"

            exec_duration = int((time.time() - exec_start) * 1000)

            # Ensure result is a string (some tools may return None)
            if result_str is None:
                result_str = ""

            # Log execution result summary
            _res_status = "ok"
            try:
                _rj = json.loads(result_str)
                if isinstance(_rj, dict):
                    if "error" in _rj:
                        _res_status = f"error: {_rj['error'][:120]}"
                    elif _rj.get("ok"):
                        _res_status = "ok"
                    elif "exit_code" in _rj:
                        _res_status = f"exit={_rj['exit_code']}"
            except (json.JSONDecodeError, TypeError):
                _res_status = self._preview(result_str, 120)
            logger.info(
                "TaskAgent FC result tool=%s status=%s duration=%dms task=%s",
                tool_name, _res_status, exec_duration, task_id,
            )

            # Append tool result to message history
            snipped_result = ctx_mgr.snip(result_str, tool_name=tool_name, workspace_path=workspace_path) if ctx_mgr else result_str
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": snipped_result,
            })

            # Persist observation
            obs_data = {"content": result_str}
            # Extract diff for frontend inline rendering (Phase 18)
            if tool_name == "file_ops":
                try:
                    _res_obj = json.loads(result_str)
                    if isinstance(_res_obj, dict) and _res_obj.get("diff"):
                        obs_data["diff"] = _res_obj["diff"]
                        obs_data["file_path"] = tool_params.get("path", "")
                except (json.JSONDecodeError, TypeError):
                    pass
            yield {"type": "observation", "data": obs_data}
            # Save observation with action_id link + diff so frontend can reconstruct ToolCard from history
            _obs_meta: dict = {
                "tool_name": tool_name, "tool_input": tool_params,
                "tool_output": result_str[:5000], "action_id": action_msg_id,
            }
            if obs_data.get("diff"):
                _obs_meta["diff"] = obs_data["diff"]
                _obs_meta["file_path"] = obs_data.get("file_path", "")
            self.db.save_task_message(
                task_id, "observation", result_str,
                metadata=_obs_meta,
            )
            self._log_execution(task_id, tool_name, tool_params, result_str, exec_duration)

            # ── Stuck detection: collect error signals only ──
            # Message insertion is deferred to after the loop (V4 Phase 10)
            result_obj = None
            try:
                result_obj = json.loads(result_str)
            except (json.JSONDecodeError, TypeError):
                pass
            is_error = (
                isinstance(result_obj, dict)
                and result_obj.get("exit_code", 0) != 0
            )
            if is_error:
                err_type = result_obj.get("error_type", "")
                err_sig = f"{err_type}:{result_obj.get('exit_code', '')}"
                recent_errors.append(err_sig)
                if len(recent_errors) > stuck_threshold:
                    recent_errors.pop(0)
            else:
                recent_errors.clear()

            # Track if edits were made (for self-reflection injection)
            # Only count successful edits, not failed attempts
            if (
                tool_name == "file_ops"
                and tool_params.get("action") in ("write", "create", "search_replace", "apply_diff", "batch_edit")
                and isinstance(result_obj, dict)
                and result_obj.get("ok")
            ):
                _edits_made = True

        # ── Stuck detection: inject hint AFTER all tool messages are appended ──
        # This ensures tool_call/tool message pairing is not broken (V4 Phase 10)
        if len(recent_errors) >= stuck_threshold and len(set(recent_errors)) == 1:
            messages.append({
                "role": "user",
                "content": (
                    f"⚠️ 你已经连续 {stuck_threshold} 次遇到相同的错误。"
                    f"请换一种方法解决，或者如果已经获得了足够的信息，直接给出最终回答。"
                ),
            })
            recent_errors.clear()
            yield {"type": "thought", "data": {"content": "⚠️ 检测到重复错误，已提示 Agent 更换策略"}}

        # ── Self-Reflection injection (Phase 17) ──
        # If edits were made in this iteration, inject a review prompt
        # to encourage the agent to verify changes before the final answer.
        if _edits_made:
            messages.append({
                "role": "user",
                "content": (
                    "🔍 自我审查: 你刚刚进行了文件编辑。"
                    "在给出最终回答前，请确认："
                    "1) 所有修改的文件语法正确（用 code_executor 或 code_lint 验证）"
                    "2) 没有遗漏的需求点"
                    "3) 如果一切就绪，直接给出最终回答"
                ),
            })
            logger.info("Self-reflection prompt injected after edits in task %s", task_id)

        yield {"type": "_fc_done", "data": {"status": "continue"}}

    async def _demo_mode(self, task_id: str, user_message: str) -> AsyncGenerator[dict, None]:
        answer = f"收到你的消息：「{user_message}」\n\n> 当前为演示模式。请在设置中配置 API Key 以启用 AI 任务功能。"
        self.db.save_task_message(task_id, "assistant", answer)
        yield {"type": "answer_chunk", "data": {"content": answer}}
        yield {"type": "done", "data": {}}
