"""meeting_notes service 测试（2.13 §3 结构化路径）。

LLM 依赖经 monkeypatch 隔离（不触网，模式同 test_profile_service）：
- _structure_text 替换测状态机四路径（成功/坏输出/异常/材料空）；
- _parse_output 直测解析容错（围栏容忍 + schema 校验）；
- fetch_active 测 Activity 快照（structuring 行 → 心跳源）。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
import meeting_notes_service as svc


@pytest.fixture
def db():
    d = Database(db_path=":memory:")
    d.initialize()
    yield d
    d.conn.close()


def _draft_note(db, raw_text="和王总聊了扩产计划", image_ids=None) -> dict:
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    return db.create_meeting_note(pid, "拜访-测试", "2026-10-27",
                                  raw_text, image_ids or [])


def test_success_marks_structured(db, monkeypatch):
    note = _draft_note(db)
    db.update_meeting_note(note["id"], {"status": "structuring"})
    payload = {"content_md": "## 纪要\n要点", "action_items": [{"title": "准备政策包"}]}
    monkeypatch.setattr(svc, "_structure_text", lambda text, settings: payload)
    svc.structure_meeting_note_sync(db, note["id"], {})
    row = db.get_meeting_note(note["id"])
    assert row["status"] == "structured"
    assert json.loads(row["draft_md"]) == payload
    assert row["last_error"] is None


def test_bad_llm_output_back_to_draft(db, monkeypatch):
    note = _draft_note(db)
    db.update_meeting_note(note["id"], {"status": "structuring"})

    def _bad(text, settings):
        raise ValueError("LLM_BAD_RESPONSE")

    monkeypatch.setattr(svc, "_structure_text", _bad)
    svc.structure_meeting_note_sync(db, note["id"], {})
    row = db.get_meeting_note(note["id"])
    assert row["status"] == "draft" and row["last_error"] == "LLM_BAD_RESPONSE"


def test_llm_exception_back_to_draft(db, monkeypatch):
    note = _draft_note(db)
    db.update_meeting_note(note["id"], {"status": "structuring"})

    def _boom(text, settings):
        raise RuntimeError("network down")

    monkeypatch.setattr(svc, "_structure_text", _boom)
    svc.structure_meeting_note_sync(db, note["id"], {})
    row = db.get_meeting_note(note["id"])
    assert row["status"] == "draft" and row["last_error"] == "LLM_FAILED"


def test_no_material_skips_llm(db, monkeypatch):
    """材料空：前置拦截不调 LLM（executor 线程抛异常无人接，落 last_error 让前端可见）。"""
    note = _draft_note(db, raw_text="   ")
    called = {"n": 0}

    def _spy(text, settings):
        called["n"] += 1
        return {"content_md": "x", "action_items": []}

    monkeypatch.setattr(svc, "_structure_text", _spy)
    svc.structure_meeting_note_sync(db, note["id"], {})
    assert called["n"] == 0
    row = db.get_meeting_note(note["id"])
    assert row["status"] == "draft" and row["last_error"] == "NO_MATERIAL"


def test_missing_note_noop(db):
    assert svc.structure_meeting_note_sync(db, "missing", {}) is None


def test_collect_materials_uses_vlm_descriptions(db):
    """图片解析文本取 images.vlm_description（现有解析产物；files 表无
    extracted_text 列——计划骨架笔误，以实际 schema 为准）。"""
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    f = db.create_file(pid, "img1.png", "image/png", 10, "h1")
    db.create_image(f["id"], "/tmp/a.png", vlm_description="厂区大门照片")
    db.create_image(f["id"], "/tmp/b.png")  # 未描述图跳过
    note = db.create_meeting_note(pid, "t", None, "文本材料", [f["id"]])
    text = svc._collect_materials(db, note)
    assert "文本材料" in text and "厂区大门照片" in text
    # 缺失 file_id（文件已删）不抛，跳过
    note2 = db.create_meeting_note(pid, "t2", None, None, ["gone"])
    assert svc._collect_materials(db, note2) == ""


def test_parse_output_tolerates_fence():
    raw = '```json\n{"content_md": "正文", "action_items": [{"title": "A"}]}\n```'
    assert svc._parse_output(raw) == {"content_md": "正文",
                                      "action_items": [{"title": "A"}]}


def test_parse_output_rejects_bad_schema():
    with pytest.raises(ValueError):
        svc._parse_output('{"content_md": "x", "action_items": "not-a-list"}')
    with pytest.raises(ValueError):
        svc._parse_output("no json here")
    with pytest.raises(ValueError):
        svc._parse_output('{"action_items": []}')  # content_md 缺失


def test_fetch_active_lists_structuring(db):
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    a = db.create_meeting_note(pid, "纪要A", None, None, [])
    b = db.create_meeting_note(pid, "纪要B", None, None, [])
    assert svc.fetch_active(db) == []  # draft 不进活动
    db.update_meeting_note(a["id"], {"status": "structuring"})
    items = svc.fetch_active(db)
    assert len(items) == 1
    assert items[0]["entity_type"] == "note_structuring"
    assert items[0]["entity_id"] == a["id"]
    assert items[0]["project_id"] == pid
    assert items[0]["project_name"] == "P"
    assert items[0]["entity_title"] == "纪要A"
    db.update_meeting_note(b["id"], {"status": "structuring"})
    assert len(svc.fetch_active(db)) == 2


# ── 定稿驱动解析（2026-10-28）：等待就绪 + 描述上下文注入 ──


def test_context_for_file_returns_note_text(db):
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    db.create_meeting_note(pid, "拜访-王总", None, "扩产计划讨论", ["f_abc12345"])
    ctx = svc.context_for_file(db, "f_abc12345")
    assert ctx is not None
    assert "拜访-王总" in ctx and "扩产计划讨论" in ctx


def test_context_for_file_exact_match_not_prefix(db):
    """LIKE 带引号：查 f1 不误命中 f12（image_file_ids 是 JSON 数组编码）。"""
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    db.create_meeting_note(pid, "t", None, None, ["f12"])
    assert svc.context_for_file(db, "f1") is None
    assert svc.context_for_file(db, "f12") is not None


def test_context_for_file_no_match_returns_none(db):
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    db.create_meeting_note(pid, "t", None, "x", ["f1"])
    assert svc.context_for_file(db, "other") is None


def _file_with_image(db, pid, file_status: str, image_status: str | None) -> str:
    f = db.create_file(pid, "img.png", "image/png", 10, "h")
    db.conn.execute("UPDATE files SET status = ? WHERE id = ?",
                    (file_status, f["id"]))
    img = db.create_image(f["id"], "/tmp/a.png")
    if image_status is not None:
        db.conn.execute("UPDATE images SET status = ? WHERE id = ?",
                        (image_status, img["id"]))
    db.conn.commit()
    return f["id"]


def test_wait_images_ready_skips_deleted_file(db):
    """已删图直接就绪（同 _collect_materials 缺失 file_id 跳过口径）。"""
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    note = db.create_meeting_note(pid, "t", None, None, ["gone"])
    assert svc._wait_images_ready(db, note) is None


def test_wait_images_ready_returns_none_when_done(db):
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    fid = _file_with_image(db, pid, "done", "done")
    note = db.create_meeting_note(pid, "t", None, None, [fid])
    assert svc._wait_images_ready(db, note) is None


def test_wait_images_ready_reports_file_error(db):
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    fid = _file_with_image(db, pid, "error", None)
    note = db.create_meeting_note(pid, "t", None, None, [fid])
    assert svc._wait_images_ready(db, note) == "IMAGE_FAILED"


def test_wait_images_ready_reports_image_error(db):
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    fid = _file_with_image(db, pid, "done", "error")
    note = db.create_meeting_note(pid, "t", None, None, [fid])
    assert svc._wait_images_ready(db, note) == "IMAGE_FAILED"


def test_wait_images_ready_polls_until_done(db, monkeypatch):
    """首轮在途、次轮就绪 → 验证轮询而非只在首轮判定。"""
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    f = db.create_file(pid, "img.png", "image/png", 10, "h")
    db.conn.commit()
    note = db.create_meeting_note(pid, "t", None, None, [f["id"]])
    monkeypatch.setattr(svc, "_POLL_INTERVAL_S", 0.0)
    calls = {"n": 0}

    def settles_second_round(db_, fid):
        calls["n"] += 1
        return calls["n"] > 1  # 首轮在途，之后就绪（模拟解析完成）

    monkeypatch.setattr(svc, "_image_settled", settles_second_round)
    assert svc._wait_images_ready(db, note) is None
    assert calls["n"] >= 2


def test_wait_images_ready_timeout(db, monkeypatch):
    """deadline 到仍 pending → IMAGES_TIMEOUT（等待不无限期挂起，图仍在后台解析）。"""
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    f = db.create_file(pid, "img.png", "image/png", 10, "h")
    db.conn.commit()
    note = db.create_meeting_note(pid, "t", None, None, [f["id"]])
    monkeypatch.setattr(svc, "_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(svc, "_IMAGES_WAIT_TIMEOUT_S", 0.05)
    assert svc._wait_images_ready(db, note) == "IMAGES_TIMEOUT"


def test_structure_sync_fails_when_image_error(db, monkeypatch):
    """受理时附图已 error → 回 draft + 错误码，不调 LLM
    （定稿驱动：等待失败即止，图修复后用户重试生成）。"""
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    fid = _file_with_image(db, pid, "error", None)
    note = db.create_meeting_note(pid, "t", None, "文本", [fid])
    db.update_meeting_note(note["id"], {"status": "structuring"})
    called = {"n": 0}

    def _spy(text, settings):
        called["n"] += 1
        return {"content_md": "x", "action_items": []}

    monkeypatch.setattr(svc, "_structure_text", _spy)
    svc.structure_meeting_note_sync(db, note["id"], {})
    assert called["n"] == 0
    row = db.get_meeting_note(note["id"])
    assert row["status"] == "draft" and row["last_error"] == "IMAGE_FAILED"


def test_collect_materials_injects_vlm_description(db):
    """端到端材料拼装：附图解析完成后其 vlm_description 进入材料文本。"""
    pid = db.create_project(name="P", seed_playbook=False)["id"]
    f = db.create_file(pid, "img.png", "image/png", 10, "h")
    db.create_image(f["id"], "/tmp/a.png", vlm_description="现场签约照片")
    note = db.create_meeting_note(pid, "t", None, "文本材料", [f["id"]])
    text = svc._collect_materials(db, note)
    assert "现场签约照片" in text
