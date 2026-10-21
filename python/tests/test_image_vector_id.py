"""python/tests/test_image_vector_id.py — 图片描述向量 id 唯一性回归。

事故背景（2026-10-14）：向量 id 用 img_{file_id}_{page_no}，同页多图 id
碰撞，ChromaDB upsert 后仅剩每页最后一张——786 张 done 图片共享 288 个
id，498 张图的 VLM 描述检索不到（BP 一页最多 24 图）。
修复：id 尾部带 images.id（UUID），构造统一走 vector_store.image_doc_id，
写入（task_queue）与单图删除（routers）单一事实来源。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db.database import Database
from knowledge import task_queue as tq
from knowledge.task_queue import KnowledgeWorker
from knowledge.vector_store import VectorStore, image_doc_id


def _make_db() -> Database:
    db = Database(db_path=":memory:")
    db.initialize()
    return db


class StubVLM:
    """VLMClient 替身：返回 chart 类目（走 done + ChromaDB 入库分支）。"""

    AUTO_ARCHIVE_CATEGORIES = ("qr_code", "signature", "seal")

    def __init__(self, api_url=None, api_key=None, model=None):
        pass

    async def describe_image(self, image_path: str, context_text=None) -> dict:
        return {"description": "测试描述", "category": "chart"}


class FakeVectorStore:
    """记录 add_documents 调用，不触真实 ChromaDB。"""

    def __init__(self):
        self.calls = []

    def add_documents(self, project_id=None, ids=None, documents=None, metadatas=None):
        self.calls.append({
            "project_id": project_id, "ids": ids,
            "documents": documents, "metadatas": metadatas,
        })


def _insert_same_page_images(db: Database, page_no: int, image_ids: list[str]):
    """project → file → 同页多张 pending 图片（尺寸达标、文件存在）。"""
    db.conn.execute("INSERT INTO projects (id, name) VALUES ('p1', 'P')")
    db.conn.execute(
        "INSERT INTO files (id, project_id, filename, file_type, file_size, "
        "file_hash, status) VALUES ('f1', 'p1', 'a.pdf', 'pdf', 1, 'h', 'done')"
    )
    for img_id in image_ids:
        db.conn.execute(
            "INSERT INTO images (id, file_id, page_no, local_path, status, "
            "width_px, height_px) VALUES (?, 'f1', ?, ?, 'pending', 200, 200)",
            (img_id, page_no, os.path.abspath(__file__)),
        )
    db.conn.commit()


class TestImageDocId:
    def test_format_contains_image_id_suffix(self):
        """id = img_{file_id}_{page_no}_{image_id}，尾部保证每图唯一。"""
        assert image_doc_id("f1", 29, "abc-123") == "img_f1_29_abc-123"

    def test_page_no_none_falls_back_to_zero(self):
        assert image_doc_id("f1", None, "abc") == "img_f1_0_abc"

    def test_same_page_different_images_unique(self):
        """同页两图 → 不同 id（事故核心断言）。"""
        assert image_doc_id("f1", 29, "img-a") != image_doc_id("f1", 29, "img-b")


class TestProcessImageUniqueVectorIds:
    def test_same_page_images_get_unique_ids(self, monkeypatch):
        """同页多张图各自处理后，写入 ChromaDB 的 id 互不相同且含各自 image_id。"""
        monkeypatch.setattr(tq, "VLMClient", StubVLM)
        db = _make_db()
        _insert_same_page_images(db, page_no=29, image_ids=["img-a", "img-b"])
        worker = KnowledgeWorker(db=db, data_dir=".")
        fake = FakeVectorStore()
        worker._vector_store = fake  # _get_vector_store 直接复用，不触真实 ChromaDB

        for image_id in ("img-a", "img-b"):
            asyncio.run(worker._process_image(image_id, force=False))

        assert len(fake.calls) == 2
        ids = [c["ids"][0] for c in fake.calls]
        assert len(ids) == len(set(ids))  # 无碰撞
        assert set(ids) == {
            image_doc_id("f1", 29, "img-a"),
            image_doc_id("f1", 29, "img-b"),
        }

    def test_metadata_keeps_image_id_link(self, monkeypatch):
        """metadata.image_id 与向量 id 尾部一致，保留 ChromaDB ↔ SQLite 关联。"""
        monkeypatch.setattr(tq, "VLMClient", StubVLM)
        db = _make_db()
        _insert_same_page_images(db, page_no=29, image_ids=["img-a"])
        worker = KnowledgeWorker(db=db, data_dir=".")
        fake = FakeVectorStore()
        worker._vector_store = fake

        asyncio.run(worker._process_image("img-a", force=False))

        meta = fake.calls[0]["metadatas"][0]
        assert meta["image_id"] == "img-a"
        assert meta["content_type"] == "figure"


class TestDeleteByImageId:
    def test_delete_filters_by_image_metadata(self):
        """单图删除按 metadata.image_id 过滤：id 格式无关，新旧格式向量一次删净。

        2026-10-15 review 发现：delete_by_ids 对存量旧格式 id 静默 no-op，
        归档/删除/重试后旧向量残留且仍可被检索。"""
        deleted = []

        class FakeCollection:
            def delete(self, ids=None, where=None):
                deleted.append({"ids": ids, "where": where})

        class FakeClient:
            def list_collections(self):
                return []

            def create_collection(self, name, embedding_function=None, metadata=None):
                return FakeCollection()

            def get_collection(self, name, embedding_function=None):
                return FakeCollection()

        vs = VectorStore.__new__(VectorStore)  # 跳过 __init__：不连真实 ChromaDB/模型
        vs.client = FakeClient()
        vs.embedding_function = None

        vs.delete_by_image_id("p1", "uuid-a")

        assert deleted == [{"ids": None, "where": {"image_id": "uuid-a"}}]
