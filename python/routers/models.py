from fastapi import APIRouter, HTTPException

import deps
from knowledge.model_manager import MODELS

router = APIRouter()


def get_manager():
    return deps.model_manager


@router.get("/models/status")
async def models_status():
    return get_manager().check_status()


@router.get("/models/gpu-status")
async def gpu_status():
    return get_manager().get_gpu_status()


@router.post("/models/{name}/download")
async def download_model(name: str):
    if name not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {name}")
    started = get_manager().start_download(name)
    if not started:
        return {"ok": False, "message": "该模型正在下载中或已下载完成"}
    return {"ok": True, "message": f"开始下载 {name}"}


@router.post("/models/download-all")
async def download_all():
    started = get_manager().start_download_all()
    if not started:
        return {"ok": False, "message": "所有模型已下载完成或正在下载中"}
    return {"ok": True, "message": f"开始下载: {', '.join(started)}"}


@router.post("/models/gpu/download")
async def download_gpu():
    get_manager().download_gpu_backend()
    return {"ok": True, "message": "开始下载 GPU 后端"}


@router.get("/models/libreoffice/status")
async def libreoffice_status():
    """检测 Office 转换工具可用性（优先 MS Office，其次 LibreOffice）。"""
    return get_manager().check_office_converter()
