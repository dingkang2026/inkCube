# 墨方 InkCube — 架构设计文档

> 版本: 0.1.0 | 更新日期: 2026-09-14 | 状态: 初始设计

## 1. 项目概述

墨方 InkCube 是一款桌面端智能助手客户端，提供本地知识库管理、文档生成（docx）、演示文稿生成（ppt）等能力。除大模型推理外，所有功能均在本地运行，保障数据隐私。

### 1.1 核心能力

- **本地知识库**: 文档导入、向量化存储、语义检索
- **智能对话**: 基于 ReAct 模式的 Agent，支持多轮推理与工具调用
- **文档生成**: 根据对话结果生成 docx / ppt 文件
- **完全本地**: 除 LLM API 外，无外部网络依赖

### 1.2 开源协议

本项目采用 **AGPL-3.0** 协议。所有修改和衍生作品必须以相同协议开源，网络服务亦视为分发。

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────┐
│                   Electron Shell                     │
│                                                      │
│  ┌─────────────────┐     contextBridge    ┌────────┐ │
│  │   Main Process   │◄───────────────────►│Renderer│ │
│  │   (Node.js)      │     IPC             │(React) │ │
│  │                  │                     │        │ │
│  │  - 窗口管理       │                     │ - Chat │ │
│  │  - Python 进程    │                     │ - 设置 │ │
│  │    生命周期管理    │                     │ - 知识库│ │
│  │  - 请求代理       │                     │        │ │
│  └────────┬─────────┘                     └────────┘ │
│           │ HTTP (localhost:动态端口)                   │
│           │ SSE (流式响应)                              │
└───────────┼─────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────┐
│   Python Agent (FastAPI)        │
│                                 │
│  ┌──────────┐   ┌────────────┐ │
│  │ ReAct    │   │  SQLite    │ │
│  │ Agent    │   │  知识库     │ │
│  └────┬─────┘   └────────────┘ │
│       │                         │
│  ┌────┴──────────────────┐     │
│  │      Tools             │     │
│  │  - kb_search (知识库)   │     │
│  │  - docx_gen (文档生成)  │     │
│  │  - ppt_gen (PPT生成)   │     │
│  └───────────────────────┘     │
└────────────────────────────────┘
```

---

## 3. 技术栈

| 层级 | 技术 | 版本要求 | 说明 |
|------|------|----------|------|
| 桌面壳 | Electron | ^33.x | 跨平台桌面应用 |
| 前端框架 | React | ^18.x | UI 组件化 |
| 前端构建 | Vite | ^6.x | 快速 HMR，ESM 原生 |
| 语言 | TypeScript | ^5.x | 类型安全 |
| Python 服务 | FastAPI | ^0.115 | HTTP API + SSE |
| ASGI 服务器 | uvicorn | ^0.32 | 生产级异步服务器 |
| 数据库 | SQLite | 内置 | 零依赖，Python sqlite3 |
| Python 打包 | PyInstaller | ^6.x | 生成独立可执行文件 |
| Electron 打包 | electron-builder | ^25.x | NSIS 安装包 |

---

## 4. 目录结构

```
InkCube/
├── electron/                   # Electron 主进程
│   ├── main.ts                 # 入口：窗口创建、应用生命周期
│   ├── preload.ts              # contextBridge 安全桥接
│   ├── python-manager.ts       # Python 子进程管理
│   └── ipc-handlers.ts         # IPC 消息处理，代理至 Python
│
├── src/                        # React 渲染进程 (Vite)
│   ├── main.tsx                # React 入口
│   ├── App.tsx                 # 根组件
│   ├── index.html              # HTML 模板
│   ├── vite-env.d.ts           # Vite 类型声明
│   ├── api/                    # API 调用层
│   │   └── chat.ts             # Chat SSE 流式封装
│   ├── components/             # UI 组件
│   │   └── chat/
│   │       ├── ChatWindow.tsx
│   │       ├── MessageList.tsx
│   │       └── MessageInput.tsx
│   ├── hooks/                  # 自定义 Hooks
│   ├── stores/                 # 状态管理
│   └── styles/                 # 全局样式
│
├── python/                     # Python Agent 后端
│   ├── main.py                 # FastAPI 入口，路由注册
│   ├── agent/                  # Agent 核心
│   │   ├── react_agent.py      # ReAct 循环实现
│   │   ├── llm_client.py       # LLM API 调用封装
│   │   └── prompt_templates.py # 提示词模板
│   ├── tools/                  # 工具集
│   │   ├── base.py             # 工具基类与注册
│   │   ├── kb_search.py        # 知识库检索
│   │   ├── docx_gen.py         # Word 文档生成
│   │   └── ppt_gen.py          # PPT 生成
│   ├── db/                     # 数据层
│   │   ├── database.py         # SQLite 连接池
│   │   └── models.py           # 表结构与迁移
│   └── requirements.txt        # Python 依赖
│
├── resources/                  # 打包资源
│   ├── python/                 # PyInstaller 产物（构建时生成）
│   └── icon.ico                # 应用图标
│
├── dev_docs/                   # 开发文档
├── vite.config.ts              # Vite 配置
├── electron-builder.yml        # electron-builder 配置
├── package.json
├── tsconfig.json
└── README.md
```

---

## 5. 关键设计决策

### 5.1 Python 进程管理

Python Agent 作为独立子进程运行，由 Electron Main Process 管理生命周期。

**启动流程:**

```
Electron app.ready
    │
    ├── 分配空闲端口 (portfinder)
    ├── spawn Python 进程 (FastAPI)
    ├── 轮询 GET /health (超时 10s)
    ├── 健康检查通过
    └── 创建 BrowserWindow
```

**进程模式:**

| 环境 | Python 启动方式 | 说明 |
|------|-----------------|------|
| 开发 | `uvicorn python.main:app --reload` | 热重载，快速迭代 |
| 生产 | `resources/python/inkcube-agent.exe` | PyInstaller 打包的单文件 |

**退出处理:**

```
Electron before-quit
    │
    ├── POST /shutdown (优雅关闭)
    ├── 等待 3s
    └── SIGTERM → SIGKILL (兜底)
```

### 5.2 通信协议

**Electron ↔ Renderer:** contextBridge + ipcRenderer/ipcMain

**Electron ↔ Python:** HTTP + SSE

| 接口 | 方法 | 说明 |
|------|------|------|
| `GET /health` | GET | 健康检查 |
| `POST /chat` | POST | 发送消息，返回 SSE 流 |
| `POST /chat/{id}/cancel` | POST | 取消生成 |
| `GET /conversations` | GET | 获取对话列表 |
| `GET /conversations/{id}` | GET | 获取对话详情 |
| `DELETE /conversations/{id}` | DELETE | 删除对话 |
| `POST /shutdown` | POST | 优雅关闭服务 |

**SSE 事件格式:**

```
event: thought
data: {"content": "我需要搜索知识库来回答这个问题..."}

event: action
data: {"tool": "kb_search", "input": {"query": "..."}}

event: observation
data: {"content": "找到 3 条相关结果..."}

event: answer
data: {"content": "根据知识库，..."}

event: done
data: {"message_id": "xxx", "tokens": 1234}

event: error
data: {"code": "LLM_TIMEOUT", "message": "..."}
```

### 5.3 ReAct Agent 设计

ReAct (Reasoning + Acting) 模式让 LLM 交替进行推理和行动。

**循环流程:**

```
用户输入
    │
    ▼
┌─────────────────┐
│  Thought (推理)  │ ◄─── LLM 生成
│  "我需要..."     │
└────────┬────────┘
         │
    ┌────▼────┐
    │ 需要行动? │
    └────┬────┘
     Yes │    │ No
         ▼    ▼
┌────────────┐  ┌──────────┐
│  Action    │  │  Final   │
│  调用工具   │  │  Answer  │
└────┬───────┘  └──────────┘
     │
     ▼
┌────────────┐
│ Observation │
│ 工具返回结果 │
└────┬───────┘
     │
     └──► 回到 Thought
```

**工具注册机制:**

```python
class BaseTool:
    name: str           # 工具标识
    description: str    # LLM 可见的描述
    parameters: dict    # JSON Schema 参数定义

    async def execute(self, **kwargs) -> str:
        raise NotImplementedError

# 工具自动注册，Agent 启动时扫描 tools/ 目录
```

### 5.4 数据存储策略

**SQLite 数据库位置:**

```
开发: python/data/inkcube.db
生产: %APPDATA%/InkCube/data/inkcube.db
```

**核心表结构:**

```sql
-- 对话
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 消息
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id),
    role TEXT NOT NULL,          -- user / assistant / tool
    content TEXT NOT NULL,
    metadata TEXT,               -- JSON: token count, tool calls, etc.
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 知识库文档 (预留)
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT,
    chunk_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 知识片段 (预留)
CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES documents(id),
    content TEXT NOT NULL,
    embedding BLOB,              -- 向量数据
    metadata TEXT
);
```

### 5.5 升级不覆盖用户数据

**目录分离原则:**

| 类型 | 位置 | 升级行为 |
|------|------|----------|
| 应用代码 | `C:\Program Files\InkCube\` | 覆盖更新 |
| 用户数据 | `%APPDATA%\InkCube\` | 保留不动 |
| 临时文件 | `%TEMP%\InkCube\` | 可随时清理 |

**用户数据子目录:**

```
%APPDATA%/InkCube/
├── data/
│   └── inkcube.db              # 数据库
├── config/
│   └── settings.json           # 用户配置
├── exports/                    # 导出的文档
│   ├── docx/
│   └── ppt/
└── logs/                       # 运行日志
    ├── main.log
    └── agent.log
```

**数据库迁移:**

使用版本号管理 schema 变更，应用启动时自动迁移：

```python
SCHEMA_VERSION = 1

def migrate(db):
    current = db.get_schema_version()
    for v in range(current, SCHEMA_VERSION):
        migration = migrations[v]
        migration.execute(db)
    db.set_schema_version(SCHEMA_VERSION)
```

---

## 6. 打包与分发

### 6.1 构建流程

```
┌─────────────────┐     ┌──────────────────┐
│  Python 构建     │     │  Electron 构建    │
│                  │     │                   │
│  PyInstaller     │     │  electron-builder │
│  main.py →       │     │                   │
│  inkcube-agent   │     │  打包 Electron    │
│  .exe            │     │  + resources/     │
│                  │     │                   │
└────────┬─────────┘     └─────────┬─────────┘
         │                         │
         └────────┬────────────────┘
                  ▼
         ┌────────────────┐
         │ InkCube-Setup  │
         │ .exe           │
         └────────────────┘
```

### 6.2 PyInstaller 配置要点

```python
# inkcube-agent.spec
a = Analysis(
    ['python/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('python/agent/prompts', 'agent/prompts'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
    ],
)
```

### 6.3 electron-builder 配置要点

```yaml
# electron-builder.yml
appId: com.inkcube.app
productName: 墨方 InkCube

directories:
  output: dist
  buildResources: resources

files:
  - "electron/**/*"
  - "dist-renderer/**/*"

extraResources:
  - from: "resources/python/"
    to: "python/"
    filter: ["**/*"]

win:
  target: nsis
  icon: resources/icon.ico

nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
  perMachine: true
```

---

## 7. 开发环境

### 7.1 前置要求

- Node.js >= 20.x
- Python >= 3.11
- pnpm (推荐) 或 npm

### 7.2 启动命令

```bash
# 安装依赖
pnpm install
cd python && pip install -r requirements.txt && cd ..

# 开发模式 (同时启动 Electron + Vite + Python)
pnpm dev

# 构建
pnpm build:python    # PyInstaller 打包
pnpm build:electron  # electron-builder 打包
pnpm build           # 完整构建
```

### 7.3 开发模式架构

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Vite Dev   │     │  Electron   │     │   Python    │
│   Server     │     │  Main       │     │   FastAPI   │
│   :5173      │◄───►│             │◄───►│   :8765     │
│              │     │             │     │             │
│  React HMR   │     │  代理请求    │     │  --reload   │
└─────────────┘     └─────────────┘     └─────────────┘
```

---

## 8. 安全考量

| 风险 | 措施 |
|------|------|
| XSS → 本地文件访问 | CSP 限制，禁用 nodeIntegration |
| Python 端口被外部访问 | 仅绑定 127.0.0.1 |
| LLM API Key 存储 | 使用系统密钥链 (keytar) |
| 用户数据泄露 | 所有数据本地存储，无遥测 |

---

## 9. 后续扩展点

以下能力在第一阶段预留接口，不实现：

- **向量检索**: chunks 表已预留 embedding 字段，后续接入 embedding 模型
- **MCP 协议**: 工具注册机制兼容 MCP，后续可接入外部工具
- **多模型切换**: llm_client 抽象层支持切换不同 LLM 提供商
- **插件系统**: 工具目录动态加载，支持第三方扩展

---

## 附录 A: 术语表

| 术语 | 说明 |
|------|------|
| ReAct | Reasoning + Acting，LLM 交替推理和行动的范式 |
| SSE | Server-Sent Events，服务端推送事件流 |
| contextBridge | Electron 安全桥接，隔离主进程与渲染进程 |
| PyInstaller | Python 打包工具，生成独立可执行文件 |
