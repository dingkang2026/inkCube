import os
import sys
import shutil
import subprocess
import threading


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
        "size": "~4.4GB",
        "size_bytes": 4_400_000_000,
        "min_size_bytes": 3_600_000_000,
        "sources": [
            "hf-mirror:BAAI/bge-m3",
            "modelscope:BAAI/bge-m3",
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
        "size": "~164MB",
        "size_bytes": 164_000_000,
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
        "size": "~200MB",
        "size_bytes": 200_000_000,
        "min_size_bytes": 150_000_000,
        "sources": [
            "modelscope:ds4sd/TableFormerV2",
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
        "size": "~30MB",
        "size_bytes": 30_000_000,
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

    def _dir_size(self, path: str) -> int:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(path):
            for f in filenames:
                if f.endswith(".incomplete"):
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

    def get_gpu_status(self) -> dict:
        gpu_marker = os.path.join(self.models_dir, ".gpu-backend-installed")
        return {
            "mode": "gpu" if os.path.isfile(gpu_marker) else "cpu",
            "gpu_downloaded": os.path.isfile(gpu_marker),
            "gpu_package_size": "~2.5GB",
        }

    def start_download(self, name: str) -> bool:
        if name not in MODELS:
            return False
        if MODELS[name]["install_type"] == "bundled":
            return False
        with self._lock:
            if self._downloads.get(name, {}).get("downloading"):
                return False
            self._downloads[name] = {"downloading": True, "progress": 0, "error": None}

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
                        print(f"[ModelManager] Downloading {name} via {source_type} → {source_value}")

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
                        print(f"[ModelManager] Source {source} failed for {name}: {e}")
                        continue
            finally:
                stop_event.set()
                monitor.join(timeout=2)

            if success:
                self._cleanup_cache(model_path)
                print(f"[ModelManager] Cleaned up cache for {name}")
            else:
                self._set_error(name, "所有下载源均失败")
            with self._lock:
                self._downloads[name]["downloading"] = False

        except Exception as e:
            self._set_error(name, str(e))
            with self._lock:
                self._downloads[name]["downloading"] = False

    def _monitor_progress(self, name: str, model_path: str, expected_bytes: int, stop_event: threading.Event):
        while not stop_event.is_set():
            current_size = self._dir_size(model_path) if os.path.isdir(model_path) else 0
            if expected_bytes > 0:
                pct = min(int(current_size / expected_bytes * 100), 99)
            else:
                pct = 0
            self._update_progress(name, pct)
            stop_event.wait(1.0)

    def _download_from_hf(self, name: str, repo_id: str, use_mirror: bool):
        from huggingface_hub import snapshot_download

        endpoint = "https://hf-mirror.com" if use_mirror else None
        model_path = os.path.join(self.models_dir, self._get_dir_name(name))
        os.makedirs(model_path, exist_ok=True)

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
        from pathlib import Path
        from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel

        os.makedirs(model_path, exist_ok=True)
        RapidOcrModel.download_models(
            backend="onnxruntime",
            local_dir=Path(model_path),
            lang="ch",
        )

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

    def download_gpu_backend(self) -> bool:
        gpu_marker = os.path.join(self.models_dir, ".gpu-backend-installed")
        if os.path.isfile(gpu_marker):
            return True

        def worker():
            try:
                with self._lock:
                    self._downloads["__gpu__"] = {"downloading": True, "progress": 0, "error": None}

                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "torch[cuda]", "-q"],
                    capture_output=True,
                    encoding="utf-8", errors="replace",
                    timeout=1800,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"GPU 后端安装失败: {result.stderr}")

                with open(gpu_marker, "w") as f:
                    f.write("installed")

                with self._lock:
                    self._downloads["__gpu__"] = {"downloading": False, "progress": 100, "error": None}
            except Exception as e:
                with self._lock:
                    self._downloads["__gpu__"] = {"downloading": False, "progress": 0, "error": str(e)}

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return True

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
