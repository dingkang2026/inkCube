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
LOGIN_GATING_MIN_HITS = 3                # 软墙（字段级遮蔽）命中次数阈值（§5.4）
SNAPSHOT_TEXT_LIMIT = 3000               # 快照正文截断（§5.2）
SNAPSHOT_ELEMENTS_LIMIT = 80             # 快照可交互元素上限（§5.2）
SNAPSHOT_NAME_LIMIT = 40                 # 元素 name 截断（§5.2）
# 待命页（close 归位目标 §5.2；前端 BrowserPanel 初始 src 用同值字符串）。
# data: URL 不产生网络请求，partition 拦截器放行（§5.3 第 2 层）。
# charset 必须显式声明：无 charset 的 data: URL 中文按 fallback 编码
# （GBK/windows-1252）解码乱码（2026-10-14 面板截图实证）
MARK_PAGE = (
    f"data:text/html;charset=utf-8,<title>{POOL_TITLE}</title>"
    # 注意：data: URL 中裸 # 会被解析为 fragment 截断（2026-10-13 review 实证），
    # 正文颜色用关键字 gray，不得用 #888 十六进制
    '<body style="font-family:sans-serif;color:gray">InkCube 网页抓取待命</body>'
)
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


# ── 跨进程抓取锁（§3.3 V2：fetch.lock，O_CREAT|O_EXCL，租约三规则）──

class FetchLock:
    """`<data_dir>/fetch.lock` 文件锁（V2 §3.3）：多任务浏览器串行化。

    - O_CREAT|O_EXCL 原子创建；内容 {"pid": …, "ts": …, "task_id": …}
    - 同 task 重入（上个 action 进程异常退出未 release）→ 接管放行
    - 异 task 且 mtime 未超 LOCK_STALE_SECONDS → 拒绝（浏览器正被其他任务使用）
    - 超 LOCK_STALE_SECONDS → 僵死接管
    - release 校验 pid + task_id 均为自己的才删除，避免误删接管者的锁
    """

    def __init__(self, data_dir: str, task_id: str = ""):
        self.path = os.path.join(data_dir, "fetch.lock")
        self.task_id = task_id
        self._mine = False

    def _payload(self) -> str:
        return json.dumps({"pid": os.getpid(), "ts": time.time(), "task_id": self.task_id})

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(self._payload())
                self._mine = True
                return True
            except FileExistsError:
                pass
            except OSError as e:
                logger.error("fetch lock create failed: %s", e)
                return False
            # 已存在：读持有者
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    holder = json.loads(f.read() or "{}")
            except (OSError, ValueError):
                holder = {}
            # 同 task 重入 → 接管（覆盖写保证单持有者语义）
            if self.task_id and holder.get("task_id") == self.task_id:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(self._payload())
                self._mine = True
                return True
            try:
                age = time.time() - os.path.getmtime(self.path)
            except OSError:
                continue  # 锁刚被释放，重试
            if age > LOCK_STALE_SECONDS:
                logger.warning(
                    "stale fetch lock (age %.0fs > %ds, holder=%s), taking over",
                    age, LOCK_STALE_SECONDS, holder.get("task_id", "?"),
                )
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(self._payload())
                self._mine = True
                return True
            return False

    def release(self) -> None:
        if not self._mine:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                holder = json.loads(f.read() or "{}")
            if (holder.get("pid") == os.getpid()
                    and holder.get("task_id") == self.task_id):
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


# 软墙（字段级遮蔽，§5.4 第 5 信号）：页面公开内容完整、仅敏感字段被登录
# 遮蔽（「181****登录查看」「登录后查看更多信息」），四信号按整页硬墙建模
# 不命中（2026-10-26 天眼查任务实证）。频次阈值避开导航栏「登录/注册」
# 级噪音；短语为中英通用措辞，非站点特判。
_GATING_TEXT_RE = re.compile(
    r"登录查看|登录后查看|log\s*in\s*to\s*view|sign\s*in\s*to\s*view"
    r"|sign\s*up\s*to\s*see",
    re.IGNORECASE)


def count_gating_hits(visible_text: str) -> int:
    """统计正文中登录遮蔽短语出现次数（软墙判定依据）。"""
    return len(_GATING_TEXT_RE.findall(visible_text or ""))


def detect_login_gating(visible_text: str) -> bool:
    """软墙判定：遮蔽短语出现 ≥ LOGIN_GATING_MIN_HITS 次。"""
    return count_gating_hits(visible_text) >= LOGIN_GATING_MIN_HITS


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


# ── 页面快照（§5.2：纯读取 evaluate，与 POOL_MARK_JS 同模式零副作用）──

def snapshot_js() -> str:
    """生成可 evaluate 的快照 JS 源码（() => {…} 形式）。

    结果：{text, truncated, elements: [{role, name, selector, href?}],
    remaining, total, hasPassword}。selector 生成优先级 #id → a[href] →
    [name=] → [data-testid=] → nth-of-type 路径，每一级都过 querySelectorAll
    唯一性验证（不唯一则降级下一级；路径级从 3 层起逐层延长至唯一）——
    selector 与可见文本跨调用稳定，从根上避免 ref 编号漂移（§1 元素定位
    决策）；唯一性验证根治前缀歧义 selector（2026-10-16 天眼查实证：
    `div:nth-of-type(1) > div > a` 同时命中导航与搜索结果，locator.first
    点错跳回首页）。链接元素附 href，支持 open <href> 直达导航。
    视口内元素优先入列。
    """
    return """() => {
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, [role], [onclick]';
  const nodes = document.querySelectorAll(SEL);
  const inVp = [], offVp = [];
  const rectOf = (el) => el.getBoundingClientRect();
  const isVisible = (el) => {
    const r = rectOf(el);
    if (r.width <= 0 || r.height <= 0) return false;
    // visibility:hidden/collapse 保留布局、rect 有宽高，几何判定漏掉它——
    // 快照把隐藏副本列进 elements，agent 照用必撞 actionability 超时
    // （2026-10-14 天眼查双搜索框实证）；定义对齐 Playwright visible
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.visibility !== 'collapse';
  };
  const isInside = (el) => {
    const r = rectOf(el);
    return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
  };
  for (const el of nodes) { if (isVisible(el)) (isInside(el) ? inVp : offVp).push(el); }

  const escAttr = (v) => v.replace(/"/g, '\\\\"');
  const count = (sel) => document.querySelectorAll(sel).length;
  const segOf = (cur) => {
    const tag = cur.tagName.toLowerCase();
    const same = Array.from(cur.parentElement.children).filter((c) => c.tagName === cur.tagName);
    return same.length > 1 ? tag + ':nth-of-type(' + (same.indexOf(cur) + 1) + ')' : tag;
  };
  const pathOf = (el, depth) => {
    const parts = [];
    let cur = el;
    for (let i = 0; i < depth && cur && cur !== document.body; i++) {
      if (!cur.parentElement) break;
      parts.unshift(segOf(cur));
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  };

  const selectorOf = (el) => {
    // 唯一性优先级：#id → a[href]（href 相同即同目标，但仍要求唯一——
    // 重复 href 的 .first 可能是隐藏副本，照用必撞 actionability）→
    // [name=] → [data-testid=] → nth-of-type 路径。每级都验证唯一，
    // 未命中则降级下一级；路径从 3 层起逐层延长至唯一（上限 8 层，
    // 深层 DOM 下 3 层截断路径跨容器重名——天眼查歧义实证）
    if (el.id) {
      const sel = '#' + CSS.escape(el.id);
      if (count(sel) === 1) return sel;
    }
    const href = el.getAttribute('href');
    if (href) {
      const sel = 'a[href="' + escAttr(href) + '"]';
      if (count(sel) === 1) return sel;
    }
    const nm = el.getAttribute('name');
    if (nm) {
      const sel = el.tagName.toLowerCase() + '[name="' + escAttr(nm) + '"]';
      if (count(sel) === 1) return sel;
    }
    const dt = el.getAttribute('data-testid');
    if (dt) {
      const sel = '[data-testid="' + escAttr(dt) + '"]';
      if (count(sel) === 1) return sel;
    }
    for (let depth = 3; depth <= 8; depth++) {
      const sel = pathOf(el, depth);
      if (sel && count(sel) === 1) return sel;
    }
    return pathOf(el, 8);  // 结构性重复（同类列表项）取最深路径
  };

  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      return 'textbox';
    }
    return tag;
  };

  const nameOf = (el) => {
    const label = el.getAttribute('aria-label')
      || (el.labels && el.labels[0] && el.labels[0].innerText)
      || el.innerText || el.value || el.getAttribute('placeholder')
      || el.getAttribute('title') || '';
    return label.trim().replace(/\\s+/g, ' ').slice(0, %(name_limit)d);
  };

  const elements = [];
  let remaining = 0;
  for (const el of [...inVp, ...offVp]) {
    const role = roleOf(el);
    const name = nameOf(el);
    if (!name && role !== 'textbox' && role !== 'select' && role !== 'textarea') continue;
    if (elements.length >= %(elem_limit)d) { remaining++; continue; }
    const item = { role, name, selector: selectorOf(el) };
    const href = el.getAttribute('href');
    if (href) item.href = href;  // 链接直达：open <href> 比 click 试错稳（天眼查绕道实证）
    elements.push(item);
  }

  const fullText = document.body ? (document.body.innerText || '') : '';
  // 滚动进度（懒加载/无限流：agent 据此判断是否到底——remaining=0 即视觉
  // 到底；height 供收敛判断，瀑布流每轮追加加载会推高它）
  const docH = Math.max(
    document.body ? document.body.scrollHeight : 0,
    document.documentElement.scrollHeight);
  return {
    text: fullText.slice(0, %(text_limit)d),
    truncated: fullText.length > %(text_limit)d,
    elements,
    remaining,
    total: nodes.length,
    hasPassword: !!document.querySelector('input[type=password]'),
    scroll: {
      y: Math.round(window.scrollY),
      height: docH,
      remaining: Math.max(0, docH - window.scrollY - window.innerHeight),
    },
  };
}""" % {
        "name_limit": SNAPSHOT_NAME_LIMIT,
        "elem_limit": SNAPSHOT_ELEMENTS_LIMIT,
        "text_limit": SNAPSHOT_TEXT_LIMIT,
    }
