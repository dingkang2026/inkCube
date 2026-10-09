"""python/tests/test_reports_recovery.py — 报告崩溃恢复单测（§3.8.3）。

覆盖 Database.recover_stale_generating_reports：
  - 超时（>timeout）的 generating 报告 → 标记 error + error_message + 返回 id
  - 未超时（≤timeout）的 generating 报告 → 保持 generating 不动（timeout>0 语义，
    仅测试/特殊场景；启动扫描默认 timeout=0，进程重启后一切 generating 均为孤儿）
  - 非 generating（done/error）报告 → 不受影响
  - updated_at 无法解析 → 保守判定为陈旧，标记 error
  - 无 generating 报告 → 返回空列表
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    # project_type="general" 无自动链接基础库（避免 in-memory DB 未播种 bl-* 的外键约束）
    return db.create_project(name="Recovery Test", project_type="general")["id"]


def _insert_report(db, project_id, report_id, status, updated_at, error_message=None):
    """直接插入一条报告记录（控制 status 与 updated_at）。"""
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
        "progress, content_path, created_at, updated_at, error_message) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (report_id, project_id, "due_diligence", f"报告{report_id}", "Word", status,
         0, f"/tmp/{report_id}", updated_at, updated_at, error_message),
    )
    db.conn.commit()


class TestRecoverStaleGeneratingReports:
    def test_stale_generating_marked_error(self, db, project_id):
        old = (datetime.now() - timedelta(minutes=10)).isoformat()
        _insert_report(db, project_id, "r_stale", "generating", old)

        recovered = db.recover_stale_generating_reports(timeout_minutes=5)

        assert recovered == ["r_stale"]
        report = db.get_report("r_stale")
        assert report["status"] == "error"
        assert report["error_message"]  # 非空错误说明

    def test_recent_generating_untouched(self, db, project_id):
        recent = (datetime.now() - timedelta(minutes=1)).isoformat()
        _insert_report(db, project_id, "r_recent", "generating", recent)

        recovered = db.recover_stale_generating_reports(timeout_minutes=5)

        assert recovered == []
        assert db.get_report("r_recent")["status"] == "generating"

    def test_non_generating_untouched(self, db, project_id):
        old = (datetime.now() - timedelta(minutes=10)).isoformat()
        _insert_report(db, project_id, "r_done", "done", old)
        _insert_report(db, project_id, "r_err", "error", old, error_message="先前错误")

        recovered = db.recover_stale_generating_reports(timeout_minutes=5)

        assert recovered == []
        assert db.get_report("r_done")["status"] == "done"
        # error 报告不被覆盖（保留原错误信息）
        assert db.get_report("r_err")["status"] == "error"
        assert db.get_report("r_err")["error_message"] == "先前错误"

    def test_unparseable_timestamp_treated_as_stale(self, db, project_id):
        _insert_report(db, project_id, "r_bad", "generating", "not-a-timestamp")

        recovered = db.recover_stale_generating_reports(timeout_minutes=5)

        assert recovered == ["r_bad"]
        assert db.get_report("r_bad")["status"] == "error"

    def test_mixed_only_stale_returned(self, db, project_id):
        old = (datetime.now() - timedelta(minutes=30)).isoformat()
        recent = datetime.now().isoformat()
        _insert_report(db, project_id, "r_old1", "generating", old)
        _insert_report(db, project_id, "r_old2", "generating", old)
        _insert_report(db, project_id, "r_new", "generating", recent)

        recovered = db.recover_stale_generating_reports(timeout_minutes=5)

        assert set(recovered) == {"r_old1", "r_old2"}
        assert db.get_report("r_new")["status"] == "generating"

    def test_no_generating_returns_empty(self, db, project_id):
        assert db.recover_stale_generating_reports(timeout_minutes=5) == []

    def test_custom_timeout_boundary(self, db, project_id):
        """恰好超过自定义 timeout 的报告被标记。"""
        ts = (datetime.now() - timedelta(minutes=3)).isoformat()
        _insert_report(db, project_id, "r_3min", "generating", ts)

        # timeout=5 → 3min 不算陈旧
        assert db.recover_stale_generating_reports(timeout_minutes=5) == []
        # timeout=2 → 3min 算陈旧
        assert db.recover_stale_generating_reports(timeout_minutes=2) == ["r_3min"]

    def test_zero_timeout_marks_all_orphans(self, db, project_id):
        """timeout=0（启动扫描默认，§3.8.3）：进程重启后 runner 必然已死，
        刚更新的 generating 同为孤儿——不设时间阈值，避免「假生成中」窗口
        （进度永不推进、暂停无人消费）。"""
        just_now = datetime.now().isoformat()
        _insert_report(db, project_id, "r_just", "generating", just_now)

        recovered = db.recover_stale_generating_reports()

        assert recovered == ["r_just"]
        assert db.get_report("r_just")["status"] == "error"
