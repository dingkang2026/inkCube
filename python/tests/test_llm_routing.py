"""python/tests/test_llm_routing.py — llm/routing.py 档位制路由单测.

2026-10-19 档位制改造（路由键值 = 档位 standard/high，解析为档位槽模型名），
本文件覆盖：
  A. 档位解析：standard/high 命中各自槽位；空值按 standard；未知档位按 standard
  B. 槽位回退：选中档空 → 用另一槽；两槽全空 → model 为空串
  C. 覆盖语义：返回副本仅替换 model，其余键保留且原 dict 不被污染
  D. 键位完备：11 个 task_type 全量映射且 settings 键唯一（含 ppt 唯一默认 high）
  E. resolve_model 直调语义（task_agent 任务/规划路由消费）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.routing import _TASK_MODEL_KEYS, get_task_settings, resolve_model  # noqa: E402


# ── A. 档位解析 ─────────────────────────────────────────────

def test_standard_level_resolves_standard_slot():
    settings = {"standard_model": "glm-air", "high_model": "glm-max"}
    out = get_task_settings(settings, "rag")
    assert out["model"] == "glm-air"


def test_high_level_resolves_high_slot():
    settings = {"standard_model": "glm-air", "high_model": "glm-max", "ppt_model": "high"}
    out = get_task_settings(settings, "ppt")
    assert out["model"] == "glm-max"


def test_empty_level_defaults_to_standard():
    settings = {"standard_model": "glm-air", "high_model": "glm-max", "rag_model": ""}
    out = get_task_settings(settings, "rag")
    assert out["model"] == "glm-air"


def test_unknown_level_treated_as_standard():
    settings = {"standard_model": "glm-air", "high_model": "glm-max"}
    assert resolve_model(settings, "turbo") == "glm-air"


# ── B. 槽位回退 ─────────────────────────────────────────────

def test_standard_empty_falls_back_to_high():
    settings = {"standard_model": "", "high_model": "glm-max"}
    assert resolve_model(settings, "standard") == "glm-max"


def test_high_empty_falls_back_to_standard():
    settings = {"standard_model": "glm-air", "high_model": ""}
    out = get_task_settings(settings, "ppt")  # ppt 默认 high
    assert out["model"] == "glm-air"


def test_both_slots_empty_model_writes_empty_string():
    settings = {"standard_model": "", "high_model": ""}
    out = get_task_settings(settings, "rag")
    assert out["model"] == ""


def test_whitespace_only_slot_treated_as_empty():
    settings = {"standard_model": "  ", "high_model": "glm-max"}
    assert resolve_model(settings, "standard") == "glm-max"


# ── C. 覆盖语义 ─────────────────────────────────────────────

def test_routing_returns_copy_without_mutating_input():
    settings = {"standard_model": "glm-air", "high_model": "glm-max", "api_key": "k"}
    out = get_task_settings(settings, "report")
    assert out is not settings            # 返回副本
    assert out["model"] == "glm-air"
    assert out["api_key"] == "k"          # 仅覆盖 model，其余键保留
    assert "model" not in settings        # 原 dict 不被污染


def test_unknown_task_type_returns_settings_unchanged():
    settings = {"standard_model": "glm-air"}
    assert get_task_settings(settings, "nonexistent") is settings


# ── D. 键位完备 ─────────────────────────────────────────────

def test_task_type_keys_complete_and_unique():
    assert set(_TASK_MODEL_KEYS) == {
        "ingest", "lint", "dedup", "ledger_parse", "enrich",
        "report", "report_extract", "form_analyze", "form_fill", "rag",
        "ppt",
    }
    assert len(_TASK_MODEL_KEYS) == len(set(_TASK_MODEL_KEYS.values()))


def test_setting_key_mapping_samples():
    assert _TASK_MODEL_KEYS["enrich"] == "wiki_enrich_model"
    assert _TASK_MODEL_KEYS["report"] == "report_model"
    assert _TASK_MODEL_KEYS["ppt"] == "ppt_model"


def test_db_defaults_level_semantics():
    """db 默认值档位语义：除 ppt=high 外全部 standard；planner 空 = 单模型。"""
    from db.database import Database
    db = Database(db_path=":memory:")
    db.initialize()
    s = db.get_settings()
    assert s["ppt_model"] == "high"
    assert s["task_model"] == "standard"
    assert s["task_planner_model"] == ""
    for key in ("wiki_ingest_model", "wiki_lint_model", "wiki_dedup_model",
                "wiki_ledger_parse_model", "wiki_enrich_model",
                "report_model", "report_extract_model",
                "form_analyze_model", "form_fill_model", "rag_model"):
        assert s[key] == "standard", key
    # 档位槽开箱默认（2026-10-19 用户指定：glm-5.3-flash / glm-5.3）
    assert s["standard_model"] == "glm-5.3-flash"
    assert s["high_model"] == "glm-5.3"
    assert "vlm_model" not in s  # 独立 VLM 链路删除（2026-10-21），vlm 三键退出 settings
    assert "model" not in s  # 旧单模型键已废弃


@pytest.mark.parametrize("task_type", [
    "ingest", "lint", "dedup", "ledger_parse", "enrich",
    "report", "report_extract", "form_analyze", "form_fill", "rag", "ppt",
])
def test_every_task_type_routes(task_type):
    settings = {
        "standard_model": "glm-air", "high_model": "glm-max",
        _TASK_MODEL_KEYS[task_type]: "high",
    }
    out = get_task_settings(settings, task_type)
    assert out["model"] == "glm-max"


# ── E. resolve_model 直调 ───────────────────────────────────

def test_resolve_model_default_level_is_standard():
    settings = {"standard_model": "glm-air", "high_model": "glm-max"}
    assert resolve_model(settings) == "glm-air"


# ── F. 消费点路由覆盖（2026-10-20 漏网审计回归）──────────────
# 背景：档位制迁移后 get_settings() 不再含 model 键，漏接路由的调用点直构
# LLMClient 会落到硬编码兜底（旧为 gpt-4o，打智谱报 1211 模型不存在，
# 「设为定时任务」refine-prompt 409 根因）。

def test_llm_client_missing_model_key_defaults_to_empty():
    """client 兜底禁令：缺 model 键 → 空串（请求被网关拒，快速显性失败）。
    禁止复活硬编码模型名兜底——否则路由漏网伪装成「模型不存在」故障。"""
    from llm.client import LLMClient
    assert LLMClient({"api_key": "k"}).model == ""


def test_compile_ingest_client_carries_resolved_model():
    """Wiki 编译摄入经 ingest 档路由：传给 WikiIngestor 的 client 带解析后
    模型，settings 兼作配置容器（含 model 键 → ingest._chat_with_retry 再
    解析一次结果相同 → 复用 client 不再每次重建）。"""
    import asyncio
    from db.database import Database
    from knowledge.task_queue import KnowledgeWorker

    async def scenario():
        db = Database(db_path=":memory:")
        db.initialize()
        worker = KnowledgeWorker(db=db, data_dir=".")
        project = db.create_project("p")
        compile_task = db.create_wiki_compile_task(project["id"], "manual")
        captured: dict = {}

        async def fake_run(task_id, project_id, llm_client, settings):
            captured["client_model"] = llm_client.model
            captured["settings_model"] = settings.get("model")

        worker._run_per_file_ingest = fake_run
        await worker._process_compile(compile_task["id"])
        assert captured["client_model"] == "glm-5.3-flash"   # db 默认 standard 槽
        assert captured["settings_model"] == "glm-5.3-flash"

    asyncio.run(scenario())
