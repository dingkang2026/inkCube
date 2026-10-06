import logging
import os
import sys
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

import activity_events

logger = logging.getLogger(__name__)


def get_models_dir() -> str:
    if os.environ.get("INKCUBE_DATA_DIR"):
        base = os.environ["INKCUBE_DATA_DIR"]
    elif getattr(sys, "frozen", False):
        from platformdirs import user_data_dir
        base = user_data_dir("InkCube", "InkCube")
    else:
        base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    d = os.path.join(base, "models")
    os.makedirs(d, exist_ok=True)
    return d


MODELS = {
    "bge-m3": {
        "category": "embedding",
        "dir_name": "bge-m3",
        "size": "~4.6GB",
        "size_bytes": 4_600_000_000,  # 校准：完整 repo 实测 ≈4.59GB（pytorch + onnx 双权重）
        "min_size_bytes": 3_600_000_000,
        # 源顺序契约：modelscope 优先（国内直连可达），hf-mirror 次之，官方 huggingface 兜底
        "sources": [
            "modelscope:BAAI/bge-m3",
            "hf-mirror:BAAI/bge-m3",
            "huggingface:BAAI/bge-m3",
        ],
        "framework": "pytorch",
        "required": True,
        "marker_files": ["config.json"],
        "install_type": "download",
    },
    "docling-layout-heron": {
        "category": "document_parsing",
        "dir_name": "docling-project--docling-layout-heron",
        "size": "~170MB",
        "size_bytes": 170_000_000,  # 校准：完整 repo 实测 ≈168MB
        "min_size_bytes": 130_000_000,
        "sources": [
            "modelscope:ds4sd/docling-layout-heron",
            "hf-mirror:docling-project/docling-layout-heron",
            "huggingface:docling-project/docling-layout-heron",
        ],
        "framework": "pytorch",
        "required": True,
        "marker_files": ["config.json", "model.safetensors"],
        "install_type": "download",
    },
    "tableformer-v2": {
        "category": "document_parsing",
        "dir_name": "docling-project--TableFormerV2",
        "size": "~220MB",
        "size_bytes": 220_000_000,  # 校准：完整 repo 实测 ≈216MB
        "min_size_bytes": 150_000_000,
        # modelscope 无此 repo（ds4sd/TableFormerV2 已验证 404），只走 HF 系；
        # HF_HUB_OFFLINE 冲突由 _hf_online 下载窗口解除（main.py 全局离线防探测）
        "sources": [
            "hf-mirror:docling-project/TableFormerV2",
            "huggingface:docling-project/TableFormerV2",
        ],
        "framework": "pytorch",
        "required": True,
        "marker_files": ["config.json", "model.safetensors"],
        "install_type": "download",
    },
    "rapidocr": {
        "category": "ocr",
        "dir_name": "RapidOcr",
        "size": "~32MB",
        "size_bytes": 32_000_000,  # 校准：三个 onnx 实测 ≈31.9MB
        "min_size_bytes": 25_000_000,
        "sources": ["docling-tools:rapidocr"],
        "framework": "onnx",
        "required": True,
        "marker_files": [],
        "install_type": "download",
    },
}


class ModelManager:
    def __init__(self):
        self.models_dir = get_models_dir()
        self._downloads: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._db = None  # Optional[Database]，由 main.py 注入（下载状态持久化 + 终态通知）
        # HF 离线开关引用计数：main.py 启动时设 HF_HUB_OFFLINE=1 防止 transformers
        # 联网探测，但同进程的模型下载需要联网——下载窗口内临时解除（0.4 §2.4）；
        # 引用计数保证 start_download_all 并行下载线程安全（最后一个退出者负责恢复）
        self._hf_online_lock = threading.Lock()
        self._hf_online_refs = 0

    def set_db(self, db):
        """注入 Database 实例（0.4 §3.3）：下载状态持久化 + 终态通知。"""
        self._db = db

    def _get_dir_name(self, name: str) -> str:
        return MODELS[name].get("dir_name", name)

    def check_status(self) -> dict:
        result = {}
        for name, info in MODELS.items():
            model_path = os.path.join(self.models_dir, self._get_dir_name(name))
            downloaded = self._verify(name, model_path)
            actual_size = self._dir_size(model_path) if os.path.isdir(model_path) else 0

            with self._lock:
                dl = self._downloads.get(name, {})

            result[name] = {
                "name": name,
                "category": info["category"],
                "framework": info["framework"],
                "size": info["size"],
                "size_bytes": info["size_bytes"],
                "required": info["required"],
                "downloaded": downloaded,
                "path": model_path,
                "actual_size": actual_size,
                "sources": info["sources"],
                "install_type": info["install_type"],
                "downloading": dl.get("downloading", False),
                "progress": dl.get("progress", 0),
                "error": dl.get("error"),
            }
        return result

    def verify_model(self, name: str, model_path: str) -> bool:
        """Public API: verify a model is correctly downloaded and intact."""
        return self._verify(name, model_path)

    def _verify(self, name: str, model_path: str) -> bool:
        if name == "rapidocr":
            if not os.path.isdir(model_path):
                return False
            onnx_files = [f for f in os.listdir(model_path) if f.endswith(".onnx")]
            return len(onnx_files) >= 3
        if not os.path.isdir(model_path):
            return False
        marker_files = MODELS[name].get("marker_files", [])
        found = set()
        for dirpath, _, filenames in os.walk(model_path):
            for f in filenames:
                if f in marker_files:
                    found.add(f)
            if len(found) == len(marker_files):
                break
        if not all(f in found for f in marker_files):
            return False
        min_size = MODELS[name].get("min_size_bytes", 0)
        if min_size > 0 and self._dir_size(model_path) < min_size:
            return False
        return True

    def _dir_size(self, path: str, include_incomplete: bool = False) -> int:
        """目录字节数。

        incomplete 半成品默认排除（_verify / actual_size 的完整性语义）；
        进度监控须传 include_incomplete=True（见 _monitor_progress）。"""
        total = 0
        for dirpath, _dirnames, filenames in os.walk(path):
            for f in filenames:
                if not include_incomplete and f.endswith(".incomplete"):
                    continue
                fp = os.path.join(dirpath, f)
                total += os.path.getsize(fp)
        return total

    def _cleanup_cache(self, model_path: str):
        for d in (".cache", ".lock"):
            p = os.path.join(model_path, d)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)

    def get_model_path(self, name: str) -> str:
        return os.path.join(self.models_dir, self._get_dir_name(name))

    def start_download(self, name: str) -> bool:
        if name not in MODELS:
            return False
        if MODELS[name]["install_type"] == "bundled":
            return False
        with self._lock:
            if self._downloads.get(name, {}).get("downloading"):
                return False
            self._downloads[name] = {"downloading": True, "progress": 0, "error": None}
        self._persist_download(name, "downloading")

        thread = threading.Thread(target=self._download_worker, args=(name,), daemon=True)
        thread.start()
        return True

    def start_download_all(self) -> list[str]:
        started = []
        for name in MODELS:
            if MODELS[name]["install_type"] == "bundled":
                continue
            model_path = os.path.join(self.models_dir, self._get_dir_name(name))
            if self._verify(name, model_path):
                continue
            with self._lock:
                if self._downloads.get(name, {}).get("downloading"):
                    continue
            if self.start_download(name):
                started.append(name)
        return started

    def _download_worker(self, name: str):
        try:
            info = MODELS[name]
            sources = info["sources"]
            success = False
            model_path = os.path.join(self.models_dir, self._get_dir_name(name))

            stop_event = threading.Event()
            monitor = threading.Thread(
                target=self._monitor_progress,
                args=(name, model_path, info["size_bytes"], stop_event),
                daemon=True,
            )
            monitor.start()

            try:
                for source in sources:
                    try:
                        self._update_progress(name, 0)
                        source_type, source_value = source.split(":", 1)
                        logger.info("Downloading %s via %s → %s", name, source_type, source_value)

                        if source_type in ("huggingface", "hf-mirror"):
                            self._download_from_hf(name, source_value, source_type == "hf-mirror")
                        elif source_type == "modelscope":
                            self._download_from_modelscope(name, source_value)
                        elif source_type == "docling-tools":
                            self._download_rapidocr(model_path)
                        else:
                            continue

                        self._update_progress(name, 100)
                        success = True
                        break
                    except Exception as e:
                        logger.warning("Source %s failed for %s: %s", source, name, e)
                        continue
            finally:
                stop_event.set()
                monitor.join(timeout=2)

            if success:
                self._cleanup_cache(model_path)
                logger.info("Cleaned up cache for %s", name)
                self._persist_download(name, "completed")
                self._notify_download(name, "completed")
            else:
                self._set_error(name, "所有下载源均失败")
                self._persist_download(name, "failed", "所有下载源均失败")
                self._notify_download(name, "failed", "所有下载源均失败")
            with self._lock:
                self._downloads[name]["downloading"] = False

        except Exception as e:
            self._set_error(name, str(e))
            self._persist_download(name, "failed", str(e))
            self._notify_download(name, "failed", str(e))
            with self._lock:
                self._downloads[name]["downloading"] = False

    def _monitor_progress(self, name: str, model_path: str, expected_bytes: int, stop_event: threading.Event):
        while not stop_event.is_set():
            # 计入 *.incomplete 半成品：HF/modelscope 下载中的大文件均以该后缀落盘，
            # 排除会导致进度条长期为 0、文件 rename 瞬间跳变（进度条失真根因）
            current_size = (
                self._dir_size(model_path, include_incomplete=True)
                if os.path.isdir(model_path)
                else 0
            )
            if expected_bytes > 0:
                pct = min(int(current_size / expected_bytes * 100), 99)
            else:
                pct = 0
            self._update_progress(name, pct)
            stop_event.wait(1.0)

    @contextmanager
    def _hf_online(self):
        """下载窗口内临时解除 HF 离线模式并禁用 Xet（引用计数，并行下载线程安全）。

        main.py 启动时设 HF_HUB_OFFLINE=1 防 transformers 联网探测，但同进程的
        snapshot_download 会同变量拦死（tableformer-v2 三连败根因）。常量在
        huggingface_hub.constants import 时已固化 env 值，仅改 os.environ 不生效，
        须同时 patch 模块常量；计数变更与状态切换同锁，避免并行下载竞态。

        窗口内同时设 HF_HUB_DISABLE_XET=1：hf-mirror 不支持 Xet 传输协议（大文件
        被导向官方 cas-server.xethub.hf.co 重建，镜像签发 token 对其 401
        Unauthorized，tableformer-v2 2026-10-05 两连败根因），禁用后大文件
        回退普通 HTTP CDN 下载。"""
        with self._hf_online_lock:
            self._hf_online_refs += 1
            if self._hf_online_refs == 1:
                self._set_hf_offline(False)
        try:
            yield
        finally:
            with self._hf_online_lock:
                self._hf_online_refs -= 1
                if self._hf_online_refs == 0:
                    self._set_hf_offline(True)

    @staticmethod
    def _set_hf_offline(offline: bool):
        """offline=True：恢复全局离线态；False：进入下载窗口（在线 + 禁 Xet）。"""
        os.environ["HF_HUB_OFFLINE"] = "1" if offline else "0"
        os.environ["TRANSFORMERS_OFFLINE"] = "1" if offline else "0"
        # Xet 开关仅下载窗口内有行为意义：窗口外全局离线（不联网）无 Xet 调用，
        # 故退出时直接还原为未设置（库默认启用）即可，无需保存/恢复原值
        if offline:
            os.environ.pop("HF_HUB_DISABLE_XET", None)
        else:
            os.environ["HF_HUB_DISABLE_XET"] = "1"
        try:
            import huggingface_hub.constants as hf_constants

            hf_constants.HF_HUB_OFFLINE = offline
            hf_constants.HF_HUB_DISABLE_XET = not offline
        except ImportError:
            pass

    def _download_from_hf(self, name: str, repo_id: str, use_mirror: bool):
        from huggingface_hub import snapshot_download

        endpoint = "https://hf-mirror.com" if use_mirror else None
        model_path = os.path.join(self.models_dir, self._get_dir_name(name))
        os.makedirs(model_path, exist_ok=True)

        with self._hf_online():
            snapshot_download(
                repo_id=repo_id,
                local_dir=model_path,
                endpoint=endpoint,
            )

    def _download_from_modelscope(self, name: str, model_id: str):
        try:
            from modelscope import snapshot_download as ms_download
        except ImportError:
            raise RuntimeError("modelscope SDK 未安装，请运行: pip install modelscope")

        model_path = os.path.join(self.models_dir, self._get_dir_name(name))
        os.makedirs(model_path, exist_ok=True)

        ms_download(model_id, local_dir=model_path)

    def _download_rapidocr(self, model_path: str):
        """RapidOCR 三件套流式下载。

        docling 内置 download_models 为内存缓冲（整体下完后一次性落盘），下载期间
        目标目录无任何增量，进度监控全程 0%；此处复用 docling 的 URL 解析结果，
        改流式写 *.incomplete 后原子改名，与 HF/modelscope 半成品语义一致，
        _monitor_progress 可实时反映进度（0.4 §2.4）。"""
        from docling.models.stages.ocr.rapid_ocr_model import (
            _backend_to_engine_type,
            _rapidocr_artifacts,
            _resolve_rapidocr,
        )

        local_dir = Path(model_path)
        local_dir.mkdir(parents=True, exist_ok=True)

        backend = "onnxruntime"
        resolved = _resolve_rapidocr("ch", backend)
        engine = _backend_to_engine_type(backend)
        artifacts = _rapidocr_artifacts(
            local_dir, engine, resolved.ppocr_version, resolved.rapidocr_code,
        )

        for artifact in artifacts.values():
            for dest, url in artifact.files.items():
                if dest.exists():
                    continue  # 已完成文件跳过（与 docling 行为一致，可断点重入）
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".incomplete")
                logger.info("RapidOCR streaming %s", url)
                self._stream_to_file(url, tmp)
                os.replace(tmp, dest)

    @staticmethod
    def _stream_to_file(url: str, dest: Path) -> None:
        """流式下载 url 到 dest（.incomplete 半成品），失败清理半成品后上抛。"""
        import requests

        try:
            with requests.get(url, stream=True, timeout=(10, 120)) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fw:
                    for chunk in resp.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            fw.write(chunk)
        except Exception:
            try:
                os.remove(dest)
            except OSError:
                pass
            raise

    def _update_progress(self, name: str, progress: int):
        with self._lock:
            if name in self._downloads:
                current = self._downloads[name].get("progress", 0)
                if progress > current:
                    self._downloads[name]["progress"] = progress

    def _set_error(self, name: str, error: str):
        with self._lock:
            if name in self._downloads:
                self._downloads[name]["error"] = error
                self._downloads[name]["downloading"] = False

    def _persist_download(self, name: str, status: str, error: str | None = None):
        """下载状态落库（start / 终态），best-effort 不影响下载流程。"""
        if not self._db:
            return
        try:
            self._db.mark_model_download_state(name, status, error)
        except Exception as e:
            logger.warning("Persist download state failed for %s: %s", name, e)

    def _notify_download(self, name: str, kind: str, detail: str | None = None):
        """下载终态通知：经 activity_events 统一入口（E2 收尾，v3.9，含 E3 事件投递）。"""
        if not self._db:
            return
        if kind == "completed":
            activity_events.complete_download(self._db, name)
        else:
            activity_events.fail_download(self._db, name, detail)

    def get_active_downloads(self) -> list[dict]:
        """当前下载中的条目（0.4 §3.4 活动状态源）。"""
        result = []
        with self._lock:
            for key, dl in self._downloads.items():
                if dl.get("downloading"):
                    result.append({
                        "entity_id": key,
                        "entity_title": key,
                        "progress": dl.get("progress", 0),
                    })
        return result

    @staticmethod
    def check_libreoffice() -> dict[str, any]:
        """检测 LibreOffice 是否可用。

        返回:
            {
                "available": bool,
                "path": str | None,  # soffice.exe 路径
                "version": str | None,  # 版本号（可选）
            }
        """
        import shutil
        from pathlib import Path

        # 1. 检查 PATH
        soffice = shutil.which("soffice")
        if soffice:
            return {"available": True, "path": soffice, "version": None}

        # 2. Windows: 检查注册表
        if sys.platform == 'win32':
            try:
                import winreg
                # LibreOffice 注册表路径
                reg_paths = [
                    r"SOFTWARE\LibreOffice\UNO\InstallPath",
                    r"SOFTWARE\LibreOffice\Path",
                ]
                for reg_path in reg_paths:
                    try:
                        key = winreg.OpenKey(
                            winreg.HKEY_LOCAL_MACHINE,
                            reg_path,
                            0,
                            winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
                        )
                        lo_path, _ = winreg.QueryValueEx(key, "")
                        winreg.CloseKey(key)
                        if lo_path:
                            soffice_exe = Path(lo_path) / "soffice.exe"
                            if soffice_exe.is_file():
                                return {"available": True, "path": str(soffice_exe), "version": None}
                    except Exception:
                        continue
            except Exception:
                pass

        # 3. 检查常见安装位置
        candidates = [
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
            Path("/usr/bin/soffice"),
            Path("/usr/bin/libreoffice"),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return {"available": True, "path": str(candidate), "version": None}

        return {"available": False, "path": None, "version": None}

    @staticmethod
    def check_office_converter() -> dict[str, any]:
        """检测 Office 转换工具可用性（优先 MS Office，其次 LibreOffice）。

        返回:
            {
                "available": bool,
                "tool": str | None,  # "ms-office" | "libreoffice"
                "path": str | None,
            }
        """
        # 1. 优先检测 Microsoft Office（Windows）- 直接尝试 COM 调用
        if sys.platform == 'win32':
            try:
                import win32com.client
                # 尝试创建 Word 应用对象（最可靠的方式）
                word = win32com.client.Dispatch('Word.Application')
                word.Quit()
                # 如果能成功创建，说明 Office 可用
                return {"available": True, "tool": "ms-office", "path": "COM"}
            except Exception:
                pass

        # 2. 备选：LibreOffice
        lo_status = ModelManager.check_libreoffice()
        if lo_status["available"]:
            return {"available": True, "tool": "libreoffice", "path": lo_status["path"]}

        return {"available": False, "tool": None, "path": None}
