# 墨方 InkCube

> 墨方，取自「笔墨」与「魔方」的谐音——寓意以同一套知识引擎，写出千变万化的报告。

[English](README.md) | 简体中文

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![Electron](https://img.shields.io/badge/electron-33-brightgreen)
![License](https://img.shields.io/badge/license-AGPL--3.0-orange)

**本地优先的招商 / 投资项目工作台**：把「收集信息 → 整理归档 → 填表 / 写报告 / 做 PPT」交给任务智能体，把人的时间还给判断。尤其适合需要同时跟进多个项目的**招商经理与投资经理**。除大模型 API 外，全部数据与计算均在本地完成，**私有数据不出域**。

## 为什么是墨方？

| 你的一天 | 没有墨方 | 墨方 |
|----------|----------|------|
| 早晨盘点 | 打开 N 个系统 / 群 / 表格逐个翻 | 首页「今日概览 + 待办 + 动态」一屏看清 |
| 领导 / 客户临时要企业情况 | 手工查天眼查 / 搜索引擎，粘贴拼报告 | 任务智能体上网采集 + 自动生成报告初稿 |
| 企业出新闻（负面 / 竞品动作） | 被动等别人转告 | 定时订阅自动监控，首页情报速览 |
| 填表格、写周报月报、做汇报材料 | 重复搬运信息到固定格式 | 填表 / 报告 / PPT 模板化自动生成 |
| 新项目立项 | 开 Word 建目录、找模板 | 项目模板一键建：招商 / 投资类型 + 阶段 + 预置结构 |
| 对上汇报 / 横向共享 | 打包发邮件、截图拼文档 | 项目导出、报告 / PPT 导出 |

内置**一级市场方法论技能**（尽调清单、商业拆解、估值建模、投行文书），方法论不再只存在于脑子里。

## ✨ 功能特性（已实现）

### 任务智能体

- 对话驱动执行：通用 / 编程双模式 Function Calling 循环，20+ 内置工具；SSE 流式思考展示、上下文压缩
- 任务韧性：崩溃恢复、断点续跑，数据库为唯一事实源
- 每任务独立工作区与 Python venv；文件树 / 预览 / 下载；产出可一键导入项目或基础库
- 技能框架：兼容 Agent Skills 标准，第三方技能安装 / 管理；内置一级市场方法论技能
- 浏览器抓取：后台窗口池采集公开信息；网页登录态管理（域名白名单 + Cookie 清理）
- 任务模板：内置 10 个（信息收集 / 舆情监控 / 产业情报 / 报告投研 / 办公辅助）+ 自定义模板，占位符表单填充

### 定时订阅任务

- 舆情 / 竞品 / 产业动态周期采集：调度器自动派发影子任务，复用 agent 执行链
- 失败自动暂停、错过补跑（≤24h）、增量注入（每期只写新增）；订阅管理台统一管理

### 项目工作区

- 项目类型（招商 / 投资 / 通用）× 生命周期三阶段（投前 / 投中 / 投后）
- 应办事务：素材 / 报告 / 里程碑 / 周期性四类，到期提醒，阶段切换自动生成
- 六大产出 Tab：素材、Wiki（知识）、对话、报告、填表、PPT
- 项目导入导出：打包带走 / 分享

### 智能层

- 项目脉搏带 AI 状态句（轻模型生成「懂行」的现状判断）
- 台账时间轴卡（监控页结构化解析，落知识总览与首页卡片墙摘要）
- 事件驱动任务建议（风险事件 → 建议跟进任务，一键采纳 / 忽略，忽略不再提）
- milestone 台账提取（台账条目 vs 看板节点比对，建议标记完成）

### 知识库与 RAG

- Docling 解析（PDF / Word / PPT）→ 分块 → bge-m3 向量化 → ChromaDB + FTS5 混合检索（RRF 融合）
- 图片自动抽取 + VLM 描述；项目库 + 基础库（跨项目共享）

### LLM Wiki

- 逐文件增量摄入（两步链式思考）；实体 / 概念 / 主题页面 + 双链引用 + Lint 健康检查 + 监督审阅

### 报告引擎

- 模板化多步流水线：变量 DAG → 多通道检索 → 知识卡片 → 逐节生成（checkpoint 断点续跑）→ 一致性收尾
- 报告模板管理；导出 Word / PDF

### 填表系统

- 填表模板上传 / 解析（全局资产）→ 发起填表实例（项目作用域）→ 人工修订 → 下载

### PPT

- PPT 模板管理与解析、交付物生成（内置 ppt-master skill 任务管线执行）、进度与产物聚合

### 模板管理

- 报告模板 / 填表模板 / PPT 模板 / 任务模板，四类统一管理

### 首页与引导

- 今日概览（项目数 / 逾期 / 今日到期 / 进行中）、今日待办 + 近期日历、项目卡片墙、动态流、快捷动作
- 情报速览：跨订阅期次时间流（未读优先，「查看全部」进订阅管理台）
- SystemAlerts 全局告警区（服务异常 / 杀软授权，正常时不可见）
- 首启初始化向导、三步建项目引导、入口气泡提示

### 活动与通知

- 统一长时活动总线：任务 / 文件解析 / 图片描述 / Wiki 编译 / 报告 / 填表 / 模型下载共享状态机与崩溃恢复
- 通知中心 + OS 系统通知 + 事务到期提醒 + 终态 Toast / 徽章 / 首页动态；SSE 实时推送 + 心跳兜底

### Token 用量

- 今日 / 总量 / 区间汇总；按天 / 来源 / 模型 / 项目分组；任务轮次明细

### 设置与系统

- LLM / VLM / Embedding 服务配置（任意 OpenAI 兼容端点，默认智谱 GLM-4-Flash）
- 内置模型下载管理（bge-m3 / Docling 系列，约 4.8 GB）：多源托底（ModelScope → hf-mirror → Hugging Face）+ 完整性校验
- 应用内日志查看、健康检查与就绪度自检

### 平台与工程

- Windows / macOS / Linux；中英双语 i18n
- 数据与程序分离，升级不覆盖用户数据
- 列表统一分页契约、删除级联清理、IPC 孤儿通道审计

## 🚧 下一步工作（Roadmap）

| 事项 | 阶段 | 说明 |
|------|------|------|
| PPT 模板 | 近期 | 在已落地的模板管理 / 解析基础上持续扩充打磨 |
| 多平台支持测试验证及配套优化 | 近期 | macOS / Linux 安装包实测（Windows 安装 / 升级 / 卸载链路已实测完整，mac / Linux 打包配置就绪） |
| 长期记忆 | 远期 | 跨任务、跨会话的记忆能力 |
| 服务器版 | 远期 | 服务器部署形态 |
| H5 远程控制 | 远期 | 通过移动端 H5 远程查看与控制 |

## 🏗 架构速览

```
React + TypeScript (Vite)
        │  contextBridge IPC
        ▼
   Electron 主进程 ──── 子进程管理 · 统一日志 · Mermaid 离屏渲染
        │  HTTP + SSE（127.0.0.1）
        ▼
   FastAPI (Python 3.12)
   知识库 │ Wiki │ 报告 │ 填表 │ PPT │ 任务智能体 │ 定时订阅 │ 活动 / 通知 / 用量
        │
   SQLite（WAL）+ ChromaDB + 本地模型（bge-m3 / Docling 系列）
```

- 全部长时操作共享统一状态机与崩溃恢复模型，数据库是唯一事实源，前端为只读镜像
- 外部依赖仅一个兼容 OpenAI API 的大模型服务（VLM 用于图片描述，可选降级）

## 🛠 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18 + TypeScript + Vite 6 |
| 桌面 | Electron 33（contextBridge 安全隔离） |
| 后端 | Python 3.12 + FastAPI（SSE 流式） |
| 数据 | SQLite（WAL）+ ChromaDB + FTS5 |
| 解析 / 向量 | Docling + bge-m3 + RapidOCR（本地推理） |
| 打包 | electron-builder + python-build-standalone + uv 依赖装配 |

## 🚀 快速开始（开发）

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

> 首次启动可在设置中配置 LLM API（默认智谱 GLM-4-Flash，可切换任意 OpenAI 兼容模型）；内置模型（bge-m3 / Docling 系列，约 4.8 GB）按需在设置中下载。环境细节见[开发指南](docs/0.1dev-guide.md)。

## 📦 打包发布

```powershell
npm run pack                        # 构建前端 + electron-builder
powershell scripts/build-installer.ps1   # Windows 安装包全流程
```

> 注：`resources/tools/` 与 `resources/python-backend/` 为不入库的可再生构建产物——前者先运行 `powershell scripts/prepare-tools.ps1` 自动下载（版本锁定于 `tool-versions.json`；网络受限可加 `-MirrorPrefix` 走加速代理，或按 [0.2 §6.2](docs/0.2packaging-design.md) 手动放置），后者由打包脚本自动装配。

## 🧪 测试与验证

```powershell
python -m pytest python/tests/ -v    # 后端单测（pytest）
npx vitest run                        # 前端单测（vitest）
npx tsc --noEmit                      # 前端类型检查
npm run build                         # 前端构建
node scripts/orphan_channel_audit.js  # IPC 孤儿通道审计（四组输出应为 0）
```

## 🤝 参与贡献

欢迎 issue 与 PR！请先阅读[贡献指南](CONTRIBUTING.md)（环境搭建、代码规范、验证清单），参与即表示接受[行为准则](CODE_OF_CONDUCT.md)。安全问题请通过[安全策略](SECURITY.md)中的渠道私下报告。

## 📚 文档导航

| 文档 | 内容 |
|---|---|
| [0.0 架构总览](docs/0.0architecture.md) | 分层架构、数据模型、模块划分、后续扩展点 |
| [0.1 开发指南](docs/0.1dev-guide.md) | 环境搭建、项目结构、测试验证、打包发布 |
| [0.2 打包设计](docs/0.2packaging-design.md) · [0.3 UX 设计](docs/0.3ux-design.md) · [0.4 生命周期](docs/0.4lifecycle-design.md) · [0.5 产品化整改](docs/0.5product-redesign.md) | 工程与产品基础专题 |
| [1.1 任务智能体](docs/1.1task-agent-design-unified.md) · [1.2 流式显示](docs/1.2streaming-thought-display.md) · [1.3 任务韧性](docs/1.3task-resilience-design.md) | 智能体专题 |
| [2.1 项目](docs/2.1project-design.md) · [2.2 知识库](docs/2.2knowledge-base-design.md) · [2.3 PPT](docs/2.3ppt-export-design.md) · [2.4 填表](docs/2.4forms-design.md) | 功能模块专题 |
| [2.5 报告](docs/2.5report-design.md) · [2.6 Wiki](docs/2.6wiki-design.md) · [2.7 基础库 RAG](docs/2.7baselib-rag-design.md) · [2.8 浏览器抓取](docs/2.8browser-fetch-design.md) | 功能模块专题 |
| [2.9 项目传输](docs/2.9project-transfer-design.md) · [2.10 Token 用量](docs/2.10token-usage-design.md) · [2.11 通知](docs/2.11notification-design.md) · [2.11 定时任务](docs/2.11scheduled-task-design.md) | 功能模块专题 |
| [3.0 项目生命周期](docs/3.0project-lifecycle-design.md) · [3.1 智能层](docs/3.1intelligence-design.md) | 生命周期与智能层专题 |

## 📄 开源协议

本项目基于 [AGPL-3.0](LICENSE) 协议开源。
