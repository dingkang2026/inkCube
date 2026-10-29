import json
import locale
import logging
import os
import re
import asyncio
import subprocess
import sys

from tools.base import BaseTool
from tools.proc_priority import hidden_window_kwargs, low_priority_kwargs
from tools.venv_manager import get_venv_bin_dir

logger = logging.getLogger(__name__)

# ── Dangerous command blacklist ──
# These patterns are ALWAYS blocked to prevent catastrophic damage.
_DANGEROUS_PATTERNS = [
    r"rm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*f\s+/",     # rm -rf /
    r"rm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*f[a-zA-Z]*r\s+/",     # rm -fr /
    r"rm\s+(-[a-zA-Z]+\s+)*~",                                # rm -rf ~
    r"rm\s+(-[a-zA-Z]+\s+)*\.\.",                             # rm -rf ..
    r"format\s+[A-Z]:",                                       # format C:
    r"mkfs\.",                                                # mkfs.ext4 etc.
    r"dd\s+if=.*of=/dev/",                                   # dd to device
    r":\(\)\s*\{.*\}\s*;",                                   # Shellshock
    r"DROP\s+TABLE",                                          # SQL
    r"DROP\s+DATABASE",
    r">\s*/dev/sd[a-z]",                                      # write to disk device
    r"\bshutdown\b(?![-_])",                              # system shutdown (not in package names)
    r"\breboot\b(?![-_])",
    r"\bhalt\b(?![-_])",
    r"\bpoweroff\b(?![-_])",
    # Windows recursive deletes against drive roots / system directories
    r"\b(rd|rmdir)\s+(?:/[sq]\s+)*[a-z]:[\\/]*\s*$",   # e.g. rd /s /q C:\
    r"\b(rd|rmdir|del|erase)\b[^&|;]*[a-z]:\\(?:windows|program files)\b",
]
_DANGEROUS_RE = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_PATTERNS]

MAX_OUTPUT_CHARS = 1_000_000  # 1MB output limit

# ── Safe command whitelist (smart mode) ──
# Commands matching these patterns are auto-approved in 'smart' confirm mode.
# Cross-platform: covers both Unix and Windows commands.
_SAFE_PATTERNS = [
    # ── Directory listing ──
    r"^(ls|dir|ll)\b",
    # ── File viewing ──
    r"^(cat|type|head|more|less|tail)\b",
    # ── Working directory ──
    r"^pwd\b",
    r"^cd\b",
    # ── Echo / print ──
    r"^echo\b",
    r"^print\b",
    # ── Locate commands ──
    r"^(which|where|whereis)\b",
    # ── System info ──
    r"^whoami\b",
    r"^date\b",
    r"^time\b",
    r"^hostname\b",
    r"^uname\b",
    r"^ver\b",                 # Windows version
    r"^systeminfo\b",         # Windows system info
    # ── Search ──
    r"^(grep|findstr)\b",
    # find: destructive modes (-delete / -exec family) must not auto-approve
    r"^find\b(?!\s+(?:[^\n]*\s)?(?:-delete|-exec|-execdir|-ok|-okdir)\b)",
    r"^tree\b",
    # ── File info ──
    r"^(wc|stat|file)\b",
    r"^(Get-Item|Get-ChildItem|Test-Path)\b",  # PowerShell
    # ── Git (read-only) ──
    r"^git\s+(status|log|diff|branch|show|blame|tag|remote)\b",
    # ── Python / Node (script execution) ──
    r"^python[23]?\s+\S+\.py\b",
    r"^node\s+\S+\.js\b",
    # ── Archive viewing ──
    r"^(tar\s+-[a-zA-Z]*t|unzip\s+-l|zipinfo)\b",
    # ── Network diagnostics (read-only) ──
    r"^(ping|traceroute|tracert|nslookup|dig|ipconfig|ifconfig)\b",
    # ── Process / session inspection (read-only) ──
    r"^tasklist\b",
    r"^query\b",
    r"^netstat\b",
    r"^(ps|top|df|du|uptime|free)\b",
]
_SAFE_RE = [re.compile(p, re.IGNORECASE) for p in _SAFE_PATTERNS]


# Chain separators: every segment must independently pass the whitelist,
# otherwise a safe prefix smuggles a dangerous tail (e.g. "echo hi & rd /s /q").
_CHAIN_SPLIT_RE = re.compile(r"&&|\|\||[|;&\n]")


def check_safe_command(command: str) -> bool:
    """Return True if every chained segment matches a safe pattern (smart mode)."""
    segments = [s for s in (seg.strip() for seg in _CHAIN_SPLIT_RE.split(command)) if s]
    if not segments:
        return False
    for seg in segments:
        # Command substitution runs arbitrary code in POSIX shells
        if "$(" in seg or "`" in seg:
            return False
        if not any(pat.search(seg) for pat in _SAFE_RE):
            return False
    return True


def check_domain_whitelist(command: str, whitelist: str) -> bool:
    """Return True if command targets a whitelisted domain.
    
    Args:
        command: The shell command to check.
        whitelist: Newline-separated list of allowed domains.
    """
    if not whitelist or not whitelist.strip():
        return False
    allowed = [d.strip().lower() for d in whitelist.splitlines() if d.strip()]
    if not allowed:
        return False
    # Extract domains from URLs in the command
    import re as _re
    # Match URLs: https://domain.com/..., http://domain.com, git@domain:user/repo
    url_matches = _re.findall(r'(?:https?://|git@)([a-zA-Z0-9._-]+)', command)
    # Also match bare domains in pip install / git clone: e.g. github.com/user/repo
    bare_matches = _re.findall(r'(?:pip\s+install\s+.*?--index-url\s+https?://|git\s+clone\s+.*?)([a-zA-Z0-9._-]+)', command)
    all_domains = url_matches + bare_matches
    for domain in all_domains:
        domain_lower = domain.lower()
        for allowed_domain in allowed:
            if domain_lower == allowed_domain or domain_lower.endswith('.' + allowed_domain):
                return True
    return False


def _strip_shell_escapes(cmd: str) -> str:
    """Remove common shell escape characters to normalize before pattern matching."""
    # Remove backslash escapes: r\m -> rm
    normalized = cmd.replace("\\", "")
    # Remove single and double quotes
    normalized = normalized.replace("'", "").replace('"', '')
    return normalized


def check_dangerous_command(command: str) -> str | None:
    """Return error message if command is dangerous, else None."""
    # Check both original and normalized (escape-stripped) versions
    for text in (command, _strip_shell_escapes(command)):
        for pat in _DANGEROUS_RE:
            if pat.search(text):
                return f"危险命令被阻止: '{command}' 匹配安全规则 '{pat.pattern}'"
    return None


# Timeout guidance returned to the agent so it can correct course instead of
# retrying the same blocking command (observed: a GUI launch burned ~386s).
_TIMEOUT_SUGGESTION = (
    "命令可能启动了不主动退出的长驻进程（GUI/服务器/watch 模式）。"
    "请改用分离或后台方式启动它，或改用非阻塞命令（语法检查、进程列表等）验证结果。"
)


def _decode_proc_output(data: bytes) -> str:
    """Decode subprocess output: UTF-8 first, then the ANSI codepage.

    The backend runs with PYTHONUTF8=1 (electron/python-manager.js), so
    locale.getpreferredencoding() reports utf-8 and cannot decode cmd.exe
    built-ins (dir/echo/ping emit the local ANSI codepage, e.g. cp936).
    Kept as defense-in-depth: _ensure_utf8_console flips cmd builtins to
    UTF-8 at the source; this still covers exotic encodings and the
    no-console edge case.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    fallbacks = ("mbcs",) if os.name == "nt" else (locale.getpreferredencoding(False),)
    for enc in fallbacks:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError, ValueError):
            continue
    return data.decode("utf-8", errors="replace")


# Windows: preset the console output CP once so every cmd.exe spawned
# afterwards (Popen per call) emits UTF-8 for built-ins (dir/type/echo).
# Root-cause note (2026-10 乱码复盘, evidence in tests): cmd.exe caches the
# console output CP at process init — the originally planned per-command
# prefix `chcp 65001>nul&&...` does NOT affect its own instance (dir still
# emitted cp936); the CP change only persists on the shared console and is
# inherited by NEW cmd.exe processes. Setting it from Python therefore must
# happen in our process, before the first spawn. Verified working incl.
# CREATE_NO_WINDOW hidden consoles (the production libuv pipe-spawn shape);
# on a truly console-less edge case the API just fails and the decoder
# fallback above still applies.
_console_cp_done = False


def _ensure_utf8_console() -> None:
    """One-shot SetConsoleOutputCP(65001); idempotent, no-op off Windows."""
    global _console_cp_done
    if _console_cp_done or os.name != "nt":
        return
    _console_cp_done = True
    try:
        import ctypes

        if ctypes.windll.kernel32.SetConsoleOutputCP(65001):
            logger.info("Console output CP preset to 65001 (UTF-8) for cmd built-ins")
        else:
            logger.warning(
                "SetConsoleOutputCP(65001) failed; cmd built-ins stay ANSI "
                "(decoder fallback applies)"
            )
    except Exception:
        logger.warning("Console CP preset unavailable", exc_info=True)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the whole process tree, not just the shell wrapper.

    On Windows, Popen.kill() only terminates cmd.exe; spawned children
    survive and keep the stdout/stderr pipes open, which stalls the
    subsequent communicate() cleanup (observed: a 30s timeout hung ~80s).
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                **hidden_window_kwargs(),  # 无控制台父进程下 taskkill 闪窗（2026-10-28）
            )
        else:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        logger.warning("Kill process tree failed for pid=%s", proc.pid, exc_info=True)
        try:
            proc.kill()
        except Exception:
            pass


def _run_shell_sync(
    command: str, timeout: int, workspace_dir: str, env: dict,
) -> tuple[str, str, int | None, bool]:
    """Blocking shell run for asyncio.to_thread; kills the process tree on timeout.

    Returns (stdout, stderr, returncode, timed_out); returncode is None on timeout.
    Output is raw bytes, decoded by _decode_proc_output (UTF-8 → ANSI codepage).
    """
    # 乱码根治：首个 shell 调用前把控制台输出 CP 切到 UTF-8，此后每个新
    # cmd 实例的内建输出即为 UTF-8（幂等一次性；详见 _ensure_utf8_console 注释）
    _ensure_utf8_console()
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=workspace_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        # 2.11 §5.6 R3：低优先级（前台交互天然抢占，人工任务同样受益）
        **low_priority_kwargs(),
        # POSIX: own process group so killpg reaps the whole tree
        start_new_session=(os.name != "nt"),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return (
            _decode_proc_output(stdout or b""),
            _decode_proc_output(stderr or b""),
            proc.returncode,
            False,
        )
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        # Tree is dead → pipes hit EOF → returns promptly with any partial
        # output produced before the timeout.
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = b"", b""
        hint = f"命令执行超时（超过 {timeout} 秒），已终止整个进程树。"
        return (
            _decode_proc_output(stdout or b""),
            _decode_proc_output(stderr or b"") + "\n" + hint,
            None,
            True,
        )


class ShellTool(BaseTool):
    name = "shell"
    description = (
        "在任务工作区中执行 shell 命令。"
        "命令在工作目录下执行。支持常见的 shell 命令如 ls/dir、cat、pip install 等。"
        "返回 stdout、stderr 和 exit code。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
        },
        "required": ["command"],
    }

    @property
    def prompt_description(self) -> str:
        # Detect shell environment for platform-specific guidance
        if sys.platform == "win32":
            shell_hint = (
                "当前环境: **Windows (cmd.exe)**。\n"
                "- 命令连接用 `&&`（不支持 `;` 分隔多条命令）\n"
                "- 目录列表用 `dir`（不是 `ls`）\n"
                "- 文件查看用 `type`（不是 `cat`）\n"
                "- 文件移动用 `move`（不是 `mv`）\n"
                "- 文件复制用 `copy`（不是 `cp`）\n"
                "- 路径用反斜杠 `\\` 或正斜杠 `/` 均可\n"
                "- cmd 支持 `|` 管道与 `>` 重定向；变量用 `%var%`（`$var` 无效）\n\n"
            )
        else:
            shell_hint = (
                "当前环境: **Linux/macOS (bash)**。\n"
                "- 命令连接可用 `&&` 或 `;`\n"
                "- 路径用正斜杠 `/`\n\n"
            )
        return (
            "## shell\n"
            "在工作目录下执行 shell 命令，返回 stdout/stderr/exit_code。\n\n"
            f"{shell_hint}"
            "用法:\n"
            "- 执行 Python 脚本文件: `python script.py`（PATH 已前插任务 venv，python 命中 venv）\n"
            "- 查看目录内容、系统信息等\n"
            "- 超时限制 30 秒，输出上限 1MB\n\n"
            "示例:\n"
            '```\n{"command": "python parse_data.py"}\n'
            '```\n\n{"command": "git status"}\n'
            "```\n\n"
            "安全限制: 以下命令被阻止执行: rm -rf /, format, DROP TABLE, shutdown 等危险操作。\n\n"
            "注意: 执行 Python 代码片段用 code_executor 更方便；安装 Python 依赖用 package_manager 工具"
            "（任务 venv 由 uv 创建、不含 pip，勿直接运行 pip——PATH 中的 pip 属于其他环境，会把包装错地方）；"
            "shell 适合运行脚本文件与系统命令。"
        )

    def __init__(self, workspace_dir: str = "", venv_path: str = ""):
        self.workspace_dir = workspace_dir
        self.venv_path = venv_path
        self.timeout = 30  # configurable timeout in seconds

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def set_venv_path(self, venv_path: str):
        self.venv_path = venv_path

    def _build_env(self) -> dict:
        """Build environment with venv bin dir prepended to PATH."""
        env = os.environ.copy()
        if self.venv_path and os.path.isdir(self.venv_path):
            bin_dir = get_venv_bin_dir(self.venv_path)
            env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = self.venv_path
        return env

    async def execute(self, command: str) -> str:
        if not self.workspace_dir:
            logger.error("Shell execute: workspace not set")
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        logger.info("Shell execute: command=%s, workspace=%s", command[:100], self.workspace_dir)
        # Safety check
        danger = check_dangerous_command(command)
        if danger:
            logger.warning("Shell blocked: dangerous command - %s", danger)
            return json.dumps({"error": danger, "exit_code": -1}, ensure_ascii=False)

        try:
            # Run in a worker thread — a blocking subprocess on the
            # event loop would freeze ALL tasks/APIs/SSE until timeout (H1)
            stdout, stderr, returncode, timed_out = await asyncio.to_thread(
                _run_shell_sync,
                command,
                self.timeout,
                self.workspace_dir,
                self._build_env(),
            )
        except Exception as e:
            logger.error("Shell execution failed: %s", str(e), exc_info=True)
            return json.dumps({
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
            }, ensure_ascii=False)

        # Truncate large output (Snip strategy)
        if len(stdout) > MAX_OUTPUT_CHARS:
            half = MAX_OUTPUT_CHARS // 2
            stdout = stdout[:half] + "\n[...snipped: output exceeded 1MB...]\n" + stdout[-half:]
        if len(stderr) > 50000:
            stderr = stderr[:25000] + "\n[...snipped...]\n" + stderr[-25000:]

        if timed_out:
            logger.warning("Shell timeout: command exceeded %ds, process tree killed", self.timeout)
            return json.dumps({
                "exit_code": -1,
                "stdout": stdout,
                "stderr": stderr,
                "error_type": "TimeoutError",
                "suggestion": _TIMEOUT_SUGGESTION,
            }, ensure_ascii=False)

        logger.info("Shell completed: exit_code=%s, stdout_len=%d, stderr_len=%d",
                   returncode, len(stdout), len(stderr))
        return json.dumps({
            "exit_code": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }, ensure_ascii=False)
