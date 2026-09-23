"""Repo-map tool: extract project structure and code signatures.

Inspired by Aider's repo-map concept — provides the LLM with a compact
overview of the codebase structure (classes, functions, imports) so it can
navigate large projects efficiently without reading every file.

Uses tree-sitter for precise AST-based extraction when available,
with regex-based fallback for cross-platform compatibility.
Supports Python, JavaScript, TypeScript, Java, Go, Rust.

Phase 17 enhancements:
- PageRank-based symbol ranking (most-referenced symbols first)
- Configurable token budget (default 1024 tokens)
- Auto-injection support for system prompt
"""
import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path

from tools.base import BaseTool

logger = logging.getLogger(__name__)

# ── tree-sitter integration (optional) ──
_ts_available = False
try:
    import tree_sitter as _ts
    _ts_available = True
except ImportError:
    pass

# File extensions to scan
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp", ".cs",
}

# Directories to skip
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".next", ".nuxt", "target", "vendor",
}

# Max files to scan (prevent huge repos from blocking)
MAX_FILES = 200
# Max output size (chars)
MAX_OUTPUT = 15000
# Default token budget for auto-injection mode
DEFAULT_TOKEN_BUDGET = 1024
# PageRank parameters
_PR_DAMPING = 0.85
_PR_ITERATIONS = 20


class RepoMapTool(BaseTool):
    name = "repo_map"
    description = "生成工作区的代码结构地图（类、函数、导入），帮助快速理解项目架构。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对路径（相对于工作区），默认为 '.'（整个工作区）",
            },
            "depth": {
                "type": "integer",
                "description": "目录树深度限制（默认 3）",
                "default": 3,
            },
            "detail": {
                "type": "string",
                "description": "详细程度: 'brief'（仅文件列表+统计）或 'full'（含函数/类签名）",
                "enum": ["brief", "full"],
                "default": "full",
            },
            "token_budget": {
                "type": "integer",
                "description": "输出 token 预算（默认 0=不限制）。设置后会按 PageRank 优先级截断低重要性符号。",
                "default": 0,
            },
        },
        "required": [],
    }

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    @property
    def prompt_description(self) -> str:
        return (
            "## repo_map\n"
            "生成项目代码结构地图，展示目录结构、文件列表、类/函数签名。\n"
            "帮助快速理解大型项目的架构，无需逐个文件阅读。\n\n"
            "用法:\n"
            "- 首次接触项目时调用，获取全局视图\n"
            "- detail='brief' 仅显示文件列表和统计\n"
            "- detail='full' 显示每个文件的 class/def/function 签名\n\n"
            "示例:\n"
            '```\n{"detail": "brief"}\n```\n\n'
            '```\n{"path": "src", "depth": 2, "detail": "full"}\n```\n\n'
            "支持语言: Python, JavaScript, TypeScript, Java, Go, Rust 等"
        )

    async def execute(self, path: str = ".", depth: int = 3, detail: str = "full", token_budget: int = 0) -> str:
        if not self.workspace_dir:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        workspace = Path(self.workspace_dir).resolve()
        scan_path = (workspace / path).resolve()
        if scan_path != workspace and not str(scan_path).startswith(str(workspace) + os.sep):
            return json.dumps({"error": "路径超出工作区范围"}, ensure_ascii=False)
        if not scan_path.exists():
            return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)

        try:
            files = self._collect_files(scan_path, workspace, depth)
            if not files:
                return json.dumps({
                    "path": path,
                    "files": [],
                    "total_files": 0,
                    "message": "未找到代码文件",
                }, ensure_ascii=False)

            # Build PageRank scores for file ordering
            file_scores = self._build_pagerank(files, workspace)

            if detail == "brief":
                return self._build_brief(files, workspace, path, file_scores)
            return self._build_full(files, workspace, path, file_scores, token_budget=token_budget)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _collect_files(self, scan_path: Path, workspace: Path, max_depth: int) -> list[Path]:
        """Collect code files up to max_depth, skipping ignored dirs."""
        result = []
        base_depth = len(scan_path.parts)

        if scan_path.is_file():
            if scan_path.suffix in _CODE_EXTENSIONS:
                return [scan_path]
            return []

        for root, dirs, files in os.walk(scan_path):
            root_path = Path(root)
            current_depth = len(root_path.parts) - base_depth
            if current_depth >= max_depth:
                dirs.clear()
                continue
            # Filter out ignored dirs
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]

            for fname in sorted(files):
                if fname.startswith("."):
                    continue
                fpath = root_path / fname
                if fpath.suffix in _CODE_EXTENSIONS:
                    result.append(fpath)
                    if len(result) >= MAX_FILES:
                        return result
        return result

    # ── PageRank ──

    def _build_pagerank(self, files: list[Path], workspace: Path) -> dict[str, float]:
        """Build import dependency graph and compute PageRank scores for files.

        Files that are imported by more other files get higher scores.
        Returns a dict mapping relative file path → PageRank score.
        """
        if len(files) <= 1:
            return {}

        # Build set of known module names for resolution
        file_map: dict[str, Path] = {}  # module_name → file_path
        for f in files:
            rel = f.relative_to(workspace)
            # Python: foo/bar.py → foo.bar
            if f.suffix == ".py":
                mod = str(rel.with_suffix("")).replace(os.sep, ".")
                file_map[mod] = f
                # Also map __init__.py parent
                if f.name == "__init__.py":
                    parent_mod = str(rel.parent).replace(os.sep, ".")
                    file_map[parent_mod] = f
            # JS/TS: foo/bar.ts → foo/bar
            elif f.suffix in (".js", ".ts", ".tsx", ".jsx"):
                mod = str(rel.with_suffix("")).replace(os.sep, "/")
                file_map[mod] = f

        # Build adjacency: target → [sources that import it]
        in_edges: dict[str, list[str]] = defaultdict(list)
        all_rel_paths = set()

        for f in files:
            rel = str(f.relative_to(workspace))
            all_rel_paths.add(rel)
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                if len(text) > 50_000:
                    text = text[:50_000]
            except OSError:
                continue

            imports = self._extract_imports(text, f.suffix)
            for imp in imports:
                # Try to resolve import to a known file
                resolved = self._resolve_import_to_file(imp, file_map, f)
                if resolved and resolved != rel and resolved in all_rel_paths:
                    in_edges[resolved].append(rel)

        # Simple PageRank iteration
        n = len(all_rel_paths)
        if n == 0:
            return {}

        path_list = list(all_rel_paths)
        scores = {p: 1.0 / n for p in path_list}
        damping = _PR_DAMPING

        for _ in range(_PR_ITERATIONS):
            new_scores: dict[str, float] = {}
            for p in path_list:
                incoming = sum(scores.get(src, 0) for src in in_edges.get(p, []))
                new_scores[p] = (1 - damping) / n + damping * incoming
            # Normalize
            total = sum(new_scores.values()) or 1
            scores = {p: s / total for p, s in new_scores.items()}

        return scores

    def _resolve_import_to_file(self, import_path: str, file_map: dict[str, Path],
                                 source_file: Path) -> str | None:
        """Try to resolve an import path to a known file in the workspace.

        Returns relative path string or None.
        """
        # Direct match
        if import_path in file_map:
            return str(file_map[import_path].relative_to(Path(self.workspace_dir).resolve()))

        # Try parent modules (e.g. "foo.bar.baz" → try "foo.bar", "foo")
        parts = import_path.split(".")
        for i in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in file_map:
                return str(file_map[candidate].relative_to(Path(self.workspace_dir).resolve()))

        # Try relative imports (JS/TS style: "./foo", "../bar")
        if import_path.startswith("."):
            source_dir = source_file.parent
            # Convert dotted path to file path segments
            segments = import_path.lstrip(".").split(".")
            levels_up = len(import_path) - len(import_path.lstrip("."))
            base = source_dir
            for _ in range(levels_up - 1):
                base = base.parent
            for ext in [".py", ".js", ".ts", ".tsx", "/__init__.py", ".jsx"]:
                candidate = base / "/".join(segments)
                candidate_str = str(candidate) + ext
                try:
                    rel = str(Path(candidate_str).relative_to(Path(self.workspace_dir).resolve()))
                    # Check if this file exists in our file set
                    if (Path(self.workspace_dir).resolve() / rel).is_file():
                        return rel
                except (ValueError, OSError):
                    continue

        return None

    def _build_brief(self, files: list[Path], workspace: Path, path: str,
                     file_scores: dict[str, float] | None = None) -> str:
        """Brief mode: directory tree + file counts, sorted by PageRank."""
        # Sort files by PageRank score (higher = more important = first)
        if file_scores:
            files = sorted(files, key=lambda f: file_scores.get(str(f.relative_to(workspace)), 0), reverse=True)
        # Group by directory
        dir_counts: dict[str, int] = {}
        ext_counts: dict[str, int] = {}
        for f in files:
            rel = f.relative_to(workspace)
            parent = str(rel.parent)
            if parent == ".":
                parent = "(root)"
            dir_counts[parent] = dir_counts.get(parent, 0) + 1
            ext_counts[f.suffix] = ext_counts.get(f.suffix, 0) + 1

        lines = [f"# 项目结构 ({path})\n"]
        lines.append(f"总文件数: {len(files)}\n")

        # Directory breakdown
        lines.append("## 目录分布")
        for d in sorted(dir_counts.keys()):
            lines.append(f"  {d}/  ({dir_counts[d]} 个文件)")

        # Language breakdown
        lang_names = {
            ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
            ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
            ".java": "Java", ".go": "Go", ".rs": "Rust",
            ".rb": "Ruby", ".php": "PHP",
            ".c": "C", ".cpp": "C++", ".h": "C/C++ Header",
        }
        lines.append("\n## 语言分布")
        for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
            name = lang_names.get(ext, ext)
            lines.append(f"  {name}: {count} 个文件")

        # File tree (compact)
        lines.append("\n## 文件列表")
        for f in files[:100]:
            rel = str(f.relative_to(workspace))
            lines.append(f"  {rel}")
        if len(files) > 100:
            lines.append(f"  ... 及其他 {len(files) - 100} 个文件")

        output = "\n".join(lines)
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n\n[...truncated]"

        return json.dumps({
            "path": path,
            "total_files": len(files),
            "map": output,
        }, ensure_ascii=False)

    def _build_full(self, files: list[Path], workspace: Path, path: str,
                    file_scores: dict[str, float] | None = None,
                    token_budget: int = 0) -> str:
        """Full mode: directory tree + signatures for each file, sorted by PageRank.
        
        When token_budget > 0, output is truncated to fit within the budget,
        keeping higher-ranked files/signatures first.
        """
        # Sort files by PageRank score (higher = more important = first)
        if file_scores:
            files = sorted(files, key=lambda f: file_scores.get(str(f.relative_to(workspace)), 0), reverse=True)

        lines = [f"# 代码结构地图 ({path})\n"]
        lines.append(f"总文件数: {len(files)}\n")

        # Track token usage if budget is set
        budget_chars = token_budget * 4 if token_budget > 0 else 0  # ~4 chars per token
        current_chars = 0
        budget_exceeded = False

        current_dir = None
        for f in files:
            rel = f.relative_to(workspace)
            parent = str(rel.parent)
            if parent == ".":
                parent = "(root)"

            # Print directory header when it changes
            if parent != current_dir:
                current_dir = parent
                section = f"\n## {parent}/"
                if budget_chars > 0 and current_chars + len(section) > budget_chars:
                    budget_exceeded = True
                    break
                lines.append(section)
                current_chars += len(section)

            # Extract signatures
            try:
                sigs = self._extract_signatures(f)
                if sigs:
                    section = f"\n### {rel.name}"
                    if budget_chars > 0 and current_chars + len(section) > budget_chars:
                        budget_exceeded = True
                        break
                    lines.append(section)
                    current_chars += len(section)
                    for sig in sigs:
                        if budget_chars > 0 and current_chars + len(sig) > budget_chars:
                            budget_exceeded = True
                            break
                        lines.append(f"  {sig}")
                        current_chars += len(sig) + 2
                else:
                    section = f"\n### {rel.name}  (无签名提取)"
                    if budget_chars > 0 and current_chars + len(section) > budget_chars:
                        budget_exceeded = True
                        break
                    lines.append(section)
                    current_chars += len(section)
            except Exception:
                section = f"\n### {rel.name}  (读取失败)"
                if budget_chars > 0 and current_chars + len(section) > budget_chars:
                    budget_exceeded = True
                    break
                lines.append(section)
                current_chars += len(section)

        if budget_exceeded:
            lines.append(f"\n\n[...truncated: token budget {token_budget} reached, {len(files)} files total]")
        else:
            output = "\n".join(lines)
            if len(output) > MAX_OUTPUT:
                output = output[:MAX_OUTPUT] + "\n\n[...truncated, 使用 detail='brief' 获取精简视图]"
            lines = [output]

        output = "\n".join(lines)
        return json.dumps({
            "path": path,
            "total_files": len(files),
            "map": output,
            "pagerank_sorted": file_scores is not None and len(file_scores) > 0,
            "token_budget": token_budget if token_budget > 0 else None,
        }, ensure_ascii=False)

    def _extract_signatures(self, filepath: Path) -> list[str]:
        """Extract class/function/import signatures from a source file.

        Uses tree-sitter AST when available for precise extraction,
        falls back to regex-based extraction otherwise.
        """
        ext = filepath.suffix
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        # Limit file size to process
        if len(text) > 100_000:
            text = text[:100_000]

        # Try tree-sitter first for Python and JS/TS
        if _ts_available:
            ts_sigs = self._extract_with_treesitter(text, ext)
            if ts_sigs is not None:
                return ts_sigs

        # Regex fallback
        if ext == ".py":
            return self._extract_python(text)
        if ext in (".js", ".ts", ".tsx", ".jsx"):
            return self._extract_js_ts(text)
        if ext == ".go":
            return self._extract_go(text)
        if ext in (".java", ".kt"):
            return self._extract_java(text)
        return self._extract_generic(text)

    def _extract_with_treesitter(self, text: str, ext: str) -> list[str] | None:
        """Try tree-sitter extraction. Returns signatures or None if language not supported."""
        lang_module_map = {
            ".py": ("tree_sitter_python", "language"),
            ".js": ("tree_sitter_javascript", "language"),
            ".jsx": ("tree_sitter_javascript", "language"),
            ".ts": ("tree_sitter_typescript", "language_typescript"),
            ".tsx": ("tree_sitter_typescript", "language_tsx"),
        }
        if ext not in lang_module_map:
            return None

        mod_name, func_name = lang_module_map[ext]
        try:
            import importlib
            lang_mod = importlib.import_module(mod_name)
            lang_func = getattr(lang_mod, func_name)
            language = _ts.Language(lang_func())
            parser = _ts.Parser(language)
            tree = parser.parse(bytes(text, "utf8"))
            return self._walk_tree(tree.root_node, text)
        except Exception:
            return None  # Fall back to regex

    def _walk_tree(self, node, text: str) -> list[str]:
        """Walk tree-sitter AST to extract signatures."""
        sigs: list[str] = []
        # Node types that represent definitions
        _DEF_TYPES = {
            "function_definition", "class_definition",
            "decorated_definition",
            "import_statement", "import_from_statement",
            "export_statement",
            "lexical_declaration",  # const/let in JS/TS
            "function_declaration",
            "interface_declaration", "type_alias_declaration",
        }
        self._collect_nodes(node, text, sigs, _DEF_TYPES, depth=0, max_depth=500)
        return sigs[:60]  # cap at 60 signatures

    def _collect_nodes(self, node, text: str, sigs: list[str],
                       def_types: set, depth: int, max_depth: int):
        """Recursively collect definition nodes from the AST."""
        if depth > max_depth or len(sigs) >= 60:
            return
        if node.type in def_types:
            # Get the first line of the node
            start = node.start_point[0]
            end_line = node.end_point[0]
            lines = text.splitlines()
            if start < len(lines):
                # For multi-line defs, take first line + "..."
                first_line = lines[start].strip()
                if end_line > start:
                    # Multi-line: check if we can get a clean signature
                    sig = first_line.rstrip(":{")
                    if len(sig) > 120:
                        sig = sig[:120] + "..."
                    elif not sig.endswith("..."):
                        sig += " ..."
                else:
                    sig = first_line.rstrip(":{")
                    if len(sig) > 120:
                        sig = sig[:120] + "..."
                if sig:
                    sigs.append(sig)
        for child in node.children:
            self._collect_nodes(child, text, sigs, def_types, depth + 1, max_depth)

    @staticmethod
    def _extract_python(text: str) -> list[str]:
        """Extract Python signatures: imports, classes, functions."""
        sigs = []
        for line in text.splitlines():
            stripped = line.strip()
            # Imports (top-level only, first 30)
            if stripped.startswith(("import ", "from ")) and len(sigs) < 30:
                # Truncate long imports
                if len(stripped) > 100:
                    stripped = stripped[:100] + "..."
                sigs.append(stripped)
            # Class definitions
            elif stripped.startswith("class "):
                sigs.append(stripped.rstrip(":"))
            # Function/method definitions
            elif re.match(r"^\s*def\s+\w+", line):
                # Extract just the def line (not the full signature)
                sig = stripped.rstrip(":")
                if len(sig) > 120:
                    sig = sig[:120] + "..."
                sigs.append(sig)
            # Decorators
            elif stripped.startswith("@") and not stripped.startswith("@property"):
                if len(stripped) > 80:
                    stripped = stripped[:80] + "..."
                sigs.append(stripped)
        return sigs

    @staticmethod
    def _extract_js_ts(text: str) -> list[str]:
        """Extract JS/TS signatures: imports, exports, classes, functions."""
        sigs = []
        for line in text.splitlines():
            stripped = line.strip()
            # Imports
            if stripped.startswith(("import ", "import{")) and len(sigs) < 30:
                if len(stripped) > 100:
                    stripped = stripped[:100] + "..."
                sigs.append(stripped)
            # Exports (functions, classes, constants)
            elif stripped.startswith("export "):
                sig = stripped.rstrip("{;")
                if len(sig) > 120:
                    sig = sig[:120] + "..."
                sigs.append(sig)
            # Class definitions
            elif stripped.startswith("class "):
                sigs.append(stripped.rstrip("{"))
            # Function definitions
            elif re.match(r"^\s*(async\s+)?function\s+\w+", line):
                sig = stripped.rstrip("{")
                if len(sig) > 120:
                    sig = sig[:120] + "..."
                sigs.append(sig)
            # Arrow functions assigned to const/let
            elif re.match(r"^\s*(const|let|var)\s+\w+\s*=\s*(async\s*)?\(", line):
                sig = stripped.rstrip("{;")
                if len(sig) > 120:
                    sig = sig[:120] + "..."
                sigs.append(sig)
            # Interface/Type definitions (TS)
            elif stripped.startswith(("interface ", "type ")):
                sig = stripped.rstrip("{;")
                if len(sig) > 100:
                    sig = sig[:100] + "..."
                sigs.append(sig)
        return sigs

    @staticmethod
    def _extract_go(text: str) -> list[str]:
        """Extract Go signatures: package, imports, func, type, struct."""
        sigs = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("package "):
                sigs.append(stripped)
            elif stripped.startswith("import ") or stripped.startswith("import("):
                if len(stripped) > 100:
                    stripped = stripped[:100] + "..."
                sigs.append(stripped)
            elif stripped.startswith("func "):
                sig = stripped.rstrip("{")
                if len(sig) > 120:
                    sig = sig[:120] + "..."
                sigs.append(sig)
            elif stripped.startswith("type "):
                sig = stripped.rstrip("{")
                if len(sig) > 100:
                    sig = sig[:100] + "..."
                sigs.append(sig)
        return sigs

    @staticmethod
    def _extract_java(text: str) -> list[str]:
        """Extract Java/Kotlin signatures."""
        sigs = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("package "):
                sigs.append(stripped)
            elif stripped.startswith("import "):
                if len(sigs) < 30:
                    sigs.append(stripped)
            elif re.match(r"(public|private|protected|static|final|abstract|\s)+\s+(class|interface|enum)\s+", stripped):
                sig = stripped.rstrip("{")
                if len(sig) > 120:
                    sig = sig[:120] + "..."
                sigs.append(sig)
            elif re.match(r"(public|private|protected|static|\s)+\s+\w+\s+\w+\s*\(", stripped):
                sig = stripped.rstrip("{")
                if len(sig) > 120:
                    sig = sig[:120] + "..."
                sigs.append(sig)
        return sigs

    @staticmethod
    def _extract_generic(text: str) -> list[str]:
        """Generic fallback: extract lines that look like definitions."""
        sigs = []
        patterns = [
            re.compile(r"^\s*(def|function|func|fn|class|struct|enum|interface|type)\s+\w+"),
            re.compile(r"^\s*(public|private|protected)\s+.*\("),
        ]
        for line in text.splitlines():
            for pat in patterns:
                if pat.match(line):
                    sig = line.strip().rstrip("{:;")
                    if len(sig) > 120:
                        sig = sig[:120] + "..."
                    sigs.append(sig)
                    break
            if len(sigs) >= 50:
                break
        return sigs

    def get_related_signatures(self, file_path: str) -> list[dict]:
        """Get signatures from files that are imported/referenced by the given file.

        Returns a list of {"file": str, "signatures": list[str]} dicts.
        """
        if not self.workspace_dir:
            return []

        workspace = Path(self.workspace_dir).resolve()
        target = (workspace / file_path).resolve()
        # Security: ensure target is within workspace
        if target != workspace and not str(target).startswith(str(workspace) + os.sep):
            return []
        if not target.is_file():
            return []

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        # Extract imports from the file
        imports = self._extract_imports(text, target.suffix)
        related_files = self._resolve_imports(imports, workspace, target.parent)

        results = []
        for rel_file in related_files[:10]:  # Limit to 10 related files
            try:
                sigs = self._extract_signatures(rel_file)
                if sigs:
                    rel_path = str(rel_file.relative_to(workspace))
                    results.append({"file": rel_path, "signatures": sigs[:20]})
            except Exception:
                continue

        return results

    def generate_summary_for_prompt(self, token_budget: int = DEFAULT_TOKEN_BUDGET) -> str:
        """Generate a compact repo-map summary for auto-injection into system prompt.

        This is called by task_agent.py before each LLM call to give the agent
        awareness of the project structure without requiring an explicit tool call.

        Args:
            token_budget: Maximum tokens for the summary (default 1024).

        Returns:
            A compact string summary, or empty string if no workspace or no files.
        """
        if not self.workspace_dir or not os.path.isdir(self.workspace_dir):
            return ""

        workspace = Path(self.workspace_dir).resolve()
        try:
            files = self._collect_files(workspace, workspace, depth=3)
            if not files:
                return ""

            # Build PageRank scores
            file_scores = self._build_pagerank(files, workspace)

            # Sort by importance
            if file_scores:
                files = sorted(files, key=lambda f: file_scores.get(str(f.relative_to(workspace)), 0), reverse=True)

            # Build compact summary within token budget
            budget_chars = token_budget * 4  # ~4 chars per token
            lines: list[str] = []
            current_chars = 0

            # Header with stats
            ext_counts: dict[str, int] = {}
            for f in files:
                ext_counts[f.suffix] = ext_counts.get(f.suffix, 0) + 1
            lang_parts = []
            lang_names = {
                ".py": "py", ".js": "js", ".ts": "ts", ".tsx": "tsx", ".jsx": "jsx",
                ".go": "go", ".rs": "rs", ".java": "java",
            }
            for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
                name = lang_names.get(ext, ext)
                lang_parts.append(f"{name}:{count}")
            header = f"项目: {len(files)} 文件 ({', '.join(lang_parts)})"
            lines.append(header)
            current_chars += len(header)

            # Top files with signatures (by PageRank)
            for f in files[:30]:  # Cap at 30 files for prompt injection
                rel = str(f.relative_to(workspace))
                try:
                    sigs = self._extract_signatures(f)
                except Exception:
                    sigs = []

                if sigs:
                    # Take top 5 signatures
                    top_sigs = sigs[:5]
                    sig_line = ", ".join(s.strip() for s in top_sigs)
                    line = f"{rel}: {sig_line}"
                else:
                    line = rel

                if current_chars + len(line) > budget_chars:
                    lines.append(f"... 及其佘 {len(files) - len(lines) + 1} 个文件")
                    break
                lines.append(line)
                current_chars += len(line) + 1

            return "\n".join(lines)
        except Exception as e:
            logger.warning("generate_summary_for_prompt failed: %s", e)
            return ""

    def _extract_imports(self, text: str, ext: str) -> list[str]:
        """Extract import/module paths from source code."""
        imports = []
        if ext == ".py":
            # Python: import X, from X import Y
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("import "):
                    parts = stripped[7:].split(",")
                    for p in parts:
                        mod = p.strip().split(" as ")[0].strip()
                        if mod:
                            imports.append(mod)
                elif stripped.startswith("from "):
                    m = re.match(r"from\s+([\w.]+)\s+import", stripped)
                    if m:
                        imports.append(m.group(1))
        elif ext in (".js", ".ts", ".tsx", ".jsx"):
            # JS/TS: import ... from 'module'
            for m in re.finditer(r"from\s+['\"]([^'\"]+)['\"]", text):
                imports.append(m.group(1))
            # Also: require('module')
            for m in re.finditer(r"require\s*\(['\"]([^'\"]+)['\"]\)", text):
                imports.append(m.group(1))
        return imports[:50]  # Limit

    def _resolve_imports(self, imports: list[str], workspace: Path, base_dir: Path) -> list[Path]:
        """Resolve import paths to actual files in the workspace."""
        resolved = []
        for imp in imports:
            # Skip standard library and external packages
            if imp.startswith((".", "/")):
                # Relative import: count leading dots for levels up
                stripped = imp.lstrip(".")
                levels = len(imp) - len(stripped)  # number of leading dots
                if not stripped:
                    continue

                # Calculate base directory for this import
                import_base = base_dir
                for _ in range(levels - 1):  # first dot = current package
                    import_base = import_base.parent
                    # Don't escape workspace
                    try:
                        import_base.relative_to(workspace)
                    except ValueError:
                        import_base = workspace
                        break

                # Convert dotted path to file path
                rel = stripped.replace(".", "/")
                for ext in [".py", ".js", ".ts", ".tsx", "/__init__.py"]:
                    candidate = import_base / (rel + ext)
                    if candidate.is_file():
                        # Ensure within workspace
                        try:
                            candidate.relative_to(workspace)
                            resolved.append(candidate)
                            break
                        except ValueError:
                            continue
            else:
                # Absolute import within workspace
                parts = imp.split(".")
                for i in range(len(parts), 0, -1):
                    candidate = workspace / "/".join(parts[:i])
                    for ext in [".py", ".js", ".ts", ".tsx"]:
                        if (candidate.with_suffix(ext)).is_file():
                            resolved.append(candidate.with_suffix(ext))
                            break
                    if resolved:
                        break
        return resolved
