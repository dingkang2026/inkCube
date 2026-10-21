# InkCube 墨方

> 墨方 (Mòfāng) — a blend of the Chinese words for "ink" (笔墨) and "Rubik's cube" (魔方): one knowledge engine, endlessly many reports.

English | [简体中文](README.zh-CN.md)

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![Electron](https://img.shields.io/badge/electron-33-brightgreen)
![License](https://img.shields.io/badge/license-AGPL--3.0-orange)

**A local-first project workbench built for investment-promotion and investment professionals.** Delegate the "collect information → organize → fill forms / draft reports / build decks" loop to a task agent, and spend your time on judgment instead — designed for managers who need to juggle dozens of deals at once. Except for LLM API calls, all data and computation stay on your machine: **your private data never leaves your domain**.

## Why InkCube?

| Your day | Without InkCube | With InkCube |
|----------|-----------------|--------------|
| Morning triage | Dig through N systems / group chats / spreadsheets | One screen: today's overview + to-dos + activity feed |
| Boss or client asks about a company out of the blue | Manual lookups on Tianyancha / search engines, pasting together a memo | Task agent browses the web and drafts the report for you |
| A target company hits the news (negative / competitor move) | You hear about it last | Scheduled subscriptions watch for you; intel digest lands on your home page |
| Forms, weekly / monthly reports, pitch decks | Copy-pasting the same info into fixed formats | Forms / reports / PPT generated from templates, automatically |
| Kicking off a new deal | New Word doc, rebuilt folder tree, hunting for templates | One-click project templates: deal type + stage + preset structure |
| Reporting upward / sharing sideways | Zip files, email attachments, stitched screenshots | Project export, report / PPT export |

Built-in **primary-market methodology skills** (due-diligence checklists, business-model teardowns, valuation models, IB documents) — methodology that used to live only in your head.

## ✨ Features (implemented)

### Task agent

- Conversational execution: general / coding dual-mode Function Calling loop with 20+ built-in tools; SSE streaming thought display, context compaction
- Task resilience: crash recovery, checkpoint resume — the database is the single source of truth
- Per-task workspace and Python venv; file tree / preview / download; one-click import of outputs into a project or the base library
- Skill framework: compatible with the Agent Skills standard; install and manage third-party skills; built-in primary-market methodology skills
- Browser fetch: background window pool collects public web information; web login-state management (domain allowlist + cookie cleanup)
- Task templates: 10 built-in (info gathering / sentiment monitoring / industry intel / report & research / office) + custom templates with placeholder forms

### Scheduled subscriptions

- Periodic collection of news / competitor moves / industry developments: the scheduler dispatches shadow tasks that reuse the agent execution chain
- Auto-pause on repeated failure, catch-up runs for missed windows (≤24h), incremental injection (each issue writes only what's new); unified subscription manager

### Project workspace

- Project types (investment promotion / investment / general) × lifecycle stages (pre / during / post deal)
- Affairs (to-dos): four kinds — material / report / milestone / recurring; due reminders; auto-generated on stage transitions
- Six output tabs: materials, wiki, chat, reports, forms, PPT
- Project export / import: pack a project up and carry or share it

### Intelligence layer

- Project pulse bar with an AI status sentence
- Ledger timeline cards (monitoring pages parsed into append-only timelines)
- Event-driven task suggestions
- Milestone ledger extraction (wiki compile hook)

### Knowledge base & RAG

- Docling parsing (PDF / Word / PPT) → chunking → bge-m3 embeddings → hybrid retrieval over ChromaDB + FTS5 (RRF fusion)
- Automatic image extraction + VLM descriptions; project library + base library (shared across projects)

### LLM Wiki

- Per-file incremental ingestion (two-step chain of thought); entity / concept / topic pages with bidirectional links, lint health checks, supervised review

### Report engine

- Template-driven multi-step pipeline: variable DAG → multi-channel retrieval → knowledge cards → section-by-section generation (checkpoint resume) → consistency pass
- Report template management; export to Word / PDF

### Form filling

- Upload / parse form templates (global assets) → launch fill instances (project-scoped) → manual revision → download

### PPT

- PPT template management and parsing, deliverable generation (task-pipeline execution), progress and artifact aggregation

### Template management

- Report / form / PPT / task templates, managed in one place

### Home & onboarding

- Today overview (projects / overdue / due today / in progress), today's to-dos + upcoming calendar, project card wall, activity feed, quick actions
- Intel digest: cross-subscription stream of the latest issues (unread first; "view all" opens the subscription manager)
- SystemAlerts global banner (service issues / antivirus authorization; invisible when healthy)
- First-launch wizard, three-step first-project guide, onboarding bubbles

### Activities & notifications

- Unified long-running activity bus: tasks / file parsing / image description / wiki compile / reports / forms / model downloads share one state machine and crash-recovery model
- Notification center + OS notifications + affair due reminders + terminal Toast / badges / home feed; SSE push with heartbeat fallback

### Token usage

- Today / total / range summaries; grouped by day / source / model / project; per-task run details

### Settings & system

- LLM / VLM / embedding service configuration (any OpenAI-compatible endpoint; Zhipu GLM-4-Flash by default)
- Built-in model download management (bge-m3 / Docling family, ~4.8 GB) with multi-source fallback (ModelScope → hf-mirror → Hugging Face) and integrity verification
- In-app log viewer, health checks and readiness self-tests

### Platform & engineering

- Windows / macOS / Linux; bilingual i18n (zh-CN / en-US)
- Data and program separated; upgrades never touch user data
- Uniform list pagination contract, cascading delete cleanup, IPC orphan-channel audit

## 🚧 Roadmap

| Item | Phase | Notes |
|------|-------|-------|
| PPT templates | Near-term | Keep polishing & expanding on top of the shipped template management / parsing |
| Multi-platform validation & tuning | Near-term | macOS / Linux installer testing (the Windows install / upgrade / uninstall chain is fully validated; mac / Linux packaging targets are configured) |
| Long-term memory | Long-term | Cross-task, cross-session memory |
| Server edition | Long-term | Server-based deployment |
| H5 remote control | Long-term | Remote view & control via mobile H5 |

## 🏗 Architecture

```
React + TypeScript (Vite)
        │  contextBridge IPC
        ▼
   Electron main ──── child-process mgmt · unified logging · Mermaid offscreen rendering
        │  HTTP + SSE (127.0.0.1)
        ▼
   FastAPI (Python 3.12)
   Knowledge │ Wiki │ Reports │ Forms │ PPT │ Task agent │ Schedules │ Activities / Notifications / Usage
        │
   SQLite (WAL) + ChromaDB + local models (bge-m3 / Docling family)
```

- All long-running operations share one state machine and crash-recovery model; the database is the single source of truth, the frontend is a read-only mirror
- The only external dependency is one OpenAI-compatible LLM endpoint (VLM for image description, optional degradation)

## 🛠 Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Vite 6 |
| Desktop | Electron 33 (contextBridge isolation) |
| Backend | Python 3.12 + FastAPI (SSE streaming) |
| Data | SQLite (WAL) + ChromaDB + FTS5 |
| Parsing / embeddings | Docling + bge-m3 + RapidOCR (local inference) |
| Packaging | electron-builder + python-build-standalone + uv dependency assembly |

## 🚀 Quick start (development)

```powershell
# 1. Frontend dependencies
npm install

# 2. Python environment (conda)
conda create -n inkcube python=3.12
conda activate inkcube
pip install -r python/requirements.txt

# 3. Start the full stack (Vite + Electron + Python)
npm run dev
```

> On first launch, configure the LLM API in Settings (defaults to Zhipu GLM-4-Flash; any OpenAI-compatible model works). Built-in models (bge-m3 / Docling family, ~4.8 GB) are downloaded on demand from Settings. See the [dev guide](docs/0.1dev-guide.md) (Chinese) for details.

## 📦 Packaging

```powershell
npm run pack                        # build frontend + electron-builder
powershell scripts/build-installer.ps1   # full Windows installer pipeline
```

> Note: `resources/tools/` and `resources/python-backend/` are git-ignored build artifacts — run `powershell scripts/prepare-tools.ps1` first for the former (pinned versions; add `-MirrorPrefix` behind restrictive networks, or place files manually per the [packaging design doc](docs/0.2packaging-design.md), Chinese), the latter is assembled by the build script.

## 🧪 Testing & verification

```powershell
python -m pytest python/tests/ -v    # backend unit tests (pytest)
npx vitest run                        # frontend unit tests (vitest)
npx tsc --noEmit                      # type check
npm run build                         # frontend build
node scripts/orphan_channel_audit.js  # IPC orphan-channel audit (all groups must be 0)
```

## 🤝 Contributing

Issues and PRs are welcome! Please read the [contributing guide](CONTRIBUTING.md) (Chinese) first. By participating you accept the [code of conduct](CODE_OF_CONDUCT.md). Security issues: please report privately via [SECURITY.md](SECURITY.md).

## 📚 Documentation (Chinese)

| Doc | Contents |
|---|---|
| [0.0 Architecture](docs/0.0architecture.md) | Layered architecture, data model, modules, extension points |
| [0.1 Dev guide](docs/0.1dev-guide.md) | Environment, project structure, testing, packaging |
| [0.2 Packaging](docs/0.2packaging-design.md) · [0.3 UX](docs/0.3ux-design.md) · [0.4 Lifecycle](docs/0.4lifecycle-design.md) · [0.5 Product redesign](docs/0.5product-redesign.md) | Engineering & product foundations |
| [1.1 Task agent](docs/1.1task-agent-design-unified.md) · [1.2 Streaming display](docs/1.2streaming-thought-display.md) · [1.3 Task resilience](docs/1.3task-resilience-design.md) | Agent topic |
| [2.1 Project](docs/2.1project-design.md) · [2.2 Knowledge base](docs/2.2knowledge-base-design.md) · [2.3 PPT](docs/2.3ppt-export-design.md) · [2.4 Forms](docs/2.4forms-design.md) | Feature modules |
| [2.5 Reports](docs/2.5report-design.md) · [2.6 Wiki](docs/2.6wiki-design.md) · [2.7 Base-lib RAG](docs/2.7baselib-rag-design.md) · [2.8 Browser fetch](docs/2.8browser-fetch-design.md) | Feature modules |
| [2.9 Project transfer](docs/2.9project-transfer-design.md) · [2.10 Token usage](docs/2.10token-usage-design.md) · [2.11 Notifications](docs/2.11notification-design.md) · [2.11 Scheduled tasks](docs/2.11scheduled-task-design.md) | Feature modules |
| [3.0 Project lifecycle](docs/3.0project-lifecycle-design.md) · [3.1 Intelligence layer](docs/3.1intelligence-design.md) | Lifecycle & intelligence |

## 📄 License

Released under the [AGPL-3.0](LICENSE).
