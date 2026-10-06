"""python/tests/test_config_error_actionable.py — 配置类错误可行动文案单测（0.4 §7.3 v3.6）。

覆盖：
  - errors.actionable()：InkCubeError 拼 message + suggestion（含设置页指引）、
    无 suggestion 仅 message、普通异常透传；
  - agent.task_agent._llm_error_msg()：网络类双分支（超时/连接）、配置类带 ❌ 与
    「设置 > 外部模型」指引、未知异常透传 detail。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from errors import InkCubeError, LLMInvalidKey, LLMNetworkError, actionable
from agent.task_agent import _llm_error_msg


class TestActionable:
    def test_joins_message_and_suggestion(self):
        msg = actionable(LLMInvalidKey())
        assert msg.startswith("LLM API Key 无效或已失效")
        assert "设置 > 外部模型" in msg
        # message 与 suggestion 以句号拼接，构成完整可行动文案
        assert "。请前往" in msg

    def test_without_suggestion_returns_message_only(self):
        e = InkCubeError("X_CODE", "INTERNAL", "内部错误")
        assert actionable(e) == "内部错误"

    def test_plain_exception_passthrough(self):
        assert actionable(RuntimeError("boom")) == "boom"


class TestLlmErrorMsg:
    def test_network_timeout_branch(self):
        msg = _llm_error_msg(LLMNetworkError(detail="Request timed out after 30s"))
        assert "超时" in msg
        assert "网络" in msg

    def test_network_connection_branch(self):
        msg = _llm_error_msg(LLMNetworkError(detail="Connection refused"))
        assert "连接" in msg

    def test_config_error_carries_guidance(self):
        msg = _llm_error_msg(LLMInvalidKey())
        assert msg.startswith("❌")
        assert "设置 > 外部模型" in msg

    def test_unknown_exception_passthrough(self):
        assert _llm_error_msg(RuntimeError("boom")) == "调用模型出错: boom"
