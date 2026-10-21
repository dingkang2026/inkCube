"""python/knowledge/knowledge_search.py"""
import asyncio
import logging
from typing import Optional, List

import numpy as np

from db.database import Database
from knowledge.vector_store import VectorStore
from knowledge.fts_store import FTSStore
from knowledge.hybrid_retriever import HybridRetriever

logger = logging.getLogger(__name__)


class KnowledgeSearchTool:
    """
    ReAct agent tool for querying the knowledge base.
    
    Provides unified search interface combining:
    - Dense vector retrieval (semantic similarity via ChromaDB)
    - Full-text keyword search (BM25 ranking via SQLite FTS5)
    - RRF fusion for hybrid results
    
    Usage in ReAct agents:
    ```python
    tool = KnowledgeSearchTool.from_db(db_conn, chroma_dir)
    results = await tool.search(
        query="risk factors",
        project_id="proj_123",
        top_k=5
    )
    ```
    """
    
    def __init__(
        self,
        db: Database,
        chroma_persist_dir: str,
        models_dir: Optional[str] = None,
        rrf_k: int = 60
    ):
        """
        Initialize knowledge search tool.
        
        Args:
            db: Database connection
            chroma_persist_dir: ChromaDB persistence directory
            models_dir: Path to bge-m3 model (auto-detected if None)
            rrf_k: RRF smoothing parameter
        """
        self.db = db
        # models_dir is the parent directory (e.g. data/models).
        # VectorStore expects the specific bge-m3 model path.
        from pathlib import Path
        bge_m3_path = str(Path(models_dir) / "bge-m3") if models_dir else None
        self.vector_store = VectorStore(chroma_persist_dir, bge_m3_path=bge_m3_path)
        self.fts_store = FTSStore(db)
        self.hybrid_retriever = HybridRetriever(
            vector_store=self.vector_store,
            fts_store=self.fts_store,
            rrf_k=rrf_k
        )
    
    @classmethod
    def from_db(cls, db_conn, chroma_dir: str):
        """Create tool from database and config"""
        return cls(db=db_conn, chroma_persist_dir=chroma_dir)
    
    async def search(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
        content_type_filter: Optional[str] = None,
        dense_weight: float = 0.7,
        fts_weight: float = 0.3,
        file_id: Optional[str] = None
    ) -> List[dict]:
        """
        Search knowledge base with hybrid retrieval.
        
        Combines semantic similarity (dense vectors) with keyword matching (FTS).
        Uses Reciprocal Rank Fusion (RRF) to merge results.
        
        Args:
            query: Search query (natural language or keywords)
            project_id: Project ID to filter
            top_k: Number of results to return
            content_type_filter: Optional filter (heading/paragraph/table/list)
            dense_weight: Weight for dense vector results in RRF
            fts_weight: Weight for FTS results in RRF
            file_id: Optional filter to restrict to a single file
        
        Returns:
            List of result dicts sorted by combined RRF score:
            - id: Chunk ID
            - content: Text content
            - _score: Combined RRF score
            - _dense_score: Dense vector score
            - _fts_score: FTS BM25 score
            - _rank_dense: Rank in dense results
            - _rank_fts: Rank in FTS results
            - metadata: Chunk metadata
        """
        # Generate query embedding + run fused retrieval — both are sync
        # CPU-bound calls (bge-m3 inference, Chroma/FTS queries) that would
        # freeze the event loop for the duration of the search (H5)
        query_vector = await asyncio.to_thread(self._embed_query, query)
        fused_results = await asyncio.to_thread(
            self.hybrid_retriever.search,
            project_id=project_id,
            query_text=query,
            query_vector=query_vector,
            top_k=top_k,
            content_type_filter=content_type_filter,
            dense_weight=dense_weight,
            fts_weight=fts_weight,
            file_id=file_id,
        )
        
        return fused_results
    
    def _embed_query(self, query: str) -> Optional[List[float]]:
        """
        Embed query using bge-m3 model via VectorStore's embedding function.
        Reuses the already-loaded model to avoid duplicate memory usage.
        
        Args:
            query: Query text
        
        Returns:
            1024-dimensional embedding vector; None when fp16 inference
            yields non-finite values (2026-10-14/15 事故：NaN 查询向量会让
            全部 cosine 距离变 NaN 并在响应序列化时 500)——caller degrades
            to FTS-only retrieval.
        """
        # Reuse VectorStore's embedding model (handles nested HF cache paths)
        model = self.vector_store._get_embedding_model()
        result = model.encode([query], batch_size=1, max_length=512)
        vec = np.asarray(result['dense_vecs'][0], dtype=np.float32)
        if not np.isfinite(vec).all():
            logger.warning(
                "Query embedding for %r is non-finite (fp16 overflow) — "
                "degrading to FTS-only retrieval", query[:40],
            )
            return None
        return vec.tolist()
    
    def list_projects(self) -> List[dict]:
        """
        List all projects with document counts.
        
        Returns:
            List of project dicts with id, name, file_count, chunk_count
        """
        cursor = self.db.conn.execute("""
            SELECT 
                p.id,
                p.name,
                COUNT(DISTINCT f.id) as file_count,
                COALESCE(SUM(f.chunk_count), 0) as chunk_count
            FROM projects p
            LEFT JOIN files f ON f.project_id = p.id
            WHERE p.kind = 'project'
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """)
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_project_files(self, project_id: str) -> List[dict]:
        """
        Get all files in a project with processing status.
        
        Args:
            project_id: Project ID
        
        Returns:
            List of file dicts with id, filename, status, chunks_count, etc.
        """
        cursor = self.db.conn.execute("""
            SELECT 
                id,
                filename,
                file_type,
                status,
                created_at,
                updated_at,
                metadata
            FROM files
            WHERE project_id = ?
            ORDER BY created_at DESC
        """, (project_id,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def delete_project(self, project_id: str):
        """
        Delete entire project including all files and chunks.
        
        Args:
            project_id: Project ID
        """
        # Delete from ChromaDB
        self.vector_store.delete_project(project_id)
        
        # Delete from FTS
        self.fts_store.delete_project_chunks(project_id)
        
        # Delete from database (cascade should handle related records)
        self.db.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self.db.conn.commit()
