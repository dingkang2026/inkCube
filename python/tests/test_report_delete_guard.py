"""python/tests/test_report_delete_guard.py — delete_report 409 门回归测试。

修复回归（review M1 + except 链透传）：
  - generating 报告删除 → 409（后台 runner 仍持有文件句柄/DB 引用并烧 token）；
    此前 try 内抛出的 HTTPException 被函数尾部 except Exception 吞成 500，
    前端呈现「删除失败」而非「生成中不可删」——同文件其余端点均有
    except HTTPException: raise 先行分支，仅 delete_report 缺失。
  - 非 generating（done/error/paused）正常删除；孤儿 generating 由启动扫描
    置 error 后即可删（不在此测试范围，见 database 测试）。
"""
import asyncio
import json
import os
import sys
from unittest.mock import patch

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database  # noqa: E402
from routers.reports import delete_report  # noqa: E402


@pytest.fixture
def db_with_report():
    d = Database(db_path=":memory:")
    d.initialize()
    proj = d.create_project("P")
    rid = d.create_report(proj["id"], "tpl-1", "报告")["id"]  # status=generating
    yield d, rid
    d.conn.close()


def test_delete_generating_rejected_409(db_with_report):
    """generating 报告删除 → 409 响应（D1 契约：{error, code} 形态）。"""
    d, rid = db_with_report
    with patch("routers.reports.get_db", return_value=d):
        resp = asyncio.run(delete_report(rid))
    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body["code"] == "REPORT_GENERATING"
    # 报告未被删除
    assert d.get_report(rid) is not None


def test_delete_done_report_succeeds(db_with_report):
    """done 报告正常删除。"""
    d, rid = db_with_report
    d.update_report_status(rid, "done")
    with patch("routers.reports.get_db", return_value=d):
        result = asyncio.run(delete_report(rid))
    assert result["success"] is True
    assert d.get_report(rid) is None


def test_delete_paused_report_succeeds(db_with_report):
    """paused 报告可删（暂停后放弃场景）。"""
    d, rid = db_with_report
    d.update_report_status(rid, "paused")
    with patch("routers.reports.get_db", return_value=d):
        result = asyncio.run(delete_report(rid))
    assert result["success"] is True
    assert d.get_report(rid) is None
