# 纪要中心（2.14）实施计划

> **For agentic workers:** 本计划由会话内直接执行（inline），每任务完成即跑该任务验证，全量验证在 T8。

**Goal:** 纪要从素材面板迁出为项目详情页独立 Tab（待处理草稿区 + 已归档时间线 + 详情浮层 + 素材 toggle）；confirm 终态从删行改为置 archived；正文默认入库素材可退出、事后可加入/移出。

**Architecture:** 后端 3 新端点 + confirm/PATCH 微改（全部在既有 routers/meeting_notes.py 与 db/database.py 内增量）；前端新增 NotesTab 组件承载向导挂载与归档列表，ProjectDetailPage/KnowledgePage 死代码全链清理。

**Tech Stack:** FastAPI + SQLite（现状增量）；React 18 + TS + react-markdown（已有依赖）；ipcMain.handle 代理（照抄现有 meetingNotes 模式）。

**Spec:** docs/2.14meeting-notes-hub-design.md（2026-10-28 用户批准）

## Global Constraints

- 禁止 git 操作（用户手动提交）
- Python：`C:/Users/Kelvin/.conda/envs/inkcube/python.exe`；测试 `pytest python/tests/ -q`
- 前端命令走 `node node_modules/vitest/vitest.mjs run`、`node node_modules/typescript/bin/tsc --noEmit`、`C:\nvm4w\nodejs\npm.cmd run build`
- 孤儿通道零容忍：交付前 `node scripts/orphan_channel_audit.js` 五组全 0
- 分页契约对齐 0.1 §16：`limit/offset/has_more/total`，`limit=0` 全量
- i18n zh-CN/en-US 双语同步；无硬编码文案
- 删除链完整：删 headerAction/draftBanner 时同步清 CSS、prop、调用方

---

### Task 1: DB 层——archived 枚举 + 归档分页查询

**Files:**
- Modify: `python/db/database.py`（`_MEETING_NOTE_STATUSES` L2825、新增 `list_meeting_notes_archived` 于 `list_meeting_notes_by_status` 后）
- Test: `python/tests/test_meeting_notes.py`（追加）

**Interfaces（产出）:**
- `list_meeting_notes_archived(self, project_id: str, limit: int = 50, offset: int = 0) -> dict`
  返回 `{"notes": [...], "limit": limit, "offset": offset, "has_more": bool, "total": int}`
  排序 `ORDER BY COALESCE(occurred_at, date(created_at)) DESC, rowid DESC`；`limit=0` 全量且 `has_more=False`
- `_MEETING_NOTE_STATUSES = ("draft", "structuring", "structured", "archived")`

**Steps:**
1. 改枚举常量（注释注明 2.14 终态语义）
2. 写查询函数（对齐 `list_pending_meeting_notes` 的 decode/排序风格 + 分页契约）
3. 追加测试：
   - `test_archived_status_in_whitelist`：update → archived 合法；bogus 仍 ValueError
   - `test_list_archived_pagination_and_order`：3 行 archived（occurred_at 乱序 + 一行 null occurred_at）+ 1 行 draft；默认 limit=2 → notes 按拜访日期倒序、has_more=True、total=3；offset=2 → 最后 1 行 has_more=False；limit=0 → 3 行全量；draft 不在列
   - `test_list_pending_excludes_archived`：archived 行不出现在 list_pending
   - `test_reset_interrupted_and_fetch_active_ignore_archived`：archived 行不被清扫改写、不进 fetch_active
4. 验证：`pytest python/tests/test_meeting_notes.py python/tests/test_meeting_notes_service.py -q` 全绿（存量用例 zero 适配——archived 是纯新增）

### Task 2: 路由——confirm 置 archived + save_to_materials + PATCH 守卫

**Files:**
- Modify: `python/routers/meeting_notes.py`（NoteConfirm L53、confirm_note L174-239、update_note L88-102）
- Test: `python/tests/test_meeting_notes.py`（适配 2 个存量用例 + 新增）

**Interfaces:**
- `NoteConfirm` 加 `save_to_materials: bool = True`
- confirm 步骤 1 前置：`if req.save_to_materials and not file_id:` 才上传；步骤 3 删行 → `update_meeting_note(note_id, {"status": "archived"})`；返回体加 `"archived": True`
- PATCH：行 status == "archived" → 409 "note archived"

**幂等裁决（spec §3）**：重试时以首次 refs 为准——`file_id` 已存在（含非 null）则本次 `save_to_materials` 被忽略，防「先存后移出素材再重试又传回」。

**Steps:**
1. NoteConfirm 加字段
2. confirm 步骤 1/3 改造（docstring 同步：终态 archived）
3. update_note 加 archived 409 守卫
4. 测试适配：
   - `test_route_confirm_guards_and_idempotent_resume`：末段断言「删行」改「status=archived + archived=True」；refs 断言不变
   - `test_route_confirm_all_unselected_completes`：同上（archived 而非 None）
5. 新增：
   - `test_route_confirm_without_materials_skips_upload`：save_to_materials=False → upload 零调用、file_id null 落 refs、archived=True
   - `test_route_confirm_retry_keeps_first_materials_choice`：首次 True 落 file_id → 移出素材模拟（手工置 refs.file_id=None）→ 重试带 save_to_materials=True → 不重传（以 refs 为准需重读行——按实现：步骤 1 条件 `req.save_to_materials and not file_id`，file_id 从**请求时行内 refs** 读，行内已 null 会重传！）——**修正裁决**：幂等锚点改为「file_id 曾存在过即不再传」不可行（无历史记录）；实际语义 = 重试时以**当前载荷**为准（save_to_materials=True 且 refs null → 补传是合理补救，与 toggle 加入素材同义）。最终裁决：**去掉「首次为准」特判，`if req.save_to_materials and not file_id` 直判**，spec §3 对应行同步修订
   - `test_route_patch_archived_409`
6. 修订 spec 2.14 §3 confirm 变更第 1 点（删「首次 refs 为准」句，改「重试时以当前载荷为准：save_to_materials=True 且 refs 无 file_id → 补传（与事后 toggle 加入素材同义补救）」）
7. 验证：`pytest python/tests/test_meeting_notes.py -q`

### Task 3: 路由——materialize / dematerialize

**Files:**
- Modify: `python/routers/meeting_notes.py`（confirm_note 之后追加两端点）
- Test: `python/tests/test_meeting_notes.py`

**Interfaces:**
- `POST /projects/{p}/notes/{id}/materialize`：archived 守卫 409 → try_acquire_confirm 占坑（与 DELETE/confirm 同坑防 refs 并发写）→ refs.file_id 存在幂等返回 → draft_md 缺/content_md 空 422 → `_upload_core` 上传（文件名规则同 confirm L202-203）→ refs 落库 → `{ok, file_id}`
- `DELETE /projects/{p}/notes/{id}/material`：archived 守卫 → 占坑 → refs.file_id null 幂等 ok → `cleanup_file_data(remove_file_record=True)`（局部 import，模式同 `_requeue_note_images`）→ refs.file_id 置 null → `{ok: True}`

**Steps:**
1. 实现两端点（模块头 docstring 补两行端点清单）
2. 测试（archived 行构造：`update_meeting_note(nid, {"status": "structured", "draft_md": json.dumps({"content_md": "# 正文", "action_items": []})})` → 再 update status archived）：
   - `test_route_materialize_upload_and_idempotent`：mock `_upload_core` → 首调返回 file_id + refs 落库；二调不重传直接返回同 id
   - `test_route_materialize_guards`：draft 行 409；无 draft_md 422
   - `test_route_dematerialize_cleanup_and_idempotent`：patch `knowledge.file_cleanup.cleanup_file_data`（async fake 记参）+ `knowledge.vector_store.VectorStore`（lambda p: object()）→ file_id 入参、remove_file_record=True、refs.file_id 置 null；二调不触发 cleanup
   - `test_route_material_guards`：draft 行 material/dematerialize 均 409；confirm 占坑中 409
3. 验证：`pytest python/tests/test_meeting_notes.py -q`

### Task 4: IPC 三通道

**Files:**
- Modify: `electron/ipc-handlers.js`（照抄 meetingNotes 现有注册模式）

**Steps:**
1. Grep 定位 `meetingNotes:listPending` 注册处，同款追加三行：`meetingNotes:listArchived`（payload `{projectId, limit, offset}`）、`meetingNotes:materialize`（`{projectId, id}`）、`meetingNotes:dematerialize`（`{projectId, id}`）
2. 验证：`node scripts/orphan_channel_audit.js`（B 组临时非零——前端尚未消费，T6 收口归零；此时仅跑语法检查 `node --check electron/ipc-handlers.js`）

### Task 5: NoteWizardDrawer 确认步勾选框

**Files:**
- Modify: `src/features/notes/NoteWizardDrawer.tsx`（确认步 state + UI + confirm 载荷）
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`（meetingNotes 加 `saveToMaterials` / `saveToMaterialsHint`）
- Test: `tests/NoteWizardDrawer.test.tsx`

**Steps:**
1. state：`const [saveToMaterials, setSaveToMaterials] = useState(true)`；confirm 载荷加 `saveToMaterials`
2. 确认步 UI：行动项区下方 checkbox（`data-testid="note-save-materials"`）+ label（`meetingNotes.saveToMaterials`）+ hint 行（`meetingNotes.saveToMaterialsHint`，`s.confirmHint` 样式类复用或新 class）
3. 测试适配：`确认步编辑保存` 用例载荷断言加 `saveToMaterials: true`；新增 `不勾存素材：载荷 saveToMaterials=false`（click 取消 → save → 断言）
4. 验证：`node node_modules/vitest/vitest.mjs run tests/NoteWizardDrawer.test.tsx`

### Task 6: NotesTab 组件 + 测试

**Files:**
- Create: `src/features/notes/NotesTab.tsx`、`src/features/notes/NotesTab.module.css`
- Test: `tests/NotesTab.test.tsx`

**Interfaces:**
- `export function NotesTab({ projectId }: { projectId: string })`
- 内部挂载 `NoteWizardDrawer`（resumeNoteId state）；新建/行点击 → openWizard
- 数据：`meetingNotes:listPending {projectId}` + `meetingNotes:listArchived {projectId, limit: 50, offset}`；toggle/delete 后重拉对应列表
- 详情浮层：行内展开或覆盖层（`data-testid="note-detail"`），react-markdown 渲染 `draft_md.content_md` + 行动项只读清单 + toggle 按钮
- testid 约定：`notes-pending` 区、`notes-archived` 区、`note-row-{id}`、`note-toggle-material-{id}`、`note-delete-{id}`、`note-load-more`、`note-detail`

**Steps:**
1. 组件骨架：载入（Promise.all 并行）、待处理区（N=0 不渲染）、归档区（空态文案）、加载更多按钮（has_more）
2. 行操作：放弃（useConfirm → delete → 重拉 pending）、toggle（未入→materialize 直调；已入→useConfirm → dematerialize）、删除归档（useConfirm 文案「素材与事务保留」→ delete → 重拉 archived）、详情浮层
3. CSS：对齐项目列表行风格（参考 AffairsTab/列表行样式密度）
4. 测试（invokeMock 模式同 NoteWizardDrawer.test）：
   - 分区渲染：草稿 1 + 归档 2 → 两区行数；无草稿 → 待处理区不渲染；归档空 → 空态文案
   - 点待处理行 → 向导抽屉出现（resumeNoteId 生效——mock listPending 返回 draft 行）
   - toggle 未入素材 → invoke materialize 载荷 `{projectId, id}`；已入素材 → confirmMock 后 dematerialize
   - 删除归档 → confirmMock → invoke delete → 行消失
   - 加载更多：首响应 has_more=True → 点按钮 → 第二次 listArchived 调用 offset=50、列表追加
   - 详情浮层：点归档行 → note-detail 出现且含正文文本
5. 验证：`node node_modules/vitest/vitest.mjs run tests/NotesTab.test.tsx`

### Task 7: ProjectDetailPage 集成 + 死代码清理

**Files:**
- Modify: `src/features/project/ProjectDetailPage.tsx`（TABS/TabKey、tab content、删 L66-83 状态、删 L544-566、删 L627-634 挂载、清 import）
- Modify: `src/features/knowledge/KnowledgePage.tsx`（headerAction prop L32-33/53/60/539 全删）
- Modify: `src/features/project/ProjectDetailPage.module.css`（noteBtn/draftBanner/materialsBar 删）
- Modify: `src/features/knowledge/KnowledgePage.module.css`（headerAction 删）
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`（`meetingNotes.draftBanner` 删；`project.tab.notes` 加）

**Steps:**
1. TabKey `'materials' | 'wiki' | 'affairs' | 'output' | 'notes'`；TABS affairs 后插 `{ key: 'notes', labelKey: 'project.tab.notes', Icon: ClipboardList }`（ClipboardList import 保留自 noteBtn 迁用）
2. `{activeTab === 'notes' && <NotesTab key={project.id} projectId={project.id} />}`（materialsWrap 内纪要相关全删）
3. 删 KnowledgePage headerAction 三处 + 两文件 CSS 类
4. i18n 两键增删（四个 locale 文件操作一次到位）
5. 验证：`node node_modules/typescript/bin/tsc --noEmit` 零错（未消费 prop 删除不引错）；`node scripts/orphan_channel_audit.js` 五组 0（三新通道已被 NotesTab 消费）

### Task 8: i18n notesHub 段 + 文档对齐 + 全量验证

**Files:**
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`（notesHub 段：newButton/pendingTitle/archivedTitle/emptyArchived/statusDraft/statusStructuring/statusStructured/inMaterials/notInMaterials/delete/loadMore/detailTitle/actionItems/addToMaterials/removeFromMaterials/addToMaterialsHint/removeConfirmTitle/removeConfirmMessage/deleteConfirmTitle/deleteConfirmMessage/materializeFailed/dematerializeFailed/deleteFailed/loadFailed）
- Modify: `docs/2.13meeting-notes-design.md`（confirm 终态改 archived + save_to_materials、偏差 18 指向 2.14）
- Modify: `docs/3.0project-lifecycle-design.md`（级联条目补注含 archived）
- Modify: `docs/2.14meeting-notes-hub-design.md`（偏差回写区）

**Steps:**
1. notesHub 双语段（zh-CN/en-US 同步）
2. 三份文档对齐修订
3. 全量验证（顺序）：`pytest python/tests/ -q` → `node node_modules/vitest/vitest.mjs run` → `node node_modules/typescript/bin/tsc --noEmit` → `node scripts/orphan_channel_audit.js` → `C:\nvm4w\nodejs\npm.cmd run build`
4. 汇报（附偏差记录）
