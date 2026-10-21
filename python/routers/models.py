import logging
import os
import threading

from fastapi import APIRouter, HTTPException

import deps
from knowledge.model_manager import MODELS

router = APIRouter()

# bge-m3 预热线程句柄：窗口整页刷新会重复触发 POST /models/preload，
# 线程存活时直接跳过（幂等；加载本体另有 vector_store 单例双检锁兜底）
_preload_thread: threading.Thread | None = None


def get_manager():
    return deps.model_manager


@router.get("/models/status")
async def models_status():
    return get_manager().check_status()


@router.post("/models/preload")
async def preload_embedding_model():
    """预热 bge-m3 嵌入模型（Electron 主窗口首屏加载完成后触发）。

    启动期即时加载会与渲染层模块装载争抢 CPU/磁盘，拖慢 dev 冷启动首屏
    （2026-10-13 实测 DCL 84s），改为渲染层就绪后触发。首查询不依赖预热：
    vector_store 单例懒加载（双检锁）保证正确性。模型未下载时跳过。
    """
    global _preload_thread
    if _preload_thread is not None and _preload_thread.is_alive():
        return {"ok": True, "message": "bge-m3 预加载进行中", "code": "PRELOAD_IN_PROGRESS"}
    model_path = get_manager().get_model_path("bge-m3")
    if not os.path.isdir(model_path):
        return {"ok": False, "message": "bge-m3 模型未下载", "code": "MODEL_NOT_DOWNLOADED"}

    from knowledge.vector_store import preload_bge_m3

    def _run():
        try:
            preload_bge_m3(model_path)
            logging.getLogger(__name__).info("bge-m3 model preloaded")
        except Exception as e:
            logging.getLogger(__name__).warning("bge-m3 preload failed: %s", e)

    _preload_thread = threading.Thread(target=_run, daemon=True, name="bge-m3-preload")
    _preload_thread.start()
    return {"ok": True, "message": "开始预热 bge-m3", "code": "PRELOAD_STARTED"}


@router.post("/models/{name}/download")
async def download_model(name: str):
    if name not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {name}")
    started = get_manager().start_download(name)
    if not started:
        return {"ok": False, "message": "该模型正在下载中或已下载完成", "code": "MODEL_ALREADY_DOWNLOADING"}
    return {"ok": True, "message": f"开始下载 {name}", "code": "DOWNLOAD_STARTED"}


@router.delete("/models/{name}")
async def delete_model(name: str):
    """删除单个已下载模型（2026-10-20 删除功能审计补齐：此前模型只有
    数据管理面板的全局清理，无法按需释放单个模型空间）。
    业务失败（捆绑/下载中/删失败）以 200 + ok:false 返回，与同文件
    download 端点风格一致；未知模型 404。"""
    if name not in MODELS:
        raise HTTPException(status_code=404, detail=f"Unknown model: {name}")
    result = get_manager().delete_model(name)
    if not result.get("ok"):
        return {"ok": False, "message": result.get("message", "删除失败"),
                "code": result.get("code", "MODEL_DELETE_FAILED")}
    return {"ok": True, "message": f"已删除 {name}", "code": "MODEL_DELETED",
            "cleaned_bytes": result.get("cleaned_bytes", 0)}


@router.post("/models/download-all")
async def download_all():
    started = get_manager().start_download_all()
    if not started:
        return {"ok": False, "message": "所有模型已下载完成或正在下载中", "code": "MODEL_ALL_BUSY"}
    return {"ok": True, "message": f"开始下载: {', '.join(started)}", "code": "DOWNLOAD_ALL_STARTED"}


@router.get("/models/libreoffice/status")
async def libreoffice_status():
    """检测 Office 转换工具可用性（优先 MS Office，其次 LibreOffice）。"""
    return get_manager().check_office_converter()
