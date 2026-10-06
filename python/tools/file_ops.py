import ast
import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import subprocess
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from tools.base import BaseTool

logger = logging.getLogger(__name__)


class FileOpsTool(BaseTool):
    name = "file_ops"
    description = (
        "在任务工作区中进行文件操作。支持以下操作：\n"
        "- read: 读取文件内容\n"
        "- write: 写入/覆盖文件内容\n"
        "- create: 创建新文件（如已存在则报错）\n"
        "- list: 列出目录内容\n"
        "- delete: 删除文件\n"
        "- search_replace: 精确替换文件中的文本块（支持 5 级模糊匹配）\n"
        "- apply_diff: 应用 SEARCH/REPLACE 格式的 diff（支持多处同时修改）\n"
        "- batch_edit: 原子性批量编辑多个文件\n"
        "- restore_backup: 从 .bak 备份恢复文件（编辑失败时自动创建备份）\n"
        "所有路径操作限制在工作区目录内。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "create", "list", "delete", "search_replace", "apply_diff", "batch_edit", "restore_backup"],
                "description": "操作类型: read/write/create/list/delete/search_replace/apply_diff/batch_edit/restore_backup",
            },
            "path": {
                "type": "string",
                "description": "文件或目录的相对路径（相对于工作区根目录）",
            },
            "content": {
                "type": "string",
                "description": "文件内容（write/create 操作）、替换文本（search_replace）或 diff 块（apply_diff）",
            },
            "old_content": {
                "type": "string",
                "description": "要查找并替换的原始文本（仅 search_replace 操作需要）",
            },
            "edits": {
                "type": "string",
                "description": "batch_edit 操作: JSON 数组，每个元素包含 {path, action, content, old_content?}",
            },
        },
        "required": ["action", "path"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## file_ops\n"
            "文件操作：read / write / create / list / delete / search_replace / apply_diff。\n"
            "所有路径相对于工作区。\n\n"
            "用法:\n"
            "- 先用 list 查看工作区有哪些文件\n"
            "- 用 read 读取文件内容\n"
            "- 用 write 创建或覆盖文件（自动创建父目录）。path 必须包含文件名，不能为空或目录\n"
            "- 用 create 创建新文件（已存在则报错，更安全）\n"
            "- 用 search_replace 精确替换文件中的文本块（推荐用于修改已有代码）\n"
            "- 用 apply_diff 应用 SEARCH/REPLACE 格式的 diff（适合多处同时修改）\n"
            "- 用 batch_edit 原子性批量编辑多个文件（全部成功或全部回滚）\n"
            "- 用 restore_backup 从备份恢复（write/search_replace 会自动创建 .bak 备份）\n"
            "- .py 文件编辑后自动语法检查，失败时自动回滚\n"
            "- read 超过 100KB 的文件会被截断\n\n"
            "search_replace 说明:\n"
            "- old_content: 要查找的原始文本（必须在文件中存在）\n"
            "- content: 替换后的新文本\n"
            "- 支持 5 级模糊匹配: 精确 → 忽略空行 → 忽略缩进 → 行级模糊(≥0.9) → 宽松模糊(≥0.8)\n"
            "- 替换失败时返回错误，不会修改文件\n\n"
            "apply_diff 说明:\n"
            "- content: SEARCH/REPLACE 格式的 diff 块\n"
            "- 支持多处同时修改，每处用 SEARCH/REPLACE 块包裹\n"
            "- 格式:\n"
            "  <<<<<<< SEARCH\n"
            "  原始代码\n"
            "  =======\n"
            "  新代码\n"
            "  >>>>>>> REPLACE\n\n"
            "batch_edit 说明:\n"
            "- edits: JSON 数组，每个元素包含 {path, action, content, old_content?}\n"
            "- action 支持 'apply_diff' 或 'search_replace'\n"
            "- 原子性执行: 全部成功或全部回滚\n"
            "- 示例: [{\"path\": \"a.py\", \"action\": \"apply_diff\", \"content\": \"...\"}, ...]\n\n"
            "示例:\n"
            '```\n{"action": "list", "path": "."}\n'
            '```\n\n{"action": "write", "path": "main.py", '
            '"content": "print(\'hello\')"}\n'
            '```\n\n{"action": "search_replace", "path": "main.py", '
            '"old_content": "def old():\\n    return 42", '
            '"content": "def new():\\n    return 100"}\n'
            "```\n\n"
            '```\n{"action": "apply_diff", "path": "main.py", '
            '"content": "<<<<<<< SEARCH\\ndef old():\\n    return 42\\n=======\\ndef new():\\n    return 100\\n>>>>>>> REPLACE"}\n'
            "```\n\n"
            "注意: 路径不能包含 .. 或超出工作区范围。"
        )

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir
        self.max_workspace_bytes: int = 0  # 0 = unlimited
        self._cached_size: int = -1  # -1 = not yet calculated
        # Read-only roots outside the workspace (installed Agent Skill
        # directories): readable via read/list only — the Agent Skills L3
        # progressive-disclosure path (references/) needs them.
        self._readonly_roots: list[str] = []

    def add_readonly_root(self, path: str):
        """Allow read-only access (read/list) to a directory outside the workspace.

        Write-style actions never resolve against these roots.
        """
        if path and path not in self._readonly_roots:
            self._readonly_roots.append(path)

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir
        self._cached_size = -1  # reset cache on workspace change

    def _create_backup(self, file_path: Path) -> Path | None:
        """Create a .bak backup before write/search_replace. Returns backup path or None."""
        bak_path = file_path.with_suffix(file_path.suffix + ".bak")
        try:
            if file_path.exists():
                shutil.copy2(str(file_path), str(bak_path))
                return bak_path
        except Exception:
            pass
        return None

    @staticmethod
    def _generate_unified_diff(old_text: str, new_text: str, path: str, n: int = 3) -> str:
        """Generate unified diff between old and new text.

        Returns a compact diff string suitable for frontend display.
        Limited to 2000 chars to avoid bloating SSE events.
        """
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff_lines = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path}", tofile=f"b/{path}",
            n=n, lineterm="",
        )
        diff_text = "".join(diff_lines)
        if len(diff_text) > 2000:
            diff_text = diff_text[:2000] + "\n... [diff truncated]"
        return diff_text

    @staticmethod
    def _check_python_syntax(content: str) -> str | None:
        """Check Python syntax. Returns error message or None if valid."""
        try:
            ast.parse(content)
            return None
        except SyntaxError as e:
            return f"SyntaxError: {e.msg} (line {e.lineno})"

    def _get_workspace_size(self) -> int:
        """Calculate total size of workspace directory in bytes.
        
        Uses a cache that is populated on first call and updated
        incrementally on write/delete operations.
        """
        if self._cached_size >= 0:
            return self._cached_size
        total = 0
        workspace = Path(self.workspace_dir)
        if not workspace.is_dir():
            self._cached_size = 0
            return 0
        for f in workspace.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        self._cached_size = total
        return total

    def _check_write_quota(self, content_size: int = 0) -> str | None:
        """Return error message if writing would exceed workspace quota, else None."""
        if self.max_workspace_bytes <= 0:
            return None
        current = self._get_workspace_size()
        if current + content_size > self.max_workspace_bytes:
            limit_mb = self.max_workspace_bytes // (1024 * 1024)
            current_mb = current // (1024 * 1024)
            return (
                f"工作区空间不足: 当前 {current_mb}MB / 上限 {limit_mb}MB。"
                f"请删除不需要的文件或调整设置中的 task_workspace_max_mb。"
            )
        return None

    def _resolve_path(self, rel_path: str, readonly: bool = False) -> Path | None:
        """Resolve a path within workspace (or read-only roots), preventing traversal.

        With ``readonly=True`` (read/list actions only), paths inside
        registered read-only roots — installed Agent Skill directories —
        also resolve, enabling on-demand access to references/ (L3).
        Write-style actions must keep ``readonly=False``.
        """
        candidates: list[tuple[Path, Path]] = []
        if self.workspace_dir:
            workspace = Path(self.workspace_dir).resolve()
            candidates.append((workspace, (workspace / rel_path).resolve()))
        if readonly:
            for root in self._readonly_roots:
                base = Path(root).resolve()
                # An absolute rel_path replaces the base (pathlib semantics),
                # which is exactly what we want for skill reference paths.
                candidates.append((base, (base / rel_path).resolve()))
        for base, target in candidates:
            # Ensure the resolved path is within its base (os.sep prevents prefix bypass)
            if target != base and not str(target).startswith(str(base) + os.sep):
                continue
            return target
        return None

    @staticmethod
    def _fuzzy_replace(file_text: str, old: str, new: str) -> tuple[str, str | None, int]:
        """Try to find and replace `old` in `file_text` using 5-level fuzzy matching.

        Returns (result_text, error_message, match_level).
        - On success: (new_text, None, level) where level is 1-5
        - On failure: (original_text, error_string, 0)
        """
        # Level 1: exact match
        if old in file_text:
            count = file_text.count(old)
            if count > 1:
                return file_text, f"匹配到 {count} 处，不唯一。请提供更多上下文。", 0
            return file_text.replace(old, new, 1), None, 1

        # Level 2: ignore blank lines
        def strip_blanks(s: str) -> list[str]:
            return [line for line in s.split("\n") if line.strip()]

        file_nonblank = strip_blanks(file_text)
        old_nonblank = strip_blanks(old)
        old_nb_joined = "\n".join(l.strip() for l in old_nonblank)
        file_nb_joined = "\n".join(l.strip() for l in file_nonblank)

        if old_nonblank and old_nb_joined in file_nb_joined:
            # Map each non-blank line in file_text to its actual line index
            file_lines = file_text.split("\n")
            nonblank_indices = [i for i, l in enumerate(file_lines) if l.strip()]
            n_old = len(old_nonblank)
            # Sliding window over non-blank lines
            for wi in range(len(nonblank_indices) - n_old + 1):
                match = True
                matched = []
                for oi in range(n_old):
                    fi = nonblank_indices[wi + oi]
                    if file_lines[fi].strip() != old_nonblank[oi].strip():
                        match = False
                        break
                    matched.append(fi)
                if match and len(matched) == n_old:
                    # Replace from first matched line to last matched line (inclusive)
                    start = matched[0]
                    end = matched[-1] + 1
                    new_lines = file_lines[:start] + new.split("\n") + file_lines[end:]
                    return "\n".join(new_lines), None, 2
            return file_text, "模糊匹配(Level 2)定位失败", 0

        # Level 3: ignore indentation
        def strip_indent(s: str) -> str:
            return "\n".join(line.strip() for line in s.split("\n") if line.strip())

        file_noindent = strip_indent(file_text)
        old_noindent = strip_indent(old)
        if old_noindent and old_noindent in file_noindent:
            # We know the content matches ignoring indent — do line-by-line replacement
            old_lines_stripped = [l.strip() for l in old.split("\n") if l.strip()]
            file_lines = file_text.split("\n")
            n_old = len(old_lines_stripped)
            for i in range(len(file_lines)):
                match = True
                matched_indices = []
                fi = i
                for oi in range(n_old):
                    while fi < len(file_lines) and not file_lines[fi].strip():
                        fi += 1
                    if fi >= len(file_lines):
                        match = False
                        break
                    if file_lines[fi].strip() != old_lines_stripped[oi]:
                        match = False
                        break
                    matched_indices.append(fi)
                    fi += 1
                if match and len(matched_indices) == n_old:
                    start = matched_indices[0]
                    end = matched_indices[-1] + 1
                    new_lines = file_lines[:start] + new.split("\n") + file_lines[end:]
                    return "\n".join(new_lines), None, 3
            return file_text, "模糊匹配(Level 3)定位失败", 0

        # ── Level 4-5: line-level fuzzy matching ──
        old_lines = [l for l in old.split("\n") if l.strip()]
        if not old_lines:
            return file_text, "未找到匹配的文本。请检查 old_content 是否正确。", 0

        file_lines = file_text.split("\n")
        n_old = len(old_lines)

        best_ratio = 0.0
        best_start = -1
        best_end = -1

        # Try windows of size n_old to n_old+2
        for window in range(n_old, min(len(file_lines) + 1, n_old + 3)):
            for i in range(len(file_lines) - window + 1):
                candidate = file_lines[i:i + window]
                # Line-level similarity: average per-line SequenceMatcher ratio
                total = 0.0
                for j in range(n_old):
                    cj = j if j < len(candidate) else len(candidate) - 1
                    total += SequenceMatcher(
                        None, old_lines[j].strip(), candidate[cj].strip()
                    ).ratio()
                ratio = total / n_old
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = i
                    best_end = i + window
                    if best_ratio >= 1.0:
                        break
            if best_ratio >= 1.0:
                break

        # Level 4: ratio >= 0.9
        if best_ratio >= 0.9 and best_start >= 0:
            new_lines = (
                file_lines[:best_start]
                + new.split("\n")
                + file_lines[best_end:]
            )
            return (
                "\n".join(new_lines), None, 4,
            )

        # Level 5: ratio >= 0.8
        if best_ratio >= 0.8 and best_start >= 0:
            new_lines = (
                file_lines[:best_start]
                + new.split("\n")
                + file_lines[best_end:]
            )
            return (
                "\n".join(new_lines), None, 5,
            )

        return file_text, "未找到匹配的文本。请检查 old_content 是否正确。", 0

    @staticmethod
    def _parse_diff_blocks(diff_content: str) -> list[tuple[str, str]]:
        """Parse SEARCH/REPLACE diff blocks from content.

        Format:
            <<<<<<< SEARCH
            original code
            =======
            new code
            >>>>>>> REPLACE

        Returns list of (search, replace) tuples.
        """
        blocks = []
        pattern = re.compile(
            r'<<<<<<<\s*SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>>\s*REPLACE',
            re.DOTALL
        )
        for match in pattern.finditer(diff_content):
            search_text = match.group(1)
            replace_text = match.group(2)
            blocks.append((search_text, replace_text))
        return blocks

    def _apply_diff(self, file_text: str, diff_content: str) -> tuple[str, str | None, int]:
        """Apply SEARCH/REPLACE diff blocks to file content.

        Returns (result_text, error_message, blocks_applied).
        """
        blocks = self._parse_diff_blocks(diff_content)
        if not blocks:
            return file_text, "未找到有效的 SEARCH/REPLACE 块。请检查格式是否正确。", 0

        result = file_text
        applied = 0
        for i, (search_text, replace_text) in enumerate(blocks):
            # Try exact match first
            if search_text in result:
                count = result.count(search_text)
                if count > 1:
                    return file_text, f"第 {i+1} 块匹配到 {count} 处，不唯一。请提供更多上下文。", 0
                result = result.replace(search_text, replace_text, 1)
                applied += 1
            else:
                # Try fuzzy match using existing _fuzzy_replace
                new_result, error, level = self._fuzzy_replace(result, search_text, replace_text)
                if error:
                    return file_text, f"第 {i+1} 块应用失败: {error}", 0
                result = new_result
                applied += 1

        return result, None, applied

    async def _batch_edit(self, edits_json: str) -> str:
        """Atomic batch edit of multiple files.

        Accepts a JSON array of edits: [{path, action, content, old_content?}]
        All edits succeed or all rollback (atomic).
        Supports action='apply_diff' or action='search_replace'.
        """
        if not edits_json:
            return json.dumps({"error": "batch_edit 需要 edits 参数 (JSON 数组)"}, ensure_ascii=False)

        try:
            edits_list = json.loads(edits_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"edits JSON 解析失败: {e}"}, ensure_ascii=False)

        if not isinstance(edits_list, list) or not edits_list:
            return json.dumps({"error": "edits 必须是非空 JSON 数组"}, ensure_ascii=False)

        workspace = Path(self.workspace_dir).resolve()

        # Phase 1: Read all files and compute results (dry run)
        file_backups: dict[str, str] = {}  # path -> original content
        file_results: dict[str, str] = {}  # path -> new content
        file_edit_types: dict[str, str] = {}  # path -> edit action type
        errors: list[str] = []

        for i, edit in enumerate(edits_list):
            rel_path = edit.get("path", "")
            edit_action = edit.get("action", "apply_diff")
            edit_content = edit.get("content", "")
            edit_old_content = edit.get("old_content", "")

            if not rel_path:
                errors.append(f"第 {i+1} 个编辑缺少 path")
                continue

            target = self._resolve_path(rel_path)
            if target is None:
                errors.append(f"第 {i+1} 个编辑路径无效: {rel_path}")
                continue

            if not target.is_file():
                errors.append(f"第 {i+1} 个编辑文件不存在: {rel_path}")
                continue

            try:
                file_text = target.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                errors.append(f"第 {i+1} 个编辑读取失败: {e}")
                continue

            # Save original content for backup
            if rel_path not in file_backups:
                file_backups[rel_path] = file_text

            # Apply the edit
            if edit_action == "apply_diff":
                result_text, error, blocks = self._apply_diff(file_text, edit_content)
                if error:
                    errors.append(f"第 {i+1} 个编辑 ({rel_path}) 失败: {error}")
                    continue
                file_results[rel_path] = result_text
                file_edit_types[rel_path] = "apply_diff"
            elif edit_action == "search_replace":
                result_text, error, level = self._fuzzy_replace(file_text, edit_old_content, edit_content)
                if error:
                    errors.append(f"第 {i+1} 个编辑 ({rel_path}) 失败: {error}")
                    continue
                file_results[rel_path] = result_text
                file_edit_types[rel_path] = "search_replace"
            else:
                errors.append(f"第 {i+1} 个编辑不支持的操作: {edit_action}")
                continue

        # If any errors, abort entire batch
        if errors:
            return json.dumps({
                "error": "批量编辑失败，未修改任何文件",
                "details": errors,
            }, ensure_ascii=False)

        # Phase 2: Create backups and apply all changes (atomic)
        backup_paths: list[Path] = []
        applied_files: list[str] = []

        try:
            for rel_path, new_content in file_results.items():
                target = self._resolve_path(rel_path)
                if target is None:
                    raise RuntimeError(f"路径解析失败: {rel_path}")

                # Create backup
                bak = self._create_backup(target)
                if bak:
                    backup_paths.append(bak)

                # Syntax check for .py files
                if target.suffix == ".py" or rel_path.endswith(".py"):
                    syntax_err = self._check_python_syntax(new_content)
                    if syntax_err:
                        # Rollback all already writes
                        for written_path in applied_files:
                            written_target = self._resolve_path(written_path)
                            if written_target and written_path in file_backups:
                                written_target.write_text(
                                    file_backups[written_path], encoding="utf-8"
                                )
                        return json.dumps({
                            "error": f"批量编辑回滚: {rel_path} 语法错误 — {syntax_err}",
                        }, ensure_ascii=False)

                # Write the new content
                target.write_text(new_content, encoding="utf-8")
                applied_files.append(rel_path)

            logger.info("batch_edit: applied %d file edits", len(applied_files))
            return json.dumps({
                "ok": True,
                "files_edited": len(applied_files),
                "files": [
                    {"path": p, "action": file_edit_types[p]}
                    for p in applied_files
                ],
            }, ensure_ascii=False)

        except Exception as e:
            # Emergency rollback
            for written_path in applied_files:
                try:
                    written_target = self._resolve_path(written_path)
                    if written_target and written_path in file_backups:
                        written_target.write_text(
                            file_backups[written_path], encoding="utf-8"
                        )
                except Exception:
                    pass
            return json.dumps({
                "error": f"批量编辑异常，已尝试回滚: {str(e)}",
            }, ensure_ascii=False)

    async def execute(self, action: str, path: str = "", content: str = "", old_content: str = "", edits: str = "") -> str:
        if not self.workspace_dir:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        # ── batch_edit: atomic multi-file editing ──
        if action == "batch_edit":
            return await self._batch_edit(edits)

        target = self._resolve_path(path)
        if target is None and action in ("read", "list"):
            # Read-only actions may also reach registered roots outside the
            # workspace sandbox (Agent Skill references/, 1.1 §4.4)
            target = self._resolve_path(path, readonly=True)
        if target is None:
            return json.dumps({"error": "路径无效或超出工作区范围"}, ensure_ascii=False)

        try:
            if action == "read":
                if not target.is_file():
                    return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
                text = target.read_text(encoding="utf-8", errors="replace")
                if len(text) > 100000:
                    text = text[:100000] + "\n... [内容过长，已截断] ..."
                return json.dumps({"content": text, "path": path}, ensure_ascii=False)

            elif action == "write":
                if target.is_dir():
                    return json.dumps(
                        {"error": f"路径 '{path}' 是一个目录，不是文件。请在 path 中指定文件名（如 'report.md'）"},
                        ensure_ascii=False,
                    )
                if not content:
                    logger.warning(
                        "file_ops write called with EMPTY content for path=%s",
                        path,
                    )
                    return json.dumps({"error": "write 操作需要提供 content 参数（文件内容不能为空）"}, ensure_ascii=False)
                logger.debug(
                    "file_ops write path=%s content_len=%d",
                    path, len(content),
                )
                quota_err = self._check_write_quota(len(content.encode("utf-8")))
                if quota_err:
                    return json.dumps({"error": quota_err}, ensure_ascii=False)
                # Read old content for diff generation (before overwrite)
                old_text = ""
                if target.exists():
                    try:
                        old_text = target.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass
                # Auto-backup before overwriting
                self._create_backup(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                # Syntax check for .py files
                if target.suffix == ".py" or path.endswith(".py"):
                    syntax_err = self._check_python_syntax(content)
                    if syntax_err:
                        return json.dumps(
                            {"error": f"Python 语法错误，已拒绝写入: {syntax_err}", "path": path},
                            ensure_ascii=False,
                        )
                target.write_text(content, encoding="utf-8")
                self._cached_size += len(content.encode("utf-8"))
                resp_w: dict = {"ok": True, "path": path, "action": "write"}
                if old_text:
                    resp_w["diff"] = self._generate_unified_diff(old_text, content, path)
                return json.dumps(resp_w, ensure_ascii=False)

            elif action == "create":
                if target.is_dir():
                    return json.dumps(
                        {"error": f"路径 '{path}' 已存在且是一个目录。请指定一个文件名"},
                        ensure_ascii=False,
                    )
                if target.exists():
                    return json.dumps({"error": f"文件已存在: {path}"}, ensure_ascii=False)
                if not content:
                    logger.warning(
                        "file_ops create called with EMPTY content for path=%s",
                        path,
                    )
                    return json.dumps({"error": "create 操作需要提供 content 参数（文件内容不能为空）"}, ensure_ascii=False)
                logger.debug(
                    "file_ops create path=%s content_len=%d",
                    path, len(content),
                )
                quota_err = self._check_write_quota(len(content.encode("utf-8")))
                if quota_err:
                    return json.dumps({"error": quota_err}, ensure_ascii=False)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                self._cached_size += len(content.encode("utf-8"))
                return json.dumps({"ok": True, "path": path, "action": "create"}, ensure_ascii=False)

            elif action == "list":
                if not target.is_dir():
                    return json.dumps({"error": f"目录不存在: {path}"}, ensure_ascii=False)
                entries = []
                for item in sorted(target.iterdir()):
                    if item.name.startswith("."):
                        continue
                    entry_type = "dir" if item.is_dir() else "file"
                    size = item.stat().st_size if item.is_file() else 0
                    entries.append({"name": item.name, "type": entry_type, "size": size})
                return json.dumps({"entries": entries, "path": path}, ensure_ascii=False)

            elif action == "delete":
                if not target.exists():
                    return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)
                # Prevent deleting the workspace root itself
                workspace = Path(self.workspace_dir).resolve()
                if target == workspace:
                    return json.dumps({"error": "不能删除工作区根目录"}, ensure_ascii=False)
                if target.is_file():
                    file_size = target.stat().st_size
                    target.unlink()
                    self._cached_size = max(0, self._cached_size - file_size)
                else:
                    dir_size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
                    shutil.rmtree(target)
                    self._cached_size = max(0, self._cached_size - dir_size)
                return json.dumps({"ok": True, "path": path, "action": "delete"}, ensure_ascii=False)

            elif action == "search_replace":
                if not target.is_file():
                    return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
                if not old_content:
                    return json.dumps({"error": "search_replace 需要 old_content 参数"}, ensure_ascii=False)
                file_text = target.read_text(encoding="utf-8", errors="replace")
                result_text, error, level = self._fuzzy_replace(file_text, old_content, content)
                if error:
                    return json.dumps({"error": error, "path": path}, ensure_ascii=False)
                # Auto-backup before writing
                self._create_backup(target)
                # Syntax check for .py files — auto-rollback on failure
                if target.suffix == ".py" or path.endswith(".py"):
                    syntax_err = self._check_python_syntax(result_text)
                    if syntax_err:
                        bak = target.with_suffix(target.suffix + ".bak")
                        if bak.exists():
                            shutil.copy2(str(bak), str(target))
                        return json.dumps(
                            {"error": f"替换后 Python 语法错误，已自动回滚: {syntax_err}", "path": path},
                            ensure_ascii=False,
                        )
                target.write_text(result_text, encoding="utf-8")
                # Update cache: delta between new and old content
                old_size = len(file_text.encode("utf-8"))
                new_size = len(result_text.encode("utf-8"))
                self._cached_size = max(0, self._cached_size + (new_size - old_size))
                resp: dict = {"ok": True, "path": path, "action": "search_replace"}
                resp["diff"] = self._generate_unified_diff(file_text, result_text, path)
                if level >= 4:
                    resp["warning"] = (
                        f"通过 Level {level} 模糊匹配成功，请确认替换内容正确"
                    )
                return json.dumps(resp, ensure_ascii=False)

            elif action == "apply_diff":
                if not target.is_file():
                    return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
                if not content:
                    return json.dumps({"error": "apply_diff 需要 content 参数（SEARCH/REPLACE 格式的 diff）"}, ensure_ascii=False)
                file_text = target.read_text(encoding="utf-8", errors="replace")
                result_text, error, blocks_applied = self._apply_diff(file_text, content)
                if error:
                    return json.dumps({"error": error, "path": path}, ensure_ascii=False)
                # Auto-backup before writing
                self._create_backup(target)
                # Syntax check for .py files — auto-rollback on failure
                if target.suffix == ".py" or path.endswith(".py"):
                    syntax_err = self._check_python_syntax(result_text)
                    if syntax_err:
                        bak = target.with_suffix(target.suffix + ".bak")
                        if bak.exists():
                            shutil.copy2(str(bak), str(target))
                        return json.dumps(
                            {"error": f"应用 diff 后 Python 语法错误，已自动回滚: {syntax_err}", "path": path},
                            ensure_ascii=False,
                        )
                target.write_text(result_text, encoding="utf-8")
                # Update cache
                old_size = len(file_text.encode("utf-8"))
                new_size = len(result_text.encode("utf-8"))
                self._cached_size = max(0, self._cached_size + (new_size - old_size))
                return json.dumps({
                    "ok": True,
                    "path": path,
                    "action": "apply_diff",
                    "blocks_applied": blocks_applied,
                    "diff": self._generate_unified_diff(file_text, result_text, path),
                }, ensure_ascii=False)

            elif action == "restore_backup":
                if not target.is_file():
                    # Try with .bak suffix
                    bak = target.with_suffix(target.suffix + ".bak") if target.suffix else Path(str(target) + ".bak")
                    if not bak.is_file():
                        return json.dumps({"error": f"备份文件不存在: {path}"}, ensure_ascii=False)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(bak), str(target))
                    return json.dumps({"ok": True, "path": path, "action": "restore_backup"}, ensure_ascii=False)
                bak = target.with_suffix(target.suffix + ".bak")
                if not bak.is_file():
                    return json.dumps({"error": f"备份文件不存在: {path}.bak"}, ensure_ascii=False)
                shutil.copy2(str(bak), str(target))
                return json.dumps({"ok": True, "path": path, "action": "restore_backup"}, ensure_ascii=False)

            else:
                return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def post_execute(self, result: str, **kwargs: Any) -> str:
        """After write/search_replace/apply_diff, run syntax/lint checks.

        Returns the result with optional `lint_warning` or `fix_suggestion` fields.
        For .py files: AST syntax check (already done during write).
        For .js/.mjs/.cjs: node --check.
        For .ts/.tsx: hint to use code_lint.
        """
        action = kwargs.get("action", "")
        path = kwargs.get("path", "")
        if action not in ("write", "search_replace", "apply_diff"):
            return result

        # Only validate on successful operations
        try:
            result_obj = json.loads(result)
            if not result_obj.get("ok"):
                return result
        except (json.JSONDecodeError, TypeError):
            return result

        # Python: already checked during write, but add lint suggestion
        if path.endswith(".py"):
            target = self._resolve_path(path)
            if target and target.is_file():
                try:
                    # Worker thread — keep the event loop responsive (H1)
                    r = await asyncio.to_thread(
                        subprocess.run,
                        ["python", "-m", "py_compile", str(target)],
                        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
                    )
                    if r.returncode != 0:
                        err_msg = r.stderr.strip() or "Syntax error"
                        result_obj["lint_warning"] = f"Python 编译错误: {err_msg}"
                        result_obj["fix_suggestion"] = "请检查语法错误并修复"
                        logger.warning("post_execute Python lint error: %s", err_msg)
                        return json.dumps(result_obj, ensure_ascii=False)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass  # python not available

        # JavaScript: node --check
        if path.endswith((".js", ".mjs", ".cjs")):
            target = self._resolve_path(path)
            if target and target.is_file():
                try:
                    # Worker thread — keep the event loop responsive (H1)
                    r = await asyncio.to_thread(
                        subprocess.run,
                        ["node", "--check", str(target)],
                        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
                    )
                    if r.returncode != 0:
                        err_msg = r.stderr.strip() or "Syntax error"
                        result_obj["lint_warning"] = f"JavaScript 语法错误: {err_msg}"
                        result_obj["fix_suggestion"] = "请检查 JavaScript 语法并修复"
                        logger.warning("post_execute JS lint error: %s", err_msg)
                        return json.dumps(result_obj, ensure_ascii=False)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass  # node not available

        # TypeScript: hint to run tsc
        if path.endswith((".ts", ".tsx")):
            result_obj["hint"] = "建议用 code_lint 或 code_executor 验证 TypeScript 语法"
            return json.dumps(result_obj, ensure_ascii=False)

        return result
