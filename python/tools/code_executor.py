import os
import sys
import json
import logging
import re
import subprocess
import time

from tools.base import BaseTool

logger = logging.getLogger(__name__)


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

        logger.info("Code executor: code_len=%d, workspace=%s", len(code), self.workspace_dir)
        start = time.time()
        try:
            result = subprocess.run(
                [self._python_cmd, "-c", code],
                cwd=self.workspace_dir,
                capture_output=True,
                encoding="utf-8", errors="replace",
                timeout=self.timeout,
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
