# 开发指南

## 环境要求

### 必需

| 工具 | 版本 | 说明 |
|---|---|---|
| Node.js | >= 18 | 推荐 LTS |
| Python | 3.12 | 通过 Conda 管理 |
| Conda | miniconda3 | [下载地址](https://docs.conda.io/en/latest/miniconda.html) |
| Git | 任意 | 克隆仓库 |

### 可选（打包时需要）

| 工具 | 说明 |
|---|---|
| PyInstaller | 将 Python Agent 打包为独立二进制 |

## 国内环境配置

国内网络下安装和打包可能因 GitHub 资源下载缓慢而卡住，建议设置以下镜像：

```bash
# npm 镜像（所有平台通用）
npm config set registry https://registry.npmmirror.com
```

设置镜像环境变量（按你的终端选择对应语法）：

**PowerShell：**

```powershell
$env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR="https://npmmirror.com/mirrors/electron-builder-binaries/"
```

**CMD（命令提示符）：**

```cmd
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
set ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
```

**Bash（Git Bash / Linux / macOS）：**

```bash
export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
export ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
```

> 以上变量仅对当前终端窗口生效。设置后执行 `npm install` 或 `npm run pack` 即可。下载一次后会缓存到本地，后续无需重复设置。

## 开发安装

### 1. 克隆仓库

```bash
git clone https://github.com/your-org/inkcube.git
cd inkcube
```

### 2. 安装前端依赖

```bash
npm install
```

### 3. 创建 Python Conda 环境

```bash
conda create -n inkcube python=3.12 -y
conda activate inkcube
```

### 4. 安装 Python 依赖

```bash
pip install -r python/requirements.txt
```

依赖清单：`fastapi`、`uvicorn[standard]`、`httpx`、`pydantic`

### 5. 启动开发模式

```bash
npm run dev
```

该命令会同时启动：
- Vite 开发服务器（`http://localhost:5173`）
- Electron 桌面窗口（等待 Vite 就绪后自动打开）
- Python FastAPI 后端（由 Electron 自动管理，随机端口）

## 单独启动 Python 后端

```bash
npm run python:dev
```

这会在 `127.0.0.1:8765` 启动 uvicorn 并开启热重载。

## PyCharm 调试

开发时可以分两步启动，手动运行 Python 后端以便在 PyCharm 中直接 Debug。

### 第一步：在 PyCharm 中配置 Run/Debug

1. Run → Edit Configurations → + → Python
2. 填写以下信息：
   - **Script path**: 选 `Module`，填 `uvicorn`
   - **Parameters**: `main:app --host 127.0.0.1 --port 8765`
   - **Working directory**: `<项目路径>/python`
   - **Python interpreter**: 选择 `inkcube` conda 环境
   - **Environment variables**: 添加 `PYTHONUNBUFFERED=1`
3. 点击 **Debug** 按钮（虫子图标），即可打断点、单步调试

### 第二步：启动 Electron 前端

直接启动即可，Electron 会自动检测到已运行的 Python 实例并连接：

```bash
npm run dev
```

> 生产环境不受影响——未检测到已运行的 Python 时，Electron 会自动启动后端。

## Conda 路径说明

开发模式下，Electron 按以下默认路径查找 Conda 环境。如果你的 Conda 安装在其他位置，需要修改 `electron/python-manager.js` 中的 `condaBase` 变量。

| 平台 | 默认路径 |
|---|---|
| Windows | `%USERPROFILE%\.conda\envs\inkcube\` |
| macOS / Linux | `/opt/conda/envs/inkcube/` |

## 打包发布

> **重要**：Python Agent 二进制（PyInstaller）不支持交叉编译，必须在目标平台上构建。例如，打 Windows 安装包需要在 Windows 上执行步骤 1，打 macOS 安装包需要在 macOS 上执行。

### 1. 打包 Python Agent 为二进制

在当前平台上执行（产物只能用于对应平台）：

**PowerShell：**

```powershell
conda activate inkcube
pip install pyinstaller

pyinstaller --onefile `
  --name inkcube-agent `
  --distpath resources/python `
  --paths python `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.protocols.websockets.auto `
  --hidden-import uvicorn.lifespan `
  --hidden-import uvicorn.lifespan.on `
  --hidden-import tools.tavily_search `
  python/main.py
```

**CMD（命令提示符）：**

```cmd
conda activate inkcube
pip install pyinstaller

pyinstaller --onefile ^
  --name inkcube-agent ^
  --distpath resources/python ^
  --paths python ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan ^
  --hidden-import uvicorn.lifespan.on ^
  --hidden-import tools.tavily_search ^
  python/main.py
```

**Bash（Git Bash / Linux / macOS）：**

```bash
conda activate inkcube
pip install pyinstaller

pyinstaller --onefile \
  --name inkcube-agent \
  --distpath resources/python \
  --paths python \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import tools.tavily_search \
  python/main.py
```

打包产物位于 `resources/python/inkcube-agent.exe`（Windows）或 `resources/python/inkcube-agent`（macOS/Linux）。

### 2. 打包 Electron 应用

```bash
npm run pack
```

该命令先执行 `vite build` 构建前端，再调用 `electron-builder` 生成当前平台的安装包。

如需指定目标平台（electron-builder 支持交叉打包）：

| 命令 | 目标平台 |
|---|---|
| `npx electron-builder --win` | Windows `.exe` (NSIS) |
| `npx electron-builder --mac` | macOS `.dmg` |
| `npx electron-builder --linux` | Linux `.AppImage` |

> 注意：虽然 electron-builder 可以交叉打包，但 Python Agent 二进制必须匹配目标平台。交叉打包时，需要提前准备好目标平台的 `inkcube-agent` 并放到 `resources/python/` 目录。

打包产物位于 `dist/` 目录。

## 项目结构

```
inkcube/
├── electron/                # Electron 主进程
│   ├── main.js              # 入口：创建窗口、管理 Python 进程
│   ├── preload.js           # contextBridge 安全桥接
│   ├── python-manager.js    # Python 子进程生命周期管理
│   └── ipc-handlers.js      # IPC 通道注册，代理到 Python HTTP API
├── src/                     # React 前端
│   ├── App.tsx              # 根组件，状态管理
│   ├── types.ts             # TypeScript 类型定义
│   ├── styles/
│   │   └── global.css       # 全局样式 + CSS 变量主题
│   └── components/chat/
│       ├── Sidebar.tsx       # 会话列表（按时间分组）
│       ├── ChatWindow.tsx    # 聊天主窗口
│       ├── MessageList.tsx   # 消息渲染（Markdown + ReAct 折叠）
│       ├── MessageInput.tsx  # 输入框（自适应高度）
│       └── SettingsPanel.tsx # LLM 配置面板
├── python/                  # Agent 后端
│   ├── main.py              # FastAPI 入口 + 生命周期
│   ├── deps.py              # 共享依赖（db, agent）
│   ├── schemas.py           # Pydantic 请求模型
│   ├── watchdog.py          # 父进程看门狗
│   ├── routers/
│   │   ├── chat.py          # /chat, /chat/cancel
│   │   ├── conversations.py # /conversations/*
│   │   └── settings.py      # /settings, /settings/test
│   ├── db/
│   │   └── database.py      # SQLite 数据层
│   ├── agent/
│   │   ├── react_agent.py   # ReAct 循环实现
│   │   └── llm_client.py    # OpenAI 兼容 HTTP 客户端
│   └── tools/
│       ├── base.py           # 工具基类
│       ├── registry.py       # 工具注册表 + 自动注册
│       └── tavily_search.py  # Tavily 网络搜索
├── docs/                    # 开发文档
├── resources/               # 打包资源（图标、Python 二进制）
├── electron-builder.yml     # Electron 打包配置
├── vite.config.ts           # Vite 构建配置
├── tsconfig.json            # TypeScript 配置
└── package.json             # 项目依赖与脚本
```

## 数据存储

| 数据 | 位置 |
|---|---|
| SQLite 数据库 | `%APPDATA%/InkCube/data/inkcube.db`（Windows）|
| | `~/Library/Application Support/InkCube/data/inkcube.db`（macOS）|
| | `~/.config/InkCube/data/inkcube.db`（Linux）|

程序升级时只替换应用代码，不触及用户数据目录。

## 默认配置

首次启动使用以下默认配置（可在设置面板中修改）：

| 参数 | 默认值 |
|---|---|
| API 地址 | `https://open.bigmodel.cn/api/paas/v4/` |
| 模型 | `glm-4-flash` |
| Temperature | 0.7 |
| Max Tokens | 4096 |
| Top P | 0.9 |

> 默认使用智谱 AI GLM-4-Flash。你可以在设置中切换为任何兼容 OpenAI API 格式的模型。
