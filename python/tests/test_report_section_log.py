"""_section_done_log 回归测试（0.4 §7.4 v3.6）。

降级显式告知：章节因材料不足降级为分析推理模式时，
报告日志必须写 warning 级（此前只区分 skipped，降级章节静默显示"章节完成"）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers.reports import _section_done_log


class TestSectionDoneLog:
    def test_normal_completion(self):
        msg, level = _section_done_log({"title": "市场分析"})
        assert msg == "章节完成: 市场分析"
        assert level == "info"

    def test_skipped(self):
        msg, level = _section_done_log({"title": "竞品对比", "skipped": True})
        assert msg == "章节跳过: 竞品对比（材料不足）"
        assert level == "info"

    def test_degraded_is_warning(self):
        """核心回归：降级章节必须 warning 级显式告知，而非静默"章节完成"。"""
        msg, level = _section_done_log(
            {"title": "技术路线", "degraded": True}
        )
        assert level == "warning"
        assert "章节降级" in msg
        assert "技术路线" in msg
        assert "分析推理" in msg
        assert "材料不足" in msg
        # 文案带修正指引（0.4 §7.4：文案必须自带行动建议）
        assert "重新生成" in msg

    def test_skipped_takes_precedence_over_degraded(self):
        """两者同时为真时 skipped 优先（跳过比降级更终态）。"""
        msg, level = _section_done_log(
            {"title": "风险", "skipped": True, "degraded": True}
        )
        assert level == "info"
        assert "章节跳过" in msg

    def test_generation_failed_is_error(self):
        """核心回归（M2）：降级终态（占位文本）必须 error 级显式告知，
        不允许「日志写完成、正文缺内容」的静默不一致。"""
        msg, level = _section_done_log(
            {"title": "市场分析", "generation_failed": True}
        )
        assert level == "error"
        assert "章节生成失败" in msg
        assert "市场分析" in msg
        assert "重新生成" in msg  # 文案带行动指引（0.4 §7.4）

    def test_generation_failed_takes_precedence_over_degraded(self):
        """generation_failed 与 degraded 同现时失败优先（更终态）。"""
        msg, level = _section_done_log(
            {"title": "风险", "generation_failed": True, "degraded": True}
        )
        assert level == "error"
        assert "章节生成失败" in msg
        assert "章节降级" not in msg
