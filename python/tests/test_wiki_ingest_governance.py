"""发现项治理第一步回归（2.6 §3.5/§3.7/§4.2/§4.4，E2-2026-10）。

数据评估结论（2026-10-08）：618 条待处理发现项全部来自摄入管线，其中 46 条
confirm（正面结论）混占待办；9 个 no_text 文件每轮编译重撞且计入 completed。

覆盖：
1. no_text 文件 → 写 wiki_source_pages(status='skipped') 后跳过，
   get_pending_wiki_files 不再包含（不重撞、不伪装成功）
2. confirm 发现项 → 落库即 resolved=1 (auto_confirmed)，不占待处理列表
3. 存量 pending confirm → 启动迁移一次性归档（幂等）
4. pending 判定同时排除 ingested 与 skipped
"""
import asyncio
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from wiki.ingest import WikiIngestor


def _run(coro):
    """Synchronously drive a coroutine (project uses plain pytest, no pytest-asyncio)."""
    return asyncio.run(coro)


# Stage 1 需 ≥50 字符，否则被视为 LLM 静默失败
_STAGE1_OUTPUT = "分析结果：" + "关键实体与概念识别，交叉发现与矛盾记录。" * 3

# Stage 2 输出：1 个来源页 + 1 条 confirm + 1 条 contradiction
_STAGE2_OUTPUT = """---PAGE: src_cert_scan---
type: source
title: 测试证书要点

- 要点一：公司成立于 2008 年
---END PAGE---

---REVIEW: confirm | 材料与既有百科吻合---
成立时间与百科一致
---END REVIEW---

---REVIEW: contradiction | 认证时间矛盾---
证书显示 2008 年，BP 显示 2009 年
---END REVIEW---
"""


@pytest.fixture
def db():
    """Create a fresh in-memory database with all tables."""
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    proj = db.create_project(name="Test Project", project_type="general")
    return proj["id"]


def _add_done_file(db, project_id: str, filename: str = "cert_scan.jpg") -> str:
    """Create a file record in 'done' status (parse succeeded)."""
    f = db.create_file(project_id, filename, ".jpg", 1024, f"hash_{filename}")
    db.update_file_status(f["id"], "done")
    return f["id"]


def _add_text_chunk(db, project_id: str, file_id: str, content: str = "公司成立于 2008 年。"):
    """Insert one FTS chunk so _read_full_text finds text."""
    db.conn.execute(
        "INSERT INTO chunks_fts (chunk_id, content, project_id, filename, content_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"chunk_{file_id}_0", content, project_id, "cert_scan.jpg", "paragraph"),
    )
    db.conn.commit()


@pytest.fixture
def wiki_data_dir(tmp_path):
    """隔离的 data_dir（tmp_path）：禁止硬编码 /tmp —— Windows 上会落到当前盘符根目录。"""
    return str(tmp_path / "inkcube-test")


def _make_patched_ingestor(monkeypatch, db, data_dir):
    """Stub LLM：stage1 返回分析文本，stage2 返回含 confirm+contradiction 的输出。"""

    async def fake_chat(self, messages, settings, task_id, stage, project_id=None):
        return _STAGE1_OUTPUT if stage == "stage1" else _STAGE2_OUTPUT

    monkeypatch.setattr(WikiIngestor, "_chat_with_retry", fake_chat)
    return WikiIngestor(db, data_dir, None, {})


class TestNoTextSkipped:
    def test_no_text_writes_skipped_source_page(self, db, project_id, wiki_data_dir):
        """无文本文件：写 skipped 留痕 + 返回 skipped=True（CompilePanel 计「跳过」）。"""
        file_id = _add_done_file(db, project_id)
        ingestor = WikiIngestor(db, wiki_data_dir, None, {})

        result = _run(ingestor.ingest_file(project_id, file_id, "task_1"))

        assert result["skipped"] is True
        assert result["error"] == "no_text"
        sp = db.get_source_page(project_id, file_id)
        assert sp is not None
        assert sp["status"] == "skipped"

    def test_skipped_file_not_pending_again(self, db, project_id, wiki_data_dir):
        """skipped 后不再进入待编译集合（修复前每轮增量重撞同一文件）。"""
        file_id = _add_done_file(db, project_id)
        ingestor = WikiIngestor(db, wiki_data_dir, None, {})
        _run(ingestor.ingest_file(project_id, file_id, "task_1"))

        pending = db.get_pending_wiki_files(project_id)

        assert file_id not in [f["id"] for f in pending]


class TestConfirmAutoArchived:
    def test_confirm_resolved_on_write(self, db, project_id, monkeypatch, wiki_data_dir):
        """confirm 落库即 resolved=1 (auto_confirmed)，不占待处理列表。"""
        patched_ingestor = _make_patched_ingestor(monkeypatch, db, wiki_data_dir)
        file_id = _add_done_file(db, project_id)
        _add_text_chunk(db, project_id, file_id)

        result = _run(patched_ingestor.ingest_file(project_id, file_id, "task_1"))

        assert result["skipped"] is False
        pending = db.list_wiki_reviews(project_id, resolved=False)
        archived = db.list_wiki_reviews(project_id, resolved=True)

        assert [r["type"] for r in pending] == ["contradiction"]
        confirm = [r for r in archived if r["type"] == "confirm"]
        assert len(confirm) == 1
        assert confirm[0]["resolved_action"] == "auto_confirmed"

    def test_non_confirm_stays_pending(self, db, project_id, monkeypatch, wiki_data_dir):
        """contradiction 保持 resolved=0 待处理（confirm 归档不影响其他类型）。"""
        patched_ingestor = _make_patched_ingestor(monkeypatch, db, wiki_data_dir)
        file_id = _add_done_file(db, project_id)
        _add_text_chunk(db, project_id, file_id)

        _run(patched_ingestor.ingest_file(project_id, file_id, "task_1"))

        pending = db.list_wiki_reviews(project_id, resolved=False)
        assert len(pending) == 1
        assert pending[0]["resolved"] == 0


class TestLegacyConfirmMigration:
    def test_initialize_archives_pending_confirm(self, tmp_path):
        """存量 pending confirm 由启动迁移归档；其他类型不受影响。

        用文件库模拟真实升级：initialize() 每次重开连接（:memory: 会得到空库），
        旧库文件在新版本代码启动后数据保留、迁移段执行。
        """
        db_path = str(tmp_path / "upgrade.db")
        db = Database(db_path)
        db.initialize()
        pid = db.create_project(name="Legacy", project_type="general")["id"]
        db.create_or_update_wiki_review(
            "rev_confirm", pid, "confirm", "材料吻合", "desc",
        )
        db.create_or_update_wiki_review(
            "rev_contra", pid, "contradiction", "时间矛盾", "desc",
        )
        db.conn.close()

        # 模拟应用重启：新版本代码 initialize()（重开连接 + 执行迁移段）
        db2 = Database(db_path)
        db2.initialize()

        confirm = db2.get_wiki_review("rev_confirm")
        contra = db2.get_wiki_review("rev_contra")
        assert confirm["resolved"] == 1
        assert confirm["resolved_action"] == "auto_confirmed"
        assert contra["resolved"] == 0
        db2.conn.close()

    def test_migration_idempotent(self, tmp_path):
        """迁移幂等：重复启动多次 initialize 后结果稳定。"""
        db_path = str(tmp_path / "idem.db")
        db = Database(db_path)
        db.initialize()
        pid = db.create_project(name="P", project_type="general")["id"]
        db.create_or_update_wiki_review(
            "rev_confirm", pid, "confirm", "吻合", "desc",
        )
        db.conn.close()

        for _ in range(3):
            dup = Database(db_path)
            dup.initialize()
            dup.conn.close()

        check = Database(db_path)
        check.initialize()
        confirm = check.get_wiki_review("rev_confirm")
        assert confirm["resolved"] == 1
        assert confirm["resolved_action"] == "auto_confirmed"
        check.conn.close()


class TestPendingExcludesSkipped:
    def test_pending_excludes_ingested_and_skipped(self, db, project_id):
        """待编译判定：ingested 与 skipped 均排除，仅未处理文件进入集合。"""
        f_ingested = _add_done_file(db, project_id, "a.pdf")
        f_skipped = _add_done_file(db, project_id, "b.jpg")
        f_new = _add_done_file(db, project_id, "c.pdf")

        db.create_or_update_source_page(
            project_id=project_id, file_id=f_ingested, status="ingested", pages_touched=[],
        )
        db.create_or_update_source_page(
            project_id=project_id, file_id=f_skipped, status="skipped", pages_touched=[],
        )

        pending_ids = [f["id"] for f in db.get_pending_wiki_files(project_id)]

        assert f_new in pending_ids
        assert f_ingested not in pending_ids
        assert f_skipped not in pending_ids

    def test_create_review_resolved_param(self, db, project_id):
        """create_or_update_wiki_review 的 resolved/resolved_action 参数生效。"""
        rec = db.create_or_update_wiki_review(
            "rev1", project_id, "confirm", "吻合", "desc",
            resolved=1, resolved_action="auto_confirmed",
        )
        assert rec["resolved"] == 1
        assert rec["resolved_action"] == "auto_confirmed"


class TestPendingCompileCountRealtime:
    """get_pending_compile_count 实时集合口径（2026-10-22）。

    替代原 projects.pending_compile_count 事件累加计数器（解析+1/删除+1/
    schema 变更同类全+1/编译启动清零）：原口径随操作序列漂移——删除未编译
    文件双计、重解析虚增、schema 变更向零文件项目广播。实时口径与编译消费
    的 get_pending_wiki_files 同一 WHERE（_PENDING_WIKI_FILES_WHERE）。"""

    def test_matches_pending_files_and_shrinks_on_ingest(self, db, project_id):
        f1 = _add_done_file(db, project_id, "a.pdf")
        _add_done_file(db, project_id, "b.pdf")
        assert db.get_pending_compile_count(project_id) == 2

        db.create_or_update_source_page(
            project_id=project_id, file_id=f1, status="ingested", pages_touched=[])

        assert db.get_pending_compile_count(project_id) == 1

    def test_delete_file_shrinks_without_double_count(self, db, project_id):
        """删除未编译文件：集合自然收缩（修复前事件计数器再 +1 双计虚增）。"""
        f1 = _add_done_file(db, project_id, "a.pdf")
        _add_done_file(db, project_id, "b.pdf")
        assert db.get_pending_compile_count(project_id) == 2

        db.conn.execute("DELETE FROM files WHERE id = ?", (f1,))
        db.conn.commit()

        assert db.get_pending_compile_count(project_id) == 1

    def test_only_done_files_counted(self, db, project_id):
        """仅解析 done 文件计入：pending（解析中）与 error 不进待编译集合。"""
        _add_done_file(db, project_id, "a.pdf")
        pending_file = db.create_file(
            project_id, "p.pdf", ".pdf", 1, "hash_p")   # status=pending
        err_file = _add_done_file(db, project_id, "e.pdf")
        db.update_file_status(err_file, "error")

        assert db.get_pending_compile_count(project_id) == 1
        _ = pending_file

    def test_force_clear_restores_full_count(self, db, project_id):
        """force 编译清空 source_pages：全量 done 文件如实回到待编译集合
        （修复前计数器被清零，全量重编期间显示 0）。"""
        f1 = _add_done_file(db, project_id, "a.pdf")
        db.create_or_update_source_page(
            project_id=project_id, file_id=f1, status="ingested", pages_touched=[])
        assert db.get_pending_compile_count(project_id) == 0

        db.conn.execute(
            "DELETE FROM wiki_source_pages WHERE project_id = ?", (project_id,))
        db.conn.commit()

        assert db.get_pending_compile_count(project_id) == 1

    def test_legacy_column_dropped_on_initialize(self, tmp_path):
        """存量库删列迁移：带 pending_compile_count 列的旧库经 initialize 后列消失
        （幂等：新库无列时 DROP 失败静默跳过）。"""
        db_path = str(tmp_path / "legacy.db")
        db = Database(db_path)
        db.initialize()
        # 模拟旧版本库：加回列
        db.conn.execute(
            "ALTER TABLE projects ADD COLUMN pending_compile_count INTEGER DEFAULT 0")
        db.conn.commit()
        db.conn.close()

        db2 = Database(db_path)
        db2.initialize()
        cols = [r["name"] for r in db2.conn.execute("PRAGMA table_info(projects)").fetchall()]
        assert "pending_compile_count" not in cols
        db2.conn.close()


class TestZeroPageNotIngested:
    """解析 0 页纵深（2026-10-26 全角冒号事故）：摄入未完成不得伪装成功。

    事故形态：LLM 输出 ---PAGE： key---（全角冒号），旧 parser 全盘漏析 →
    0 页仍被标 ingested，skip-if-unchanged 使重试/增量永不可达。
    处置：① 不写 wiki_source_pages（文件保持待编译，重试/下轮增量可达）；
    ② summary 带 error（复用失败契约：CompilePanel 失败计数/详情、
    「重试失败」、终态 warning toast 全自动生效）。"""

    def test_zero_page_returns_error_and_stays_pending(self, db, project_id, monkeypatch, wiki_data_dir):
        """0 页：返回 error、不写 source_page、文件仍在待编译集合。"""

        async def fake_chat(self, messages, settings, task_id, stage, project_id=None):
            if stage == "stage1":
                return _STAGE1_OUTPUT
            # 真实事故形态：全角冒号分隔符（修复后 parser 已能解析，这里
            # 用不可解析的垃圾输出模拟 0 页——纵深防御与 parser 修复独立验证）
            return "这是一段没有任何结构化块的模型输出。"

        monkeypatch.setattr(WikiIngestor, "_chat_with_retry", fake_chat)
        ingestor = WikiIngestor(db, wiki_data_dir, None, {})
        file_id = _add_done_file(db, project_id)
        _add_text_chunk(db, project_id, file_id)

        result = _run(ingestor.ingest_file(project_id, file_id, "task_z"))

        assert result["skipped"] is False
        assert "0 页" in result["error"]
        assert result["pages_created"] == 0
        # 不写 source_page → 文件保持待编译（修复前此处为 ingested 脏行）
        assert db.get_source_page(project_id, file_id) is None
        assert file_id in [f["id"] for f in db.get_pending_wiki_files(project_id)]

    def test_fullwidth_output_fully_recovered(self, db, project_id, monkeypatch, wiki_data_dir):
        """根因修复端到端：全角冒号输出经新 parser 正常产出页面并标 ingested。"""

        async def fake_chat(self, messages, settings, task_id, stage, project_id=None):
            if stage == "stage1":
                return _STAGE1_OUTPUT
            return _STAGE2_OUTPUT.replace("---PAGE:", "---PAGE：")

        monkeypatch.setattr(WikiIngestor, "_chat_with_retry", fake_chat)
        ingestor = WikiIngestor(db, wiki_data_dir, None, {})
        file_id = _add_done_file(db, project_id)
        _add_text_chunk(db, project_id, file_id)

        result = _run(ingestor.ingest_file(project_id, file_id, "task_f"))

        # 成功路径无 error 键（失败契约：有 error 键即失败）
        assert result.get("error") is None
        assert result["pages_created"] == 1
        pages = db.list_wiki_pages(project_id)
        assert len(pages) == 1
        assert pages[0]["page_key"] == "src_cert_scan"
        sp = db.get_source_page(project_id, file_id)
        assert sp is not None
        assert sp["status"] == "ingested"
