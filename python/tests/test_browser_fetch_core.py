# -*- coding: utf-8 -*-
"""browser-fetch 核心逻辑测试（2.8 §5 / §11）。

覆盖：SSRF 校验矩阵（DNS mock 离线）、重定向探测链（httpx.MockTransport
离线——已验证 follow_redirects=False 下 Mock 仍填充 next_request，与生产
路径一致）、跨进程抓取锁（获取/互斥/僵死接管/pid 校验释放）、登录墙
四信号、软墙第 5 信号（遮蔽短语频次）、HTML→Markdown 及工具函数。

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


# ── 软墙第 5 信号：遮蔽短语频次（§5.4，2026-10-26 天眼查任务实证）──

def test_gating_hits_counts_phrases():
    """中英短语全部计数（交替分支不重叠：「登录后查看」≠「登录查看」）。"""
    text = ("电话：181****登录查看\n邮箱：登录后查看更多信息\n"
            "股东：登录查看\nMore: Sign In to View")
    assert core.count_gating_hits(text) == 4
    assert core.detect_login_gating(text) is True


def test_gating_below_threshold_not_wall():
    """频次阈值：导航栏/页脚级噪音（≤2 处）不判软墙——公开页「登录」链接常见。"""
    text = "公开年报经营数据。" * 100 + "请登录查看 或 登录后查看"
    assert core.count_gating_hits(text) == 2
    assert core.detect_login_gating(text) is False


def test_gating_english_phrases_case_insensitive():
    assert core.detect_login_gating(
        "Phone: 181**** Log In to View / Email: Sign In to View / "
        "More: Sign Up to See") is True


def test_gating_empty_text_safe():
    """空/None 文本安全（防御上游空 innerText）。"""
    assert core.count_gating_hits("") == 0
    assert core.detect_login_gating("") is False
    assert core.detect_login_gating(None) is False


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


# ── V2 租约锁（2.8 §3.3：锁内容 {pid, ts, task_id} 三规则）──

class TestFetchLockTaskId:

    def test_lock_payload_contains_task_id(self, tmp_path):
        lock = core.FetchLock(str(tmp_path), task_id="t1")
        assert lock.acquire()
        data = json.loads((tmp_path / "fetch.lock").read_text(encoding="utf-8"))
        assert data["task_id"] == "t1" and data["pid"] == os.getpid()
        lock.release()
        assert not (tmp_path / "fetch.lock").exists()

    def test_same_task_reentry_takes_over(self, tmp_path):
        first = core.FetchLock(str(tmp_path), task_id="t1")
        assert first.acquire()   # 模拟同 task 上个 CLI 进程异常退出未 release
        second = core.FetchLock(str(tmp_path), task_id="t1")
        assert second.acquire()  # 同 task 重入放行（接管）
        second.release()

    def test_other_task_rejected_within_stale_window(self, tmp_path):
        first = core.FetchLock(str(tmp_path), task_id="t1")
        assert first.acquire()
        second = core.FetchLock(str(tmp_path), task_id="t2")
        assert not second.acquire()
        data = json.loads((tmp_path / "fetch.lock").read_text(encoding="utf-8"))
        assert data["task_id"] == "t1"  # 原锁未被破坏

    def test_other_task_takes_over_after_stale(self, tmp_path):
        first = core.FetchLock(str(tmp_path), task_id="t1")
        assert first.acquire()
        stale_ts = time.time() - (core.LOCK_STALE_SECONDS + 5)
        os.utime(tmp_path / "fetch.lock", (stale_ts, stale_ts))
        second = core.FetchLock(str(tmp_path), task_id="t2")
        assert second.acquire()
        second.release()

    def test_release_verifies_task_id(self, tmp_path):
        lock = core.FetchLock(str(tmp_path), task_id="t1")
        assert lock.acquire()
        # 锁内容被接管者覆盖 → release 不得删除
        (tmp_path / "fetch.lock").write_text(
            json.dumps({"pid": os.getpid(), "ts": time.time(), "task_id": "t2"}),
            encoding="utf-8")
        lock.release()
        assert (tmp_path / "fetch.lock").exists()


# ── V2 快照 JS / 待命页（2.8 §5.2；真实 evaluate 由 Task 8 手动验收覆盖）──

class TestSnapshotJs:

    def test_snapshot_js_contains_limits(self):
        js = core.snapshot_js()
        assert "3000" in js and "80" in js and "40" in js

    def test_snapshot_js_is_arrow_function_source(self):
        assert core.snapshot_js().lstrip().startswith("() =>")

    def test_snapshot_js_visible_definition_matches_playwright(self):
        # 几何尺寸判定（rect.width>0）漏掉 visibility:hidden（保留布局、rect
        # 有宽高）与 visibility:collapse——快照把隐藏副本列进 elements，agent
        # 照用必撞 actionability 超时（2026-10-14 天眼查双搜索框实证）。快照
        # 的可见定义必须对齐 Playwright（非空 bounding box + visibility 可见）
        js = core.snapshot_js()
        assert "getComputedStyle" in js
        assert "visibility" in js

    def test_snapshot_js_includes_scroll_progress(self):
        # 懒加载/无限流（2026-10-14 用户提出）：agent 需要滚动位置信息判断
        # 是否到底——scroll.remaining=0 即视觉到底，height 供收敛判断
        js = core.snapshot_js()
        assert "scrollY" in js and "scrollHeight" in js
        assert "remaining" in js

    def test_snapshot_js_selector_uniqueness_guard(self):
        # 天眼查实证（2026-10-16）：3 层截断路径生成前缀歧义 selector
        # （`div:nth-of-type(1) > div > a` 同时命中导航与搜索结果），
        # locator.first 点错跳回首页。每级候选必须过 querySelectorAll
        # 唯一性验证，路径级从 3 层起逐层延长至唯一
        js = core.snapshot_js()
        assert "document.querySelectorAll(sel).length" in js
        assert "count(sel) === 1" in js
        assert "depth <= 8" in js  # 路径延长上限（原 3 层硬截断已废除）
        assert "a[href=\"'" in js or "a[href=\"' + escAttr(href)" in js  # href 优先定位

    def test_snapshot_js_elements_carry_href(self):
        # 天眼查实证（2026-10-16）：elements 只有 selector/name，LLM 拿不到
        # 公司详情页 URL，绕道 web_search+extract 三轮全败。链接类元素
        # 必须附 href，支持 open <href> 直达导航
        js = core.snapshot_js()
        assert "item.href = href" in js

    def test_mark_page_is_data_url_with_pool_title(self):
        # charset 必须显式声明：无 charset 的 data: URL 中文按 fallback 编码
        # （GBK/windows-1252）解码乱码（2026-10-14 面板截图实证）
        assert core.MARK_PAGE.startswith("data:text/html;charset=utf-8,")
        assert "inkcube-fetch-pool" in core.MARK_PAGE
        # data: URL 中裸 # 会被解析为 fragment 截断（2026-10-13 review 实证），
        # 待命页正文必须无裸 #（否则 body 后半段不渲染）
        assert "#" not in core.MARK_PAGE
