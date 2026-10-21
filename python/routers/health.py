"""python/routers/health.py — Service health check API.

Checks configuration and model availability for all external services:
  - LLM (external API)
  - VLM (external API)
  - Web Search (Tavily / Zhipu, provider-configurable)
  - Embedding (bge-m3 model)
  - Parsing (Docling models)

Service-level checks live in readiness.py (single source of truth, shared
with entry-API preflight validation); this endpoint additionally exposes a
feature readiness matrix (0.4lifecycle-design.md §7.2) so the frontend can
guide users to the exact missing dependency before they hit a runtime error.
"""
import logging

from fastapi import APIRouter

from readiness import OVERALL_REQUIRED_SERVICES, compute_features, compute_service_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check():
    """Comprehensive health check: service status + feature readiness."""
    services = compute_service_status()
    features = compute_features(services)
    # overall 全必配聚合（readiness.OVERALL_REQUIRED_SERVICES，§7.2，2026-10-19
    # 用户拍板 llm/vlm/web_search/内部模型全部必配）：五项任一缺失即 degraded，
    # 红条 + 导航状态点提示面统一；入口拦截仍按功能矩阵（REQUIRED_SERVICES）
    overall = (
        "ok"
        if all(services[svc]["status"] == "ok" for svc in OVERALL_REQUIRED_SERVICES)
        else "degraded"
    )
    return {"status": overall, "services": services, "features": features}
