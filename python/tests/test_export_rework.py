"""python/tests/test_export_rework.py — 报告导出改造单测（2.5 §1.3 / §3.7 / §5 API 14）。

覆盖：
  - doc_template.resolve_reference_doc：portrait/landscape 模板解析（复用
    paths.get_templates_dir 链路）、非法方向回退 portrait、模板缺失回退 None
  - reports._sanitize_filename：非法字符替换、首尾点/空白剥离、超长截断、空回退
  - export 端点：orientation 校验（400）、默认位置（sanitize 文件名）、
    target_path 另存为形式、orientation 透传 renderer、无请求体默认 portrait

不触 pandoc 不触网：渲染函数 monkeypatch 捕获参数，模板目录指向 tmp_path。
"""
import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from routers import reports as reports_module

_MD = "# 报告\n\n正文内容"


# ── renderer._resolve_reference_doc ────────────────────────


class TestResolveReferenceDoc:
    @pytest.fixture
    def templates_dir(self, tmp_path, monkeypatch):
        from tools import doc_template as doc_template_mod

        monkeypatch.setattr(doc_template_mod, "get_templates_dir", lambda: str(tmp_path))
        return tmp_path

    def _resolve(self, orientation):
        from tools.doc_template import resolve_reference_doc

        return resolve_reference_doc(orientation)

    def test_portrait_template_resolved(self, templates_dir):
        (templates_dir / "custom-reference_portrait.docx").write_bytes(b"x")
        path = self._resolve("portrait")
        assert path is not None and path.endswith("custom-reference_portrait.docx")

    def test_landscape_template_resolved(self, templates_dir):
        (templates_dir / "custom-reference_landscape.docx").write_bytes(b"x")
        path = self._resolve("landscape")
        assert path is not None and path.endswith("custom-reference_landscape.docx")

    def test_invalid_orientation_falls_back_to_portrait(self, templates_dir):
        (templates_dir / "custom-reference_portrait.docx").write_bytes(b"x")
        path = self._resolve("diagonal")  # 非法值回退 portrait
        assert path is not None and path.endswith("custom-reference_portrait.docx")

    def test_missing_template_returns_none(self, templates_dir):
        """模板意外缺失 → None（不带 --reference-doc 以默认样式导出 + WARNING）。"""
        assert self._resolve("portrait") is None
        assert self._resolve("landscape") is None


# ── reports._sanitize_filename ─────────────────────────────


class TestSanitizeFilename:
    def test_invalid_chars_replaced(self):
        assert reports_module._sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"

    def test_control_chars_replaced(self):
        assert reports_module._sanitize_filename("报\u0001告") == "报_告"

    def test_trailing_dots_and_spaces_stripped(self):
        assert reports_module._sanitize_filename("  报告..  ") == "报告"

    def test_long_title_truncated(self):
        assert len(reports_module._sanitize_filename("长" * 100)) == 80

    def test_empty_falls_back(self):
        assert reports_module._sanitize_filename("") == "报告"
        assert reports_module._sanitize_filename(None) == "报告"
        # 路径穿越标题：非法字符替换后仅剩下划线主干（不再含分隔符）
        assert reports_module._sanitize_filename("../..") == "_"


# ── export 端点 ────────────────────────────────────────────


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    return db.create_project(name="Export Test", project_type="general")["id"]


@pytest.fixture
def report_dir(tmp_path):
    d = tmp_path / "r_export"
    d.mkdir()
    (d / "content.md").write_text(_MD, encoding="utf-8")
    return d


@pytest.fixture
def report_id(db, project_id, report_dir):
    rid = "r_export"
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
        "progress, content_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, project_id, "due_diligence", "A/B:市场*报告?", "Word", "done",
         100, str(report_dir), "2026-10-08T00:00:00", "2026-10-08T00:00:00"),
    )
    db.conn.commit()
    return rid


@pytest.fixture
def render_spy(monkeypatch):
    """捕获渲染调用参数（函数内 import，patch 源模块符号）。"""
    from report_generator import renderer as renderer_mod

    calls = []

    def fake_render(markdown_text, output_path, title="报告", orientation="portrait"):
        calls.append({
            "markdown_text": markdown_text,
            "output_path": output_path,
            "title": title,
            "orientation": orientation,
        })
        return output_path

    monkeypatch.setattr(renderer_mod, "render_markdown_to_docx", fake_render)
    return calls


class TestExportEndpoint:
    @pytest.fixture(autouse=True)
    def _inject_db(self, monkeypatch, db):
        """get_db() 走 deps.db 全局，测试内存库注入。"""
        import deps
        monkeypatch.setattr(deps, "db", db)

    def test_invalid_orientation_rejected(self, db, report_id, render_spy):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(reports_module.export_report(
                report_id, reports_module.ExportReportRequest(orientation="diagonal")))
        assert exc.value.status_code == 400
        assert render_spy == []  # 校验失败不触发渲染

    def test_default_path_uses_sanitized_title(self, db, report_id, render_spy):
        result = asyncio.run(reports_module.export_report(report_id, None))
        # 标题 A/B:市场*报告? → A_B_市场_报告_（尾字符 _ 后 strip 掉点空白，保持 _）
        out = render_spy[0]["output_path"]
        assert out == result["path"]
        assert os.path.basename(out) == "A_B_市场_报告_.docx"
        assert render_spy[0]["orientation"] == "portrait"  # 无请求体默认 portrait
        assert render_spy[0]["markdown_text"] == _MD

    def test_target_path_rendered_as_given(self, db, report_id, render_spy, tmp_path):
        target = str(tmp_path / "保存目录" / "自定义.docx")
        result = asyncio.run(reports_module.export_report(
            report_id,
            reports_module.ExportReportRequest(orientation="landscape", target_path=target),
        ))
        assert result["path"] == target
        assert render_spy[0]["output_path"] == target
        assert render_spy[0]["orientation"] == "landscape"  # 方向透传 renderer

    def test_missing_content_file_404(self, db, project_id, render_spy):
        rid = "r_no_md"
        db.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
            "progress, content_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, project_id, "due_diligence", "无内容报告", "Word", "done",
             100, "Z:/not/exist", "2026-10-08T00:00:00", "2026-10-08T00:00:00"),
        )
        db.conn.commit()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(reports_module.export_report(rid, None))
        assert exc.value.status_code == 404

    def test_unknown_report_404(self, render_spy):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(reports_module.export_report("r_missing", None))
        assert exc.value.status_code == 404
