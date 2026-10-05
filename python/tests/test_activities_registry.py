"""tests/test_activities_registry.py — 统一活动注册表单元测试（0.4lifecycle-design.md §6.1）。

覆盖：注册合并 / 同 kind 覆盖 / 注销 / 单源异常隔离降级（不抛、不影响其余源）/ 聚合端点经注册表取数。
"""
import asyncio

import pytest

import activities_registry as registry
import deps


class _StubDB:
    """notifications 读取 stub（db fixture 在 test_activities.py 内，不共享）。"""

    def list_unread_notifications(self):
        return []

    def list_recent_notifications(self, limit):
        return []


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """测试隔离：把注册表换成临时空表，结束后由 monkeypatch 还原原表
    （保留其他模块 import 时的注册副作用，不污染后续用例）。"""
    monkeypatch.setattr(registry, "_providers", {})
    yield


class TestRegisterAndSnapshot:
    def test_snapshot_merges_registered_sources(self):
        registry.register(registry.FunctionSource("file", lambda: [{"entity_type": "file", "count": 2}]))
        registry.register(registry.FunctionSource("report", lambda: [{"entity_type": "report", "entity_id": "r1"}]))

        items = registry.snapshot()
        assert len(items) == 2
        assert {i["entity_type"] for i in items} == {"file", "report"}

    def test_empty_registry_returns_empty(self):
        assert registry.snapshot() == []

    def test_same_kind_overrides(self):
        registry.register(registry.FunctionSource("task", lambda: [{"entity_type": "task", "entity_id": "old"}]))
        registry.register(registry.FunctionSource("task", lambda: [{"entity_type": "task", "entity_id": "new"}]))

        items = registry.snapshot()
        assert [i["entity_id"] for i in items] == ["new"]

    def test_unregister_silent_on_missing(self):
        registry.unregister("nonexistent")  # 不抛
        registry.register(registry.FunctionSource("file", lambda: []))
        registry.unregister("file")
        assert registry.snapshot() == []

    def test_registered_kinds_sorted(self):
        registry.register(registry.FunctionSource("task", lambda: []))
        registry.register(registry.FunctionSource("file", lambda: []))
        assert registry.registered_kinds() == ["file", "task"]


class TestIsolatedDegradation:
    def test_single_source_failure_degrades_to_empty_only_for_that_kind(self):
        """单源异常：只影响本源，其余源照常返回，整体不抛（0.4 §6.1 契约）。"""
        def boom():
            raise RuntimeError("source exploded")

        registry.register(registry.FunctionSource("file", boom))
        registry.register(registry.FunctionSource("report", lambda: [{"entity_type": "report", "entity_id": "r1"}]))

        items = registry.snapshot()
        assert [i["entity_id"] for i in items] == ["r1"]

    def test_fetch_reference_resolved_at_call_time(self):
        """FunctionSource 持有的 lambda 在调用时解析模块名（monkeypatch 兼容契约）。"""
        counter = {"calls": 0}

        def original():
            counter["calls"] += 1
            return [{"entity_type": "file"}]

        registry.register(registry.FunctionSource("file", lambda: original()))
        registry.snapshot()
        assert counter["calls"] == 1


class TestRoutersWiring:
    def test_status_endpoint_reads_registry(self, monkeypatch):
        """聚合端点活跃侧确实经注册表取数（0.4 §6.1）：注入单一 fake 源，
        /activities/status 的 active/summary 应完全来自该源，不再直调 _active_*。"""
        from routers import activities as activities_router

        monkeypatch.setattr(deps, "db", _StubDB())
        monkeypatch.setattr(
            registry,
            "_providers",
            {"file": registry.FunctionSource("file", lambda: [{"entity_type": "file", "count": 7}])},
        )

        result = asyncio.run(activities_router.activities_status())
        assert result["summary"]["active_files"] == 7
        assert result["summary"]["downloading_models"] == 0  # 其余源不在注册表 → 不取数
