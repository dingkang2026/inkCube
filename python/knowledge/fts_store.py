"""python/knowledge/fts_store.py"""
from db.database import Database
from typing import Optional


class FTSStore:
    """
    SQLite FTS5 full-text search store.
    
    ChromaDB does NOT support true FTS, so we use SQLite FTS5 for keyword matching.
    FTS5 provides BM25 ranking and supports boolean queries.
    
    Key features:
    - BM25 ranking algorithm (built-in to FTS5)
    - Boolean operators: AND, OR, NOT
    - Phrase matching: "exact phrase"
    - Prefix matching: keyw*
    - Unicode tokenization (unicode61 tokenizer)
    """
    
    def __init__(self, db: Database):
        """
        Initialize FTS5 virtual table.
        
        Args:
            db: Database connection
        """
        self.db = db
        
        # Create FTS5 virtual table if not exists
        # Note: FTS5 tables don't have explicit PRIMARY KEY,
        # but chunk_id serves as unique identifier
        db.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id,
                content,
                project_id,
                filename,
                content_type,
                tokenize='unicode61'
            )
        """)
        db.conn.commit()
    
    def add_chunk(
        self,
        chunk_id: str,
        content: str,
        project_id: str,
        filename: str,
        content_type: str
    ):
        """
        Add chunk to FTS index.
        
        Call this when adding chunks to ChromaDB to keep FTS in sync.
        
        Args:
            chunk_id: Unique chunk identifier
            content: Text content for indexing
            project_id: Project ID for filtering
            filename: Source filename
            content_type: Type of content (heading/paragraph/table/etc.)
        """
        self.db.conn.execute(
            """INSERT INTO chunks_fts (chunk_id, content, project_id, filename, content_type)
               VALUES (?, ?, ?, ?, ?)""",
            (chunk_id, content, project_id, filename, content_type)
        )
        self.db.conn.commit()
    
    def add_chunks_batch(self, chunks: list[dict]):
        """
        Batch add chunks to FTS index.
        All inserts are done in a single transaction for performance.
        
        Args:
            chunks: List of dicts with keys:
                - chunk_id: Unique chunk identifier
                - content: Text content for indexing
                - project_id: Project ID for filtering
                - filename: Source filename
                - content_type: Type of content
        """
        for chunk in chunks:
            self.db.conn.execute(
                """INSERT INTO chunks_fts (chunk_id, content, project_id, filename, content_type)
                   VALUES (?, ?, ?, ?, ?)""",
                (chunk["chunk_id"], chunk["content"], chunk["project_id"],
                 chunk["filename"], chunk["content_type"])
            )
        self.db.conn.commit()
    
    def delete_chunk(self, chunk_id: str):
        """
        Delete chunk from FTS index.
        
        Args:
            chunk_id: Chunk identifier to delete
        """
        self.db.conn.execute(
            "DELETE FROM chunks_fts WHERE chunk_id = ?",
            (chunk_id,)
        )
        self.db.conn.commit()
    
    def delete_project_chunks(self, project_id: str):
        """
        Delete all chunks for a project from FTS index.
        
        Args:
            project_id: Project ID
        """
        self.db.conn.execute(
            "DELETE FROM chunks_fts WHERE project_id = ?",
            (project_id,)
        )
        self.db.conn.commit()

    def delete_by_file_id(self, file_id: str):
        """
        Delete all FTS chunks for a file.
        
        Uses LIKE pattern matching on chunk_id which follows patterns:
        - chunk_{file_id}_{idx} for text chunks
        - img_{file_id}_{idx} for image chunks
        
        Args:
            file_id: File ID whose chunks to delete
        """
        self.db.conn.execute(
            "DELETE FROM chunks_fts WHERE chunk_id LIKE ? OR chunk_id LIKE ?",
            (f"chunk_{file_id}_%", f"img_{file_id}_%")
        )
        self.db.conn.commit()
    
    def search(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
        content_type_filter: Optional[str] = None,
        file_id: Optional[str] = None
    ) -> list[dict]:
        """
        Full-text search with BM25 ranking.
        
        Supports FTS5 query syntax:
        - Phrase matching: "exact phrase"
        - Boolean operators: AND, OR, NOT
        - Prefix matching: keyw*
        - Column filters: content:search_term
        
        Args:
            project_id: Project ID to filter
            query: Search query (FTS5 syntax)
            top_k: Number of results to return
            content_type_filter: Optional filter by content type
            file_id: Optional filter by file ID (using chunk_id LIKE pattern)
        
        Returns:
            List of dicts with id, content, _score (BM25), metadata
        """
        # Build WHERE clause
        where_clauses = ["project_id = ?"]
        params = [project_id]
        
        if content_type_filter:
            where_clauses.append("content_type = ?")
            params.append(content_type_filter)
        
        if file_id:
            where_clauses.append("(chunk_id LIKE ? OR chunk_id LIKE ?)")
            params.extend([f"chunk_{file_id}_%", f"img_{file_id}_%"])
        
        where_sql = " AND ".join(where_clauses)
        
        # FTS5 MATCH query with BM25 ranking
        # bm25() returns negative scores, so we use abs() for positive values
        cursor = self.db.conn.execute(
            f"""SELECT chunk_id, content, bm25(chunks_fts) as score
                FROM chunks_fts
                WHERE chunks_fts MATCH ? AND {where_sql}
                ORDER BY bm25(chunks_fts) DESC
                LIMIT ?""",
            [query] + params + [top_k]
        )
        
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row["chunk_id"],
                "content": row["content"],
                "_score": abs(row["score"]),  # BM25 scores are negative, make positive
                "metadata": {}  # FTS5 doesn't store metadata, fetch from DB if needed
            })
        
        return results
