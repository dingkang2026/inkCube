import os
import shutil
import sqlite3
import json
import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class _LockedConnection:
    """Serialize access to the shared sqlite3 connection.

    The single connection is used from the event loop, worker threads
    (run_in_executor / asyncio.to_thread) and background threads (model
    download worker). sqlite3's serialized compile mode keeps individual
    C-level calls safe, but interleaved transactions from two threads can
    still corrupt logical state — one thread's commit() would finalize
    another thread's half-written transaction. An RLock serializes all
    connection-level operations; cursors returned by execute() each belong
    to a single prepared statement and stay internally consistent.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq_of_params):
        with self._lock:
            return self._conn.executemany(sql, seq_of_params)

    def executescript(self, script: str):
        with self._lock:
            return self._conn.executescript(script)

    def commit(self):
        with self._lock:
            return self._conn.commit()

    def rollback(self):
        with self._lock:
            return self._conn.rollback()

    @contextmanager
    def transaction(self):
        """事务级锁:逻辑事务整体串行,防工作线程在语句间交叉 commit/rollback。

        RLock 可重入,同线程事务体内的 execute/嵌套调用不死锁;
        其他线程的语句在事务结束前被阻塞(期望行为:不交叉半成品事务)。
        """
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                # BaseException:KeyboardInterrupt/SystemExit 也不残留开事务
                self._conn.rollback()
                raise

    def close(self):
        with self._lock:
            return self._conn.close()

    @property
    def row_factory(self):
        with self._lock:
            return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        with self._lock:
            self._conn.row_factory = value

    def __getattr__(self, name):
        # Rare passthrough (e.g. `in_transaction`) — single calls are safe
        # under sqlite3's serialized mode.
        return getattr(self._conn, name)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: _LockedConnection = None

    def initialize(self):
        self.conn = _LockedConnection(
            sqlite3.connect(self.db_path, check_same_thread=False)
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")  # Enable foreign key support
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Task agent tables
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                workspace_path TEXT NOT NULL,
                origin TEXT NOT NULL DEFAULT 'chat',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_messages (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_task_messages_task
                ON task_messages(task_id, created_at);

            -- Knowledge base tables
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_hash TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                chunk_count INTEGER DEFAULT 0,
                image_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                page_no INTEGER,
                caption TEXT,
                width_px INTEGER,
                height_px INTEGER,
                dpi INTEGER,
                local_path TEXT NOT NULL,
                figure_type TEXT,
                vlm_description TEXT,
                context_text TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
            CREATE INDEX IF NOT EXISTS idx_images_file ON images(file_id);

            -- FTS5 virtual table for full-text chunk search
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id,
                content,
                project_id,
                filename,
                content_type,
                tokenize='unicode61'
            );
        """)
        self.conn.commit()

        self._migrate_add_column("images", "context_text", "TEXT")

        # Migrate: files table - image tracking
        self._migrate_add_column("files", "image_status", "TEXT DEFAULT 'none'")
        self._migrate_add_column("files", "image_described_count", "INTEGER DEFAULT 0")

        # Migrate: images table - status tracking
        self._migrate_add_column("images", "status", "TEXT DEFAULT 'pending'")
        self._migrate_add_column("images", "error_message", "TEXT")

        # Migrate: files table - progress tracking
        self._migrate_add_column("files", "progress", "INTEGER DEFAULT 0")

        # Migrate: images table - manual category
        self._migrate_add_column("images", "category", "TEXT DEFAULT 'uncategorized'")

        # Migrate: tasks table - venv_path for per-task isolation
        self._migrate_add_column("tasks", "venv_path", "TEXT DEFAULT ''")

        # Migrate: tasks table - mode for coding/general mode
        # DEFAULT 'general'（2026-10-21 chat 默认改通用模式）：_migrate_add_column 对
        # 存量库幂等跳过（列已存在，存量列 DEFAULT 不变、存量行不动）；仅新装库生效，
        # 与应用层默认（schemas/create_task 签名）保持一致
        self._migrate_add_column("tasks", "mode", "TEXT DEFAULT 'general'")

        # Migrate: tasks table - origin (chat / ppt, 2.3 §3.1 形态分离；
        # ppt 任务不进任务 Tab，由报告 Tab PPT 区块承载)
        self._migrate_add_column("tasks", "origin", "TEXT DEFAULT 'chat'")

        # Migrate: projects table - type/stage for project classification
        self._migrate_add_column("projects", "type", "TEXT DEFAULT 'general'")
        self._migrate_add_column("projects", "stage", "TEXT DEFAULT 'none'")

        # Migrate: projects table - company info fields
        self._migrate_add_column("projects", "company_name", "TEXT DEFAULT ''")
        self._migrate_add_column("projects", "credit_code", "TEXT DEFAULT ''")

        # Migrate: projects table - kind/lib_type for base library support
        self._migrate_add_column("projects", "kind", "TEXT DEFAULT 'project'")
        self._migrate_add_column("projects", "lib_type", "TEXT DEFAULT NULL")

        # Migrate: projects table - archived_at for project archive (归档=生命周期软关闭，
        # 删除准入 = 已归档；NULL = 有效，非空 = 已归档且时间戳兼作展示字段)
        self._migrate_add_column("projects", "archived_at", "DATETIME DEFAULT NULL")

        # Project-BaseLib association table
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS project_base_libs (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                base_lib_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                auto_linked INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, base_lib_id)
            );
        """)
        self.conn.commit()

        # ── Activity Hub tables (0.4lifecycle-design.md §3.2 / §3.3) ──
        # notifications: 终态事件持久化（完成/失败），弹出即已读
        # model_downloads: 模型下载状态持久化（崩溃检测用，进度不落库）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,   -- task / file / wiki_compile / report / model_download
                entity_id TEXT NOT NULL,
                project_id TEXT,             -- 所属项目（任务/模型下载无项目时为 NULL）
                container TEXT DEFAULT 'project', -- 挂载容器 project / baselib（0.4 §3.6，前端导航分流）
                kind TEXT NOT NULL,          -- 终态 completed/failed/paused/awaiting_input；信息类 file_changed/due/overdue/info/warning（2.11）
                entity_title TEXT NOT NULL,  -- 展示名（用户数据，不翻译）
                detail TEXT,
                read_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_unread
                ON notifications(read_at, created_at);

            CREATE TABLE IF NOT EXISTS model_downloads (
                name TEXT PRIMARY KEY,      -- 模型名
                status TEXT NOT NULL DEFAULT 'downloading',
                error TEXT,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        self.conn.commit()

        # Migrate: notifications — add container for base-library routing (0.4 §3.6)
        self._migrate_add_column("notifications", "container", "TEXT DEFAULT 'project'")

        # Skills table
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                source_type TEXT NOT NULL DEFAULT 'builtin',
                source_path TEXT,
                install_path TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_agent_state (
                task_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                paused_at TEXT NOT NULL
            );

            -- Report system tables
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                template_id TEXT NOT NULL,
                title TEXT NOT NULL,
                output_format TEXT NOT NULL DEFAULT 'docx',
                status TEXT NOT NULL DEFAULT 'generating',
                progress INTEGER DEFAULT 0,
                content_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS report_sections (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                section_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                word_count INTEGER DEFAULT 0,
                compose_mode TEXT DEFAULT 'standard',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                -- 同一章节唯一：restart/resume 重跑重复落库幂等（与 wiki_pages
                -- UNIQUE(project_id, page_key) 同模式；存量库由迁移重建补齐）
                UNIQUE(report_id, section_id)
            );

            CREATE TABLE IF NOT EXISTS templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                template_json TEXT NOT NULL,
                is_custom INTEGER DEFAULT 0,
                version TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reports_project ON reports(project_id);
            CREATE INDEX IF NOT EXISTS idx_report_sections_report ON report_sections(report_id);
        """)
        self.conn.commit()

        # ── Migrations ──
        # Migrate: skills table - needs_confirm for first-use confirmation.
        # Must run AFTER the skills CREATE TABLE above: on a fresh database the
        # table doesn't exist when the earlier migration block executes, and
        # _migrate_add_column silently skips missing tables — the column would
        # never be added (broke confirm_skill_by_name with "no such column").
        self._migrate_add_column("skills", "needs_confirm", "INTEGER DEFAULT 1")
        # 2.10 单源迁移：reports.token_usage（旧 TokenTracker 产物，前端无消费）
        # 由 llm_usage 表取代；存量库删列（SQLite ≥3.35），新库本就无此列
        try:
            self.conn.execute("ALTER TABLE reports DROP COLUMN token_usage")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # 列不存在（新库/已迁移）
        self._migrate_add_column("reports", "error_message", "TEXT")

        # Web logins table (2.8 §3.2)：设置页「网页登录态」管理列表。
        # 仅存用户主动登记的站点与备注，不存凭据；抓取不依赖此表。
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS web_logins (
                domain TEXT PRIMARY KEY,
                note TEXT,
                created_at TEXT NOT NULL
            );
        """)
        self.conn.commit()

        # ── Token usage ledger (2.10 §3.1) ──
        # append-only：client 收口层单点写入（llm/usage.py sink → insert_llm_usage），
        # 聚合查询现算（无缓存层，刷新机制因此无需失效管理，§5.4）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,                   -- ISO 本地时间，按天分组用 date(ts)
                source TEXT NOT NULL,               -- task / chat / knowledge / wiki / report / form / system / other（vlm 为历史值）
                purpose TEXT,                       -- 调用用途细分（2.10 §3.3）
                task_id TEXT,
                round_id TEXT,                      -- 任务「每次对话」= agent 一次 run 的 uuid
                project_id TEXT,
                entity_id TEXT,                     -- report_id / compile_task_id / fill_id / image_id / file_id
                model TEXT NOT NULL,
                mode TEXT NOT NULL,                 -- full / stream（vlm 为历史值）
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                detail TEXT                         -- JSON 扩展位（如报告 section_id）
            );
            CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts);
            CREATE INDEX IF NOT EXISTS idx_llm_usage_source ON llm_usage(source, ts);
            CREATE INDEX IF NOT EXISTS idx_llm_usage_task ON llm_usage(task_id, round_id);
            CREATE INDEX IF NOT EXISTS idx_llm_usage_project ON llm_usage(project_id, ts);
            CREATE INDEX IF NOT EXISTS idx_llm_usage_entity ON llm_usage(entity_id);
        """)
        self.conn.commit()
        # 报告级暂停请求标志（2.5 §3.1 修订）：pause 端点写入，Runner 在章节边界
        # 消费（当前章节完成→落 checkpoint→status=paused），恢复走 retry 断点续跑
        self._migrate_add_column("reports", "pause_requested", "INTEGER DEFAULT 0")
        self._migrate_add_column("reports", "variables", "TEXT")
        self._migrate_add_column("reports", "base_project_ids", "TEXT")
        self._migrate_add_column("reports", "section_internet", "TEXT")
        self._migrate_add_column("reports", "internet_enabled", "INTEGER DEFAULT 0")
        # 历史行统一 'docx'（历史版本 IPC 曾写 'Word'，与后端/API 默认不一致）
        self.conn.execute(
            "UPDATE reports SET output_format = 'docx' WHERE output_format = 'Word'"
        )
        self.conn.commit()
        # report_sections 补 UNIQUE(report_id, section_id)：存量库重建表去重
        self._migrate_report_sections_unique()

        # Report logs table (for progress tracking)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS report_logs (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL,
                section_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_report_logs_report ON report_logs(report_id, created_at);
        """)
        self.conn.commit()

        # ── PPT deliverables (2.3 §5.1)：交付物登记簿——执行态权威在 tasks，
        # 产物文件权威在 workspace，本表只做登记与索引 ──
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ppts (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,               -- 引擎句柄：tasks.id（origin='ppt'）
                title TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'deck',   -- deck=生成 / enhanced=增强
                status TEXT NOT NULL DEFAULT 'running',
                    -- running / awaiting_input / paused / completed / failed / cancelled
                stage TEXT,                          -- intake / strategy / authoring / export
                progress REAL NOT NULL DEFAULT 0,    -- 0-1（authoring 按页计）
                page_count INTEGER,                  -- 规划页数
                page_done INTEGER,                   -- 已完成页数
                source_report_id TEXT,               -- 内容源报告（可空=自由主题）
                source_images_json TEXT,             -- 图片库选中项 ["<image_id>", ...]
                style_ref TEXT,                      -- 内置 style 名或 library 模板名
                workspace_path TEXT,                 -- ppt-master 项目目录（任务 workspace 内）
                export_path TEXT,                    -- 最终 PPTX 相对路径
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ppts_project ON ppts(project_id, created_at DESC);
        """)
        self.conn.commit()

        # Migrate: ppts — 模板工坊任务字段（2.3 §4.2，E2）：kind='template' 行记录
        # 目标模板的类型与 id（完成判定 = 对应索引出现该 id；project_id 传空串
        # 表示全局资产，活动/通知层 LEFT JOIN 后按 kind 分流 container）
        self._migrate_add_column("ppts", "template_kind", "TEXT")
        self._migrate_add_column("ppts", "template_id", "TEXT")

        # Migrate: ppts — 成品增强源（2.3 §4.3，E3）：kind='enhanced' 行记录
        # staging 后的源 PPTX 相对路径（init 的输入，apply 产物在 exports/）
        self._migrate_add_column("ppts", "enhance_source_path", "TEXT")

        # ── Wiki tables (§3 of 2.6wiki-design.md) ──
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS wiki_schemas (
                id TEXT PRIMARY KEY,
                project_type TEXT NOT NULL,
                page_key TEXT NOT NULL,
                title TEXT NOT NULL,
                compile_hint TEXT,
                base_lib_search_hints TEXT,
                position INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS wiki_pages (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                page_key TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                compile_version INTEGER DEFAULT 0,
                last_compiled_at TEXT,
                last_compiled_file_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, page_key)
            );

            CREATE TABLE IF NOT EXISTS wiki_page_sources (
                id TEXT PRIMARY KEY,
                wiki_page_id TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
                file_id TEXT NOT NULL,
                contribution TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wiki_change_log (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                wiki_page_id TEXT,
                action TEXT NOT NULL,
                trigger_file_id TEXT,
                summary TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wiki_reviews (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                affected_page_ids TEXT,
                evidence TEXT,
                source_file_id TEXT,
                resolved INTEGER DEFAULT 0,
                resolved_action TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wiki_compile_tasks (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                trigger_type TEXT NOT NULL,
                trigger_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                stage TEXT,
                pages_total INTEGER DEFAULT 0,
                pages_completed INTEGER DEFAULT 0,
                pages_failed INTEGER DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_wiki_pages_project ON wiki_pages(project_id);
            CREATE INDEX IF NOT EXISTS idx_wiki_change_log_project ON wiki_change_log(project_id);
            CREATE INDEX IF NOT EXISTS idx_wiki_reviews_project ON wiki_reviews(project_id);
            CREATE INDEX IF NOT EXISTS idx_wiki_compile_tasks_project ON wiki_compile_tasks(project_id);
        """)
        self.conn.commit()

        # 2.11 迁移反转（2026-10-22）：pending_compile_count 事件累加计数器
        # 随删除/重解析/schema 变更漂移，与编译真实消费的待编译集合脱节——
        # get_pending_compile_count 改为实时计算，存量库删列（SQLite ≥3.35），
        # 新库本就无此列。
        try:
            self.conn.execute("ALTER TABLE projects DROP COLUMN pending_compile_count")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # 列不存在（新库/已迁移）

        # Migrate: wiki_pages — add page_type, refs, sources, tags (§3.2)
        self._migrate_add_column("wiki_pages", "page_type", "TEXT DEFAULT 'structure'")
        self._migrate_add_column("wiki_pages", "refs", "TEXT")
        self._migrate_add_column("wiki_pages", "sources", "TEXT")
        self._migrate_add_column("wiki_pages", "tags", "TEXT")

        # Migrate: wiki_change_log — add source_title, affected_page_keys (§3.4)
        self._migrate_add_column("wiki_change_log", "source_title", "TEXT DEFAULT ''")
        self._migrate_add_column("wiki_change_log", "affected_page_keys", "TEXT")

        # Migrate: projects — add wiki_purpose, wiki_overview (§3.6)
        self._migrate_add_column("projects", "wiki_purpose", "TEXT DEFAULT ''")
        self._migrate_add_column("projects", "wiki_overview", "TEXT DEFAULT ''")

        # Wiki compile tasks — V2 per-file ingest columns
        self._migrate_add_column("wiki_compile_tasks", "ingest_file_summaries", "TEXT")
        self._migrate_add_column("wiki_compile_tasks", "stop_requested", "INTEGER DEFAULT 0")

        # Migrate: wiki_reviews evidence（§3.5/§5.5，E2-2026-10）——矛盾证据结构化
        # [{page_key, quote}]，摄入产出的 contradiction 附带，供两栏对比裁决
        self._migrate_add_column("wiki_reviews", "evidence", "TEXT")

        # Wiki source pages table (§3.7) — tracks ingested files
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS wiki_source_pages (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_id TEXT NOT NULL,
                source_page_key TEXT,
                content_hash TEXT,
                status TEXT NOT NULL DEFAULT 'ingested',
                pages_touched TEXT,
                entities_found TEXT,
                concepts_found TEXT,
                ingested_at TEXT,
                updated_at TEXT,
                UNIQUE(project_id, file_id)
            );
            CREATE INDEX IF NOT EXISTS idx_wiki_source_pages_project ON wiki_source_pages(project_id);
        """)
        self.conn.commit()

        # Data fix (E2-2026-10, §4.4): 归档存量 pending confirm——confirm 是
        # 「材料与百科吻合」类正面结论，不占待处理列表；新代码落库即 resolved=1
        # (auto_confirmed)，此处一次性清理历史积压。幂等，重复执行无副作用。
        self.conn.execute(
            "UPDATE wiki_reviews SET resolved = 1, resolved_action = 'auto_confirmed' "
            "WHERE type = 'confirm' AND resolved = 0"
        )
        self.conn.commit()

        # Wiki page versions table — stores historical snapshots for version history
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS wiki_page_versions (
                id TEXT PRIMARY KEY,
                wiki_page_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                page_key TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                version INTEGER NOT NULL,
                change_summary TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wiki_page_versions_page ON wiki_page_versions(wiki_page_id);
            CREATE INDEX IF NOT EXISTS idx_wiki_page_versions_project ON wiki_page_versions(project_id);
        """)
        self.conn.commit()

        # ── Affairs: Playbook & Phase Tasks (3.0 §4, phase 2) ──
        # playbook_templates: 类型 × 时期任务模板(种子化,运行期只读);
        # phase_tasks: 项目任务实例(脉搏带/事务 Tab/首页待办唯一数据源)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS playbook_templates (
                id TEXT PRIMARY KEY,
                project_type TEXT NOT NULL,
                phase TEXT NOT NULL,
                task_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                due_offset_days INTEGER,
                due_date_fixed TEXT,
                recurrence_rule TEXT,
                auto_complete TEXT,
                position INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_playbook_type_phase
                ON playbook_templates(project_type, phase, position);

            CREATE TABLE IF NOT EXISTS phase_tasks (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                phase TEXT NOT NULL,
                task_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                template_id TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                occurrence_key TEXT,
                due_date TEXT,
                recurrence_rule TEXT,
                auto_complete TEXT,
                linked_entity_type TEXT,
                linked_entity_id TEXT,
                completed_at DATETIME,
                completed_by TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, template_id, occurrence_key)
            );
            CREATE INDEX IF NOT EXISTS idx_phase_tasks_project
                ON phase_tasks(project_id, status, due_date);
            CREATE INDEX IF NOT EXISTS idx_phase_tasks_due
                ON phase_tasks(status, due_date);
        """)
        self.conn.commit()

        # ── Intelligence: ledger & suggestions (3.0 §8 phase 4, 3.1 §3) ──
        # ledger_entries: 台账结构化解析产物（时间轴卡/卡片墙摘要/AI 状态句数据源);
        #   wiki_pages.content 是唯一事实源,本表可随时全量重建(3.1 不变量 1)
        # task_suggestions: 事件驱动任务建议(③建议新建/④建议标记完成);
        #   (entry_fingerprint, kind) 幂等,忽略过不再提(3.1 不变量 3)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                page_key TEXT NOT NULL,          -- 来源台账页:milestones / risk_watchlist / policy_ledger / ...
                entry_date TEXT,                 -- 条目日期,尽力规范:YYYY-MM-DD 或 YYYY-MM;解析不出为 NULL
                entry_type TEXT NOT NULL,        -- milestone / risk / policy / finance / service / ...(自由语义标注)
                content TEXT NOT NULL,           -- 条目原文(一行)
                source_file_id TEXT,             -- 来源文件(可追溯到哪份月报)
                fingerprint TEXT NOT NULL,       -- 内容指纹(hash):幂等去重的锚
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, page_key, fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_entries_project
                ON ledger_entries(project_id, page_key, entry_date);

            CREATE TABLE IF NOT EXISTS task_suggestions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,              -- create_task(③建议新建) / complete_task(④建议标记完成)
                title TEXT NOT NULL,             -- 建议的任务标题 / 目标任务标题
                description TEXT DEFAULT '',
                rationale TEXT,                  -- 一句人话:为什么建议(引用台账条目)
                entry_fingerprint TEXT,          -- 触发条目指纹(⚠ 不存 entry id,见不变量 2)
                target_task_id TEXT,             -- kind=complete_task 时的目标任务(phase_tasks.id)
                suggested_phase TEXT,            -- create_task:建议放入的时期(before/during/after)
                suggested_task_type TEXT,        -- create_task:建议的任务类型,默认 milestone(§7 决策 4)
                status TEXT NOT NULL DEFAULT 'pending',  -- pending / accepted / dismissed
                accepted_task_id TEXT,           -- 采纳后生成的任务 id(审计可溯)
                created_at TEXT NOT NULL,
                decided_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_task_suggestions_project
                ON task_suggestions(project_id, status);
        """)
        self.conn.commit()

        # Migrate: projects — playbook 铺入标记(防删光任务后重铺,3.0 §4.3)
        self._migrate_add_column("projects", "playbook_seeded", "INTEGER DEFAULT 0")

        # Migrate: intelligence layer (3.0 phase 4, 3.1 §3.3/§3.4) —
        # AI 状态句(编译时点快照,wiki_overview 同款列迁移先例)+ 台账页标注
        self._migrate_add_column("projects", "pulse_ai_status", "TEXT DEFAULT ''")
        self._migrate_add_column("projects", "pulse_ai_status_at", "TEXT")
        self._migrate_add_column("wiki_schemas", "is_ledger", "INTEGER DEFAULT 0")

        # ── Forms: form_templates & form_fills (2.4 §3) ──
        # form_templates: 填表模板（全局资产，模板不可变——上传重传=新记录、
        #   预置仅 reanalyze）；form_fills: 填表实例（项目作用域，产物 docx 落盘
        #   data/form_fills/{id}.docx）。模板删除不阻断：实例 template_id 悬空容错
        #   （2026-10-16 摘除外键，2.4 §5.4 不变量 11）。
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS form_templates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'upload',
                file_path TEXT NOT NULL,
                template_schema TEXT,
                llm_mapping TEXT,
                user_hint TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                field_count INTEGER,
                file_hash TEXT,                  -- SHA-256（分析成功时写入；用于启动/列表时检测文件变更，2.4 §2.4）
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_form_templates_status
                ON form_templates(status);

            CREATE TABLE IF NOT EXISTS form_fills (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                template_id TEXT NOT NULL,       -- 无外键（2.4 §5.4 不变量 11：
                                                 --   模板随时可删，悬空容错）
                title TEXT NOT NULL,
                output_path TEXT,
                report TEXT,
                filled_values TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_form_fills_project
                ON form_fills(project_id);
        """)
        # 存量库补 file_hash（分析成功后写入；NULL=未知不参与变更提示）
        self._migrate_add_column("form_templates", "file_hash", "TEXT")
        self.conn.commit()

        # ── Task Templates (0.5 §5, E2) ──
        # 自定义任务模板持久化；内置模板不落库（始终以 i18n 为准），表只存
        # 自定义行 + 内置置顶态记录行（builtin=1，id='tpl_builtin_<name>'）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_templates (
                id          TEXT PRIMARY KEY,      -- 'tpl_custom_<hex8>'；内置置顶记录为 'tpl_builtin_<name>'
                scope       TEXT NOT NULL,         -- 'global' | 'project'
                project_id  TEXT,                  -- scope='project' 时必填
                category    TEXT NOT NULL,         -- info_gather|monitor|industry|report_invest|office
                title       TEXT NOT NULL,
                prompt      TEXT NOT NULL,         -- 含 {占位符}
                builtin     INTEGER NOT NULL DEFAULT 0,
                pinned      INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_task_templates_scope
                ON task_templates(scope, project_id);
        """)
        self.conn.commit()

        # ── Task Schedules (2.11 §3, E3) ──
        # 订阅式情报采集：task_schedules 订阅 + task_schedule_runs 执行历史
        # （阅读层数据源）；tasks.schedule_id 迁移列使影子任务走 agent 既有
        # 执行链而与人任务列表隔离（D2：服务端排除）。外键依赖 PRAGMA
        # foreign_keys=ON（本文件初始化已开）：删订阅级联删 runs、任务行
        # schedule_id 置 NULL（历史保留可读）。
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_schedules (
                id          TEXT PRIMARY KEY,            -- 'sch_<hex8>'
                name        TEXT NOT NULL,               -- 用户可读名
                prompt      TEXT NOT NULL,               -- 填好占位符的完整 prompt（含产出结构约定，2.11 §8.3）
                mode        TEXT NOT NULL DEFAULT 'general',
                category    TEXT NOT NULL DEFAULT 'general',
                interval_type  TEXT NOT NULL,            -- daily | weekly | monthly
                interval_value TEXT NOT NULL,            -- "09:00" | "MON,THU 09:00" | "1,15 09:00"
                target_type TEXT,                        -- 'project' | 'baselib' | NULL（不自动沉淀）
                target_id   TEXT,                        -- target_type 非空时必填
                archive_file_id TEXT,                    -- 档案在目标容器的 file id 锚点（2.11 §6）
                archive_last_run_id TEXT,                -- 档案已合并到的最近 run id（失败补并锚点）
                auto_import INTEGER NOT NULL DEFAULT 1,
                enabled     INTEGER NOT NULL DEFAULT 1,  -- 0 = 已暂停（含连续失败自动暂停）
                next_run_at TEXT,                        -- ISO；enabled=0 时为 NULL
                last_run_at TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,  -- ≥3 自动暂停（2.11 §5.5）
                scope       TEXT,                        -- 'focus'|'sweep'|'rollup'，NULL 读取归一 'focus'（2.11 §12）
                digest_schedule_ids TEXT,                -- JSON 数组文本，仅 rollup 用；NULL=全部启用订阅（2.11 §12）
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_schedule_runs (
                id          TEXT PRIMARY KEY,            -- 'run_<hex8>'
                schedule_id TEXT NOT NULL REFERENCES task_schedules(id) ON DELETE CASCADE,
                task_id     TEXT REFERENCES tasks(id) ON DELETE SET NULL,  -- 任务被删后历史仍可读
                status      TEXT NOT NULL,               -- running | done | failed | skipped | missed
                report_path TEXT,                        -- 报告副本 data/schedules/{run_id}.md（D5）
                summary     TEXT,                        -- 摘要要点 JSON 数组（解析自报告「## 摘要」节）
                error       TEXT,                        -- 失败原因（§8.4 ReadView 失败态；done 恒 NULL）
                read_at     TEXT,                        -- NULL=未读（收件箱/红点/摘要行计数数据源）
                started_at  TEXT,
                finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_runs_schedule
                ON task_schedule_runs(schedule_id, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_schedules_next
                ON task_schedules(enabled, next_run_at);
            CREATE INDEX IF NOT EXISTS idx_runs_inbox
                ON task_schedule_runs(status, finished_at DESC);
        """)
        # Migrate: tasks.schedule_id（影子任务归属，D2；订阅删除后任务行保留）
        self._migrate_add_column(
            "tasks", "schedule_id",
            "TEXT REFERENCES task_schedules(id) ON DELETE SET NULL")
        # Migrate: task_schedule_runs.error（§8.4 失败原因，旧库由迁移补列）
        self._migrate_add_column("task_schedule_runs", "error", "TEXT")
        # Migrate: 订阅 scope 三型 + rollup 聚合圈选列（2.11 §12，V2.1-T2）
        self._migrate_add_column("task_schedules", "scope", "TEXT")
        self._migrate_add_column("task_schedules", "digest_schedule_ids", "TEXT")
        self._migrate_schedule_scope_backfill()
        self.conn.commit()

        # Leads（线索池，0.6 §4.1 V2-E2 唯一新表）：情报→项目蓄水池，薄卡反 CRM。
        # 新表启动幂等建表（存量库升级零迁移）；建表失败向上抛阻断启动。
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
                id          TEXT PRIMARY KEY,               -- 'lead_<hex8>'
                name        TEXT NOT NULL,                  -- 企业/项目名称
                source_type TEXT NOT NULL DEFAULT 'manual', -- 'run' | 'manual'
                source_run_id  TEXT,                        -- 来源期次（溯源；期次删除置 NULL）
                source_schedule_name TEXT DEFAULT '',       -- 来源订阅名快照（期次删除后仍可读）
                status      TEXT NOT NULL DEFAULT 'new',    -- new/contacting/evaluating/hold/converted
                note        TEXT DEFAULT '',                -- 备注/下一步
                project_id  TEXT,                           -- 立项后指向项目（converted 必填）
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status, updated_at);
        """)
        self.conn.commit()

        # Migrate: suggestions 幂等约束补项目作用域(整体 review C1)
        self._migrate_task_suggestions_unique()

        # Migrate: 清理独立 VLM 配置三键（2026-10-21 图片理解统一走 LLM 链路）
        self._migrate_drop_vlm_settings()

        # Migrate: shell 确认模式存量 smart 值跟随新默认 auto（2026-10-22）
        self._migrate_shell_confirm_default_auto()

    def _migrate_shell_confirm_default_auto(self):
        """shell 确认模式存量 smart 值跟随新默认 auto（2026-10-22 用户裁决）。

        设置页全量保存会把未修改的出厂值被动落库，仅按值升级 smart→auto；
        显式 strict / auto 保留不动。边缘取舍：显式选 smart 的存量值无法
        与被动落库区分，被一并升级（自用为主，可接受）；此后用户改回
        smart 不会被二次迁移（幂等按值匹配，改回即视为显式选择）。
        """
        cur = self.conn.execute(
            "UPDATE settings SET value = ? "
            "WHERE key = 'task_shell_confirm_mode' AND value = ?",
            ('"auto"', '"smart"'))
        self.conn.commit()
        if cur.rowcount:
            logger.info(
                "Migrated settings: task_shell_confirm_mode smart->auto x%d",
                cur.rowcount)

    def _migrate_drop_vlm_settings(self):
        """清理独立 VLM 配置三键（vlm_api_url/vlm_api_key/vlm_model）。

        2026-10-21 架构收敛：图片理解统一走 LLMClient（llm/vision.py），
        读侧已全删（readiness/tools/knowledge/routers），启动时幂等清理
        存量键，避免设置页出现无 UI 对应的幽灵键。已删即跳过（DELETE
        不存在的键 rowcount=0）；失败向上抛（settings 表异常意味着库文件
        损坏，静默继续不可取）。
        """
        cur = self.conn.execute(
            "DELETE FROM settings WHERE key IN "
            "('vlm_api_url', 'vlm_api_key', 'vlm_model')"
        )
        self.conn.commit()
        if cur.rowcount:
            logger.info("Migrated settings: dropped %d legacy vlm keys", cur.rowcount)

    def _migrate_task_suggestions_unique(self):
        """task_suggestions 补 UNIQUE(project_id, entry_fingerprint, kind)（整体
        review C1:应用层幂等查询需配套真实唯一索引兑底,且必须项目作用域——
        否则 A 项目 dismissed 会抑制 B 项目同文本建议）。

        SQLite 不能 ALTER 加约束：先按 created_at 保留最新去重存量重复行
        （仅限有 fingerprint 的行，无 fp 行不参与幂等），再建唯一索引；
        索引已存在则幂等跳过。失败向上抛（约束缺失会让「忽略过不再提」静默失效）。
        """
        for row in self.conn.execute("PRAGMA index_list(task_suggestions)").fetchall():
            if row["name"] == "uq_task_suggestions_fp":
                return  # 已迁移（上次启动已建）
        self.conn.execute(
            """DELETE FROM task_suggestions
               WHERE entry_fingerprint IS NOT NULL AND id NOT IN (
                   SELECT id FROM (
                       SELECT id, ROW_NUMBER() OVER (
                           PARTITION BY project_id, entry_fingerprint, kind
                           ORDER BY created_at DESC) AS rn
                       FROM task_suggestions
                       WHERE entry_fingerprint IS NOT NULL
                   ) WHERE rn = 1)""")
        self.conn.execute(
            "CREATE UNIQUE INDEX uq_task_suggestions_fp "
            "ON task_suggestions(project_id, entry_fingerprint, kind)")
        self.conn.commit()
        logger.info("Migrated task_suggestions: added UNIQUE(project_id, entry_fingerprint, kind)")

    def _migrate_schedule_scope_backfill(self):
        """存量订阅 scope 按 category 归类回填（2.11 §12，V2.1-T2）。

        industry/region → sweep（撒网），其余 → focus（盯梢）；rollup 仅用户
        显式新建不参与回填。幂等只回填 NULL 行——用户后续改值不被覆盖；
        失败向上抛（订阅行 scope 全 NULL 会让聚合源判定歧义，静默继续不可取）。
        """
        cur = self.conn.execute(
            "UPDATE task_schedules SET scope = CASE category "
            "WHEN 'industry' THEN 'sweep' WHEN 'region' THEN 'sweep' "
            "ELSE 'focus' END WHERE scope IS NULL")
        self.conn.commit()
        if cur.rowcount:
            logger.info(
                "Migrated task_schedules: scope backfilled x%d", cur.rowcount)

    def _migrate_add_column(self, table: str, column: str, col_type: str):
        """Add column to existing table if it doesn't exist yet."""
        try:
            cursor = self.conn.execute(f"PRAGMA table_info({table})")
            existing = [row["name"] for row in cursor.fetchall()]
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                self.conn.commit()
        except Exception:
            pass  # Table might not exist yet

    def _load_json_list(self, raw) -> list | None:
        """Safely decode a TEXT(JSON) list column; returns None for empty/invalid."""
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else None
        except (json.JSONDecodeError, TypeError):
            return None

    def _migrate_report_sections_unique(self):
        """report_sections 补 UNIQUE(report_id, section_id)（restart/resume 防重复行）。

        SQLite 不能 ALTER TABLE 加约束：先按 updated_at 保留最新一条去重存量
        重复行，再重建表；唯一索引已存在则幂等跳过。失败向上抛（DB 迁移失败
        意味着库文件异常，静默继续会让应用带着脏约束运行）。
        """
        for row in self.conn.execute("PRAGMA index_list(report_sections)").fetchall():
            cols = [
                c["name"] for c in
                self.conn.execute(f"PRAGMA index_info({row['name']})").fetchall()
            ]
            if row["unique"] and set(cols) == {"report_id", "section_id"}:
                return  # 已迁移（新库 DDL 自带或上次迁移已建）
        self.conn.executescript("""
            CREATE TABLE report_sections_migrated (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                section_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                word_count INTEGER DEFAULT 0,
                compose_mode TEXT DEFAULT 'standard',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(report_id, section_id)
            );
            INSERT INTO report_sections_migrated
                SELECT * FROM report_sections
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY report_id, section_id
                            ORDER BY updated_at DESC, created_at DESC
                        ) AS rn
                        FROM report_sections
                    ) WHERE rn = 1
                );
            DROP TABLE report_sections;
            ALTER TABLE report_sections_migrated RENAME TO report_sections;
            CREATE INDEX IF NOT EXISTS idx_report_sections_report
                ON report_sections(report_id);
        """)
        self.conn.commit()
        logger.info("Migrated report_sections: added UNIQUE(report_id, section_id)")

    def _now(self) -> str:
        return datetime.now().isoformat()

    def _uuid(self) -> str:
        return str(uuid.uuid4())

    # ── Settings ──

    def get_settings(self) -> dict:
        defaults = {
            "api_url": "https://open.bigmodel.cn/api/paas/v4/",
            "api_key": "",
            # 模型档位槽（2026-10-19 档位制，替代原单一 model 键）：
            # standard = 标准模型（默认档），high = 高配模型；路由键存档位（standard/high），
            # 由 llm/routing.get_task_settings 解析为最终模型名写入 model 键（client 契约不变）。
            # 两槽互为回退；开箱默认 glm-5.3-flash / glm-5.3，清空后全空时 model 为
            # 空串，请求将被网关拒（入口已被 readiness 拦截）。
            "standard_model": "glm-5.3-flash",
            "high_model": "glm-5.3",
            "temperature": 0.7,
            # 预算两键（llm/budget.py floor 读取时抬升）：默认值 = 现代模型实际
            # 下限（上下文 128K / 输出 32K），显示值即生效值；手填 0 = 自动（同样落下限）
            "max_tokens": 32768,
            "context_window": 131072,
            "top_p": 0.9,
            # Reasoning params (settings page 思考深度/高级覆盖) — must stay in
            # this whitelist or get_settings() silently drops saved values
            "reasoning_effort": "low",
            "reasoning_override": "",
            "system_prompt": "",
            "tavily_key": "",
            # 网络搜索供应商化（2026-10-19）：tavily | zhipu 二选一；空 = 未选择（必配未完成）；
            # 两把 Key 并存，切换供应商不丢配置
            "web_search_provider": "",
            "zhipu_search_key": "",
            "zhipu_search_engine": "search_pro",  # search_std | search_pro（智谱引擎档位）
            "max_concurrent_file_parsing": 2,
            "max_concurrent_image_description": 2,
            # Wiki compile settings（默认 2：并发单位是项目，项目内文件本就串行，
            # 2 = 两个项目并行编译；同项目由 API 层 409 防重保证单一编译任务）
            "max_compile_concurrency": 2,
            # LLM timeout settings
            "llm_connect_timeout": 30,
            "llm_read_timeout": 300,
            # Task-specific settings（路由键值 = 档位 standard/high；
            # planner 空串 = 不启用规划阶段（单模型模式，双模型架构语义保留））
            "task_model": "standard",
            "task_planner_model": "",
            "task_timeout": 30,  # chat 单命令护栏；ppt 长命令由 ORIGIN_PRESETS 落地 600（2.3 §3.2），不在此放大
            "task_max_iterations": 60,  # FC loop max iterations (TaskSettings UI)
            # PPT 任务专用参数（2.3 §3.2 ORIGIN_PRESETS）：None = 未设置，运行时
            # 落地预设值；显式设置即覆盖。通用键（上面两键）恒有默认值，以
            # 「get 为 None」判定未设置只对专用键成立——ORIGIN_PRESETS 必须用专用键
            "task_ppt_max_iterations": None,  # model-exempt: 无设置页 UI（全量 PUT 会覆盖用户值），覆盖走后端 API
            "task_ppt_timeout": None,  # model-exempt: 同上
            "task_shell_confirm_mode": "auto",  # strict | smart | auto（2026-10-22 默认自 smart 改为 auto；存量 smart 值启动迁移跟随，显式 strict/auto 不动）
            "task_auto_confirm_shell": False,  # legacy, kept for backward compat
            "task_shell_domain_whitelist": "",  # newline-separated domains auto-approved in smart mode
            "task_show_execution_log": True,
            "task_workspace_max_mb": 1024,
            "task_max_count": 50,
            "task_auto_archive_days": 30,
            # 存储保留策略（2026-10-20 数据管理审计，maintenance 启动清扫消费；
            # 0 = 永不清理，设置页 DataManagement 面板可改）
            "archived_task_cleanup_days": 30,
            "llm_usage_retention_days": 90,
            "schedule_runs_retention_days": 90,
            # Notification settings (2.11 §2.3)：OS 系统通知与提示音；
            # 设置页 UI 可改键，入全量保存白名单（ActivityMonitor 消费）
            "os_notify_enabled": True,
            "os_notify_sound": True,
            "affair_reminder_enabled": True,  # 事务到期/逾期提醒扫描开关（2.11 §3.2）
            # R4 订阅完成通知（review P2-7 提前落地，原 T11 项）：默认 False =
            # 静默（通知中心可见+静默已读，管理台红点是完成态消费入口）；开启后
            # completed 走正常 Toast/OS 通道（ActivityMonitor 即时重读）
            "schedule_notify": False,
            # 用户身份层（0.6 §3.8 V2-E1a）：PersonaPicker 首启呈现 + 模板排序
            "persona": "",
            "persona_prompted": False,
            # Report-specific settings
            "report_max_concurrent": 2,  # model-exempt: 预留位暂无 UI，后端消费（reports.py）；最大并发生成报告数
            # Form fill settings (2.4 §4.2)
            "form_fill_max_concurrent": 2,  # model-exempt: 预留位暂无 UI，后端消费（forms.py）；最大并发填表数
            "report_channel_parallelism": 4,  # model-exempt: 预留位暂无 UI，后端消费（runner.py）；通道内多 query 并行数
            # LLM task routing（llm/routing.py 唯一入口；值 = 档位 standard/high，
            # 空值按 standard 处理；除 ppt 默认 high 外全部默认 standard）
            "wiki_ingest_model": "standard",
            "wiki_lint_model": "standard",
            "wiki_dedup_model": "standard",
            "wiki_ledger_parse_model": "standard",  # 3.1 §4.2 台账解析（轻量）
            "report_model": "standard",  # 报告生成（长文写作）
            "report_extract_model": "standard",  # 报告模板提取（样例结构识别）
            "form_analyze_model": "standard",  # 填表模板分析（结构提取与语义映射）
            "form_fill_model": "standard",  # 填表执行（按检索资料生成字段值）
            "rag_model": "standard",  # 知识库问答
            "wiki_enrich_model": "standard",  # wiki 链接补全（轻量结构化）
            "ppt_model": "high",  # PPT 生成（逐页 SVG 创作，唯一默认高配）
            # 队列暂停开关（0.4 §队列暂停）：持久化于 settings，重启保持；
            # 经 POST /knowledge/queue/pause 写入（set_flag），运行时由 worker 闸门消费。
            # wiki 编译无队列暂停（0.4 §6.7 修订）：任务级「停止」+ 断点续传已覆盖；
            # 报告生成亦无队列暂停（2.5 §3.1 修订）：报告级「暂停」（章节边界协作生效
            # + checkpoint 续跑）与「重新生成」双出口已覆盖全部价值
            "parsing_paused": False,  # model-exempt: set_flag 直写（queue/pause 端点），入 Model 会被设置页全量保存误翻转
            "describe_paused": False,  # model-exempt: set_flag 直写（queue/pause 端点），入 Model 会被设置页全量保存误翻转
        }
        cursor = self.conn.execute("SELECT key, value FROM settings")
        for row in cursor.fetchall():
            key = row["key"]
            if key in defaults:
                val = row["value"]
                try:
                    defaults[key] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    defaults[key] = val
        return defaults

    def save_settings(self, settings: dict):
        for key, value in settings.items():
            self.conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
        self.conn.commit()

    def get_flag(self, key: str, default: bool = False) -> bool:
        """Read a boolean flag from the settings kv table (e.g. onboarding_done)."""
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return bool(json.loads(row["value"]))
        except (json.JSONDecodeError, TypeError):
            return default

    def set_flag(self, key: str, value: bool):
        """Persist a boolean flag into the settings kv table."""
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        self.conn.commit()

    # ── Knowledge Base: Projects ──

    def create_project(self, name: str, description: str = "", project_type: str = "general", stage: str = "none",
                       company_name: str = "", credit_code: str = "", seed_playbook: bool = True) -> dict:
        now = self._now()
        proj_id = self._uuid()
        # Pre-fill Wiki purpose by project type (user-editable later, §4.6)
        default_purpose = self.DEFAULT_PURPOSES.get(project_type, self.DEFAULT_PURPOSES["general"])
        # 3.0 §5.3: 创建+铺入+置标记同一事务(事务级锁覆盖,
        # 防工作线程在语句间交叉提交半成品事务或被误回滚;失败由 transaction 回滚并上抛)
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO projects (id, name, description, type, stage, company_name, credit_code, kind, wiki_purpose, playbook_seeded, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'project', ?, 0, ?, ?)",
                (proj_id, name, description, project_type, stage, company_name, credit_code, default_purpose, now, now),
            )
            seeded = self.insert_playbook_tasks(proj_id, project_type, stage) if seed_playbook else 0
            if seed_playbook:
                self.conn.execute(
                    "UPDATE projects SET playbook_seeded = 1 WHERE id = ?", (proj_id,))

        # Auto-link base libraries based on project type
        auto_libs = self.get_auto_linked_base_libs(project_type)
        for lib_id in auto_libs:
            self.link_project_to_base_lib(proj_id, lib_id, auto=True)

        return {
            "id": proj_id,
            "name": name,
            "description": description,
            "type": project_type,
            "stage": stage,
            "company_name": company_name,
            "credit_code": credit_code,
            "created_at": now,
            "updated_at": now,
            "seeded_tasks_count": seeded,
        }

    def list_projects(self, archived: bool = False) -> list[dict]:
        """列出项目（kind='project'），按 archived_at 分流：False=有效，True=已归档。"""
        cursor = self.conn.execute(
            """SELECT p.*, COUNT(f.id) as file_count
               FROM projects p
               LEFT JOIN files f ON p.id = f.project_id
               WHERE p.kind = 'project' AND (p.archived_at IS NOT NULL) = ?
               GROUP BY p.id
               ORDER BY p.updated_at DESC""",
            (1 if archived else 0,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_archived_projects(self) -> int:
        """已归档项目计数（侧边栏归档 tab 徽记）。"""
        cursor = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM projects "
            "WHERE kind = 'project' AND archived_at IS NOT NULL"
        )
        return cursor.fetchone()["cnt"]

    def archive_project(self, proj_id: str) -> bool:
        """归档项目（写 archived_at，数据零清理）。仅 kind='project'，返回是否生效。"""
        cursor = self.conn.execute(
            """UPDATE projects SET archived_at = ?, updated_at = ?
               WHERE id = ? AND kind = 'project' AND archived_at IS NULL""",
            (self._now(), self._now(), proj_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def unarchive_project(self, proj_id: str) -> bool:
        """恢复归档项目（清 archived_at）。仅 kind='project'，返回是否生效。"""
        cursor = self.conn.execute(
            """UPDATE projects SET archived_at = NULL, updated_at = ?
               WHERE id = ? AND kind = 'project' AND archived_at IS NOT NULL""",
            (self._now(), proj_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_project(self, proj_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM projects WHERE id = ?", (proj_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_project(
        self,
        proj_id: str,
        name: str | None = None,
        description: str | None = None,
        type: str | None = None,
        stage: str | None = None,
        company_name: str | None = None,
        credit_code: str | None = None,
    ):
        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if type is not None:
            updates.append("type = ?")
            params.append(type)
        if stage is not None:
            updates.append("stage = ?")
            params.append(stage)
        if company_name is not None:
            updates.append("company_name = ?")
            params.append(company_name)
        if credit_code is not None:
            updates.append("credit_code = ?")
            params.append(credit_code)
        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(proj_id)
        
        self.conn.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()

    def delete_project(self, proj_id: str) -> bool:
        """Delete a project (kind='project' only). Returns True if deleted."""
        cursor = self.conn.execute(
            "DELETE FROM projects WHERE id = ? AND kind = 'project'", (proj_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ── Knowledge Base: Files ──

    def create_file(
        self,
        project_id: str,
        filename: str,
        file_type: str,
        file_size: int,
        file_hash: str,
        file_id: str | None = None,
    ) -> dict:
        now = self._now()
        file_id = file_id or self._uuid()
        self.conn.execute(
            "INSERT INTO files (id, project_id, filename, file_type, file_size, file_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, project_id, filename, file_type, file_size, file_hash, "pending", now),
        )
        self.conn.commit()
        return {
            "id": file_id,
            "project_id": project_id,
            "filename": filename,
            "file_type": file_type,
            "file_size": file_size,
            "file_hash": file_hash,
            "status": "pending",
            "error_message": None,
            "chunk_count": 0,
            "image_count": 0,
            "created_at": now,
        }

    def update_file_status(
        self,
        file_id: str,
        status: str,
        error_message: str | None = None,
        chunk_count: int | None = None,
        image_count: int | None = None,
    ):
        updates = ["status = ?"]
        params = [status]
        
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if chunk_count is not None:
            updates.append("chunk_count = ?")
            params.append(chunk_count)
        if image_count is not None:
            updates.append("image_count = ?")
            params.append(image_count)
        
        params.append(file_id)
        
        self.conn.execute(
            f"UPDATE files SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()

        # 终态事件已解耦（E2，0.4 §6.4）：done / error 的通知由调用方
        # （pipeline / task_queue）经 activity_events.complete_file / fail_file 显式声明，
        # DB 层回归纯状态写入（写入位置盘点见 0.4 §8.2）。

    def list_files(self, project_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM files WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_file(self, file_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_file(self, file_id: str):
        self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        self.conn.commit()

    def find_duplicate_file(self, project_id: str, file_hash: str) -> dict | None:
        """Check if a file with the same hash already exists in the project."""
        cursor = self.conn.execute(
            "SELECT * FROM files WHERE project_id = ? AND file_hash = ? LIMIT 1",
            (project_id, file_hash),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    # ── Knowledge Base: Images ──

    def create_image(
        self,
        file_id: str,
        local_path: str,
        page_no: int | None = None,
        caption: str | None = None,
        width_px: int | None = None,
        height_px: int | None = None,
        dpi: int | None = None,
        figure_type: str | None = None,
        vlm_description: str | None = None,
        context_text: str | None = None,
        category: str | None = None,
    ) -> dict:
        now = self._now()
        img_id = self._uuid()
        self.conn.execute(
            "INSERT INTO images (id, file_id, page_no, caption, width_px, height_px, dpi, local_path, figure_type, vlm_description, context_text, category, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (img_id, file_id, page_no, caption, width_px, height_px, dpi, local_path, figure_type, vlm_description, context_text, category or "uncategorized", now),
        )
        self.conn.commit()
        return {
            "id": img_id,
            "file_id": file_id,
            "page_no": page_no,
            "caption": caption,
            "width_px": width_px,
            "height_px": height_px,
            "dpi": dpi,
            "local_path": local_path,
            "figure_type": figure_type,
            "vlm_description": vlm_description,
            "context_text": context_text,
            "category": category or "uncategorized",
            "created_at": now,
        }

    def list_images(self, file_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM images WHERE file_id = ? ORDER BY page_no ASC",
            (file_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_image(self, image_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM images WHERE id = ?", (image_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_image_vlm_description(self, image_id: str, vlm_description: str):
        self.conn.execute(
            "UPDATE images SET vlm_description = ? WHERE id = ?",
            (vlm_description, image_id),
        )
        self.conn.commit()

    def delete_image(self, image_id: str):
        self.conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
        self.conn.commit()

    def update_image_category(self, image_id: str, category: str):
        """Update image manual category."""
        self.conn.execute(
            "UPDATE images SET category = ? WHERE id = ?",
            (category, image_id),
        )
        self.conn.commit()

    # ── File management helpers ──

    def get_file_project(self, file_id: str) -> str | None:
        """Get project_id for a file."""
        cursor = self.conn.execute("SELECT project_id FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        return row["project_id"] if row else None

    def reset_file_for_retry(self, file_id: str):
        """Reset file status to pending for retry."""
        self.conn.execute(
            "UPDATE files SET status = 'pending', error_message = NULL, "
            "chunk_count = 0, image_count = 0, image_status = 'none', "
            "image_described_count = 0, progress = 0 WHERE id = ?",
            (file_id,)
        )
        self.conn.commit()

    def update_file_image_status(self, file_id: str):
        """Recalculate image_status from images table.
        
        Terminal states: 'done', 'archived' count as completed.
        'error' triggers error status if no other work is pending.
        """
        cursor = self.conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN status IN ('done', 'archived') THEN 1 ELSE 0 END) as done_cnt, "
            "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as err_cnt, "
            "SUM(CASE WHEN status IN ('pending', 'processing') THEN 1 ELSE 0 END) as pending_cnt "
            "FROM images WHERE file_id = ?",
            (file_id,)
        )
        row = cursor.fetchone()
        total = row["total"] or 0
        done_cnt = row["done_cnt"] or 0
        err_cnt = row["err_cnt"] or 0
        pending_cnt = row["pending_cnt"] or 0

        if total == 0:
            image_status = "none"
        elif pending_cnt > 0:
            image_status = "describing"
        elif done_cnt + err_cnt == total:
            # All images are in terminal states
            if err_cnt > 0 and done_cnt == 0:
                image_status = "error"
            else:
                image_status = "done"  # mix of done+archived, or all done
        else:
            image_status = "describing"

        self.conn.execute(
            "UPDATE files SET image_status = ?, image_described_count = ? WHERE id = ?",
            (image_status, done_cnt, file_id)
        )
        self.conn.commit()

    def update_file_progress(self, file_id: str, progress: int):
        """Update file processing progress (0-100)."""
        self.conn.execute(
            "UPDATE files SET progress = ? WHERE id = ?",
            (progress, file_id)
        )
        self.conn.commit()

    def delete_images_by_file(self, file_id: str) -> list[str]:
        """Delete all image records for a file, return list of local_paths."""
        cursor = self.conn.execute(
            "SELECT local_path FROM images WHERE file_id = ?", (file_id,)
        )
        paths = [row["local_path"] for row in cursor.fetchall()]
        self.conn.execute("DELETE FROM images WHERE file_id = ?", (file_id,))
        self.conn.commit()
        return paths

    def list_images_by_file(self, file_id: str) -> list[dict]:
        """List all images for a file."""
        cursor = self.conn.execute(
            "SELECT * FROM images WHERE file_id = ? ORDER BY page_no ASC",
            (file_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_image_status(self, image_id: str, status: str,
                            error_message: str | None = None,
                            vlm_description: str | None = None,
                            clear_vlm_description: bool = False):
        """Update image processing status.
        
        Args:
            clear_vlm_description: When True, explicitly set vlm_description to NULL.
        """
        updates = ["status = ?"]
        params: list = [status]
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if clear_vlm_description:
            updates.append("vlm_description = NULL")
        elif vlm_description is not None:
            updates.append("vlm_description = ?")
            params.append(vlm_description)
        params.append(image_id)
        self.conn.execute(
            f"UPDATE images SET {', '.join(updates)} WHERE id = ?", params
        )
        self.conn.commit()

    def get_pending_images(self, limit: int = 50) -> list[dict]:
        """Get pending images for image description worker."""
        cursor = self.conn.execute(
            "SELECT i.*, f.project_id FROM images i "
            "JOIN files f ON i.file_id = f.id "
            "WHERE i.status = 'pending' ORDER BY i.created_at ASC LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ── Tasks ──

    def create_task(self, title: str, data_dir: str, workspace_path: str = "", mode: str = "general", origin: str = "chat", schedule_id: str | None = None) -> dict:
        now = self._now()
        task_id = self._uuid()

        if workspace_path:
            # Use the user-provided directory
            os.makedirs(workspace_path, exist_ok=True)
            task_dir = os.path.dirname(workspace_path)
        else:
            # Auto-create under data_dir/tasks/{task_id}/workspace
            task_dir = os.path.join(data_dir, "tasks", task_id)
            workspace_path = os.path.join(task_dir, "workspace")
            os.makedirs(workspace_path, exist_ok=True)

        # Lazy per-task venv (空间优化): only record the deterministic venv
        # path; the venv directory itself is created on first code-execution
        # need (tools.venv_manager.ensure_venv via task_agent) so chat-only
        # tasks cost zero disk. User-provided workspaces stay venv-free.
        venv_path = ""
        if os.path.normcase(os.path.abspath(task_dir)).startswith(
                os.path.normcase(os.path.abspath(data_dir)) + os.sep):
            venv_path = os.path.join(task_dir, ".venv")

        self.conn.execute(
            "INSERT INTO tasks (id, title, status, workspace_path, venv_path, mode, origin, schedule_id, created_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
            (task_id, title, workspace_path, venv_path, mode, origin, schedule_id, now, now),
        )
        self.conn.commit()
        return {
            "id": task_id,
            "title": title,
            "status": "active",
            "workspace_path": workspace_path,
            "venv_path": venv_path,
            "mode": mode,
            "origin": origin,
            "schedule_id": schedule_id,
            "created_at": now,
            "updated_at": now,
        }

    def list_tasks(
        self, status: str | None = None, limit: int = 0, offset: int = 0,
        origin: str | None = None,
    ) -> tuple[list[dict], bool]:
        """List tasks. If status is None, return all non-deleted tasks.
        If status='active', return only active. If status='archived', return only archived.
        origin 过滤（2.3 §3.1 形态分离）：任务 Tab 固定传 'chat'；None 不过滤
        （崩溃恢复等内部扫描需要全量）。PPT 任务永不进任务 Tab。
        影子任务恒排除（2.11 D2：定时订阅执行不进人工任务列表，服务端排除
        前端零特判；get_task 直查仍可回放）。
        limit=0 → no pagination (backward compatible); limit>0 → fetch limit+1 rows
        to detect has_more without a COUNT query. Returns (tasks, has_more)."""
        if status == "archived":
            where = "t.status = 'archived'"
        elif status == "active":
            where = "t.status = 'active'"
        else:
            where = "t.status != 'deleted'"
        where += " AND t.schedule_id IS NULL"
        params: list = []
        if origin is not None:
            where += " AND t.origin = ?"
            params.append(origin)
        sql = f"""SELECT t.*, COUNT(m.id) as message_count
               FROM tasks t
               LEFT JOIN task_messages m ON t.id = m.task_id
               WHERE {where}
               GROUP BY t.id
               ORDER BY t.updated_at DESC"""
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit + 1, offset])
        rows = [dict(row) for row in self.conn.execute(sql, params).fetchall()]
        if limit > 0:
            has_more = len(rows) > limit
            return rows[:limit], has_more
        return rows, False

    def count_tasks(self) -> int:
        """Count non-deleted tasks (same visibility as list_tasks(status=None)).

        供 create_task 配额检查使用：SELECT COUNT(*) 代替全表载入
        （配额只需计数，反序列化全部行随任务量线性劣化）。"""
        cursor = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM tasks WHERE status != 'deleted'"
        )
        return cursor.fetchone()["cnt"]

    def get_task(self, task_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_task(self, task_id: str):
        task = self.get_task(task_id)
        if task:
            # Clean up workspace and venv directories — ONLY for app-managed
            # task dirs (data_dir/tasks/{task_id}). For user-provided workspaces
            # the parent dir belongs to the user; rmtree'ing it would wipe the
            # directory the user chose (or worse, its parent).
            # 锚点与 create_task 同源（get_data_dir）：曾锚 db_path 同级，
            # 两个数据目录推导并存会在布局迁移时互不一致（2026-10-20 审计）。
            from paths import get_data_dir
            workspace = task["workspace_path"]
            task_dir = os.path.dirname(workspace)
            managed_root = os.path.join(get_data_dir(), "tasks")
            if task_dir.startswith(managed_root + os.sep) and os.path.isdir(task_dir):
                shutil.rmtree(task_dir, ignore_errors=True)
        self.conn.execute("DELETE FROM task_messages WHERE task_id = ?", (task_id,))
        self.conn.execute("DELETE FROM task_agent_state WHERE task_id = ?", (task_id,))
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()

    def get_task_messages(self, task_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM task_messages WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
        results = []
        for row in cursor.fetchall():
            msg = dict(row)
            if msg.get("metadata"):
                msg["metadata"] = json.loads(msg["metadata"])
            results.append(msg)
        return results

    def save_task_message(self, task_id: str, role: str, content: str, metadata: dict | None = None) -> str:
        now = self._now()
        msg_id = self._uuid()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        self.conn.execute(
            "INSERT INTO task_messages (id, task_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, task_id, role, content, meta_json, now),
        )
        self.conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        self.conn.commit()
        return msg_id

    def update_task_message_metadata(self, msg_id: str, metadata: dict):
        """Update metadata for an existing task message."""
        meta_json = json.dumps(metadata, ensure_ascii=False)
        self.conn.execute(
            "UPDATE task_messages SET metadata = ? WHERE id = ?",
            (meta_json, msg_id),
        )
        self.conn.commit()

    def update_task_title_from_message(self, task_id: str, message: str):
        cursor = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM task_messages WHERE task_id = ?",
            (task_id,),
        )
        count = cursor.fetchone()["cnt"]
        if count <= 1:
            title = message[:30] + ("..." if len(message) > 30 else "")
            self.conn.execute(
                "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
                (title, self._now(), task_id),
            )
            self.conn.commit()

    def rename_task(self, task_id: str, title: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
            (title, self._now(), task_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def set_task_mode(self, task_id: str, mode: str) -> bool:
        """Set task mode to 'coding' or 'general'."""
        if mode not in ("coding", "general"):
            return False
        cursor = self.conn.execute(
            "UPDATE tasks SET mode = ?, updated_at = ? WHERE id = ?",
            (mode, self._now(), task_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def archive_task(self, task_id: str) -> bool:
        """Set task status to 'archived'."""
        cursor = self.conn.execute(
            "UPDATE tasks SET status = 'archived', updated_at = ? WHERE id = ? AND status != 'deleted'",
            (self._now(), task_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def unarchive_task(self, task_id: str) -> bool:
        """Set task status back to 'active'."""
        cursor = self.conn.execute(
            "UPDATE tasks SET status = 'active', updated_at = ? WHERE id = ?",
            (self._now(), task_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def auto_archive_stale_tasks(self, days: int = 30) -> int:
        """Archive tasks that haven't been updated in `days` days. Returns count archived."""
        if days <= 0:
            return 0
        cursor = self.conn.execute(
            "UPDATE tasks SET status = 'archived' WHERE status = 'active' "
            "AND updated_at < datetime('now', '-' || ? || ' days')",
            (str(days),),
        )
        self.conn.commit()
        return cursor.rowcount

    # ── Task Templates (0.5 §5, E2) ──

    def list_task_templates(self, scope: str | None = None,
                            project_id: str | None = None) -> list[dict]:
        """自定义行 + builtin=1 置顶记录行；scope/project_id 过滤
        （scope='project' 时须匹配 project_id）。"""
        sql = "SELECT * FROM task_templates"
        where, params = [], []
        if scope:
            where.append("scope = ?")
            params.append(scope)
        if scope == "project" and project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY pinned DESC, updated_at DESC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def upsert_task_template(self, tpl_id: str | None, scope: str,
                             project_id: str | None, category: str,
                             title: str, prompt: str,
                             pinned: bool = False) -> dict:
        """tpl_id 为 None 则新建（'tpl_custom_<hex8>'），否则更新；返回完整行。"""
        now = self._now()
        if tpl_id is None:
            tpl_id = f"tpl_custom_{uuid.uuid4().hex[:8]}"
            self.conn.execute(
                "INSERT INTO task_templates (id, scope, project_id, category, title,"
                " prompt, builtin, pinned, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (tpl_id, scope, project_id, category, title, prompt,
                 int(pinned), now, now))
        else:
            self.conn.execute(
                "UPDATE task_templates SET scope = ?, project_id = ?, category = ?,"
                " title = ?, prompt = ?, pinned = ?, updated_at = ? WHERE id = ?",
                (scope, project_id, category, title, prompt, int(pinned), now, tpl_id))
        self.conn.commit()
        return dict(self.conn.execute(
            "SELECT * FROM task_templates WHERE id = ?", (tpl_id,)).fetchone())

    def delete_task_template(self, tpl_id: str) -> bool:
        """删除自定义行；内置置顶记录行也可删（= 取消置顶，路由层同语义）。"""
        cur = self.conn.execute("DELETE FROM task_templates WHERE id = ?", (tpl_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def set_task_template_pinned(self, tpl_id: str, pinned: bool) -> bool:
        """自定义行直接 UPDATE；内置 id（'builtin_*' 前缀）映射为
        'tpl_builtin_<name>' 记录行：置顶 upsert 一行 builtin=1，取消置顶删行
        （0.5 §5 读写行为：内置模板不落库，记录行仅存置顶态）。"""
        if tpl_id.startswith("builtin_"):
            record_id = f"tpl_builtin_{tpl_id[len('builtin_'):]}"
            if pinned:
                now = self._now()
                self.conn.execute(
                    "INSERT INTO task_templates (id, scope, project_id, category,"
                    " title, prompt, builtin, pinned, created_at, updated_at)"
                    " VALUES (?, 'global', NULL, '', '', '', 1, 1, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET pinned = 1, updated_at = ?",
                    (record_id, now, now, now))
                self.conn.commit()
            else:
                self.delete_task_template(record_id)
            return True
        cur = self.conn.execute(
            "UPDATE task_templates SET pinned = ?, updated_at = ? WHERE id = ?",
            (int(pinned), self._now(), tpl_id))
        self.conn.commit()
        return cur.rowcount > 0

    # ── Task Schedules (2.11 §3, E3) ──

    _SCHEDULE_FIELDS = (
        "name", "prompt", "category", "interval_type", "interval_value",
        "target_type", "target_id", "auto_import", "archive_file_id",
        "archive_last_run_id", "enabled", "next_run_at",
        "scope", "digest_schedule_ids",
    )

    def create_schedule(self, name: str, prompt: str, mode: str, category: str,
                        interval_type: str, interval_value: str,
                        target_type: str | None = None,
                        target_id: str | None = None,
                        auto_import: int = 1, next_run_at: str | None = None,
                        enabled: int = 1,
                        scope: str | None = None,
                        digest_schedule_ids: str | None = None) -> dict:
        """digest_schedule_ids 传 TEXT(JSON)（router 序列化后传入），NULL=全量。"""
        now = self._now()
        sch_id = f"sch_{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO task_schedules (id, name, prompt, mode, category,"
            " interval_type, interval_value, target_type, target_id, auto_import,"
            " enabled, next_run_at, scope, digest_schedule_ids,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sch_id, name, prompt, mode, category, interval_type, interval_value,
             target_type, target_id, int(auto_import), int(enabled),
             next_run_at, scope, digest_schedule_ids, now, now))
        self.conn.commit()
        return self.get_schedule(sch_id)

    def update_schedule(self, schedule_id: str, fields: dict) -> bool:
        """白名单字段更新（2.11 §4.1 PATCH 语义）；白名单外键忽略，空集返回 False。"""
        sets, params = [], []
        for key in self._SCHEDULE_FIELDS:
            if key not in fields:
                continue
            value = fields[key]
            if key in ("auto_import", "enabled"):
                value = int(value)
            elif key == "digest_schedule_ids" and isinstance(value, (list, tuple)):
                # 入参归一：list 形态（直调方未序列化）落库前转 TEXT(JSON)
                value = json.dumps(list(value), ensure_ascii=False)
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.extend([self._now(), schedule_id])
        cur = self.conn.execute(
            f"UPDATE task_schedules SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()
        return cur.rowcount > 0

    def clear_schedules_target(self, target_type: str, target_id: str) -> list:
        """目标容器删除联动（验收-P2 对称置空，2.11 §6）：指向该容器的订阅
        target_type/target_id 置空（auto_import 保留——重新绑定后偏好不变），
        返回受影响订阅行（id/name）供通知合并。与「删订阅时 tasks.schedule_id
        外键 SET NULL」同构——target_id 无外键，此为手动等价。"""
        rows = self.conn.execute(
            "SELECT id, name FROM task_schedules"
            " WHERE target_type = ? AND target_id = ?",
            (target_type, target_id)).fetchall()
        if not rows:
            return []
        self.conn.execute(
            "UPDATE task_schedules SET target_type = NULL, target_id = NULL,"
            " updated_at = ? WHERE target_type = ? AND target_id = ?",
            (self._now(), target_type, target_id))
        self.conn.commit()
        return [dict(r) for r in rows]

    def delete_schedule(self, schedule_id: str) -> bool:
        """删订阅；runs 外键 CASCADE 级联删，tasks.schedule_id 外键 SET NULL。"""
        cur = self.conn.execute(
            "DELETE FROM task_schedules WHERE id = ?", (schedule_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_schedule(self, schedule_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM task_schedules WHERE id = ?",
            (schedule_id,)).fetchone()
        return self._normalize_schedule_row(dict(row)) if row else None

    def _normalize_schedule_row(self, row: dict) -> dict:
        """订阅行出参归一（2.11 §12）：scope NULL → 'focus'（存量/迁移前零判空）；
        digest_schedule_ids TEXT → list[str] | None（rollup 编辑回填直读）。"""
        row["scope"] = row.get("scope") or "focus"
        row["digest_schedule_ids"] = self._load_json_list(
            row.get("digest_schedule_ids"))
        return row

    def list_schedules(self, category: str | None = None,
                       enabled: int | None = None,
                       q: str | None = None,
                       limit: int = 0, offset: int = 0,
                       scope: str | None = None) -> list[dict]:
        """管理台左列 / 速览同源；各行附 latest_done（最新 done run 摘要，无则 None）
        与 is_running（存在 running run，§8.4 行呼吸点数据源）；scope 过滤
        （2.11 §12，管理台定位筛选；新参数置尾防既有位置调用错位）。
        limit=0 → 全量（默认，向后兼容）；limit>0 → 至多 limit 行
        （路由层传 limit+1 探测 has_more）。"""
        sql = (
            "SELECT s.*, r.id AS _ld_run_id, r.finished_at AS _ld_finished_at,"
            " r.summary AS _ld_summary, r2.id AS _lr_run_id"
            " FROM task_schedules s"
            " LEFT JOIN task_schedule_runs r ON r.id = ("
            "   SELECT id FROM task_schedule_runs"
            "   WHERE schedule_id = s.id AND status = 'done'"
            "   ORDER BY finished_at DESC LIMIT 1)"
            " LEFT JOIN task_schedule_runs r2 ON r2.id = ("
            "   SELECT id FROM task_schedule_runs"
            "   WHERE schedule_id = s.id AND status = 'running'"
            "   ORDER BY started_at DESC LIMIT 1)")
        where, params = [], []
        if category:
            where.append("s.category = ?")
            params.append(category)
        if enabled is not None:
            where.append("s.enabled = ?")
            params.append(int(enabled))
        if q:
            where.append("s.name LIKE ?")
            params.append(f"%{q}%")
        if scope:
            where.append("s.scope = ?")
            params.append(scope)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY s.created_at DESC"
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = []
        for r in self.conn.execute(sql, params).fetchall():
            row = self._normalize_schedule_row(dict(r))
            ld_id = row.pop("_ld_run_id")
            row["is_running"] = row.pop("_lr_run_id") is not None
            if ld_id:
                row["latest_done"] = {
                    "run_id": ld_id,
                    "finished_at": row.pop("_ld_finished_at"),
                    "summary": self._load_json_list(row.pop("_ld_summary")),
                }
            else:
                row.pop("_ld_finished_at")
                row.pop("_ld_summary")
                row["latest_done"] = None
            rows.append(row)
        return rows

    def count_schedules(self, category: str | None = None,
                        enabled: int | None = None,
                        q: str | None = None,
                        scope: str | None = None) -> int:
        """订阅计数（与 list_schedules 同口径：category/enabled/q/scope 筛选后计数）。"""
        sql = "SELECT COUNT(*) AS cnt FROM task_schedules"
        where, params = [], []
        if category:
            where.append("category = ?")
            params.append(category)
        if enabled is not None:
            where.append("enabled = ?")
            params.append(int(enabled))
        if q:
            where.append("name LIKE ?")
            params.append(f"%{q}%")
        if scope:
            where.append("scope = ?")
            params.append(scope)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.conn.execute(sql, params).fetchone()["cnt"]

    def list_due_schedules(self, now_iso: str) -> list[dict]:
        """调度 tick 扫描：enabled=1 AND next_run_at <= now（idx_schedules_next）；
        出参同 _normalize_schedule_row（rollup 分支按 scope 判定，2.11 §12）。"""
        return [self._normalize_schedule_row(dict(r)) for r in self.conn.execute(
            "SELECT * FROM task_schedules"
            " WHERE enabled = 1 AND next_run_at <= ?"
            " ORDER BY next_run_at ASC", (now_iso,)).fetchall()]

    def set_schedule_state(self, schedule_id: str,
                           next_run_at: str | None = None,
                           last_run_at: str | None = None,
                           consecutive_failures: int | None = None,
                           enabled: int | None = None) -> None:
        """调度器 tick 后进度更新；None = 不动该字段（熔断时 next_run_at 保留）。"""
        sets, params = [], []
        if next_run_at is not None:
            sets.append("next_run_at = ?")
            params.append(next_run_at)
        if last_run_at is not None:
            sets.append("last_run_at = ?")
            params.append(last_run_at)
        if consecutive_failures is not None:
            sets.append("consecutive_failures = ?")
            params.append(int(consecutive_failures))
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(int(enabled))
        if not sets:
            return
        params.append(schedule_id)
        self.conn.execute(
            f"UPDATE task_schedules SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()

    def _run_row(self, row) -> dict:
        """run 行 dict 化 + summary JSON 解析（TEXT → list[str] | None）。"""
        d = dict(row)
        d["summary"] = self._load_json_list(d.get("summary"))
        return d

    def insert_schedule_run(self, run_id: str, schedule_id: str,
                            task_id: str | None, status: str = "running",
                            error: str | None = None) -> None:
        """error：创建即带原因（V2.1-T3 rollup 空窗口 skipped 落
        「窗口内无期次」，ReadView 留痕可读）；终态失败原因走
        update_schedule_run（避免 skipped 行被写入 finished_at）。"""
        self.conn.execute(
            "INSERT INTO task_schedule_runs (id, schedule_id, task_id, status,"
            " error, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, schedule_id, task_id, status, error, self._now()))
        self.conn.commit()

    def update_schedule_run(self, run_id: str, status: str,
                            report_path: str | None = None,
                            summary: list | None = None,
                            error: str | None = None) -> None:
        """终态回填；summary（list）落库 JSON；error 记失败原因（§8.4）；
        status != 'running' 时记 finished_at。"""
        sets = ["status = ?"]
        params: list = [status]
        if report_path is not None:
            sets.append("report_path = ?")
            params.append(report_path)
        if summary is not None:
            sets.append("summary = ?")
            params.append(json.dumps(summary, ensure_ascii=False))
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if status != "running":
            sets.append("finished_at = ?")
            params.append(self._now())
        params.append(run_id)
        self.conn.execute(
            f"UPDATE task_schedule_runs SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()

    def mark_run_read(self, run_id: str) -> bool:
        """ReadView 打开即已读：回填 read_at；不存在 False，已读再读幂等 True。"""
        cur = self.conn.execute(
            "UPDATE task_schedule_runs SET read_at = ?"
            " WHERE id = ? AND read_at IS NULL", (self._now(), run_id))
        self.conn.commit()
        if cur.rowcount > 0:
            return True
        row = self.conn.execute(
            "SELECT 1 FROM task_schedule_runs WHERE id = ?", (run_id,)).fetchone()
        return row is not None

    def delete_schedule_run(self, run_id: str) -> bool:
        """单条删除期次行（路由层先行终态守卫与产物清理：报告副本 unlink /
        cancelled 半成品 workspace rmtree / 锚点悬空防护，2026-10-20 审计补齐）。
        done/failed 的 workspace 不在此入口删（保留期 keep=10 治理）。"""
        cur = self.conn.execute(
            "DELETE FROM task_schedule_runs WHERE id = ?", (run_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_schedule_runs(self, schedule_id: str, limit: int = 20,
                           offset: int = 0) -> list[dict]:
        """订阅期次历史（started_at DESC）；带订阅名与档案锚点（ReadView 沉淀态）；
        offset 翻页（路由层传 limit+1 探测 has_more）；limit=-1 → 全部
        （SQLite 语义，delete_schedule 级联清理用）。"""
        return [self._run_row(r) for r in self.conn.execute(
            "SELECT r.*, s.name AS schedule_name, s.archive_last_run_id"
            " FROM task_schedule_runs r JOIN task_schedules s ON s.id = r.schedule_id"
            " WHERE r.schedule_id = ? ORDER BY r.started_at DESC LIMIT ? OFFSET ?",
            (schedule_id, limit, offset)).fetchall()]

    def get_run(self, run_id: str) -> dict | None:
        """按 id 查单个期次（report 端点 / ReadView 入口）。"""
        row = self.conn.execute(
            "SELECT r.*, s.name AS schedule_name, s.archive_last_run_id"
            " FROM task_schedule_runs r JOIN task_schedules s ON s.id = r.schedule_id"
            " WHERE r.id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_run_inbox(self, unread_only: bool = False,
                       limit: int = 50, offset: int = 0,
                       sort: str = "unread", category: str | None = None) -> list[dict]:
        """跨订阅收件箱流（V2-E1a 情报中心数据源）：done；sort=unread 未读
        优先（read_at IS NULL 前置）/ sort=latest 纯 finished_at DESC；
        category 按订阅分类直读过滤（corporate/industry/region/general）；
        返回行附 schedule_category（卡片类别 chip 数据源）+ schedule_scope
        （rollup 徽标随行，2.11 §12.4）；
        offset 翻页（路由层传 limit+1 探测 has_more）。"""
        sql = (
            "SELECT r.*, s.name AS schedule_name, s.category AS schedule_category,"
            " s.scope AS schedule_scope"
            " FROM task_schedule_runs r"
            " JOIN task_schedules s ON s.id = r.schedule_id"
            " WHERE r.status = 'done'")
        params: list = []
        if category:
            sql += " AND s.category = ?"
            params.append(category)
        if unread_only:
            sql += " AND r.read_at IS NULL"
        if sort == "latest":
            sql += " ORDER BY r.finished_at DESC LIMIT ? OFFSET ?"
        else:
            sql += (" ORDER BY (r.read_at IS NULL) DESC, r.finished_at DESC"
                    " LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        return [self._run_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def list_run_digest(self) -> list[dict]:
        """首页情报速览（T9 修订①）：每订阅最新 1 条 run（含 running/failed，
        COALESCE(finished_at, started_at) 排序 + rowid 兼同微秒序）+ 订阅
        enabled —— §8.4 速览行三态（运行呼吸/失败红/暂停灰）数据可达。"""
        return [self._run_row(r) for r in self.conn.execute(
            "SELECT r.*, s.name AS schedule_name, s.enabled AS schedule_enabled,"
            "       s.category AS schedule_category, s.scope AS schedule_scope"
            " FROM task_schedule_runs r"
            " JOIN task_schedules s ON s.id = r.schedule_id"
            " WHERE r.id = ("
            "   SELECT id FROM task_schedule_runs"
            "   WHERE schedule_id = r.schedule_id"
            "   ORDER BY COALESCE(finished_at, started_at) DESC, rowid DESC LIMIT 1)"
            " ORDER BY COALESCE(r.finished_at, r.started_at) DESC, r.rowid DESC").fetchall()]

    def schedule_unread_count(self) -> dict:
        """侧栏摘要行计数：total=未读 done run 数；n=订阅总数；
        abnormal=熔断订阅数（enabled=0 且 consecutive_failures>0）——手动暂停
        是用户主动决策非告警，不计入（2026-10-23 口径收紧，对齐管理台
        abnormal/paused 筛选）。"""
        unread = self.conn.execute(
            "SELECT COUNT(*) AS c FROM task_schedule_runs"
            " WHERE status = 'done' AND read_at IS NULL").fetchone()["c"]
        row = self.conn.execute(
            "SELECT COUNT(*) AS n,"
            " COALESCE(SUM(CASE WHEN enabled = 0 AND consecutive_failures > 0"
            " THEN 1 ELSE 0 END), 0) AS abnormal"
            " FROM task_schedules").fetchone()
        return {"total": unread, "abnormal": row["abnormal"], "n": row["n"]}

    def count_running_runs(self) -> int:
        """R1 定时单并发：全局 running 计数。"""
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM task_schedule_runs"
            " WHERE status = 'running'").fetchone()["c"]

    def fail_stale_running_runs(self, error: str) -> list[str]:
        """启动清扫（2.11 §5.4，review P0-1）：进程中断残留的 running run 行
        批量置 failed（error 落库供 ReadView 显示），返回被清扫的 run id。
        仅调度器启动路径调用（此刻无在途 _execute，running 必为残留）——
        否则 R1 全局闸门被永久占住，全部订阅 deferred。"""
        ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM task_schedule_runs"
            " WHERE status = 'running'").fetchall()]
        for run_id in ids:
            self.update_schedule_run(run_id, "failed", error=error)
        return ids

    def schedule_last_run_running(self, schedule_id: str) -> bool:
        """2.11 §5.2 防重入：该订阅最新一条 run 是否仍 running（rowid 兼同微秒序）。"""
        row = self.conn.execute(
            "SELECT status FROM task_schedule_runs WHERE schedule_id = ?"
            " ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (schedule_id,)).fetchone()
        return bool(row) and row["status"] == "running"

    def last_done_run(self, schedule_id: str) -> dict | None:
        """增量注入 + 时间基准（2.11 §5.3/§6）：上期 done run 全行
        （report_path 供复制《上一期报告.md》、finished_at 供执行侧拼
        「上期：」时间基准行）。"""
        row = self.conn.execute(
            "SELECT id, report_path, finished_at FROM task_schedule_runs"
            " WHERE schedule_id = ? AND status = 'done'"
            " ORDER BY finished_at DESC LIMIT 1", (schedule_id,)).fetchone()
        return dict(row) if row else None

    def list_digest_source_runs(self, schedule_ids: list[str] | None,
                                window_start: str,
                                max_runs: int | None = None) -> list[dict]:
        """组合周报聚合素材（2.11 §12.3）：圈定订阅 × 窗口内 done 期次（升序）。
        schedule_ids=None → 全部 enabled=1 订阅；有值 → 显式圈定（含已暂停
        订阅的窗口内期次——勾选即用户意志，期次已产出仍可汇总）。
        scope='rollup' 恒排除（不变量①防递归聚合，聚合侧兜底正确性；
        IS NULL 兜住未被 backfill 覆盖的异常行）；summary 出参解析为 list
        （与 _run_row 同口径）。空圈选集返回 []（同时规避 IN () 语法错）。
        max_runs：防无界 prompt（CodeReview m-2）——保留窗口内最近 N 期，
        None = 不设限（全量，向后兼容）；0 → []（无负索引切片陷阱）。"""
        if schedule_ids is not None and not schedule_ids:
            return []
        sql = (
            "SELECT r.finished_at, r.summary, s.name AS schedule_name"
            " FROM task_schedule_runs r"
            " JOIN task_schedules s ON s.id = r.schedule_id"
            " WHERE r.status = 'done' AND r.finished_at > ?"
            "   AND (s.scope IS NULL OR s.scope != 'rollup')")
        params: list = [window_start]
        if schedule_ids is None:
            sql += " AND s.enabled = 1"
        else:
            sql += (" AND r.schedule_id IN ("
                    + ",".join("?" * len(schedule_ids)) + ")")
            params.extend(schedule_ids)
        sql += " ORDER BY r.finished_at ASC"          # 素材时间序（截断前排序）
        rows = self.conn.execute(sql, params).fetchall()
        if max_runs is not None:
            # 窗口内期次多时保留最近 N 期（取尾段，返回仍升序）；
            # 不用 rows[-max_runs:]（max_runs=0 时 -0 切片返回全量）
            rows = rows[len(rows) - max_runs:] if max_runs > 0 else []
        return [{"schedule_name": r["schedule_name"],
                 "finished_at": r["finished_at"],
                 "summary": self._load_json_list(r["summary"]) or []}
                for r in rows]

    def count_schedule_runs(self, schedule_id: str) -> int:
        """期数（2.11 §5.3 时间基准行「第 N 期」）：该订阅全部期次行数——
        本期 run 行在 dispatch 时已插入，故含本期（第 N 期语义）。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM task_schedule_runs"
            " WHERE schedule_id = ?", (schedule_id,)).fetchone()
        return int(row["cnt"])

    def pending_archive_runs(self, schedule_id: str) -> list[dict]:
        """档案待合并期次：archive_last_run_id 之后的 done 副本（升序；锚点空=全部 done）。"""
        row = self.conn.execute(
            "SELECT archive_last_run_id FROM task_schedules WHERE id = ?",
            (schedule_id,)).fetchone()
        anchor = row["archive_last_run_id"] if row else None
        if anchor:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM task_schedule_runs"
                " WHERE schedule_id = ? AND status = 'done'"
                " AND started_at > (SELECT started_at FROM task_schedule_runs"
                "                    WHERE id = ?)"
                " ORDER BY started_at ASC",
                (schedule_id, anchor)).fetchall()]
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM task_schedule_runs"
            " WHERE schedule_id = ? AND status = 'done'"
            " ORDER BY started_at ASC", (schedule_id,)).fetchall()]

    def count_schedule_workspaces_keep(self, schedule_id: str,
                                       keep: int = 10) -> list[str]:
        """保留期清理（2.11 §6）：超出最近 keep 期终态 run（done/failed，
        review P2-8：failed 亦占 workspace 目录，永久累积泄漏磁盘）的 workspace。"""
        rows = self.conn.execute(
            "SELECT t.workspace_path FROM task_schedule_runs r"
            " JOIN tasks t ON t.id = r.task_id"
            " WHERE r.schedule_id = ? AND r.status IN ('done', 'failed')"
            "   AND t.workspace_path != ''"
            " ORDER BY r.started_at DESC LIMIT -1 OFFSET ?",
            (schedule_id, keep)).fetchall()
        return [r["workspace_path"] for r in rows]

    # ── Leads（线索池，0.6 §4.1 V2-E2）──

    def create_lead(self, name: str, source_type: str = "manual",
                    source_run_id: str | None = None,
                    source_schedule_name: str = "",
                    status: str = "new", note: str = "") -> dict:
        """新建线索（存为线索 / 提取入池 / 手动录入共用）。"""
        lead_id = f"lead_{uuid.uuid4().hex[:8]}"
        now = self._now()
        self.conn.execute(
            "INSERT INTO leads (id, name, source_type, source_run_id,"
            " source_schedule_name, status, note, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_id, name, source_type, source_run_id,
             source_schedule_name, status, note, now, now))
        self.conn.commit()
        return self.get_lead(lead_id)

    def get_lead(self, lead_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None

    def list_leads(self, status: str | None = None,
                   limit: int = 0, offset: int = 0) -> list[dict]:
        """线索列表（updated_at DESC）；status 过滤；limit=0 → 全量
        （LeadPool 状态分组视图）；limit>0 → 至多 limit 行（路由层传
        limit+1 探测 has_more）。"""
        sql = "SELECT * FROM leads"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC"
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def count_leads(self, status: str | None = None) -> int:
        """线索计数（与 list_leads 同口径 status 过滤后）。"""
        sql = "SELECT COUNT(*) AS cnt FROM leads"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        return self.conn.execute(sql, params).fetchone()["cnt"]

    def update_lead(self, lead_id: str, fields: dict) -> bool:
        """单行 PATCH（状态推进 / 备注编辑 / 立项回填 project_id+converted）。
        白名单 status/note/project_id；不存在 False；updated_at 自动。"""
        allowed = {"status", "note", "project_id"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.extend([self._now(), lead_id])
        cur = self.conn.execute(
            f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()
        return cur.rowcount > 0

    def delete_lead(self, lead_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM leads WHERE id = ?", (lead_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def detach_leads_for_run(self, run_id: str) -> int:
        """期次删除联动（0.6 §4.1）：source_run_id 置 NULL（线索保留），
        订阅名快照兜底可读溯源。返回受影响行数（调用方日志）。"""
        cur = self.conn.execute(
            "UPDATE leads SET source_run_id = NULL, updated_at = ?"
            " WHERE source_run_id = ?", (self._now(), run_id))
        self.conn.commit()
        return cur.rowcount

    def detach_leads_for_schedule(self, schedule_id: str) -> int:
        """订阅删除联动（runs 级联删前调用）：按订阅关联全部期次置 NULL。"""
        cur = self.conn.execute(
            "UPDATE leads SET source_run_id = NULL, updated_at = ?"
            " WHERE source_run_id IN ("
            "   SELECT id FROM task_schedule_runs WHERE schedule_id = ?)",
            (self._now(), schedule_id))
        self.conn.commit()
        return cur.rowcount

    def detach_leads_for_project(self, project_id: str) -> int:
        """项目删除联动（V2-E2 CodeReview Major 2026-10-24）：立项引用置 NULL
        （线索与 converted 状态保留——前端 project_id 为空即不渲染「查看项目」，
        与 clear_schedules_target 同构：project_id 无外键，手动等价 SET NULL）。"""
        cur = self.conn.execute(
            "UPDATE leads SET project_id = NULL, updated_at = ?"
            " WHERE project_id = ?", (self._now(), project_id))
        self.conn.commit()
        return cur.rowcount

    def detach_rollup_digest_references(self, removed_schedule_id: str) -> int:
        """订阅删除联动（V2.1 CodeReview Major）：从引用它的 rollup 订阅
        digest_schedule_ids 中剔除——悬空 id 会使编辑保存 400 死锁（回填含
        悬空 id → 勾选框只渲染现存候选无法移除 → _validate_digest_ids
        fail-fast）。剔空保留 [] 不转 NULL：NULL=全量会突变聚合范围
        （静默泄露未圈定订阅进 rollup）；[] → 空窗口 skipped 留痕，用户
        主动修正。返回受影响 rollup 行数（调用方日志）。"""
        rows = self.conn.execute(
            "SELECT id, digest_schedule_ids FROM task_schedules"
            " WHERE scope = 'rollup' AND digest_schedule_ids IS NOT NULL").fetchall()
        touched = 0
        for row in rows:
            try:
                ids = json.loads(row["digest_schedule_ids"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(ids, list) or removed_schedule_id not in ids:
                continue
            self.update_schedule(
                row["id"],
                {"digest_schedule_ids": [i for i in ids
                                         if i != removed_schedule_id]})
            touched += 1
        return touched

    # ── Skills ──

    def list_skills(self, limit: int = 0, offset: int = 0) -> list[dict]:
        """limit=0 → 全量（默认，向后兼容：install 查重/delete/toggle 内部调用）；
        limit>0 → 至多 limit 行（路由层传 limit+1 探测 has_more）。"""
        sql = "SELECT * FROM skills ORDER BY created_at DESC"
        params: list = []
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def create_skill(
        self, name: str, description: str, source_type: str,
        source_path: str, install_path: str,
    ) -> dict:
        now = self._now()
        skill_id = self._uuid()
        self.conn.execute(
            """INSERT INTO skills (id, name, description, source_type, source_path, install_path, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (skill_id, name, description, source_type, source_path, install_path, now),
        )
        self.conn.commit()
        return {
            "id": skill_id,
            "name": name,
            "description": description,
            "source_type": source_type,
            "source_path": source_path,
            "install_path": install_path,
            "enabled": True,
            "created_at": now,
        }

    def delete_skill(self, skill_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def toggle_skill(self, skill_id: str, enabled: bool):
        self.conn.execute(
            "UPDATE skills SET enabled = ? WHERE id = ?",
            (int(enabled), skill_id),
        )
        self.conn.commit()

    def confirm_skill(self, skill_id: str) -> bool:
        """Mark a skill as confirmed (first-use acknowledgement done)."""
        cursor = self.conn.execute(
            "UPDATE skills SET needs_confirm = 0 WHERE id = ?",
            (skill_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def confirm_skill_by_name(self, name: str) -> bool:
        """Permanent trust by skill name (task-dialog confirmation, 1.1 §7.2).

        The task confirm dialog is the first-use acknowledgement point; after
        this the skill never prompts again (settings toggle can still disable)."""
        cursor = self.conn.execute(
            "UPDATE skills SET needs_confirm = 0 WHERE name = ?",
            (name,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ── Web Logins (2.8 §3.2) ──

    def list_web_logins(self, limit: int = 0, offset: int = 0) -> list[dict]:
        """limit=0 → 全量（默认，向后兼容）；limit>0 → 至多 limit 行
        （路由层传 limit+1 探测 has_more，对齐 list_tasks 先例）。"""
        sql = "SELECT domain, note, created_at FROM web_logins ORDER BY created_at DESC"
        params: list = []
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def add_web_login(self, domain: str, note: str | None) -> dict:
        """登记站点（幂等）：已存在时保留原行（note 也不覆盖——避免任务卡片
        自动登记路径覆盖用户手写备注），返回行与 inserted 标志。"""
        now = self._now()
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO web_logins (domain, note, created_at) VALUES (?, ?, ?)",
            (domain, note, now),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT domain, note, created_at FROM web_logins WHERE domain = ?",
            (domain,),
        ).fetchone()
        result = dict(row) if row else {"domain": domain, "note": note, "created_at": now}
        result["inserted"] = cursor.rowcount > 0
        return result

    def delete_web_login(self, domain: str) -> bool:
        cursor = self.conn.execute("DELETE FROM web_logins WHERE domain = ?", (domain,))
        self.conn.commit()
        return cursor.rowcount > 0

    # ── Token Usage (2.10 §3.1/§4.5) ──

    _USAGE_AGG = (
        "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
        "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
        "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
        "COUNT(*) AS calls"
    )

    def insert_llm_usage(self, row: dict):
        """llm/usage.py sink 目标：一调用一行（append-only，无 UPDATE）。"""
        self.conn.execute(
            """INSERT INTO llm_usage
               (ts, source, purpose, task_id, round_id, project_id, entity_id,
                model, mode, prompt_tokens, completion_tokens, total_tokens, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["ts"], row["source"], row.get("purpose"),
                row.get("task_id"), row.get("round_id"),
                row.get("project_id"), row.get("entity_id"),
                row["model"], row["mode"],
                row.get("prompt_tokens", 0), row.get("completion_tokens", 0),
                row.get("total_tokens", 0),
                json.dumps(row["detail"], ensure_ascii=False) if row.get("detail") else None,
            ),
        )
        self.conn.commit()

    def attach_usage_to_last_assistant(self, task_id: str, usage: dict):
        """轮次收尾：该轮 usage 汇总写进最后一条 assistant 消息 metadata.usage
        （气泡历史回放数据源，§4.5；只改 metadata 不动 content）。"""
        row = self.conn.execute(
            "SELECT id, metadata FROM task_messages "
            "WHERE task_id = ? AND role = 'assistant' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is None:
            return
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            if not isinstance(meta, dict):
                meta = {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        meta["usage"] = usage
        self.conn.execute(
            "UPDATE task_messages SET metadata = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), row["id"]),
        )
        self.conn.commit()

    @staticmethod
    def _usage_range_start(range_key: str):
        """range_key → 本地时间起点（None = 全部）；today = 本地自然日零点。"""
        now = datetime.now()
        if range_key == "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        if range_key == "7d":
            return (now - timedelta(days=7)).isoformat()
        if range_key == "30d":
            return (now - timedelta(days=30)).isoformat()
        return None  # all

    def _usage_agg_rows(self, conds: list[str], params: list,
                        group: str | None = None) -> list[dict]:
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        sql = f"SELECT {self._USAGE_AGG}"
        if group:
            sql += f", {group} AS grp"
        sql += f" FROM llm_usage {where}"
        if group:
            sql += f" GROUP BY {group} ORDER BY total_tokens DESC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_usage_summary(self, range_key: str = "all",
                          project_id: str | None = None) -> dict:
        """聚合总览（后端实时计算无缓存——任何重取天然正确，§5.4 自愈根因）。

        today/total 两组固定（状态栏一次请求拿全）；range 段与 by_* 明细
        随 range_key 窗口变化（统计页汇总卡与三表数据源）。
        """
        def _scoped_conds(scope_range: str | None):
            conds, params = [], []
            start = self._usage_range_start(scope_range) if scope_range else None
            if start:
                conds.append("ts >= ?")
                params.append(start)
            if project_id:
                conds.append("project_id = ?")
                params.append(project_id)
            return conds, params

        today_conds, today_params = _scoped_conds("today")
        total_conds, total_params = _scoped_conds(None)
        range_conds, range_params = _scoped_conds(
            range_key if range_key in ("today", "7d", "30d") else None)

        today_agg = self._usage_agg_rows(today_conds, today_params)
        total_agg = self._usage_agg_rows(total_conds, total_params)
        range_agg = self._usage_agg_rows(range_conds, range_params)
        by_day = self._usage_agg_rows(
            range_conds, range_params, group="date(ts)")
        by_source = self._usage_agg_rows(
            range_conds, range_params, group="source")
        by_model = self._usage_agg_rows(
            range_conds, range_params, group="model")
        by_project = self._usage_agg_rows(
            range_conds, range_params, group="project_id")
        # by_project 补名称（列表展示；无关联行如 task 场景为 NULL → 显示原 id）
        names = {r["id"]: r["name"] for r in self.conn.execute(
            "SELECT id, name FROM projects").fetchall()}
        for r in by_project:
            r["project_name"] = names.get(r["grp"]) if r["grp"] else None
        empty = {"prompt_tokens": 0, "completion_tokens": 0,
                 "total_tokens": 0, "calls": 0}
        models_count = self.conn.execute(
            f"SELECT COUNT(DISTINCT model) AS n FROM llm_usage "
            f"{f'WHERE {" AND ".join(total_conds)}' if total_conds else ''}",
            total_params,
        ).fetchone()["n"]
        return {
            "today": today_agg[0] if today_agg else empty,
            "total": dict(total_agg[0] if total_agg else empty,
                          models_count=models_count),
            "range": range_agg[0] if range_agg else empty,
            "range_key": range_key,
            "by_day": by_day,
            "by_source": by_source,
            "by_model": by_model,
            "by_project": by_project,
        }

    def get_usage_task_rounds(self, task_id: str, limit: int = 0,
                              offset: int = 0) -> list[dict]:
        """任务轮次聚合（GET /usage/task/{id}）：每轮 = 同 round_id 的全部调用。

        limit=0 → 全量（默认，向后兼容）；limit>0 → 至多 limit 轮
        （路由层传 limit+1 探测 has_more；分页作用于 GROUP BY 之后的轮次行）。
        """
        sql = f"""SELECT round_id,
                       MIN(ts) AS started_at,
                       {self._USAGE_AGG},
                       GROUP_CONCAT(DISTINCT model) AS models
                FROM llm_usage
                WHERE task_id = ? AND round_id IS NOT NULL
                GROUP BY round_id ORDER BY started_at"""
        params: list = [task_id]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_usage_by_round(self, round_id: str) -> dict:
        """单轮聚合（SSE usage 事件 / metadata.usage 轮次收尾，§4.5）。

        models 为该轮去重模型列表（GROUP_CONCAT，供气泡 tooltip 多模型显示）；
        无记录（流式端点不支持 include_usage 等）返回空 dict，调用方跳过。
        """
        row = self.conn.execute(
            f"SELECT {self._USAGE_AGG}, GROUP_CONCAT(DISTINCT model) AS models "
            "FROM llm_usage WHERE round_id = ?",
            (round_id,),
        ).fetchone()
        if row is None or row["calls"] == 0:
            return {}
        return dict(row)

    def _usage_group_by(self, col: str, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT {col} AS grp, {self._USAGE_AGG} FROM llm_usage "
            f"WHERE {col} IN ({placeholders}) GROUP BY {col}",
            list(ids),
        ).fetchall()
        return {r["grp"]: {k: v for k, v in dict(r).items() if k != "grp"}
                for r in rows}

    def get_usage_by_tasks(self, task_ids: list[str]) -> dict[str, dict]:
        """任务列表行聚合（GET /tasks 附加字段）。"""
        return self._usage_group_by("task_id", task_ids)

    def get_usage_by_projects(self, project_ids: list[str]) -> dict[str, dict]:
        """项目列表行聚合（listProjects 附加字段）。"""
        return self._usage_group_by("project_id", project_ids)

    def get_usage_by_entities(self, entity_ids: list[str]) -> dict[str, dict]:
        """实体行聚合（报告/填表/编译列表行内消耗）。"""
        return self._usage_group_by("entity_id", entity_ids)

    # ── Agent State (Pause/Resume) ──

    def save_agent_state(self, task_id: str, state: dict) -> bool:
        """Save agent execution state for pause."""
        state_json = json.dumps(state, ensure_ascii=False)
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO task_agent_state (task_id, state_json, paused_at) VALUES (?, ?, ?)",
                (task_id, state_json, self._now()),
            )
            self.conn.commit()
            return True
        except Exception:
            return False

    def load_agent_state(self, task_id: str) -> dict | None:
        """Load saved agent state for resume. Returns None if no saved state."""
        cursor = self.conn.execute(
            "SELECT state_json FROM task_agent_state WHERE task_id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row["state_json"])
        return None

    def delete_agent_state(self, task_id: str):
        """Delete saved agent state after resume or cancel."""
        self.conn.execute(
            "DELETE FROM task_agent_state WHERE task_id = ?",
            (task_id,),
        )
        self.conn.commit()

    def list_tasks_with_saved_state(self) -> list[str]:
        """Return task_ids that have saved agent state (for crash recovery)."""
        cursor = self.conn.execute(
            "SELECT task_id FROM task_agent_state"
        )
        return [row["task_id"] for row in cursor.fetchall()]

    def has_interrupt_marker(self, task_id: str) -> bool:
        """Check if the last message for a task is already an interrupt marker."""
        cursor = self.conn.execute(
            "SELECT content FROM task_messages WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        row = cursor.fetchone()
        if row:
            content = row["content"] or ""
            return content.startswith("\u26a0\ufe0f")
        return False

    # ── Reports ──

    def create_report(
        self,
        project_id: str,
        template_id: str,
        title: str,
        output_format: str = "docx",
        content_path: str | None = None,
    ) -> dict:
        now = self._now()
        report_id = self._uuid()
        self.conn.execute(
            """INSERT INTO reports (id, project_id, template_id, title, output_format, status, content_path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'generating', ?, ?, ?)""",
            (report_id, project_id, template_id, title, output_format, content_path, now, now),
        )
        self.conn.commit()
        return {
            "id": report_id,
            "project_id": project_id,
            "template_id": template_id,
            "title": title,
            "output_format": output_format,
            "status": "generating",
            "progress": 0,
            "content_path": content_path,
            "created_at": now,
            "updated_at": now,
        }

    def list_reports(self, project_id: str, limit: int = 0, offset: int = 0) -> list[dict]:
        """limit=0 → 全量（默认，向后兼容）；limit>0 → 至多 limit 行
        （路由层传 limit+1 探测 has_more，对齐 list_tasks 先例）。"""
        sql = "SELECT * FROM reports WHERE project_id = ? ORDER BY created_at DESC"
        params: list = [project_id]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_report(self, report_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_report_status(self, report_id: str, status: str, progress: int | None = None):
        # 纯状态更新：终态通知由调用方经 activity_events 显式声明（0.4 §6.4 E2）；
        # 崩溃恢复为 SQL 直写不走本方法，其通知由 main.py 启动扫描显式补发（0.4 §2.4）。
        updates = ["status = ?", "updated_at = ?"]
        params = [status, self._now()]
        if progress is not None:
            updates.insert(1, "progress = ?")
            params.insert(1, progress)
        params.append(report_id)
        self.conn.execute(
            f"UPDATE reports SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()

    def delete_report(self, report_id: str):
        self.conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
        self.conn.commit()

    def update_report_error(self, report_id: str, error_message: str):
        """保存报告生成的错误信息。"""
        self.conn.execute(
            "UPDATE reports SET error_message = ?, updated_at = ? WHERE id = ?",
            (error_message, self._now(), report_id),
        )
        self.conn.commit()

    def set_report_pause_requested(self, report_id: str, requested: bool):
        """报告级暂停请求标志（2.5 §3.1 修订）：pause 端点写入，Runner 在章节边界
        消费；消费 / 恢复 / 完成 / 失败后由调用方清除，防遗留标志影响后续生成。"""
        self.conn.execute(
            "UPDATE reports SET pause_requested = ?, updated_at = ? WHERE id = ?",
            (1 if requested else 0, self._now(), report_id),
        )
        self.conn.commit()

    def get_report_pause_requested(self, report_id: str) -> bool:
        """读取报告级暂停请求标志（Runner 章节边界检查用）。"""
        row = self.conn.execute(
            "SELECT pause_requested FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        return bool(row and row["pause_requested"])

    def count_active_reports(self) -> int:
        """统计正在生成的报告数量（用于并发限制）。"""
        cursor = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM reports WHERE status = 'generating'"
        )
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def recover_stale_generating_reports(self, timeout_minutes: int = 0) -> list[str]:
        """崩溃恢复（§3.8.3）：扫描遗留的 generating 报告，标记为 error。

        应用启动时调用。runner 跑在本进程 BackgroundTasks 内——进程重启后
        runner 必然全部不存在，任何 status='generating' 均为孤儿，故默认
        timeout=0 不设时间阈值。曾用 5 分钟阈值，反而留下「假生成中」窗口：
        进度永不推进、暂停请求无人消费，须等下次重启满 5 分钟才被救回。
        标记 error 并保留 checkpoint，等用户在详情页点「恢复生成」从
        checkpoint 续跑（启动时不自动续跑）；timeout>0 仅测试/特殊场景使用。

        Returns:
            被标记为 error 的 report_id 列表。
        """
        cursor = self.conn.execute(
            "SELECT id, updated_at FROM reports WHERE status = 'generating'"
        )
        rows = cursor.fetchall()
        now = datetime.now()
        stale_ids: list[str] = []
        for row in rows:
            try:
                last = datetime.fromisoformat(row["updated_at"])
                age_seconds = (now - last).total_seconds()
            except (ValueError, TypeError):
                # 时间戳无法解析 → 保守判定为陈旧，避免永久卡在 generating
                age_seconds = timeout_minutes * 60 + 1
            # timeout<=0 = 不设时间阈值（全部孤儿）；显式特判而非 >0 比较——
            # 同时钟 tick 内的时间戳 age_seconds 精确为 0，比较会漏标
            if timeout_minutes <= 0 or age_seconds > timeout_minutes * 60:
                stale_ids.append(row["id"])
        for rid in stale_ids:
            self.conn.execute(
                "UPDATE reports SET status = 'error', error_message = ?, updated_at = ? WHERE id = ?",
                ("程序异常中断，请重新生成", self._now(), rid),
            )
        if stale_ids:
            self.conn.commit()
        return stale_ids

    # ── Report Logs ──

    def create_report_log(
        self,
        report_id: str,
        message: str,
        level: str = "info",
        section_id: str | None = None,
    ) -> dict:
        """创建一条报告生成日志。"""
        now = self._now()
        log_id = self._uuid()
        self.conn.execute(
            """INSERT INTO report_logs (id, report_id, level, message, section_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (log_id, report_id, level, message, section_id, now),
        )
        self.conn.commit()
        return {
            "id": log_id,
            "report_id": report_id,
            "level": level,
            "message": message,
            "section_id": section_id,
            "created_at": now,
        }

    def list_report_logs(self, report_id: str, limit: int = 100) -> list[dict]:
        """获取报告的生成日志（时间正序 = 时间轴视图，取最新 limit 条）。

        详情页日志面板直接渲染（旧上新下，生成中配合自动滚底看最新）：
        SQL 倒序截取最新 N 条（rowid = 插入序作同 created_at 的稳定次级键），
        Python 侧反转为正序返回。
        """
        cursor = self.conn.execute(
            """SELECT * FROM report_logs WHERE report_id = ?
               ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (report_id, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows.reverse()
        return rows

    # ── PPTs (2.3 §5.1)：交付物登记簿 ──

    def create_ppt(
        self,
        project_id: str,
        task_id: str,
        title: str,
        kind: str = "deck",
        source_report_id: str | None = None,
        source_images: list[str] | None = None,
        style_ref: str | None = None,
        workspace_path: str | None = None,
        template_kind: str | None = None,
        template_id: str | None = None,
        enhance_source_path: str | None = None,
    ) -> dict:
        now = self._now()
        ppt_id = self._uuid()
        self.conn.execute(
            """INSERT INTO ppts (id, project_id, task_id, title, kind, status,
               source_report_id, source_images_json, style_ref, workspace_path,
               template_kind, template_id, enhance_source_path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ppt_id, project_id, task_id, title, kind, source_report_id,
             json.dumps(source_images or [], ensure_ascii=False),
             style_ref, workspace_path, template_kind, template_id,
             enhance_source_path, now, now),
        )
        self.conn.commit()
        return self.get_ppt(ppt_id)  # type: ignore[return-value]

    def list_ppts(self, project_id: str, limit: int = 0, offset: int = 0) -> list[dict]:
        """limit=0 → 全量（默认，向后兼容）；limit>0 → 至多 limit 行
        （路由层传 limit+1 探测 has_more，对齐 list_tasks 先例）。"""
        sql = "SELECT * FROM ppts WHERE project_id = ? ORDER BY created_at DESC"
        params: list = [project_id]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_ppt(self, ppt_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM ppts WHERE id = ?", (ppt_id,)).fetchone()
        if not row:
            return None
        ppt = dict(row)
        try:
            ppt["source_images"] = json.loads(ppt.pop("source_images_json") or "[]")
        except (ValueError, TypeError):
            ppt["source_images"] = []
        return ppt

    def get_ppt_by_task(self, task_id: str) -> dict | None:
        row = self.conn.execute("SELECT id FROM ppts WHERE task_id = ?", (task_id,)).fetchone()
        return self.get_ppt(row["id"]) if row else None

    def update_ppt_progress(
        self, ppt_id: str, *, stage: str | None = None,
        page_count: int | None = None, page_done: int | None = None,
    ) -> None:
        """推进阶段/页级进度（progress 由 page_done/page_count 派生，
        authoring 之外的阶段由调用方传 stage 边界；非法页比静默夹取）。"""
        updates = ["updated_at = ?"]
        params: list = [self._now()]
        if stage is not None:
            updates.append("stage = ?")
            params.append(stage)
        if page_count is not None:
            updates.append("page_count = ?")
            params.append(page_count)
        if page_done is not None:
            updates.append("page_done = ?")
            params.append(page_done)
        row = self.conn.execute(
            "SELECT page_count, page_done FROM ppts WHERE id = ?", (ppt_id,)
        ).fetchone()
        if row:
            total = page_count if page_count is not None else row["page_count"]
            done = page_done if page_done is not None else row["page_done"]
            if total and done is not None:
                progress = max(0.0, min(1.0, done / total))
                updates.append("progress = ?")
                params.append(progress)
        params.append(ppt_id)
        self.conn.execute(
            f"UPDATE ppts SET {', '.join(updates)} WHERE id = ?", params
        )
        self.conn.commit()

    def update_ppt_status(
        self, ppt_id: str, status: str, *,
        error: str | None = None, export_path: str | None = None,
    ) -> None:
        updates = ["status = ?", "updated_at = ?"]
        params: list = [status, self._now()]
        if error is not None:
            updates.append("error = ?")
            params.append(error[:2000])
        if export_path is not None:
            updates.append("export_path = ?")
            params.append(export_path)
        params.append(ppt_id)
        self.conn.execute(
            f"UPDATE ppts SET {', '.join(updates)} WHERE id = ?", params
        )
        self.conn.commit()

    def delete_ppt(self, ppt_id: str) -> dict | None:
        """删除登记行，返回删除前的行（调用方负责级联任务/工作区清理）。"""
        ppt = self.get_ppt(ppt_id)
        if not ppt:
            return None
        self.conn.execute("DELETE FROM ppts WHERE id = ?", (ppt_id,))
        self.conn.commit()
        return ppt

    def list_active_ppts(self) -> list[dict]:
        """活跃 PPT（活动注册表 source 与启动崩溃恢复扫描共用）。"""
        cursor = self.conn.execute(
            "SELECT * FROM ppts WHERE status IN ('running', 'awaiting_input') "
            "ORDER BY created_at DESC"
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_template_jobs(self, limit: int = 20, offset: int = 0) -> tuple[list[dict], bool]:
        """模板创建任务（2.3 §4.2）：活跃段全量（进度轮询/门禁不可截断，天然小）
        + 终态段至多 limit 行；返回 (jobs, has_more)——has_more 只看终态段
        （limit>0 时终态段多取 1 行探测，对齐 list_tasks 先例；limit<=0 → 终态段全量）。
        """
        active = [dict(r) for r in self.conn.execute(
            "SELECT * FROM ppts WHERE kind = 'template' "
            "AND status IN ('running', 'awaiting_input') "
            "ORDER BY created_at DESC"
        ).fetchall()]
        probe = limit + 1 if limit > 0 else 0
        terminal_sql = (
            "SELECT * FROM ppts WHERE kind = 'template' "
            "AND status NOT IN ('running', 'awaiting_input') "
            "ORDER BY created_at DESC"
        )
        terminal_params: list = []
        if probe > 0:
            terminal_sql += " LIMIT ? OFFSET ?"
            terminal_params.extend([probe, offset])
        terminal = [dict(r) for r in self.conn.execute(
            terminal_sql, terminal_params).fetchall()]
        has_more = probe > 0 and len(terminal) > limit
        if has_more:
            terminal = terminal[:limit]
        return active + terminal, has_more

    def list_ppts_by_template(self, template_kind: str, template_id: str) -> list[dict]:
        """指定模板的创建任务登记行（6b 删除连带清理的数据源）。"""
        cursor = self.conn.execute(
            "SELECT * FROM ppts WHERE kind = 'template' "
            "AND template_kind = ? AND template_id = ? "
            "ORDER BY created_at DESC",
            (template_kind, template_id),
        )
        return [dict(row) for row in cursor.fetchall()]

    # ── Notifications (Activity Hub, 0.4 §3.2) ──

    def add_notification(
        self,
        entity_type: str,
        entity_id: str,
        project_id: str | None,
        kind: str,
        entity_title: str,
        detail: str | None = None,
        container: str = "project",
    ) -> int | None:
        """写入一条终态通知。未读查重：同 (entity_type, entity_id, kind) 存在未读记录则跳过。

        container: 挂载容器 project / baselib（基础库活动导航分流，0.4 §3.6），
            由调用方 JOIN projects.kind 提供；task / model_download 等无项目实体保持默认。

        Returns:
            新通知 id；被查重跳过时返回 None。
        """
        dup = self.conn.execute(
            "SELECT id FROM notifications WHERE entity_type = ? AND entity_id = ? "
            "AND kind = ? AND read_at IS NULL",
            (entity_type, entity_id, kind),
        ).fetchone()
        if dup:
            return None
        cursor = self.conn.execute(
            """INSERT INTO notifications
               (entity_type, entity_id, project_id, container, kind, entity_title, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_type, entity_id, project_id, container, kind, entity_title, detail, self._now()),
        )
        self.conn.commit()
        return cursor.lastrowid

    def add_notification_daily(
        self,
        entity_type: str,
        entity_id: str,
        project_id: str | None,
        kind: str,
        entity_title: str,
        detail: str | None = None,
        container: str = "project",
    ) -> int | None:
        """写入一条按天查重通知（提醒语义，2.11 D9）：同 (entity_type, entity_id, kind)
        存在未读记录、或本地日期当天（date(created_at)）已有记录则跳过。

        与 add_notification 的差异：终态语义「未读期间只一条」对提醒不够——
        用户已读昨天的逾期提醒后今天仍逾期应再提醒（D1 跨天重发）；
        未读优先于按天（未读期间不重复轰炸）。现有 11 个 emit 调用方语义不动（D9）。

        Returns:
            新通知 id；被查重跳过时返回 None。
        """
        dup = self.conn.execute(
            "SELECT id FROM notifications WHERE entity_type = ? AND entity_id = ? "
            "AND kind = ? AND (read_at IS NULL OR date(created_at) = ?)",
            (entity_type, entity_id, kind, date.today().isoformat()),
        ).fetchone()
        if dup:
            return None
        cursor = self.conn.execute(
            """INSERT INTO notifications
               (entity_type, entity_id, project_id, container, kind, entity_title, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_type, entity_id, project_id, container, kind, entity_title, detail, self._now()),
        )
        self.conn.commit()
        return cursor.lastrowid

    def list_unread_notifications(self) -> list[dict]:
        """未读通知（按时间正序，逐条弹出）。"""
        cursor = self.conn.execute(
            "SELECT * FROM notifications WHERE read_at IS NULL ORDER BY created_at ASC, id ASC"
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_recent_notifications(self, limit: int = 10) -> list[dict]:
        """最近通知（含已读，首页「最近动态」用）。"""
        cursor = self.conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_notifications(self, offset: int = 0, limit: int = 50) -> tuple[list[dict], int]:
        """分页全量通知（含已读，通知中心历史面，2.11 §3.4）。返回 (items, total)。"""
        total = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM notifications"
        ).fetchone()["cnt"]
        cursor = self.conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()], total

    def mark_all_notifications_read(self) -> int:
        """全部标记已读（通知中心一键操作，2.11 §3.4）。返回标记条数。"""
        cursor = self.conn.execute(
            "UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (self._now(),)
        )
        self.conn.commit()
        return cursor.rowcount

    def mark_notifications_read(self, ids: list[int]) -> int:
        """批量标记已读（弹出即已读，0.4 §5.5）。"""
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = self.conn.execute(
            f"UPDATE notifications SET read_at = ? WHERE id IN ({placeholders}) AND read_at IS NULL",
            [self._now()] + list(ids),
        )
        self.conn.commit()
        return cursor.rowcount

    def delete_notification(self, notification_id: int) -> int:
        """单条删除通知（通知中心行内删除，2.11 §3.4）。返回删除条数（幂等：不存在为 0）。"""
        cursor = self.conn.execute(
            "DELETE FROM notifications WHERE id = ?", (notification_id,)
        )
        self.conn.commit()
        return cursor.rowcount

    def cleanup_old_notifications(self, keep: int = 100) -> int:
        """保留最近 keep 条通知，清理更早记录（启动时调用，防无限增长）。"""
        cursor = self.conn.execute(
            """DELETE FROM notifications WHERE id NOT IN (
                   SELECT id FROM notifications ORDER BY id DESC LIMIT ?
               )""",
            (keep,),
        )
        self.conn.commit()
        return cursor.rowcount

    # ── Model Downloads (Activity Hub, 0.4 §3.3) ──

    def mark_model_download_state(self, name: str, status: str, error: str | None = None):
        """记录模型下载状态（start / 终态）。进度不落库（内存态由 check_status 提供）。"""
        now = self._now()
        self.conn.execute(
            """INSERT INTO model_downloads (name, status, error, started_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET status = excluded.status,
                   error = excluded.error, updated_at = excluded.updated_at""",
            (name, status, error, now, now),
        )
        self.conn.commit()

    def recover_stale_model_downloads(self) -> list[dict]:
        """崩溃恢复（0.4 §2.4 第 6 步）：遗留 downloading 记录 → 标 failed 并返回。

        只负责状态标记；failed 通知由调用方（main.py）写入。
        不自动重新下载——用户重新点击即可，HF/ModelScope 断点续传。
        """
        cursor = self.conn.execute(
            "SELECT name FROM model_downloads WHERE status = 'downloading'"
        )
        stale = [dict(row) for row in cursor.fetchall()]
        for row in stale:
            self.conn.execute(
                "UPDATE model_downloads SET status = 'failed', "
                "error = '程序异常中断', updated_at = ? WHERE name = ?",
                (self._now(), row["name"]),
            )
        if stale:
            self.conn.commit()
        return stale

    # ── Report Sections ──

    def create_report_section(
        self,
        report_id: str,
        section_id: str,
        title: str,
        compose_mode: str = "standard",
    ) -> dict:
        now = self._now()
        # UPSERT：同 (report_id, section_id) 重复落库幂等（resume 重复事件 /
        # restart 未清表的纵深防御），冲突时重置为 pending 并刷新标题
        self.conn.execute(
            """INSERT INTO report_sections (id, report_id, section_id, title, status, word_count, compose_mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)
               ON CONFLICT(report_id, section_id) DO UPDATE SET
                   title = excluded.title,
                   status = 'pending',
                   word_count = 0,
                   compose_mode = excluded.compose_mode,
                   updated_at = excluded.updated_at""",
            (self._uuid(), report_id, section_id, title, compose_mode, now, now),
        )
        row = self.conn.execute(
            "SELECT id, created_at FROM report_sections WHERE report_id = ? AND section_id = ?",
            (report_id, section_id),
        ).fetchone()
        self.conn.commit()
        return {
            "id": row["id"],
            "report_id": report_id,
            "section_id": section_id,
            "title": title,
            "content": None,
            "status": "pending",
            "word_count": 0,
            "compose_mode": compose_mode,
            "created_at": row["created_at"],
            "updated_at": now,
        }

    def delete_report_sections(self, report_id: str):
        """清空报告的章节行（restart 全量重跑前重置，防旧行残留）。"""
        self.conn.execute("DELETE FROM report_sections WHERE report_id = ?", (report_id,))
        self.conn.commit()

    def update_section_content(self, section_pk: str, content: str, status: str = "completed", word_count: int | None = None):
        if word_count is not None:
            self.conn.execute(
                "UPDATE report_sections SET content = ?, word_count = ?, status = ?, updated_at = ? WHERE id = ?",
                (content, word_count, status, self._now(), section_pk),
            )
        else:
            self.conn.execute(
                "UPDATE report_sections SET content = ?, status = ?, updated_at = ? WHERE id = ?",
                (content, status, self._now(), section_pk),
            )
        self.conn.commit()

    # ── Templates ──

    def save_template(self, template_id: str, name: str, template_json: str, is_custom: bool = False, version: str | None = None) -> dict:
        now = self._now()
        self.conn.execute(
            """INSERT OR REPLACE INTO templates (id, name, template_json, is_custom, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (template_id, name, template_json, 1 if is_custom else 0, version, now, now),
        )
        self.conn.commit()
        return {
            "id": template_id,
            "name": name,
            "is_custom": is_custom,
            "version": version,
            "created_at": now,
            "updated_at": now,
        }

    def list_templates(self, is_custom: bool | None = None) -> list[dict]:
        if is_custom is not None:
            cursor = self.conn.execute(
                "SELECT * FROM templates WHERE is_custom = ? ORDER BY created_at ASC",
                (1 if is_custom else 0,),
            )
        else:
            cursor = self.conn.execute("SELECT * FROM templates ORDER BY created_at ASC")
        return [dict(row) for row in cursor.fetchall()]

    def get_template(self, template_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_template(self, template_id: str):
        self.conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        self.conn.commit()

    # ── Base Library CRUD ──

    # Mapping: project type → preset base lib IDs for auto-linking
    _AUTO_LINK_MAP = {
        "recruitment": ["bl-recruitment"],
        "investment": ["bl-investment"],
    }

    # Default Wiki purpose per project type (§4.6 of 2.6wiki-design.md),
    # pre-filled on create_project; user-editable from the Wiki home page.
    DEFAULT_PURPOSES = {
        "recruitment": (
            "构建招商全周期知识库：前期完成企业画像与政策匹配评估，中期推进项目"
            "落地实施，投产后持续监测企业经营、政策兑现与履约风险，为招商决策与"
            "企业服务提供依据。"
        ),
        "investment": (
            "构建投资全周期知识库：覆盖尽职调查、投资决策、投后监控到退出分析，"
            "持续追踪企业经营、财务健康、条款触发与风险预警。"
        ),
        "general": (
            "构建项目知识库：沉淀项目资料的关键信息、结论与风险，支持检索与"
            "持续更新。"
        ),
    }

    PRESET_BASE_LIBS = [
        {
            "id": "bl-recruitment",
            "name": "招商基础库",
            "description": "政策、园区、产业扶持等基础资料",
            "kind": "base_lib",
            "lib_type": "preset_recruitment",
        },
        {
            "id": "bl-investment",
            "name": "投资基础库",
            "description": "估值基准、法规、行业数据等",
            "kind": "base_lib",
            "lib_type": "preset_investment",
        },
    ]

    def ensure_preset_base_libs(self):
        """Create preset base libraries if they don't exist yet (idempotent)."""
        for lib in self.PRESET_BASE_LIBS:
            cursor = self.conn.execute(
                "SELECT id FROM projects WHERE id = ?", (lib["id"],)
            )
            if cursor.fetchone() is None:
                now = self._now()
                self.conn.execute(
                    "INSERT INTO projects (id, name, description, kind, lib_type, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'base_lib', ?, ?, ?)",
                    (lib["id"], lib["name"], lib["description"], lib["lib_type"], now, now),
                )
        self.conn.commit()

    def list_base_libs(self, limit: int = 0, offset: int = 0) -> list[dict]:
        """limit=0 → 全量（默认，向后兼容）；limit>0 → 至多 limit 行
        （路由层传 limit+1 探测 has_more）。"""
        sql = """SELECT p.*, COUNT(f.id) as file_count
               FROM projects p
               LEFT JOIN files f ON p.id = f.project_id
               WHERE p.kind = 'base_lib'
               GROUP BY p.id
               ORDER BY p.created_at ASC"""
        params: list = []
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_base_lib(self, lib_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM projects WHERE id = ? AND kind = 'base_lib'", (lib_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    # ── Project-BaseLib Association ──

    def link_project_to_base_lib(self, project_id: str, base_lib_id: str, auto: bool = False):
        """Link a project to a base library. Idempotent."""
        self.conn.execute(
            "INSERT OR IGNORE INTO project_base_libs (project_id, base_lib_id, auto_linked, created_at) "
            "VALUES (?, ?, ?, ?)",
            (project_id, base_lib_id, 1 if auto else 0, self._now()),
        )
        self.conn.commit()

    def unlink_project_from_base_lib(self, project_id: str, base_lib_id: str):
        self.conn.execute(
            "DELETE FROM project_base_libs WHERE project_id = ? AND base_lib_id = ?",
            (project_id, base_lib_id),
        )
        self.conn.commit()

    def get_project_base_libs(self, project_id: str) -> list[dict]:
        """Get base libraries linked to a project."""
        cursor = self.conn.execute(
            """SELECT p.id, p.name, p.description, p.lib_type, pbl.auto_linked, pbl.created_at as linked_at
               FROM project_base_libs pbl
               JOIN projects p ON p.id = pbl.base_lib_id
               WHERE pbl.project_id = ? AND p.kind = 'base_lib'
               ORDER BY p.created_at ASC""",
            (project_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_auto_linked_base_libs(self, project_type: str) -> list[str]:
        """Return base lib IDs that should be auto-linked for a given project type."""
        return self._AUTO_LINK_MAP.get(project_type, [])

    # ── Wiki: Schemas ──

    def seed_wiki_schemas(self, schemas: list[dict]):
        """Seed wiki_schemas table (idempotent: INSERT OR REPLACE by id).

        Also removes retired schema rows (RETIRED_SCHEMA_IDS from wiki_seed,
        §2.2 Schema 演化) so stale compile hints stop being injected into
        prompts. User-defined custom schemas (CRUD API) are unaffected.
        """
        from db.wiki_seed import RETIRED_SCHEMA_IDS
        for schema_id in RETIRED_SCHEMA_IDS:
            self.conn.execute("DELETE FROM wiki_schemas WHERE id = ?", (schema_id,))
        for s in schemas:
            self.conn.execute(
                """INSERT OR REPLACE INTO wiki_schemas
                   (id, project_type, page_key, title, compile_hint, base_lib_search_hints, position, is_ledger)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (s["id"], s["project_type"], s["page_key"], s["title"],
                 s.get("compile_hint", ""),
                 json.dumps(s.get("base_lib_search_hints", []), ensure_ascii=False),
                 s.get("position", 0),
                 s.get("is_ledger", 0)),
            )
        self.conn.commit()

    def get_wiki_schemas(self, project_type: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM wiki_schemas WHERE project_type = ? ORDER BY position ASC",
            (project_type,),
        )
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            if d.get("base_lib_search_hints"):
                try:
                    d["base_lib_search_hints"] = json.loads(d["base_lib_search_hints"])
                except (json.JSONDecodeError, TypeError):
                    d["base_lib_search_hints"] = []
            rows.append(d)
        return rows

    def create_wiki_schema(
        self,
        project_type: str,
        page_key: str,
        title: str,
        compile_hint: str = "",
        base_lib_search_hints: list[str] | None = None,
        position: int | None = None,
        is_ledger: bool = False,
    ) -> dict:
        """Create a new wiki schema entry."""
        schema_id = f"wiki_schema_{project_type}_{page_key}"
        # Auto-assign position if not provided
        if position is None:
            cursor = self.conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 as next_pos FROM wiki_schemas WHERE project_type = ?",
                (project_type,),
            )
            position = cursor.fetchone()["next_pos"]

        hints_json = json.dumps(base_lib_search_hints or [], ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO wiki_schemas (id, project_type, page_key, title, compile_hint, base_lib_search_hints, position, is_ledger)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (schema_id, project_type, page_key, title, compile_hint, hints_json, position, 1 if is_ledger else 0),
        )
        self.conn.commit()
        return {"id": schema_id, "project_type": project_type, "page_key": page_key, "title": title}

    def update_wiki_schema(
        self,
        schema_id: str,
        title: str | None = None,
        compile_hint: str | None = None,
        base_lib_search_hints: list[str] | None = None,
        position: int | None = None,
        is_ledger: bool | None = None,
    ) -> dict | None:
        """Update an existing wiki schema entry."""
        updates = []
        params: list = []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if compile_hint is not None:
            updates.append("compile_hint = ?")
            params.append(compile_hint)
        if base_lib_search_hints is not None:
            updates.append("base_lib_search_hints = ?")
            params.append(json.dumps(base_lib_search_hints, ensure_ascii=False))
        if position is not None:
            updates.append("position = ?")
            params.append(position)
        if is_ledger is not None:
            updates.append("is_ledger = ?")
            params.append(1 if is_ledger else 0)
        if not updates:
            return self._get_schema_by_id(schema_id)

        params.append(schema_id)
        self.conn.execute(
            f"UPDATE wiki_schemas SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        return self._get_schema_by_id(schema_id)

    def delete_wiki_schema(self, schema_id: str) -> bool:
        """Delete a wiki schema entry."""
        cursor = self.conn.execute("DELETE FROM wiki_schemas WHERE id = ?", (schema_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def get_wiki_schema(self, project_type: str, page_key: str) -> dict | None:
        """Get a single wiki schema by (project_type, page_key)."""
        return self._get_schema_by_id(f"wiki_schema_{project_type}_{page_key}")

    def _get_schema_by_id(self, schema_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM wiki_schemas WHERE id = ?", (schema_id,))
        row = cursor.fetchone()
        if row:
            d = dict(row)
            if d.get("base_lib_search_hints"):
                try:
                    d["base_lib_search_hints"] = json.loads(d["base_lib_search_hints"])
                except (json.JSONDecodeError, TypeError):
                    d["base_lib_search_hints"] = []
            return d
        return None

    # ── Forms: Templates & Fills (2.4 §3) ──

    def seed_form_templates(self, templates: list[dict]) -> list[str]:
        """Seed 预置填表模板（幂等：按 id 已存在则跳过，2.4 §3.1）。

        与 wiki/playbook 的 INSERT OR REPLACE 不同：模板分析产物
        （schema/mapping/status）是耗时 LLM 结果，重种子不得覆盖——
        只在不存在时插入。带出厂预解析产物（template_schema，随包
        分发）的条目直接落 status=success，新用户零 LLM 调用开箱即用；
        无产物条目落 pending 由启动拾取分析。已存在的预置记录仅对齐
        file_path（预置文件随版本移动/改名时迁移路径，不重分析），
        返回新插入的 id。
        """
        seeded: list[str] = []
        migrated = False
        for t in templates:
            row = self.conn.execute(
                "SELECT id, file_path FROM form_templates WHERE id = ?", (t["id"],)
            ).fetchone()
            if row:
                if row["file_path"] != t["file_path"]:
                    self.conn.execute(
                        "UPDATE form_templates SET file_path = ?, updated_at = ? WHERE id = ?",
                        (t["file_path"], self._now(), t["id"]),
                    )
                    migrated = True
                continue
            now = self._now()
            has_schema = bool(t.get("template_schema"))
            self.conn.execute(
                """INSERT INTO form_templates
                   (id, name, source, file_path, template_schema, llm_mapping,
                    field_count, file_hash, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (t["id"], t["name"], t["source"], t["file_path"],
                 t.get("template_schema"), t.get("llm_mapping"),
                 t.get("field_count"), t.get("file_hash"),
                 "success" if has_schema else "pending", now, now),
            )
            seeded.append(t["id"])
        if seeded or migrated:
            self.conn.commit()
        return seeded

    def create_form_template(self, template_id: str, name: str,
                             source: str, file_path: str) -> dict:
        """上传模板落库（status=pending，由后台分析任务拾取）。"""
        now = self._now()
        self.conn.execute(
            """INSERT INTO form_templates
               (id, name, source, file_path, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (template_id, name, source, file_path, now, now),
        )
        self.conn.commit()
        return {"id": template_id, "name": name, "status": "pending"}

    def list_form_templates(
        self, q: str | None = None, status: str | None = None,
        limit: int = 0, offset: int = 0,
    ) -> list[dict]:
        """模板列表（列表页轻量视图：不含 schema/mapping 大列；含
        file_path/file_hash 供路由层动态比对 file_changed，两者不出路由层）。
        q 模糊匹配 name、status 精确过滤（服务端筛选下沉，对齐 knowledge files 范式）；
        limit=0 → 全量（选择器默认），limit>0 → 至多 limit 行
        （路由层传 limit+1 探测 has_more）。"""
        sql = (
            """SELECT id, name, source, status, error, field_count, user_hint,
                      file_path, file_hash, created_at, updated_at
               FROM form_templates"""
        )
        where, params = [], []
        if q:
            where.append("name LIKE ?")
            params.append(f"%{q}%")
        if status:
            where.append("status = ?")
            params.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC"
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def count_form_templates(self, q: str | None = None,
                             status: str | None = None) -> int:
        """模板计数（与 list_form_templates 同口径：q/status 筛选后计数）。"""
        sql = "SELECT COUNT(*) AS cnt FROM form_templates"
        where, params = [], []
        if q:
            where.append("name LIKE ?")
            params.append(f"%{q}%")
        if status:
            where.append("status = ?")
            params.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.conn.execute(sql, params).fetchone()["cnt"]

    def get_form_template(self, template_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM form_templates WHERE id = ?", (template_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_form_template_status(self, template_id: str, status: str,
                                    error: str | None = None):
        """状态流转（仅后台分析任务调用）。error 随 failed/invalid 落库，成功清空。"""
        self.conn.execute(
            "UPDATE form_templates SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, self._now(), template_id),
        )
        self.conn.commit()

    def update_form_template_schema(self, template_id: str, schema: dict,
                                    llm_mapping: dict, field_count: int,
                                    file_hash: str | None = None):
        """分析成功：schema/mapping/file_hash 落盘 + status=success。

        file_hash 与 schema 同源（同一份文件内容），列表/详情据此动态比对
        file_changed；NULL（存量/历史失败）不参与比对。
        """
        self.conn.execute(
            """UPDATE form_templates SET template_schema = ?, llm_mapping = ?,
               field_count = ?, file_hash = ?, status = 'success', error = NULL,
               updated_at = ? WHERE id = ?""",
            (json.dumps(schema, ensure_ascii=False),
             json.dumps(llm_mapping, ensure_ascii=False),
             field_count, file_hash, self._now(), template_id),
        )
        self.conn.commit()

    def update_form_template_hint(self, template_id: str, user_hint: str):
        """重解析：写入用户补充说明并回到 pending（由后台任务拾取，2.4 §2.3）。"""
        self.conn.execute(
            "UPDATE form_templates SET user_hint = ?, status = 'pending', "
            "error = NULL, updated_at = ? WHERE id = ?",
            (user_hint, self._now(), template_id),
        )
        self.conn.commit()

    def delete_form_template(self, template_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM form_templates WHERE id = ?", (template_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_form_template_ids_by_status(self, status: str) -> list[str]:
        cursor = self.conn.execute(
            "SELECT id FROM form_templates WHERE status = ?", (status,)
        )
        return [row["id"] for row in cursor.fetchall()]

    def recover_stale_form_templates(self) -> list[str]:
        """崩溃恢复（2.4 §2.4）：analyzing 残留 → pending，由启动拾取重新分析。

        分析任务跑在本进程内，重启后必然不存在，任何 analyzing 均为孤儿
        （同 recover_stale_generating_reports 不设时间阈值的裁决）。
        返回被重置的 id（启动拾取自动重新拉起，分析产物幂等覆盖）。
        """
        cursor = self.conn.execute(
            "SELECT id FROM form_templates WHERE status = 'analyzing'"
        )
        ids = [row["id"] for row in cursor.fetchall()]
        if ids:
            self.conn.execute(
                "UPDATE form_templates SET status = 'pending', updated_at = ? "
                "WHERE status = 'analyzing'",
                (self._now(),),
            )
            self.conn.commit()
        return ids

    def create_form_fill(self, project_id: str, template_id: str, title: str) -> dict:
        now = self._now()
        fill_id = self._uuid()
        self.conn.execute(
            """INSERT INTO form_fills
               (id, project_id, template_id, title, status, progress, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)""",
            (fill_id, project_id, template_id, title, now, now),
        )
        self.conn.commit()
        return {
            "id": fill_id,
            "project_id": project_id,
            "template_id": template_id,
            "title": title,
            "status": "pending",
            "progress": 0,
            "created_at": now,
            "updated_at": now,
        }

    def list_form_fills(self, project_id: str, limit: int = 0,
                        offset: int = 0) -> list[dict]:
        """实例列表（列表页轻量视图：不含 report/filled_values 大列，附模板名）。

        LEFT JOIN：模板随时可删（2.4 不变量 11），悬空实例可见，template_name 为 NULL。
        limit=0 → 全量（默认，向后兼容）；limit>0 → 至多 limit 行
        （路由层传 limit+1 探测 has_more）。
        """
        sql = (
            """SELECT f.id, f.project_id, f.template_id, f.title, f.output_path,
                      f.status, f.progress, f.error, f.created_at, f.updated_at,
                      t.name AS template_name
               FROM form_fills f LEFT JOIN form_templates t ON t.id = f.template_id
               WHERE f.project_id = ? ORDER BY f.created_at DESC"""
        )
        params: list = [project_id]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_form_fill(self, fill_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM form_fills WHERE id = ?", (fill_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_form_fill_status(self, fill_id: str, status: str,
                                progress: int | None = None,
                                error: str | None = None):
        """状态/进度流转（仅后台填表任务调用）。

        progress 单调递增（SQL MAX 保护，不因批次失败回退，2.4 §5.4 不变量 4）。
        """
        if progress is not None:
            self.conn.execute(
                "UPDATE form_fills SET status = ?, progress = MAX(progress, ?), "
                "error = ?, updated_at = ? WHERE id = ?",
                (status, progress, error, self._now(), fill_id),
            )
        else:
            self.conn.execute(
                "UPDATE form_fills SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, self._now(), fill_id),
            )
        self.conn.commit()

    def update_form_fill_result(self, fill_id: str, report: list,
                                filled_values: dict, output_path: str):
        """填表完成：产物路径 + 值集/报告落库 + success/progress=100。"""
        self.conn.execute(
            """UPDATE form_fills SET output_path = ?, report = ?, filled_values = ?,
               status = 'success', progress = 100, error = NULL, updated_at = ?
               WHERE id = ?""",
            (output_path, json.dumps(report, ensure_ascii=False),
             json.dumps(filled_values, ensure_ascii=False),
             self._now(), fill_id),
        )
        self.conn.commit()

    def update_form_fill_values(self, fill_id: str, filled_values: dict,
                                report: list, output_path: str):
        """人工修订（2.4 §4.2 路径 C）：值集/报告/产物更新，status 保持 success。"""
        self.conn.execute(
            """UPDATE form_fills SET output_path = ?, report = ?, filled_values = ?,
               updated_at = ? WHERE id = ?""",
            (output_path, json.dumps(report, ensure_ascii=False),
             json.dumps(filled_values, ensure_ascii=False),
             self._now(), fill_id),
        )
        self.conn.commit()

    def count_active_form_fills(self) -> int:
        """排队/填写中的实例数（并发限制，2.4 §4.2）。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM form_fills WHERE status IN ('pending', 'filling')"
        ).fetchone()
        return row["cnt"] if row else 0

    def list_form_fill_ids_by_status(self, status: str) -> list[str]:
        cursor = self.conn.execute(
            "SELECT id FROM form_fills WHERE status = ?", (status,)
        )
        return [row["id"] for row in cursor.fetchall()]

    def recover_stale_form_fills(self) -> list[str]:
        """崩溃恢复（2.4 §2.4）：filling 残留 → pending，由启动拾取自动重跑
        （对齐 task_queue 中断恢复模式）。返回被重置的 id。"""
        cursor = self.conn.execute(
            "SELECT id FROM form_fills WHERE status = 'filling'"
        )
        ids = [row["id"] for row in cursor.fetchall()]
        if ids:
            self.conn.execute(
                "UPDATE form_fills SET status = 'pending', updated_at = ? "
                "WHERE status = 'filling'",
                (self._now(),),
            )
            self.conn.commit()
        return ids

    def delete_form_fill(self, fill_id: str):
        self.conn.execute("DELETE FROM form_fills WHERE id = ?", (fill_id,))
        self.conn.commit()

    # ── Affairs: Playbook templates (3.0 §4.1, phase 2) ──

    def seed_playbook_templates(self, templates: list[dict]) -> None:
        """Seed playbook_templates (idempotent: INSERT OR REPLACE by id).

        对齐 seed_wiki_schemas 模式(§4.1):应用更新可迭代行业最佳实践,
        运行期只读;已生成实例与模板解耦,重种子不影响存量任务。
        """
        for t in templates:
            self.conn.execute(
                """INSERT OR REPLACE INTO playbook_templates
                   (id, project_type, phase, task_type, title, description,
                    due_offset_days, due_date_fixed, recurrence_rule, auto_complete, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    t["id"], t["project_type"], t["phase"], t["task_type"], t["title"],
                    t.get("description", ""), t.get("due_offset_days"),
                    t.get("due_date_fixed"),
                    json.dumps(t["recurrence_rule"], ensure_ascii=False) if t.get("recurrence_rule") else None,
                    json.dumps(t["auto_complete"], ensure_ascii=False) if t.get("auto_complete") else None,
                    t.get("position", 0),
                ),
            )
        self.conn.commit()

    def list_playbook_templates(self, project_type: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM playbook_templates WHERE project_type = ? ORDER BY phase ASC, position ASC",
            (project_type,),
        )
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            for key in ("recurrence_rule", "auto_complete"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except (json.JSONDecodeError, TypeError):
                        d[key] = None
            rows.append(d)
        return rows

    # ── Affairs: Phase Tasks (3.0 §4.2, phase 2) ──

    def _phase_of_stage(self, stage: str) -> str:
        # general 无时期(stage='none')→ 任务归 before 单时期(§3.3)
        return stage if stage in ("before", "during", "after") else "before"

    def _occurrence_for_now(self, rule: dict) -> tuple[str, str]:
        """铺入时的「当期」(key, due)。与服务层 affairs_service.current_occurrence
        同构;DB 层自持一份避免循环依赖,口径以服务层为准(Task 3 测试互证)。"""
        import calendar as _cal
        from datetime import date as _date
        today = _date.today()
        freq, day = rule.get("freq"), rule.get("due_day")
        if freq == "monthly":
            d = min(int(day), _cal.monthrange(today.year, today.month)[1])
            return (today.strftime("%Y-%m"),
                    f"{today.year:04d}-{today.month:02d}-{d:02d}")
        if freq == "quarterly":
            q = (today.month - 1) // 3 + 1
            last_month = q * 3
            due = _date(today.year, last_month, 1).replace(
                day=_cal.monthrange(today.year, last_month)[1])
            return f"{today.year}-Q{q}", due.isoformat()
        mmdd = str(day or "12-31")
        return today.strftime("%Y"), f"{today.year}-{mmdd}"

    def insert_playbook_tasks(self, project_id: str, project_type: str, stage: str) -> int:
        from datetime import date as _date, timedelta as _td
        templates = self.list_playbook_templates(project_type)
        current_phase = self._phase_of_stage(stage)
        today = _date.today()
        count = 0
        for t in templates:
            status = "open" if t["phase"] == current_phase else "pending_activation"
            due = None
            occ = None
            if status == "open":
                # 节奏判定按 recurrence_rule 而非 task_type:report 类可带
                # recurrence_rule(§3.3 服务报告/投后管理报告),同样铺当期 occurrence
                if t.get("recurrence_rule"):
                    occ, due = self._occurrence_for_now(t["recurrence_rule"])
                elif t.get("due_date_fixed"):
                    due = t["due_date_fixed"]
                elif t.get("due_offset_days"):
                    due = (today + _td(days=int(t["due_offset_days"]))).isoformat()
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO phase_tasks
                   (id, project_id, phase, task_type, title, description, template_id,
                    status, occurrence_key, due_date, recurrence_rule, auto_complete)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self._uuid(), project_id, t["phase"], t["task_type"], t["title"],
                 t.get("description", ""), t["id"], status, occ, due,
                 json.dumps(t["recurrence_rule"], ensure_ascii=False) if t.get("recurrence_rule") else None,
                 json.dumps(t["auto_complete"], ensure_ascii=False) if t.get("auto_complete") else None,
                 ),
            )
            count += cur.rowcount  # OR IGNORE 命中冲突时 rowcount=0,计数不虚高
        return count

    def migrate_backfill_playbooks(self) -> int:
        rows = self.conn.execute(
            "SELECT id, type, stage FROM projects WHERE kind='project' AND playbook_seeded = 0"
        ).fetchall()
        n = 0
        for r in rows:
            try:
                with self.conn.transaction():
                    self.insert_playbook_tasks(r["id"], r["type"], r["stage"])
                    self.conn.execute(
                        "UPDATE projects SET playbook_seeded = 1 WHERE id = ?", (r["id"],))
                n += 1
            except Exception:
                logger.warning("backfill playbooks failed for %s", r["id"], exc_info=True)
        return n

    def list_phase_tasks(self, project_id: str, limit: int = 0,
                         offset: int = 0) -> list[dict]:
        """limit=0 → 全量(默认,内部 35+ 调用方兼容);limit>0 → 至多 limit 行
        (路由层传 limit+1 探测 has_more,对齐 list_tasks 先例)。"""
        sql = ("SELECT * FROM phase_tasks WHERE project_id = ? "
               "ORDER BY phase ASC, task_type ASC, due_date ASC, created_at ASC")
        params: list = [project_id]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        cursor = self.conn.execute(sql, params)
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            for key in ("recurrence_rule", "auto_complete"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except (json.JSONDecodeError, TypeError):
                        d[key] = None
            rows.append(d)
        return rows

    def get_phase_task(self, task_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM phase_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        # JSON 解码与 list_phase_tasks 对齐(消费方统一拿到 dict 而非原始字符串)
        for key in ("recurrence_rule", "auto_complete"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = None
        return d

    def create_phase_task(self, project_id, phase, task_type, title, description="",
                          due_date=None, recurrence_rule=None, auto_complete=None,
                          template_id=None, status="open", occurrence_key=None) -> dict:
        # 自建 recurring 无模板:生成共享 template_id='user_<8位>' 供全部期次使用,
        # 否则 UNIQUE(project_id, template_id, occurrence_key) 对 NULL 不防重(裁决 2)
        if template_id is None and task_type == "recurring":
            template_id = "user_" + self._uuid().replace("-", "")[:8]
        task_id = self._uuid()
        self.conn.execute(
            """INSERT INTO phase_tasks
               (id, project_id, phase, task_type, title, description, template_id,
                status, occurrence_key, due_date, recurrence_rule, auto_complete)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, project_id, phase, task_type, title, description, template_id,
             status, occurrence_key, due_date,
             json.dumps(recurrence_rule, ensure_ascii=False) if recurrence_rule else None,
             json.dumps(auto_complete, ensure_ascii=False) if auto_complete else None,
             ),
        )
        self.conn.commit()
        return self.get_phase_task(task_id)

    # HTTP PATCH 可改字段白名单;linked_entity_* 由系统链路(闭环)直接
    # SQL 维护,不开放给更新通道。phase 自 3.0 二期 Task 10 开放编辑
    # (TaskFormModal 时期下拉),值域 before/during/after 由路由层校验
    _PHASE_TASK_FIELDS = ("title", "description", "due_date", "status",
                          "recurrence_rule", "auto_complete", "phase")

    def update_phase_task(self, task_id: str, fields: dict) -> dict | None:
        allowed = {k: v for k, v in fields.items() if k in self._PHASE_TASK_FIELDS}
        if not allowed:
            return self.get_phase_task(task_id)
        for key in ("recurrence_rule", "auto_complete"):
            if key in allowed and allowed[key] is not None and not isinstance(allowed[key], str):
                allowed[key] = json.dumps(allowed[key], ensure_ascii=False)
        sets, params = [], []
        if "status" in allowed:
            status = allowed["status"]
            if status == "done":
                # completed_* 不在白名单:不接受外部注入(防 PATCH 伪造完成者),
                # 自动完成链路走原生 SQL 维护不经此通道(评审 C-6)
                sets += ["completed_at = ?", "completed_by = ?"]
                params += [self._now(), "manual"]
            else:
                sets += ["completed_at = NULL", "completed_by = NULL"]
        for k, v in allowed.items():
            sets.append(f"{k} = ?")
            params.append(v)
        params.append(task_id)
        self.conn.execute(
            f"UPDATE phase_tasks SET {', '.join(sets)} WHERE id = ?", params)
        self.conn.commit()
        return self.get_phase_task(task_id)

    def delete_phase_task(self, task_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM phase_tasks WHERE id = ?", (task_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_due_phase_tasks(self, horizon_days: int = 7) -> list[dict]:
        """跨项目 open 且有截止日的任务(逾期 + horizon 日内),due ASC(3.0 三期 §7.2)。

        逾期判定(due_date < today)交由前端纯函数 isOverdue;后端只按
        due_date <= today+horizon 过滤(含今日与逾期),用本地日期避免 UTC 分叉。
        """
        limit = (date.today() + timedelta(days=horizon_days)).isoformat()
        cursor = self.conn.execute(
            """SELECT pt.*, p.name AS project_name, p.stage AS project_stage
               FROM phase_tasks pt
               JOIN projects p ON p.id = pt.project_id
               WHERE p.kind = 'project' AND p.archived_at IS NULL AND pt.status = 'open'
                 AND pt.due_date IS NOT NULL AND pt.due_date <= ?
               ORDER BY pt.due_date ASC, pt.created_at ASC""",
            (limit,),
        )
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            # JSON 解码与 list_phase_tasks 对齐(消费方统一拿到 dict 而非原始字符串)
            for key in ("recurrence_rule", "auto_complete"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except (json.JSONDecodeError, TypeError):
                        d[key] = None
            rows.append(d)
        return rows

    def count_pending_reviews_by_project(self) -> dict[str, dict[str, int]]:
        """per 项目待处理发现项分型计数(resolved=0),供卡片墙/侧栏健康色点(3.0 §7.2)
        与首页卡片矛盾/缺口行(V2-E4);一条 GROUP BY 出 total+分型,
        gap = data-gap + missing-page(与知识册封面信任信号行同口径,2.6 §3.5)。"""
        cursor = self.conn.execute(
            "SELECT project_id, type, COUNT(*) AS cnt FROM wiki_reviews "
            "WHERE resolved = 0 GROUP BY project_id, type")
        acc: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            bucket = acc.setdefault(
                row["project_id"], {"total": 0, "contradiction": 0, "gap": 0})
            bucket["total"] += row["cnt"]
            if row["type"] == "contradiction":
                bucket["contradiction"] += row["cnt"]
            elif row["type"] in ("data-gap", "missing-page"):
                bucket["gap"] += row["cnt"]
        return acc

    # ── Intelligence: Ledger entries (3.1 §3.1, phase 4) ──

    def list_ledger_entries(self, project_id: str, limit: int = 20,
                            offset: int = 0,
                            page_key: str | None = None) -> list[dict]:
        """台账条目时间轴(3.1 §4.3):entry_date 倒序,NULL 日期置尾;offset 翻页
        (路由层传 limit+1 探测 has_more;1≤limit≤100 校验在路由层)。
        page_key 非空 → 仅该台账页条目(V2 E3 台账页时间轴),空 = 全项目。"""
        sql = "SELECT * FROM ledger_entries WHERE project_id = ?"
        params: list = [project_id]
        if page_key:
            sql += " AND page_key = ?"
            params.append(page_key)
        sql += (" ORDER BY (entry_date IS NULL), entry_date DESC, created_at DESC"
                " LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def latest_ledger_entries(self) -> list[dict]:
        """跨项目每项目最新 1 条(首页卡片墙摘要行,3.1 §5.4);JOIN 项目名防 N+1。"""
        cursor = self.conn.execute(
            """SELECT le.*, p.name AS project_name FROM ledger_entries le
               JOIN projects p ON p.id = le.project_id
               WHERE le.id IN (
                   SELECT id FROM (
                       SELECT id, ROW_NUMBER() OVER (
                           PARTITION BY project_id
                           ORDER BY (entry_date IS NULL), entry_date DESC,
                                    created_at DESC) AS rn
                       FROM ledger_entries) WHERE rn = 1)
               ORDER BY (le.entry_date IS NULL), le.entry_date DESC""")
        return [dict(row) for row in cursor.fetchall()]

    # ── Intelligence: Task suggestions (3.1 §3.2, phase 4) ──

    def list_suggestions(self, project_id: str, status: str = "pending",
                         limit: int = 0, offset: int = 0) -> list[dict]:
        """limit=0 → 全量（默认，脉搏徽章等内部调用方兼容）；limit>0 → 至多 limit 行
        （路由层传 limit+1 探测 has_more，对齐 list_phase_tasks 先例）。"""
        sql = ("SELECT * FROM task_suggestions WHERE project_id = ? AND status = ?"
               " ORDER BY created_at ASC")
        params: list = [project_id, status]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_suggestion(self, suggestion_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM task_suggestions WHERE id = ?", (suggestion_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_suggestion(
        self, project_id: str, kind: str, title: str, description: str = "",
        rationale: str | None = None, entry_fingerprint: str | None = None,
        target_task_id: str | None = None, suggested_phase: str | None = None,
        suggested_task_type: str | None = None,
    ) -> dict | None:
        """INSERT 一条建议;UNIQUE(project_id, entry_fingerprint, kind) 撞车返回 None
        (并发/重放安全,3.1 不变量 3;整体 review C1:唯一索引为项目作用域,
        跨项目同文本互不抑制)。"""
        try:
            self.conn.execute(
                """INSERT INTO task_suggestions
                   (id, project_id, kind, title, description, rationale,
                    entry_fingerprint, target_task_id, suggested_phase,
                    suggested_task_type, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (self._uuid(), project_id, kind, title, description, rationale,
                 entry_fingerprint, target_task_id, suggested_phase,
                 suggested_task_type, self._now()))
            self.conn.commit()
        except sqlite3.IntegrityError:
            return None
        return self.get_suggestion(
            self._last_suggestion_id(project_id, entry_fingerprint, kind))

    def _last_suggestion_id(self, project_id: str,
                            fingerprint: str | None, kind: str) -> str | None:
        cursor = self.conn.execute(
            """SELECT id FROM task_suggestions
               WHERE project_id = ? AND entry_fingerprint IS ? AND kind = ?
               ORDER BY created_at DESC LIMIT 1""", (project_id, fingerprint, kind))
        row = cursor.fetchone()
        return row["id"] if row else None

    def decide_suggestion(self, suggestion_id: str, status: str,
                          accepted_task_id: str | None = None,
                          only_pending: bool = False) -> dict | None:
        """采纳/忽略(置 decided_at);调用方负责 status 合法性(pending 之外不可再改)。
        only_pending=True 时条件更新,已决状态不可覆盖(rowcount=0 返回 None,
        整体 review W2:dismiss 与 accept 并发时后到者不得覆盖已决状态)。"""
        sql = ("""UPDATE task_suggestions
                   SET status = ?, accepted_task_id = COALESCE(?, accepted_task_id),
                       decided_at = ?
                   WHERE id = ?""")
        if only_pending:
            sql += " AND status = 'pending'"
        cur = self.conn.execute(
            sql, (status, accepted_task_id, self._now(), suggestion_id))
        self.conn.commit()
        if only_pending and cur.rowcount == 0:
            return None
        return self.get_suggestion(suggestion_id)

    def accept_suggestion_atomic(self, suggestion_id: str) -> dict | None:
        """原子采纳(整体 review W2:路由层 check-then-act 有竞态窗口——
        accept×dismiss 并发可产生「任务已建但建议显示 dismissed」悬挂,
        create 与 decide 间断进可致重复建任务)。

        单事务内:pending 守卫(条件 UPDATE 占位,rowcount=0 → 不存在/已被决策)
        → 按 kind 执行 create/complete 动作 → 回填 accepted_task_id。
        全程裸 SQL(不经 create_phase_task/update_phase_task,其内层 commit
        会提前终结事务破坏原子性)。

        返回:
        - {"suggestion": …, "task": …}  成功(task 已建/已置 done)
        - {"suggestion": …dismissed, "task": None, "reason": "target_missing"}
                                         目标失效(建议已转 dismissed,永不再提)
        - None                           不存在/已被决策(路由转 409)
        """
        with self.conn.transaction():
            cur = self.conn.execute(
                """UPDATE task_suggestions SET status = 'accepted', decided_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (self._now(), suggestion_id))
            if cur.rowcount == 0:
                return None
            sug = self.get_suggestion(suggestion_id)
            if sug["kind"] == "create_task":
                task_id = self._uuid()
                self.conn.execute(
                    """INSERT INTO phase_tasks
                       (id, project_id, phase, task_type, title, description,
                        template_id, status, occurrence_key, due_date,
                        recurrence_rule, auto_complete)
                       VALUES (?, ?, ?, ?, ?, ?, NULL, 'open', NULL, NULL,
                               NULL, NULL)""",
                    (task_id, sug["project_id"],
                     sug["suggested_phase"] or "during",
                     sug["suggested_task_type"] or "milestone",
                     sug["title"], sug["description"] or ""))
                self.conn.execute(
                    "UPDATE task_suggestions SET accepted_task_id = ? WHERE id = ?",
                    (task_id, suggestion_id))
                return {"suggestion": self.get_suggestion(suggestion_id),
                        "task": self.get_phase_task(task_id)}
            # complete_task:目标校验 + 置 done(completed_by='manual':人是
            # 决策者,与 PATCH 通道同口径;completed_* 不接受外部注入的规则不变)
            target = self.get_phase_task(sug["target_task_id"] or "")
            if (not target or target["project_id"] != sug["project_id"]
                    or target["status"] != "open"):
                self.conn.execute(
                    "UPDATE task_suggestions SET status = 'dismissed' WHERE id = ?",
                    (suggestion_id,))
                return {"suggestion": self.get_suggestion(suggestion_id),
                        "task": None, "reason": "target_missing"}
            self.conn.execute(
                """UPDATE phase_tasks SET status = 'done', completed_at = ?,
                   completed_by = 'manual' WHERE id = ?""",
                (self._now(), target["id"]))
            self.conn.execute(
                "UPDATE task_suggestions SET accepted_task_id = ? WHERE id = ?",
                (target["id"], suggestion_id))
            return {"suggestion": self.get_suggestion(suggestion_id),
                    "task": self.get_phase_task(target["id"])}

    def suggestion_exists(self, project_id: str, fingerprint: str, kind: str,
                          statuses: tuple = ("pending", "accepted", "dismissed")) -> bool:
        """生成幂等查询:(project_id, fingerprint, kind) 在任一给定状态已有记录 →
        不再生成(3.1 §3.2:忽略过不再提;整体 review C1:项目作用域,
        跨项目同文本互不抑制)。"""
        placeholders = ",".join("?" for _ in statuses)
        cursor = self.conn.execute(
            f"""SELECT 1 FROM task_suggestions
                WHERE project_id = ? AND entry_fingerprint = ? AND kind = ?
                  AND status IN ({placeholders}) LIMIT 1""",
            (project_id, fingerprint, kind, *statuses))
        return cursor.fetchone() is not None

    def set_pulse_ai_status(self, project_id: str, status: str) -> None:
        """AI 状态句写入(编译时点快照,3.1 §3.3);空串=清除,前端回落规则句。"""
        self.conn.execute(
            "UPDATE projects SET pulse_ai_status = ?, pulse_ai_status_at = ? WHERE id = ?",
            (status, self._now(), project_id))
        self.conn.commit()

    # ── Wiki: Pages ──

    def create_or_update_wiki_page(
        self,
        project_id: str,
        page_key: str,
        title: str,
        content: str | None = None,
        status: str = "draft",
        compile_version: int | None = None,
        last_compiled_file_id: str | None = None,
        page_type: str = "structure",
        refs: list[str] | None = None,
        sources: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        now = self._now()
        page_id = self._uuid()
        version = compile_version if compile_version is not None else 1
        refs_json = json.dumps(refs, ensure_ascii=False) if refs is not None else None
        sources_json = json.dumps(sources, ensure_ascii=False) if sources is not None else None
        tags_json = json.dumps(tags, ensure_ascii=False) if tags is not None else None
        self.conn.execute(
            """INSERT INTO wiki_pages (id, project_id, page_key, title, content, status, compile_version, last_compiled_at, last_compiled_file_id, page_type, refs, sources, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, page_key) DO UPDATE SET
                   title=excluded.title, content=excluded.content, status=excluded.status,
                   compile_version=excluded.compile_version, last_compiled_at=excluded.last_compiled_at,
                   last_compiled_file_id=excluded.last_compiled_file_id, page_type=excluded.page_type,
                   refs=excluded.refs, sources=excluded.sources, tags=excluded.tags, updated_at=excluded.updated_at""",
            (page_id, project_id, page_key, title, content, status, version, now, last_compiled_file_id,
             page_type, refs_json, sources_json, tags_json, now, now),
        )
        self.conn.commit()
        return self.get_wiki_page(project_id, page_key)

    def get_wiki_page(self, project_id: str, page_key: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM wiki_pages WHERE project_id = ? AND page_key = ?",
            (project_id, page_key),
        )
        row = cursor.fetchone()
        if row:
            d = dict(row)
            d = self._parse_wiki_page_json_fields(d)
            return d
        return None

    def get_wiki_page_by_id(self, page_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM wiki_pages WHERE id = ?", (page_id,))
        row = cursor.fetchone()
        if row:
            d = dict(row)
            d = self._parse_wiki_page_json_fields(d)
            return d
        return None

    @staticmethod
    def _parse_wiki_page_json_fields(d: dict) -> dict:
        """Parse JSON string fields (refs, sources, tags) into Python lists."""
        for field in ("refs", "sources", "tags"):
            val = d.get(field)
            if val:
                try:
                    d[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
            else:
                d[field] = []
        return d

    def list_wiki_pages(self, project_id: str, limit: int = 0,
                        offset: int = 0) -> list[dict]:
        """limit=0 → 全量(默认,内部调用方兼容);limit>0 → 至多 limit 行
        (路由层传 limit+1 探测 has_more)。"""
        sql = """SELECT wp.*, ws.position
               FROM wiki_pages wp
               LEFT JOIN wiki_schemas ws ON ws.project_type = (
                   SELECT type FROM projects WHERE id = ?
               ) AND ws.page_key = wp.page_key
               WHERE wp.project_id = ?
               ORDER BY COALESCE(ws.position, 999) ASC, wp.page_key ASC"""
        params: list = [project_id, project_id]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [self._parse_wiki_page_json_fields(dict(row)) for row in self.conn.execute(sql, params).fetchall()]

    def list_wiki_page_keys(self, project_id: str) -> list[str]:
        """全量 page_key 集(key-only 轻量查询):pages 分页后 missing_pages
        仍须基于全量 key 集计算,不得基于当前页。"""
        return [row["page_key"] for row in self.conn.execute(
            "SELECT page_key FROM wiki_pages WHERE project_id = ?",
            (project_id,)).fetchall()]

    def search_wiki_pages(self, project_id: str, query: str, top_k: int = 5) -> list[dict]:
        """Search wiki pages by keyword matching on title/page_key/content.

        Returns pages ranked by relevance (title match > page_key match > content match).
        Only returns pages with status='compiled'.
        """
        cursor = self.conn.execute(
            "SELECT * FROM wiki_pages WHERE project_id = ? AND status = 'compiled'",
            (project_id,),
        )
        pages = [dict(row) for row in cursor.fetchall()]
        if not pages:
            return []

        keywords = query.lower().split()
        scored: list[tuple[float, dict]] = []
        for page in pages:
            title_lower = (page.get("title") or "").lower()
            key_lower = (page.get("page_key") or "").lower()
            content_lower = (page.get("content") or "").lower()

            score = 0.0
            for kw in keywords:
                if kw in title_lower:
                    score += 3.0
                if kw in key_lower:
                    score += 2.0
                # Count content occurrences (capped)
                content_hits = min(content_lower.count(kw), 10)
                score += content_hits * 0.5

            if score > 0:
                scored.append((score, page))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [page for _, page in scored[:top_k]]

    def update_wiki_page_content(
        self,
        page_id: str,
        content: str,
        title: str | None = None,
        status: str = "compiled",
        compile_version: int | None = None,
        last_compiled_file_id: str | None = None,
        refs: list[str] | None = None,
        sources: list[str] | None = None,
        tags: list[str] | None = None,
        page_type: str | None = None,
    ):
        now = self._now()
        updates = ["content = ?", "updated_at = ?"]
        params: list = [content, now]
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        updates.append("status = ?")
        params.append(status)
        if compile_version is not None:
            updates.append("compile_version = ?")
            params.append(compile_version)
            updates.append("last_compiled_at = ?")
            params.append(now)
        if last_compiled_file_id is not None:
            updates.append("last_compiled_file_id = ?")
            params.append(last_compiled_file_id)
        if refs is not None:
            updates.append("refs = ?")
            params.append(json.dumps(refs, ensure_ascii=False))
        if sources is not None:
            updates.append("sources = ?")
            params.append(json.dumps(sources, ensure_ascii=False))
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if page_type is not None:
            updates.append("page_type = ?")
            params.append(page_type)
        params.append(page_id)
        self.conn.execute(
            f"UPDATE wiki_pages SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()

        # Auto-save version snapshot
        if compile_version is not None:
            self._save_page_version(page_id, content, title, compile_version)

    def _save_page_version(
        self,
        page_id: str,
        content: str | None,
        title: str | None,
        version: int,
        change_summary: str = "",
    ):
        """Save a version snapshot for a wiki page."""
        try:
            # Get project_id and page_key from the page
            page = self.conn.execute(
                "SELECT project_id, page_key FROM wiki_pages WHERE id = ?", (page_id,)
            ).fetchone()
            if not page:
                return
            version_id = self._uuid()
            self.conn.execute(
                """INSERT INTO wiki_page_versions
                   (id, wiki_page_id, project_id, page_key, title, content, version, change_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (version_id, page_id, page["project_id"], page["page_key"],
                 title or "", content or "", version, change_summary, self._now()),
            )
            self.conn.commit()
        except Exception as e:
            logger.warning("Failed to save page version: %s", e)

    def replace_wiki_page_text(
        self,
        project_id: str,
        page_key: str,
        old_text: str,
        new_text: str,
        change_summary: str = "",
    ) -> dict:
        """Precise text replacement on a wiki page (§5.5 adjudicate).

        全量替换 old_text → new_text（确定性，不经 LLM）；落库为
        status=user_edited（编译保护，用户裁决的说法不被下轮摄入覆盖）+
        compile_version+1 自动版本快照。任一环节失配返回 success=False
        且不产生写操作，宁可留待人工不误改。
        """
        page = self.get_wiki_page(project_id, page_key)
        if not page:
            return {"success": False, "reason": "page_not_found", "page_key": page_key}
        content = page.get("content") or ""
        count = content.count(old_text)
        if count == 0:
            if new_text and new_text in content:
                # 重试语义（§5.5）：上一轮部分失败后，已成功侧已替换为目标文本——
                # 视为达成且不再产生写操作，否则三侧以上矛盾的修复重试将永久 partial
                return {"success": True, "reason": "already_applied", "page_key": page_key}
            return {"success": False, "reason": "text_not_found", "page_key": page_key}
        new_content = content.replace(old_text, new_text)
        version = (page.get("compile_version") or 0) + 1
        now = self._now()
        self.conn.execute(
            "UPDATE wiki_pages SET content = ?, status = 'user_edited', "
            "compile_version = ?, last_compiled_at = ?, updated_at = ? WHERE id = ?",
            (new_content, version, now, now, page["id"]),
        )
        self.conn.commit()
        self._save_page_version(page["id"], new_content, page.get("title"), version, change_summary)
        return {
            "success": True,
            "page_key": page_key,
            "page_id": page["id"],
            "replaced_count": count,
            "version": version,
        }

    def list_wiki_page_versions(self, page_id: str, limit: int = 20,
                                offset: int = 0) -> list[dict]:
        """List version history for a wiki page (offset 翻页,路由层
        传 limit+1 探测 has_more)。"""
        cursor = self.conn.execute(
            """SELECT id, wiki_page_id, page_key, title, version, change_summary, created_at
               FROM wiki_page_versions
               WHERE wiki_page_id = ?
               ORDER BY version DESC LIMIT ? OFFSET ?""",
            (page_id, limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_wiki_page_version(self, version_id: str) -> dict | None:
        """Get a specific version with full content."""
        cursor = self.conn.execute(
            "SELECT * FROM wiki_page_versions WHERE id = ?", (version_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_wiki_page(self, project_id: str, page_key: str) -> bool:
        """Delete a Wiki page with full cascade cleanup.

        Cleans:
          - wiki_page_sources (provenance links)
          - wiki_change_log entries for this page
          - wiki_reviews referencing this page in affected_page_ids
          - Other pages' refs that contain [[page_key]] (broken wikilink removal)
          - The page itself from wiki_pages
        All operations are in a single transaction for atomicity.
        """
        page = self.get_wiki_page(project_id, page_key)
        if not page:
            return False
        page_id = page["id"]

        try:
            # 1. Delete page sources (provenance)
            self.conn.execute(
                "DELETE FROM wiki_page_sources WHERE wiki_page_id = ?", (page_id,)
            )

            # 2. Delete change log entries for this page
            self.conn.execute(
                "DELETE FROM wiki_change_log WHERE project_id = ? AND wiki_page_id = ?",
                (project_id, page_id),
            )

            # 3. Clean wiki_reviews: remove page_id from affected_page_ids,
            #    delete reviews whose affected list becomes empty
            reviews = self.conn.execute(
                "SELECT id, affected_page_ids FROM wiki_reviews "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            for review in reviews:
                affected_raw = review["affected_page_ids"]
                if not affected_raw:
                    continue
                try:
                    affected_list = json.loads(affected_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if page_id not in affected_list:
                    continue
                affected_list.remove(page_id)
                if not affected_list:
                    # No affected pages left — delete the review
                    self.conn.execute(
                        "DELETE FROM wiki_reviews WHERE id = ?", (review["id"],)
                    )
                else:
                    # Update with remaining affected pages
                    self.conn.execute(
                        "UPDATE wiki_reviews SET affected_page_ids = ? WHERE id = ?",
                        (json.dumps(affected_list, ensure_ascii=False), review["id"]),
                    )

            # 4. Strip [[page_key]] from other pages' refs
            #    Find all pages whose refs JSON contains this page_key
            pattern = f'%"{page_key}"%'
            referring = self.conn.execute(
                "SELECT id, refs FROM wiki_pages "
                "WHERE project_id = ? AND id != ? AND refs LIKE ?",
                (project_id, page_id, pattern),
            ).fetchall()
            for ref_page in referring:
                refs_raw = ref_page["refs"]
                if not refs_raw:
                    continue
                try:
                    refs_list = json.loads(refs_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if page_key in refs_list:
                    refs_list.remove(page_key)
                    self.conn.execute(
                        "UPDATE wiki_pages SET refs = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(refs_list, ensure_ascii=False), self._now(), ref_page["id"]),
                    )

            # 5. Delete the page itself
            self.conn.execute(
                "DELETE FROM wiki_pages WHERE project_id = ? AND page_key = ?",
                (project_id, page_key),
            )

            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    # ── Wiki: Page Sources ──

    def add_wiki_page_source(self, wiki_page_id: str, file_id: str, contribution: str = "") -> str:
        # Idempotent: skip if (wiki_page_id, file_id) already exists
        existing = self.conn.execute(
            "SELECT id FROM wiki_page_sources WHERE wiki_page_id = ? AND file_id = ?",
            (wiki_page_id, file_id),
        ).fetchone()
        if existing:
            return existing["id"]
        source_id = self._uuid()
        self.conn.execute(
            "INSERT INTO wiki_page_sources (id, wiki_page_id, file_id, contribution, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_id, wiki_page_id, file_id, contribution, self._now()),
        )
        self.conn.commit()
        return source_id

    def list_wiki_page_sources(self, wiki_page_id: str) -> list[dict]:
        cursor = self.conn.execute(
            """SELECT wps.*, f.filename
               FROM wiki_page_sources wps
               LEFT JOIN files f ON f.id = wps.file_id
               WHERE wps.wiki_page_id = ?
               ORDER BY wps.created_at ASC""",
            (wiki_page_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_wiki_page_sources(self, wiki_page_id: str):
        self.conn.execute("DELETE FROM wiki_page_sources WHERE wiki_page_id = ?", (wiki_page_id,))
        self.conn.commit()

    def remove_wiki_page_source(self, wiki_page_id: str, file_id: str):
        self.conn.execute(
            "DELETE FROM wiki_page_sources WHERE wiki_page_id = ? AND file_id = ?",
            (wiki_page_id, file_id),
        )
        self.conn.commit()

    # ── Wiki: Change Log ──

    def add_wiki_change_log(
        self,
        project_id: str,
        action: str,
        wiki_page_id: str | None = None,
        trigger_file_id: str | None = None,
        summary: str = "",
        source_title: str = "",
        affected_page_keys: list[str] | None = None,
    ) -> str:
        log_id = self._uuid()
        affected_json = json.dumps(affected_page_keys, ensure_ascii=False) if affected_page_keys else None
        self.conn.execute(
            """INSERT INTO wiki_change_log (id, project_id, wiki_page_id, action, trigger_file_id, summary, source_title, affected_page_keys, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (log_id, project_id, wiki_page_id, action, trigger_file_id, summary, source_title, affected_json, self._now()),
        )
        self.conn.commit()
        return log_id

    def list_wiki_change_log(self, project_id: str, wiki_page_id: str | None = None,
                             limit: int = 50, offset: int = 0) -> list[dict]:
        """limit<=0 → 全量(新增语义);limit>0 → LIMIT+OFFSET(路由层
        传 limit+1 探测 has_more)。默认 limit=50 向后兼容既有调用方。"""
        if wiki_page_id:
            sql = ("SELECT * FROM wiki_change_log WHERE project_id = ? "
                   "AND wiki_page_id = ? ORDER BY created_at DESC")
            params: list = [project_id, wiki_page_id]
        else:
            sql = ("SELECT * FROM wiki_change_log WHERE project_id = ? "
                   "ORDER BY created_at DESC")
            params = [project_id]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        cursor = self.conn.execute(sql, params)
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            val = d.get("affected_page_keys")
            if val:
                try:
                    d["affected_page_keys"] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    d["affected_page_keys"] = []
            else:
                d["affected_page_keys"] = []
            rows.append(d)
        return rows

    def get_wiki_backlinks(self, project_id: str, page_key: str) -> list[dict]:
        """Find all pages whose refs JSON array contains the given page_key."""
        # refs stored as JSON array like ["key1","key2"] — use LIKE to search
        pattern = f'%"{page_key}"%'
        cursor = self.conn.execute(
            "SELECT id, page_key, title, page_type, status FROM wiki_pages "
            "WHERE project_id = ? AND refs LIKE ? ORDER BY title",
            (project_id, pattern),
        )
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            # Verify the match is exact (LIKE is approximate for JSON)
            refs_raw = d.get("refs")
            if refs_raw:
                try:
                    refs_list = json.loads(refs_raw)
                    if page_key in refs_list:
                        results.append(d)
                except (json.JSONDecodeError, TypeError):
                    pass
        return results

    # ── Wiki: Reviews ──

    def create_or_update_wiki_review(
        self,
        review_id: str,
        project_id: str,
        review_type: str,
        title: str,
        description: str = "",
        affected_page_ids: list[str] | None = None,
        source_file_id: str | None = None,
        resolved: int = 0,
        resolved_action: str | None = None,
        evidence: list[dict] | None = None,
    ) -> dict:
        """Create or idempotently update a review (stable ID).

        resolved/resolved_action 仅在首次插入时生效；ON CONFLICT 不改 resolved
        ——重复摄入重放时保留用户已做的 dismiss 等处理状态（§4.4）。
        evidence：矛盾证据结构化 [{page_key, quote}]（§3.5），随内容幂等更新；
        传入 None 时保留旧值（COALESCE）——Lint 重跑落库不传 evidence，
        不得抹除摄入产出的结构化证据（§4.4）。
        project_id 随 ON CONFLICT 同步覆盖（review H-1 兑底）：review_id 已按
        项目作用域哈希（wiki.review_id），正常不会撞行；万一撞行也不允许
        B 的写入留在 A 的行上（内容串台）。
        """
        now = self._now()
        ids_json = json.dumps(affected_page_ids, ensure_ascii=False) if affected_page_ids else None
        evidence_json = json.dumps(evidence, ensure_ascii=False) if evidence else None
        self.conn.execute(
            """INSERT INTO wiki_reviews (id, project_id, type, title, description, affected_page_ids, evidence, source_file_id, resolved, resolved_action, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   project_id=excluded.project_id,
                   description=excluded.description, affected_page_ids=excluded.affected_page_ids,
                   evidence=COALESCE(excluded.evidence, wiki_reviews.evidence),
                   source_file_id=excluded.source_file_id""",
            (review_id, project_id, review_type, title, description, ids_json, evidence_json, source_file_id, resolved, resolved_action, now),
        )
        self.conn.commit()
        return self.get_wiki_review(review_id)

    def get_wiki_review(self, review_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM wiki_reviews WHERE id = ?", (review_id,))
        row = cursor.fetchone()
        if row:
            d = dict(row)
            d["affected_page_ids"] = self._load_json_list(d.get("affected_page_ids"))
            d["evidence"] = self._load_json_list(d.get("evidence"))
            return d
        return None

    def list_wiki_reviews(self, project_id: str, resolved: bool | None = None,
                          limit: int = 0, offset: int = 0) -> list[dict]:
        """limit=0 → 全量(默认,向后兼容);limit>0 → 至多 limit 行
        (路由层传 limit+1 探测 has_more)。"""
        if resolved is not None:
            sql = ("SELECT * FROM wiki_reviews WHERE project_id = ? AND resolved = ? "
                   "ORDER BY created_at DESC")
            params: list = [project_id, 1 if resolved else 0]
        else:
            sql = "SELECT * FROM wiki_reviews WHERE project_id = ? ORDER BY created_at DESC"
            params = [project_id]
        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        cursor = self.conn.execute(sql, params)
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            d["affected_page_ids"] = self._load_json_list(d.get("affected_page_ids"))
            d["evidence"] = self._load_json_list(d.get("evidence"))
            rows.append(d)
        return rows

    def count_wiki_reviews_by_type(self, project_id: str) -> dict:
        """按 type 分型计数(徽章数据源):reviews 分页后数组不再完整,
        PulseBar/详情页计数改用本查询,替代全量数组客户端计数。
        返回 {type: {pending, resolved, total}}。"""
        counts: dict = {}
        for row in self.conn.execute(
            "SELECT type, resolved, COUNT(*) AS cnt FROM wiki_reviews "
            "WHERE project_id = ? GROUP BY type, resolved", (project_id,)
        ).fetchall():
            bucket = counts.setdefault(
                row["type"], {"pending": 0, "resolved": 0, "total": 0})
            bucket["resolved" if row["resolved"] else "pending"] += row["cnt"]
            bucket["total"] += row["cnt"]
        return counts

    def resolve_wiki_review(self, project_id: str, review_id: str, action: str) -> bool:
        """核销发现项。project_id 条件是纵深防御（review L-1）：sweep/RESOLVES
        链路的 id 若来自 LLM 输出错配，跨项目核销在 DB 层被挡下。"""
        cursor = self.conn.execute(
            "UPDATE wiki_reviews SET resolved = 1, resolved_action = ? "
            "WHERE id = ? AND project_id = ?",
            (action, review_id, project_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ── Wiki: Compile Tasks ──

    def create_wiki_compile_task(
        self,
        project_id: str,
        trigger_type: str,
        trigger_file_id: str | None = None,
    ) -> dict:
        now = self._now()
        task_id = self._uuid()
        self.conn.execute(
            """INSERT INTO wiki_compile_tasks
               (id, project_id, trigger_type, trigger_file_id, status, created_at)
               VALUES (?, ?, ?, ?, 'queued', ?)""",
            (task_id, project_id, trigger_type, trigger_file_id, now),
        )
        self.conn.commit()
        return self.get_wiki_compile_task(task_id)

    def get_wiki_compile_task(self, task_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM wiki_compile_tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_latest_compile_task(self, project_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM wiki_compile_tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_compile_task_status(
        self,
        task_id: str,
        status: str,
        stage: str | None = None,
        pages_total: int | None = None,
        pages_completed: int | None = None,
        pages_failed: int | None = None,
        error_message: str | None = None,
    ):
        updates = ["status = ?"]
        params: list = [status]
        if stage is not None:
            updates.append("stage = ?")
            params.append(stage)
        if pages_total is not None:
            updates.append("pages_total = ?")
            params.append(pages_total)
        if pages_completed is not None:
            updates.append("pages_completed = ?")
            params.append(pages_completed)
        if pages_failed is not None:
            updates.append("pages_failed = ?")
            params.append(pages_failed)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if status in ("completed", "failed"):
            updates.append("completed_at = ?")
            params.append(self._now())
        params.append(task_id)
        self.conn.execute(
            f"UPDATE wiki_compile_tasks SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        # 纯状态更新：终态通知由调用方经 activity_events 显式声明（0.4 §6.4 E2）；
        # stopped / queued / ingesting 等中间态静默（用户主动停止不算事件）。

    def start_compile_task(self, task_id: str):
        """Record compile task start time. Status is managed by the caller."""
        self.conn.execute(
            "UPDATE wiki_compile_tasks SET started_at = ? WHERE id = ?",
            (self._now(), task_id),
        )
        self.conn.commit()

    def reset_interrupted_compile_tasks(self) -> int:
        """Reset interrupted compile tasks to queued for crash recovery. Returns count.

        Resets in-flight statuses when crashed: 'ingesting' / 'storing' (V2
        per-file ingest) → queued；QueueScanner 拾起后按 ingest_summaries 断点续跑。
        """
        cursor = self.conn.execute(
            "UPDATE wiki_compile_tasks SET status = 'queued' "
            "WHERE status IN ('ingesting', 'storing')"
        )
        self.conn.commit()
        return cursor.rowcount

    def request_compile_stop(self, task_id: str):
        """Request a running compile task to stop after the current file."""
        self.conn.execute(
            "UPDATE wiki_compile_tasks SET stop_requested = 1 WHERE id = ?",
            (task_id,),
        )
        self.conn.commit()

    def is_compile_stop_requested(self, task_id: str) -> bool:
        """Check if stop has been requested for a compile task."""
        cursor = self.conn.execute(
            "SELECT stop_requested FROM wiki_compile_tasks WHERE id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        return bool(row and row["stop_requested"])

    def append_ingest_summary(self, task_id: str, file_summary: dict):
        """Append a per-file ingest summary to the compile task's summary list.
        If a summary with the same file_id already exists, replace it (re-compile).
        """
        task = self.get_wiki_compile_task(task_id)
        existing = []
        if task and task.get("ingest_file_summaries"):
            try:
                raw = task["ingest_file_summaries"]
                existing = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                existing = []
        # Replace existing entry with same file_id, or append
        file_id = file_summary.get("file_id")
        if file_id:
            existing = [s for s in existing if s.get("file_id") != file_id]
        existing.append(file_summary)
        self.conn.execute(
            "UPDATE wiki_compile_tasks SET ingest_file_summaries = ? WHERE id = ?",
            (json.dumps(existing, ensure_ascii=False), task_id),
        )
        self.conn.commit()

    def get_ingest_summaries(self, task_id: str) -> list[dict]:
        """Get all per-file ingest summaries for a compile task."""
        task = self.get_wiki_compile_task(task_id)
        if not task or not task.get("ingest_file_summaries"):
            return []
        try:
            raw = task["ingest_file_summaries"]
            return json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return []

    def get_pending_wiki_files(self, project_id: str, exclude_file_ids: list[str] | None = None) -> list[dict]:
        """Get files that need Wiki ingestion for a project.

        Returns files with status='done' (parsed successfully) that don't have
        an ingested source_page yet, excluding any file_ids already processed.
        """
        query = (
            "SELECT id, filename FROM files "
            f"WHERE {self._PENDING_WIKI_FILES_WHERE}"
        )
        params: list = [project_id, project_id]
        if exclude_file_ids:
            placeholders = ",".join("?" for _ in exclude_file_ids)
            query += f" AND id NOT IN ({placeholders})"
            params.extend(exclude_file_ids)
        query += " ORDER BY created_at ASC"
        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    # ── Wiki: Batch Review Operations (Step 1.4) ──

    def batch_resolve_reviews(self, project_id: str, review_ids: list[str], action: str) -> int:
        """Batch resolve multiple reviews. Returns count of resolved reviews."""
        if not review_ids:
            return 0
        placeholders = ",".join("?" for _ in review_ids)
        cursor = self.conn.execute(
            f"UPDATE wiki_reviews SET resolved = 1, resolved_action = ? "
            f"WHERE project_id = ? AND id IN ({placeholders}) AND resolved = 0",
            [action, project_id] + list(review_ids),
        )
        self.conn.commit()
        return cursor.rowcount

    # ── Wiki: Pending Compile Count ──

    # 待编译集合口径（get_pending_wiki_files 与 get_pending_compile_count 共用，
    # 两处永不漂移的唯一事实源）
    _PENDING_WIKI_FILES_WHERE = (
        "project_id = ? AND status = 'done' "
        "AND id NOT IN ("
        "  SELECT file_id FROM wiki_source_pages "
        "  WHERE project_id = ? AND status IN ('ingested', 'skipped')"
        ")"
    )

    def get_pending_compile_count(self, project_id: str) -> int:
        """待编译文件数 = 解析 done 且未入库（wiki_source_pages 无 ingested/skipped
        记录）的文件集合大小——与编译消费的 get_pending_wiki_files 同口径实时计算。

        2026-10-22 修复：原实现是事件累加计数器（解析+1/删除+1/schema 变更同类
        全+1/编译启动清零），与真实集合脱节——删除未编译文件双计、重解析虚增、
        schema 变更向零文件项目广播，数字随操作序列漂移。实时计算后：删除文件
        集合自然收缩、reprocess 已入库文件不重复计数、force 编译清 source_pages
        后如实反映全量重编。"""
        row = self.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM files WHERE {self._PENDING_WIKI_FILES_WHERE}",
            (project_id, project_id),
        ).fetchone()
        return row["cnt"]

    # ── Wiki: Source Pages (ingestion tracking, §3.7) ──

    def create_or_update_source_page(
        self,
        project_id: str,
        file_id: str,
        source_page_key: str | None = None,
        content_hash: str | None = None,
        status: str = "ingested",
        pages_touched: list[str] | None = None,
        entities_found: list[str] | None = None,
        concepts_found: list[str] | None = None,
    ) -> dict:
        """Create or update a wiki_source_pages entry (one per project+file)."""
        now = self._now()
        sp_id = self._uuid()
        pages_json = json.dumps(pages_touched, ensure_ascii=False) if pages_touched else None
        entities_json = json.dumps(entities_found, ensure_ascii=False) if entities_found else None
        concepts_json = json.dumps(concepts_found, ensure_ascii=False) if concepts_found else None
        self.conn.execute(
            """INSERT INTO wiki_source_pages
               (id, project_id, file_id, source_page_key, content_hash, status,
                pages_touched, entities_found, concepts_found, ingested_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, file_id) DO UPDATE SET
                   source_page_key=excluded.source_page_key,
                   content_hash=excluded.content_hash,
                   status=excluded.status,
                   pages_touched=excluded.pages_touched,
                   entities_found=excluded.entities_found,
                   concepts_found=excluded.concepts_found,
                   updated_at=excluded.updated_at""",
            (sp_id, project_id, file_id, source_page_key, content_hash, status,
             pages_json, entities_json, concepts_json, now, now),
        )
        self.conn.commit()
        return self.get_source_page(project_id, file_id)

    def get_source_page(self, project_id: str, file_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM wiki_source_pages WHERE project_id = ? AND file_id = ?",
            (project_id, file_id),
        )
        row = cursor.fetchone()
        if row:
            return self._parse_source_page_json(dict(row))
        return None

    def list_source_pages(self, project_id: str) -> list[dict]:
        """List ingestion-tracking rows; LEFT JOIN files brings filename for
        the frontend "open original file" entry on src pages (2.2 §10.4).
        LEFT JOIN (not INNER): files row may be gone while tracking remains."""
        cursor = self.conn.execute(
            "SELECT sp.*, f.filename FROM wiki_source_pages sp "
            "LEFT JOIN files f ON f.id = sp.file_id "
            "WHERE sp.project_id = ? ORDER BY sp.ingested_at DESC",
            (project_id,),
        )
        return [self._parse_source_page_json(dict(row)) for row in cursor.fetchall()]

    def delete_source_page(self, project_id: str, file_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM wiki_source_pages WHERE project_id = ? AND file_id = ?",
            (project_id, file_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _parse_source_page_json(d: dict) -> dict:
        """Parse JSON string fields in wiki_source_pages."""
        for field in ("pages_touched", "entities_found", "concepts_found"):
            val = d.get(field)
            if val:
                try:
                    d[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
            else:
                d[field] = []
        return d

    # ── Wiki: Projects purpose/overview (§3.6) ──

    def update_wiki_purpose(self, project_id: str, purpose: str):
        self.conn.execute(
            "UPDATE projects SET wiki_purpose = ?, updated_at = ? WHERE id = ?",
            (purpose, self._now(), project_id),
        )
        self.conn.commit()

    def get_wiki_purpose(self, project_id: str) -> str:
        cursor = self.conn.execute(
            "SELECT wiki_purpose FROM projects WHERE id = ?",
            (project_id,),
        )
        row = cursor.fetchone()
        return (row["wiki_purpose"] or "") if row else ""

    def update_wiki_overview(self, project_id: str, overview: str):
        self.conn.execute(
            "UPDATE projects SET wiki_overview = ?, updated_at = ? WHERE id = ?",
            (overview, self._now(), project_id),
        )
        self.conn.commit()

    def get_wiki_overview(self, project_id: str) -> str:
        cursor = self.conn.execute(
            "SELECT wiki_overview FROM projects WHERE id = ?",
            (project_id,),
        )
        row = cursor.fetchone()
        return (row["wiki_overview"] or "") if row else ""

    # ── Wiki: Cascade cleanup for file deletion (§Step 1.5) ──

    def cleanup_wiki_data_for_file(self, project_id: str, file_id: str) -> dict:
        """Remove all Wiki-derived data associated with a file.

        Cleans:
          - wiki_source_pages (ingestion tracking) — reads pages_touched first
          - wiki_page_sources (page ↔ file provenance links)
          - wiki_reviews.source_file_id → set to NULL (keep review, detach file)
          - Marks affected wiki_pages as 'draft' (need recompilation)

        Does NOT delete wiki_pages themselves — they may aggregate multiple sources.
        All operations run in a single transaction to prevent partial failures.
        Returns counts of cleaned records for logging.
        """
        counts: dict[str, int] = {}

        # 0. Read pages_touched BEFORE deleting source_page record
        touched_page_keys: list[str] = []
        sp = self.get_source_page(project_id, file_id)
        if sp:
            touched_page_keys = sp.get("pages_touched") or []

        try:
            # 1. Delete wiki_source_pages record
            cur = self.conn.execute(
                "DELETE FROM wiki_source_pages WHERE project_id = ? AND file_id = ?",
                (project_id, file_id),
            )
            counts["source_pages"] = cur.rowcount

            # 2. Delete wiki_page_sources referencing this file
            cur = self.conn.execute(
                "DELETE FROM wiki_page_sources WHERE file_id = ?",
                (file_id,),
            )
            counts["page_sources"] = cur.rowcount

            # 3. Detach wiki_reviews from this file (keep reviews, null out file ref)
            cur = self.conn.execute(
                "UPDATE wiki_reviews SET source_file_id = NULL "
                "WHERE project_id = ? AND source_file_id = ?",
                (project_id, file_id),
            )
            counts["reviews_detached"] = cur.rowcount

            # 4. Mark affected wiki_pages as 'draft' (need recompilation)
            #    Only mark pages that are currently 'compiled' and have no other source files
            pages_marked = 0
            for page_key in touched_page_keys:
                page_row = self.conn.execute(
                    "SELECT id FROM wiki_pages WHERE project_id = ? AND page_key = ?",
                    (project_id, page_key),
                ).fetchone()
                if not page_row:
                    continue
                page_id = page_row["id"]
                remaining = self.conn.execute(
                    "SELECT COUNT(*) as cnt FROM wiki_page_sources WHERE wiki_page_id = ?",
                    (page_id,),
                ).fetchone()
                if remaining and remaining["cnt"] == 0:
                    self.conn.execute(
                        "UPDATE wiki_pages SET status = 'draft', updated_at = ? WHERE id = ? AND status = 'compiled'",
                        (self._now(), page_id),
                    )
                    pages_marked += self.conn.execute("SELECT changes()").fetchone()[0]
            counts["pages_marked_draft"] = pages_marked

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        logger.info("cleanup_wiki_data_for_file(%s, %s): %s", project_id, file_id, counts)
        return counts

    def cleanup_wiki_data_for_project(self, project_id: str) -> dict:
        """Explicitly clean Wiki data not covered by ON DELETE CASCADE.

        CASCADE via projects(id) handles: wiki_pages → wiki_page_sources,
        wiki_change_log, wiki_reviews, wiki_compile_tasks, wiki_source_pages.
        This method handles edge cases and external references.
        Returns counts for logging.
        """
        counts: dict[str, int] = {}

        # wiki_source_pages — CASCADE should handle, but be explicit
        try:
            cur = self.conn.execute(
                "DELETE FROM wiki_source_pages WHERE project_id = ?",
                (project_id,),
            )
            counts["source_pages"] = cur.rowcount
        except Exception:
            counts["source_pages"] = 0

        return counts
