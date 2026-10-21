"""python/knowledge/doc_parser.py"""
import logging
import os
import hashlib
import threading
import time
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field
import tempfile
import subprocess

from knowledge.office_media import extract_office_images

logger = logging.getLogger(__name__)

# COM hresults meaning "callee busy, retry shortly". Office automation under
# heavy CPU load (e.g. concurrent embedding inference) transiently rejects
# calls with these even though the app is healthy; the COM-prescribed remedy
# is backoff-and-retry. Incident 2026-10-07: a docx conversion failed with
# "Open.Close" while embedding saturated the CPU; a manual rerun succeeded.
_COM_RETRY_HRESULTS = {-2147418111, -2147417846}  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER
_COM_RETRY_DELAYS = (0.5, 1.0, 2.0)


# ---------------------------------------------------------------------------
# Module-level singletons: Docling DocumentConverters are shared across ALL
# DocumentParser instances so its models (layout, table, OCR) are loaded
# exactly once per process and per options-set.
#
# Two converters by design（Office 图片无损改造，2.2 §4.4）：
# - pictures=True : PDF 带图裁剪 + 独立图片输入，服务 PDF/HTML/XLSX 等格式；
# - pictures=False: 仅服务 DOCX/PPTX 转 PDF 产物——图片改由原生 zip 提取
#   （office_media），Docling 不再生成有损裁剪图。
# docling 2.127 的 pipeline 缓存键为 (pipeline_class, options_hash)，两组
# options 各自缓存 pipeline 同进程共存；独立 converter 实例避免运行时切换
# 共享 format_to_options[PDF] 槽位的并发竞态（文件并发 2）。
# 无图 converter 仅在首个 DOCX/PPTX 解析时才加载模型（不上传 Office 文档零开销）。
# ---------------------------------------------------------------------------
_docling_converters: dict[bool, "_ThreadsafeConverter"] = {}
_docling_converter_lock = threading.Lock()


def _get_docling_converter(models_dir: Path, generate_picture_images: bool = True):
    """Return the singleton _ThreadsafeConverter for the given options set."""
    wrapper = _docling_converters.get(generate_picture_images)
    if wrapper is not None:
        return wrapper
    with _docling_converter_lock:
        wrapper = _docling_converters.get(generate_picture_images)
        if wrapper is not None:
            return wrapper
        try:
            from docling.document_converter import (
                DocumentConverter, PdfFormatOption, ImageFormatOption,
            )
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions, TableStructureV2Options,
            )

            def _build_pipeline_options(with_pictures: bool):
                opts = PdfPipelineOptions()
                opts.artifacts_path = str(models_dir)
                opts.do_ocr = True
                opts.do_table_structure = True
                if opts.do_table_structure:
                    opts.table_structure_options = TableStructureV2Options()
                opts.generate_picture_images = with_pictures
                opts.generate_page_images = False
                return opts

            if generate_picture_images:
                format_options = {
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
            else:
                # 无图 converter 仅注册 PDF：只接 DOCX/PPTX 的转 PDF 产物。
                # 降级路径（无 Office 转换器）直喂 DOCX/PPTX 时走 docling 默认
                # 原生 backend，与图片无关（图片由原生提取器负责）。
                format_options = {
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=_build_pipeline_options(False)
                    ),
                }
            wrapper = _ThreadsafeConverter(DocumentConverter(format_options=format_options))
            _docling_converters[generate_picture_images] = wrapper
            logger.info(
                "Docling converter loaded (artifacts: %s, pictures: %s)",
                models_dir, generate_picture_images,
            )
        except ImportError as e:
            logger.warning("Docling not available: %s", e)
            wrapper = None
    return wrapper


class _ThreadsafeConverter:
    """Wrap a DocumentConverter, serializing only its first (lazy-init) call.

    Docling loads its pipeline models lazily inside the FIRST convert() call
    (unlocked internal caches + model init on that path), so two workers
    converting concurrently at startup can crash each other. Steady-state
    convert() is PyTorch/ONNX inference and is thread-safe, so after the first
    call completes the lock is never taken again (zero steady-state cost).
    The lock is per converter: the two singletons initialize their pipelines
    independently, so a shared warmed flag would leave the second converter's
    lazy init unprotected.
    """

    def __init__(self, converter):
        self._converter = converter
        self._first_convert_lock = threading.Lock()
        self._warmed = False

    def convert(self, file_path: str):
        if self._warmed:
            return self._converter.convert(file_path)
        with self._first_convert_lock:
            if not self._warmed:
                try:
                    return self._converter.convert(file_path)
                finally:
                    # Mark warmed even on failure: init state is already mutated;
                    # retrying the racy init path would only re-open the race
                    self._warmed = True
        return self._converter.convert(file_path)


@dataclass
class ImageContext:
    """Image metadata with context windows for LLM description"""
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
        # Module-level singleton converters (shared across all instances):
        # 带图（PDF/HTML/XLSX/独立图片）与无图（仅 DOCX/PPTX 产物 PDF，图片由
        # office_media 原生无损提取）各一，见 _get_docling_converter 注释
        self.converter = _get_docling_converter(
            self.models_dir, generate_picture_images=True
        )
        self._office_converter = _get_docling_converter(
            self.models_dir, generate_picture_images=False
        )
        # Cache for tokenizer / chunker (lazily initialised in _extract_chunks)
        self._chunker = None
        self._tokenizer = None
    
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
        # 图片无损改造（2.2 §4.4）：DOCX/PPTX 的 PDF 仅服务文字/版面，图片由
        # office_media 从原始 zip 包提取，Docling 转换产物不再生成裁剪图
        is_office = file_path.suffix.lower() in ['.docx', '.pptx']
        original_path = file_path
        temp_pdf = None
        conversion_warnings: List[str] = []
        if is_office:
            pdf_path, conv_warning = self._convert_office_to_pdf(file_path)
            if conv_warning:
                conversion_warnings.append(conv_warning)
            if pdf_path:
                temp_pdf = pdf_path
                file_path = pdf_path
        
        try:
            if self.converter is None:
                raise RuntimeError("Docling converter not initialized")
            
            # Parse with Docling (first call serialized — see _ThreadsafeConverter)
            # 按源格式分发而非转换产物后缀：file_path 此时已是 temp_pdf，
            # 后缀恒为 .pdf，若按后缀分发则无图单例在主路径永远脱靶
            conv_result = (
                self._office_converter if is_office else self.converter
            ).convert(str(file_path))
            doc = conv_result.document
            
            # Extract text chunks with HybridChunker
            text_chunks = self._extract_chunks(doc)
            
            # Extract images with context
            # DOCX/PPTX：原生无损提取；失败声明 0 图 + warning（不重新带图解析，
            # 避免双倍解析成本），文件照常 done；其他格式维持版面裁剪提取
            if is_office:
                try:
                    images = extract_office_images(original_path, file_id, file_path.parent)
                except Exception as e:
                    logger.error(
                        "Native image extraction failed for %s: %s",
                        original_path.name, e, exc_info=True,
                    )
                    conversion_warnings.append(
                        f"图片无损提取失败({e.__class__.__name__})，本次解析不含图片"
                    )
                    images = []
            else:
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
        # 懒加载：序列化器类定义需要 docling_core 基类（~6s 的
        # docling_core → transformers → torch 链），拆到独立子模块后仅
        # 首次解析文档时付出该代价（冷启动优化，2.2 §4.3）
        from knowledge.docling_serializers import RowMarkdownSerializerProvider
        
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
