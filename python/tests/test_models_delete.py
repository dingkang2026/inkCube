"""python/tests/test_models_delete.py — 单模型删除全链单测（2026-10-20 删除功能审计）。

覆盖 model_manager.delete_model 三层状态收口 + DELETE /models/{name} 端点契约：
- bundled / 未知模型拒绝（fake MODELS 注入，不随真实清单漂移）
- 下载中拒绝（内存态判定，磁盘目录不动）
- 成功删除：磁盘目录 + model_downloads 行 + 内存下载态残留三收口
- 目录不存在的幂等删除（未下载态 ok:true, cleaned_bytes 0）
- 端点：未知 404；业务失败 200 + ok:false + code（对齐同文件 download 风格）

MODELS 注入说明：manager fixture 统一 patch knowledge.model_manager.MODELS
（delete_model 的未知/bundled 判定读此全局）；client 追加 patch
routers.models.MODELS（from-import 绑定，端点 404 判定读彼全局）——
两处须 patch 同一 fake 字典。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
import knowledge.model_manager as mm
from db.database import Database
from knowledge.model_manager import ModelManager
from routers import models as models_router

# 测试专用最小清单：download 型 + bundled 型各一（真实清单无 bundled 条目，
# 该防御分支当前不可达——配置未来可能加，分支语义须有测试锚定）
FAKE_MODELS = {
    "bge-m3": {"dir_name": "bge-m3", "install_type": "download",
               "marker_files": [], "min_size_bytes": 0},
    "bundled-thing": {"dir_name": "bundled", "install_type": "bundled"},
}


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "inkcube.db"))
    database.initialize()
    yield database
    database.conn.close()


@pytest.fixture
def manager(tmp_path, db, monkeypatch):
    monkeypatch.setattr("knowledge.model_manager.get_models_dir",
                        lambda: str(tmp_path / "models"))
    # bundled 防御分支不可达于真实清单（四模型均 download 型），全 fixture 级
    # 注入 fake 清单，TestDeleteModel 的分支语义测试不随真实清单漂移
    monkeypatch.setattr(mm, "MODELS", FAKE_MODELS)
    m = ModelManager()
    m.set_db(db)
    return m


@pytest.fixture
def client(manager, monkeypatch):
    monkeypatch.setattr(models_router, "MODELS", FAKE_MODELS)
    monkeypatch.setattr(deps, "model_manager", manager)
    app = FastAPI()
    app.include_router(models_router.router)
    return TestClient(app)


def _make_model_dir(manager: ModelManager, name: str) -> str:
    path = manager.get_model_path(name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "config.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    return path


class TestDeleteModel:
    def test_success_removes_dir_row_and_memory_state(self, manager, db):
        path = _make_model_dir(manager, "bge-m3")
        db.conn.execute(
            "INSERT INTO model_downloads (name, status, started_at, updated_at)"
            " VALUES ('bge-m3', 'completed', 't', 't')")
        db.conn.commit()
        manager._downloads["bge-m3"] = {"downloading": False, "progress": 100}

        result = manager.delete_model("bge-m3")

        assert result["ok"] is True
        assert result["cleaned_bytes"] > 0
        assert not os.path.isdir(path)
        remain = db.conn.execute(
            "SELECT COUNT(*) AS c FROM model_downloads"
            " WHERE name = 'bge-m3'").fetchone()["c"]
        assert remain == 0
        assert "bge-m3" not in manager._downloads

    def test_bundled_rejected(self, manager):
        result = manager.delete_model("bundled-thing")
        assert result["ok"] is False
        assert result["code"] == "MODEL_BUNDLED"

    def test_downloading_rejected_keeps_dir(self, manager):
        path = _make_model_dir(manager, "bge-m3")
        manager._downloads["bge-m3"] = {"downloading": True, "progress": 10}

        result = manager.delete_model("bge-m3")

        assert result["ok"] is False
        assert result["code"] == "MODEL_DOWNLOAD_IN_PROGRESS"
        assert os.path.isdir(path)

    def test_unknown_model(self, manager):
        result = manager.delete_model("no-such-model")
        assert result["ok"] is False
        assert result["code"] == "MODEL_NOT_FOUND"

    def test_missing_dir_idempotent_ok(self, manager):
        """未下载（目录不存在）删除幂等成功：UI 状态收口仍需走完整链。"""
        result = manager.delete_model("bge-m3")
        assert result["ok"] is True
        assert result["cleaned_bytes"] == 0


class TestDeleteEndpoint:
    def test_unknown_model_404(self, client):
        assert client.delete("/models/no-such").status_code == 404

    def test_business_reject_returns_200_ok_false(self, client, manager):
        """业务拒绝（下载中）为 200 + ok:false + code，前端显式检查分支。"""
        manager._downloads["bge-m3"] = {"downloading": True, "progress": 10}

        resp = client.delete("/models/bge-m3")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "MODEL_DOWNLOAD_IN_PROGRESS"

    def test_success_contract(self, client, manager):
        _make_model_dir(manager, "bge-m3")

        resp = client.delete("/models/bge-m3")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["code"] == "MODEL_DELETED"
        assert body["cleaned_bytes"] > 0
