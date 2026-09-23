"""python/errors.py — InkCube error hierarchy.

All domain errors inherit InkCubeError, which carries structured fields
(code, category, message, suggestion, detail) so that every layer of the
application (Python backend → Electron IPC → React frontend) can render
consistent, actionable error messages.

Categories
----------
CONFIG     – missing required configuration
CREDENTIAL – invalid credentials or parameters
NETWORK    – network unreachable / timeout
RESOURCE   – local resource missing (model files, etc.)
INTERNAL   – program bug (unexpected exception)
PROCESS    – process lifecycle (Python crash, port conflict)
"""


class InkCubeError(Exception):
    """Base error with structured info for cross-layer propagation."""

    def __init__(
        self,
        code: str,
        category: str,
        message: str,
        suggestion: str = "",
        detail: str = "",
    ):
        self.code = code
        self.category = category  # CONFIG|CREDENTIAL|NETWORK|RESOURCE|INTERNAL|PROCESS
        self.message = message
        self.suggestion = suggestion
        self.detail = detail
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "suggestion": self.suggestion,
            "detail": self.detail,
        }


# ── VLM Errors ──────────────────────────────────────────────


class VLMNotConfigured(InkCubeError):
    def __init__(self):
        super().__init__(
            "VLM_NOT_CONFIGURED", "CONFIG",
            "VLM API 未配置",
            "前往 设置 > 外部模型 > VLM 配置 API Key",
        )


class VLMInvalidKey(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "VLM_INVALID_KEY", "CREDENTIAL",
            "VLM API Key 无效",
            "检查 VLM API Key 是否正确",
            detail=detail,
        )


class VLMNetworkError(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "VLM_NETWORK_ERROR", "NETWORK",
            "无法连接 VLM 服务",
            "检查网络连接",
            detail=detail,
        )


class VLMRateLimited(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "VLM_RATE_LIMITED", "NETWORK",
            "VLM API 调用频率超限",
            "稍后重试",
            detail=detail,
        )


class VLMCallFailed(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "VLM_CALL_FAILED", "INTERNAL",
            "VLM 调用异常",
            "查看详细错误",
            detail=detail,
        )


# ── LLM Errors ──────────────────────────────────────────────


class LLMNotConfigured(InkCubeError):
    def __init__(self):
        super().__init__(
            "LLM_NOT_CONFIGURED", "CONFIG",
            "LLM API 未配置",
            "前往 设置 > 外部模型 配置 LLM API Key",
        )


class LLMInvalidKey(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "LLM_INVALID_KEY", "CREDENTIAL",
            "LLM API Key 无效",
            "检查 LLM API Key 是否正确",
            detail=detail,
        )


class LLMNetworkError(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "LLM_NETWORK_ERROR", "NETWORK",
            "无法连接 LLM 服务",
            "检查网络连接",
            detail=detail,
        )


class LLMCallFailed(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "LLM_CALL_FAILED", "INTERNAL",
            "LLM 调用异常",
            "查看详细错误",
            detail=detail,
        )


# ── Embedding / Parsing / File Errors ───────────────────────


class EmbeddingModelMissing(InkCubeError):
    def __init__(self):
        super().__init__(
            "EMBEDDING_MODEL_MISSING", "RESOURCE",
            "Embedding 模型未下载",
            "前往 设置 > 内部模型 下载 bge-m3 模型",
        )


class ParsingModelMissing(InkCubeError):
    def __init__(self):
        super().__init__(
            "PARSING_MODEL_MISSING", "RESOURCE",
            "文档解析模型缺失",
            "前往 设置 > 内部模型 下载解析模型",
        )


class FileNotFound(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "FILE_NOT_FOUND", "RESOURCE",
            "文件不存在",
            "检查文件是否被移动或删除",
            detail=detail,
        )


# ── System / Process Errors ─────────────────────────────────


class SystemDBError(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "SYSTEM_DB_ERROR", "INTERNAL",
            "数据库操作异常",
            "查看日志",
            detail=detail,
        )


class SystemProcessDown(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "SYSTEM_PROCESS_DOWN", "PROCESS",
            "Python 服务已停止",
            "重启应用",
            detail=detail,
        )


class SystemStartFailed(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "SYSTEM_START_FAILED", "PROCESS",
            "Python 服务启动失败",
            "查看日志 / 重启应用",
            detail=detail,
        )
