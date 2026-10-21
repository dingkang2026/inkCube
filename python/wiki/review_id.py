"""python/wiki/review_id.py — wiki_reviews 稳定 ID 唯一实现.

review_id 必须项目作用域（整体 review H-1）：同类型项目的结构页来自同一
schema 种子，标题相同——若只按 type+title 哈希，两项目的发现项会同 id 撞车，
ON CONFLICT 令后写者覆盖前项目行（内容串台 + 前项目列表丢失）。

三处消费方共用此唯一实现：
  - routers/wiki.py（lint 落库）
  - wiki/ingest.py（摄入落库）
  - db/database.py（无存量迁移：项目未上线、无存量数据，规范 §一）
"""


def compute_review_id(project_id: str, review_type: str, title: str) -> str:
    """Compute a stable, project-scoped review ID (FNV-1a 32-bit, per 2.6 §3.5)."""
    normalized = f"{project_id}::{review_type}::{title.strip().lower()}"
    # FNV-1a 32-bit
    h = 0x811C9DC5
    for byte in normalized.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"review_{h:08x}"
