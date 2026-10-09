"""python/tests/test_report_error_event.py — 报告 error 事件终态落库单测（2.5 §3.8）。

背景：runner 异常路径 yield error 事件后正常返回（不再 raise），路由层 error
事件分支是失败终态的唯一落库点（状态 / error_message / 报告日志 / 失败通知 /
清暂停标志，统一走 _fail_report_terminal）；事件流之外的意外异常仍由
_run_report_generation 外层 except 兜底（共用同一实现，无双写）。

覆盖：
  - runner.run() 异常路径：yield error 后迭代正常结束（异常不传播给消费者）
  - error 事件分支：五项终态全部落库，且「报告生成异常」日志只写一次（无双写）
  - _fail_report_terminal 直测：except 兜底路径行为一致（清暂停标志）
"""
import asyncio
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from report_generator.runner import ProgressEvent, ReportRunner
from routers import reports as reports_module


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    # project_type="general" 无自动链接基础库（避免 in-memory DB 外键约束）
    return db.create_project(name="ErrorEvent Test", project_type="general")["id"]


@pytest.fixture
def report_id(db, project_id, tmp_path):
    """一条 generating 中、带遗留暂停标志的报告。"""
    rid = "r_err_evt"
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
        "progress, content_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, project_id, "due_diligence", "错误事件测试报告", "Word", "generating",
         30, str(tmp_path / "r_err_evt"), "2026-10-08T00:00:00", "2026-10-08T00:00:00"),
    )
    db.conn.commit()
    db.set_report_pause_requested(rid, True)  # 模拟暂停请求先于失败到达
    return rid


class _FakeRunner:
    """yield error 事件后正常返回的最小 runner 替身。"""

    def __init__(self, *args, **kwargs):
        pass

    async def run(self):
        yield ProgressEvent("error", {"message": "LLM 服务不可达"})


class TestRunnerErrorSemantics:
    def test_run_yields_error_then_stops_without_raising(self, tmp_path, monkeypatch):
        """runner 异常路径：yield error 后正常返回，异常不传播给消费者。"""
        runner = ReportRunner(
            template={}, variables={}, project_id="p1",
            search_tool=None, llm_client=None, llm_settings={},
            output_dir=str(tmp_path),
        )

        async def _boom():
            raise RuntimeError("章节致命失败")

        monkeypatch.setattr(runner, "_prepare", _boom)

        events = []

        async def collect():
            async for ev in runner.run():
                events.append(ev)

        asyncio.run(collect())  # 不抛异常即通过

        assert [ev.event_type for ev in events] == ["error"]
        assert "章节致命失败" in events[0].data["message"]


class TestErrorEventTerminal:
    def test_error_event_writes_full_terminal_state(self, db, project_id, report_id, monkeypatch, tmp_path):
        """error 事件分支：状态/错误信息/日志/通知/清标志五项全部落库，且无双写。"""
        monkeypatch.setattr(reports_module, "_get_search_tool", lambda db_, d: None)
        monkeypatch.setattr("report_generator.runner.ReportRunner", _FakeRunner)

        import deps
        monkeypatch.setattr(deps, "db", db)

        asyncio.run(reports_module._run_report_generation(
            report_id=report_id,
            template={"outline": []},
            variables={},
            project_id=project_id,
            data_dir=str(tmp_path),
            report_dir=str(tmp_path / "r_err_evt"),
            base_project_ids=[],
            section_internet={},
            internet_enabled=False,
        ))

        report = db.get_report(report_id)
        assert report["status"] == "error"
        assert report["error_message"] == "LLM 服务不可达"
        assert not db.get_report_pause_requested(report_id)

        logs = db.list_report_logs(report_id, limit=100)
        err_logs = [l for l in logs if "报告生成异常" in l["message"]]
        assert len(err_logs) == 1  # 唯一落库点：runner return 后 except 不再触发
        assert err_logs[0]["level"] == "error"
        assert "LLM 服务不可达" in err_logs[0]["message"]

        notif = db.conn.execute(
            "SELECT detail FROM notifications "
            "WHERE entity_type='report' AND entity_id=? AND kind='failed'",
            (report_id,),
        ).fetchone()
        assert notif is not None
        assert "LLM 服务不可达" in notif["detail"]

    def test_fail_report_terminal_clears_pause_flag(self, db, report_id):
        """except 兜底共用实现：同样清理遗留暂停标志（终态统一清标志不变量）。"""
        assert db.get_report_pause_requested(report_id)  # 前置：标志在位

        reports_module._fail_report_terminal(db, report_id, "意外异常")

        report = db.get_report(report_id)
        assert report["status"] == "error"
        assert report["error_message"] == "意外异常"
        assert not db.get_report_pause_requested(report_id)
