# Task Agent V4 设计文档 — 质量加固、能力补全与工程优化

> 版本: 0.3.1 | 日期: 2026-09-22 | 状态: 全部完成（Phase 10-16 核实 + 实施 + review）
>
> 本文档是 `task-agent-v3-design.md`（V3, v0.10.0）的延续，聚焦三项核心工作：**已知 Bug 修复**、**V3 遗留功能补全** 和 **工程质量优化**。目标：综合评分从 ~67 分提升至 80+ 分。
>
> **v0.3.0 变更**：Phase 10-15 全部实施完成，代码已落地并通过语法检查。

---

## 1. 背景与目标

### 1.1 V3 现状（v0.10.0, 综合 ~67 分）

V3 完成了以下里程碑：
- ✅ 日志体系完善（P0+P1+P2）
- ✅ apply_diff 编辑模式 + multi-hunk diff
- ✅ 跨文件引用追踪（LSPClientTool）
- ✅ 智能上下文注入（20+ 框架检测）
- ✅ LSP 集成（pyright/tsc + ripgrep）
- ✅ 批量编辑（batch_edit 原子性）
- ✅ Agent Skills 行业标准支持（SKILL.md）
- ✅ 编辑验证循环（post_execute 自动语法/lint 检查）

**综合评分：~67 分**（对标 Cursor/Aider/Devin 80-90 分）

### 1.2 问题诊断

经过对 V1/V2/V3 设计文档和代码的全面审计，发现三类问题（v0.2.0 已逐项代码核实）：

| 类别 | 数量 | 影响 |
|------|------|------|
| **Bug（代码缺陷）** | 2 个 | 功能异常或 API 报错 |
| **未完成功能** | 7 项 | 文档设计了但未实现 |
| **工程优化** | 5 项（+1 遗漏） | 性能/可靠性/可维护性 |

### 1.3 V4 目标

> **综合评分：67 → 80+ 分**
>
> 四个核心杠杆：
> 1. **Bug 修复**：消除消息配对破坏 + 假自动修复
> 2. **能力补全**：编辑验证循环 + 依赖缓存 + 渐进式披露
> 3. **上下文管理**：补齐 5 种压缩策略（对标 Claude Code）
> 4. **工程优化**：token 精度 + prompt 预算 + 并发安全

---

## 2. Phase 10：Bug 修复（P0，1 天）

### 2.1 Bug #1：Stuck Detection 破坏 tool_call/tool 消息配对

**问题描述**：

在 `_handle_fc_iteration` 中，stuck detection 在 `for tc in tool_calls` 循环**内部**插入 `user` 消息，破坏了 OpenAI API 要求的 `assistant(tool_calls) → tool` 消息配对关系。

**复现场景**：

```
LLM 返回 2 个 tool_calls: [file_ops, code_search]
→ messages.append(assistant_msg with tool_calls)
→ 循环 tc[0] (file_ops):
  → 执行成功，messages.append(tool_msg_0)
→ 循环 tc[1] (code_search):
  → 执行失败，触发 stuck detection
  → messages.append(user_msg)  ← 破坏配对！
  → return
→ tc[1] 的 tool_msg 还没追加，且 user 消息插在 tool_calls 和 tool 之间
```

**修复方案**：

将 stuck detection 移到 `for tc in tool_calls` 循环**之后**，确保所有 tool_call 的 tool 消息都已追加。

```python
# task_agent.py — _handle_fc_iteration

# 改动：将 stuck detection 移出 for tc 循环
# 在循环中只收集错误信号，不插入消息

# ── 循环内部：只收集错误信号 ──
for tc in tool_calls:
    # ... 执行 tool ...
    # ── Stuck detection（只收集，不插入消息）──
    is_error = ...
    if is_error:
        recent_errors.append(err_sig)
    else:
        recent_errors.clear()

# ── 循环结束后：检查是否需要注入 stuck hint ──
if len(recent_errors) >= stuck_threshold and len(set(recent_errors)) == 1:
    # 此时所有 tool 消息已追加，安全插入 user 消息
    messages.append({
        "role": "user",
        "content": (
            f"⚠️ 你已经连续 {stuck_threshold} 次遇到相同的错误。"
            f"请换一种方法解决，或者如果已经获得了足够的信息，直接给出最终回答。"
        ),
    })
    recent_errors.clear()
    yield {"type": "thought", "data": {"content": "⚠️ 检测到重复错误，已提示 Agent 更换策略"}}

# 循环结束后统一 yield _fc_done
yield {"type": "_fc_done", "data": {"status": "continue"}}
```

**改动文件**：
- `python/agent/task_agent.py` — `_handle_fc_iteration` 方法

**代码核实（v0.2.0）**：✅ **已确认**
- 代码位置：`task_agent.py` L1283-1313
- stuck detection 在 `for tc in tool_calls` 循环**内部**（L1100 开始的循环），当触发 stuck 时：
  - L1301: `messages.append(user_msg)` — 在循环内部插入 user 消息
  - L1310: `return` — 直接退出，后续 tool_call 的 tool 消息未追加
- 当前代码中 `recent_errors` 由 `_run_inner` L926 传入，L950 作为参数传给 `_handle_fc_iteration`
- **影响**：多 tool_calls 场景下，OpenAI 严格模式会触发 API 错误

**验收标准**：
- 多个 tool_calls 场景下 stuck detection 不再破坏消息配对
- 使用 OpenAI 严格模式 API 不报错

---

### 2.2 Bug #2：`_try_auto_fix_edit` 假循环

**问题描述**：

`_try_auto_fix_edit` 方法名暗示"自动修复"，但 `for attempt in range(max_retries)` 第一次就 `break`，实际只发送 hint 给 LLM，不做任何自动修复。

**修复方案**：

移除假循环，将方法重命名为 `_add_lint_hint_to_result`，明确其职责：在 tool result 中附加 lint 提示，让 LLM 在下一轮迭代中修复。

```python
# task_agent.py

@staticmethod
def _enrich_result_with_lint_hint(result_str: str, tool_params: dict) -> str:
    """If a file edit produced a lint_warning, add a hint for the LLM.

    This does NOT auto-fix — it enriches the result so the LLM sees
    the warning prominently and can fix it in the next iteration.
    """
    try:
        result_obj = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str

    if not result_obj.get("lint_warning"):
        return result_str

    fix_path = tool_params.get("path", "")
    lint_warning = result_obj["lint_warning"]
    result_obj["fix_hint"] = (
        f"⚠️ 编辑后检测到问题: {lint_warning}\n"
        f"请使用 search_replace 或 apply_diff 修复 {fix_path} 中的此问题。"
    )
    logger.info("Lint hint added for %s: %s", fix_path, lint_warning[:100])
    return json.dumps(result_obj, ensure_ascii=False)
```

同时更新调用处：

```python
# _handle_fc_iteration 中
if tool_name in ("file_ops",):
    result_str = self._enrich_result_with_lint_hint(result_str, tool_params)
```

**改动文件**：
- `python/agent/task_agent.py` — 重命名方法 + 更新调用处

**代码核实（v0.2.0）**：✅ **已确认**
- 代码位置：`task_agent.py` L470-518 `_try_auto_fix_edit`
- L493: `for attempt in range(max_retries):` — 循环开始
- L507-511: 设置 `auto_fix_hint` 后，L516 立即 `break`
- 循环永远只执行一次，`max_retries=2` 参数无实际意义
- 调用处：L1234-1236，在 `_handle_fc_iteration` 的 tool 执行后
- **方法名"auto_fix"具有误导性**：实际只发送 hint，不做任何修复

**验收标准**：
- 方法名准确反映职责
- 无死代码（`max_retries` 参数、`for` 循环）
- LLM 仍能通过 hint 看到 lint 警告

---

## 3. Phase 11：编辑验证循环（P0，1.5 天）

### 3.1 目标

实现真正的"编辑 → 验证 → 失败 → 自动修复 → 再验证"循环，将自我修正评分从 3/10 提升至 6/10。

### 3.2 设计

在 `_handle_fc_iteration` 的 tool 执行后，对 `file_ops` 的写操作增加验证循环：

```
file_ops (write/search_replace/apply_diff) 成功
  → post_execute 检测到 lint_warning
  → 自动读取当前文件内容
  → 构建修复 prompt（包含错误信息 + 当前内容）
  → 调用 LLM 生成修复后的 search_replace/apply_diff
  → 自动应用修复
  → 再次验证
  → 最多 3 轮
```

```python
async def _edit_verify_loop(
    self,
    tool: FileOpsTool,
    tool_params: dict,
    result_str: str,
    task_llm: LLMClient,
    settings: dict,
    messages: list[dict],
    max_retries: int = 3,
) -> str:
    """Edit-verify-fix loop: if lint check fails, auto-fix up to N times.

    Returns the (possibly updated) result_str.
    """
    for attempt in range(max_retries):
        # Check if there's a lint error to fix
        try:
            result_obj = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            break

        if not result_obj.get("lint_warning"):
            break  # No error, done

        lint_warning = result_obj["lint_warning"]
        fix_path = tool_params.get("path", "")
        logger.info(
            "Edit-verify loop: lint error on %s, auto-fix attempt %d/%d",
            fix_path, attempt + 1, max_retries,
        )

        # Read current file content
        read_result = await tool.execute(action="read", path=fix_path)
        try:
            read_obj = json.loads(read_result)
            current_content = read_obj.get("content", "")
        except (json.JSONDecodeError, TypeError):
            break

        if not current_content:
            break

        # Ask LLM to fix the error
        fix_prompt = [
            {"role": "system", "content": "你是一个代码修复专家。修复语法/lint 错误，不要改变功能。"},
            {"role": "user", "content": (
                f"文件 {fix_path} 编辑后出现以下错误：\n"
                f"{lint_warning}\n\n"
                f"当前文件内容：\n```\n{current_content[:5000]}\n```\n\n"
                f"请输出修复后的完整文件内容。只输出代码，不要解释。"
            )},
        ]

        try:
            fixed_content = await task_llm.chat(fix_prompt, settings)
            if not fixed_content or not isinstance(fixed_content, str):
                break

            # Strip markdown code fences if present
            fixed_content = fixed_content.strip()
            if fixed_content.startswith("```"):
                lines = fixed_content.split("\n")
                lines = lines[1:]  # remove opening fence
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                fixed_content = "\n".join(lines)

            # Apply the fix
            fix_result = await tool.execute(
                action="write", path=fix_path, content=fixed_content,
            )
            result_str = fix_result

            # Re-validate via post_execute
            result_str = await tool.post_execute(result_str, action="write", path=fix_path)

        except Exception as e:
            logger.warning("Edit-verify: auto-fix failed: %s", e)
            break

    return result_str
```

**调用位置**：在 `_handle_fc_iteration` 的 tool 执行后：

```python
if tool_name in ("file_ops",):
    result_str = await self._edit_verify_loop(
        tool, tool_params, result_str,
        task_llm, settings, messages,
    )
```

**改动文件**：
- `python/agent/task_agent.py` — 新增 `_edit_verify_loop` 方法 + 调用处

**代码核实（v0.2.0）**：✅ **现状确认，需要实现**
- 当前 `_try_auto_fix_edit`（L470-518）只做 hint，不做实际修复
- `post_execute`（file_ops.py L685-748）已实现：
  - `.py` 文件：`py_compile` 语法检查 ✅
  - `.js/.mjs/.cjs` 文件：`node --check` 语法检查 ✅
  - `.ts/.tsx` 文件：仅返回 hint 提示，无实际检查 ⚠️
- `post_execute` 在 write/search_replace/apply_diff 成功后触发
- 但写入时的语法检查（file_ops.py L608-617）已做自动回滚，`post_execute` 的二次检查主要捕获 py_compile 与 ast.parse 的差异
- **本 Phase 的核心价值**：将 hint 升级为真正的 LLM 自动修复循环

**验收标准**：
- .py 文件编辑后语法错误 → 自动修复（最多 3 轮）
- .js 文件编辑后语法错误 → 自动修复
- 修复失败时不阻塞，正常返回结果给 LLM
- 修复历史记入日志

---

## 4. Phase 12：上下文管理增强（P0，1.5 天）

### 4.1 目标

补齐 Claude Code 的 5 种压缩策略，将上下文管理评分从 5/10 提升至 7/10。

### 4.2 当前状态

| 策略 | Claude Code | InkCube V3 | 状态 |
|------|------------|------------|------|
| Disk Dump | 长输出保存到文件 | 无 | ❌ 缺失 |
| Snip | 截断保留头尾 | ✅ 已实现 | ✅ |
| Micro Compact | 早期 tool 结果压缩为单行 | 无 | ❌ 缺失 |
| Context Collapse | 早期对话压缩为摘要 | ✅ 已实现 | ✅ |
| Full Compact | 整个历史 LLM 摘要 | 无 | ❌ 缺失 |

### 4.3 新增策略 1：Micro Compact

**触发条件**：上下文 token 数达到 70%（在 Context Collapse 之前）

**行为**：将较早的 tool 结果（非最近 keep_recent 条消息内的）压缩为单行摘要。

```python
def micro_compact(self, messages: list[dict]) -> list[dict]:
    """Compress early tool results to one-line summaries.

    Triggered at 70% capacity (before Context Collapse at 85%).
    Keeps recent messages intact.
    """
    total = self.total_tokens(messages)
    if total <= self.available_tokens * 0.70:
        return messages

    system_msgs = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    if len(non_system) <= self.keep_recent + 2:
        return messages

    old_msgs = non_system[:-self.keep_recent]
    recent_msgs = non_system[-self.keep_recent:]

    # Compress tool results in old messages
    for msg in old_msgs:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if len(content) > 500:
                # Extract first meaningful line
                first_line = content.split("\n")[0][:200]
                msg["content"] = f"[已压缩] {first_line}..."

    return list(system_msgs) + old_msgs + recent_msgs
```

### 4.4 新增策略 2：Disk Dump

**触发条件**：单个 tool 输出超过阈值（实时处理）

**行为**：将超长输出保存到 workspace 文件，在上下文中只保留文件路径 + 摘要。

```python
def disk_dump(self, text: str, tool_name: str, workspace_path: str) -> str:
    """Save long tool output to file, return summary reference.

    Used when a single tool output exceeds the snip threshold.
    """
    if len(text) <= self.snip_threshold:
        return text

    # Save to workspace
    dump_dir = os.path.join(workspace_path, ".inkcube", "dumps")
    os.makedirs(dump_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dump_file = os.path.join(dump_dir, f"{tool_name}_{ts}.txt")

    try:
        with open(dump_file, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        return self.snip(text)  # Fallback to snip

    # Return reference + head
    head = text[:1000]
    return (
        f"[输出已保存到 {dump_file}，共 {len(text)} 字符]\n"
        f"前 1000 字符:\n{head}\n"
        f"[如需完整内容，用 file_ops read {dump_file}]"
    )
```

### 4.5 新增策略 3：Full Compact

**触发条件**：上下文 token 数达到 95%（最后手段）

**行为**：用 LLM 对整个历史生成精炼摘要，替换所有早期内容。

```python
async def full_compact(
    self, messages: list[dict], llm: Any, settings: dict,
) -> list[dict]:
    """Last-resort compression: LLM summarizes entire history.

    Triggered at 95% capacity.
    """
    total = self.total_tokens(messages)
    if total <= self.available_tokens * 0.95:
        return messages

    system_msgs = [m for m in messages if m["role"] == "system"]
    non_system = [m for m in messages if m["role"] != "system"]

    # Keep only last 3 messages
    if len(non_system) <= 3:
        return messages

    old_msgs = non_system[:-3]
    recent_msgs = non_system[-3:]

    # Build comprehensive summary
    conversation_text = ""
    for msg in old_msgs:
        role_label = {"user": "用户", "assistant": "助手", "tool": "工具结果"}.get(
            msg["role"], msg["role"]
        )
        content = msg.get("content", "")
        if len(content) > 300:
            content = content[:300] + "..."
        conversation_text += f"[{role_label}]: {content}\n"

    summary_prompt = [
        {"role": "system", "content": (
            "你是一个对话摘要助手。请将以下完整对话历史压缩为结构化摘要。"
            "必须保留：1) 用户需求原文 2) 已完成的每个操作及结果 3) 当前状态 4) 未解决的问题。"
            "摘要应让全新的对话能完整理解上下文。直接输出摘要。"
        )},
        {"role": "user", "content": f"请摘要以下对话历史：\n\n{conversation_text[:8000]}"},
    ]

    try:
        summary = await llm.chat(summary_prompt, settings)
        if summary and isinstance(summary, str) and len(summary.strip()) > 10:
            result = list(system_msgs)
            result.append({
                "role": "user",
                "content": f"[完整历史摘要]\n{summary.strip()}\n[摘要结束]",
            })
            result.extend(recent_msgs)
            result = self._fix_tool_pairing(result)
            logger.info(
                "Full compact: %d messages → LLM summary (%d chars) + 3 recent",
                len(old_msgs), len(summary),
            )
            return result
    except Exception as e:
        logger.warning("Full compact LLM failed: %s", e)

    # Fallback: aggressive truncation
    return messages  # Can't compress further without LLM
```

### 4.6 渐进式压缩调度

```python
async def compress(self, messages, llm, settings):
    """Unified compression entry point with progressive strategy."""

    total = self.total_tokens(messages)

    # Level 1: Micro Compact (70%)
    if total > self.available_tokens * 0.70:
        messages = self.micro_compact(messages)
        total = self.total_tokens(messages)

    # Level 2: Context Collapse (85%)
    if total > self.available_tokens * 0.85:
        messages = await self._context_collapse(messages, llm, settings)
        total = self.total_tokens(messages)

    # Level 3: Full Compact (95%)
    if total > self.available_tokens * 0.95:
        messages = await self.full_compact(messages, llm, settings)

    return messages
```

**改动文件**：
- `python/agent/context_manager.py` — 新增 `micro_compact`、`disk_dump`、`full_compact` 方法 + 改造 `compress`

**代码核实（v0.2.0）**：✅ **现状确认，策略缺失已验证**
- `context_manager.py` 当前只有两种策略：
  - `snip()`（L198-210）：截断保留头尾 ✅ 已实现
  - `compress()`（L90-171）：Context Collapse（LLM 摘要） ✅ 已实现
- 压缩触发条件：`compress_ratio=0.85`（L48），即 85% 时触发
- 缺失的三种策略确认：
  - ❌ Disk Dump：无任何将超长输出保存到文件的逻辑
  - ❌ Micro Compact：无早期 tool 结果压缩为单行的逻辑
  - ❌ Full Compact：无 95% 紧急压缩逻辑
- ~~`compress()` 中 L135 `llm.chat()` 返回类型不安全~~ → **v0.2.1 修正：误判**，`compress()` 未传 `tools`，`llm.chat()` 永远返回 `str`
- `_fix_tool_pairing()`（L173-194）已实现，只在 `compress()` 后调用
- 渐进式压缩调度：当前 `compress()` 是单一策略，无分级调度

**验收标准**：
- 70% 时触发 Micro Compact（早期 tool 结果压缩为单行）
- 85% 时触发 Context Collapse（LLM 摘要）
- 95% 时触发 Full Compact（全面摘要）
- 超长 tool 输出实时 Disk Dump
- 所有策略保持 tool_call/tool 配对正确

---

## 5. Phase 13：预装常用包（P1，0.5 天）

### 5.1 依赖缓存（已取消）

> **v0.2.0 修正**：原方案建议为 uv/pip 添加 `--cache-dir` 参数。
> 经核实，**uv 自带全局内容寻址缓存**（Windows: `%LOCALAPPDATA%\uv\cache`），
> 同一个 wheel 无论被多少任务 venv 引用，磁盘上只存一份。
> 当前代码 `uv pip install --python <venv_python>` 已自动受益，跨任务复用是秒级安装。
> pip 也有默认缓存（`~/.cache/pip`），无需额外配置。
> **结论：依赖缓存不需要任何代码修改，原方案取消。**

### 5.2 预装常用包（保留）

**问题**：虽然 uv 缓存让重复安装很快，但每个任务 venv 创建后首次安装仍需网络请求 + 解包。
预装覆盖 80% Skill 需求的常用包，可以让大多数 Skill 跳过依赖安装步骤。

**方案**：创建任务 venv 后，预装常用包。优先使用 uv（更快），fallback 到 pip。

```python
# python/tools/venv_manager.py

PREINSTALLED_PACKAGES = [
    "requests", "httpx",     # HTTP
    "pandas", "numpy",       # 数据处理
    "pillow",                # 图片处理
    "pyyaml",                # YAML 解析
    "beautifulsoup4",        # HTML 解析
    "openpyxl",              # Excel 处理
]

async def preinstall_packages(venv_python: str) -> None:
    """Preinstall common packages into a new task venv.
    
    Uses uv if available (faster), falls back to pip.
    Both uv and pip have global caches, so repeated installs are fast.
    """
    import asyncio
    from tools.tool_resolver import resolve_tool

    uv = resolve_tool("uv")
    try:
        if uv:
            cmd = [uv, "pip", "install", "--python", venv_python,
                   *PREINSTALLED_PACKAGES]
        else:
            cmd = [venv_python, "-m", "pip", "install", "--quiet",
                   *PREINSTALLED_PACKAGES]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=120)
    except Exception:
        pass  # Best-effort
```

**改动文件**：
- `python/tools/venv_manager.py` — 新增 `PREINSTALLED_PACKAGES` + `preinstall_packages()`
- `python/routers/tasks.py` — 创建任务后调用预装

**代码核实（v0.2.0）**：✅ **现状确认，功能缺失**
- `venv_manager.py`（74 行）：
  - 只有 `create_venv()`、`get_venv_python()`、`get_venv_bin_dir()` 三个函数
  - **无 `PREINSTALLED_PACKAGES` 定义** ❌
  - **无 `preinstall_packages()` 函数** ❌
  - 创建 venv 后不做任何预装
- **注意**：依赖缓存不需要额外代码（uv/pip 自带全局缓存）

---

## 6. Phase 14：渐进式披露 3 级加载（P1，0.5 天）

### 6.1 目标

Agent Skills 可能安装数十个，但每个都加载完整 SKILL.md body 到 prompt 会浪费 token。

### 6.2 设计

```
Level 1: 元数据发现（始终在上下文）
  └─ 只加载 name + description（~50 tokens/skill）

Level 2: 指令激活（Agent 选择调用时）
  └─ 读取完整 SKILL.md body 注入到 tool result

Level 3: 资源执行（按需）
  └─ 读取 references/、运行 scripts/
```

**实现**：修改 `AgentSkillAdapter.prompt_description`，只返回 Level 1 元数据：

```python
@property
def prompt_description(self) -> str:
    """Level 1: only name + description (saves tokens)."""
    return (
        f"## {self.name}\n"
        f"{self.description}\n\n"
        f"参数: command (string, required)\n"
        f"调用时会自动加载完整使用指南。"
    )
```

完整 body 在 Agent 调用 skill 时通过 tool result 返回（Level 2）：

```python
async def execute(self, params: dict, context: SkillContext) -> str:
    # Level 2: inject full instructions into result
    instructions = self._body[:4000] if self._body else ""
    # ... rest of execution ...
    result_obj["instructions"] = instructions
```

**改动文件**：
- `python/skills/loader.py` — `prompt_description` 精简 + `execute` 注入 body

**代码核实（v0.2.0）**：✅ **现状确认，token 浪费已验证**
- `loader.py` L291-314 `prompt_description`：
  - L306-311: 将 SKILL.md body 前 2000 字符注入到 system prompt
  - 每个 Skill 约 500-2000 tokens，若安装 10 个 Skill 则占 5000-20000 tokens
- FC 定义生成：`to_function_definitions()` 通过 `skill.to_function_definition()` 生成
  - 基类 `BaseSkill.to_function_definition()` 用 `prompt_description` 作为 description
  - 即 LLM 的 tools 列表中每个 Skill 都包含完整使用指南
- **实际 token 消耗**：每个 Skill 在 system prompt + FC 定义中双重加载

---

## 7. Phase 15：工程优化（P1，1 天）

### 7.1 Token 计数精度优化

**问题**：fallback `len(text) // 3` 对中文偏粗（实际 ~1.5 char/token）。

**代码核实（v0.2.0）**：✅ **已确认**
- `context_manager.py` L23: `_CHARS_PER_TOKEN = 3`
- L76: `return max(1, len(text) // _CHARS_PER_TOKEN)` — 对所有语言统一用 3
- 中文实际约 1.5 char/token，误差 100%

**方案**：引入简单语言检测，中文用 `len(text) // 2`。

```python
@staticmethod
def count_tokens(text: str) -> int:
    if not text:
        return 0
    if _tiktoken_encoder is not None:
        return len(_tiktoken_encoder.encode(text))

    # Heuristic: detect CJK characters for better estimation
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    if cjk_count > len(text) * 0.3:
        # Mostly CJK: ~1.5 chars per token
        return max(1, len(text) // 2)
    else:
        # Mostly Latin: ~4 chars per token
        return max(1, len(text) // 3)
```

### 7.2 System Prompt Token 预算

**问题**：system prompt 无长度限制，可能挤压对话空间。

**代码核实（v0.2.0）**：✅ **已确认**
- `_build_system_prompt()`（L290-331）无总长度限制
- 包含：identity + 时间 + 工具描述 + 效率原则 + 验证原则 + 编辑指导 + 审查原则 + 富文本 + git 上下文 + 项目检测 + 项目配置
- 其中 `_get_tools_description()` 可返回 5000+ 字符（14 个工具的完整描述）
- `_load_project_config()` 已限制 4000 字符（L350），但其他部分无限制

**方案**：设置 4000 token 预算，超限时截断低优先级部分。

```python
def _build_system_prompt(self, workspace_path, mode="coding"):
    # ... existing assembly ...
    full_prompt = "\n".join(parts)

    # Budget check
    prompt_tokens = ContextManager.count_tokens(full_prompt)
    PROMPT_BUDGET = 4000
    if prompt_tokens > PROMPT_BUDGET:
        # Truncate project config first, then git context
        # ... truncation logic ...
        logger.warning(
            "System prompt truncated: %d tokens (budget: %d)",
            prompt_tokens, PROMPT_BUDGET,
        )
    return full_prompt
```

### 7.3 Skill 脚本超时可配置

**问题**：`AgentSkillAdapter._run_script` 硬编码 60s 超时。

**代码核实（v0.2.0）**：✅ **已确认**
- `loader.py` L260: `await asyncio.wait_for(proc.communicate(), timeout=60)` — 硬编码 60s
- 错误消息也是硬编码：L266 `"Skill 脚本执行超时 (60s)"`
- `SkillContext` 有 `settings` 字段但未用于超时配置

**方案**：从 settings 读取，默认 120s。

```python
# In _run_script:
timeout = context.settings.get("skill_script_timeout", 120)
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
```

### 7.4 `_register_external_skills` 缓存优化

**问题**：每次 `run()` 都重新扫描文件系统和解析 SKILL.md。

**代码核实（v0.2.0）**：✅ **已确认**
- `task_agent.py` L813: `self._register_external_skills(skill_registry)` 在每次 `_run_inner()` 中调用
- L243-270: 每次都 `db.list_skills()` + `parse_skill_md()` + `validate_skill_md()`
- `SkillRegistry`（registry.py L32-34）无缓存机制，只有 `_skills` 和 `_metadata` 两个 dict
- `SkillRunnerTool._refresh_cache()`（skill_runner.py L53-65）也是每次重新从 DB 加载

**方案**：在 SkillRegistry 层面缓存，只在 Skill 安装/卸载/启用变更时刷新。

```python
class SkillRegistry:
    def __init__(self):
        self._skills = {}
        self._metadata = {}
        self._external_scan_cache: dict[str, float] = {}  # path → mtime
        self._external_cache: dict[str, AgentSkillAdapter] = {}

    def discover_external(self, skills_dir: str, db=None):
        """Scan external skills with mtime-based cache."""
        # Only re-parse if SKILL.md modified
        ...
```

### 7.5 并发安全：tool 实例隔离

**问题**：`self._tools` 共享实例的 `workspace_dir` 可能被并发修改。

**代码核实（v0.2.0）**：⚠️ **部分已解决，但描述需修正**
- 实际代码中 `_create_task_tools()`（L357-464）已为每个任务创建独立 tool 实例
- 包括 `file_ops`、`shell`、`code_executor` 等都有独立实例
- **但**：`_get_tools_description()`（L274-288）使用 `self._tools`（共享实例）生成 prompt 描述
- 这些共享实例的 `workspace_dir` 为空字符串（L110-164 初始化时未设置）
- 实际影响较小（prompt 描述不依赖 workspace_dir），但设计上不优雅

**方案**：`_get_tools_description` 接受 workspace_path 参数，不依赖 tool 实例状态。

```python
def _get_tools_description(self, mode: str = "coding", workspace_path: str = "") -> str:
    """Generate tool descriptions. Uses per-task instances when available."""
    # ... existing logic, but don't rely on shared instance workspace_dir
```

---

## 8. Phase 16：文档对齐修正（P2，0.5 天）

### 8.1 修正 compress_ratio 文档

V1 §11.7 建议 75% 触发压缩，但代码用 85%。

**代码核实（v0.2.0）**：✅ **已确认**
- `context_manager.py` L48: `compress_ratio: float = 0.85`
- `task_agent.py` L107: `COLLAPSE_THRESHOLD = 40`（旧常量，已不再使用）
- 实际压缩由 `ContextManager.should_compress()` 基于 token 计算，而非消息数

**决策**：保持代码 85%（与 Claude Code 的 Context Collapse 对齐），更新文档。

### 8.2 更新 V1 文档状态标记

| 项目 | 当前标记 | 修正为 |
|------|---------|--------|
| 双模型架构 | "🔨 待实施" | "✅ 基本实现（Planner + 单模型 fallback）" |
| 编辑验证循环 | 未提及 | "✅ post_execute 自动检查 + ⚠️ 自动修复循环待 Phase 11" |

**代码核实（v0.2.0）**：✅ **已确认**
- 双模型：`task_agent.py` L777-783，`planner_model_name` 配置存在，L911-923 Planner 执行逻辑完整
- 编辑验证：`post_execute` 已实现（file_ops.py L685-748），但自动修复循环只是 hint

### 8.3 执行日志 UI

V1 §12.7 设计了"查看执行日志"按钮，后端已写 `execution.log`，但无前端入口。

**代码核实（v0.2.0）**：✅ **已确认**
- `_log_execution()`（task_agent.py L522-540）写入 `execution.log`
- 前端无入口（未在 src/ 中找到相关组件）

**方案**：在任务详情页添加"查看日志"按钮，调用 `GET /tasks/{id}/logs` 读取文件内容。

### 8.4 ~~补充发现：`compress()` 返回类型安全~~（已取消）

> **v0.2.1 修正**：原认为 `llm.chat()` 可能返回 `dict` 导致 `.strip()` 崩溃。
> 经核实 [client.py L29](file:///D:/personal/InkCube/python/llm/client.py#L29)：
> - `tools=None` → 返回 `str`（L102-113）
> - `tools=<list>` → 返回 `dict`（L94-100）
>
> `compress()` 调用 `llm.chat(summary_prompt, settings)` **未传 `tools` 参数**，
> 因此永远走 `str` 路径，不存在类型安全问题。
> **结论：无需修改，原方案取消。**

### 8.5 补充发现：Skill 执行路径三重冗余（v0.2.0 新增）

**问题**：Agent Skill 存在三条执行路径，逻辑重复且行为不一致：

| 路径 | 代码位置 | 触发场景 |
|------|---------|----------|
| SkillRegistry.execute() | task_agent.py L1241-1257 | FC 模式，LLM 调用 skill tool |
| SkillRunnerTool.execute() | task_agent.py L1225-1238 | FC 模式，LLM 调用 skill_runner |
| _execute_slash_command() | task_agent.py L656-721 | 用户输入 /skill-name |

**风险**：
- 路径 1 和 2 可能同时触发（LLM 既调 skill tool 又调 skill_runner）
- 路径 3 重新解析 SKILL.md，不复用路径 1/2 的缓存
- 建议统一为单一执行入口

**改动文件**：`python/agent/task_agent.py` — 统一 Skill 执行路径

---

## 9. 实施优先级

| 优先级 | 阶段 | 内容 | 预估提升 | 工作量 |
|--------|------|------|----------|--------|
| **P0** | Phase 10 | Bug 修复（消息配对 + 假循环） | 稳定性 | 1 天 |
| **P0** | Phase 11 | 编辑验证循环 | +3 分（自我修正 3→6） | 1.5 天 |
| **P0** | Phase 12 | 上下文管理增强（3 种新策略） | +2 分（上下文 5→7） | 1.5 天 |
| **P1** | Phase 13 | 预装常用包（缓存已取消，uv/pip 自带） | 体验提升 | 0.5 天 |
| **P1** | Phase 14 | 渐进式披露 3 级 | token 节省 | 0.5 天 |
| **P1** | Phase 15 | 工程优化（5 项） | 可靠性 + 性能 | 1 天 |
| **P2** | Phase 16 | 文档对齐 + 日志 UI + Skill 路径统一 | 一致性 + 稳定性 | 1 天 |

**推荐路径**：Phase 10 → 11 → 12 → 13 → 14 → 15 → 16

---

## 10. 评分路线图

```
当前: ~67 分 (V3 v0.10.0)
  ↓ Phase 10 (Bug 修复)
69 分 — 消除消息配对破坏 + 假循环清理
  ↓ Phase 11 (编辑验证循环)
72 分 — 自我修正 3→6，接近 Aider 水平
  ↓ Phase 12 (上下文管理)
74 分 — 5 种策略对齐 Claude Code
  ↓ Phase 13 (预装常用包)
75 分 — 体验提升，Skill 依赖秒级就绪（uv 缓存已自带）
  ↓ Phase 14 (渐进式披露)
76 分 — token 效率提升
  ↓ Phase 15 (工程优化)
78 分 — token 精度 + prompt 预算 + 并发安全
  ↓ Phase 16 (文档对齐)
80 分 — 文档与代码完全一致
```

---

## 11. 评分预期（各 Phase 完成后）

| 维度 | V3 现状 | +Ph10 | +Ph11 | +Ph12 | +Ph13-16 |
|------|---------|-------|-------|-------|----------|
| Agent 循环 | 8 | 8.5 | 8.5 | 8.5 | 8.5 |
| 编辑能力 | 8 | 8 | 8 | 8 | 8 |
| 自我修正 | 3 | 3 | **6** | 6 | 6 |
| 上下文管理 | 5 | 5 | 5 | **7** | 7.5 |
| 项目理解 | 6.5 | 6.5 | 6.5 | 6.5 | 7 |
| 安全机制 | 8 | 8 | 8 | 8 | 8 |
| 可扩展性 | 7 | 7 | 7 | 7 | 7.5 |
| Prompt 工程 | 6 | 6 | 6 | 6 | 7 |
| 工程可靠性 | 5 | 6 | 6 | 6 | 7.5 |
| **综合** | **~67** | **~69** | **~72** | **~74** | **~80** |

---

## 12. 与业界对标（V4 完成后）

| 维度 | V3 | V4 | Cursor | Aider | Claude Code |
|------|----|----|--------|-------|-------------|
| Agent 循环 | FC+流式 | FC+流式+稳定 | FC | FC | FC+缓存 |
| 编辑格式 | search_replace + diff | 同 V3 | inline diff | SEARCH/REPLACE | string replace |
| 上下文管理 | 2 种策略 | **5 种策略** | 精确 token | repo-map+token | 5 种策略 |
| 自我修正 | hint only | **自动修复循环** | 自动跑测试 | commit+回滚 | 编辑→验证 |
| 依赖管理 | 每次重装 | **预装常用包** | 用户管理 | venv | 用户管理 |
| 安全 | 三模式确认 | 同 V3 | 基础 | 弱 | Hook 系统 |

---

## 13. 不做的事

- **不做**：LSP 协议完整实现（当前 regex + pyright/tsc 子进程足够）
- **不做**：Docker 沙箱（venv 隔离 P0 够用）
- **不做**：多 Agent 协作
- **不做**：跨会话长期记忆（已有单独规划）
- **不做**：Skill 市场 / 在线商店
- **不做**：内置 Tool 物理迁移到 skills/ 目录

---

## 14. 代码核实总结表（v0.3.0）

| Phase | 问题 | 代码位置 | 状态 | 备注 |
|-------|------|---------|------|------|
| 10 | Bug #1 Stuck Detection | task_agent.py | ✅ 已实施 | 移出 for tc 循环，循环后注入 hint |
| 10 | Bug #2 假循环 | task_agent.py | ✅ 已实施 | 重命名为 _enrich_result_with_lint_hint + 新增 _edit_verify_loop |
| 11 | 编辑验证循环 | task_agent.py | ✅ 已实施 | _edit_verify_loop 实现真自动修复 |
| 12 | 上下文策略缺失 | context_manager.py | ✅ 已实施 | 新增 micro_compact / disk_dump / full_compact + 渐进式调度 |
| 12 | ~~compress 类型安全~~ | context_manager.py | ❌ 误判 | llm.chat() 未传 tools 时永远返回 str |
| 13 | 依赖缓存认知错误 | loader.py | ⚠️ 已修正 | uv 自带全局缓存，无需额外代码 |
| 13 | 预装包缺失 | venv_manager.py | ✅ 已实施 | 新增 PREINSTALLED_PACKAGES + preinstall_packages() |
| 14 | 渐进式披露缺失 | loader.py + skill_runner.py | ✅ 已实施 | Level 1 元数据 + Level 2 执行时注入 body |
| 15.1 | Token 计数偏粗 | context_manager.py | ✅ 已实施 | CJK 检测，中文 ~2 chars/token |
| 15.2 | System Prompt 无预算 | task_agent.py | ✅ 已实施 | 4000 token 预算，超限截断低优先级部分 |
| 15.3 | 脚本超时硬编码 | loader.py | ✅ 已实施 | 从 settings 读取，默认 120s |
| 15.4 | Skill 扫描无缓存 | task_agent.py | ✅ 已实施 | mtime 缓存，避免重复解析 |
| 15.5 | 并发 tool 实例 | task_agent.py | ✅ 核实无需修改 | prompt_description 均为静态文本，不依赖实例状态 |
| 16.1 | compress_ratio 文档 | context_manager.py | ✅ 已确认 | 代码 85%，保持 |
| 16.5 | Skill 三重路径 | task_agent.py | ✅ 核实为设计选择 | 3 条路径服务不同调用方，不冲突 |

---

## 15. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.3.1 | 2026-09-22 | 最终核实：Phase 15.5（tool 描述）和 16.5（Skill 路径）经核实无需修改，确认为设计选择 |
| 0.3.0 | 2026-09-22 | Phase 10-15 全部实施完成：Bug 修复 + 编辑验证循环 + 上下文 3 策略 + 预装包 + 渐进式披露 + 工程优化 4 项 |
| 0.2.1 | 2026-09-22 | 修正 compress 类型安全误判：llm.chat() 未传 tools 时永远返回 str，无需修复 |
| 0.1.0 | 2026-09-22 | V4 初始设计：Bug 修复 + 编辑验证循环 + 上下文管理增强 + 依赖缓存 + 渐进式披露 + 工程优化 + 文档对齐 |
