"""python/knowledge/pipeline.py"""
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime

from knowledge.doc_parser import DocumentParser, ParseResult, ImageContext, ChunkInfo
from knowledge.vector_store import VectorStore
from knowledge.fts_store import FTSStore
from knowledge.vlm_client import VLMClient
import activity_events


class ProcessingPipeline:
    """
    Document processing pipeline orchestrator.
    
    Pipeline stages:
    1. Save uploaded file to storage
    2. Parse document (Docling) → text chunks + images
    3. Chunk text (HybridChunker aligned with bge-m3)
    4. Embed chunks (bge-m3) → store in ChromaDB
    5. Index chunks for keyword search → SQLite FTS5
    6. Describe images (VLM API) → update ImageContext
    7. Update file status in database
    
    Supports async execution with progress tracked via database.
    """
    
    def __init__(
        self,
        db_conn,
        chroma_persist_dir: str,
        models_dir: Optional[str] = None,
        vlm_api_url: Optional[str] = None,
        vlm_api_key: Optional[str] = None,
        vlm_model: str = "glm-4v"
    ):
        """
        Initialize pipeline with all required components.
        
        Args:
            db_conn: Database connection (for FTS store and status updates)
            chroma_persist_dir: ChromaDB persistence directory
            models_dir: Path to models directory (auto-detected if None)
            vlm_api_url: VLM API endpoint
            vlm_api_key: VLM API key
            vlm_model: VLM model name
        """
        self.db = db_conn
        
        # Initialize components
        self.parser = DocumentParser(models_dir=models_dir)
        
        # Construct bge-m3 model path
        from pathlib import Path
        bge_m3_path = Path(models_dir) / "bge-m3"
        self.vector_store = VectorStore(chroma_persist_dir, bge_m3_path=str(bge_m3_path))
        
        self.fts_store = FTSStore(db_conn)
        self.vlm_client = VLMClient(
            api_url=vlm_api_url,
            api_key=vlm_api_key,
            model=vlm_model
        )
    
    async def process_file(
        self,
        file_path: str,
        project_id: str,
        file_id: str,
        filename: str
    ) -> dict:
        """
        Process a single document file through the parsing pipeline.
        
        Stages:
        1. Parse document (Docling) → text chunks + images
        2. Store chunks in ChromaDB + FTS
        3. Store image records in SQLite (status=pending, VLM handled by worker)
        4. Finalize
        
        Image VLM description is NOT done here — it's handled asynchronously
        by the image description worker in task_queue.py.
        """
        try:
            # Stage 1: Parse document via Docling (0% → 25%)
            # This is the longest step (layout analysis + OCR + table recognition)
            self._set_progress(file_id, 2)
            parse_result = await asyncio.to_thread(
                self.parser.parse_file, file_path, file_id
            )
            self._set_progress(file_id, 25)
            
            # Stage 2: Store chunks in ChromaDB + FTS (25% → 75%)
            # Embedding via bge-m3 + FTS indexing
            self._set_progress(file_id, 30)
            await asyncio.to_thread(
                self._store_chunks, parse_result, project_id, file_id, filename
            )
            self._set_progress(file_id, 75)
            
            # Stage 3: Store image records in DB (75% → 90%)
            if parse_result.images:
                self._set_progress(file_id, 80)
                self._store_image_records(parse_result.images, file_id)
            self._set_progress(file_id, 90)
            
            # Stage 4: Finalize (90% → 100%)
            self._update_file_status(file_id, "done", {
                "chunks_count": len(parse_result.text_chunks),
                "images_count": len(parse_result.images),
                "completed_at": datetime.now().isoformat()
            })
            # 终态事件显式声明（E2，0.4 §6.4）：完成通知由框架写入
            activity_events.complete_file(self.db, file_id)
            self._set_progress(file_id, 100)
            
            return {
                "success": True,
                "chunks_count": len(parse_result.text_chunks),
                "images_count": len(parse_result.images),
                "error": None
            }
        
        except Exception as e:
            self._set_progress(file_id, 0)
            self._update_file_status(file_id, "error", {"error_message": str(e)})
            # 终态事件显式声明（E2）：与 task_queue 外层兑底双写，未读查重保证幂等（0.4 §3.2）
            activity_events.fail_file(self.db, file_id, str(e))
            
            return {
                "success": False,
                "chunks_count": 0,
                "images_count": 0,
                "error": str(e)
            }
    
    def _store_chunks(
        self,
        parse_result: ParseResult,
        project_id: str,
        file_id: str,
        filename: str
    ):
        """
        Store text chunks in ChromaDB and FTS index.
        
        Uses rich ChunkInfo data from parser (design doc §2.2):
        - content: contextualized text (for embedding)
        - markdown_content: Markdown format (for RAG display, preserves tables)
        - content_type: derived from doc_items (heading/table/figure/paragraph/list)
        - section_path: heading hierarchy path
        - page: page number
        
        Args:
            parse_result: Parsed document result with ChunkInfo list
            project_id: Project ID
            file_id: File ID
            filename: Original filename
        """
        fts_chunks = []
        chunk_ids = []
        documents = []
        metadatas = []
        
        for idx, chunk_info in enumerate(parse_result.text_chunks):
            chunk_id = f"chunk_{file_id}_{idx}"
            
            # ChromaDB metadata (design doc §2.2)
            meta = {
                "file_id": file_id,
                "filename": filename,
                "content_type": chunk_info.content_type,
                "chunk_index": idx,
                "markdown_content": chunk_info.markdown,
                "section_path": chunk_info.section_path,
            }
            if chunk_info.page is not None:
                meta["page"] = chunk_info.page
            
            fts_chunks.append({
                "chunk_id": chunk_id,
                "content": chunk_info.text,
                "project_id": project_id,
                "filename": filename,
                "content_type": chunk_info.content_type,
            })
            
            chunk_ids.append(chunk_id)
            documents.append(chunk_info.text)
            metadatas.append(meta)
        
        # Add to ChromaDB (let it compute embeddings via bge-m3)
        self.vector_store.add_documents(
            project_id=project_id,
            ids=chunk_ids,
            documents=documents,
            metadatas=metadatas
        )
        
        # Add to FTS index
        self.fts_store.add_chunks_batch(fts_chunks)
    
    def _store_image_records(self, images: list[ImageContext], file_id: str):
        """
        Store image metadata in SQLite images table with status='pending'.
        
        VLM description is handled asynchronously by the image description
        worker in task_queue.py, not here.
        """
        for img_ctx in images:
            try:
                if hasattr(self.db, 'create_image'):
                    context_text = ""
                    if img_ctx.before_text:
                        context_text += f"[上文] {img_ctx.before_text}\n"
                    if img_ctx.after_text:
                        context_text += f"[下文] {img_ctx.after_text}"
                    
                    self.db.create_image(
                        file_id=file_id,
                        local_path=img_ctx.image_path,
                        page_no=img_ctx.page,
                        caption=img_ctx.caption,
                        width_px=img_ctx.width_px,
                        height_px=img_ctx.height_px,
                        vlm_description=None,  # Filled by image worker
                        context_text=context_text.strip() if context_text else None,
                    )
            except Exception as e:
                print(f"[WARN] Failed to save image metadata to SQLite: {e}")
    
    def _update_file_status(
        self,
        file_id: str,
        status: str,
        metadata: dict
    ):
        """
        Update file processing status in database.
        
        Args:
            file_id: File ID
            status: Status string (pending/parsing/done/error)
            metadata: Additional metadata (chunks_count, images_count, error_message)
        """
        try:
            # Use the Database helper which knows the actual schema
            if hasattr(self.db, 'update_file_status'):
                self.db.update_file_status(
                    file_id=file_id,
                    status=status,
                    error_message=metadata.get("error_message"),
                    chunk_count=metadata.get("chunks_count"),
                    image_count=metadata.get("images_count"),
                )
            else:
                # Fallback: raw SQL with only known columns
                self.db.conn.execute(
                    "UPDATE files SET status = ? WHERE id = ?",
                    (status, file_id)
                )
                self.db.conn.commit()
        except Exception as e:
            print(f"[WARN] Failed to update file status: {e}")

    def _set_progress(self, file_id: str, progress: int):
        """Write progress (0-100) to database."""
        try:
            if hasattr(self.db, 'update_file_progress'):
                self.db.update_file_progress(file_id, progress)
        except Exception as e:
            print(f"[WARN] Failed to update progress: {e}")
    
    async def process_batch(
        self,
        files: list[dict],
        project_id: str
    ) -> list[dict]:
        """
        Process multiple files in parallel.
        
        Args:
            files: List of dicts with keys:
                - file_path: Path to file
                - file_id: File ID from database
                - filename: Original filename
            project_id: Project ID
        
        Returns:
            List of processing results (same order as input)
        """
        tasks = [
            self.process_file(f["file_path"], project_id, f["file_id"], f["filename"])
            for f in files
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exceptions
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append({
                    "success": False,
                    "chunks_count": 0,
                    "images_count": 0,
                    "error": str(result)
                })
            else:
                processed_results.append(result)
        
        return processed_results
