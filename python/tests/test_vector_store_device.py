"""python/tests/test_vector_store_device.py — bge-m3 模型加载设备选择回归测试。

覆盖（2026-10-07 GPU 落地）：
  - CUDA 可用 → BGEM3FlagModel(use_fp16=True, devices=["cuda"])；
  - 无 CUDA → BGEM3FlagModel(use_fp16=False)（CPU 回退，不传 devices）；
  - 模块级单例：第二次调用不再重新加载。
"""
import os
import sys
import types

import pytest
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import knowledge.vector_store as vs


class _FakeModel:
    def __init__(self, path, **kwargs):
        self.path = path
        self.kwargs = kwargs


@pytest.fixture
def fake_flagembedding(monkeypatch, tmp_path):
    """伪装 FlagEmbedding 模块 + 假模型文件，记录构造参数。"""
    (tmp_path / "pytorch_model.bin").write_bytes(b"fake weights")

    created = []

    def _fake_bgem3(path, **kwargs):
        m = _FakeModel(path, **kwargs)
        created.append(m)
        return m

    fake_module = types.ModuleType("FlagEmbedding")
    fake_module.BGEM3FlagModel = _fake_bgem3
    monkeypatch.setitem(sys.modules, "FlagEmbedding", fake_module)

    # 每用例独立单例状态
    monkeypatch.setattr(vs, "_bge_m3_model", None)
    return created


class TestBgeM3DeviceSelection:
    def test_cuda_available_uses_fp16_cuda(self, monkeypatch, fake_flagembedding, tmp_path):
        # torch 已改为 _get_bge_m3_model 内懒加载，探测点直接 patch torch.cuda
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        model = vs._get_bge_m3_model(str(tmp_path))

        assert model is fake_flagembedding[0]
        assert model.kwargs["use_fp16"] is True
        assert model.kwargs["devices"] == ["cuda"]

    def test_no_cuda_falls_back_to_cpu_fp32(self, monkeypatch, fake_flagembedding, tmp_path):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

        model = vs._get_bge_m3_model(str(tmp_path))

        assert model is fake_flagembedding[0]
        assert model.kwargs["use_fp16"] is False
        assert "devices" not in model.kwargs  # CPU 回退不传 devices

    def test_singleton_loads_once(self, monkeypatch, fake_flagembedding, tmp_path):
        # torch 已改为 _get_bge_m3_model 内懒加载，探测点直接 patch torch.cuda
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        m1 = vs._get_bge_m3_model(str(tmp_path))
        m2 = vs._get_bge_m3_model(str(tmp_path / "irrelevant"))  # 换路径也走单例（首个路径胜出）

        assert m1 is m2
        assert len(fake_flagembedding) == 1
