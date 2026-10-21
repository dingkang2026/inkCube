# -*- coding: utf-8 -*-
"""test_browser_fetch_actions.py — browser-fetch action 层拟真化与导航容错（2.8 §5.2）。

覆盖（2026-10-14 天眼查任务实证的三类问题，全部通用实现、零站点特判）：
1. 快照撞导航窗口：dcl 后页面的二次导航销毁 evaluate 上下文（Execution
   context was destroyed）——导航本身成功（用户可视面板已打开），报失败会
   误导上层重复 open。_snapshot 必须等稳定后重取一次。
2. 拟真输入：fill 是瞬时置值（无键盘事件流），行为风控按键盘节奏识别自动
   化——type 必须逐键（press_sequentially）+ 随机键间隔 + 先清空。
3. 拟真点击：locator.click 鼠标瞬移（无 mousemove 轨迹）——click 前必须
   mouse.move(steps) 轨迹移动；无 bounding_box（隐藏元素）降级原 click。
4. settle 语义：load 容错窗后追加 networkidle（二次导航+首屏 XHR 完成）+
   随机拟人停顿；长轮询站永不空闲 → 容错窗后照常。

模块加载：_actions.py 位于 skill 安装源（非包），importlib 直载；
`import _fetch_core` 为裸名 —— scripts 目录先入 sys.path。
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "resources" / "skills" / "browser-fetch" / "scripts"
)
sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location("_actions_test", str(_SCRIPTS / "_actions.py"))
actions = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("_actions_test", actions)
_spec.loader.exec_module(actions)

PWError = actions.PlaywrightError

_SNAP_OK = {"text": "正文内容", "truncated": False, "elements": [], "remaining": 0, "hasPassword": False}


def _destroyed() -> PWError:
    return PWError(
        "Execution context was destroyed, most likely because of a navigation")


class FakeLocator:
    def __init__(self, box=None, visible=True, count=1):
        self._box = box
        self._visible = visible
        self._count = count
        self.click_calls = []
        self.fills = []
        self.seq_calls = []
        self.press_calls = []
        self.filtered_with = []   # filter(visible=X) 调用记录
        self.is_visible_calls = 0

    @property
    def first(self):
        return self

    async def is_visible(self):
        self.is_visible_calls += 1
        return self._visible

    def filter(self, *, visible=None):
        self.filtered_with.append(visible)
        return self

    async def count(self):
        return self._count

    async def bounding_box(self):
        return self._box

    async def click(self, timeout=None):
        self.click_calls.append(timeout)

    async def fill(self, text, timeout=None):
        self.fills.append((text, timeout))

    async def press_sequentially(self, text, delay=None, timeout=None):
        self.seq_calls.append((text, delay, timeout))

    async def press(self, key, timeout=None):
        self.press_calls.append(key)


class FakeMouse:
    def __init__(self):
        self.moves = []
        self.clicks = []

    async def move(self, x, y, steps=None):
        self.moves.append((x, y, steps))

    async def click(self, x, y):
        self.clicks.append((x, y))


class FakePage:
    def __init__(self, eval_seq=None, locator_spec=None, scroll_infos=None):
        self.url = "https://site.example/page"
        self._eval_seq = list(eval_seq or [])
        self.mouse = FakeMouse()
        self.load_states = []
        self.timeouts = []
        self.locator_sels = []    # locator(sel) 调用记录
        self.locator_log = []     # locator(sel) 返回的实例（按序）
        self._locator_spec = dict(locator_spec or {})
        self._scroll_infos = list(scroll_infos or [])  # SCROLL_INFO_JS 探针返回序列
        self.eval_calls = []      # 非-探针 evaluate JS 记录（scrollTo/scrollBy）

    def locator(self, sel):
        self.locator_sels.append(sel)
        loc = FakeLocator(**self._locator_spec)
        self.locator_log.append(loc)
        return loc

    async def evaluate(self, js, arg=None):
        if js.startswith("() => ({y:"):   # _scroll_to_bottom 的滚动信息探针
            return self._scroll_infos.pop(0)
        self.eval_calls.append(js)
        if self._eval_seq:
            item = self._eval_seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return dict(_SNAP_OK)

    async def title(self):
        return "Site Page"

    async def wait_for_load_state(self, state="load", timeout=None):
        self.load_states.append(state)

    async def wait_for_timeout(self, ms):
        self.timeouts.append(ms)


# ── 1. 快照撞导航窗口：destroyed 重取 ──

def test_snapshot_retries_after_context_destroyed():
    """首次 evaluate 撞 destroyed → 等 load 稳定 → 重取成功，按成功返回。"""
    page = FakePage(eval_seq=[_destroyed(), dict(_SNAP_OK)])
    out = asyncio.run(actions._snapshot(page, "open", "https://site.example/page", 200))
    assert out["action"] == "open"
    assert out["url"] == "https://site.example/page"
    assert out["title"] == "Site Page"
    assert out["page"]["text"] == "正文内容"
    assert "load" in page.load_states  # 重取前等过导航稳定


def test_snapshot_other_error_raises():
    """非 destroyed 错误（如选择器超时）不重试，照常上抛。"""
    page = FakePage(eval_seq=[PWError("Timeout 30000ms exceeded")])
    with pytest.raises(PWError):
        asyncio.run(actions._snapshot(page, "open", "https://site.example/page", 200))


def test_snapshot_second_destroyed_raises():
    """重取后仍 destroyed（连续导航异常）→ 上抛给外层报错。"""
    page = FakePage(eval_seq=[_destroyed(), _destroyed()])
    with pytest.raises(PWError):
        asyncio.run(actions._snapshot(page, "open", "https://site.example/page", 200))


# ── 2. settle：networkidle + 拟人停顿 ──

def test_settle_waits_networkidle_and_pauses():
    page = FakePage()
    asyncio.run(actions._settle(page))
    assert page.load_states == ["load", "networkidle"]
    # timeouts[0] = SETTLE_QUIET_MS 静默窗；timeouts[1] = 拟人随机停顿
    assert page.timeouts[0] == actions.SETTLE_QUIET_MS
    lo, hi = actions.HUMAN_PAUSE_MS
    assert lo <= page.timeouts[1] <= hi


# ── 3. 拟真点击：轨迹移动 + 原地按下 ──

def test_click_human_moves_with_steps_then_clicks():
    box = {"x": 100, "y": 200, "width": 80, "height": 30}
    loc = FakeLocator(box=box)
    page = FakePage()
    asyncio.run(actions._click_human(page, loc, 5000))
    assert len(page.mouse.moves) == 1
    x, y, steps = page.mouse.moves[0]
    lo, hi = actions.MOUSE_STEPS
    assert lo <= steps <= hi
    assert box["x"] <= x <= box["x"] + box["width"]
    assert box["y"] <= y <= box["y"] + box["height"]
    assert len(loc.click_calls) == 1  # actionability 完整保留的原地 click


def test_click_human_falls_back_without_box():
    """隐藏元素 bounding_box 为 None → 降级 locator.click（actionability 报错语义）。"""
    loc = FakeLocator(box=None)
    page = FakePage()
    asyncio.run(actions._click_human(page, loc, 5000))
    assert page.mouse.moves == []
    assert len(loc.click_calls) == 1


# ── 4. 拟真输入：清空 + 逐键 + 随机间隔 ──

def test_type_human_clears_then_types_with_delay():
    loc = FakeLocator(box=None)
    page = FakePage()
    asyncio.run(actions._type_human(loc, "上海旦迪", 5000, submit=False))
    assert loc.fills and loc.fills[0][0] == ""  # 先清空
    assert len(loc.seq_calls) == 1
    text, delay, _ = loc.seq_calls[0]
    assert text == "上海旦迪"
    lo, hi = actions.TYPE_KEY_DELAY_MS
    assert lo <= delay <= hi
    assert loc.press_calls == []


def test_type_human_submit_presses_enter():
    loc = FakeLocator(box=None)
    asyncio.run(actions._type_human(loc, "query", 5000, submit=True))
    assert loc.press_calls == ["Enter"]


# ── 5. type 隐藏副本自动切换（2026-10-14 天眼查实证）──
# 页面常有多个同名表单（桌面/移动端互斥显隐），.first 可能命中
# visibility:hidden 副本，actionability 永不过（fill 超时 20s）——
# first 不可见时改选 filter(visible=True) 的第一个可见副本。

def _run_type(page):
    return asyncio.run(actions._execute(
        page, "type", {"selector": "div > div > input", "text": "上海旦迪"}, ""))


def test_type_switches_to_visible_counterpart():
    """first 不可见且存在可见副本 → 切换副本输入，首个 locator 未被打扰。"""
    page = FakePage(locator_spec={"visible": False, "count": 1})
    out = _run_type(page)
    assert "error" not in out
    assert page.locator_sels == ["div > div > input"] * 2  # 探测 + 副本重定位
    first_loc, vis_loc = page.locator_log
    assert vis_loc.filtered_with == [True]                 # 可见过滤器
    assert first_loc.fills == [] and first_loc.seq_calls == []
    assert vis_loc.fills and vis_loc.fills[0][0] == ""     # 副本上先清空
    assert len(vis_loc.seq_calls) == 1                     # 副本上逐键输入


def test_type_keeps_first_when_no_visible_counterpart():
    """first 不可见且无可见副本 → 保留 first（由 actionability 报精确错误）。"""
    page = FakePage(locator_spec={"visible": False, "count": 0})
    _run_type(page)
    first_loc = page.locator_log[0]
    assert first_loc.filtered_with == []   # count=0 未采纳
    assert first_loc.fills                 # 输入仍发生在 first 上


def test_type_skips_probe_when_first_visible():
    """first 本就可见 → 不发第二次 locator 查询（零额外开销）。"""
    page = FakePage(locator_spec={"visible": True, "count": 1})
    _run_type(page)
    assert page.locator_sels == ["div > div > input"]


# ── 6. scroll --to bottom：懒加载/无限流收敛滚动（2026-10-14 用户提出）──
# 瀑布流页面滚到底会触发加载更多，没有真正的底——收敛循环：滚到底 →
# settle → 高度仍在增长则继续；连续 SCROLL_STABLE_ROUNDS 轮无增长 =
# 加载器无更多内容。上限/预算双保险防失控。

def _run_scroll(page, opts):
    return asyncio.run(actions._execute(page, "scroll", opts, ""))


def test_scroll_to_bottom_converges_when_height_stabilizes():
    """高度涨→涨→稳→稳 → 第 4 轮收敛退出，返回轮数与收敛标记 + 终态快照。"""
    page = FakePage(scroll_infos=[
        {"y": 800, "h": 2000, "vh": 800},
        {"y": 2000, "h": 4000, "vh": 800},
        {"y": 3200, "h": 4000, "vh": 800},   # 高度未涨 stable=1
        {"y": 3200, "h": 4000, "vh": 800},   # stable=2 → 收敛退出
    ])
    out = _run_scroll(page, {"to": "bottom"})
    assert "error" not in out
    assert out["scroll_rounds"] == 4
    assert out["converged"] is True
    assert out["action"] == "scroll"                      # 终态仍是快照
    assert sum(1 for js in page.eval_calls if "scrollTo" in js) == 4  # 每轮一次 scrollTo


def test_scroll_to_bottom_caps_at_max_rounds():
    """高度一直涨（无限流无底）→ 轮数上限截断，converged=False 提示 LLM。"""
    page = FakePage(scroll_infos=[
        {"y": 800 * (i + 1), "h": (i + 2) * 1000, "vh": 800}
        for i in range(actions.SCROLL_MAX_ROUNDS)
    ])
    out = _run_scroll(page, {"to": "bottom"})
    assert out["scroll_rounds"] == actions.SCROLL_MAX_ROUNDS
    assert out["converged"] is False


def test_scroll_to_top_jumps_directly():
    page = FakePage()
    out = _run_scroll(page, {"to": "top"})
    assert "error" not in out
    assert any("scrollTo(0, 0)" in js for js in page.eval_calls)


def test_scroll_screenwise_semantics_unchanged():
    """逐屏 scroll（无 --to）保持原语义：scrollBy + settle + 快照，无收敛字段。"""
    page = FakePage()
    out = _run_scroll(page, {"direction": "down", "amount": "600"})
    assert "error" not in out
    assert any("scrollBy" in js for js in page.eval_calls)
    assert "scroll_rounds" not in out and "converged" not in out
