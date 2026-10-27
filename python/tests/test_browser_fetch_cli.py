"""test_browser_fetch_cli.py — V2 action CLI 契约（2.8 §5.2）。

纯解析/预检层测试（不触网、不连 CDP）；CDP 交互由 Task 8 手动验收覆盖。

加载注意：main.py 必须以别名模块名加载（spec_from_file_location），
绝不能 import_module("main")——会污染 sys.modules["main"]，令同轮次
字母序靠后的 test_security_p0 的 `import main`（FastAPI 后端）拿到
CLI main 而确定性失败（2026-10-13 review 实证）。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

# main.py 模块级 import _actions；playwright 缺失时 emit+sys.exit(0)
# 会打断 pytest 收集——缺失环境整文件 skip（与 V1 防线同语义）
try:
    import playwright  # noqa: F401
except ImportError:
    pytest.skip("playwright 不可用：CLI 无法加载", allow_module_level=True)

SCRIPTS = Path(__file__).resolve().parents[2] / "resources" / "skills" / "browser-fetch" / "scripts"
sys.path.insert(0, str(SCRIPTS))   # main.py 顶部 import _fetch_core/_actions 需要
_spec = importlib.util.spec_from_file_location("browser_fetch_main", str(SCRIPTS / "main.py"))
main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main)     # 别名加载：sys.modules["main"] 不被污染


@pytest.fixture
def mock_exec(monkeypatch):
    """预检通过的用例打桩执行层（run_action），避免触网。"""
    seen: dict = {}

    async def fake_run(action, opts):
        seen.update({"action": action, "opts": opts})
        return {"action": action, "ok": True}

    monkeypatch.setattr(main, "run_action", fake_run)
    return seen


def run(argv):
    """跑 main.dispatch_argv，捕获 emit，返回 (exit_code, 最后一个 JSON 对象)。"""
    calls: list = []
    orig_emit = main.emit
    main.emit = lambda obj: calls.append(obj)
    try:
        code = main.dispatch_argv(argv)
    finally:
        main.emit = orig_emit
    return code, (calls[-1] if calls else {})


class TestV2Contract:
    def test_seven_actions_defined(self):
        assert set(main.ACTIONS) == {"open", "click", "type", "select", "scroll", "extract", "close"}

    def test_no_args_returns_v2_usage(self):
        _code, obj = run([])
        assert obj["error"] and "usage" in obj and "open" in obj["usage"]

    def test_old_single_command_syntax_teaches_v2(self):
        _code, obj = run(["https://example.com"])
        assert "action" in obj["error"] or "V2" in obj["error"]
        assert "example" in obj

    def test_unknown_action(self):
        _code, obj = run(["navigate", "https://example.com"])
        assert "usage" in obj

    def test_open_missing_url(self):
        _code, obj = run(["open"])
        assert "URL" in obj["error"]

    def test_open_bad_scheme_precheck(self):
        # 形似性预检拒绝非 http(s)（SSRF 全量校验仍在执行层）
        _code, obj = run(["open", "ftp://example.com"])
        assert obj["error"]

    def test_click_requires_text_or_selector(self):
        _code, obj = run(["click"])
        assert "--text" in obj["usage"] and "error" in obj

    def test_type_requires_selector_and_text_source(self):
        _code, obj = run(["type", "--selector", "#q"])
        assert obj["error"]            # 缺文本来源
        _code, obj = run(["type", "--text", "hi"])
        assert obj["error"]            # 缺 --selector

    def test_type_text_and_text_file_mutually_exclusive(self):
        _code, obj = run(["type", "--selector", "#q", "--text", "a", "--text-file", "b.txt"])
        assert obj["error"]

    def test_select_requires_value(self):
        _code, obj = run(["select", "--selector", "#s"])
        assert obj["error"]

    def test_scroll_direction_validated(self, mock_exec):
        _code, obj = run(["scroll", "--direction", "left"])
        assert obj["error"]
        _code, obj = run(["scroll"])
        assert obj.get("error") is None and mock_exec["action"] == "scroll"
        assert mock_exec["opts"]["direction"] == "down"   # 默认值写入契约层

    def test_scroll_to_parsed(self, mock_exec):
        _code, obj = run(["scroll", "--to", "bottom"])
        assert obj.get("error") is None and mock_exec["opts"]["to"] == "bottom"
        _code, obj = run(["scroll", "--to", "top"])
        assert mock_exec["opts"]["to"] == "top"

    def test_scroll_to_value_validated(self):
        _code, obj = run(["scroll", "--to", "middle"])
        assert obj["error"] and "--to" in obj["error"]

    def test_scroll_to_excludes_direction_amount(self):
        # --to 是直达/收敛语义，与逐屏滚动参数互斥（防 LLM 拼接出歧义命令）
        _code, obj = run(["scroll", "--to", "bottom", "--direction", "down"])
        assert obj["error"]
        _code, obj = run(["scroll", "--to", "bottom", "--amount", "600"])
        assert obj["error"]

    def test_extract_optional_selector(self, mock_exec):
        _code, obj = run(["extract", "--save-to", "out.md"])
        assert obj.get("error") is None and mock_exec["action"] == "extract"

    def test_close_no_args(self, mock_exec):
        _code, obj = run(["close"])
        assert obj.get("error") is None and mock_exec["action"] == "close"

    def test_open_unrecognized_flag_rejected(self):
        # open 不支持 --selector（V1 残留参数显式拒绝，教 V2 契约）
        _code, obj = run(["open", "https://example.com", "--selector", "#a"])
        assert obj["error"]

    def test_open_opts_normalized(self, mock_exec):
        _code, obj = run(["open", "https://example.com", "--wait-for", ".a", "--timeout", "30"])
        assert mock_exec["opts"] == {"url": "https://example.com", "wait_for": ".a", "timeout": "30"}

    def test_type_submit_flag(self, mock_exec):
        _code, obj = run(["type", "--selector", "#q", "--text", "x", "--submit"])
        assert mock_exec["opts"]["submit"] is True


class TestReadTypeText:
    """--text-file 工作区包含性校验（2026-10-13 review：绝对路径/.. 穿越拒）。

    纯文件系统逻辑，零网络依赖；真实回归由 Task 8 手动验收覆盖。
    """

    @staticmethod
    def _read(opts: dict):
        import _actions
        import asyncio
        return asyncio.run(_actions._read_type_text(opts))

    def test_text_channel_takes_priority(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert self._read({"text": "inline", "text_file": "a.txt"}) == "inline"

    def test_relative_path_within_workspace(self, tmp_path, monkeypatch):
        (tmp_path / "note.txt").write_text("内容", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert self._read({"text_file": "note.txt"}) == "内容"

    def test_dotdot_escape_rejected(self, tmp_path, monkeypatch):
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("x", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="工作区内"):
            self._read({"text_file": "../secret.txt"})

    def test_absolute_path_outside_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="工作区内"):
            self._read({"text_file": str(tmp_path.parent / "win.ini")})

    def test_missing_file_reports_relative_hint(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match="相对任务工作区"):
            self._read({"text_file": "nope.txt"})


class TestSnapshotBody:
    """_snapshot 组装契约（2026-10-14 final review Finding 2）：remaining 透传。

    snapshot_js 计算 remaining/total（SNAPSHOT_ELEMENTS_LIMIT 截断）,
    SKILL.md/设计文档承诺 remaining>0 时 LLM 应先 scroll 再快照——
    组装层丢弃该字段会让截断信号永不存在，LLM 拿截断文本当全文用。
    FakePage 只实现 _snapshot 消费的三个面（evaluate/title/url 属性），
    登录墙检测走真实 detect_login_wall（本组输入不触发任何信号）。
    """

    _SNAP = {
        "text": "page body text",
        "truncated": False,
        "elements": [{"role": "link", "name": "A", "selector": "#a"}],
        "remaining": 0,
        "total": 1,
        "hasPassword": False,
    }

    @staticmethod
    def _page(snap: dict, url: str = "https://gitlab.corp.cn/a"):
        class FakePage:
            # url 存实例属性：类体作用域不参与闭包，`url = url` 会 NameError
            def __init__(self):
                self.url = url

            async def evaluate(self, _js):
                return snap

            async def title(self):
                return "Dashboard"

        return FakePage()

    @classmethod
    def _snapshot(cls, snap: dict) -> dict:
        import asyncio
        import _actions
        return asyncio.run(_actions._snapshot(
            cls._page(snap), "open", "https://gitlab.corp.cn/a", 200))

    def test_remaining_forwarded_when_truncated(self):
        snap = dict(self._SNAP, truncated=True, remaining=37)
        body = self._snapshot(snap)
        assert body["remaining"] == 37
        assert body["page"]["truncated"] is True

    def test_remaining_defaults_to_zero(self):
        """上游快照脚本未返回 remaining（旧版本）不炸组装层。"""
        snap = {k: v for k, v in self._SNAP.items() if k != "remaining"}
        assert self._snapshot(snap)["remaining"] == 0

    def test_body_shape_contract(self):
        body = self._snapshot(dict(self._SNAP))
        assert body["action"] == "open"
        assert body["url"] == "https://gitlab.corp.cn/a"
        assert body["title"] == "Dashboard"
        assert body["page"]["text"] == "page body text"
        assert body["elements"] == self._SNAP["elements"]
        assert body["needs_login"] is False


class TestOpenReadiness:
    """open 就绪语义（2026-10-14 新浪/天眼查两任务实证）：wait_until="load"
    对重广告资源站拖满超时（ent.sina.com.cn 首次必 20s 超时、重试必成）。
    修复：goto 改 domcontentloaded + goto 后接 _settle（3s load 容错窗 +
    500ms 静默窗）——load 在容错窗内来则等到（保留就绪语义），不来则
    照常快照，不再整体超时。FakePage 记录 goto/wait_for_load_state 调用。"""

    _SNAP = {
        "text": "ent page body",
        "truncated": False,
        "elements": [],
        "remaining": 0,
        "total": 0,
        "hasPassword": False,
    }

    @staticmethod
    def _page(calls: dict):
        class FakePage:
            url = "https://ent.sina.com.cn/"

            async def goto(self, target_url, wait_until=None, timeout=None):
                calls["goto"].append(
                    {"url": target_url, "wait_until": wait_until})
                return type("R", (), {"status": 200})()

            async def wait_for_load_state(self, state=None, timeout=None):
                calls["wait_for_load_state"].append(
                    {"state": state, "timeout": timeout})

            async def wait_for_timeout(self, _ms):
                pass

            async def evaluate(self, _js):
                return dict(TestOpenReadiness._SNAP)

            async def title(self):
                return "新浪娱乐首页"

        return FakePage()

    @staticmethod
    def _open(calls: dict) -> dict:
        import asyncio
        import _actions
        page = TestOpenReadiness._page(calls)
        return asyncio.run(_actions._execute(
            page, "open", {"url": "https://ent.sina.com.cn/"},
            "https://ent.sina.com.cn/"))

    def test_goto_uses_domcontentloaded(self):
        calls: dict = {"goto": [], "wait_for_load_state": []}
        body = self._open(calls)
        assert body.get("error") is None
        assert calls["goto"][0]["wait_until"] == "domcontentloaded"

    def test_settle_follows_goto(self):
        """goto 后接 _settle：等 load（容错窗）——不触发即 500ms 静默窗兜底。"""
        calls: dict = {"goto": [], "wait_for_load_state": []}
        self._open(calls)
        states = [c["state"] for c in calls["wait_for_load_state"]]
        assert "load" in states


class TestCliArgMerge:
    """未加引号的含空格值容错合并（2026-10-16 天眼查任务实证）。

    LLM 照抄快照 selector/name 时常漏引号：`--selector div > div > input`、
    `--text 知识产权 78` 分词后 `>`/`78` 成了「无法识别的位置参数」，报错
    一轮再自纠。本工具除 open 的 url 外无位置参数，未识别 token 并入上一
    flag 值是唯一合理解释——解析层直接合并，不再消耗一轮报错自纠。
    """

    def test_selector_with_spaces_merged(self, mock_exec):
        # shlex 分词后的 `div > div > input` 五个 token 全部归属 --selector
        _code, obj = run(["type", "--selector", "div", ">", "div", ">", "input",
                          "--text", "x"])
        assert obj.get("error") is None
        assert mock_exec["opts"]["selector"] == "div > div > input"
        assert mock_exec["opts"]["text"] == "x"

    def test_text_with_trailing_number_merged(self, mock_exec):
        # 快照 name='知识产权 78' 含空格，LLM 照抄未加引号（天眼查 [69] 实证）
        _code, obj = run(["click", "--text", "知识产权", "78"])
        assert obj.get("error") is None
        assert mock_exec["opts"]["text"] == "知识产权 78"

    def test_flag_still_parsed_after_merge(self, mock_exec):
        # 合并不影响后续 --flag 解析（merge 只吞非 -- token）
        _code, obj = run(["click", "--text", "知识产权", "78", "--timeout", "15"])
        assert obj.get("error") is None
        assert mock_exec["opts"]["text"] == "知识产权 78"
        assert mock_exec["opts"]["timeout"] == "15"

    def test_open_extra_positional_still_rejected(self):
        # open 的 url 已消费且无前置 flag：多余位置参数无归属，仍显式报错
        _code, obj = run(["open", "https://example.com", "extra"])
        assert obj["error"] and "无法识别的位置参数" in obj["error"]

    def test_positional_before_any_flag_rejected(self):
        # 无 flag 可归属（click 旦迪通信）→ 报错教学 flag 用法
        _code, obj = run(["click", "旦迪通信"])
        assert obj["error"] and "--text" in obj["error"]


class TestOpenWaitForTimeout:
    """--wait-for 超时附快照（2026-10-16 天眼查任务实证）。

    裸错误让 LLM 无页面实况可看，绕道 web_search 烧两轮。等待的元素未出现
    ≠ 页面不可用——返回当前快照 + warning，LLM 基于实况决策；命中登录墙
    时仍按登录墙错误体返回（登录引导优先于快照）。
    """

    _SNAP = {
        "text": "天眼查首页正文都在用的商业查询工具" * 10,
        "truncated": False,
        "elements": [],
        "remaining": 0,
        "total": 0,
        "hasPassword": False,
    }

    @staticmethod
    def _page(calls: dict, snap: dict | None = None):
        body = dict(TestOpenWaitForTimeout._SNAP if snap is None else snap)

        class FakePage:
            url = "https://www.tianyancha.com/"

            async def goto(self, target_url, wait_until=None, timeout=None):
                calls["goto"] = target_url
                return type("R", (), {"status": 200})()

            async def wait_for_load_state(self, state=None, timeout=None):
                pass

            async def wait_for_timeout(self, _ms):
                pass

            async def wait_for_selector(self, sel, timeout=None):
                calls["wait_for"] = sel
                # 真实 playwright 超时抛 TimeoutError（PlaywrightError 子类）——
                # 桩必须同型，否则 _execute 的 except PlaywrightError 不命中
                from playwright.async_api import TimeoutError as PWTimeout
                raise PWTimeout(f"Timeout exceeded waiting for selector '{sel}'")

            async def evaluate(self, _js):
                return body

            async def title(self):
                return "天眼查-商业查询平台"

        return FakePage()

    @staticmethod
    def _open(calls: dict, snap: dict | None = None) -> dict:
        import asyncio
        import _actions
        page = TestOpenWaitForTimeout._page(calls, snap)
        return asyncio.run(_actions._execute(
            page, "open",
            {"url": "https://www.tianyancha.com/", "wait_for": "input"},
            "https://www.tianyancha.com/"))

    def test_timeout_returns_snapshot_with_warning(self):
        calls: dict = {}
        body = self._open(calls)
        assert body.get("error") is None
        assert calls["wait_for"] == "input"
        assert "等待选择器超时" in body["warning"] and "input" in body["warning"]
        assert body["page"]["text"] == self._SNAP["text"]  # 实况快照已附上
        assert body["action"] == "open"

    def test_login_wall_error_not_masked_by_snapshot(self):
        # 登录墙（hasPassword + 稀疏文本）：返回登录引导而非带 warning 的快照
        calls: dict = {}
        wall_snap = dict(self._SNAP, text="登录", hasPassword=True)
        body = self._open(calls, wall_snap)
        assert body.get("needs_login") is True
        assert "warning" not in body


class TestExtractSoftWall:
    """extract 软墙返回体（§5.4 第 5 信号，2026-10-26 天眼查任务实证）。

    页面公开内容完整、仅敏感字段被登录遮蔽（「181****登录查看」），硬墙
    四信号全部不命中——LLM 拿到「数据看似完整」的返回体后不引导登录。
    修复：extract 返回体附 needs_login+login_gated+domain（数据照常落盘）：
    needs_login+domain 复用前端登录横幅（§8.3），login_gated 供 SKILL.md
    指引 LLM 提示登录后重采。FakePage 只实现 extract 消费的
    evaluate(js, sel)/title/url 面；INKCUBE_DATA_DIR 锚 tmp_path，
    落盘断言不污染真实 data 目录。
    """

    _GATED_TEXT = (
        "旦迪通信科技有限公司\n联系电话：181****登录查看\n"
        "邮箱：登录后查看更多信息\n股东信息：登录查看\n"
        "More: Sign In to View\n年报：公开可见的经营数据")

    @staticmethod
    def _page(text: str):
        class FakePage:
            url = "https://www.tianyancha.com/company/xxx"

            async def evaluate(self, _js, _sel=None):
                return {"html": "<p>body</p>", "text": text,
                        "hasPassword": False}

            async def title(self):
                return "旦迪通信 - 天眼查"

        return FakePage()

    @staticmethod
    def _extract(text: str, tmp_path, monkeypatch) -> dict:
        import asyncio
        import _actions
        monkeypatch.setenv("INKCUBE_DATA_DIR", str(tmp_path))
        return asyncio.run(_actions._execute(
            TestExtractSoftWall._page(text), "extract", {}, ""))

    def test_gated_body_flags(self, tmp_path, monkeypatch):
        body = self._extract(self._GATED_TEXT, tmp_path, monkeypatch)
        assert body.get("error") is None        # 软墙不是错误：数据照常
        assert body["needs_login"] is True
        assert body["login_gated"] is True
        assert body["domain"] == "www.tianyancha.com"
        assert body["length"] > 0 and body["path"]
        # 产物照常落盘且锚 data_dir/fetched（B7 锚定语义随测验证）
        out = Path(body["path"])
        assert out.exists() and out.parent == tmp_path / "fetched"

    def test_clean_body_no_gating(self, tmp_path, monkeypatch):
        body = self._extract("公开年报数据完整，无遮蔽字段。", tmp_path, monkeypatch)
        assert body.get("error") is None
        assert body["needs_login"] is False
        assert "login_gated" not in body
        assert "domain" not in body
