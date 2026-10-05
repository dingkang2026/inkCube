"""python/readiness.py — 功能就绪度模型与入口校验（0.4lifecycle-design.md §7）。

单一真相：/health 的 services + features 段与入口 API 前置校验共用本模块，
依赖矩阵（功能 → 必需/可选依赖）只在此维护，避免两处实现漂移。

- compute_service_status(): 4 服务状态（llm/vlm/embedding/parsing）
- compute_features(): 功能就绪度矩阵（§5.2，missing 项带 action 锚点编码）
- ensure_ready(feature): 入口 API 前置校验（§5.3 第三层），
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


def compute_service_status() -> dict:
    """4 服务状态（服务视角）——/health services 段数据源。

    模型检查复用 ModelManager.verify_model()，与内部模型设置页判定一致。
    """
    db = deps.db
    services = {}

    # 1. LLM — check config exists
    api_key = _get_setting(db, "api_key")
    api_url = _get_setting(db, "api_url")
    model = _get_setting(db, "model")
    if not api_key or not api_url:
        services["llm"] = {
            "status": "error",
            "code": "LLM_NOT_CONFIGURED",
            "message": "LLM API 未配置",
            "suggestion": "前往 设置 > 外部模型 配置 LLM API Key",
        }
    else:
        services["llm"] = {
            "status": "ok",
            "message": f"{model} — 已配置",
        }

    # 2. VLM — check config exists
    vlm_key = _get_setting(db, "vlm_api_key")
    vlm_url = _get_setting(db, "vlm_api_url")
    vlm_model = _get_setting(db, "vlm_model")
    if not vlm_key or not vlm_url:
        services["vlm"] = {
            "status": "error",
            "code": "VLM_NOT_CONFIGURED",
            "message": "VLM API 未配置",
            "suggestion": "前往 设置 > 外部模型 > VLM 配置 API Key",
        }
    else:
        services["vlm"] = {
            "status": "ok",
            "message": f"{vlm_model or 'glm-4v'} — 已配置",
        }

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
        }
    else:
        services["parsing"] = {
            "status": "ok",
            "message": "文档解析 — 就绪",
        }

    return services


# ── 功能依赖矩阵（§5.2）──────────────────────────────────────
# feature -> {required: [service...], optional: [(service, impact)...]}
# service 为 compute_service_status 的 key；VLM 仅 file_parsing 需要（图片描述），缺失降级不阻断。

_FEATURE_MATRIX: dict[str, dict] = {
    "file_parsing": {
        "required": ["parsing", "embedding"],
        "optional": [("vlm", "image_description")],
    },
    "wiki_compile": {"required": ["llm", "embedding"], "optional": []},
    "report": {"required": ["llm", "embedding"], "optional": []},
    "task": {"required": ["llm"], "optional": []},
    "knowledge_search": {"required": ["embedding"], "optional": []},
}

# service -> missing 条目属性；action 锚点编码（前端映射为直达设置导航）
_SERVICE_META = {
    "llm": {"kind": "config", "id": "llm", "action": "settings:external"},
    "vlm": {"kind": "config", "id": "vlm", "action": "settings:external"},
    "embedding": {"kind": "model", "id": "bge-m3", "action": "settings:internal"},
    "parsing": {"kind": "model", "id": "parsing", "action": "settings:internal"},
}


def compute_features(services: dict | None = None) -> dict:
    """功能就绪度矩阵（功能视角）——/health features 段数据源（§5.2）。"""
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


# ── 入口 API 前置校验（§5.3 第三层）─────────────────────────
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
