"""python/tests/test_report_retry_classification.py — 报告生成智能重试分类回归测试（§3.9）。

覆盖：
  - _classify_error 纯函数全部分类规则：内容过滤→降级 / token 超限→截断 /
    401·invalid key→致命 / 限流·超时·网络·连接→重试 / 空输出→重试 / 未知→重试
  - _generate_with_retry 行为分支：首次成功直返 / 结构化异常类优先于字符串规则 /
    限流重试后成功 / 凭据类致命抛 ReportGenerationError / 内容过滤降级模式重试一次
    （成功 → 正常内容；仍失败 → 占位文本 finish=error，不静默返回空串）/
    上下文溢出保尾截断重试（耗尽 → 占位）/ 校验违规携带反馈重试

LLM 客户端 mock，validate_section patch，asyncio.sleep mock（不等待）。
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import report_generator.runner as runner_mod
from errors import LLMInvalidKey, LLMRateLimited
from report_generator.runner import (
    DEGRADE_PLACEHOLDER, MAX_RETRIES, ReportGenerationError, ReportRunner,
    _classify_error,
)
from report_generator.validator import SectionViolation


# ── _classify_error 纯函数 ──────────────────────────────────


@pytest.mark.parametrize("error_str,expected", [
    ("content_filter triggered by policy", (False, "degrade")),
    ("response blocked by safety system", (False, "degrade")),
    ("moderation: flagged", (False, "degrade")),
    ("responsible_ai violation", (False, "degrade")),
    ("context_length exceeded", (True, "truncate")),
    ("max_tokens reached", (True, "truncate")),
    ("401 unauthorized", (False, "fatal")),
    ("invalid api key provided", (False, "fatal")),
    ("429 too many requests", (True, "retry")),
    ("rate limit hit", (True, "retry")),
    ("request timed out", (True, "retry")),
    ("connection reset by peer", (True, "retry")),
    ("network unreachable", (True, "retry")),
    ("empty response from model", (True, "retry")),
    ("null content returned", (True, "retry")),
    ("some unknown failure", (True, "retry")),
])
def test_classify_error_rules(error_str, expected):
    assert _classify_error(error_str) == expected


def test_classify_error_priority_content_filter_over_retry():
    """同时命中内容过滤与限流关键词 → 内容过滤优先（degrade）。"""
    assert _classify_error("content_filter after 429") == (False, "degrade")


def test_classify_error_priority_truncate_over_401():
    """token 超限优先于 401 规则（max_tokens 含 'token' 与 '401' 同现场景）。"""
    assert _classify_error("401 after max_tokens") == (True, "truncate")


# ── _generate_with_retry 行为 ───────────────────────────────


def _ok_result(content="好的内容", finish_reason="stop"):
    return {"content": content, "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "finish_reason": finish_reason}


@pytest.fixture
def make_runner(tmp_path):
    def _make(chat_full):
        runner = ReportRunner(
            template={"report_depth": "full"},
            variables={}, project_id="p1",
            search_tool=MagicMock(), llm_client=MagicMock(),
            llm_settings={}, output_dir=str(tmp_path),
        )
        runner.llm_client.chat_full = AsyncMock(side_effect=chat_full)
        runner.system_prompt = "sys"
        return runner
    return _make


_SECTION = {"section_id": "s1", "title": "T"}


def _run_generation(runner, user_prompt="写点什么"):
    async def _go():
        with patch.object(runner_mod, "validate_section", return_value=[]):
            return await runner._generate_with_retry(_SECTION, user_prompt, given_refs=set())
    return asyncio.run(_go())


def test_first_attempt_success(make_runner):
    """首次成功 → 直接返回 (output, finish_reason)，只调用一次 LLM。"""
    runner = make_runner([_ok_result("输出")])
    output, finish = _run_generation(runner)
    assert (output, finish) == ("输出", "stop")
    assert runner.llm_client.chat_full.await_count == 1


def test_structured_error_takes_priority_over_string(make_runner):
    """LLMRateLimited 的 message 含 'invalid key' 字样 → 结构化类优先，仍按可重试处理。"""
    runner = make_runner([LLMRateLimited("wrapped: invalid key"), _ok_result("恢复")])
    output, _ = _run_generation(runner)
    assert output == "恢复"
    assert runner.llm_client.chat_full.await_count == 2


def test_rate_limited_exhausts_retries_then_raises(make_runner):
    """持续限流 → 重试至 MAX_RETRIES 次后抛 ReportGenerationError。"""
    runner = make_runner([LLMRateLimited("qps")] * (MAX_RETRIES + 1))
    with patch.object(runner_mod.asyncio, "sleep", new=AsyncMock()):
        with pytest.raises(ReportGenerationError):
            _run_generation(runner)
    assert runner.llm_client.chat_full.await_count == MAX_RETRIES


def test_invalid_key_fatal_no_retry(make_runner):
    """凭据类致命错误 → 不重试，直接抛 ReportGenerationError。"""
    runner = make_runner([LLMInvalidKey("bad key")])
    with pytest.raises(ReportGenerationError):
        _run_generation(runner)
    assert runner.llm_client.chat_full.await_count == 1


def test_content_filter_degrade_retry_succeeds(make_runner):
    """内容安全过滤 → 降级模式重试一次（§3.9）：第二次成功 → 正常内容。

    递归调用的 prompt 必须带重写提示（中性事实陈述口径）；降级重试以
    sparse 口径生成（字数校验不启用）。
    """
    runner = make_runner(
        [Exception("request blocked by content_filter"), _ok_result("降级重写成功")]
    )
    output, finish = _run_generation(runner)
    assert (output, finish) == ("降级重写成功", "stop")
    assert runner.llm_client.chat_full.await_count == 2
    second_messages = runner.llm_client.chat_full.await_args_list[1].args[0]
    assert "内容安全过滤" in second_messages[1]["content"]


def test_content_filter_persists_returns_placeholder(make_runner):
    """持续 content_filter → 降级重试一次仍失败 → 占位文本 + finish=error。

    回归核心：不得返回空串（空串会在组装时静默消失，成品缺节且
    resume 永不补）；占位节用户可见，可调整后重新生成。
    """
    runner = make_runner([Exception("blocked by content_filter")] * 4)
    output, finish = _run_generation(runner)
    assert (output, finish) == (DEGRADE_PLACEHOLDER, "error")
    # 恰好重试一次：外层 1 次 + 递归 1 次（递归 allow_degrade_retry=False）
    assert runner.llm_client.chat_full.await_count == 2
    # 失败可观测：pending warning 带降级重试告知
    assert any("内容安全过滤" in w for w in runner._pending_warnings)


def test_degrade_retry_disabled_returns_placeholder_immediately(make_runner):
    """allow_degrade_retry=False（递归内）→ 不再重试，直接占位。"""
    runner = make_runner([Exception("blocked by safety")])
    async def _go():
        with patch.object(runner_mod, "validate_section", return_value=[]):
            return await runner._generate_with_retry(
                _SECTION, "写点什么", given_refs=set(), allow_degrade_retry=False)
    output, finish = asyncio.run(_go())
    assert (output, finish) == (DEGRADE_PLACEHOLDER, "error")
    assert runner.llm_client.chat_full.await_count == 1


def test_context_overflow_truncates_prompt_tail(make_runner):
    """上下文溢出 → prompt 保尾截断（4000 字符）后重试一次即成功。"""
    tail_marker = "KEEP-TAIL"
    long_prompt = "x" * 4000 + "\n" + tail_marker
    runner = make_runner([Exception("maximum context length exceeded"), _ok_result("截断后成功")])
    output, _ = _run_generation(runner, user_prompt=long_prompt)
    assert output == "截断后成功"
    assert runner.llm_client.chat_full.await_count == 2
    second_prompt = runner.llm_client.chat_full.await_args_list[1].args[0][1]["content"]
    assert second_prompt.startswith("（注：前文摘要因上下文长度限制已截断）")
    assert tail_marker in second_prompt
    assert len(second_prompt) < 4100


def test_truncate_exhausts_attempts_returns_placeholder(make_runner):
    """截断分支每次重试仍溢出，for 循环耗尽后返回占位文本（不抛错、
不返回空串——空串会静默消失，§3.9 降级终态一律可观测）。"""
    runner = make_runner(
        [Exception("context_length")] * (MAX_RETRIES + 1)
    )
    output, finish = _run_generation(runner, user_prompt="x" * 5000)
    assert (output, finish) == (DEGRADE_PLACEHOLDER, "error")
    assert runner.llm_client.chat_full.await_count == MAX_RETRIES


def test_validation_violation_retries_with_feedback(make_runner):
    """校验违规 → 第二次携带 assistant 反馈消息重试，成功后返回新输出。"""
    runner = make_runner([_ok_result("违规草稿"), _ok_result("合规输出")])
    sec = _SECTION
    async def _go():
        with patch.object(runner_mod, "validate_section",
                          side_effect=[[SectionViolation(1, "缺少引用")], []]):
            return await runner._generate_with_retry(sec, "写点什么", given_refs=set())
    output, _ = asyncio.run(_go())
    assert output == "合规输出"
    second_messages = runner.llm_client.chat_full.await_args_list[1].args[0]
    assert len(second_messages) == 4  # system + user + assistant(草稿) + user(反馈)
    assert second_messages[2] == {"role": "assistant", "content": "违规草稿"}


def test_violations_exhaust_retries_accepts_last(make_runner):
    """校验违规耗尽重试次数 → 接受最后一次输出（不抛错）。"""
    runner = make_runner([_ok_result(f"草稿{i}") for i in range(MAX_RETRIES)])
    async def _go():
        with patch.object(runner_mod, "validate_section", return_value=[SectionViolation(1, "违规")]):
            return await runner._generate_with_retry(_SECTION, "写点什么", given_refs=set())
    output, _ = asyncio.run(_go())
    assert output == f"草稿{MAX_RETRIES - 1}"
    assert runner.llm_client.chat_full.await_count == MAX_RETRIES
