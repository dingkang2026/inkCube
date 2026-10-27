"""python/routers/ppt_templates.py — PPT 模板工坊 API（2.3 §4.2 / §6.3，E2 库外置）。

模板库双源（E2）：
- 用户库：`<data_dir>/ppt-library/templates/<kind_dir>/<id>/` + 四个索引 JSON，
  纯目录文件即状态、无库表（2.3 §5.3），可管理（删除/重扫）——模板创建任务
  收录（ppt_service._collect_template）的唯一落地目标；
- 内置源：随包资源 `builtin_skill_source('ppt-master')/templates/`，只读展示，
  条目带 builtin 标记（不可删除）。

端点：
- GET    /ppt-templates                      双源聚合（列表/详情数据源，条目带 builtin 标记）
- GET    /ppt-templates/{kind}/{id}/spec     模板规范文档（随条目源读取，详情抽屉渲染源）
- DELETE /ppt-templates/{kind}/{template_id} 删除模板（用户库目录 + 索引条目；内置/不存在拒绝）
- POST   /ppt-templates/rescan               自扫用户库重建索引（InkCube 自实现，不再调 register CLI）

发现契约（ppt-master routing.md）：索引是唯一发现源，禁扫描目录推测条目——
本路由列表一律读索引文件，仅删除/预览等用户显式定位的动作才触碰目录树
（rescan 例外：它是索引修复动作，本身就是扫描重建）。
"""
import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import deps
import ppt_service
from readiness import ensure_ready
from skills.seed import builtin_skill_source

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ppt-templates"])


class PptTemplateCreateRequest(BaseModel):
    kind: str                    # brand / style / layout / deck
    template_id: str             # slug：library 目录名与索引 key
    template_name: str           # 显示名
    ref_paths: list[str] = []    # 本机参考素材绝对路径（A-D 通道可混合）
    notes: str | None = None

# kind → 子目录/索引文件名（对齐 ppt-master register_template.py KIND_CONFIG）
_KIND_DIRS = {
    "brand": ("brands", "brands_index.json"),
    "style": ("styles", "styles_index.json"),
    "layout": ("layouts", "layouts_index.json"),
    "deck": ("decks", "decks_index.json"),
}
# template_id 路径与命令安全：禁 shell 分词符（空白）与引号、禁路径分隔与
# Windows 保留符；.. 与首尾点由校验函数另拦（索引 key 允许中文/字母数字/_/-/.）
_ID_SAFE_RE = re.compile(r"^[^\s'\"\\/:*?<>|]+$")


def _templates_root() -> Path:
    """用户库根（E2 库外置，可管理）。目录可不存在（空库态），首次收录/删除时创建。"""
    from paths import get_data_dir
    return Path(get_data_dir()) / "ppt-library" / "templates"


def _builtin_root() -> Path | None:
    """随包源 templates/ 根（内置条目只读展示源；源定位失败返回 None）。"""
    src = builtin_skill_source("ppt-master")
    return Path(src) / "templates" if src else None


def _builtin_template_ws(kind: str, template_id: str) -> Path | None:
    """随包源中同名模板目录（存在即内置，用户决策 2026-10-19：内置不可删除）。

    以 seed 资源源为唯一事实来源：升级新增的内置模板自动纳入保护；
    源定位失败（打包异常）返回 None → 退化为非内置，不锁死用户库。
    """
    root = _builtin_root()
    if not root:
        return None
    ws = root / _KIND_DIRS[kind][0] / template_id
    return ws if ws.is_dir() else None


def _validate_template_id(template_id: str) -> JSONResponse | None:
    if not template_id or len(template_id) > 120 or not _ID_SAFE_RE.match(template_id) \
            or ".." in template_id or template_id.startswith(".") or template_id.endswith("."):
        return JSONResponse(status_code=400, content={
            "error": "模板 id 非法", "code": "INVALID_TEMPLATE_ID"})
    return None


def _read_index(root: Path, kind: str) -> dict:
    kind_dir, index_name = _KIND_DIRS[kind]
    try:
        with open(root / kind_dir / index_name, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _strip_frontmatter(text: str) -> str:
    """剥离 YAML frontmatter（summary/keywords/主色等已由索引结构化展示）。"""
    if not text.startswith("---"):
        return text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:]).lstrip("\n")
    return text


def _template_entry(root: Path, kind: str, tid: str, meta: dict, *, builtin: bool) -> dict:
    """索引条目 → API 形态：索引字段直传 + workspace 产物盘点（预览数据源）。

    root = 条目所属源（内置传随包源根，用户库传库根），产物盘点随 root 展开。"""
    kind_dir, _ = _KIND_DIRS[kind]
    ws = root / kind_dir / tid
    tpl_dir = ws / "templates"
    entry = {
        "id": tid,
        "kind": kind,
        "summary": meta.get("summary"),
        "workspace_path": str(ws),
        "has_workspace": ws.is_dir(),
        # 内置标记（随包源同名目录存在，前端隐藏删除入口 + 内置徽标）；
        # E2 起用户库条目恒为 False，随包条目恒为 True，不再逐条目探测
        "builtin": builtin,
        # 页面原型（仅结构型 layout/deck 有；brand/style 纯 spec 型为空）
        "page_svgs": sorted(p.name for p in tpl_dir.glob("*.svg")) if tpl_dir.is_dir() else [],
        # 包内资产（images/：logo 与品牌视觉元素，详情页「品牌资产」展示源）
        "images": (
            sorted(p.name for p in (ws / "images").glob("*") if p.is_file())
            if (ws / "images").is_dir() else []
        ),
        # Review PPTX（多 Master 必有；详情页下载入口）
        "preview_pptx": None,
        # 包内全文件清单（相对路径 + 字节数）：详情页「包内文件」零盲区盘点
        # （可预览的已在上方区块直观展示，清单保证非预览类文件同样可见）
        "files": [
            {"path": str(p.relative_to(ws)).replace("\\", "/"), "size": p.stat().st_size}
            for p in sorted(ws.rglob("*")) if p.is_file()
        ] if ws.is_dir() else [],
    }
    for key in ("primary_color", "canvas_format", "page_count", "page_types", "keywords"):
        if key in meta:
            entry[key] = meta[key]
    exports = ws / "exports"
    if exports.is_dir():
        previews = sorted(exports.glob("*_template_preview.pptx"))
        if previews:
            entry["preview_pptx"] = str(previews[-1])
    return entry


@router.post("/ppt-templates/create")
async def create_template(req: PptTemplateCreateRequest):
    """创建模板任务（2.3 §4.2）：origin='ppt' 任务驱动 Create Template 工作流。

    与 Generate 同构的 prepare（to_thread：素材拷贝为同步 IO）+ launch 两段；
    launch 失败回滚 failed（对齐 routers/ppts.create_ppt，防僵尸 running）。
    """
    ensure_ready("task")
    kind = (req.kind or "").strip()
    if kind not in ppt_service._TEMPLATE_KIND_DIRS:
        return JSONResponse(status_code=400, content={
            "error": "模板类型非法", "code": "INVALID_TEMPLATE_KIND"})
    bad = _validate_template_id(req.template_id)
    if bad:
        return bad
    template_id = req.template_id.strip()
    template_name = (req.template_name or "").strip()
    if not template_name:
        return JSONResponse(status_code=400, content={
            "error": "模板名称不能为空", "code": "INVALID_TEMPLATE_NAME"})
    # 冲突预检（E2 双源）：用户库或内置源已有同名模板时提前拒绝
    # （否则 agent 跑完全程才在收录时撞车）
    user_ws = _templates_root() / _KIND_DIRS[kind][0] / template_id
    builtin_ws = _builtin_template_ws(kind, template_id)
    if user_ws.exists() or builtin_ws is not None:
        return JSONResponse(status_code=409, content={
            "error": f"模板 id 已存在：{template_id}", "code": "TEMPLATE_ID_EXISTS"})
    logger.info("Creating ppt template job: kind=%s id=%s refs=%d",
                kind, template_id, len(req.ref_paths))
    try:
        ppt, message = await asyncio.to_thread(
            ppt_service.service.prepare_template,
            deps.db, kind=kind, template_id=template_id,
            template_name=template_name,
            ref_paths=req.ref_paths, notes=(req.notes or "").strip() or None,
        )
    except Exception as e:
        logger.error("Create ppt template failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={
            "error": f"创建失败: {e}", "code": "CREATE_FAILED"})
    try:
        ppt = ppt_service.service.launch(deps.db, ppt, message)
    except RuntimeError as e:
        code = str(e)
        deps.db.update_ppt_status(
            ppt["id"], "failed",
            error=("任务正在执行中，无法重复启动" if code == "TASK_RUNNING"
                   else "已达到最大并发任务数限制，启动失败"))
        if code == "TASK_RUNNING":
            return JSONResponse(status_code=409, content={
                "error": "任务正在执行中", "code": "TASK_RUNNING"})
        return JSONResponse(status_code=429, content={
            "error": "已达到最大并发任务数限制", "code": "TASK_LIMIT_REACHED"})
    except Exception as e:
        # 与 _launch_or_rollback 同语义：非 RuntimeError（如写库锁冲突）不处置
        # 则行卡在无泵状态，永久占据模板分区轮询
        logger.error("PPT template launch failed: ppt=%s", ppt["id"], exc_info=True)
        deps.db.update_ppt_status(ppt["id"], "failed", error=f"启动失败: {e}")
        return JSONResponse(status_code=500, content={
            "error": "任务启动失败，请重试", "code": "LAUNCH_FAILED"})
    return {"ppt": ppt}


@router.get("/ppt-templates/jobs")
async def list_template_jobs(
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """模板创建任务列表：活跃段全量（分区轮询进度与门禁不可截断）
    + 终态段 limit/offset（has_more 只看终态段，探测在 db 层完成）。"""
    jobs, has_more = deps.db.list_template_jobs(limit=limit, offset=offset)
    # 失败任务的收录重试入口判定（免培训：重试收录按钮只在有意义的行上出现）
    for job in jobs:
        if job.get("kind") == "template":
            job["package_ready"] = ppt_service._template_package_valid(job)
    return {"jobs": jobs, "has_more": has_more}


@router.get("/ppt-templates")
async def list_templates():
    """双源聚合（E2）：内置条目（builtin=True，随包源盘点）+ 用户库条目
    （builtin=False，可管理）。同 id 冲突时用户库优先（create 409 预检已拦
    双源同名，此处兜底防升级新增内置与历史自建撞名）。"""
    result: dict[str, list] = {k: [] for k in _KIND_DIRS}
    for kind in _KIND_DIRS:
        builtin_root = _builtin_root()
        entries: list[dict] = []
        if builtin_root:
            entries += [
                _template_entry(builtin_root, kind, tid,
                                meta if isinstance(meta, dict) else {}, builtin=True)
                for tid, meta in _read_index(builtin_root, kind).items()
            ]
        root = _templates_root()
        user_ids = set(_read_index(root, kind))
        entries += [
            _template_entry(root, kind, tid,
                            meta if isinstance(meta, dict) else {}, builtin=False)
            for tid, meta in _read_index(root, kind).items()
        ]
        # 用户库优先：同名内置条目让位（自建可管理的胜出，展示层不出现双条目）
        if user_ids:
            entries = [e for e in entries if not (e["builtin"] and e["id"] in user_ids)]
        result[kind] = entries
    return {"templates": result}


@router.delete("/ppt-templates/{kind}/{template_id}")
async def delete_template(kind: str, template_id: str):
    """删除用户库模板（目录 + 索引条目）。仅用户库可管理：内置源只读；
    E2 起删除目标恒为用户库（收录即进库，与创建任务产出一致）。

    6b 连带清理：同模板创建任务历史（kind='template' 行）——存在活跃任务
    （running/awaiting_input）时 409 拒绝（防「任务跑完收录重建同名模板」
    与删除竞态，提示先取消）；仅终态 → 逐行 delete_task（消息 + agent 状态 +
    tasks 行 + 托管工作区）+ delete_ppt，JobCard 不残留指向已删模板的孤儿记录。
    终态判定存在 TOCTOU 窗口（快照检查后 failed/paused 行可被用户 resume）:
    _do_delete 两阶段重查——先全量确认全部任务仍为可删态（部分清理比整体
    放弃更糟），再进入删除循环；有激活行则整体放弃返回 409（防删行+删工作
    区打断在飞 agent）；索引删条目持库锁（与收录/rescan 互斥）。"""
    if kind not in _KIND_DIRS:
        return JSONResponse(status_code=400, content={
            "error": "模板类型非法", "code": "INVALID_TEMPLATE_KIND"})
    bad = _validate_template_id(template_id)
    if bad:
        return bad
    # 内置同名保护：若随包源存在同名目录（历史自建撞名场景用户库优先胜出）
    # 也拒绝删除——删除只会造成「删了又回来」的理解负担（纵深防御）
    if _builtin_template_ws(kind, template_id) is not None:
        return JSONResponse(status_code=400, content={
            "error": "内置模板不可删除", "code": "BUILTIN_TEMPLATE"})
    root = _templates_root()
    kind_dir, index_name = _KIND_DIRS[kind]
    ws = root / kind_dir / template_id
    # 路径穿越终检：resolve 后必须仍位于对应 kind 目录之下
    if not str(ws.resolve()).startswith(str((root / kind_dir).resolve()) + os.sep):
        return JSONResponse(status_code=400, content={
            "error": "模板路径非法", "code": "INVALID_TEMPLATE_ID"})
    jobs = deps.db.list_ppts_by_template(kind, template_id)
    if any(j["status"] in ("running", "awaiting_input") for j in jobs):
        return JSONResponse(status_code=409, content={
            "error": "该模板存在进行中的创建任务，请先取消或等待完成",
            "code": "TEMPLATE_JOB_ACTIVE"})
    index = _read_index(root, kind)
    # 404 判定在任务历史检查之后：模板已不在但终态历史残留（手工改动/中断）
    # 时仍可借本次删除一并清掉（幂等修复），而非永远无入口
    if template_id not in index and not ws.is_dir() and not jobs:
        return JSONResponse(status_code=404, content={
            "error": "模板不存在", "code": "TEMPLATE_NOT_FOUND"})

    def _do_delete() -> bool:
        # 两阶段（P3-1）：先全量重查确认全部仍为可删态再动手——边查边删在
        # 后面行放弃时前面行已清，409 伴随部分清理不可重试收敛
        for job in jobs:
            cur = deps.db.get_ppt(job["id"])
            if cur and cur["status"] in ("running", "awaiting_input"):
                return False
        for job in jobs:
            # 终态任务无泵无 agent，db 层直接删（delete_task 含托管工作区 rmtree）
            deps.db.delete_task(job["task_id"])
            deps.db.delete_ppt(job["id"])
        if ws.is_dir():
            shutil.rmtree(ws)
        with ppt_service._TEMPLATE_LIB_LOCK:
            # 锁内重读索引（主线程快照可能已被并发写更新）：与收录/rescan
            # 的读-改-写互斥，防删掉的条目被旧快照写回复活
            cur_index = _read_index(root, kind)
            if template_id in cur_index:
                cur_index.pop(template_id)
                ppt_service._atomic_write_json(
                    root / kind_dir / index_name, cur_index)
        return True

    try:
        deleted = await asyncio.to_thread(_do_delete)
    except Exception as e:
        # 半清理状态比「删不掉」更糟：任务历史清理失败即整体失败，模板原样保留，
        # 用户重试即可（终态清理是幂等的）
        logger.error("PPT template delete failed: kind=%s id=%s",
                     kind, template_id, exc_info=True)
        return JSONResponse(status_code=500, content={
            "error": f"删除失败: {e}", "code": "TEMPLATE_DELETE_FAILED"})
    if not deleted:
        logger.info("PPT template delete aborted (job re-activated): kind=%s id=%s",
                    kind, template_id)
        return JSONResponse(status_code=409, content={
            "error": "该模板存在进行中的创建任务，请先取消或等待完成",
            "code": "TEMPLATE_JOB_ACTIVE"})
    logger.info("PPT template deleted: kind=%s id=%s jobs_purged=%d",
                kind, template_id, len(jobs))
    return {"ok": True, "purged_jobs": len(jobs)}


@router.get("/ppt-templates/{kind}/{template_id}/spec")
async def get_template_spec(kind: str, template_id: str):
    """模板规范文档（templates/design_spec.md 正文，剥离 frontmatter）。

    详情抽屉「模板规范」数据源：Brand/Style 纯 spec 型的唯一实质内容
    （色板/字体/语气/Logo 规则），Layout/Deck 的 Page Roster 亦在此
    （逐张解释页面原型的用途与槽位）。
    """
    if kind not in _KIND_DIRS:
        return JSONResponse(status_code=400, content={
            "error": "模板类型非法", "code": "INVALID_TEMPLATE_KIND"})
    bad = _validate_template_id(template_id)
    if bad:
        return bad
    # 源随条目切换（E2）：内置条目读随包源（只读），用户库条目读库根
    builtin_ws = _builtin_template_ws(kind, template_id)
    root = (_builtin_root() if builtin_ws is not None else _templates_root())
    spec_path = root / _KIND_DIRS[kind][0] / template_id / "templates" / "design_spec.md"
    # 路径穿越终检（与 delete 同口径）：resolve 后必须仍位于对应 kind 目录之下
    expected_root = (root / _KIND_DIRS[kind][0]).resolve()
    if not str(spec_path.resolve()).startswith(str(expected_root) + os.sep):
        return JSONResponse(status_code=400, content={
            "error": "模板路径非法", "code": "INVALID_TEMPLATE_ID"})
    if not spec_path.is_file():
        return JSONResponse(status_code=404, content={
            "error": "模板规范文档不存在", "code": "SPEC_NOT_FOUND"})
    text = await asyncio.to_thread(spec_path.read_text, encoding="utf-8")
    return {"spec": _strip_frontmatter(text)}


@router.post("/ppt-templates/rescan")
async def rescan_templates():
    """自扫用户库重建四类索引（E2：InkCube 自实现，不再调 register CLI——
    其根写死 skill 安装目录内，与库外置后用户库无关）。

    以目录树为权威（修复手工改动/收录中断导致的索引漂移）：
    <kind_dir>/<id>/templates/design_spec.md 存在才算完整包，frontmatter
    按 KIND_CONFIG 字段重建条目；残缺目录跳过不入索引（文件即状态）。"""
    root = _templates_root()

    def _scan_kind(kind: str) -> dict:
        kind_dir, index_name = _KIND_DIRS[kind]
        base = root / kind_dir
        index: dict = {}
        skipped: list[str] = []
        if base.is_dir():
            for ws in sorted(base.iterdir()):
                if not ws.is_dir():
                    continue
                spec = ws / "templates" / "design_spec.md"
                if not spec.is_file():
                    skipped.append(ws.name)
                    continue
                try:
                    fm = ppt_service._parse_spec_frontmatter(
                        spec.read_text(encoding="utf-8"))
                except OSError:
                    skipped.append(ws.name)
                    continue
                index[ws.name] = {
                    k: fm[k] for k in ppt_service._TEMPLATE_INDEX_FIELDS[kind]
                    if fm.get(k) not in (None, "")
                }
        # 持库锁内合并重读再写回（P2-1）：仅护写入动作不够——扫描在锁外，
        # 收录若在「扫描完→拿锁前」完成，其条目不在扫描快照里，整体覆盖
        # 写会把「目录在、索引丢」重新引入。合并规则：扫描快照优先（目录树
        # 权威，手工改动/损坏修复生效），快照缺失但目录仍存在的现存条目
        # 保留（= 扫描窗口内新收录/既有有效包），目录已消失的丢弃
        with ppt_service._TEMPLATE_LIB_LOCK:
            index = ppt_service._merge_rescan_index(base, index_name, index)
            ppt_service._atomic_write_json(base / index_name, index)
        return {"kind": kind, "ok": True, "count": len(index), "skipped": skipped}

    results = await asyncio.gather(*[asyncio.to_thread(_scan_kind, k) for k in _KIND_DIRS])
    total = sum(r["count"] for r in results)
    skipped = [s for r in results for s in r["skipped"]]
    logger.info("PPT templates rescan done: total=%d skipped=%s", total, skipped or "none")
    return {"results": list(results), "ok": True}
