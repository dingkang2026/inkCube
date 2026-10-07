"""Regression: 0-chunk parses must complete as done, not error.

Incident: an empty (0-byte) txt parsed to 0 chunks and _store_chunks passed
empty lists to ChromaDB, whose add() rejects empty input ("Non-empty lists
are required for ['ids', 'metadatas', 'documents'] in add.") — surfacing an
opaque error for a successful-but-empty parse. The short-circuit keeps
0-chunk files on the done path; the empty-content notice rides the warnings
channel (0.4 §7.3 v3.6).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from knowledge.doc_parser import ChunkInfo, ParseResult
from knowledge.pipeline import ProcessingPipeline


def _pipeline() -> ProcessingPipeline:
    # 跳过 __init__（避免加载嵌入模型），仅注入双写依赖
    p = ProcessingPipeline.__new__(ProcessingPipeline)
    p.vector_store = MagicMock()
    p.fts_store = MagicMock()
    return p


class TestStoreChunksEmpty:
    def test_empty_chunks_skip_both_stores(self):
        """0 chunks → 双写都不触发（ChromaDB 空列表 add 会抛异常）。"""
        p = _pipeline()
        p._store_chunks(
            ParseResult(text_chunks=[], images=[], metadata={}),
            "p1", "file_x", "empty.txt",
        )
        p.vector_store.add_documents.assert_not_called()
        p.fts_store.add_chunks_batch.assert_not_called()

    def test_nonempty_chunks_still_write_both_stores(self):
        """非空 chunks → 双写正常触发（短路不得误伤正常路径）。"""
        p = _pipeline()
        chunk = ChunkInfo(
            text="hello", markdown="hello", content_type="paragraph",
            section_path="", page=None,
        )
        p._store_chunks(
            ParseResult(text_chunks=[chunk], images=[], metadata={}),
            "p1", "file_x", "a.txt",
        )
        p.vector_store.add_documents.assert_called_once()
        p.fts_store.add_chunks_batch.assert_called_once()
        _, kwargs = p.vector_store.add_documents.call_args
        assert kwargs["ids"] == ["chunk_file_x_0"]
