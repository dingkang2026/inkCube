"""任务轮次聚合分页回归（GET /usage/task/{id}，0.1dev-guide 列表分页统一约定）。

回归背景（2026-10）：
- rounds 增 limit/offset/has_more/total；分页作用于 GROUP BY 之后的轮次行；
  total = COUNT(DISTINCT round_id)（round_id NULL 的散调用不计轮次）。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.usage as usage
from db.database import Database


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def routed(db, monkeypatch):
    monkeypatch.setattr(usage, "get_db", lambda: db)
    return db


def _seed_rounds(database, task_id, rounds=3, rows_per_round=2):
    """每轮 rows_per_round 行 llm_usage；轮间 ts 递增保证 started_at ASC 确定。"""
    for r in range(rounds):
        for k in range(rows_per_round):
            minute = r * 10 + k
            database.conn.execute(
                "INSERT INTO llm_usage (ts, source, task_id, round_id, model,"
                " mode, prompt_tokens, completion_tokens, total_tokens)"
                " VALUES (?, 'task', ?, ?, 'model-a', 'full', 10, 5, 15)",
                (f"2026-01-01T00:{minute:02d}:00", task_id, f"round-{r}"),
            )
    database.conn.commit()


class TestUsageTaskRoundsPagination:
    def test_limit_zero_returns_all_rounds(self, routed):
        _seed_rounds(routed, "t1", rounds=3)
        result = asyncio.run(usage.usage_task_rounds("t1", limit=0))
        assert len(result["rounds"]) == 3
        assert result["has_more"] is False
        assert result["total"] == 3

    def test_limit_returns_page_and_has_more(self, routed):
        _seed_rounds(routed, "t1", rounds=5)
        result = asyncio.run(usage.usage_task_rounds("t1", limit=2, offset=0))
        assert len(result["rounds"]) == 2
        assert result["has_more"] is True
        assert result["total"] == 5

    def test_offset_paginates_across_rounds(self, routed):
        _seed_rounds(routed, "t1", rounds=5)
        page1 = asyncio.run(usage.usage_task_rounds("t1", limit=2, offset=0))
        page2 = asyncio.run(usage.usage_task_rounds("t1", limit=2, offset=2))
        ids1 = {r["round_id"] for r in page1["rounds"]}
        ids2 = {r["round_id"] for r in page2["rounds"]}
        assert ids1.isdisjoint(ids2)
        assert len(ids1 | ids2) == 4
        assert page2["has_more"] is True

    def test_total_counts_distinct_rounds_not_rows(self, routed):
        """total = 去重轮次数：每轮 3 行 × 2 轮 = 6 行，但 total == 2。"""
        _seed_rounds(routed, "t1", rounds=2, rows_per_round=3)
        result = asyncio.run(usage.usage_task_rounds("t1", limit=1, offset=0))
        assert result["total"] == 2

    def test_null_round_id_excluded_from_total(self, routed):
        """round_id NULL 的散调用不计轮次（WHERE round_id IS NOT NULL）。"""
        _seed_rounds(routed, "t1", rounds=2)
        routed.conn.execute(
            "INSERT INTO llm_usage (ts, source, task_id, round_id, model, mode,"
            " prompt_tokens, completion_tokens, total_tokens)"
            " VALUES (?, 'task', 't1', NULL, 'model-a', 'full', 1, 1, 2)",
            ("2026-01-01T09:00:00",),
        )
        routed.conn.commit()
        result = asyncio.run(usage.usage_task_rounds("t1", limit=0))
        assert result["total"] == 2
        assert all(r["round_id"] for r in result["rounds"])

    def test_task_isolation(self, routed):
        _seed_rounds(routed, "t1", rounds=2)
        _seed_rounds(routed, "t2", rounds=1)
        result = asyncio.run(usage.usage_task_rounds("t1", limit=0))
        assert result["total"] == 2
        assert {r["round_id"] for r in result["rounds"]} == {"round-0", "round-1"}
