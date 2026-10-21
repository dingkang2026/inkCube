# 贡献指南

感谢你对墨方 InkCube 的关注！无论是报告问题、完善文档还是提交代码，都欢迎。

## 环境要求

- Node.js ≥ 24（含 npm）
- conda（Python 3.12）
- 操作系统：Windows / macOS / Linux（当前 Windows 打包链路实测最完整）

## 本地开发

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

> 首次启动可在设置中配置 LLM API（任意 OpenAI 兼容端点）；内置模型（bge-m3 / Docling 系列，约 4.8 GB）按需在设置中下载。更多细节见[开发指南](docs/0.1dev-guide.md)。

## 📦 打包发布（可选）

```powershell
powershell scripts/build-installer.ps1   # Windows 安装包全流程
```

> 前置条件：`resources/tools/`（python-build-standalone、uv / rg / ruff / pandoc.exe、tool-versions.json）与 `resources/python-backend/` 均不入库（见 .gitignore）——前者先运行 `powershell scripts/prepare-tools.ps1`（按锁定版本自动下载并逐件回读校验，可选 `-MirrorPrefix` 走加速代理；网络受限也可按 [0.2 §6.2](docs/0.2packaging-design.md) 手动放置），后者由构建脚本自动装配。

## 🧪 测试与验证

提交前请确保以下检查通过：

```powershell
python -m pytest python/tests/ -v    # 后端单测（pytest）
npx vitest run                        # 前端单测（vitest）
npx tsc --noEmit                      # 前端类型检查
npm run build                         # 前端构建
node scripts/orphan_channel_audit.js  # IPC 孤儿通道审计（四组输出应为 0）
```

## 代码规范要点

- **统一日志**：Python 使用 `logging` 模块，Electron 使用 `log-utils.js`（同写 `app.log`）；禁止随意 `console.log` / `print`。
- **中英双语**：所有用户可见文案 zh-CN / en-US 同步维护（`src/i18n`）。
- **长时活动统一模型**：凡「排队 → 执行 → 终态」的异步作业，一律注册到统一 Activity 总线（`kind/status/progress/message`），禁止自建轮询与自定义状态语义。
- **全链路交付**：新增后端端点 / IPC 通道必须在同一变更内落地前端消费与 UI 承载，禁止预留无消费方的通道；交付前运行孤儿通道审计。
- **文档双向对齐**：改动涉及设计文档（`docs/`）三要素（表结构 / 数据流 / 前端交互）时，代码与文档同步更新。
- **根因优先**：修复 bug 必须先通过测试定位根因，并补充回归测试；兜底防御只能叠加在根因修复之上。

## 提交与 PR

- Git 操作由贡献者自行完成；提交信息建议遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:` …）。
- PR 请附：变更动机、验证证据（测试 / 构建输出）、涉及的设计文档同步说明。
- 想找活干？可从 [README「下一步工作」](README.zh-CN.md#-下一步工作roadmap)认领，或浏览 issue 列表。

## 行为准则

参与本项目（含 issue、PR、讨论区）即表示接受[行为准则](CODE_OF_CONDUCT.md)。

## 许可

提交贡献即表示你同意以 [AGPL-3.0](LICENSE) 协议许可所提交的内容。
