"""python/tests/test_models_preload.py — POST /models/preload 幂等预热回归测试。

回归背景：bge-m3 预加载原在 lifespan 启动即触发，与渲染层模块装载争抢
CPU/磁盘拖慢 dev 冷启动首屏（2026-10-13 实测 DCL 84s）；现移出启动链，
由 Electron 窗口 did-finish-load 调用本端点（根因修复）。端点必须幂等：
整页刷新重复触发不得重复起加载线程；模型未下载时明确跳过。
"""
import os
import sys
import threading
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
import routers.models as models_router
from routers.models import router


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_preload_thread():
    """模块级线程句柄在用例间隔离，避免互相判定为「加载中」。"""
    models_router._preload_thread = None
    yield
    models_router._preload_thread = None


@pytest.fixture()
def preload_env(monkeypatch, tmp_path):
    """model_manager mock + preload_bge_m3 桩（隔离真实 CUDA 模型加载）。

    fake_preload 记录调用 → set started → 等 release（模拟加载耗时窗口）；
    teardown 强制放行，保证 daemon 线程不跨用例残留。
    """
    manager = MagicMock()
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    manager.get_model_path.return_value = str(model_dir)
    monkeypatch.setattr(deps, "model_manager", manager)

    calls = []
    started = threading.Event()
    release = threading.Event()

    def fake_preload(path):
        calls.append(path)
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr("knowledge.vector_store.preload_bge_m3", fake_preload)
    yield manager, calls, started, release
    release.set()


class TestPreloadEndpoint:
    def test_model_not_downloaded_skipped(self, client, preload_env):
        """模型目录不存在：ok=False + MODEL_NOT_DOWNLOADED，不起线程。"""
        manager, calls, _, _ = preload_env
        manager.get_model_path.return_value = os.path.join(
            os.path.dirname(__file__), "no-such-model-dir")

        resp = client.post("/models/preload")

        assert resp.status_code == 200
        assert resp.json() == {
            "ok": False, "message": "bge-m3 模型未下载", "code": "MODEL_NOT_DOWNLOADED",
        }
        assert calls == []
        assert models_router._preload_thread is None

    def test_preload_starts_background_thread(self, client, preload_env):
        """模型就绪：PRELOAD_STARTED，后台线程执行真实预热调用。"""
        _, calls, started, _ = preload_env

        resp = client.post("/models/preload")

        assert resp.status_code == 200
        assert resp.json()["code"] == "PRELOAD_STARTED"
        assert started.wait(timeout=5), "预热线程未执行 preload_bge_m3"
        assert models_router._preload_thread is not None
        assert models_router._preload_thread.name == "bge-m3-preload"
        assert len(calls) == 1

    def test_repeat_trigger_while_loading_is_noop(self, client, preload_env):
        """加载进行中重复触发（整页刷新场景）：PRELOAD_IN_PROGRESS，不重复加载。"""
        _, calls, started, _ = preload_env

        first = client.post("/models/preload")
        assert first.json()["code"] == "PRELOAD_STARTED"
        assert started.wait(timeout=5)

        second = client.post("/models/preload")

        assert second.status_code == 200
        assert second.json() == {
            "ok": True, "message": "bge-m3 预加载进行中", "code": "PRELOAD_IN_PROGRESS",
        }
        assert len(calls) == 1, "重复触发引发了第二次模型加载"
