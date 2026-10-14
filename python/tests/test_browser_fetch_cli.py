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
