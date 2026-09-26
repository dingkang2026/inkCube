# 墨方 InkCube

本地知识库 + 文档生成智能助手。除大模型外，所有功能均在本地运行。

> 当前阶段：基础框架 — 聊天功能（ReAct Agent）

## 功能特性

- ReAct 智能体（Thought → Action → Observation 循环）
- 可配置 LLM（兼容 OpenAI API 格式的任意模型）
- 本地 SQLite 持久化（会话、消息、设置）
- 内置 Tavily 网络搜索工具
- 可拖拽调整宽度的三栏布局（侧边栏 / 聊天区 / 设置面板）
- 用户数据与程序分离，升级不覆盖用户数据

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18 + TypeScript + Vite 6 |
| 桌面 | Electron 33 |
| Agent 后端 | Python 3.12 + FastAPI |
| 数据库 | SQLite (WAL 模式) |
| 通信 | HTTP + SSE |
| 打包 | electron-builder + PyInstaller |

## 开发安装

详见 [开发指南](docs/0.1dev-guide.md)。

## 开源协议

[AGPL-3.0](LICENSE)
