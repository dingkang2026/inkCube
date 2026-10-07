"""python/tests/test_knowledge_images_endpoint.py — 图库端点活动置顶排序 + 计数语义。

回归背景（2026-10）：
- 图库排序原为纯 created_at DESC，描述中的图（pending/processing）夹在已完成
  中间——与文件列表活动置顶（test_knowledge_files_pagination.
  TestActiveFirstOrdering）同型对齐；
- status_counts（筛选 Tab 计数）此前无测试锁定，一并覆盖（搜索/类型口径、
  不含状态筛选；total 仍随状态筛选——分页口径）。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.knowledge as knowledge
from db.database import Database


@pytest.fixture
def routed(monkeypatch):
    """项目/基础库图库端点共用同一内存库；base-lib 存在性校验放行。"""
    database = Database(db_path=":memory:")
    database.initialize()
    monkeypatch.setattr(knowledge, "get_db", lambda: database)

    async def _allow(_db, _lib_id):
        return None

    monkeypatch.setattr(knowledge, "_validate_base_lib_exists", _allow)
    return database


def _ensure_lib(db, lib_id):
    """基础库图片外键指向 projects 行（kind='base_lib'），直插最小行。"""
    now = db._now()
    db.conn.execute(
        "INSERT INTO projects (id, name, description, type, stage, company_name, credit_code, kind, created_at, updated_at) "
        "VALUES (?, 'T', '', 'general', 'none', '', '', 'base_lib', ?, ?)",
        (lib_id, now, now),
    )
    db.conn.commit()


def _seed_gallery(db, pid, statuses):
    """按 statuses 顺序造图（挂在 done 文件下），显式递增 created_at 保证排序确定。

    返回 (img_id, status, created_at) 列表；created_at 形如 2026-01-01T00:00:0{i}。
    """
    fid = db.create_file(
        project_id=pid, filename="a.pdf", file_type=".pdf",
        file_size=1, file_hash=f"h-{pid}", file_id=f"fimg-{pid}",
    )["id"]
    db.update_file_status(fid, "done")
    seeded = []
    for i, status in enumerate(statuses):
        img = db.create_image(file_id=fid, local_path=f"p{i}.png", page_no=i + 1)
        if status != "pending":
            db.update_image_status(img["id"], status)
        created = f"2026-01-01T00:00:{i:02d}"
        db.conn.execute(
            "UPDATE images SET created_at = ? WHERE id = ?", (created, img["id"])
        )
        seeded.append((img["id"], status, created))
    db.conn.commit()
    return seeded


class TestImageGalleryActiveFirstOrdering:
    """活动置顶：pending/processing 恒排最前（组内时间倒序），其余按时间倒序。"""

    def test_project_gallery_pins_active_images(self, routed):
        pid = routed.create_project("P")["id"]
        # 时间序（旧→新）：done, pending, done, processing, done
        _seed_gallery(routed, pid, ["done", "pending", "done", "processing", "done"])
        result = asyncio.run(knowledge.get_project_images(project_id=pid))
        statuses = [r["status"] for r in result["images"]]
        # 活动组置顶，组内 processing(00:03) > pending(00:01)；done 组时间倒序
        assert statuses == ["processing", "pending", "done", "done", "done"]

    def test_base_lib_gallery_pins_active_images(self, routed):
        lib_id = "bl-img"
        _ensure_lib(routed, lib_id)
        _seed_gallery(routed, lib_id, ["done", "pending", "processing", "done"])
        result = asyncio.run(knowledge.get_base_lib_images(lib_id))
        statuses = [r["status"] for r in result["images"]]
        assert statuses == ["processing", "pending", "done", "done"]

    def test_all_done_keeps_time_order(self, routed):
        """无活动图时排序与旧行为一致（created_at DESC）。"""
        pid = routed.create_project("P")["id"]
        _seed_gallery(routed, pid, ["done", "done", "done"])
        result = asyncio.run(knowledge.get_project_images(project_id=pid))
        created = [r["created_at"] for r in result["images"]]
        assert created == sorted(created, reverse=True)


class TestImageGalleryStatusCounts:
    """status_counts：搜索/类型口径，不受状态筛选影响（total 仍随筛选——分页口径）。"""

    def test_counts_not_affected_by_status_filter(self, routed):
        pid = routed.create_project("P")["id"]
        _seed_gallery(routed, pid, ["done", "pending", "done", "processing", "done"])
        result = asyncio.run(knowledge.get_project_images(pid, status="done"))
        assert result["status_counts"] == {"done": 3, "pending": 1, "processing": 1}
        assert result["total"] == 3

    def test_base_lib_counts_not_affected_by_status_filter(self, routed):
        lib_id = "bl-img"
        _ensure_lib(routed, lib_id)
        _seed_gallery(routed, lib_id, ["done", "pending", "processing", "done"])
        result = asyncio.run(knowledge.get_base_lib_images(lib_id, status="done"))
        assert result["status_counts"] == {"done": 2, "pending": 1, "processing": 1}
        assert result["total"] == 2
