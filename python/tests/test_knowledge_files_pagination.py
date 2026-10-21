"""python/tests/test_knowledge_files_pagination.py — 文件列表 offset 分页 + 状态筛选回归。

回归背景（2026-10）：
- IPC 层此前丢弃前端 offset 参数 → 文件列表翻页恒回第 1 页（分页失效）；
- 状态筛选原为纯客户端过滤、只作用于当前页 → 下沉服务端（status 参数，
  processing = pending+parsing），项目/基础库两端点共用 _file_status_clause；
- 修复同时暴露项目端点 offset 分支 LIMIT/OFFSET 参数绑定顺序颠倒的潜伏 bug
  （该分支因 IPC 断链从未被执行过，test_offset_param_binding_order 防回归）。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.knowledge as knowledge
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def routed(db, monkeypatch):
    """两个端点共用同一内存库；base-lib 存在性校验放行。"""
    monkeypatch.setattr(knowledge, "get_db", lambda: db)

    async def _allow(_db, _lib_id):
        return None

    monkeypatch.setattr(knowledge, "_validate_base_lib_exists", _allow)
    return db


def _seed(db, pid, spec):
    """按 {status: count} 造文件；file_id 零填充保证同秒 created_at 下 id DESC 排序确定。"""
    seq = 0
    for status, count in spec.items():
        for _ in range(count):
            fid = f"fid{seq:03d}"
            db.create_file(
                project_id=pid, filename=f"{status}-{seq}.pdf",
                file_type=".pdf", file_size=1, file_hash=f"h{seq}", file_id=fid,
            )
            if status != "pending":
                db.update_file_status(fid, status)
            seq += 1


def _ensure_lib(db, lib_id):
    """基础库文件外键指向 projects 行（kind='base_lib'），直插最小行。"""
    now = db._now()
    db.conn.execute(
        "INSERT INTO projects (id, name, description, type, stage, company_name, credit_code, kind, created_at, updated_at) "
        "VALUES (?, 'T', '', 'general', 'none', '', '', 'base_lib', ?, ?)",
        (lib_id, now, now),
    )
    db.conn.commit()


class TestProjectFilesOffsetPagination:
    def test_offset_paginates_across_pages(self, routed):
        """核心回归：offset 生效，第 2 页与第 1 页无重叠，total 全量。"""
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 25})
        page1 = asyncio.run(knowledge.get_project_files(pid, limit=20, offset=0))
        page2 = asyncio.run(knowledge.get_project_files(pid, limit=20, offset=20))
        assert len(page1["files"]) == 20
        assert page1["has_more"] is True
        assert page1["total"] == 25
        assert len(page2["files"]) == 5
        ids1 = {f["id"] for f in page1["files"]}
        ids2 = {f["id"] for f in page2["files"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 25

    def test_offset_param_binding_order(self, routed):
        """LIMIT ?/OFFSET ? 占位符顺序与参数 append 顺序一致（绑定颠倒防回归）。"""
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 5})
        # 全量排序（created_at DESC, id DESC）：fid004 > fid003 > fid002 > fid001 > fid000
        page2 = asyncio.run(knowledge.get_project_files(pid, limit=2, offset=2))
        assert [f["id"] for f in page2["files"]] == ["fid002", "fid001"]

    def test_offset_zero_keeps_first_page(self, routed):
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 3})
        result = asyncio.run(knowledge.get_project_files(pid, limit=2))
        assert len(result["files"]) == 2
        assert result["files"][0]["id"] == "fid002"


class TestProjectFilesStatusFilter:
    def test_processing_includes_pending_and_parsing(self, routed):
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 3, "pending": 2, "parsing": 1, "error": 1})
        result = asyncio.run(knowledge.get_project_files(pid, limit=20, status="processing"))
        statuses = {f["status"] for f in result["files"]}
        assert statuses == {"pending", "parsing"}
        assert result["total"] == 3

    def test_literal_status_filters(self, routed):
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 3, "error": 1})
        done = asyncio.run(knowledge.get_project_files(pid, limit=20, status="done"))
        assert len(done["files"]) == 3
        assert done["total"] == 3
        error = asyncio.run(knowledge.get_project_files(pid, limit=20, status="error"))
        assert len(error["files"]) == 1
        assert error["total"] == 1

    def test_all_and_none_return_everything(self, routed):
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 2, "pending": 1})
        for status in (None, "all"):
            result = asyncio.run(knowledge.get_project_files(pid, limit=20, status=status))
            assert result["total"] == 3

    def test_filter_combined_with_offset(self, routed):
        """筛选口径下分页：处理中 25 条（20 done 不计），第 2 页取剩余 5 条。"""
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 20, "pending": 25})
        page2 = asyncio.run(
            knowledge.get_project_files(pid, limit=20, offset=20, status="processing")
        )
        assert len(page2["files"]) == 5
        assert page2["total"] == 25
        assert all(f["status"] == "pending" for f in page2["files"])


class TestBaseLibFilesOffsetPagination:
    def test_offset_and_total(self, routed):
        """基础库端点对齐项目端点：offset 分页 + total（此前两者均缺失）。"""
        lib_id = "bl-test"
        _ensure_lib(routed, lib_id)
        _seed(routed, lib_id, {"done": 25})
        page1 = asyncio.run(knowledge.get_base_lib_files(lib_id, limit=20, offset=0))
        page2 = asyncio.run(knowledge.get_base_lib_files(lib_id, limit=20, offset=20))
        assert len(page1["files"]) == 20
        assert page1["total"] == 25
        assert page2["total"] == 25
        ids1 = {f["id"] for f in page1["files"]}
        ids2 = {f["id"] for f in page2["files"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 25

    def test_status_filter(self, routed):
        lib_id = "bl-test"
        _ensure_lib(routed, lib_id)
        _seed(routed, lib_id, {"done": 3, "pending": 2, "parsing": 1})
        result = asyncio.run(knowledge.get_base_lib_files(lib_id, limit=20, status="processing"))
        statuses = {f["status"] for f in result["files"]}
        assert statuses == {"pending", "parsing"}
        assert result["total"] == 3

    def test_no_filter_backward_compatible(self, routed):
        """不带筛选参数的既有调用形态：files + has_more + total 全量。"""
        lib_id = "bl-test"
        _ensure_lib(routed, lib_id)
        _seed(routed, lib_id, {"done": 3})
        result = asyncio.run(knowledge.get_base_lib_files(lib_id, limit=20))
        assert len(result["files"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3


class TestFileListStatusCounts:
    """status_counts（筛选 Tab 计数）：搜索/类型口径，不受状态筛选影响（与图库同语义）。"""

    def test_counts_not_affected_by_status_filter(self, routed):
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 3, "pending": 2, "parsing": 1, "error": 1})
        result = asyncio.run(knowledge.get_project_files(pid, limit=20, status="done"))
        assert result["status_counts"] == {
            "done": 3, "pending": 2, "parsing": 1, "error": 1,
        }
        assert result["total"] == 3  # total 仍随状态筛选（分页口径）

    def test_counts_respect_search(self, routed):
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 3, "error": 1})
        # _seed 文件名形如 done-0.pdf/error-3.pdf → 搜 done 只命中 3 个 done
        result = asyncio.run(knowledge.get_project_files(pid, limit=20, filename_search="done"))
        assert result["status_counts"] == {"done": 3}

    def test_base_lib_status_counts(self, routed):
        lib_id = "bl-test"
        _ensure_lib(routed, lib_id)
        _seed(routed, lib_id, {"done": 2, "pending": 1})
        result = asyncio.run(knowledge.get_base_lib_files(lib_id, limit=20, status="done"))
        assert result["status_counts"] == {"done": 2, "pending": 1}
        assert result["total"] == 2


class TestActiveFirstOrdering:
    """活动置顶排序：pending/parsing 恒排最前（组内时间倒序），其余按时间倒序。"""

    def test_active_files_pinned_to_top(self, routed):
        pid = routed.create_project("P")["id"]
        # seq 递增 → done（fid000-004）在先、pending（fid005-006）在后；
        # 同秒 created_at 下组内 id DESC
        _seed(routed, pid, {"done": 5, "pending": 2})
        result = asyncio.run(knowledge.get_project_files(pid, limit=20))
        ids = [f["id"] for f in result["files"]]
        assert ids[:2] == ["fid006", "fid005"]  # 活动置顶
        assert ids[2:] == ["fid004", "fid003", "fid002", "fid001", "fid000"]
        assert all(f["status"] == "pending" for f in result["files"][:2])

    def test_parsing_also_pinned(self, routed):
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 3, "parsing": 1})
        result = asyncio.run(knowledge.get_project_files(pid, limit=20))
        assert result["files"][0]["status"] == "parsing"

    def test_all_done_keeps_time_order(self, routed):
        """无活动文件时排序与旧行为一致（created_at/id 倒序）。"""
        pid = routed.create_project("P")["id"]
        _seed(routed, pid, {"done": 5})
        result = asyncio.run(knowledge.get_project_files(pid, limit=20))
        assert [f["id"] for f in result["files"]] == [
            "fid004", "fid003", "fid002", "fid001", "fid000",
        ]
