"""tests/test_uv_cache_signal.py — uv-cache 阻断信号（2.8 §12 F3）。

覆盖 get_uv_cache_tmp_residue 的计数语义与边界（无环境变量 / 目录缺失 /
非 .tmp 条目不计 / 陈旧阈值），以及 /system/uv-cache-blocked 端点的
blocked 判定。信号 = uv-cache 下陈旧（mtime 超过 _RESIDUE_STALE_SECS）
.tmp* 残留数：rename 被实时防护阻断 / 安装中断的持久物证；健康安装的
在途瞬态解压目录不计，避免误报。
"""
import os
import sys
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routers import system as system_router  # noqa: E402
from tools import venv_manager as vm  # noqa: E402


def _set_cache_dir(monkeypatch, path):
    monkeypatch.setenv("UV_CACHE_DIR", str(path))


def _make_client(monkeypatch, cache_dir):
    _set_cache_dir(monkeypatch, cache_dir)
    app = FastAPI()
    app.include_router(system_router.router)
    return TestClient(app)


def _make_stale(path, age_secs=3600.0):
    """把条目 mtime 拨旧（默认 1h），越过 _RESIDUE_STALE_SECS 陈旧阈值。"""
    old = time.time() - age_secs
    os.utime(path, (old, old))


class TestGetUvCacheTmpResidue:
    def test_counts_stale_tmp_entries_only(self, tmp_path, monkeypatch):
        _set_cache_dir(monkeypatch, tmp_path)
        stale_a = tmp_path / ".tmpAbCdEf"
        stale_a.mkdir()
        stale_b = tmp_path / ".tmpXyZ123"
        stale_b.mkdir()
        stale_f = tmp_path / ".tmpfile"
        stale_f.write_text("x")  # .tmp 前缀的文件也计（罕见但语义一致）
        for p in (stale_a, stale_b, stale_f):
            _make_stale(p)
        (tmp_path / "archive-v0").mkdir()      # 正常缓存条目，不计
        (tmp_path / "wheels-v6").mkdir()       # 正常缓存条目，不计
        (tmp_path / ".tmpFresh").mkdir()       # 在途解压瞬态（mtime 新鲜），不计

        assert vm.get_uv_cache_tmp_residue() == 3

    def test_empty_cache_returns_zero(self, tmp_path, monkeypatch):
        _set_cache_dir(monkeypatch, tmp_path)
        assert vm.get_uv_cache_tmp_residue() == 0

    def test_missing_dir_returns_zero(self, tmp_path, monkeypatch):
        _set_cache_dir(monkeypatch, tmp_path / "not-created")
        assert vm.get_uv_cache_tmp_residue() == 0

    def test_unset_env_returns_zero(self, monkeypatch):
        monkeypatch.delenv("UV_CACHE_DIR", raising=False)
        assert vm.get_uv_cache_tmp_residue() == 0


class TestUvCacheBlockedEndpoint:
    def test_blocked_true_when_stale_residue_exists(self, tmp_path, monkeypatch):
        residue = tmp_path / ".tmpResidue1"
        residue.mkdir()
        _make_stale(residue)
        client = _make_client(monkeypatch, tmp_path)

        res = client.get("/system/uv-cache-blocked")

        assert res.status_code == 200
        assert res.json() == {"blocked": True, "tmp_count": 1}

    def test_blocked_false_when_only_fresh_residue(self, tmp_path, monkeypatch):
        """在途解压的瞬态 .tmp（mtime 新鲜）不构成阻断信号（防误报）。"""
        (tmp_path / ".tmpInFlight").mkdir()
        client = _make_client(monkeypatch, tmp_path)

        res = client.get("/system/uv-cache-blocked")

        assert res.json() == {"blocked": False, "tmp_count": 0}

    def test_blocked_false_when_clean(self, tmp_path, monkeypatch):
        client = _make_client(monkeypatch, tmp_path)

        res = client.get("/system/uv-cache-blocked")

        assert res.status_code == 200
        assert res.json() == {"blocked": False, "tmp_count": 0}
