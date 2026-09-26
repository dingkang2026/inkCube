# Phase 18 提升计划：代码编辑 UX + 规划深度 + 用户体验增强

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 task-agent 综合评分从 ~84.5 分提升到 ~87 分，通过 inline diff 展示、计划执行进度追踪、错误恢复增强等 UX 改进。

**Architecture:** 后端 file_ops 返回 unified diff 格式变更摘要 → SSE 事件流新增 `diff` 类型 → 前端 ToolCard 内渲染红绿 diff 视图。规划阶段新增结构化计划解析 + 进度追踪事件。

**Tech Stack:** Python (difflib) / TypeScript+React / SSE / Electron IPC

**Spec:** `../../1.1task-agent-design-unified.md` §15.3

## Global Constraints

- 跨平台兼容（Windows/macOS/Linux）
- 中/英双语支持（i18n）
- 日志管理要求（logger 而非 print）
- 有文档指导，先同步文档后再执行

---

## File Structure

| 操作 | 文件 | 职责 |
|------|------|------|
| Modify | `python/tools/file_ops.py` | 编辑操作返回 unified diff |
| Modify | `python/agent/task_agent.py` | 结构化计划解析 + diff 事件传递 + 进度追踪 |
| Modify | `src/types.ts` | 扩展 StreamStep 类型（diff / plan_progress） |
| Modify | `src/features/task/useTaskChat.ts` | 处理新 SSE 事件类型 |
| Modify | `src/features/task/TaskMessageList.tsx` | DiffCard 组件 + PlanProgress 组件 |
| Modify | `src/features/task/TaskMessageList.module.css` | Diff 样式 + 进度样式 |
| Modify | `../../1.1task-agent-design-unified.md` | 更新文档反映实施结果 |

---

### Task 1: file_ops 返回 unified diff

**Files:**
- Modify: `python/tools/file_ops.py:537-662`（write / search_replace / apply_diff 的 execute 分支）

**Interfaces:**
- Consumes: 无新依赖
- Produces: `execute()` 返回值新增 `diff` 字段（unified diff 字符串，仅编辑操作）

- [ ] **Step 1: 添加 diff 生成辅助方法**

在 `FileOpsTool` 类中（`_create_backup` 方法之后）添加：

```python
@staticmethod
def _generate_unified_diff(old_text: str, new_text: str, path: str, n: int = 3) -> str:
    """Generate unified diff between old and new text.
    
    Returns a compact diff string suitable for frontend display.
    Limited to 2000 chars to avoid bloating SSE events.
    """
    import difflib
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}", tofile=f"b/{path}",
        n=n, lineterm="",
    )
    diff_text = "".join(diff_lines)
    # Truncate if too large
    if len(diff_text) > 2000:
        diff_text = diff_text[:2000] + "\n... [diff truncated]"
    return diff_text
```

- [ ] **Step 2: 在 write 操作中返回 diff**

在 `execute()` 方法的 `action == "write"` 分支中，写入前读取旧内容，写入后生成 diff：

```python
elif action == "write":
    quota_err = self._check_write_quota(len(content.encode("utf-8")))
    if quota_err:
        return json.dumps({"error": quota_err}, ensure_ascii=False)
    # Read old content for diff generation
    old_text = ""
    if target.exists():
        try:
            old_text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    # Auto-backup before overwriting
    self._create_backup(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Syntax check for .py files
    if target.suffix == ".py" or path.endswith(".py"):
        syntax_err = self._check_python_syntax(content)
        if syntax_err:
            return json.dumps(
                {"error": f"Python 语法错误，已拒绝写入: {syntax_err}", "path": path},
                ensure_ascii=False,
            )
    target.write_text(content, encoding="utf-8")
    self._cached_size += len(content.encode("utf-8"))
    resp: dict = {"ok": True, "path": path, "action": "write"}
    if old_text:
        resp["diff"] = self._generate_unified_diff(old_text, content, path)
    return json.dumps(resp, ensure_ascii=False)
```

- [ ] **Step 3: 在 search_replace 操作中返回 diff**

在 `action == "search_replace"` 分支的返回值中追加 diff：

```python
resp: dict = {"ok": True, "path": path, "action": "search_replace"}
# Generate diff
resp["diff"] = self._generate_unified_diff(file_text, result_text, path)
if level >= 4:
    resp["warning"] = (
        f"通过 Level {level} 模糊匹配成功，请确认替换内容正确"
    )
return json.dumps(resp, ensure_ascii=False)
```

- [ ] **Step 4: 在 apply_diff 操作中返回 diff**

在 `action == "apply_diff"` 分支的返回值中追加 diff：

```python
return json.dumps({
    "ok": True,
    "path": path,
    "action": "apply_diff",
    "blocks_applied": blocks_applied,
    "diff": self._generate_unified_diff(file_text, result_text, path),
}, ensure_ascii=False)
```

- [ ] **Step 5: 语法验证**

运行: `python -c "import ast; ast.parse(open('python/tools/file_ops.py', encoding='utf-8').read()); print('OK')"`
Expected: OK

- [ ] **Step 6: Commit**

```bash
git add python/tools/file_ops.py
git commit -m "feat(file_ops): return unified diff in edit operations"
```

---

### Task 2: task_agent 传递 diff 到 SSE 事件

**Files:**
- Modify: `python/agent/task_agent.py:1100-1450`（`_handle_fc_iteration` 中的 observation 事件）

**Interfaces:**
- Consumes: file_ops 返回的 `diff` 字段（Task 1）
- Produces: `observation` SSE 事件 data 中新增 `diff` 字段

- [ ] **Step 1: 在 observation 事件中传递 diff**

在 `_handle_fc_iteration` 中，当工具执行完成后 yield observation 事件时，解析结果 JSON 并提取 diff 字段：

找到当前 yield observation 的代码位置，修改为：

```python
# After tool execution, yield observation
obs_data = {"content": result_str}
# Extract diff for frontend rendering
if tool_name == "file_ops":
    try:
        result_obj = json.loads(result_str)
        if isinstance(result_obj, dict) and result_obj.get("diff"):
            obs_data["diff"] = result_obj["diff"]
            obs_data["file_path"] = tool_params.get("path", "")
    except (json.JSONDecodeError, TypeError):
        pass
yield {"type": "observation", "data": obs_data}
```

- [ ] **Step 2: 语法验证**

运行: `python -c "import ast; ast.parse(open('python/agent/task_agent.py', encoding='utf-8').read()); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add python/agent/task_agent.py
git commit -m "feat(task_agent): pass diff field in observation SSE events"
```

---

### Task 3: 结构化计划解析 + 进度追踪

**Files:**
- Modify: `python/agent/task_agent.py:874-895`（`_run_planner` 方法）
- Modify: `python/agent/task_agent.py:1044-1057`（planner 调用处）

**Interfaces:**
- Consumes: planner LLM 输出
- Produces: 新增 `plan_progress` SSE 事件类型；`_run_planner` 返回结构化计划

- [ ] **Step 1: 添加结构化计划解析方法**

在 `_run_planner` 方法之后添加：

```python
@staticmethod
def _parse_plan_steps(plan_text: str) -> list[dict]:
    """Parse planner output into structured steps.
    
    Expected format:
        Plan:
        1. [步骤描述] — 涉及文件: [文件路径]
        2. ...
    
    Returns list of {"id": int, "desc": str, "files": list[str], "status": "pending"}
    """
    steps = []
    step_pattern = re.compile(r'^\s*(\d+)\.\s*(.+?)(?:\s*[—\-]\s*涉及文件:\s*(.+))?$', re.MULTILINE)
    for m in step_pattern.finditer(plan_text):
        step_id = int(m.group(1))
        desc = m.group(2).strip()
        files_str = m.group(3) or ""
        files = [f.strip() for f in files_str.split(",") if f.strip()]
        steps.append({
            "id": step_id,
            "desc": desc,
            "files": files,
            "status": "pending",
        })
    return steps
```

- [ ] **Step 2: 修改 planner 调用处，发送结构化计划事件**

在 `_run_inner` 中 planner 调用处（约 L1044-1057），修改为：

```python
if planner_llm:
    yield {"type": "thought", "data": {"content": "🏗️ 架构师正在制定执行计划..."}}
    try:
        plan = await self._run_planner(planner_llm, settings, user_message, messages)
        if plan:
            # Parse into structured steps
            plan_steps = self._parse_plan_steps(plan)
            # Inject plan as system-level context
            plan_msg = f"[架构师执行计划]\n{plan}\n[请按照以上计划执行]"
            messages.append({"role": "user", "content": plan_msg})
            # Send structured plan to frontend
            yield {"type": "plan", "data": {
                "text": plan,
                "steps": plan_steps,
                "total": len(plan_steps),
            }}
            logger.info("Planner plan for task %s: %d steps", task_id, len(plan_steps))
    except Exception as e:
        logger.warning("Planner failed for task %s: %s, falling back to single-model", task_id, e)
        yield {"type": "thought", "data": {"content": "⚠️ 规划阶段出错，回退到单模型模式"}}
```

- [ ] **Step 3: 添加计划进度追踪事件**

在 `_handle_fc_iteration` 中，当工具执行完成后（observation yield 之后），添加进度更新逻辑：

```python
# ── Plan progress tracking ──
# Check if a plan exists and update step status
if hasattr(self, '_current_plan_steps') and self._current_plan_steps:
    for step in self._current_plan_steps:
        if step["status"] == "pending":
            # Check if any of the step's files were touched
            step_files = step.get("files", [])
            edited_path = tool_params.get("path", "")
            if not step_files or edited_path in step_files:
                step["status"] = "completed"
                yield {"type": "plan_progress", "data": {
                    "step_id": step["id"],
                    "status": "completed",
                    "completed": sum(1 for s in self._current_plan_steps if s["status"] == "completed"),
                    "total": len(self._current_plan_steps),
                }}
                break  # Only mark one step per iteration
```

同时在 `_run_inner` 中 planner 成功后保存计划步骤：

```python
if plan:
    plan_steps = self._parse_plan_steps(plan)
    self._current_plan_steps = plan_steps  # Save for progress tracking
```

并在任务结束时清理：

```python
# In the finally block of run()
self._current_plan_steps = []
```

- [ ] **Step 4: 语法验证**

运行: `python -c "import ast; ast.parse(open('python/agent/task_agent.py', encoding='utf-8').read()); print('OK')"`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add python/agent/task_agent.py
git commit -m "feat(task_agent): structured plan parsing + progress tracking SSE events"
```

---

### Task 4: 前端类型扩展 + SSE 事件处理

**Files:**
- Modify: `src/types.ts:74-78`
- Modify: `src/features/task/useTaskChat.ts:64-115`

**Interfaces:**
- Consumes: 后端新增的 `plan` / `plan_progress` / `observation.diff` 事件（Task 2, 3）
- Produces: 前端 state 中新增 `planSteps` / `diffData` 字段

- [ ] **Step 1: 扩展 StreamStep 类型**

在 `src/types.ts` 中扩展：

```typescript
export type StreamStep =
  | { type: 'reasoning'; content: string }
  | { type: 'thought'; content: string }
  | { type: 'action'; tool: string; input: Record<string, unknown>; output?: string; diff?: string; filePath?: string }

export interface PlanStep {
  id: number
  desc: string
  files: string[]
  status: 'pending' | 'completed'
}

export interface PlanData {
  text: string
  steps: PlanStep[]
  total: number
}
```

- [ ] **Step 2: 扩展 useTaskChat state**

在 `useTaskChat.ts` 中添加新 state：

```typescript
const [planData, setPlanData] = useState<PlanData | null>(null)
```

- [ ] **Step 3: 处理新 SSE 事件类型**

在 `handleStreamEvent` 中添加：

```typescript
} else if (type === 'plan') {
  setPlanData(parsed as PlanData)
} else if (type === 'plan_progress') {
  setPlanData((prev) => {
    if (!prev) return prev
    const steps = prev.steps.map((s) =>
      s.id === parsed.step_id ? { ...s, status: parsed.status } : s
    )
    return { ...prev, steps }
  })
}
```

- [ ] **Step 4: 传递 diff 数据到 action step**

在 `observation` 事件处理中：

```typescript
} else if (type === 'observation') {
  setStreamSteps((prev) => {
    const steps = [...prev]
    for (let i = steps.length - 1; i >= 0; i--) {
      const step = steps[i]
      if (step.type === 'action' && !step.output) {
        steps[i] = {
          ...step,
          output: parsed.content,
          diff: parsed.diff,
          filePath: parsed.file_path,
        }
        if (step.tool === 'file_ops' || step.tool === 'doc_generator') {
          window.dispatchEvent(new Event('task:refresh-files'))
        }
        break
      }
    }
    return steps
  })
}
```

- [ ] **Step 5: 在 return 中导出 planData**

```typescript
return {
  messages,
  isGenerating,
  streamContent,
  streamSteps,
  streamIsThought,
  planData,  // NEW
  pendingShellConfirm,
  pendingSkillConfirm,
  handleSendMessage,
  handleStopGenerate,
  handlePauseTask,
  handleShellConfirm,
  handleSkillConfirm,
}
```

- [ ] **Step 6: 更新 TaskChatView / TaskMessageList props**

在 `TaskChatView.tsx` 和 `TaskMessageList.tsx` 中传递 `planData` prop。

- [ ] **Step 7: 语法验证**

运行: `npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 8: Commit**

```bash
git add src/types.ts src/features/task/useTaskChat.ts src/features/task/TaskChatView.tsx src/features/task/TaskMessageList.tsx
git commit -m "feat(frontend): handle plan/diff SSE events + extend StreamStep types"
```

---

### Task 5: DiffCard 组件 — inline diff 渲染

**Files:**
- Modify: `src/features/task/TaskMessageList.tsx:280-420`（ToolCard 组件）
- Modify: `src/features/task/TaskMessageList.module.css`

**Interfaces:**
- Consumes: `StreamStep.diff` / `StreamStep.filePath`（Task 4）
- Produces: 可视化 inline diff 渲染

- [ ] **Step 1: 添加 DiffCard 组件**

在 `TaskMessageList.tsx` 中 ToolCard 组件之前添加：

```tsx
/** Inline diff viewer for file edits */
function DiffCard({ diff, filePath }: { diff: string; filePath?: string }) {
  const [expanded, setExpanded] = useState(false)
  const lines = diff.split('\n')
  // Count additions/deletions
  let additions = 0, deletions = 0
  for (const line of lines) {
    if (line.startsWith('+') && !line.startsWith('+++')) additions++
    if (line.startsWith('-') && !line.startsWith('---')) deletions++
  }
  const preview = lines.slice(0, 8).join('\n')
  const hasMore = lines.length > 8

  return (
    <div className={s.diffCard}>
      <button className={s.diffHeader} onClick={() => setExpanded(!expanded)}>
        <span className={`${s.chevron} ${expanded ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.diffTitle}>
          {filePath || '变更'}
          <span className={s.diffStats}>
            <span className={s.diffAdd}>+{additions}</span>
            <span className={s.diffDel}>-{deletions}</span>
          </span>
        </span>
      </button>
      {expanded ? (
        <pre className={s.diffBody}>
          {lines.map((line, i) => (
            <span key={i} className={
              line.startsWith('+') && !line.startsWith('+++') ? s.diffLineAdd :
              line.startsWith('-') && !line.startsWith('---') ? s.diffLineDel :
              line.startsWith('@@') ? s.diffLineHunk : s.diffLineCtx
            }>
              {line}{'\n'}
            </span>
          ))}
        </pre>
      ) : (
        <pre className={s.diffPreview}>{preview}{hasMore ? '\n...' : ''}</pre>
      )}
    </div>
  )
}
```

- [ ] **Step 2: 在 ToolCard 中集成 DiffCard**

在 `ToolCard` 组件的展开区域（`{open && output && ...}`）之前添加 diff 渲染：

```tsx
{/* Diff viewer for file edits */}
{step.type === 'action' && step.diff && (
  <DiffCard diff={step.diff} filePath={step.filePath} />
)}
```

注意：需要将 `StreamStepView` 中的 diff/filePath 传递给 ToolCard。修改 `StreamStepView`：

```tsx
function StreamStepView({ step }: { step: StreamStep }) {
  if (step.type === 'reasoning') {
    return <ReasoningBlock content={step.content} defaultOpen />
  }
  if (step.type === 'thought') {
    return <ThoughtBlock content={step.content} defaultOpen />
  }
  return (
    <ToolCard
      taskId={null}
      toolName={step.tool}
      input={step.input}
      output={step.output}
      diff={step.diff}
      filePath={step.filePath}
      done={!!step.output}
    />
  )
}
```

并更新 ToolCard 的 props 和渲染逻辑。

- [ ] **Step 3: 添加 CSS 样式**

在 `TaskMessageList.module.css` 中添加：

```css
/* ── Diff Card ── */
.diffCard {
  margin: 4px 0 4px 28px;
  border: 1px solid var(--border, #333);
  border-radius: 6px;
  overflow: hidden;
  font-size: 12px;
}
.diffHeader {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  background: var(--diff-header-bg, #1e1e2e);
  border: none;
  cursor: pointer;
  color: var(--text-secondary, #aaa);
  text-align: left;
}
.diffTitle {
  font-weight: 500;
  color: var(--text, #ccc);
}
.diffStats {
  margin-left: 8px;
  font-size: 11px;
  font-weight: 400;
}
.diffAdd { color: #4ec9b0; margin-right: 6px; }
.diffDel { color: #f48771; }
.diffBody, .diffPreview {
  margin: 0;
  padding: 8px 10px;
  overflow-x: auto;
  font-family: 'Cascadia Code', 'Fira Code', monospace;
  font-size: 12px;
  line-height: 1.5;
  background: var(--diff-bg, #1a1a2a);
  white-space: pre;
}
.diffPreview {
  max-height: 120px;
  overflow: hidden;
  opacity: 0.7;
}
.diffLineAdd { background: rgba(78, 201, 176, 0.12); color: #4ec9b0; display: block; }
.diffLineDel { background: rgba(244, 135, 113, 0.12); color: #f48771; display: block; }
.diffLineHunk { color: #569cd6; display: block; }
.diffLineCtx { color: var(--text-secondary, #888); display: block; }
```

- [ ] **Step 4: 验证构建**

运行: `npx vite build`
Expected: 构建成功

- [ ] **Step 5: Commit**

```bash
git add src/features/task/TaskMessageList.tsx src/features/task/TaskMessageList.module.css
git commit -m "feat(ui): inline diff viewer for file edit operations"
```

---

### Task 6: PlanProgress 组件 — 计划进度展示

**Files:**
- Modify: `src/features/task/TaskMessageList.tsx`
- Modify: `src/features/task/TaskMessageList.module.css`

**Interfaces:**
- Consumes: `PlanData` state（Task 4）
- Produces: 可视化计划进度

- [ ] **Step 1: 添加 PlanProgress 组件**

```tsx
/** Plan progress card — shows execution plan with step-by-step status */
function PlanProgress({ plan }: { plan: PlanData }) {
  const [open, setOpen] = useState(true)
  const completed = plan.steps.filter(s => s.status === 'completed').length
  const total = plan.total || plan.steps.length
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0

  return (
    <div className={s.planCard}>
      <button className={s.planHeader} onClick={() => setOpen(!open)}>
        <span className={`${s.chevron} ${open ? s.chevronOpen : ''}`}>▸</span>
        <span className={s.planTitle}>📋 执行计划</span>
        <span className={s.planBadge}>{completed}/{total}</span>
        <div className={s.planProgressBar}>
          <div className={s.planProgressFill} style={{ width: `${pct}%` }} />
        </div>
      </button>
      {open && (
        <div className={s.planBody}>
          {plan.steps.map((step) => (
            <div key={step.id} className={`${s.planStep} ${step.status === 'completed' ? s.planStepDone : ''}`}>
              <span className={s.planStepIcon}>
                {step.status === 'completed' ? '✓' : '○'}
              </span>
              <span className={s.planStepDesc}>{step.desc}</span>
              {step.files.length > 0 && (
                <span className={s.planStepFiles}>{step.files.join(', ')}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: 在 TaskMessageList 中渲染 PlanProgress**

在 `TaskMessageList` 组件中，在 streamSteps 渲染之前添加：

```tsx
{planData && planData.steps.length > 0 && (
  <PlanProgress plan={planData} />
)}
```

- [ ] **Step 3: 添加 CSS 样式**

```css
/* ── Plan Progress ── */
.planCard {
  margin: 8px 0;
  border: 1px solid var(--border, #333);
  border-radius: 8px;
  overflow: hidden;
}
.planHeader {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  background: var(--plan-header-bg, #1e1e2e);
  border: none;
  cursor: pointer;
  color: var(--text, #ccc);
  text-align: left;
}
.planTitle { font-weight: 600; }
.planBadge {
  margin-left: auto;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--plan-badge-bg, #2a2a3a);
  color: var(--text-secondary, #aaa);
}
.planProgressBar {
  flex: 0 0 80px;
  height: 4px;
  border-radius: 2px;
  background: var(--plan-bar-bg, #2a2a3a);
  overflow: hidden;
}
.planProgressFill {
  height: 100%;
  border-radius: 2px;
  background: #4ec9b0;
  transition: width 0.3s ease;
}
.planBody { padding: 8px 12px; }
.planStep {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  color: var(--text-secondary, #888);
}
.planStepDone { color: var(--text, #ccc); }
.planStepIcon { font-size: 14px; }
.planStepDone .planStepIcon { color: #4ec9b0; }
.planStepFiles {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-dim, #666);
}
```

- [ ] **Step 4: 验证构建**

运行: `npx vite build`
Expected: 构建成功

- [ ] **Step 5: Commit**

```bash
git add src/features/task/TaskMessageList.tsx src/features/task/TaskMessageList.module.css
git commit -m "feat(ui): plan progress card with step-by-step status tracking"
```

---

### Task 7: 文档更新 + 最终评估

**Files:**
- Modify: `../../1.1task-agent-design-unified.md`

- [ ] **Step 1: 更新文档**

更新以下章节：
- §4.6 Self-Reflection — 标注 Phase 18 增强（如适用）
- §7.2 repo_map — 确认 Phase 17 已完成
- §15.1 对标表 — 更新代码编辑能力和用户体验评分
- §15.2 综合评分 — 更新为 ~87 分
- §15.3 提升建议 — 标记已完成项
- §15.5 V3 用户体验 — 标记已实施项
- 版本历史 — 添加 1.3.0 条目

- [ ] **Step 2: 最终业界对标评估**

更新后的评分表：

| 维度 | Phase 18 | Cursor | Aider | Devin |
|------|---------|--------|-------|-------|
| 代码编辑能力 | **8.5** | 9 | 9 | 8 |
| 规划与推理 | **8** | 7 | 6 | 9 |
| 用户体验 | **8** | 9 | 6 | 9 |

综合评分: ~87 分

- [ ] **Step 3: Commit**

```bash
git add docs/1.1task-agent-design-unified.md
git commit -m "docs: update unified doc to v1.3.0 — Phase 18 complete"
```

---

## 实施顺序与依赖关系

```
Task 1 (file_ops diff)
  ↓
Task 2 (task_agent SSE diff)
  ↓
Task 3 (结构化计划 + 进度)
  ↓
Task 4 (前端类型 + SSE 处理)
  ↓
Task 5 (DiffCard 组件) ← 依赖 Task 4
Task 6 (PlanProgress 组件) ← 依赖 Task 4
  ↓
Task 7 (文档更新)
```

Task 5 和 Task 6 可以并行实施。

## 预估工作量

| Task | 预估时间 | 改动行数 |
|------|---------|---------|
| Task 1 | 15 min | ~40 行 |
| Task 2 | 10 min | ~15 行 |
| Task 3 | 20 min | ~60 行 |
| Task 4 | 20 min | ~50 行 |
| Task 5 | 20 min | ~80 行 |
| Task 6 | 15 min | ~60 行 |
| Task 7 | 10 min | ~20 行 |
| **总计** | **~2 小时** | **~325 行** |
