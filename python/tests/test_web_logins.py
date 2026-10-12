# -*- coding: utf-8 -*-
"""web_logins 路由测试（2.8 §9.1）：域名规范化矩阵 + CRUD 幂等语义。

要点回归：
- add 幂等：已存在时 INSERT OR IGNORE 保留原行，用户手写 note 不得被
  任务卡片自动登记路径覆盖（design §3.2 交互约束）；
- normalize_domain 拒绝 localhost / *.local 与全部 IPv4 字面量形式
  （含 Chromium BSD 简写：127.1 / 2130706433 / 0x7f000001 / 0177.0.0.1）。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deps
import routers.web_logins as wl_router
from db.database import Database


@pytest.fixture
def db():
    d = Database(db_path=":memory:")
    d.initialize()
    yield d
    d.conn.close()


@pytest.fixture
def patched(db, monkeypatch):
    """router 依赖注入：deps.db → 内存 DB。"""
    monkeypatch.setattr(deps, "db", db)
    return db


def _add(domain: str, note: str | None = None) -> dict:
    return asyncio.run(
        wl_router.add_web_login(wl_router.WebLoginAddRequest(domain=domain, note=note))
    )


def test_normalize_domain_matrix():
    """裸域名 / 完整 URL / 端口剥离 / 大小写归一 / 非法值拒绝。"""
    cases = {
        "Example.COM": "example.com",
        "  https://www.Example.com/path?q=1  ": "www.example.com",
        "http://example.com:8080/x": "example.com",
        "https://blog.example.com.cn/a/b": "blog.example.com.cn",
        "": None,
        "   ": None,
        "localhost": None,
        "internal.local": None,
        "http://": None,
        # IP 字面量拒绝（与 Electron openLoginWindow 内网防线一致）
        "192.168.1.1": None,
        "http://10.0.0.5/x": None,
        "http://[::1]:8080/": None,
        "http://[fe80::1]/": None,
        # Chromium BSD 简写形式（会被浏览器规范化为 127.0.0.1，仅拦点分
        # 会造成登记成功但登录窗被内网防线拒绝）
        "127.1": None,
        "2130706433": None,
        "0x7f000001": None,
        "0177.0.0.1": None,
        "http://0x7f.1/x": None,
    }
    for raw, expected in cases.items():
        assert wl_router.normalize_domain(raw) == expected, repr(raw)


def test_add_idempotent_keeps_user_note(patched):
    """核心回归：重复登记不覆盖用户手写 note（§3.2 任务卡片自动登记约束）。"""
    first = _add("example.com", note="工作账号")
    assert first["login"]["inserted"] is True
    assert first["login"]["note"] == "工作账号"

    second = _add("example.com", note=None)  # 任务卡片自动登记：无备注
    assert second["login"]["inserted"] is False
    assert second["login"]["note"] == "工作账号"  # 保留原值

    rows = asyncio.run(wl_router.list_web_logins())["logins"]
    assert len(rows) == 1
    assert rows[0]["domain"] == "example.com"
    assert set(rows[0].keys()) == {"domain", "note", "created_at"}


def test_add_invalid_domain_rejected(patched):
    for bad in ("localhost", "myserver.local", "192.168.1.1", "2130706433", "127.1", "   ", ""):
        resp = _add(bad)
        assert "error" in resp, repr(bad)
    assert asyncio.run(wl_router.list_web_logins())["logins"] == []


def test_delete_existing_and_case_normalized(patched):
    _add("MiXeD.Com")
    assert asyncio.run(wl_router.delete_web_login("mixed.com")) == {"ok": True}
    assert asyncio.run(wl_router.delete_web_login("mixed.com")) == {
        "error": "站点不存在", "code": "SITE_NOT_FOUND",
    }
    assert asyncio.run(wl_router.list_web_logins())["logins"] == []


def test_builtin_skill_delete_rejected(patched):
    """§9.1：builtin skill 不可卸载（随种子安装，删了下次启动会被重新种回）。"""
    from routers.skills import delete_skill

    patched.create_skill(name="browser-fetch", description="d", source_type="builtin",
                         source_path="x", install_path="y")
    row = next(s for s in patched.list_skills() if s["name"] == "browser-fetch")

    result = asyncio.run(delete_skill(row["id"]))

    assert "error" in result
    assert [s for s in patched.list_skills() if s["id"] == row["id"]]  # 仍在
