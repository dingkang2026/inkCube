"""python/knowledge/hybrid_retriever.py"""
import logging
from typing import Optional
from knowledge.vector_store import VectorStore
from knowledge.fts_store import FTSStore
from db.database import Database

logger = logging.getLogger(__name__)


class HybridRetriever:
    """
    Hybrid retrieval combining dense vector search (ChromaDB) and 
    full-text search (SQLite FTS5) with RRF fusion.
    
    RRF (Reciprocal Rank Fusion) formula:
        score = sum(1 / (k + rank_i)) for each result i
    
    Where k is a constant (default 60) that controls the weight of rank position.
    Higher k gives more weight to lower-ranked results.
    """
    
    def __init__(
        self,
        vector_store: VectorStore,
        fts_store: FTSStore,
        rrf_k: int = 60
    ):
        """
        Initialize hybrid retriever.
        
        Args:
            vector_store: ChromaDB vector store instance
            fts_store: SQLite FTS5 store instance
            rrf_k: RRF fusion parameter (default 60)
        """
        self.vector_store = vector_store
        self.fts_store = fts_store
        self.rrf_k = rrf_k
    
    def search(
        self,
        project_id: str,
        query_text: str,
        query_vector: list[float],
        top_k: int = 5,
        content_type_filter: Optional[str] = None,
        dense_weight: float = 0.7,
        fts_weight: float = 0.3,
        file_id: Optional[str] = None
    ) -> list[dict]:
        """
        Hybrid search combining dense vectors and full-text search.
        
        Flow:
        1. Search ChromaDB with query_vector (dense retrieval)
        2. Search SQLite FTS5 with query_text (keyword matching)
        3. Merge results using RRF fusion
        4. Return top_k results sorted by combined score
        
        Args:
            project_id: Project ID to search within
            query_text: Text query for FTS (e.g., "人工智能")
            query_vector: Embedding vector for dense search (1024-dim for bge-m3)
            top_k: Number of final results to return
            content_type_filter: Optional filter by content type
            dense_weight: Weight for dense search scores (0-1)
            fts_weight: Weight for FTS scores (0-1)
            file_id: Optional filter to restrict results to a single file
        
        Returns:
            List of dicts with id, content, metadata, _score (combined),
            _dense_score, _fts_score, _rank_dense, _rank_fts
        """
        # Build ChromaDB where clause
        where = {}
        if content_type_filter:
            where["content_type"] = content_type_filter
        if file_id:
            where["file_id"] = file_id

        # Step 1: Dense vector search
        dense_results = self.vector_store.search_dense(
            project_id=project_id,
            query_vector=query_vector,
            top_k=top_k * 2,  # Retrieve more for fusion
            where=where if where else None
        )
        
        # Step 2: Full-text search — degrade to dense-only on FTS failure.
        # The two legs are independent; a broken FTS leg (bad query, index
        # issues) must not 500 the whole retrieval — the dense leg remains
        # the primary semantic signal.
        try:
            fts_results = self.fts_store.search(
                project_id=project_id,
                query=query_text,
                top_k=top_k * 2,
                content_type_filter=content_type_filter,
                file_id=file_id
            )
        except Exception as e:
            logger.warning(
                "FTS leg failed, falling back to dense-only (query=%r): %s",
                query_text, e,
            )
            fts_results = []
        
        # Step 3: RRF fusion
        fused = self._rrf_fusion(
            dense_results=dense_results,
            fts_results=fts_results,
            top_k=top_k,
            dense_weight=dense_weight,
            fts_weight=fts_weight
        )
        
        # Step 4: Fill missing metadata from ChromaDB
        # FTS-only results (not in dense) have empty metadata — fetch from ChromaDB
        ids_missing_meta = [
            r["id"] for r in fused
            if not r.get("metadata") or not r["metadata"].get("markdown_content")
        ]
        if ids_missing_meta:
            meta_map = self.vector_store.get_metadata_by_ids(
                project_id, ids_missing_meta
            )
            for r in fused:
                if r["id"] in meta_map:
                    if not r.get("metadata"):
                        r["metadata"] = {}
                    # Merge ChromaDB metadata (don't overwrite existing)
                    for k, v in meta_map[r["id"]].items():
                        if k not in r["metadata"]:
                            r["metadata"][k] = v
        
        return fused
    
    def _rrf_fusion(
        self,
        dense_results: list[dict],
        fts_results: list[dict],
        top_k: int,
        dense_weight: float,
        fts_weight: float
    ) -> list[dict]:
        """
        Reciprocal Rank Fusion of dense and FTS results.
        
        RRF formula: score = sum(1 / (k + rank_i))
        
        For each document appearing in either result set:
        - If in dense results at rank r_d: add dense_weight / (k + r_d)
        - If in FTS results at rank r_f: add fts_weight / (k + r_f)
        
        Args:
            dense_results: Results from dense vector search
            fts_results: Results from FTS keyword search
            top_k: Number of top results to return after fusion
            dense_weight: Weight for dense scores
            fts_weight: Weight for FTS scores
        
        Returns:
            Fused and re-ranked results
        """
        # Build lookup maps: chunk_id -> (rank, original_result)
        dense_map = {}
        for rank, result in enumerate(dense_results, 1):
            dense_map[result["id"]] = {
                "rank": rank,
                "result": result
            }
        
        fts_map = {}
        for rank, result in enumerate(fts_results, 1):
            fts_map[result["id"]] = {
                "rank": rank,
                "result": result
            }
        
        # Collect all unique chunk IDs
        all_ids = set(dense_map.keys()) | set(fts_map.keys())
        
        # Calculate RRF scores
        fused_results = []
        for chunk_id in all_ids:
            dense_info = dense_map.get(chunk_id)
            fts_info = fts_map.get(chunk_id)
            
            # Calculate RRF components
            dense_rrf = dense_weight / (self.rrf_k + dense_info["rank"]) if dense_info else 0
            fts_rrf = fts_weight / (self.rrf_k + fts_info["rank"]) if fts_info else 0
            
            # Combined RRF score
            combined_score = dense_rrf + fts_rrf
            
            # Use dense result as primary source (has embeddings/metadata)
            # Fall back to FTS result if not in dense results
            primary_result = (
                dense_info["result"] if dense_info else fts_info["result"]
            )
            
            fused_results.append({
                "id": chunk_id,
                "content": primary_result.get("content", ""),
                "metadata": primary_result.get("metadata", {}),
                "_score": combined_score,
                "_dense_score": dense_info["result"].get("_score", 0) if dense_info else 0,
                "_fts_score": fts_info["result"].get("_score", 0) if fts_info else 0,
                "_rank_dense": dense_info["rank"] if dense_info else None,
                "_rank_fts": fts_info["rank"] if fts_info else None,
            })
        
        # Sort by combined RRF score (descending)
        fused_results.sort(key=lambda x: x["_score"], reverse=True)
        
        # Return top_k
        return fused_results[:top_k]
