"""browser-fetch 核心逻辑（设计文档 2.8 §5）。

自 web_fetch 内置工具迁移的 SSRF 校验（_validate_url/_is_private_host），
新增：重定向探测链、跨进程抓取锁、登录墙检测、HTML→Markdown。

本模块不 import playwright（由入口 main.py 负责，以便捕获 ImportError
返回 retryable 语义）；httpx/html2text 由任务 venv 预装保证可用。
"""
from __future__ import annotations

import html as html_mod
import ipaddress
import json
import logging
import os
import re
import socket
import time
from urllib.parse import urlparse

logger = logging.getLogger("browser-fetch")

POOL_TITLE = "inkcube-fetch-pool"       # 池窗口标题（可视化用，§6.2）
# 池窗口匹配锚点（§5.3）：preload 每次导航重注入的运行时标记。不能用
# document.title——抓取后窗口停留在目标页，页面 title 首次成功即污染锚点
# （2026-10-12 实证：首次抓取 ok，之后全部「未找到抓取窗口」）
POOL_MARK_JS = "() => window.__INKCUBE_POOL__ === true"
LOCK_STALE_SECONDS = 60                  # 持锁超过此时长视为僵死，可接管（§3.3）
LOGIN_TEXT_SPARSE = 500                  # 登录墙「正文稀疏」阈值（§5.4）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ── SSRF 校验（自 web_fetch 迁移）──

def is_private_host(hostname: str) -> bool:
    """DNS 解析后判断主机是否指向内网/保留地址（§5.3 第 1 层）。

    解析所有地址，任一命中即拒绝；不可解析的域名拒绝。
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_unspecified
        ):
            return True
    return False


def validate_url(url: str) -> str | None:
    """基础校验：仅 http/https + 主机非内网。返回错误消息或 None。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"仅支持 http/https URL: {url}"
    if not parsed.hostname:
        return f"URL 缺少主机名: {url}"
    if parsed.hostname.lower() == "localhost" or is_private_host(parsed.hostname):
        return f"拒绝内网地址（安全策略）: {url}"
    return None


def probe_redirect_chain(
    url: str, max_hops: int = 3, timeout: float = 10.0
) -> tuple[str, str | None]:
    """重定向探测链（§5.3 第 1 层）：GET + stream 逐跳校验，返回最终 URL。

    - httpx 不支持 HEAD 的站点用 GET + stream（读响应头即断开）
    - 逐跳 validate_url；超过 max_hops 仍重定向 → 拒绝
    - 探测请求收到 4xx/5xx（无 Location）不算失败：SSRF 链校验已完成
      （站点对 httpx 探测反爬 ≠ 浏览器抓不到，浏览器侧有登录态，放行）
    - 连接失败 / DNS 失败 → 报错（确实不可达，浏览器同样抓不到）

    Returns:
        (final_url, None) 或 ("", error_message)
    """
    import httpx  # 任务 venv 预装；延迟导入便于单测打桩

    current = url
    try:
        with httpx.Client(
            follow_redirects=False, timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for _hop in range(max_hops + 1):
                err = validate_url(current)
                if err:
                    return "", err
                # stream：只取响应头与状态行，不下载正文
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location", "")
                        if not location:
                            return "", f"重定向响应缺少 Location: {current}"
                        current = str(resp.next_request.url)
                        continue
                    # 非重定向：链路校验结束（4xx/5xx 放行，见 docstring）
                    return current, None
            return "", f"重定向超过 {max_hops} 跳，拒绝抓取: {url}"
    except (httpx.ConnectError, httpx.ConnectTimeout, socket.gaierror) as e:
        return "", f"无法连接目标站点: {e}"
    except httpx.HTTPError as e:
        # 其余 httpx 异常（协议错误等）按不可达处理
        return "", f"访问目标站点失败: {e}"
    except Exception as e:  # 探测层自身异常不阻塞浏览器抓取（SSRF 已逐跳校验）
        logger.warning("redirect probe unexpected error, continue with %s: %s", url, e)
        return url, None


# ── 跨进程抓取锁（§3.3：fetch.lock，O_CREAT|O_EXCL，僵死接管）──

class FetchLock:
    """`<data_dir>/fetch.lock` 文件锁：多任务并发抓取串行化。

    - O_CREAT|O_EXCL 原子创建；内容为 {"pid": …, "ts": …}
    - 已存在且 mtime 超过 LOCK_STALE_SECONDS → 僵死锁，接管（覆盖写入）
    - release 校验锁内容是自己的 pid 才删除，避免误删接管者的锁
    """

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "fetch.lock")
        self._mine = False

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "ts": time.time()})
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                self._mine = True
                return True
            except FileExistsError:
                pass
            except OSError as e:
                logger.error("fetch lock create failed: %s", e)
                return False
            # 已存在：检查是否僵死
            try:
                age = time.time() - os.path.getmtime(self.path)
                if age > LOCK_STALE_SECONDS:
                    logger.warning(
                        "stale fetch lock (age %.0fs > %ds), taking over",
                        age, LOCK_STALE_SECONDS,
                    )
                    # 直接覆盖（unlink+create 有竞态窗口；覆盖写保证仍只有一个持有者语义）
                    with open(self.path, "w", encoding="utf-8") as f:
                        f.write(payload)
                    self._mine = True
                    return True
            except OSError:
                continue  # 锁刚被释放，重试
            return False

    def release(self) -> None:
        if not self._mine:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                holder = json.loads(f.read() or "{}")
            if holder.get("pid") == os.getpid():
                os.unlink(self.path)
        except (OSError, ValueError):
            pass
        self._mine = False


# ── 登录墙检测（§5.4：命中任一信号即 needs_login）──

_LOGIN_TITLE_RE = re.compile(r"登录|sign in|log in", re.IGNORECASE)
_LOGIN_URL_RE = re.compile(r"login|signin|auth", re.IGNORECASE)


def detect_login_wall(
    status_code: int | None,
    final_url: str,
    original_url: str,
    title: str,
    visible_text: str,
    has_password_input: bool,
) -> bool:
    """四信号判定，命中任一即视为登录墙。"""
    if status_code in (401, 403):
        return True
    sparse = len((visible_text or "").strip()) < LOGIN_TEXT_SPARSE
    if has_password_input and sparse:
        return True
    if _LOGIN_URL_RE.search(final_url) and not _LOGIN_URL_RE.search(original_url):
        return True
    if _LOGIN_TITLE_RE.search(title or "") and sparse:
        return True
    return False


# ── HTML → Markdown（html2text 优先，正则降级）──

def html_to_markdown(html: str) -> str:
    try:
        import html2text
        h = html2text.HTML2Text()
        h.body_width = 0            # 不强制换行
        h.ignore_images = True      # 抓正文，图片链接是噪音
        h.ignore_emphasis = False
        h.skip_internal_links = True
        return h.handle(html)
    except ImportError:
        logger.info("html2text unavailable, falling back to regex stripper")
        return _strip_html(html)


def _strip_html(html: str) -> str:
    """html2text 不可用时的正则降级（自 web_fetch 迁移，保留文本与链接结构）。"""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                  flags=re.DOTALL | re.IGNORECASE)
    # 标题/段落/换行转文本分隔
    text = re.sub(r"<(br|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", text,
                  flags=re.IGNORECASE)
    # 链接保留 href
    text = re.sub(r"<a[^>]*href=[\"']([^\"']*)[\"'][^>]*>(.*?)</a>",
                  r"\2 (\1)", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_outline(markdown: str, max_items: int = 8) -> list[str]:
    """从 Markdown 提取章节大纲（##/### 行），供摘要返回体使用。"""
    outline: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            outline.append(stripped)
            if len(outline) >= max_items:
                break
    return outline


def normalize_domain(url: str) -> str:
    """从 URL 提取小写主机名（落盘文件名 / needs_login 返回体用）。"""
    return (urlparse(url).hostname or "unknown").lower()


def sanitize_filename(name: str) -> str:
    """文件名安全化（Windows 保留字符 + 路径穿越）。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:80] or "page"
