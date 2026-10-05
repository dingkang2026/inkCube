# 墨方 InkCube

> 墨方，取自「笔墨」与「魔方」的谐音——寓意以同一套知识引擎，写出千变万化的报告。

本地优先的知识工作台：项目化知识库（RAG）、LLM Wiki、研究报告引擎与任务智能体。除大模型 API 外，全部数据与计算均在本地完成。

## 功能特性

- **项目管理** — 项目 / 基础库双类型：项目为素材 · Wiki · 报告 · 填表四 Tab 工作区（含跨 Tab Chat，填表为占位功能），基础知识库预置 2 个、跨项目共享知识
- **知识库 RAG** — Docling 解析（PDF / Word / PPT）→ 分块 → bge-m3 向量化 → ChromaDB + FTS5 混合检索（RRF 融合）；图片自动抽取 + VLM 描述
- **LLM Wiki** — 逐文件增量摄入（两步链式思考），实体 / 概念 / 主题页面 + 双链引用 + Lint 健康检查 + 监督审阅
- **报告引擎** — 模板化多步流水线：变量 DAG → 多通道检索 → 知识卡片 → 逐节生成（checkpoint 断点续跑）→ 一致性收尾，导出 Word / PDF
- **任务智能体** — 通用 / 编程双模式 Function Calling 循环 + Skill 框架（内置 20+ 工具，兼容 Agent Skills 标准）；SSE 流式输出、上下文压缩、每任务独立 venv
- **全局反馈（Activity Hub）** — 统一活动总线：终态通知（Toast / 徽章 / 首页动态）、崩溃恢复、功能就绪度引导（首启向导 + 入口引导弹层）
- **本地与跨平台** — Windows / macOS / Linux；中英双语；用户数据与程序分离，升级不覆盖

## 架构速览

```
React + TypeScript (Vite)
        │  contextBridge IPC
        ▼
   Electron 主进程 ──── 子进程管理 · 统一日志 · Mermaid 离屏渲染
        │  HTTP + SSE（127.0.0.1）
        ▼
   FastAPI (Python 3.12)
   知识库 │ Wiki │ 报告 │ 任务智能体 │ 活动 / 就绪度
        │
   SQLite（23 表）+ ChromaDB + 本地模型（bge-m3 / Docling 系列）
```

- 五大长时操作（任务 / 文件解析 / 图片描述 / Wiki 编译 / 报告生成）共享统一状态机与崩溃恢复模型，DB 是唯一事实源，前端为只读镜像
- 外部依赖仅一个兼容 OpenAI API 的 LLM（VLM 用于图片描述，可选降级）

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18 + TypeScript + Vite 6 |
| 桌面 | Electron 33（contextBridge 安全隔离） |
| 后端 | Python 3.12 + FastAPI（SSE 流式） |
| 数据 | SQLite（WAL）+ ChromaDB + FTS5 |
| 解析 / 向量 | Docling + bge-m3 + RapidOCR（本地推理） |
| 打包 | electron-builder + python-build-standalone + uv 依赖装配 |

## 快速开始（开发）

```powershell
# 1. 前端依赖
npm install

# 2. Python 环境（conda）
conda create -n inkcube python=3.12
conda activate inkcube
pip install -r python/requirements.txt

# 3. 启动完整栈（Vite + Electron + Python）
npm run dev
```

> 首次启动可在设置中配置 LLM API（默认智谱 GLM-4-Flash，可切换任意 OpenAI 兼容模型）；内部模型（bge-m3 / Docling 系列，约 4.8 GB）按需在设置中下载。环境细节见[开发指南](docs/0.1dev-guide.md)。

## 测试与验证

```powershell
python -m pytest python/tests/ -v   # 后端单测
npx tsc --noEmit                    # 前端类型检查
npm run build                       # 前端构建
```

## 路线图

### v1.0 — 首个可发布版（当前阶段）

目标：从功能完备间可分发、可依赖的桌面产品。

| # | 事项 | 说明 |
|---|------|------|
| 1 | 模型下载托底 | 内置模型（bge-m3 / Docling 系列）多下载源托底 + 完整性校验、失败可恢复——首启体验关键路径 |
| 2 | 打包安装实测 | prepare-tools → 依赖装配（uv）→ electron-builder 全流程 Windows 实机验证安装 / 升级 / 卸载（用户数据不丢），沉淀可复现发布流程 |
| 3 | Token 计量 | 任务 / Wiki / 报告统一 token 用量统计与历史展示（报告引擎已有 token_usage 基础） |

### 后续 — 产出能力扩展

| # | 事项 | 说明 |
|---|------|------|
| 4 | 填表系统 | 结构化数据填充，与报告并列的产出形式（2.4 设计文档，前端 UI 已就绪） |
| 5 | PPT 导出 | Word 报告内容的下游渲染，非独立生成管线（2.3 设计文档） |

## 文档导航

| 文档 | 内容 |
|---|---|
| [0.0 架构总览](docs/0.0architecture.md) | 分层架构、数据模型、模块划分、后续扩展点 |
| [0.1 开发指南](docs/0.1dev-guide.md) | 环境搭建、项目结构、测试验证、打包发布 |
| [0.2 打包设计](docs/0.2packaging-design.md) · [0.3 UX 设计](docs/0.3ux-design.md) · [0.4 生命周期](docs/0.4lifecycle-design.md) | 工程基础专题 |
| [1.1 任务智能体](docs/1.1task-agent-design-unified.md) · [1.2 流式显示](docs/1.2streaming-thought-display.md) · [1.3 任务韧性](docs/1.3task-resilience-design.md) | 智能体专题 |
| [2.x 功能设计](docs/2.1project-design.md)（项目 / 知识库 / PPT / 表单 / 报告 / Wiki / 基础库 RAG） | 功能模块专题 |

## 开源协议

[AGPL-3.0](LICENSE)
