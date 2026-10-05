"""python/routers/health.py — Service health check API.

Checks configuration and model availability for all external services:
  - LLM (external API)
  - VLM (external API)
  - Embedding (bge-m3 model)
  - Parsing (Docling models)

Service-level checks live in readiness.py (single source of truth, shared
with entry-API preflight validation); this endpoint additionally exposes a
feature readiness matrix (0.4lifecycle-design.md §7.2) so the frontend can
guide users to the exact missing dependency before they hit a runtime error.
"""
import logging

from fastapi import APIRouter

from readiness import compute_features, compute_service_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check():
    """Comprehensive health check: service status + feature readiness."""
    services = compute_service_status()
    features = compute_features(services)
    overall = "ok" if all(s["status"] == "ok" for s in services.values()) else "degraded"
    return {"status": overall, "services": services, "features": features}
