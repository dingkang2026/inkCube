"""python/knowledge/vector_store.py"""
# CRITICAL: Set offline mode BEFORE any imports that might load transformers
import logging
import math
import os
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

import threading
import chromadb
from chromadb.errors import NotFoundError
from chromadb.config import Settings as ChromaSettings
from chromadb.utils import embedding_functions
from pathlib import Path
import numpy as np
from typing import Optional, List


# ---------------------------------------------------------------------------
# Module-level singleton: bge-m3 model is shared across ALL VectorStore
# instances so it is loaded into memory exactly once per process.
# ---------------------------------------------------------------------------
_bge_m3_model = None
_bge_m3_lock = threading.Lock()

logger = logging.getLogger(__name__)


def _get_bge_m3_model(model_path: str):
    """Return the singleton BGEM3FlagModel, loading it on first call.

    Subsequent calls with any *model_path* return the already-loaded model
    (the path from the first call wins).
    """
    global _bge_m3_model
    if _bge_m3_model is not None:
        return _bge_m3_model
    with _bge_m3_lock:
        if _bge_m3_model is not None:          # double-check inside lock
            return _bge_m3_model
        from FlagEmbedding import BGEM3FlagModel
        # 懒加载 torch（唯一使用点：下方 cuda 探测）——模块级 import 会把
        # torch 全家（~1.7s）拖进启动链；此处仅在首次 embedding 时付出
        import torch

        resolved = Path(model_path)
        # Handle nested HF-cache snapshots directory
        possible = [
            resolved,
            resolved / "models" / "BAAI--bge-m3" / "snapshots" / "master",
        ]
        actual = None
        for p in possible:
            if (p / "pytorch_model.bin").exists():
                actual = p
                break
        if actual is None:
            actual = resolved  # fallback

        logger.info("BgeM3 loading model from: %s", actual)
        if torch.cuda.is_available():
            # GPU acceleration (RTX 5050 spike 2026-10-07: 3x3636-char encode
            # 5.99s CPU fp32 → 0.73s CUDA fp16, ~8x); fp16 needs ~2-3GB VRAM
            _bge_m3_model = BGEM3FlagModel(str(actual), use_fp16=True, devices=["cuda"])
            logger.info("BgeM3 model loaded on CUDA (fp16)")
        else:
            # No NVIDIA GPU / CPU-only torch build → fp32 fallback
            _bge_m3_model = BGEM3FlagModel(str(actual), use_fp16=False)
            logger.info("BgeM3 model loaded on CPU (fp32)")
    return _bge_m3_model


def preload_bge_m3(model_path: str):
    """Trigger model loading in the current thread (blocking).

    Call from a background thread during app startup so the model is
    warm before the first user query.
    """
    _get_bge_m3_model(model_path)


# ---------------------------------------------------------------------------
# Process-wide ChromaDB client singleton (one per resolved path).
#
# chromadb keys its in-process backend cache by (path, settings) and raises
# "An instance of Chroma already exists ... with different settings" when the
# same directory is opened twice with different Settings. Routing EVERY
# caller (VectorStore indexing + startup cleanup in task_queue) through this
# helper guarantees a single Settings per directory, so the collision is
# structurally impossible. Incident 2026-10-07: a bare PersistentClient()
# in the startup-reset path poisoned the cache and failed 181 files.
# ---------------------------------------------------------------------------
_chroma_clients: dict = {}
_chroma_clients_lock = threading.Lock()


def get_chroma_client(persist_dir: str):
    """Return the shared PersistentClient for *persist_dir*.

    Created once per process with the canonical project settings
    (telemetry off) and reused by every later caller.
    """
    key = str(Path(persist_dir).resolve())
    with _chroma_clients_lock:
        client = _chroma_clients.get(key)
        if client is None:
            client = chromadb.PersistentClient(
                path=key,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            _chroma_clients[key] = client
        return client


def drop_chroma_client(persist_dir: str) -> None:
    """Close and evict the shared client for *persist_dir*.

    Call before deleting the directory itself (e.g. knowledge-base wipe) so
    the cache never hands out a client whose backing files are gone.
    stop() releases the on-disk sqlite/segment handles — popping the cache
    alone left them open and the following rmtree silently kept locked
    files behind on Windows (incident 2026-10-20: "Deleted ChromaDB
    directory" logged while chroma/ survived). clear_system_cache() drops
    chromadb's global (path, settings) registry entry so a later
    get_chroma_client() re-registers cleanly (a bare PersistentClient
    would poison it — incident 2026-10-07). stop runs inside the lock:
    a concurrent get_chroma_client() between pop and stop would re-open
    the directory and re-hold the very handles being released.
    """
    key = str(Path(persist_dir).resolve())
    with _chroma_clients_lock:
        client = _chroma_clients.pop(key, None)
        if client is None:
            return
        try:
            client._system.stop()
        except Exception:
            logger.warning("chroma _system.stop failed: %s", key, exc_info=True)
        try:
            client.clear_system_cache()
        except Exception:
            logger.warning(
                "chroma clear_system_cache failed: %s", key, exc_info=True)


def image_doc_id(file_id: str, page_no, image_id: str) -> str:
    """图片描述向量的唯一 id：img_{file_id}_{page_no}_{image_id}。

    前缀 img_{file_id}_ 供文件级 LIKE 清理匹配（fts_store.delete_by_file_id、
    task_queue 文件删除、routers 按前缀清理）；尾部 image_id（SQLite
    images.id，UUID）保证同页多图 id 唯一。事故 2026-10-14：旧格式
    img_{file_id}_{page_no} 同页碰撞，ChromaDB upsert 互相覆盖，498 张图
    的 VLM 描述检索不到。写入与删除统一走本函数。
    """
    return f"img_{file_id}_{page_no if page_no is not None else 0}_{image_id}"


# Serializes bge-m3 encode batches across threads. FlagEmbedding 1.3 moves
# weights to CUDA lazily on the first encode; two concurrent first-calls can
# race the transfer and crash one caller with a device-mismatch error. On GPU
# a batch takes ~0.2s, so serializing costs nothing — file-parsing parallelism
# wins by overlapping the CPU-bound Docling stage with these short GPU batches.
_bge_encode_lock = threading.Lock()


class BgeM3EmbeddingFunction(embedding_functions.EmbeddingFunction):
    """Custom embedding function for bge-m3 model using FlagEmbedding.

    Delegates to the module-level singleton so the model is loaded only
    once regardless of how many instances are created.
    """

    def __init__(self, model_path: str):
        self.model_path = model_path

    def _get_model(self):
        return _get_bge_m3_model(self.model_path)

    def __call__(self, texts: List[str]) -> List[List[float]]:
        model = self._get_model()
        with _bge_encode_lock:
            # 显式限定批次与截断（2026-10-09 GPU TDR 崩溃根因修复）：
            # FlagEmbedding 默认 batch_size=256，批量入库（如 94 chunks）会
            # 一炮全进 GPU forward，fp16 eager attention 峰值 ~7.4GB 打爆
            # 8GB 显存 → 库内 OOM 降级循环反复满载重试 → 驱动 TDR
            # （nvlddmkm 153）→ CUDA 上下文失效 → 后端进程假死。
            # batch_size=8 将单批显存峰值压到 ~250MB；max_length=1024 与
            # doc_parser chunker 的 max_tokens=1024 对齐，入库文本零截断损失。
            result = model.encode(
                texts, return_dense=True, batch_size=8, max_length=1024,
            )
        # 入库边界 NaN 消毒（2026-10-14/15 事故根因修复）：fp16 CUDA 推理对
        # 个别输入会溢出产生非有限值，裸入库即永久毒化——该 chunk 的 cosine
        # 距离对任何查询恒为 NaN，检索响应 JSON 序列化直接 500。非有限行
        # 替换为单位基向量 e0（有限、模长 1，cosine 可计算不毒化），该
        # chunk 仍可经 FTS 腿命中；日志留痕提示重新入库该文件。
        vecs = np.asarray(result['dense_vecs'], dtype=np.float32)
        bad_rows = ~np.isfinite(vecs).all(axis=1)
        if bad_rows.any():
            vecs = vecs.copy()
            vecs[bad_rows] = 0.0
            vecs[bad_rows, 0] = 1.0
            for i in np.flatnonzero(bad_rows):
                logger.warning(
                    "BgeM3 embedding produced a non-finite vector (text %d/%d "
                    "%r); stored unit placeholder — re-ingest the source file",
                    i + 1, len(texts), str(texts[i])[:40],
                )
        return vecs.tolist()


class VectorStore:
    """
    ChromaDB vector store with explicit bge-m3 embedding function.
    
    CRITICAL: Must configure embedding_function to use LOCAL bge-m3 path.
    Never allow ChromaDB to download its own model copy.
    
    NOTE: ChromaDB does NOT support true FTS (Full-Text Search).
    Use SQLite FTS5 for keyword-based search instead.
    """
    
    def __init__(self, persist_dir: str, bge_m3_path: Optional[str] = None):
        """
        Initialize ChromaDB client with bge-m3 embedding function.
        
        Args:
            persist_dir: ChromaDB persistence directory
            bge_m3_path: Path to local bge-m3 model (optional, auto-detected if None)
        """
        # Shared client — one Settings per directory, process-wide
        # (a second PersistentClient with different settings would raise;
        # see get_chroma_client docstring)
        self.client = get_chroma_client(persist_dir)
        
        # Auto-detect bge-m3 path if not provided
        if bge_m3_path is None:
            try:
                from knowledge.model_manager import get_models_dir
                bge_m3_path = Path(get_models_dir()) / "bge-m3"
            except ImportError:
                # Fallback for testing without model_manager
                bge_m3_path = Path("python/data/models/bge-m3")
        
        # CRITICAL: Configure explicit embedding function to reuse local model
        # This prevents ChromaDB from downloading its own model copy
        # Use custom embedding function for bge-m3
        self.embedding_function = BgeM3EmbeddingFunction(str(Path(bge_m3_path).resolve()))
        
        # Cache embedding model for direct encoding
        self._embedding_model = None
    
    def _get_embedding_model(self):
        """Return the singleton bge-m3 model for direct encoding.

        Delegates to the module-level singleton so the model is loaded
        only once per process regardless of how many VectorStore instances
        exist.
        """
        if self._embedding_model is None:
            self._embedding_model = self.embedding_function._get_model()
        return self._embedding_model
    
    def get_or_create_collection(self, project_id: str) -> chromadb.Collection:
        """
        Get or create project collection with HNSW optimization.
        
        Collection naming: project_{id}
        Example: project_abc123
        
        Args:
            project_id: Project ID
            
        Returns:
            ChromaDB collection object
        """
        collection_name = f"project_{project_id}"
        
        # Check if exists
        existing = self.client.list_collections()
        if any(c.name == collection_name for c in existing):
            return self.client.get_collection(
                collection_name,
                embedding_function=self.embedding_function,
            )
        
        # Create new collection with HNSW config
        collection = self.client.create_collection(
            name=collection_name,
            embedding_function=self.embedding_function,
            metadata={
                "hnsw:space": "cosine",
                "hnsw:construction_ef": 128,
                "hnsw:search_ef": 64,
            }
        )
        
        return collection
    
    def add_documents(
        self,
        project_id: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: Optional[list[list[float]]] = None
    ) -> None:
        """
        Batch add documents to collection.
        
        Args:
            project_id: Project ID
            ids: ChromaDB document IDs (e.g., ["chunk_xxx_0"])
            documents: Pure text content (for embedding + display)
            metadatas: Metadata including content_type, filename, etc.
            embeddings: Pre-computed embeddings (optional). If None, uses embedding_function.
        """
        collection = self.get_or_create_collection(project_id)
        
        if embeddings is not None:
            # Use pre-computed embeddings (faster for testing)
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings
            )
        else:
            # Let ChromaDB compute embeddings using bge-m3
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
    
    def search_dense(
        self,
        project_id: str,
        query_vector: list[float],
        top_k: int = 5,
        where: Optional[dict] = None
    ) -> list[dict]:
        """
        Dense vector retrieval using ChromaDB.
        
        Args:
            project_id: Project ID
            query_vector: Query embedding vector (1024-dim for bge-m3)
            top_k: Number of results to return
            where: Optional filter conditions
            
        Returns:
            List of dicts with id, content, metadata, distance, _score
        """
        collection = self.get_or_create_collection(project_id)
        
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"]
        )
        
        formatted = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                dist = results["distances"][0][i]
                # 出口 NaN 守卫（纵深防御）：存量库若残留 fp16 毒化向量，其
                # 距离恒为 NaN——跳过并留痕，绝不让 NaN 进入响应序列化
                # （2026-10-14/15 json.dumps 500 事故）。根因消毒在
                # BgeM3EmbeddingFunction.__call__；此处兜存量库遗留脏数据。
                if not math.isfinite(dist):
                    logger.warning(
                        "Dense search returned non-finite distance for chunk "
                        "%s (project %s) — skipped; re-ingest the source file",
                        doc_id, project_id,
                    )
                    continue
                formatted.append({
                    "id": doc_id,
                    "content": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": dist,
                    "_score": 1.0 / (1.0 + dist)
                })
        
        return formatted
    
    def get_metadata_by_ids(
        self,
        project_id: str,
        ids: list[str]
    ) -> dict[str, dict]:
        """
        Fetch metadata for specific document IDs from ChromaDB.
        
        Args:
            project_id: Project ID
            ids: List of document IDs
            
        Returns:
            Dict mapping id -> metadata dict
        """
        if not ids:
            return {}
        collection = self.get_or_create_collection(project_id)
        results = collection.get(ids=ids, include=["metadatas"])
        metadata_map = {}
        if results["ids"]:
            for doc_id, meta in zip(results["ids"], results["metadatas"]):
                metadata_map[doc_id] = meta or {}
        return metadata_map

    def export_collection(self, project_id: str) -> dict:
        """
        Export the full project collection for transfer (2.9 §4.4).

        collection.get 全量导出（向量 + 文本 + metadata），同时覆盖文本 chunk
        与图片描述向量——后者只入 Chroma 不入 FTS，按 id 取向量的路线会漏导。
        metadata 原样返回（markdown_content/section_path/page/chunk_index 是
        rag.py / hybrid_retriever.py 的消费依赖，不可丢）。

        Returns:
            {"ids": [...], "documents": [...], "metadatas": [...], "embeddings": [...]}
        """
        collection = self.get_or_create_collection(project_id)
        return collection.get(include=["embeddings", "documents", "metadatas"])

    def delete_project(self, project_id: str) -> None:
        """
        Delete entire project collection.
        
        Args:
            project_id: Project ID
        """
        collection_name = f"project_{project_id}"
        try:
            self.client.delete_collection(collection_name)
        except NotFoundError:
            # Collection doesn't exist, ignore（chromadb ≥1.x 抛 NotFoundError，
            # 旧版为 ValueError；两皆容忍，避免删除无文件项目时 500）
            pass
        except ValueError:
            pass

    def delete_by_file_id(self, project_id: str, file_id: str) -> None:
        """
        Delete all chunks for a file from ChromaDB.
        
        Args:
            project_id: Project ID (to locate collection)
            file_id: File ID whose chunks to delete
        """
        collection = self.get_or_create_collection(project_id)
        collection.delete(where={"file_id": file_id})

    def delete_by_ids(self, project_id: str, ids: list[str]) -> None:
        """
        Delete specific document IDs from ChromaDB.
        
        Args:
            project_id: Project ID
            ids: List of ChromaDB document IDs to delete
        """
        if not ids:
            return
        collection = self.get_or_create_collection(project_id)
        collection.delete(ids=ids)

    def delete_by_image_id(self, project_id: str, image_id: str) -> None:
        """删除某张图片的全部向量块（按 metadata.image_id 过滤）。

        id 格式无关：新旧格式向量的 metadata.image_id 均为同一 SQLite
        images.id，metadata 过滤一次删净。避免 delete_by_ids 对不存在
        id 静默 no-op——2026-10-15 review 发现：存量旧格式向量与
        img_{file_id}_{page_no}_{image_id} 新 id 失配，删除/归档/重试后
        旧向量残留且仍可被检索（dense 是图片描述唯一检索通道）。

        Args:
            project_id: Project ID (to locate collection)
            image_id: SQLite images.id whose chunks to delete
        """
        collection = self.get_or_create_collection(project_id)
        collection.delete(where={"image_id": image_id})
