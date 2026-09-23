"""python/knowledge/doc_parser.py"""
import os
import hashlib
import threading
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field
import tempfile
import subprocess


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

            pipeline_options = PdfPipelineOptions()
            pipeline_options.artifacts_path = str(models_dir)
            pipeline_options.do_ocr = True
            pipeline_options.do_table_structure = True
            if pipeline_options.do_table_structure:
                pipeline_options.table_structure_options = TableStructureV2Options()
            pipeline_options.generate_picture_images = True
            pipeline_options.generate_page_images = False

            _docling_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                    InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
                }
            )
            import sys
            print(f"[Docling] Converter loaded (artifacts: {models_dir})", file=sys.stderr)
        except ImportError as e:
            print(f"[WARN] Docling not available: {e}")
            _docling_converter = None
    return _docling_converter


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
    markdown_content: str
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
        if file_path.suffix.lower() in ['.docx', '.pptx']:
            pdf_path = self._convert_office_to_pdf(file_path)
            if pdf_path:
                temp_pdf = pdf_path
                file_path = pdf_path
        
        try:
            if self.converter is None:
                raise RuntimeError("Docling converter not initialized")
            
            # Parse with Docling
            conv_result = self.converter.convert(str(file_path))
            doc = conv_result.document
            
            # Extract text chunks with HybridChunker
            text_chunks = self._extract_chunks(doc)
            
            # Extract images with context
            images = self._extract_images(doc, file_id, file_path.parent)
            
            # Generate markdown
            markdown_content = doc.export_to_markdown()
            
            # Build metadata
            metadata = {
                "filename": original_path.name,
                "file_type": original_path.suffix.lower(),
                "page_count": len(doc.pages) if hasattr(doc, 'pages') else 0,
            }
            
            return ParseResult(
                text_chunks=text_chunks,
                markdown_content=markdown_content,
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
    
    def _convert_office_to_pdf(self, file_path: Path) -> Optional[Path]:
        """
        Convert Office document to PDF.
        
        Strategy: MS Office COM (Windows) → LibreOffice fallback
        """
        from knowledge.model_manager import ModelManager
        
        status = ModelManager.check_office_converter()
        if not status["available"]:
            return None
        
        output_dir = file_path.parent
        output_pdf = output_dir / f"{file_path.stem}.pdf"
        
        if status["tool"] == "ms-office":
            return self._convert_with_ms_office(file_path, output_pdf)
        elif status["tool"] == "libreoffice":
            return self._convert_with_libreoffice(file_path, output_pdf, status["path"])
        
        return None
    
    def _convert_with_ms_office(self, input_path: Path, output_path: Path) -> Optional[Path]:
        """Convert using Microsoft Office COM automation"""
        try:
            import win32com.client
            
            ext = input_path.suffix.lower()
            
            if ext == '.docx':
                # Word: FileFormat=17 (wdFormatPDF)
                app = win32com.client.Dispatch('Word.Application')
                app.Visible = False
                doc = app.Documents.Open(str(input_path.absolute()))
                doc.SaveAs(str(output_path.absolute()), FileFormat=17)
                doc.Close()
                app.Quit()
                
            elif ext == '.pptx':
                # PowerPoint: FileFormat=32 (ppSaveAsPDF)
                app = win32com.client.Dispatch('PowerPoint.Application')
                # PowerPoint doesn't allow hiding the window
                pres = app.Presentations.Open(str(input_path.absolute()))
                pres.SaveAs(str(output_path.absolute()), FileFormat=32)
                pres.Close()
                app.Quit()
                
            elif ext == '.xlsx':
                # Excel: use ExportAsFixedFormat
                app = win32com.client.Dispatch('Excel.Application')
                app.Visible = False
                wb = app.Workbooks.Open(str(input_path.absolute()))
                wb.ExportAsFixedFormat(0, str(output_path.absolute()))  # 0 = xlTypePDF
                wb.Close()
                app.Quit()
            else:
                return None
            
            return output_path if output_path.exists() else None
        except Exception as e:
            print(f"[MS Office conversion failed: {e}]")
            return None
    
    def _convert_with_libreoffice(self, input_path: Path, output_path: Path, soffice_path: str) -> Optional[Path]:
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
                    return output_path
            
            if stderr_text:
                print(f"[LibreOffice stderr: {stderr_text.strip()}]")
            return None
        except Exception as e:
            print(f"[LibreOffice conversion failed: {e}]")
            return None
    
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
                max_tokens=512,
            )
            self._chunker = HybridChunker(
                tokenizer=self._tokenizer,
                merge_peers=True,
                repeat_table_header=True,   # Repeat table headers in split chunks
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
                print(f"[WARN] Failed to extract image: {e}")
            
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
