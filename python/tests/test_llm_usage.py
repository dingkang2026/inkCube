"""python/tests/test_llm_usage.py — 用量记录层单元测试（2.10 §4.1/§8）。

覆盖：contextvar push/reset 嵌套与手动 token 对、push_purpose 局部覆盖、
record 落库字段完整性、无 usage/无 sink 不落库、漏上下文 other 告警、
sink 异常隔离。纯内存 spy sink，零 DB 依赖。
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import usage as usage_mod
from llm.usage import (
    UsageContext,
    configure,
    push_context,
    push_purpose,
    record,
    reset_context,
    set_context,
)


@pytest.fixture(autouse=True)
def _clean_usage_state(monkeypatch):
    """隔离模块级全局：sink 置空、contextvar 复位（防前序用例残留）。"""
    monkeypatch.setattr(usage_mod, "_sink", None)
    token = usage_mod._context.set(None)
    yield
    usage_mod._context.reset(token)


def _install_sink(monkeypatch):
    rows = []
    monkeypatch.setattr(usage_mod, "_sink", rows.append)
    return rows


class TestContextManagement:
    def test_push_reset_nested(self):
        """with 嵌套：内层覆盖、退出逐层恢复、最外层退出归 None。"""
        a = UsageContext(source="task", task_id="t1")
        b = UsageContext(source="chat", project_id="p1")
        assert usage_mod._context.get() is None
        with push_context(a):
            assert usage_mod._context.get() is a
            with push_context(b):
                assert usage_mod._context.get() is b
            assert usage_mod._context.get() is a
        assert usage_mod._context.get() is None

    def test_set_reset_manual_pair(self):
        """路由层 async for 大块执行体：set/reset 手动 token 成对。"""
        ctx = UsageContext(source="report", entity_id="r1")
        token = set_context(ctx)
        assert usage_mod._context.get() is ctx
        reset_context(token)
        assert usage_mod._context.get() is None

    def test_push_purpose_overrides_only_purpose_and_detail(self):
        """push_purpose 在既有环境上仅覆盖 purpose/detail（报告 runner 细分）。"""
        base = UsageContext(source="report", project_id="p1", entity_id="r1")
        with push_context(base):
            with push_purpose("generation", detail={"section_id": "s1"}):
                cur = usage_mod._context.get()
                assert cur.source == "report"
                assert cur.project_id == "p1"
                assert cur.entity_id == "r1"
                assert cur.purpose == "generation"
                assert cur.detail == {"section_id": "s1"}
            cur = usage_mod._context.get()
            assert cur.purpose is None and cur.detail is None

    def test_push_purpose_without_base_context(self):
        """无基础环境时 push_purpose 以默认 ctx 兜底（source 保持 other）。"""
        with push_purpose("digest"):
            assert usage_mod._context.get().purpose == "digest"
            assert usage_mod._context.get().source == "other"


class TestRecord:
    def test_record_full_fields(self, monkeypatch):
        rows = _install_sink(monkeypatch)
        with push_context(UsageContext(
            source="task", purpose="answer", task_id="t1", round_id="r1",
            project_id="p1", entity_id=None, detail=None,
        )):
            record("gpt-x", "stream", {
                "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
            })
        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "task" and row["purpose"] == "answer"
        assert row["task_id"] == "t1" and row["round_id"] == "r1"
        assert row["project_id"] == "p1" and row["entity_id"] is None
        assert row["model"] == "gpt-x" and row["mode"] == "stream"
        assert row["prompt_tokens"] == 10
        assert row["completion_tokens"] == 5
        assert row["total_tokens"] == 15
        assert row["ts"]  # ISO 本地时间字符串（date(ts) 按天分组依赖）

    def test_record_total_falls_back_to_sum(self, monkeypatch):
        """API 只给分项时 total = prompt + completion（如 400 降级端点）。"""
        rows = _install_sink(monkeypatch)
        record("m", "full", {"prompt_tokens": 7, "completion_tokens": 3})
        assert rows[0]["total_tokens"] == 10

    def test_record_detail_dict_passthrough(self, monkeypatch):
        """detail dict 原样透传（json.dumps 在 db.insert_llm_usage 层）。"""
        rows = _install_sink(monkeypatch)
        with push_context(UsageContext(
            source="report", entity_id="r1", detail={"section_id": "s1"},
        )):
            record("m", "full", {"prompt_tokens": 1, "completion_tokens": 1,
                                 "total_tokens": 2})
        assert rows[0]["detail"] == {"section_id": "s1"}
        assert rows[0]["entity_id"] == "r1"

    def test_no_usage_not_recorded(self, monkeypatch):
        """无精确值不落库（宁缺勿估）：None / {} 均忽略。"""
        rows = _install_sink(monkeypatch)
        record("m", "full", None)
        record("m", "full", {})
        assert rows == []

    def test_no_sink_silent(self, monkeypatch):
        """未注入 sink（组装前/纯单测）→ 不落库不炸。"""
        assert usage_mod._sink is None
        record("m", "full", {"prompt_tokens": 1, "completion_tokens": 1,
                             "total_tokens": 2})

    def test_missing_context_falls_back_to_other_with_warning(
            self, monkeypatch, caplog):
        """漏 push 上下文 → source=other 可观测不缺账 + WARNING 告警。"""
        rows = _install_sink(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="llm.usage"):
            record("m", "full", {"prompt_tokens": 1, "completion_tokens": 1,
                                 "total_tokens": 2})
        assert rows[0]["source"] == "other"
        assert any("without context" in r.message for r in caplog.records)

    def test_sink_exception_isolated(self, monkeypatch, caplog):
        """sink 抛异常 → record 吞掉只记 ERROR，业务主流程零感知。"""
        def _boom(row):
            raise RuntimeError("db locked")

        monkeypatch.setattr(usage_mod, "_sink", _boom)
        with caplog.at_level(logging.ERROR, logger="llm.usage"):
            record("m", "full", {"prompt_tokens": 1, "completion_tokens": 1,
                                 "total_tokens": 2})  # 不应抛出
        assert any("sink failed" in r.message for r in caplog.records)

    def test_configure_injects_sink(self):
        """configure 注入后 record 走注入 sink（main.py lifespan 组装路径）。"""
        rows = []
        configure(rows.append)
        record("m", "full", {"prompt_tokens": 1, "completion_tokens": 1,
                             "total_tokens": 2})
        assert len(rows) == 1
