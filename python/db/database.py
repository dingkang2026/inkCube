import os
import shutil
import sqlite3
import json
import logging
import threading
import uuid
from datetime import datetime
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
                output_format TEXT NOT NULL DEFAULT 'Word',
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
                updated_at TEXT NOT NULL
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
        self._migrate_add_column("reports", "variables", "TEXT")
        self._migrate_add_column("reports", "base_project_ids", "TEXT")
        self._migrate_add_column("reports", "section_internet", "TEXT")
        self._migrate_add_column("reports", "internet_enabled", "INTEGER DEFAULT 0")

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
            "max_concurrent_file_parsing": 1,
            "max_concurrent_image_description": 2,
            # Wiki compile settings
            "max_compile_concurrency": 1,
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
            "report_max_concurrent": 2,  # 最大并发生成报告数
            "report_channel_parallelism": 4,  # 通道内多 query 并行数
            # Wiki LLM task routing (§12.2.13)
            "wiki_ingest_model": "",  # empty = use global model
            "wiki_lint_model": "",
            "wiki_dedup_model": "",
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
                       company_name: str = "", credit_code: str = "") -> dict:
        now = self._now()
        proj_id = self._uuid()
        self.conn.execute(
            "INSERT INTO projects (id, name, description, type, stage, company_name, credit_code, kind, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'project', ?, ?)",
            (proj_id, name, description, project_type, stage, company_name, credit_code, now, now),
        )
        self.conn.commit()

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

        # Create per-task venv (only for auto-created dirs, not user-provided)
        venv_path = ""
        if task_dir.startswith(data_dir):
            try:
                from tools.venv_manager import create_venv, get_venv_python, preinstall_packages
                venv_python_path = get_venv_python(os.path.join(task_dir, ".venv"))
                venv_existed = os.path.isfile(venv_python_path)
                venv_path = create_venv(task_dir)
                # Only preinstall when venv is newly created (skip if already existed)
                # Run preinstall in background thread so task creation returns immediately
                if not venv_existed:
                    import threading
                    t = threading.Thread(
                        target=preinstall_packages,
                        args=(venv_python_path,),
                        daemon=True,
                    )
                    t.start()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("venv creation failed for task %s: %s", task_id, e)

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

    def rename_task(self, task_id: str, title: str):
        self.conn.execute(
            "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
            (title, self._now(), task_id),
        )
        self.conn.commit()

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
        output_format: str = "Word",
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

    def count_active_reports(self) -> int:
        """统计正在生成的报告数量（用于并发限制）。"""
        cursor = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM reports WHERE status = 'generating'"
        )
        row = cursor.fetchone()
        return row["cnt"] if row else 0

    def recover_stale_generating_reports(self, timeout_minutes: int = 5) -> list[str]:
        """崩溃恢复（§3.8.3）：扫描卡在 generating 且超时未更新的报告，标记为 error。

        应用启动时调用。程序异常退出会遗留 status='generating' 的报告；
        updated_at 距今 > timeout_minutes 判定为中断，标记 error 并保留 checkpoint，
        等用户在详情页点「重新生成」从 checkpoint 续跑（启动时不自动续跑）。

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
            if age_seconds > timeout_minutes * 60:
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
        """获取报告的生成日志（按时间倒序）。"""
        cursor = self.conn.execute(
            """SELECT * FROM report_logs WHERE report_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (report_id, limit),
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
        section_pk = self._uuid()
        self.conn.execute(
            """INSERT INTO report_sections (id, report_id, section_id, title, status, word_count, compose_mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)""",
            (section_pk, report_id, section_id, title, compose_mode, now, now),
        )
        self.conn.commit()
        return {
            "id": section_pk,
            "report_id": report_id,
            "section_id": section_id,
            "title": title,
            "content": None,
            "status": "pending",
            "word_count": 0,
            "compose_mode": compose_mode,
            "created_at": now,
            "updated_at": now,
        }

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
        """Seed wiki_schemas table (idempotent: INSERT OR REPLACE by id)."""
        for s in schemas:
            self.conn.execute(
                """INSERT OR REPLACE INTO wiki_schemas
                   (id, project_type, page_key, title, compile_hint, base_lib_search_hints, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (s["id"], s["project_type"], s["page_key"], s["title"],
                 s.get("compile_hint", ""),
                 json.dumps(s.get("base_lib_search_hints", []), ensure_ascii=False),
                 s.get("position", 0)),
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
            """INSERT INTO wiki_schemas (id, project_type, page_key, title, compile_hint, base_lib_search_hints, position)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (schema_id, project_type, page_key, title, compile_hint, hints_json, position),
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
    ) -> dict:
        now = self._now()
        ids_json = json.dumps(affected_page_ids, ensure_ascii=False) if affected_page_ids else None
        self.conn.execute(
            """INSERT INTO wiki_reviews (id, project_id, type, title, description, affected_page_ids, source_file_id, resolved, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
               ON CONFLICT(id) DO UPDATE SET
                   description=excluded.description, affected_page_ids=excluded.affected_page_ids,
                   source_file_id=excluded.source_file_id""",
            (review_id, project_id, review_type, title, description, ids_json, source_file_id, now),
        )
        self.conn.commit()
        return self.get_wiki_review(review_id)

    def get_wiki_review(self, review_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM wiki_reviews WHERE id = ?", (review_id,))
        row = cursor.fetchone()
        if row:
            d = dict(row)
            if d.get("affected_page_ids"):
                try:
                    d["affected_page_ids"] = json.loads(d["affected_page_ids"])
                except (json.JSONDecodeError, TypeError):
                    pass
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
            if d.get("affected_page_ids"):
                try:
                    d["affected_page_ids"] = json.loads(d["affected_page_ids"])
                except (json.JSONDecodeError, TypeError):
                    pass
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
            "  WHERE project_id = ? AND status = 'ingested'"
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
        cursor = self.conn.execute(
            "SELECT * FROM wiki_source_pages WHERE project_id = ? ORDER BY ingested_at DESC",
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
