#!/usr/bin/env python3
"""browser-fetch CLI 入口（设计文档 2.8 §5.2）。

用法: python main.py <url> [--selector CSS] [--wait-for CSS] [--timeout 秒] [--save-to 名.md]

流程: 参数契约预检 → SSRF 探测链 → 抓取锁 → CDP attach 抓取窗口 →
      导航/等待 → 提取 → 登录墙检测 → HTML→MD 落盘 → stdout JSON 摘要。
stdout 只输出一行 JSON（唯一通信通道，adapter 解析）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import _fetch_core as core


def emit(obj: dict) -> None:
    """唯一出口：stdout 一行 JSON。"""
    print(json.dumps(obj, ensure_ascii=False))


# playwright 导入失败 = venv 预装后台线程尚未完成（§5.5 竞态）→ retryable
try:
    from playwright.async_api import async_playwright, Error as PlaywrightError
except ImportError:  # pragma: no cover - 需真实缺包环境
    emit({"error": "browser-fetch 依赖预装中，请稍后重试", "retryable": True})
    sys.exit(0)


# 参数契约摘要：预检错误里随 JSON 返回，LLM 拿到即可下轮自纠
_ARG_CONTRACT = (
    "browser-fetch <url> [--selector CSS] [--wait-for CSS] "
    "[--timeout 秒] [--save-to 文件名.md]"
)


def _arg_contract_error(url: str, extra: list[str]) -> dict | None:
    """argparse 之后的参数契约检查（返回错误 dict，None 表示通过）。

    实测失败模式：弱模型把自然语言需求直译成 command="open pricing"，
    argparse 的英文 usage + unrecognized arguments 无法引导自纠，模型
    误判「工具不支持打开网址」后直接放弃。这里用中文 JSON 错误 + 正确
    示例替代（stdout 单行 JSON 契约不变）；仅做形似性检查，SSRF 安全
    校验仍由 fetch() 里的 core.validate_url 全量负责，职责不重叠。
    """
    if not url:
        return {
            "error": "缺少 URL 参数（第一个位置参数）",
            "usage": _ARG_CONTRACT,
            "example": 'browser-fetch "https://example.com/pricing"',
        }
    if not url.lower().startswith(("http://", "https://")):
        return {
            "error": (
                f'第一个参数必须是 http(s):// 开头的完整 URL，收到: "{url}"'
                "（自然语言需求不能直接作为本工具的输入，请翻译成 URL）"
            ),
            "usage": _ARG_CONTRACT,
            "example": 'browser-fetch "https://example.com/pricing"',
        }
    if extra:
        return {
            "error": f"无法识别的参数: {' '.join(extra)}",
            "usage": _ARG_CONTRACT,
        }
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="browser-fetch")
    # url 用 nargs="?" + 主动预检：argparse 对必填位置参数缺失只会打英文
    # usage 后 exit 2，LLM 无法自纠（见 _arg_contract_error docstring）
    parser.add_argument("url", nargs="?", default="",
                        help="目标页 URL（http/https）")
    parser.add_argument("--selector", default=None, help="CSS 选择器，只提取匹配区域")
    parser.add_argument("--wait-for", dest="wait_for", default=None,
                        help="等待该选择器出现后再提取")
    parser.add_argument("--timeout", type=int, default=20, help="超时秒数，默认 20")
    parser.add_argument("--save-to", dest="save_to", default=None, help="落盘文件名")
    args, extra = parser.parse_known_args(argv)
    err = _arg_contract_error(args.url, extra)
    if err:
        emit(err)
        sys.exit(0)
    return args


async def find_pool_page(browser) -> object | None:
    """按 preload 运行时标记匹配抓取窗口 target（§5.3）。

    window.__INKCUBE_POOL__ 由 fetch-pool-preload.js 注入，每次导航后
    自动重执行——窗口无论停在待命页、抓取目标页还是登录页，标记恒在。
    （title 匹配不可用：抓取后 document.title 是目标页面内容，首次成功
    即污染锚点。）
    """
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                if await page.evaluate(core.POOL_MARK_JS):
                    return page
            except Exception:
                continue
    return None


async def fetch(args: argparse.Namespace) -> dict:
    # 1. SSRF：基础校验 + 重定向探测链逐跳复检
    err = core.validate_url(args.url)
    if err:
        return {"error": err}
    final_url, probe_err = core.probe_redirect_chain(args.url)
    if probe_err:
        return {"error": probe_err}

    # 2. 跨进程抓取锁（data_dir 由 Electron 注入；缺失时跳过锁——单实例场景）
    lock = None
    data_dir = os.environ.get("INKCUBE_DATA_DIR", "")
    if data_dir:
        lock = core.FetchLock(data_dir)
        if not lock.acquire():
            return {"error": "另一抓取正在进行，请稍后重试", "retryable": True}

    try:
        return await _fetch_via_cdp(args, final_url)
    finally:
        if lock:
            lock.release()


async def _fetch_via_cdp(args: argparse.Namespace, url: str) -> dict:
    cdp_port = os.environ.get("INKCUBE_CDP_PORT", "")
    if not cdp_port:
        return {"error": "INKCUBE_CDP_PORT 未设置，浏览器抓取不可用（需重启应用）"}

    timeout_ms = args.timeout * 1000
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except PlaywrightError as e:
            return {"error": f"连接内置浏览器失败（CDP:{cdp_port}）: {e}"}

        try:
            pool = await find_pool_page(browser)
            if pool is None:
                return {"error": "未找到抓取窗口（inkcube-fetch-pool），请重启应用"}

            # 3. 导航 + 等待（Electron 侧经 did-start-navigation 感知并显示窗口）
            resp = None
            try:
                resp = await pool.goto(url, wait_until="load", timeout=timeout_ms)
            except PlaywrightError as e:
                return {"error": f"页面加载失败: {e}"}
            if args.wait_for:
                try:
                    await pool.wait_for_selector(args.wait_for, timeout=timeout_ms)
                except PlaywrightError:
                    return {"error": f"等待选择器超时: {args.wait_for}（页面未渲染出该元素）"}

            status = resp.status if resp is not None else None
            title = await pool.title()
            original_host = core.normalize_domain(url)
            final_url = pool.url

            # 4. 提取（selector 命中取区域，否则全文）+ 登录墙信号
            if args.selector:
                sel_exists = await pool.query_selector(args.selector)
                if sel_exists is None:
                    return {"error": f"选择器未命中任何元素: {args.selector}",
                            "hint": "可去掉 --selector 抓全文，或换用页面实际存在的选择器"}
            js_extract = """
            (sel) => {
              const root = sel ? document.querySelector(sel) : document.body;
              return {
                html: root ? root.outerHTML : '',
                text: root ? root.innerText : '',
                hasPassword: !!document.querySelector('input[type=password]'),
              };
            }"""
            extracted = await pool.evaluate(js_extract, args.selector)

            # 5. 登录墙检测（§5.4）
            if core.detect_login_wall(
                status, final_url, url, title,
                extracted["text"], extracted["hasPassword"],
            ):
                return {
                    "error": f"该站点需要登录: {original_host}",
                    "needs_login": True,
                    "domain": original_host,
                }

            # 6. HTML→MD → 落盘（正文不进上下文，stdout 只有摘要）
            markdown = core.html_to_markdown(extracted["html"])
            fetched_dir = os.path.join(os.getcwd(), "fetched")
            os.makedirs(fetched_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            filename = args.save_to or f"{original_host}-{ts}.md"
            filename = core.sanitize_filename(filename)
            if not filename.endswith(".md"):
                filename += ".md"
            path = os.path.join(fetched_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n> 来源: {final_url}\n\n{markdown}")

            return {
                "url": final_url,
                "title": title,
                "path": os.path.relpath(path, os.getcwd()).replace("\\", "/"),
                "length": len(markdown),
                "outline": core.extract_outline(markdown),
                "needs_login": False,
            }
        finally:
            # connect_over_cdp 的 close 只断开连接，不关闭浏览器（窗口常驻语义）
            try:
                await browser.close()
            except Exception:
                pass


def main() -> None:
    args = parse_args(sys.argv[1:])
    try:
        emit(asyncio.run(fetch(args)))
    except KeyboardInterrupt:  # pragma: no cover
        emit({"error": "抓取被中断"})
    except Exception as e:  # 顶层兜底：任何异常都以 JSON 形式回到 LLM
        emit({"error": f"抓取失败: {e}"})


if __name__ == "__main__":
    main()
