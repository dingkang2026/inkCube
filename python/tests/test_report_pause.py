"""python/tests/test_report_pause.py — 报告级暂停（2.5 §3.1 修订）。

覆盖：pause 端点（404 / 409 / 写标志）、DB 标志读写、retry 从 paused 恢复
（resume 保留进度 + 清标志；restart 删 checkpoint 重置进度）、Runner 章节
边界协作暂停（当前章节完成 → yield paused → 终止推进）与无暂停时行为不变。

复审补充（顺序 5）：晚到请求 409 不落标志、非法 mode 400、模板 404 不留
generating 假态（重排回归）、paused 隔离（不占并发 + 崩溃恢复不误判）、
post_body 边界暂停、section_done 绝对完成数 + 消费者进度单调（r2 回归）。
"""
import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database


class TestPauseFlagDb:
    def test_flag_roundtrip(self):
        db = Database(db_path=":memory:")
        db.initialize()
        proj = db.create_project("P")
        report = db.create_report(proj["id"], "tpl", "T")
        rid = report["id"]
        assert db.get_report_pause_requested(rid) is False
        db.set_report_pause_requested(rid, True)
        assert db.get_report_pause_requested(rid) is True
        db.set_report_pause_requested(rid, False)
        assert db.get_report_pause_requested(rid) is False

    def test_missing_report_reads_false(self):
        db = Database(db_path=":memory:")
        db.initialize()
        assert db.get_report_pause_requested("nope") is False


class TestPauseEndpoint:
    def _seed(self, monkeypatch, status="generating"):
        db = Database(db_path=":memory:")
        db.initialize()
        proj = db.create_project("P")
        report = db.create_report(proj["id"], "tpl", "T")
        rid = report["id"]
        if status != "generating":
            db.update_report_status(rid, status, 40)
        import routers.reports as reports
        monkeypatch.setattr(reports, "get_db", lambda: db)
        return reports, db, rid

    def test_pause_writes_flag_when_generating(self, monkeypatch):
        reports, db, rid = self._seed(monkeypatch)
        result = asyncio.run(reports.pause_report(rid))
        assert result["pause_requested"] is True
        assert db.get_report_pause_requested(rid) is True

    def test_pause_404_when_missing(self, monkeypatch):
        reports, _, _ = self._seed(monkeypatch)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(reports.pause_report("ghost"))
        assert ei.value.status_code == 404

    def test_pause_409_when_not_generating(self, monkeypatch):
        """暂停只对在跑的报告有意义：done / error / paused 均 409，且不落标志（晚到请求）。"""
        for status in ("done", "error", "paused"):
            reports, db, rid = self._seed(monkeypatch, status=status)
            with pytest.raises(HTTPException) as ei:
                asyncio.run(reports.pause_report(rid))
            assert ei.value.status_code == 409
            assert db.get_report_pause_requested(rid) is False


class TestRetryFromPaused:
    class FakeBT:
        def __init__(self):
            self.tasks = []

        def add_task(self, fn, **kwargs):
            self.tasks.append((fn, kwargs))

    def _seed(self, monkeypatch, tmp_path, status="paused"):
        db = Database(db_path=":memory:")
        db.initialize()
        proj = db.create_project("P")
        report = db.create_report(proj["id"], "tpl", "T", content_path=str(tmp_path))
        rid = report["id"]
        db.update_report_status(rid, status, 45)
        db.set_report_pause_requested(rid, True)
        # checkpoint 文件（restart 应删除）
        ckpt = tmp_path / "checkpoint.json"
        ckpt.write_text("{}", encoding="utf-8")

        import routers.reports as reports
        monkeypatch.setattr(reports, "get_db", lambda: db)
        monkeypatch.setattr(reports, "ensure_ready", lambda kind: None)

        class FakeLoader:
            def get_template(self, template_id):
                return {"template_id": template_id, "global_requirements": []}

        monkeypatch.setattr(reports, "get_loader", lambda: FakeLoader())
        return reports, db, rid, ckpt

    def test_resume_keeps_progress_and_clears_flag(self, monkeypatch, tmp_path):
        reports, db, rid, ckpt = self._seed(monkeypatch, tmp_path)
        result = asyncio.run(reports.retry_report(rid, background_tasks=self.FakeBT(), req=None))
        assert result["mode"] == "resume"
        row = db.get_report(rid)
        assert row["status"] == "generating"
        assert row["progress"] == 45  # paused 续跑保留原进度
        assert db.get_report_pause_requested(rid) is False  # 恢复清标志
        assert ckpt.exists()  # resume 不删 checkpoint

    def test_restart_deletes_checkpoint_and_resets_progress(self, monkeypatch, tmp_path):
        reports, db, rid, ckpt = self._seed(monkeypatch, tmp_path)
        from routers.reports import RetryReportRequest
        result = asyncio.run(
            reports.retry_report(rid, background_tasks=self.FakeBT(), req=RetryReportRequest(mode="restart"))
        )
        assert result["mode"] == "restart"
        row = db.get_report(rid)
        assert row["progress"] == 0
        assert not ckpt.exists()
        assert db.get_report_pause_requested(rid) is False

    def test_retry_rejected_400_for_done(self, monkeypatch, tmp_path):
        reports, _, rid, _ = self._seed(monkeypatch, tmp_path, status="done")
        with pytest.raises(HTTPException) as ei:
            asyncio.run(reports.retry_report(rid, background_tasks=self.FakeBT(), req=None))
        assert ei.value.status_code == 400

    def test_retry_400_for_generating(self, monkeypatch, tmp_path):
        """晚到的 retry（报告仍在生成）→ 400，状态不被触碰。"""
        reports, db, rid, _ = self._seed(monkeypatch, tmp_path, status="done")
        db.update_report_status(rid, "generating", 60)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(reports.retry_report(rid, background_tasks=self.FakeBT(), req=None))
        assert ei.value.status_code == 400
        assert db.get_report(rid)["status"] == "generating"

    def test_retry_invalid_mode_400(self, monkeypatch, tmp_path):
        """mode 枚举校验：非法值 400，且不落状态变更。"""
        reports, db, rid, _ = self._seed(monkeypatch, tmp_path)
        from routers.reports import RetryReportRequest
        with pytest.raises(HTTPException) as ei:
            asyncio.run(
                reports.retry_report(rid, background_tasks=self.FakeBT(), req=RetryReportRequest(mode="bogus"))
            )
        assert ei.value.status_code == 400
        assert db.get_report(rid)["status"] == "paused"

    def test_retry_template_missing_keeps_paused_state(self, monkeypatch, tmp_path):
        """重排回归：模板 404 发生在状态变更前——报告保持 paused 稳态与标志。"""
        reports, db, rid, _ = self._seed(monkeypatch, tmp_path)
        import routers.reports as reports_mod

        class NoneLoader:
            def get_template(self, template_id):
                return None

        monkeypatch.setattr(reports_mod, "get_loader", lambda: NoneLoader())
        with pytest.raises(HTTPException) as ei:
            asyncio.run(reports.retry_report(rid, background_tasks=self.FakeBT(), req=None))
        assert ei.value.status_code == 404
        row = db.get_report(rid)
        assert row["status"] == "paused"
        assert row["progress"] == 45
        assert db.get_report_pause_requested(rid) is True


class TestRunnerSectionBoundary:
    @staticmethod
    def _make_runner(tmp_path, monkeypatch, sections=None, **kwargs):
        """构造绕过 LLM / 检索的最小 Runner（仅验证事件流与暂停逻辑）。

        sections 可注入自定义章节清单（如含 post_body 的组合），默认两节 standard。
        """
        import report_generator.runner as runner_mod
        from report_generator.runner import ReportRunner

        async def fake_retrieve(**kwargs):
            class R:
                cards: list = []
                warnings: list = []
                skip = False
                is_degraded = False
                sufficiency = None

            return R(), {}

        monkeypatch.setattr(runner_mod, "retrieve_for_section", fake_retrieve)

        runner = ReportRunner(
            template={"global_requirements": []},
            variables={},
            project_id="p1",
            search_tool=None,
            llm_client=None,
            llm_settings={},
            output_dir=str(tmp_path),
            **kwargs,
        )

        async def fake_prepare(self):
            self.report_title = "T"
            self.sections = sections if sections is not None else [
                {"section_id": "s1", "title": "S1", "compose_mode": "standard"},
                {"section_id": "s2", "title": "S2", "compose_mode": "standard"},
            ]

        async def fake_extract(self):
            return ""

        async def fake_gen(self, **kwargs):
            return "content", "stop"  # (output, finish_reason)——§3.9 截断可观测签名

        async def fake_sum(self, text, max_words=None):
            return "digest"

        runner._prepare = fake_prepare.__get__(runner)
        runner._generate_with_retry = fake_gen.__get__(runner)
        runner._summarize = fake_sum.__get__(runner)
        runner._extract_numbers = fake_extract.__get__(runner)
        return runner

    def test_run_yields_paused_at_section_boundary(self, monkeypatch, tmp_path):
        """章节边界协作暂停：s1 完成后请求到达 → yield paused 并终止推进。"""
        state = {"requested": False}
        runner = self._make_runner(tmp_path, monkeypatch, pause_check=lambda: state["requested"])

        events: list[tuple[str, dict]] = []

        async def consume():
            async for ev in runner.run():
                events.append((ev.event_type, ev.data))
                if ev.event_type == "section_done":
                    state["requested"] = True  # s1 完成后请求暂停

        asyncio.run(consume())

        kinds = [e[0] for e in events]
        assert kinds == ["section_start", "section_done", "paused"]
        # section_done 携带绝对完成数（r2），paused 事件同理
        assert events[1][1]["completed"] == 1
        assert events[-1][1] == {"completed": 1, "total": 2}
        # checkpoint 已落盘（s1 内容不丢，恢复可续跑）
        assert (tmp_path / "checkpoint.json").exists()
        # s2 未开始
        assert "s2" not in runner.outputs

    def test_run_completes_without_pause(self, monkeypatch, tmp_path):
        """无暂停请求（含未注入探针）时行为不变：两节跑完正常 complete。"""
        runner = self._make_runner(tmp_path, monkeypatch)
        runner._write_final_artifacts = lambda: tmp_path / "content.md"

        events: list[tuple[str, dict]] = []

        async def consume():
            async for ev in runner.run():
                events.append((ev.event_type, ev.data))

        asyncio.run(consume())

        kinds = [e[0] for e in events]
        assert kinds == ["section_start", "section_done", "section_start", "section_done", "progress", "complete"]
        assert set(runner.outputs) == {"s1", "s2"}
        # 绝对完成数随进度推进（r2）：s1 → 1，s2 → 2
        assert events[1][1]["completed"] == 1
        assert events[3][1]["completed"] == 2

    def test_run_yields_paused_at_post_body_boundary(self, monkeypatch, tmp_path):
        """post_body 边界同样响应暂停：两遍循环统一在章节入口检查（2.5 §3.1 修订）。"""
        state = {"requested": False}
        sections = [
            {"section_id": "s1", "title": "S1", "compose_mode": "standard"},
            {"section_id": "s2", "title": "S2", "compose_mode": "post_body"},
        ]
        runner = self._make_runner(tmp_path, monkeypatch, sections=sections, pause_check=lambda: state["requested"])

        events: list[tuple[str, dict]] = []

        async def consume():
            async for ev in runner.run():
                events.append((ev.event_type, ev.data))
                if ev.event_type == "section_done":
                    state["requested"] = True  # s1（标准节）完成后请求暂停

        asyncio.run(consume())

        kinds = [e[0] for e in events]
        # 标准节正常完成 → 进入 post_body 循环时边界检查生效
        assert kinds == ["section_start", "section_done", "paused"]
        assert events[-1][1] == {"completed": 1, "total": 2}
        assert "s2" not in runner.outputs
        assert (tmp_path / "checkpoint.json").exists()


class TestPausedIsolation:
    """paused 稳态隔离：不占并发名额、不被崩溃恢复误判为中断。"""

    def test_paused_not_counted_as_active(self):
        """count_active_reports 只扫 generating：paused 不占并发名额，可立即 retry。"""
        db = Database(db_path=":memory:")
        db.initialize()
        proj = db.create_project("P")
        report = db.create_report(proj["id"], "tpl", "T")
        db.update_report_status(report["id"], "paused", 45)
        assert db.count_active_reports() == 0

    def test_crash_recovery_does_not_touch_paused(self):
        """recover_stale_generating_reports 只针对 generating：paused 稳态不被误判为中断。"""
        db = Database(db_path=":memory:")
        db.initialize()
        proj = db.create_project("P")
        report = db.create_report(proj["id"], "tpl", "T")
        rid = report["id"]
        db.update_report_status(rid, "paused", 45)
        # timeout_minutes=0 → 全部 generating 行均视为超时；paused 仍不在扫描范围
        touched = db.recover_stale_generating_reports(timeout_minutes=0)
        assert rid not in touched
        assert db.get_report(rid)["status"] == "paused"


class TestConsumerProgress:
    """_run_report_generation 进度消费回归（r2）：进度取事件绝对完成数，resume 后不回退。"""

    @staticmethod
    def _seed_consumer(monkeypatch, event_tuples, outputs=None):
        import routers.reports as reports
        import report_generator.runner as runner_mod
        import llm.client as llm_client_mod
        from report_generator.runner import ProgressEvent

        db = Database(db_path=":memory:")
        db.initialize()
        proj = db.create_project("P")
        report = db.create_report(proj["id"], "tpl", "T")
        rid = report["id"]
        db.update_report_status(rid, "generating", 45)  # 模拟 resume 后起始进度

        events = [ProgressEvent(t, d) for t, d in event_tuples]

        class FakeRunner:
            def __init__(self, **kwargs):
                self.outputs = outputs or {}

            async def run(self):
                for ev in events:
                    yield ev

        # 消费者在函数体内延迟导入：补丁必须落在源模块属性上
        monkeypatch.setattr(runner_mod, "ReportRunner", FakeRunner)
        monkeypatch.setattr(llm_client_mod, "LLMClient", lambda settings: object())
        monkeypatch.setattr(reports, "get_db", lambda: db)
        monkeypatch.setattr(reports, "_get_search_tool", lambda db_, data_dir: None)
        return reports, db, rid

    @staticmethod
    def _consume(reports, rid):
        asyncio.run(reports._run_report_generation(
            report_id=rid, template={}, variables={}, project_id="p1",
            data_dir="d", report_dir="rd", base_project_ids=[], section_internet={},
        ))

    def test_section_done_progress_uses_absolute_completed(self, monkeypatch):
        """r2 回归：resume 后首事件 completed=6/10 → 54；旧实现本地计数 1/10 → 9（回退）。"""
        reports, db, rid = self._seed_consumer(
            monkeypatch,
            [("section_done", {"section_id": "s6", "title": "S6", "degraded": False, "total": 10, "completed": 6})],
            outputs={"s6": "c6"},
        )
        self._consume(reports, rid)
        row = db.get_report(rid)
        assert row["status"] == "generating"
        assert row["progress"] == 54

    def test_paused_event_persists_state_and_clears_flag(self, monkeypatch):
        """paused 事件：状态与进度落库（4/10 → 36 ≠ 起始 45，证明写入发生）+ 消费即清标志。"""
        reports, db, rid = self._seed_consumer(monkeypatch, [("paused", {"completed": 4, "total": 10})])
        db.set_report_pause_requested(rid, True)
        self._consume(reports, rid)
        row = db.get_report(rid)
        assert row["status"] == "paused"
        assert row["progress"] == 36  # 4/10 × 90
        assert db.get_report_pause_requested(rid) is False
