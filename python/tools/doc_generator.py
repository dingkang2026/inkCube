"""python/tools/doc_generator.py — Document format conversion tool.

Supports:
  - md_to_pdf:  Markdown → PDF  (via weasyprint or markdown+pdfkit)
  - md_to_docx: Markdown → DOCX (via pandoc subprocess)
  - docx_to_pdf: DOCX → PDF    (reuse knowledge module's MS Office / LibreOffice)
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.base import BaseTool
from tools.tool_resolver import resolve_tool


class DocGeneratorTool(BaseTool):
    name = "doc_generator"
    description = (
        "文档格式转换工具。支持 Markdown 转 PDF、Markdown 转 DOCX、DOCX 转 PDF。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["md_to_pdf", "md_to_docx", "docx_to_pdf"],
                "description": "转换操作类型",
            },
            "input_path": {
                "type": "string",
                "description": "输入文件的相对路径（相对于工作区）",
            },
            "output_path": {
                "type": "string",
                "description": "输出文件的相对路径（相对于工作区）。省略则自动生成同名输出文件",
            },
        },
        "required": ["action", "input_path"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## doc_generator\n"
            "文档格式转换工具，在工作区中生成输出文件。\n\n"
            "可用操作:\n"
            "- `md_to_pdf`: 将 Markdown 文件转换为 PDF（支持中文、代码高亮、表格）\n"
            "- `md_to_docx`: 将 Markdown 文件转换为 DOCX（需要系统安装 pandoc）\n"
            "- `docx_to_pdf`: 将 DOCX 文件转换为 PDF（需要 MS Office 或 LibreOffice）\n\n"
            "用法:\n"
            "- input_path 是相对于工作区的路径\n"
            "- output_path 可省略，自动生成（如 report.md → report.pdf）\n"
            "- md_to_pdf 使用 weasyprint，首次使用需 pip install weasyprint markdown pygments\n"
            "- md_to_docx 需要系统安装 pandoc（pandoc --version 检查）\n\n"
            "示例:\n"
            '```\n{"action": "md_to_pdf", "input_path": "report.md"}\n'
            '→ {"ok": true, "output": "report.pdf", "size_kb": 42}\n```\n\n'
            '```\n{"action": "md_to_docx", "input_path": "report.md", "output_path": "output/report.docx"}\n'
            '→ {"ok": true, "output": "output/report.docx"}\n```'
        )

    def __init__(self, workspace_dir: str = ""):
        self.workspace_dir = workspace_dir

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    async def execute(self, action: str, input_path: str, output_path: str = "") -> str:
        if not self.workspace_dir:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)

        workspace = Path(self.workspace_dir).resolve()
        input_file = (workspace / input_path).resolve()

        # Security: ensure input is within workspace
        if not str(input_file).startswith(str(workspace) + os.sep) and input_file != workspace:
            return json.dumps({"error": "输入路径超出工作区范围"}, ensure_ascii=False)

        if not input_file.exists():
            return json.dumps({"error": f"输入文件不存在: {input_path}"}, ensure_ascii=False)

        # Determine output path
        if output_path:
            out_file = (workspace / output_path).resolve()
        else:
            ext_map = {
                "md_to_pdf": ".pdf",
                "md_to_docx": ".docx",
                "docx_to_pdf": ".pdf",
            }
            out_ext = ext_map.get(action, ".out")
            out_file = input_file.with_suffix(out_ext)

        # Security: ensure output is within workspace
        if not str(out_file).startswith(str(workspace) + os.sep) and out_file != workspace:
            return json.dumps({"error": "输出路径超出工作区范围"}, ensure_ascii=False)

        # Ensure output directory exists
        out_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            if action == "md_to_pdf":
                return self._md_to_pdf(input_file, out_file)
            elif action == "md_to_docx":
                return self._md_to_docx(input_file, out_file)
            elif action == "docx_to_pdf":
                return self._docx_to_pdf(input_file, out_file)
            else:
                return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"转换失败: {str(e)}"}, ensure_ascii=False)

    def _md_to_pdf(self, input_file: Path, output_file: Path) -> str:
        """Convert Markdown to PDF using weasyprint."""
        try:
            import markdown
            from weasyprint import HTML
        except ImportError:
            return json.dumps({
                "error": "缺少依赖: weasyprint 或 markdown",
                "suggestion": "运行 pip install weasyprint markdown pygments 安装",
            }, ensure_ascii=False)

        # Read and convert Markdown to HTML
        md_content = input_file.read_text(encoding="utf-8")
        html_body = markdown.markdown(
            md_content,
            extensions=["tables", "fenced_code", "codehilite", "toc", "nl2br"],
            extension_configs={
                "codehilite": {"css_class": "code", "guess_lang": False},
            },
        )

        # Wrap in full HTML with CSS styling
        html_doc = self._wrap_html_with_css(html_body, input_file.stem)

        # Convert HTML to PDF
        HTML(string=html_doc).write_pdf(str(output_file))

        size_kb = output_file.stat().st_size // 1024
        return json.dumps({
            "ok": True,
            "action": "md_to_pdf",
            "input": str(input_file.relative_to(self.workspace_dir)),
            "output": str(output_file.relative_to(self.workspace_dir)),
            "size_kb": size_kb,
        }, ensure_ascii=False)

    def _md_to_docx(self, input_file: Path, output_file: Path) -> str:
        """Convert Markdown to DOCX using pandoc."""
        # Check if pandoc is available (unified resolver: bundled > PATH)
        pandoc = resolve_tool("pandoc")
        if not pandoc:
            return json.dumps({
                "error": "pandoc 未安装",
                "suggestion": (
                    "请先安装 pandoc:\n"
                    "- Windows: winget install JohnMacFarlane.Pandoc 或 choco install pandoc\n"
                    "- macOS: brew install pandoc\n"
                    "- Linux: sudo apt install pandoc\n"
                    "安装后重启应用生效"
                ),
            }, ensure_ascii=False)

        cmd = [
            pandoc,
            str(input_file),
            "-o", str(output_file),
            "--from", "markdown",
            "--to", "docx",
        ]

        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        if result.returncode != 0:
            return json.dumps({
                "error": f"pandoc 转换失败: {result.stderr.strip()}",
            }, ensure_ascii=False)

        size_kb = output_file.stat().st_size // 1024
        return json.dumps({
            "ok": True,
            "action": "md_to_docx",
            "input": str(input_file.relative_to(self.workspace_dir)),
            "output": str(output_file.relative_to(self.workspace_dir)),
            "size_kb": size_kb,
        }, ensure_ascii=False)

    def _docx_to_pdf(self, input_file: Path, output_file: Path) -> str:
        """Convert DOCX to PDF using MS Office (Windows) or LibreOffice."""
        if input_file.suffix.lower() not in (".docx", ".pptx", ".xlsx"):
            return json.dumps({
                "error": f"不支持的文件类型: {input_file.suffix}",
                "supported": [".docx", ".pptx", ".xlsx"],
            }, ensure_ascii=False)

        # Try to reuse knowledge module's conversion logic
        try:
            from knowledge.model_manager import ModelManager
            status = ModelManager.check_office_converter()
        except Exception:
            status = {"available": False}

        if not status.get("available"):
            return json.dumps({
                "error": "未找到 Office 转换工具",
                "suggestion": (
                    "需要安装以下任一工具:\n"
                    "- Windows: Microsoft Office（自动检测）\n"
                    "- 跨平台: LibreOffice（sudo apt install libreoffice / brew install --cask libreoffice）"
                ),
            }, ensure_ascii=False)

        tool = status.get("tool")
        tool_path = status.get("path", "")

        if tool == "ms-office":
            success = self._convert_with_ms_office(input_file, output_file)
        elif tool == "libreoffice":
            success = self._convert_with_libreoffice(input_file, output_file, tool_path)
        else:
            success = False

        if not success:
            return json.dumps({"error": "DOCX → PDF 转换失败"}, ensure_ascii=False)

        size_kb = output_file.stat().st_size // 1024
        return json.dumps({
            "ok": True,
            "action": "docx_to_pdf",
            "input": str(input_file.relative_to(self.workspace_dir)),
            "output": str(output_file.relative_to(self.workspace_dir)),
            "size_kb": size_kb,
            "converter": tool,
        }, ensure_ascii=False)

    def _convert_with_ms_office(self, input_path: Path, output_path: Path) -> bool:
        """Convert using Microsoft Office COM automation (Windows)."""
        try:
            import win32com.client

            ext = input_path.suffix.lower()
            if ext == ".docx":
                app = win32com.client.Dispatch("Word.Application")
                app.Visible = False
                doc = app.Documents.Open(str(input_path.absolute()))
                doc.SaveAs(str(output_path.absolute()), FileFormat=17)
                doc.Close()
                app.Quit()
            elif ext == ".pptx":
                app = win32com.client.Dispatch("PowerPoint.Application")
                pres = app.Presentations.Open(str(input_path.absolute()))
                pres.SaveAs(str(output_path.absolute()), FileFormat=32)
                pres.Close()
                app.Quit()
            elif ext == ".xlsx":
                app = win32com.client.Dispatch("Excel.Application")
                app.Visible = False
                wb = app.Workbooks.Open(str(input_path.absolute()))
                wb.ExportAsFixedFormat(0, str(output_path.absolute()))
                wb.Close()
                app.Quit()
            else:
                return False

            return output_path.exists()
        except Exception:
            return False

    def _convert_with_libreoffice(self, input_path: Path, output_path: Path, soffice_path: str) -> bool:
        """Convert using LibreOffice headless mode."""
        try:
            cmd = [
                soffice_path,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(input_path.parent),
                str(input_path.absolute()),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

            if result.returncode == 0 and "Error" not in stderr_text:
                # LibreOffice outputs to same dir with .pdf extension
                lo_pdf = input_path.with_suffix(".pdf")
                if lo_pdf.exists():
                    if lo_pdf != output_path:
                        lo_pdf.rename(output_path)
                    return True
            return False
        except Exception:
            return False

    @staticmethod
    def _wrap_html_with_css(html_body: str, title: str) -> str:
        """Wrap HTML body with CSS styling for PDF output."""
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
@page {{
    margin: 2cm;
    size: A4;
}}
body {{
    font-family: "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",
                 "PingFang SC", "Hiragino Sans GB", sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #333;
}}
h1 {{ font-size: 20pt; margin-top: 1em; margin-bottom: 0.5em; color: #111; }}
h2 {{ font-size: 16pt; margin-top: 0.8em; margin-bottom: 0.4em; color: #222; }}
h3 {{ font-size: 13pt; margin-top: 0.6em; margin-bottom: 0.3em; color: #333; }}
p {{ margin: 0.5em 0; }}
code {{
    font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
    background: #f5f5f5;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 0.9em;
}}
pre {{
    background: #f5f5f5;
    padding: 12px;
    border-radius: 4px;
    border: 1px solid #e0e0e0;
    overflow-x: auto;
    font-size: 0.85em;
    line-height: 1.4;
}}
pre code {{
    background: none;
    padding: 0;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}}
th, td {{
    border: 1px solid #ccc;
    padding: 6px 10px;
    text-align: left;
}}
th {{
    background: #f0f0f0;
    font-weight: bold;
}}
blockquote {{
    border-left: 3px solid #ccc;
    margin: 1em 0;
    padding: 0.5em 1em;
    color: #666;
}}
img {{
    max-width: 100%;
    height: auto;
}}
</style>
</head>
<body>
{html_body}
</body>
</html>"""
