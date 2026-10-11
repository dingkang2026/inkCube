"""python/tests/test_knowledge_like_escape.py — LIKE 通配符转义回归测试。

回归背景：knowledge.py 所有用户可控输入（filename_search / search / file_id）
此前直接拼入 LIKE 模式，输入含 % / _ 时改变匹配语义（如 file_id=%abc 使
chunk_id LIKE '%abc%' 全表模糊匹配）。现统一经 _escape_like 转义 + ESCAPE '\\'。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers.knowledge import _escape_like


class TestEscapeLike:
    def test_plain_text_unchanged(self):
        """正常输入（uuid/file_id 常见字符）不受影响。"""
        assert _escape_like("abc-123") == "abc-123"
        assert _escape_like("file_ab12cd34") == "file\\_ab12cd34"

    def test_percent_escaped(self):
        assert _escape_like("50%") == "50\\%"

    def test_underscore_escaped(self):
        assert _escape_like("a_b") == "a\\_b"

    def test_backslash_escaped_first(self):
        """反斜杠先转义，避免破坏后续转义序列：\% → \\ + \\% = \\\\%。"""
        assert _escape_like("a\\") == "a\\\\"
        assert _escape_like("\\%") == "\\\\\\%"

    @pytest.mark.parametrize("raw", ["%", "_", "\\", "a%b_c\\d"])
    def test_roundtrip_literal_match(self, raw):
        """转义后接 %/% 包裹，仍只匹配字面值（内存库端到端）。"""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (name TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (raw,))
        conn.execute("INSERT INTO t VALUES ('unrelated')")
        pattern = f"%{_escape_like(raw)}%"
        rows = conn.execute(
            f"SELECT name FROM t WHERE name LIKE ? ESCAPE '\\'", (pattern,)
        ).fetchall()
        assert [r[0] for r in rows] == [raw]
