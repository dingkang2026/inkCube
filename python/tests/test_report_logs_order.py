"""python/tests/test_report_logs_order.py — 进展日志排序单测（§1.3）。

覆盖 Database.list_report_logs：
  - 时间正序返回（旧上新下，时间轴视图；详情页配合自动滚底看最新）
  - limit 语义 = 取最新 N 条（非最旧 N 条），再按正序输出
  - 同 created_at 的多条按插入序稳定排列（轮询重取不抖动）
  - report_id 隔离
"""
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database  # noqa: E402


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    return db.create_project(name="Logs Order Test", project_type="general")["id"]


@pytest.fixture
def report_id(db, project_id):
    now = "2026-01-01T00:00:00"
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
        "progress, content_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r_logs", project_id, "due_diligence", "日志排序", "Word", "generating",
         0, "/tmp/r_logs", now, now),
    )
    db.conn.commit()
    return "r_logs"


def _insert_log(db, report_id, log_id, message, created_at):
    """直接插入日志（控制 created_at；id 由调用方保证唯一）。"""
    db.conn.execute(
        "INSERT INTO report_logs (id, report_id, level, message, created_at) "
        "VALUES (?, ?, 'info', ?, ?)",
        (log_id, report_id, message, created_at),
    )


class TestListReportLogsOrder:
    def test_returns_ascending_order(self, db, report_id):
        """时间正序：旧上新下（回归：旧实现 DESC 最新在上，与自动滚底矛盾）。"""
        for i, ts in enumerate(["2026-01-01T00:00:01", "2026-01-01T00:00:02",
                                "2026-01-01T00:00:03"], start=1):
            _insert_log(db, report_id, f"l{i}", f"日志{i}", ts)

        logs = db.list_report_logs(report_id)

        assert [log["message"] for log in logs] == ["日志1", "日志2", "日志3"]

    def test_limit_takes_latest_not_oldest(self, db, report_id):
        """limit = 取最新 N 条再正序输出；不得因 ASC 化退化为取最旧 N 条。"""
        for i in range(5):
            ts = f"2026-01-01T00:00:0{i + 1}"
            _insert_log(db, report_id, f"l{i + 1}", f"日志{i + 1}", ts)

        logs = db.list_report_logs(report_id, limit=3)

        assert [log["message"] for log in logs] == ["日志3", "日志4", "日志5"]

    def test_same_timestamp_stable_insert_order(self, db, report_id):
        """同 created_at 的多条按插入序稳定排列（轮询重取间不抖动）。"""
        same_ts = "2026-01-01T00:00:01.000000"
        _insert_log(db, report_id, "l1", "开始生成", same_ts)
        _insert_log(db, report_id, "l2", "开始生成章节", same_ts)
        _insert_log(db, report_id, "l3", "章节完成", same_ts)

        logs = db.list_report_logs(report_id)

        assert [log["message"] for log in logs] == ["开始生成", "开始生成章节", "章节完成"]
        # 重取（轮询语义）顺序一致
        assert [log["message"] for log in db.list_report_logs(report_id)] == \
            ["开始生成", "开始生成章节", "章节完成"]

    def test_report_isolation(self, db, project_id, report_id):
        db.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
            "progress, content_path, created_at, updated_at) "
            "VALUES ('r_other', ?, 'due_diligence', '其他', 'Word', 'generating', "
            "0, '/tmp/r_other', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            (project_id,),
        )
        db.conn.commit()
        _insert_log(db, report_id, "l1", "本报告日志", "2026-01-01T00:00:01")
        _insert_log(db, "r_other", "l2", "其他报告日志", "2026-01-01T00:00:02")

        assert [log["message"] for log in db.list_report_logs(report_id)] == ["本报告日志"]
        assert [log["message"] for log in db.list_report_logs("r_other")] == ["其他报告日志"]
