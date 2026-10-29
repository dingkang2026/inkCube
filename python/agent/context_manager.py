"""python/agent/context_manager.py — Token-aware context management.

Replaces the hard-coded thresholds in task_agent.py:
- SNIP_THRESHOLD (32000 chars) → ContextManager.snip()
- COLLAPSE_THRESHOLD (40 msgs) → ContextManager.should_compress()
- KEEP_RECENT (10 msgs) → ContextManager.compress()

Also provides git context injection and project auto-detection
for the system prompt.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from typing import Any

from llm.budget import count_tokens
from tools.proc_priority import hidden_window_kwargs

logger = logging.getLogger(__name__)


def summarize_tool_result_line(tool_name: str, content: str, max_len: int = 160) -> str:
    """One-line road-sign summary of a tool result（统一路标格式，1.1 §6.2）。

    两个入口共用同一格式（工具结果在上下文中被丢弃时的活动线索）：
    - 回合内 Micro Compact（70% 压力时压早期工具结果）
    - 回合间重建（DB 回放只携 user/assistant 全文，TaskAgent._rebuild_history_from_db）
    提取结构化信号（exit_code / ok / error）+ 首行语义；「文件即状态」
    仍是最终权威，本函数只产出路标，不承诺还原全部信息。
    """
    name = (tool_name or "tool").strip() or "tool"
    text = (content or "").strip()
    if not text:
        return f"{name} → (空结果)"

    status = ""
    head = ""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        obj = None
    if isinstance(obj, dict):
        if "exit_code" in obj:
            status = f"exit={obj['exit_code']}"
        elif "ok" in obj:
            status = f"ok={obj['ok']}"
        err = obj.get("error") or obj.get("stderr")
        if err:
            head = str(err).strip().split("\n")[0]
        else:
            for key in ("installed", "uninstalled", "stdout", "output",
                        "content", "result"):
                val = obj.get(key)
                if isinstance(val, list) and val:
                    head = ", ".join(str(v) for v in val[:3])
                    break
                if isinstance(val, str) and val.strip():
                    head = val.strip().split("\n")[0]
                    break
    if not head:
        head = text.split("\n")[0].strip()

    line = f"{name} → {status + ' | ' if status else ''}{head}"
    if len(line) > max_len:
        line = line[:max_len] + "..."
    return line


class ContextManager:
    """Token-aware context window manager for the agent loop.

    Responsibilities:
    1. Estimate token usage of the message history.
    2. Decide when to compress (Context Collapse).
    3. Compress by summarising early messages via LLM.
    4. Snip overly-long individual tool outputs.
    """

    def __init__(
        self,
        max_tokens: int = 128_000,
        reserve_for_response: int = 4096,
        reserve_for_system: int = 2000,
        snip_threshold: int = 32000,
        compress_ratio: float = 0.85,
        keep_recent: int = 10,
    ):
        self.max_tokens = max_tokens
        self.reserve_for_response = reserve_for_response
        self.reserve_for_system = reserve_for_system
        self.snip_threshold = snip_threshold
        self.compress_ratio = compress_ratio
        self.keep_recent = keep_recent

    # ── Token estimation ──
    # count_tokens 自 llm.budget 导入（D2 唯一计数入口），此处不再持有实现。

    @property
    def available_tokens(self) -> int:
        """Tokens available for the conversation history."""
        return self.max_tokens - self.reserve_for_response - self.reserve_for_system

    def total_tokens(self, messages: list[dict]) -> int:
        """Estimate total tokens across all messages."""
        return sum(count_tokens(m.get("content", "") or "") for m in messages)

    # ── Compression decision ──

    def should_compress(self, messages: list[dict]) -> bool:
        """Return True when the message history exceeds the budget."""
        return self.total_tokens(messages) > self.available_tokens * self.compress_ratio

    # ── Compression (Context Collapse) ──

    async def compress(
        self,
        messages: list[dict],
        llm: Any,
    ) -> list[dict]:
        """Progressive compression entry point.

        Applies strategies in order based on token usage:
        1. Micro Compact (70%) — compress early tool results to one-liners
        2. Context Collapse (85%) — LLM summary of early messages
        3. Full Compact (95%) — aggressive LLM summary of entire history
        """
        total = self.total_tokens(messages)

        # Level 1: Micro Compact (70%)
        if total > self.available_tokens * 0.70:
            messages = self.micro_compact(messages)
            total = self.total_tokens(messages)

        # Level 2: Context Collapse (85%)
        if total > self.available_tokens * 0.85:
            messages = await self._context_collapse(messages, llm)
            total = self.total_tokens(messages)

        # Level 3: Full Compact (95%)
        if total > self.available_tokens * 0.95:
            messages = await self.full_compact(messages, llm)

        return messages

    async def _context_collapse(
        self,
        messages: list[dict],
        llm: Any,
    ) -> list[dict]:
        """Context Collapse: summarise early messages via LLM.

        Keeps the system message + the most recent ``keep_recent`` messages
        intact.  Earlier messages are summarised into a single user message.

        Falls back to truncation-based summary if LLM is unavailable.
        """
        # Separate system message
        system_msgs = [m for m in messages if m["role"] == "system"]
        non_system = [m for m in messages if m["role"] != "system"]

        if len(non_system) <= self.keep_recent + 2:
            return messages

        old_msgs = non_system[:-self.keep_recent]
        recent_msgs = non_system[-self.keep_recent:]

        summary_text = None

        # Try LLM-based summarisation（api_key 判据改实例状态：settings 不再随调用传递）
        if llm and getattr(llm, "api_key", ""):
            try:
                convo_lines = []
                for msg in old_msgs:
                    role_label = "用户" if msg["role"] == "user" else "助手"
                    content = msg.get("content", "")
                    if len(content) > 500:
                        content = content[:500] + "..."
                    convo_lines.append(f"[{role_label}]: {content}")
                conversation_text = "\n".join(convo_lines)

                summary_prompt = [
                    {"role": "system", "content": "你是一个摘要助手。请将以下对话历史压缩为简洁的摘要，保留关键信息（用户需求、已完成的操作、重要结果）。摘要应让后续对话能理解上下文。直接输出摘要内容，不要加前缀。"},
                    {"role": "user", "content": f"请摘要以下对话历史：\n\n{conversation_text}"},
                ]
                summary_text = (await llm.chat_full(summary_prompt))["content"]
                if summary_text and len(summary_text.strip()) > 10:
                    summary_text = summary_text.strip()
                    logger.info(
                        "Context compressed via LLM: %d messages → summary (%d chars) + %d recent",
                        len(old_msgs), len(summary_text), len(recent_msgs),
                    )
                else:
                    summary_text = None
            except Exception as e:
                logger.warning("LLM summarisation failed, falling back to truncation: %s", e)
                summary_text = None

        # Fallback: truncation-based summary
        if not summary_text:
            summary_parts = []
            for msg in old_msgs:
                role_label = "用户" if msg["role"] == "user" else "助手"
                content = msg.get("content", "")
                if len(content) > 200:
                    content = content[:200] + "..."
                summary_parts.append(f"[{role_label}]: {content}")
            summary_text = "\n".join(summary_parts)
            logger.info(
                "Context compressed via truncation: %d messages → summary + %d recent",
                len(old_msgs), len(recent_msgs),
            )

        result = list(system_msgs)
        result.append({
            "role": "user",
            "content": f"[早期对话摘要]\n{summary_text}\n[摘要结束 — 后续为最近对话]",
        })
        result.extend(recent_msgs)
        # Ensure tool_call/tool message pairing is not broken
        result = self._fix_tool_pairing(result)
        return result

    @staticmethod
    def _fix_tool_pairing(messages: list[dict]) -> list[dict]:
        """Remove orphaned tool messages whose assistant(tool_calls) was summarised away.

        OpenAI API requires every ``tool`` role message to have a matching
        ``assistant`` message with a ``tool_calls`` entry.  After compression
        the assistant message may have been summarised, leaving orphaned
        tool messages that would cause an API error.
        """
        # Collect valid tool_call_ids from assistant messages
        valid_ids: set[str] = set()
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if isinstance(tc, dict) and "id" in tc:
                        valid_ids.add(tc["id"])

        # Filter: keep non-tool messages + tool messages with valid parent
        return [
            m for m in messages
            if m.get("role") != "tool" or m.get("tool_call_id") in valid_ids
        ]

    # ── Observation snipping ──

    def snip(self, text: str, tool_name: str = "", workspace_path: str = "") -> str:
        """Snip long tool output: prefer disk dump, fallback to head+tail.

        If workspace_path is provided, saves full output to file (Disk Dump).
        Otherwise keeps head + tail and marks middle as truncated.
        """
        if not text:  # Handle None or empty string
            return text or ""
        if len(text) <= self.snip_threshold:
            return text
        # Disk Dump: save to file if workspace available
        if workspace_path:
            return self.disk_dump(text, tool_name or "tool", workspace_path)
        # Fallback: head + tail snip
        head_size = self.snip_threshold * 2 // 3
        tail_size = self.snip_threshold // 3
        return (
            text[:head_size]
            + f"\n\n[...snipped: {len(text)} chars total, showing head+tail...]\n\n"
            + text[-tail_size:]
        )

    # ── Micro Compact (Phase 12) ──

    def micro_compact(self, messages: list[dict]) -> list[dict]:
        """Compress early tool results to one-line summaries.

        Triggered at 70% capacity (before Context Collapse at 85%).
        Keeps recent messages intact. Creates new dicts to avoid
        mutating the original message list.
        """
        total = self.total_tokens(messages)
        if total <= self.available_tokens * 0.70:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        non_system = [m for m in messages if m["role"] != "system"]

        if len(non_system) <= self.keep_recent + 2:
            return messages

        old_msgs = non_system[:-self.keep_recent]
        recent_msgs = non_system[-self.keep_recent:]

        # tool_call_id → 工具名（OpenAI 格式 tool 消息不带 name，从配对的
        # assistant.tool_calls 反查；查不到回落 "tool"）
        tc_names: dict[str, str] = {}
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("id"):
                        fname = (tc.get("function") or {}).get("name", "")
                        if fname:
                            tc_names[tc["id"]] = fname

        # Compress tool results in old messages (create new dicts)
        # 摘要统一走 summarize_tool_result_line（与回合间重建共用路标格式）
        compressed_old = []
        for msg in old_msgs:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if len(content) > 500:
                    summary = summarize_tool_result_line(
                        tc_names.get(msg.get("tool_call_id", ""), "tool"), content)
                    msg = dict(msg)
                    msg["content"] = f"[已压缩] {summary}"
            compressed_old.append(msg)

        return list(system_msgs) + compressed_old + recent_msgs

    # ── Disk Dump (Phase 12) ──

    def disk_dump(self, text: str, tool_name: str, workspace_path: str) -> str:
        """Save long tool output to file, return summary reference.

        Used when a single tool output exceeds the snip threshold.
        Falls back to snip() if file writing fails.
        """
        if len(text) <= self.snip_threshold:
            return text

        if not workspace_path:
            return self.snip(text)

        dump_dir = os.path.join(workspace_path, ".inkcube", "dumps")
        try:
            os.makedirs(dump_dir, exist_ok=True)
        except Exception:
            return self.snip(text)

        ts = time.strftime("%Y%m%d_%H%M%S")
        short_id = uuid.uuid4().hex[:6]
        dump_file = os.path.join(dump_dir, f"{tool_name}_{ts}_{short_id}.txt")

        try:
            with open(dump_file, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            return self.snip(text)

        head = text[:4000]
        return (
            f"[输出已保存到 {dump_file}，共 {len(text)} 字符]\n"
            f"前 4000 字符:\n{head}\n"
            f"[如需完整内容，用 file_ops read {dump_file}]"
        )

    # ── Full Compact (Phase 12) ──

    async def full_compact(
        self,
        messages: list[dict],
        llm: Any,
    ) -> list[dict]:
        """Last-resort compression: LLM summarizes entire history.

        Triggered at 95% capacity. Keeps only the last 3 messages
        and creates a comprehensive structured summary of everything else.
        """
        total = self.total_tokens(messages)
        if total <= self.available_tokens * 0.95:
            return messages

        system_msgs = [m for m in messages if m["role"] == "system"]
        non_system = [m for m in messages if m["role"] != "system"]

        if len(non_system) <= 3:
            return messages

        old_msgs = non_system[:-3]
        recent_msgs = non_system[-3:]

        conversation_text = ""
        for msg in old_msgs:
            role_label = {"user": "用户", "assistant": "助手", "tool": "工具结果"}.get(
                msg["role"], msg["role"]
            )
            content = msg.get("content", "")
            if len(content) > 300:
                content = content[:300] + "..."
            conversation_text += f"[{role_label}]: {content}\n"

        summary_prompt = [
            {"role": "system", "content": (
                "你是一个对话摘要助手。请将以下完整对话历史压缩为结构化摘要。"
                "必须保留：1) 用户需求原文 2) 已完成的每个操作及结果 3) 当前状态 4) 未解决的问题。"
                "摘要应让全新的对话能完整理解上下文。直接输出摘要。"
            )},
            {"role": "user", "content": f"请摘要以下对话历史：\n\n{conversation_text[:8000]}"},
        ]

        try:
            summary = (await llm.chat_full(summary_prompt))["content"]
            if summary and len(summary.strip()) > 10:
                result = list(system_msgs)
                result.append({
                    "role": "user",
                    "content": f"[完整历史摘要]\n{summary.strip()}\n[摘要结束]",
                })
                result.extend(recent_msgs)
                result = self._fix_tool_pairing(result)
                logger.info(
                    "Full compact: %d messages → LLM summary (%d chars) + %d recent",
                    len(old_msgs), len(summary), len(recent_msgs),
                )
                return result
        except Exception as e:
            logger.warning("Full compact LLM failed: %s", e)

        return messages


# ── Git context injection ──

def build_git_context(workspace_path: str) -> str:
    """Generate a git context block for the system prompt.

    Includes current branch, recent commits, and uncommitted changes.
    Returns empty string if not a git repository or git is unavailable.
    """
    try:
        # hidden_window_kwargs：无控制台父进程下 git 闪窗（2026-10-28）
        # Current branch
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=workspace_path, timeout=5,
            **hidden_window_kwargs(),
        ).stdout.strip()

        # Recent 5 commits
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=workspace_path, timeout=5,
            **hidden_window_kwargs(),
        ).stdout.strip()

        # Uncommitted changes summary
        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=workspace_path, timeout=5,
            **hidden_window_kwargs(),
        ).stdout.strip()

        if not branch and not log:
            return ""  # Not a git repository

        parts = [f"📂 Git: 分支 `{branch}`"]
        if log:
            parts.append(f"最近提交:\n{log}")
        if status:
            parts.append(f"未提交变更:\n{status}")
        return "\n".join(parts)
    except Exception:
        return ""


# ── Project context auto-detection ──

def detect_project_context(workspace_path: str) -> str:
    """Auto-detect project tech stack from marker files and dependencies.

    Returns a multi-line hint string with framework-specific guidance,
    or empty if nothing detected.
    """
    import json as _json

    hints: list[str] = []
    frameworks: list[str] = []

    # ── Python ──
    is_python = False
    if os.path.isfile(os.path.join(workspace_path, "pyproject.toml")):
        hints.append("项目类型: Python (pyproject.toml)")
        is_python = True
        # Try to extract framework from pyproject.toml
        try:
            with open(os.path.join(workspace_path, "pyproject.toml"), "r", encoding="utf-8") as f:
                content = f.read()[:5000]  # Read first 5000 chars
            if "django" in content.lower():
                frameworks.append("Django")
            elif "flask" in content.lower():
                frameworks.append("Flask")
            elif "fastapi" in content.lower():
                frameworks.append("FastAPI")
        except Exception:
            pass
    elif os.path.isfile(os.path.join(workspace_path, "requirements.txt")):
        hints.append("项目类型: Python (requirements.txt)")
        is_python = True
        # Scan requirements.txt for known frameworks
        try:
            with open(os.path.join(workspace_path, "requirements.txt"), "r", encoding="utf-8") as f:
                req_content = f.read().lower()
            if "django" in req_content:
                frameworks.append("Django")
            elif "flask" in req_content:
                frameworks.append("Flask")
            elif "fastapi" in req_content:
                frameworks.append("FastAPI")
        except Exception:
            pass

    # ── Node.js ──
    pkg_json = os.path.join(workspace_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = _json.load(f)
            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))
            dep_names = list(all_deps.keys())
            hints.append(f"项目类型: Node.js | 主要依赖: {', '.join(dep_names[:10])}")

            # Detect frontend frameworks
            if "react" in all_deps or "react-dom" in all_deps:
                frameworks.append("React")
            if "next" in all_deps:
                frameworks.append("Next.js")
            if "vue" in all_deps:
                frameworks.append("Vue")
            if "nuxt" in all_deps or "nuxt3" in all_deps:
                frameworks.append("Nuxt.js")
            if "@angular/core" in all_deps:
                frameworks.append("Angular")
            if "svelte" in all_deps:
                frameworks.append("Svelte")

            # Detect build tools
            if "vite" in all_deps:
                frameworks.append("Vite")
            if "webpack" in all_deps:
                frameworks.append("Webpack")

            # Detect CSS frameworks
            if "tailwindcss" in all_deps:
                frameworks.append("Tailwind CSS")

            # Detect state management
            if "@reduxjs/toolkit" in all_deps or "react-redux" in all_deps:
                frameworks.append("Redux")
            if "zustand" in all_deps:
                frameworks.append("Zustand")
            if "pinia" in all_deps:
                frameworks.append("Pinia")

            # Detect testing
            if "vitest" in all_deps:
                frameworks.append("Vitest")
            if "jest" in all_deps:
                frameworks.append("Jest")
            if "@testing-library/react" in all_deps:
                frameworks.append("Testing Library")

            # Detect Electron
            if "electron" in all_deps:
                frameworks.append("Electron")

        except Exception:
            hints.append("项目类型: Node.js")

    # ── Other languages ──
    for marker, framework in [
        ("Cargo.toml", "Rust"),
        ("go.mod", "Go"),
        ("pom.xml", "Java/Maven"),
        ("build.gradle", "Java/Gradle"),
        ("Gemfile", "Ruby"),
        ("composer.json", "PHP/Composer"),
        ("Package.swift", "Swift"),
    ]:
        if os.path.isfile(os.path.join(workspace_path, marker)):
            if framework not in frameworks:
                frameworks.append(framework)

    # ── Build framework-specific guidance ──
    if frameworks:
        hints.append(f"检测到框架: {', '.join(frameworks)}")
        guidance = _get_framework_guidance(frameworks)
        if guidance:
            hints.append(guidance)

    return "\n".join(hints) if hints else ""


def _get_framework_guidance(frameworks: list[str]) -> str:
    """Return framework-specific coding guidance for the system prompt."""
    guidance_parts: list[str] = []

    if "React" in frameworks or "Next.js" in frameworks:
        guidance_parts.append(
            "React 规范: 使用函数组件 + Hooks；状态管理优先 useState/useReducer；"
            "组件文件用 PascalCase；样式用 CSS Modules 或 Tailwind"
        )
    if "Vue" in frameworks or "Nuxt.js" in frameworks:
        guidance_parts.append(
            "Vue 规范: 使用 Composition API (<script setup>)；"
            "响应式用 ref/reactive；组件文件用 PascalCase"
        )
    if "Django" in frameworks:
        guidance_parts.append(
            "Django 规范: 使用 ORM 进行数据库操作；遵循 MTV 模式；"
            "迁移用 makemigrations/migrate；表单用 Django Forms"
        )
    if "Flask" in frameworks:
        guidance_parts.append(
            "Flask 规范: 使用蓝图组织代码；数据库用 SQLAlchemy；"
            "配置用 config 对象；错误处理用 @app.errorhandler"
        )
    if "FastAPI" in frameworks:
        guidance_parts.append(
            "FastAPI 规范: 使用 Pydantic 模型做数据验证；"
            "异步路由用 async def；依赖注入用 Depends()"
        )
    if "Electron" in frameworks:
        guidance_parts.append(
            "Electron 规范: 主进程 (main.js) 与渲染进程分离；"
            "IPC 用 ipcMain/ipcRenderer；安全: 启用 contextIsolation"
        )
    if "Tailwind CSS" in frameworks:
        guidance_parts.append("样式: 使用 Tailwind CSS utility-first 类名")
    if "Vite" in frameworks:
        guidance_parts.append("构建: Vite — 开发用 vite，构建用 vite build")

    if guidance_parts:
        return "💡 " + " | ".join(guidance_parts)
    return ""
