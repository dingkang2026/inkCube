"""wiki.profile_service 单元测试（2.6 §4.8 编译后产业画像提取）.

覆盖：
- _clean_entries 白名单清洗（strip / chain 必填 / 非 dict 剔除 / 同批去重 /
  上限 5 / 无 JSON 数组抛错）
- on_compile_complete_sync：成功回写（wiki_compile）/ manual 短路不触 LLM /
  LLM 失败不阻断（旧值保留 + 活动不残留）/ 无素材不动现状 / 空识别结果落
  '[]'（与 NULL=未提取区分）/ 占坑窗口内 manual 复检跳过回写 / 已在途并发守卫
- run_manual_extract_sync：覆盖 manual / 失败吞异常
- try_acquire / fetch_running：端点 409 语义与活动快照契约

LLM 依赖经 monkeypatch profile_service._extract_entries 隔离（不触网，
§2.6）；db fixture 同 test_settings_schema_sync 模式（tmp_path + initialize）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import wiki.profile_service as ps
from db.database import Database


@pytest.fixture()
def db(tmp_path):
    database = Database(str(tmp_path / "profile_test.db"))
    database.initialize()
    yield database
    database.conn.close()


@pytest.fixture(autouse=True)
def clean_running():
    """_RUNNING 是模块级进程态，用例前后必须清空（占坑残留会污染后续用例）。"""
    with ps._LOCK:
        ps._RUNNING.clear()
    yield
    with ps._LOCK:
        ps._RUNNING.clear()


def _make_project(db, overview: str = "") -> str:
    proj = db.create_project("Acme", seed_playbook=False)
    pid = proj["id"]
    if overview:
        db.conn.execute(
            "UPDATE projects SET wiki_overview = ? WHERE id = ?", (overview, pid))
        db.conn.commit()
    return pid


def _add_page(db, pid: str, page_key: str, content: str) -> None:
    db.conn.execute(
        "INSERT INTO wiki_pages (id, project_id, page_key, title, content, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, '2026-10-25', '2026-10-25')",
        (f"wp-{page_key}", pid, page_key, page_key, content),
    )
    db.conn.commit()


class TestCleanEntries:
    def test_strips_fields_and_allows_empty_segment(self):
        out = ps._clean_entries(
            '[{"chain": " 具身智能 ", "segment": " 四足机器狗 "},'
            ' {"chain":"机器人","segment":""}]')
        assert out == [{"chain": "具身智能", "segment": "四足机器狗"},
                       {"chain": "机器人", "segment": ""}]

    def test_extracts_array_from_noisy_llm_output(self):
        """LLM 常见的 ```json 围栏与前后客套话不碍解析（正则取 [..]）。"""
        raw = '好的，以下是提取结果：\n```json\n[{"chain":"新能源","segment":"储能"}]\n```'
        assert ps._clean_entries(raw) == [{"chain": "新能源", "segment": "储能"}]

    def test_drops_entries_without_chain_and_non_dict(self):
        out = ps._clean_entries(
            '[{"segment":"无链条"}, "文本", 42, {"chain":"", "segment":"x"},'
            ' {"chain":"有效"}]')
        assert out == [{"chain": "有效", "segment": ""}]

    def test_dedupes_and_caps_at_five(self):
        """同批去重 + 上限 5（重复元素被合并，不挤占唯一条目名额）。"""
        items = [{"chain": f"链{i}", "segment": ""} for i in range(7)]
        items.insert(2, {"chain": "链0", "segment": ""})  # 复述同一链条
        out = ps._clean_entries(json.dumps(items, ensure_ascii=False))
        assert [e["chain"] for e in out] == ["链0", "链1", "链2", "链3", "链4"]

    def test_raises_without_json_array(self):
        with pytest.raises(ValueError):
            ps._clean_entries("抱歉，资料中未找到可靠的产业链信息。")


class TestAcquireAndSnapshot:
    def test_acquire_exclusive_and_fetch_running_snapshot(self):
        assert ps.try_acquire("p1") is True
        assert ps.try_acquire("p1") is False  # 已在途 → 端点转 409 的判定源
        snap = ps.fetch_running()
        assert len(snap) == 1
        assert snap[0]["entity_type"] == "profile_extract"
        assert snap[0]["project_id"] == "p1"
        with ps._LOCK:
            ps._RUNNING.pop("p1")
        assert ps.fetch_running() == []


class TestOnCompileComplete:
    def test_success_writes_profile_and_clears_running(self, db, monkeypatch):
        pid = _make_project(db, overview="公司主营四足机器人，隶属具身智能产业链。")
        monkeypatch.setattr(
            ps, "_extract_entries",
            lambda overview, pages, settings:
                [{"chain": "具身智能", "segment": "四足机器狗"}])
        ps.on_compile_complete_sync(db, pid, {})
        proj = db.get_project(pid)
        assert json.loads(proj["industry_profile"]) == [
            {"chain": "具身智能", "segment": "四足机器狗"}]
        assert proj["profile_source"] == "wiki_compile"
        assert ps.fetch_running() == []  # finally 清理，活动不残留

    def test_skips_manual_source_without_calling_llm(self, db, monkeypatch):
        """manual 保护第一重：占坑前短路，人工修正编译不覆盖（2.1 §4.5）。
        用调用计数断言而非 raise（挂钩吞 Exception，raise-AE 会被吞掉失.test效）。"""
        pid = _make_project(db, overview="有素材")
        db.update_project(pid, profile_source="manual")
        called = []
        monkeypatch.setattr(
            ps, "_extract_entries", lambda *a: called.append(a))
        ps.on_compile_complete_sync(db, pid, {})
        assert called == []                            # 未触 LLM
        assert db.get_project(pid)["industry_profile"] is None  # 旧值（NULL）原样

    def test_llm_failure_keeps_null_and_does_not_raise(self, db, monkeypatch):
        """失败不阻断编译主流程：吞异常 + 旧值（NULL）保留 + 活动不残留。"""
        pid = _make_project(db, overview="素材")

        def _boom(overview, pages, settings):
            raise RuntimeError("LLM 超时")

        monkeypatch.setattr(ps, "_extract_entries", _boom)
        ps.on_compile_complete_sync(db, pid, {})
        assert db.get_project(pid)["industry_profile"] is None
        assert ps.fetch_running() == []

    def test_llm_failure_keeps_previous_profile(self, db, monkeypatch):
        """已有画像时提取失败 → 保旧值不清空（宁旧勿空，下次编译自动重试）。"""
        pid = _make_project(db, overview="素材")
        db.update_project(
            pid, industry_profile='[{"chain": "旧链", "segment": ""}]',
            profile_source="wiki_compile")

        def _boom(overview, pages, settings):
            raise RuntimeError("LLM 失败")

        monkeypatch.setattr(ps, "_extract_entries", _boom)
        ps.on_compile_complete_sync(db, pid, {})
        assert db.get_project(pid)["industry_profile"] == \
            '[{"chain": "旧链", "segment": ""}]'

    def test_no_material_is_noop(self, db, monkeypatch):
        """无 overview 且无内容页：不动现状，不产空提取（§4.8）。"""
        pid = _make_project(db)
        called = []
        monkeypatch.setattr(
            ps, "_extract_entries", lambda *a: called.append(a))
        ps.on_compile_complete_sync(db, pid, {})
        assert called == []
        assert db.get_project(pid)["industry_profile"] is None

    def test_page_only_material_feeds_prompt_and_persists_empty_array(
            self, db, monkeypatch):
        """仅页面素材（无 overview）也触发提取；取材只含有内容页；
        识别不出落 '[]' 而非 NULL（'[]'=已提取未识别，前端空态文案不同）。"""
        pid = _make_project(db)
        _add_page(db, pid, "company", "公司位于苏州工业园区。")
        seen = {}

        def fake(overview, pages, settings):
            seen["overview"], seen["pages"] = overview, pages
            return []

        monkeypatch.setattr(ps, "_extract_entries", fake)
        ps.on_compile_complete_sync(db, pid, {})
        assert seen["overview"] == ""
        assert [p["page_key"] for p in seen["pages"]] == ["company"]
        proj = db.get_project(pid)
        assert proj["industry_profile"] == "[]"
        assert proj["profile_source"] == "wiki_compile"

    def test_manual_edit_during_run_blocks_writeback(self, db, monkeypatch):
        """manual 保护第二重：占坑窗口内用户置 manual → 回写前复检跳过
        （竞态窗口缩到单条 UPDATE）。"""
        pid = _make_project(db, overview="素材")

        def _edit_during_run(overview, pages, settings):
            db.update_project(pid, profile_source="manual")
            return [{"chain": "提取结果", "segment": ""}]

        monkeypatch.setattr(ps, "_extract_entries", _edit_during_run)
        ps.on_compile_complete_sync(db, pid, {})
        proj = db.get_project(pid)
        assert proj["profile_source"] == "manual"
        assert proj["industry_profile"] is None  # 提取结果被丢弃，用户编辑优先

    def test_already_running_is_silent_skip(self, db, monkeypatch):
        """并发守卫：端点已受理在途时编译挂钩静默跳过，不重复提取；且不误删
        他方占坑（review M-1：finally 只清自己占的坑，否则单飞被击穿）。"""
        pid = _make_project(db, overview="素材")
        called = []
        monkeypatch.setattr(
            ps, "_extract_entries", lambda *a: called.append(a))
        assert ps.try_acquire(pid) is True
        ps.on_compile_complete_sync(db, pid, {})
        assert called == []
        # 他方（端点路径）持坑幸存：此刻 try_acquire 仍 False → 单飞未被击穿
        assert ps.try_acquire(pid) is False
        with ps._LOCK:
            ps._RUNNING.pop(pid)


class TestManualExtract:
    def test_overrides_manual(self, db, monkeypatch):
        """显式「重新提取」覆盖 manual（与编译自动提取的守卫语义相反）。"""
        pid = _make_project(db, overview="素材")
        db.update_project(
            pid, industry_profile='[{"chain": "旧", "segment": ""}]',
            profile_source="manual")
        monkeypatch.setattr(
            ps, "_extract_entries",
            lambda overview, pages, settings:
                [{"chain": "新链", "segment": "细分"}])
        ps.run_manual_extract_sync(db, pid, {})
        proj = db.get_project(pid)
        assert json.loads(proj["industry_profile"]) == [
            {"chain": "新链", "segment": "细分"}]
        assert proj["profile_source"] == "wiki_compile"

    def test_failure_swallows_and_releases(self, db, monkeypatch):
        """受理已成立（端点已返回 accepted），失败只吞异常：画像保旧值 +
        活动释放（前端靠心跳看到活动消失）。"""
        pid = _make_project(db, overview="素材")
        db.update_project(
            pid, industry_profile='[{"chain": "旧", "segment": ""}]',
            profile_source="manual")

        def _boom(overview, pages, settings):
            raise RuntimeError("LLM 失败")

        monkeypatch.setattr(ps, "_extract_entries", _boom)
        ps.run_manual_extract_sync(db, pid, {})  # 不上抛
        assert db.get_project(pid)["industry_profile"] == \
            '[{"chain": "旧", "segment": ""}]'
        assert ps.fetch_running() == []
