"""表单模板 / 填表实例列表分页回归（0.1dev-guide 列表分页统一约定）。

回归背景（2026-10）：
- GET /forms/templates 此前纯客户端过滤、无上限；新增 q（name LIKE）+
  status 服务端筛选下沉（对齐 knowledge files 范式）+ limit/offset/total；
- GET /fills 增 limit/offset/has_more/total；usage enrichment 只算当前页。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.forms as forms
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def routed(db, monkeypatch):
    monkeypatch.setattr(forms, "get_db", lambda: db)
    return db


def _seed_templates(database, spec):
    """按 {name: (status, seq)} 形态造模板；created_at 秒级递增保证排序确定。"""
    seq = 0
    ids = []
    for name, status in spec:
        tid = f"tpl{seq:03d}"
        database.create_form_template(tid, name, "upload", f"C:/tmp/{tid}.docx")
        if status != "pending":
            database.update_form_template_status(tid, status)
        ts = f"2026-01-01T00:00:{seq:02d}"
        database.conn.execute(
            "UPDATE form_templates SET created_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, tid),
        )
        ids.append(tid)
        seq += 1
    database.conn.commit()
    return ids


class TestFormTemplatesPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        """limit=0（默认）→ 全量：创建弹窗选择器等既有调用方不受影响。"""
        _seed_templates(routed, [(f"模板-{i}", "success") for i in range(3)])
        result = asyncio.run(
            forms.list_form_templates(q=None, status=None, limit=0))
        assert len(result["templates"]) == 3
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        _seed_templates(routed, [(f"模板-{i}", "success") for i in range(5)])
        result = asyncio.run(
            forms.list_form_templates(q=None, status=None, limit=3, offset=0))
        assert len(result["templates"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, routed):
        _seed_templates(routed, [(f"模板-{i}", "success") for i in range(25)])
        page1 = asyncio.run(
            forms.list_form_templates(q=None, status=None, limit=20, offset=0))
        page2 = asyncio.run(
            forms.list_form_templates(q=None, status=None, limit=20, offset=20))
        assert len(page1["templates"]) == 20
        assert page1["has_more"] is True
        assert len(page2["templates"]) == 5
        ids1 = {t["id"] for t in page1["templates"]}
        ids2 = {t["id"] for t in page2["templates"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 25
        assert page2["total"] == 25

    def test_limit_offset_binding_order(self, routed):
        """LIMIT ?/OFFSET ? 占位符顺序与参数 append 顺序一致（绑定颠倒防回归）。"""
        _seed_templates(routed, [(f"模板-{i}", "success") for i in range(5)])
        page2 = asyncio.run(
            forms.list_form_templates(q=None, status=None, limit=2, offset=2))
        assert [t["name"] for t in page2["templates"]] == ["模板-2", "模板-1"]

    def test_q_filters_by_name(self, routed):
        _seed_templates(routed, [
            ("合同-封面", "success"), ("合同-附件", "success"), ("发票", "pending"),
        ])
        result = asyncio.run(
            forms.list_form_templates(q="合同", status=None, limit=0, offset=0))
        assert {t["name"] for t in result["templates"]} == {"合同-封面", "合同-附件"}
        assert result["total"] == 2

    def test_status_filters(self, routed):
        _seed_templates(routed, [
            ("a", "success"), ("b", "pending"), ("c", "pending"), ("d", "failed"),
        ])
        result = asyncio.run(
            forms.list_form_templates(q=None, status="pending", limit=0, offset=0))
        assert {t["name"] for t in result["templates"]} == {"b", "c"}
        assert result["total"] == 2

    def test_q_and_status_combined(self, routed):
        _seed_templates(routed, [
            ("合同-封面", "success"), ("合同-附件", "pending"), ("发票", "pending"),
        ])
        result = asyncio.run(
            forms.list_form_templates(q="合同", status="pending", limit=0, offset=0))
        assert [t["name"] for t in result["templates"]] == ["合同-附件"]
        assert result["total"] == 1

    def test_filter_combined_with_pagination(self, routed):
        """筛选口径下分页：success 25 条（pending 20 条不计），第 2 页取剩余 5 条。"""
        spec = [(f"成功-{i}", "success") for i in range(25)]
        spec += [(f"待定-{i}", "pending") for i in range(20)]
        _seed_templates(routed, spec)
        page2 = asyncio.run(
            forms.list_form_templates(q=None, status="success", limit=20, offset=20))
        assert len(page2["templates"]) == 5
        assert page2["total"] == 25
        assert all(t["status"] == "success" for t in page2["templates"])


class TestFormFillsPagination:
    def test_limit_zero_returns_all_backward_compatible(self, routed):
        pid = routed.create_project("P")["id"]
        routed.create_form_template("t0", "T", "upload", "C:/tmp/t0.docx")
        for i in range(3):
            routed.create_form_fill(pid, "t0", f"实例-{i}")
        result = asyncio.run(forms.list_form_fills(pid, limit=0))
        assert len(result["fills"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        pid = routed.create_project("P")["id"]
        for i in range(5):
            routed.create_form_fill(pid, "t", f"实例-{i}")
        result = asyncio.run(forms.list_form_fills(pid, limit=3, offset=0))
        assert len(result["fills"]) == 3
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_pages(self, routed):
        pid = routed.create_project("P")["id"]
        for i in range(7):
            routed.create_form_fill(pid, "t", f"实例-{i}")
        page1 = asyncio.run(forms.list_form_fills(pid, limit=3, offset=0))
        page2 = asyncio.run(forms.list_form_fills(pid, limit=3, offset=3))
        ids1 = {f["id"] for f in page1["fills"]}
        ids2 = {f["id"] for f in page2["fills"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 6
        assert page1["total"] == 7

    def test_dangling_template_name_null(self, routed):
        """模板随时可删（2.4 不变量 11）：悬空实例可见，template_name 为 NULL。"""
        pid = routed.create_project("P")["id"]
        routed.create_form_template("t0", "T", "upload", "C:/tmp/t0.docx")
        routed.create_form_fill(pid, "t0", "实例-A")
        routed.delete_form_template("t0")
        result = asyncio.run(forms.list_form_fills(pid, limit=0))
        assert len(result["fills"]) == 1
        assert result["fills"][0]["template_name"] is None
