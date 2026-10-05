"""python/tools/doc_generator.py — Document format conversion tool.

Supports:
  - md_to_pdf:  Markdown → PDF  (via pandoc + LibreOffice/MS Office)
  - md_to_docx: Markdown → DOCX (via pandoc)
  - docx_to_pdf: DOCX/PPTX/XLSX → PDF (via MS Office COM or LibreOffice headless)

Mermaid diagrams are pre-processed via a standalone Electron sub-process
that renders ```mermaid blocks to SVG files using the project's bundled
mermaid.min.js — no extra dependency (puppeteer / mmdc) needed.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools.base import BaseTool
from tools.tool_resolver import resolve_tool

logger = logging.getLogger(__name__)


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
            "- `md_to_pdf`: 将 Markdown 文件转换为 PDF（通过 pandoc + LibreOffice/MS Office）\n"
            "- `md_to_docx`: 将 Markdown 文件转换为 DOCX（通过 pandoc，已内置）\n"
            "- `docx_to_pdf`: 将 DOCX/PPTX/XLSX 文件转换为 PDF（需要 MS Office 或 LibreOffice）\n\n"
            "用法:\n"
            "- input_path 是相对于工作区的路径\n"
            "- output_path 可省略，自动生成（如 report.md → report.pdf）\n"
            "- md_to_pdf 需要 pandoc（已内置）和 LibreOffice 或 MS Office\n\n"
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
            # Conversions run pandoc/LibreOffice subprocesses (up to 120s) and
            # MS Office COM — the whole sync pipeline goes to a worker thread
            # so the event loop stays responsive (H1). COM is initialized
            # per-thread inside _convert_with_ms_office.
            if action == "md_to_pdf":
                return await asyncio.to_thread(self._md_to_pdf, input_file, out_file)
            elif action == "md_to_docx":
                return await asyncio.to_thread(self._md_to_docx, input_file, out_file)
            elif action == "docx_to_pdf":
                return await asyncio.to_thread(self._docx_to_pdf, input_file, out_file)
            else:
                return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"转换失败: {str(e)}"}, ensure_ascii=False)

    def _md_to_pdf(self, input_file: Path, output_file: Path) -> str:
        """Convert Markdown to PDF via pandoc (md→docx) + LibreOffice/MS Office (docx→pdf).

        Fallback chain:
        1. pandoc (md → docx) — pandoc is bundled with the app
        2. LibreOffice or MS Office (docx → pdf) — detected at runtime

        Mermaid diagrams are pre-processed to SVG before pandoc.
        """
        # Step 0: Pre-process Mermaid diagrams
        actual_input, mmd_tmp = self._preprocess_mermaid(input_file)

        # Step 1: Check pandoc availability
        pandoc = resolve_tool("pandoc")
        if not pandoc:
            return json.dumps({
                "error": "pandoc 未找到",
                "suggestion": "pandoc 应已内置。请检查应用安装是否完整。",
            }, ensure_ascii=False)

        # Step 2: Check Office converter availability
        try:
            from knowledge.model_manager import ModelManager
            status = ModelManager.check_office_converter()
        except Exception:
            status = {"available": False}

        if not status.get("available"):
            return json.dumps({
                "error": "未找到 PDF 转换工具",
                "suggestion": (
                    "md_to_pdf 需要 LibreOffice 或 MS Office 来生成 PDF。\n"
                    "请安装以下任一工具:\n"
                    "- Windows: Microsoft Office（自动检测）\n"
                    "- 跨平台: LibreOffice（https://www.libreoffice.org/download/）"
                ),
            }, ensure_ascii=False)

        # Step 3: pandoc md → docx (temp file)
        tmp_docx = output_file.with_suffix(".tmp.docx")
        try:
            cmd_pandoc = [
                pandoc,
                str(actual_input),
                "-o", str(tmp_docx),
                "--from", "markdown",
                "--to", "docx",
            ]
            result = subprocess.run(
                cmd_pandoc, capture_output=True, encoding="utf-8", errors="replace", timeout=60,
            )
            if result.returncode != 0:
                return json.dumps({
                    "error": f"pandoc md→docx 失败: {result.stderr.strip()}",
                }, ensure_ascii=False)

            # Step 4: docx → pdf using existing converter
            tool = status.get("tool")
            tool_path = status.get("path", "")

            if tool == "ms-office":
                success = self._convert_with_ms_office(tmp_docx, output_file)
            elif tool == "libreoffice":
                success = self._convert_with_libreoffice(tmp_docx, output_file, tool_path)
            else:
                success = False

            if not success:
                return json.dumps({
                    "error": f"{tool} docx→pdf 转换失败",
                }, ensure_ascii=False)

            size_kb = output_file.stat().st_size // 1024
            return json.dumps({
                "ok": True,
                "action": "md_to_pdf",
                "input": str(input_file.relative_to(self.workspace_dir)),
                "output": str(output_file.relative_to(self.workspace_dir)),
                "size_kb": size_kb,
                "converter": f"pandoc+{tool}",
            }, ensure_ascii=False)

        finally:
            # Clean up temp docx
            if tmp_docx.exists():
                try:
                    tmp_docx.unlink()
                except OSError:
                    pass
            # Clean up mermaid temp dir
            if mmd_tmp:
                shutil.rmtree(mmd_tmp, ignore_errors=True)

    def _md_to_docx(self, input_file: Path, output_file: Path) -> str:
        """Convert Markdown to DOCX using pandoc.

        Mermaid diagrams are pre-processed to SVG before pandoc.
        """
        # Pre-process Mermaid diagrams
        actual_input, mmd_tmp = self._preprocess_mermaid(input_file)

        try:
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
                str(actual_input),
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
        finally:
            if mmd_tmp:
                shutil.rmtree(mmd_tmp, ignore_errors=True)

    # ── Mermaid pre-processing (zero-dependency via Electron) ───────────

    _MERMAID_RE = re.compile(r'^```mermaid\s*\n(.*?)^```\s*$', re.MULTILINE | re.DOTALL)

    def _find_electron(self) -> str | None:
        """Locate the Electron binary for spawning a mermaid renderer."""
        # 1. Environment variable override
        env = os.environ.get('INKCUBE_ELECTRON_PATH')
        if env and os.path.isfile(env):
            return env

        project_root = Path(__file__).resolve().parent.parent.parent

        # 2. Development: node_modules/electron/dist/
        if sys.platform == 'win32':
            candidates = [
                project_root / 'node_modules' / 'electron' / 'dist' / 'electron.exe',
            ]
        else:
            candidates = [
                project_root / 'node_modules' / 'electron' / 'dist' / 'electron',
            ]
        for c in candidates:
            if c.is_file():
                return str(c)

        # 3. System PATH
        found = shutil.which('electron')
        if found:
            return found

        logger.warning('Electron binary not found — Mermaid diagrams will be skipped')
        return None

    def _render_mermaid_to_svg(self, electron: str, code: str) -> str | None:
        """Render a single Mermaid diagram to SVG via a headless Electron process."""
        renderer_script = str(
            Path(__file__).resolve().parent.parent.parent / 'electron' / 'mermaid-renderer.js'
        )
        if not os.path.isfile(renderer_script):
            logger.warning('mermaid-renderer.js not found at %s', renderer_script)
            return None

        try:
            result = subprocess.run(
                [electron, renderer_script],
                input=code,
                capture_output=True,
                encoding='utf-8',
                timeout=30,
                env={**os.environ, 'INKCUBE_MERMAID_RENDERER': '1'},
            )
            if result.returncode != 0:
                logger.warning(
                    'Mermaid render failed (exit %d): %s',
                    result.returncode, result.stderr.strip(),
                )
                return None
            svg = result.stdout.strip()
            return svg if svg.startswith('<svg') or svg.startswith('<?xml') else None
        except subprocess.TimeoutExpired:
            logger.warning('Mermaid render timed out')
            return None
        except Exception as e:
            logger.warning('Mermaid render error: %s', e)
            return None

    def _preprocess_mermaid(self, input_file: Path) -> tuple[Path, str | None]:
        """Replace ```mermaid blocks with SVG image references.

        Returns (actual_input_path, temp_dir_or_None).
        If no mermaid blocks exist or Electron is unavailable, returns the
        original input_file and None — callers can use input_file directly.
        """
        content = input_file.read_text(encoding='utf-8')
        if not self._MERMAID_RE.search(content):
            return input_file, None

        electron = self._find_electron()
        if not electron:
            return input_file, None

        tmp_dir = tempfile.mkdtemp(prefix='inkcube_mmd_')
        svg_dir = Path(tmp_dir) / 'svgs'
        svg_dir.mkdir()

        success_count = 0

        def _replace_block(m: re.Match) -> str:
            nonlocal success_count
            code = m.group(1)
            svg = self._render_mermaid_to_svg(electron, code)
            if svg:
                svg_path = svg_dir / f'diagram_{success_count}.svg'
                svg_path.write_text(svg, encoding='utf-8')
                success_count += 1
                return f'![diagram]({svg_path})'
            logger.warning('Mermaid block render failed, keeping as code')
            return m.group(0)  # leave block unchanged on failure

        modified = self._MERMAID_RE.sub(_replace_block, content)

        if success_count == 0:
            # All renders failed — no change
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return input_file, None

        # Write pre-processed markdown to a temp file
        preprocessed = Path(tmp_dir) / 'preprocessed.md'
        preprocessed.write_text(modified, encoding='utf-8')
        logger.info('Mermaid: rendered %d diagram(s) to SVG', success_count)
        return preprocessed, tmp_dir

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
        """Convert using Microsoft Office COM automation (Windows only).

        Runs inside a worker thread (asyncio.to_thread from execute) — COM
        must be initialized per-thread, so CoInitialize/CoUninitialize
        bracket the automation here (main-thread auto-init doesn't cover
        worker threads).
        """
        app = None
        doc = None
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
        except Exception:
            return False  # pywin32 unavailable or COM init failed

        try:
            ext = input_path.suffix.lower()
            if ext == ".docx":
                app = win32com.client.Dispatch("Word.Application")
                app.Visible = False
                doc = app.Documents.Open(str(input_path.absolute()))
                doc.SaveAs(str(output_path.absolute()), FileFormat=17)
                doc.Close()
                doc = None
                app.Quit()
                app = None
            elif ext == ".pptx":
                app = win32com.client.Dispatch("PowerPoint.Application")
                pres = app.Presentations.Open(str(input_path.absolute()))
                pres.SaveAs(str(output_path.absolute()), FileFormat=32)
                pres.Close()
                pres = None
                app.Quit()
                app = None
            elif ext == ".xlsx":
                app = win32com.client.Dispatch("Excel.Application")
                app.Visible = False
                wb = app.Workbooks.Open(str(input_path.absolute()))
                wb.ExportAsFixedFormat(0, str(output_path.absolute()))
                wb.Close()
                wb = None
                app.Quit()
                app = None
            else:
                return False

            return output_path.exists()
        except Exception:
            return False
        finally:
            # Ensure COM objects are always released to prevent process leaks
            if doc is not None:
                try:
                    doc.Close()
                except Exception:
                    pass
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

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
