import os
import shutil
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection = None

    def initialize(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
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

        # Migrate: skills table - needs_confirm for first-use confirmation
        self._migrate_add_column("skills", "needs_confirm", "INTEGER DEFAULT 1")

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
        self._migrate_add_column("reports", "token_usage", "TEXT")
        self._migrate_add_column("reports", "error_message", "TEXT")

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
            "system_prompt": "",
            "tavily_key": "",
            "vlm_api_url": "https://open.bigmodel.cn/api/paas/v4/",
            "vlm_api_key": "",
            "vlm_model": "glm-4v",
            "max_concurrent_file_parsing": 1,
            "max_concurrent_image_description": 2,
            # Task-specific settings
            "task_model": "",
            "task_planner_model": "",
            "task_timeout": 30,
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

    def update_project(self, proj_id: str, name: str | None = None, description: str | None = None):
        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
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

    def list_tasks(self, status: str | None = None) -> list[dict]:
        """List tasks. If status is None, return all non-deleted tasks.
        If status='active', return only active. If status='archived', return only archived."""
        if status == "archived":
            where = "t.status = 'archived'"
        elif status == "active":
            where = "t.status = 'active'"
        else:
            where = "t.status != 'deleted'"
        cursor = self.conn.execute(
            f"""SELECT t.*, COUNT(m.id) as message_count
               FROM tasks t
               LEFT JOIN task_messages m ON t.id = m.task_id
               WHERE {where}
               GROUP BY t.id
               ORDER BY t.updated_at DESC"""
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_task(self, task_id: str) -> dict | None:
        cursor = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_task(self, task_id: str):
        task = self.get_task(task_id)
        if task:
            # Clean up workspace and venv directories
            workspace = task["workspace_path"]
            task_dir = os.path.dirname(workspace)
            if os.path.isdir(task_dir):
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

    def list_files_paginated(
        self, project_id: str, limit: int = 20,
        before_created_at: str | None = None,
        before_id: str | None = None,
    ) -> tuple[list[dict], bool]:
        """Cursor-based pagination. Returns (files, has_more)."""
        conditions = ["project_id = ?"]
        params: list = [project_id]
        if before_created_at and before_id:
            conditions.append(
                "(created_at < ? OR (created_at = ? AND id < ?))"
            )
            params.extend([before_created_at, before_created_at, before_id])
        where = " AND ".join(conditions)
        cursor = self.conn.execute(
            f"SELECT * FROM files WHERE {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            params + [limit + 1]
        )
        rows = cursor.fetchall()
        has_more = len(rows) > limit
        files = [dict(row) for row in rows[:limit]]
        return files, has_more

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

    def update_section_content(self, section_pk: str, content: str, word_count: int, status: str = "completed"):
        self.conn.execute(
            "UPDATE report_sections SET content = ?, word_count = ?, status = ?, updated_at = ? WHERE id = ?",
            (content, word_count, status, self._now(), section_pk),
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
