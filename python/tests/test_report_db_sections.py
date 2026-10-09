"""python/tests/test_report_db_sections.py — report_sections 唯一性与迁移单测。

修复回归（review H2/L2）：
  - create_report_section UPSERT 幂等：restart/resume 重复落库返回同一行，
    不产生重复行（此前同一章节出现两行，前端章节列表重复渲染）
  - UPSERT 语义：冲突重置 pending / 刷新标题；created_at 保留首建时间
  - UNIQUE(report_id, section_id) 约束真实存在（与 wiki_pages 同模式）
  - 存量库迁移：重复行按 updated_at 保留最新一条；迁移幂等
  - output_format 存量值归一：历史 IPC 写入的 'Word' → 'docx'
"""
import os
import sqlite3
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database  # noqa: E402


@pytest.fixture
def db():
    d = Database(db_path=":memory:")
    d.initialize()
    yield d
    d.conn.close()


@pytest.fixture
def report_id(db):
    proj = db.create_project("测试项目")
    return db.create_report(proj["id"], "tpl-1", "测试报告")["id"]


# ── UPSERT 幂等（restart/resume 防重复行）───────────────────


class TestCreateReportSectionUpsert:
    def test_upsert_idempotent_same_pk(self, db, report_id):
        """重复落库返回同一行（同 pk）：resume 重复 section_done 事件不产生重复行。"""
        first = db.create_report_section(report_id, "s1", "标题v1")
        second = db.create_report_section(report_id, "s1", "标题v2")
        assert first["id"] == second["id"]
        rows = db.list_report_sections(report_id)
        assert len(rows) == 1
        # 冲突语义：刷新标题 + 重置 pending
        assert rows[0]["title"] == "标题v2"
        assert rows[0]["status"] == "pending"
        # 首建时间保留（返回值与行内一致）
        assert rows[0]["created_at"] == first["created_at"]

    def test_upsert_resets_completed_section(self, db, report_id):
        """已完成章节重复落库 → 重置 pending（restart 纵深防御路径）。"""
        rec = db.create_report_section(report_id, "s1", "T")
        db.update_section_content(rec["id"], "正文内容", status="done", word_count=120)
        db.create_report_section(report_id, "s1", "T")
        rows = db.list_report_sections(report_id)
        assert rows[0]["status"] == "pending"
        assert rows[0]["word_count"] == 0
        # UPSERT 不清 content：restart 主路径先 delete_report_sections 清表，
        # 本路径为纵深防御，旧 content 会被新一轮 update_section_content 覆盖
        assert rows[0]["content"] == "正文内容"

    def test_unique_constraint_blocks_direct_duplicate(self, db, report_id):
        """UNIQUE(report_id, section_id) 约束真实存在：绕过 API 直插必失败。"""
        db.create_report_section(report_id, "s1", "T")
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO report_sections (id, report_id, section_id, title, created_at, updated_at) "
                "VALUES ('x', ?, 's1', 'T', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
                (report_id,),
            )

    def test_delete_report_sections_clears_rows(self, db, report_id):
        """restart 全量重跑前清空章节行（防旧行残留）。"""
        db.create_report_section(report_id, "s1", "A")
        db.create_report_section(report_id, "s2", "B")
        db.delete_report_sections(report_id)
        assert db.list_report_sections(report_id) == []


# ── 存量库迁移（UNIQUE 补齐 + 去重重建）─────────────────────


class TestMigrateReportSectionsUnique:
    def _rebuild_legacy_table(self, d):
        """把 report_sections 重建为无 UNIQUE 的存量旧结构。"""
        d.conn.executescript("""
            DROP TABLE report_sections;
            CREATE TABLE report_sections (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                section_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                word_count INTEGER DEFAULT 0,
                compose_mode TEXT DEFAULT 'standard',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

    def _insert_row(self, d, report_id, pk, section_id, content, updated_at):
        d.conn.execute(
            "INSERT INTO report_sections VALUES (?, ?, ?, 'T', ?, 'done', 10, 'standard', ?, ?)",
            (pk, report_id, section_id, content, updated_at, updated_at),
        )

    def test_migrate_dedupes_keeps_latest_by_updated_at(self, db, report_id):
        """存量重复行迁移：每 (report_id, section_id) 保留 updated_at 最新一条。"""
        self._rebuild_legacy_table(db)
        self._insert_row(db, report_id, "pk1", "s1", "旧内容", "2026-01-01T00:00:00")
        self._insert_row(db, report_id, "pk2", "s1", "新内容", "2026-01-02T00:00:00")
        self._insert_row(db, report_id, "pk3", "s2", "其他节", "2026-01-01T00:00:00")
        db.conn.commit()

        db._migrate_report_sections_unique()

        rows = db.list_report_sections(report_id)
        s1 = [r for r in rows if r["section_id"] == "s1"]
        assert len(s1) == 1
        assert s1[0]["id"] == "pk2"       # 保留最新
        assert s1[0]["content"] == "新内容"
        assert len(rows) == 2             # s2 无重复，照常保留

        # 迁移幂等：二次调用安全（唯一索引已存在 → 早退）
        db._migrate_report_sections_unique()
        assert len(db.list_report_sections(report_id)) == 2

        # 迁移后约束生效：直插重复行失败
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO report_sections (id, report_id, section_id, title, created_at, updated_at) "
                "VALUES ('x', ?, 's1', 'T', '2026-01-03T00:00:00', '2026-01-03T00:00:00')",
                (report_id,),
            )


# ── output_format 存量归一（'Word' → 'docx'）────────────────


class TestOutputFormatMigration:
    def test_legacy_word_normalized_on_reopen(self, tmp_path):
        """存量库 'Word' 值在下次 initialize 迁移统一为 'docx'。

        须用文件库真实驱动迁移路径（:memory: 每次都是新库，新库 DDL
        DEFAULT 已是 'docx'，UPDATE 扫不到行）。
        """
        p = tmp_path / "inkcube.db"
        d1 = Database(db_path=str(p))
        d1.initialize()
        proj = d1.create_project("P")
        d1.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title, output_format, status, created_at, updated_at) "
            "VALUES ('r1', ?, 't', '报告', 'Word', 'done', '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
            (proj["id"],),
        )
        d1.conn.commit()
        d1.conn.close()

        d2 = Database(db_path=str(p))  # 重新打开触发迁移
        d2.initialize()
        try:
            assert d2.get_report("r1")["output_format"] == "docx"
        finally:
            d2.conn.close()
