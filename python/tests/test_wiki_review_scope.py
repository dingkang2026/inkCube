"""wiki_reviews 项目作用域回归（整体 review H-1/L-1）。

review_id 必须项目作用域：同类型项目结构页同标题，type+title 哈希会跨项目
撞车（ON CONFLICT 覆盖前项目行 → 内容串台 + 前项目列表丢失）。三处消费方
共用 wiki.review_id.compute_review_id 唯一实现。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from wiki.review_id import compute_review_id


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def pa(db):
    return db.create_project(name="A", project_type="general")["id"]


@pytest.fixture
def pb(db):
    return db.create_project(name="B", project_type="general")["id"]


def test_review_id_is_project_scoped(pa, pb):
    """同 type+title 不同项目 → 不同 id；同项目同输入 → 稳定（upsert 幂等前提）。"""
    a1 = compute_review_id(pa, "missing-page", "缺页面")
    a2 = compute_review_id(pa, "missing-page", "缺页面")
    b1 = compute_review_id(pb, "missing-page", "缺页面")
    assert a1 == a2  # 同项目稳定
    assert a1 != b1  # 跨项目不同（H-1 根治：原 type+title 哈希必撞）


def test_upsert_conflict_updates_project_id(db, pa, pb):
    """H-1 兜底：万一 id 撞行，project_id 随 ON CONFLICT 覆盖——B 的写入
    不得留在 A 的行上（原实现只更新内容列，project_id 保持 A → 内容串台）。"""
    rid = compute_review_id(pa, "gap", "缺年报")
    db.create_or_update_wiki_review(rid, pa, "gap", "缺年报", description="A 版")
    # 模拟撞行：同 id 以 B 项目落库（哈希根治后正常不可达，防御纵深）
    db.create_or_update_wiki_review(rid, pb, "gap", "缺年报", description="B 版")
    row = db.get_wiki_review(rid)
    assert row["project_id"] == pb
    assert row["description"] == "B 版"


def test_resolve_wiki_review_is_project_scoped(db, pa, pb):
    """L-1：sweep/RESOLVES 链路的核销按 (id, project_id) 定位——错配项目
    的核销在 DB 层被挡下（LLM 幻觉 id / 撞车 id 不再可跨项目核销）。"""
    rid = compute_review_id(pa, "gap", "缺年报")
    db.create_or_update_wiki_review(rid, pa, "gap", "缺年报")
    assert db.resolve_wiki_review(pb, rid, "auto_resolved") is False
    assert db.get_wiki_review(rid)["resolved"] == 0
    assert db.resolve_wiki_review(pa, rid, "auto_resolved") is True
    assert db.get_wiki_review(rid)["resolved"] == 1
