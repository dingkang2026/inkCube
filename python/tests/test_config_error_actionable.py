"""python/tests/test_config_error_actionable.py — 配置类错误可行动文案单测（0.4 §7.3 v3.6，D1 错误契约）。

覆盖：
  - errors.actionable()：InkCubeError 产出 `INKCUBE_CODE:<CODE>|<detail>` 协议串
    （前端 translateStoredError 还原为当前语言；后端日志用 describe）、普通异常透传；
  - errors.describe()：后端日志可读文案（message + suggestion 拼接、透传）；
  - agent.task_agent._llm_error_msg()：网络类双分支（超时/连接）产出协议串、
    配置类带 ❌ 前缀协议串、未知异常透传 detail。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from errors import InkCubeError, LLMInvalidKey, LLMNetworkError, actionable, describe
from agent.task_agent import _llm_error_msg


class TestActionable:
    def test_protocol_without_detail(self):
        # detail 为空 → 纯 code 协议串
        assert actionable(LLMInvalidKey()) == "INKCUBE_CODE:LLM_INVALID_KEY"

    def test_protocol_with_detail(self):
        msg = actionable(LLMInvalidKey(detail="401 from api"))
        assert msg == "INKCUBE_CODE:LLM_INVALID_KEY|401 from api"

    def test_plain_exception_passthrough(self):
        assert actionable(RuntimeError("boom")) == "boom"

    def test_custom_code_without_detail(self):
        e = InkCubeError("X_CODE", "INTERNAL", "内部错误")
        assert actionable(e) == "INKCUBE_CODE:X_CODE"


class TestDescribe:
    """describe() 是后端日志可读文案，替代 actionable() 旧版的中文拼接职责。"""

    def test_joins_message_and_suggestion(self):
        msg = describe(LLMInvalidKey())
        assert msg.startswith("LLM API Key 无效或已失效")
        assert "设置 > 外部模型" in msg
        # message 与 suggestion 以句号拼接，构成完整可行动文案
        assert "。请前往" in msg

    def test_without_suggestion_returns_message_only(self):
        e = InkCubeError("X_CODE", "INTERNAL", "内部错误")
        assert describe(e) == "内部错误"

    def test_plain_exception_passthrough(self):
        assert describe(RuntimeError("boom")) == "boom"


class TestLlmErrorMsg:
    def test_network_timeout_branch(self):
        msg = _llm_error_msg(LLMNetworkError(detail="Request timed out after 30s"))
        assert msg == "INKCUBE_CODE:LLM_NETWORK_TIMEOUT"

    def test_network_connection_branch(self):
        msg = _llm_error_msg(LLMNetworkError(detail="Connection refused"))
        assert msg == "INKCUBE_CODE:LLM_NETWORK_ERROR"

    def test_config_error_carries_protocol(self):
        msg = _llm_error_msg(LLMInvalidKey())
        assert msg.startswith("❌")
        assert msg.endswith("INKCUBE_CODE:LLM_INVALID_KEY")

    def test_unknown_exception_passthrough(self):
        assert _llm_error_msg(RuntimeError("boom")) == "调用模型出错: boom"
