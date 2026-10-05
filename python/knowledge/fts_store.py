"""python/knowledge/fts_store.py"""
from db.database import Database
from typing import Optional


def _contains_cjk(s: str) -> bool:
    """True if the string contains at least one CJK ideograph."""
    return any('\u4e00' <= ch <= '\u9fff' for ch in s)


def _like_escape(s: str) -> str:
    """Escape LIKE wildcards (percent, underscore, backslash) literally."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _bigrams(token: str) -> list[str]:
    """Split a token into 2-char sliding windows (single char if shorter).

    Without a word segmentation library, bigram substrings are the standard
    CJK approximation: they hit any document sharing a 2-char sequence with
    the query while avoiding the noise of single-character matching.
    """
    if len(token) <= 2:
        return [token]
    return [token[i:i + 2] for i in range(len(token) - 1)]


def _sanitize_fts_query(tokens: list[str]) -> str:
    """Build a safe FTS5 MATCH expression from plain latin tokens.

    The MATCH parameter is an FTS5 query *expression*, not plain text — raw
    user input containing syntax characters (?, *, ", (, :, AND/OR/NOT …)
    raises "fts5: syntax error". Tokens are wrapped in double quotes as
    phrases (everything inside quotes is matched literally); embedded
    double quotes must be stripped (illegal inside a phrase). Tokens are
    joined with implicit AND.
    """
    cleaned = [t.replace('"', '') for t in tokens]
    return " ".join(f'"{t}"' for t in cleaned if t)


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
        Full-text search, accepts arbitrary user text (chat messages, search
        box input, LLM search hints).

        Routing by script:
        - Queries containing CJK run on a LIKE substring path: the unicode61
          tokenizer merges consecutive CJK chars into ONE token, so FTS5
          MATCH can never hit a Chinese word (only the exact full sentence).
          Each token becomes bigram patterns (OR within a token, AND across
          tokens), ranked by hit count.
        - Pure latin/digit queries run on FTS5 MATCH with phrase-escaped
          tokens (BM25 ranking; syntax chars are matched literally).

        Args:
            project_id: Project ID to filter
            query: Search query (arbitrary user text, auto-escaped)
            top_k: Number of results to return
            content_type_filter: Optional filter by content type
            file_id: Optional filter by file ID (using chunk_id LIKE pattern)

        Returns:
            List of dicts with id, content, _score (BM25 or hit count),
            metadata
        """
        tokens = [t.replace('"', '') for t in query.split()]
        tokens = [t for t in tokens if t]
        if not tokens:
            return []
        if any(_contains_cjk(t) for t in tokens):
            return self._search_cjk_like(
                project_id, tokens, top_k, content_type_filter, file_id,
            )
        return self._search_fts_match(
            project_id, _sanitize_fts_query(tokens),
            top_k, content_type_filter, file_id,
        )

    def _filter_clauses(self, content_type_filter, file_id) -> tuple[list[str], list]:
        """Shared project/content-type/file-id WHERE clauses + params."""
        where_clauses = ["project_id = ?"]
        params = []
        if content_type_filter:
            where_clauses.append("content_type = ?")
            params.append(content_type_filter)
        if file_id:
            where_clauses.append("(chunk_id LIKE ? OR chunk_id LIKE ?)")
            params.extend([f"chunk_{file_id}_%", f"img_{file_id}_%"])
        return where_clauses, params

    def _search_fts_match(
        self, project_id: str, match_expr: str, top_k: int,
        content_type_filter: Optional[str], file_id: Optional[str],
    ) -> list[dict]:
        """Latin-script FTS5 MATCH path with BM25 ranking."""
        where_clauses, params = self._filter_clauses(content_type_filter, file_id)
        where_sql = " AND ".join(where_clauses)

        # FTS5 MATCH query with BM25 ranking
        # bm25() returns negative scores, so we use abs() for positive values
        cursor = self.db.conn.execute(
            f"""SELECT chunk_id, content, bm25(chunks_fts) as score
                FROM chunks_fts
                WHERE chunks_fts MATCH ? AND {where_sql}
                ORDER BY bm25(chunks_fts) DESC
                LIMIT ?""",
            [match_expr, project_id] + params + [top_k]
        )
        return [
            {
                "id": row["chunk_id"],
                "content": row["content"],
                "_score": abs(row["score"]),  # BM25 scores are negative
                "metadata": {},  # FTS5 doesn't store metadata
            }
            for row in cursor.fetchall()
        ]

    def _search_cjk_like(
        self, project_id: str, tokens: list[str], top_k: int,
        content_type_filter: Optional[str], file_id: Optional[str],
    ) -> list[dict]:
        """CJK LIKE-substring path (unicode61 cannot tokenize Chinese).

        Per token: bigram patterns joined with OR (any 2-char window hit
        recalls the chunk); across tokens: AND (every token must match).
        Results are ranked by total pattern hit count — HybridRetriever's
        RRF fusion only consumes ranks, so this stays compatible with the
        MATCH path. LIKE wildcards in user text are escaped.
        """
        where_clauses, params = self._filter_clauses(content_type_filter, file_id)

        groups: list[str] = []
        patterns: list[str] = []
        for tok in tokens:
            grams = _bigrams(tok) if _contains_cjk(tok) else [tok]
            pats = [f"%{_like_escape(g)}%" for g in grams]
            groups.append(
                "(" + " OR ".join([r"content LIKE ? ESCAPE '\'"] * len(pats)) + ")"
            )
            patterns.extend(pats)

        order_terms = " + ".join(
            [r"(content LIKE ? ESCAPE '\')"] * len(patterns)
        )
        where_sql = " AND ".join(where_clauses + groups)

        cursor = self.db.conn.execute(
            f"""SELECT chunk_id, content
                FROM chunks_fts
                WHERE {where_sql}
                ORDER BY ({order_terms}) DESC
                LIMIT ?""",
            [project_id] + params + patterns + patterns + [top_k],
        )
        results = []
        for row in cursor.fetchall():
            content = row["content"]
            hits = sum(1 for p in patterns if self._like_match(content, p))
            results.append({
                "id": row["chunk_id"],
                "content": content,
                "_score": hits,
                "metadata": {},
            })
        return results

    @staticmethod
    def _like_match(content: str, escaped_pattern: str) -> bool:
        """Python-side recheck of a LIKE pattern (case-insensitive for ASCII,
        mirrors SQLite LIKE) — used only for the display _score."""
        raw = escaped_pattern[1:-1].replace(r"\%", "%").replace(r"\_", "_").replace(r"\\", "\\")
        return raw.lower() in content.lower()
