from pydantic import BaseModel


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


class SettingsModel(BaseModel):
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    api_key: str = ""
    model: str = "glm-4-flash"
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 0.9
    system_prompt: str = ""
    tavily_key: str = ""
    vlm_api_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    vlm_api_key: str = ""
    vlm_model: str = "glm-4v"
    max_concurrent_file_parsing: int = 1
    max_concurrent_image_description: int = 2
    # Task-specific settings
    task_model: str = ""  # empty = use global model
    task_planner_model: str = ""  # empty = use task_model or global model
    task_timeout: int = 30  # seconds
    task_auto_confirm_shell: bool = False
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
