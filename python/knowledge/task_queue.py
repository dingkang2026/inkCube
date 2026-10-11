"""python/knowledge/task_queue.py

Async worker infrastructure for knowledge base processing.

Two worker pools:
1. File parsing workers: process documents (parse → chunk → embed → store → extract images)
2. Image description workers: process images (VLM API → store description)

Each pool has configurable concurrency via asyncio.Semaphore.
A queue scanner periodically picks up pending items from the database.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from knowledge.pipeline import ProcessingPipeline
from knowledge.vlm_client import VLMClient
from knowledge.vector_store import VectorStore
from errors import InkCubeError, FileNotFound, actionable
import activity_events

logger = logging.getLogger(__name__)


class KnowledgeWorker:
    """
    Manages async file parsing and image description workers.

    Lifecycle:
        worker = KnowledgeWorker(db, data_dir, ...)
        await worker.start()   # call from FastAPI lifespan startup
        ...
        await worker.stop()    # call from FastAPI lifespan shutdown
    """

    def __init__(
        self,
        db,
        data_dir: str,
        max_file_concurrency: int = 2,
        max_image_concurrency: int = 2,
        max_compile_concurrency: int = 2,
        scan_initial_delay: float = 3.0,
        scan_interval: float = 5.0,
    ):
        self.db = db
        self.data_dir = data_dir
        self._file_sem = asyncio.Semaphore(max_file_concurrency)
        self._image_sem = asyncio.Semaphore(max_image_concurrency)
        self._compile_sem = asyncio.Semaphore(max_compile_concurrency)
        self._file_queue: asyncio.Queue[str] = asyncio.Queue()
        self._image_queue: asyncio.Queue[tuple] = asyncio.Queue()
        self._compile_queue: asyncio.Queue[str] = asyncio.Queue()
        # 暂停闸门：set=运行，clear=暂停。暂停 = 不再开始新任务（worker 在闸门处
        # 挂起，任务留在内存队列等恢复）；在飞任务（已过闸门的）自然跑完。
        # 持久化在 settings（parsing_paused 等键，经 /knowledge/queue/pause 写入），
        # 重启后由 main.py lifespan 恢复。基础库与项目共用同一队列，天然同暂停。
        self._file_run = asyncio.Event()
        self._file_run.set()
        self._image_run = asyncio.Event()
        self._image_run.set()
        # 三队列统一防重：track 已入队条目（出队时 discard）。QueueScanner 周期
        # 全量补投递 pending 条目，无防重时积压期间同一 pending 每轮重复入队
        # （事故 2026-10-09/10：图片积压时单张被入队 40+ 次，叠加出队守卫放行
        # archived，seal 类图片被真实重复调用 VLM 45 次）。compile 队列原本就有。
        self._compile_enqueued: set[str] = set()  # track task_ids already in queue to prevent duplicates
        self._file_enqueued: set[str] = set()
        self._image_enqueued: set[str] = set()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._max_file_concurrency = max_file_concurrency
        self._max_image_concurrency = max_image_concurrency
        self._max_compile_concurrency = max_compile_concurrency
        # 扫描节奏可注入（测试用）；生产默认 3s 启动延迟 + 5s 轮询
        self._scan_initial_delay = scan_initial_delay
        self._scan_interval = scan_interval

        # Shared VectorStore instance – avoids creating a new one (and
        # potentially triggering a separate model load) per image.
        self._vector_store: Optional[VectorStore] = None

    def _get_vector_store(self) -> VectorStore:
        """Return a lazily-initialised, shared VectorStore."""
        if self._vector_store is None:
            self._vector_store = VectorStore(
                str(Path(self.data_dir) / "chroma"),
                bge_m3_path=str(Path(self.data_dir) / "models" / "bge-m3"),
            )
        return self._vector_store

    # ── Public API ──

    def update_concurrency(self, file_concurrency: int, image_concurrency: int, compile_concurrency: int | None = None):
        """Update concurrency limits (takes effect on next task acquisition)."""
        self._file_sem = asyncio.Semaphore(file_concurrency)
        self._image_sem = asyncio.Semaphore(image_concurrency)
        if compile_concurrency is not None:
            self._compile_sem = asyncio.Semaphore(compile_concurrency)

    def set_file_paused(self, paused: bool):
        if paused:
            self._file_run.clear()
        else:
            self._file_run.set()
        logger.info("[KnowledgeWorker] File parsing %s", "paused" if paused else "resumed")

    def set_image_paused(self, paused: bool):
        if paused:
            self._image_run.clear()
        else:
            self._image_run.set()
        logger.info("[KnowledgeWorker] Image description %s", "paused" if paused else "resumed")

    # Wiki 编译不设队列暂停（0.4 §6.7 修订）：其任务级「停止」（协作式，stopped
    # 终态）+ ingest_summaries 断点续传已覆盖暂停的全部用户价值（效果等价、恢复
    # 成本相同），双出口只会增加语义混淆。

    async def start(self):
        """Start all workers. Call from FastAPI lifespan."""
        self._reset_interrupted_items()
        self._running = True
        for i in range(self._max_file_concurrency):
            self._tasks.append(
                asyncio.create_task(self._file_worker(f"file-w-{i}"))
            )
        for i in range(self._max_image_concurrency):
            self._tasks.append(
                asyncio.create_task(self._image_worker(f"img-w-{i}"))
            )
        for i in range(self._max_compile_concurrency):
            self._tasks.append(
                asyncio.create_task(self._compile_worker(f"compile-w-{i}"))
            )
        self._tasks.append(asyncio.create_task(self._queue_scanner()))
        logger.info(
            "[KnowledgeWorker] Started: %d file, %d image, %d compile workers",
            self._max_file_concurrency, self._max_image_concurrency,
            self._max_compile_concurrency,
        )

    async def stop(self):
        """Stop all workers gracefully."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # 清空防重集合：防止 stop→start 复用实例时残留条目挡住新投递
        self._file_enqueued.clear()
        self._image_enqueued.clear()
        self._compile_enqueued.clear()
        logger.info("[KnowledgeWorker] Stopped")

    async def enqueue_file(self, file_id: str):
        """Add a file to the parsing queue (skip if already enqueued)."""
        if file_id in self._file_enqueued:
            logger.debug("File %s already in queue, skipping duplicate", file_id)
            return
        self._file_enqueued.add(file_id)
        logger.debug("Enqueue file: %s", file_id)
        await self._file_queue.put(file_id)

    async def enqueue_image(self, image_id: str, force: bool = False):
        """Add an image to the description queue (skip if already enqueued).
        
        Args:
            force: If True, skip small-image auto-archive (user manual trigger);
                bypasses dedup so an explicit retry always schedules a run.
        """
        if not force and image_id in self._image_enqueued:
            logger.debug("Image %s already in queue, skipping duplicate", image_id)
            return
        self._image_enqueued.add(image_id)
        logger.debug("Enqueue image: %s (force=%s)", image_id, force)
        await self._image_queue.put((image_id, force))

    async def enqueue_compile(self, task_id: str):
        """Add a Wiki compile task to the compile queue (skip if already enqueued)."""
        if task_id in self._compile_enqueued:
            logger.debug("Compile task %s already in queue, skipping duplicate", task_id)
            return
        self._compile_enqueued.add(task_id)
        logger.debug("Enqueue compile task: %s", task_id)
        await self._compile_queue.put(task_id)

    # ── File Parsing Worker ──

    async def _file_worker(self, name: str):
        """Worker loop: pick files from queue and process."""
        logger.info("[%s] Worker started", name)
        while self._running:
            try:
                file_id = await asyncio.wait_for(
                    self._file_queue.get(), timeout=2.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                logger.info("[%s] Cancelled", name)
                return

            # 暂停闸门：sem 外等待，不占并发额度；wait 可被 stop() 的 cancel 打断
            await self._file_run.wait()

            async with self._file_sem:
                try:
                    self._file_enqueued.discard(file_id)
                    logger.info("[%s] Processing file %s", name, file_id)
                    await self._process_file(file_id)
                except Exception as e:
                    logger.error("[%s] Error processing %s: %s", name, file_id, e, exc_info=True)
                finally:
                    self._file_queue.task_done()

    async def _process_file(self, file_id: str):
        """Process a single file through the parsing pipeline."""
        file_record = self.db.get_file(file_id)
        if not file_record:
            logger.warning("File %s not found in DB, skipping", file_id)
            return
        if file_record["status"] != "pending":
            logger.info("File %s status is '%s', skipping", file_id, file_record["status"])
            return

        project_id = file_record["project_id"]
        file_path = (
            Path(self.data_dir) / "files" / project_id
            / file_id / file_record["filename"]
        )
        if not file_path.exists():
            logger.error("File not found on disk: %s", file_path)
            self.db.update_file_status(
                file_id, "error",
                error_message=f"File not found: {file_path}",
            )
            # 终态事件显式声明（E2，0.4 §6.4）
            activity_events.fail_file(self.db, file_id, f"File not found: {file_path}")
            return

        logger.info("Setting file %s status to parsing", file_id)
        self.db.update_file_status(file_id, "parsing")

        vlm = self._get_vlm_settings()
        pipeline = ProcessingPipeline(
            db_conn=self.db,
            chroma_persist_dir=str(Path(self.data_dir) / "chroma"),
            models_dir=str(Path(self.data_dir) / "models"),
            vlm_api_url=vlm.get("vlm_api_url"),
            vlm_api_key=vlm.get("vlm_api_key"),
            vlm_model=vlm.get("vlm_model", "glm-4v"),
        )

        try:
            result = await pipeline.process_file(
                file_path=str(file_path),
                project_id=project_id,
                file_id=file_id,
                filename=file_record["filename"],
            )

            # Re-check: file/project might have been deleted during processing
            file_record = self.db.get_file(file_id)
            if not file_record:
                logger.info("File %s was deleted during processing, discarding results", file_id)
                return
            
            project_exists = self.db.conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if not project_exists:
                logger.info("Project %s was deleted during processing, discarding results", project_id)
                return

            if result["success"]:
                logger.info("File %s parsed: %d chunks, %d images",
                            file_id, result["chunks_count"], result["images_count"])
                # Enqueue images for async VLM description
                images = self.db.list_images_by_file(file_id)
                for img in images:
                    if img.get("status", "pending") == "pending":
                        await self.enqueue_image(img["id"])
                if images:
                    self.db.update_file_image_status(file_id)

                # Wiki notification: file parsed → increment pending compile count
                try:
                    self.db.increment_pending_compile_count(project_id)
                    logger.debug("Wiki: incremented pending_compile_count for project %s", project_id)
                except Exception as e:
                    logger.warning("Wiki notification failed: %s", e)
            else:
                logger.error("File %s parse failed: %s", file_id, result.get("error"))
                self.db.update_file_status(
                    file_id, "error",
                    error_message=result.get("error"),
                )
                # 兜底声明（pipeline 内部 except 已发过，未读查重去重；防状态写入与事件脱钩，0.4 §3.2）
                activity_events.fail_file(self.db, file_id, result.get("error"))
        except Exception as e:
            logger.error("File %s exception: %s", file_id, e, exc_info=True)
            # actionable：配置类异常（如解析模型缺失）保留「设置 > 内部模型」修正指引（0.4 §7.3 v3.6）
            error_msg = actionable(e)
            self.db.update_file_status(file_id, "error", error_message=error_msg)
            activity_events.fail_file(self.db, file_id, error_msg)

    # ── Image Description Worker ──

    async def _image_worker(self, name: str):
        """Worker loop: pick images from queue and describe."""
        logger.info("[%s] Worker started", name)
        while self._running:
            try:
                item = await asyncio.wait_for(
                    self._image_queue.get(), timeout=2.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                logger.info("[%s] Cancelled", name)
                return

            # Support both (image_id, force) tuples and plain image_id strings
            if isinstance(item, tuple):
                image_id, force = item
            else:
                image_id, force = item, False

            # 暂停闸门（同 _file_worker）
            await self._image_run.wait()

            async with self._image_sem:
                try:
                    self._image_enqueued.discard(image_id)
                    logger.info("[%s] Processing image %s", name, image_id)
                    await self._process_image(image_id, force=force)
                except Exception as e:
                    logger.error("[%s] Error processing image %s: %s", name, image_id, e, exc_info=True)
                finally:
                    self._image_queue.task_done()

    # Minimum image dimensions (px) for VLM processing. Smaller images are auto-archived.
    MIN_IMAGE_SIZE = 100

    async def _process_image(self, image_id: str, force: bool = False):
        """Process a single image through VLM description.
        
        Args:
            image_id: Image record ID
            force: If True, skip small-image check (user manually triggered)
        """
        cursor = self.db.conn.execute(
            "SELECT i.*, f.project_id, f.filename "
            "FROM images i JOIN files f ON i.file_id = f.id "
            "WHERE i.id = ?",
            (image_id,),
        )
        row = cursor.fetchone()
        if not row:
            return
        img = dict(row)

        # 出队守卫：仅 force（用户显式重试）可重新处理 archived 图片。普通出队
        # 放行 archived 时，队列中残留的重复条目会把已自动归档的图片（seal/qr_code
        # /signature）再次送去 VLM——事故 2026-10-09/10 重复解析根因之二。
        allowed_statuses = ("pending", "error", "archived") if force else ("pending", "error")
        if img.get("status") not in allowed_statuses:
            return

        # Small image check: auto-archive unless forced
        img_path = Path(img["local_path"])
        if not force:
            # Use DB dimensions if available, otherwise read from file
            w = img.get("width_px")
            h = img.get("height_px")
            if (w is None or h is None) and img_path.exists():
                try:
                    from PIL import Image
                    with Image.open(str(img_path)) as im:
                        w, h = im.size
                except Exception:
                    pass  # Can't read dimensions, proceed normally
            
            if w is not None and h is not None:
                if w < self.MIN_IMAGE_SIZE and h < self.MIN_IMAGE_SIZE:
                    logger.info("Image %s too small (%dx%d), auto-archiving", image_id, w, h)
                    self.db.update_image_status(
                        image_id, "archived",
                        error_message=f"图片尺寸过小 ({w}x{h}px)，已自动归档。可手动强制解析。"
                    )
                    self.db.update_file_image_status(img["file_id"])
                    return

        self.db.update_image_status(image_id, "processing")

        vlm = self._get_vlm_settings()
        vlm_client = VLMClient(
            api_url=vlm.get("vlm_api_url"),
            api_key=vlm.get("vlm_api_key"),
            model=vlm.get("vlm_model", "glm-4v"),
        )

        file_id = img["file_id"]
        project_id = img["project_id"]
        context_text = img.get("context_text")

        try:
            if img_path.exists():
                result = await vlm_client.describe_image(str(img_path), context_text=context_text)
            else:
                raise FileNotFound(detail=str(img_path))

            # Re-check: image/file/project might have been deleted during processing
            img_record = self.db.conn.execute(
                "SELECT id FROM images WHERE id = ?", (image_id,)
            ).fetchone()
            if not img_record:
                logger.info("Image %s was deleted during processing, discarding results", image_id)
                return
            
            file_record = self.db.conn.execute(
                "SELECT id FROM files WHERE id = ?", (file_id,)
            ).fetchone()
            if not file_record:
                logger.info("File %s was deleted during image processing, discarding results", file_id)
                return
            
            project_exists = self.db.conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if not project_exists:
                logger.info("Project %s was deleted during image processing, discarding results", project_id)
                return

            # Success — store description and category
            description = result["description"]
            category = result.get("category")

            # Set category from VLM classification
            if category and category != "other":
                self.db.update_image_category(image_id, category)

            # Auto-archive categories not useful for reports
            if category in VLMClient.AUTO_ARCHIVE_CATEGORIES:
                self.db.update_image_status(
                    image_id, "archived",
                    vlm_description=description,
                    error_message=f"自动归档：{category} 类型不适合报告配图",
                )
            else:
                self.db.update_image_status(
                    image_id, "done", vlm_description=description
                )
                # Store as ChromaDB chunk (reuse shared VectorStore)
                try:
                    vs = self._get_vector_store()
                    img_doc_id = f"img_{file_id}_{img.get('page_no', 0)}"
                    vs.add_documents(
                        project_id=project_id,
                        ids=[img_doc_id],
                        documents=[description],
                        metadatas=[{
                            "file_id": file_id,
                            "filename": img["filename"],
                            "content_type": "figure",
                            "image_id": image_id,
                            "markdown_content": description,
                        }],
                    )
                except Exception as e:
                    logger.warning("ChromaDB image chunk failed: %s", e)

        except InkCubeError as e:
            logger.error("Image %s VLM error [%s]: %s", image_id, e.code, e.message)
            self.db.update_image_status(
                image_id, "error", error_message=actionable(e)
            )
        except Exception as e:
            logger.error("Image %s unexpected error: %s", image_id, e, exc_info=True)
            self.db.update_image_status(
                image_id, "error", error_message=str(e)
            )

        # Update file's overall image status
        self.db.update_file_image_status(file_id)

    # ── Wiki Compile Worker ──

    async def _compile_worker(self, name: str):
        """Worker loop: pick compile tasks from queue and process."""
        logger.info("[%s] Worker started", name)
        while self._running:
            try:
                task_id = await asyncio.wait_for(
                    self._compile_queue.get(), timeout=2.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                logger.info("[%s] Cancelled", name)
                return

            async with self._compile_sem:
                try:
                    self._compile_enqueued.discard(task_id)  # remove from tracking set
                    logger.info("[%s] Processing compile task %s", name, task_id)
                    await self._process_compile(task_id)
                except Exception as e:
                    logger.error("[%s] Error processing compile %s: %s", name, task_id, e, exc_info=True)
                finally:
                    self._compile_queue.task_done()

    async def _process_compile(self, task_id: str):
        """Process a single Wiki compile task through the two-stage pipeline."""
        task = self.db.get_wiki_compile_task(task_id)
        if not task:
            logger.warning("Compile task %s not found in DB, skipping", task_id)
            return
        if task["status"] not in ("queued", "failed"):
            logger.info("Compile task %s status is '%s', skipping", task_id, task["status"])
            return

        project_id = task["project_id"]

        # Verify project still exists
        project = self.db.get_project(project_id)
        if not project:
            logger.info("Project %s deleted, skipping compile task %s", project_id, task_id)
            self.db.update_compile_task_status(task_id, "failed", error_message="Project deleted")
            activity_events.fail_compile(self.db, task_id, "Project deleted")
            return

        # Mark task as started
        self.db.start_compile_task(task_id)

        # Get LLM settings
        settings = self.db.get_settings()

        from llm import LLMClient
        llm_client = LLMClient(settings)

        # V2 per-file ingest, continuous run (failure-isolated per file,
        # no pauses — 2.6 §4.7)
        await self._run_per_file_ingest(task_id, project_id, llm_client, settings)

    async def _run_per_file_ingest(
        self, task_id: str, project_id: str, llm_client, llm_settings: dict,
    ):
        """V2 per-file ingest: run all pending files back-to-back (no pauses).

        连续编译（2.6 §4.7）：逐文件串行保证摄入上下文滚动更新（index_summary /
        overview 随之更新）；单文件失败隔离（记入 summaries 不中断队列，含凭据类
        错误——每文件失败各记一条可行动文案，修好 Key 后重试增量即重跑失败文件）；
        停止为协作式（当前文件完成后生效）。
        """
        from wiki.ingest import WikiIngestor

        ingestor = WikiIngestor(self.db, self.data_dir, llm_client, llm_settings)
        already_done = [s["file_id"] for s in self.db.get_ingest_summaries(task_id)]
        files_processed = len(already_done)

        while self._running:
            # Check stop request（协作式：当前文件完成后才检查到）
            if self.db.is_compile_stop_requested(task_id):
                logger.info("Compile [%s]: stop requested after %d files", task_id, files_processed)
                self.db.update_compile_task_status(task_id, "stopped")
                return

            # Determine remaining files
            pending_files = self.db.get_pending_wiki_files(
                project_id, exclude_file_ids=already_done,
            )
            if not pending_files:
                logger.info("Compile [%s]: all files completed (%d total)", task_id, files_processed)
                self.db.update_compile_task_status(
                    task_id, "completed",
                    pages_total=files_processed, pages_completed=files_processed,
                )
                activity_events.complete_compile(self.db, task_id)
                # 3.1 §4.2:编译完成后直调台账解析 —— run_in_executor 后台线程,
                # 不阻塞 worker 事件循环;整体吞异常在 ledger_service 内,
                # 解析失败绝不影响编译主流程
                from wiki.ledger_service import on_compile_complete_sync
                asyncio.get_running_loop().run_in_executor(
                    None, lambda: on_compile_complete_sync(
                        self.db, project_id, llm_settings))
                return

            # Process the next pending file
            next_file = pending_files[0]
            file_id = next_file["id"]
            filename = next_file["filename"]
            logger.info("Compile [%s]: ingesting file '%s' (%d remaining)",
                         task_id, filename, len(pending_files))

            try:
                result = await ingestor.ingest_file(project_id, file_id, task_id)
                self.db.append_ingest_summary(task_id, {
                    "file_id": file_id, "filename": filename, **result,
                })
                logger.info("Compile [%s]: file '%s' done: %s", task_id, filename, result)
            except Exception as e:
                logger.error("Compile [%s]: file '%s' failed: %s", task_id, filename, e, exc_info=True)
                self.db.append_ingest_summary(task_id, {
                    "file_id": file_id, "filename": filename,
                    "error": actionable(e), "skipped": False,
                })

            already_done.append(file_id)
            files_processed += 1

            # 连续编译：逐文件刷新进度（前端进度条数据源；总数含未处理余量）
            self.db.update_compile_task_status(
                task_id, "ingesting",
                pages_total=files_processed + len(pending_files) - 1,
                pages_completed=files_processed,
            )

    def signal_compile_stop(self, task_id: str) -> bool:
        """Request a compile task to stop (takes effect after the current file)."""
        task = self.db.get_wiki_compile_task(task_id)
        if not task:
            return False
        self.db.request_compile_stop(task_id)
        return True

    # ── Queue Scanner ──

    async def _queue_scanner(self):
        """Periodically scan DB for pending files/images and enqueue them.

        Only enqueues items that are not already being processed (avoids
        duplicate queue entries for items picked up via the API).
        """
        # Give workers a moment to start up before first scan
        await asyncio.sleep(self._scan_initial_delay)
        logger.info("[QueueScanner] Running")

        while self._running:
            try:
                await asyncio.sleep(self._scan_interval)

                # Pick up pending files (only those still in 'pending' status)
                # 暂停时跳过拾取（闸门在 worker 侧也能拦住，这里省去无谓入队）
                pending_files: list[str] = []
                if self._file_run.is_set():
                    cursor = self.db.conn.execute(
                        "SELECT id FROM files WHERE status = 'pending' "
                        "ORDER BY created_at ASC"
                    )
                    pending_files = [row["id"] for row in cursor.fetchall()]
                if pending_files:
                    logger.info("[QueueScanner] Found %d pending files", len(pending_files))
                for fid in pending_files:
                    await self.enqueue_file(fid)

                # Pick up pending images
                pending_images = (
                    self.db.get_pending_images(limit=50)
                    if self._image_run.is_set()
                    else []
                )
                if pending_images:
                    logger.info("[QueueScanner] Found %d pending images", len(pending_images))
                for img in pending_images:
                    await self.enqueue_image(img["id"])

                # Pick up queued compile tasks（无队列暂停，见 __init__ 注释）
                cursor = self.db.conn.execute(
                    "SELECT id FROM wiki_compile_tasks WHERE status = 'queued' "
                    "ORDER BY created_at ASC LIMIT 5"
                )
                queued_compiles = [row["id"] for row in cursor.fetchall()]
                if queued_compiles:
                    logger.info("[QueueScanner] Found %d queued compile tasks", len(queued_compiles))
                for tid in queued_compiles:
                    await self.enqueue_compile(tid)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("[QueueScanner] Error: %s", e, exc_info=True)

    # ── Startup Recovery ──

    def _reset_interrupted_items(self):
        """Reset files/images stuck in processing due to unexpected shutdown.

        Files in 'parsing' → full cleanup (ChromaDB + FTS + images) → pending.
        Images in 'processing' → just reset to pending.
        """
        # 1. Reset interrupted file parsing
        cursor = self.db.conn.execute(
            "SELECT id, project_id FROM files WHERE status = 'parsing'"
        )
        interrupted_files = [dict(r) for r in cursor.fetchall()]

        if interrupted_files:
            # Shared ChromaDB client (no embedding model needed for delete).
            # MUST go through get_chroma_client: a bare PersistentClient() with
            # default settings would register a second (path, settings) entry in
            # chromadb's in-process cache and make every later VectorStore(...)
            # fail with "different settings" — incident 2026-10-07: after a
            # restart with an interrupted file, 181 files failed in a row.
            try:
                from knowledge.vector_store import get_chroma_client
                chroma_client = get_chroma_client(str(Path(self.data_dir) / "chroma"))
            except Exception as e:
                logger.warning("[Startup] Failed to open ChromaDB for cleanup: %s", e)
                chroma_client = None

            for f in interrupted_files:
                file_id = f["id"]
                project_id = f["project_id"]
                logger.info("[Startup] Resetting interrupted file: %s", file_id)

                # Clean ChromaDB chunks for this file
                if chroma_client:
                    try:
                        coll_name = f"project_{project_id}"
                        existing = {c.name for c in chroma_client.list_collections()}
                        if coll_name in existing:
                            coll = chroma_client.get_collection(coll_name)
                            coll.delete(where={"file_id": file_id})
                    except Exception as e:
                        logger.warning("[Startup] ChromaDB cleanup failed for %s: %s", file_id, e)

                # Clean FTS entries
                try:
                    self.db.conn.execute(
                        "DELETE FROM chunks_fts WHERE chunk_id LIKE ? OR chunk_id LIKE ?",
                        (f"chunk_{file_id}_%", f"img_{file_id}_%"),
                    )
                except Exception as e:
                    logger.warning("[Startup] FTS cleanup failed for %s: %s", file_id, e)

                # Clean image records
                try:
                    self.db.conn.execute(
                        "DELETE FROM images WHERE file_id = ?", (file_id,)
                    )
                except Exception as e:
                    logger.warning("[Startup] Image cleanup failed for %s: %s", file_id, e)

                # Reset file status to pending
                self.db.reset_file_for_retry(file_id)

            self.db.conn.commit()
            logger.info("[Startup] Reset %d interrupted files", len(interrupted_files))

        # 2. Reset interrupted image processing
        cursor = self.db.conn.execute(
            "SELECT id FROM images WHERE status = 'processing'"
        )
        interrupted_images = [dict(r) for r in cursor.fetchall()]

        if interrupted_images:
            for img in interrupted_images:
                self.db.conn.execute(
                    "UPDATE images SET status = 'pending', error_message = NULL "
                    "WHERE id = ?",
                    (img["id"],),
                )
            self.db.conn.commit()
            logger.info("[Startup] Reset %d interrupted images", len(interrupted_images))

        # 3. Reset interrupted compile tasks
        try:
            reset_count = self.db.reset_interrupted_compile_tasks()
            if reset_count > 0:
                logger.info("[Startup] Reset %d interrupted compile tasks", reset_count)
        except Exception as e:
            logger.warning("[Startup] Failed to reset compile tasks: %s", e)

    # ── Helpers ──

    def _get_vlm_settings(self) -> dict:
        """Read VLM settings from database."""
        cursor = self.db.conn.execute(
            "SELECT key, value FROM settings "
            "WHERE key IN ('vlm_api_url', 'vlm_api_key', 'vlm_model')"
        )
        settings = {}
        for row in cursor.fetchall():
            val = row["value"]
            try:
                settings[row["key"]] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                settings[row["key"]] = val
        return settings
