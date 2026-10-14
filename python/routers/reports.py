"""python/routers/reports.py — Report system API routes.

Template management (CRUD, copy, extract-sample, upgrade).
Report generation (background task + progress polling), retry, export (Word), content retrieval.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from readiness import ensure_ready
from errors import actionable
import activity_events
from paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


def get_db():
    """Get database instance."""
    try:
        import deps
        return deps.db
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not initialized")


def get_loader():
    """Return the process-wide TemplateLoader singleton (created at cold start).

    Templates are loaded once in main.py lifespan(). This lazily creates the
    loader only if accessed before startup completed (e.g. in tests).
    """
    import deps
    if deps.template_loader is None:
        from report_generator.loader import TemplateLoader
        deps.template_loader = TemplateLoader(db=get_db(), data_dir=get_data_dir())
    return deps.template_loader


# ── Template Models ──


class TemplateSaveRequest(BaseModel):
    """Request body for saving a custom template."""
    template_json: dict  # Full template JSON


class TemplateCopyRequest(BaseModel):
    """Request body for copying a preset template."""
    new_name: str


# ── Template API Routes ──


@router.get("/templates")
async def list_templates(
    project_type: Optional[str] = None,
    stage: Optional[str] = None,
):
    """Get all templates (preset + custom), optionally filtered by project_type and stage."""
    try:
        loader = get_loader()
        templates = loader.get_all_templates(project_type=project_type, stage=stage)

        # Return summary info (not full outline) for list view
        result = []
        for tpl in templates:
            outline = tpl.get("outline", [])
            chapter_count = len(outline)
            result.append({
                "template_id": tpl.get("template_id"),
                "template_name": tpl.get("template_name"),
                "version": tpl.get("version"),
                "project_types": tpl.get("project_types", []),
                "stage": tpl.get("stage"),
                "output_format": tpl.get("output_format", "docx"),
                "role_hint": tpl.get("role_hint"),
                "audience": tpl.get("audience"),
                "report_depth": tpl.get("report_depth", "full"),
                "chapter_count": chapter_count,
            })

        # Mark is_custom and merge updated_at based on DB info
        db_templates = get_db().list_templates()
        db_rows = {row["id"]: row for row in db_templates}
        for item in result:
            row = db_rows.get(item["template_id"])
            item["is_custom"] = bool(row and row.get("is_custom"))
            item["updated_at"] = row.get("updated_at") if row else None

        # Surface custom templates needing user upgrade/repair (never silently dropped)
        problems = loader.list_custom_problems()
        known = {item["template_id"] for item in result}
        for tpl_id, reason in problems.items():
            if tpl_id in known:
                continue
            row = db_rows.get(tpl_id)
            result.append({
                "template_id": tpl_id,
                "template_name": (row or {}).get("name") or tpl_id,
                "version": (row or {}).get("version"),
                "project_types": [],
                "stage": None,
                "output_format": "docx",
                "role_hint": None,
                "audience": None,
                "report_depth": "full",
                "chapter_count": 0,
                "is_custom": True,
                "updated_at": (row or {}).get("updated_at"),
                "upgrade_needed": True,
                "upgrade_error": reason,
            })
        for item in result:
            item.setdefault("upgrade_needed", False)
            item.setdefault("upgrade_error", None)

        return {"templates": result}
    except Exception as e:
        logger.error("Failed to list templates: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get full template details including outline."""
    try:
        loader = get_loader()
        template = loader.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
        return {"template": template}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get template %s: %s", template_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/templates")
async def save_template(req: TemplateSaveRequest):
    """Save a custom template (create or update)."""
    try:
        loader = get_loader()
        template = req.template_json

        # Ensure template_id exists
        if "template_id" not in template:
            template["template_id"] = f"custom_{uuid.uuid4().hex[:8]}"

        # Ensure custom_ prefix (prevent overwriting preset templates)
        if not template["template_id"].startswith("custom_"):
            return JSONResponse(
                status_code=400,
                content={"error": "预置模板不可修改，请复制为自定义模板后编辑。", "code": "TEMPLATE_PRESET_IMMUTABLE"},
            )

        template.setdefault("version", "1.0")

        result = loader.save_custom_template(template)
        if not result:
            raise HTTPException(status_code=400, detail="Template validation failed")

        return {"success": True, "template_id": result["template_id"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to save template: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


class TemplateUpdateRequest(BaseModel):
    """Request body for updating a custom template."""
    template_json: dict  # Full template JSON with updated fields


@router.put("/templates/{template_id}")
async def update_template(template_id: str, req: TemplateUpdateRequest):
    """Update a custom template. Preset templates cannot be modified."""
    try:
        if not template_id.startswith("custom_"):
            return JSONResponse(
                status_code=400,
                content={"error": "预置模板不可修改，请复制为自定义模板后编辑。", "code": "TEMPLATE_PRESET_IMMUTABLE"},
            )

        loader = get_loader()
        # Verify template exists
        existing = loader.get_template(template_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")

        template = req.template_json
        template["template_id"] = template_id  # Ensure ID is not changed
        template.setdefault("version", "1.0")

        result = loader.save_custom_template(template)
        if not result:
            raise HTTPException(status_code=400, detail="Template update failed")

        return {"success": True, "template_id": template_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update template %s: %s", template_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/templates/{template_id}/copy")
async def copy_template(template_id: str, req: TemplateCopyRequest):
    """Copy a preset template as a custom template."""
    try:
        loader = get_loader()

        new_id = f"custom_{uuid.uuid4().hex[:8]}"
        result = loader.copy_template(template_id, new_id, req.new_name)
        if not result:
            raise HTTPException(status_code=404, detail=f"Source template not found: {template_id}")

        return {"success": True, "template_id": new_id, "template": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to copy template: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/templates/{template_id}/upgrade")
async def upgrade_template(template_id: str):
    """Upgrade/repair a custom template to the current schema.

    Auto-heals when migration+validation pass; otherwise returns the
    best-effort migrated JSON plus validation errors for guided repair.
    """
    try:
        from report_generator.loader import migrate_template

        if not template_id.startswith("custom_"):
            return JSONResponse(status_code=400, content={"error": "预置模板无需升级。", "code": "TEMPLATE_PRESET_NO_UPGRADE"})

        loader = get_loader()
        row = get_db().get_template(template_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
        try:
            raw = json.loads(row["template_json"])
        except (json.JSONDecodeError, KeyError):
            raise HTTPException(status_code=400, detail="Cached template is corrupted.")

        migrated = migrate_template(raw)
        if migrated is None:
            return {"success": True, "upgraded": False, "migrated": None,
                    "errors": ["unsupported schema version"]}

        errors = loader.validate_template(migrated)
        if not errors:
            loader.save_custom_template(migrated)
            return {"success": True, "upgraded": True, "migrated": migrated, "errors": []}
        return {"success": True, "upgraded": False, "migrated": migrated, "errors": errors}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to upgrade template %s: %s", template_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    """Delete a custom template. Preset templates cannot be deleted."""
    try:
        loader = get_loader()
        success = loader.delete_custom_template(template_id)
        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete template: {template_id}. "
                       "It may be a preset template or not exist.",
            )
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete template: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/templates/extract-sample")
async def extract_template_from_sample(
    file: UploadFile = File(...),
):
    """Upload a Word/PDF sample and extract template structure using LLM.

    Returns a template JSON draft (not saved yet).
    User must confirm and save in the template editor.
    """
    try:
        # Validate file type
        filename = file.filename or "sample"
        ext = Path(filename).suffix.lower()
        if ext not in (".docx", ".doc", ".pdf"):
            return JSONResponse(
                status_code=400,
                content={"error": f"不支持的文件格式: {ext}。仅支持 Word (.docx) 和 PDF (.pdf)。", "code": "UNSUPPORTED_SAMPLE_FORMAT"},
            )

        # Save uploaded file temporarily
        data_dir = get_data_dir()
        temp_dir = Path(data_dir) / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"sample_{uuid.uuid4().hex[:8]}{ext}"

        content = await file.read()
        temp_path.write_bytes(content)

        try:
            # Get LLM client and settings
            db = get_db()
            llm_settings = db.get_settings()
            from llm.client import LLMClient
            llm_client = LLMClient(llm_settings)

            from report_generator.template_extractor import extract_template_from_sample as do_extract
            template = await do_extract(str(temp_path), llm_client, llm_settings)

            # Count outline chapters
            outline = template.get("outline", [])
            chapter_count = len(outline)

            return {
                "template": template,
                "chapter_count": chapter_count,
            }
        finally:
            # Clean up temp file
            if temp_path.exists():
                temp_path.unlink()

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to extract template from sample: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Report API Routes ──


@router.get("")
async def list_reports(project_id: str):
    """List reports for a project, enriched with template_name and stage."""
    try:
        db = get_db()
        reports = db.list_reports(project_id)
        # Enrich with template info. stage 存于 template_json 内部（DB 行顶层无此列），
        # 解析后回填；模板缺失/无 stage/JSON 损坏时归入前端 other（none）列。
        for r in reports:
            tmpl = db.get_template(r.get("template_id", ""))
            r["template_name"] = tmpl["name"] if tmpl else "Unknown"
            stage = None
            if tmpl:
                try:
                    stage = json.loads(tmpl["template_json"]).get("stage")
                except (json.JSONDecodeError, TypeError):
                    stage = None
            r["stage"] = stage if stage in ("before", "during", "after") else "none"
        return {"reports": reports}
    except Exception as e:
        logger.error("Failed to list reports: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{report_id}")
async def get_report(report_id: str):
    """Get report details."""
    try:
        db = get_db()
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

        # Get sections
        sections = db.list_report_sections(report_id)
        return {"report": report, "sections": sections}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get report %s: %s", report_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{report_id}")
async def delete_report(report_id: str):
    """Delete a report and clean up disk files."""
    try:
        db = get_db()
        report = db.get_report(report_id)
        if report:
            if report["status"] == "generating":
                # 生成中的报告后台任务持有文件句柄/DB 引用：删文件与 runner 写盘
                # 竞争、删行后事件落库 FK 失败，且 LLM 继续烧 token —— 拒绝删除
                # （崩溃后遗留的孤儿 generating 由启动扫描置 error，不会被此门误拦）
                return JSONResponse(
                    status_code=409,
                    content={"error": "报告正在生成中，请先暂停或等待完成后再删除", "code": "REPORT_GENERATING"},
                )
            content_path = report.get("content_path", "")
            if content_path and os.path.isdir(content_path):
                shutil.rmtree(content_path, ignore_errors=True)
                logger.info("Deleted report files: %s", content_path)
        db.delete_report(report_id)
        return {"success": True}
    except HTTPException:
        raise  # 409 拒绝门必须透传（否则被下方 except Exception 吞成 500，前端看不到「生成中」提示）
    except Exception as e:
        logger.error("Failed to delete report: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh-templates")
async def refresh_templates():
    """Force refresh template cache from disk.

    Useful after updating preset templates or importing custom ones.
    """
    try:
        loader = get_loader()
        result = loader.refresh()
        return {"success": True, **result}
    except Exception as e:
        logger.error("Failed to refresh templates: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Report Generation (Phase 3) ──


class GenerateRequest(BaseModel):
    """Request body for generating a report."""
    project_id: str
    template_id: str
    title: str
    variables: Optional[dict] = None
    base_project_ids: Optional[list[str]] = None
    output_format: Optional[str] = "docx"
    supplementary_notes: Optional[str] = None  # 用户补充说明，注入 global_requirements
    section_internet: Optional[dict] = None  # 章节级互联网开关 {section_id: bool}
    internet_enabled: bool = False  # 报告级互联网总开关（总闸）


@router.post("/generate")
async def generate_report(req: GenerateRequest, background_tasks: BackgroundTasks):
    """Start report generation as a background task.

    Returns {report_id} immediately. Frontend polls GET /reports/{id} for progress.
    """
    # 准入前置校验（0.4 §7.3）：缺 LLM/嵌入模型时结构化报错，避免向导走到一半才失败
    ensure_ready("report")
    try:
        db = get_db()
        loader = get_loader()

        # 并发限制检查
        settings = db.get_settings()
        max_concurrent = settings.get("report_max_concurrent", 2)
        active_count = db.count_active_reports()
        if active_count >= max_concurrent:
            return JSONResponse(
                status_code=429,
                content={"error": f"当前已有 {active_count} 个报告正在生成（上限 {max_concurrent}），请稍后再试。", "code": "REPORT_CONCURRENT_LIMIT"},
            )

        # 获取模板
        template = loader.get_template(req.template_id)
        if not template:
            raise HTTPException(status_code=404, detail=f"Template not found: {req.template_id}")

        # 创建报告记录
        report_id = str(uuid.uuid4())
        data_dir = get_data_dir()
        report_dir = os.path.join(data_dir, "reports", report_id)
        os.makedirs(report_dir, exist_ok=True)

        now = _now()
        output_fmt = req.output_format or "docx"
        variables_json = json.dumps(req.variables or {}, ensure_ascii=False)
        base_ids_json = json.dumps(req.base_project_ids or [], ensure_ascii=False)
        section_internet = req.section_internet or {}
        internet_enabled = 1 if req.internet_enabled else 0
        section_internet_json = json.dumps(section_internet, ensure_ascii=False)
        db.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title, output_format, status, progress, content_path, variables, base_project_ids, section_internet, internet_enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, req.project_id, req.template_id, req.title, output_fmt, "generating", 0, report_dir, variables_json, base_ids_json, section_internet_json, internet_enabled, now, now),
        )
        db.conn.commit()

        # 合并用户补充说明到模板 global_requirements
        if req.supplementary_notes:
            import copy
            template = copy.deepcopy(template)
            reqs = template.setdefault("global_requirements", [])
            reqs.append(req.supplementary_notes)

        # 启动后台生成任务
        background_tasks.add_task(
            _run_report_generation,
            report_id=report_id,
            template=template,
            variables=req.variables or {},
            project_id=req.project_id,
            data_dir=data_dir,
            report_dir=report_dir,
            base_project_ids=req.base_project_ids or [],
            section_internet=section_internet,
            internet_enabled=bool(req.internet_enabled),
        )

        return {"report_id": report_id, "status": "generating"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start report generation: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Export (Phase 4) ──


_FILENAME_INVALID_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _sanitize_filename(title: str) -> str:
    """报告标题 → 安全文件名主干（导出默认文件名，2.5 §1.3）。

    替换 Windows/macOS/Linux 通用非法字符，去首尾空白与点（Windows 目录
    结尾点非法），限长 80 字符；空标题回退「报告」。
    """
    name = _FILENAME_INVALID_RE.sub("_", str(title or "")).strip(" .")
    name = name[:80].strip(" .")
    return name or "报告"


class ExportReportRequest(BaseModel):
    """Request body for exporting a report as Word (2.5 §5 API 14)."""
    # portrait | landscape → reference-doc 样式模板选择（§1.3 / §3.7）
    orientation: str = "portrait"
    # 提供时渲染到指定路径（另存为形式）；缺省写产物目录默认位置（直接打开形式）
    target_path: Optional[str] = None


@router.post("/{report_id}/export")
async def export_report(report_id: str, req: Optional[ExportReportRequest] = None):
    """Export report as Word (docx).

    Returns the output file path.
    """
    try:
        db = get_db()
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

        req = req or ExportReportRequest()
        if req.orientation not in ("portrait", "landscape"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid orientation: {req.orientation} (must be portrait or landscape)"
            )

        content_path = report.get("content_path", "")
        if not content_path:
            raise HTTPException(status_code=400, detail="Report content not available")

        # 读取 Markdown 内容
        md_path = Path(content_path) / "content.md"
        if not md_path.exists():
            raise HTTPException(status_code=404, detail="Report content file not found")

        markdown_text = md_path.read_text(encoding="utf-8")

        from report_generator.renderer import render_markdown_to_docx
        if req.target_path:
            output_path = req.target_path  # 另存为：主进程 showSaveDialog 选定路径
        else:
            # 直接打开：产物目录默认位置（已存在则覆盖重渲染，§1.3）
            output_path = str(Path(content_path) / f"{_sanitize_filename(report['title'])}.docx")
        # 渲染含子进程链（N 图 × mermaid Electron + pandoc，最长分钟级）：
        # 必须下沉线程池，否则 async 端点同步阻塞事件循环，全部路由排队假死
        await asyncio.to_thread(
            render_markdown_to_docx,
            markdown_text, output_path,
            title=report["title"], orientation=req.orientation,
        )
        logger.info("Report %s exported (orientation=%s): %s",
                    report_id, req.orientation, output_path)
        return {"success": True, "format": "Word", "path": output_path}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to export report %s: %s", report_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Report Content (Phase 4) ──


@router.get("/{report_id}/content")
async def get_report_content(report_id: str):
    """Get report Markdown content and registry data."""
    try:
        db = get_db()
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

        content_path = report.get("content_path", "")
        if not content_path:
            return {"content": "", "registry": {}}

        # 读取 Markdown
        md_path = Path(content_path) / "content.md"
        content = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

        # 读取 Registry
        reg_path = Path(content_path) / "registry.json"
        registry_data = {}
        if reg_path.exists():
            registry_data = json.loads(reg_path.read_text(encoding="utf-8"))

        return {
            "content": content,
            "registry": registry_data,
            "report": report,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get report content %s: %s", report_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Pause (报告级，2.5 §3.1 修订) ──


@router.post("/{report_id}/pause")
async def pause_report(report_id: str):
    """请求暂停报告生成（报告级协作暂停，章节边界生效）。

    写 pause_requested 标志即返回 accepted；Runner 在章节边界消费（当前章节
    完成 → 落 checkpoint → status=paused）。不中断进行中的章节。
    非 generating 状态（done / error / paused）409 拒绝：暂停只对在跑的报告有意义。
    恢复走 retry(resume) 断点续跑，重新生成走 retry(restart)。
    """
    try:
        db = get_db()
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")
        if report["status"] != "generating":
            raise HTTPException(
                status_code=409,
                detail=f"Report is not generating (current: {report['status']})"
            )
        db.set_report_pause_requested(report_id, True)
        logger.info("Report %s pause requested (takes effect at section boundary)", report_id)
        return {"report_id": report_id, "status": "generating", "pause_requested": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to request pause for report %s: %s", report_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Retry & Logs ──


class RetryReportRequest(BaseModel):
    """Request body for retrying a failed report."""
    mode: str = "resume"  # "resume" (from checkpoint) | "restart" (from scratch)


@router.post("/{report_id}/retry")
async def retry_report(report_id: str, background_tasks: BackgroundTasks, req: Optional[RetryReportRequest] = None):
    """Retry a paused or failed report generation.

    mode=resume: Continue from checkpoint (default)
    mode=restart: Delete checkpoint and start fresh
    """
    # 事前准入（0.4 §7.3 第三层 v3.7）：重试入口与 generate 同等拦截；
    # 必须在 try 块外调用，避免 InkCubeError 被 except Exception → 500 吞掉结构化字段
    ensure_ready("report")
    try:
        db = get_db()
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

        if report["status"] not in ("error", "paused"):
            raise HTTPException(
                status_code=400,
                detail=f"Report is not in error/paused state (current: {report['status']})"
            )

        # 并发限制检查
        settings = db.get_settings()
        max_concurrent = settings.get("report_max_concurrent", 2)
        active_count = db.count_active_reports()
        if active_count >= max_concurrent:
            return JSONResponse(
                status_code=429,
                content={"error": f"当前已有 {active_count} 个报告正在生成（上限 {max_concurrent}），请稍后再试。", "code": "REPORT_CONCURRENT_LIMIT"},
            )

        mode = req.mode if req else "resume"
        if mode not in ("resume", "restart"):
            raise HTTPException(status_code=400, detail=f"Invalid retry mode: {mode}")

        # 模板与配置解析（排在状态变更前）：解析失败（模板 404 / JSON 损坏）时
        # 报告保持原稳态，不留「generating 假态」（无后台任务接管会永久卡死）
        loader = get_loader()
        template = loader.get_template(report["template_id"])
        if not template:
            raise HTTPException(status_code=404, detail=f"Template not found: {report['template_id']}")

        # 从 DB 恢复原始变量和知识库引用
        variables = {}
        if report.get("variables"):
            try:
                variables = json.loads(report["variables"])
            except (json.JSONDecodeError, TypeError):
                pass
        base_project_ids = []
        if report.get("base_project_ids"):
            try:
                base_project_ids = json.loads(report["base_project_ids"])
            except (json.JSONDecodeError, TypeError):
                pass

        # 从 DB 恢复互联网开关配置（供 retry 续跑时保持与首次生成一致）
        section_internet = {}
        if report.get("section_internet"):
            try:
                section_internet = json.loads(report["section_internet"])
            except (json.JSONDecodeError, TypeError):
                pass
        internet_enabled = bool(report.get("internet_enabled"))

        report_dir = report.get("content_path", "")

        if mode == "restart":
            # 删除 checkpoint 文件 + 清空章节行：全量重跑不应在 DB 留旧章节行
            # （UNIQUE(report_id, section_id) 之下 UPSERT 可依赖，但旧行的
            # created_at/word_count 语义已失真，重置更干净）
            ckpt_path = Path(report_dir) / "checkpoint.json"
            if ckpt_path.exists():
                ckpt_path.unlink()
            db.delete_report_sections(report_id)
            logger.info("Report %s restart: checkpoint deleted, sections reset", report_id)

        # 重置状态（前置解析全部成功后才落）。paused + resume 保留原进度（checkpoint
        # 已完成章节将跳过，进度随 section_done 携带的绝对完成数推进）；
        # restart / error 重生成从 0 计（全量重跑，原语义不变）。
        resume_progress = (
            int(report.get("progress") or 0)
            if (mode == "resume" and report["status"] == "paused")
            else 0
        )
        db.update_report_status(report_id, "generating", resume_progress)
        db.update_report_error(report_id, "")  # 清空错误信息
        # 清除遗留暂停请求标志（终态统一清标志不变量；防恢复后立即再暂停）
        db.set_report_pause_requested(report_id, False)

        # 启动后台任务
        data_dir = get_data_dir()
        background_tasks.add_task(
            _run_report_generation,
            report_id=report_id,
            template=template,
            variables=variables,
            project_id=report["project_id"],
            data_dir=data_dir,
            report_dir=report_dir,
            base_project_ids=base_project_ids,
            section_internet=section_internet,
            internet_enabled=internet_enabled,
        )

        return {"report_id": report_id, "status": "generating", "mode": mode}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to retry report %s: %s", report_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{report_id}/logs")
async def get_report_logs(report_id: str, limit: int = 100):
    """Get report generation logs (for progress tracking)."""
    try:
        db = get_db()
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

        logs = db.list_report_logs(report_id, limit=limit)
        return {"logs": logs, "report": report}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get report logs %s: %s", report_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Helper Functions ──


def _now() -> str:
    """Get current timestamp string."""
    from datetime import datetime
    return datetime.now().isoformat()


def _format_retrieval_summary(title: str, summary: dict) -> str:
    """section_done 检索摘要 → 报告日志文案（2.5 §3.8）。

    覆盖 query 数 / 各通道命中 / raw→cards / 充足性三字段 / 每轮补搜 queries，
    让用户在报告详情页直接看到检索行为全貌（B 层增强：UI 日志补检索明细）。
    """
    rounds = summary.get("rounds") or []
    level = summary.get("sufficiency_level") or "unknown"
    reason = str(summary.get("sufficiency_reason") or "").strip()
    parts = [
        f"章节「{title}」检索完成：queries={summary.get('query_count', 0)}, "
        f"cards={summary.get('card_count', 0)}, 充足性={level}"
    ]
    if reason:
        parts.append(f"（{reason}）")
    round_descs = []
    for r in rounds:
        stats = r.get("channel_stats") or {}
        stat_text = "/".join(f"{k}={v}" for k, v in stats.items()) or "无命中"
        desc = (
            f"第{r.get('round_no')}轮[{stat_text}] "
            f"raw={r.get('raw_count', 0)}(新{r.get('new_raw_count', 0)})"
        )
        if r.get("card_count") is not None:
            desc += f" cards={r['card_count']}"
        fu = r.get("follow_up_queries") or []
        if fu:
            desc += f" → 补搜:{'; '.join(fu)}"
        round_descs.append(desc)
    if round_descs:
        parts.append("；".join(round_descs))
    return "，".join(parts)


def _section_done_log(data: dict) -> tuple[str, str]:
    """section_done 事件 → (日志文案, 日志级别)。

    降级显式告知（0.4 §7.4 v3.6）：材料不足但章节以分析推理模式继续时，
    必须写 warning 级日志让用户在报告详情页显式可见，而非静默完成。
    """
    title = data.get("title", "")
    if data.get("generation_failed"):
        # 降级终态可观测（§3.9）：重试后仍失败落占位说明，error 级显式告知，
        # 不允许「日志写完成、正文缺内容」的静默不一致
        return (
            f"章节生成失败: {title}（多次重试后仍未通过，正文以占位说明呈现，"
            "可调整模板写作要求后重新生成）",
            "error",
        )
    if data.get("skipped"):
        return f"章节跳过: {title}（材料不足）", "info"
    if data.get("degraded"):
        return (
            f"章节降级: {title}（材料不足，已以分析推理模式生成，"
            "内容可能缺乏数据支撑，建议补充项目材料后重新生成）",
            "warning",
        )
    return f"章节完成: {title}", "info"


def _fail_report_terminal(db, report_id: str, error_msg: str) -> None:
    """报告失败终态统一落库（error 事件分支与 except 兜底分支共用，单一实现）。

    失败通知 detail 由 error_msg 直接传参（不回读 reports.error_message 列——
    旧 DB 钩子在 update_report_error 写入前读列恒为 NULL，detail 从未带出过
    错误信息，顺序 4 修复，见 activity_events.fail_report）。
    """
    db.update_report_status(report_id, "error")
    db.update_report_error(report_id, error_msg)
    db.set_report_pause_requested(report_id, False)  # 终态统一清暂停标志
    db.create_report_log(report_id, f"报告生成异常: {error_msg}", level="error")
    activity_events.fail_report(db, report_id, error_msg)


def _get_search_tool(db, data_dir: str):
    """Get knowledge search tool instance."""
    from knowledge.knowledge_search import KnowledgeSearchTool
    chroma_dir = os.path.join(data_dir, "chroma")
    return KnowledgeSearchTool(db=db, chroma_persist_dir=chroma_dir)


async def _run_report_generation(
    report_id: str,
    template: dict,
    variables: dict,
    project_id: str,
    data_dir: str,
    report_dir: str,
    base_project_ids: list[str],
    section_internet: dict,
    internet_enabled: bool = False,
):
    """Background task: run report generation and update DB progress."""
    from report_generator.runner import ReportRunner
    from llm.client import LLMClient

    db = get_db()
    llm_settings = db.get_settings()
    search_tool = _get_search_tool(db, data_dir)

    # Copy settings — reasoning effort comes from user settings (settings page 思考深度)
    report_llm_settings = dict(llm_settings)
    # 输出预算抬升（同 wiki/ingest 先例）：节正文目标 1200 字 + 引用标注，
    # reasoning 模型的思考 token 亦计入 max_tokens，全局默认（8192）有截断风险
    report_llm_settings["max_tokens"] = max(
        int(llm_settings.get("max_tokens") or 4096), 32000,
    )
    logger.info(
        "Report [%s]: reasoning_effort=%s, max_tokens=%s",
        report_id, report_llm_settings.get("reasoning_effort"), report_llm_settings.get("max_tokens"),
    )

    llm_client = LLMClient(report_llm_settings)

    # 写入开始日志
    db.create_report_log(report_id, "开始生成报告", level="info")

    runner = ReportRunner(
        template=template,
        variables=variables,
        project_id=project_id,
        search_tool=search_tool,
        llm_client=llm_client,
        llm_settings=report_llm_settings,
        output_dir=report_dir,
        base_project_ids=base_project_ids,
        db=db,
        section_internet=section_internet,
        internet_enabled=internet_enabled,
        # 报告级暂停探针（2.5 §3.1 修订）：章节边界轮询 DB 标志
        pause_check=lambda: db.get_report_pause_requested(report_id),
    )

    try:
        async for event in runner.run():
            if event.event_type == "section_start":
                # 章节开始日志
                db.create_report_log(
                    report_id,
                    f"开始生成章节: {event.data.get('title', '')}",
                    level="info",
                    section_id=event.data.get("section_id"),
                )

            elif event.event_type == "section_done":
                # completed 为 runner 维护的绝对完成数（含恢复续跑的既有进度）：
                # 直接采用保证 resume 后进度自断点单调推进，不因本地重新计数而回退
                completed = event.data.get("completed", 0)
                total = event.data.get("total", 1)
                # 章节阶段进度封顶 90%，收尾(finalizing)推进到 95%，完成 100%，保证单调递增
                progress = int(completed / max(total, 1) * 90)
                db.update_report_status(report_id, "generating", progress)

                # 保存章节到 DB
                section_record = db.create_report_section(
                    report_id=report_id,
                    section_id=event.data["section_id"],
                    title=event.data["title"],
                )
                db.update_section_content(
                    section_pk=section_record["id"],
                    content=runner.outputs.get(event.data["section_id"], ""),
                    status="done",
                )

                # 章节完成/跳过/降级日志（降级显式告知：0.4 §7.4 v3.6）
                log_msg, log_level = _section_done_log(event.data)
                db.create_report_log(
                    report_id,
                    log_msg,
                    level=log_level,
                    section_id=event.data.get("section_id"),
                )

                # 检索摘要（§3.8）：query 数 / 各通道命中 / raw→cards / 充足性 / 补搜轮次
                summary = event.data.get("retrieval_summary")
                if summary:
                    db.create_report_log(
                        report_id,
                        _format_retrieval_summary(event.data.get("title", ""), summary),
                        level="info",
                        section_id=event.data.get("section_id"),
                    )

            elif event.event_type == "progress":
                # 收尾阶段（finalizing）进度：写日志 + 推进到 95%
                db.create_report_log(
                    report_id,
                    event.data.get("message", "收尾处理中..."),
                    level="info",
                )
                db.update_report_status(report_id, "generating", 95)

            elif event.event_type == "paused":
                # 报告级暂停生效（2.5 §3.1 修订）：章节边界消费请求 → paused 稳态；
                # 恢复走 retry(resume) 断点续跑、重新生成走 retry(restart)
                completed = event.data.get("completed", 0)
                total = event.data.get("total", 1)
                db.update_report_status(report_id, "paused", int(completed / max(total, 1) * 90))
                db.set_report_pause_requested(report_id, False)  # 消费即清
                db.create_report_log(
                    report_id,
                    f"报告已暂停（已完成 {completed}/{total} 章节），可从断点恢复或重新生成",
                    level="warning",
                )
                activity_events.pause_report(db, report_id)  # 暂停通知（E2）

            elif event.event_type == "complete":
                db.update_report_status(report_id, "done", 100)
                # 收尾期到达的暂停请求随完成清除（终态统一清标志不变量）
                db.set_report_pause_requested(report_id, False)
                activity_events.complete_report(db, report_id)  # 终态事件显式声明（E2，顺序 4）
                # 3.0 二期完成闭环(§5.4):终态声明点旁直调;服务层自身
                # try/except 仅记日志不阻塞报告流
                import affairs_service
                affairs_service.on_report_completed(db, report_id)
                # 保存 token 统计
                token_usage = event.data.get("token_usage")
                if token_usage:
                    db.update_report_token_usage(report_id, token_usage)
                # 完成日志
                db.create_report_log(report_id, "报告生成完成", level="info")

            elif event.event_type == "warning":
                # 降级/配置提示（如 Tavily Key 缺失或无效，0.4 §7.3 v3.6）：
                # warning 级写入报告日志，用户在报告详情页显式可见
                db.create_report_log(
                    report_id,
                    event.data.get("message", ""),
                    level="warning",
                )

            elif event.event_type == "error":
                # 章节致命失败终态（runner yield error 后正常返回，本分支是
                # 唯一落库点；事件流之外的意外异常仍由下方 except 兜底）
                _fail_report_terminal(db, report_id, event.data.get("message", "未知错误"))

    except Exception as e:
        logger.exception("Report generation failed for %s: %s", report_id, e)
        _fail_report_terminal(db, report_id, actionable(e))
