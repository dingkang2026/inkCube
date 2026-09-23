from fastapi import APIRouter

import deps
from schemas import SettingsModel

router = APIRouter()


@router.get("/settings")
async def get_settings():
    return deps.db.get_settings()


@router.post("/settings")
async def save_settings(settings: SettingsModel):
    deps.db.save_settings(settings.model_dump())
    deps.agent.update_settings(settings.model_dump())

    # Update worker concurrency if changed
    if deps.worker:
        deps.worker.update_concurrency(
            settings.max_concurrent_file_parsing,
            settings.max_concurrent_image_description,
        )

    return {"ok": True}


@router.post("/settings/personal_test")
async def test_settings(settings: SettingsModel):
    from llm.client import LLMClient

    client = LLMClient(settings.model_dump())
    try:
        result = await client.test_connection()
        return {"ok": result, "message": "连接成功" if result else "连接失败"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {str(e)}"}
