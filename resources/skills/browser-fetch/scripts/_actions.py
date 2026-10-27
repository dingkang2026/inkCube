"""browser-fetch action 执行层（2.8 §5.2）。

CLI 每步 attach 常驻面板 webview 的 target（页面状态常驻 webview，
无进程内会话管理器，§2）；执行完 disconnect，锁随 CLI 进程生命周期。
本模块不 print——stdout 是 main.py 的唯一出口。
"""
from __future__ import annotations

import os
import random
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
SETTLE_NETWORK_MS = 3000  # networkidle 容错窗：dcl 后二次导航+首屏 XHR 完成（长轮询站永不空闲则超时照常）
SETTLE_QUIET_MS = 500    # settle 静默窗
HUMAN_PAUSE_MS = (120, 380)   # 动作后拟人停顿（随机，模拟人看完页面再决定下一步）
TYPE_KEY_DELAY_MS = (40, 140)  # 逐键输入随机键间隔（行为风控按键盘节奏识别自动化）
MOUSE_STEPS = (10, 22)   # 拟真鼠标轨迹步数（locator.click 默认瞬移，无 mousemove 事件流）
EXTRACT_TEXT_HINT = 200  # extract 摘要的正文预览长度
SCROLL_MAX_ROUNDS = 12         # scroll --to bottom 收敛滚动轮数上限（无限流防烧预算）
SCROLL_STABLE_ROUNDS = 2       # 高度连续 N 轮无增长 = 内容收敛（加载器无更多）
SCROLL_BOTTOM_BUDGET_MS = 45_000  # 收敛滚动总预算（超预算按未收敛返回，LLM 可判断内容可能不全）

# 轻量滚动信息探针（收敛循环每轮读取；与 snapshot_js 分离避免全量枚举。
# JS 以 "() => ({y:" 开头，与快照源区分——测试 FakePage 据此分发）
SCROLL_INFO_JS = ("() => ({y: window.scrollY,"
                  " h: Math.max(document.body ? document.body.scrollHeight : 0,"
                  " document.documentElement.scrollHeight), vh: window.innerHeight})")


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
    try:
        # 2026-10-14 天眼查实证：dcl 后的二次导航（登录重定向/URL 规范化）
        # 会销毁快照 evaluate 上下文——networkidle 意味着导航与首屏 XHR 全部
        # 结束，根治快照撞导航窗口；长轮询/广告站永不空闲 → 容错窗后照常快照
        await page.wait_for_load_state("networkidle", timeout=SETTLE_NETWORK_MS)
    except PlaywrightError:
        pass
    await page.wait_for_timeout(SETTLE_QUIET_MS)
    # 拟人节奏：真人每步操作后有阅读/决策停顿，匀速连发是自动化特征
    await page.wait_for_timeout(random.randint(*HUMAN_PAUSE_MS))


async def _snapshot(page, action: str, orig_url: str, status: int | None) -> dict:
    """快照 + 登录墙检测（§4.1 步 4）。命中登录墙返回错误体。

    evaluate 撞上导航窗口（Execution context was destroyed）时：导航本身
    成功（用户可视面板页面已打开），报失败会误导上层重复 open——等待导航
    稳定后重取一次（此时 status 已不可信，按 None 传入登录墙检测）。
    """
    try:
        snap = await page.evaluate(core.snapshot_js())
        title = await page.title()
        url = page.url
    except PlaywrightError as e:
        if "Execution context was destroyed" not in str(e):
            raise
        await page.wait_for_load_state("load", timeout=SETTLE_LOAD_MS)
        await page.wait_for_timeout(SETTLE_QUIET_MS)
        snap = await page.evaluate(core.snapshot_js())
        title = await page.title()
        url = page.url
        status = None
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
        "remaining": snap.get("remaining", 0),
        "scroll": snap.get("scroll"),
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


async def _click_human(page, locator, timeout_ms: int) -> None:
    """拟真点击：mouse.move(steps) 轨迹移动到元素 → locator.click 原地按下。

    locator.click 默认鼠标瞬移（无 mousemove 事件流），行为风控采集轨迹熵；
    轨迹移动顺带产生真实 hover 事件。终点仍用 locator.click：actionability
    检查（可见/可点/接收事件）完整保留，只拟真移动不牺牲可靠性。隐藏元素
    拿不到 bounding_box → 降级原 click（由 actionability 报精确错误）。
    """
    box = await locator.bounding_box()
    if box:
        jx = min(4, box["width"] / 4)
        jy = min(4, box["height"] / 4)
        cx = box["x"] + box["width"] / 2 + random.uniform(-jx, jx)
        cy = box["y"] + box["height"] / 2 + random.uniform(-jy, jy)
        await page.mouse.move(cx, cy, steps=random.randint(*MOUSE_STEPS))
    await locator.click(timeout=timeout_ms)


async def _type_human(locator, text: str, timeout_ms: int, submit: bool) -> None:
    """拟真输入：先清空 → 逐键输入（随机键间隔）→ 可选回车。

    fill 是瞬时置值（单个 input 事件，零键盘事件流）；真人输入是逐键
    keydown/keypress 流。press_sequentially 发真实按键事件且自带
    scroll-into-view（输入框在视口外时自动滚入视野）。
    """
    await locator.fill("", timeout=timeout_ms)  # 真人先清空再输入
    await locator.press_sequentially(
        text, delay=random.randint(*TYPE_KEY_DELAY_MS), timeout=timeout_ms)
    if submit:
        await locator.press("Enter", timeout=timeout_ms)


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


async def _scroll_to_bottom(page, action: str) -> dict:
    """懒加载/无限滚动收敛滚动（2026-10-14 用户提出）：滚到底 → settle → 高度仍增长则继续。

    瀑布流页面没有真正的底：每轮触发的追加加载会推高 scrollHeight，高度
    连续 SCROLL_STABLE_ROUNDS 轮无增长即加载器无更多内容（普通长页首轮
    到底后两轮即收敛）。轮数上限/时间预算双保险防失控；返回体带 rounds
    与 converged——未收敛说明内容可能仍不完整，LLM 可据此决定是否继续。
    """
    rounds = 0
    stable = 0
    last_height = 0
    deadline = time.monotonic() + SCROLL_BOTTOM_BUDGET_MS / 1000
    while rounds < SCROLL_MAX_ROUNDS and time.monotonic() < deadline:
        await page.evaluate(
            "window.scrollTo(0, Math.max("
            "document.body ? document.body.scrollHeight : 0,"
            " document.documentElement.scrollHeight))")
        await _settle(page)
        rounds += 1
        info = await page.evaluate(SCROLL_INFO_JS)
        height = info["h"]
        if height <= last_height:
            stable += 1
            if stable >= SCROLL_STABLE_ROUNDS:
                break
        else:
            stable = 0
        last_height = height
    out = await _snapshot(page, action, "", None)
    out["scroll_rounds"] = rounds
    out["converged"] = stable >= SCROLL_STABLE_ROUNDS
    return out


async def _execute(page, action: str, opts: dict, final_url: str) -> dict:
    """按 action 分支执行 → settle → 快照（extract/close 例外，见各分支）。"""
    try:
        if action == "open":
            # 就绪语义（2026-10-14 新浪/天眼查两任务实证）：wait_until="load"
            # 对重广告资源站拖满超时（ent.sina.com.cn 首次必 20s 超时）——
            # 改 dcl + _settle 组合：load 在 3s 容错窗内来则等到（保留就绪
            # 语义），不来则静默窗后照常快照，不再整体超时
            resp = await page.goto(final_url, wait_until="domcontentloaded",
                                   timeout=_timeout_ms(opts))
            await _settle(page)
            status = resp.status if resp is not None else None
            if opts.get("wait_for"):
                try:
                    await page.wait_for_selector(opts["wait_for"], timeout=_timeout_ms(opts))
                except PlaywrightError:
                    # 等待的元素未出现 ≠ 页面不可用（2026-10-16 天眼查实证：
                    # --wait-for input 超时返回裸错误，LLM 无页面实况可看，
                    # 绕道 web_search 烧两轮）。返回当前快照 + warning，
                    # LLM 基于实况决策；命中登录墙时仍按登录墙错误体返回
                    # （登录引导优先于快照）
                    snap = await _snapshot(page, action, opts.get("url", ""), status)
                    if "error" not in snap:
                        snap["warning"] = (
                            f"等待选择器超时: {opts['wait_for']}"
                            "（元素未渲染；以上为页面当前实况，未加载完可稍后重试）")
                    return snap
            return await _snapshot(page, action, opts.get("url", ""), status)

        if action == "click":
            locator = _locate(page, opts)
            try:
                await _click_human(page, locator, _timeout_ms(opts))
            except PlaywrightError as e:
                # --text 精确匹配未命中 → 部分匹配降级一次（可见文本常带前后缀）
                if opts.get("text"):
                    try:
                        await _click_human(page, page.get_by_text(opts["text"]).first, _timeout_ms(opts))
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
            sel = opts["selector"]
            locator = page.locator(sel).first
            # 隐藏副本自动切换（2026-10-14 天眼查实证：首页多个同名搜索框
            # （桌面/移动端互斥显隐），.first 命中 visibility:hidden 副本时
            # actionability 永不过 → fill 超时 20s。first 不可见时改选第一个
            # 真可见副本；无副本则保留 first 由 actionability 报精确错误）
            try:
                if not await locator.is_visible():
                    vis = page.locator(sel).filter(visible=True).first
                    if await vis.count() > 0:
                        locator = vis
                await _type_human(locator, text, _timeout_ms(opts), bool(opts.get("submit")))
            except PlaywrightError as e:
                return {"error": f"输入失败（检查 --selector 是否命中可输入元素）: {e}",
                        "hint": "元素不可见多为表单在折叠区/tab/弹窗内——先 click 展开对应区域再 type"}
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
            if opts.get("to") == "bottom":
                return await _scroll_to_bottom(page, action)
            if opts.get("to") == "top":
                await page.evaluate("window.scrollTo(0, 0)")
                await _settle(page)
                return await _snapshot(page, action, "", None)
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
                    return {"error": f"等待选择器超时: {opts['wait_for']}",
                            "hint": "可去掉 --wait-for 直接提取当前内容"}
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
            # 产物目录锚 data_dir/fetched（B7 审计）：抓取产物是跨任务资产，
            # 落任务工作区会随归档清盘/重置蒸发，且数据管理面板不可见；
            # 统一入 data_dir（task_agent 已将 fetched/ 注册为 file_ops
            # 只读根，任务内可用返回的绝对路径回读）。无 INKCUBE_DATA_DIR
            # （dev 直跑技能调试）保持 cwd/fetched 兜底，判定与租约锁一致。
            data_dir_env = os.environ.get("INKCUBE_DATA_DIR", "")
            fetched_dir = (os.path.join(data_dir_env, "fetched") if data_dir_env
                           else os.path.join(os.getcwd(), "fetched"))
            os.makedirs(fetched_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            filename = opts.get("save_to") or f"{domain}-{ts}.md"
            filename = core.sanitize_filename(filename)
            if not filename.endswith(".md"):
                filename += ".md"
            path = os.path.join(fetched_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n> 来源: {url}\n\n{markdown}")
            # 软墙检测只挂 extract（§5.4 第 5 信号）：这里才拿得到完整
            # innerText——快照文本经 SNAPSHOT_TEXT_LIMIT 截断，遮蔽短语多在
            # 中后部（2026-10-26 天眼查实证：截断区内仅 1 处，全文 12 处）
            gated = core.detect_login_gating(extracted["text"])
            body = {
                "url": url,
                "title": title,
                # 绝对路径（/ 分隔）：锚 data_dir 后相对工作区是 ../ 深链，
                # file_ops read 直接消费绝对路径
                "path": path.replace("\\", "/"),
                "length": len(markdown),
                "outline": core.extract_outline(markdown),
                "preview": extracted["text"][:EXTRACT_TEXT_HINT],
                "needs_login": gated,
            }
            if gated:
                # 数据照常返回（落盘/摘要不缺）；needs_login+domain 复用前端
                # 登录横幅（§8.3），login_gated 供 LLM 按 SKILL.md 提示用户
                # 登录后重新采集，禁止静默跳过遮蔽字段收尾
                body["login_gated"] = True
                body["domain"] = domain
            return body

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
