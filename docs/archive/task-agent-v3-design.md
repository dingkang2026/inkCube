# Task Agent V3 设计文档 — 编辑能力、项目理解与日志体系

> 版本: 0.10.0 | 日期: 2026-09-21 | 状态: 完整实施完成 + Agent Skills 行业标准支持
>
> 本文档是 `task-agent-v2-design.md`（V2, v0.6.0）的延续，聚焦四项核心升级：**代码编辑能力跃升**、**项目理解深化**、**日志体系完善** 和 **Agent Skills 行业标准支持**。

---

## 1. 背景与目标

### 1.1 V2 现状（v0.6.0, 综合 ~76 分）

V2 完成了以下里程碑：
- ✅ 统一 Skill 框架（内置 Tool + 外部 Skill 双轨并存）
- ✅ Function Calling + 文本解析 fallback
- ✅ Token-aware 上下文管理（tiktoken 可选 + tree-sitter AST）
- ✅ Git 集成（git_ops 内置 Tool + diff stat + 上下文注入）
- ✅ LLM 流式输出（chat_stream + 前端增量渲染）
- ✅ Self-reflection（Final Answer 前审查）

**综合评分：~76 分**（对标 Cursor/Aider/Devin 80-90 分）

### 1.2 剩余差距

| 维度 | V2 现状 | 业界一线 | 差距 | V3 目标 |
|------|---------|---------|------|---------|
| 代码编辑 | search_replace（单块替换） | multi-hunk diff / line-based | 多处编辑需多次调用，无行号定位 | 统一 diff 编辑 + 行号定位 |
| 项目理解 | repo_map（AST 签名 + 目录树） | 依赖图 + 智能上下文注入 | 无跨文件依赖分析，无自动关联 | 依赖图 + 智能上下文 |
| 日志体系 | 97 条 / 55 文件（20% 覆盖） | 全模块覆盖 + 结构化日志 | 核心模块无日志，无法排查问题 | 全模块日志 + 结构化 |
| 错误恢复 | 自动语法检查 + 备份回滚 | 编辑验证循环 + 自动重试 | 编辑失败后无自动修复 | 编辑验证循环 |

### 1.3 V3 目标

> **综合评分：76 → 83+ 分**
>
> 三个核心杠杆：
> 1. **编辑能力**：从"单块 search_replace"到"multi-hunk diff + 行号编辑"
> 2. **项目理解**：从"静态 AST 签名"到"依赖图 + 智能上下文注入"
> 3. **日志体系**：从"20% 覆盖"到"全模块结构化日志"

---

## 2. 日志体系现状审计

### 2.1 总体统计

| 指标 | 数值 |
|------|------|
| 日志语句总数 | 97 条 |
| Python 日志 | 87 条 |
| Electron 日志 | 10 条 |
| Python 文件总数 | 55 个 |
| 有日志的文件 | 11 个 (20%) |
| 无日志的文件 | 44 个 (80%) |

### 2.2 按级别分布

| 级别 | 数量 | 占比 | 评估 |
|------|------|------|------|
| INFO | 47 | 48% | ✅ 合理 |
| WARNING | 18 | 19% | ✅ 合理 |
| ERROR | 19 | 20% | ✅ 合理 |
| DEBUG | 3 | 3% | ⚠️ 偏少 |

### 2.3 按模块分布

| 模块 | 日志量 | 评估 |
|------|--------|------|
| knowledge/task_queue.py | 38 条 | ✅ 最完善，文件/图片处理全流程覆盖 |
| routers/system.py | 16 条 | ✅ 数据清理操作有完整日志 |
| agent/task_agent.py | 10 条 | ⚠️ 核心模块，偏少 |
| main.py | 6 条 | ✅ 启动/关闭关键节点 |
| tools/venv_manager.py | 6 条 | ✅ 合理 |
| agent/context_manager.py | 3 条 | ⚠️ 上下文管理关键模块，偏少 |
| routers/health.py | 2 条 | ⚠️ 健康检查模块，偏少 |
| 其他 | 各 1-2 条 | ⚠️ 零散 |

### 2.4 关键缺失（高优先级）

以下核心模块**完全没有日志**：

| 模块 | 文件 | 影响 |
|------|------|------|
| Agent 核心 | `react_agent.py` | 聊天 Agent 无日志，无法追踪对话流程 |
| LLM 客户端 | `llm_client.py`, `llm/client.py` | API 调用无日志，无法排查连接问题 |
| 所有 Routers | `chat.py`, `tasks.py`, `skills.py` 等 6 个 | API 请求/响应无日志 |
| 知识库核心 | `vector_store.py`, `pipeline.py`, `rag.py` 等 8 个 | RAG 流程无日志 |
| 所有 Tools | `shell_tool.py`, `code_executor.py` 等 14 个 | 工具执行无日志 |
| VLM 客户端 | `vlm_client.py` | 图片描述 API 调用无日志 |

### 2.5 日志体系结论

| 维度 | 评分 | 说明 |
|------|------|------|
| 总量 | ⚠️ 偏少 | 97 条 / 55 文件，平均不到 2 条/文件 |
| 覆盖率 | ❌ 不足 | 仅 20% 文件有日志 |
| 级别分布 | ✅ 合理 | INFO/WARN/ERROR 比例正常 |
| 关键路径 | ❌ 缺失 | Agent、LLM、Tools 无日志 |
| 调试能力 | ⚠️ 有限 | DEBUG 仅 3 条，排查问题困难 |

### 2.6 建议补充优先级

**P0（必须补充）：**
1. `react_agent.py` — 对话流程、LLM 调用、工具选择
2. `llm/client.py` — API 请求/响应/错误
3. `routers/tasks.py` — 任务创建/执行/错误
4. `tools/shell_tool.py`, `tools/code_executor.py` — 命令执行结果

**P1（建议补充）：**
5. `routers/chat.py` — 聊天请求
6. `knowledge/rag.py` — RAG 检索流程
7. `vlm_client.py` — 图片描述调用
8. `agent/context_manager.py` — 上下文压缩

**P2（可选）：**
9. 其他 Tools 的执行日志
10. 其他 Routers 的请求日志

### 2.7 总结

当前日志量对于一个小项目来说勉强够用，但对于有 Agent、LLM、知识库等复杂流程的项目来说**明显不足**。核心路径（Agent 对话、LLM 调用、工具执行）几乎没有日志，一旦出问题很难排查。建议优先补充 P0 模块的日志。

---

## 3. Phase 5：编辑能力升级（+4~5 分 → 80 分）

### 3.1 Unified Diff 编辑模式

**目标**: 让 LLM 输出标准 unified diff，而非 search_replace 文本匹配

**方案**:
- 新增 `apply_diff` action 到 `file_ops.py`
- LLM 输出格式: `<<<<<<< SEARCH` / `=======` / `>>>>>>> REPLACE` 块（Aider 风格）
- 解析 diff 块 → 精确替换，比 search_replace 更可靠
- 支持多块同时替换（一次调用修改多处）

**改动文件**:
- `python/tools/file_ops.py` — 新增 `apply_diff` action
- `python/agent/task_agent.py` — prompt 增加 diff 格式说明
- `python/skills/registry.py` — 更新 function calling schema

**预估**: +2~3 分（编辑准确率大幅提升）

### 3.2 编辑后自动修复循环

**目标**: 编辑失败时自动重试，最多 3 次

**方案**:
- `post_execute` 钩子检测语法/lint 错误
- 将错误信息反馈给 LLM，请求修复
- 记录修复历史，避免重复错误

**改动文件**:
- `python/tools/file_ops.py` — 增强 `post_execute`
- `python/agent/task_agent.py` — 编辑-验证循环逻辑

**预估**: +1 分（减少人工干预）

### 3.3 智能文件上下文注入

**目标**: 编辑文件时自动注入相关文件上下文

**方案**:
- 编辑 `foo.py` 时，自动检测 imports 的文件
- 将相关文件的签名（tree-sitter 提取）注入 prompt
- 让 LLM 理解跨文件依赖

**改动文件**:
- `python/agent/context_manager.py` — 新增 `_inject_related_context`
- `python/tools/repo_map.py` — 新增 `get_related_signatures(path)`

**预估**: +1 分（减少"不了解依赖"导致的错误）

---

## 4. Phase 6：项目理解深化（+2~3 分 → 83 分）

### 4.1 跨文件引用追踪

**目标**: 追踪函数/类的调用关系

**方案**:
- 利用 tree-sitter 构建简单的调用图
- 支持 `get_references(symbol)` 查询
- 编辑函数时，自动列出调用者

**改动文件**:
- `python/tools/lsp_client.py` — `find_references` + `go_to_definition` 实现引用查找
- `python/tools/repo_map.py` — `get_related_signatures(path)` 实现跨文件依赖签名注入

**实际实现**: 未创建独立 `symbol_tracker.py`，引用追踪由 `lsp_client.py`（ripgrep + regex fallback）承担，跨文件依赖注入由 `repo_map.get_related_signatures()` 承担。

**预估**: +1~2 分（理解代码影响范围）

### 4.2 项目类型自动检测

**目标**: 自动识别项目框架（React/Vue/Django/Flask 等）

**方案**:
- 扫描 `package.json` / `requirements.txt` / `pyproject.toml`
- 匹配已知框架特征
- 注入框架特定 prompt（如 React 用 hooks、Django 用 ORM）

**改动文件**:
- `python/agent/context_manager.py` — `detect_project_context()` 增强 + `_get_framework_guidance()` 新增

**实际实现**: 未创建独立 `project_detector.py`，功能集成在 `context_manager.py` 中。支持 20+ 框架检测（React/Vue/Angular/Svelte/Next.js/Nuxt.js/Django/Flask/FastAPI/Vite/Webpack/Tailwind/Redux/Zustand/Pinia/Vitest/Jest/Electron/Rust/Go/Java 等），并注入框架特定编码指导。

**预估**: +0.5~1 分（框架感知）

---

## 5. Phase 7：日志体系完善（基础设施）

### 5.1 目标

将日志覆盖率从 20% 提升到 80%+，核心模块 100% 覆盖。

### 5.2 日志规范

```python
# 统一日志格式
logger = logging.getLogger(__name__)

# 日志级别使用规范
logger.debug(...)   # 开发调试：变量值、中间状态
logger.info(...)    # 关键节点：任务开始/结束、API 调用
logger.warning(...) # 可恢复异常：降级、重试、默认值
logger.error(...)   # 不可恢复异常：捕获的 exception
```

### 5.3 需补充日志的模块（按优先级）

#### P0 — 必须补充

| 文件 | 需添加的日志点 |
|------|---------------|
| `agent/react_agent.py` | 对话流程、LLM 调用、工具选择 |
| `llm/client.py` | API 请求/响应/错误 |
| `routers/tasks.py` | 任务创建/执行/错误 |
| `tools/shell_tool.py` | 命令执行结果 |
| `tools/code_executor.py` | 代码执行结果 |

#### P1 — 建议补充

| 文件 | 需添加的日志点 |
|------|---------------|
| `routers/chat.py` | 聊天请求 |
| `knowledge/rag.py` | RAG 检索流程 |
| `vlm_client.py` | 图片描述调用 |
| `agent/context_manager.py` | 上下文压缩 |

#### P2 — 可选

| 文件 | 需添加的日志点 |
|------|---------------|
| 其他 Tools | 执行日志 |
| 其他 Routers | 请求日志 |

### 5.4 结构化日志（可选增强）

```python
# 结构化 JSON 日志（便于后续分析）
import structlog

logger = structlog.get_logger()
logger.info("task_started", task_id="xxx", workspace="/path/to", mode="coding")
```

### 5.5 改动文件清单

| 文件 | 优先级 | 改动类型 |
|------|--------|----------|
| `python/agent/react_agent.py` | P0 | 添加 ~20 条日志 |
| `python/llm/client.py` | P0 | 添加 ~15 条日志 |
| `python/routers/tasks.py` | P0 | 添加 ~15 条日志 |
| `python/tools/shell_tool.py` | P0 | 添加 ~10 条日志 |
| `python/tools/code_executor.py` | P0 | 添加 ~8 条日志 |
| `python/routers/chat.py` | P1 | 添加 ~10 条日志 |
| `python/knowledge/rag.py` | P1 | 添加 ~8 条日志 |
| `python/knowledge/vlm_client.py` | P1 | 添加 ~5 条日志 |
| `python/agent/context_manager.py` | P1 | 添加 ~8 条日志 |
| 其他 Tools / Routers | P2 | 各添加 ~5 条日志 |

**预估新增**: ~120 条日志语句（P0: ~68, P1: ~31, P2: ~20）

---

## 6. Phase 8：用户体验优化（+2~3 分 → 85 分）

### 6.1 Thought 过程可视化

**目标**: 前端展示 Agent 思考过程

**方案**:
- 前端渲染 `thought` 事件为折叠面板
- 展示 LLM 的推理链（为什么选择这个工具）
- 支持展开/折叠

**改动文件**:
- `src/features/task/TaskMessageList.tsx` — 渲染 thought 组件
- `src/features/task/TaskMessageList.module.css` — 样式

**预估**: +1 分（透明度提升）

### 6.2 工具执行进度条

**目标**: 长时间工具执行显示进度

**方案**:
- 工具执行超过 3 秒时显示进度指示
- Shell 命令实时输出（tail -f 风格）
- 支持取消正在执行的工具

**改动文件**:
- `src/features/task/TaskMessageList.tsx` — 进度组件
- `python/tools/shell_tool.py` — 流式输出支持

**预估**: +0.5 分（感知性能提升）

### 6.3 错误恢复建议

**目标**: 工具失败时提供具体修复建议

**方案**:
- 错误返回中包含 `suggestion` 字段
- 前端高亮显示建议
- 一键应用建议（如"重试"、"查看日志"）

**改动文件**:
- `python/tools/*.py` — 统一错误格式
- `src/features/task/TaskMessageList.tsx` — 渲染建议

**预估**: +0.5 分（降低使用门槛）

---

## 7. Phase 9：高级功能（+3~5 分 → 88+ 分）

### 7.1 LSP 集成（类型感知）

**目标**: 利用 LSP 获取类型信息、跳转定义

**方案**:
- 集成 `pyright` (Python) / `tsserver` (TypeScript)
- 支持 `go_to_definition`、`find_references`
- 编辑时自动类型检查

**改动文件**:
- 新增 `python/tools/lsp_client.py`
- `python/tools/repo_map.py` — 集成类型信息

**预估**: +2~3 分（专业级代码理解）

### 7.2 多文件批量编辑

**目标**: 一次操作修改多个相关文件

**方案**:
- 新增 `batch_edit` action
- LLM 输出多个文件的 diff
- 原子性执行（全部成功或全部回滚）

**改动文件**:
- `python/tools/file_ops.py` — 新增 `batch_edit`
- `python/agent/task_agent.py` — 批量编辑逻辑

**预估**: +1 分（效率提升）

---

## 8. 实施优先级

| 优先级 | 阶段 | 预估提升 | 难度 | 建议时间 |
|--------|------|----------|------|----------|
| **P0** | Phase 7 日志体系 | 基础设施 | 低 | 1~2 天 |
| **P0** | Phase 5.1 Diff 编辑 | +2~3 | 中 | 1~2 天 |
| P1 | Phase 5.2 自动修复循环 | +1 | 中 | 1 天 |
| P1 | Phase 5.3 智能上下文 | +1 | 中 | 1 天 |
| P2 | Phase 6.1 引用追踪 | +1~2 | 高 | 2~3 天 |
| P2 | Phase 8 UX 优化 | +1 | 低 | 1 天 |
| P3 | Phase 9.1 LSP | +2~3 | 高 | 3~5 天 |
| P3 | Phase 9.2 批量编辑 | +1 | 中 | 1 天 |

**推荐路径**: Phase 7 → 5.1 → 5.2 → 5.3 → 6.1 → 8.x → 9.x

---

## 9. 评分路线图

```
当前: 76 分 (v0.6.0)
  ↓ Phase 7 (日志体系)
77 分 (v0.6.1) — 可排查、可追踪
  ↓ Phase 5 (编辑升级)
80 分 (v0.7.0) — 接近 Aider 水平
  ↓ Phase 6 (项目理解)
83 分 (v0.8.0) — 专业编程助手
  ↓ Phase 8 (UX 优化)
85 分 (v0.9.0) — 生产可用
  ↓ Phase 9 (高级功能)
88+ 分 (v0.9.0) — 对标 Cursor/Devin
```

---

## 10. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-21 | V3 初始设计：编辑能力 + 项目理解 + 日志体系 |
| 0.7.0 | 2026-09-21 | V3 实施完成：日志体系完善(P0+P1) + apply_diff 编辑模式 + 跨文件引用追踪 + 智能上下文注入 |
| 0.8.0 | 2026-09-21 | V3 完整实施：编辑验证循环 + 项目检测增强 + P2日志补全 + LSP集成 + 批量编辑 + UX优化 |
| 0.9.0 | 2026-09-21 | V3 全功能完成：综合 88 分，对标 Cursor/Devin |
| 0.10.0 | 2026-09-21 | Agent Skills 行业标准支持：新增 §11，支持 SKILL.md 格式（与 Claude Code/Cursor/Codex 兼容）+ 三模式并存架构（内置 Tool / Agent Skills / 隔离 Skill）+ 任务 venv 运行脚本 + 依赖缓存优化 + 预装常用包 + 渐进式披露（3 级加载） |
| 0.11.0 | 2026-09-22 | 简化为两模式并存（内置 Tool + Agent Skills），移除 skill.yaml 隔离格式 |

---

## 11. Agent Skills 行业标准支持（v0.10.0 新增）

### 11.1 背景

2025 年 10 月，Anthropic 推出 **Agent Skills** 开放标准，2025 年 12 月正式发布为行业规范。截至 2026 年 9 月：

- **~60,000 个 Agent Skills** 可用
- **~40 个产品**支持该标准（Claude Code、Cursor、Codex、VS Code 等）
- 已成为 AI Agent 领域的事实标准

**核心特点**：
- Skill = 文件夹（`SKILL.md` + 可选 `scripts/`、`references/`、`assets/`）
- 脚本在 Agent 的环境中运行（非隔离）
- 渐进式披露（3 级加载，节省 token）

### 11.2 标准格式

```
my-skill/
├── SKILL.md              # 必需：元数据 + 指令
├── scripts/              # 可选：可执行脚本
│   ├── process.py
│   └── validate.sh
├── references/           # 可选：参考文档
│   └── api-guide.md
└── assets/               # 可选：模板、资源
    └── template.json
```

**SKILL.md 格式**：

```yaml
---
name: pdf-converter                    # 必需：小写+连字符，最长 64 字符
description: |                         # 必需：最长 1024 字符
  将文档转换为 PDF。当用户要求转换 HTML/Markdown 为 PDF 时使用。
compatibility: "Requires Python 3.8+ with weasyprint"  # 可选：环境依赖声明
---

# PDF Converter

## 何时使用
当用户要求将文档转换为 PDF 时触发。

## 执行步骤
1. 读取源文件
2. 运行转换脚本：`scripts/convert.py input.html output.pdf`
3. 返回生成的 PDF 路径

## 注意事项
- 如果缺少依赖，先安装：`pip install weasyprint`
```

### 11.3 与现有架构的融合

**挑战**：Agent Skills 标准假设脚本在 Agent 的运行时环境中运行，但我们的应用打包成 exe，需要特殊处理。

**解决方案**：在任务的 venv 中运行 Skill 脚本

```
┌─────────────────────────────────────────┐
│  打包的 exe                              │
│  ├── 捆绑的 Python（只读，不直接用）      │
│  └── Agent 逻辑                          │
└─────────────────────────────────────────┘
           │
           │ 创建任务时
           ▼
┌─────────────────────────────────────────┐
│  tasks/task-abc123/                      │
│  ├── .venv/          ← 任务的 Python 环境 │
│  │   ├── python.exe                      │
│  │   └── site-packages/                  │
│  │       ├── pandas                      │
│  │       └── weasyprint  ← 按需安装      │
│  └── workspace/                          │
└─────────────────────────────────────────┘
```

**执行流程**：
1. 创建任务时，`uv venv` 创建任务的 venv
2. Agent 读取 SKILL.md，发现需要运行 `scripts/convert.py`
3. 检查 `compatibility` 字段：`"Requires weasyprint"`
4. 在任务的 venv 中安装依赖：`uv pip install weasyprint`
5. 在任务的 venv 中运行脚本：`.venv/python scripts/convert.py`

### 11.4 依赖管理优化

**问题**：每个任务独立安装依赖会导致重复下载和安装，影响性能。

**优化方案**：

1. **依赖缓存**
   ```
   %APPDATA%/InkCube/
   ├── cache/
   │   └── packages/       ← 缓存下载的包
   │       ├── pandas-2.0.whl
   │       └── weasyprint-5.0.whl
   ```
   - 首次安装从网络下载，缓存到本地
   - 后续任务直接从缓存安装，秒级完成

2. **预装常用依赖**
   ```python
   # 在打包时，预装常用库到任务的默认模板
   PREINSTALLED_PACKAGES = [
       "pandas", "numpy", "requests", 
       "weasyprint", "pillow"
   ]
   ```
   - 创建任务时，预装这些包
   - 覆盖 80% 的 Skill 需求

3. **智能依赖检查**
   ```python
   async def check_and_install(skill_compatibility: str, task_venv: str):
       """检查并安装 Skill 需要的依赖"""
       # 解析 compatibility 字段
       required_packages = parse_compatibility(skill_compatibility)
       
       # 检查已安装的包
       installed = get_installed_packages(task_venv)
       
       # 安装缺少的
       missing = [p for p in required_packages if p not in installed]
       if missing:
           await install_packages(task_venv, missing, use_cache=True)
   ```

### 11.5 渐进式披露（3 级加载）

```
Level 1: 元数据发现（始终在上下文）
  └─ Agent 启动时只加载 name + description
  
Level 2: 指令激活（任务匹配时）
  └─ Agent 读取完整 SKILL.md
  
Level 3: 资源执行（按需）
  └─ 读取 references/、运行 scripts/
```

**好处**：可以安装数百个 Skills，但只加载相关的，节省 token。

### 11.6 两模式并存架构

```
┌────────────────────────────────────────────────────────┐
│                    Skill Registry                       │
│                                                        │
│  ┌── 内置 Tool ──┐  ┌── Agent Skills ──────────────┐ │
│  │ python/tools/ │  │ SKILL.md + scripts/           │ │
│  │ BaseTool      │  │ 任务 venv 运行                 │ │
│  │ in-process    │  │ 依赖按需安装                   │ │
│  │               │  │ 行业标准，兼容 60,000+ Skill  │ │
│  │ code_executor │  │                                │ │
│  │ shell         │  │ 用户安装的行业标准 Skills      │ │
│  │ file_ops      │  │                                │ │
│  └───────────────┘  └────────────────────────────────┘ │
│                                                        │
│  统一：                                                  │
│  • SkillRegistry 是唯一入口                             │
│  • 自动生成 function calling tools 列表                 │
│  • 统一执行协议                                         │
└────────────────────────────────────────────────────────┘
```

**判断逻辑**：
- 内置能力 → **内置 Tool**（in-process）
- 外部 Skill → **Agent Skills 模式**（SKILL.md + scripts/，任务 venv）

### 11.7 实现要点

1. **Skill 发现**
   ```python
   def discover_skill(skill_dir: str) -> dict | None:
       """发现 Skill，检查是否为 Agent Skills 标准"""
       if os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
           return {"type": "agent_skill", "path": skill_dir}
       return None
   ```

2. **Agent Skill 加载**
   ```python
   async def load_agent_skill(skill_dir: str) -> AgentSkillAdapter:
       """加载 Agent Skills 标准的 Skill"""
       skill_md = os.path.join(skill_dir, "SKILL.md")
       frontmatter, body = parse_skill_md(skill_md)
       
       return AgentSkillAdapter(
           name=frontmatter["name"],
           description=frontmatter["description"],
           compatibility=frontmatter.get("compatibility", ""),
           instructions=body,
           scripts_dir=os.path.join(skill_dir, "scripts"),
       )
   ```

3. **脚本执行**
   ```python
   async def execute_skill_script(
       script_path: str, 
       compatibility: str,
       context: SkillContext
   ) -> str:
       """在任务 venv 中执行 Skill 脚本"""
       # 检查并安装依赖
       await check_and_install(compatibility, context.venv_path)
       
       # 执行脚本
       result = await subprocess.run(
           [context.venv_python, script_path],
           capture_output=True,
           text=True,
           cwd=context.workspace_path,
       )
       return result.stdout
   ```

### 11.8 与外部生态兼容

**优势**：
- ✅ 可直接使用 60,000+ 现有 Agent Skills
- ✅ 与 Claude Code、Cursor、Codex 等平台兼容
- ✅ 符合行业标准，降低开发者学习成本

**注意事项**：
- ⚠️ 脚本在任务 venv 中运行，非完全隔离
- ⚠️ 依赖可能冲突（不同 Skill 需要不同版本的同一库）
- ⚠️ 首次运行需要安装依赖（通过缓存优化）
