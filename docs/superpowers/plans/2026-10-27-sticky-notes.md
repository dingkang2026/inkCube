# 便签（Sticky Notes）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全局速记收件箱——任意页面（含任务页）经侧栏图标 / `Ctrl+Shift+N` 调起右抽屉，纯文本随手记，可归档进项目素材、转为事务、删除。

**Architecture:** 新表 `sticky_notes`（database.py 集中 DDL）+ 新 router `routers/sticky_notes.py`（六端点）+ IPC 六通道（ipc-handlers.js fetchWithTimeout 代理，preload 通用 invoke 零改动）+ App 级 `StickyDrawer` 抽屉组件。归档复用 `_upload_core` 落素材，转事务复用 `db.create_phase_task`。零 LLM、零新 Activity。

**Tech Stack:** Python 3.12 + FastAPI / SQLite / React 18 + TypeScript / Electron IPC / vitest + pytest

**Spec:** [docs/2.12sticky-notes-design.md](../../2.12sticky-notes-design.md)（执行者须同读）

## Global Constraints

- 禁止任何 git 操作；每任务末尾 checkpoint 处由用户手动提交
- i18n 双语同步：`src/i18n/locales/zh-CN.json` 与 `en-US.json` 成对新增，禁止硬编码文案
- 分页契约 `limit/offset/has_more/total`（limit+1 探测 has_more，参考 `web_logins.py`）
- 孤儿通道零容忍：交付前 `node scripts/orphan_channel_audit.js` 四组为 0
- Python 验证：`C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/ -v`；前端：`npx tsc --noEmit` + `npx vitest run` + `npm run build`
- 轻的不做清单（spec §1）：无富文本/分类/标签体系/按色筛选/自定义颜色/独立路由/LLM/解析/后台活动/提醒角标/自动清理

---

### Task 1: DB 层——DDL 与 CRUD 函数

**Files:**
- Modify: `python/db/database.py`（DDL 集中区 L370 `web_logins` 之后加表；CRUD 函数加在 `add_web_login` L2658 附近同类区）
- Test: `python/tests/test_sticky_notes.py`（新建）

**Interfaces:**
- Produces（后续任务依赖的 db 方法签名）:
  - `create_sticky_note(content: str, color: str | None) -> dict`
  - `list_sticky_notes(view: str, limit: int, offset: int) -> list[dict]`（view: `'inbox'|'archived'`）
  - `get_sticky_note(note_id: str) -> dict | None`
  - `update_sticky_note(note_id: str, content: str | None, color: str | None) -> dict | None`
  - `delete_sticky_note(note_id: str) -> bool`
  - `archive_sticky_note(note_id: str, project_id: str) -> dict | None`（置 project_id/archived_at；已归档返回 None）
  - `detach_sticky_notes_for_project(project_id: str) -> int`（项目删除联动：project_id 置 NULL 回收件箱）

- [ ] **Step 1: 写失败测试**

```python
"""python/tests/test_sticky_notes.py — sticky_notes CRUD 与归档联动。"""
import pytest
from db.database import Database

@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))

COLORS = {"yellow", "red", "blue", "green", "purple"}

def test_create_and_list_inbox(db):
    db.create_sticky_note("第一笔", "yellow")
    db.create_sticky_note("第二笔", None)
    rows = db.list_sticky_notes("inbox", limit=10, offset=0)
    assert len(rows) == 2 and rows[0]["content"] == "第二笔"  # updated_at 倒序
    assert rows[0]["archived_at"] is None

def test_color_whitelist(db):
    with pytest.raises(ValueError):
        db.create_sticky_note("x", "pink")  # 非法色值拒绝

def test_archive_and_views(db):
    n = db.create_sticky_note("待归档", None)
    pid = db.create_project(name="P", type="general")["id"]
    ok = db.archive_sticky_note(n["id"], pid)
    assert ok["project_id"] == pid and ok["archived_at"]
    assert db.list_sticky_notes("inbox", limit=0, offset=0) == []
    assert len(db.list_sticky_notes("archived", limit=0, offset=0)) == 1
    assert db.archive_sticky_note(n["id"], pid) is None  # 幂等：已归档返回 None

def test_detach_on_project_delete(db):
    n = db.create_sticky_note("x", None)
    pid = db.create_project(name="P", type="general")["id"]
    db.archive_sticky_note(n["id"], pid)
    assert db.detach_sticky_notes_for_project(pid) == 1
    assert len(db.list_sticky_notes("inbox", limit=0, offset=0)) == 1
```

- [ ] **Step 2: 运行确认失败**——`python -m pytest python/tests/test_sticky_notes.py -v`，预期 FAIL（方法不存在）

- [ ] **Step 3: 最小实现**——database.py DDL 区（`web_logins` 表后）追加：

```sql
CREATE TABLE IF NOT EXISTS sticky_notes (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    color       TEXT,
    project_id  TEXT,
    archived_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sticky_notes_updated ON sticky_notes(updated_at DESC);
```

CRUD 区（web_logins 函数后）追加，模式对齐 `add_web_login`（`self._uuid()` / `self._now()` / `dict(row)`）：

```python
_STICKY_COLORS = ("yellow", "red", "blue", "green", "purple")

def _check_color(self, color):
    if color is not None and color not in self._STICKY_COLORS:
        raise ValueError(f"invalid color: {color}")

def create_sticky_note(self, content: str, color: str | None) -> dict:
    self._check_color(color)
    nid, now = self._uuid(), self._now()
    self.conn.execute(
        "INSERT INTO sticky_notes (id, content, color, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)", (nid, content, color, now, now))
    self.conn.commit()
    return self.get_sticky_note(nid)

def list_sticky_notes(self, view: str, limit: int, offset: int) -> list[dict]:
    where = "WHERE archived_at IS NULL" if view == "inbox" else "WHERE archived_at IS NOT NULL"
    sql = f"SELECT * FROM sticky_notes {where} ORDER BY updated_at DESC"
    params: list = []
    if limit > 0:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

def get_sticky_note(self, note_id: str) -> dict | None:
    row = self.conn.execute("SELECT * FROM sticky_notes WHERE id = ?", (note_id,)).fetchone()
    return dict(row) if row else None

def update_sticky_note(self, note_id: str, content: str | None, color: str | None) -> dict | None:
    self._check_color(color)
    if content is None and color is None:
        return self.get_sticky_note(note_id)
    sets, params = [], []
    if content is not None: sets.append("content = ?"); params.append(content)
    if color is not None:   sets.append("color = ?");   params.append(color)
    sets.append("updated_at = ?"); params.append(self._now()); params.append(note_id)
    cur = self.conn.execute(f"UPDATE sticky_notes SET {', '.join(sets)} WHERE id = ?", params)
    self.conn.commit()
    return self.get_sticky_note(note_id) if cur.rowcount else None

def delete_sticky_note(self, note_id: str) -> bool:
    cur = self.conn.execute("DELETE FROM sticky_notes WHERE id = ?", (note_id,))
    self.conn.commit()
    return cur.rowcount > 0

def archive_sticky_note(self, note_id: str, project_id: str) -> dict | None:
    row = self.get_sticky_note(note_id)
    if not row or row["archived_at"]:
        return None
    self.conn.execute(
        "UPDATE sticky_notes SET project_id = ?, archived_at = ?, updated_at = ? WHERE id = ?",
        (project_id, self._now(), self._now(), note_id))
    self.conn.commit()
    return self.get_sticky_note(note_id)

def detach_sticky_notes_for_project(self, project_id: str) -> int:
    cur = self.conn.execute(
        "UPDATE sticky_notes SET project_id = NULL, archived_at = NULL, updated_at = ?"
        " WHERE project_id = ?", (self._now(), project_id))
    self.conn.commit()
    return cur.rowcount
```

注：`create_project` 返回形态以文件内现有实现为准（测试中若签名不同，按现有签名修正测试，不改现有函数）。

- [ ] **Step 4: 运行确认通过**——`python -m pytest python/tests/test_sticky_notes.py -v` 全 PASS

- [ ] **Step 5: Checkpoint**——用户手动提交

### Task 2: Python 路由——六端点

**Files:**
- Create: `python/routers/sticky_notes.py`
- Modify: `python/main.py`（L364 `web_logins.router` 后加 import 与 `app.include_router(sticky_notes.router)`）；`python/routers/knowledge.py` 的项目删除端点（L812 delete_project，`db.detach_leads_for_project` 同位置追加 `db.detach_sticky_notes_for_project(project_id)`）
- Test: `python/tests/test_sticky_notes.py`（追加路由层用例）

**Interfaces:**
- Consumes: Task 1 全部 db 方法；`routers.knowledge._upload_core / FileUpload / ensure_ready`；`affairs_service.phase_of`；`deps.worker`
- Produces: 端点 `GET/POST /sticky/notes`、`PATCH/DELETE /sticky/notes/{id}`、`POST /sticky/notes/{id}/archive`、`POST /sticky/notes/{id}/to-affair`

- [ ] **Step 1: 写失败测试**（追加到 test_sticky_notes.py，用 fastapi TestClient + monkeypatch deps.db；参考现有 `python/tests/test_web_logins*.py` 的 client fixture 模式）

关键用例：创建返回行；列表 has_more 契约（51 条 limit=50 → has_more=True）；归档端点调 `_upload_core`（monkeypatch 为 fake 返回 `{"file_id": "file_x"}`）后行已落 project_id；未就绪模型（ensure_ready 抛 HTTPException）归档 4xx 不置 archived；to-affair 后 phase_tasks 出现 material 行（title=首行 40 字截断）；非法 color 422。

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**（骨架，模式对齐 web_logins.py）：

```python
"""python/routers/sticky_notes.py — 便签（2.12）：全局速记收件箱。"""
from __future__ import annotations
import base64, logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import deps
from deps import ensure_ready
from routers.knowledge import FileUpload, _upload_core

logger = logging.getLogger(__name__)
router = APIRouter()

class StickyCreate(BaseModel):
    content: str
    color: str | None = None

class StickyUpdate(BaseModel):
    content: str | None = None
    color: str | None = None

class StickyArchive(BaseModel):
    project_id: str

class StickyToAffair(BaseModel):
    project_id: str
    due_date: str | None = None

def _db():
    return deps.db

@router.get("/sticky/notes")
async def list_notes(view: str = "inbox",
                     limit: int = Query(50, ge=0), offset: int = Query(0, ge=0)):
    if view not in ("inbox", "archived"):
        raise HTTPException(422, "invalid view")
    rows = _db().list_sticky_notes(view, limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(rows) > limit
    return {"notes": rows[:limit] if has_more else rows,
            "has_more": has_more,
            "total": _db().conn.execute(
                "SELECT COUNT(*) AS n FROM sticky_notes WHERE archived_at IS "
                + ("NULL" if view == "inbox" else "NOT NULL")).fetchone()["n"]}

@router.post("/sticky/notes")
async def create_note(req: StickyCreate):
    content = req.content.strip()
    if not content:
        raise HTTPException(422, "content required")
    return {"note": _db().create_sticky_note(content, req.color)}

@router.patch("/sticky/notes/{note_id}")
async def update_note(note_id: str, req: StickyUpdate):
    note = _db().update_sticky_note(note_id, req.content, req.color)
    if not note:
        raise HTTPException(404, "note not found")
    return {"note": note}

@router.delete("/sticky/notes/{note_id}")
async def delete_note(note_id: str):
    if not _db().delete_sticky_note(note_id):
        raise HTTPException(404, "note not found")
    return {"ok": True}

@router.post("/sticky/notes/{note_id}/archive")
async def archive_note(note_id: str, req: StickyArchive):
    """归档：便签 md 经 _upload_core 落项目素材（复用上传+解析链），行落 project_id。"""
    ensure_ready("file_parsing")
    db = _db()
    note = db.get_sticky_note(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    if note["archived_at"]:
        raise HTTPException(409, "note already archived")
    proj = db.get_project(req.project_id)
    if not proj or proj.get("kind") != "project":
        raise HTTPException(404, "project not found")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    upload = FileUpload(projectId=req.project_id, filename=f"便签-{ts}.md",
                        fileType="text/markdown",
                        data=base64.b64encode(note["content"].encode("utf-8")).decode())
    result = await _upload_core(req.project_id, upload, db, deps.get_data_dir(), deps.worker)
    return {"note": db.archive_sticky_note(note_id, req.project_id), "file": result}

@router.post("/sticky/notes/{note_id}/to-affair")
async def to_affair(note_id: str, req: StickyToAffair):
    """转事务：material 类，title=首行 40 字截断，description=全文；便签原地留存。"""
    db = _db()
    note = db.get_sticky_note(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    proj = db.get_project(req.project_id)
    if not proj or proj.get("kind") != "project":
        raise HTTPException(404, "project not found")
    from affairs_service import phase_of
    task = db.create_phase_task(
        project_id=req.project_id, phase=phase_of(proj.get("stage") or "none"),
        task_type="material", title=note["content"].splitlines()[0][:40],
        description=note["content"], due_date=req.due_date)
    logger.info("Sticky note %s -> affair %s (project %s)", note_id, task["id"], req.project_id)
    return {"task": task}
```

- [ ] **Step 4: 运行确认通过**；全量回归 `python -m pytest python/tests/ -v`

- [ ] **Step 5: Checkpoint**——用户手动提交

### Task 3: IPC 通道

**Files:**
- Modify: `electron/ipc-handlers.js`（L2727 weblogin 区后追加）

**Interfaces:** Produces 通道 `sticky:list/create/update/delete/archiveToProject/toAffair`（preload 通用 invoke，零 preload 改动）

- [ ] **Step 1: 实现**（对齐 `weblogin:list` 的 fetchWithTimeout 代理模式）：

```js
// ── 便签（2.12）：六通道直代理 python /sticky 端点 ──
ipcMain.handle('sticky:list', async (_e, { view, limit, offset } = {}) => {
  const qs = new URLSearchParams({ view: view || 'inbox', limit: String(limit ?? 50), offset: String(offset ?? 0) })
  const res = await fetchWithTimeout(`${pythonManager.getBaseUrl()}/sticky/notes?${qs}`)
  return res.json()
})
ipcMain.handle('sticky:create', async (_e, payload) => (await fetchWithTimeout(`${pythonManager.getBaseUrl()}/sticky/notes`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json())
ipcMain.handle('sticky:update', async (_e, { id, ...payload }) => (await fetchWithTimeout(`${pythonManager.getBaseUrl()}/sticky/notes/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json())
ipcMain.handle('sticky:delete', async (_e, { id }) => (await fetchWithTimeout(`${pythonManager.getBaseUrl()}/sticky/notes/${encodeURIComponent(id)}`, { method: 'DELETE' })).json())
ipcMain.handle('sticky:archiveToProject', async (_e, { id, ...payload }) => (await fetchWithTimeout(`${pythonManager.getBaseUrl()}/sticky/notes/${encodeURIComponent(id)}/archive`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json())
ipcMain.handle('sticky:toAffair', async (_e, { id, ...payload }) => (await fetchWithTimeout(`${pythonManager.getBaseUrl()}/sticky/notes/${encodeURIComponent(id)}/to-affair`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json())
```

- [ ] **Step 2: 验证**——`node -c electron/ipc-handlers.js`（语法）+ `node scripts/orphan_channel_audit.js`（此时 A/B 组应出现 6 通道无前端消费——预期中，Task 4 消化）

- [ ] **Step 3: Checkpoint**——用户手动提交

### Task 4: 前端——StickyDrawer + NavTabs 入口 + i18n

**Files:**
- Create: `src/features/sticky/StickyDrawer.tsx`、`src/features/sticky/StickyDrawer.module.css`、`src/features/sticky/StickyDrawer.test.tsx`、`src/features/sticky/types.ts`
- Modify: `src/components/layout/NavTabs.tsx`（TABS 后追加动作按钮）、`src/App.tsx`（挂载 + Ctrl+Shift+N）、`src/i18n/locales/zh-CN.json` / `en-US.json`

**Interfaces:**
- Consumes: Task 3 六通道（经 `window.electronAPI.invoke`）、`useDrawerWidth`、`useToast`、`useConfirm`
- Produces: `StickyDrawer` 无 props（内部自含 open state，监听 window 事件 `sticky:toggle-drawer`）

- [ ] **Step 1: 类型**（`src/features/sticky/types.ts`）：

```ts
export interface StickyNote {
  id: string; content: string; color: 'yellow' | 'red' | 'blue' | 'green' | 'purple' | null
  project_id: string | null; archived_at: string | null
  created_at: string; updated_at: string
}
export type StickyView = 'inbox' | 'archived'
```

- [ ] **Step 2: StickyDrawer 组件**——结构按 spec §4 线框：头部（标题+✕）/输入区（textarea auto 高度 2 行，Enter 存 Shift+Enter 换行，空禁用存钮）/二分区 toggle（收件箱|已归档，对齐 AffairsTab 按钮形态）/卡片列表（`usePagedList<StickyNote>({channel:'sticky:list', itemsKey:'notes', params:{view}})`，卡片左缘色条 `borderLeft: 4px solid var(--sticky-<color>)`，操作组：复制（`navigator.clipboard.writeText`）/编辑（inline textarea+色板圆点行，单选即调 sticky:update）/归档（项目下拉选择器，数据经现有项目列表通道 `knowledge:listProjects` limit=0）/转事务（同选择器+可选截止日 input type=date）/删除（useConfirm））。open 状态：本地 useState + `window.addEventListener('sticky:toggle-drawer')`；open 时 `ref.focus()` 落输入框；列表打开即拉取（usePagedList enabled=open）。
- [ ] **Step 3: NavTabs 动作按钮**——TABS map 之后追加（不在 TABS 数组内，避免 activeNav 语义）：

```tsx
<button className={styles.tab} onClick={() => window.dispatchEvent(new Event('sticky:toggle-drawer'))}>
  <StickyNote className={styles.icon} size={18} strokeWidth={1.8} />
  <span className={styles.label}>{t('nav.stickyNotes')}</span>
</button>
```

- [ ] **Step 4: App.tsx 挂载 + 快捷键**——`<StickyDrawer />` 置于根组件层（SystemAlerts 同级）；全局 keydown：

```tsx
useEffect(() => {
  const onKey = (e: KeyboardEvent) => {
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'n') {
      e.preventDefault()
      window.dispatchEvent(new Event('sticky:toggle-drawer'))
    }
  }
  window.addEventListener('keydown', onKey)
  return () => window.removeEventListener('keydown', onKey)
}, [])
```

- [ ] **Step 5: i18n 双语**——`nav.stickyNotes`（便签/Stickies）+ `sticky.*` 全套（inputPlaceholder「记一笔…」、tabInbox、tabArchived、copy、edit、archive、toAffair、delete、archiveToast、affairToast、emptyInbox「随手记一笔，之后可归档进项目或转成待办」、emptyArchived、colorNone、contentRequired 等；zh-CN 与 en-US 成对）
- [ ] **Step 6: 验证**——`npx tsc --noEmit` + `npm run build` + `node scripts/orphan_channel_audit.js` 四组 0
- [ ] **Step 7: Checkpoint**——用户手动提交

### Task 5: 组件测试 + 全链验收

**Files:**
- Test: `src/features/sticky/StickyDrawer.test.tsx`

- [ ] **Step 1: vitest 用例**（mock `window.electronAPI.invoke`，参考现有 `ClipboardImageName.test.ts` 同目录模式）：打开抽屉拉列表渲染卡片；Enter 创建后列表刷新输入框清空；归档成功 toast 且卡片移出收件箱；归档 409 显示错误 toast；转事务成功 toast 且卡片原地；删除经 confirm 后移除；Ctrl+Shift+N toggle。
- [ ] **Step 2: 全量验证**——`python -m pytest python/tests/ -v` + `npx vitest run` + `npx tsc --noEmit` + `npm run build` + `node scripts/orphan_channel_audit.js` 全绿
- [ ] **Step 3: 运行验收**——`npm run dev`：任意页调起抽屉、任务页内调起不打断任务、归档后素材 Tab 出现解析中 md、双语切换正常
- [ ] **Step 4: 文档回写**——spec §6 对齐清单核销；最终提交由用户手动完成
