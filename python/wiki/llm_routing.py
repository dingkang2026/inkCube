"""python/wiki/llm_routing.py — LLM task routing for Wiki operations.

Per §12.2.13 of 2.6wiki-design.md.

Different tasks use different LLM configurations:
  - ingest: high-quality model (needs deep understanding + generation)
  - lint: lightweight model (simple analysis, fast response)
  - dedup: medium model (JSON output, moderate capability)
  - chat: user's default model (interactive, low latency)

Users can configure task-specific models in settings.
When a task-specific model is not set (empty string), the global default is used.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Mapping from task type to settings key for model override
_TASK_MODEL_KEYS: dict[str, str] = {
    "ingest": "wiki_ingest_model",
    "lint": "wiki_lint_model",
    "dedup": "wiki_dedup_model",
}


def get_task_settings(settings: dict[str, Any], task_type: str) -> dict[str, Any]:
    """Get task-specific LLM settings.

    If a task-specific model is configured (non-empty string),
    returns a copy of settings with the model overridden.
    Otherwise returns the original settings unchanged.

    Args:
        settings: Base application settings dict.
        task_type: One of 'ingest', 'lint', 'dedup'.

    Returns:
        Settings dict with task-specific model if configured.
    """
    settings_key = _TASK_MODEL_KEYS.get(task_type)
    if not settings_key:
        return settings

    task_model = settings.get(settings_key, "")
    if not task_model or not isinstance(task_model, str):
        return settings

    task_settings = dict(settings)
    task_settings["model"] = task_model
    logger.info("LLM task routing: %s → model=%s (override from %s)",
                task_type, task_model, settings.get("model", "default"))
    return task_settings
