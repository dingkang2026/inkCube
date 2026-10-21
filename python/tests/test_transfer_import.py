"""python/tests/test_transfer_import.py — 导入器单测（2.9 §6/§10）。

覆盖：包校验拒绝矩阵（坏 zip / 缺 manifest / 版本不符 / embedder 不符 / zip-slip /
解压炸弹）、导出→导入往返一致性（含 FTS/Chroma 重建、img 向量重映射、模板
悬空容错、基础库自动关联）、DB 事务阶段与 Chroma 阶段失败的补偿回滚。
向量用假 1024 维，不加载 bge-m3。
"""
import io
import json
import os
import sys
import uuid
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from db.database import Database
from knowledge.fts_store import FTSStore
from knowledge.vector_store import VectorStore
from transfer.exporter import export_project
from transfer.importer import TransferError, run_import, validate_package

DIM = 1024


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    # 同真实启动顺序 seed 预置库：create_project(recruitment) 内部即自动关联
    # bl-recruitment，不 seed 则 project_base_libs FK 失败
    database.ensure_preset_base_libs()
    return database


def _make_data_dir(tmp_path, name="data"):
    d = tmp_path / name
    for sub in ("chroma", "files", "reports", "form_templates", "form_fills"):
        (d / sub).mkdir(parents=True)
    return str(d)


def _seed_project(db, data_dir, project_type="general") -> str:
    pid = db.create_project(name="Alpha", project_type=project_type)["id"]
    f1 = db.create_file(pid, "a.txt", ".txt", 10, "h1")["id"]
    db.update_file_status(f1, "done", chunk_count=1)
    FTSStore(db).add_chunk(f"chunk_{f1}_0", "hello world", pid, "a.txt", "paragraph")
    vs = VectorStore(os.path.join(data_dir, "chroma"))
    vs.add_documents(
        pid, [f"chunk_{f1}_0"], ["hello world"],
        [{"file_id": f1, "filename": "a.txt", "content_type": "paragraph"}],
        embeddings=[[0.1] * DIM])
    # 图片描述向量：只入 Chroma 不入 FTS（2.9 §4.4 现状）——roundtrip 必须重建。
    # 用反平行向量 [-0.2]：与文本 [0.1] 的 cosine 距离=2，否则平行向量
    # cosine 等价（距离均 0），检索断言会双命中
    vs.add_documents(
        pid, [f"img_{f1}_0_img-old-1"], ["图：签约现场"],
        [{"file_id": f1, "image_id": "img-old-1", "markdown_content": "图：签约现场"}],
        embeddings=[[-0.2] * DIM])
    os.makedirs(os.path.join(data_dir, "files", pid, f1), exist_ok=True)
    with open(os.path.join(data_dir, "files", pid, f1, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello")
    return pid


def _seed_full(db, data_dir):
    """文件+向量 + 报告 + wiki 页/版本/来源 + 填表模板/实例 + 事务任务。返回 (pid, refs)。"""
    pid = _seed_project(db, data_dir, project_type="recruitment")   # D6 自动关联断言用
    f1 = [f for f in db.list_files(pid) if f["status"] == "done"][0]["id"]

    # images 行：img 向量的 image_id 映射闭合（导出全量、导入重映射）
    img_path = os.path.join(data_dir, "files", pid, f1, "img.png")
    with open(img_path, "w", encoding="utf-8") as fh:
        fh.write("png")
    db.conn.execute(
        "INSERT INTO images (id, file_id, page_no, local_path, status, created_at) "
        "VALUES ('img-old-1', ?, 0, ?, 'done', datetime('now'))", (f1, img_path))

    rid = "rep-old-1"
    rdir = os.path.join(data_dir, "reports", pid, rid)
    os.makedirs(rdir, exist_ok=True)
    with open(os.path.join(rdir, "content.md"), "w", encoding="utf-8") as fh:
        fh.write("# content")
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
        "progress, content_path, base_project_ids, created_at, updated_at) "
        "VALUES (?, ?, 'tpl-1', '报告A', 'docx', 'done', 100, ?, '[\"bl-src-1\"]', "
        "datetime('now'), datetime('now'))",
        (rid, pid, rdir))
    db.conn.execute(
        "INSERT INTO templates (id, name, template_json, is_custom, created_at, updated_at) "
        "VALUES ('tpl-1', '自定义模板', '{}', 1, datetime('now'), datetime('now'))")

    wp = "wp-old-1"
    db.conn.execute(
        "INSERT INTO wiki_pages (id, project_id, page_key, title, content, status, created_at, updated_at) "
        "VALUES (?, ?, 'milestones', '里程碑', 'v1', 'compiled', datetime('now'), datetime('now'))",
        (wp, pid))
    db.conn.execute(
        "INSERT INTO wiki_page_sources (id, wiki_page_id, file_id, created_at) "
        "VALUES ('wps-old-1', ?, ?, datetime('now'))", (wp, f1))
    wpv = "wpv-old-1"
    db.conn.execute(
        "INSERT INTO wiki_page_versions (id, wiki_page_id, project_id, page_key, "
        "title, content, version, created_at) "
        "VALUES (?, ?, ?, 'milestones', '里程碑', 'v1', 1, datetime('now'))",
        (wpv, wp, pid))

    tpl_path = os.path.join(data_dir, "form_templates", "tpl-f-1.docx")
    with open(tpl_path, "w", encoding="utf-8") as fh:
        fh.write("docx")
    db.conn.execute(
        "INSERT INTO form_templates (id, name, source, file_path, status, created_at, updated_at) "
        "VALUES ('tpl-f-1', '模板A', 'upload', ?, 'success', datetime('now'), datetime('now'))",
        (tpl_path,))
    ff = "ff-old-1"
    os.makedirs(os.path.join(data_dir, "form_fills"), exist_ok=True)
    out_path = os.path.join(data_dir, "form_fills", f"{ff}.docx")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("docx")
    db.conn.execute(
        "INSERT INTO form_fills (id, project_id, template_id, title, output_path, status, "
        "progress, created_at, updated_at) "
        "VALUES (?, ?, 'tpl-f-1', '填表A', ?, 'success', 100, datetime('now'), datetime('now'))",
        (ff, pid, out_path))

    pt = "pt-old-1"
    db.conn.execute(
        "INSERT INTO phase_tasks (id, project_id, phase, task_type, title, status, created_at) "
        "VALUES (?, ?, 'during', 'milestone', '签约', 'done', datetime('now'))", (pt, pid))
    db.conn.commit()
    return pid, {"f1": f1, "rid": rid, "wp": wp, "wpv": wpv, "ff": ff, "pt": pt}


def test_validate_rejects_bad_zip(tmp_path):
    bad = tmp_path / "bad.inkproj"
    bad.write_bytes(b"not a zip")
    with pytest.raises(TransferError):
        validate_package(str(bad))


def test_validate_rejects_version_and_embedder(tmp_path):
    pkg = tmp_path / "v.inkproj"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("data/manifest.json", json.dumps(
            {"format_version": 99, "embedder": {"model": "bge-m3", "dim": 1024},
             "project": {"name": "X"}}))
    with pytest.raises(TransferError):
        validate_package(str(pkg))

    pkg2 = tmp_path / "e.inkproj"
    with zipfile.ZipFile(pkg2, "w") as zf:
        zf.writestr("data/manifest.json", json.dumps(
            {"format_version": 1, "embedder": {"model": "other", "dim": 768},
             "project": {"name": "X"}}))
    with pytest.raises(TransferError):
        validate_package(str(pkg2))


def test_zip_slip_rejected(tmp_path):
    evil = tmp_path / "evil.inkproj"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../evil.txt", "boom")
    with pytest.raises(TransferError):
        validate_package(str(evil))


def test_validate_rejects_bomb(tmp_path, monkeypatch):
    """解压炸弹守卫（2.9 §9）：条目数与未压缩总大小超限拒绝。"""
    import transfer.importer as importer_mod
    pkg = tmp_path / "many.inkproj"
    with zipfile.ZipFile(pkg, "w") as zf:
        for i in range(5):
            zf.writestr(f"data/fill_{i}.bin", b"0" * 100)
    monkeypatch.setattr(importer_mod, "_MAX_ENTRIES", 3)
    with pytest.raises(TransferError):
        validate_package(str(pkg))
    monkeypatch.setattr(importer_mod, "_MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(TransferError):
        validate_package(str(pkg))


def test_roundtrip_full(db, tmp_path):
    data_dir = _make_data_dir(tmp_path, "data1")
    pid, refs = _seed_full(db, data_dir)
    pkg = str(tmp_path / "swap.inkproj")
    export_project(db, data_dir, pid, pkg)

    # 模拟另一台机器：全新 DB + 数据目录（含预置库 seed，模拟真实启动顺序）
    db2 = Database(db_path=":memory:")
    db2.initialize()
    db2.ensure_preset_base_libs()
    data_dir2 = _make_data_dir(tmp_path, "data2")
    new_pid = str(uuid.uuid4())

    result = run_import(db2, data_dir2, pkg, new_pid)
    assert result["project_id"] == new_pid

    # 项目属性保留、行数一致
    proj = db2.get_project(new_pid)
    assert proj["name"] == "Alpha"
    assert proj["kind"] == "project"
    files2 = db2.list_files(new_pid)
    assert len(files2) == 1
    new_f1 = files2[0]["id"]
    assert new_f1 != refs["f1"]

    # 报告：行重映射、产物目录还原
    reports2 = db2.conn.execute(
        "SELECT * FROM reports WHERE project_id = ?", (new_pid,)).fetchall()
    assert len(reports2) == 1
    assert reports2[0]["id"] != refs["rid"]
    assert os.path.isdir(reports2[0]["content_path"])
    # 报告模板不随包（D5）：template_id 悬空保留（reports 本无外键）
    assert reports2[0]["template_id"] == "tpl-1"
    # 源机基础库 id 快照不随包迁移（Review M2）：悬空快照会让检索腿静默返空，
    # 且 get_or_create_collection 为不存在的 id 创建垃圾空 collection——导入端置空
    assert reports2[0]["base_project_ids"] is None
    assert db2.conn.execute(
        "SELECT COUNT(*) AS c FROM templates WHERE id = 'tpl-1'").fetchone()["c"] == 0

    # wiki：页重映射 + source 指向新 file + 版本历史随行重映射
    pages2 = db2.conn.execute(
        "SELECT * FROM wiki_pages WHERE project_id = ?", (new_pid,)).fetchall()
    assert len(pages2) == 1 and pages2[0]["id"] != refs["wp"]
    src2 = db2.conn.execute(
        "SELECT * FROM wiki_page_sources WHERE wiki_page_id = ?",
        (pages2[0]["id"],)).fetchone()
    assert src2["file_id"] == new_f1
    ver2 = db2.conn.execute(
        "SELECT * FROM wiki_page_versions WHERE wiki_page_id = ?",
        (pages2[0]["id"],)).fetchone()
    assert ver2 is not None and ver2["id"] != refs["wpv"]
    assert ver2["project_id"] == new_pid

    # 填表：模板不随包（D5）——新库无模板行、实例悬空落库（template_id 原值）、产物落盘
    assert db2.conn.execute(
        "SELECT COUNT(*) AS c FROM form_templates WHERE id = 'tpl-f-1'"
    ).fetchone()["c"] == 0
    fills2 = db2.conn.execute(
        "SELECT * FROM form_fills WHERE project_id = ?", (new_pid,)).fetchall()
    assert len(fills2) == 1 and fills2[0]["id"] != refs["ff"]
    assert fills2[0]["template_id"] == "tpl-f-1"   # 悬空保留（LEFT JOIN 列表可见）
    assert os.path.isfile(fills2[0]["output_path"])

    # 事务任务重映射 + 基础库自动关联（D6：recruitment → 仅 bl-recruitment）
    tasks2 = db2.conn.execute(
        "SELECT * FROM phase_tasks WHERE project_id = ?", (new_pid,)).fetchall()
    assert len(tasks2) == 1 and tasks2[0]["id"] != refs["pt"]
    libs2 = db2.conn.execute(
        "SELECT base_lib_id, auto_linked FROM project_base_libs WHERE project_id = ?",
        (new_pid,)).fetchall()
    assert [b["base_lib_id"] for b in libs2] == ["bl-recruitment"]
    assert libs2[0]["auto_linked"] == 1

    # FTS 重建：chunk_id 以新 fid 为前缀，内容可检索（img 向量不入 FTS）
    fts_rows = db2.conn.execute(
        "SELECT chunk_id, content FROM chunks_fts WHERE project_id = ?",
        (new_pid,)).fetchall()
    assert len(fts_rows) == 1
    assert fts_rows[0]["chunk_id"].startswith(f"chunk_{new_f1}_")

    # Chroma 重建：假向量可检索回（cosine 最近邻即自身）；img 向量随行重建。
    # top_k=5 > 集合大小 2，两向量均返回；首条必须是最近邻（距离 0）
    vs2 = VectorStore(os.path.join(data_dir2, "chroma"))
    hits = vs2.search_dense(new_pid, [0.1] * DIM, top_k=5)
    assert len(hits) == 2
    assert hits[0]["id"].startswith(f"chunk_{new_f1}_")
    assert hits[0]["metadata"]["file_id"] == new_f1
    imgs2 = db2.conn.execute("SELECT * FROM images WHERE file_id = ?",
                             (new_f1,)).fetchall()
    assert len(imgs2) == 1 and os.path.isfile(imgs2[0]["local_path"])
    img_hits = vs2.search_dense(new_pid, [-0.2] * DIM, top_k=5)
    assert len(img_hits) == 2
    assert img_hits[0]["id"].startswith(f"img_{new_f1}_")
    assert img_hits[0]["metadata"]["image_id"] == imgs2[0]["id"]   # image_id 重映射
    assert img_hits[0]["metadata"]["file_id"] == new_f1

    # 原文件 blob 还原
    assert os.path.isfile(os.path.join(data_dir2, "files", new_pid, new_f1, "a.txt"))


def test_import_failure_compensates(db, tmp_path):
    data_dir = _make_data_dir(tmp_path, "data1")
    pid, _refs = _seed_full(db, data_dir)
    pkg_path = str(tmp_path / "swap.inkproj")
    export_project(db, data_dir, pid, pkg_path)

    db2 = Database(db_path=":memory:")
    db2.initialize()
    db2.ensure_preset_base_libs()   # run_import 事务后自动关联（D6）需预置库在位
    data_dir2 = _make_data_dir(tmp_path, "data2")
    new_pid = str(uuid.uuid4())

    # 篡改包：affairs.json 损坏 → run_import 中途抛异常
    with zipfile.ZipFile(pkg_path) as zf:
        items = {n: zf.read(n) for n in zf.namelist()}
    items["data/affairs.json"] = b"{broken json"
    tampered = str(tmp_path / "tampered.inkproj")
    with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, d in items.items():
            zf.writestr(n, d)

    with pytest.raises(Exception):
        run_import(db2, data_dir2, tampered, new_pid)

    # 补偿：不留半项目
    assert db2.get_project(new_pid) is None
    cnt = db2.conn.execute(
        "SELECT COUNT(*) AS c FROM chunks_fts WHERE project_id = ?",
        (new_pid,)).fetchone()["c"]
    assert cnt == 0
    assert not os.path.exists(os.path.join(data_dir2, "files", new_pid))


def test_import_failure_at_chroma_compensates(db, tmp_path, monkeypatch):
    """Chroma 重建阶段失败（2.9 §10）：DB/集合/目录三清，不留半项目。"""
    data_dir = _make_data_dir(tmp_path, "data1")
    pid, _refs = _seed_full(db, data_dir)
    pkg_path = str(tmp_path / "swap.inkproj")
    export_project(db, data_dir, pid, pkg_path)

    db2 = Database(db_path=":memory:")
    db2.initialize()
    db2.ensure_preset_base_libs()   # 自动关联（事务后、Chroma 前）需预置库存在，
    # 否则 FK 失败点前移、异常类型不再是预期的 RuntimeError
    data_dir2 = _make_data_dir(tmp_path, "data2")
    new_pid = str(uuid.uuid4())

    from knowledge import vector_store as vs_mod

    def _boom(self, *args, **kwargs):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(vs_mod.VectorStore, "add_documents", _boom)
    with pytest.raises(RuntimeError):
        run_import(db2, data_dir2, pkg_path, new_pid)

    assert db2.get_project(new_pid) is None
    assert db2.conn.execute(
        "SELECT COUNT(*) AS c FROM chunks_fts WHERE project_id = ?",
        (new_pid,)).fetchone()["c"] == 0
    assert not os.path.exists(os.path.join(data_dir2, "files", new_pid))
    # form_fills 产物在 DB 事务前已落盘（Review M3）：补偿必须一并清掉，
    # 否则每次导入失败泄漏包内全部填表产物
    residual = [n for n in os.listdir(os.path.join(data_dir2, "form_fills"))
                if n.endswith(".docx")]
    assert residual == []
    assert VectorStore(os.path.join(data_dir2, "chroma")).search_dense(
        new_pid, [0.1] * DIM, top_k=5) == []
