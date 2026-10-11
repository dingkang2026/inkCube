from pydantic import BaseModel


class SettingsModel(BaseModel):
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    api_key: str = ""
    model: str = "glm-4-flash"
    temperature: float = 0.7
    max_tokens: int = 8192
    top_p: float = 0.9
    # Reasoning/thinking params (vendor-specific; effort is the de-facto OpenAI-compatible
    # standard, override accepts a free-form JSON object for non-standard endpoints)
    reasoning_effort: str = "low"  # low/medium/high/max/xhigh/minimal... empty = omit (model default)
    reasoning_override: str = ""  # JSON object merged into request body top-level, highest priority
    system_prompt: str = ""
    tavily_key: str = ""
    vlm_api_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    vlm_api_key: str = ""
    vlm_model: str = "glm-4v"
    max_concurrent_file_parsing: int = 2
    max_concurrent_image_description: int = 2
    # Wiki compile settings（默认 2 = 两个项目并行编译，项目内文件串行）
    max_compile_concurrency: int = 2
    # LLM timeout settings
    llm_connect_timeout: int = 30
    llm_read_timeout: int = 300
    # Task-specific settings
    task_model: str = ""  # empty = use global model
    task_planner_model: str = ""  # empty = use task_model or global model
    # Wiki LLM task routing (§12.2.13 of 2.6wiki-design.md)
    wiki_ingest_model: str = ""  # empty = use global model
    wiki_lint_model: str = ""  # empty = use global model
    wiki_dedup_model: str = ""  # empty = use global model
    wiki_ledger_parse_model: str = ""  # 3.1 §4.2 台账解析(轻模型;empty = 全局默认)
    task_timeout: int = 30  # seconds
    task_max_iterations: int = 25  # FC loop max iterations (TaskSettings UI)
    task_auto_confirm_shell: bool = False  # legacy, kept for backward compat
    task_shell_confirm_mode: str = "smart"  # strict | smart | auto
    task_shell_domain_whitelist: str = ""  # newline-separated domains auto-approved in smart mode
    task_show_execution_log: bool = True
    task_workspace_max_mb: int = 1024  # max workspace size in MB (0 = unlimited)
    task_max_count: int = 50  # max number of tasks (0 = unlimited)
    task_auto_archive_days: int = 30  # auto-archive tasks not updated in N days (0 = disabled)


class TaskCreateRequest(BaseModel):
    title: str = "新任务"
    workspace_path: str = ""  # empty = auto-create under data_dir
    mode: str = "coding"  # "coding" or "general"


class TaskChatRequest(BaseModel):
    task_id: str
    message: str
    resume: bool = False
