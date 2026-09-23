import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import AsyncGenerator

from llm.client import LLMClient
from db.database import Database
from tools.code_executor import CodeExecutorTool
from tools.shell_tool import ShellTool, check_safe_command, check_domain_whitelist
from tools.file_ops import FileOpsTool
from tools.code_search import CodeSearchTool
from tools.web_fetch import WebFetchTool
from tools.code_lint import CodeLintTool
from tools.code_format import CodeFormatTool
from tools.package_manager import PackageManagerTool
from tools.project_scaffold import ProjectScaffoldTool
from tools.task_knowledge_search import TaskKnowledgeSearchTool
from tools.doc_generator import DocGeneratorTool
from tools.tavily_search import TavilySearchTool
from tools.repo_map import RepoMapTool
from tools.skill_runner import SkillRunnerTool
from tools.git_ops import GitOpsTool
from tools.lsp_client import LSPClientTool
from tools.venv_manager import get_venv_python
from skills.registry import SkillRegistry
from skills.context import SkillContext
from skills.loader import AgentSkillAdapter
from agent.context_manager import ContextManager, build_git_context, detect_project_context

logger = logging.getLogger(__name__)

# ── Modular System Prompt Components ──
# Each section is independently maintained and dynamically assembled.

_PROMPT_IDENTITY = """你是一个编程助手“墨方-任务”。你可以使用工具来编写和执行代码、操作文件。
始终使用用户提问的语言回复。"""

_PROMPT_IDENTITY_GENERAL = """你是一个智能助手"墨方-任务"。你可以使用工具来搜索信息、获取网页内容、生成文档、操作文件。
专注于帮助用户完成研究、写作、文档生成等通用任务。
始终使用用户提问的语言回复。

💡 提示：当前为通用模式，已有文件操作和 Shell 命令能力。如需执行代码、代码搜索、代码检查、包管理等高级编程功能，可建议用户切换到编程模式（🛠）。"""

_PROMPT_EFFICIENCY = """⚠️ 效率原则：
- 一旦获得了回答用户问题所需的信息，立即输出 Final Answer 总结结果
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
- 如果一切就绪，输出 Final Answer"""

_PROMPT_RICH_TEXT = """📊 富文本支持：
你可以在 Final Answer 中使用 Markdown 格式：
- Mermaid 图表：用 ```mermaid 代码块绘制流程图、时序图、甘特图等
- SVG 图形：直接嵌入 <svg> 标签绘制自定义图形
- 代码块、表格、列表等标准 Markdown 语法"""

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

_EDITOR_PROMPT = """你是“执行者”，负责根据架构师的计划精确执行代码修改。
请严格按照提供的执行计划进行操作。
使用工具完成每个步骤，并在完成后验证结果。"""

# ── Context compression constants ──
# Snip: truncate individual tool outputs longer than this
SNIP_THRESHOLD = 8000  # chars
# Context Collapse: when total messages exceed this, summarize early ones
COLLAPSE_THRESHOLD = 40  # messages
# After collapse, keep this many recent messages intact
KEEP_RECENT = 10


class TaskAgent:
    MAX_CONCURRENT = 3  # max simultaneous running tasks

    def __init__(self, db: Database):
        self.db = db
        self.max_iterations = 15
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._active_task_id: str | None = None
        self._running_task_ids: set[str] = set()
        self._task_iterations: dict[str, int] = {}

        # Shell confirmation: pending confirmations waiting for user response
        self._pending_confirms: dict[str, dict] = {}
        # Session-level auto-allow: task_id -> True when user clicks "allow all for this session"
        self._session_auto_allow: set[str] = set()
        # Skill confirmation: pending skill confirmations
        self._pending_skill_confirms: dict[str, dict] = {}
        # Skill scan cache: install_path → (mtime, parsed_dict) (Phase 15.4)
        self._skill_scan_cache: dict[str, tuple[float, dict]] = {}

        # Shared tool instances (used for prompt description generation only)
        self.code_executor = CodeExecutorTool()
        self.shell = ShellTool()
        self.file_ops = FileOpsTool()
        self.code_search = CodeSearchTool()
        self.web_fetch = WebFetchTool()
        self.code_lint = CodeLintTool()
        self.code_format = CodeFormatTool()
        self.package_manager = PackageManagerTool()
        self.project_scaffold = ProjectScaffoldTool()
        self.knowledge_search = TaskKnowledgeSearchTool()
        self.doc_generator = DocGeneratorTool()
        self.web_search = TavilySearchTool()
        self.repo_map = RepoMapTool()
        self.skill_runner = SkillRunnerTool(db=db)
        self.git_ops = GitOpsTool()
        self.lsp_client = LSPClientTool()
        self._tools = {
            "code_executor": self.code_executor,
            "shell": self.shell,
            "file_ops": self.file_ops,
            "code_search": self.code_search,
            "web_fetch": self.web_fetch,
            "web_search": self.web_search,
            "code_lint": self.code_lint,
            "code_format": self.code_format,
            "package_manager": self.package_manager,
            "project_scaffold": self.project_scaffold,
            "knowledge_search": self.knowledge_search,
            "doc_generator": self.doc_generator,
            "repo_map": self.repo_map,
            "skill_runner": self.skill_runner,
            "git_ops": self.git_ops,
            "lsp_client": self.lsp_client,
        }

    def update_settings(self, settings: dict):
        self.settings = settings
        self.llm = LLMClient(settings)

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
            pending["confirmed"] = confirmed
            if confirmed and allow_session:
                self._session_auto_allow.add(task_id)
            pending["event"].set()

    def respond_skill_confirm(self, task_id: str, confirmed: bool):
        """Respond to a pending skill confirmation from the frontend."""
        pending = self._pending_skill_confirms.get(task_id)
        if pending:
            pending["confirmed"] = confirmed
            pending["event"].set()

    def pause_task(self, task_id: str, messages: list[dict], iteration: int, recent_errors: list[str]) -> bool:
        """Pause a running task: save state and request cancellation."""
        state = {
            "messages": messages,
            "iteration": iteration,
            "recent_errors": recent_errors,
        }
        ok = self.db.save_agent_state(task_id, state)
        if ok:
            self.request_cancel(task_id)
        return ok

    def get_paused_state(self, task_id: str) -> dict | None:
        """Check if a task has a paused state."""
        return self.db.load_agent_state(task_id)

    def clear_paused_state(self, task_id: str):
        """Delete paused state after resume."""
        self.db.delete_agent_state(task_id)

    def _reset_cancel(self, task_id: str):
        """Create a fresh cancel event for a specific task."""
        self._cancel_events[task_id] = asyncio.Event()

    @property
    def is_any_running(self) -> bool:
        return len(self._running_task_ids) > 0

    @property
    def running_count(self) -> int:
        return len(self._running_task_ids)

    def can_start_task(self) -> bool:
        return len(self._running_task_ids) < self.MAX_CONCURRENT

    def _register_external_skills(self, skill_registry: SkillRegistry):
        """Load external skills from DB and register them into SkillRegistry.

        Supports Agent Skills standard format (SKILL.md).
        Uses mtime-based cache to avoid re-parsing unchanged SKILL.md files (Phase 15.4).
        """
        try:
            from skills.parser import parse_skill_md, discover_skill_type, validate_skill_md
            all_skills = self.db.list_skills()
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
                    logger.info("Registered Agent Skill: %s (from %s)", adapter.name, install_path)

                else:
                    logger.debug("No valid skill manifest found in %s, skipping", install_path)
        except Exception as e:
            logger.warning("Failed to load external skills: %s", e)

    def _get_tools_description(self, mode: str = "coding") -> str:
        """Generate rich tool descriptions for the system prompt."""
        # In general mode, show file_ops + shell + web/knowledge/doc tools
        if mode == "general":
            general_tools = ["file_ops", "shell", "web_fetch", "web_search", "knowledge_search", "doc_generator", "repo_map", "skill_runner", "git_ops", "lsp_client"]
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
            "所有文件操作的路径都是相对于工作目录的。",
        ]
        if mode == "coding":
            parts.append("每个任务有独立的 Python 虚拟环境，pip install 的包只在此任务中可用。")
        parts.extend([
            "",
            _PROMPT_EFFICIENCY,
        ])
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
        full_parts = parts + [s for _, s in included_sections]
        full_prompt = "\n".join(full_parts)
        prompt_tokens = ContextManager.count_tokens(full_prompt)

        if prompt_tokens > PROMPT_BUDGET:
            # Drop sections from lowest priority first
            for name, section_parts in reversed(list(included_sections)):
                included_sections = [(n, s) for n, s in included_sections if n != name]
                full_parts = parts + [s for _, s in included_sections]
                test_prompt = "\n".join(full_parts)
                test_tokens = ContextManager.count_tokens(test_prompt)
                if test_tokens <= PROMPT_BUDGET:
                    logger.warning(
                        "System prompt truncated (dropped %s): %d tokens (budget: %d)",
                        name, test_tokens, PROMPT_BUDGET,
                    )
                    return test_prompt
            full_prompt = "\n".join(full_parts)
            logger.warning(
                "System prompt truncated (dropped all optional): %d tokens (budget: %d)",
                ContextManager.count_tokens(full_prompt), PROMPT_BUDGET,
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

    def _create_task_tools(self, workspace_path: str, venv_path: str = "", settings: dict | None = None, mode: str = "coding") -> dict:
        """Create independent tool instances for a single task.
        
        Each concurrent task gets its own tools to avoid workspace/venv conflicts.
        In 'general' mode, returns only web_fetch, knowledge_search, doc_generator.
        """
        doc_generator = DocGeneratorTool()
        doc_generator.set_workspace(workspace_path)

        # Create independent instances for web/knowledge tools (safe for concurrent use)
        web_fetch = WebFetchTool()
        web_search = TavilySearchTool(api_key=settings.get("tavily_key", "") if settings else "")
        knowledge_search = TaskKnowledgeSearchTool()
        if self.knowledge_search.db:
            knowledge_search.configure(
                db=self.knowledge_search.db,
                chroma_persist_dir=self.knowledge_search.chroma_persist_dir,
                models_dir=self.knowledge_search.models_dir,
            )

        # Repo-map tool: per-task workspace-aware instance
        repo_map = RepoMapTool(workspace_dir=workspace_path)

        # Skill runner: per-task instance with DB reference
        skill_runner = SkillRunnerTool(db=self.db)
        skill_runner.task_workspace_path = workspace_path

        # Git ops: per-task workspace-aware instance
        git_ops = GitOpsTool()
        git_ops.set_workspace(workspace_path)

        # LSP client: per-task workspace-aware instance
        lsp_client = LSPClientTool(workspace_dir=workspace_path)

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
            return {
                "file_ops": file_ops,
                "shell": shell,
                "web_fetch": web_fetch,
                "web_search": web_search,
                "knowledge_search": knowledge_search,
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
            "web_fetch": web_fetch,
            "web_search": web_search,
            "code_lint": code_lint,
            "code_format": code_format,
            "package_manager": package_manager,
            "project_scaffold": project_scaffold,
            "knowledge_search": knowledge_search,
            "doc_generator": doc_generator,
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
        for attempt in range(max_retries):
            # Check if there's a lint error to fix
            try:
                result_obj = json.loads(result_str)
            except (json.JSONDecodeError, TypeError):
                break

            if not result_obj.get("lint_warning"):
                break  # No error, done

            lint_warning = result_obj["lint_warning"]
            fix_path = tool_params.get("path", "")
            logger.info(
                "Edit-verify loop: lint error on %s, auto-fix attempt %d/%d",
                fix_path, attempt + 1, max_retries,
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
                fixed_content = await task_llm.chat(fix_prompt, settings)
                if not fixed_content or not isinstance(fixed_content, str):
                    break

                # Strip markdown code fences if present
                fixed_content = fixed_content.strip()
                if fixed_content.startswith("```"):
                    lines = fixed_content.split("\n")
                    lines = lines[1:]  # remove opening fence
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    fixed_content = "\n".join(lines)

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
            # This is best-effort; don't fail the task if logging fails
            from main import get_data_dir
            log_dir = os.path.join(get_data_dir(), "tasks", task_id)
            if not os.path.isdir(log_dir):
                return
            log_path = os.path.join(log_dir, "execution.log")
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            # Truncate result for log
            result_preview = result[:500] + "..." if len(result) > 500 else result
            input_preview = json.dumps(tool_input, ensure_ascii=False)[:200]
            entry = f"[{ts}] [INFO] [{tool_name}] input={input_preview} | duration={duration_ms}ms | result={result_preview}\n"
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

    async def _execute_slash_command(
        self, task_id: str, cmd: str, params_str: str, tools: dict,
        settings: dict | None = None,
        skill_registry: SkillRegistry | None = None,
        workspace_path: str = "", venv_path: str = "",
    ) -> AsyncGenerator[dict, None]:
        """Execute a slash command directly, bypassing the LLM.

        Handles /help and /skill-name [params].
        Supports Agent Skills (SKILL.md) format.
        """
        # Reconstruct original user message for saving
        text = f"/{cmd} {params_str}".strip() if params_str else f"/{cmd}"

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

        # Check if this matches an installed skill
        skill_runner._refresh_cache()
        skill_info = skill_runner._find_skill(cmd)
        if not skill_info:
            available = [s["name"] for s in skill_runner._skill_cache]
            if available:
                answer = f"未找到 Skill: `{cmd}`\n\n可用 Skill: {', '.join(f'`{s}`' for s in available)}"
            else:
                answer = "当前无已安装的第三方 Skill。请在设置页安装。"
            # NOTE: user message already saved by routers/tasks.py task_chat()
            self.db.save_task_message(task_id, "assistant", answer)
            yield {"type": "answer_chunk", "data": {"content": answer}}
            yield {"type": "done", "data": {}}
            return

        # Parse optional JSON params
        params: dict = {}
        if params_str:
            try:
                params = json.loads(params_str)
            except json.JSONDecodeError:
                # Treat as a plain text input
                params = {"input": params_str}

        # ── Skill confirmation check (for unconfirmed third-party skills) ──
        if skill_info.get("needs_confirm"):
            confirm_event = asyncio.Event()
            self._pending_skill_confirms[task_id] = {
                "skill_name": cmd,
                "confirmed": False,
                "event": confirm_event,
            }
            yield {"type": "confirm_skill", "data": {
                "skill_name": cmd,
                "task_id": task_id,
            }}
            try:
                await asyncio.wait_for(confirm_event.wait(), timeout=60)
            except asyncio.TimeoutExpired:
                pass
            pending_skill = self._pending_skill_confirms.pop(task_id, {})
            if not pending_skill.get("confirmed"):
                answer = f"⚠️ Skill `{cmd}` 未经用户确认，已取消执行。"
                self.db.save_task_message(task_id, "assistant", answer)
                yield {"type": "answer_chunk", "data": {"content": answer}}
                yield {"type": "done", "data": {}}
                return

        # Execute the skill directly
        # NOTE: user message already saved by routers/tasks.py task_chat()
        yield {"type": "thought", "data": {"content": f"🧩 直接调用 Skill: {cmd}"}}

        install_path = skill_info.get("install_path", "")
        skill_md = os.path.join(install_path, "SKILL.md") if install_path else ""

        # ── Direct execution path (CLI input or isolated skill) ──
        # Note: Agent Skills with NL input are handled by the normal agent loop
        # (see pre-check in _run_inner), so we only reach here for CLI input.
        action_meta = {"tool_name": "skill_runner", "tool_input": {"skill": cmd, "input": params}}
        self.db.save_task_message(task_id, "action", "", metadata=action_meta)
        yield {"type": "action", "data": {"tool": "skill_runner", "input": {"skill": cmd, "input": params}}}

        exec_start = time.time()

        # Detect Agent Skill (SKILL.md format) and use AgentSkillAdapter
        if os.path.isfile(skill_md):
            try:
                from skills.parser import parse_skill_md, validate_skill_md
                parsed = parse_skill_md(skill_md)
                errors = validate_skill_md(parsed)
                if errors:
                    result = json.dumps({"error": f"SKILL.md 验证失败: {'; '.join(errors)}"}, ensure_ascii=False)
                else:
                    adapter = AgentSkillAdapter(install_path, parsed, skill_info)
                    # Build SkillContext with task's venv
                    venv_python = get_venv_python(venv_path) if venv_path else ""
                    ctx = SkillContext(
                        workspace_path=workspace_path,
                        venv_python=venv_python,
                        task_id=task_id,
                        settings=settings or {},
                    )
                    # Map slash command params → Agent Skill 'command' param
                    agent_params = {"command": params_str}
                    result = await adapter.execute(agent_params, ctx)
            except Exception as e:
                result = json.dumps({"error": str(e)}, ensure_ascii=False)
        else:
            # Not a recognized Agent Skill
            result = json.dumps({"error": f"Skill `{cmd}` 不是有效的 Agent Skill (缺少 SKILL.md)"}, ensure_ascii=False)
        exec_duration = int((time.time() - exec_start) * 1000)

        # Format result for display
        try:
            result_obj = json.loads(result)
            if "error" in result_obj:
                answer = f"❌ Skill `{cmd}` 执行失败: {result_obj['error']}"
            elif "output" in result_obj:
                answer = result_obj["output"]
            else:
                answer = f"```json\n{json.dumps(result_obj, ensure_ascii=False, indent=2)}\n```"
        except (json.JSONDecodeError, TypeError):
            answer = result

        yield {"type": "observation", "data": {"content": result}}
        self.db.save_task_message(
            task_id, "observation", result,
            metadata={**action_meta, "tool_output": result[:5000]},
        )
        self._log_execution(task_id, "skill_runner", {"skill": cmd, "input": params}, result, exec_duration)

        self.db.save_task_message(task_id, "assistant", answer)
        yield {"type": "answer_chunk", "data": {"content": answer}}
        yield {"type": "done", "data": {}}

    # ── Main Run Loop ──

    async def run(self, task_id: str, user_message: str) -> AsyncGenerator[dict, None]:
        self._reset_cancel(task_id)
        self._active_task_id = task_id
        self._running_task_ids.add(task_id)
        self._task_iterations[task_id] = 0
        try:
            async for event in self._run_inner(task_id, user_message):
                yield event
        finally:
            self._running_task_ids.discard(task_id)
            self._cancel_events.pop(task_id, None)
            self._task_iterations.pop(task_id, None)
            if self._active_task_id == task_id:
                self._active_task_id = None

    async def _run_planner(
        self, planner_llm: LLMClient, settings: dict,
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

        plan = await planner_llm.chat(planner_msgs, settings)
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

        # Override model with task-specific model if configured
        task_model = settings.get("task_model", "")
        if task_model:
            settings["model"] = task_model

        # Apply task-specific timeout to tools
        task_timeout = settings.get("task_timeout", 30)

        # Create per-task LLM client to avoid model cross-contamination in concurrent mode
        task_llm = LLMClient(settings)

        # ── Planner model setup (dual-model architecture) ──
        planner_model_name = settings.get("task_planner_model", "")
        planner_llm = None
        if planner_model_name:
            planner_settings = dict(settings)
            planner_settings["model"] = planner_model_name
            planner_llm = LLMClient(planner_settings)

        if not settings.get("api_key"):
            async for event in self._demo_mode(task_id, user_message):
                yield event
            return

        # Get task info
        task = self.db.get_task(task_id)
        if not task:
            yield {"type": "answer_chunk", "data": {"content": "任务不存在"}}
            yield {"type": "done", "data": {}}
            return

        workspace_path = task["workspace_path"]
        venv_path = task.get("venv_path", "")
        task_mode = task.get("mode", "coding") or "coding"
        # Create per-task tool instances to avoid workspace/venv conflicts in concurrent mode
        tools = self._create_task_tools(workspace_path, venv_path, settings, mode=task_mode)
        # Apply task-specific timeout to shell and code_executor
        if "shell" in tools:
            tools["shell"].timeout = task_timeout
        if "code_executor" in tools:
            tools["code_executor"].timeout = task_timeout

        # ── V2: Skill Registry + Function Calling detection ──
        skill_registry = SkillRegistry()
        skill_registry.register_builtin_tools(tools)

        # ── Load external skills from DB and register them ──
        self._register_external_skills(skill_registry)

        model_name = settings.get("model", "gpt-4o")
        logger.info("Using Function Calling mode for task %s (model=%s)", task_id, model_name)

        # ── V2: Context Manager (token-aware) ──
        ctx_mgr = ContextManager(
            snip_threshold=SNIP_THRESHOLD,
            keep_recent=KEEP_RECENT,
        )

        # ── Slash command interception: /skill-name bypasses LLM ──
        slash_parsed = self._is_slash_command(user_message)
        if slash_parsed:
            slash_cmd, slash_params = slash_parsed

            # Pre-check: Agent Skill + natural language → let normal agent loop handle it
            # so the LLM has tool access and can call the skill's script for real data.
            _should_fallthrough = False
            if slash_params:
                _sr = tools.get("skill_runner")
                if _sr:
                    _sr._refresh_cache()
                    _si = _sr._find_skill(slash_cmd)
                    if _si:
                        _ip = _si.get("install_path", "")
                        _smd = os.path.join(_ip, "SKILL.md") if _ip else ""
                        if os.path.isfile(_smd):
                            # It's an Agent Skill — check if input is CLI or NL
                            import shlex as _sh
                            try:
                                _toks = _sh.split(slash_params)
                            except ValueError:
                                _toks = []
                            if _toks:
                                from skills.parser import parse_skill_md as _psm
                                _p = _psm(_smd)
                                _body_text = _p.get("body", "") if _p else ""
                                _is_cli = False
                                for _ln in _body_text.splitlines():
                                    _s = _ln.strip().lstrip("-").strip("`").strip()
                                    if _s and _toks[0] in _s and _s.startswith(_toks[0]):
                                        _is_cli = True
                                        break
                                if not _is_cli:
                                    _should_fallthrough = True

            if not _should_fallthrough:
                async for event in self._execute_slash_command(
                    task_id, slash_cmd, slash_params, tools, settings,
                    skill_registry=skill_registry,
                    workspace_path=workspace_path, venv_path=venv_path,
                ):
                    yield event
                return
            # else: fall through to normal agent loop below

        # Build message history
        history = self.db.get_task_messages(task_id)
        system_prompt = self._build_system_prompt(workspace_path, mode=task_mode)
        if settings.get("system_prompt"):
            system_prompt = settings["system_prompt"] + "\n\n" + system_prompt

        # ── Check for paused state (resume) ──
        paused_state = self.db.load_agent_state(task_id)
        start_iteration = 0
        if paused_state:
            messages = paused_state.get("messages", [])
            start_iteration = paused_state.get("iteration", 0)
            recent_errors_saved = paused_state.get("recent_errors", [])
            self.db.delete_agent_state(task_id)
            yield {"type": "thought", "data": {"content": f"▶️ 从第 {start_iteration} 轮恢复执行..."}}
        else:
            messages = [{"role": "system", "content": system_prompt}]
            for msg in history:
                if msg["role"] in ("user", "assistant"):
                    messages.append({"role": msg["role"], "content": msg["content"]})
            recent_errors_saved = None

        # ── Slash command fallthrough hint ──
        # When an Agent Skill is invoked via /skill-name with natural language,
        # guide the LLM to call the skill tool instead of answering directly.
        if slash_parsed and _should_fallthrough:
            _fc, _p = slash_parsed
            _hint = (
                f"[系统提示] 用户通过 `/{_fc}` 调用了名为 `{_fc}` 的 Skill。\n"
                f"你 **必须** 使用工具来执行此 Skill，不要自己直接回答。\n\n"
                f"可用工具（任选其一）：\n"
                f"1. **优先**: 调用 `{_fc}` 函数（将自然语言翻译为 CLI 参数传入 `command` 字段）\n"
                f"2. **备选**: 调用 `skill_runner` 工具，参数 skill=\"{_fc}\", input={{\"command\": \"CLI参数\"}}\n\n"
                f"示例：用户说「统计字数 Hello」→ command=\"wordcount --text Hello\"\n"
                f"用户的完整输入: `{_p}`\n"
                f"请参考 `{_fc}` 的使用指南构造正确的 CLI 参数。"
            )
            messages.append({"role": "user", "content": _hint})
            logger.info("Injected slash fallthrough hint for skill '%s'", _fc)

        # ── Phase 1: Planner (if configured) ──
        if planner_llm:
            yield {"type": "thought", "data": {"content": "🏗️ 架构师正在制定执行计划..."}}
            try:
                plan = await self._run_planner(planner_llm, settings, user_message, messages)
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

        # Stuck detection state
        recent_errors: list[str] = recent_errors_saved if recent_errors_saved is not None else []
        STUCK_THRESHOLD = 3  # consecutive same errors → stuck

        # Per-task cancel event for this execution
        cancel_event = self._cancel_events.get(task_id) or asyncio.Event()

        # Configurable max iterations (default 25, up from 15)
        max_iter = settings.get("task_max_iterations", 25)
        if not isinstance(max_iter, int) or max_iter < 1:
            max_iter = 25

        for iteration in range(start_iteration, max_iter):
            self._task_iterations[task_id] = iteration
            if cancel_event.is_set():
                break

            # Context Collapse: compress if too many messages (token-aware)
            messages = await ctx_mgr.compress(messages, task_llm, settings)

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
            ):
                if fc_event.get("type") == "_fc_done":
                    fc_completed = True
                    fc_status = fc_event["data"].get("status", "continue")
                    break
                yield fc_event
            if fc_completed:
                if fc_status in ("done", "error"):
                    yield {"type": "done", "data": {}}
                    return

        if cancel_event.is_set():
            fallback = "已停止生成。"
        else:
            fallback = "抱歉，我尝试了多次但未能得出答案。请尝试换一种方式描述需求。"
        self.db.save_task_message(task_id, "assistant", fallback)
        yield {"type": "answer_chunk", "data": {"content": fallback}}
        yield {"type": "done", "data": {}}

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
        reasoning_flushed = False  # Track if reasoning has been yielded
        tc_accum: dict[int, dict] = {}  # index -> {id, function: {name, arguments}}
        finish_reason = "unknown"
        try:
            async for chunk in task_llm.chat_stream(messages, settings, tools=tool_defs):
                if cancel_event.is_set():
                    yield {"type": "_fc_done", "data": {"status": "error"}}
                    return
                if chunk["type"] == "reasoning":
                    delta = chunk["delta"]
                    reasoning_parts.append(delta)
                elif chunk["type"] == "content":
                    # Flush reasoning before first content chunk (ensure correct order)
                    if not reasoning_flushed and reasoning_parts:
                        rc = "".join(reasoning_parts)
                        if rc.strip():
                            yield {"type": "reasoning", "data": {"content": rc}}
                            self.db.save_task_message(task_id, "reasoning", rc)
                        reasoning_flushed = True
                    
                    delta = chunk["delta"]
                    content_parts.append(delta)
                    # Stream text content to frontend
                    yield {"type": "answer_chunk", "data": {"content": delta}}
                elif chunk["type"] == "tool_calls":
                    # Streaming complete — tool_calls accumulated
                    for i, tc in enumerate(chunk["tool_calls"]):
                        tc_accum[i] = tc
                elif chunk["type"] == "finish":
                    finish_reason = chunk.get("finish_reason", "unknown")

            logger.info(
                "TaskAgent FC response [%d] finish=%s tool_calls=%d for task %s",
                iteration, finish_reason, len(tc_accum), task_id,
            )
        except Exception as e:
            # Include detail from LLMCallFailed if available
            detail = getattr(e, 'detail', '') or str(e)
            error_msg = f"调用模型出错: {detail}"
            logger.error("TaskAgent FC LLM call exception for task %s: %s", task_id, detail)
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
        # Yield reasoning if not already flushed during streaming
        if not reasoning_flushed and reasoning_content and reasoning_content.strip():
            yield {"type": "reasoning", "data": {"content": reasoning_content}}
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
        confirm_mode = settings.get("task_shell_confirm_mode", "smart")
        if settings.get("task_auto_confirm_shell", False):
            confirm_mode = "auto"

        for tc in tool_calls:
            if cancel_event.is_set():
                yield {"type": "_fc_done", "data": {"status": "error"}}
                return

            tc_id = tc["id"]
            tool_name = tc["function"]["name"]
            try:
                tool_params = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                tool_params = {}

            # Yield action event for frontend
            yield {"type": "action", "data": {"tool": tool_name, "input": tool_params}}
            self.db.save_task_message(
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
                    self.db.save_task_message(
                        task_id, "action", "",
                        metadata={"confirm_shell": command, "task_id": task_id},
                    )
                    yield {"type": "confirm_shell", "data": {
                        "command": command,
                        "task_id": task_id,
                    }}
                    try:
                        await asyncio.wait_for(confirm_event.wait(), timeout=30)
                    except asyncio.TimeoutExpired:
                        pass
                    pending = self._pending_confirms.pop(task_id, {})
                    if not pending.get("confirmed"):
                        result_str = json.dumps({
                            "exit_code": -1, "stdout": "",
                            "stderr": "用户拒绝了此 shell 命令的执行。",
                            "error_type": "UserDenied",
                        }, ensure_ascii=False)

            # ── Skill confirmation check ──
            if tool_name == "skill_runner" and result_str is None:
                _skill_name = tool_params.get("skill", "")
                _skill_runner_inst = tools.get("skill_runner")
                if _skill_runner_inst:
                    _skill_runner_inst._refresh_cache()
                    _sinfo = _skill_runner_inst._find_skill(_skill_name)
                    if _sinfo and _sinfo.get("needs_confirm"):
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
                        try:
                            await asyncio.wait_for(_confirm_event.wait(), timeout=60)
                        except asyncio.TimeoutExpired:
                            pass
                        _pending = self._pending_skill_confirms.pop(task_id, {})
                        if not _pending.get("confirmed"):
                            result_str = json.dumps({
                                "error": f"用户未确认 Skill '{_skill_name}' 的执行权限",
                                "error_type": "UserDenied",
                            }, ensure_ascii=False)

            # ── git_ops commit confirmation ──
            if tool_name == "git_ops" and result_str is None:
                if tool_params.get("action") == "commit":
                    _commit_msg = tool_params.get("args", "")
                    _confirm_event = asyncio.Event()
                    self._pending_confirms[task_id] = {
                        "command": f"git commit {_commit_msg}",
                        "confirmed": False,
                        "event": _confirm_event,
                    }
                    self.db.save_task_message(
                        task_id, "action", "",
                        metadata={"confirm_shell": f"git commit {_commit_msg}", "task_id": task_id},
                    )
                    yield {"type": "confirm_shell", "data": {
                        "command": f"git commit {_commit_msg}",
                        "task_id": task_id,
                    }}
                    try:
                        await asyncio.wait_for(_confirm_event.wait(), timeout=30)
                    except asyncio.TimeoutExpired:
                        pass
                    _pending = self._pending_confirms.pop(task_id, {})
                    if not _pending.get("confirmed"):
                        result_str = json.dumps({
                            "exit_code": -1, "stdout": "",
                            "stderr": "用户拒绝了此 git commit 操作。",
                            "error_type": "UserDenied",
                        }, ensure_ascii=False)

            # ── Tool execution ──
            if result_str is None:
                tool = tools.get(tool_name)
                if tool:
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
                        result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                else:
                    # Check if it's an external skill registered in skill_registry
                    skill = skill_registry.get(tool_name)
                    if skill:
                        # External skill execution via SkillRegistry
                        try:
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
                        except Exception as e:
                            result_str = json.dumps({"error": str(e)}, ensure_ascii=False)
                    else:
                        result_str = f"未知工具: {tool_name}"

            exec_duration = int((time.time() - exec_start) * 1000)

            # Ensure result is a string (some tools may return None)
            if result_str is None:
                result_str = ""

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
            self.db.save_task_message(
                task_id, "observation", result_str,
                metadata={"tool_name": tool_name, "tool_input": tool_params, "tool_output": result_str[:5000]},
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
            if tool_name == "file_ops" and tool_params.get("action") in ("write", "create", "search_replace", "apply_diff", "batch_edit"):
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
        # to encourage the agent to verify changes before Final Answer.
        if _edits_made:
            messages.append({
                "role": "user",
                "content": (
                    "🔍 自我审查: 你刚刚进行了文件编辑。"
                    "在给出最终回答前，请确认："
                    "1) 所有修改的文件语法正确（用 code_executor 或 code_lint 验证）"
                    "2) 没有遗漏的需求点"
                    "3) 如果一切就绪，输出 Final Answer"
                ),
            })
            logger.info("Self-reflection prompt injected after edits in task %s", task_id)

        yield {"type": "_fc_done", "data": {"status": "continue"}}

    async def _demo_mode(self, task_id: str, user_message: str) -> AsyncGenerator[dict, None]:
        answer = f"收到你的消息：「{user_message}」\n\n> 当前为演示模式。请在设置中配置 API Key 以启用 AI 任务功能。"
        self.db.save_task_message(task_id, "assistant", answer)
        yield {"type": "answer_chunk", "data": {"content": answer}}
        yield {"type": "done", "data": {}}
