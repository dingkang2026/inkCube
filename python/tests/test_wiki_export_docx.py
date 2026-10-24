"""知识册导出 docx 端点回归（2.6 §14，V2.1-T4）。

覆盖：目录序拼接（schemas position 序，与插入序无关）、空 content 页排除、
空册 409 WIKI_EMPTY（D1 契约带 code）、项目 404、落盘路径
{data_dir}/files/{pid}/exports/ + 文件名 slug sanitize、fake 渲染器收到
拼接后的 markdown（每页一级标题 + 页间分隔线）。
隔离：monkeypatch wiki.get_db / wiki.get_data_dir；render_markdown_to_docx
为端点内延迟 import，patch report_generator.renderer 命名空间即可（不依赖
真实 pandoc）。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.wiki as wiki
from db.database import Database
from report_generator import renderer as renderer_mod


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def routed(db, monkeypatch, tmp_path):
    monkeypatch.setattr(wiki, "get_db", lambda: db)
    monkeypatch.setattr(wiki, "get_data_dir", lambda: str(tmp_path))
    return db


@pytest.fixture
def fake_render(monkeypatch):
    """假 pandoc：捕获拼接 markdown 与标题，落盘最小 docx 占位字节。"""
    calls = {}

    def _render(markdown_text, output_path, title="", orientation="portrait",
                timeout=None):
        calls["md"] = markdown_text
        calls["title"] = title
        calls["timeout"] = timeout
        # 真实 renderer 会 mkdir parents（端点依赖此行为落 exports 目录），fake 同款
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PK\x03\x04")
        return str(output_path)

    monkeypatch.setattr(renderer_mod, "render_markdown_to_docx", _render)
    return calls


def _add_schema(db, key, pos):
    db.conn.execute(
        "INSERT INTO wiki_schemas (id, project_type, page_key, title, position)"
        " VALUES (?, 'general', ?, ?, ?)",
        (f"ws_{key}", key, f"概要-{key}", pos))
    db.conn.commit()


def _add_page(db, pid, key, title, content):
    db.conn.execute(
        "INSERT INTO wiki_pages (id, project_id, page_key, title, content,"
        " status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'done', ?, ?)",
        (f"wp_{key}", pid, key, title, content,
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    db.conn.commit()


def test_export_ok_joins_by_schema_position(routed, fake_render, tmp_path):
    """拼接序 = schemas position 序（目录序），非插入序；每页一级标题 + 分隔线。"""
    pid = routed.create_project("财报研究")["id"]
    # 目录序：overview(0) → moat(1)；插入序故意颠倒
    _add_schema(routed, "moat", 1)
    _add_schema(routed, "overview", 0)
    _add_page(routed, pid, "moat", "护城河", "护城河正文")
    _add_page(routed, pid, "overview", "总览", "总览正文")

    result = asyncio.run(wiki.export_wiki_docx(pid))

    assert result["pages"] == 2
    assert fake_render["md"] == (
        "# 总览\n\n总览正文\n\n---\n\n# 护城河\n\n护城河正文")
    assert "财报研究" in fake_render["title"]
    # 落盘：{data_dir}/files/{pid}/exports/wiki_{slug}_{stamp}.docx
    out = Path(result["path"])
    assert out.parent == tmp_path / "files" / pid / "exports"
    assert out.name.startswith("wiki_财报研究_") and out.suffix == ".docx"
    assert out.read_bytes() == b"PK\x03\x04"


def test_export_excludes_blank_content_pages(routed, fake_render):
    """空 content 页（占位/未编译）不进册，pages 计数只含有效页。"""
    pid = routed.create_project("P")["id"]
    _add_page(routed, pid, "a", "页A", "正文A")
    _add_page(routed, pid, "empty", "占位", "")
    _add_page(routed, pid, "ws", "纯空白", "   \n  ")

    result = asyncio.run(wiki.export_wiki_docx(pid))

    assert result["pages"] == 1
    assert fake_render["md"] == "# 页A\n\n正文A"


def test_export_empty_wiki_409_with_code(routed, fake_render):
    """空册 409 D1 契约：{error, code: WIKI_EMPTY}，前端据 errorCode 分型提示。"""
    pid = routed.create_project("P")["id"]
    _add_page(routed, pid, "placeholder", "占位", "")

    resp = asyncio.run(wiki.export_wiki_docx(pid))

    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body["code"] == "WIKI_EMPTY"
    assert body["error"]
    # 无页面行同口径
    resp2 = asyncio.run(wiki.export_wiki_docx(
        routed.create_project("Q")["id"]))
    assert resp2.status_code == 409


def test_export_project_404(routed, fake_render):
    with pytest.raises(wiki.HTTPException) as exc_info:
        asyncio.run(wiki.export_wiki_docx("nope"))
    assert exc_info.value.status_code == 404


def test_export_slug_sanitizes_project_name(routed, fake_render, tmp_path):
    """项目名含文件名非法字符 → slug 替换（Windows/Linux 通用非法集 +
    控制字符集，CodeReview M-1：与 reports._sanitize_filename 同规则）。"""
    pid = routed.create_project('研发:进度*V2?\n含\t制表')["id"]
    _add_page(routed, pid, "a", "页A", "正文")

    result = asyncio.run(wiki.export_wiki_docx(pid))

    name = Path(result["path"]).name
    for ch in '\\/:*?"<>|':
        assert ch not in name
    for ch in '\n\t\x00\x1f':
        assert ch not in name          # 控制字符（含换行/制表）同样被替换
    assert name.startswith("wiki_研发")


def test_export_filename_has_random_suffix(routed, fake_render):
    """m-1：文件名 = wiki_{slug}_{时间戳}_{8位随机后缀}.docx——同分钟重复
    导出/并发导出天然不同名（确定性递增序号存在 check-then-act 竞态）。"""
    import re as _re
    pid = routed.create_project("P")["id"]
    _add_page(routed, pid, "a", "页A", "正文")

    r1 = asyncio.run(wiki.export_wiki_docx(pid))

    assert _re.fullmatch(
        r"wiki_P_\d{8}-\d{4}_[0-9a-f]{8}\.docx",
        Path(r1["path"]).name)


def test_export_repeated_keeps_both_files(routed, fake_render):
    """重复导出新时间戳文件不覆盖旧导出（文件名含分钟级时间戳）。"""
    pid = routed.create_project("P")["id"]
    _add_page(routed, pid, "a", "页A", "正文")

    r1 = asyncio.run(wiki.export_wiki_docx(pid))
    r2 = asyncio.run(wiki.export_wiki_docx(pid))

    assert Path(r1["path"]).name != Path(r2["path"]).name
