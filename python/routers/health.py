"""python/routers/health.py — Service health check API.

Checks configuration and model availability for all external services:
  - LLM (external API)
  - VLM (external API)
  - Embedding (bge-m3 model)
  - Parsing (Docling models)

Model availability reuses ModelManager.verify_model() to stay consistent
with the Internal Model settings page.
"""
import json
import logging

from fastapi import APIRouter

import deps

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


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


@router.get("/health")
async def health_check():
    """Comprehensive health check of all services."""
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
    #    This guarantees the same result as the Internal Model settings page.
    mgr = getattr(deps, 'model_manager', None)
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

    # 4. Parsing — check Docling models
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

    overall = "ok" if all(s["status"] == "ok" for s in services.values()) else "degraded"
    return {"status": overall, "services": services}
