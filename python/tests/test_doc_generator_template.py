"""python/tests/test_doc_generator_template.py — doc_generator 统一 Word 模板单测。

覆盖（2.5 §3.7 pandoc reference-doc 全应用统一）：
  - md_to_docx：模板存在 → pandoc 命令带 --reference-doc（portrait 模板）
  - md_to_docx：模板缺失 → 不带该参数以默认样式导出，不报错（仅兜底）
  - md_to_pdf：pandoc md→docx 中间步同样带 --reference-doc（PDF 继承统一样式）

不触真实 pandoc / Electron / Office：subprocess.run 与转换器 monkeypatch
捕获命令，模板目录指向 tmp_path。
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.doc_generator import DocGeneratorTool

_MD = "# 任务报告\n\n正文段落。"


class _FakePandoc:
    """替换 doc_generator.subprocess.run：捕获命令并写出 -o 目标文件。"""

    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, **kwargs):
        self.cmds.append(list(cmd))
        out = cmd[cmd.index("-o") + 1]
        with open(out, "wb") as f:
            f.write(b"docx-bytes")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    from tools import doc_template as doc_template_mod

    monkeypatch.setattr(doc_template_mod, "get_templates_dir", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def fake_pandoc(monkeypatch):
    import tools.doc_generator as doc_gen_mod

    fake = _FakePandoc()
    monkeypatch.setattr(doc_gen_mod, "resolve_tool", lambda name: "pandoc-fake")
    monkeypatch.setattr(doc_gen_mod.subprocess, "run", fake)
    return fake


def _tool(tmp_path) -> DocGeneratorTool:
    """构造工具并预建输出目录（mkdir 逻辑在 execute 层，直调内部方法需自建）。"""
    (tmp_path / "out").mkdir()
    tool = DocGeneratorTool()
    tool.set_workspace(str(tmp_path))
    return tool


def _write_input(ws) -> str:
    input_file = ws / "report.md"
    input_file.write_text(_MD, encoding="utf-8")
    return str(input_file)


class TestMdToDocxTemplate:
    def test_reference_doc_attached(self, tmp_path, templates_dir, fake_pandoc):
        (templates_dir / "custom-reference_portrait.docx").write_bytes(b"x")
        _write_input(tmp_path)
        out = str(tmp_path / "out" / "report.docx")
        result = json.loads(_tool(tmp_path)._md_to_docx(
            tmp_path / "report.md", tmp_path / "out" / "report.docx"))
        assert result["ok"] is True
        expected = str(templates_dir / "custom-reference_portrait.docx")
        assert fake_pandoc.cmds[0][fake_pandoc.cmds[0].index("--reference-doc") + 1] == expected
        assert os.path.exists(out)

    def test_missing_template_falls_back_silently(self, tmp_path, templates_dir, fake_pandoc):
        """模板意外缺失 → 不带 --reference-doc 以默认样式导出，不报错（仅兜底）。"""
        _write_input(tmp_path)
        result = json.loads(_tool(tmp_path)._md_to_docx(
            tmp_path / "report.md", tmp_path / "out" / "report.docx"))
        assert result["ok"] is True
        assert "--reference-doc" not in fake_pandoc.cmds[0]


class TestMdToPdfTemplate:
    @pytest.fixture
    def office_available(self, monkeypatch):
        from knowledge.model_manager import ModelManager

        monkeypatch.setattr(
            ModelManager, "check_office_converter",
            staticmethod(lambda: {"available": True, "tool": "libreoffice",
                                  "path": "soffice-fake"}))

    @pytest.fixture
    def libreoffice_ok(self, monkeypatch):
        def fake_convert(self, input_path, output_path, soffice_path):
            output_path.write_bytes(b"pdf-bytes")
            return True

        monkeypatch.setattr(DocGeneratorTool, "_convert_with_libreoffice", fake_convert)

    def test_pandoc_step_carries_reference_doc(
            self, tmp_path, templates_dir, fake_pandoc, office_available, libreoffice_ok):
        (templates_dir / "custom-reference_portrait.docx").write_bytes(b"x")
        _write_input(tmp_path)
        result = json.loads(_tool(tmp_path)._md_to_pdf(
            tmp_path / "report.md", tmp_path / "out" / "report.pdf"))
        assert result["ok"] is True
        expected = str(templates_dir / "custom-reference_portrait.docx")
        pandoc_cmd = fake_pandoc.cmds[0]
        assert pandoc_cmd[pandoc_cmd.index("--reference-doc") + 1] == expected

    def test_missing_template_falls_back_silently(
            self, tmp_path, templates_dir, fake_pandoc, office_available, libreoffice_ok):
        _write_input(tmp_path)
        result = json.loads(_tool(tmp_path)._md_to_pdf(
            tmp_path / "report.md", tmp_path / "out" / "report.pdf"))
        assert result["ok"] is True
        assert "--reference-doc" not in fake_pandoc.cmds[0]
