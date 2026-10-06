"""任务确认流（skill 首次确认永久信任 + 超时失效反馈）回归测试。

背景（用户实测暴露，1.1 §7.2）：
1. 任务弹窗点「允许」只放行当次执行、不落库 → needs_confirm 恒为 1，
   每个 skill 每个任务都弹确认框；
2. 确认等待超时后后端 pop 掉 pending 且不通知前端 → 残留弹窗点击全部落空。
修复：确认即永久信任（confirm_skill_by_name）；超时 yield confirm_expired；
respond 返回 found=False 供前端提示。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database  # noqa: E402
from agent.task_agent import TaskAgent  # noqa: E402


@pytest.fixture()
def db():
    database = Database(":memory:")
    database.initialize()
    return database


@pytest.fixture()
def agent(db):
    return TaskAgent(db)


def _make_skill(db: Database, name: str) -> str:
    return db.create_skill(
        name=name, description="d", source_type="local",
        source_path="X", install_path="X",
    )["id"]


class TestConfirmSkillByName:
    def test_sets_needs_confirm_zero(self, db: Database):
        _make_skill(db, "my-skill")
        assert db.confirm_skill_by_name("my-skill") is True
        skill = next(s for s in db.list_skills() if s["name"] == "my-skill")
        assert not skill["needs_confirm"]

    def test_unknown_name_returns_false(self, db: Database):
        assert db.confirm_skill_by_name("nope") is False

    def test_installed_skill_defaults_to_needs_confirm(self, db: Database):
        """新装 skill 默认 needs_confirm=1（首次使用确认语义的前提）。"""
        _make_skill(db, "fresh-skill")
        skill = next(s for s in db.list_skills() if s["name"] == "fresh-skill")
        assert skill["needs_confirm"]


class TestRespondSkillConfirm:
    def test_confirm_persists_permanent_trust(self, agent: TaskAgent, db: Database):
        _make_skill(db, "trusted")
        event = asyncio.Event()
        agent._pending_skill_confirms["t1"] = {
            "skill_name": "trusted", "confirmed": False, "event": event,
        }
        assert agent.respond_skill_confirm("t1", True) is True
        assert event.is_set()
        skill = next(s for s in db.list_skills() if s["name"] == "trusted")
        assert not skill["needs_confirm"]  # 永久信任落库，下次不再弹

    def test_reject_does_not_persist(self, agent: TaskAgent, db: Database):
        """拒绝仅本次：不清 needs_confirm（下次仍询问）。"""
        _make_skill(db, "strict")
        agent._pending_skill_confirms["t2"] = {
            "skill_name": "strict", "confirmed": False, "event": asyncio.Event(),
        }
        assert agent.respond_skill_confirm("t2", False) is True
        skill = next(s for s in db.list_skills() if s["name"] == "strict")
        assert skill["needs_confirm"]

    def test_expired_pending_returns_false(self, agent: TaskAgent):
        """超时后 pending 已被 pop：点击落空必须返回 False 供前端提示。"""
        assert agent.respond_skill_confirm("t3", True) is False

    def test_db_failure_degrades_to_session_allow(self, agent: TaskAgent, monkeypatch):
        """落库失败降级为仅本次放行（不阻塞执行链），返回值仍为 True。"""
        def _boom(name: str) -> bool:
            raise RuntimeError("db down")
        monkeypatch.setattr(agent.db, "confirm_skill_by_name", _boom)
        agent._pending_skill_confirms["t4"] = {
            "skill_name": "x", "confirmed": False, "event": asyncio.Event(),
        }
        assert agent.respond_skill_confirm("t4", True) is True
