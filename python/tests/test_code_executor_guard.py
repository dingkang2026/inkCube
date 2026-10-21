"""code_executor 危险调用守卫（S12）回归测试。

背景（c2f24e5c 交互⑨）：code_executor 是 shell 确认体系的完全旁路——
生成的 shutil.rmtree / os.system 既不触发三档确认也不进黑名单。
修复按「工作区边界」划自由度：区内放行、区外/动态路径需确认、
命令执行复用 shell 同一套黑白名单判定（一视同仁）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tools.code_executor import CodeExecutorTool, check_code_danger


class TestWorkspaceBoundary:
    """删除类 API：区内放行、区外/动态需确认。"""

    def test_plain_code_passes(self, tmp_path):
        assert check_code_danger("print(2 + 2)", str(tmp_path)) is None

    def test_syntax_error_passes(self, tmp_path):
        """解析失败不拦——执行时自然抛 SyntaxError。"""
        assert check_code_danger("def broken(:", str(tmp_path)) is None

    def test_rmtree_inside_passes(self, tmp_path):
        code = "import shutil\nshutil.rmtree('build')"
        assert check_code_danger(code, str(tmp_path)) is None

    def test_rmtree_outside_confirms(self, tmp_path):
        code = "import shutil\nshutil.rmtree(r'D:/other/dir')"
        verdict = check_code_danger(code, str(tmp_path))
        assert verdict and verdict["action"] == "confirm"

    def test_rmtree_relative_escape_confirms(self, tmp_path):
        verdict = check_code_danger(
            "import shutil\nshutil.rmtree('../outside')", str(tmp_path))
        assert verdict and verdict["action"] == "confirm"

    def test_remove_dynamic_path_confirms(self, tmp_path):
        verdict = check_code_danger("import os\nos.remove(target)", str(tmp_path))
        assert verdict and verdict["action"] == "confirm"

    def test_import_alias_detected(self, tmp_path):
        code = "import shutil as sh\nsh.rmtree(r'C:/Windows/temp')"
        verdict = check_code_danger(code, str(tmp_path))
        assert verdict and verdict["action"] == "confirm"

    def test_from_import_detected(self, tmp_path):
        code = "from shutil import rmtree\nrmtree(r'D:/elsewhere')"
        verdict = check_code_danger(code, str(tmp_path))
        assert verdict and verdict["action"] == "confirm"


class TestCommandExecutionSameAsShell:
    """命令执行类：与 shell 共用黑白名单（一视同仁）。"""

    def test_whitelisted_command_passes(self, tmp_path):
        code = "import subprocess\nsubprocess.run(['python', 'x.py'])"
        assert check_code_danger(code, str(tmp_path)) is None

    def test_blacklisted_command_blocks(self, tmp_path):
        code = "import os\nos.system('rd /s /q C:\\\\')"
        verdict = check_code_danger(code, str(tmp_path))
        assert verdict and verdict["action"] == "block"

    def test_unknown_command_confirms(self, tmp_path):
        """不在白名单也不在黑名单 → 确认（与 shell smart 模式同判）。"""
        code = "import os\nos.system('rd /s /q build')"
        verdict = check_code_danger(code, str(tmp_path))
        assert verdict and verdict["action"] == "confirm"

    def test_subprocess_dynamic_confirms(self, tmp_path):
        verdict = check_code_danger(
            "import subprocess\nsubprocess.run(cmd)", str(tmp_path))
        assert verdict and verdict["action"] == "confirm"


class TestExecuteLayerBlock:
    """execute 内部兜底：黑名单调用直接拒绝、不触达 subprocess。"""

    def test_block_returned_without_execution(self, tmp_path, monkeypatch):
        import tools.code_executor as mod
        tool = CodeExecutorTool(workspace_dir=str(tmp_path))

        def _fail(*a, **kw):
            raise AssertionError("subprocess must not be reached")

        monkeypatch.setattr(mod.asyncio, "to_thread", _fail)
        out = json.loads(asyncio.run(
            tool.execute("import os\nos.system('rd /s /q C:\\\\')")))
        assert out["error_type"] == "DangerousCallBlocked"
        assert "rd" in out["error"]

    def test_safe_code_still_runs(self, tmp_path):
        """放行路径回归：普通代码照常执行。"""
        tool = CodeExecutorTool(workspace_dir=str(tmp_path))
        out = json.loads(asyncio.run(tool.execute("print(1 + 1)")))
        assert out["exit_code"] == 0
        assert out["stdout"].strip() == "2"
