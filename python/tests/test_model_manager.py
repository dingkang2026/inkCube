"""python/tests/test_model_manager.py — ModelManager 下载源配置与 HF 离线开关单测。

覆盖：
- 源顺序契约：modelscope 优先（国内可达）→ hf-mirror → huggingface；
  tableformer-v2 无 modelscope 源（ds4sd/TableFormerV2 已验证 404，无效配置已删）
- _dir_size 半成品统计开关（进度条失真修复：HF/modelscope 下载中的大文件
  均以 *.incomplete 落盘，进度监控必须计入）
- _hf_online 引用计数开关（HF_HUB_OFFLINE 冲突修复：main.py 全局离线防探测
  与同进程 snapshot_download 的冲突，下载窗口内临时解除）
"""
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from knowledge.model_manager import MODELS, ModelManager


class TestSourceOrdering:
    def test_modelscope_first_then_hfmirror_then_huggingface(self):
        """国内可达性契约：modelscope 优先，hf-mirror 次之，官方 huggingface 兜底。"""
        for name in ("bge-m3", "docling-layout-heron"):
            types = [s.split(":", 1)[0] for s in MODELS[name]["sources"]]
            assert types == ["modelscope", "hf-mirror", "huggingface"], name

    def test_tableformer_has_no_modelscope_source(self):
        """modelscope 无 TableFormerV2 repo（404 实锤），无效源已删除。"""
        types = [s.split(":", 1)[0] for s in MODELS["tableformer-v2"]["sources"]]
        assert "modelscope" not in types
        assert types == ["hf-mirror", "huggingface"]

    def test_rapidocr_single_source(self):
        """docling-tools 源底层即 modelscope 直链（rapidocr 库硬编码），唯一源。"""
        assert MODELS["rapidocr"]["sources"] == ["docling-tools:rapidocr"]

    def test_size_bytes_calibrated(self):
        """size_bytes 与历史完整下载实测对齐（进度条分母）。"""
        assert MODELS["bge-m3"]["size_bytes"] == 4_600_000_000
        assert MODELS["docling-layout-heron"]["size_bytes"] == 170_000_000
        assert MODELS["tableformer-v2"]["size_bytes"] == 220_000_000


class TestDirSize:
    @pytest.fixture
    def model_dir(self, tmp_path):
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        (tmp_path / "pytorch_model.bin.incomplete").write_bytes(b"x" * 1000)
        sub = tmp_path / "onnx"
        sub.mkdir()
        (sub / "model.onnx_data.incomplete").write_bytes(b"x" * 500)
        (sub / "config.json").write_text("{}", encoding="utf-8")
        return tmp_path

    def test_excludes_incomplete_by_default(self, model_dir):
        """完整性语义（_verify / actual_size）：半成品不计入。"""
        m = ModelManager()
        # 仅两个 config.json（各 2 字节）
        assert m._dir_size(str(model_dir)) == 4

    def test_includes_incomplete_when_requested(self, model_dir):
        """进度监控语义：半成品计入（1500 字节半成品 + 4 字节有效文件）。"""
        m = ModelManager()
        assert m._dir_size(str(model_dir), include_incomplete=True) == 1504


class TestHFOnlineSwitch:
    @pytest.fixture(autouse=True)
    def _offline_env(self, monkeypatch):
        """建立与 main.py 启动时一致的初始离线态，测试后 monkeypatch 自动清理。
        同步清理 Xet 开关残留，保证退出窗口后断言确定性。"""
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
        monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)

    def test_enter_releases_offline_and_exit_restores(self):
        m = ModelManager()
        with m._hf_online():
            assert os.environ.get("HF_HUB_OFFLINE") == "0"
            assert os.environ.get("TRANSFORMERS_OFFLINE") == "0"
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"

    def test_window_disables_xet(self):
        """窗口内禁 Xet（env + 常量），退出还原为未设置（库默认启用）。
        hf-mirror 不支持 Xet：大文件被导向官方 CAS 服务器重建、镜像 token
        401（tableformer-v2 2026-10-05 两连败根因），禁用后回退普通 HTTP CDN。"""
        hfc = pytest.importorskip("huggingface_hub.constants")
        m = ModelManager()
        with m._hf_online():
            assert os.environ.get("HF_HUB_DISABLE_XET") == "1"
            assert hfc.HF_HUB_DISABLE_XET is True
        assert os.environ.get("HF_HUB_DISABLE_XET") is None
        assert hfc.HF_HUB_DISABLE_XET is False

    def test_nested_keeps_online_until_last_exit(self):
        """嵌套窗口：内层退出不恢复，最后一个退出者负责恢复。"""
        m = ModelManager()
        with m._hf_online():
            with m._hf_online():
                assert os.environ.get("HF_HUB_OFFLINE") == "0"
            assert os.environ.get("HF_HUB_OFFLINE") == "0"
        assert os.environ.get("HF_HUB_OFFLINE") == "1"

    def test_huggingface_constants_patched(self):
        """仅改 os.environ 不生效——huggingface_hub.constants 在 import 时已固化
        env 值，_set_hf_offline 必须同时 patch 模块常量（tableformer 修复关键）。"""
        hfc = pytest.importorskip("huggingface_hub.constants")
        m = ModelManager()
        with m._hf_online():
            assert hfc.HF_HUB_OFFLINE is False
        assert hfc.HF_HUB_OFFLINE is True

    def test_exception_still_restores(self):
        """下载抛异常也必须恢复离线态（finally 兜底）。"""
        m = ModelManager()
        with pytest.raises(RuntimeError):
            with m._hf_online():
                raise RuntimeError("boom")
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("HF_HUB_DISABLE_XET") is None

    def test_download_from_hf_runs_online(self, monkeypatch, tmp_path):
        """回归断言：snapshot_download 执行期间离线已解除且 Xet 已禁用（env + 常量）。"""
        seen = {}

        def fake_download(repo_id, local_dir, endpoint):
            import huggingface_hub.constants as hfc

            seen["env"] = os.environ.get("HF_HUB_OFFLINE")
            seen["constant"] = hfc.HF_HUB_OFFLINE
            seen["xet_env"] = os.environ.get("HF_HUB_DISABLE_XET")
            seen["xet_constant"] = hfc.HF_HUB_DISABLE_XET
            seen["endpoint"] = endpoint

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_download)
        m = ModelManager()
        m.models_dir = str(tmp_path)
        m._download_from_hf(
            "tableformer-v2", "docling-project/TableFormerV2", use_mirror=True,
        )
        assert seen["env"] == "0"
        assert seen["constant"] is False
        assert seen["xet_env"] == "1"
        assert seen["xet_constant"] is True
        assert seen["endpoint"] == "https://hf-mirror.com"
        # 退出后恢复
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("HF_HUB_DISABLE_XET") is None


class TestRapidOcrStreaming:
    """RapidOCR 流式下载：docling 内置下载器为内存缓冲（进度全程 0%），
    改造后须流式写 *.incomplete 并原子改名，进度监控可实时统计。"""

    @pytest.fixture
    def fake_artifacts(self, monkeypatch, tmp_path):
        """mock docling URL 解析：两个文件映射（dest → url）。"""
        dest_a = tmp_path / "det.onnx"
        dest_b = tmp_path / "rec.onnx"
        mapping = {"det": type("A", (), {"files": {dest_a: "http://x/det.onnx"}})(),
                   "rec": type("A", (), {"files": {dest_b: "http://x/rec.onnx"}})()}

        monkeypatch.setattr(
            "docling.models.stages.ocr.rapid_ocr_model._resolve_rapidocr",
            lambda lang, backend: type("R", (), {"ppocr_version": "v6", "rapidocr_code": "ch"})(),
        )
        monkeypatch.setattr(
            "docling.models.stages.ocr.rapid_ocr_model._backend_to_engine_type",
            lambda backend: "onnxruntime",
        )
        monkeypatch.setattr(
            "docling.models.stages.ocr.rapid_ocr_model._rapidocr_artifacts",
            lambda target_dir, engine, version, rec_code, **kw: mapping,
        )
        return mapping

    def _fake_requests(self, monkeypatch, payload=b"data"):
        calls = []

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield payload

        def fake_get(url, stream=False, timeout=None):
            calls.append(url)
            return FakeResp()

        import requests as requests_mod

        monkeypatch.setattr(requests_mod, "get", fake_get)
        return calls

    def test_streams_to_incomplete_then_renames(self, fake_artifacts, tmp_path, monkeypatch):
        m = ModelManager()
        calls = self._fake_requests(monkeypatch, b"onnx-bytes")
        m._download_rapidocr(str(tmp_path))

        assert len(calls) == 2
        for key, art in fake_artifacts.items():
            for dest in art.files:
                assert dest.exists()
                assert dest.read_bytes() == b"onnx-bytes"
                assert not dest.with_name(dest.name + ".incomplete").exists()

    def test_skips_existing_files(self, fake_artifacts, tmp_path, monkeypatch):
        m = ModelManager()
        calls = self._fake_requests(monkeypatch)
        # 预置 det.onnx 已存在（断点重入场景）
        existing = list(fake_artifacts.values())[0]
        for dest in existing.files:
            dest.write_bytes(b"already-here")

        m._download_rapidocr(str(tmp_path))

        assert len(calls) == 1  # 只下载缺失的 rec.onnx
        for dest in existing.files:
            assert dest.read_bytes() == b"already-here"  # 未被覆盖

    def test_failure_cleans_incomplete_and_raises(self, fake_artifacts, tmp_path, monkeypatch):
        m = ModelManager()

        import requests as requests_mod

        def boom(url, stream=False, timeout=None):
            raise OSError("network down")

        monkeypatch.setattr(requests_mod, "get", boom)
        with pytest.raises(OSError):
            m._download_rapidocr(str(tmp_path))

        # 无半成品残留
        for art in fake_artifacts.values():
            for dest in art.files:
                assert not dest.exists()
                assert not dest.with_name(dest.name + ".incomplete").exists()
