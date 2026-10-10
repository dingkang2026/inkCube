# -*- coding: utf-8 -*-
"""browser-fetch 核心逻辑测试（2.8 §5 / §11）。

覆盖：SSRF 校验矩阵（DNS mock 离线）、重定向探测链（httpx.MockTransport
离线——已验证 follow_redirects=False 下 Mock 仍填充 next_request，与生产
路径一致）、跨进程抓取锁（获取/互斥/僵死接管/pid 校验释放）、登录墙
四信号、HTML→Markdown 及工具函数。

模块加载：_fetch_core.py 位于 skill 安装源（非包），用 importlib 直载。
"""
import importlib.util
import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

_CORE_PATH = (
    Path(__file__).resolve().parents[2]
    / "resources" / "skills" / "browser-fetch" / "scripts" / "_fetch_core.py"
)
_core_spec = importlib.util.spec_from_file_location("_fetch_core_test", str(_CORE_PATH))
core = importlib.util.module_from_spec(_core_spec)
sys.modules.setdefault("_fetch_core_test", core)
_core_spec.loader.exec_module(core)

# probe 探测链测试用的假 DNS 判定表（避免真实 DNS 依赖）
_PRIVATE_FAKE = {"127.0.0.1", "10.0.0.5", "169.254.1.1", "localhost"}


def _fake_dns(monkeypatch, table: dict[str, list[str]]) -> None:
    """getaddrinfo 打桩：table[hostname] → 地址列表；未登记主机视为不可解析
    （gaierror → is_private_host 返回 True，与真实 DNS 失败语义一致）。"""
    def getaddrinfo(host, port=None):
        addrs = table.get(host)
        if addrs is None:
            raise socket.gaierror(11001, "no such host (fake dns)")
        infos = []
        for a in addrs:
            sockaddr = (a, 0) if ":" not in a else (a, 0, 0, 0)
            infos.append((0, 0, 0, "", sockaddr))
        return infos

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


# ── SSRF：is_private_host（§5.3 第 1 层）──

@pytest.mark.parametrize("addr,expected", [
    ("10.0.0.5", True),           # private
    ("192.168.1.1", True),        # private
    ("172.16.0.1", True),         # private 段下界
    ("172.31.255.255", True),     # private 段上界
    ("172.32.0.1", False),        # 恰好越出 private 段 → 公网
    ("127.0.0.1", True),          # loopback
    ("::1", True),                # loopback v6
    ("169.254.1.1", True),        # link-local（云元数据段）
    ("fe80::1", True),            # link-local v6
    ("240.0.0.1", True),          # reserved
    ("0.0.0.0", True),            # unspecified
    ("8.8.8.8", False),           # 公网
])
def test_is_private_host_by_addr(monkeypatch, addr, expected):
    _fake_dns(monkeypatch, {"whatever.example": [addr]})
    assert core.is_private_host("whatever.example") is expected


def test_is_private_host_any_address_hits(monkeypatch):
    """多地址解析：任一命中内网即拒绝（防 DNS rebinding 的多记录绕过）。"""
    _fake_dns(monkeypatch, {"mixed.example": ["8.8.8.8", "10.0.0.5"]})
    assert core.is_private_host("mixed.example") is True


def test_is_private_host_unresolvable(monkeypatch):
    def _raise(host, port):
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(socket, "getaddrinfo", _raise)
    assert core.is_private_host("nope.example") is True


def test_validate_url_matrix(monkeypatch):
    _fake_dns(monkeypatch, {"example.com": ["93.184.216.34"]})
    assert core.validate_url("ftp://example.com") is not None      # scheme 白名单
    assert core.validate_url("https:///path") is not None          # 缺主机名
    assert core.validate_url("https://localhost/x") is not None    # localhost
    assert core.validate_url("http://10.0.0.5/") is not None       # 内网（未登记→不可解析拒）
    assert core.validate_url("https://example.com/") is None       # 通过


# ── SSRF：probe_redirect_chain（§5.3 第 1 层，MockTransport 离线）──

@pytest.fixture
def probe_env(monkeypatch):
    """探测链测试环境：假 DNS 判定 + httpx.Client 注入 MockTransport。"""
    monkeypatch.setattr(core, "is_private_host", lambda h: h in _PRIVATE_FAKE)

    import httpx
    real_client = httpx.Client

    def _use_transport(handler):
        def factory(**kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(**kwargs)
        monkeypatch.setattr(httpx, "Client", factory)

    return _use_transport


def test_probe_direct_200(probe_env):
    import httpx
    probe_env(lambda req: httpx.Response(200, text="ok"))
    final, err = core.probe_redirect_chain("https://site.example/page")
    assert err is None
    assert final == "https://site.example/page"


def test_probe_follows_redirects(probe_env):
    import httpx

    def handler(req):
        if req.url.host == "a.example":
            return httpx.Response(302, headers={"Location": "https://b.example/final"})
        return httpx.Response(200)

    probe_env(handler)
    final, err = core.probe_redirect_chain("https://a.example/")
    assert err is None
    assert final == "https://b.example/final"


def test_probe_blocks_redirect_to_private(probe_env):
    """核心安全用例：公网入口 302 → 内网地址，逐跳校验必须拦截。"""
    import httpx
    probe_env(lambda req: httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"}))
    final, err = core.probe_redirect_chain("https://public.example/")
    assert final == ""
    assert err and "内网" in err


def test_probe_rejects_redirect_loop(probe_env):
    import httpx
    probe_env(lambda req: httpx.Response(
        302, headers={"Location": "https://a.example/loop"}))
    final, err = core.probe_redirect_chain("https://a.example/start")
    assert final == ""
    assert err and "超过" in err


@pytest.mark.parametrize("status", [403, 500])
def test_probe_4xx_5xx_passes(probe_env, status):
    """4xx/5xx 放行：站点对探测请求反爬 ≠ 浏览器抓不到（浏览器侧有登录态）。"""
    import httpx
    probe_env(lambda req: httpx.Response(status))
    final, err = core.probe_redirect_chain("https://site.example/")
    assert err is None
    assert final == "https://site.example/"


def test_probe_connect_error_reports(probe_env):
    """连接失败必须报错：确实不可达，浏览器同样抓不到。"""
    import httpx

    def handler(req):
        raise httpx.ConnectError("connection refused", request=req)

    probe_env(handler)
    final, err = core.probe_redirect_chain("https://down.example/")
    assert final == ""
    assert err and "无法连接" in err


# ── 抓取锁 FetchLock（§3.3）──

def test_lock_acquire_release(tmp_path):
    lock = core.FetchLock(str(tmp_path))
    assert lock.acquire() is True
    lockfile = tmp_path / "fetch.lock"
    assert lockfile.is_file()
    payload = json.loads(lockfile.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    lock.release()
    assert not lockfile.exists()


def test_lock_mutual_exclusion(tmp_path):
    first = core.FetchLock(str(tmp_path))
    second = core.FetchLock(str(tmp_path))
    assert first.acquire() is True
    assert second.acquire() is False          # 未僵死 → 拒绝
    first.release()
    assert second.acquire() is True           # 释放后可获取
    second.release()


def test_lock_stale_takeover(tmp_path):
    holder = core.FetchLock(str(tmp_path))
    assert holder.acquire() is True
    old = time.time() - (core.LOCK_STALE_SECONDS + 1)
    os.utime(tmp_path / "fetch.lock", (old, old))  # 拨回 → 僵死
    taker = core.FetchLock(str(tmp_path))
    assert taker.acquire() is True            # 超时接管
    taker.release()


def test_release_skips_foreign_lock(tmp_path):
    """pid 校验：锁内容不是自己的 pid 时不删除（防误删接管者的锁）。"""
    lock = core.FetchLock(str(tmp_path))
    lock._mine = True
    foreign = 2 ** 30  # 不可能出现的 pid
    (tmp_path / "fetch.lock").write_text(
        json.dumps({"pid": foreign, "ts": time.time()}), encoding="utf-8")
    lock.release()
    assert (tmp_path / "fetch.lock").exists()


def test_release_without_acquire_is_noop(tmp_path):
    lock = core.FetchLock(str(tmp_path))
    lock.release()  # 未 acquire：不抛异常、不产生文件
    assert not (tmp_path / "fetch.lock").exists()


# ── 登录墙四信号（§5.4）──

_SPARSE = "short page"
_DENSE = "字" * (core.LOGIN_TEXT_SPARSE + 100)


def test_login_wall_http_status():
    assert core.detect_login_wall(401, "https://s/", "https://s/", "t", _DENSE, False)
    assert core.detect_login_wall(403, "https://s/", "https://s/", "t", _DENSE, False)


def test_login_wall_password_and_sparse():
    assert core.detect_login_wall(200, "https://s/", "https://s/", "t", _SPARSE, True)


def test_login_wall_password_but_dense_ok():
    """富文本页也带密码框（如评论区登录）→ 不判。"""
    assert not core.detect_login_wall(200, "https://s/", "https://s/", "t", _DENSE, True)


def test_login_wall_url_signal():
    assert core.detect_login_wall(
        200, "https://s/login?next=/private", "https://s/private", "t", _DENSE, False)


def test_login_wall_original_is_login_page_no_signal():
    """用户主动访问登录页（original 已含 login）→ URL 信号不命中。"""
    assert not core.detect_login_wall(
        200, "https://s/login", "https://s/login", "My Account", _DENSE, False)


def test_login_wall_title_signal():
    assert core.detect_login_wall(200, "https://s/", "https://s/", "登录 - Site", _SPARSE, False)
    assert core.detect_login_wall(200, "https://s/", "https://s/", "Sign In", _SPARSE, False)
    assert not core.detect_login_wall(200, "https://s/", "https://s/", "登录 - Site", _DENSE, False)


def test_login_wall_none_status_and_no_signal():
    assert not core.detect_login_wall(None, "https://s/", "https://s/", "Home", _SPARSE, False)
    assert not core.detect_login_wall(200, "https://s/", "https://s/", "Home", _SPARSE, False)


# ── HTML → Markdown（html2text 优先，正则降级）──

def test_html_to_markdown_with_lib():
    md = core.html_to_markdown(
        "<html><body><h2>章节标题</h2><p>正文内容</p></body></html>")
    assert "## 章节标题" in md
    assert "正文内容" in md


def test_html_to_markdown_fallback(monkeypatch):
    """强制降级分支（sys.modules 塞 None 使 import 抛 ImportError）。"""
    monkeypatch.setitem(sys.modules, "html2text", None)
    md = core.html_to_markdown("<h2>标题</h2><p>段落</p>")
    assert "标题" in md and "段落" in md


def test_strip_html_structure():
    html = (
        "<html><head><style>.x{}</style><script>var a=1;</script></head>"
        "<body><h1>大标题</h1><p>第一段 &amp; 符号</p>"
        '<a href="https://x.example">链接文本</a></body></html>'
    )
    text = core._strip_html(html)
    assert "大标题" in text
    assert "第一段 & 符号" in text
    assert "链接文本 (https://x.example)" in text
    assert "var a=1" not in text      # script 剥除
    assert ".x{}" not in text         # style 剥除
    assert "<" not in text            # 标签清除


# ── 工具函数 ──

def test_extract_outline():
    md = "# 主\n\n## 一\n\n### 一.一\n\n正文\n\n## 二\n"
    assert core.extract_outline(md) == ["## 一", "### 一.一", "## 二"]


def test_extract_outline_cap():
    md = "\n".join(f"## c{i}" for i in range(20))
    assert len(core.extract_outline(md)) == 8  # 默认 max_items=8


def test_normalize_domain_core():
    assert core.normalize_domain("https://Example.COM/path") == "example.com"
    assert core.normalize_domain("https://192.168.1.4:8080/") == "192.168.1.4"
    assert core.normalize_domain("weird") == "unknown"  # 无 host 兜底


def test_sanitize_filename():
    assert core.sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert core.sanitize_filename("..\x00evil") == ".._evil"
    assert core.sanitize_filename("x" * 100) == "x" * 80
    assert core.sanitize_filename("???") == "___"
    assert core.sanitize_filename("") == "page"
