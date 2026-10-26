from pydantic import BaseModel


class SettingsModel(BaseModel):
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    api_key: str = ""
    # 模型档位槽（2026-10-19 档位制，替代原单一 model 键）：standard = 标准档，
    # high = 高配档；路由键存档位，llm/routing.get_task_settings 解析为最终模型名
    # 写入 model 键（LLMClient 契约不变）。两槽互为回退，开箱默认 glm-5.3-flash /
    # glm-5.3，清空后全空则 model 为空串。
    standard_model: str = "glm-5.3-flash"
    high_model: str = "glm-5.3"
    temperature: float = 0.7
    # 预算两键（D1/D2，llm/budget.py 唯一取值入口）：默认值 = 现代模型实际
    # 下限（上下文 128K / 输出 32K），显示值即生效值；存储值低于下限读取时
    # 自动抬升（raise-only floor）；context_window 手填 0 = 自动（同样落下限）
    max_tokens: int = 32768
    context_window: int = 131072
    top_p: float = 0.9
    # Reasoning/thinking params (vendor-specific; effort is the de-facto OpenAI-compatible
    # standard, override accepts a free-form JSON object for non-standard endpoints)
    reasoning_effort: str = "low"  # low/medium/high/max/xhigh/minimal... empty = omit (model default)
    reasoning_override: str = ""  # JSON object merged into request body top-level, highest priority
    system_prompt: str = ""
    tavily_key: str = ""
    # 网络搜索供应商化（2026-10-19）：tavily | zhipu 二选一，空 = 未选择；两把 Key 并存
    web_search_provider: str = ""
    zhipu_search_key: str = ""
    zhipu_search_engine: str = "search_pro"
    max_concurrent_file_parsing: int = 2
    max_concurrent_image_description: int = 2
    # Wiki compile settings（默认 2 = 两个项目并行编译，项目内文件串行）
    max_compile_concurrency: int = 2
    # LLM timeout settings
    llm_connect_timeout: int = 30
    llm_read_timeout: int = 300
    # Task-specific routing（值 = 档位 standard/high，llm/routing.py 解析；
    # planner 空串 = 不启用规划阶段（单模型模式））
    task_model: str = "standard"
    task_planner_model: str = ""
    # LLM task routing（llm/routing.py 唯一入口；除 ppt 默认 high 外全部默认 standard）
    wiki_ingest_model: str = "standard"
    wiki_lint_model: str = "standard"
    wiki_dedup_model: str = "standard"
    wiki_ledger_parse_model: str = "standard"  # 3.1 §4.2 台账解析（轻量）
    wiki_profile_model: str = "standard"  # 2.6 §4.8 产业画像提取（轻量 JSON 输出）
    report_model: str = "standard"  # 报告生成（长文写作）
    report_extract_model: str = "standard"  # 报告模板提取（样例结构识别）
    form_analyze_model: str = "standard"  # 填表模板分析（结构提取与语义映射）
    form_fill_model: str = "standard"  # 填表执行（按检索资料生成字段值）
    rag_model: str = "standard"  # 知识库问答
    wiki_enrich_model: str = "standard"  # wiki 链接补全（轻量结构化）
    ppt_model: str = "high"  # PPT 生成（逐页 SVG 创作，唯一默认高配）
    task_timeout: int = 30  # seconds；ppt 长命令由 ORIGIN_PRESETS 落地 600（2.3 §3.2）
    task_max_iterations: int = 60  # FC loop max iterations (TaskSettings UI)
    task_auto_confirm_shell: bool = False  # legacy, kept for backward compat
    task_shell_confirm_mode: str = "auto"  # strict | smart | auto（2026-10-22 默认自 smart 改）
    task_shell_domain_whitelist: str = ""  # newline-separated domains auto-approved in smart mode
    task_show_execution_log: bool = True
    task_workspace_max_mb: int = 1024  # max workspace size in MB (0 = unlimited)
    task_max_count: int = 50  # max number of tasks (0 = unlimited)
    task_auto_archive_days: int = 30  # auto-archive tasks not updated in N days (0 = disabled)
    # 存储保留策略（2026-10-20 数据管理审计）：设置页数据管理面板可改，
    # 启动时由 maintenance 后台执行；0 = 永不清理
    archived_task_cleanup_days: int = 30  # 归档超 N 天自动清盘 workspace（保留 DB 行）
    llm_usage_retention_days: int = 90  # token 用量记录保留天数
    schedule_runs_retention_days: int = 90  # 订阅执行记录保留天数（连带影子任务）
    # OS 通知设置（2.11 §2.3）：设置页 GeneralSettings 开关可改，全量保存白名单键，
    # ActivityMonitor 消费（缺字段会被 PUT /settings 静默丢弃）
    os_notify_enabled: bool = True
    os_notify_sound: bool = True
    # 事务提醒（2.11 §3.2）：到期/逾期扫描开关，同白名单键
    affair_reminder_enabled: bool = True
    # 订阅完成 Toast（2.11 §5.6 R4）：默认 False 静默（仅通知中心），开启后
    # completed 也弹 Toast——ActivityMonitor 消费，同白名单键
    schedule_notify: bool = False
    # 用户身份层（0.6 §3.8 V2-E1a）：persona 空 = 未设置（business_attraction/
    # investment/both/other），驱动任务/订阅模板排序；persona_prompted =
    # 首启 PersonaPicker 已呈现过（新老用户统一判定，True 后不再弹）
    persona: str = ""
    persona_prompted: bool = False


class TaskCreateRequest(BaseModel):
    title: str = "新任务"
    workspace_path: str = ""  # empty = auto-create under data_dir
    mode: str = "general"  # "coding" or "general"；chat 默认 general（2026-10-21：系统提示骨架更小，通用任务更常见；编程任务手动切 coding）


class TaskChatRequest(BaseModel):
    task_id: str
    message: str
    resume: bool = False
