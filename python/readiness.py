"""python/readiness.py — 功能就绪度模型与入口校验（0.4lifecycle-design.md §7）。

单一真相：/health 的 services + features 段与入口 API 前置校验共用本模块，
依赖矩阵（功能 → 必需/可选依赖）只在此维护，避免两处实现漂移。

- compute_service_status(): 4 服务状态（llm/web_search/embedding/parsing）
- compute_features(): 功能就绪度矩阵（§7.2，missing 项带 action 锚点编码）
- ensure_ready(feature): 入口 API 前置校验（§7.3 第三层），
  缺必需依赖时抛对应 InkCubeError（CONFIG/RESOURCE 类），
  经全局 exception handler 转结构化响应 → 前端 useErrorHandler 弹错 + 跳设置。
"""
import json
import logging
import time

import deps
from errors import (
    EmbeddingModelMissing,
    InkCubeError,
    LLMNotConfigured,
    ParsingModelMissing,
)

logger = logging.getLogger(__name__)


def _get_setting(db, key: str) -> str:
    """Read a single setting value from DB, handling JSON-encoded values."""
    row = db.conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return ""
    val = row["value"]
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val or ""


# service -> 条目属性；action 锚点编码（前端 settingsSectionOfAction 映射为直达设置导航）。
# error 项随 services 下发（compute_service_status），供 SystemAlerts 红条明细直达
_SERVICE_META = {
    "llm": {"kind": "config", "id": "llm", "action": "settings:external"},
    "web_search": {"kind": "config", "id": "web_search", "action": "settings:webSearch"},
    "embedding": {"kind": "model", "id": "bge-m3", "action": "settings:internal"},
    "parsing": {"kind": "model", "id": "parsing", "action": "settings:internal"},
}


def compute_service_status() -> dict:
    """5 服务状态（服务视角）——/health services 段数据源。

    模型检查复用 ModelManager.verify_model()，与内部模型设置页判定一致。
    """
    db = deps.db
    services = {}

    # 1. LLM — check config exists（档位制：展示高配/标准模型名，判定仍只看 api_key/api_url）
    api_key = _get_setting(db, "api_key")
    api_url = _get_setting(db, "api_url")
    model_display = (_get_setting(db, "high_model") or _get_setting(db, "standard_model") or "").strip()
    if not api_key or not api_url:
        services["llm"] = {
            "status": "error",
            "code": "LLM_NOT_CONFIGURED",
            "message": "LLM API 未配置",
            "suggestion": "前往 设置 > 外部模型 配置 LLM API Key",
            "action": _SERVICE_META["llm"]["action"],
        }
    else:
        services["llm"] = {
            "status": "ok",
            "message": f"{model_display} — 已配置" if model_display else "已配置（未命名模型）",
        }

    # 2. 网络搜索 — 互联网检索（必配，2026-10-19 用户拍板与 llm 同档：缺失进
    # OVERALL_REQUIRED_SERVICES 红条与导航状态点；入口拦截仍不因它触发——报告
    # 离线可生成，提示面强、功能面宽容）。供应商用户二选一（tavily/zhipu，
    # 2026-10-19 供应商化）：无默认——未选择或所选供应商 Key 为空均视为未配置
    # （判定与 retrieval.web_search_cfg_from_settings 同口径；数据源不同——此处 db
    # 逐键、彼处 settings dict 快照——不共用函数，避免 readiness → report_generator
    # 依赖耦合；改动判定必须同步两处）
    provider = str(_get_setting(db, "web_search_provider") or "").strip()
    if provider == "zhipu":
        key = str(_get_setting(db, "zhipu_search_key") or "").strip()
    elif provider == "tavily":
        key = str(_get_setting(db, "tavily_key") or "").strip()
    else:
        key = ""
    if not provider or not key:
        services["web_search"] = {
            "status": "error",
            "code": "WEB_SEARCH_NOT_CONFIGURED",
            "message": "网络搜索未配置",
            "suggestion": "前往 设置 > 基础配置 > 网络搜索 选择供应商并填写 API Key",
            "action": _SERVICE_META["web_search"]["action"],
        }
    else:
        label = "智谱" if provider == "zhipu" else "Tavily"
        services["web_search"] = {"status": "ok", "message": f"网络搜索（{label}）— 已配置"}

    # 3 & 4. Embedding + Parsing — delegate to ModelManager.verify_model()
    mgr = getattr(deps, "model_manager", None)
    if mgr is None:
        logger.warning("model_manager not initialized yet, reporting models as unavailable")
        embedding_ok = False
        parsing_ok = False
    else:
        try:
            embedding_ok = mgr.verify_model("bge-m3", mgr.get_model_path("bge-m3"))
            parsing_ok = (
                mgr.verify_model("docling-layout-heron", mgr.get_model_path("docling-layout-heron"))
                and mgr.verify_model("tableformer-v2", mgr.get_model_path("tableformer-v2"))
            )
        except Exception as e:
            logger.warning("Model verification failed: %s", e)
            embedding_ok = False
            parsing_ok = False

    if not embedding_ok:
        services["embedding"] = {
            "status": "error",
            "code": "EMBEDDING_MODEL_MISSING",
            "message": "Embedding 模型未下载",
            "suggestion": "前往 设置 > 内部模型 下载 bge-m3 模型",
            "action": _SERVICE_META["embedding"]["action"],
        }
    else:
        services["embedding"] = {
            "status": "ok",
            "message": "bge-m3 — 已加载",
        }

    if not parsing_ok:
        services["parsing"] = {
            "status": "error",
            "code": "PARSING_MODEL_MISSING",
            "message": "文档解析模型缺失",
            "suggestion": "前往 设置 > 内部模型 下载解析模型",
            "action": _SERVICE_META["parsing"]["action"],
        }
    else:
        services["parsing"] = {
            "status": "ok",
            "message": "文档解析 — 就绪",
        }

    return services


# ── 功能依赖矩阵（§7.2）──────────────────────────────────────
# feature -> {required: [service...], optional: [(service, impact)...]}
# service 为 compute_service_status 的 key（图片描述走 llm 服务，多模态前提由
# 设置页 hint 与调用侧错误引导承担，不再单列 vlm 依赖）。

_FEATURE_MATRIX: dict[str, dict] = {
    "file_parsing": {
        "required": ["parsing", "embedding"],
        "optional": [],
    },
    "wiki_compile": {"required": ["llm", "embedding"], "optional": []},
    "report": {"required": ["llm", "embedding"], "optional": []},
    "task": {"required": ["llm"], "optional": []},
    "knowledge_search": {"required": ["embedding"], "optional": []},
    # 填表（2.4 §2.4）：发起填表需检索（嵌入）+ LLM；模板分析/重解析仅 LLM
    "form_fill": {"required": ["llm", "embedding"], "optional": []},
    "form_analysis": {"required": ["llm"], "optional": []},
}

# overall 聚合口径（/health status，health.py 消费）。REQUIRED_SERVICES = 功能矩阵
# required 依赖并集（= llm + embedding + parsing），供 ensure_ready/功能判定；
# OVERALL_REQUIRED_SERVICES = REQUIRED_SERVICES ∪ {web_search}（2026-10-19 用户
# 拍板全必配：任一缺失即 degraded，红条 + 导航状态点提示面统一；推翻同日早前
# 「不因可选项缺失显示红条」口径——红条常驻是必配未完成的真实状态，属预期）。
# 功能面不变：web_search 不进矩阵——入口拦截不因它触发，报告离线可用
# （提示面强、功能面宽容）
REQUIRED_SERVICES = frozenset(
    svc for spec in _FEATURE_MATRIX.values() for svc in spec["required"]
)
OVERALL_REQUIRED_SERVICES = REQUIRED_SERVICES | {"web_search"}


def compute_features(services: dict | None = None) -> dict:
    """功能就绪度矩阵（功能视角）——/health features 段数据源（§7.2）。"""
    if services is None:
        services = compute_service_status()

    features = {}
    for feature, spec in _FEATURE_MATRIX.items():
        missing = [
            dict(_SERVICE_META[svc])
            for svc in spec["required"]
            if services.get(svc, {}).get("status") != "ok"
        ]
        optional_missing = []
        for svc, impact in spec["optional"]:
            if services.get(svc, {}).get("status") != "ok":
                entry = dict(_SERVICE_META[svc])
                entry["impact"] = impact
                optional_missing.append(entry)
        features[feature] = {
            "ready": not missing,
            "missing": missing,
            "optional_missing": optional_missing,
        }
    return features


# ── 入口 API 前置校验（§7.3 第三层）─────────────────────────────
# ensure_ready 高频调用（每次入口请求），readiness 为低频数据 → 10s TTL 缓存。
# /health 端点不走缓存（用户刚改完配置立即反馈）。

_features_cache: dict | None = None
_features_cache_at: float = 0.0
_FEATURES_TTL = 10.0


def get_features_cached() -> dict:
    """带短 TTL 缓存的就绪度（入口校验路径）。"""
    global _features_cache, _features_cache_at
    now = time.monotonic()
    if _features_cache is None or now - _features_cache_at > _FEATURES_TTL:
        _features_cache = compute_features()
        _features_cache_at = now
    return _features_cache


def ensure_ready(feature: str) -> None:
    """入口 API 前置校验：缺必需依赖时抛对应 InkCubeError，可选依赖不拦截。

    调用约定：放在端点函数 try 块之外（InkCubeError 直接冒泡到全局 handler，
    不被 except Exception → HTTPException(500) 吞掉结构化字段）。
    """
    features = get_features_cached()
    info = features.get(feature)
    if info is None:
        logger.warning("ensure_ready: unknown feature %s, skipping check", feature)
        return
    if info["ready"]:
        return

    first = info["missing"][0]
    dep_id = first["id"]
    if dep_id == "llm":
        raise LLMNotConfigured()
    if dep_id == "bge-m3":
        raise EmbeddingModelMissing()
    if dep_id == "parsing":
        raise ParsingModelMissing()
    # 兜底：矩阵新增依赖但未映射错误类时仍给出结构化 CONFIG 错误
    raise InkCubeError(
        f"{feature.upper()}_NOT_READY", "CONFIG",
        f"功能依赖未就绪: {dep_id}",
        "前往 设置 检查外部模型与内部模型",
    )
