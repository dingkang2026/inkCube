"""python/tests/test_doc_parser_office.py — MS Office COM 转换回归测试。

覆盖（2026-10-07 修复）：
  - DispatchEx 独立实例：批量转换不复用用户开着的 Office、结束时才 Quit 自己的实例；
  - 转换失败（Open/SaveAs 抛不可重试 com_error）→ 降级返回，且 finally 仍执行
    doc.Close + app.Quit（不泄漏 WINWORD/POWERPNT 进程）；
  - 瞬态 RPC_E_CALL_REJECTED → 短退避重试后成功；
  - 不支持的扩展名直接降级，不启动 Office。
"""
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pywintypes
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from knowledge import doc_parser
from knowledge.doc_parser import DocumentParser

RPC_E_CALL_REJECTED = -2147418111
RPC_E_SERVERCALL_RETRYLATER = -2147417846
DISP_E_EXCEPTION = -2147352567  # 不可重试的普通 COM 失败


def _com_error(hresult):
    return pywintypes.com_error(hresult, "mocked", None, None)


class _FakeContainer:
    """Documents / Presentations 容器：Open 按预设失败列表逐个抛出。"""

    def __init__(self, app):
        self._app = app

    def Open(self, path, **kwargs):
        self._app.calls.append(("Open", path, kwargs))
        if self._app.open_failures:
            raise self._app.open_failures.pop(0)
        doc = _FakeDoc(self._app)
        self._app.opened_docs.append(doc)
        return doc


class _FakeDoc:
    def __init__(self, app):
        self._app = app

    def SaveAs(self, path, FileFormat=None):
        self._app.calls.append(("SaveAs", path, FileFormat))
        if self._app.saveas_failures:
            raise self._app.saveas_failures.pop(0)
        Path(path).write_bytes(b"%PDF-fake")

    def Close(self, **kwargs):
        self._app.calls.append(("Close", kwargs))


class _FakeApp:
    def __init__(self, prog_id):
        self.prog_id = prog_id
        self.calls = []
        self.open_failures = []
        self.saveas_failures = []
        self.opened_docs = []
        self.quit_called = False
        self.Documents = _FakeContainer(self)
        self.Presentations = _FakeContainer(self)

    def Quit(self):
        self.quit_called = True
        self.calls.append(("Quit",))


@pytest.fixture

def office_env(monkeypatch):
    """把 win32com.client 替换为 fake：DispatchEx 每次返回新 _FakeApp。

    真环境装了 pywin32：import win32com.client 会校验父包属性，
    因此父包 win32com 也要一起伪装。"""
    apps = []

    def _dispatch_ex(prog_id):
        app = _FakeApp(prog_id)
        apps.append(app)
        return app

    fake_module = types.ModuleType("win32com.client")
    fake_module.DispatchEx = _dispatch_ex
    fake_module.Dispatch = MagicMock(
        side_effect=AssertionError("must use DispatchEx (dedicated instance)")
    )
    fake_pkg = types.ModuleType("win32com")
    fake_pkg.client = fake_module
    fake_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "win32com", fake_pkg)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_module)
    return apps


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """重试退避不真等待。"""
    sleeps = []
    monkeypatch.setattr(doc_parser.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _parser() -> DocumentParser:
    # 跳过 __init__（避免加载 Docling 模型），仅测 COM 转换路径
    return DocumentParser.__new__(DocumentParser)


class TestDocxConversion:
    def test_success_uses_dedicated_instance(self, office_env, tmp_path):
        src = tmp_path / "a.docx"
        src.write_bytes(b"fake docx")
        out = tmp_path / "a.pdf"

        pdf, warning = _parser()._convert_with_ms_office(src, out)

        assert pdf == out and out.exists()
        assert warning == ""
        app = office_env[0]
        assert app.prog_id == "Word.Application"
        # 独立实例 + 用完即退（Quit 只发生在自己的实例上）
        assert ("Quit",) in app.calls
        assert app.calls[-1] == ("Quit",)
        # Word 弹窗抑制 + SaveAs 格式 17 (wdFormatPDF)
        assert app.DisplayAlerts == 0
        assert app.Visible is False
        saveas = next(c for c in app.calls if c[0] == "SaveAs")
        assert saveas[2] == 17
        # Open 携带 AddToRecentFiles=False（不污染用户最近文件列表）
        open_call = next(c for c in app.calls if c[0] == "Open")
        assert open_call[2].get("AddToRecentFiles") is False

    def test_failure_still_closes_doc_and_quits_app(self, office_env, tmp_path):
        """SaveAs 失败 → 降级返回，finally 仍 Close 文档 + Quit 应用（不泄漏进程）。"""
        src = tmp_path / "a.docx"
        src.write_bytes(b"fake docx")
        out = tmp_path / "a.pdf"

        # DispatchEx 创建 app 时注入一次不可重试的 SaveAs 失败
        original = sys.modules["win32com.client"].DispatchEx

        def _dispatch_ex(prog_id):
            app = original(prog_id)
            app.saveas_failures.append(_com_error(DISP_E_EXCEPTION))
            return app

        sys.modules["win32com.client"].DispatchEx = _dispatch_ex

        pdf, warning = _parser()._convert_with_ms_office(src, out)

        assert pdf is None
        assert "转换失败" in warning and "内置解析器" in warning
        app = office_env[0]
        assert ("Close", {"SaveChanges": 0}) in app.calls
        assert ("Quit",) in app.calls
        assert not out.exists()

    def test_transient_rejection_retried_then_succeeds(self, office_env, tmp_path, _no_sleep):
        """RPC_E_CALL_REJECTED 属瞬态：重试后成功而非整个转换失败。"""
        src = tmp_path / "a.docx"
        src.write_bytes(b"fake docx")
        out = tmp_path / "a.pdf"

        original = sys.modules["win32com.client"].DispatchEx

        def _dispatch_ex(prog_id):
            app = original(prog_id)
            app.open_failures = [
                _com_error(RPC_E_CALL_REJECTED),
                _com_error(RPC_E_SERVERCALL_RETRYLATER),
            ]
            return app

        sys.modules["win32com.client"].DispatchEx = _dispatch_ex

        pdf, warning = _parser()._convert_with_ms_office(src, out)

        assert pdf == out and warning == ""
        assert len(_no_sleep) == 2  # 两次失败各退避一次

    def test_retries_exhausted_degrades_and_cleans_up(self, office_env, tmp_path, _no_sleep):
        """重试额度耗尽 → 降级 + 清理。"""
        src = tmp_path / "a.docx"
        src.write_bytes(b"fake docx")
        out = tmp_path / "a.pdf"

        original = sys.modules["win32com.client"].DispatchEx

        def _dispatch_ex(prog_id):
            app = original(prog_id)
            app.open_failures = [_com_error(RPC_E_CALL_REJECTED)] * 10
            return app

        sys.modules["win32com.client"].DispatchEx = _dispatch_ex

        pdf, warning = _parser()._convert_with_ms_office(src, out)

        assert pdf is None and "转换失败" in warning
        app = office_env[0]
        assert ("Quit",) in app.calls  # doc 未打开也要 Quit app
        assert len(_no_sleep) == len(doc_parser._COM_RETRY_DELAYS)


class TestPptxConversion:
    def test_pptx_branch(self, office_env, tmp_path):
        src = tmp_path / "a.pptx"
        src.write_bytes(b"fake pptx")
        out = tmp_path / "a.pdf"

        pdf, warning = _parser()._convert_with_ms_office(src, out)

        assert pdf == out
        app = office_env[0]
        assert app.prog_id == "PowerPoint.Application"
        # PowerPoint 弹窗级别=1（ppAlertsNone），且不设置 Visible（不允许隐藏）
        assert app.DisplayAlerts == 1
        assert not hasattr(app, "Visible")
        open_call = next(c for c in app.calls if c[0] == "Open")
        assert open_call[2].get("WithWindow") is False
        saveas = next(c for c in app.calls if c[0] == "SaveAs")
        assert saveas[2] == 32  # ppSaveAsPDF


class TestUnsupportedExt:
    def test_no_office_started(self, office_env, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("hello")
        pdf, warning = _parser()._convert_with_ms_office(src, tmp_path / "a.pdf")
        assert pdf is None
        assert "暂不支持" in warning
        assert office_env == []  # 从未启动 Office
