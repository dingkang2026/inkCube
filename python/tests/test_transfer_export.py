"""python/tests/test_transfer_export.py — 导出器单测（2.9 §5/§10）。

向量用显式 1024 维假向量灌入 Chroma（add_documents 支持 embeddings 预计算），
不加载 bge-m3；FTS chunk 行直插 chunks_fts；图片描述向量只入 Chroma
（2.9 §4.4 现状），验证全量导出不漏。
"""
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from db.database import Database
from knowledge.fts_store import FTSStore
from knowledge.vector_store import VectorStore
from transfer.exporter import TransferError, export_project

DIM = 1024


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    for sub in ("chroma", "files", "reports", "form_templates", "form_fills"):
        (d / sub).mkdir(parents=True)
    return str(d)


def _seed_project(db, data_dir) -> str:
    pid = db.create_project(name="Alpha", project_type="general")["id"]
    f1 = db.create_file(pid, "a.txt", ".txt", 10, "h1")["id"]
    db.create_file(pid, "b.txt", ".txt", 10, "h2")   # 保持 pending → 应被跳过
    db.update_file_status(f1, "done", chunk_count=1)
    FTSStore(db).add_chunk(f"chunk_{f1}_0", "hello world", pid, "a.txt", "paragraph")
    vs = VectorStore(os.path.join(data_dir, "chroma"))
    vs.add_documents(
        pid, [f"chunk_{f1}_0"], ["hello world"],
        [{"file_id": f1, "filename": "a.txt", "content_type": "paragraph"}],
        embeddings=[[0.1] * DIM])
    # 图片描述向量：只入 Chroma 不入 FTS（2.9 §4.4 现状）——全量导出必须带上
    vs.add_documents(
        pid, [f"img_{f1}_0_img-old-1"], ["图：签约现场"],
        [{"file_id": f1, "image_id": "img-old-1", "markdown_content": "图：签约现场"}],
        embeddings=[[0.2] * DIM])
    os.makedirs(os.path.join(data_dir, "files", pid, f1), exist_ok=True)
    with open(os.path.join(data_dir, "files", pid, f1, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello")
    return pid


def test_export_package_layout(db, data_dir, tmp_path):
    pid = _seed_project(db, data_dir)
    target = str(tmp_path / "out.inkproj")

    result = export_project(db, data_dir, pid, target)

    assert os.path.isfile(target)
    assert result["path"] == target
    assert result["counts"]["files"] == 1
    assert result["counts"]["chunks"] == 2   # 文本 chunk + 图片描述向量
    assert result["skipped"] == {"files_processing": 1, "reports_generating": 0,
                                 "forms_processing": 0}

    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
        for required in ("data/manifest.json", "data/files.json", "data/chunks.npz",
                         "data/chunks_meta.json", "data/wiki.json", "data/reports.json",
                         "data/affairs.json", "data/forms.json"):
            assert required in names
        assert "data/templates.json" not in names   # D5：模板不随包
        manifest = json.loads(zf.read("data/manifest.json").decode("utf-8"))
        assert manifest["format_version"] == 1
        assert manifest["embedder"] == {"model": "bge-m3", "dim": 1024}
        assert manifest["project"]["name"] == "Alpha"
        assert manifest["counts"]["files"] == 1

        files_payload = json.loads(zf.read("data/files.json").decode("utf-8"))
        assert len(files_payload["files"]) == 1
        f1 = files_payload["files"][0]
        assert f1["status"] == "done"
        assert f1["rel_path"] == f1["id"]           # blob 目录按旧 file_id 命名
        assert any(n.startswith("data/blobs/files/") for n in names)

        with zf.open("data/chunks.npz") as npz_file:
            npz = np.load(io.BytesIO(npz_file.read()))
            assert npz["vectors"].shape == (2, DIM)
        meta = json.loads(zf.read("data/chunks_meta.json").decode("utf-8"))
        by_id = {c["chunk_id"]: c for c in meta["chunks"]}
        assert set(by_id) == {f"chunk_{f1['id']}_0", f"img_{f1['id']}_0_img-old-1"}
        text_row = by_id[f"chunk_{f1['id']}_0"]
        assert text_row["has_vector"] is True and text_row["in_fts"] is True
        assert text_row["document"] == "hello world"
        assert text_row["metadata"]["filename"] == "a.txt"
        assert text_row["old_file_id"] == f1["id"]
        img_row = by_id[f"img_{f1['id']}_0_img-old-1"]
        assert img_row["has_vector"] is True and img_row["in_fts"] is False
        assert img_row["metadata"]["image_id"] == "img-old-1"   # metadata 原样保留


def test_export_missing_project_raises(db, data_dir, tmp_path):
    with pytest.raises(TransferError):
        export_project(db, data_dir, "nope", str(tmp_path / "x.inkproj"))


def test_export_progress_callback(db, data_dir, tmp_path):
    pid = _seed_project(db, data_dir)
    seen = []
    export_project(db, data_dir, pid, str(tmp_path / "p.inkproj"),
                   on_progress=lambda p, m: seen.append(p))
    assert seen[0] == 5 and seen[-1] == 100
    # zip 阶段中间进度（Review m6）：最耗时阶段不再 60→100 一卡到底，
    # 且按已写字节数单调递增
    zipping = [p for p in seen if 60 < p < 100]
    assert zipping and zipping == sorted(zipping)
