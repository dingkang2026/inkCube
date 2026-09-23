# 通用智能体（任务）设计文档

> 版本: 0.13.0 | 日期: 2026-09-22 | 状态: 全部功能实施完成（P0+P1+P2+Wave 8-17+安全增强）；打包安装：路径抽象层 + 构建脚本 + 初始化逻辑已实施，待全流程测试
>
> **V2 升级设计**：→ [task-agent-v2-design.md](task-agent-v2-design.md) — Skill 框架统一 + Function Calling + 上下文管理 + Git 集成（目标 80-90 分）
>
> **V4 质量加固**：→ [task-agent-v4-design.md](task-agent-v4-design.md) — Bug 修复 + 编辑验证循环 + 上下文管理增强 + 依赖缓存 + 工程优化（目标 80 分）

## 1. 概述

在墨方左侧栏新增"任务"菜单项，提供通用编程智能体能力。与现有的产业洞察（chat）、知识库、产业报告并列，是独立功能模块，不干涉已有功能。

智能体具备代码编写与执行、文件操作、代码搜索、静态分析、网络搜索等编程能力，支持安装第三方 skill 扩展。每个任务运行在隔离的 Python 虚拟环境中，互不污染。

### 1.1 核心定位

- **通用编程智能体**：能写代码、跑代码、迭代调试，面向开发任务
- **独立模块**：新增菜单入口，不影响现有功能
- **可扩展**：支持第三方 skill 安装，uv 管理依赖
- **跨平台**：Windows / macOS / Linux 一致体验

### 1.2 技术选型

| 组件 | 选型 | 说明 |
|------|------|------|
| Python 运行时 | python-build-standalone | 预装独立 Python，跨平台，随安装包分发 |
| 依赖管理 | uv | Rust 编写，跨平台，~20MB，随安装包分发 |
| 代码搜索 | ripgrep (rg) | Rust 编写，~5MB，跨平台预编译二进制 |
| 代码检查/格式化 | ruff | Rust 编写，~15MB，跨平台预编译二进制 |
| 后端集成 | 扩展现有 FastAPI | 统一端口 8765，前端无感 |
| 执行隔离 | 每任务独立 venv | uv 基于预装 Python 创建，依赖隔离 |
| 记忆 | 会话级 + 自动压缩 | P0 不做跨会话长期记忆 |

---

## 2. 系统架构

### 2.1 整体架构

```
安装包内容（跨平台一致）：
├── inkcube-agent.exe / .app / AppImage   # PyInstaller 后端（已有）
├── python-build-standalone/              # 预装 Python（只读，不直接执行用户代码）
├── uv                                    # 依赖管理
├── rg                                    # ripgrep 代码搜索
└── ruff                                  # 代码检查/格式化
```

```
运行时架构：

React 前端
  │  所有请求走同一个端口（8765）
  ▼
FastAPI 后端（PyInstaller exe）
  ├── 已有路由：chat / knowledge / settings / models ...
  ├── 新增路由：tasks / skills / agent-memory
  │
  │  代码执行和 skill 运行时：
  │  subprocess 调用任务 venv 的 Python
  ▼
%APPDATA%/InkCube/（跨平台对应目录）
├── python/                     ← 预装 python-build-standalone（只读）
├── tasks/
│   ├── task-abc123/
│   │   ├── .venv/              ← uv 基于预装 Python 创建
│   │   ├── workspace/          ← 任务工作目录
│   │   └── metadata.json       ← 任务状态
│   └── task-def456/
│       ├── .venv/
│       ├── workspace/
│       └── metadata.json
├── skills/                     ← 第三方 Skill（Agent Skills 标准格式）
│   ├── hello-world/
│   │   ├── SKILL.md
│   │   └── scripts/
│   └── pdf-converter/
│       ├── SKILL.md
│       └── scripts/
├── models/                     ← AI 模型（已有）
├── files/                      ← 知识库文件（已有）
└── data.db                     ← 数据库（已有）
```

### 2.2 执行流程

```
用户输入任务描述
  → Agent 规划执行步骤
  → 调用 skill（code-executor / shell / file-ops / ...）
  → 后端 subprocess 调用任务 venv 的 Python
  → 捕获 stdout/stderr/返回值
  → Agent 分析结果，迭代
  → 输出最终结果
```

后端通过 subprocess 调用预装 Python：

```python
# 执行用户代码（在任务 venv 中）
subprocess.run(
    [task_venv_python, "-c", code],
    cwd=workspace_dir,
    capture_output=True,
    timeout=30
)

# 安装 skill 依赖
subprocess.run(
    [UV_PATH, "pip", "install", "-r", "requirements.txt"],
    env={"VIRTUAL_ENV": skill_venv_path}
)
```

### 2.3 预装 Python 的角色

预装 Python（python-build-standalone）**只作为 Python 发行版**，不直接用来执行用户代码。所有执行都在任务 venv 里：

```
创建任务时：
uv venv tasks/task-abc123/.venv --python <预装python路径>

执行代码时：
tasks/task-abc123/.venv/Scripts/python.exe -c "用户代码"
```

好处：
- 预装 Python 保持干净，不被任何 `pip install` 污染
- 任务间依赖完全隔离
- 任务可以独立安装不同版本的依赖

---

## 3. 内置 Skill

### 3.1 Skill vs Tool

Skill 是工作流驱动的复合能力（编排 + 领域知识 + 脚本），Tool 是原子函数调用。内置 skill 大部分封装现成工具，提供薄包装让 agent 能调用。

### 3.2 Skill 清单

#### P0 — 必须有（没这些不算编程智能体）

| Skill | 能力 | 底层实现 | 需捆绑 |
|-------|------|---------|--------|
| **code-executor** | 执行 Python 代码，捕获 stdout/stderr/返回值/耗时 | `subprocess.run([venv_python, ...])` | 否 |
| **shell** | 在 workspace 执行 shell 命令 | `subprocess.run(cmd)` | 否 |
| **file-ops** | 文件读写/创建/删除/移动 | Python 标准库 `pathlib` / `shutil` | 否 |
| **code-search** | 在代码库中搜索（grep/正则/符号查找） | ripgrep (rg) | 是，~5MB |
| **web-search** | 网络搜索 | Tavily API（已有） | 否 |
| **web-fetch** | 抓取网页内容，HTML→Markdown | httpx + html2text | 否 |

#### P1 — 补上

| Skill | 能力 | 底层实现 | 需捆绑 |
|-------|------|---------|--------|
| **code-lint** | 静态分析（ruff check / mypy / pyright） | ruff | 是，~15MB |
| **code-format** | 代码格式化（ruff format） | ruff（同上） | 同上 |
| **package-manager** | 依赖管理（uv pip install / requirements.txt 生成） | uv（已捆绑） | 否 |
| **project-scaffold** | 项目脚手架（创建项目结构、配置文件） | 模板 + 文件生成 | 否 |

#### P2 — 按需

| Skill | 能力 | 底层实现 | 需捆绑 |
|-------|------|---------|--------|
| **doc-generator** ✅ 已实现 | 生成文档（MD / DOCX / PDF） | weasyprint + pandoc + MS Office/LibreOffice | 否，pandoc 需系统安装 |

### 3.3 捆绑工具汇总

| 工具 | 大小 | 来源 | 跨平台 |
|------|------|------|--------|
| uv | ~20MB | astral-sh/uv releases | Windows/macOS/Linux |
| ripgrep (rg) | ~5MB | BurntSushi/ripgrep releases | Windows/macOS/Linux |
| ruff | ~15MB | astral-sh/ruff releases | Windows/macOS/Linux |
| python-build-standalone | ~50MB | indygreg/python-build-standalone | Windows/macOS/Linux |

总计额外捆绑约 ~90MB。

### 3.4 外部依赖（不捆绑）

| 工具 | 用途 | 安装方式 | 备注 |
|------|------|----------|------|
| pandoc | MD → DOCX 转换 | 用户自行安装 | 约 30MB，详见 §12.18 |
| MS Office / LibreOffice | DOCX → PDF 转换 | 用户自行安装 | 复用知识模块已有能力 |

---

## 4. 第三方 Skill

### 4.1 Skill 结构

采用 Agent Skills 行业标准格式（SKILL.md）：

```
skill/
├── SKILL.md            # 元数据 + 指令（必需）
├── scripts/            # 可执行脚本（可选）
│   └── main.py
├── references/         # 参考文档（可选）
└── assets/             # 模板、资源（可选）
```

> 详见 V3 设计文档 §11.2 Agent Skills 标准格式。

### 4.2 安装来源

仅支持：
- 本地路径

不支持 Git URL、skill 市场下载。

### 4.3 安装流程

```
用户操作：设置页 → Skill 管理 → 安装
  → 输入本地路径
  → 复制 Skill 目录到 %APPDATA%/InkCube/skills/{name}/
  → 注册到 skill registry
  → “任务”智能体可以发现并调用
  → 脚本在任务 venv 中运行，依赖按需安装
```

### 4.4 安全策略

- 内置 skill：全自动执行
- 第三方 skill：
  - ✅ 安装确认 API（`/skills/{id}/confirm`）
  - ✅ 安装时前端弹出确认对话框，显示来源类型和路径
  - ✅ 安装后需在 Skill 列表中点击"确认启用"方可使用
  - ✅ Agent 运行时强制拦截：未确认的第三方 skill 执行前弹出确认卡片（60s 超时自动拒绝）
  - ✅ Skill 卡片展示权限声明（permissions 字段）

---

## 5. 记忆系统

### 5.1 P0 范围：会话级 + 自动压缩

- **工作记忆**：当前任务的上下文、中间结果，存在内存中
- **会话记忆**：跨轮对话的上下文，存在 SQLite 中
- **自动压缩**：长对话达到阈值时自动摘要，避免 context overflow

### 5.2 不做的事

- P0 不做跨会话长期记忆
- 不做隐式记忆提取（从对话中自动学习模式）

---

## 6. 前端 UI

### 6.1 布局

"任务"页面是类 IDE 的复杂界面，不是简单 chat：

```
┌──────────────────────────────────────────────────────────┐
│ 任务列表（左）  │  对话 + 执行过程（中）    │ 文件树（右）  │
│                │                          │              │
│ ▶ Task 1       │  [对话消息流]             │ 📁 workspace │
│   Task 2       │  ┌─ 代码执行 ──────────┐  │  ├─ main.py  │
│   Task 3       │  │ def hello():        │  │  ├─ data.csv │
│                │  │     print("hi")     │  │  └─ output/  │
│                │  │ stdout: hi          │  │              │
│                │  │ exit code: 0        │  │              │
│ [+ 新任务]     │  └─────────────────────┘  │              │
│                │                          │              │
│                │  [输入框]                 │              │
└──────────────────────────────────────────────────────────┘
```

### 6.2 UI 组件

| 区域 | 组件 | 说明 |
|------|------|------|
| 左侧 | TaskList | 任务列表，创建/切换/删除任务 |
| 中间 | MessageFlow | 对话消息流，嵌入可折叠的代码执行结果 |
| 中间 | CodeBlock | 代码 view，语法高亮 |
| 中间 | ExecutionPanel | Agent 思考/行动步骤可视化（Thought → Action → Observation） |
| 中间 | MessageInput | 输入框，支持发送/取消 |
| 右侧 | FileTree | 浏览任务 workspace 文件结构 |

### 6.3 与现有 chat 的区别

| 维度 | 产业洞察（chat） | 任务 |
|------|-----------------|------|
| Agent 类型 | ReAct（Tavily + KnowledgeSearch） | 通用编程智能体 |
| 代码执行 | 无 | 有，每任务独立 venv |
| 文件操作 | 无 | 有，workspace 文件树 |
| 执行过程 | Thought/Action/Observation 文本流 | 结构化展示 + 代码块 + 输出面板 |
| 记忆 | 会话级 | 会话级 + 自动压缩 |

---

## 7. 数据库扩展

### 7.1 新增表

```sql
-- 任务表
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',  -- active / archived / deleted
    workspace_path TEXT NOT NULL,
    venv_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 任务消息表（会话记忆）
CREATE TABLE task_messages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    role TEXT NOT NULL,           -- user / assistant / tool
    content TEXT NOT NULL,
    tool_calls TEXT,              -- JSON: agent 的工具调用
    tool_results TEXT,            -- JSON: 工具返回结果
    created_at TEXT NOT NULL
);

-- Skill 注册表
CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    source_type TEXT NOT NULL,    -- builtin / local / git
    source_path TEXT,
    install_path TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
```

---

## 8. 新增 API 路由

| 路由 | 方法 | 说明 |
|------|------|------|
| `/tasks` | GET | 任务列表 |
| `/tasks` | POST | 创建任务（自动创建 venv + workspace） |
| `/tasks/{id}` | GET | 任务详情 |
| `/tasks/{id}` | DELETE | 删除任务 |
| `/tasks/{id}/messages` | GET | 获取任务消息历史 |
| `/tasks/{id}/chat` | POST | 发送消息（SSE 流式响应） |
| `/tasks/{id}/chat/cancel` | POST | 取消执行 |
| `/tasks/{id}/files` | GET | 获取 workspace 文件树 |
| `/tasks/{id}/files/{path}` | GET | 读取文件内容 |
| `/skills` | GET | 已安装 skill 列表 |
| `/skills/install` | POST | 安装第三方 skill |
| `/skills/{id}` | DELETE | 卸载 skill |

---

## 9. 分阶段实施

### P0 — 最小可用 ✅ 已完成

- ✅ 预装 Python + uv 打包分发（推迟，开发阶段用 conda 环境代替）
- ✅ 任务 CRUD + 独立 venv 创建
- ✅ 基础 skill：code-executor、shell、file-ops、code-search、web-search、web-fetch
- ✅ 会话级记忆 + 自动压缩（Snip + Context Collapse）
- ✅ 前端：任务列表 + 对话流 + 代码执行结果展示
- ✅ 第三方 skill 安装（仅本地路径）

### P1 — 补全能力 ✅ 已完成

- ✅ code-lint、code-format、package-manager、project-scaffold
- ✅ 文件树组件
- ✅ Agent 执行过程结构化展示
- ✅ 任务归档 / 取消归档
- ✅ 任务导出（zip）
- ✅ 选择已有目录作为工作区
- ✅ 知识库集成（knowledge-search skill）
- ✅ 项目级配置（inkcube.md）

### P2 — 增强

- doc-generator
- 更多 skill

---

## 10. 关键设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| Python 预装方案 | python-build-standalone 随安装包分发 | 跨平台一致，用户无需手动安装 |
| 代码执行环境 | 每任务独立 venv | 任务间依赖隔离，预装 Python 不被污染 |
| 后端架构 | 扩展现有 FastAPI | 统一端口，前端无感，减少进程管理复杂度 |
| 依赖管理 | uv | 速度快，跨平台，同时承担 skill 依赖安装 |
| 记忆范围 | 会话级 + 自动压缩 | P0 先做好基础，长期记忆后续迭代 |
| 第三方 skill 来源 | 本地路径 + Git URL | 暂不做 skill 市场 |
| 内置 skill 范围 | 面向编程，不含 data-analysis / knowledge-search / git-ops | 用户确认 |
| UI 形态 | 类 IDE（对话 + 代码 view + 文件树 + 执行过程） | 编程智能体需要丰富的信息展示 |

---

## 11. 工程最佳实践与开源参考

本章基于对 Aider、OpenHands、SWE-agent、Claude Code 四个成熟开源编程智能体的深入调研，提炼可直接指导 InkCube 实现的技术方案。

### 11.1 能力差距分析

| 维度 | 成熟产品（Cursor/Claude Code/Qoder） | InkCube 预期 | 差距 |
|------|--------------------------------------|-------------|------|
| 模型能力 | Claude Sonnet/Opus, GPT-4o | 可切换多模型 | 取决于模型选择 |
| 工具精度 | 行级 diff 编辑 | 需设计 | 中等，需刻意设计 |
| 自我修正 | 编辑→验证→失败→修复多轮循环 | 需实现 | 中等，prompt + 流程控制 |
| 上下文管理 | 智能选择相关文件，LSP 跳转 | 会话压缩 | 较大，核心难点 |
| Prompt 工程 | 数千 token 精炼 system prompt | 从零开始 | 可借鉴开源参考 |
| 编辑-验证循环 | 改完就跑测试看结果再改 | 需实现 | 中等，流程设计 |

**核心判断**：模型能力相同时，编程能力差异主要来自**工具设计**和 **prompt 工程**，不是架构。

---

### 11.2 Aider — 开源 AI 编程助手

> 项目地址：aider-chat/aider | 协议：Apache-2.0 | 语言：Python

#### 11.2.1 核心架构

Aider 采用 **ReAct（Reasoning + Acting）** 循环：

```
用户消息 → LLM 思考（thinking）→ 生成编辑指令 → 应用到文件 → 将结果反馈给 LLM → 循环
```

关键设计特点：
- **Git 原生集成**：每次编辑前自动 commit，编辑失败可 `git checkout` 回滚
- **多文件编辑**：一次对话可编辑多个文件，用 `path/to/file.py` 标记文件边界
- **双模型架构**（Architect 模式）：用便宜快速的模型（如 GPT-4o）做规划，用强模型（如 Claude Opus）做编辑，节省成本同时保证质量

#### 11.2.2 编辑格式 — SEARCH/REPLACE Block

Aider 最核心的设计是 **edit format**，决定 LLM 如何表达代码修改。默认使用 `whole` 格式（整文件输出），推荐 `diff` 格式（search/replace block）：

```
<<<<<<< SEARCH
def old_function():
    return 42
=======
def new_function():
    return 100
>>>>>>> REPLACE
```

**为什么不用整文件替换**：
- 整文件替换 token 消耗大（文件越大越贵）
- 容易丢失未修改部分的内容
- 无法精确 diff

**SEARCH/REPLACE 的优势**：
- 只传输变更部分，token 效率高
- 可以精确 diff 和 review
- LLM 只需关注要改的部分

**5 级模糊匹配容错**（Aider 的核心工程细节）：

LLM 生成的 SEARCH 块经常和实际文件有微小差异（缩进、空行、空格），Aider 实现了 5 级逐步放宽的匹配策略：

| 级别 | 策略 | 说明 |
|------|------|------|
| 1 | 精确匹配 | SEARCH 块与文件内容完全一致 |
| 2 | 去除空行后匹配 | 忽略空行差异 |
| 3 | 去除缩进后匹配 | 忽略缩进差异（统一 strip 后比较） |
| 4 | 模糊匹配（fuzz ratio ≥ 90%） | 允许 ≤10% 的字符差异 |
| 5 | 模糊匹配（fuzz ratio ≥ 80%） | 允许 ≤20% 的字符差异 |

如果 5 级都匹配失败，Aider 会：
1. 将失败信息反馈给 LLM，让它重试
2. 如果多次失败，回滚到上次 commit

**对 InkCube 的启示**：
- P0 实现 search/replace 编辑格式，至少支持前 3 级匹配
- 编辑失败时自动回滚（我们用 venv 快照或文件备份代替 git）
- 编辑格式直接写入 system prompt，让 LLM 知道如何输出

#### 11.2.3 Repo-map — 代码库结构感知

**问题**：LLM 上下文窗口有限，不可能把所有文件都塞进去。但 LLM 需要知道项目结构才能正确编辑。

**Aider 的方案**：用 tree-sitter 提取代码结构，生成精简的"地图"：

```
path/to/file.py:
├─ class MyClass
│  ├─ def method_a(self, arg1)
│  └─ def method_b(self)
├─ def standalone_func(x, y)
└─ CONSTANT = 42

path/to/other.py:
├─ class AnotherClass
│  └─ def process(self, data) → Result
└─ def helper()
```

**技术细节**：
- 使用 **tree-sitter** 解析多种语言的 AST（Python/JS/TS/Go/Rust/Java 等）
- 提取：函数签名、类定义、方法、装饰器、类型注解
- 用 **PageRank 算法** 对符号排序 — 被引用多的符号排在前面（更重要）
- 可配置 **token 预算**（默认 1024 tokens），超过预算时截断低优先级符号
- 每次 LLM 调用前动态重新生成（因为文件可能已被编辑）

**对 InkCube 的启示**：
- P0 可先用简单方案：`find` + 文件头 50 行扫描
- P1 引入 tree-sitter 做精确的代码结构提取
- repo-map 是 agent 理解大项目的关键能力，优先级应提高

#### 11.2.4 System Prompt 结构

Aider 的 system prompt 包含以下部分（按顺序）：

1. **角色定义**：你是一个 pair programmer，和用户一起编辑文件
2. **规则清单**：
   - 始终使用指定的 edit format
   - 每次只输出修改的部分
   - 不要输出未修改的代码
   - 如果需要创建新文件，输出完整内容
3. **工具/格式说明**：详细说明 SEARCH/REPLACE block 的语法
4. **文件列表**：当前正在编辑的文件（动态注入）
5. **Repo-map**：当前项目的代码结构摘要（动态注入）
6. **对话历史**：之前的消息和编辑结果

**关键设计**：
- System prompt 是**动态组装**的，不是固定模板
- 每次调用 LLM 前重新组装（文件列表变了、repo-map 变了）
- 规则用祈使句，简洁直接（"Always use..."、"Never output..."）

#### 11.2.5 自动提交策略

Aider 每次编辑文件前自动 `git commit`，好处：
- 编辑失败可以回滚
- 用户可以 `git diff` 查看每次编辑的变化
- 形成编辑历史，方便追溯

**对 InkCube 的启示**：
- 我们没有 git（workspace 不一定初始化 git），但可以用文件快照
- 每次编辑前备份原文件（`.bak` 或内存快照）
- 编辑失败时自动恢复备份

---

### 11.3 SWE-agent — 面向软件工程的 Agent

> 项目地址：princeton-nlp/SWE-agent | 协议：MIT | 语言：Python
> 背景：Princeton NLP 团队开发，专为 SWE-bench 设计

#### 11.3.1 核心理念 — ACI（Agent-Computer Interface）

SWE-agent 最重要的贡献是提出了 **ACI** 概念：

> 就像人机界面（HCI）设计决定了人使用软件的效率，Agent-Computer Interface（ACI）决定了 LLM agent 解决问题的效率。

**核心观点**：
- 给 LLM 一个和人类一样的终端界面（bash + 文本编辑器）是**低效**的
- LLM 需要**专门为 agent 设计的简化界面** — 命令更少、输出更结构化、错误更明确
- 工具设计比模型选择更重要：同一个模型，用好的 ACI 可以大幅提升表现

**SWE-agent 的实验数据**：
- 使用默认终端界面的 agent：SWE-bench 解决率 ~5%
- 使用 SWE-agent ACI 的同一模型：解决率 ~12%
- 差距来自**界面设计**，不是模型

#### 11.3.2 工具设计细节

SWE-agent 只提供了 **6 个命令**，刻意保持精简：

| 命令 | 功能 | 设计要点 |
|------|------|---------|
| `open <path>` | 打开文件 | 自动显示前 100 行，带行号 |
| `scroll_down` | 向下滚动 100 行 | 窗口式查看，不是一次加载全部 |
| `scroll_up` | 向上滚动 100 行 | 同上 |
| `goto <line>` | 跳转到指定行 | 快速定位 |
| `str_replace <old> <new>` | 替换字符串 | 自动验证语法 + 回滚 |
| `create <path> <content>` | 创建新文件 | 一步完成 |

**窗口式文件查看**（关键设计）：
- 每次只显示 100 行（一个"窗口"），带行号
- LLM 可以 scroll_up / scroll_down 移动窗口
- 为什么：LLM 处理超长文件效果差，限制视野反而提高精度

**str_replace 的自动验证**：
- 执行替换后，自动检查 Python 语法（`python -c "import ast; ast.parse(open(file).read())"`)
- 如果语法错误，**自动回滚**替换
- 将错误信息反馈给 LLM，让它修正

**对 InkCube 的启示**：
- 工具要少而精，不要给 LLM 太多选择
- 每个工具的返回值要结构化、信息丰富
- 文件查看用窗口式，不要一次加载整个大文件
- 编辑操作要有自动验证和回滚机制

#### 11.3.3 YAML 驱动的工具配置

SWE-agent 的工具定义用 YAML 配置，不是硬编码：

```yaml
tools:
  - name: str_replace
    description: "Replace a string in a file. Automatically validates syntax."
    parameters:
      - name: old_str
        type: string
        required: true
        description: "The string to find and replace"
      - name: new_str
        type: string
        required: true
        description: "The new string to replace with"
    validation:
      type: python_syntax_check
      on_failure: rollback
```

好处：
- 修改工具行为不需要改代码
- 可以针对不同场景配置不同工具集
- 工具描述直接生成到 system prompt

#### 11.3.4 五步执行模式（Recipe）

SWE-agent 的标准执行流程：

```
Step 1: 理解问题
  → 读 issue 描述
  → 搜索相关代码文件

Step 2: 定位问题
  → 打开相关文件
  → 滚动到问题区域
  → 理解上下文

Step 3: 制定方案
  → 分析根因
  → 思考修复策略

Step 4: 实施修复
  → 使用 str_replace 编辑
  → 自动验证语法

Step 5: 验证修复
  → 运行相关测试
  → 确认测试通过
```

**对 InkCube 的启示**：
- 在 system prompt 中引导 agent 遵循类似的结构化流程
- 但不是硬编码 — 是 prompt 中的建议，agent 可以灵活调整

---

### 11.4 OpenHands — 开放式 AI 软件开发 Agent

> 项目地址：All-Hands-AI/OpenHands | 协议：MIT | 语言：Python
> 前身：OpenDevin，2024 年更名

#### 11.4.1 核心架构 — 事件源（Event-Sourced）

OpenHands 最独特的设计是**事件源架构**：

```
所有状态变化都记录为不可变的事件（Event），追加到事件日志中。
Agent 本身是无状态的 — 它从事件日志重建上下文。
```

事件类型：
- **Action**：agent 执行的操作（编辑文件、运行命令、搜索代码等）
- **Observation**：操作的返回结果（命令输出、文件内容等）
- **MessageAction**：用户或 agent 的消息

```python
# 事件模型（Pydantic）
class Action(BaseModel):
    type: str           # "edit_file" | "run_command" | "search" | ...
    source: str         # "agent" | "user"
    content: dict       # 操作参数

class Observation(BaseModel):
    type: str           # "file_edit_result" | "command_output" | ...
    content: dict       # 操作结果
    parent_action_id: str  # 关联的 Action
```

**好处**：
- 完整的执行历史，可追溯、可回放
- 上下文压缩时只需对事件做摘要，不丢失关键信息
- 支持多 agent 协作（事件日志是共享状态）

**对 InkCube 的启示**：
- P0 用 SQLite 存消息历史（已设计），本质上就是简化版的事件日志
- 确保每条 tool_call 和 tool_result 都完整记录
- 未来如果要支持多 agent 协作，事件源架构是好基础

#### 11.4.2 Docker 沙箱执行

OpenHands 的代码执行在 Docker 容器中：
- 完全隔离，不影响宿主机
- 可以安装任意依赖
- 执行失败不影响 agent 进程

**对 InkCube 的启示**：
- 我们用 per-task venv 做隔离，比 Docker 轻量
- 但 venv 的隔离不如 Docker 彻底（共享文件系统）
- P0 用 venv 足够，P2 可以考虑 Docker 方案（面向高级用户）

#### 11.4.3 Condenser — 上下文压缩器

OpenHands 的 Condenser 负责在上下文过长时自动压缩：

**压缩策略**：
1. **选择性保留**：保留最近 N 条消息 + 关键 tool 结果
2. **摘要压缩**：对较早的消息用 LLM 生成摘要
3. **工具结果裁剪**：长输出（如大文件内容）只保留关键部分

```
原始上下文：
  msg1 (user) → msg2 (assistant + tool_call) → msg3 (tool_result: 500行)
  → msg4 (assistant + tool_call) → msg5 (tool_result) → ... → msg20

压缩后：
  [摘要: 用户要求修复 main.py 中的 bug，agent 已定位到第 42 行]
  → msg18 (assistant + tool_call) → msg19 (tool_result) → msg20
```

**触发条件**：
- 上下文 token 数超过阈值的 80% 时触发
- 压缩后保留最近 5 条完整消息 + 之前消息的摘要

**对 InkCube 的启示**：
- P0 必须实现类似机制（已在设计中规划）
- 建议阈值：上下文使用率达到 75% 时触发压缩
- 压缩时保留最近的完整消息 + 早期消息摘要
- Tool 结果（特别是长输出）要裁剪，只保留关键信息

#### 11.4.4 Microagents — 领域知识注入

OpenHands 用 **Microagents** 实现领域知识的模块化注入：

```markdown
<!-- microagent: python-best-practices.md -->
---
trigger:
  file_pattern: "*.py"
---

当编辑 Python 文件时：
- 使用 type hints
- 遵循 PEP 8
- 用 pytest 写测试
- 错误处理用具体异常类型，不要 bare except
```

- 每个 Microagent 是一个 Markdown 文件，包含触发条件和注入内容
- 当满足触发条件（如编辑 .py 文件）时，自动注入到 system prompt
- 用户可以自定义 Microagent

**对 InkCube 的启示**：
- 很好的领域知识注入机制
- P1 可以实现类似功能：根据当前任务类型自动注入相关指导
- 例如：检测到用户在写 Flask 项目时，自动注入 Flask 最佳实践

#### 11.4.5 卡住检测（Stuck Detection）

OpenHands 实现了 agent 卡住的自动检测：

**检测条件**：
- 连续 3 次执行相同的操作
- 连续 3 次得到相同的错误
- 在同一个文件上来回修改但没有进展

**处理方式**：
1. 向 agent 注入提示："你似乎卡住了，请尝试不同的方法"
2. 如果继续卡住，暂停执行，报告给用户

**对 InkCube 的启示**：
- P0 应实现基础卡住检测：连续 N 次相同错误 → 暂停并报告
- 避免 agent 无限循环浪费 token 和时间
- 建议最大重试次数：同一错误 3 次，总编辑-验证循环 5 次

---

### 11.5 Claude Code — Anthropic 的编程 Agent

> 来源：Anthropic 公开的技术博客、文档和开源 prompt 分析

#### 11.5.1 System Prompt 架构

Claude Code 的 system prompt 约 **34,000 tokens**，采用**模块化条件组装**：

```
System Prompt 结构（按顺序）：
├── 1. 身份与角色定义（~500 tokens）
├── 2. 工具使用规范（~8000 tokens）— 25+ 工具的详细说明
├── 3. 代码编辑指南（~3000 tokens）— 编辑策略、格式要求
├── 4. 安全与权限（~2000 tokens）— 什么能做、什么不能做
├── 5. Git 操作规范（~2000 tokens）— commit 策略、分支管理
├── 6. 代码风格与质量（~2000 tokens）— 编码标准
├── 7. 上下文管理策略（~1500 tokens）— 如何选择相关文件
├── 8. 验证与测试（~1500 tokens）— 编辑后必须验证
├── 9. 动态注入区域：
│   ├── 当前工作目录信息
│   ├── 项目结构摘要
│   ├── CLAUDE.md 内容（项目级指令）
│   └── 环境信息（OS、语言、工具版本）
└── 10. 行为约束（~1500 tokens）— 不要过度解释、不要创建不必要的文件等
```

**关键设计原则**：
- **条件组装**：不是所有部分都注入，根据上下文条件选择（如在 git 仓库中才注入 Git 规范）
- **缓存友好**：稳定的部分放前面（可利用 prompt cache），动态部分放后面
- **工具描述极其详细**：每个工具都有完整的使用说明、参数说明、示例、注意事项

**对 InkCube 的启示**：
- System prompt 要模块化，不要一个大字符串
- 工具描述要详细 — LLM 对工具的理解完全依赖描述文本
- 条件组装：根据当前任务类型、文件类型动态注入相关指导

#### 11.5.2 工具设计 — 25+ 工具的详细描述

Claude Code 的工具描述示例（Edit 工具）：

```
## Edit
Performs exact string replacements in files.

Usage:
- You must use your Read tool at least once before editing
- The edit will FAIL if old_string is not unique in the file
- Include at least 3 lines of context BEFORE and AFTER the target text
- Prefer editing existing files over creating new ones

Parameters:
- file_path (required): Absolute path to the file
- old_string (required): The exact text to replace
- new_string (required): The replacement text

Example:
old_string: "def calculate_total(items):\n    total = 0\n    for item in items:\n        total += item\n    return total"
new_string: "def calculate_total(items):\n    return sum(items)"
```

**特点**：
- 每个工具都有 **Usage** 说明（什么时候用、注意事项）
- 有 **Parameters** 说明（参数名、类型、是否必须）
- 有 **Example**（具体示例，减少 LLM 误解）
- 有 **Error cases**（什么情况下会失败）

**对 InkCube 的启示**：
- 每个 skill/tool 的描述必须包含：用途、参数、示例、错误情况
- 描述质量直接决定 LLM 使用工具的准确性
- 这是投入产出比最高的 prompt 工程工作

#### 11.5.3 上下文压缩 — 5 种策略

Claude Code 使用 5 种不同的上下文管理策略：

| 策略 | 触发条件 | 做法 |
|------|---------|------|
| **Disk Dump** | 工具输出过长 | 将长输出保存到文件，在上下文中只保留文件路径和摘要 |
| **Snip** | 单个工具结果超过阈值 | 截断输出，保留头部 + 尾部，中间用 `[...snipped...]` 代替 |
| **Micro Compact** | 上下文接近阈值 | 压缩早期工具结果为单行摘要 |
| **Context Collapse** | 上下文达到 80% | 将早期对话轮次压缩为摘要段落 |
| **Full Compact** | 上下文达到 95% | 用 LLM 对整个历史生成摘要，替换全部早期内容 |

**渐进式策略**：
```
正常 → Disk Dump/Snip（实时）
     → Micro Compact（接近阈值）
     → Context Collapse（80%）
     → Full Compact（95%）
     → 如果 Full Compact 后仍超限 → 拒绝继续，要求用户开新对话
```

**对 InkCube 的启示**：
- P0 实现 Snip + Context Collapse（两种最关键的策略）
- Snip：工具返回长输出时自动截断，保留头尾
- Context Collapse：上下文达到 80% 时压缩早期对话
- 渐进式压缩比一次性全压缩效果好

#### 11.5.4 CLAUDE.md — 项目级记忆

Claude Code 支持 `CLAUDE.md` 文件作为项目级指令：

```
项目根目录/CLAUDE.md：
- 项目概述和技术栈
- 编码规范和约定
- 构建和测试命令
- 常见陷阱和注意事项
```

- 每次启动时自动读取并注入 system prompt
- 相当于项目的"说明书"，让 agent 快速了解项目上下文
- 支持多层级：全局 `~/.claude/CLAUDE.md` + 项目级 `./CLAUDE.md`

**对 InkCube 的启示**：
- P1 可以实现类似机制：workspace 中的配置文件（如 `inkcube.md`）
- Agent 启动时自动读取，注入到 system prompt
- 让用户可以定制 agent 的行为

#### 11.5.5 验证引导（Verification Guidance）

Claude Code 的 system prompt 中有大量关于**验证**的指导：

```
## 验证规则（摘要）
- 编辑代码后，必须运行类型检查（如果有）和测试
- 不要假设编辑是正确的 — 必须验证
- 如果测试失败，修复后再验证
- 类型检查和测试通过 ≠ 功能正确，但这是能做到的最好验证
- UI 修改必须在浏览器中验证
- 不要在没有验证的情况下声称任务完成
```

**对 InkCube 的启示**：
- System prompt 必须强调"验证"的重要性
- 编辑代码 → 运行测试/lint → 确认通过 → 才能报告完成
- 这个行为主要靠 prompt 引导，不是代码逻辑

#### 11.5.6 Hook 系统 — 安全与扩展

Claude Code 的 Hook 系统允许在特定事件前后执行自定义脚本：

```
Hook 事件：
- PreToolUse: 工具执行前
- PostToolUse: 工具执行后
- OnError: 发生错误时

示例 Hook：
- PreToolUse("bash"): 检查命令是否安全（阻止 rm -rf /）
- PostToolUse("edit"): 自动运行 lint
```

**对 InkCube 的启示**：
- P1 可以实现简单的 hook 机制
- 最关键的 hook：shell 命令执行前的安全检查
- 例如：阻止 `rm -rf`、`DROP TABLE` 等危险操作

---

### 11.6 开源项目参考总览

| 项目 | 借鉴点 | 说明 |
|------|--------|------|
| **Aider** | 编辑格式（search/replace blocks + 5 级模糊匹配）、repo-map（tree-sitter + PageRank）、prompt 模板、双模型架构 | 开源，Python 实现，Apache-2.0 |
| **OpenHands** | 事件源架构、Condenser 上下文压缩、Microagents 领域知识注入、卡住检测、Docker 沙箱 | 开源，架构参考价值高，MIT |
| **SWE-agent** | ACI 概念（界面设计 > 模型选择）、窗口式文件查看、str_replace 自动验证+回滚、YAML 工具配置 | Princeton NLP，MIT |
| **Claude Code** | 模块化 system prompt、详细工具描述、5 种压缩策略、CLAUDE.md 项目记忆、验证引导、Hook 系统 | Anthropic，技术博客公开 |

---

### 11.7 InkCube 实现建议 — 分阶段

#### P0 必须做好的（决定基本能力下限）

**1. 编辑工具要精确 — 借鉴 Aider 的 search/replace**

```
<<<<<<< SEARCH
def old_function():
    return 42
=======
def new_function():
    return 100
>>>>>>> REPLACE
```

- 不用全文替换，用精确的 search/replace blocks
- 至少实现 3 级模糊匹配（精确 → 去空行 → 去缩进）
- 编辑失败自动回滚（文件备份机制）
- Token 消耗少，不容易出错，可以精确 diff

**2. 执行反馈要结构化**

```python
# 不要只返回 exit code
{"exit_code": 1, "output": "Error..."}

# 要返回丰富信息
{
    "exit_code": 1,
    "stdout": "...",
    "stderr": "...",
    "duration_ms": 234,
    "error_type": "SyntaxError",
    "error_line": 42,
    "suggestion": "Missing colon on line 42"
}
```

**3. 自我修正循环 — 借鉴 SWE-agent 的编辑-验证模式**

```
Agent 生成代码 → 执行 → 失败
  → Agent 看到错误信息 → 修复 → 再执行
  → 最多 N 轮（建议 5 轮），超过则报告给用户
```

关键：让循环自动化，不需要用户每步确认。

**4. System Prompt 精心设计 — 借鉴 Claude Code 的模块化结构**

- 模块化条件组装（不是固定模板）
- 工具描述要详细（用途 + 参数 + 示例 + 错误情况）
- 包含验证引导（编辑后必须验证）
- 包含代码风格偏好
- 参考 Aider/Claude Code 的 prompt 结构，针对 InkCube 工具集定制

**5. 上下文压缩 — 借鉴 OpenHands 的 Condenser**

- Snip：工具返回长输出时截断，保留头尾
- Context Collapse：上下文达到 80% 时压缩早期对话为摘要
- 保留最近 5 条完整消息 + 早期消息摘要

**6. 卡住检测 — 借鉴 OpenHands**

- 连续 3 次相同错误 → 暂停并报告给用户
- 避免无限循环浪费 token

#### P1 提升上限

**7. Repo-map / 代码结构感知 — 借鉴 Aider**

- 用 tree-sitter 提取项目结构（函数签名、类定义、调用关系）
- 生成精简的代码地图注入 prompt
- 让 agent 能快速理解大项目
- 可配置 token 预算

**8. 智能上下文选择**

- 不是把所有文件都塞进 context
- 根据当前任务自动选择相关文件
- 可以用 embedding 相似度，也可以用 LSP 的引用关系

**9. 项目级配置 — 借鉴 Claude Code 的 CLAUDE.md** ✅ 已完成

- ✅ workspace 中支持 `inkcube.md` 配置文件
- ✅ Agent 启动时自动读取注入 system prompt
- ✅ 让用户可以定制 agent 行为

**10. Hook 系统 — 借鉴 Claude Code**

- 工具执行前的安全检查（阻止危险命令）
- 工具执行后的自动操作（编辑后自动 lint）

#### P2 长期优化

**11. 评估体系**

- 准备一组编程任务测试集（从简单到复杂）
- 定期跑评估，跟踪能力变化
- 参考 SWE-bench 的思路，针对 InkCube 场景定制

**12. 双模型架构 — 借鉴 Aider 的 Architect 模式** — 🔨 待实施

- 用便宜快速的模型做规划和上下文选择
- 用强模型做代码编辑和复杂推理
- 节省成本同时保证质量

---

### 11.8 模型选择建议

编程能力高度依赖底层模型。建议：

| 场景 | 推荐模型 | 说明 |
|------|---------|------|
| 简单脚本/快速任务 | GLM-4-Flash / GPT-4o-mini | 成本低，响应快 |
| 复杂编程/多文件协作 | Claude Sonnet / GPT-4o | 能力强，适合生产级任务 |
| 超长上下文/大型项目 | Claude Opus / Gemini 1.5 Pro | 上下文窗口大 |

设置页应支持按任务类型推荐模型，用户可自由切换。

---

### 11.9 目标

达到成熟编程智能体（Cursor/Claude Code）**70-80%** 的能力水平。关键变量：
- 模型选择（用强模型可大幅缩小差距）
- 工具设计精度（精确编辑 + 结构化反馈）
- 自我修正流程（编辑-验证循环）
- Prompt 工程质量（借鉴开源项目并持续迭代）

---

## 12. 待明确的设计问题与实施指南

本章列出当前设计尚未详细说明、但实现人员必然会遇到的问题，并提供推荐方案。实现前需逐一确认。

### 12.1 工作区管理

**问题：创建任务时，用户能否选择已有目录作为工作区？**

**推荐方案**：

| 模式 | 说明 | P0 是否支持 |
|------|------|------------|
| **新建工作区** | 每次创建任务自动生成空白工作区（`%APPDATA%/InkCube/tasks/task-{id}/workspace/`） | ✅ 是 |
| **选择已有目录** | 用户指定本地目录作为工作区，agent 直接在该目录操作 | ✅ 已完成 |
| **复制模板** | 从预设模板创建工作区（如 Flask 项目模板、数据分析模板） | ❌ 否，P2 考虑 |

**P0 实现细节**：
- 工作区路径对用户**可见但不可编辑**（显示在 UI 右侧文件树顶部）
- 用户可以手动打开工作区目录（右键菜单 → "在文件管理器中打开"）
- 工作区初始为空目录，用户通过文件上传或 agent 创建文件

**P1 扩展**：
- 支持"选择已有目录"模式，agent 直接操作用户的项目目录
- 需要额外的安全确认（"agent 将修改 `/path/to/your/project`，确认？"）
- 需要处理目录权限问题

---

### 12.2 任务类型与通用性

**问题：任务是否只限于编程？能否用于写作、翻译、数据分析等通用任务？**

**推荐方案**：

当前设计是**通用编程智能体**，但实际使用中用户可能用来做非编程任务。建议：

| 任务类型 | 是否需要 venv | 是否需要代码执行 | P0 处理方式 |
|---------|--------------|----------------|------------|
| **编程任务**（写代码、调试、项目搭建） | ✅ 是 | ✅ 是 | ✅ 完整支持 |
| **通用任务**（写作、翻译、总结、分析） | ❌ 否 | ❌ 否 | ✅ 支持，但**仍创建 venv**（统一逻辑，避免复杂分支） |
| **数据分析**（处理 CSV、生成图表） | ✅ 是 | ✅ 是 | ✅ 支持（需安装 pandas/matplotlib 等） |

**实现建议**：
- **P0 统一创建 venv**，不管任务类型。理由：
  - 简化逻辑，不需要判断任务类型
  - 用户可能在"写作任务"中突然想跑段代码
  - venv 创建成本低（<1 秒）
- **不在 UI 中区分任务类型**，统一叫"任务"
- System prompt 根据用户输入自动调整（检测到编程意图时注入代码相关指导）

**P1 优化**：
- 根据任务内容自动推荐模型（编程任务推荐强模型，通用任务推荐快模型）
- 提供“任务模板”（编程、写作、数据分析等预设 prompt）

> **2026-09-19 更新**：已设计任务双模式（编程/通用），详见 §12.17.2。新建和任务中均可切换。

---

### 12.3 文件上传

**问题：用户能否上传文件到工作区？如何上传？**

**推荐方案**：

| 功能 | P0 是否支持 | 说明 |
|------|------------|------|
| **文件上传** | ✅ 是 | 用户通过 UI 上传文件到工作区 |
| **拖拽上传** | ✅ 是 | 支持拖拽文件到对话区域上传 |
| **文件夹上传** | ❌ 不做 | — |
| **文件大小限制** | 单文件 ≤ 50MB | 防止占用过多存储 |
| **格式限制** | 无限制 | 支持任意文件格式 |
| **上传时机** | 任务创建后任意时刻 | 可在对话中途上传 |

**实现细节**：

**前端**：
```
对话输入框左侧添加"上传"按钮（📎 图标）
  → 点击弹出文件选择器
  → 或拖拽文件到对话区域
  → 上传进度条（大文件）
  → 上传完成后在对话中显示文件卡片（文件名 + 大小 + 删除按钮）
```

**后端 API**：
```
POST /tasks/{id}/files/upload
  Content-Type: multipart/form-data
  Body: file(s)
  Response: { "uploaded": ["file1.py", "data.csv"], "workspace_path": "..." }
```

**Agent 感知**：
- 上传完成后，自动注入消息到 agent 上下文：
  ```
  [系统] 用户上传了文件: file1.py (1.2KB), data.csv (45KB)
  文件已保存到工作区: /path/to/workspace/
  ```
- Agent 可以立即使用这些文件（读取、分析、修改）

---

### 12.4 打包与安装

**问题：新增的 ~90MB 捆绑包如何处理？跨平台安装包格式？**

**状态：推迟处理。** 开发阶段使用 conda 环境代替预装 Python，捆绑工具（uv/rg/ruff）通过 PATH 或 resources 目录查找。

**推荐方案**：

**安装包结构**：
```
InkCube-Setup-{version}-{os}-{arch}/
├── inkcube.exe / InkCube.app / inkcube        # 主程序（PyInstaller 打包，已有）
├── resources/
│   ├── python/                                 # python-build-standalone（~50MB）
│   ├── uv.exe / uv                             # uv 依赖管理（~20MB）
│   ├── rg.exe / rg                             # ripgrep（~5MB）
│   ├── ruff.exe / ruff                         # ruff（~15MB）
│   └── app/                                    # 前端静态文件（已有）
└── uninstall.exe                               # 卸载程序（Windows）
```

**跨平台安装包格式**：

| 平台 | 格式 | 工具 | 说明 |
|------|------|------|------|
| Windows | `.exe` 安装包 | NSIS / Inno Setup | 双击安装，自动创建快捷方式 |
| macOS | `.dmg` | create-dmg | 拖拽安装到 Applications |
| Linux | `.AppImage` / `.deb` / `.rpm` | appimage-builder / dpkg | AppImage 免安装，deb/rpm 包管理 |

**首次启动初始化**：
```
首次启动 InkCube：
1. 解压安装包到 %APPDATA%/InkCube/（Windows）或 ~/.inkcube/（macOS/Linux）
2. 设置预装 Python 为可执行（chmod +x，macOS/Linux）
3. 验证 uv / rg / ruff 可正常执行
4. 创建默认数据目录（tasks/ skills/ models/）
5. 初始化数据库（创建 tasks / skills 表）
6. 显示"初始化完成"，进入主界面
```

**更新策略**：
- **主程序更新**：整个安装包覆盖安装（包括捆绑工具）
- **捆绑工具独立更新**：P1 考虑提供"检查更新"功能，单独更新 uv/rg/ruff
- **模型更新**：通过现有模型管理器下载

**安装包大小预估**：
- 当前 InkCube（不含任务功能）：~150MB
- 新增捆绑工具：~90MB
- 总计：~240MB（可接受范围）

---

### 12.5 安全控制

**问题：如何防止 agent 执行危险操作？如何让用户确认？**

**推荐方案**：

**安全分级**：

| 操作类型 | 风险等级 | 处理方式 | 状态 |
|---------|---------|---------|------|
| **只读操作** | 低 | 自动执行 | ✅ 已实现 |
| **工作区内写操作** | 中 | 自动执行，UI 显示操作日志 | ✅ 已实现 |
| **代码执行** | 中 | 自动执行（在 venv 中） | ✅ 已实现 |
| **Shell 命令** | 高 | 设置项 `task_auto_confirm_shell` 控制 | ✅ 已实现 |
| **危险命令** | 极高 | 黑名单硬编码阻止 | ✅ 已实现 |
| **网络请求** | 中 | 通过 Tavily API | ✅ 已实现 |
| **第三方 skill** | 高 | 安装确认 + 运行时强制拦截 + 权限展示 | ✅ 已实现 |

**已实现的安全措施**：
- ✅ 路径穿越防护：所有工具 resolve() + startswith() 校验，禁止访问工作区外文件
- ✅ 资源限制：代码执行超时 30s、输出截断、并发任务数限制
- ✅ 危险命令黑名单：硬编码阻止 `rm -rf`、`DROP TABLE` 等
- ✅ Shell 命令内联确认 UI：允许/拒绝，30s 超时自动拒绝
- ✅ 第三方 skill 首次执行确认（Agent 运行时强制拦截，60s 超时）
- ✅ Shell 命令域名白名单（智能模式下匹配白名单域名的网络命令自动放行）
- ✅ Skill 安装时权限展示（解析 skill.yaml 的 permissions 字段）
- ✅ Skill 安装确认对话框（安装前显示来源类型、路径、安全警告）

**用户确认 UI**（✅ 已实现）：

```
┌─────────────────────────────────────────┐
│ Agent 请求执行 Shell 命令：              │
│                                         │
│ pip install requests                    │
│                                         │
│ [允许]  [拒绝]  [始终允许此类操作]       │
└─────────────────────────────────────────┘
```

- 确认弹窗**内联在对话流中**，不是模态对话框（避免打断用户）
- 用户可以选择"始终允许此类操作"（写入白名单）
- 超时 30 秒未确认 → 自动拒绝，agent 收到"用户拒绝执行"

**危险命令黑名单**（✅ 已实现）：
```python
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",           # 删除根目录
    r"format\s+[A-Z]:",        # 格式化磁盘
    r"DROP\s+TABLE",           # 删除数据库表
    r"DROP\s+DATABASE",        # 删除数据库
    r":\(\)\s*\{.*\}\s*;",    # Shellshock 攻击
    r"mkfs\.",                 # 格式化文件系统
    r"dd\s+if=.*of=/dev/",    # 覆盖设备文件
]
```

**文件系统访问限制**：
- ✅ agent 只能访问工作区目录（路径穿越防护已实现）
- ✅ 支持“选择已有目录”模式，限制为指定目录

**网络访问控制**：
- ✅ web-search / web-fetch：通过 Tavily API
- ✅ Shell 命令中的网络请求域名白名单（设置项 `task_shell_domain_whitelist`，智能模式自动放行匹配命令）

**第三方 skill 安全**：
- ✅ 提供"卸载"功能
- ✅ 安装确认 API（`/skills/{id}/confirm`）
- ✅ Agent 运行时强制拦截未确认的第三方 skill（confirm_skill SSE 事件 + 确认/拒绝 API）
- ✅ 安装时显示权限请求（Skill 卡片展示 permissions badges）
- ✅ 安装前确认对话框（显示来源类型、路径、安全警告）

---

### 12.6 资源限制

**问题：如何防止 agent 消耗过多资源？**

**推荐方案**：

| 资源 | 限制 | 处理方式 | 状态 |
|------|------|---------|------|
| **单次代码执行超时** | 30 秒 | 超时后终止进程，返回“执行超时” | ✅ 已实现 |
| **单次输出大小** | Prompt 截断 8000 字符 | Snip 策略，保留头尾 | ✅ 已实现 |
| **单任务工作区大小** | 1GB | 超过后拒绝写入 | ✅ 已实现 |
| **并发任务数** | 3 个 | 超过后提示 | ✅ 已实现 |
| **单任务对话轮次** | 无硬限制 | 通过上下文压缩管理 | ✅ 已实现 |
| **长时间任务** | 无超时 | 用户可手动取消 | ✅ 已实现 |

**实现细节**：

**代码执行超时**：
```python
subprocess.run(
    [venv_python, "-c", code],
    timeout=30,  # 30 秒超时
    capture_output=True
)
# 超时后抛出 TimeoutExpired，捕获并返回错误
```

**输出大小限制**：
```python
MAX_OUTPUT_SIZE = 1 * 1024 * 1024  # 1MB
if len(stdout) > MAX_OUTPUT_SIZE:
    stdout = stdout[:MAX_OUTPUT_SIZE//2] + "\n[...truncated...]\n" + stdout[-MAX_OUTPUT_SIZE//2:]
```

**并发任务限制**：
- 前端：超过 3 个活跃任务时，"新建任务"按钮禁用
- 后端：API 返回 429 Too Many Requests

---

### 12.7 错误处理与恢复

**问题：各种异常情况如何处理？**

**推荐方案**：

| 异常场景 | 处理方式 | 用户提示 |
|---------|---------|---------|
| **venv 创建失败** | 重试 1 次，仍失败则任务创建失败 | "创建任务环境失败，请重试" |
| **Python 进程崩溃** | 捕获异常，返回错误信息 | "代码执行失败：{error}" |
| **Agent 无响应** | 30 秒无输出 → 显示"思考中..."，60 秒 → 提示取消 | "Agent 响应缓慢，[继续等待] [取消]" |
| **API 调用失败** | 重试 2 次，仍失败则报告错误 | "AI 服务暂时不可用，请稍后重试" |
| **网络中断** | web-search/web-fetch 失败 → 返回错误 | "网络连接失败，请检查网络" |
| **磁盘空间不足** | 捕获 OSError，提示用户清理 | "磁盘空间不足，请清理后重试" |
| **文件被占用** | Windows 常见，重试 1 次 | "文件被占用，请关闭后重试" |

**执行日志**：
- 所有操作记录到 `tasks/task-{id}/execution.log`
- 包含：时间戳、操作类型、输入、输出、耗时、错误信息
- 用户可在 UI 中查看（"查看执行日志"按钮）
- 用于问题排查和用户反馈

---

### 12.8 用户体验细节

**问题：任务执行中的交互细节？**

**推荐方案**：

| 功能 | P0 是否支持 | 说明 |
|------|------------|------|
| **取消执行** | ✅ 是 | 点击“停止”按钮，终止当前操作 |
| **暂停/恢复** | ✅ 已完成 | 保存中间状态，稍后恢复 |
| **任务导出** | ✅ 已完成 | 导出工作区为 zip |
| **执行历史查看** | ✅ 是 | 对话流中显示所有操作和结果 |
| **重新执行** | ✅ 已完成 | 对历史操作重新执行 |
| **任务克隆** | ❌ 不做 | — |

**取消执行**：
```
用户点击"停止"按钮
  → 后端终止当前 subprocess（SIGTERM / taskkill）
  → Agent 收到"用户取消执行"
  → Agent 停止后续操作
  → UI 显示"已取消"
```

**执行状态显示**：
```
对话流中每个操作显示状态：
  ⏳ 执行中...
  ✅ 完成（耗时 1.2s）
  ❌ 失败（错误信息）
  ⚠️ 已取消
```

---

### 12.9 与现有功能的交互

**问题：任务功能与现有功能（知识库、模型管理）如何交互？**

**推荐方案**：

| 交互场景 | P0 是否支持 | 说明 |
|---------|------------|------|
| **任务访问知识库** | ✅ 已完成 | 新增 knowledge-search skill |
| **知识库引用任务结果** | ❌ 否 | 暂不支持，任务结果不自动入库 |
| **模型设置共享** | ✅ 是 | 任务功能使用全局模型设置，可在设置页切换 |
| **任务使用外部 API** | ✅ 是 | web-search 使用现有 Tavily API key |

**模型设置**：
- ✅ 任务功能在设置页新增“任务模型”选项（独立于“对话模型”）
- 默认与“对话模型”相同，用户可单独配置
- 理由：编程任务可能需要更强的模型

**知识库集成** ✅ 已完成：
- ✅ 新增 `knowledge-search` skill，调用现有知识库检索 API
- ✅ Agent 可以搜索知识库中的文档，辅助编程

---

### 12.10 数据生命周期

**问题：任务数据如何管理？存储配额？清理策略？**

**推荐方案**：

| 功能 | P0 是否支持 | 说明 |
|------|------------|------|
| **任务删除** | ✅ 是 | 删除任务时清空工作区和 venv |
| **任务归档** | ✅ 已完成 | 归档后不显示但保留数据，可取消归档 |
| **自动清理** | ✅ 已完成 | 30 天未访问自动归档 |
| **存储配额** | ✅ 已完成 | 限制总任务数 + 工作区大小 |
| **任务导出** | ✅ 已完成 | 导出工作区为 zip |

**任务删除流程**：
```
用户点击"删除任务"
  → 确认弹窗："删除后无法恢复，确认？"
  → 用户确认
  → 后端删除：
    1. 终止任务相关的所有 subprocess
    2. 删除工作区目录（tasks/task-{id}/workspace/）
    3. 删除 venv 目录（tasks/task-{id}/.venv/）
    4. 删除数据库记录（tasks / task_messages 表）
  → 前端刷新任务列表
```

**存储位置**：
- 任务数据：`%APPDATA%/InkCube/tasks/`（Windows）或 `~/.inkcube/tasks/`（macOS/Linux）
- 与主程序分离，卸载时可选择是否保留

---

### 12.11 配置项

**问题：用户可以配置什么？默认值是什么？**

**推荐方案**：

**设置页新增"任务"选项卡**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| **任务模型** | 与对话模型相同 | 任务使用的 AI 模型 |
| **代码执行超时** | 30 秒 | 单次代码执行的最大时间 |
| **自动确认 Shell 命令** | 关闭 | 开启后 shell 命令无需确认（高级用户） |
| **显示执行日志** | 开启 | 是否在对话流中显示详细操作日志 |
| **工作区路径** | `%APPDATA%/InkCube/tasks/` | 任务数据存储位置（高级设置） |

**高级配置（配置文件，非 UI）**：
```yaml
# %APPDATA%/InkCube/config.yaml
tasks:
  max_concurrent: 3              # 最大并发任务数
  max_workspace_size_mb: 1024    # 单任务工作区大小限制
  default_timeout: 30            # 默认执行超时（秒）
  auto_confirm_shell: false      # 是否自动确认 shell 命令
  log_level: "info"              # 日志级别
```

---

### 12.12 日志与调试

**问题：如何帮助用户排查问题？如何收集反馈？**

**推荐方案**：

**执行日志**：
- 每个任务独立日志文件：`tasks/task-{id}/execution.log`
- 格式：`[时间戳] [级别] [操作类型] 消息`
- 示例：
  ```
  [2026-09-19 14:32:15] [INFO] [code_executor] 执行 Python 代码
  [2026-09-19 14:32:16] [INFO] [code_executor] 执行成功，耗时 1.2s
  [2026-09-19 14:32:20] [ERROR] [shell] 命令失败：pip install invalid-package
  ```

**应用日志**：
- 全局日志：`%APPDATA%/InkCube/logs/inkcube.log`
- 记录：API 请求、错误、性能指标
- 日志轮转：保留最近 7 天

**用户反馈**：
- UI 中提供"报告问题"按钮
- 点击后自动收集：当前任务日志 + 应用日志 + 系统信息
- 生成 zip 包，用户可发送给支持团队

---

### 12.13 国际化

**问题：如何支持多语言？**

**推荐方案**：

| 维度 | 实现状态 |
|------|----------|
| **Agent 响应语言** | ✅ 跟随用户输入语言（中文问中文答） |
| **UI 界面** | ✅ 已实现中英双语 i18n |
| **文件名编码** | ✅ 支持 UTF-8，允许中文文件名 |
| **错误信息** | ✅ 已实现双语 |

**System prompt 中的语言指导**：
```
始终使用用户提问的语言回复。如果用户用中文提问，用中文回复。
代码注释和文档字符串使用英文（除非用户明确要求中文）。
```

---

### 12.14 性能考虑

**问题：大文件、大量文件、长对话的性能如何处理？**

**推荐方案**：

| 场景 | 处理方式 |
|------|---------|
| **大文件（>1MB）** | 读取时截断，保留前 1000 行 + 后 100 行 |
| **大量文件（>1000 个）** | 文件树组件懒加载，只显示第一层 |
| **长对话（>100 轮）** | 上下文压缩（见 11.4.3） |
| **大输出（>1MB）** | 截断输出，保留头尾（见 12.6） |

**文件树性能**：
```
首次加载：只显示第一层目录
用户展开目录时：异步加载子目录
文件数量 >1000 时：显示"文件过多，请使用搜索"
```

**代码搜索性能**：
- ripgrep 本身性能极好，无需额外优化
- 搜索结果限制：最多显示 100 条匹配

---

### 12.15 测试策略

**问题：如何测试任务功能？用户如何验证自己的任务？**

**推荐方案**：

**开发阶段测试**：
- **单元测试**：每个 skill 独立测试（mock subprocess）
- **集成测试**：测试 skill 组合（如 code-executor + file-ops）
- **E2E 测试**：模拟完整任务流程（创建任务 → 上传文件 → 执行代码 → 查看结果）

**用户验收测试**：
- 提供"示例任务"（预设的编程任务，验证功能正常）
- 示例：
  - "创建一个 Python 脚本，计算斐波那契数列"
  - "上传 data.csv，分析数据并生成图表"
  - "修复 main.py 中的 bug"

**性能基准**：
- 创建任务时间：< 2 秒
- 代码执行响应时间：< 1 秒（不含执行时间）
- 文件上传速度：> 10MB/s（本地）

---

### 12.16 实现优先级总结

**P0 必须实现**（最小可用产品）：✅ 全部完成
- ✅ 新建工作区（自动生成）
- ✅ 统一任务类型（不区分编程/通用）
- ✅ 文件上传（单文件 + 拖拽）
- ✅ 安全控制（路径穿越防护 + 资源限制）
- ✅ 资源限制（超时 + 输出截断）
- ✅ 错误处理（基本异常捕获）
- ✅ 取消执行
- ✅ 任务删除
- ✅ 基础配置（模型选择 + 超时）
- ✅ 执行日志

**P1 提升体验**：✅ 全部完成
- ✅ 选择已有目录作为工作区
- ✅ 任务归档 / 取消归档
- ✅ 任务导出（zip）
- ✅ 知识库集成（knowledge-search skill）
- ✅ 项目级配置（inkcube.md）
- ✅ code-lint、code-format、package-manager、project-scaffold
- ✅ 文件树组件
- ✅ Agent 执行过程结构化展示

**Wave 8-13 增强**：✅ 全部完成
- ✅ 危险命令黑名单（shell_tool.py）
- ✅ 工作区大小限制 + 存储配额 + 任务数上限
- ✅ 自动清理（30 天归档）
- ✅ 重新执行（历史操作重放）
- ✅ Shell 命令内联确认 UI（允许/拒绝，30s 超时）
- ✅ 第三方 skill 首次确认
- ✅ 模糊匹配 Level 4-5（difflib SequenceMatcher）
- ✅ 编辑失败自动回滚（.bak 备份 + .py 语法检查）
- ✅ 双模型架构（Planner + Editor）
- ✅ 暂停/恢复（Agent 中间状态持久化）
- ✅ Hook 系统（pre_execute / post_execute）

**安全增强**：✅ 全部完成
- ✅ 第三方 skill 运行时强制确认（confirm_skill SSE + 前端确认卡片）
- ✅ Shell 命令域名白名单（设置 UI + 智能模式自动放行）
- ✅ Skill 安装时权限展示（permissions 解析 + badge 展示）
- ✅ 外部依赖检测 UI（pandoc/libreoffice/git/ffmpeg）
- ✅ Skill 安装确认对话框（安装前弹出确认）

**P2 — 增强（待实施）**：

- 更多 skill（按需扩展）

**P2 — 已完成**：

- ✅ doc-generator（MD → PDF / MD → DOCX / DOCX → PDF）— 详见 §12.17.1
- ✅ 任务双模式（编程模式 / 通用模式）— 详见 §12.17.2
- ✅ 文件只读预览 — 详见 §12.17.3

**明确不做**：
- ~~打包安装~~ — 推迟
- ~~任务克隆~~ — 不做
- ~~文件夹上传~~ — 不做

---

### 12.17 后续功能设计

> 以下为 Wave 8-13 完成后确定的后续功能方向。

#### 12.17.1 doc-generator — 文档格式转换 ✅ 已实现

**工具选型**：

| 转换路径 | 工具 | 说明 |
|----------|------|------|
| **MD → PDF** | `weasyprint` | 纯 Python，跨平台好，中文需配置字体 |
| **MD → DOCX** | `pandoc` | 跨平台标准工具，格式保真度高，需系统安装 pandoc |
| **DOCX → PDF** | 复用知识模块 `doc_parser.py` | Windows: MS Office COM；其它: LibreOffice headless |

**优先级**：MD → PDF > MD → DOCX > DOCX → PDF

**实现位置**：`python/tools/doc_generator.py`，作为 Task Agent 的 tool 注册

**MD → PDF 细节**：
- 使用 `weasyprint`（纯 Python，无外部依赖）
- 中文排版：配置 Noto Sans CJK / 思源黑体
- 代码高亮：`pygments` 集成
- 表格、Mermaid 图表支持
- 输出到任务工作区，用户可直接查看

**MD → DOCX 细节**：
- 使用 `pandoc`（系统级工具，跨平台）
- 通过 `subprocess` 调用 `pandoc input.md -o output.docx`
- 支持自定义 reference.docx 模板（控制样式）
- 若 pandoc 未安装，提示用户安装或降级为纯文本输出

**DOCX → PDF 细节**：
- 直接复用知识模块已有能力（`doc_parser.py` 的 `_convert_office_to_pdf`）
- Windows: MS Office COM 自动化（需安装 Word）
- 其它平台: LibreOffice headless 模式
- 检测逻辑：`ModelManager.check_office_converter()` 已实现

#### 12.17.2 任务双模式 — 编程模式 / 通用模式 ✅ 已实现

**核心理念**：任务不仅面向编程，也面向通用知识工作（写报告、分析、翻译等）。

**模式定义**：

| | 编程模式（coding） | 通用模式（general） |
|---|---|---|
| **定位** | 写代码、操作文件、调试 | 写报告、分析、翻译、总结 |
| **工具集** | 全部工具 | 精简：`file_ops`、`shell`、`web_fetch`、`web_search`、`knowledge_search`、`doc_generator`、`repo_map`、`skill_runner` |
| **System Prompt** | 当前 ReAct 编程提示 | 侧重结构化写作、分析框架、数据整理 |
| **输出形式** | 代码文件 + 执行结果 | Markdown 文档（报告/方案/摘要） |
| **典型场景** | "创建 Flask 应用" | "写一份 AI 产业分析报告" |

**模式切换**：
- **新建时选择**：创建任务对话框中增加模式选择（默认 coding）
- **任务中切换**：对话区头部增加模式切换按钮，切换后 Prompt 和工具集即时更新
- 切换不影响已有对话历史，仅影响后续 Agent 行为

**实现要点**：
- `schemas.py`：`task_mode: str = "coding"`（`coding` / `general`）
- `database.py`：tasks 表新增 `mode` 列
- `task_agent.py`：根据 mode 选择不同 system prompt 和工具子集
- 通用模式 Prompt 侧重：报告结构（摘要/背景/分析/结论）、数据引用格式、图表建议
- 前端：创建任务时 + 任务执行中均可切换

#### 12.17.3 文件只读预览 ✅ 已实现

**功能**：点击工作区文件 → 右侧预览子面板显示文件内容（与目录树并排）

**布局**：文件面板内左右两个子面板，各有独立标题栏

```
┌───────────────────┬───────────────────┐
│ 工作区       🔄   │ main.py       ✕   │  ← 各自标题
├───────────────────┼───────────────────┤
│ 📁 src/           │ 1 import os       │
│   🐍 main.py     │ 2 import sys      │
│   📋 req.txt     │ 3 ...             │
───────────────────┴───────────────────
```

**支持格式**：

| 文件类型 | 预览方式 |
|----------|----------|
| 文本/代码文件 | `react-syntax-highlighter` 语法高亮 + 行号 |
| Markdown 文件 | 默认渲染为 HTML，可切换查看源码（高亮显示） |
| 图片文件 | base64 内嵌显示（`<img>`） |
| 其它二进制 | 显示 📦 图标 + "二进制文件，无法预览" + 文件大小 + **打开所在目录** 按钮 |

**代码文件呈现**：
- 使用 `react-syntax-highlighter`（Prism 引擎 + oneDark 主题）
- 自动识别语言（按扩展名映射，支持 40+ 种）
- 显示行号
- 横向/纵向滚动条在容器边缘

**Markdown 文件**：
- 默认渲染模式：将 Markdown 渲染为 HTML 显示
- 切换按钮（📄/👁）：点击切换渲染预览 ↔ 源码高亮

**二进制文件**：
- 显示文件信息和“打开所在目录”按钮
- 点击按钮调用 Electron `shell.showItemInFolder()` 打开系统文件管理器

**实现方式**：
- 后端 API：`/tasks/{id}/files/{path}` 返回文件内容（文本/base64/二进制类型）
- 前端组件：`FilePreview.tsx` 根据文件类型切换显示方式
- 预览面板顶部显示文件名 + 文件大小 +  关闭按钮
- 目录树和预览之间可拖拽调整宽度（内层 resize）

#### 12.17.4 Agent 日期时间感知 ✅ 已实现

**功能**：Agent 在 system prompt 中获取当前日期时间，理解"今天""昨天""星期一"等时间概念。

**实现方式**：
- `task_agent.py` 的 `_build_system_prompt()` 动态注入当前时间
- 格式：`当前时间: 2026-09-21 13:51 (星期一)`
- 使用中文星期数组映射 `datetime.weekday()` 结果
- 每次 LLM 调用时动态生成，确保时间准确

**示例**：
```
用户：今天天气如何？
Agent：（知道"今天"是 2026-09-21，可调用 web_search 查询当天天气）
```

#### 12.17.5 斜杠命令机制 ✅ 已实现

**功能**：用户可通过 `/skill-name` 直接调用已安装的第三方 Skill，绕过 LLM 节省 token。

**后端实现**（`task_agent.py`）：
- `_is_slash_command()`：同步检测用户输入是否为 `/command` 格式
- `_execute_slash_command()`：异步执行 Skill，绕过 LLM 循环
- 拦截点：在 `_run_inner` 中，工具创建后、LLM 循环前
- 支持 `/help` 查看所有可用命令和已安装 Skill
- 支持 JSON 参数和纯文本参数

**前端实现**（`TaskChatView.tsx`）：
- 输入 `/` 触发自动补全弹窗
- 从 IPC 获取已安装 Skill 列表构建命令候选
- 支持键盘导航（↑↓ + Enter/Tab/Esc）和鼠标选择
- 弹窗定位在 `.inputArea` 内部，`position: absolute; bottom: 100%`

**IPC 桥接**：
- `tasks:getSkills`：获取已安装 Skill 列表供前端弹窗使用

#### 12.17.6 执行日志 API + UI ✅ 已实现

**功能**：记录 Agent 每次工具调用的详细日志，用户可在 UI 中查看。

**后端实现**：
- `_log_execution()`：每次工具调用写入 `tasks/{task_id}/execution.log`
- 格式：`[时间戳] [INFO] [工具名] input=... | duration=...ms | result=...`
- `GET /tasks/{id}/logs`：读取日志文件，支持 `lines` 参数指定返回行数

**前端实现**（`TaskLogViewer.tsx`）：
- 覆盖层面板，从顶栏按钮触发打开
- 支持自动刷新（3 秒轮询）
- 显示总行数、当前行数
- 自动滚动到最新日志

#### 12.17.7 安全增强 — 第三方 Skill 运行时确认 + Shell 域名白名单 ✅ 已实现

**第三方 Skill 运行时强制确认**：

- Agent 执行第三方 skill 前，检查 `needs_confirm` 标志
- 未确认的 skill 触发 `confirm_skill` SSE 事件，前端弹出确认卡片
- 用户有 60 秒确认时间，超时自动拒绝
- 支持斜杠命令（`/skill-name`）和正常工具调用两条路径
- 后端 API：`POST /tasks/{task_id}/confirm-skill`

**Shell 命令域名白名单**：

- 设置项 `task_shell_domain_whitelist`：每行一个域名（如 `pypi.org`、`github.com`）
- `shell_tool.py` 的 `check_domain_whitelist()` 函数从命令中提取 URL 域名
- 智能模式下，匹配白名单域名的网络命令自动放行，无需确认
- 支持 `https://`、`git@`、`pip install --index-url` 等多种 URL 格式

**Skill 安装确认对话框**：

- 安装前弹出模态对话框，显示来源类型、来源路径、安全警告
- 用户确认后才执行安装
- 安装后 Skill 卡片展示权限声明（permissions badges）

**外部依赖检测 UI**：

- 后端 `GET /system/deps/check` 检测 pandoc / libreoffice / git / ffmpeg
- 前端 `ExternalDepsSettings` 组件展示各依赖的安装状态、版本、路径
- 支持刷新重新检测

---

### 12.18 外部依赖与打包考量

> 以下工具为系统级依赖，不随应用安装包捆绑，需用户自行安装。

#### 12.18.1 外部依赖清单

| 工具 | 用途 | 检测方式 | 缺失时行为 | 安装指引 |
|------|------|----------|------------|----------|
| **pandoc** | MD → DOCX 转换 | `shutil.which("pandoc")` | 返回错误，提示安装 | Windows: `winget install JohnMacFarlane.Pandoc`<br>macOS: `brew install pandoc`<br>Linux: `sudo apt install pandoc` |
| **MS Office** | DOCX → PDF（Windows） | `ModelManager.check_office_converter()` | 回退到 LibreOffice 或提示 | 安装 Microsoft Word/PowerPoint/Excel |
| **LibreOffice** | DOCX → PDF（跨平台回退） | 同上 | 提示安装 | Windows: `winget install TheDocumentFoundation.LibreOffice`<br>macOS: `brew install --cask libreoffice`<br>Linux: `sudo apt install libreoffice` |

#### 12.18.2 检测与降级策略

```python
# doc_generator.py 中的检测逻辑

# 1. MD → DOCX：检测 pandoc
pandoc = shutil.which("pandoc")
if not pandoc:
    return {"error": "pandoc 未安装", "suggestion": "安装指引..."}

# 2. DOCX → PDF：检测 Office 转换器
status = ModelManager.check_office_converter()
if not status["available"]:
    return {"error": "未找到 Office 转换工具", "suggestion": "安装 MS Office 或 LibreOffice"}
```

**降级策略**：
- MD → PDF（weasyprint）：**无外部依赖**，纯 Python 实现，始终可用
- MD → DOCX：pandoc 缺失时返回明确错误，不自动降级
- DOCX → PDF：MS Office 缺失时自动尝试 LibreOffice，均缺失时提示安装

#### 12.18.3 打包安装影响

| 依赖 | 是否捆绑 | 安装包影响 | 备注 |
|------|----------|------------|------|
| weasyprint | ✅ pip 安装 | 无额外体积 | 随 `requirements.txt` 安装 |
| pandoc | ❌ 不捆绑 | 0 | 用户自行安装，约 30MB |
| MS Office | ❌ 不捆绑 | 0 | 用户已有或自行安装 |
| LibreOffice | ❌ 不捆绑 | 0 | 用户自行安装，约 800MB |

**打包建议**：
1. **pandoc 可选捆绑**：考虑将 pandoc 放入安装包（约 30MB），减少用户配置成本
2. **安装检测**：应用启动时检测外部依赖，在设置页显示安装状态
3. **一键安装**：提供“一键安装 pandoc”按钮（通过 winget/choco/brew）

#### 12.18.4 设置页集成（✅ 已实现）

外部依赖检测 UI 已实现（`ExternalDepsSettings.tsx`），检测 pandoc / LibreOffice / Git / FFmpeg 的安装状态、版本和路径，支持刷新重新检测。

```
设置 → 外部工具
┌─────────────────────────────────────────┐
│ pandoc                                  │
│ 状态: ✅ 已安装 (v3.1.0)                │
│ 用途: Markdown → DOCX 文档转换           │
│ [检测] [安装指引]                        │
├─────────────────────────────────────────┤
│ LibreOffice / MS Office                 │
│ 状态: ✅ MS Office 已安装               │
│ 用途: DOCX/PPTX/XLSX → PDF 转换         │
│ [检测] [安装指引]                        │
└─────────────────────────────────────────┘
```

---

### 12.19 待确认清单

> 以下问题已通过实施决策确认，不再待定。

| # | 问题 | 决策 |
|---|------|------|
| 1 | 工作区管理：是否支持选择已有目录？ | ✅ 已支持，含确认弹窗 |
| 2 | 任务类型：是否统一创建 venv？ | ✅ 统一创建，不区分类型 |
| 3 | 文件上传：50MB 限制？文件夹上传？ | ✅ 50MB 限制保留，文件夹上传不做 |
| 4 | 安全控制：Shell 命令确认 UI？ | 设置项 `task_auto_confirm_shell` 已实现，内联确认 UI 后续考虑 |
| 5 | 资源限制：30 秒超时？并发 3 个？ | ✅ 已实现，参数可调 |
| 6 | 打包安装：~240MB 安装包？ | 基础设施已实施（路径抽象层 + 构建脚本 + 初始化逻辑），待全流程测试 → [packaging-design.md](packaging-design.md) |
| 7 | 数据生命周期：自动清理？存储配额？ | ✅ 要做：自动清理 + 存储配额 |
| 8 | 国际化：P0 仅中文？ | ✅ 已实现中英双语 i18n |
| 9 | 测试策略：是否需要示例任务？ | 已建立 122 个单元测试覆盖（Wave 8-14） |
| 10 | 第三方 skill 来源 | 仅本地路径，不做 Git URL |
| 11 | 双模型架构 | ✅ 已实施（Planner + Editor 两阶段） |
| 12 | 任务克隆 | 不做 |
| 13 | doc-generator | ✅ 已实现（MD→PDF / MD→DOCX / DOCX→PDF） |
| 14 | 任务双模式 | ✅ 已实现（coding / general，可切换） |
| 15 | 文件预览 | ✅ 已实现（代码/Markdown/图片/二进制） |
| 16 | Agent 日期时间感知 | ✅ 已实现（system prompt 动态注入当前时间） |
| 17 | 斜杠命令机制 | ✅ 已实现（/skill-name 绕过 LLM + 前端自动补全弹窗） |
| 18 | 执行日志 UI | ✅ 已实现（TaskLogViewer + 自动刷新） |
| 19 | 第三方 skill 运行时强制确认 | ✅ 已实现（confirm_skill SSE 事件 + 前端确认卡片 + 60s 超时） |
| 20 | Shell 命令域名白名单 | ✅ 已实现（task_shell_domain_whitelist 设置 + check_domain_whitelist 函数 + 前端 textarea） |
| 21 | Skill 安装时权限展示 | ✅ 已实现（后端解析 permissions + 前端 badge 展示） |
| 22 | 外部依赖检测 UI | ✅ 已实现（GET /system/deps/check + ExternalDepsSettings 组件） |
| 23 | Skill 安装确认对话框 | ✅ 已实现（安装前弹出确认，显示来源和安全警告） |

---

## 13. 附录

### 13.1 术语表

| 术语 | 说明 |
|------|------|
| **任务（Task）** | 用户创建的一个工作单元，包含独立的工作区和对话历史 |
| **工作区（Workspace）** | 任务的工作目录，agent 在此目录中操作文件 |
| **venv** | Python 虚拟环境，每个任务独立一个，隔离依赖 |
| **Skill** | 智能体的能力模块，分为内置 skill 和第三方 skill |
| **Tool** | Skill 中的原子操作（如 code-executor 是一个 skill，执行代码是一个 tool） |
| **ACI** | Agent-Computer Interface，为 agent 设计的简化界面 |
| **Repo-map** | 代码库结构摘要，用 tree-sitter 提取 |
| **Context Collapse** | 上下文压缩策略，将早期对话压缩为摘要 |

### 13.2 参考资源

- Aider：https://aider.chat/
- OpenHands：https://github.com/All-Hands-AI/OpenHands
- SWE-agent：https://github.com/princeton-nlp/SWE-agent
- Claude Code：https://docs.anthropic.com/en/docs/claude-code
- python-build-standalone：https://github.com/indygreg/python-build-standalone
- uv：https://github.com/astral-sh/uv
- ripgrep：https://github.com/BurntSushi/ripgrep
- ruff：https://github.com/astral-sh/ruff

### 13.3 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-19 | 初始设计 |
| 0.2.0 | 2026-09-19 | 添加开源项目调研（第 11 章） |
| 0.3.0 | 2026-09-19 | 添加待明确设计问题与实施指南（第 12 章） |
| 0.4.0 | 2026-09-19 | 实施完成：P0+P1 全部实现，更新状态标记，明确不做项 |
| 0.5.0 | 2026-09-19 | Wave 8-13 全部实施完成；新增 §12.17 后续功能设计（doc-generator / 任务双模式 / 文件预览） |
| 0.6.0 | 2026-09-19 | Wave 14-15 实施完成；新增 §12.18 外部依赖与打包考量（pandoc / Office / LibreOffice） |
| 0.7.0 | 2026-09-19 | 全部功能实施完成；更新测试计数至 122；标记 §12.17.1/§12.17.2 为已实现 |
| 0.8.0 | 2026-09-19 | Wave 16 文件预览实施完成；新增 FilePreview 组件 + 后端文件内容 API |
| 0.9.0 | 2026-09-20 | 文件预览增强：语法高亮（react-syntax-highlighter）+ MD 渲染/源码切换 + 二进制文件打开所在目录 |
| 0.10.0 | 2026-09-21 | Wave 17 完成：Agent 日期时间感知（system prompt 注入当前时间）；斜杠命令机制（/skill-name 绕过 LLM 直接执行）；前端斜杠命令自动补全弹窗；执行日志 API + TaskLogViewer UI；Code Review 修复方向键导航 bug |
| 0.11.0 | 2026-09-21 | 安全增强：第三方 skill 运行时强制确认（confirm_skill SSE + 前端确认卡片）；Shell 命令域名白名单（设置 UI + 智能模式自动放行）；Skill 安装时权限展示（permissions 解析 + badge 展示）；外部依赖检测 UI（pandoc/libreoffice/git/ffmpeg）；Skill 安装确认对话框 |
| 0.12.0 | 2026-09-21 | 打包安装基础设施：统一工具路径解析器 (tool_resolver.py)；迁移 7 个工具文件使用统一解析；Electron 首次启动工具链初始化 (tool-initializer.js)；工具链下载脚本 (prepare-tools.ps1)；一键构建脚本 (build-installer.ps1)；GET /system/tools/status API；electron-builder.yml 增加 tools extraResources；详细方案见 [packaging-design.md](packaging-design.md) |
