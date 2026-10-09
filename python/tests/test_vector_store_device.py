"""python/tests/test_vector_store_device.py — bge-m3 模型加载设备选择回归测试。

覆盖（2026-10-07 GPU 落地）：
  - CUDA 可用 → BGEM3FlagModel(use_fp16=True, devices=["cuda"])；
  - 无 CUDA → BGEM3FlagModel(use_fp16=False)（CPU 回退，不传 devices）；
  - 模块级单例：第二次调用不再重新加载；
  - encode 批次/截断参数（2026-10-09 GPU TDR 崩溃根因修复）。
"""
import os
import sys
import types

import numpy as np
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


class TestBgeM3EncodeParams:
    """encode 批次/截断参数回归（2026-10-09 GPU TDR 崩溃根因修复）。

    FlagEmbedding 默认 batch_size=256 会在批量入库时把全部 chunks 一次送进
    GPU forward（8GB 卡必爆 → 驱动 TDR）；调用点必须显式限定小批次与
    max_length=1024（与 doc_parser chunker 的 max_tokens=1024 对齐）。
    """

    def test_embedding_function_encodes_with_safe_batch(
        self, monkeypatch, fake_flagembedding, tmp_path,
    ):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        encode_calls = []

        def _fake_encode(texts, **kwargs):
            encode_calls.append({"texts": texts, **kwargs})
            # 真实 FlagEmbedding 返回 numpy 数组（__call__ 会调 .tolist()）
            return {"dense_vecs": np.zeros((len(texts), 4))}

        model = vs._get_bge_m3_model(str(tmp_path))
        model.encode = _fake_encode

        ef = vs.BgeM3EmbeddingFunction(str(tmp_path))
        out = ef(["文本一", "文本二"])

        # 核心契约：encode 被以安全批次/截断参数调用（本回归的主断言）
        assert encode_calls == [{
            "texts": ["文本一", "文本二"],
            "return_dense": True,
            "batch_size": 8,
            "max_length": 1024,
        }]
        # 返回维度正确（chromadb 基类包装后元素可能是 ndarray，不比元素类型）
        assert len(out) == 2
        assert all(len(vec) == 4 for vec in out)
