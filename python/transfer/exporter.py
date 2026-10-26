"""python/transfer/exporter.py — 项目交换包导出（2.9project-transfer-design.md §4/§5）。

导出 = 删除链路的反向打包：终态行快照 + 原文件 blob + 向量。Chroma
collection.get 全量导出（向量+文本+metadata 原样，2.9 §4.4）——图片描述向量
只入 Chroma 不入 FTS，全量源是 Chroma，FTS 仅作文本 chunk 的 content/
filename/content_type 补充源并生成 in_fts 标记。FTS 索引、ledger_entries
（可全量重建，3.1 不变量 1）、task_suggestions / notifications /
wiki_compile_tasks / wiki_source_pages（临时态或摄入态）不导出。
模板不随包（2.9 D5）。非终态行（pending/parsing/generating/filling 等）
跳过并计入 skipped 精准计数（2.9 §4）。
"""
import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone

import numpy as np

from knowledge.vector_store import VectorStore

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
_CHUNK_ID_PREFIXES = ("chunk_", "img_")


class TransferError(Exception):
    """导出/导入业务失败；message 用户可读，直接透传 HTTP 400 detail。"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_id_of_chunk(chunk_id: str) -> str | None:
    """从 chunk_id 提取源 file_id（uuid4 无下划线：前缀后首个 '_' 前即 file_id）。"""
    for prefix in _CHUNK_ID_PREFIXES:
        if chunk_id.startswith(prefix):
            return chunk_id[len(prefix):].split("_", 1)[0] or None
    return None


def rel_path_under(path: str | None, base: str) -> str | None:
    """path 相对 base 的 relpath（'/' 分隔）；path 空/越出 base 返回 None。"""
    if not path:
        return None
    try:
        rel = os.path.relpath(path, base)
    except ValueError:  # Windows 跨盘符
        return None
    if rel == ".." or rel.startswith(".." + os.sep):
        return None
    return rel.replace("\\", "/")


def export_project(db, data_dir: str, project_id: str, target_path: str,
                   on_progress=None) -> dict:
    """导出项目为 .inkproj 交换包（同步、终态快照）。

    返回 {"path", "counts", "skipped"}；项目不存在/非项目抛 TransferError。
    on_progress(percent, message) 供 transfer/service 推进活动进度。
    """
    progress = on_progress or (lambda p, m: None)
    project = db.get_project(project_id)
    if not project or project.get("kind") != "project":
        raise TransferError(f"Project not found: {project_id}")

    progress(5, "collecting rows")
    all_files = db.list_files(project_id)
    files = [f for f in all_files if f["status"] in ("done", "error")]
    skipped_files = len(all_files) - len(files)
    file_ids = {f["id"] for f in files}

    def _rows(sql: str, params: tuple) -> list[dict]:
        return [dict(r) for r in db.conn.execute(sql, params).fetchall()]

    all_reports = _rows("SELECT * FROM reports WHERE project_id = ?", (project_id,))
    reports = [r for r in all_reports if r["status"] in ("done", "error")]
    skipped_reports = len(all_reports) - len(reports)
    report_ids = {r["id"] for r in reports}

    all_fills = _rows("SELECT * FROM form_fills WHERE project_id = ?", (project_id,))
    fills = [r for r in all_fills if r["status"] in ("success", "failed")]
    skipped_fills = len(all_fills) - len(fills)

    phase_tasks = _rows("SELECT * FROM phase_tasks WHERE project_id = ?", (project_id,))
    wiki_pages = _rows("SELECT * FROM wiki_pages WHERE project_id = ?", (project_id,))
    # wiki_page_versions 自带 project_id 列（database.py 建表处），直接按项目查
    wiki_page_versions = _rows(
        "SELECT * FROM wiki_page_versions WHERE project_id = ?", (project_id,))
    # 引用了被跳过文件的 source 行不打包（悬挂引用）；wiki_pages 不剔除
    # （last_compiled_file_id 可空可悬挂，仅展示层字段）
    wiki_page_sources = [
        r for r in _rows(
            "SELECT wps.* FROM wiki_page_sources wps "
            "JOIN wiki_pages wp ON wp.id = wps.wiki_page_id WHERE wp.project_id = ?",
            (project_id,))
        if r["file_id"] in file_ids]
    wiki_change_log = _rows("SELECT * FROM wiki_change_log WHERE project_id = ?", (project_id,))
    wiki_reviews = _rows("SELECT * FROM wiki_reviews WHERE project_id = ?", (project_id,))

    sections: list[dict] = []
    logs: list[dict] = []
    if report_ids:
        q = ",".join("?" * len(report_ids))
        sections = _rows(f"SELECT * FROM report_sections WHERE report_id IN ({q})",
                         tuple(report_ids))
        logs = _rows(f"SELECT * FROM report_logs WHERE report_id IN ({q})",
                     tuple(report_ids))
    images: list[dict] = []
    if file_ids:
        q = ",".join("?" * len(file_ids))
        images = _rows(f"SELECT * FROM images WHERE file_id IN ({q})", tuple(file_ids))

    progress(15, "collecting vectors")
    vs = VectorStore(os.path.join(data_dir, "chroma"))
    exported = vs.export_collection(project_id)
    # FTS 辅助源：文本 chunk 标记 in_fts；图片描述向量不在 FTS（in_fts=False）
    fts_map = {r["chunk_id"]: r for r in _rows(
        "SELECT chunk_id, content, filename, content_type FROM chunks_fts "
        "WHERE project_id = ?", (project_id,))}

    chunk_meta = []
    vec_rows = []
    # embeddings 是 ndarray（chromadb 返回类型），不可用 `or []` 求真值（多维数组
    # 真值歧义 ValueError）；documents/metadatas 是 list-or-None，or [] 安全
    embeddings = exported.get("embeddings") if exported.get("embeddings") is not None else []
    for cid, document, metadata, emb in zip(
            exported["ids"], exported["documents"] or [],
            exported["metadatas"] or [], embeddings):
        fts_row = fts_map.get(cid)
        if emb is not None:
            vec_rows.append(emb.tolist() if hasattr(emb, "tolist") else list(emb))
        chunk_meta.append({
            "chunk_id": cid,
            "document": document if document is not None else (fts_row or {}).get("content"),
            "metadata": dict(metadata or {}),
            "in_fts": fts_row is not None,
            "has_vector": emb is not None,
            "old_file_id": file_id_of_chunk(cid),
        })
    vectors = (np.array(vec_rows, dtype=np.float32) if vec_rows
               else np.zeros((0, 1024), dtype=np.float32))

    progress(30, "packing blobs")
    files_dir = os.path.join(data_dir, "files", project_id)
    files_meta = []
    for f in files:
        row = dict(f)
        row["rel_path"] = f["id"] if os.path.isdir(os.path.join(files_dir, f["id"])) else None
        files_meta.append(row)
    images_meta = [dict(r, rel_path=rel_path_under(r.get("local_path"), files_dir))
                   for r in images]
    reports_meta = [dict(r, content_dir=r["id"] if r.get("content_path") else None)
                    for r in reports]
    # 模板不随包（2.9 D5）：form_templates / report templates 均不导出，
    # 实侧行的 template_id 保留原值悬空容错（Task 0 已摘外键）

    manifest = {
        "format_version": FORMAT_VERSION,
        "app_version": os.environ.get("INKCUBE_APP_VERSION", "unknown"),
        "exported_at": _utc_now_iso(),
        "embedder": {"model": "bge-m3", "dim": 1024},
        "project": {k: project.get(k) for k in (
            "name", "description", "type", "stage", "company_name", "credit_code",
            "wiki_purpose", "wiki_overview", "playbook_seeded", "archived_at",
            # 2.6 §4.8 画像随包:manual 编辑是用户策展数据,不属可再生 AI 字段
            "industry_profile", "profile_source",
            "created_at", "updated_at")},
        "counts": {
            "files": len(files_meta), "images": len(images_meta),
            "reports": len(reports_meta), "form_fills": len(fills),
            "phase_tasks": len(phase_tasks),
            "wiki_pages": len(wiki_pages), "chunks": len(chunk_meta),
        },
        "skipped": {"files_processing": skipped_files,
                    "reports_generating": skipped_reports,
                    "forms_processing": skipped_fills},
    }

    tmp_dir = tempfile.mkdtemp(prefix="inkcube-export-")
    data_out = os.path.join(tmp_dir, "data")
    os.makedirs(data_out, exist_ok=True)
    try:
        def _dump(name: str, obj) -> None:
            with open(os.path.join(data_out, name), "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False)

        _dump("manifest.json", manifest)
        _dump("files.json", {"files": files_meta, "images": images_meta})
        np.savez_compressed(os.path.join(data_out, "chunks.npz"), vectors=vectors)
        _dump("chunks_meta.json", {"chunks": chunk_meta})
        _dump("wiki.json", {"pages": wiki_pages, "versions": wiki_page_versions,
                            "sources": wiki_page_sources, "reviews": wiki_reviews,
                            "change_log": wiki_change_log})
        _dump("reports.json", {"reports": reports_meta, "sections": sections, "logs": logs})
        _dump("affairs.json", {"phase_tasks": phase_tasks})
        _dump("forms.json", {"form_fills": fills})

        for f in files_meta:
            if f["rel_path"]:
                shutil.copytree(os.path.join(files_dir, f["id"]),
                                os.path.join(data_out, "blobs", "files", f["id"]))
        for r in reports_meta:
            if os.path.isdir(r.get("content_path") or ""):
                shutil.copytree(r["content_path"],
                                os.path.join(data_out, "blobs", "reports", r["id"]))
        for fill in fills:
            src = fill.get("output_path")
            if src and os.path.isfile(src):
                os.makedirs(os.path.join(data_out, "blobs", "form_fills"), exist_ok=True)
                shutil.copy2(src, os.path.join(data_out, "blobs",
                                               "form_fills", f"{fill['id']}.docx"))

        progress(60, "zipping")
        os.makedirs(os.path.dirname(os.path.abspath(target_path)) or ".", exist_ok=True)
        tmp_zip = target_path + ".tmp"
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            # 先集齐清单算总量，按已写字节数推进 60→95（Review m6）：zip 是最耗时
            # 阶段，不再 60 一卡数分钟；进度单调且 < 100，收尾仍由下方报 100
            entries = []
            for root, _dirs, fnames in os.walk(data_out):
                for fn in fnames:
                    full = os.path.join(root, fn)
                    entries.append((full, os.path.getsize(full),
                                    os.path.relpath(full, tmp_dir)))
            total = sum(sz for _, sz, _ in entries) or 1
            done = 0
            for full, sz, arc in entries:
                zf.write(full, arc)
                done += sz
                progress(60 + int(35 * done / total), "zipping")
        os.replace(tmp_zip, target_path)   # 原子落盘：中途失败不产生半个 .inkproj
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    progress(100, "done")
    logger.info("Exported project %s -> %s (counts=%s, skipped=%s)",
                project_id, target_path, manifest["counts"], manifest["skipped"])
    return {"path": target_path, "counts": manifest["counts"], "skipped": manifest["skipped"]}
