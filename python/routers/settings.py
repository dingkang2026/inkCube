import logging

from fastapi import APIRouter

import deps
from schemas import SettingsModel

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/settings")
async def get_settings():
    return deps.db.get_settings()


@router.post("/settings")
async def save_settings(settings: SettingsModel):
    deps.db.save_settings(settings.model_dump())

    # Update worker concurrency if changed
    if deps.worker:
        deps.worker.update_concurrency(
            settings.max_concurrent_file_parsing,
            settings.max_concurrent_image_description,
            settings.max_compile_concurrency,
        )

    return {"ok": True}


@router.get("/onboarding")
async def get_onboarding():
    """首启向导完成标记（0.4lifecycle-design.md §7.3 第一层）。"""
    return {"done": deps.db.get_flag("onboarding_done")}


@router.post("/onboarding/done")
async def set_onboarding_done():
    deps.db.set_flag("onboarding_done", True)
    return {"ok": True}


# Minimal tool definition used to probe native function-calling support
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "probe_ping",
        "description": "Capability probe tool (never actually called)",
        "parameters": {"type": "object", "properties": {}},
    },
}


@router.post("/settings/personal_test")
async def test_settings(settings: SettingsModel):
    """连通性测试 + 模型能力探测（FC / reasoning_effort / thinking 开关）。

    每项探测发送最小请求：200=支持，400=不支持；网络失败中止后续项
    （该项及之后置 None=未知）。探测结论仅供参考，非标端点可通过
    设置项 reasoning_override（思考参数高级覆盖）适配。
    """
    from llm.client import LLMClient

    client = LLMClient(settings.model_dump())
    capabilities = {
        "function_calling": None,
        "reasoning_effort": None,
        "thinking_toggle": None,
    }
    try:
        ok = await client.test_connection()
    except Exception as e:
        logger.warning("personal_test connectivity failed: %s", str(e))
        return {"ok": False, "message": f"连接失败: {str(e)}", "capabilities": capabilities}
    if not ok:
        return {"ok": False, "message": "连接失败", "capabilities": capabilities}

    probes = [
        ("function_calling", {"tools": [_PROBE_TOOL], "tool_choice": "auto"}),
        ("reasoning_effort", {"reasoning_effort": "low"}),
        ("thinking_toggle", {"thinking": {"type": "disabled"}}),
    ]
    for cap_key, extra in probes:
        result = await client.probe(extra)
        capabilities[cap_key] = result
        if result is None:  # network/other failure — abort remaining probes
            break

    logger.info("personal_test capabilities: %s", capabilities)
    return {"ok": True, "message": "连接成功", "capabilities": capabilities}
