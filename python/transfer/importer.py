"""python/transfer/importer.py — 项目交换包导入（2.9project-transfer-design.md §6/§9）。

复制为新项目（D1）：所有 ID 重生成 + bundle 内引用重映射；chunk 文本与向量
重建（FTS add_chunks_batch + Chroma add_documents 预计算 embeddings，导入零 API 成本）。
模板不随包（D5）：实侧行 template_id 悬空直插（Task 0 已摘 form_fills 外键）；
基础库按 project_type 自动关联（D6，同 create_project）。
安全（§9）：zip-slip 防护；仅 json.loads + np.load(默认 allow_pickle=False)，禁 pickle。
失败补偿回滚（§6.5）：删半成品项目行（级联）+ Chroma collection + blobs 目录，不留半项目。
"""
import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone

import numpy as np

from transfer.exporter import FORMAT_VERSION, TransferError

logger = logging.getLogger(__name__)

SUPPORTED_EMBEDDER = {"model": "bge-m3", "dim": 1024}
_LINKED_MAPS = ("file", "report", "form_fill", "wiki_page")
_MAX_ENTRIES = 20000                     # zip 条目数上限（解压炸弹守卫，2.9 §9）
_MAX_UNCOMPRESSED_BYTES = 4 * 1024 ** 3  # 未压缩总大小上限 4GB


def _assert_safe_member(name: str) -> None:
    """zip-slip 防护（2.9 §9）：拒绝绝对路径与 .. 逃逸成员。"""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise TransferError(f"unsafe zip member: {name}")
    if any(p == ".." for p in normalized.split("/")):
        raise TransferError(f"unsafe zip member: {name}")


def validate_package(source_path: str) -> dict:
    """快速校验（同步，供导入端点先返回 400）：zip 结构、zip-slip、manifest、embedder 兼容。

    返回 manifest dict；不合法抛 TransferError。
    """
    if not os.path.isfile(source_path):
        raise TransferError("package file not found")
    try:
        with zipfile.ZipFile(source_path) as zf:
            for member in zf.namelist():
                _assert_safe_member(member)
            infos = zf.infolist()
            if len(infos) > _MAX_ENTRIES:
                raise TransferError("package has too many entries")
            if sum(i.file_size for i in infos) > _MAX_UNCOMPRESSED_BYTES:
                raise TransferError("package uncompressed size exceeds limit")
            if "data/manifest.json" not in zf.namelist():
                raise TransferError("manifest.json missing")
            manifest = json.loads(zf.read("data/manifest.json").decode("utf-8"))
    except zipfile.BadZipFile:
        raise TransferError("not a valid .inkproj package")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise TransferError(f"unsupported format_version: {manifest.get('format_version')}")
    emb = manifest.get("embedder") or {}
    if (emb.get("model") != SUPPORTED_EMBEDDER["model"]
            or int(emb.get("dim") or 0) != SUPPORTED_EMBEDDER["dim"]):
        raise TransferError(f"embedder mismatch: {emb}")
    if not (manifest.get("project") or {}).get("name"):
        raise TransferError("manifest.project.name missing")
    return manifest


def _remap_chunk_id(chunk_id: str, fid_map: dict[str, str]) -> str:
    """chunk_id 重生成（2.9 §6.3）：前缀（chunk_/img_）+ 旧 fid 前缀 → 新 fid。

    file_id 是 uuid4 字符串（不含下划线），`old_fid + "_"` 前缀匹配安全。
    """
    for prefix in ("chunk_", "img_"):
        if chunk_id.startswith(prefix):
            rest = chunk_id[len(prefix):]
            for old_fid, new_fid in fid_map.items():
                token = old_fid + "_"
                if rest.startswith(token):
                    return prefix + new_fid + "_" + rest[len(token):]
    return "chunk_" + str(uuid.uuid4())   # 不明格式：保内容换新 id（防御，不应发生）


def _insert_row(db, table: str, row: dict, overrides: dict, or_ignore: bool = False) -> None:
    """动态列 INSERT：导出行是全列 dict，导入端覆写映射列后原样插入。"""
    merged = {**row, **overrides}
    cols = ", ".join(merged.keys())
    qmarks = ", ".join("?" * len(merged))
    verb = "INSERT OR IGNORE INTO" if or_ignore else "INSERT INTO"
    db.conn.execute(f"{verb} {table} ({cols}) VALUES ({qmarks})", tuple(merged.values()))


def _compensate(db, data_dir: str, new_project_id: str,
                extra_files: list[str] | None = None) -> None:
    """失败补偿（2.9 §6.5）：尽力清理半成品。自身异常只记日志，不掩盖原始失败。

    extra_files（Review M3）：DB 事务前已落盘的产物文件（form_fills docx）——
    行随项目级联删除后文件必须显式清理，否则成为永久孤儿。"""
    try:
        from knowledge.vector_store import VectorStore
        VectorStore(os.path.join(data_dir, "chroma")).delete_project(new_project_id)
    except Exception:
        logger.warning("compensate: chroma cleanup failed", exc_info=True)
    try:
        db.conn.execute("DELETE FROM projects WHERE id = ?", (new_project_id,))
        db.conn.commit()   # projects 行级联清子表；FTS 无 FK 需显式
        db.conn.execute("DELETE FROM chunks_fts WHERE project_id = ?", (new_project_id,))
        db.conn.commit()
    except Exception:
        logger.warning("compensate: db rollback failed", exc_info=True)
    shutil.rmtree(os.path.join(data_dir, "files", new_project_id), ignore_errors=True)
    shutil.rmtree(os.path.join(data_dir, "reports", new_project_id), ignore_errors=True)
    for p in (extra_files or []):
        try:
            os.remove(p)
        except OSError:
            logger.warning("compensate: remove %s failed", p, exc_info=True)


def run_import(db, data_dir: str, source_path: str, new_project_id: str,
               on_progress=None) -> dict:
    """执行导入（validate_package 已通过的前提下；调用方须在线程中运行）。

    任何失败走补偿回滚后 re-raise（2.9 §6.5）。返回 {"project_id", "counts"}。
    """
    from knowledge.fts_store import FTSStore
    from knowledge.vector_store import VectorStore

    progress = on_progress or (lambda p, m: None)
    tmp_dir = tempfile.mkdtemp(prefix="inkcube-import-")
    written_docx: list[str] = []   # DB 事务前已落盘的产物（补偿需显式清，Review M3）
    try:
        progress(5, "extracting package")
        with zipfile.ZipFile(source_path) as zf:
            for member in zf.namelist():
                _assert_safe_member(member)
            zf.extractall(tmp_dir)
        pkg = os.path.join(tmp_dir, "data")

        def _load(name):
            with open(os.path.join(pkg, name), encoding="utf-8") as fh:
                return json.load(fh)

        manifest = _load("manifest.json")
        counts = manifest.get("counts") or {}
        files_payload = _load("files.json")
        files_rows = files_payload["files"]
        images_rows = files_payload["images"]
        with np.load(os.path.join(pkg, "chunks.npz")) as npz:   # 默认 allow_pickle=False
            vectors = npz["vectors"]
        chunk_meta = _load("chunks_meta.json")["chunks"]
        wiki = _load("wiki.json")
        reports_payload = _load("reports.json")
        phase_tasks = _load("affairs.json")["phase_tasks"]
        fills = _load("forms.json")["form_fills"]

        # ── ID 重映射（2.9 §6.3）：bundle 内全部 id 重生成 ──
        progress(25, "remapping ids")
        def _new_id() -> str:
            return str(uuid.uuid4())

        fid_map = {r["id"]: _new_id() for r in files_rows}
        # 导出端附加的包内元数据字段（rel_path/content_dir）非表列，插入前剔除
        for r in files_rows:
            r.pop("rel_path", None)

        # ── blobs 还原 + 路径重写（先于 DB 事务，行内路径以最终值插入）──
        progress(40, "restoring blobs")
        proj_files_dir = os.path.join(data_dir, "files", new_project_id)
        os.makedirs(proj_files_dir, exist_ok=True)
        for old_fid, nf in fid_map.items():
            src = os.path.join(pkg, "blobs", "files", old_fid)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(proj_files_dir, nf))

        # images：local_path 重写；文件缺失的 image 行跳过（列 NOT NULL）
        kept_images = []
        for r in images_rows:
            rel = r.get("rel_path")
            local_abs = None
            if rel:
                parts = rel.split("/", 1)
                new_rel = fid_map.get(parts[0], parts[0]) + (f"/{parts[1]}" if len(parts) > 1 else "")
                cand = os.path.join(proj_files_dir, *new_rel.split("/"))
                if os.path.isfile(cand):
                    local_abs = cand
            if local_abs:
                r["local_path"] = local_abs
                kept_images.append(r)
        images_rows = kept_images
        for r in images_rows:
            r.pop("rel_path", None)
        iid_map = {r["id"]: _new_id() for r in images_rows}

        rid_map = {r["id"]: _new_id() for r in reports_payload["reports"]}
        # reports：content_path 重写为新目录（以新 rid 命名，与下方 blob 恢复一致）；
        # blob 缺失置 None（列可空，行保留）
        for r in reports_payload["reports"]:
            r.pop("content_dir", None)   # 导出附加字段（blob 键），非表列
            # 源机基础库 id 快照不随包迁移（D5/D6）：置空防悬空检索腿静默返空，
            # 及 get_or_create_collection 为不存在的 id 创建垃圾空 collection（Review M2）
            r["base_project_ids"] = None
            if os.path.isdir(os.path.join(pkg, "blobs", "reports", r["id"])):
                r["content_path"] = os.path.join(
                    data_dir, "reports", new_project_id, rid_map[r["id"]])
            else:
                r["content_path"] = None
        for r in reports_payload["reports"]:
            src = os.path.join(pkg, "blobs", "reports", r["id"])
            if os.path.isdir(src):
                dst = os.path.join(data_dir, "reports", new_project_id, rid_map[r["id"]])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copytree(src, dst)
        sec_map = {r["id"]: _new_id() for r in reports_payload["sections"]}
        log_map = {r["id"]: _new_id() for r in reports_payload["logs"]}

        # 模板不随包（2.9 D5）：fills 原样直插，template_id 悬空保留
        # （Task 0 已摘 form_fills 外键；查看/修订走悬空容错提示）
        ff_map = {r["id"]: _new_id() for r in fills}
        fills_dir = os.path.join(data_dir, "form_fills")
        os.makedirs(fills_dir, exist_ok=True)
        for f in fills:
            src = os.path.join(pkg, "blobs", "form_fills", f"{f['id']}.docx")
            if os.path.isfile(src):
                dst = os.path.join(fills_dir, f"{ff_map[f['id']]}.docx")
                shutil.copy2(src, dst)
                written_docx.append(dst)
                f["output_path"] = dst
            else:
                f["output_path"] = None   # 产物丢失：记录保留，文件不可下载

        wp_map = {r["id"]: _new_id() for r in wiki["pages"]}
        wpv_map = {r["id"]: _new_id() for r in wiki.get("versions") or []}
        wps_map = {r["id"]: _new_id() for r in wiki["sources"]}
        wcl_map = {r["id"]: _new_id() for r in wiki["change_log"]}
        wrv_map = {r["id"]: _new_id() for r in wiki["reviews"]}
        pt_map = {r["id"]: _new_id() for r in phase_tasks}

        def _opt(mapping: dict, key):
            """悬挂引用容错：映射不到（源行被跳过）置 None（仅用于可空列）。"""
            return mapping.get(key) if key is not None else None

        # ── 单事务插入（2.9 §6.2；transaction 与 create_project 同款事务级锁）──
        progress(55, "writing database rows")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        p = manifest["project"]
        project_row = {
            "id": new_project_id, "name": p["name"],
            "description": p.get("description") or "",
            "type": p.get("type") or "general", "stage": p.get("stage") or "none",
            "company_name": p.get("company_name") or "",
            "credit_code": p.get("credit_code") or "",
            "kind": "project", "lib_type": None,
            "wiki_purpose": p.get("wiki_purpose") or "",
            "wiki_overview": p.get("wiki_overview") or "",
            "playbook_seeded": 1 if p.get("playbook_seeded") else 0,
            "archived_at": p.get("archived_at"),
            "pending_compile_count": 0, "pulse_ai_status": "",
            "pulse_ai_status_at": None,
            "created_at": p.get("created_at") or now, "updated_at": now,
        }
        linked_maps = {
            "file": fid_map, "report": rid_map, "form_fill": ff_map, "wiki_page": wp_map,
        }
        with db.conn.transaction():
            _insert_row(db, "projects", project_row, {})
            for r in files_rows:
                _insert_row(db, "files", r,
                            {"id": fid_map[r["id"]], "project_id": new_project_id})
            for r in images_rows:
                _insert_row(db, "images", r,
                            {"id": iid_map[r["id"]], "file_id": fid_map[r["file_id"]]})
            for r in reports_payload["reports"]:
                _insert_row(db, "reports", r,
                            {"id": rid_map[r["id"]], "project_id": new_project_id,
                             "pause_requested": 0})
            for r in reports_payload["sections"]:
                _insert_row(db, "report_sections", r,
                            {"id": sec_map[r["id"]], "report_id": rid_map[r["report_id"]]})
            for r in reports_payload["logs"]:
                _insert_row(db, "report_logs", r,
                            {"id": log_map[r["id"]], "report_id": rid_map[r["report_id"]]})
            for r in fills:   # 模板不随包（D5）：template_id 悬空保留原值
                _insert_row(db, "form_fills", r,
                            {"id": ff_map[r["id"]], "project_id": new_project_id})
            for r in wiki["pages"]:
                _insert_row(db, "wiki_pages", r,
                            {"id": wp_map[r["id"]], "project_id": new_project_id,
                             "last_compiled_file_id": _opt(fid_map, r.get("last_compiled_file_id"))})
            for r in wiki.get("versions") or []:
                _insert_row(db, "wiki_page_versions", r,
                            {"id": wpv_map[r["id"]], "wiki_page_id": wp_map[r["wiki_page_id"]],
                             "project_id": new_project_id})
            for r in wiki["sources"]:
                if r.get("file_id") not in fid_map:
                    continue   # 悬挂引用（不应发生——导出端已过滤；列 NOT NULL）
                _insert_row(db, "wiki_page_sources", r,
                            {"id": wps_map[r["id"]], "wiki_page_id": wp_map[r["wiki_page_id"]],
                             "file_id": fid_map[r["file_id"]]})
            for r in wiki["change_log"]:
                _insert_row(db, "wiki_change_log", r,
                            {"id": wcl_map[r["id"]], "project_id": new_project_id,
                             "wiki_page_id": _opt(wp_map, r.get("wiki_page_id")),
                             "trigger_file_id": _opt(fid_map, r.get("trigger_file_id"))})
            for r in wiki["reviews"]:
                # affected_page_ids 是 wiki_page_id 的 JSON 数组（TEXT 列）：
                # 逐个重映射、悬挂条目剔除，全空置 NULL（2.9 §6.3）
                raw = r.get("affected_page_ids")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except (ValueError, TypeError):
                        raw = []
                remapped = [wp_map[pid] for pid in (raw or []) if pid in wp_map]
                _insert_row(db, "wiki_reviews", r,
                            {"id": wrv_map[r["id"]], "project_id": new_project_id,
                             "source_file_id": _opt(fid_map, r.get("source_file_id")),
                             "affected_page_ids":
                                 json.dumps(remapped, ensure_ascii=False) if remapped else None})
            for r in phase_tasks:
                lm = linked_maps.get(r.get("linked_entity_type") or "")
                _insert_row(db, "phase_tasks", r,
                            {"id": pt_map[r["id"]], "project_id": new_project_id,
                             "linked_entity_id": _opt(lm, r.get("linked_entity_id")) if lm
                             else r.get("linked_entity_id")})
        # 基础库自动关联（2.9 D6）：事务提交后按 project_type 关联
        # （get_auto_linked_base_libs + link_project_to_base_lib(auto=True)，
        # 与 create_project 同款；general 无映射则不关联）
        for lib_id in db.get_auto_linked_base_libs(p.get("type") or "general"):
            db.link_project_to_base_lib(new_project_id, lib_id, auto=True)

        # ── FTS / Chroma 重建（事务提交后；失败走补偿）──
        progress(70, "rebuilding search index")
        fts_rows = []
        vec_ids: list[str] = []
        vec_docs: list[str] = []
        vec_metas: list[dict] = []
        vecs: list[list[float]] = []
        vec_iter = iter(vectors) if vectors.size else iter(())
        for c in chunk_meta:
            # metadata 原样写回（markdown_content/section_path/page/chunk_index 等
            # 检索消费依赖，2.9 §6.3），仅重映射 file_id / img 的 image_id
            meta_src = c.get("metadata") or {}
            new_meta = dict(meta_src)
            new_meta["file_id"] = fid_map.get(c.get("old_file_id"))
            if c["chunk_id"].startswith("img_"):
                old_image_id = meta_src.get("image_id")
                if old_image_id not in iid_map:
                    continue   # image 行被跳过 → 其向量与索引一并跳过
                new_meta["image_id"] = iid_map[old_image_id]
            new_cid = _remap_chunk_id(c["chunk_id"], fid_map)
            if c.get("in_fts"):
                fts_rows.append({
                    "chunk_id": new_cid, "content": c["document"],
                    "project_id": new_project_id,
                    "filename": meta_src.get("filename"),
                    "content_type": meta_src.get("content_type")})
            if c["has_vector"]:
                vec_ids.append(new_cid)
                vec_docs.append(c["document"])
                vec_metas.append(new_meta)
                vecs.append(next(vec_iter).tolist())
        FTSStore(db).add_chunks_batch(fts_rows)

        progress(85, "restoring vectors")
        if vec_ids:
            VectorStore(os.path.join(data_dir, "chroma")).add_documents(
                project_id=new_project_id, ids=vec_ids, documents=vec_docs,
                metadatas=vec_metas, embeddings=vecs)

        progress(100, "done")
        logger.info("Imported %s -> project %s (counts=%s)",
                    source_path, new_project_id, counts)
        return {"project_id": new_project_id, "counts": counts}
    except Exception:
        _compensate(db, data_dir, new_project_id, extra_files=written_docx)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
