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
        self._migrate_add_column("tasks", "mode", "TEXT DEFAULT 'coding'")

        # Migrate: projects table - type/stage for project classification
        self._migrate_add_column("projects", "type", "TEXT DEFAULT 'general'")
        self._migrate_add_column("projects", "stage", "TEXT DEFAULT 'none'")

        # Migrate: projects table - company info fields
        self._migrate_add_column("projects", "company_name", "TEXT DEFAULT ''")
        self._migrate_add_column("projects", "credit_code", "TEXT DEFAULT ''")

        # Migrate: projects table - kind/lib_type for base library support
        self._migrate_add_column("projects", "kind", "TEXT DEFAULT 'project'")
        self._migrate_add_column("projects", "lib_type", "TEXT DEFAULT NULL")

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
                kind TEXT NOT NULL,          -- completed / failed
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
        self._migrate_add_column("reports", "token_usage", "TEXT")
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

        # Migrate: projects table - pending_compile_count for Wiki notification
        self._migrate_add_column("projects", "pending_compile_count", "INTEGER DEFAULT 0")

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

        # Migrate: suggestions 幂等约束补项目作用域(整体 review C1)
        self._migrate_task_suggestions_unique()

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
            "model": "glm-4-flash",
            "temperature": 0.7,
            "max_tokens": 8192,
            "top_p": 0.9,
            # Reasoning params (settings page 思考深度/高级覆盖) — must stay in
            # this whitelist or get_settings() silently drops saved values
            "reasoning_effort": "low",
            "reasoning_override": "",
            "system_prompt": "",
            "tavily_key": "",
            "vlm_api_url": "https://open.bigmodel.cn/api/paas/v4/",
            "vlm_api_key": "",
            "vlm_model": "glm-4v",
            "max_concurrent_file_parsing": 2,
            "max_concurrent_image_description": 2,
            # Wiki compile settings（默认 2：并发单位是项目，项目内文件本就串行，
            # 2 = 两个项目并行编译；同项目由 API 层 409 防重保证单一编译任务）
            "max_compile_concurrency": 2,
            # LLM timeout settings
            "llm_connect_timeout": 30,
            "llm_read_timeout": 300,
            # Task-specific settings
            "task_model": "",
            "task_planner_model": "",
            "task_timeout": 30,
            "task_max_iterations": 25,  # FC loop max iterations (TaskSettings UI)
            "task_shell_confirm_mode": "smart",  # strict | smart | auto
            "task_auto_confirm_shell": False,  # legacy, kept for backward compat
            "task_shell_domain_whitelist": "",  # newline-separated domains auto-approved in smart mode
            "task_show_execution_log": True,
            "task_workspace_max_mb": 1024,
            "task_max_count": 50,
            "task_auto_archive_days": 30,
            # Report-specific settings
            "report_max_concurrent": 2,  # model-exempt: 预留位暂无 UI，后端消费（reports.py）；最大并发生成报告数
            "report_channel_parallelism": 4,  # model-exempt: 预留位暂无 UI，后端消费（runner.py）；通道内多 query 并行数
            # Wiki LLM task routing (§12.2.13)
            "wiki_ingest_model": "",  # empty = use global model
            "wiki_lint_model": "",
            "wiki_dedup_model": "",
            "wiki_ledger_parse_model": "",  # 3.1 §4.2 台账解析(轻模型;empty = 全局默认)
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

    def list_projects(self) -> list[dict]:
        cursor = self.conn.execute(
            """SELECT p.*, COUNT(f.id) as file_count
               FROM projects p
               LEFT JOIN files f ON p.id = f.project_id
               WHERE p.kind = 'project'
               GROUP BY p.id
               ORDER BY p.updated_at DESC"""
        )
        return [dict(row) for row in cursor.fetchall()]

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

        # 终态事件已解耦（E2，0.4 §6.4 / §8.5 顺序 3）：done / error 的通知由调用方
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

    def create_task(self, title: str, data_dir: str, workspace_path: str = "", mode: str = "coding") -> dict:
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
            "INSERT INTO tasks (id, title, status, workspace_path, venv_path, mode, created_at, updated_at) VALUES (?, ?, 'active', ?, ?, ?, ?, ?)",
            (task_id, title, workspace_path, venv_path, mode, now, now),
        )
        self.conn.commit()
        return {
            "id": task_id,
            "title": title,
            "status": "active",
            "workspace_path": workspace_path,
            "venv_path": venv_path,
            "mode": mode,
            "created_at": now,
            "updated_at": now,
        }

    def list_tasks(
        self, status: str | None = None, limit: int = 0, offset: int = 0,
    ) -> tuple[list[dict], bool]:
        """List tasks. If status is None, return all non-deleted tasks.
        If status='active', return only active. If status='archived', return only archived.
        limit=0 → no pagination (backward compatible); limit>0 → fetch limit+1 rows
        to detect has_more without a COUNT query. Returns (tasks, has_more)."""
        if status == "archived":
            where = "t.status = 'archived'"
        elif status == "active":
            where = "t.status = 'active'"
        else:
            where = "t.status != 'deleted'"
        sql = f"""SELECT t.*, COUNT(m.id) as message_count
               FROM tasks t
               LEFT JOIN task_messages m ON t.id = m.task_id
               WHERE {where}
               GROUP BY t.id
               ORDER BY t.updated_at DESC"""
        params: list = []
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
            workspace = task["workspace_path"]
            task_dir = os.path.dirname(workspace)
            managed_root = os.path.join(os.path.dirname(self.db_path), "tasks")
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

    # ── Skills ──

    def list_skills(self) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM skills ORDER BY created_at DESC"
        )
        return [dict(row) for row in cursor.fetchall()]

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

    def list_web_logins(self) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT domain, note, created_at FROM web_logins ORDER BY created_at DESC"
        )
        return [dict(row) for row in cursor.fetchall()]

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

    def list_reports(self, project_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM reports WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_report(self, report_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_report_status(self, report_id: str, status: str, progress: int | None = None):
        # 纯状态更新：终态通知由调用方经 activity_events 显式声明（0.4 §6.4 E2，顺序 4）；
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

    def update_report_token_usage(self, report_id: str, token_usage: dict):
        """保存报告生成的 token 统计（JSON 格式）。"""
        self.conn.execute(
            "UPDATE reports SET token_usage = ?, updated_at = ? WHERE id = ?",
            (json.dumps(token_usage, ensure_ascii=False), self._now(), report_id),
        )
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

    def list_report_sections(self, report_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM report_sections WHERE report_id = ? ORDER BY created_at ASC",
            (report_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

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

    def create_base_lib(self, name: str, description: str = "", lib_type: str = "custom") -> dict:
        now = self._now()
        lib_id = self._uuid()
        self.conn.execute(
            "INSERT INTO projects (id, name, description, kind, lib_type, created_at, updated_at) "
            "VALUES (?, ?, ?, 'base_lib', ?, ?, ?)",
            (lib_id, name, description, lib_type, now, now),
        )
        self.conn.commit()
        return {
            "id": lib_id,
            "name": name,
            "description": description,
            "kind": "base_lib",
            "lib_type": lib_type,
            "created_at": now,
            "updated_at": now,
        }

    def list_base_libs(self) -> list[dict]:
        cursor = self.conn.execute(
            """SELECT p.*, COUNT(f.id) as file_count
               FROM projects p
               LEFT JOIN files f ON p.id = f.project_id
               WHERE p.kind = 'base_lib'
               GROUP BY p.id
               ORDER BY p.created_at ASC"""
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_base_lib(self, lib_id: str) -> dict | None:
        cursor = self.conn.execute(
            "SELECT * FROM projects WHERE id = ? AND kind = 'base_lib'", (lib_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_base_lib(self, lib_id: str, name: str | None = None, description: str | None = None) -> bool:
        """Update a custom base library. Returns True if updated."""
        lib = self.get_base_lib(lib_id)
        if not lib:
            return False
        if lib.get("lib_type", "").startswith("preset_"):
            return False  # Preset libs cannot be renamed
        updates = []
        params: list = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if not updates:
            return False
        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(lib_id)
        self.conn.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id = ? AND kind = 'base_lib'",
            params,
        )
        self.conn.commit()
        return True

    def delete_base_lib(self, lib_id: str) -> bool:
        """Delete a custom base library. Returns True if deleted. Preset libs cannot be deleted."""
        lib = self.get_base_lib(lib_id)
        if not lib:
            return False
        if lib.get("lib_type", "").startswith("preset_"):
            return False  # Preset libs cannot be deleted
        self.conn.execute("DELETE FROM projects WHERE id = ? AND kind = 'base_lib'", (lib_id,))
        self.conn.commit()
        return True

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
        # Notify affected projects
        self._notify_schema_change(project_type, page_key, "added")
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
        # Notify affected projects
        schema = self._get_schema_by_id(schema_id)
        if schema:
            self._notify_schema_change(schema["project_type"], schema["page_key"], "modified")
        return schema

    def delete_wiki_schema(self, schema_id: str) -> bool:
        """Delete a wiki schema entry."""
        schema = self._get_schema_by_id(schema_id)
        cursor = self.conn.execute("DELETE FROM wiki_schemas WHERE id = ?", (schema_id,))
        self.conn.commit()
        if schema:
            self._notify_schema_change(schema["project_type"], schema["page_key"], "removed")
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

    def _notify_schema_change(self, project_type: str, page_key: str, action: str):
        """Increment pending_compile_count for all projects of the affected type."""
        try:
            cursor = self.conn.execute(
                "SELECT id FROM projects WHERE type = ? AND kind = 'project'",
                (project_type,),
            )
            project_ids = [row["id"] for row in cursor.fetchall()]
            for pid in project_ids:
                self.conn.execute(
                    "UPDATE projects SET pending_compile_count = pending_compile_count + 1 WHERE id = ?",
                    (pid,),
                )
            if project_ids:
                self.conn.commit()
                logger.info("Schema %s %s: notified %d projects", action, page_key, len(project_ids))
        except Exception as e:
            logger.warning("Failed to notify schema change: %s", e)

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

    def list_phase_tasks(self, project_id: str) -> list[dict]:
        cursor = self.conn.execute(
            "SELECT * FROM phase_tasks WHERE project_id = ? "
            "ORDER BY phase ASC, task_type ASC, due_date ASC, created_at ASC",
            (project_id,),
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
               WHERE p.kind = 'project' AND pt.status = 'open'
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

    def count_pending_reviews_by_project(self) -> dict[str, int]:
        """per 项目待处理发现项计数(resolved=0),供卡片墙/侧栏健康色点(3.0 §7.2)。"""
        cursor = self.conn.execute(
            "SELECT project_id, COUNT(*) AS cnt FROM wiki_reviews "
            "WHERE resolved = 0 GROUP BY project_id")
        return {row["project_id"]: row["cnt"] for row in cursor.fetchall()}

    # ── Intelligence: Ledger entries (3.1 §3.1, phase 4) ──

    def list_ledger_entries(self, project_id: str, limit: int = 20) -> list[dict]:
        """台账条目时间轴(3.1 §4.3):entry_date 倒序,NULL 日期置尾。"""
        cursor = self.conn.execute(
            """SELECT * FROM ledger_entries WHERE project_id = ?
               ORDER BY (entry_date IS NULL), entry_date DESC, created_at DESC
               LIMIT ?""",
            (project_id, limit),
        )
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

    def list_suggestions(self, project_id: str, status: str = "pending") -> list[dict]:
        cursor = self.conn.execute(
            """SELECT * FROM task_suggestions WHERE project_id = ? AND status = ?
               ORDER BY created_at ASC""",
            (project_id, status),
        )
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

    def list_wiki_pages(self, project_id: str) -> list[dict]:
        cursor = self.conn.execute(
            """SELECT wp.*, ws.position
               FROM wiki_pages wp
               LEFT JOIN wiki_schemas ws ON ws.project_type = (
                   SELECT type FROM projects WHERE id = ?
               ) AND ws.page_key = wp.page_key
               WHERE wp.project_id = ?
               ORDER BY COALESCE(ws.position, 999) ASC, wp.page_key ASC""",
            (project_id, project_id),
        )
        return [self._parse_wiki_page_json_fields(dict(row)) for row in cursor.fetchall()]

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

    def list_wiki_page_versions(self, page_id: str, limit: int = 20) -> list[dict]:
        """List version history for a wiki page."""
        cursor = self.conn.execute(
            """SELECT id, wiki_page_id, page_key, title, version, change_summary, created_at
               FROM wiki_page_versions
               WHERE wiki_page_id = ?
               ORDER BY version DESC LIMIT ?""",
            (page_id, limit),
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

    def list_wiki_change_log(self, project_id: str, wiki_page_id: str | None = None, limit: int = 50) -> list[dict]:
        if wiki_page_id:
            cursor = self.conn.execute(
                "SELECT * FROM wiki_change_log WHERE project_id = ? AND wiki_page_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, wiki_page_id, limit),
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM wiki_change_log WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
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
        """
        now = self._now()
        ids_json = json.dumps(affected_page_ids, ensure_ascii=False) if affected_page_ids else None
        evidence_json = json.dumps(evidence, ensure_ascii=False) if evidence else None
        self.conn.execute(
            """INSERT INTO wiki_reviews (id, project_id, type, title, description, affected_page_ids, evidence, source_file_id, resolved, resolved_action, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
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

    def list_wiki_reviews(self, project_id: str, resolved: bool | None = None) -> list[dict]:
        if resolved is not None:
            cursor = self.conn.execute(
                "SELECT * FROM wiki_reviews WHERE project_id = ? AND resolved = ? ORDER BY created_at DESC",
                (project_id, 1 if resolved else 0),
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM wiki_reviews WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            )
        rows = []
        for row in cursor.fetchall():
            d = dict(row)
            d["affected_page_ids"] = self._load_json_list(d.get("affected_page_ids"))
            d["evidence"] = self._load_json_list(d.get("evidence"))
            rows.append(d)
        return rows

    def resolve_wiki_review(self, review_id: str, action: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE wiki_reviews SET resolved = 1, resolved_action = ? WHERE id = ?",
            (action, review_id),
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
        # 纯状态更新：终态通知由调用方经 activity_events 显式声明（0.4 §6.4 E2，
        # 顺序 4）；stopped / queued / ingesting 等中间态静默（用户主动停止不算事件）。

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
            "WHERE project_id = ? AND status = 'done' "
            "AND id NOT IN ("
            "  SELECT file_id FROM wiki_source_pages "
            "  WHERE project_id = ? AND status IN ('ingested', 'skipped')"
            ")"
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

    def increment_pending_compile_count(self, project_id: str, delta: int = 1):
        self.conn.execute(
            "UPDATE projects SET pending_compile_count = COALESCE(pending_compile_count, 0) + ?, updated_at = ? WHERE id = ?",
            (delta, self._now(), project_id),
        )
        self.conn.commit()

    def reset_pending_compile_count(self, project_id: str):
        self.conn.execute(
            "UPDATE projects SET pending_compile_count = 0, updated_at = ? WHERE id = ?",
            (self._now(), project_id),
        )
        self.conn.commit()

    def get_pending_compile_count(self, project_id: str) -> int:
        cursor = self.conn.execute(
            "SELECT pending_compile_count FROM projects WHERE id = ?",
            (project_id,),
        )
        row = cursor.fetchone()
        return (row["pending_compile_count"] or 0) if row else 0

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
