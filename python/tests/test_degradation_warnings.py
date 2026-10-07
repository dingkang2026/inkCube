"""python/tests/test_degradation_warnings.py — 降级显式警告单测（0.4 §7.3 v3.6）。

覆盖：
  - retrieval._tavily_failure_warning()：401/403/402/429/连接类/服务异常 → 自带
    「设置 > 外部模型」等修正指引的显式文案；
  - retrieval._search_internet_channel()：多 query 失败警告去重、缺 Key 显式警告；
  - doc_parser Office→PDF 转换降级：无转换器 / LibreOffice 失败 → (None, 降级说明)；
  - activity_events.complete_file()：降级说明随 completed 通知 detail 显式呈现。
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import report_generator.retrieval as retrieval
from report_generator.retrieval import _search_internet_channel, _tavily_failure_warning
from knowledge import doc_parser
from knowledge.doc_parser import DocumentParser
import activity_events


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.tavily.com/search")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class TestTavilyFailureWarning:
    # (status, 关键词, 是否要求设置页指引)：429/用量类修正动作在服务商控制台，
    # 不强制设置页指引；Key 类必须指向 设置 > 外部模型
    @pytest.mark.parametrize("status,keyword,needs_settings", [
        (401, "Key 无效", True),
        (403, "Key 无效", True),
        (402, "额度", True),
        (429, "超限", False),
    ])
    def test_config_statuses(self, status, keyword, needs_settings):
        warning = _tavily_failure_warning(_status_error(status))
        assert keyword in warning
        if needs_settings:
            assert "设置 > 外部模型" in warning
        assert "仅使用项目内材料" in warning

    def test_connection_error(self):
        warning = _tavily_failure_warning(httpx.ConnectError("conn refused"))
        assert "连接" in warning and "网络" in warning

    def test_server_error_still_warns(self):
        warning = _tavily_failure_warning(_status_error(500))
        assert "暂时异常" in warning


class TestInternetChannelWarnings:
    def test_dedup_across_queries(self, monkeypatch):
        """多 query 同样 401 → 警告去重为一条且带设置页指引。"""
        async def _fail(*args, **kwargs):
            raise _status_error(401)

        monkeypatch.setattr(retrieval, "_tavily_search", _fail)
        results, usage, warnings = asyncio.run(
            _search_internet_channel(["q1", "q2"], "key", 5)
        )
        assert results == []
        assert len(warnings) == 1
        assert "设置 > 外部模型" in warnings[0]

    def test_missing_key_warns_explicitly(self):
        results, usage, warnings = asyncio.run(
            _search_internet_channel(["q"], "", 5)
        )
        assert results == []
        assert len(warnings) == 1
        assert "未配置" in warnings[0]
        assert "设置" in warnings[0]


class TestOfficeConversionDegradation:
    def _parser(self) -> DocumentParser:
        # 跳过 __init__（避免加载 Docling 模型），仅测转换降级路径
        return DocumentParser.__new__(DocumentParser)

    def test_unavailable_returns_warning(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "knowledge.model_manager.ModelManager.check_office_converter",
            staticmethod(lambda: {"available": False, "tool": None, "path": None}),
        )
        pdf, warning = self._parser()._convert_office_to_pdf(tmp_path / "a.docx")
        assert pdf is None
        assert "Office" in warning and "LibreOffice" in warning
        assert "内置解析器" in warning

    def test_libreoffice_failure_degrades(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "knowledge.model_manager.ModelManager.check_office_converter",
            staticmethod(lambda: {"available": True, "tool": "libreoffice", "path": "soffice"}),
        )
        fake_result = MagicMock(returncode=1, stderr=b"Error: could not be loaded")
        monkeypatch.setattr(doc_parser.subprocess, "run", lambda *a, **kw: fake_result)
        pdf, warning = self._parser()._convert_with_libreoffice(
            tmp_path / "a.docx", tmp_path / "a.pdf", "soffice",
        )
        assert pdf is None
        assert "内置解析器" in warning


class TestEmptyParseWarning:
    """空文件/无文本内容 → 0 chunks 是成功结果，但需显式声明（0.4 §7.3 v3.6）。"""

    def _parser(self, tmp_path) -> DocumentParser:
        parser = DocumentParser.__new__(DocumentParser)
        parser.models_dir = tmp_path
        parser.converter = MagicMock()
        parser._chunker = MagicMock()
        parser._chunker.chunk.return_value = iter([])  # 空文档 → 0 chunks
        parser._tokenizer = None
        return parser

    def test_empty_parse_warns(self, tmp_path):
        from docling_core.types.doc import DoclingDocument

        parser = self._parser(tmp_path)
        parser.converter.convert.return_value = MagicMock(
            document=DoclingDocument(name="empty"),
        )
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        result = parser.parse_file(str(f), "file_x")
        assert result.text_chunks == []
        warnings = result.metadata.get("warnings") or []
        assert any("文本内容" in w for w in warnings)


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return self._row


_UNSET = object()  # 区分「未传 row」与「传 row=None（文件已删除）」


class _FakeDb:
    def __init__(self, row=_UNSET):
        self.conn = _FakeConn({
            "project_id": "p1", "filename": "a.docx", "container": "project",
        } if row is _UNSET else row)
        self.calls: list[dict] = []

    def add_notification(self, **kwargs):
        self.calls.append(kwargs)


class TestCompleteFileDetail:
    def test_degradation_detail_carried(self):
        db = _FakeDb()
        activity_events.complete_file(db, "f1", "未检测到 Microsoft Office 或 LibreOffice")
        assert db.calls[0]["kind"] == "completed"
        assert "Microsoft Office" in db.calls[0]["detail"]

    def test_no_detail_keeps_none(self):
        db = _FakeDb()
        activity_events.complete_file(db, "f1", None)
        assert db.calls[0]["detail"] is None

    def test_gone_file_skips_notification(self):
        db = _FakeDb(row=None)
        activity_events.complete_file(db, "f1", "x")
        assert db.calls == []
