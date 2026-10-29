# 拜访/会议纪要（Meeting Notes）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 项目内结构化素材创建器——粘贴转写稿/手记 + 图片 → LLM 结构化（正文+行动项）→ 人工确认 → 行动项建 material 事务、正文落素材 md；`meeting_notes` 行为向导中间态，终态自清理。

**Architecture:** 新表 `meeting_notes`（行=draft→structuring→structured 中间态，confirm 分步落 `saved_refs` 幂等重试，完整即删行）+ `meeting_notes_service.py`（LLM 结构化，模式对齐 `wiki/profile_service.py`：run_in_executor 后台 + LLMClient.chat_full）+ Activity kind=`note_structuring`（FunctionSource）+ router 六端点 + IPC 六通道 + `NoteWizardDrawer` 两步向导（素材 Tab 入口）。正文落盘复用 `_upload_core`（同便签归档路径）。

**Tech Stack:** 同便签计划 + `llm.LLMClient` / `llm.routing.get_task_settings` / `llm.usage.UsageContext`

**Spec:** [docs/2.13meeting-notes-design.md](../../2.13meeting-notes-design.md)（执行者须同读；实施前置：便签计划已完成——复用其 `_upload_core` 复用经验）

## Global Constraints

- 禁止任何 git 操作；checkpoint 处用户手动提交
- i18n 双语成对；分页/has_more 契约；孤儿通道审计四组 0
- 结构化 LLM 走 `llm_routing` 路由键 `'meeting_note'`（settings 未配置则全局默认），usage source=`meeting_note`
- 反幻觉纪律入 prompt：仅基于输入材料；无支撑推断标「待确认」；输出 JSON schema `{content_md, action_items:[{title}]}`
- confirm 幂等：分步落 `saved_refs`，重试只补未完成步骤，杜绝事务重复创建
- 转写外包原则：不做现场收音/转写模型；图片走现有素材上传链

---

### Task 1: DB 层——meeting_notes 表与状态机函数

**Files:**
- Modify: `python/db/database.py`（DDL 区 sticky_notes 后；CRUD 加同类区）；`python/routers/knowledge.py` delete_project 端点（`detach_sticky_notes_for_project` 同位置加 `db.delete_meeting_notes_for_project(project_id)`）
- Test: `python/tests/test_meeting_notes.py`

**Interfaces:**
- Produces:
  - `create_meeting_note(project_id, title, occurred_at, raw_text, image_file_ids: list[str]) -> dict`
  - `get_meeting_note(note_id) -> dict | None`
  - `list_pending_meeting_notes(project_id) -> list[dict]`（status IN draft,structuring）
  - `update_meeting_note(note_id, fields: dict) -> dict | None`（白名单：title/occurred_at/raw_text/image_file_ids/draft_md/saved_refs/last_error/status）
  - `delete_meeting_note(note_id) -> bool`
  - `delete_meeting_notes_for_project(project_id) -> int`
  - `reset_interrupted_meeting_notes() -> int`（启动清扫：structuring → draft + last_error='interrupted'）

- [ ] **Step 1: 失败测试**：建行默认 draft；status 流转经 update 白名单；`reset_interrupted_meeting_notes` 只动 structuring 行且幂等（二次调用 0）；项目级联删行；image_file_ids JSON 编解码对称。
- [ ] **Step 2: 确认失败**（pytest）
- [ ] **Step 3: 实现**：

```sql
CREATE TABLE IF NOT EXISTS meeting_notes (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL,
    title          TEXT NOT NULL,
    occurred_at    TEXT,
    raw_text       TEXT,
    image_file_ids TEXT,
    draft_md       TEXT,
    saved_refs     TEXT,
    last_error     TEXT,
    status         TEXT NOT NULL DEFAULT 'draft',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meeting_notes_project ON meeting_notes(project_id, status);
```

CRUD 模式同便签 Task 1（_uuid/_now/dict(row)）；image_file_ids 写入 `json.dumps(list)`、读取 JSON 解码（容错 decay 为 []，对齐 list_due_phase_tasks 的解码容错）。`reset_interrupted_meeting_notes`：

```python
def reset_interrupted_meeting_notes(self) -> int:
    cur = self.conn.execute(
        "UPDATE meeting_notes SET status='draft', last_error='interrupted', updated_at=?"
        " WHERE status='structuring'", (self._now(),))
    self.conn.commit()
    return cur.rowcount
```

- [ ] **Step 4: 通过 + 全量回归**
- [ ] **Step 5: Checkpoint**——用户手动提交

### Task 2: 结构化 service + Activity 注册

**Files:**
- Create: `python/meeting_notes_service.py`
- Modify: `python/routers/activities.py`（注册 FunctionSource kind=`note_structuring`，fetch 返回全项目 status='structuring' 行，字段 entity_type/entity_id/project_id/entity_title/progress_text）；`python/main.py` 启动恢复序列（现有 structuring 残留清扫处，追加 `db.reset_interrupted_meeting_notes()`）
- Test: `python/tests/test_meeting_notes_service.py`

**Interfaces:**
- Consumes: `llm.LLMClient` / `llm.routing.get_task_settings` / `llm.usage.UsageContext, push_context`（对齐 profile_service L27/L166-187 模式）
- Produces: `structure_meeting_note_sync(db, note_id: str, settings: dict) -> None`（后台同步函数：取材料 → LLM → 落 draft_md/status；失败回 draft+last_error）

- [ ] **Step 1: 失败测试**（monkeypatch LLMClient 返回固定 JSON）：成功 → status=structured 且 draft_md 为合法 JSON；LLM 返回坏 JSON → status 回 draft + last_error 记录（LLM_BAD_RESPONSE）；LLM 抛异常 → last_error 记录异常码；材料为空 → 前置抛 ValueError 不调 LLM。
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**（骨架）：

```python
"""python/meeting_notes_service.py — 纪要结构化（2.13 §3 结构化路径）。"""
import asyncio, json, logging
logger = logging.getLogger(__name__)

_PROMPT_TMPL = """你是招商/投资助理。基于以下拜访/会议材料，输出 JSON：
{{"content_md": "纪要正文(markdown，含 时间/参与人/要点/结论 段落)", "action_items": ["行动项标题", ...]}}
纪律：仅基于材料；无材料支撑的推断在条目前加「待确认：」；无行动项输出空数组。禁止编造。
【材料】\n{text}"""

def _collect_materials(db, note: dict) -> str:
    parts = []
    if note.get("raw_text"): parts.append(note["raw_text"])
    for fid in note.get("image_file_ids") or []:
        f = db.get_file(fid)   # 复用现有 db 方法；缺解析文本则跳过
        if f and f.get("extracted_text"): parts.append(f[" extracted_text"])
    return "\n\n".join(parts)

def structure_meeting_note_sync(db, note_id: str, settings: dict) -> None:
    note = db.get_meeting_note(note_id)
    if not note: return
    text = _collect_materials(db, note)
    if not text.strip():
        db.update_meeting_note(note_id, {"status": "draft", "last_error": "NO_MATERIAL"}); return
    try:
        from llm import LLMClient
        from llm.routing import get_task_settings
        from llm.usage import UsageContext, push_context
        effective = get_task_settings(settings, "meeting_note")
        messages = [{"role": "user", "content": _PROMPT_TMPL.format(text=text[:30000])}]
        with push_context(UsageContext(source="meeting_note", project_id=note["project_id"])):
            raw = asyncio.run(LLMClient(effective).chat_full(messages))["content"]
        parsed = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])  # 容忍围栏
        if not isinstance(parsed.get("action_items"), list): raise ValueError("bad schema")
        db.update_meeting_note(note_id, {
            "draft_md": json.dumps(parsed, ensure_ascii=False),
            "status": "structured", "last_error": None})
    except Exception as e:
        logger.warning("meeting note structure failed %s: %s", note_id, e)
        db.update_meeting_note(note_id, {"status": "draft", "last_error": "LLM_FAILED"})
```

注：`_collect_materials` 中字段名以 `db.get_file` 实际返回的解析文本字段为准（实施时读 files 表取数函数对齐）；Activity 注册：

```python
from activities_registry import FunctionSource
import meeting_notes_service
activities_registry.register(FunctionSource("note_structuring",
    lambda: [dict(entity_type="note_structuring", entity_id=r["id"], project_id=r["project_id"],
                  entity_title=r["title"], progress_text="正在整理纪要…")
             for r in db.list_meeting_notes_by_status("structuring")]))
```

（`list_meeting_notes_by_status` 为 Task 1 补充的跨项目查询，测试覆盖。）

- [ ] **Step 4: 通过 + 全量回归**
- [ ] **Step 5: Checkpoint**——用户手动提交

### Task 3: Python 路由——六端点

**Files:**
- Create: `python/routers/meeting_notes.py`
- Modify: `python/main.py`（include_router）
- Test: `python/tests/test_meeting_notes.py`（追加路由层）

**Interfaces:**
- Consumes: Task 1 db 方法、Task 2 service、`_upload_core`、`create_phase_task`
- Produces: `POST/GET /projects/{p}/notes`、`PATCH/DELETE /projects/{p}/notes/{id}`、`POST .../structure`（202 受理）、`POST .../confirm`

- [ ] **Step 1: 失败测试**关键用例：创建草稿（title 默认）；structure 前置校验（材料空 422 / 非 draft 409 / 受理返回 202 且 status=structuring，service 经 run_in_executor 被 monkeypatch 断言被调）；confirm 分步幂等——monkeypatch `_upload_core` 与 `create_phase_task`，首次 confirm 第二个事务 fake 抛异常 → 返回 failed_tasks 含该项且行留存 saved_refs 已记 file_id+首事务；重试 → 不再重写 md（_upload_core 未再被调）不再重建首事务（create_phase_task 仅被调一次补第二项）→ 完成后行已删；confirm 非 structured 409。
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**（骨架）：

```python
"""python/routers/meeting_notes.py — 纪要（2.13 §3）。"""
from __future__ import annotations
import base64, json, logging
from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import deps
from deps import ensure_ready

logger = logging.getLogger(__name__)
router = APIRouter()

class NoteCreate(BaseModel):
    title: str | None = None
    occurred_at: str | None = None
    raw_text: str | None = None
    image_file_ids: list[str] | None = None

class NoteStructure(BaseModel):
    pass

class ConfirmItem(BaseModel):
    title: str
    selected: bool = True
    due_date: str | None = None

class NoteConfirm(BaseModel):
    content_md: str
    action_items: list[ConfirmItem]

def _db(): return deps.db
def _get_owned(note_id: str, project_id: str):
    n = _db().get_meeting_note(note_id)
    if not n or n["project_id"] != project_id: raise HTTPException(404, "note not found")
    return n

@router.post("/projects/{project_id}/notes")
async def create_note(project_id: str, req: NoteCreate):
    proj = _db().get_project(project_id)
    if not proj or proj.get("kind") != "project": raise HTTPException(404, "project not found")
    title = (req.title or "").strip() or f"拜访-{date.today().strftime('%Y%m%d')}"
    return {"note": _db().create_meeting_note(
        project_id, title, req.occurred_at, req.raw_text, req.image_file_ids or [])}

@router.get("/projects/{project_id}/notes")
async def list_pending(project_id: str):
    return {"notes": _db().list_pending_meeting_notes(project_id)}

@router.patch("/projects/{project_id}/notes/{note_id}")
async def update_note(project_id: str, note_id: str, req: dict):
    _get_owned(note_id, project_id)
    allowed = {k: v for k, v in (req or {}).items()
               if k in ("title", "occurred_at", "raw_text", "image_file_ids")}
    return {"note": _db().update_meeting_note(note_id, allowed)}

@router.delete("/projects/{project_id}/notes/{note_id}")
async def delete_note(project_id: str, note_id: str):
    _get_owned(note_id, project_id)
    _db().delete_meeting_note(note_id)
    return {"ok": True}

@router.post("/projects/{project_id}/notes/{note_id}/structure")
async def structure_note(project_id: str, note_id: str):
    """异步受理（模式对齐 profile/extract：run_in_executor + 行级 status 守卫）。"""
    ensure_ready("task")
    note = _get_owned(note_id, project_id)
    if note["status"] != "draft": raise HTTPException(409, "note not in draft")
    db = _db()
    _db().update_meeting_note(note_id, {"status": "structuring"})
    settings = db.get_settings()
    import meeting_notes_service as svc
    loop = __import__("asyncio").get_running_loop()
    loop.run_in_executor(None, lambda: svc.structure_meeting_note_sync(db, note_id, settings))
    return {"accepted": True}

@router.post("/projects/{project_id}/notes/{note_id}/confirm")
async def confirm_note(project_id: str, note_id: str, req: NoteConfirm):
    """分步落引用幂等：md → 事务（逐条记 saved_refs）→ 完整即删行。"""
    ensure_ready("file_parsing")
    note = _get_owned(note_id, project_id)
    if note["status"] != "structured": raise HTTPException(409, "note not structured")
    db = _db()
    refs = json.loads(note.get("saved_refs") or "{}")
    file_id, task_ids = refs.get("file_id"), refs.get("task_ids", [])
    failed: list[str] = []
    # 步骤 1：正文 md（saved_refs.file_id 已存在则跳过）
    if not file_id:
        from routers.knowledge import FileUpload, _upload_core
        fname = f"纪要-{(note.get('occurred_at') or date.today().isoformat()).replace('-', '')}-{note['title']}.md"
        result = await _upload_core(project_id, FileUpload(
            projectId=project_id, filename=fname, fileType="text/markdown",
            data=base64.b64encode(req.content_md.encode("utf-8")).decode()),
            db, deps.get_data_dir(), deps.worker)
        file_id = result["file_id"]
        db.update_meeting_note(note_id, {"saved_refs": json.dumps({"file_id": file_id, "task_ids": task_ids})})
    # 步骤 2：事务（saved_refs.task_ids 已含的跳过）
    from affairs_service import phase_of
    proj = db.get_project(project_id)
    phase = phase_of(proj.get("stage") or "none")
    for idx, item in enumerate(req.action_items):
        if not item.selected or idx in task_ids: continue
        try:
            task = db.create_phase_task(project_id=project_id, phase=phase, task_type="material",
                                        title=item.title, due_date=item.due_date)
            task_ids.append(idx)
            db.update_meeting_note(note_id, {"saved_refs": json.dumps(
                {"file_id": file_id, "task_ids": task_ids})})
            _ = task
        except Exception as e:
            logger.warning("confirm task failed note=%s idx=%s: %s", note_id, idx, e)
            failed.append(item.title)
    # 步骤 3：全部选中项完成 → 删行
    selected = [i for i, it in enumerate(req.action_items) if it.selected]
    if all(i in task_ids for i in selected):
        db.delete_meeting_note(note_id)
    return {"file_id": file_id, "task_count": len(task_ids), "failed_tasks": failed}
```

- [ ] **Step 4: 通过 + 全量回归**
- [ ] **Step 5: Checkpoint**——用户手动提交

### Task 4: IPC 通道

**Files:** Modify: `electron/ipc-handlers.js`（sticky 区后）

- [ ] **Step 1: 六通道**（同便签代理模式）：`meetingNotes:create|listPending|update|delete|structure|confirm` 分别 POST/GET/PATCH/DELETE `/projects/{pid}/notes...`；structure 与 confirm 为 POST。
- [ ] **Step 2: `node -c electron/ipc-handlers.js` + 审计（B 组 6 通道预期中，Task 5 消化）**
- [ ] **Step 3: Checkpoint**——用户手动提交

### Task 5: 前端——NoteWizardDrawer + 入口 + i18n

**Files:**
- Create: `src/features/notes/NoteWizardDrawer.tsx` + `.module.css` + `NoteWizardDrawer.test.tsx` + `types.ts`
- Modify: `src/features/project/ProjectDetailPage.tsx`（materials 分支：按钮 + 草稿提示条 + 抽屉挂载）、i18n 双语

**Interfaces:**
- Consumes: Task 4 六通道、`useActivity('note_structuring')`、`usePagedList`（files 解析态判断复用现有 files 心跳）、`_upload_core` 前端等价通道（图片上传走现有 `knowledge:upload`）
- Produces: 素材 Tab「新建纪要」按钮 + 草稿提示条 + 两步向导

- [ ] **Step 1: 类型 + 转写稿检测纯函数**（`types.ts`）：`MeetingNote` 接口 + `detectTranscript(text: string): boolean`（正则：行首 `发言人|说话人\d*|[\u4e00-\u9fa5A-Za-z0-9]{2,4}[:：]` 或 `[MM:SS`，命中 ≥2 行）——**附单测**（可放同文件导出，vitest 覆盖）。
- [ ] **Step 2: 输入步**：标题（默认「拜访-YYYYMMDD」）/日期/文本区（onPaste 后 detectTranscript → 提示条）/图片上传（现有素材上传通道 + 缩略图 + files 心跳就绪角标 ✓/⟳）/生成钮门控（图片全就绪 && (raw_text 非空 || 图片≥1)）+ 产出预期说明文案。提交链路：meetingNotes:create →（带图先 knowledge:upload 收集 file_ids）→ meetingNotes:update 补 image_file_ids → meetingNotes:structure。
- [ ] **Step 3: 生成中/确认步**：`useActivity('note_structuring')` 心跳驱动进度态；structured（轮询 listPending 或终态边沿重拉）自动进确认步：正文 textarea（初值 draft_md.content_md）+ 行动项清单（checkbox 默认勾选、「待确认：」前缀项默认不勾、title 可编辑、可选截止日）+ 保存钮（confirm；返回 failed_tasks 非空红字逐项列出 +「重试保存」只重发 confirm）+ 上一步（回输入步，status 已 structured 时后端 409——上一步仅查看禁改，或在 confirm 前禁用上一步；**裁决：确认步禁用「上一步」**，重新生成走「放弃草稿重建」）。
- [ ] **Step 4: 入口与草稿提示条**（ProjectDetailPage materials 分支）：按钮（ClipboardList 图标）挂载抽屉；挂载时 `meetingNotes:listPending` 拉计数，N>0 渲染提示条点击恢复（按 status 落步骤：draft→输入步，structuring→进度态，structured→确认步）。
- [ ] **Step 5: i18n 双语**（`meetingNotes.*` 全套：按钮/步进/占位/转写稿提示/图片理解中 n/m/生成门控/确认步标签/保存反馈/失败重试/草稿提示条）。
- [ ] **Step 6: 验证**——tsc + build + 审计四组 0。
- [ ] **Step 7: Checkpoint**——用户手动提交

### Task 6: 测试 + 全链验收

- [ ] **Step 1: vitest**（`NoteWizardDrawer.test.tsx`，mock invoke + Activity）：转写稿检测提示出现/不出现；生成门控（图片未就绪禁用）；确认步编辑行动项后保存载荷正确；failed_tasks 渲染重试钮；草稿提示条计数与恢复。
- [ ] **Step 2: 全量验证**——pytest + vitest + tsc + build + 审计全绿
- [ ] **Step 3: 运行验收**——`npm run dev` 全动线：粘贴转写稿 → 提示出现 → 生成 → 确认步改两笔 → 保存 → 素材 md + 事务双落位 → 今日待办可见；中途关抽屉草稿留存恢复；重启后 structuring 残留回 draft
- [ ] **Step 4: 文档回写**——spec §6 对齐清单核销（3.0 级联清单、2.10 usage source、0.0/0.1 登记）；最终提交由用户手动完成
