#!/usr/bin/env python3
"""browser-fetch CLI 入口（2.8 §5.2 V2：LLM 驱动 action 循环）。

用法: python main.py <action> [参数…]
action ∈ open/click/type/select/scroll/extract/close——每步返回页面快照
（正文截断 + 可交互元素清单），LLM 看快照决定下一步。
stdout 只输出一行 JSON（唯一通信通道，adapter 解析）。
"""
from __future__ import annotations

import json
import sys

import _fetch_core as core  # noqa: F401  （import 面自检：核心模块可加载）
import _actions


def emit(obj: dict) -> None:
    """唯一出口：stdout 一行 JSON。"""
    print(json.dumps(obj, ensure_ascii=False))


# playwright 导入失败 = venv 预装后台线程尚未完成（§5.5 竞态）→ retryable。
# _actions 顶部捕获 ImportError 置 async_playwright=None（不 print），
# 此处检查属性统一出 retryable JSON（stdout 单通道不变）
if _actions.async_playwright is None:  # pragma: no cover - 需真实缺包环境
    emit({"error": "browser-fetch 依赖预装中，请稍后重试", "retryable": True})
    sys.exit(0)

run_action = _actions.run_action

# ── action 契约（§5.2 定版）──
# value: url 位置参数；required: 必填 --flag；optional: 可选 --flag→opts 键；
# oneof: 二选一必填；flags: 布尔开关
ACTIONS: dict[str, dict] = {
    "open":    {"url_required": True,
                "optional": {"--wait-for": "wait_for", "--timeout": "timeout"}},
    "click":   {"oneof": [("--text", "text"), ("--selector", "selector")],
                "optional": {"--timeout": "timeout"}},
    "type":    {"required": ["--selector"],
                "oneof": [("--text", "text"), ("--text-file", "text_file")],
                "flags": ["--submit"]},
    "select":  {"required": ["--selector", "--value"]},
    "scroll":  {"optional": {"--direction": "direction", "--amount": "amount", "--to": "to"}},
    "extract": {"optional": {"--selector": "selector", "--wait-for": "wait_for",
                             "--save-to": "save_to"}},
    "close":   {},
}

_V2_CONTRACT = (
    "browser-fetch V2 为 action 模式: open <url> [--wait-for CSS] [--timeout 秒] | "
    "click --text 文本|--selector CSS [--timeout 秒] | "
    "type --selector CSS (--text 文本|--text-file 相对路径) [--submit] | "
    "select --selector CSS --value 值 | scroll [--direction up|down] [--amount 像素] | scroll --to top|bottom | "
    "extract [--selector CSS] [--wait-for CSS] [--save-to 名.md] | close"
)

_EXAMPLES = {
    "open": "open https://example.com --wait-for .article",
    "click": 'click --text "登录"',
    "type": 'type --selector input[name=q] --text "inkcube" --submit',
    "select": 'select --selector select#size --value "M"',
    "scroll": "scroll --to bottom  # 懒加载/无限流收敛滚动后提取全文",
    "extract": "extract --selector article --save-to result.md",
    "close": "close",
}


def _norm(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def _usage_error(action: str | None, message: str) -> dict:
    obj: dict = {"error": message, "usage": _V2_CONTRACT}
    if action in _EXAMPLES:
        obj["example"] = _EXAMPLES[action]
    return obj


def parse_action(argv: list[str]) -> tuple[str | None, dict, dict | None]:
    """解析 + 预检。仅形似性检查；SSRF 安全校验由执行层全量负责（§5.2）。"""
    if not argv:
        return None, {}, _usage_error(
            None, "缺少 action 参数（open/click/type/select/scroll/extract/close）")
    action, rest = argv[0], argv[1:]
    if action.lower().startswith(("http://", "https://")):
        return None, {}, _usage_error("open", (
            f"browser-fetch V2 已改为 action 模式，不再接受 URL 直抓: {action}。"
            "先 open 拿到页面快照，再按快照决定后续 action"))
    if action not in ACTIONS:
        return None, {}, _usage_error(action,
            f"未知 action: {action}（合法: open/click/type/select/scroll/extract/close）")
    spec = ACTIONS[action]
    opts: dict = {}
    last_flag: str | None = None  # 最近一个取值 flag：未加引号的含空格值合并目标
    i = 0
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("--"):
            # open 允许一个位置参数：URL（主形态 open <url> [flags…]）
            if action == "open" and not opts.get("url"):
                opts["url"] = tok
                i += 1
                continue
            # 含空格值容错合并（2026-10-16 天眼查任务实证）：LLM 照抄快照
            # selector/name 时常漏引号（`--selector div > div > input`、
            # `--text 知识产权 78` 各报一轮「无法识别的位置参数」再自纠）。
            # 本工具除 open 的 url 外无位置参数，未识别 token 并入上一 flag
            # 值是唯一合理解释——直接合并，不再消耗一轮报错自纠
            if last_flag:
                opts[last_flag] = f"{opts[last_flag]} {tok}"
                i += 1
                continue
            return action, opts, _usage_error(action, (
                f"无法识别的位置参数: {tok}（用 --text/--selector 等 flag 传值，"
                "含空格的值请整体加引号）"))
        if tok in spec.get("flags", []):
            opts[_norm(tok)] = True
            i += 1
            continue
        if i + 1 >= len(rest):
            return action, opts, _usage_error(action, f"参数 {tok} 缺少值")
        known = (tok in spec.get("optional", {})
                 or tok in spec.get("required", [])
                 or any(tok == k for k, _v in spec.get("oneof", [])))
        if not known:
            return action, opts, _usage_error(action, f"无法识别的参数: {tok}")
        if _norm(tok) in opts:
            return action, opts, _usage_error(action, f"参数 {tok} 重复")
        opts[_norm(tok)] = rest[i + 1]
        last_flag = _norm(tok)
        i += 2
    # 必填校验
    if spec.get("url_required") and not opts.get("url"):
        return action, opts, _usage_error(action, "缺少 URL 参数（open <url>）")
    if opts.get("url") and not opts["url"].lower().startswith(("http://", "https://")):
        return action, opts, _usage_error(action, f"URL 必须 http(s):// 开头，收到: {opts['url']}")
    for req in spec.get("required", []):
        if not opts.get(_norm(req)):
            return action, opts, _usage_error(action, f"缺少必需参数 {req}")
    oneof = spec.get("oneof", [])
    if oneof:
        provided = [k for k, _v in oneof if _norm(k) in opts]
        if not provided:
            names = " 或 ".join(k for k, _v in oneof)
            return action, opts, _usage_error(action, f"必须提供 {names} 之一")
        if len(provided) > 1:
            names = " 与 ".join(k for k, _v in oneof)
            return action, opts, _usage_error(action, f"{names} 只能二选一")
    # 值域校验
    if action == "scroll":
        if "to" in opts:
            # --to 是直达/收敛语义（懒加载/无限流），与逐屏滚动参数互斥
            if opts["to"] not in ("top", "bottom"):
                return action, opts, _usage_error(action, f"--to 只支持 top|bottom，收到: {opts['to']}")
            if "direction" in opts or "amount" in opts:
                return action, opts, _usage_error(action, "--to 与 --direction/--amount 互斥（--to 自带收敛/直达语义，无需逐屏参数）")
        else:
            d = opts.get("direction", "down")
            if d not in ("up", "down"):
                return action, opts, _usage_error(action, f"--direction 只支持 up|down，收到: {d}")
            opts.setdefault("direction", d)  # 默认值写入契约层，LLM 可见
            if "amount" in opts and not opts["amount"].isdigit():
                return action, opts, _usage_error(action, f"--amount 必须为正整数，收到: {opts['amount']}")
    for int_flag in ("timeout",):
        if int_flag in opts and not opts[int_flag].isdigit():
            return action, opts, _usage_error(action, f"--{int_flag} 必须为正整数，收到: {opts[int_flag]}")
    return action, opts, None


def dispatch_argv(argv: list[str]) -> int:
    """CLI 主入口（可测）：解析 → 预检 → 执行 → emit → 返回 exit code。"""
    action, opts, err = parse_action(argv)
    if err:
        emit(err)
        return 0  # 契约错误也走 stdout JSON（exit 0，adapter 以 JSON 内容为准）
    emit(dispatch_action(action, opts))
    return 0


def dispatch_action(action: str, opts: dict) -> dict:
    """同步包装执行层：任何异常都以 JSON 形式回到 LLM（顶层防线）。"""
    import asyncio
    try:
        return asyncio.run(run_action(action, opts))
    except KeyboardInterrupt:  # pragma: no cover
        return {"error": "操作被中断"}
    except Exception as e:
        return {"error": f"browser-fetch 执行失败: {e}"}


def main() -> None:
    sys.exit(dispatch_argv(sys.argv[1:]))


if __name__ == "__main__":
    main()
