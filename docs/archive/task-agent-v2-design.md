# 通用智能体 V2 设计文档 — Skill 框架与智能体能力升级

> 版本: 0.7.0 | 日期: 2026-09-22 | 状态: 实施完成
>
> 本文档是 `task-agent-design.md`（V1）的补充，聚焦两项核心升级：**统一 Skill 框架** 和 **智能体能力对标业界一线**（Cursor / Aider / Devin 80-90 分水平）。

---

## 1. 目标与差距分析

### 1.1 对标评估（V1 现状）

| 维度 | 墨方 V1 | Cursor | Aider | Devin | V1 得分 | V2 目标 |
|------|---------|--------|-------|-------|---------|---------|
| Agent 循环架构 | 5 | 9 | 8 | 9 | 文本解析 | Function Calling |
| Prompt 工程 | 5 | 9 | 8 | 9 | 基础模块化 | 注入 git/项目上下文 |
| 工具系统 | 7 | 9 | 7 | 9 | 14 个硬编码 Tool | 统一 Skill 框架 |
| 上下文管理 | 4 | 9 | 8 | 9 | 硬截断 | Token-aware + git diff |
| 代码编辑能力 | 5.5 | 9 | 9 | 8 | search_replace | diff 格式 + 行号编辑 |
| 错误恢复 | 6 | 8 | 7 | 9 | stuck detection | 自动验证 + 回退策略 |
| 规划与推理 | 5 | 7 | 6 | 9 | 双模型 planner | self-reflection |
| 安全机制 | 7 | 7 | 5 | 8 | 三模式确认 | 保持 |
| 项目理解 | 5 | 9 | 7 | 9 | repo_map | git 集成 + 上下文注入 |
| 用户体验 | 6 | 9 | 6 | 9 | SSE 流式 | inline diff 展示 |

**V1 加权综合：~55 分 → V2 目标：80-90 分**

### 1.2 核心判断

> V1 在工具生态和安全机制上做得扎实，但三个最关键维度存在代际差距：
> 1. **Agent 循环**：文本解析 vs 原生 Function Calling
> 2. **上下文管理**：无 token 计数、无 git diff 注入
> 3. **Skill 框架**：内置 Tool 硬编码、外部 Skill 万能代理模式
>
> 这三项改进占总提升的 ~60%。

---

## 2. 统一 Skill 框架

### 2.1 现状问题

```
当前：两套割裂的系统

┌── 内置 Tool（python/tools/）──────────────┐  ┌── 第三方 Skill ──────────┐
│ 14 个 BaseTool 子类，硬编码在 task_agent.py │  │ skill_runner 万能代理    │
│ 无 manifest / 无版本 / 无依赖声明          │  │ 只有 run(params) 入口    │
│ 无法从外部增删                            │  │ 独立 venv，subprocess    │
└──────────────────────────────────────────┘  └──────────────────────────┘

问题：
• 内置 Tool 是"焊死"的，增加新能力需改源码
• 第三方 Skill 与内置 Tool 接口完全不同
• 无法为 function calling 动态生成 tools 列表
• 外部开发者不知道能声明什么、怎么被调用
```

### 2.2 目标架构 — 两模式并存

```
┌────────────────────────────────────────────────────────┐
│                    Skill Registry                       │
│                                                        │
│  ┌── 内置 Tool（python/tools/）──┐  ┌── Agent Skills ─┐ │
│  │ BaseTool 子类，in-process     │  │ SKILL.md +      │ │
│  │ 通过 BuiltinToolAdapter 注册  │  │ scripts/，       │ │
│  │                              │  │ 任务 venv 运行   │ │
│  │ code_executor, shell,        │  │ 行业标准格式     │ │
│  │ file_ops, code_search, ...   │  │ （60,000+ 可用） │ │
│  └──────────────────────────────┘  └────────────────┘ │
│                                                        │
│  统一：                                                  │
│  • SkillRegistry 是唯一入口，agent 只跟它打交道          │
│  • 自动生成 function calling tools 列表                  │
│  • 统一执行协议（pre_execute → execute → post_execute）  │
│                                                        │
│  区别（有意保留）：                                       │
│  • 内置 Tool：留在 python/tools/，继承 BaseTool         │
│  • Agent Skills：SKILL.md + scripts/，任务 venv 运行    │
│  • Adapter 不是过渡方案，是最终设计                      │
└────────────────────────────────────────────────────────┘
```

### 2.3 Skill 定义规范

**内置 Tool** 通过 `BuiltinToolAdapter` 自动注册，无需额外定义文件。

**Agent Skills** 使用行业标准 `SKILL.md` 格式（详见 V3 设计文档 §11.2）：

```yaml
---
name: my-skill                       # 唯一标识（小写+连字符）
description: "Skill 功能描述"         # 最长 1024 字符
compatibility: "Requires Python 3.8+" # 可选：环境依赖声明
---

# Skill 标题

## 何时使用
描述触发条件。

## 执行步骤
1. 运行脚本：`scripts/main.py ...`
2. 返回结果
```

### 2.4 统一 Skill 协议

```python
# python/skills/protocol.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillContext:
    """Skill 执行时的运行时上下文"""
    workspace_path: str           # 任务工作目录
    venv_python: str = ""         # 任务 venv 的 python 路径
    task_id: str = ""
    settings: dict = field(default_factory=dict)
    cancel_event: Any = None      # asyncio.Event，用于取消


class BaseSkill:
    """所有 Skill 的基类（内置和外部统一）"""

    name: str = ""
    description: str = ""
    input_schema: dict = {}
    permissions: list[str] = []

    def get_manifest(self) -> dict:
        """返回 skill.yaml 解析后的 manifest"""
        ...

    async def execute(self, params: dict, context: SkillContext) -> str:
        """执行 Skill

        Args:
            params: 符合 input_schema 的参数
            context: 运行时上下文

        Returns:
            JSON 字符串，标准格式:
            成功: {"exit_code": 0, "stdout": "...", ...}
            失败: {"error": "...", "error_type": "...", "exit_code": -1}
        """
        raise NotImplementedError

    async def pre_execute(self, params: dict, context: SkillContext) -> dict | None:
        """执行前钩子，可修改参数"""
        return None

    async def post_execute(self, result: str, params: dict, context: SkillContext) -> str:
        """执行后钩子，可修改结果"""
        return result

    def to_function_definition(self) -> dict:
        """生成 OpenAI function calling 的 tool definition"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
```

### 2.5 Skill Registry

```python
# python/skills/registry.py

class SkillRegistry:
    """所有 Skill 的统一注册表"""

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}
        self._manifests: dict[str, dict] = {}

    def register(self, skill: BaseSkill, manifest: dict):
        """注册一个 Skill"""
        self._skills[skill.name] = skill
        self._manifests[skill.name] = manifest

    def register_builtin_tools(self, tools: dict[str, BaseTool]):
        """批量注册内置 Tool（通过 BuiltinToolAdapter 包装）"""
        for name, tool in tools.items():
            adapter = BuiltinToolAdapter(tool)
            self.register(adapter, {"type": "builtin", "enabled": True})

    def discover_external(self, skills_dir: str, db=None):
        """扫描 %APPDATA%/InkCube/skills/，注册所有外部 Skill"""
        ...

    def get(self, name: str) -> BaseSkill | None:
        return self._skills.get(name)

    def list_enabled(self) -> list[str]:
        return [n for n, s in self._skills.items()
                if self._manifests[n].get("enabled", True)]

    def to_function_definitions(self) -> list[dict]:
        """生成 function calling 的 tools 列表（核心！）"""
        return [
            skill.to_function_definition()
            for name, skill in self._skills.items()
            if self._manifests[name].get("enabled", True)
        ]

    def get_prompt_descriptions(self) -> str:
        """生成 system prompt 中的工具描述（精简版）"""
        ...

    async def execute(self, name: str, params: dict, context: SkillContext) -> str:
        """统一执行入口"""
        skill = self._skills.get(name)
        if not skill:
            return json.dumps({"error": f"未知 Skill: {name}"})
        # pre_execute → execute → post_execute
        override = await skill.pre_execute(params, context)
        if override is not None:
            params = override
        result = await skill.execute(params, context)
        return await skill.post_execute(result, params, context)
```

### 2.6 目录结构

```
python/tools/                           ← 保持不变：内置 Tool
├── base.py                             ← BaseTool 基类
├── code_executor.py                    ← BaseTool 子类（in-process）
├── shell_tool.py
├── file_ops.py
├── code_search.py
├── web_fetch.py
├── web_search.py (tavily_search.py)
├── code_lint.py
├── code_format.py
├── package_manager.py
├── project_scaffold.py
├── knowledge_search.py (task_knowledge_search.py)
├── doc_generator.py
├── repo_map.py
└── skill_runner.py                     ← Agent Skill 执行器

python/skills/                          ← 新增：Skill 框架层
├── __init__.py
├── protocol.py                         ← BaseSkill + SkillContext
├── registry.py                         ← SkillRegistry
├── loader.py                           ← BuiltinToolAdapter + AgentSkillAdapter
├── parser.py                           ← SKILL.md 解析器
└── context.py                          ← SkillContext 数据类

%APPDATA%/InkCube/skills/               ← Agent Skill 安装目录
├── hello-world/
│   ├── SKILL.md                        ← 定义文件（行业标准）
│   └── scripts/
│       └── hello.py
└── pdf-converter/
    ├── SKILL.md
    └── scripts/
        └── convert.py
```

> **设计决策**：内置 Tool 留在 `python/tools/`，不做物理迁移。
> `BuiltinToolAdapter` 不是过渡方案，是最终设计。
> 新增内置能力（如 git_ops）直接在 `python/tools/` 中创建新文件。

### 2.7 外部 Skill 开发者视角

开发者提供 `SKILL.md` + 可选 `scripts/` 目录（Agent Skills 行业标准）：

```
my-skill/
├── SKILL.md            # 必须：元数据 + 指令
├── scripts/            # 可选：可执行脚本
│   └── main.py
├── references/         # 可选：参考文档
└── assets/             # 可选：模板、资源
```

`scripts/main.py` 示例：

```python
import sys
import json

# 从 CLI 参数获取输入
action = sys.argv[1] if len(sys.argv) > 1 else "default"
# ... 业务逻辑 ...
print(json.dumps({"output": f"Result for {action}"}))
```

框架负责：注册、发现、依赖安装、任务 venv 中执行脚本、function calling 适配、安全确认。

### 2.8 两模式架构确认

本框架采用**两模式并存**设计，这不是过渡方案，而是最终架构：

| 模式 | 位置 | 协议 | 执行方式 | 适用场景 |
|------|------|------|----------|----------|
| **内置 Tool** | `python/tools/*.py` | 继承 `BaseTool`，通过 `BuiltinToolAdapter` 注册 | in-process 直接调用 | 核心能力（代码执行、文件操作、搜索等） |
| **Agent Skills** | `%APPDATA%/InkCube/skills/` | `SKILL.md` + `scripts/`，通过 `AgentSkillAdapter` 注册 | 任务 venv 中运行脚本 | 第三方扩展、行业标准 Skill（60,000+ 可用） |

**为什么采用两模式？**
- 内置 Tool 需要访问主进程资源（DB、settings、共享状态），in-process 更高效
- Agent Skills 使用行业标准格式（SKILL.md），兼容 Claude Code / Cursor / Codex 生态
- Agent Skills 脚本在任务 venv 中运行，共享任务环境
- `BuiltinToolAdapter` 足够薄（~30 行），维护成本几乎为零

**新增内置能力的流程**：在 `python/tools/` 中创建新文件 → 继承 `BaseTool` → 在 `task_agent.py` 中注册到 `SkillRegistry`。

**BuiltinToolAdapter 实现**：

```python
# python/skills/loader.py

class BuiltinToolAdapter(BaseSkill):
    """将 BaseTool 子类适配为 BaseSkill 接口（永久设计，非过渡方案）"""

    def __init__(self, tool: BaseTool):
        self._tool = tool
        self.name = tool.name
        self.description = tool.description
        self.input_schema = tool.parameters

    async def execute(self, params: dict, context: SkillContext) -> str:
        return await self._tool.execute(**params)

    async def pre_execute(self, params: dict, context: SkillContext) -> dict | None:
        return await self._tool.pre_execute(**params)

    async def post_execute(self, result: str, params: dict, context: SkillContext) -> str:
        return await self._tool.post_execute(result, **params)
```

---

## 3. 原生 Function Calling

### 3.1 现状 vs 目标

```
V1（文本解析）：
  LLM 输出纯文本 → 正则匹配 "Action: xxx" → 解析 JSON → 执行
  问题：格式不可靠、JSON 易出错、无法多工具并行

V2（Function Calling）：
  声明 tools 参数 → LLM 返回结构化 tool_calls → 直接读 JSON → 执行
  优势：100% 可靠、schema 约束、支持并行调用
```

### 3.2 LLM Client 改造

```python
# python/llm/client.py — 新增 chat_with_tools 方法

class LLMClient:
    async def chat(
        self,
        messages: list[dict],
        settings: dict,
        tools: list[dict] | None = None,      # 新增
    ) -> dict:
        """
        返回格式统一：
        {
            "content": str | None,         # 文本回复
            "tool_calls": list[dict] | None,  # 工具调用
            "finish_reason": str,          # "stop" | "tool_calls"
        }
        """
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.get("temperature", 0.7),
            "max_tokens": settings.get("max_tokens", 4096),
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # ... HTTP 请求 ...

        choice = data["choices"][0]
        msg = choice["message"]
        return {
            "content": msg.get("content"),
            "tool_calls": msg.get("tool_calls"),  # [{id, function: {name, arguments}}]
            "finish_reason": choice.get("finish_reason"),
        }
```

### 3.3 Agent Loop 改造

```python
# task_agent.py — _run_inner 核心循环改造

async def _run_inner(self, task_id, user_message):
    # ...
    tool_defs = self.skill_registry.to_function_definitions()

    for iteration in range(self.max_iterations):
        # ── LLM 调用（带 tools 声明）──
        result = await task_llm.chat(messages, settings, tools=tool_defs)

        # ── 情况 1：LLM 返回文本（Final Answer 或思考）──
        if result["content"] and not result["tool_calls"]:
            final = self._extract_final_answer(result["content"])
            if final:
                yield answer...
                return
            # 纯思考文本，作为 observation 反馈
            messages.append({"role": "assistant", "content": result["content"]})
            continue

        # ── 情况 2：LLM 返回 tool_calls ──
        if result["tool_calls"]:
            # 先把 assistant 消息（含 tool_calls）加入历史
            messages.append({
                "role": "assistant",
                "content": result["content"],
                "tool_calls": result["tool_calls"],
            })

            for tc in result["tool_calls"]:
                tool_name = tc["function"]["name"]
                tool_params = json.loads(tc["function"]["arguments"])

                yield {"type": "action", "data": {"tool": tool_name, "input": tool_params}}

                # 执行（含确认检查）
                tool_result = await self._execute_with_confirm(
                    task_id, tool_name, tool_params, tools, settings
                )

                # 将结果作为 tool role 消息追加
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": self._snip_observation(tool_result),
                })

                yield {"type": "observation", "data": {"content": tool_result}}
            continue
```

### 3.4 兼容性回退

对于不支持 function calling 的模型（如部分本地模型），保留文本解析作为 fallback：

```python
# 检测模型是否支持 function calling
SUPPORTS_FUNCTION_CALLING = {
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
    "claude-3-5-sonnet", "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "deepseek-chat", "deepseek-coder",
    # ...
}

def _should_use_function_calling(self, model: str) -> bool:
    return model in SUPPORTS_FUNCTION_CALLING or self.settings.get("force_function_calling", False)
```

---

## 4. 上下文管理升级

### 4.1 Token 感知

```python
# python/agent/context_manager.py

class ContextManager:
    """Token-aware 上下文管理器"""

    def __init__(self, max_tokens: int = 128000):
        self.max_tokens = max_tokens
        self.reserve_for_response = 4096
        self.reserve_for_system = 2000

    @property
    def available_tokens(self) -> int:
        return self.max_tokens - self.reserve_for_response - self.reserve_for_system

    def count_tokens(self, text: str) -> int:
        """近似 token 计数（中文 ≈ 1.5 char/token，英文 ≈ 4 char/token）"""
        # 使用 tiktoken 或简单估算
        return len(text) // 3  # 粗略估算

    def should_compress(self, messages: list[dict]) -> bool:
        """判断是否需要压缩"""
        total = sum(self.count_tokens(m.get("content", "")) for m in messages)
        return total > self.available_tokens * 0.85

    async def compress(self, messages: list[dict], llm, settings) -> list[dict]:
        """智能压缩：保留最近 N 条 + LLM 摘要早期内容"""
        # 与 V1 的 _collapse_messages 类似，但基于 token 而非消息数
        ...

    def snip(self, text: str, max_chars: int = 8000) -> str:
        """截断超长输出（保留头尾）"""
        ...
```

### 4.2 Git Diff 注入

```python
# 在 system prompt 中注入 git 上下文

def _build_git_context(self, workspace_path: str) -> str:
    """生成 git 上下文注入到 system prompt"""
    try:
        # 当前分支
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=workspace_path, timeout=5
        ).stdout.strip()

        # 最近 5 次 commit
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, cwd=workspace_path, timeout=5
        ).stdout.strip()

        # 未提交的变更摘要
        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=workspace_path, timeout=5
        ).stdout.strip()

        if not branch and not log:
            return ""  # 不是 git 仓库

        parts = [f"📂 Git: 分支 `{branch}`"]
        if log:
            parts.append(f"最近提交:\n{log}")
        if status:
            parts.append(f"未提交变更:\n{status}")
        return "\n".join(parts)
    except Exception:
        return ""
```

### 4.3 项目上下文自动检测

```python
def _detect_project_context(self, workspace_path: str) -> str:
    """自动检测项目技术栈和配置"""
    hints = []

    # Python 项目
    if os.path.isfile(os.path.join(workspace_path, "pyproject.toml")):
        hints.append("项目类型: Python (pyproject.toml)")
    elif os.path.isfile(os.path.join(workspace_path, "requirements.txt")):
        hints.append("项目类型: Python (requirements.txt)")

    # Node.js 项目
    pkg_json = os.path.join(workspace_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            deps = list(pkg.get("dependencies", {}).keys())[:10]
            hints.append(f"项目类型: Node.js | 主要依赖: {', '.join(deps)}")
        except Exception:
            hints.append("项目类型: Node.js")

    # 其他框架检测
    for marker, framework in [
        ("Cargo.toml", "Rust"), ("go.mod", "Go"),
        ("pom.xml", "Java/Maven"), ("build.gradle", "Java/Gradle"),
    ]:
        if os.path.isfile(os.path.join(workspace_path, marker)):
            hints.append(f"项目类型: {framework}")

    return "\n".join(hints) if hints else ""
```

---

## 5. Git 工具（新增内置 Tool）

### 5.1 设计

新增 `python/tools/git_ops.py`，继承 `BaseTool`，通过 `BuiltinToolAdapter` 自动注册到 `SkillRegistry`。

```python
# python/tools/git_ops.py

class GitOpsTool(BaseTool):
    name = "git_ops"
    description = "Git 版本控制操作：查看状态、diff、log、commit"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "diff", "log", "show", "blame", "commit"],
                "description": "Git 操作类型",
            },
            "args": {
                "type": "string",
                "description": "传递给 git 命令的额外参数",
            },
        },
        "required": ["action"],
    }
    # ... execute 实现 ...
```

### 5.2 支持的操作

| action | 说明 | 用途 |
|--------|------|------|
| `status` | `git status --short` | 查看工作区状态 |
| `diff` | `git diff [args]` | 查看未提交变更 |
| `diff_staged` | `git diff --cached` | 查看已暂存变更 |
| `log` | `git log --oneline -20` | 查看提交历史 |
| `show` | `git show <commit>` | 查看某次提交详情 |
| `blame` | `git blame <file>` | 查看文件逐行归属 |
| `commit` | `git commit -m "msg"` | 提交变更（需确认） |

---

## 6. Self-Reflection / 验证循环

### 6.1 编辑后自动验证

```python
# 在 file_ops 的 post_execute 中自动验证

class FileOpsTool(BaseTool):
    async def post_execute(self, result: str, **kwargs) -> str:
        """编辑 .py 文件后自动语法检查，编辑 .ts/.js 后自动 lint"""
        action = kwargs.get("action", "")
        path = kwargs.get("path", "")
        if action in ("write", "search_replace"):
            if path.endswith(".py"):
                # 自动语法检查（已内置）
                ...
            elif path.endswith((".ts", ".tsx", ".js", ".jsx")):
                # 自动调用 ruff / eslint（如果可用）
                ...
        return result
```

### 6.2 Agent 自我审查步骤

在 Final Answer 之前插入 self-reflection：

```python
# 当 agent 准备输出 Final Answer 时，注入审查提示

_SELF_REVIEW_PROMPT = """
在给出最终回答之前，请检查：
1. 所有修改的文件是否已保存且语法正确？
2. 是否需要运行测试验证？
3. 是否有遗漏的需求点？
如果一切就绪，输出 Final Answer。如果需要补充操作，继续 Action。
"""
```

### 6.3 编辑-验证-修复循环

```
编辑文件 → 自动语法检查 → 失败 → 将错误反馈给 LLM → 自动修复 → 再检查
                                    （最多 3 轮）
```

---

## 7. Prompt 工程升级

### 7.1 新版 System Prompt 结构

```python
def _build_system_prompt(self, workspace_path, mode, settings):
    parts = [
        # 1. 身份
        _PROMPT_IDENTITY,

        # 2. 时间
        _date_time_line,

        # 3. 项目上下文（V2 新增）
        _detect_project_context(workspace_path),    # ← V2 新增
        _build_git_context(workspace_path),          # ← V2 新增
        _load_project_config(workspace_path),        # inkcube.md（V1 已有）

        # 4. 工具列表（V2：从 SkillRegistry 生成）
        "可用工具:",
        self.skill_registry.get_prompt_descriptions(),  # ← V2 改造

        # 5. 工作目录
        f"工作目录: {workspace_path}",

        # 6. 行为准则
        _PROMPT_EFFICIENCY,
        _PROMPT_VERIFICATION,
        _PROMPT_RICH_TEXT,

        # 7. 编辑格式指导（V2 新增）
        _PROMPT_EDIT_FORMAT,                       # ← V2 新增
    ]
    return "\n".join(p for p in parts if p)
```

### 7.2 编辑格式指导（新增 prompt 段）

```python
_PROMPT_EDIT_FORMAT = """📝 代码编辑指导：
- 修改已有文件时，优先使用 search_replace（比整文件重写更高效）
- search_replace 的 old_content 应包含足够的上下文以唯一定位（建议包含函数签名或前后几行）
- 创建新文件用 write/create
- 编辑后建议用 code_executor 运行验证（至少一次）
- 如果需要了解项目结构，先调用 repo_map 获取全局视图
- 如果需要搜索特定代码，使用 code_search 而非逐个文件 read"""
```

---

## 8. 前端适配

### 8.1 Function Calling 的 UI 变化

V2 的 tool_calls 可能一次返回多个工具调用，前端需要支持并行展示：

```
┌─────────────────────────────────────────┐
│ 💭 我需要搜索相关代码并查看项目结构      │
│                                         │
│ 🔧 并行调用:                            │
│ ├─ code_search {"pattern": "auth"}      │
│ │  → 找到 12 处匹配                    │
│ └─ repo_map {"detail": "brief"}         │
│    → 项目结构: src/ (23 文件) ...       │
│                                         │
│ 💭 找到了认证模块，现在修改...           │
│ 🔧 file_ops {"action": "search_replace"}│
│    → ✅ 替换成功 (Level 1 精确匹配)     │
└─────────────────────────────────────────┘
```

### 8.2 Skill 管理页升级

V2 的 Skill 管理页需要展示更多信息：

```
┌─────────────────────────────────────────────────────┐
│ Skill 管理                                         │
├─────────────────────────────────────────────────────┤
│ 内置 Tool (14)               外部 Skill (2)         │
│ ┌──────────────────────┐    ┌──────────────────┐   │
│ │ ✅ code-executor     │    │ 🔌 pdf-converter │   │
│ │ ✅ shell             │    │ 🔌 api-tester    │   │
│ │ ✅ file-ops          │    └──────────────────┘   │
│ │ ✅ code-search       │                           │
│ │ ✅ git-ops     🆕   │    [安装新 Skill]          │
│ │ ...                  │                           │
│ └──────────────────────┘                           │
└─────────────────────────────────────────────────────┘
```

---

## 9. 迁移计划

### Phase 1：框架骨架（纯新增，零破坏）

| 文件 | 操作 | 说明 |
|------|------|------|
| `python/skills/__init__.py` | 新建 | 包初始化 |
| `python/skills/protocol.py` | 新建 | BaseSkill + SkillContext |
| `python/skills/registry.py` | 新建 | SkillRegistry |
| `python/skills/loader.py` | 新建 | BuiltinToolAdapter（桥接 V1） |
| `python/skills/context.py` | 新建 | SkillContext 数据类 |

**验收**：现有功能不受影响，`SkillRegistry` 可通过适配层加载所有 V1 工具。

### Phase 2：Agent Loop 接入 Function Calling

| 文件 | 操作 | 说明 |
|------|------|------|
| `python/llm/client.py` | 修改 | `chat()` 新增 `tools` 参数，返回结构化结果 |
| `python/agent/task_agent.py` | 修改 | `_run_inner` 支持 function calling 分支 |
| `python/agent/task_agent.py` | 保留 | 文本解析作为 fallback |

**验收**：使用 GPT-4o 时走 function calling，使用不支持的模型时回退到文本解析。

### Phase 3：上下文管理升级

| 文件 | 操作 | 说明 |
|------|------|------|
| `python/agent/context_manager.py` | 新建 | Token-aware 上下文管理 |
| `python/agent/task_agent.py` | 修改 | 接入 ContextManager，替换硬编码阈值 |
| `python/agent/task_agent.py` | 修改 | system prompt 注入 git 上下文 + 项目上下文 |

**验收**：长任务不再"忘记"早期上下文；agent 知道当前 git 状态。

### Phase 4：Git 工具 + Self-Reflection

| 文件 | 操作 | 说明 |
|------|------|------|
| `python/tools/git_ops.py` | 新建 | git_ops 内置 Tool（BaseTool 子类） |
| `python/agent/task_agent.py` | 修改 | Final Answer 前插入 self-review 提示 |
| `python/tools/file_ops.py` | 修改 | post_execute 自动验证增强 |

**验收**：agent 可以查看 git 状态/diff/log；编辑后自动验证。

> **注**：内置 Tool 不做物理迁移。`python/tools/` 保持现状，
> 通过 `BuiltinToolAdapter` 永久桥接到 `SkillRegistry`。

---

## 10. 评分预期

### 10.1 各 Phase 完成后的分数预估

| 阶段 | Agent 循环 | 上下文 | 工具系统 | 编辑 | 项目理解 | 综合 |
|------|-----------|--------|---------|------|---------|------|
| V1 现状 | 5 | 4 | 7 | 5.5 | 5 | ~55 |
| + Phase 1 (框架) | 5 | 4 | 8 | 5.5 | 5 | ~58 |
| + Phase 2 (FC) | 8 | 4 | 8 | 5.5 | 5 | ~68 |
| + Phase 3 (上下文) | 8 | 7 | 8 | 5.5 | 7 | ~76 |
| + Phase 4 (Git+反思) | 8 | 7 | 8.5 | 6.5 | 8 | ~82 |

### 10.2 与业界对标（V2 完成后）

| 维度 | V1 | V2 | Cursor | Aider |
|------|----|----|--------|-------|
| Agent 循环 | 文本解析 | Function Calling + fallback | FC | FC |
| 上下文管理 | 硬截断 | Token-aware + git diff | 精确 token | repo-map + token |
| 代码编辑 | search_replace | search_replace + 自动验证 | inline diff | SEARCH/REPLACE |
| Git 集成 | 无 | git_ops skill + 上下文注入 | 深度集成 | 核心特性 |
| 安全 | 三模式确认 | 同 V1 + 权限声明 | 基础 | 弱 |
| 可扩展性 | 硬编码 | 统一 Skill 框架 | 插件 | 无 |

---

## 11. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Skill 框架 | 统一 manifest + 统一协议 | 内置/外部一致体验，支持 function calling |
| Function Calling | 支持 + 文本解析 fallback | 可靠性飞跃，同时兼容本地模型 |
| 迁移策略 | 双轨并存（Adapter 永久桥接） | 零破坏，内置 Tool 无需物理迁移 |
| 上下文管理 | Token-aware + git diff 注入 | 解决长任务"失忆"问题 |
| Git 集成 | 新增 git_ops 内置 Tool | 让 agent 感知版本控制状态 |
| Self-reflection | Final Answer 前插入审查 | 低成本提升准确性 |
| Agent Skills 格式 | SKILL.md + scripts/，任务 venv 运行 | 行业标准，兼容 60,000+ 现有 Skill |

---

## 12. 不做的事

- **不做**：内置 Tool 物理迁移到 skills/builtin/（Adapter 永久桥接，无实际收益）
- **不做**：Skill 市场 / 在线商店（V2 范围外）
- **不做**：跨会话长期记忆（已有单独规划）
- **不做**：LSP 集成（V2 范围外）
- **不做**：多 Agent 协作（V2 范围外）

---

## 13. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-21 | 初始设计：Skill 框架 + Function Calling + 上下文管理 + Git 集成 + Self-Reflection |
| 0.2.0 | 2026-09-21 | 确认双轨架构：内置 Tool 留在 python/tools/，通过 BuiltinToolAdapter 永久桥接；取消 Phase 5 物理迁移 |
| 0.3.0 | 2026-09-21 | Phase 1-3 实施完成：Skill 框架骨架 + Function Calling + Token-aware 上下文管理 + git/project 上下文注入 |
| 0.4.0 | 2026-09-21 | Phase 4 + 查缺补漏完成：git_ops 内置 Tool + commit 确认流程 + _PROMPT_EDIT_FORMAT + _PROMPT_SELF_REVIEW + file_ops post_execute .ts/.js lint |
| 0.5.0 | 2026-09-21 | 全面 Code Review 修复：FC skill 确认弹窗 + git_ops shlex 引号解析 + commit 不再无条件 add -A + 清理死代码 + 压缩 prompt 错别字 + FC 模型集合更新(gpt-4.1/claude-3-7/deepseek-reasoner) + LLM 超时可配置(默认 120s) + 压缩后 tool_call/tool 配对保护 + max_iterations 可配置(默认 25) + git_ops 支持 worktree 检测 + 项目配置支持 .cursorrules/AGENTS.md |
| 0.6.0 | 2026-09-21 | 能力提升：tiktoken 精确 token 计数(可选+fallback) + tree-sitter AST 签名提取(repo_map) + LLM 流式输出(chat_stream + 前端增量渲染) + git_ops commit 前展示 diff --stat |
| 0.7.0 | 2026-09-22 | 外部 Skill 统一为 Agent Skills 标准（SKILL.md），移除 skill.yaml 隔离格式，两模式并存（内置 Tool + Agent Skills） |
