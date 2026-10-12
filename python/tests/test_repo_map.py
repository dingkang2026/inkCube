# -*- coding: utf-8 -*-
"""repo_map 回归测试。

回归背景：generate_summary_for_prompt 以 ``depth=`` 关键字调用
_collect_files（签名形参是 max_depth）→ TypeError 被 except 吞掉 →
摘要永远返回 ""（每次任务 app.log 必现 warning），repo-map 自动注入
系统提示词的链路整体失效（死功能）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.repo_map import RepoMapTool


@pytest.fixture
def mini_workspace(tmp_path):
    """最小代码工作区：一个包 + 一个带类/函数签名的模块。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        "import os\n"
        "\n"
        "class Engine:\n"
        "    def start(self):\n"
        "        pass\n"
        "\n"
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    return tmp_path


def test_generate_summary_non_empty(mini_workspace):
    """修复后摘要非空且含文件与签名（修复前 TypeError 吞掉恒空串）。"""
    tool = RepoMapTool(workspace_dir=str(mini_workspace))
    summary = tool.generate_summary_for_prompt()
    assert summary, "repo-map 摘要不应为空（depth→max_depth 关键字回归）"
    assert "pkg" in summary
    assert "Engine" in summary  # 签名提取链路可达


def test_generate_summary_empty_workspace(tmp_path):
    """无代码文件 → 空串（合法分支，不抛错）。"""
    tool = RepoMapTool(workspace_dir=str(tmp_path))
    assert tool.generate_summary_for_prompt() == ""


def test_generate_summary_no_workspace():
    """未设置工作区 → 空串快路径。"""
    assert RepoMapTool(workspace_dir="").generate_summary_for_prompt() == ""
