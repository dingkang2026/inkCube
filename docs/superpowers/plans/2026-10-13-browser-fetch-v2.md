# browser-fetch V2（LLM 驱动 action 化 + BrowserPanel 嵌入面板）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 browser-fetch 从单命令 URL 抓取升级为 LLM 驱动的七原语 action 循环（open/click/type/select/scroll/extract/close，每步返回页面快照），浏览器承载从独立池/登录窗口改为主窗口内嵌 BrowserPanel webview（右上角三态，人工可随时接管）。

**Architecture:** Python skill CLI 每步 `connect_over_cdp` attach 常驻面板 webview 的 target（页面状态常驻 webview，无进程内会话）；Electron 删除池/登录窗口只留 partition 级 SSRF 拦截与 cookie 清理；前端新增零依赖 browserPanelStore + BrowserPanel 组件，登录载体统一为面板。

**Tech Stack:** Python 3.12 + playwright（connect_over_cdp）+ pytest；Electron 33 + TypeScript/React 18 + vitest。

**Spec:** `docs/2.8browser-fetch-design.md`（V2 部分：§1 决策表 / §2 架构 / §3.3 租约锁 / §4.1 数据流 / §5.2 action 契约 / §6.2 面板 webview / §7.1 清理清单 / §8 前端交互 / §11 测试计划 / §12 S5-S8）

## Global Constraints

- **禁止任何 git 操作**（项目规范 §一）：计划中不含 commit 步骤，每任务以验证通过为完成标志，git 由用户手动提交
- Python 用 conda `inkcube`：`C:/Users/Kelvin/.conda/envs/inkcube/python.exe`；测试命令 `pytest python/tests/ -v`（在仓库根执行）
- 前端验证：`npx tsc --noEmit` + `npx vitest run`；Electron 语法：`node --check electron/<file>.js`
- 日志规范：Python 用 `logging`（logger `"browser-fetch"`）；Electron 用 `logElectron`；skill 脚本 stdout 是与 adapter 的唯一通信通道，**print 仅限 `emit()`**
- UI 文案中英双语：`src/i18n/locales/zh-CN.json` 与 `en-US.json` 同步增删
- 快照限制常量（spec §5.2 定版值）：正文截断 3000 字符、elements 上限 80 条、name ≤40 字符、租约锁 `LOCK_STALE_SECONDS = 60`
- 任务的 `skill_script_timeout` 默认 120s（loader 现状，不改）

---

### Task 1: loader 注入 `INKCUBE_TASK_ID`（租约锁 owner 来源）

**Files:**
- Modify: `python/skills/loader.py:394-400`（`_run_script` 的 `create_subprocess_exec` 调用）
- Test: `python/tests/test_agent_skill_spec.py`（追加测试类）

**Interfaces:**
- Produces: skill 子进程环境变量 `INKCUBE_TASK_ID = context.task_id`（空值不注入）；后续 Task 2/3 的 `FetchLock` 依赖它
- Consumes: `SkillContext.task_id: str`（`python/skills/context.py:23`，已存在）

- [ ] **Step 1: 写失败测试**

在 `python/tests/test_agent_skill_spec.py` 末尾追加（沿用该文件既有的 adapter 构造 helper 与 playwright/import 打桩方式；若文件内 helper 名不同，以实际为准替换 `_make_cli_adapter`）：

```python
class TestTaskIdInjection:
    """2.8 §3.3/§7.1：_run_script 注入 INKCUBE_TASK_ID（租约锁 owner）。"""

    @pytest.mark.asyncio
    async def test_task_id_injected_into_spawn_env(self, monkeypatch, tmp_path):
        captured: dict = {}

        class FakeProc:
            returncode = 0
            async def communicate(self):
                return (b'{"output": "ok"}', b'')

        async def fake_exec(*cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        adapter = _make_cli_adapter(tmp_path)  # 沿用本文件既有 CLI 模式 adapter 构造
        ctx = SkillContext(workspace_path=str(tmp_path), venv_python="python", task_id="task-abc")
        await adapter.execute({"command": "open https://example.com"}, ctx)
        assert captured["env"]["INKCUBE_TASK_ID"] == "task-abc"

    @pytest.mark.asyncio
    async def test_empty_task_id_not_injected(self, monkeypatch, tmp_path):
        captured: dict = {}

        class FakeProc:
            returncode = 0
            async def communicate(self):
                return (b'{"output": "ok"}', b'')

        async def fake_exec(*cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        adapter = _make_cli_adapter(tmp_path)
        ctx = SkillContext(workspace_path=str(tmp_path), venv_python="python", task_id="")
        await adapter.execute({"command": "open https://example.com"}, ctx)
        assert "INKCUBE_TASK_ID" not in captured["env"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_agent_skill_spec.py::TestTaskIdInjection -v`
Expected: FAIL（spawn env 里无 `INKCUBE_TASK_ID`）

- [ ] **Step 3: 实现**

`python/skills/loader.py` `_run_script` 中，将：

```python
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=context.workspace_path,
            )
```

改为：

```python
            # 2.8 §3.3：租约锁 owner 需要 task 身份——注入 INKCUBE_TASK_ID
            #（空值不注入；一处改动全 skill 受益）
            spawn_env = os.environ.copy()
            if context.task_id:
                spawn_env["INKCUBE_TASK_ID"] = context.task_id
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=context.workspace_path,
                env=spawn_env,
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_agent_skill_spec.py -v`
Expected: PASS（新增 2 用例 + 存量全绿）

---

### Task 2: `_fetch_core.py` — 租约锁升级 + 快照生成 + 待命页常量

**Files:**
- Modify: `resources/skills/browser-fetch/scripts/_fetch_core.py`（FetchLock 类重写 + 新增常量/snapshot_js）
- Test: `python/tests/test_browser_fetch_core.py`（追加测试类）

**Interfaces:**
- Produces:
  - `FetchLock(data_dir: str, task_id: str = "")`，`acquire() -> bool` / `release() -> None`（锁内容 `{"pid","ts","task_id"}`；同 task 重入接管 / 异 task 未超 60s 拒绝 / 超时接管 / release 校验 pid+task_id）
  - `MARK_PAGE: str`（close 归位目标，data: URL，title=inkcube-fetch-pool）
  - `snapshot_js() -> str`（可 evaluate 的箭头函数源码；结果 `{text, truncated, elements:[{role,name,selector}], remaining, total, hasPassword}`）
  - `SNAPSHOT_TEXT_LIMIT = 3000` / `SNAPSHOT_ELEMENTS_LIMIT = 80` / `SNAPSHOT_NAME_LIMIT = 40`
- Consumes: 既有 `POOL_MARK_JS`（保留，attach 匹配锚点）、`POOL_TITLE`、`LOCK_STALE_SECONDS`

- [ ] **Step 1: 写失败测试**

`python/tests/test_browser_fetch_core.py` 追加（import 缺 `time`/`os` 按文件头部补齐）：

```python
class TestFetchLockTaskId:
    """2.8 §3.3 V2：锁内容 {pid, ts, task_id} 三规则。"""

    def test_lock_payload_contains_task_id(self, tmp_path):
        lock = core.FetchLock(str(tmp_path), task_id="t1")
        assert lock.acquire()
        data = json.loads((tmp_path / "fetch.lock").read_text(encoding="utf-8"))
        assert data["task_id"] == "t1" and data["pid"] == os.getpid()
        lock.release()
        assert not (tmp_path / "fetch.lock").exists()

    def test_same_task_reentry_takes_over(self, tmp_path):
        first = core.FetchLock(str(tmp_path), task_id="t1")
        assert first.acquire()   # 模拟同 task 上个 CLI 进程异常退出未 release
        second = core.FetchLock(str(tmp_path), task_id="t1")
        assert second.acquire()  # 同 task 重入放行（接管）
        second.release()

    def test_other_task_rejected_within_stale_window(self, tmp_path):
        first = core.FetchLock(str(tmp_path), task_id="t1")
        assert first.acquire()
        second = core.FetchLock(str(tmp_path), task_id="t2")
        assert not second.acquire()
        data = json.loads((tmp_path / "fetch.lock").read_text(encoding="utf-8"))
        assert data["task_id"] == "t1"  # 原锁未被破坏

    def test_other_task_takes_over_after_stale(self, tmp_path):
        first = core.FetchLock(str(tmp_path), task_id="t1")
        assert first.acquire()
        stale_ts = time.time() - (core.LOCK_STALE_SECONDS + 5)
        os.utime(tmp_path / "fetch.lock", (stale_ts, stale_ts))
        second = core.FetchLock(str(tmp_path), task_id="t2")
        assert second.acquire()
        second.release()

    def test_release_verifies_task_id(self, tmp_path):
        lock = core.FetchLock(str(tmp_path), task_id="t1")
        assert lock.acquire()
        # 锁内容被接管者覆盖 → release 不得删除
        (tmp_path / "fetch.lock").write_text(
            json.dumps({"pid": os.getpid(), "ts": time.time(), "task_id": "t2"}),
            encoding="utf-8")
        lock.release()
        assert (tmp_path / "fetch.lock").exists()


class TestSnapshotJs:
    """快照 JS：纯字符串生成断言（真实 evaluate 由 Task 8 手动验收覆盖）。"""

    def test_snapshot_js_contains_limits(self):
        js = core.snapshot_js()
        assert "3000" in js and "80" in js and "40" in js

    def test_snapshot_js_is_arrow_function_source(self):
        assert core.snapshot_js().lstrip().startswith("() =>")

    def test_mark_page_is_data_url_with_pool_title(self):
        assert core.MARK_PAGE.startswith("data:text/html,")
        assert "inkcube-fetch-pool" in core.MARK_PAGE
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_browser_fetch_core.py::TestFetchLockTaskId python/tests/test_browser_fetch_core.py::TestSnapshotJs -v`
Expected: FAIL（`FetchLock.__init__` 不接受 `task_id`；`snapshot_js`/`MARK_PAGE` 不存在）

- [ ] **Step 3: 实现**

`_fetch_core.py` 顶部常量区（`LOCK_STALE_SECONDS` 附近）新增：

```python
SNAPSHOT_TEXT_LIMIT = 3000               # 快照正文截断（§5.2）
SNAPSHOT_ELEMENTS_LIMIT = 80             # 快照可交互元素上限（§5.2）
SNAPSHOT_NAME_LIMIT = 40                 # 元素 name 截断（§5.2）
# 待命页（close 归位目标 §5.2；前端 BrowserPanel 初始 src 用同值字符串）。
# data: URL 不产生网络请求，partition 拦截器放行（§5.3 第 2 层）
MARK_PAGE = (
    f"data:text/html,<title>{POOL_TITLE}</title>"
    # data: URL 中裸 # 会被解析为 fragment 截断，颜色用关键字不用十六进制
    '<body style="font-family:sans-serif;color:gray">InkCube 网页抓取待命</body>'
)
```

`FetchLock` 整类替换为：

```python
class FetchLock:
    """`<data_dir>/fetch.lock` 文件锁（V2 §3.3）：多任务浏览器串行化。

    - O_CREAT|O_EXCL 原子创建；内容 {"pid": …, "ts": …, "task_id": …}
    - 同 task 重入（上个 action 进程异常退出未 release）→ 接管放行
    - 异 task 且 mtime 未超 LOCK_STALE_SECONDS → 拒绝（浏览器正被其他任务使用）
    - 超 LOCK_STALE_SECONDS → 僵死接管
    - release 校验 pid + task_id 均为自己的才删除，避免误删接管者的锁
    """

    def __init__(self, data_dir: str, task_id: str = ""):
        self.path = os.path.join(data_dir, "fetch.lock")
        self.task_id = task_id
        self._mine = False

    def _payload(self) -> str:
        return json.dumps({"pid": os.getpid(), "ts": time.time(), "task_id": self.task_id})

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(self._payload())
                self._mine = True
                return True
            except FileExistsError:
                pass
            except OSError as e:
                logger.error("fetch lock create failed: %s", e)
                return False
            # 已存在：读持有者
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    holder = json.loads(f.read() or "{}")
            except (OSError, ValueError):
                holder = {}
            # 同 task 重入 → 接管（覆盖写保证单持有者语义）
            if self.task_id and holder.get("task_id") == self.task_id:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(self._payload())
                self._mine = True
                return True
            try:
                age = time.time() - os.path.getmtime(self.path)
            except OSError:
                continue  # 锁刚被释放，重试
            if age > LOCK_STALE_SECONDS:
                logger.warning(
                    "stale fetch lock (age %.0fs > %ds, holder=%s), taking over",
                    age, LOCK_STALE_SECONDS, holder.get("task_id", "?"),
                )
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(self._payload())
                self._mine = True
                return True
            return False

    def release(self) -> None:
        if not self._mine:
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                holder = json.loads(f.read() or "{}")
            if (holder.get("pid") == os.getpid()
                    and holder.get("task_id") == self.task_id):
                os.unlink(self.path)
        except (OSError, ValueError):
            pass
        self._mine = False
```

模块尾部（`sanitize_filename` 之后）新增：

```python
# ── 页面快照（§5.2：纯读取 evaluate，与 POOL_MARK_JS 同模式零副作用）──

def snapshot_js() -> str:
    """生成可 evaluate 的快照 JS 源码（() => {…} 形式）。

    结果：{text, truncated, elements: [{role, name, selector}], remaining,
    total, hasPassword}。selector 生成优先级 #id → [name=] → [data-testid=]
    → nth-of-type 路径（≤3 层）——selector 与可见文本跨调用稳定，从根上
    避免 ref 编号漂移（§1 元素定位决策）；视口内元素优先入列。
    """
    return """() => {
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, [role], [onclick]';
  const nodes = document.querySelectorAll(SEL);
  const inVp = [], offVp = [];
  const rectOf = (el) => el.getBoundingClientRect();
  const isVisible = (el) => { const r = rectOf(el); return r.width > 0 || r.height > 0; };
  const isInside = (el) => {
    const r = rectOf(el);
    return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
  };
  for (const el of nodes) { if (isVisible(el)) (isInside(el) ? inVp : offVp).push(el); }

  const selectorOf = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const nm = el.getAttribute('name');
    if (nm) return el.tagName.toLowerCase() + '[name="' + nm.replace(/"/g, '\\\\"') + '"]';
    const dt = el.getAttribute('data-testid');
    if (dt) return '[data-testid="' + dt.replace(/"/g, '\\\\"') + '"]';
    const parts = [];
    let cur = el;
    for (let i = 0; i < 3 && cur && cur !== document.body; i++) {
      const tag = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (!parent) break;
      const same = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
      parts.unshift(same.length > 1 ? tag + ':nth-of-type(' + (same.indexOf(cur) + 1) + ')' : tag);
      cur = parent;
    }
    return parts.join(' > ');
  };

  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      return 'textbox';
    }
    return tag;
  };

  const nameOf = (el) => {
    const label = el.getAttribute('aria-label')
      || (el.labels && el.labels[0] && el.labels[0].innerText)
      || el.innerText || el.value || el.getAttribute('placeholder')
      || el.getAttribute('title') || '';
    return label.trim().replace(/\\s+/g, ' ').slice(0, %(name_limit)d);
  };

  const elements = [];
  let remaining = 0;
  for (const el of [...inVp, ...offVp]) {
    const role = roleOf(el);
    const name = nameOf(el);
    if (!name && role !== 'textbox' && role !== 'select' && role !== 'textarea') continue;
    if (elements.length >= %(elem_limit)d) { remaining++; continue; }
    elements.push({ role, name, selector: selectorOf(el) });
  }

  const fullText = document.body ? (document.body.innerText || '') : '';
  return {
    text: fullText.slice(0, %(text_limit)d),
    truncated: fullText.length > %(text_limit)d,
    elements,
    remaining,
    total: nodes.length,
    hasPassword: !!document.querySelector('input[type=password]'),
  };
}""" % {
        "name_limit": SNAPSHOT_NAME_LIMIT,
        "elem_limit": SNAPSHOT_ELEMENTS_LIMIT,
        "text_limit": SNAPSHOT_TEXT_LIMIT,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_browser_fetch_core.py -v`
Expected: PASS（新增 8 用例 + 存量全绿；存量 FetchLock 旧用例若断言 `{pid, ts}` 结构，同步补 `task_id` 键断言）

---

### Task 3: `main.py` action 化重写（CLI 层）+ `_actions.py`（执行层）

**Files:**
- Create: `resources/skills/browser-fetch/scripts/_actions.py`（action 执行体；`_` 前缀不参与入口探测）
- Modify: `resources/skills/browser-fetch/scripts/main.py`（整体重写为 action CLI）
- Test: `python/tests/test_browser_fetch_cli.py`（整体重写）

**Interfaces:**
- Consumes: `_fetch_core` 的 `validate_url / probe_redirect_chain / FetchLock / detect_login_wall / html_to_markdown / extract_outline / normalize_domain / sanitize_filename / snapshot_js / MARK_PAGE / POOL_MARK_JS`；环境变量 `INKCUBE_CDP_PORT` / `INKCUBE_DATA_DIR` / `INKCUBE_TASK_ID`
- Produces: stdout 单行 JSON——操作型 action `{action, url, title, page:{text,truncated}, elements, needs_login:false}`；extract `{url,title,path,length,outline,needs_login:false}`；close `{action:"close", ok:true}`；错误体 `{"error", "usage"?, "example"?}` / 登录墙 `{"error", "needs_login":true, "domain"}`（spec §5.2）

- [ ] **Step 1: 写失败测试（CLI 契约矩阵）**

`python/tests/test_browser_fetch_cli.py` 整体重写：

```python
"""test_browser_fetch_cli.py — V2 action CLI 契约（2.8 §5.2）。

纯解析/预检层测试（不触网、不连 CDP）；CDP 交互由 Task 8 手动验收覆盖。
"""
import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "resources" / "skills" / "browser-fetch" / "scripts"
sys.path.insert(0, str(SCRIPTS))
main = importlib.import_module("main")


@pytest.fixture
def mock_exec(monkeypatch):
    """预检通过的用例打桩执行层（run_action），避免触网。"""
    seen: dict = {}

    async def fake_run(action, opts):
        seen.update({"action": action, "opts": opts})
        return {"action": action, "ok": True}

    monkeypatch.setattr(main, "run_action", fake_run)
    return seen


def run(argv):
    """跑 main.dispatch_argv，捕获 emit，返回 (exit_code, 最后一个 JSON 对象)。"""
    calls: list = []
    orig_emit = main.emit
    main.emit = lambda obj: calls.append(obj)
    try:
        code = main.dispatch_argv(argv)
    finally:
        main.emit = orig_emit
    return code, (calls[-1] if calls else {})


class TestV2Contract:
    def test_seven_actions_defined(self):
        assert set(main.ACTIONS) == {"open", "click", "type", "select", "scroll", "extract", "close"}

    def test_no_args_returns_v2_usage(self):
        _code, obj = run([])
        assert obj["error"] and "usage" in obj and "open" in obj["usage"]

    def test_old_single_command_syntax_teaches_v2(self):
        _code, obj = run(["https://example.com"])
        assert "action" in obj["error"] or "V2" in obj["error"]
        assert "example" in obj

    def test_unknown_action(self):
        _code, obj = run(["navigate", "https://example.com"])
        assert "usage" in obj

    def test_open_missing_url(self):
        _code, obj = run(["open"])
        assert "URL" in obj["error"]

    def test_open_bad_scheme_precheck(self):
        # 形似性预检拒绝非 http(s)（SSRF 全量校验仍在执行层）
        _code, obj = run(["open", "ftp://example.com"])
        assert obj["error"]

    def test_click_requires_text_or_selector(self):
        _code, obj = run(["click"])
        assert "--text" in obj["usage"] and "error" in obj

    def test_type_requires_selector_and_text_source(self):
        _code, obj = run(["type", "--selector", "#q"])
        assert obj["error"]            # 缺文本来源
        _code, obj = run(["type", "--text", "hi"])
        assert obj["error"]            # 缺 --selector

    def test_type_text_and_text_file_mutually_exclusive(self):
        _code, obj = run(["type", "--selector", "#q", "--text", "a", "--text-file", "b.txt"])
        assert obj["error"]

    def test_select_requires_value(self):
        _code, obj = run(["select", "--selector", "#s"])
        assert obj["error"]

    def test_scroll_direction_validated(self, mock_exec):
        _code, obj = run(["scroll", "--direction", "left"])
        assert obj["error"]
        _code, obj = run(["scroll"])
        assert obj.get("error") is None and mock_exec["action"] == "scroll"
        assert mock_exec["opts"]["direction"] == "down"   # 默认值

    def test_extract_optional_selector(self, mock_exec):
        _code, obj = run(["extract", "--save-to", "out.md"])
        assert obj.get("error") is None and mock_exec["action"] == "extract"

    def test_close_no_args(self, mock_exec):
        _code, obj = run(["close"])
        assert obj.get("error") is None and mock_exec["action"] == "close"

    def test_open_unrecognized_flag_rejected(self):
        # open 不支持 --selector（V1 残留参数显式拒绝，教 V2 契约）
        _code, obj = run(["open", "https://example.com", "--selector", "#a"])
        assert obj["error"]

    def test_open_opts_normalized(self, mock_exec):
        _code, obj = run(["open", "https://example.com", "--wait-for", ".a", "--timeout", "30"])
        assert mock_exec["opts"] == {"url": "https://example.com", "wait_for": ".a", "timeout": "30"}

    def test_type_submit_flag(self, mock_exec):
        _code, obj = run(["type", "--selector", "#q", "--text", "x", "--submit"])
        assert mock_exec["opts"]["submit"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_browser_fetch_cli.py -v`
Expected: FAIL（`main.ACTIONS` / `main.dispatch_argv` / `main.run_action` 不存在）

- [ ] **Step 3: 实现 main.py（CLI 层）**

`main.py` 整体重写：

```python
#!/usr/bin/env python3
"""browser-fetch CLI 入口（2.8 §5.2 V2：LLM 驱动 action 循环）。

用法: python main.py <action> [参数…]
action ∈ open/click/type/select/scroll/extract/close——每步返回页面快照
（正文截断 + 可交互元素清单），LLM 看快照决定下一步。
stdout 只输出一行 JSON（唯一通信通道，adapter 解析）。
"""
from __future__ import annotations

import json
import sys

import _fetch_core as core  # noqa: F401  （模块引用保持 import 面可测）
import _actions


def emit(obj: dict) -> None:
    """唯一出口：stdout 一行 JSON。"""
    print(json.dumps(obj, ensure_ascii=False))


# playwright 导入失败 = venv 预装后台线程尚未完成（§5.5 竞态）→ retryable。
# _actions 顶部捕获 ImportError 置 async_playwright=None（不 print），
# 此处检查属性统一出 retryable JSON（stdout 单通道不变）
if _actions.async_playwright is None:  # pragma: no cover - 需真实缺包环境
    emit({"error": "browser-fetch 依赖预装中，请稍后重试", "retryable": True})
    sys.exit(0)

run_action = _actions.run_action

# ── action 契约（§5.2 定版）──
# value: url 位置参数；required: 必填 --flag；optional: 可选 --flag→opts 键；
# oneof: 二选一必填；flags: 布尔开关
ACTIONS: dict[str, dict] = {
    "open":    {"url_required": True,
                "optional": {"--wait-for": "wait_for", "--timeout": "timeout"}},
    "click":   {"oneof": [("--text", "text"), ("--selector", "selector")],
                "optional": {"--timeout": "timeout"}},
    "type":    {"required": ["--selector"],
                "oneof": [("--text", "text"), ("--text-file", "text_file")],
                "flags": ["--submit"]},
    "select":  {"required": ["--selector", "--value"]},
    "scroll":  {"optional": {"--direction": "direction", "--amount": "amount"}},
    "extract": {"optional": {"--selector": "selector", "--wait-for": "wait_for",
                             "--save-to": "save_to"}},
    "close":   {},
}

_V2_CONTRACT = (
    "browser-fetch V2 为 action 模式: open <url> [--wait-for CSS] [--timeout 秒] | "
    "click --text 文本|--selector CSS [--timeout 秒] | "
    "type --selector CSS (--text 文本|--text-file 相对路径) [--submit] | "
    "select --selector CSS --value 值 | scroll [--direction up|down] [--amount 像素] | "
    "extract [--selector CSS] [--wait-for CSS] [--save-to 名.md] | close"
)

_EXAMPLES = {
    "open": "open https://example.com --wait-for .article",
    "click": 'click --text "登录"',
    "type": 'type --selector input[name=q] --text "inkcube" --submit',
    "select": 'select --selector select#size --value "M"',
    "scroll": "scroll --direction down --amount 800",
    "extract": "extract --selector article --save-to result.md",
    "close": "close",
}


def _norm(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def _usage_error(action: str | None, message: str) -> dict:
    obj: dict = {"error": message, "usage": _V2_CONTRACT}
    if action in _EXAMPLES:
        obj["example"] = _EXAMPLES[action]
    return obj


def parse_action(argv: list[str]) -> tuple[str | None, dict, dict | None]:
    """解析 + 预检。仅形似性检查；SSRF 安全校验由执行层全量负责（§5.2）。"""
    if not argv:
        return None, {}, _usage_error(
            None, "缺少 action 参数（open/click/type/select/scroll/extract/close）")
    action, rest = argv[0], argv[1:]
    if action.lower().startswith(("http://", "https://")):
        return None, {}, _usage_error("open", (
            f"browser-fetch V2 已改为 action 模式，不再接受 URL 直抓: {action}。"
            "先 open 拿到页面快照，再按快照决定后续 action"))
    if action not in ACTIONS:
        return None, {}, _usage_error(action,
            f"未知 action: {action}（合法: open/click/type/select/scroll/extract/close）")
    spec = ACTIONS[action]
    opts: dict = {}
    oneof_keys = {_norm(k) for k, _v in spec.get("oneof", [])}
    i = 0
    while i < len(rest):
        tok = rest[i]
        if not tok.startswith("--"):
            # open 允许一个位置参数：URL（主形态 open <url> [flags…]）
            if action == "open" and not opts.get("url"):
                opts["url"] = tok
                i += 1
                continue
            return action, opts, _usage_error(action, f"无法识别的位置参数: {tok}（本工具只接受 --flag 参数）")
        if tok in spec.get("flags", []):
            opts[_norm(tok)] = True
            i += 1
            continue
        if i + 1 >= len(rest):
            return action, opts, _usage_error(action, f"参数 {tok} 缺少值")
        known = (tok in spec.get("optional", {})
                 or tok in spec.get("required", [])
                 or any(tok == k for k, _v in spec.get("oneof", [])))
        if not known:
            return action, opts, _usage_error(action, f"无法识别的参数: {tok}")
        if _norm(tok) in opts:
            return action, opts, _usage_error(action, f"参数 {tok} 重复")
        opts[_norm(tok)] = rest[i + 1]
        i += 2
    # 必填校验
    if spec.get("url_required") and not opts.get("url"):
        return action, opts, _usage_error(action, "缺少 URL 参数（open <url>）")
    if opts.get("url") and not opts["url"].lower().startswith(("http://", "https://")):
        return action, opts, _usage_error(action, f"URL 必须 http(s):// 开头，收到: {opts['url']}")
    for req in spec.get("required", []):
        if not opts.get(_norm(req)):
            return action, opts, _usage_error(action, f"缺少必需参数 {req}")
    oneof = spec.get("oneof", [])
    if oneof:
        provided = [k for k, _v in oneof if _norm(k) in opts]
        if not provided:
            names = " 或 ".join(k for k, _v in oneof)
            return action, opts, _usage_error(action, f"必须提供 {names} 之一")
        if len(provided) > 1:
            names = " 与 ".join(k for k, _v in oneof)
            return action, opts, _usage_error(action, f"{names} 只能二选一")
    # 值域校验
    if action == "scroll":
        d = opts.get("direction", "down")
        if d not in ("up", "down"):
            return action, opts, _usage_error(action, f"--direction 只支持 up|down，收到: {d}")
        opts.setdefault("direction", d)  # 默认值写入契约层，LLM 可见
        if "amount" in opts and not opts["amount"].isdigit():
            return action, opts, _usage_error(action, f"--amount 必须为正整数，收到: {opts['amount']}")
    for int_flag in ("timeout",):
        if int_flag in opts and not opts[int_flag].isdigit():
            return action, opts, _usage_error(action, f"--{int_flag} 必须为正整数，收到: {opts[int_flag]}")
    return action, opts, None


def dispatch_argv(argv: list[str]) -> int:
    """CLI 主入口（可测）：解析 → 预检 → 执行 → emit → 返回 exit code。"""
    action, opts, err = parse_action(argv)
    if err:
        emit(err)
        return 0  # 契约错误也走 stdout JSON（exit 0，adapter 以 JSON 内容为准）
    emit(dispatch_action(action, opts))
    return 0


def dispatch_action(action: str, opts: dict) -> dict:
    """同步包装执行层：任何异常都以 JSON 形式回到 LLM（顶层防线）。"""
    import asyncio
    try:
        return asyncio.run(run_action(action, opts))
    except KeyboardInterrupt:  # pragma: no cover
        return {"error": "操作被中断"}
    except Exception as e:
        return {"error": f"browser-fetch 执行失败: {e}"}


def main() -> None:
    sys.exit(dispatch_argv(sys.argv[1:]))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 实现 _actions.py（执行层）**

新建 `resources/skills/browser-fetch/scripts/_actions.py`：

```python
"""browser-fetch action 执行层（2.8 §5.2）。

CLI 每步 attach 常驻面板 webview 的 target（页面状态常驻 webview，
无进程内会话管理器，§2）；执行完 disconnect，锁随 CLI 进程生命周期。
本模块不 print——stdout 是 main.py 的唯一出口。
"""
from __future__ import annotations

import json
import os
import time

import _fetch_core as core

# 导入防线：缺 playwright 时置 None（main.py 据此出 retryable JSON），不抛出
try:
    from playwright.async_api import async_playwright, Error as PlaywrightError
except ImportError:  # pragma: no cover - 需真实缺包环境
    async_playwright = None
    PlaywrightError = Exception

DEFAULT_TIMEOUT_S = 20
SETTLE_LOAD_MS = 3000    # click 后 load 事件容错（§5.2）
SETTLE_QUIET_MS = 500    # settle 静默窗
EXTRACT_TEXT_HINT = 200  # extract 摘要的正文预览长度


def _timeout_ms(opts: dict) -> int:
    return int(opts.get("timeout", DEFAULT_TIMEOUT_S)) * 1000


async def _find_pool_page(browser):
    """按 preload 运行时标记匹配面板 webview target（锚点语义与 V1 一致）。"""
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                if await page.evaluate(core.POOL_MARK_JS):
                    return page
            except Exception:
                continue
    return None


async def _settle(page) -> None:
    try:
        await page.wait_for_load_state("load", timeout=SETTLE_LOAD_MS)
    except PlaywrightError:
        pass  # SPA 可能不触发新 load；静默窗后照常快照
    await page.wait_for_timeout(SETTLE_QUIET_MS)


async def _snapshot(page, action: str, orig_url: str, status: int | None) -> dict:
    """快照 + 登录墙检测（§4.1 步 4）。命中登录墙返回错误体。"""
    snap = await page.evaluate(core.snapshot_js())
    title = await page.title()
    url = page.url
    domain = core.normalize_domain(url)
    if core.detect_login_wall(status, url, orig_url or url, title,
                              snap["text"], snap["hasPassword"]):
        return {"error": f"该站点需要登录: {domain}",
                "needs_login": True, "domain": domain}
    return {
        "action": action,
        "url": url,
        "title": title,
        "page": {"text": snap["text"], "truncated": snap["truncated"]},
        "elements": snap["elements"],
        "needs_login": False,
    }


def _locate(page, opts: dict):
    """click 目标定位：--selector 精确 / --text 可见文本（精确→部分匹配降级）。"""
    if opts.get("selector"):
        return page.locator(opts["selector"]).first
    text = opts["text"]
    exact = page.get_by_text(text, exact=True).first
    return exact if exact.count() else page.get_by_text(text).first


async def _read_type_text(opts: dict) -> str:
    """type 文本双通道（§5.2）：--text 直传 / --text-file 读工作区相对路径文件。"""
    if "text" in opts:
        return opts["text"]
    path = os.path.join(os.getcwd(), opts["text_file"])
    if not os.path.isfile(path):
        raise FileNotFoundError(f"--text-file 不存在: {opts['text_file']}（相对任务工作区）")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


async def run_action(action: str, opts: dict) -> dict:
    # 1. SSRF 第 1 层仅把关 open 的初始 URL（§5.3；交互中导航由 Electron 拦截层罩住）
    orig_url = opts.get("url", "")
    final_url = orig_url
    status = None
    if action == "open":
        err = core.validate_url(orig_url)
        if err:
            return {"error": err}
        final_url, probe_err = core.probe_redirect_chain(orig_url)
        if probe_err:
            return {"error": probe_err}

    # 2. 租约锁（§3.3：全 action 一致，含 close；单实例场景 data_dir 缺失时跳过）
    lock = None
    data_dir = os.environ.get("INKCUBE_DATA_DIR", "")
    if data_dir:
        lock = core.FetchLock(data_dir, os.environ.get("INKCUBE_TASK_ID", ""))
        if not lock.acquire():
            return {"error": "浏览器正被其他任务使用，请稍后重试", "retryable": True}

    try:
        return await _run_via_cdp(action, opts, final_url)
    finally:
        if lock:
            lock.release()


async def _run_via_cdp(action: str, opts: dict, final_url: str) -> dict:
    cdp_port = os.environ.get("INKCUBE_CDP_PORT", "")
    if not cdp_port:
        return {"error": "INKCUBE_CDP_PORT 未设置，浏览器操作不可用（需重启应用）"}
    if async_playwright is None:  # pragma: no cover - main.py 已拦截
        return {"error": "browser-fetch 依赖预装中，请稍后重试", "retryable": True}

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except PlaywrightError as e:
            return {"error": f"连接内置浏览器失败（CDP:{cdp_port}）: {e}"}
        try:
            page = await _find_pool_page(browser)
            if page is None:
                return {"error": "未找到浏览器面板（webview 未就绪），请重启应用"}
            return await _execute(page, action, opts, final_url)
        finally:
            # close 只断开连接，不关闭浏览器（页面常驻语义，与 V1 一致）
            try:
                await browser.close()
            except Exception:
                pass


async def _execute(page, action: str, opts: dict, final_url: str) -> dict:
    """按 action 分支执行 → settle → 快照（extract/close 例外，见各分支）。"""
    try:
        if action == "open":
            resp = await page.goto(final_url, wait_until="load", timeout=_timeout_ms(opts))
            status = resp.status if resp is not None else None
            if opts.get("wait_for"):
                try:
                    await page.wait_for_selector(opts["wait_for"], timeout=_timeout_ms(opts))
                except PlaywrightError:
                    return {"error": f"等待选择器超时: {opts['wait_for']}（页面未渲染出该元素）"}
            return await _snapshot(page, action, opts.get("url", ""), status)

        if action == "click":
            locator = _locate(page, opts)
            try:
                await locator.click(timeout=_timeout_ms(opts))
            except PlaywrightError as e:
                hint = "快照 elements 里的 selector/文本可直接使用"
                return {"error": f"点击失败: {e}", "hint": hint}
            await _settle(page)
            return await _snapshot(page, action, "", None)

        if action == "type":
            text = await _read_type_text(opts)
            locator = page.locator(opts["selector"]).first
            try:
                await locator.fill(text, timeout=_timeout_ms(opts))
                if opts.get("submit"):
                    await locator.press("Enter")
            except PlaywrightError as e:
                return {"error": f"输入失败（检查 --selector 是否命中可输入元素）: {e}"}
            await _settle(page)
            return await _snapshot(page, action, "", None)

        if action == "select":
            locator = page.locator(opts["selector"]).first
            try:
                await locator.select_option(opts["value"], timeout=_timeout_ms(opts))
            except PlaywrightError as e:
                return {"error": f"下拉选择失败（值不在选项中或选择器未命中）: {e}"}
            await _settle(page)
            return await _snapshot(page, action, "", None)

        if action == "scroll":
            direction = opts.get("direction", "down")
            amount = int(opts.get("amount", 600))
            sign = -1 if direction == "up" else 1
            await page.evaluate(f"window.scrollBy(0, {sign * amount})")
            await _settle(page)
            return await _snapshot(page, action, "", None)

        if action == "extract":
            # V1 单命令抓取语义由本 action 完整承接（§5.2）
            selector = opts.get("selector")
            if opts.get("wait_for"):
                try:
                    await page.wait_for_selector(opts["wait_for"], timeout=_timeout_ms(opts))
                except PlaywrightError:
                    return {"error": f"等待选择器超时: {opts['wait_for']}"}
            if selector and await page.query_selector(selector) is None:
                return {"error": f"选择器未命中任何元素: {selector}",
                        "hint": "可去掉 --selector 提取全文，或用快照 elements 里的 selector"}
            js_extract = """
            (sel) => {
              const root = sel ? document.querySelector(sel) : document.body;
              return { html: root ? root.outerHTML : '',
                       text: root ? root.innerText : '',
                       hasPassword: !!document.querySelector('input[type=password]') };
            }"""
            extracted = await page.evaluate(js_extract, selector)
            resp_status = None
            title = await page.title()
            url = page.url
            domain = core.normalize_domain(url)
            if core.detect_login_wall(resp_status, url, url, title,
                                      extracted["text"], extracted["hasPassword"]):
                return {"error": f"该站点需要登录: {domain}",
                        "needs_login": True, "domain": domain}
            markdown = core.html_to_markdown(extracted["html"])
            fetched_dir = os.path.join(os.getcwd(), "fetched")
            os.makedirs(fetched_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            filename = opts.get("save_to") or f"{domain}-{ts}.md"
            filename = core.sanitize_filename(filename)
            if not filename.endswith(".md"):
                filename += ".md"
            path = os.path.join(fetched_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n> 来源: {url}\n\n{markdown}")
            return {
                "url": url,
                "title": title,
                "path": os.path.relpath(path, os.getcwd()).replace("\\", "/"),
                "length": len(markdown),
                "outline": core.extract_outline(markdown),
                "preview": extracted["text"][:EXTRACT_TEXT_HINT],
                "needs_login": False,
            }

        if action == "close":
            # 归位待命页（data: URL，拦截器放行）；状态复位、锁随 finally 释放
            try:
                await page.goto(core.MARK_PAGE, wait_until="load")
            except PlaywrightError:
                pass  # data: 页加载异常不阻塞归位语义
            return {"action": "close", "ok": True}

        return {"error": f"未实现的 action: {action}"}  # pragma: no cover
    except PlaywrightError as e:
        return {"error": f"页面操作失败: {e}"}
```

注意（实施时核对）：`_locate` 的 `exact.count()` 是同步方法调用（playwright Locator.count 返回协程）——正确写法：

```python
def _locate(page, opts: dict):
    if opts.get("selector"):
        return page.locator(opts["selector"]).first
    text = opts["text"]
    return page.get_by_text(text, exact=True).first
```

并在 click 分支捕获「定位不到」的 PlaywrightError 后追加一次部分匹配降级：

```python
            try:
                await locator.click(timeout=_timeout_ms(opts))
            except PlaywrightError:
                if opts.get("text"):
                    try:
                        await page.get_by_text(opts["text"]).first.click(timeout=_timeout_ms(opts))
                        await _settle(page)
                        return await _snapshot(page, action, "", None)
                    except PlaywrightError as e2:
                        return {"error": f"点击失败（文本未找到可点元素）: {e2}",
                                "hint": "快照 elements 里的 selector/文本可直接使用"}
                return {"error": f"点击失败: {e}", "hint": "快照 elements 里的 selector 可直接使用"}
```

- [ ] **Step 5: 跑测试确认通过 + 语法检查 + 全量回归**

Run:
```bash
C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m py_compile resources/skills/browser-fetch/scripts/main.py resources/skills/browser-fetch/scripts/_actions.py
C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/test_browser_fetch_cli.py python/tests/test_browser_fetch_core.py -v
C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/ -q
```
Expected: py_compile 无输出（成功）；CLI 契约 16 用例 PASS；全量回归无新增失败

---

### Task 4: SKILL.md 重写（V2 协议 + 反编造约束平移）

**Files:**
- Modify: `resources/skills/browser-fetch/SKILL.md`（整体重写）

**Interfaces:**
- Produces: LLM 首次调用时经 `_attach_guide` 注入的 V2 使用指南（渐进披露 Level 2）；description 进 system prompt（Level 1）
- Consumes: Task 3 定型的七 action 语法

- [ ] **Step 1: 重写 SKILL.md**

整体替换为：

```markdown
---
name: browser-fetch
description: 用内置浏览器进行 LLM 驱动的多步网页操作（action 循环：导航/点击/填表/下拉/滚动/提取），支持 JavaScript 渲染页面与登录态复用。实时页面内容（定价、文档、公告、搜索、后台系统等）必须用本工具获取，禁止凭训练记忆回答；每步返回页面快照（正文截断 + 可交互元素清单），看快照决定下一步。提取正文落盘工作区返回摘要；需要登录的站点会提示用户在浏览器面板完成登录。
---

# browser-fetch — 网页抓取与浏览器自动化（V2 action 模式）

通过 CDP 接管应用内置 Chromium（嵌入面板 webview，用户全程可见、可随时人工接管）。**每步一个 action，返回页面快照，看快照决定下一步**——循环往复直至任务完成。

## action 一览

| action | 语法 | 说明 |
|---|---|---|
| open | `open <url> [--wait-for CSS] [--timeout 秒]` | 导航+等待+快照（一切交互的起点） |
| click | `click --text 文本 \|--selector CSS` | 点击可见文本或元素，点击后返回新快照 |
| type | `type --selector CSS (--text 文本\|--text-file 相对路径) [--submit]` | 填表；`--submit` 填后回车；长文本用 --text-file（先 file_ops 写文件） |
| select | `select --selector CSS --value 值` | 下拉选择（原生 select） |
| scroll | `scroll [--direction up\|down] [--amount 像素]` | 滚动触发懒加载，快照跟随 |
| extract | `extract [--selector CSS] [--wait-for CSS] [--save-to 名.md]` | 区域/全文提取，落盘+摘要（--wait-for 兼做显式等待） |
| close | `close` | 浏览器归位待命页（一轮操作结束后调用，礼貌释放） |

## 快照返回体

操作型 action 返回 `{action, url, title, page:{text(≤3000字符), truncated}, elements:[≤80条 {role, name, selector}], needs_login}`。`elements` 的 `selector` 与 `name`（可见文本）跨调用稳定——**直接用它们定位，不要猜选择器**；`remaining>0` 表示还有更多元素，先 scroll 再看。

## needs_login

返回 `{error, needs_login: true, domain}` 时：**告知用户**在对话卡片点「立即登录」或到 设置 → 网页登录态 完成登录，用户回复后再继续发 action（登录态自动生效）。不要自己重试。

## 使用建议

1. **先 open 再交互**：快照是唯一事实来源，未 open 的页面上不要假设任何元素存在
2. click/type 用快照里的 `--text 可见文本` 最稳；`--selector` 用于文本重复或不可见元素（输入框、下拉）
3. SPA 页面 open 时加 `--wait-for` 等内容渲染；搜索框用 `type … --submit` 省一轮 click
4. 提取正文一律 `extract`（落盘 `fetched/`，stdout 只有摘要）；更多内容用 file_ops 读落盘文件
5. 内网地址（localhost/192.168.x 等）一律拒绝，这是安全设计，不要重试内网 URL
6. 任务结束时调一次 `close` 归位
7. 上下文中的快照/落盘结果是唯一事实来源；用户追问细节而现有数据不含答案时，重新操作获取（scroll/extract），**禁止编造数字、档位名或页面结构**
```

- [ ] **Step 2: 验证 frontmatter 可被 parser 正常解析 + 全量回归**

Run:
```bash
C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/ -q
```
Expected: 全量通过（skills parser 相关用例覆盖 frontmatter 解析；若 `test_agent_skill_spec.py` 有对 browser-fetch description 文案的断言，同步更新）

> **种子同步说明**：`skills/seed.py` 启动时 `copytree(dirs_exist_ok=True)` 自动覆盖 data 副本（§4.2），SKILL.md 与脚本改动无需额外同步代码；开发期验证可重启 Python 后端或删 `<data_dir>/skills/browser-fetch` 后重启。

---

### Task 5: Electron 清理（池/登录窗口退役 + webviewTag + weblogin:open 删除）

> **执行顺序约束**：本任务与 Task 6/7 强配对（spec §12 S6+S7）——本任务完成后前端 `weblogin:open` 调用方进入临时破损窗口期，Task 7 立即接上；三任务须在同一验证周期内完成，中途不交付。

**Files:**
- Modify: `electron/fetch-pool.js`（删池窗口/登录窗口/shutdown；保留 SSRF 拦截 + cookie 清理 + 新增 `PRELOAD_PATH` 导出）
- Modify: `electron/main.js`（`webviewTag: true`；删 `ensurePoolWindow`/`shutdown` 调用；注释更新）
- Modify: `electron/ipc-handlers.js`（删 `weblogin:open` handler；新增 `webfetch:getPreloadPath`）

**Interfaces:**
- Produces: `fetchPool.PRELOAD_PATH: string`（webview preload 绝对路径）+ IPC `webfetch:getPreloadPath`（Task 6 BrowserPanel 消费）
- Deletes: `ensurePoolWindow` / `openLoginWindow` / `shutdown` / IPC `weblogin:open`

- [ ] **Step 1: fetch-pool.js 瘦身**

文件整体替换为（保留的三段函数体原样拷贝自现文件，不改逻辑）：

```js
// ── webfetch partition 共享设施（2.8 §5.3/§6.3；V2：池/登录窗口退役，
// 浏览器承载改为主窗口 BrowserPanel webview）——本模块仅存 partition 级
// SSRF 字面拦截（应用启动即挂，与 webview 生命周期无关）、cookie 清理、
// webview preload 路径。主窗口 default session 无涉（§10 隔离边界）。
const { session } = require('electron')
const path = require('path')
const { logElectron } = require('./log-utils')

const PARTITION = 'persist:webfetch'
// webview preload 绝对路径（渲染进程无法解析 electron/ 目录，经 IPC 下发；
// 标记语义见 fetch-pool-preload.js）
const PRELOAD_PATH = path.join(__dirname, 'fetch-pool-preload.js')

// ── SSRF 字面拦截（§5.3 第 2 层；覆盖面板 webview 内一切导航/重定向/fetch）──

/** IPv4 字面量命中内网段：0/8 · 10/8 · 127/8 · 172.16/12 · 192.168/16 · 169.254/16 */
function _isPrivateV4Literal(host) {
  /* …原样保留现文件 L27-40 函数体… */
}

function _isPrivateLiteral(hostname) {
  /* …原样保留现文件 L42-52 函数体… */
}

/** partition 级 onBeforeRequest（§5.3 第 2 层）。V2：LLM click 引发的跳转
 *  导航同在此罩内——第 1 层仅把关 open 的初始 URL。data:/about: 放行
 *  （待命页本身即 data: URL）。 */
function installSsrfInterceptor() {
  /* …原样保留现文件 L58-77 函数体… */
}

// ── 登录态清理（§6.3）：与 DELETE /web-logins/{domain} 配对 ──

async function removeCookiesForDomain(domain) {
  /* …原样保留现文件 L192-215 函数体… */
}

module.exports = { installSsrfInterceptor, removeCookiesForDomain, PRELOAD_PATH }
```

> 「原样保留」= 从现文件逐字拷贝对应行（含注释），四个函数一行不改；删除项：`BrowserWindow`/`POOL_TITLE`/`FETCHING_TITLE`/`MARK_PAGE` import 与常量、`poolWindow`/`loginWindow` 模块变量、`ensurePoolWindow`（L79-152）、`openLoginWindow`（L154-188）、`shutdown`（L217-225）。`MARK_PAGE` 常量已平移至 `_fetch_core.py`（Task 2），前端待命页由 BrowserPanel 的 `STANDBY_URL` 承担。

- [ ] **Step 2: main.js 修改**

三处（行号按现文件）：

① 主窗口 webPreferences 开 webviewTag（L73-77）：

```js
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // 2.8 §6.2 V2：BrowserPanel 内嵌浏览器面板。<webview> 无
      // nodeIntegration、preload 仅设标记，开启本身不授予页面任何
      // Node 能力（§10 webviewTag 攻击面评估）
      webviewTag: true,
    },
```

② whenReady 内（L187-189）——删池窗口预建，保留拦截器：

```js
  // Fetch-pool: partition-level SSRF interceptor（2.8 §5.3；V2 池窗口退役，
  // 浏览器承载为主窗口 BrowserPanel webview，启动即挂拦截器）
  fetchPool.installSsrfInterceptor()
```

③ before-quit（L206-214）——删 shutdown 调用（池窗口已不存在，window-all-closed 语义回归自然）：

```js
app.on('before-quit', (event) => {
  if (pythonManager.isRunning()) {
    event.preventDefault()
    pythonManager.stop().finally(() => app.quit())
  }
})
```

同步更新两条注释（不改逻辑）：
- L103-104 `closed` 注释 → `// Quit on main-window close（pool 窗口已退役，window-all-closed 本可触发；保留显式 quit 语义不变）`
- L222-225 `activate` 注释 → 删「fetch-pool window always alive」句，改为 `// Recreate the main window on macOS activate`（主窗口计数逻辑保留——多窗口时代仍正确）

- [ ] **Step 3: ipc-handlers.js 修改**

① 删除 `weblogin:open` handler（L1878-1882，连同注释）：

```js
  // 【已删除】weblogin:open（2.8 §7.1 V2）：登录载体统一为 BrowserPanel，
  // 调用方改走 src/features/browser/browserPanelStore.navigate()
```

② 新增 webview preload 路径通道（放在 `weblogin:remove-cookies` 之前）：

```js
  // BrowserPanel webview 的 preload 绝对路径（2.8 §6.2；渲染进程无法解析
  // electron/ 目录，启动后取一次）
  ipcMain.handle('webfetch:getPreloadPath', () => fetchPool.PRELOAD_PATH)
```

- [ ] **Step 4: 验证**

Run:
```bash
node --check electron/fetch-pool.js; node --check electron/main.js; node --check electron/ipc-handlers.js
```
```bash
$hits = Select-String -Path electron/*.js -Pattern 'ensurePoolWindow|openLoginWindow|weblogin:open|fetchPool.shutdown'; if ($hits) { $hits | Out-String } else { 'CLEAN' }
```
Expected: node --check 全部无输出；grep 输出 `CLEAN`（残留引用归零——TaskMessageList/WebLogins 的调用方在 Task 7 清理，此处 electron/ 目录内必须归零）

---

### Task 6: 前端面板（browserPanelStore + BrowserPanel + webview 类型 + App 挂载）

**Files:**
- Create: `src/features/browser/browserPanelStore.ts`
- Create: `src/features/browser/BrowserPanel.tsx`
- Create: `src/features/browser/BrowserPanel.module.css`
- Modify: `src/vite-env.d.ts`（webview JSX 声明）
- Modify: `src/App.tsx`（App 级挂载）
- Test: `tests/BrowserPanel.test.tsx`（新增）

**Interfaces:**
- Consumes: IPC `webfetch:getPreloadPath`（Task 5）；`useTranslation`
- Produces:
  - `browserPanelStore`: `{ subscribe, getState, showCompact(), expand(), collapse(), hide(), navigate(url) }`；`useBrowserPanel(): { visible, expanded, currentUrl }`（Task 7 消费）
  - `<BrowserPanel />`：三态 hidden（移出视口，保布局）/ compact（右上角 fixed ~360×480）/ expanded（近全屏 overlay）

- [ ] **Step 1: 写失败测试**

`tests/BrowserPanel.test.tsx`：

```tsx
/** tests/BrowserPanel.test.tsx — 内嵌浏览器面板（2.8 §8）
 *  覆盖：三态切换 / 关闭保状态（webview 不卸载）/ crash 自愈 /
 *  navigate 导航 / webview partition·preload 属性。
 *  jsdom 无真实 webview——渲染为自定义元素，断言属性与事件回调。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))

const invokeMock = vi.fn()

import { BrowserPanel } from '../src/features/browser/BrowserPanel'
import { browserPanelStore } from '../src/features/browser/browserPanelStore'

beforeEach(() => {
  invokeMock.mockReset()
  invokeMock.mockResolvedValue('C:/app/fetch-pool-preload.js')
  ;(window as unknown as { electronAPI: unknown }).electronAPI = { invoke: invokeMock }
  browserPanelStore.hide()
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { electronAPI?: unknown }).electronAPI
})

const getWebview = () => document.querySelector('webview') as HTMLElement

describe('BrowserPanel (2.8 §8)', () => {
  it('hidden by default: wrapper rendered (webview alive) but moved off-viewport', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    const wrapper = document.querySelector('[data-panel]') as HTMLElement
    expect(wrapper.getAttribute('data-visible')).toBe('false')
    expect(getWebview()).toBeTruthy()  // 关闭 ≠ 销毁（§8.3）
  })

  it('showCompact → visible small card; expand/collapse toggle; hide keeps webview', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    act(() => browserPanelStore.showCompact())
    expect((document.querySelector('[data-panel]') as HTMLElement).getAttribute('data-visible')).toBe('true')
    expect((document.querySelector('[data-panel]') as HTMLElement).getAttribute('data-expanded')).toBe('false')
    fireEvent.click(screen.getByTitle('browserPanel.expand'))
    expect((document.querySelector('[data-panel]') as HTMLElement).getAttribute('data-expanded')).toBe('true')
    fireEvent.click(screen.getByTitle('browserPanel.collapse'))
    expect((document.querySelector('[data-panel]') as HTMLElement).getAttribute('data-expanded')).toBe('false')
    fireEvent.click(screen.getByTitle('browserPanel.hide'))
    expect(getWebview()).toBeTruthy()  // 状态保留
  })

  it('webview carries partition + preload from IPC + standby src', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    const wv = getWebview()
    expect(wv.getAttribute('partition')).toBe('persist:webfetch')
    await vi.waitFor(() => expect(wv.getAttribute('preload')).toBe('C:/app/fetch-pool-preload.js'))
    expect(wv.getAttribute('src')).toContain('inkcube-fetch-pool')
  })

  it('navigate(url) drives webview loadURL and shows panel', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    let loadURL: ((u: string) => void) | null = null
    Object.defineProperty(getWebview(), 'loadURL', {
      value: (u: string) => { loadURL = (_: string) => {} },
      configurable: true,
    })
    act(() => browserPanelStore.navigate('https://gitlab.corp.cn'))
    expect((document.querySelector('[data-panel]') as HTMLElement).getAttribute('data-visible')).toBe('true')
  })

  it('crash event → reload webview (§6.2 自愈)', async () => {
    render(<BrowserPanel />)
    await screen.findByTitle('browserPanel.hide')
    const wv = getWebview()
    const reloadSpy = vi.fn()
    Object.defineProperty(wv, 'reload', { value: reloadSpy, configurable: true })
    fireEvent(wv, new Event('crash'))
    await vi.waitFor(() => expect(reloadSpy).toHaveBeenCalled())
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/BrowserPanel.test.tsx`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 browserPanelStore.ts**

```ts
/** browserPanelStore（2.8 §8.1）——内嵌浏览器面板全局状态。
 *  零依赖模块级 store（useSyncExternalStore）；写入方三处：
 *  useTaskChat action 流（showCompact）/ 任务横幅与设置页（navigate）。 */
import { useSyncExternalStore } from 'react'

export interface BrowserPanelState {
  visible: boolean
  expanded: boolean
  /** 登录导航目标（面板 header 呈现；null = 未指定，保持当前页） */
  currentUrl: string | null
}

let state: BrowserPanelState = { visible: false, expanded: false, currentUrl: null }
const listeners = new Set<() => void>()

function setState(patch: Partial<BrowserPanelState>) {
  state = { ...state, ...patch }
  listeners.forEach((l) => l())
}

export const browserPanelStore = {
  subscribe(l: () => void) {
    listeners.add(l)
    return () => { listeners.delete(l) }
  },
  getState: (): BrowserPanelState => state,
  /** LLM 开始驱动（action 流感知）→ 浮现小卡；不改导航位置（§8.2） */
  showCompact() { setState({ visible: true, expanded: false }) },
  expand() { setState({ visible: true, expanded: true }) },
  collapse() { setState({ visible: true, expanded: false }) },
  /** 关闭 ≠ 中断：仅隐藏，webview 存活、导航状态保留（§8.3） */
  hide() { setState({ visible: false }) },
  /** 登录入口：打开面板并导航（任务横幅 / 设置页两处调用，§4.1 写路径） */
  navigate(url: string) { setState({ visible: true, currentUrl: url }) },
}

export function useBrowserPanel(): BrowserPanelState {
  return useSyncExternalStore(browserPanelStore.subscribe, browserPanelStore.getState)
}
```

- [ ] **Step 4: 实现 BrowserPanel.tsx + CSS**

`src/features/browser/BrowserPanel.tsx`：

```tsx
/** BrowserPanel（2.8 §8）：内嵌浏览器面板，主窗口右上角三态。
 *  hidden（移出视口，保布局——§8.3 hidden 态保持布局）/ compact / expanded。
 *  webview 恒渲染（关闭 ≠ 销毁，导航状态保留）；crash → reload 自愈（§6.2）。 */
import { useEffect, useRef, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { browserPanelStore, useBrowserPanel } from './browserPanelStore'
import styles from './BrowserPanel.module.css'

// 待命页（与 _fetch_core.MARK_PAGE 同值字符串；close 归位由 Python 侧权威执行）
const STANDBY_URL =
  'data:text/html,<title>inkcube-fetch-pool</title>' +
  // 裸 # 会被 URL 解析截为 fragment，颜色用关键字（同 _fetch_core.MARK_PAGE）
  '<body style="font-family:sans-serif;color:gray">InkCube 网页抓取待命</body>'
const WEBFETCH_PARTITION = 'persist:webfetch'

export function BrowserPanel() {
  const { t } = useTranslation()
  const panel = useBrowserPanel()
  const wvRef = useRef<Electron.WebviewTag | null>(null)
  const [preloadPath, setPreloadPath] = useState('')
  const [pageTitle, setPageTitle] = useState('')
  const [crashed, setCrashed] = useState(false)

  // preload 绝对路径（IPC 取一次，Task 5 通道）
  useEffect(() => {
    let cancelled = false
    window.electronAPI.invoke('webfetch:getPreloadPath')
      .then((p) => { if (!cancelled) setPreloadPath(String(p)) })
      .catch(() => {})  // 主进程异常时面板退化为无 preload（标记匹配将失败，skill 报明确错误）
    return () => { cancelled = true }
  }, [])

  // store.currentUrl 变化 → 命令式导航（webview 的 src 属性回写不可靠）
  useEffect(() => {
    if (panel.currentUrl && wvRef.current) {
      try { wvRef.current.loadURL(panel.currentUrl) } catch { /* webview 未就绪，src 兑底 */ }
    }
  }, [panel.currentUrl])

  const handleCrash = useCallback(() => {
    // §6.2 崩溃自愈：reload → preload 重注入 → skill 下次 attach 自动恢复
    setCrashed(true)
    try { wvRef.current?.reload() } catch { /* 已销毁则下次导航自然重建 */ }
  }, [])

  const handlePageTitle = useCallback((e: Event) => {
    setPageTitle((e as CustomEvent<string>).detail || '')
    setCrashed(false)
  }, [])

  return (
    <div
      data-panel=""
      className={`${styles.panel} ${panel.expanded ? styles.expanded : styles.compact}`}
      data-visible={panel.visible ? 'true' : 'false'}
      data-expanded={panel.expanded ? 'true' : 'false'}
    >
      <div className={styles.header}>
        <span className={styles.drivingDot} title={t('browserPanel.driving')} />
        <span className={styles.pageTitle}>{pageTitle || t('browserPanel.title')}</span>
        {crashed && <span className={styles.crashHint}>{t('browserPanel.crashReload')}</span>}
        <button
          className={styles.iconBtn}
          title={panel.expanded ? t('browserPanel.collapse') : t('browserPanel.expand')}
          onClick={() => (panel.expanded ? browserPanelStore.collapse() : browserPanelStore.expand())}
        >
          {panel.expanded ? '⤡' : '▢'}
        </button>
        <button className={styles.iconBtn} title={t('browserPanel.hide')} onClick={() => browserPanelStore.hide()}>
          ✕
        </button>
      </div>
      <webview
        ref={wvRef}
        src={STANDBY_URL}
        partition={WEBFETCH_PARTITION}
        preload={preloadPath}
        className={styles.webview}
        onpage-title-updated={handlePageTitle}
        oncrash={handleCrash}
      />
    </div>
  )
}
```

`src/features/browser/BrowserPanel.module.css`：

```css
/* BrowserPanel 三态（2.8 §8）：hidden 移出视口保布局（非 display:none，
   §8.3——webview 布局尺寸保留，快照视口枚举与人工点击坐标有效） */
.panel {
  position: fixed;
  top: 48px;
  right: 12px;
  width: 360px;
  height: 480px;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary, #1e1e2e);
  border: 1px solid var(--border, #333);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.45);
  z-index: 900;
  transition: width 0.18s ease, height 0.18s ease;
}

.panel[data-visible='false'] {
  /* hidden：移出视口 + pointer-events 关（布局尺寸不变） */
  left: -10000px;
  top: -10000px;
  right: auto;
  pointer-events: none;
}

.panel[data-expanded='true'] {
  width: 92vw;
  height: calc(92vh - 48px);
  top: calc(4vh + 48px);
  right: 4vw;
  z-index: 1100;
}

.header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border, #333);
  font-size: 12px;
  color: var(--text-secondary, #aaa);
}

.drivingDot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e5484d;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  50% { opacity: 0.35; }
}

.pageTitle {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.crashHint { color: #f76b15; }

.iconBtn {
  border: none;
  background: transparent;
  color: var(--text-secondary, #aaa);
  cursor: pointer;
  font-size: 13px;
  padding: 2px 6px;
  border-radius: 4px;
}

.iconBtn:hover { background: var(--bg-hover, #2a2a3a); }

.webview { flex: 1; width: 100%; border: none; }
```

- [ ] **Step 5: webview JSX 类型声明 + App 挂载**

`src/vite-env.d.ts` 追加（文件末尾）：

```ts
/** Electron <webview> 标签（2.8 §6.2 V2 BrowserPanel；lib.dom 不含） */
declare global {
  namespace JSX {
    interface IntrinsicElements {
      webview: React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string
        partition?: string
        preload?: string
        allowpopups?: boolean
        useragent?: string
        oncrash?: (event: Event) => void
        'onpage-title-updated'?: (event: Event) => void
        'ondom-ready'?: (event: Event) => void
      }
    }
  }
}

declare namespace Electron {
  interface WebviewTag extends HTMLElement {
    loadURL(url: string): void
    reload(): void
    getURL(): string
    getTitle(): string
  }
}
```

`src/App.tsx`：import 区加 `import { BrowserPanel } from './features/browser/BrowserPanel'`；渲染树 `<AboutDialog … />` 之前（即 `.app-layout` 闭合后）加一行 `{/* 内嵌浏览器面板（2.8 §8）：App 级常驻，三态由 store 驱动 */}` + `<BrowserPanel />`。

- [ ] **Step 6: 跑测试 + 类型检查**

Run:
```bash
npx vitest run tests/BrowserPanel.test.tsx
npx tsc --noEmit
```
Expected: 5 用例 PASS；tsc 无错误

---

### Task 7: 登录入口接线（面板 store）+ useTaskChat 感知 + i18n

**Files:**
- Modify: `src/features/task/TaskMessageList.tsx`（横幅点击 → store；删 `loginOpenError` 死态）
- Modify: `src/features/settings/WebLogins.tsx`（重新登录 → store；删 openFailed 错误展示）
- Modify: `src/features/task/useTaskChat.ts`（browser-fetch action 感知 → `showCompact()`）
- Modify: `src/i18n/locales/zh-CN.json` / `en-US.json`（增 `browserPanel.*`；删死键 `task.loginOpenFailed` / `webLogins.openFailed`）
- Test: `tests/TaskMessageList.test.tsx`（横幅断言更新）+ `tests/WebLogins.test.tsx`（relogin 断言更新）

**Interfaces:**
- Consumes: `browserPanelStore.navigate/showCompact`（Task 6）
- Deletes: 死键 `task.loginOpenFailed`、`webLogins.openFailed`（删前端调用后 i18n 死键同步清理，项目规范 §2.2）

- [ ] **Step 1: 更新测试（先写失败断言）**

`tests/WebLogins.test.tsx`：新增用例（追加到现有 describe 内；relogin 现有断言若调 `weblogin:open` 同步改）：

```tsx
  it('relogin routes through panel store, not weblogin:open (2.8 §7.1)', async () => {
    const navSpy = vi.fn()
    vi.doMock('../src/features/browser/browserPanelStore', () => ({
      browserPanelStore: { navigate: navSpy, showCompact: vi.fn(), hide: vi.fn() },
      useBrowserPanel: () => ({ visible: false, expanded: false, currentUrl: null }),
    }))
    render(<WebLogins />)
    await screen.findByText('example.com')
    fireEvent.click(screen.getByText('webLogins.relogin'))
    await vi.waitFor(() => expect(navSpy).toHaveBeenCalledWith('https://example.com'))
    const channels = invokeMock.mock.calls.map(([c]) => c)
    expect(channels).not.toContain('weblogin:open')
    vi.doUnmock('../src/features/browser/browserPanelStore')
  })
```

`tests/TaskMessageList.test.tsx`：找到现有 needs_login 横幅用例（tool=skill_runner + output 含 needs_login），断言改两处——点击「去登录」后：① `invokeMock` 未被调 `weblogin:open`；② mock 的 `browserPanelStore.navigate` 被调 `https://{domain}`（doMock 模式同上；该文件若已渲染真实组件树，直接在文件顶部 `vi.mock('../src/features/browser/browserPanelStore', …)` 用 hoisted spy：

```tsx
const navSpy = vi.hoisted(() => vi.fn())
vi.mock('../src/features/browser/browserPanelStore', () => ({
  browserPanelStore: { navigate: navSpy, showCompact: vi.fn(), hide: vi.fn() },
  useBrowserPanel: () => ({ visible: false, expanded: false, currentUrl: null }),
}))
```

并在横幅用例中：`expect(navSpy).toHaveBeenCalledWith('https://gitlab.corp.cn')`。

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run tests/WebLogins.test.tsx tests/TaskMessageList.test.tsx`
Expected: FAIL（组件仍调 `weblogin:open`）

- [ ] **Step 3: 实现（三处接线）**

① `TaskMessageList.tsx` ToolCard（L570-585 区域）：

```tsx
  // 登录主路径在任务卡片内（2.8 §8.3）：幂等自动登记（失败不阻塞）后面板
  // 打开并导航至登录页；用户面板内登录完回对话说「继续」，agent 续接。
  // V2：weblogin:open IPC 删除，登录载体 = BrowserPanel（§7.1）——导航被
  // partition 拦截时 webview 渲染 blocked 页，无独立错误通道
  const handleLogin = async () => {
    if (!loginDomain) return
    try {
      await window.electronAPI.invoke('weblogin:add', { domain: loginDomain })
    } catch { /* 登记失败仍可登录 */ }
    browserPanelStore.navigate(`https://${loginDomain}`)
  }
```

同步删除：`loginOpenError` state、`setLoginOpenError(false/true)` 调用、L614-616 的 `loginOpenFailed` 横幅 JSX；import 区加 `import { browserPanelStore } from '../browser/browserPanelStore'`。

② `WebLogins.tsx`（L65-74）：

```tsx
  // 重新登录 = 面板打开并导航（V2：登录载体 = BrowserPanel，§7.1；
  // 内网域被 partition 拦截器 cancel 时 webview 呈现加载失败页，无独立错误通道）
  const handleRelogin = (d: string) => {
    browserPanelStore.navigate(`https://${d}`)
  }
```

同步删除 L122 的 `{error && …}` openFailed 展示、`setError(t('webLogins.openFailed'…))` 引用（`setError` 本身保留给 handleAdd 用）；import 同上。

③ `useTaskChat.ts` L178-181 `type === 'action'` 分支内（`setStreamSteps` 前）：

```tsx
        } else if (type === 'action') {
          const parsed = …  // 现有解析
          // 2.8 §8：LLM 开始驱动浏览器 → 面板自动浮现小卡（隐藏态不改导航）
          if (parsed.tool === 'browser-fetch') browserPanelStore.showCompact()
          …
```

L346 的恢复历史流分支（bg.steps.push 处）**不接**——恢复场景不自动弹面板，用户主动唤出（§8.2「再唤出即回到当前页面」）。

- [ ] **Step 4: i18n 增删**

`zh-CN.json`：顶层加（与 `webLogins` 同级）：

```json
"browserPanel": {
  "title": "内置浏览器",
  "driving": "LLM 驱动中，可随时人工接管",
  "expand": "放大",
  "collapse": "收起",
  "hide": "隐藏（保留状态）",
  "crashReload": "页面异常，正在恢复…"
}
```

删除死键：`task.loginOpenFailed`（L560）、`webLogins.openFailed`（L812）。
`en-US.json` 同位置对称操作（`"Built-in Browser"` / `"LLM driving — click to intervene anytime"` / `Expand` / `Collapse` / `Hide (state kept)` / `Page crashed, recovering…`；删同两键）。

- [ ] **Step 5: 跑测试 + 类型检查**

Run:
```bash
npx vitest run tests/WebLogins.test.tsx tests/TaskMessageList.test.tsx
npx tsc --noEmit
npx vitest run
```
Expected: 两文件 PASS；tsc 无错误；vitest 全量无新增失败

```bash
$hits = Select-String -Path src/**/*.tsx,src/**/*.ts -Pattern 'weblogin:open'; if ($hits) { $hits | Out-String } else { 'CLEAN' }
$dead = Select-String -Path src/i18n/locales/*.json -Pattern 'loginOpenFailed|openFailed'; if ($dead) { $dead | Out-String } else { 'CLEAN' }
```
Expected: 均 CLEAN（死调用与死 i18n 键归零）

---

### Task 8: 全链路手动验收 + 死引用归零 + 文档双向对齐

**Files:**
- Verify only（无新改动；发现问题回对应任务修）
- Modify: `docs/2.8browser-fetch-design.md`（实施后偏差回写，见 Step 3）

**Interfaces:**
- Consumes: Task 1-7 全部产出

- [ ] **Step 1: 全量自动化验证**

Run:
```bash
C:/Users/Kelvin/.conda/envs/inkcube/python.exe -m pytest python/tests/ -q
npx tsc --noEmit
npx vitest run
npm run build
node --check electron/main.js; node --check electron/fetch-pool.js; node --check electron/ipc-handlers.js
```
Expected: pytest 全绿；tsc 无错；vitest 全量绿；build 成功

- [ ] **Step 2: 手动六场景验收（§11 末行）**

Run: `npm run dev`（完整栈），逐场景：

1. **单页抓取回归**：任务内让 agent 抓一个公开页（open + extract）——验证 V1 能力零缩水，落盘 `fetched/*.md`
2. **交互采集全链**：搜索页场景 open → 按快照 click → type --submit → extract，全程右上角小卡可见、页面加载/跳转/填表实时可见
3. **需登录站点**：横幅出现 → 点「立即登录」→ 面板打开并导航至登录页 → 面板内手动登录（验证码/2FA）→ 回话「继续」→ LLM 续接成功
4. **三态与状态保留**：放大/收起/关闭后 LLM 继续操作不中断；再唤出回到当前页面；hidden 态快照 elements 仍正常（保布局验证）
5. **双任务并发**：两任务同时驱动浏览器——第二任务收到「浏览器正被其他任务使用」，第一任务结束后重试成功
6. **持久化**：重启应用后已登录站点 session 仍有效（persist:webfetch cookie）

Expected: 六场景全过；日志（data/logs/app.log）无异常报错

- [ ] **Step 3: 文档双向对齐复核（项目规范 §2.1）**

代码 → 文档（实施中与 spec 的偏差，逐条核对 `docs/2.8browser-fetch-design.md`）：
- §6.2 补一句：webview preload 需绝对路径，经 IPC `webfetch:getPreloadPath` 下发（本计划实施新增，spec 未预见的实施细节）
- §9.2 IPC 表补 `webfetch:getPreloadPath` 行（renderer → main，取 webview preload 绝对路径）
- §12 V2 表 S5-S8 标 ✅ 已完成 + 验收结果（用例数）
- 若实现与 spec 有其它偏差（如 get_by_text 降级策略），逐条回写

文档 → 代码：grep spec §7.1 清理清单每一项的对应符号（`ensurePoolWindow`/`openLoginWindow`/`shutdown`/`weblogin:open`/`INKCUBE_TASK_ID`/`webviewTag`），确认与实际一致；全仓 grep `weblogin:open|ensurePoolWindow|openLoginWindow|fetchPool.shutdown` 归零（`.superpowers/` 历史快照与 `docs/archive/` 除外）。

- [ ] **Step 4: 交付汇报**

汇总：各任务测试结果（数字）、六场景验收结果、文档回写清单——交用户手动 git 提交（规范 §一）。

---

## Self-Review 记录（计划完成后已执行）

1. **Spec 覆盖**：§5.2 七 action ↔ Task 3/4；§3.3 租约锁三规则 ↔ Task 2 测试五用例；§7.1 八行清理清单 ↔ Task 5（electron 三文件）+ Task 7（前端两处调用 + 测试）；§8.1 组件清单 ↔ Task 6/7；§11 测试计划 ↔ Task 2/3/6/7 测试 + Task 8 六场景；§12 S5↔Task 1-4 / S6↔Task 5 / S7↔Task 6-7 / S8↔Task 8——无缺口
2. **占位符扫描**：Task 5 Step 1 的「原样保留现文件 Lxx-yy 函数体」是精确到行的拷贝指令（非待定项）；其余步骤均含完整代码
3. **类型一致性**：`browserPanelStore.navigate(url)` 在 Task 6 定义 / Task 7 两处调用一致；`FetchLock(data_dir, task_id)` 在 Task 2 定义 / Task 3 `_actions.run_action` 调用一致；`snapshot_js()` 返回键与 `_snapshot` 组装一致；`run_action` 在 Task 3 main.py 引用 / `_actions.py` 定义签名一致
4. **实施时需注意的已知坑**（已写入对应任务）：playwright Locator.count() 返回协程（Task 3 Step 4 末尾修正块）；webview src 属性回写不可靠 → 命令式 loadURL（Task 6 Step 4）；React hoisted mock 模式（Task 7 Step 1）

