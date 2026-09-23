import json
import locale
import logging
import os
import re
import subprocess
import sys

from tools.base import BaseTool
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
    r"^find\b",
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
]
_SAFE_RE = [re.compile(p, re.IGNORECASE) for p in _SAFE_PATTERNS]


def check_safe_command(command: str) -> bool:
    """Return True if command matches a safe pattern (auto-approve in smart mode)."""
    stripped = command.strip()
    for pat in _SAFE_RE:
        if pat.search(stripped):
            return True
    return False


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
                "- **禁止**使用 bash/PowerShell 专有语法（如 `$var`、`|`、`>` 重定向等）\n\n"
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
            "- 运行 pip install 安装依赖（已激活任务 venv，直接 pip install 即可）\n"
            "- 执行 Python 脚本文件: `python script.py`\n"
            "- 查看目录内容、系统信息等\n"
            "- 超时限制 30 秒，输出上限 1MB\n\n"
            "示例:\n"
            '```\n{"command": "pip install requests"}\n'
            '```\n\n{"command": "python parse_pptx.py"}\n'
            "```\n\n"
            "安全限制: 以下命令被阻止执行: rm -rf /, format, DROP TABLE, shutdown 等危险操作。\n\n"
            "注意: 执行 Python 代码片段用 code_executor 更方便；shell 适合运行脚本文件或安装依赖。"
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
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_dir,
                capture_output=True,
                # Use explicit encoding with error replacement to avoid
                # UnicodeDecodeError on non-UTF-8 output (e.g. GBK from cmd.exe)
                encoding=locale.getpreferredencoding(),
                errors="replace",
                timeout=self.timeout,
                env=self._build_env(),
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            logger.info("Shell completed: exit_code=%d, stdout_len=%d, stderr_len=%d",
                       result.returncode, len(stdout), len(stderr))
            # Truncate large output (Snip strategy)
            if len(stdout) > MAX_OUTPUT_CHARS:
                half = MAX_OUTPUT_CHARS // 2
                stdout = stdout[:half] + "\n[...snipped: output exceeded 1MB...]\n" + stdout[-half:]
            if len(stderr) > 50000:
                stderr = stderr[:25000] + "\n[...snipped...]\n" + stderr[-25000:]
            return json.dumps({
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            logger.warning("Shell timeout: command exceeded %ds", self.timeout)
            return json.dumps({
                "exit_code": -1,
                "stdout": "",
                "stderr": f"命令执行超时（超过 {self.timeout} 秒）",
                "error_type": "TimeoutError",
            }, ensure_ascii=False)
        except Exception as e:
            logger.error("Shell execution failed: %s", str(e), exc_info=True)
            return json.dumps({
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
            }, ensure_ascii=False)
