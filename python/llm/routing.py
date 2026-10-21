"""python/llm/routing.py — 全管线统一的 LLM 任务路由（档位制）.

2026-10-19 档位制改造（替代原「管线级模型名覆盖」）：路由键值 = 档位
（"standard" | "high"，空值按 standard），解析为对应档位槽的模型名后写入
settings["model"]——LLMClient 与全部消费点（wiki/reports/forms/rag/agent）
契约不变，仍按「派生 dict → 构造新 client」（D3 唯一差异化模式）。

档位槽（外部模型 > 模型配置）：standard_model = 标准模型（默认档），
high_model = 高配模型。两槽互为回退：选中档为空 → 用另一槽；全空 →
model 写空串（请求将被网关拒，入口已被 readiness LLM_NOT_CONFIGURED 拦截）。
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Mapping from pipeline/task type to settings key for model level (standard/high)
_TASK_MODEL_KEYS: dict[str, str] = {
    # Wiki（2.6 §12.2.13，2026-10-15 泛化入本表）
    "ingest": "wiki_ingest_model",               # 摄入——深度理解与页面生成
    "lint": "wiki_lint_model",                   # 语义 Lint——轻量分析，快速响应
    "dedup": "wiki_dedup_model",                 # 去重——JSON 输出，中等能力
    "ledger_parse": "wiki_ledger_parse_model",   # 台账解析（轻模型；JSON 输出）
    "enrich": "wiki_enrich_model",               # 链接补全——轻量结构化
    # 报告
    "report": "report_model",                    # 报告生成——长文写作
    "report_extract": "report_extract_model",    # 模板提取——样例结构识别
    # 填表
    "form_analyze": "form_analyze_model",        # 模板分析——结构提取与语义映射
    "form_fill": "form_fill_model",              # 填表执行——按检索资料生成字段值
    # RAG
    "rag": "rag_model",                          # 知识库问答
    # PPT（2.3：ppt-master 工作流在任务对话内执行；唯一默认 high 的业务线）
    "ppt": "ppt_model",
}

_SLOT_KEYS = {"standard": "standard_model", "high": "high_model"}


def resolve_model(settings: dict[str, Any], level: str = "standard") -> str:
    """档位 → 模型名。未知档位按 standard；两槽互为回退，全空返回空串。"""
    slot = _SLOT_KEYS.get(level, "standard_model")
    other = "high_model" if slot == "standard_model" else "standard_model"
    return (str(settings.get(slot) or "").strip()
            or str(settings.get(other) or "").strip())


def get_task_settings(settings: dict[str, Any], task_type: str) -> dict[str, Any]:
    """Get pipeline-specific LLM settings（档位制）.

    Resolves the task's configured level to a concrete model name and writes
    it into settings["model"]. Returns a copy; the input dict is never mutated.

    Args:
        settings: Base application settings dict.
        task_type: One of _TASK_MODEL_KEYS keys.

    Returns:
        Settings dict with settings["model"] set to the resolved model name.
    """
    settings_key = _TASK_MODEL_KEYS.get(task_type)
    if not settings_key:
        return settings

    level = str(settings.get(settings_key) or "standard")
    task_settings = dict(settings)
    task_settings["model"] = resolve_model(settings, level)
    logger.info("LLM task routing: %s → level=%s model=%r",
                task_type, level, task_settings["model"])
    return task_settings
