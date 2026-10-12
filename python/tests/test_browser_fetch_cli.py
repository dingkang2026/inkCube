# -*- coding: utf-8 -*-
"""browser-fetch CLI 入口参数契约预检测试（2.8 §5.2）。

回归背景：弱模型把自然语言需求直译成 command="open pricing"，argparse
对必填位置参数只打英文 usage 后 exit 2，无法引导 LLM 自纠——模型误判
「工具不支持打开网址」后放弃（2026-10-11 app.log 实证）。预检以中文
JSON 错误 + 正确示例替代，stdout 单行 JSON 契约不变，LLM 可下轮自纠。

模块加载：main.py 位于 skill 安装源（非包），与 test_browser_fetch_core
同样用 importlib 直载；先注册 _fetch_core 再载 main（顶部 import 依赖）。
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "resources" / "skills" / "browser-fetch" / "scripts"
)

_core_spec = importlib.util.spec_from_file_location(
    "_fetch_core", str(_SCRIPTS_DIR / "_fetch_core.py"))
_core_mod = importlib.util.module_from_spec(_core_spec)
sys.modules.setdefault("_fetch_core", _core_mod)
_core_spec.loader.exec_module(_core_mod)

_main_spec = importlib.util.spec_from_file_location(
    "browser_fetch_main_test", str(_SCRIPTS_DIR / "main.py"))
main = importlib.util.module_from_spec(_main_spec)
sys.modules.setdefault("browser_fetch_main_test", main)
try:
    _main_spec.loader.exec_module(main)
except SystemExit:
    # 模块级 playwright ImportError 分支（emit retryable + exit 0）是设计
    # 行为：无 playwright 的环境下本文件没有可测对象
    pytest.skip("playwright 未安装，main.py 未完成加载", allow_module_level=True)


def _exit_with_json(argv, capsys):
    """parse_args 预检路径：断言 exit 0 且 stdout 是单行 JSON，返回之。"""
    with pytest.raises(SystemExit) as ei:
        main.parse_args(argv)
    assert ei.value.code == 0
    out = capsys.readouterr().out.strip()
    assert "\n" not in out  # stdout 单行 JSON 契约
    return json.loads(out)


class TestArgContractPreflight:
    def test_missing_url_returns_usage_and_example(self, capsys):
        """argv 为空 → 缺 URL 错误 + 用法契约 + 正确示例（LLM 可自纠）。"""
        result = _exit_with_json([], capsys)
        assert "URL" in result["error"]
        assert result["usage"].startswith("browser-fetch <url>")
        assert result["example"].startswith('browser-fetch "https://')

    def test_natural_language_first_arg_rejected(self, capsys):
        """实测失败模式 ["open", "pricing"]：首个位置参数非 URL → 指名道姓。"""
        result = _exit_with_json(["open", "pricing"], capsys)
        assert '"open"' in result["error"]          # 指出收到的错误值
        assert "http" in result["error"]            # 给出形态要求
        assert "https://" in result["example"]      # 给出正确示例

    def test_unrecognized_extra_rejected(self, capsys):
        """合法 URL 后跟未知参数 → 列出多余参数（替代 argparse 英文报错）。"""
        result = _exit_with_json(["https://example.com/", "--bogus", "x"], capsys)
        assert "无法识别的参数" in result["error"]
        assert "--bogus" in result["error"]

    def test_option_values_not_mistaken_as_positional(self, capsys):
        """--selector 的值不以 - 开头，不得误判为位置参数/多余参数。"""
        args = main.parse_args(
            ["https://example.com/a", "--selector", "main.content"])
        assert args.url == "https://example.com/a"
        assert args.selector == "main.content"

    def test_valid_url_passes_through(self):
        """合法 URL 直通（返回 Namespace，不退出、不产出 stdout）。"""
        args = main.parse_args(["https://example.com/pricing"])
        assert args.url == "https://example.com/pricing"
        assert args.selector is None
        assert args.wait_for is None
        assert args.timeout == 20
        assert args.save_to is None


# ── find_pool_page 匹配锚点回归（2026-10-12：title 锚点被首次抓取污染）──


class _MockPage:
    """最小 page 桩：只实现 find_pool_page 用到的 evaluate。"""

    def __init__(self, evaluate_result=None, raise_exc=None, name="page"):
        self._result = evaluate_result
        self._exc = raise_exc
        self.name = name

    async def evaluate(self, _js):
        if self._exc:
            raise self._exc
        return self._result


class _MockContext:
    def __init__(self, pages):
        self.pages = pages


class _MockBrowser:
    def __init__(self, contexts):
        self.contexts = contexts


class TestFindPoolPage:
    """同步包装 asyncio.run（项目无 pytest-asyncio 配置，与现有同步用例一致）。"""

    def test_mark_hit_across_contexts(self):
        """主窗口（无标记）与池窗口（有标记）分属不同 context，仍能命中。"""
        main_win = _MockPage(evaluate_result=False, name="main")
        pool = _MockPage(evaluate_result=True, name="pool")
        browser = _MockBrowser([_MockContext([main_win]), _MockContext([pool])])

        assert asyncio.run(main.find_pool_page(browser)) is pool

    def test_raising_page_skipped_not_fatal(self):
        """evaluate 抛异常的 page 跳过，不阻断后续 page 的标记命中。"""
        broken = _MockPage(raise_exc=RuntimeError("target closed"))
        pool = _MockPage(evaluate_result=True, name="pool")
        browser = _MockBrowser([_MockContext([broken, pool])])

        assert asyncio.run(main.find_pool_page(browser)) is pool

    def test_no_mark_returns_none(self):
        """全部 page 无标记（旧窗口无 preload / 未启动）→ None。"""
        browser = _MockBrowser([_MockContext([_MockPage(evaluate_result=False)])])

        assert asyncio.run(main.find_pool_page(browser)) is None

    def test_mark_js_is_pure_read_expression(self):
        """锚点表达式精确锁定：纯读取（无副作用），可对任意页面安全执行。"""
        assert _core_mod.POOL_MARK_JS == "() => window.__INKCUBE_POOL__ === true"
