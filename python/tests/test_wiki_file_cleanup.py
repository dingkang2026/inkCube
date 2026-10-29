"""python/tests/test_wiki_file_cleanup.py — 源文件删除的 Wiki 清理回归（B2，2026-10-28）.

覆盖四组：
  1. TestSingleSourceRemoval — 单来源 compiled 页随源文件删除（替代原置 draft：
     draft 无消费方与重编译路径——删除不点火、前端零消费、报告/Chat 不采信，
     滞留即死状态且内容残留可读），change_log 留 source_removed 时间线痕迹
  2. TestMultiSourceKeep     — 多来源页删一来源仍 compiled（剩余来源仍有效，
     内容随下次编译自然更新）
  3. TestUserEditedKeep      — user_edited 页永不动（用户手工内容）
  4. TestCascadeSemantics    — 复用 delete_wiki_page 级联语义：死链 refs 剥离、
     source_pages 登记清理
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database


@pytest.fixture()
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    yield database
    database.conn.close()


def _seed_compiled_source(db, pid, fid, filename="a.pdf", page_key="alpha",
                          status="compiled", refs=None):
    """模拟一次编译产物三项齐备：wiki_pages + page_sources + source_pages 登记。"""
    page = db.create_or_update_wiki_page(
        pid, page_key, f"页-{page_key}", "编译内容", status=status, refs=refs)
    db.add_wiki_page_source(page["id"], fid)
    db.create_file(project_id=pid, filename=filename, file_type=".pdf",
                   file_size=1, file_hash=f"h_{fid}", file_id=fid)
    db.update_file_status(fid, "done")
    db.create_or_update_source_page(
        project_id=pid, file_id=fid, status="ingested", pages_touched=[page_key])
    return page


class TestSingleSourceRemoval:
    """单来源 compiled 页：删除文件即删页 + source_removed 时间线痕迹。"""

    def test_page_removed_with_trace(self, db):
        pid = db.create_project("P")["id"]
        _seed_compiled_source(db, pid, "f1", page_key="alpha")

        counts = db.cleanup_wiki_data_for_file(pid, "f1")

        assert db.get_wiki_page(pid, "alpha") is None  # 删页，非置 draft
        assert counts["pages_removed"] == 1
        logs = db.list_wiki_change_log(pid, limit=0)
        removed = [e for e in logs if e["action"] == "source_removed"]
        assert len(removed) == 1
        assert removed[0]["wiki_page_id"] is None  # 删页后补记（级联清该页日志）
        assert removed[0]["affected_page_keys"] == ["alpha"]
        assert "alpha" in removed[0]["summary"]  # 摘要含页题，时间线可读

    def test_source_pages_record_cleaned(self, db):
        """顺带覆盖步骤 1-3：摄入登记/溯源/review 解除关联。"""
        pid = db.create_project("P")["id"]
        _seed_compiled_source(db, pid, "f1", page_key="alpha")

        counts = db.cleanup_wiki_data_for_file(pid, "f1")

        assert db.get_source_page(pid, "f1") is None
        assert counts["source_pages"] == 1
        assert counts["page_sources"] == 1


class TestMultiSourceKeep:
    """多来源页：删一来源仍 compiled，不误删。"""

    def test_page_keeps_compiled_after_one_source_removed(self, db):
        pid = db.create_project("P")["id"]
        page = db.create_or_update_wiki_page(pid, "alpha", "页-alpha", "编译内容",
                                             status="compiled")
        db.add_wiki_page_source(page["id"], "f1")
        db.add_wiki_page_source(page["id"], "f2")
        for fid, fn in (("f1", "a.pdf"), ("f2", "b.pdf")):
            db.create_file(project_id=pid, filename=fn, file_type=".pdf",
                           file_size=1, file_hash=f"h_{fid}", file_id=fid)
            db.update_file_status(fid, "done")
            db.create_or_update_source_page(
                project_id=pid, file_id=fid, status="ingested",
                pages_touched=["alpha"])

        counts = db.cleanup_wiki_data_for_file(pid, "f1")

        kept = db.get_wiki_page(pid, "alpha")
        assert kept is not None and kept["status"] == "compiled"
        assert counts["pages_removed"] == 0


class TestUserEditedKeep:
    """user_edited 页永不删（即变孤儿来源也保留用户手工内容）。"""

    def test_user_edited_page_never_touched(self, db):
        pid = db.create_project("P")["id"]
        _seed_compiled_source(db, pid, "f1", page_key="alpha", status="user_edited")

        counts = db.cleanup_wiki_data_for_file(pid, "f1")

        kept = db.get_wiki_page(pid, "alpha")
        assert kept is not None and kept["status"] == "user_edited"
        assert counts["pages_removed"] == 0


class TestCascadeSemantics:
    """复用 delete_wiki_page 级联：他页死链 refs 剥离。"""

    def test_dead_link_refs_stripped_from_surviving_pages(self, db):
        pid = db.create_project("P")["id"]
        _seed_compiled_source(db, pid, "f1", page_key="alpha")
        beta = db.create_or_update_wiki_page(
            pid, "beta", "页-beta", "引用 [[alpha]]", status="compiled",
            refs=["alpha"])
        db.add_wiki_page_source(beta["id"], "f2")  # beta 另有来源，自身保留
        db.create_file(project_id=pid, filename="b.pdf", file_type=".pdf",
                       file_size=1, file_hash="h_f2", file_id="f2")
        db.update_file_status("f2", "done")
        db.create_or_update_source_page(
            project_id=pid, file_id="f2", status="ingested", pages_touched=["beta"])

        db.cleanup_wiki_data_for_file(pid, "f1")

        assert db.get_wiki_page(pid, "alpha") is None
        assert db.get_wiki_page(pid, "beta")["refs"] == []  # 死链已剥
