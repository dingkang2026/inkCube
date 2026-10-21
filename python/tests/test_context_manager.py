# -*- coding: utf-8 -*-
"""python/tests/test_context_manager.py — 统一工具摘要路标回归（2026-10-16，1.1 §6.2）。

summarize_tool_result_line 是工具结果的两个「丢细节」入口共用的一致格式：
- 回合内 Micro Compact（70% 压力压早期工具结果）
- 回合间重建（DB 回放只携 user/assistant，TaskAgent._rebuild_history_from_db）
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.context_manager import (  # noqa: E402
    ContextManager,
    summarize_tool_result_line,
)


class TestSummarizeToolResultLine:
    def test_json_exit_code_plus_stdout_first_line(self):
        content = json.dumps({"exit_code": 0, "stdout": "wrote 01.svg\nsecond line"})
        line = summarize_tool_result_line("shell", content)
        assert line.startswith("shell → exit=0 | ")
        assert "wrote 01.svg" in line
        assert "second line" not in line

    def test_json_error_preferred_over_stdout(self):
        content = json.dumps({
            "exit_code": 1, "error": "ModuleNotFoundError: pptx", "stdout": "irrelevant"})
        line = summarize_tool_result_line("shell", content)
        assert "exit=1" in line
        assert "ModuleNotFoundError" in line
        assert "irrelevant" not in line

    def test_json_list_field_joined(self):
        content = json.dumps({
            "exit_code": 0, "installed": ["requests", "pandas", "pillow", "extra"]})
        line = summarize_tool_result_line("package_manager", content)
        assert "requests, pandas, pillow" in line
        assert "extra" not in line

    def test_ok_field_status(self):
        content = json.dumps({"ok": True, "path": "a.txt"})
        line = summarize_tool_result_line("file_ops", content)
        assert "ok=True" in line

    def test_plain_text_first_line(self):
        assert summarize_tool_result_line("file_ops", "created file\nsecond") \
            == "file_ops → created file"

    def test_empty_content(self):
        assert summarize_tool_result_line("shell", "") == "shell → (空结果)"
        assert summarize_tool_result_line("shell", None) == "shell → (空结果)"

    def test_truncation_marks_ellipsis(self):
        line = summarize_tool_result_line("shell", "x" * 500, max_len=100)
        assert len(line) <= 103
        assert line.endswith("...")


class TestMicroCompactUsesUnifiedSummary:
    def test_compressed_tool_result_carries_tool_name(self):
        """Micro Compact 压早期工具结果时带工具名（从 assistant.tool_calls 反查）。"""
        cm = ContextManager(max_tokens=1000, reserve_for_response=0,
                            reserve_for_system=0, keep_recent=2)
        long_out = "x" * 4000
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "shell", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": long_out},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "next"},
        ]
        out = cm.micro_compact(messages)

        tools = [m for m in out if m.get("role") == "tool"]
        assert tools, "tool 消息不应被丢弃"
        assert tools[0]["content"].startswith("[已压缩] shell →")
        assert "tool_call_id" in tools[0], "压缩必须保留 tool_call_id（配对合法）"

    def test_short_tool_results_kept_intact(self):
        cm = ContextManager(max_tokens=1000, reserve_for_response=0,
                            reserve_for_system=0, keep_recent=2)
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "c1", "content": "short"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "next"},
        ]
        out = cm.micro_compact(messages)
        tool_msg = next(m for m in out if m.get("role") == "tool")
        assert tool_msg["content"] == "short", "短工具结果不压缩"
