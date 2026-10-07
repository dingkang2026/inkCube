"""python/knowledge/doc_parser.py"""
import logging
import os
import hashlib
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field
import tempfile
import subprocess

# docling-core is a hard dependency of docling (same failure surface), but the
# serializer classes below need their base classes at module import time.
from docling_core.transforms.chunker.hierarchical_chunker import ChunkingDocSerializer
from docling_core.transforms.serializer.base import (
    BaseDocSerializer,
    BaseSerializerProvider,
    BaseTableSerializer,
)
from docling_core.transforms.serializer.common import (
    SerializationResult,
    create_ser_result,
)
from docling_core.types.doc import DoclingDocument, TableItem

logger = logging.getLogger(__name__)

# COM hresults meaning "callee busy, retry shortly". Office automation under
# heavy CPU load (e.g. concurrent embedding inference) transiently rejects
# calls with these even though the app is healthy; the COM-prescribed remedy
# is backoff-and-retry. Incident 2026-10-07: a docx conversion failed with
# "Open.Close" while embedding saturated the CPU; a manual rerun succeeded.
_COM_RETRY_HRESULTS = {-2147418111, -2147417846}  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER
_COM_RETRY_DELAYS = (0.5, 1.0, 2.0)


# ---------------------------------------------------------------------------
# Module-level singleton: Docling DocumentConverter is shared across ALL
# DocumentParser instances so its models (layout, table, OCR) are loaded
# exactly once per process.
# ---------------------------------------------------------------------------
_docling_converter = None
_docling_converter_lock = threading.Lock()


def _get_docling_converter(models_dir: Path):
    """Return the singleton DocumentConverter, loading it on first call."""
    global _docling_converter
    if _docling_converter is not None:
        return _docling_converter
    with _docling_converter_lock:
        if _docling_converter is not None:
            return _docling_converter
        try:
            from docling.document_converter import (
                DocumentConverter, PdfFormatOption, ImageFormatOption,
            )
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions, TableStructureV2Options,
            )

            def _build_pipeline_options(generate_picture_images: bool):
                opts = PdfPipelineOptions()
                opts.artifacts_path = str(models_dir)
                opts.do_ocr = True
                opts.do_table_structure = True
                if opts.do_table_structure:
                    opts.table_structure_options = TableStructureV2Options()
                opts.generate_picture_images = generate_picture_images
                opts.generate_page_images = False
                return opts

            _docling_converter = DocumentConverter(
                format_options={
                    # 文档输入：提取内嵌图区域（图片保留是文档理解的核心，2.2 §4.4）
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=_build_pipeline_options(True)
                    ),
                    # 独立图片输入：禁用 picture 区域裁剪——版面模型会把照片内的
                    # 图形元素（如证书上的国徽/公章）框成「图中图」碎片，图片库
                    # 出现碎片而非用户上传的整图；整图由 pipeline 阶段③登记
                    # （_source_image_context，2.2 §4.4）
                    InputFormat.IMAGE: ImageFormatOption(
                        pipeline_options=_build_pipeline_options(False)
                    ),
                }
            )
            logger.info("Docling converter loaded (artifacts: %s)", models_dir)
        except ImportError as e:
            logger.warning("Docling not available: %s", e)
            _docling_converter = None
    return _docling_converter


# ---------------------------------------------------------------------------
# First-convert serialization: Docling loads its pipeline models lazily
# inside the FIRST convert() call (unlocked internal caches + model init on
# that path), so two workers converting concurrently at startup can crash
# each other. Steady-state convert() is PyTorch/ONNX inference and is
# thread-safe, so after the first call completes the lock is never taken
# again (zero steady-state cost).
# ---------------------------------------------------------------------------
_docling_first_convert_lock = threading.Lock()
_docling_warmed = False


def _convert_threadsafe(converter, file_path: str):
    """Run converter.convert, serializing only the first (lazy-init) call."""
    global _docling_warmed
    if _docling_warmed:
        return converter.convert(file_path)
    with _docling_first_convert_lock:
        if not _docling_warmed:
            try:
                return converter.convert(file_path)
            finally:
                # Mark warmed even on failure: init state is already mutated;
                # retrying the racy init path would only re-open the race
                _docling_warmed = True
    return converter.convert(file_path)


class RowMarkdownTableSerializer(BaseTableSerializer):
    """Table serializer emitting row-level Markdown (2.2 §4.3).

    Replaces docling_core's default TripletTableSerializer whose
    "row, col = value." triplets lose column-header semantics on xlsx
    (merged title/unit rows become empty headers, label rows repeat per
    column). All formats serialize tables uniformly as Markdown rows so
    HybridChunker can split on line boundaries and repeat the header per
    chunk (repeat_table_header).

    Header detection: first row within the top MAX_HEADER_SCAN having
    >= MIN_HEADER_NON_EMPTY non-empty cells covering >= MIN_HEADER_FILL_RATIO
    of all columns. xlsx merged cells do not share objects in the grid
    (col_span is always 1), so sparse title/unit rows can only be told
    apart by sparsity (measured: title 1/16, unit row 2/16, header 15/16);
    the fill ratio is computed against num_cols so unit rows with a few
    non-None cells cannot reach it.
    Rows above the header are emitted once as preamble text.
    """

    MAX_HEADER_SCAN = 5
    MIN_HEADER_NON_EMPTY = 2
    MIN_HEADER_FILL_RATIO = 0.6   # of num_cols: a real header covers most columns
    # Excel serial dates for 2000-01-01 .. 2099-12-31 (epoch 1899-12-30)
    _SERIAL_MIN = 36526
    _SERIAL_MAX = 73050

    def serialize(
        self,
        *,
        item: TableItem,
        doc_serializer: BaseDocSerializer,
        doc: DoclingDocument,
        **kwargs,
    ) -> SerializationResult:
        parts = []
        cap_res = doc_serializer.serialize_captions(item=item, **kwargs)
        if cap_res.text:
            parts.append(cap_res.text)

        grid = item.data.grid
        if not grid or not grid[0]:
            return create_ser_result(text="\n".join(parts) if parts else "", span_source=item)

        num_rows, num_cols = len(grid), len(grid[0])

        def cell_text(r, c):
            cell = grid[r][c]
            return (cell.text or "").strip() if cell is not None else ""

        # 垂直合并的重复格：span 覆盖位置与锚点是同一对象（仅 PDF 等真合并
        # 后端存在；xlsx 后端不共享对象，此分支不会触发）
        def is_span_repeat(r, c):
            return r > 0 and grid[r][c] is not None and grid[r][c] is grid[r - 1][c]

        # 水平 span 按 id 去重后的唯一格列表 [(col, cell), ...]
        def row_unique_cells(r):
            seen = {}
            for c in range(num_cols):
                cell = grid[r][c]
                if cell is not None and id(cell) not in seen:
                    seen[id(cell)] = (c, cell)
            return list(seen.values())

        # 表头检测：非空格 >= 2 且占列数 >= 60%（标题/说明行稀疏，真表头满格）。
        # 分母必须是总列数：说明行若只有少数非 None 格（如「编制单位」+
        # 「金额单位」），按非 None 格算占比会是 100% 而被误判为表头
        header_idx = None
        for r in range(min(self.MAX_HEADER_SCAN, num_rows)):
            unique = row_unique_cells(r)
            non_empty = sum(1 for _, cell in unique if (cell.text or "").strip())
            if (
                non_empty >= self.MIN_HEADER_NON_EMPTY
                and non_empty / num_cols >= self.MIN_HEADER_FILL_RATIO
            ):
                header_idx = r
                break

        preamble_lines = []
        if header_idx is None:
            # 无可检测表头：全部行按纯行输出（退化但可读），不造表头/分隔行
            body_start = 0
        else:
            body_start = header_idx + 1
            for r in range(header_idx):
                vals = [cell_text(r, c) for c, _ in row_unique_cells(r) if cell_text(r, c)]
                if vals:
                    preamble_lines.append("，".join(dict.fromkeys(vals)))

        # 表头行 Excel 日期序列号 → YYYY-MM-DD：xlsx 日期单元格被序列化为
        # 天数整数；仅当行内 >= 3 个纯数字格（多列日期表头）才整行转换
        def to_iso_date(v):
            try:
                n = float(v)
            except (TypeError, ValueError):
                return v
            if self._SERIAL_MIN <= n <= self._SERIAL_MAX and n == int(n):
                return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
            return v

        lines = list(preamble_lines)
        if header_idx is not None and header_idx < num_rows:
            header_cells = [cell_text(header_idx, c) or " " for c in range(num_cols)]
            if sum(1 for v in header_cells if v.strip().isdigit()) >= 3:
                header_cells = [to_iso_date(v) for v in header_cells]
            lines.append("| " + " | ".join(header_cells) + " |")
            lines.append("|" + "|".join([" - " for _ in range(num_cols)]) + "|")

        for r in range(body_start, num_rows):
            cells = [cell_text(r, c) for c in range(num_cols)]
            if not any(cells):
                continue
            row_cells = []
            for c, t in enumerate(cells):
                if is_span_repeat(r, c):
                    row_cells.append(" ")
                else:
                    row_cells.append(t.replace("\n", " ").replace("|", "&#124;") or " ")
            lines.append("| " + " | ".join(row_cells) + " |")

        parts.append("\n".join(lines))
        return create_ser_result(text="\n\n".join(p for p in parts if p), span_source=item)

    def get_header_and_body_lines(self, *, table_text: str, **kwargs):
        """Split the serialized table into (header lines, body lines) for the
        chunker's table path: header = column row + separator row, so split
        chunks stay on line boundaries and repeat the header."""
        all_lines = table_text.splitlines(True)
        sep_idx = next(
            (i for i, l in enumerate(all_lines)
             if l.rstrip("\n").startswith("|") and set(l) <= set("|-: \n")),
            None,
        )
        if sep_idx is None or sep_idx == 0:
            return [], all_lines
        return all_lines[sep_idx - 1: sep_idx + 1], all_lines[sep_idx + 1:]


class RowMarkdownSerializerProvider(BaseSerializerProvider):
    """ChunkingDocSerializer with the row-markdown table serializer."""

    def get_serializer(self, doc: DoclingDocument):
        serializer = ChunkingDocSerializer(doc=doc)
        serializer.table_serializer = RowMarkdownTableSerializer()
        return serializer


@dataclass
class ImageContext:
    """Image metadata with context windows for VLM description"""
    image_id: str
    file_id: str
    page: Optional[int]
    image_path: str
    caption: Optional[str]
    before_text: str  # N paragraphs before image
    after_text: str   # N paragraphs after image
    vlm_description: Optional[str] = None
    width_px: Optional[int] = None
    height_px: Optional[int] = None


@dataclass
class ChunkInfo:
    """Rich chunk data from HybridChunker, aligned with design doc §2.2"""
    text: str                    # Pure text (for embedding)
    markdown: str                # Markdown format (for RAG display, preserves tables)
    content_type: str            # heading / paragraph / table / figure / caption / list
    section_path: str            # e.g. "第三章 > 3.2 财务分析"
    page: Optional[int]          # Page number (if available)


@dataclass
class ParseResult:
    """Document parsing result"""
    text_chunks: List[ChunkInfo]
    images: List[ImageContext]
    metadata: dict  # filename, file_type, page_count, etc.


class DocumentParser:
    """
    Unified document parser using Docling.
    
    Supports: PDF, DOCX, PPTX, XLSX, HTML, Images
    Pipeline: Layout analysis → OCR → Table recognition → Image extraction
    
    Office conversion strategy: MS Office COM (Windows) → LibreOffice fallback
    """
    
    def __init__(self, models_dir: Optional[str] = None):
        """
        Initialize Docling converter with model paths.
        
        Args:
            models_dir: Path to models directory (auto-detected if None)
        """
        from knowledge.model_manager import get_models_dir
        
        if models_dir is None:
            models_dir = get_models_dir()
        
        self.models_dir = Path(models_dir)
        # Use module-level singleton converter (shared across all instances)
        self.converter = _get_docling_converter(self.models_dir)
        # Cache for tokenizer / chunker (lazily initialised in _extract_chunks)
        self._chunker = None
        self._tokenizer = None
    
    # _init_converter kept for backward compatibility but now delegates
    # to the module-level singleton so repeated calls are no-ops.
    def _init_converter(self):
        self.converter = _get_docling_converter(self.models_dir)
    
    def parse_file(self, file_path: str, file_id: str) -> ParseResult:
        """
        Parse document file through Docling pipeline.
        
        Args:
            file_path: Path to document file
            file_id: File ID from database
        
        Returns:
            ParseResult with chunks, images, metadata
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Office → PDF conversion if needed (Excel handled directly by Docling)
        original_path = file_path
        temp_pdf = None
        conversion_warnings: List[str] = []
        if file_path.suffix.lower() in ['.docx', '.pptx']:
            pdf_path, conv_warning = self._convert_office_to_pdf(file_path)
            if conv_warning:
                conversion_warnings.append(conv_warning)
            if pdf_path:
                temp_pdf = pdf_path
                file_path = pdf_path
        
        try:
            if self.converter is None:
                raise RuntimeError("Docling converter not initialized")
            
            # Parse with Docling (first call serialized — see helper docstring)
            conv_result = _convert_threadsafe(self.converter, str(file_path))
            doc = conv_result.document
            
            # Extract text chunks with HybridChunker
            text_chunks = self._extract_chunks(doc)
            
            # Extract images with context
            images = self._extract_images(doc, file_id, file_path.parent)
            
            # Build metadata
            metadata = {
                "filename": original_path.name,
                "file_type": original_path.suffix.lower(),
                "page_count": len(doc.pages) if hasattr(doc, 'pages') else 0,
            }
            if conversion_warnings:
                # 降级显式声明（0.4 §7.3 v3.6）：随成功结果上抛，pipeline 写入完成通知 detail
                metadata["warnings"] = conversion_warnings
            if not text_chunks:
                # 空内容显式声明（0.4 §7.3 v3.6）：空文件等场景 0 chunks 是
                # 成功结果，用户需被告知「没有提取到文本」而非只看到 0 chunks
                metadata.setdefault("warnings", []).append("未从文件中提取到文本内容")
            
            return ParseResult(
                text_chunks=text_chunks,
                images=images,
                metadata=metadata
            )
        finally:
            # Cleanup temporary PDF
            if temp_pdf and temp_pdf.exists():
                try:
                    temp_pdf.unlink()
                except Exception:
                    pass
    
    def _convert_office_to_pdf(self, file_path: Path) -> tuple[Optional[Path], str]:
        """
        Convert Office document to PDF.
        
        Strategy: MS Office COM (Windows) → LibreOffice fallback
        
        Returns:
            (pdf_path, warning)：warning 非空表示发生降级（回退内置解析器），文案面向
            用户、随解析结果上抛（pipeline → 完成通知 detail，0.4 §7.3 v3.6）。
        """
        from knowledge.model_manager import ModelManager
        
        status = ModelManager.check_office_converter()
        if not status["available"]:
            return None, (
                "未检测到 Microsoft Office 或 LibreOffice，已由内置解析器直接解析原文件，"
                "复杂版式（表格/图文）还原度可能降低"
            )
        
        output_dir = file_path.parent
        output_pdf = output_dir / f"{file_path.stem}.pdf"
        
        if status["tool"] == "ms-office":
            return self._convert_with_ms_office(file_path, output_pdf)
        elif status["tool"] == "libreoffice":
            return self._convert_with_libreoffice(file_path, output_pdf, status["path"])
        
        return None, "Office→PDF 转换器不可用，已由内置解析器直接解析原文件"
    
    @staticmethod
    def _com_call(fn, *args, **kwargs):
        """Invoke a COM method, retrying transient "callee busy" rejections.

        Office automation under heavy CPU load can reject calls with
        RPC_E_CALL_REJECTED / RPC_E_SERVERCALL_RETRYLATER; retry with short
        backoff instead of failing the whole conversion. Non-retryable
        com_error and any other exception propagate immediately.
        """
        import pywintypes

        last = None
        for attempt, delay in enumerate((0.0,) + _COM_RETRY_DELAYS):
            if attempt:
                time.sleep(delay)
            try:
                return fn(*args, **kwargs)
            except pywintypes.com_error as e:
                hresult = e.args[0] if e.args else None
                if hresult not in _COM_RETRY_HRESULTS:
                    raise
                last = e
        raise last

    def _convert_with_ms_office(self, input_path: Path, output_path: Path) -> tuple[Optional[Path], str]:
        """Convert using Microsoft Office COM automation.

        DispatchEx creates a dedicated Office instance so a document the user
        has open is never attached to (and quit by) the batch conversion;
        the finally-block guarantees document/app cleanup even on failure
        (no leaked WINWORD/POWERPNT/EXCEL processes).
        """
        try:
            import win32com.client
        except ImportError:
            return None, "pywin32 不可用，已由内置解析器直接解析原文件"

        ext = input_path.suffix.lower()
        # Only .docx/.pptx reach this method (parse_file converts those to PDF
        # for layout-faithful parsing; xlsx is parsed directly by Docling)
        prog_id = {
            '.docx': 'Word.Application',
            '.pptx': 'PowerPoint.Application',
        }.get(ext)
        if prog_id is None:
            return None, f"暂不支持 {ext} 格式的 MS Office 转换，已由内置解析器直接解析原文件"

        app = None
        doc = None  # Document / Presentation
        try:
            app = win32com.client.DispatchEx(prog_id)
            # Suppress modal dialogs (password/repair prompts, font substitution,
            # macro warnings...) that would block COM calls forever in a
            # headless batch run. Alert-level values differ per app:
            # Word=0 (wdAlertsNone), PowerPoint=1 (ppAlertsNone).
            try:
                if ext == '.pptx':
                    app.DisplayAlerts = 1
                else:
                    app.DisplayAlerts = 0
                    app.Visible = False
            except Exception:
                pass  # cosmetic only — conversion must not depend on it

            if ext == '.docx':
                # Word: FileFormat=17 (wdFormatPDF); AddToRecentFiles keeps the
                # user's recent-files list clean during batch conversion
                doc = self._com_call(
                    app.Documents.Open,
                    str(input_path.absolute()),
                    ReadOnly=False, AddToRecentFiles=False,
                )
                self._com_call(doc.SaveAs, str(output_path.absolute()), FileFormat=17)
            else:  # '.pptx'
                # PowerPoint: FileFormat=32 (ppSaveAsPDF); WithWindow=False
                # avoids a visible editor window flash (cannot hide the app)
                doc = self._com_call(
                    app.Presentations.Open,
                    str(input_path.absolute()), WithWindow=False,
                )
                self._com_call(doc.SaveAs, str(output_path.absolute()), FileFormat=32)

            if output_path.exists():
                return output_path, ""
            return None, "MS Office 转换未生成 PDF，已由内置解析器直接解析原文件"
        except Exception as e:
            logger.warning("MS Office conversion failed for %s: %s", input_path.name, e)
            return None, "MS Office 转换失败，已由内置解析器直接解析原文件"
        finally:
            if doc is not None:
                try:
                    doc.Close(SaveChanges=0)
                except Exception:
                    pass
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
    
    def _convert_with_libreoffice(self, input_path: Path, output_path: Path, soffice_path: str) -> tuple[Optional[Path], str]:
        """Convert using LibreOffice command-line"""
        try:
            cmd = [
                soffice_path,
                '--headless',
                '--convert-to', 'pdf',
                '--outdir', str(input_path.parent),
                str(input_path.absolute()),
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            stderr_text = result.stderr.decode('utf-8', errors='replace') if result.stderr else ''
            
            # LibreOffice may return 0 even on failure — check stderr
            if result.returncode == 0 and 'Error' not in stderr_text and 'could not be loaded' not in stderr_text.lower():
                libreoffice_pdf = input_path.parent / f"{input_path.stem}.pdf"
                if libreoffice_pdf.exists():
                    libreoffice_pdf.rename(output_path)
                    return output_path, ""
            
            if stderr_text:
                logger.warning("LibreOffice stderr: %s", stderr_text.strip())
            return None, "LibreOffice 转换失败，已由内置解析器直接解析原文件"
        except Exception as e:
            logger.warning("LibreOffice conversion failed: %s", e)
            return None, "LibreOffice 转换失败，已由内置解析器直接解析原文件"
    
    def _extract_chunks(self, doc) -> List[ChunkInfo]:
        """Extract text chunks from Docling document using HybridChunker.
        
        Uses HuggingFaceTokenizer wrapper per design doc §3 chunker.py.
        Returns rich ChunkInfo with markdown, content_type, section_path, page.
        
        Tokenizer and chunker are cached on the instance so they are only
        created once per DocumentParser (and the underlying model files are
        loaded from disk only on the first call).
        """
        from docling.chunking import HybridChunker
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
        
        # Lazily create tokenizer + chunker (cached on self)
        if self._chunker is None:
            # Resolve bge-m3 tokenizer path
            if self.models_dir:
                tokenizer_path = str(Path(self.models_dir) / "bge-m3")
            else:
                tokenizer_path = "BAAI/bge-m3"
            
            self._tokenizer = HuggingFaceTokenizer.from_pretrained(
                model_name=tokenizer_path,
                max_tokens=1024,
            )
            self._chunker = HybridChunker(
                tokenizer=self._tokenizer,
                merge_peers=True,
                repeat_table_header=True,   # Repeat table headers in split chunks
                serializer_provider=RowMarkdownSerializerProvider(),
            )
        
        chunker = self._chunker
        
        results = []
        for chunk in chunker.chunk(doc):
            if not chunk.text or not chunk.text.strip():
                continue
            
            # Get contextualized text for embedding
            contextualized = chunker.contextualize(chunk)
            
            # Get markdown representation from doc_items
            md = self._build_markdown_from_doc_items(chunk, doc)
            
            # Derive content_type from doc_items (design doc §2.2)
            content_type = self._infer_content_type_from_items(chunk)
            
            # Derive section_path from headings
            headings = []
            if hasattr(chunk, 'meta') and hasattr(chunk.meta, 'headings') and chunk.meta.headings:
                headings = list(chunk.meta.headings)
            section_path = " > ".join(headings) if headings else ""
            
            # Try to get page number from chunk.meta.doc_items → prov
            page = self._extract_page_from_chunk(chunk)
            
            results.append(ChunkInfo(
                text=contextualized or chunk.text,
                markdown=md,
                content_type=content_type,
                section_path=section_path,
                page=page,
            ))
        
        return results
    
    def _extract_page_from_chunk(self, chunk) -> Optional[int]:
        """Extract page number from HybridChunker chunk.
        
        Page info is in chunk.meta.doc_items[].prov[].page_no
        """
        if not hasattr(chunk, 'meta'):
            return None
        
        doc_items = getattr(chunk.meta, 'doc_items', None)
        if not doc_items:
            return None
        
        for doc_item in doc_items:
            prov_list = getattr(doc_item, 'prov', None)
            if not prov_list:
                continue
            for prov in prov_list:
                page_no = getattr(prov, 'page_no', None)
                if page_no is not None:
                    return page_no
        return None
    
    def _infer_content_type_from_items(self, chunk) -> str:
        """Infer content_type from chunk.meta.doc_items per design doc §2.2.
        
        Uses item.label to determine type: title/section_header→heading, table→table, 
        picture→figure, caption→caption, list→list, text→paragraph
        """
        doc_items = None
        if hasattr(chunk, 'meta') and hasattr(chunk.meta, 'doc_items'):
            doc_items = chunk.meta.doc_items
        if not doc_items:
            return "paragraph"
        
        # Collect labels from all items
        labels = set()
        for item in doc_items:
            label = getattr(item, 'label', None)
            if label:
                labels.add(str(label).lower())
        
        # Priority mapping
        if 'title' in labels or 'section_header' in labels:
            return 'heading'
        if 'table' in labels:
            return 'table'
        if 'picture' in labels:
            return 'figure'
        if 'caption' in labels:
            return 'caption'
        if 'list' in labels:
            return 'list'
        
        return "paragraph"
    
    def _extract_images(self, doc, file_id: str, output_dir: Path) -> List[ImageContext]:
        """Extract images with context windows.
        
        Docling stores content at document level:
        - doc.pictures: list of PictureItem
        - doc.texts: list of text items
        Each item has prov (provenance) with page_no.
        """
        images = []
        
        if not hasattr(doc, 'pictures'):
            return images
        
        # Build a list of all items in document order for context lookup
        all_items = []
        if hasattr(doc, 'texts'):
            all_items.extend(doc.texts)
        if hasattr(doc, 'pictures'):
            all_items.extend(doc.pictures)
        if hasattr(doc, 'tables'):
            all_items.extend(doc.tables)
        
        # Sort by page number only (stable sort preserves order within page)
        def get_page(item):
            prov = getattr(item, 'prov', None)
            if prov:
                return prov[0].page_no
            return 999999
        all_items.sort(key=get_page)
        
        for img_idx, picture in enumerate(doc.pictures):
            # Get page number from provenance
            page_no = None
            if hasattr(picture, 'prov') and picture.prov:
                page_no = picture.prov[0].page_no
            
            # Save the image
            img_filename = f"page_{page_no or 0}_img_{img_idx}.png"
            img_path = output_dir / img_filename
            
            image_saved = False
            try:
                if hasattr(picture, 'image') and picture.image:
                    img_ref = picture.image
                    uri = getattr(img_ref, 'uri', None)
                    if uri and 'base64' in str(uri):
                        import base64
                        b64_data = str(uri).split(',', 1)[1]
                        img_bytes = base64.b64decode(b64_data)
                        img_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(img_path, 'wb') as f:
                            f.write(img_bytes)
                        image_saved = True
            except Exception as e:
                logger.warning("Failed to extract image: %s", e)
            
            if not image_saved:
                continue
            
            # Read image dimensions
            img_width, img_height = None, None
            try:
                from PIL import Image
                with Image.open(str(img_path)) as im:
                    img_width, img_height = im.size
            except Exception:
                pass  # Dimensions optional, will be read later if needed
            
            # Get context: find nearby text items on the same page
            before_text, after_text = self._get_text_context(picture, all_items, n_paragraphs=2)
            
            # Get caption if available
            caption = None
            if hasattr(picture, 'captions') and picture.captions:
                caption = picture.captions[0].text if hasattr(picture.captions[0], 'text') else str(picture.captions[0])
            
            img_ctx = ImageContext(
                image_id=f"img_{file_id}_{img_idx}",
                file_id=file_id,
                page=page_no,
                image_path=str(img_path),
                caption=caption,
                before_text=before_text,
                after_text=after_text,
                width_px=img_width,
                height_px=img_height,
            )
            images.append(img_ctx)
        
        return images
    
    def _get_text_context(self, target_item, all_items: list, n_paragraphs: int) -> tuple:
        """Get before/after text context for an item."""
        # Find target position in all_items
        target_idx = None
        target_page = None
        if hasattr(target_item, 'prov') and target_item.prov:
            target_page = target_item.prov[0].page_no
        
        for i, item in enumerate(all_items):
            if item is target_item:
                target_idx = i
                break
        
        if target_idx is None:
            return "", ""
        
        # Get before text (same page)
        before_texts = []
        for i in range(target_idx - 1, -1, -1):
            item = all_items[i]
            # Check same page
            if hasattr(item, 'prov') and item.prov:
                if item.prov[0].page_no != target_page:
                    break
            if hasattr(item, 'text') and item.text.strip():
                before_texts.insert(0, item.text.strip())
                if len(before_texts) >= n_paragraphs:
                    break
        
        # Get after text (same page)
        after_texts = []
        for i in range(target_idx + 1, len(all_items)):
            item = all_items[i]
            if hasattr(item, 'prov') and item.prov:
                if item.prov[0].page_no != target_page:
                    break
            if hasattr(item, 'text') and item.text.strip():
                after_texts.append(item.text.strip())
                if len(after_texts) >= n_paragraphs:
                    break
        
        return "\n".join(before_texts), "\n".join(after_texts)
    
    def _build_markdown_from_doc_items(self, chunk, doc=None) -> str:
        """Build markdown from chunk's doc_items.
        
        Uses item.label to determine type. For tables, uses item.self_ref to
        look up the actual TableItem from doc.tables for proper markdown export.
        """
        if not hasattr(chunk, 'meta') or not hasattr(chunk.meta, 'doc_items'):
            return chunk.text
        
        doc_items = chunk.meta.doc_items
        if not doc_items:
            return chunk.text
        
        # Build self_ref → table mapping for quick lookup
        table_map = {}
        if doc and hasattr(doc, 'tables'):
            for table in doc.tables:
                ref = getattr(table, 'self_ref', None)
                if ref:
                    table_map[ref] = table
        
        parts = []
        for item in doc_items:
            label = str(getattr(item, 'label', '')).lower()
            
            if label == 'table':
                # Look up actual TableItem by self_ref
                item_ref = getattr(item, 'self_ref', None)
                table = table_map.get(item_ref) if item_ref else None
                
                if table and hasattr(table, 'export_to_markdown'):
                    try:
                        parts.append(table.export_to_markdown(doc))
                        continue
                    except Exception:
                        pass
                # Fallback: use item text
                if hasattr(item, 'text') and item.text:
                    parts.append(item.text)
            
            else:
                # text, section_header, title, etc.
                if hasattr(item, 'text') and item.text:
                    parts.append(item.text)
        
        return "\n\n".join(parts) if parts else chunk.text
