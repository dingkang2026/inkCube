"""python/tests/test_ppt_templates_api.py — 模板工坊 API：E2 双源列表 / 409 双源
预检 / 用户库删除与内置保护 / spec 随条目源 / rescan 自扫重建（2.3 §4.2 / §5.3）。

E2 库外置后的双源模型：
- 用户库 `<data_dir>/ppt-library/templates/`：收录落地目标，可管理（删除/重扫）；
- 随包源 `builtin_skill_source('ppt-master')/templates/`：只读展示，
  条目 builtin=True。

用户决策（2026-10-19）：内置模板不可删除——升级种子重拷会原样恢复源包内容，
删除只会造成「删了又回来」的理解负担。内置判定 = 随包资源源（seed 同源）
同名模板目录存在；源定位失败退化为非内置（不锁死用户库）。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_data_lib(data):
    """用户库：styles/custom_b（完整包 + images 资产）+ 索引。"""
    styles = data / "ppt-library" / "templates" / "styles"
    ws = styles / "custom_b" / "templates"
    ws.mkdir(parents=True, exist_ok=True)
    (styles / "custom_b" / "images").mkdir(parents=True, exist_ok=True)
    (styles / "custom_b" / "images" / "logo.png").write_bytes(b"png")
    # custom_b 的 spec 带 frontmatter：断言端点剥离行为（正文不露 frontmatter）
    (ws / "design_spec.md").write_text(
        "---\nstyle_id: custom_b\nsummary: 前言元信息\n---\n\n"
        "# 正文标题\n\n| Role | HEX |\n|---|---|\n| primary | #336699 |\n",
        encoding="utf-8")
    (styles / "styles_index.json").write_text(json.dumps(
        {"custom_b": {"summary": "c"}}, ensure_ascii=False), encoding="utf-8")


def _make_resource_src(res):
    """随包资源源：styles/builtin_a + 随包索引（内置判定与内置条目展示的事实来源）。"""
    styles = res / "skills" / "ppt-master" / "templates" / "styles"
    ws = styles / "builtin_a" / "templates"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "design_spec.md").write_text("builtin-spec-body", encoding="utf-8")
    (styles / "styles_index.json").write_text(json.dumps(
        {"builtin_a": {"summary": "b"}}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import paths
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import ppt_templates as router_mod

    data = tmp_path / "data"
    _make_data_lib(data)
    _make_resource_src(tmp_path / "res")
    # 源定位指向 tmp：避免开发机回退到真实项目根 resources 干扰断言
    monkeypatch.setattr(
        router_mod, "builtin_skill_source",
        lambda name: str(tmp_path / "res" / "skills" / "ppt-master"))
    monkeypatch.setattr(paths, "get_data_dir", lambda: str(data))
    # 6b 删除连带清理走 deps.db：注入独立内存库，不触及全局单例
    from db.database import Database
    import deps
    database = Database(db_path=":memory:")
    database.initialize()
    monkeypatch.setattr(deps, "db", database)
    app = FastAPI()
    app.include_router(router_mod.router)
    yield TestClient(app)


# ── 列表：双源聚合 ──

def test_list_dual_source(client, tmp_path):
    """双源聚合：内置条目（builtin=True，随包源盘点）+ 用户库条目
    （builtin=False，库根盘点），同列表呈现。"""
    r = client.get("/ppt-templates")
    assert r.status_code == 200
    styles = {t["id"]: t for t in r.json()["templates"]["style"]}
    assert set(styles) == {"builtin_a", "custom_b"}
    assert styles["builtin_a"]["builtin"] is True
    assert styles["custom_b"]["builtin"] is False
    # 盘点随条目源展开：内置条目 workspace 指向随包源，用户库条目指向库根
    assert styles["builtin_a"]["workspace_path"].startswith(str(tmp_path / "res"))
    assert [f["path"] for f in styles["builtin_a"]["files"]] == ["templates/design_spec.md"]
    assert styles["custom_b"]["images"] == ["logo.png"]
    # files：包内全文件盘点（相对路径正斜杠 + 字节数），按路径排序
    cb_files = styles["custom_b"]["files"]
    assert [f["path"] for f in cb_files] == ["images/logo.png", "templates/design_spec.md"]
    assert all(isinstance(f["size"], int) and f["size"] > 0 for f in cb_files)


# ── 创建：409 双源冲突预检 ──

def test_create_conflict_dual_source(client, monkeypatch):
    """E2 双源 409 预检：用户库同名或内置同名均提前拒绝
    （否则 agent 跑完全程才在收录时撞车）。"""
    from routers import ppt_templates as router_mod
    monkeypatch.setattr(router_mod, "ensure_ready", lambda scope: None)
    for tid in ("custom_b", "builtin_a"):
        r = client.post("/ppt-templates/create", json={
            "kind": "style", "template_id": tid, "template_name": "X"})
        assert r.status_code == 409, tid
        assert r.json()["code"] == "TEMPLATE_ID_EXISTS"


def test_create_fresh_id_passes_preflight(client, monkeypatch):
    """全新 id 不被预检误伤：通过预检走 prepare/launch（打桩）。"""
    from routers import ppt_templates as router_mod
    monkeypatch.setattr(router_mod, "ensure_ready", lambda scope: None)
    ppt_stub = {"id": "p1", "status": "running"}
    seen = {}

    def _fake_prepare(db, **kw):
        seen["prepare"] = kw
        return ppt_stub, "msg"

    def _fake_launch(db, ppt, message):
        seen["launch"] = ppt["id"]
        return ppt

    monkeypatch.setattr(router_mod.ppt_service.service, "prepare_template", _fake_prepare)
    monkeypatch.setattr(router_mod.ppt_service.service, "launch", _fake_launch)
    r = client.post("/ppt-templates/create", json={
        "kind": "style", "template_id": "fresh_x", "template_name": "F"})
    assert r.status_code == 200
    assert r.json()["ppt"]["id"] == "p1"
    assert seen["prepare"]["template_id"] == "fresh_x"
    assert seen["launch"] == "p1"


# ── 删除：内置保护 / 用户库可管理 ──

def test_delete_builtin_rejected(client, tmp_path):
    """内置删除 → 400 BUILTIN_TEMPLATE，随包源包原样保留（只读）。"""
    r = client.delete("/ppt-templates/style/builtin_a")
    assert r.status_code == 400
    assert r.json()["code"] == "BUILTIN_TEMPLATE"
    src = (tmp_path / "res" / "skills" / "ppt-master" / "templates" / "styles"
           / "builtin_a" / "templates" / "design_spec.md")
    assert src.is_file()
    # 内置只存在于随包源，用户库本无此目录
    assert not (tmp_path / "data" / "ppt-library" / "templates" / "styles"
                / "builtin_a").exists()


def test_delete_custom_ok(client, tmp_path):
    """自建删除 → ok，用户库目录与索引条目清除，内置随包源不受影响。"""
    r = client.delete("/ppt-templates/style/custom_b")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    lib = tmp_path / "data" / "ppt-library" / "templates" / "styles"
    assert not (lib / "custom_b").exists()
    index = json.loads((lib / "styles_index.json").read_text(encoding="utf-8"))
    assert "custom_b" not in index
    assert (tmp_path / "res" / "skills" / "ppt-master" / "templates" / "styles"
            / "builtin_a" / "templates" / "design_spec.md").is_file()


def test_delete_missing_404(client):
    """用户库无条目且无目录 → 404 TEMPLATE_NOT_FOUND。"""
    r = client.delete("/ppt-templates/style/never_x")
    assert r.status_code == 404
    assert r.json()["code"] == "TEMPLATE_NOT_FOUND"


def test_delete_when_source_unavailable(client, monkeypatch):
    """源定位失败（打包异常）→ 内置保护退化为非内置，不锁死用户库（删除照常）。"""
    from routers import ppt_templates as router_mod
    monkeypatch.setattr(router_mod, "builtin_skill_source", lambda name: None)
    r = client.delete("/ppt-templates/style/custom_b")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── 删除：6b 连带清理任务历史 ──

def _mk_template_job(db, data, kind: str, tid: str, status: str):
    """造一条模板创建任务历史（kind='template' 行 + origin='ppt' 任务 + 托管工作区）。"""
    task = db.create_task(title=f"[PPT模板] {tid}", data_dir=str(data), origin="ppt")
    ws = Path(task["workspace_path"]) / "ppt_project"
    (ws / "templates").mkdir(parents=True, exist_ok=True)
    ppt = db.create_ppt(project_id="", task_id=task["id"], title=tid,
                        kind="template", workspace_path=str(ws),
                        template_kind=kind, template_id=tid)
    db.update_ppt_status(ppt["id"], status)
    return db.get_ppt(ppt["id"])


def test_delete_rejected_with_active_job(client, tmp_path):
    """6b：存在 running/awaiting_input 创建任务 → 409 拒绝，模板原样保留
    （防「任务跑完收录重建同名模板」与删除竞态）。"""
    import deps
    data = tmp_path / "data"
    for status in ("running", "awaiting_input"):
        _mk_template_job(deps.db, data, "style", "custom_b", status)
        r = client.delete("/ppt-templates/style/custom_b")
        assert r.status_code == 409, status
        assert r.json()["code"] == "TEMPLATE_JOB_ACTIVE"
        assert (data / "ppt-library" / "templates" / "styles"
                / "custom_b").is_dir()
    assert len(deps.db.list_ppts_by_template("style", "custom_b")) == 2


def test_delete_purges_terminal_job_history(client, tmp_path):
    """6b：仅终态历史 → 模板删除 + 任务历史连带清
    （ppts 登记 / tasks 行 / 消息 / 托管工作区）。"""
    import deps
    data = tmp_path / "data"
    for status in ("completed", "failed", "cancelled"):
        _mk_template_job(deps.db, data, "style", "custom_b", status)
    r = client.delete("/ppt-templates/style/custom_b")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["purged_jobs"] == 3
    # 登记行与任务行/消息全清
    assert deps.db.list_ppts_by_template("style", "custom_b") == []
    assert deps.db.conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == 0
    assert deps.db.conn.execute(
        "SELECT COUNT(*) c FROM task_messages").fetchone()["c"] == 0
    # 托管工作区（data/tasks/{tid}）已 rmtree
    tasks_root = data / "tasks"
    assert not tasks_root.exists() or not any(tasks_root.iterdir())
    # 模板与索引照常清除
    lib = data / "ppt-library" / "templates" / "styles"
    assert not (lib / "custom_b").exists()
    assert "custom_b" not in json.loads(
        (lib / "styles_index.json").read_text(encoding="utf-8"))


def test_delete_orphan_history_without_template(client, tmp_path):
    """6b 幂等修复：模板已不在（索引+目录均无）但终态历史残留
    → 照常清理返回 ok（404 判定在历史检查之后，不留无入口孤儿）。"""
    import deps
    _mk_template_job(deps.db, tmp_path / "data", "style", "ghost_s", "failed")
    r = client.delete("/ppt-templates/style/ghost_s")
    assert r.status_code == 200
    assert r.json()["purged_jobs"] == 1
    assert deps.db.list_ppts_by_template("style", "ghost_s") == []


# ── spec：源随条目切换 ──

def test_spec_returns_body_without_frontmatter(client):
    """spec 端点：返回 design_spec.md 正文且剥离 frontmatter（用户库条目）。"""
    r = client.get("/ppt-templates/style/custom_b/spec")
    assert r.status_code == 200
    spec = r.json()["spec"]
    assert spec.startswith("# 正文标题")
    assert "style_id" not in spec and "前言元信息" not in spec
    assert "| primary | #336699 |" in spec


def test_spec_builtin_reads_packaged_source(client):
    """内置条目 spec 读随包源（源随条目切换，E2）。"""
    r = client.get("/ppt-templates/style/builtin_a/spec")
    assert r.status_code == 200
    assert r.json()["spec"] == "builtin-spec-body"


def test_spec_missing_404(client, tmp_path):
    """用户库纯索引条目（无 spec 文档）→ 404 SPEC_NOT_FOUND。"""
    (tmp_path / "data" / "ppt-library" / "templates" / "styles"
     / "custom_b" / "templates" / "design_spec.md").unlink()
    r = client.get("/ppt-templates/style/custom_b/spec")
    assert r.status_code == 404
    assert r.json()["code"] == "SPEC_NOT_FOUND"


def test_spec_traversal_rejected(client):
    """路径穿越与 create 同口径拒绝（.. 与分隔符在 _ID_SAFE_RE/终检双层拦截）。"""
    r = client.get("/ppt-templates/style/../brand/x/spec")
    assert r.status_code in (400, 404)  # FastAPI 路由归一化或校验拒绝均可


# ── rescan：自扫用户库重建索引 ──

def test_rescan_rebuilds_user_library_index(client, tmp_path):
    """rescan 自扫用户库重建：完整包按 frontmatter 重建条目、残缺目录跳过、
    损坏索引被覆盖（目录树为权威，文件即状态）。"""
    styles = tmp_path / "data" / "ppt-library" / "templates" / "styles"
    # 残缺包：目录存在但无库形态 spec → skipped 不入索引
    broken = styles / "broken_pk" / "templates"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "notes.md").write_text("x", encoding="utf-8")
    # 损坏索引：非 JSON → 扫描重建覆盖
    (styles / "styles_index.json").write_text("{broken", encoding="utf-8")

    r = client.post("/ppt-templates/rescan")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # 四类 kind 逐类返回（gather 保序）
    assert [x["kind"] for x in body["results"]] == ["brand", "style", "layout", "deck"]
    style_res = next(x for x in body["results"] if x["kind"] == "style")
    assert style_res["count"] == 1
    assert style_res["skipped"] == ["broken_pk"]
    index = json.loads((styles / "styles_index.json").read_text(encoding="utf-8"))
    assert set(index) == {"custom_b"}
    # 条目按 KIND_CONFIG 字段提取（style: summary/keywords）；非索引字段（style_id）不入库
    assert index["custom_b"] == {"summary": "前言元信息"}
