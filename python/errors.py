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


def actionable(e: BaseException) -> str:
    """长时活动落库/助手消息用的错误串（D1 错误契约，见 0.1dev-guide）。

    InkCubeError → `INKCUBE_CODE:<CODE>|<detail>` 结构化串：前端渲染前经
    translateStoredError 还原为当前语言文案；其他异常透传 str(e)
    （第三方异常串保持原文，历史落库数据同样原文展示，自动降级兼容）。
    后端日志请用 describe(e)，避免日志出现协议串。"""
    if isinstance(e, InkCubeError):
        return f"INKCUBE_CODE:{e.code}|{e.detail}" if e.detail else f"INKCUBE_CODE:{e.code}"
    return str(e)


def describe(e: BaseException) -> str:
    """后端日志可读文案：InkCubeError 拼 message + suggestion；其余透传 str(e)。"""
    if isinstance(e, InkCubeError):
        return f"{e.message}。{e.suggestion}" if e.suggestion else e.message
    return str(e)


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
            "VLM API Key 无效或已失效",
            "请前往 设置 > 外部模型 > VLM 更新 API Key 后重试",
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
            "VLM API 调用频率或用量超限",
            "稍后重试；若持续出现请检查 VLM 账户用量限额",
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
            "LLM API Key 无效或已失效",
            "请前往 设置 > 外部模型 更新 API Key 后重试",
            detail=detail,
        )


class LLMRateLimited(InkCubeError):
    """429：限流或用量超限。需用户检查账户/稍后重试，不可自动重试。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            "LLM_RATE_LIMITED", "CREDENTIAL",
            "API 调用频率或用量超限",
            "请检查服务商控制台的余额与用量限额，稍后重试",
            detail=detail,
        )


class LLMQuotaExhausted(InkCubeError):
    """402/403：余额不足或欠费。需用户充值或更换 Key，不可重试。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            "LLM_QUOTA_EXHAUSTED", "CREDENTIAL",
            "API 账户余额不足或已欠费",
            "请前往服务商控制台充值，或在 设置 > 外部模型 更换 API Key",
            detail=detail,
        )


class LLMModelNotFound(InkCubeError):
    """404：模型名不存在或无访问权限。需用户改模型名，不可重试。"""

    def __init__(self, detail: str = ""):
        super().__init__(
            "LLM_MODEL_NOT_FOUND", "CREDENTIAL",
            "模型不存在或当前 Key 无权访问该模型",
            "请检查模型名称是否正确（设置 > 外部模型）",
            detail=detail,
        )


class LLMNetworkError(InkCubeError):
    def __init__(self, detail: str = ""):
        super().__init__(
            "LLM_NETWORK_ERROR", "NETWORK",
            "无法连接 LLM 服务",
            "检查网络连接；若网络正常，请检查 API 地址是否正确（设置 > 外部模型）",
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
