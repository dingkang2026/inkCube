import ast
import os
import sys
import json
import logging
import re
import asyncio
import subprocess
import time

from tools.base import BaseTool
from tools.proc_priority import low_priority_kwargs
from tools.shell_tool import check_dangerous_command, check_safe_command

logger = logging.getLogger(__name__)


# ── Dangerous-call guard (S12, c2f24e5c interaction-9) ──
# code_executor was a full bypass of the shell confirmation system: generated
# shutil.rmtree()/os.system() hit neither the 3-mode confirm flow nor the
# blacklist. Freedom is now scoped by the workspace boundary: inside →
# allowed, outside/undecidable → confirm, catastrophic commands → blocked
# via the SAME shell checkers (一视同仁).
_DELETE_CALLS = {"rmtree", "rmdir", "remove", "unlink"}   # shutil.* / os.*
_OS_CMD_CALLS = {"system", "popen"}
_SUBPROCESS_CALLS = {"run", "call", "check_call", "check_output", "Popen",
                     "getoutput", "getstatusoutput"}


def _collect_import_aliases(tree: ast.Module) -> dict:
    """Map local names → fully-qualified module names (import/alias aware)."""
    alias_map: dict = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                alias_map[a.asname or a.name] = a.name
        elif isinstance(n, ast.ImportFrom) and n.module:
            for a in n.names:
                alias_map[a.asname or a.name] = f"{n.module}.{a.name}"
    return alias_map


def _resolve_call(node: ast.Call, alias_map: dict) -> tuple[str, str] | None:
    """Return (module, func) when the call targets a tracked module, else None."""
    if isinstance(node.func, ast.Name):
        full = alias_map.get(node.func.id, "")
        if full:
            mod, _, func = full.rpartition(".")
            return mod, func
        return None
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        mod = alias_map.get(node.func.value.id, node.func.value.id)
        return mod, node.func.attr
    return None


def _path_outside_workspace(path_str: str, workspace_dir: str) -> bool:
    """True when path_str resolves outside workspace_dir (.. escapes included)."""
    p = os.path.expanduser(path_str.strip())
    if not os.path.isabs(p):
        p = os.path.join(workspace_dir, p)
    real_ws = os.path.realpath(workspace_dir)
    target = os.path.realpath(p)
    try:
        return os.path.commonpath([target, real_ws]) != real_ws
    except ValueError:
        return True


def check_code_danger(code: str, workspace_dir: str) -> dict | None:
    """AST-scan generated code for dangerous calls before execution.

    Returns None to allow, or {"action": "confirm"|"block", "reason": str}:
    - deletion APIs (shutil.rmtree / os.rmdir|remove|unlink): constant path
      inside the workspace → allow; outside or a dynamic expression → confirm.
    - command execution (os.system|popen / subprocess.*): the command string
      goes through the SAME shell checkers — blacklist → block, whitelist →
      allow, otherwise → confirm.
    Unparseable code returns None; the run itself will raise the SyntaxError.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    alias_map = _collect_import_aliases(tree)
    confirm_reason = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_call(node, alias_map)
        if not resolved:
            continue
        mod, func = resolved
        args = node.args

        if func in _DELETE_CALLS and mod in ("shutil", "os"):
            if not args:
                continue
            arg0 = args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                if _path_outside_workspace(arg0.value, workspace_dir):
                    confirm_reason = confirm_reason or (
                        f"{mod}.{func} 删除目标在工作区外: {arg0.value!r}"
                    )
            else:
                confirm_reason = confirm_reason or (
                    f"{mod}.{func} 路径为动态表达式，无法静态判定是否在工作区内"
                )

        is_cmd = (mod == "os" and func in _OS_CMD_CALLS) or (
            mod == "subprocess" and func in _SUBPROCESS_CALLS
        )
        if is_cmd:
            if not args:
                continue
            arg0 = args[0]
            if isinstance(arg0, (ast.List, ast.Tuple)):
                elts = arg0.elts
                if elts and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in elts
                ):
                    cmd = " ".join(e.value for e in elts)
                else:
                    confirm_reason = confirm_reason or (
                        f"{mod}.{func} 命令含动态表达式，无法静态判定"
                    )
                    continue
            elif isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                cmd = arg0.value
            else:
                confirm_reason = confirm_reason or (
                    f"{mod}.{func} 命令为动态表达式，无法静态判定"
                )
                continue
            danger = check_dangerous_command(cmd)
            if danger:
                return {"action": "block", "reason": danger}
            if not check_safe_command(cmd):
                confirm_reason = confirm_reason or (
                    f"{mod}.{func} 执行非白名单命令: {cmd[:80]!r}"
                )

    if confirm_reason:
        return {"action": "confirm", "reason": confirm_reason}
    return None


class CodeExecutorTool(BaseTool):
    name = "code_executor"
    description = (
        "在任务工作区中执行 Python 代码。"
        "代码在工作目录下执行，可以访问工作区中的文件。"
        "返回 stdout、stderr、exit code 和执行耗时。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            },
        },
        "required": ["code"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## code_executor\n"
            "执行 Python 代码，返回 stdout/stderr/exit_code/耗时。代码在工作目录下执行。\n\n"
            "用法:\n"
            "- 适合快速验证想法、处理文件、计算数据\n"
            "- stdout 被捕获，不要调用 sys.stdout.reconfigure()\n"
            "- 如果输出含中文/特殊字符，将结果写入文件再用 file_ops 读取\n"
            "- 超时限制 30 秒\n"
            "- 执行后检查 exit_code，非 0 表示失败，根据 error_type 和 error_line 修正\n\n"
            "示例:\n"
            '```\n{"code": "print(2 + 2)"}\n'
            "→ {\"exit_code\": 0, \"stdout\": \"4\", \"duration_ms\": 35}\n"
            '```\n\n{"code": "import os\\nprint(os.listdir(\'.\'))"}\n'
            "```\n\n"
            "错误时返回:\n"
            "```\n"
            '{"exit_code": 1, "error_type": "NameError", "error_line": 2,\n'
            ' "error_message": "name \'x\' is not defined", "suggestion": "检查第 2 行变量名是否拼写正确"}\n'
            "```"
        )

    def __init__(self, workspace_dir: str = "", venv_python: str = ""):
        self.workspace_dir = workspace_dir
        self.venv_python = venv_python  # path to venv python; empty = use sys.executable
        self.timeout = 30  # configurable timeout in seconds

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def set_venv_python(self, venv_python: str):
        self.venv_python = venv_python

    @property
    def _python_cmd(self) -> str:
        return self.venv_python if self.venv_python and os.path.isfile(self.venv_python) else sys.executable

    async def execute(self, code: str) -> str:
        if not self.workspace_dir:
            logger.error("Code executor: workspace not set")
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        # Guard rail (S12): catastrophic calls are blocked even if the
        # agent-layer confirm branch is ever bypassed; confirm-level verdicts
        # are handled by TaskAgent's confirmation flow.
        guard = check_code_danger(code, self.workspace_dir)
        if guard and guard["action"] == "block":
            logger.warning("Code executor blocked dangerous call: %s", guard["reason"])
            return json.dumps({
                "error": guard["reason"],
                "error_type": "DangerousCallBlocked",
                "exit_code": -1,
            }, ensure_ascii=False)

        logger.info("Code executor: code_len=%d, workspace=%s", len(code), self.workspace_dir)
        start = time.time()
        try:
            # Worker thread — keep the event loop responsive (H1)
            result = await asyncio.to_thread(
                subprocess.run,
                [self._python_cmd, "-c", code],
                cwd=self.workspace_dir,
                capture_output=True,
                encoding="utf-8", errors="replace",
                timeout=self.timeout,
                # 2.11 §5.6 R3：低优先级（前台交互天然抢占）
                **low_priority_kwargs(),
            )
            duration_ms = int((time.time() - start) * 1000)
            logger.info("Code executor completed: exit_code=%d, duration_ms=%d, stdout_len=%d, stderr_len=%d",
                       result.returncode, duration_ms, len(result.stdout), len(result.stderr))
            resp: dict = {
                "exit_code": result.returncode,
                "stdout": result.stdout[:50000],
                "stderr": result.stderr[:10000],
                "duration_ms": duration_ms,
            }
            # Parse structured error info from stderr
            if result.returncode != 0 and result.stderr:
                err_info = self._parse_error(result.stderr)
                if err_info:
                    resp.update(err_info)
            return json.dumps(resp, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            logger.warning("Code executor timeout: exceeded %ds, duration_ms=%d", self.timeout, duration_ms)
            return json.dumps({
                "exit_code": -1,
                "stdout": "",
                "stderr": f"执行超时（超过 {self.timeout} 秒）",
                "error_type": "TimeoutError",
                "suggestion": f"代码执行超过 {self.timeout} 秒限制。考虑优化算法或将结果写入文件分批处理。",
                "duration_ms": duration_ms,
            }, ensure_ascii=False)
        except Exception as e:
            logger.error("Code executor failed: %s", str(e), exc_info=True)
            return json.dumps({
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "duration_ms": 0,
            }, ensure_ascii=False)

    @staticmethod
    def _parse_error(stderr: str) -> dict:
        """Extract structured error info from Python traceback."""
        # Match the last line of traceback: "SomeError: message"
        m = re.search(r'(\w+Error):\s*(.+)$', stderr, re.MULTILINE)
        if not m:
            # Try SyntaxError special format
            m = re.search(r'(\w+Error):\s*(.+)', stderr)
        if not m:
            return {}

        error_type = m.group(1)
        error_message = m.group(2).strip()

        # Extract line number from traceback
        line_m = re.findall(r'line (\d+)', stderr)
        error_line = int(line_m[-1]) if line_m else None

        # Generate suggestion based on error type
        suggestions = {
            "NameError": f"检查第 {error_line} 行变量名是否拼写正确，或是否已定义",
            "TypeError": f"检查第 {error_line} 行参数类型是否匹配",
            "ValueError": f"检查第 {error_line} 行的值是否合法",
            "KeyError": f"检查第 {error_line} 行字典键是否存在",
            "IndexError": f"检查第 {error_line} 行索引是否超出范围",
            "FileNotFoundError": f"检查文件路径是否正确",
            "ImportError": f"检查模块是否已安装，可能需要 pip install",
            "ModuleNotFoundError": f"模块未安装，使用 pip install <模块名> 安装",
            "ZeroDivisionError": f"检查第 {error_line} 行除数是否可能为零",
            "AttributeError": f"检查第 {error_line} 行对象是否有该属性或方法",
            "SyntaxError": f"检查第 {error_line} 行语法是否正确",
            "IndentationError": f"检查缩进是否一致",
        }
        suggestion = suggestions.get(error_type, f"根据错误信息修正第 {error_line} 行的问题")

        result = {
            "error_type": error_type,
            "error_message": error_message,
            "suggestion": suggestion,
        }
        if error_line:
            result["error_line"] = error_line
        return result
