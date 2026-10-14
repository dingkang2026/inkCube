"""browser-fetch action 执行层（2.8 §5.2）。

CLI 每步 attach 常驻面板 webview 的 target（页面状态常驻 webview，
无进程内会话管理器，§2）；执行完 disconnect，锁随 CLI 进程生命周期。
本模块不 print——stdout 是 main.py 的唯一出口。
"""
from __future__ import annotations

import os
import time

import _fetch_core as core

# 导入防线：缺 playwright 时置 None（main.py 据此出 retryable JSON），不抛出
try:
    from playwright.async_api import async_playwright, Error as PlaywrightError
except ImportError:  # pragma: no cover - 需真实缺包环境
    async_playwright = None
    PlaywrightError = Exception

DEFAULT_TIMEOUT_S = 20
SETTLE_LOAD_MS = 3000    # click 后 load 事件容错（§5.2）
SETTLE_QUIET_MS = 500    # settle 静默窗
EXTRACT_TEXT_HINT = 200  # extract 摘要的正文预览长度


def _timeout_ms(opts: dict) -> int:
    return int(opts.get("timeout", DEFAULT_TIMEOUT_S)) * 1000


async def _find_pool_page(browser):
    """按 preload 运行时标记匹配面板 webview target（锚点语义与 V1 一致）。"""
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                if await page.evaluate(core.POOL_MARK_JS):
                    return page
            except Exception:
                continue
    return None


async def _settle(page) -> None:
    try:
        await page.wait_for_load_state("load", timeout=SETTLE_LOAD_MS)
    except PlaywrightError:
        pass  # SPA 可能不触发新 load；静默窗后照常快照
    await page.wait_for_timeout(SETTLE_QUIET_MS)


async def _snapshot(page, action: str, orig_url: str, status: int | None) -> dict:
    """快照 + 登录墙检测（§4.1 步 4）。命中登录墙返回错误体。"""
    snap = await page.evaluate(core.snapshot_js())
    title = await page.title()
    url = page.url
    domain = core.normalize_domain(url)
    if core.detect_login_wall(status, url, orig_url or url, title,
                              snap["text"], snap["hasPassword"]):
        return {"error": f"该站点需要登录: {domain}",
                "needs_login": True, "domain": domain}
    return {
        "action": action,
        "url": url,
        "title": title,
        "page": {"text": snap["text"], "truncated": snap["truncated"]},
        "elements": snap["elements"],
        "needs_login": False,
    }


def _locate(page, opts: dict):
    """click 目标定位：--selector 精确 / --text 可见文本精确匹配。

    注意不用 Locator.count() 判断命中（同步调用拿到的是协程，恒真值）——
    精确匹配定位不到时由 click 分支捕获 PlaywrightError 后降级部分匹配。
    """
    if opts.get("selector"):
        return page.locator(opts["selector"]).first
    return page.get_by_text(opts["text"], exact=True).first


async def _read_type_text(opts: dict) -> str:
    """type 文本双通道（§5.2）：--text 直传 / --text-file 读工作区相对路径文件。

    --text-file 契约为工作区相对路径：realpath 归一后校验包含性，
    绝对路径/.. 穿越拒绝（与 extract --save-to 的 sanitize_filename 对称）。
    """
    if "text" in opts:
        return opts["text"]
    root = os.path.realpath(os.getcwd())
    path = os.path.realpath(os.path.join(root, opts["text_file"]))
    if os.path.commonpath([root, path]) != root:
        raise ValueError(f"--text-file 必须位于任务工作区内: {opts['text_file']}")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"--text-file 不存在: {opts['text_file']}（相对任务工作区）")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


async def run_action(action: str, opts: dict) -> dict:
    # 1. SSRF 第 1 层仅把关 open 的初始 URL（§5.3；交互中导航由 Electron 拦截层罩住）
    orig_url = opts.get("url", "")
    final_url = orig_url
    status = None
    if action == "open":
        err = core.validate_url(orig_url)
        if err:
            return {"error": err}
        final_url, probe_err = core.probe_redirect_chain(orig_url)
        if probe_err:
            return {"error": probe_err}

    # 2. 租约锁（§3.3：全 action 一致，含 close；单实例场景 data_dir 缺失时跳过）
    lock = None
    data_dir = os.environ.get("INKCUBE_DATA_DIR", "")
    if data_dir:
        lock = core.FetchLock(data_dir, os.environ.get("INKCUBE_TASK_ID", ""))
        if not lock.acquire():
            return {"error": "浏览器正被其他任务使用，请稍后重试", "retryable": True}

    try:
        return await _run_via_cdp(action, opts, final_url)
    finally:
        if lock:
            lock.release()


async def _run_via_cdp(action: str, opts: dict, final_url: str) -> dict:
    cdp_port = os.environ.get("INKCUBE_CDP_PORT", "")
    if not cdp_port:
        return {"error": "INKCUBE_CDP_PORT 未设置，浏览器操作不可用（需重启应用）"}
    if async_playwright is None:  # pragma: no cover - main.py 已拦截
        return {"error": "browser-fetch 依赖预装中，请稍后重试", "retryable": True}

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except PlaywrightError as e:
            return {"error": f"连接内置浏览器失败（CDP:{cdp_port}）: {e}"}
        try:
            page = await _find_pool_page(browser)
            if page is None:
                return {"error": "未找到浏览器面板（webview 未就绪），请重启应用"}
            return await _execute(page, action, opts, final_url)
        finally:
            # close 只断开连接，不关闭浏览器（页面常驻语义，与 V1 一致）
            try:
                await browser.close()
            except Exception:
                pass


async def _execute(page, action: str, opts: dict, final_url: str) -> dict:
    """按 action 分支执行 → settle → 快照（extract/close 例外，见各分支）。"""
    try:
        if action == "open":
            resp = await page.goto(final_url, wait_until="load", timeout=_timeout_ms(opts))
            status = resp.status if resp is not None else None
            if opts.get("wait_for"):
                try:
                    await page.wait_for_selector(opts["wait_for"], timeout=_timeout_ms(opts))
                except PlaywrightError:
                    return {"error": f"等待选择器超时: {opts['wait_for']}（页面未渲染出该元素）"}
            return await _snapshot(page, action, opts.get("url", ""), status)

        if action == "click":
            locator = _locate(page, opts)
            try:
                await locator.click(timeout=_timeout_ms(opts))
            except PlaywrightError as e:
                # --text 精确匹配未命中 → 部分匹配降级一次（可见文本常带前后缀）
                if opts.get("text"):
                    try:
                        await page.get_by_text(opts["text"]).first.click(timeout=_timeout_ms(opts))
                        await _settle(page)
                        return await _snapshot(page, action, "", None)
                    except PlaywrightError as e2:
                        return {"error": f"点击失败（文本未找到可点元素）: {e2}",
                                "hint": "快照 elements 里的 selector/文本可直接使用"}
                return {"error": f"点击失败: {e}", "hint": "快照 elements 里的 selector 可直接使用"}
            await _settle(page)
            return await _snapshot(page, action, "", None)

        if action == "type":
            text = await _read_type_text(opts)
            locator = page.locator(opts["selector"]).first
            try:
                await locator.fill(text, timeout=_timeout_ms(opts))
                if opts.get("submit"):
                    await locator.press("Enter")
            except PlaywrightError as e:
                return {"error": f"输入失败（检查 --selector 是否命中可输入元素）: {e}"}
            await _settle(page)
            return await _snapshot(page, action, "", None)

        if action == "select":
            locator = page.locator(opts["selector"]).first
            try:
                await locator.select_option(opts["value"], timeout=_timeout_ms(opts))
            except PlaywrightError as e:
                return {"error": f"下拉选择失败（值不在选项中或选择器未命中）: {e}"}
            await _settle(page)
            return await _snapshot(page, action, "", None)

        if action == "scroll":
            direction = opts.get("direction", "down")
            amount = int(opts.get("amount", 600))
            sign = -1 if direction == "up" else 1
            await page.evaluate(f"window.scrollBy(0, {sign * amount})")
            await _settle(page)
            return await _snapshot(page, action, "", None)

        if action == "extract":
            # V1 单命令抓取语义由本 action 完整承接（§5.2）
            selector = opts.get("selector")
            if opts.get("wait_for"):
                try:
                    await page.wait_for_selector(opts["wait_for"], timeout=_timeout_ms(opts))
                except PlaywrightError:
                    return {"error": f"等待选择器超时: {opts['wait_for']}"}
            if selector and await page.query_selector(selector) is None:
                return {"error": f"选择器未命中任何元素: {selector}",
                        "hint": "可去掉 --selector 提取全文，或用快照 elements 里的 selector"}
            js_extract = """
            (sel) => {
              const root = sel ? document.querySelector(sel) : document.body;
              return { html: root ? root.outerHTML : '',
                       text: root ? root.innerText : '',
                       hasPassword: !!document.querySelector('input[type=password]') };
            }"""
            extracted = await page.evaluate(js_extract, selector)
            resp_status = None
            title = await page.title()
            url = page.url
            domain = core.normalize_domain(url)
            if core.detect_login_wall(resp_status, url, url, title,
                                      extracted["text"], extracted["hasPassword"]):
                return {"error": f"该站点需要登录: {domain}",
                        "needs_login": True, "domain": domain}
            markdown = core.html_to_markdown(extracted["html"])
            fetched_dir = os.path.join(os.getcwd(), "fetched")
            os.makedirs(fetched_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            filename = opts.get("save_to") or f"{domain}-{ts}.md"
            filename = core.sanitize_filename(filename)
            if not filename.endswith(".md"):
                filename += ".md"
            path = os.path.join(fetched_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n> 来源: {url}\n\n{markdown}")
            return {
                "url": url,
                "title": title,
                "path": os.path.relpath(path, os.getcwd()).replace("\\", "/"),
                "length": len(markdown),
                "outline": core.extract_outline(markdown),
                "preview": extracted["text"][:EXTRACT_TEXT_HINT],
                "needs_login": False,
            }

        if action == "close":
            # 归位待命页（data: URL，拦截器放行）；状态复位、锁随 finally 释放
            try:
                await page.goto(core.MARK_PAGE, wait_until="load")
            except PlaywrightError:
                pass  # data: 页加载异常不阻塞归位语义
            return {"action": "close", "ok": True}

        return {"error": f"未实现的 action: {action}"}  # pragma: no cover
    except PlaywrightError as e:
        return {"error": f"页面操作失败: {e}"}
