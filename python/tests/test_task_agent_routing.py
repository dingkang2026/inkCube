"""python/tests/test_task_agent_routing.py — 任务主对话模型档位分流（2026-10-19）.

背景：ppt_model 路由键曾是死键——task_agent._run_inner 对所有任务统一按
task_model 解析主模型，PPT 任务（origin='ppt'）改档位下拉实际不生效。
修复：TaskAgent._apply_task_routing 按 origin 分流（ppt → get_task_settings("ppt")，
其余 → task_model）。本文件锁死分流映射与档位解析值。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.task_agent import TaskAgent

BASE = {
    "standard_model": "glm-5.3-flash",
    "high_model": "glm-5.3",
}


class TestApplyTaskRouting:
    def test_ppt_origin_resolves_ppt_model_tier(self):
        """origin='ppt' 按 ppt_model 档位解析（默认 high → 高配槽）。"""
        settings = dict(BASE, ppt_model="high", task_model="standard")
        out = TaskAgent._apply_task_routing(settings, "ppt")
        assert out["model"] == "glm-5.3"

    def test_ppt_origin_ignores_task_model(self):
        """ppt 分流不受 task_model 影响（死键回归防线）。"""
        settings = dict(BASE, ppt_model="standard", task_model="high")
        out = TaskAgent._apply_task_routing(settings, "ppt")
        assert out["model"] == "glm-5.3-flash"

    def test_chat_origin_resolves_task_model_tier(self):
        """普通任务按 task_model 档位解析。"""
        settings = dict(BASE, ppt_model="high", task_model="high")
        out = TaskAgent._apply_task_routing(settings, "chat")
        assert out["model"] == "glm-5.3"

    def test_empty_tier_falls_back_to_standard(self):
        """档位键空值按 standard 解析（routing 语义：空按标准档）。"""
        settings = dict(BASE, ppt_model="", task_model="")
        out = TaskAgent._apply_task_routing(settings, "chat")
        assert out["model"] == "glm-5.3-flash"

    def test_tier_falls_back_across_slots(self):
        """选中槽为空时互回退到另一槽（两槽互为回退语义）。"""
        settings = dict(BASE, ppt_model="high", task_model="standard")
        settings["high_model"] = ""  # 高配槽空
        out = TaskAgent._apply_task_routing(settings, "ppt")
        assert out["model"] == "glm-5.3-flash"

    def test_missing_origin_defaults_to_chat(self):
        """origin 缺失（旧任务行）按普通任务走 task_model。"""
        settings = dict(BASE, ppt_model="high", task_model="standard")
        out = TaskAgent._apply_task_routing(settings, "")
        assert out["model"] == "glm-5.3-flash"
