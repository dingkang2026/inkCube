"""python/tests/test_token_tracker.py — Token 统计单测（§3.8.7）。

回归覆盖：
  - B4：summary() 的 total_tokens 必须等于各记录 total_tokens 之和，
    与 by_section/by_type 的 total 口径一致（原用 prompt+completion 会不一致，
    因 API 的 total 可能含推理 token 等额外部分）。
"""
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.token_tracker import TokenTracker  # noqa: E402


class TestSummary:
    def test_total_equals_sum_of_record_totals(self):
        """total_tokens == Σ 各记录 total_tokens，且与分组口径一致（回归 B4）。"""
        t = TokenTracker()
        # API 的 total 可能 != prompt+completion（含推理 token 等）
        t.record("s1", "generation", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 20})
        t.record("s2", "retrieval", {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 7})
        s = t.summary()
        assert s["total_tokens"] == 27
        assert sum(v["total"] for v in s["by_section"].values()) == 27
        assert sum(v["total"] for v in s["by_type"].values()) == 27
        assert s["total_prompt_tokens"] == 13
        assert s["total_completion_tokens"] == 7
        assert s["call_count"] == 2

    def test_total_defaults_when_missing(self):
        """usage 缺 total_tokens 时回退 prompt+completion。"""
        t = TokenTracker()
        t.record("s1", "generation", {"prompt_tokens": 10, "completion_tokens": 5})
        assert t.summary()["total_tokens"] == 15

    def test_empty_summary(self):
        """空记录返回全零。"""
        s = TokenTracker().summary()
        assert s["total_tokens"] == 0
        assert s["call_count"] == 0
        assert s["by_section"] == {}
        assert s["by_type"] == {}


class TestSerialization:
    def test_roundtrip(self):
        """to_dict/from_dict 保持记录一致（用于 checkpoint 恢复）。"""
        t = TokenTracker()
        t.record("s1", "generation", {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
        restored = TokenTracker.from_dict(t.to_dict())
        assert restored.summary() == t.summary()
