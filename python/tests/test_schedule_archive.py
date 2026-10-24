"""python/tests/test_schedule_archive.py — 2.11 E3 T10: 情报档案沉淀单测（2.11 §6）。

覆盖：_merge_archive 窗口合并四步（待合并副本收集 / LLM 输入窗口裁剪 / 机械
替换窗口区间 / 锚点回填）、失败补并（档案与锚点不动 + 通知 + 下期多期一次
合并）、入库链（archive_file_id 清旧 + _upload_core 重入库 + 新锚点回填）、
rebuild_archive 分批重建（窗口=2 → 3 期两次调用）、_cleanup_workspaces 保留期
（12 期清最早 2 个 workspace）、_on_finish done 分支接线（合并 + 清理）。

LLM client 全程 mock（monkeypatch scheduler 命名空间）；
asyncio.run 驱动（项目惯例，不引入 pytest-asyncio）；run 时间戳显式回填
（连续 insert 的 now 微秒序不稳定，pending_archive_runs 排序需确定性）。
"""
import asyncio
import base64
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import scheduler as scheduler_mod
import routers.knowledge as knowledge_router
from db.database import Database
from scheduler import ScheduleScheduler

_NOW = datetime(2026, 10, 19, 10, 0, 0)


@pytest.fixture
def tmp_db():
    database = Database(db_path=":memory:")
    database.initialize()
    yield database
    database.conn.close()


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "_now_dt", lambda: _NOW)


def _make_schedule(db, **kw):
    args = dict(name="华润舆情", prompt="扫描并输出报告", mode="general",
                category="corporate", interval_type="daily",
                interval_value="09:00", next_run_at=None)
    args.update(kw)
    return db.create_schedule(**args)


def _make_done_run(db, tmp_path, s, run_id, started, body=None):
    """建 1 期 done run + 报告副本落盘（_on_finish 同构），时间戳显式回填。"""
    text = body or f"## 摘要\n- {run_id}要点\n## 正文\n{run_id}正文内容\n"
    dest = Path(tmp_path) / "schedules" / f"{run_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    db.insert_schedule_run(run_id, s["id"], None)
    db.update_schedule_run(run_id, "done", report_path=str(dest),
                           summary=[f"{run_id}要点"])
    db.conn.execute(
        "UPDATE task_schedule_runs SET started_at = ?, finished_at = ?"
        " WHERE id = ?",
        (started, started.replace("T09", "T10"), run_id))
    db.conn.commit()
    return dest


def _install_llm(monkeypatch, *script):
    """LLMClient 替身：记录每次 messages 输入；脚本项为 Exception 则抛（失败补并用）。"""
    calls: list = []

    class _FakeLLM:
        def __init__(self, settings):
            pass

        async def chat_full(self, messages):
            calls.append(messages)
            step = script[len(calls) - 1]
            if isinstance(step, Exception):
                raise step
            return {"content": step}

    monkeypatch.setattr(scheduler_mod, "LLMClient", _FakeLLM)
    return calls


def _install_import_chain(monkeypatch):
    """§6 步骤 3 桩：清旧 / 入库 / 向量与 FTS 存储构造全部记录调用，不触真实 Chroma。"""
    calls = {"cleanup": [], "upload": []}

    async def fake_cleanup(db, vs, fts, file_id, project_id, data_dir,
                           remove_file_record=True):
        calls["cleanup"].append({"file_id": file_id, "project_id": project_id})

    async def fake_upload(project_id, upload, db, data_dir, worker):
        calls["upload"].append({
            "project_id": project_id,
            "filename": upload.filename,
            "fileType": upload.fileType,
            "data": base64.b64decode(upload.data).decode("utf-8"),
        })
        return {"success": True, "file_id": f"file_new{len(calls['upload'])}"}

    monkeypatch.setattr(scheduler_mod, "cleanup_file_data", fake_cleanup)
    # _upload_core 延迟 import（routers.knowledge → deps → scheduler 成环），
    # 打在源模块命名空间上（函数内 from-import 每次执行时取模块属性）
    monkeypatch.setattr(knowledge_router, "_upload_core", fake_upload)
    monkeypatch.setattr(scheduler_mod, "VectorStore", lambda root: ("vs", root))
    monkeypatch.setattr(scheduler_mod, "FTSStore", lambda db: ("fts", db))
    return calls


def _archive_path(tmp_path, s):
    return Path(tmp_path) / "schedules" / f"archive_{s['id']}.md"


# ── _merge_archive：首次合并（§6 四步）─────────────────────────


def test_merge_first_time_builds_archive(tmp_db, tmp_path, frozen_now,
                                         monkeypatch):
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    _make_done_run(tmp_db, tmp_path, s, "run_2", "2026-09-02T09:00:00")
    llm_calls = _install_llm(
        monkeypatch,
        "## 时间线\n### 2026-09-01\n一期要点\n### 2026-09-02\n二期要点\n")
    calls = _install_import_chain(monkeypatch)

    asyncio.run(sched._merge_archive(s))

    text = _archive_path(tmp_path, s).read_text(encoding="utf-8")
    assert "# 华润舆情·情报档案" in text                  # 头部初始化
    assert "## 时间线" in text
    assert "### 2026-09-01" in text and "### 2026-09-02" in text
    user = llm_calls[0][-1]["content"]
    assert "run_1正文内容" in user and "run_2正文内容" in user  # 待合并报告全文进 LLM
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] == "run_2"
    assert calls["upload"] == [] and calls["cleanup"] == []     # 未绑定去向不入库


# ── _merge_archive：窗口裁剪与机械替换（ARCHIVE_WINDOW=30）────


def _d(i: int) -> str:
    return (datetime(2026, 8, 1) + timedelta(days=i)).strftime("%Y-%m-%d")


def test_merge_window_keeps_early_sections(tmp_db, tmp_path, frozen_now,
                                           monkeypatch):
    assert scheduler_mod.ARCHIVE_WINDOW == 30
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    sections = "".join(f"### {_d(i)}\n旧节{i + 1}内容\n" for i in range(35))
    archive = _archive_path(tmp_path, s)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(f"# 华润舆情·情报档案\n\n## 时间线\n{sections}",
                       encoding="utf-8")
    for i in range(35):                                # 旧 35 期 done
        _make_done_run(tmp_db, tmp_path, s, f"run_o{i + 1:02d}",
                       f"{_d(i)}T09:00:00",
                       body=f"## 摘要\n- 旧{i + 1}\n## 正文\n旧{i + 1}正文\n")
    tmp_db.update_schedule(s["id"], {"archive_last_run_id": "run_o35"})
    _make_done_run(tmp_db, tmp_path, s, "run_new", "2026-10-05T09:00:00")

    window_reply = "".join(f"### {_d(i)}\n修订{i + 1}节\n"
                           for i in range(5, 35)) + f"### {_d(35)}\n新节36\n"
    llm_calls = _install_llm(monkeypatch, f"## 时间线\n{window_reply}")
    _install_import_chain(monkeypatch)

    asyncio.run(sched._merge_archive(s))

    user = llm_calls[0][-1]["content"]
    assert f"### {_d(5)}" in user and f"### {_d(34)}" in user  # 最近 30 节进窗口
    assert f"### {_d(4)}" not in user                          # 早期 5 节不进 LLM
    assert "run_new正文内容" in user                           # 新报告全文进 LLM
    text = archive.read_text(encoding="utf-8")
    assert "旧节1内容" in text and "旧节5内容" in text         # 早期 5 节逐字节保留
    assert "旧节6内容" not in text                             # 窗口区间被替换
    assert "修订6节" in text and "新节36" in text
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] == "run_new"


# ── _merge_archive：失败补并（锚点不动 + 通知）────────────────


def test_merge_failure_keeps_anchor_and_backfills_later(
        tmp_db, tmp_path, frozen_now, monkeypatch):
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    _install_llm(monkeypatch, TimeoutError("llm timeout"))
    calls = _install_import_chain(monkeypatch)
    notified = []
    monkeypatch.setattr(scheduler_mod.activity_events, "notify",
                        lambda db, **kw: notified.append(kw))

    asyncio.run(sched._merge_archive(s))

    assert not _archive_path(tmp_path, s).exists()     # 档案未写
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] is None  # 锚点不动
    assert calls["upload"] == []
    assert notified and "档案合并失败" in notified[0]["detail"]
    assert notified[0]["entity_id"] == s["id"]

    # 失败补并：下期 done 后锚点未动 → pending 2 期一次合并
    _make_done_run(tmp_db, tmp_path, s, "run_2", "2026-09-02T09:00:00")
    llm_calls = _install_llm(
        monkeypatch,
        "## 时间线\n### 2026-09-01\n一期\n### 2026-09-02\n二期\n")
    asyncio.run(sched._merge_archive(s))
    user = llm_calls[0][-1]["content"]
    assert "run_1正文内容" in user and "run_2正文内容" in user
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] == "run_2"


# ── _merge_archive：入库链（target 绑定 + 清旧 + 重入库）──────


def test_merge_imports_to_target(tmp_db, tmp_path, frozen_now, monkeypatch):
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db, target_type="project", target_id="proj1")
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    tmp_db.update_schedule(s["id"], {"archive_file_id": "file_old"})
    _install_llm(monkeypatch, "## 时间线\n### 2026-09-01\n一期\n")
    calls = _install_import_chain(monkeypatch)

    asyncio.run(sched._merge_archive(s))

    assert calls["cleanup"] == [{"file_id": "file_old", "project_id": "proj1"}]
    up = calls["upload"][0]
    assert up["project_id"] == "proj1"
    assert up["filename"] == "华润舆情·档案.md"
    assert up["fileType"] == ".md"
    assert "### 2026-09-01" in up["data"]              # base64 解回档案全文
    row = tmp_db.get_schedule(s["id"])
    assert row["archive_file_id"] == "file_new1"       # 新锚点回填
    assert row["archive_last_run_id"] == "run_1"


# ── rebuild_archive：窗口分批重建 ─────────────────────────────


def test_rebuild_archive_batches(tmp_db, tmp_path, frozen_now, monkeypatch):
    monkeypatch.setattr(scheduler_mod, "ARCHIVE_WINDOW", 2, raising=False)
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db, target_type="project", target_id="proj1")
    for i in range(1, 4):
        _make_done_run(tmp_db, tmp_path, s, f"run_{i}",
                       f"2026-09-{i:02d}T09:00:00")
    llm_calls = _install_llm(
        monkeypatch,
        "## 时间线\n### 2026-09-01\n批1节1\n### 2026-09-02\n批1节2\n",
        "## 时间线\n### 2026-09-01\n批1节1\n### 2026-09-02\n批1节2\n"
        "### 2026-09-03\n批2节3\n")
    calls = _install_import_chain(monkeypatch)
    archive = _archive_path(tmp_path, s)
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("脏数据", encoding="utf-8")     # rebuild 前置重置

    asyncio.run(sched.rebuild_archive(s["id"]))

    assert len(llm_calls) == 2                         # 窗口=2 → 3 期分两批
    text = archive.read_text(encoding="utf-8")
    assert "脏数据" not in text
    assert "### 2026-09-03" in text and "批2节3" in text
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] == "run_3"
    assert len(calls["upload"]) == 2                   # 计划：循环步骤 2-4


# ── 档案互斥与换向（review P1-1/P1-2）──────────────────────


def test_merge_and_rebuild_serialized(tmp_db, tmp_path, frozen_now, monkeypatch):
    """P1-1：tick 合并与 rebuild 两路都在 LLM await 处交出事件循环，无互斥
    则后写覆盖先写（被覆盖批次对应的期次节永久丢失，且锚点已声称合并永不
    补并）。同锁串行后事件序列必然 in/out 交替（无锁红灯：出现 in,in）。"""
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db, target_type="project", target_id="proj1")
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    events: list[str] = []

    class _SerialLLM:
        def __init__(self, settings):
            pass

        async def chat_full(self, messages):
            events.append("in")
            await asyncio.sleep(0)          # 让出循环：无锁时另一协程在此进入
            events.append("out")
            return {"content": "## 时间线\n### 2026-09-01\n一期\n"}

    monkeypatch.setattr(scheduler_mod, "LLMClient", _SerialLLM)
    _install_import_chain(monkeypatch)

    async def scenario():
        async def merge_locked():
            # tick 真实路径：_on_finish 持锁调 _merge_archive
            async with sched._archive_lock:
                await sched._merge_archive(s)
        await asyncio.gather(merge_locked(), sched.rebuild_archive(s["id"]))

    asyncio.run(scenario())
    assert events[0] == "in"
    for i in range(1, len(events)):
        if events[i] == "in":          # 每次「进入」的前驱必须是「退出」
            assert events[i - 1] == "out", f"交错进入（无互斥）：{events}"


def test_retarget_cleans_old_container_and_rebuilds(
        tmp_db, tmp_path, frozen_now, monkeypatch):
    """P1-2：更换去向（A→B）后若下期合并用【新 target_id + 旧
    archive_file_id】清旧，按容器定位的向量 collection 删不到旧数据
    （旧容器幽灵检索 + DB 记录误删）。切换时应以旧 target 清旧、置空锚点、
    并从全部 done 副本在新容器重建（§6「绑定即重建」）。"""
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db, target_type="project", target_id="proj1")
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    tmp_db.update_schedule(s["id"], {"archive_file_id": "file_old"})
    old = dict(tmp_db.get_schedule(s["id"]))
    tmp_db.update_schedule(s["id"], {"target_id": "proj2"})   # 端点已更新去向
    llm_calls = _install_llm(
        monkeypatch, "## 时间线\n### 2026-09-01\n一期\n")
    calls = _install_import_chain(monkeypatch)

    async def scenario():
        bg = await sched.retarget_archive(s["id"], old)  # R3-P1：返回后台任务
        await bg                                         # 等待后台重建完成
    asyncio.run(scenario())

    assert calls["cleanup"] == [{"file_id": "file_old", "project_id": "proj1"}]
    row = tmp_db.get_schedule(s["id"])
    assert row["archive_file_id"] == "file_new1"       # 新容器重建入库
    assert len(llm_calls) == 1                         # 全部 done 副本重建
    assert "run_1正文内容" in llm_calls[0][-1]["content"]
    assert calls["upload"][0]["project_id"] == "proj2"


def test_retarget_rebuild_background_acceptance(
        tmp_db, tmp_path, frozen_now, monkeypatch):
    """R3-P1：换向重建后台化——同步段（旧容器清旧 + 置空入库锚点）在返回前
    完成（必须先于任何后续合并落定，否则「旧 file_id + 新 target」清旧
    复现 P1-2 幽灵数据）；分批 LLM 重建转后台任务，受理即返回（同步等待
    会击穿 schedules:update 通道 15s IPC 超时——前端假报保存失败而服务端
    已落库）。"""
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db, target_type="project", target_id="proj1")
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    tmp_db.update_schedule(s["id"], {"archive_file_id": "file_old"})
    old = dict(tmp_db.get_schedule(s["id"]))
    tmp_db.update_schedule(s["id"], {"target_id": "proj2"})
    llm_calls = _install_llm(
        monkeypatch, "## 时间线\n### 2026-09-01\n一期\n")
    calls = _install_import_chain(monkeypatch)

    async def scenario():
        bg = await sched.retarget_archive(s["id"], old)
        assert calls["cleanup"] == [                   # 同步段已完成
            {"file_id": "file_old", "project_id": "proj1"}]
        assert tmp_db.get_schedule(s["id"])["archive_file_id"] is None
        assert llm_calls == []                         # 重建未跑（受理即返回）
        await bg
    asyncio.run(scenario())

    assert len(llm_calls) == 1
    assert tmp_db.get_schedule(s["id"])["archive_file_id"] == "file_new1"


def test_retarget_cancels_inflight_rebuild(tmp_db, tmp_path, frozen_now,
                                           monkeypatch):
    """R4-P1：换向前置取消本订阅在途重建——旧任务每批重读实时行，连续换向
    （A→B 未完成又 B→C）会把档案传到最新目标并双重回填（锚点外孤儿档案
    永久残留）；且旧任务持 _archive_lock 分钟级，不取消则 PATCH 重新击穿
    schedules:update 15s IPC 超时（R3-P1 声明消除的问题在锁竞争下复发）。"""
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db, target_type="project", target_id="proj1")
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    old = dict(tmp_db.get_schedule(s["id"]))
    tmp_db.update_schedule(s["id"], {"target_id": "proj2"})
    _install_llm(monkeypatch, "## 时间线\n### 2026-09-01\n一期\n")
    _install_import_chain(monkeypatch)

    async def scenario():
        async def stuck() -> None:       # 模拟在途重建（锚点回填前挂起）
            await asyncio.Event().wait()
        inflight = asyncio.create_task(stuck())
        sched._archive_bg.setdefault(s["id"], set()).add(inflight)
        bg = await sched.retarget_archive(s["id"], old)
        assert inflight.cancelled()      # 在途重建已取消（不双跑/不等锁）
        await bg                         # 新重建照常完成
    asyncio.run(scenario())

    assert tmp_db.get_schedule(s["id"])["archive_file_id"] == "file_new1"


def test_retarget_rebuild_bg_failure_notifies(
        tmp_db, tmp_path, frozen_now, monkeypatch):
    """R3-P1：后台重建失败不逃逸（bg task 异常无人 retrieve → 警示刷屏），
    经 archive_failed 通知反馈（下期 done 补并/再次重建可续）。"""
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db, target_type="project", target_id="proj1")
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    old = dict(tmp_db.get_schedule(s["id"]))
    tmp_db.update_schedule(s["id"], {"target_id": "proj2"})
    _install_llm(monkeypatch, TimeoutError("llm timeout"))
    _install_import_chain(monkeypatch)
    notified: list = []
    monkeypatch.setattr(scheduler_mod.activity_events, "notify",
                        lambda db, **kw: notified.append(kw))

    async def scenario():
        bg = await sched.retarget_archive(s["id"], old)
        await bg                                       # 不抛异常
    asyncio.run(scenario())

    assert notified and notified[0]["kind"] == "archive_failed"
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] is None


# ── 切节安全与合并上限（review P2-9/P2-5/P2-3）──────────────


def test_split_sections_only_date_headings():
    """P2-9：切节仅认 `### YYYY-MM-DD` 日期标题——报告正文风格的 `### `
    子标题不得截断日期节（否则窗口区间错位、期数失真）。"""
    tail = ("### 2026-09-01\n一期\n### 数据明细\n节内子标题\n"
            "### 2026-09-02\n二期\n")
    sections = scheduler_mod._split_sections(tail)
    assert len(sections) == 2
    assert "### 数据明细" in sections[0]              # 子标题留在所属日期节内
    assert sections[1].startswith("### 2026-09-02")
    assert scheduler_mod._split_sections("") == []
    assert scheduler_mod._split_sections("前导杂文无节") == []   # 无日期节不产出


def test_merge_rejects_section_loss(tmp_db, tmp_path, frozen_now, monkeypatch):
    """P2-9：LLM 输出节数少于窗口（丢节）→ 拒绝落盘走失败补并（档案与锚点
    不动 + 通知），不静默损坏档案后照常推进锚点。"""
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    sections = "".join(f"### 2026-09-{d:02d}\n旧节{d}\n" for d in range(1, 4))
    archive = _archive_path(tmp_path, s)
    archive.parent.mkdir(parents=True, exist_ok=True)
    before = f"# 华润舆情·情报档案\n\n## 时间线\n{sections}"
    archive.write_text(before, encoding="utf-8")
    _make_done_run(tmp_db, tmp_path, s, "run_new", "2026-10-05T09:00:00")
    _install_llm(monkeypatch, "## 时间线\n### 2026-09-01\n只剩一节\n")  # 1 < 3
    _install_import_chain(monkeypatch)
    notified: list = []
    monkeypatch.setattr(scheduler_mod.activity_events, "notify",
                        lambda db, **kw: notified.append(kw))

    asyncio.run(sched._merge_archive(s))

    assert archive.read_text(encoding="utf-8") == before   # 档案逐字节未动
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] is None
    assert notified and notified[0]["kind"] == "archive_failed"


def test_rebuild_reports_failure_and_batches(tmp_db, tmp_path, frozen_now,
                                             monkeypatch):
    """P2-3：rebuild 部分批失败不得虚报成功——返回 {ok: False, batches: n}
    （前端分级提示），锚点停在最后成功批。"""
    monkeypatch.setattr(scheduler_mod, "ARCHIVE_WINDOW", 2, raising=False)
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db, target_type="project", target_id="proj1")
    for i in range(1, 4):
        _make_done_run(tmp_db, tmp_path, s, f"run_{i}",
                       f"2026-09-{i:02d}T09:00:00")
    _install_llm(
        monkeypatch,
        "## 时间线\n### 2026-09-01\n批1节1\n### 2026-09-02\n批1节2\n",
        TimeoutError("llm timeout"))                   # 批 2 失败
    _install_import_chain(monkeypatch)

    res = asyncio.run(sched.rebuild_archive(s["id"]))

    assert res["ok"] is False and res["batches"] == 1
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] == "run_2"


def test_on_finish_merges_with_batch_limit(tmp_db, tmp_path, frozen_now,
                                           monkeypatch):
    """P2-5：长期合并失败下 pending 无界增长会击穿模型上下文（合并自锁
    循环）——tick 路径每批上限 ARCHIVE_MERGE_BATCH，多期分周期补完。"""
    assert scheduler_mod.ARCHIVE_MERGE_BATCH == 10
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    task = tmp_db.create_task("影子", data_dir=str(tmp_path))
    ws = Path(task["workspace_path"])
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "report.md").write_text("## 摘要\n- 要点\n", encoding="utf-8")
    tmp_db.insert_schedule_run("run_x", s["id"], task["id"])
    seen: dict = {}

    async def fake_merge(schedule, limit=None):
        seen["limit"] = limit

    monkeypatch.setattr(sched, "_merge_archive", fake_merge)

    asyncio.run(sched._on_finish(s, task["id"], "run_x", True))

    assert seen["limit"] == scheduler_mod.ARCHIVE_MERGE_BATCH


# ── _cleanup_workspaces：保留期（每订阅最近 10 期）────────────


def test_cleanup_workspaces_keeps_recent_ten(tmp_db, tmp_path, frozen_now):
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    paths = []
    for i in range(1, 13):
        ws = Path(tmp_path) / f"ws_{i:02d}"
        ws.mkdir()
        task = tmp_db.create_task(f"影子{i}", data_dir=str(tmp_path))
        tmp_db.conn.execute(
            "UPDATE tasks SET workspace_path = ? WHERE id = ?",
            (str(ws), task["id"]))
        run = f"run_{i:02d}"
        tmp_db.insert_schedule_run(run, s["id"], task["id"])
        tmp_db.update_schedule_run(run, "done")
        tmp_db.conn.execute(
            "UPDATE task_schedule_runs SET started_at = ? WHERE id = ?",
            (f"2026-09-{i:02d}T09:00:00", run))
        tmp_db.conn.commit()
        paths.append(ws)

    sched._cleanup_workspaces(s)

    assert not paths[0].exists() and not paths[1].exists()   # 最早 2 个清理
    assert all(p.exists() for p in paths[2:])                # 最近 10 保留


# ── _on_finish 接线：done → 合并 + 清理 ───────────────────────


def test_on_finish_done_wires_archive_and_cleanup(
        tmp_db, tmp_path, frozen_now, monkeypatch):
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    task = tmp_db.create_task("影子任务", data_dir=str(tmp_path))
    ws = Path(task["workspace_path"])
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "report.md").write_text("## 摘要\n- 要点\n", encoding="utf-8")
    tmp_db.insert_schedule_run("run_w", s["id"], task["id"])
    merged, cleaned = [], []

    async def fake_merge(schedule, limit=None):   # P2-5：_on_finish 现传 limit
        merged.append(schedule["id"])

    monkeypatch.setattr(sched, "_merge_archive", fake_merge)
    monkeypatch.setattr(sched, "_cleanup_workspaces",
                        lambda schedule: cleaned.append(schedule["id"]))

    asyncio.run(sched._on_finish(s, task["id"], "run_w", True))

    assert merged == [s["id"]] and cleaned == [s["id"]]


# ── 档位制路由回归：归档合并必须注入 model（2026-10-22）────────


def test_merge_routes_model_via_tier_slots(tmp_db, tmp_path, frozen_now,
                                           monkeypatch):
    """settings["model"] 是派生键（不落库），归档合并构造 LLMClient 前
    必须经 resolve_model 注入——漏注入则空串发网关报 1211「模型不存在」
    （档位制改造唯一漏网消费点，期次首次 done 触发归档合并才暴露）。"""
    tmp_db.save_settings({
        "api_key": "k",
        "standard_model": "glm-standard-x",
        "high_model": "glm-high-x",
    })
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    constructed = []

    class _CaptureLLM:
        def __init__(self, settings):
            constructed.append(dict(settings))

        async def chat_full(self, messages):
            return {"content": "## 时间线\n### 2026-09-01\n一期要点\n"}

    monkeypatch.setattr(scheduler_mod, "LLMClient", _CaptureLLM)
    _install_import_chain(monkeypatch)

    asyncio.run(sched._merge_archive(s))

    assert constructed[0]["model"] == "glm-standard-x"   # standard 档默认
    assert constructed[0]["high_model"] == "glm-high-x"  # 其余键透传
    assert tmp_db.get_schedule(s["id"])["archive_last_run_id"] == "run_1"


def test_merge_unconfigured_tiers_pass_empty_model(tmp_db, tmp_path,
                                                   frozen_now, monkeypatch):
    """用户显式清空两档（存空串覆盖出厂默认）：model 空串透传网关显性失败
    ——禁止硬编码模型名兜底（否则路由漏网变成「向网关请求不存在的模型」
    的误导故障）。注：未存键时 get_settings 落出厂默认 glm 档，非空。"""
    tmp_db.save_settings({
        "standard_model": "",
        "high_model": "",
    })
    sched = ScheduleScheduler(tmp_db, str(tmp_path), agent=object())
    s = _make_schedule(tmp_db)
    _make_done_run(tmp_db, tmp_path, s, "run_1", "2026-09-01T09:00:00")
    constructed = []

    class _CaptureLLM:
        def __init__(self, settings):
            constructed.append(dict(settings))

        async def chat_full(self, messages):
            return {"content": "## 时间线\n### 2026-09-01\n一期要点\n"}

    monkeypatch.setattr(scheduler_mod, "LLMClient", _CaptureLLM)
    _install_import_chain(monkeypatch)

    asyncio.run(sched._merge_archive(s))

    assert constructed[0]["model"] == ""
