"""python/routers/reports.py — Report system API routes.

Template management (CRUD, copy, extract-sample, upgrade).
Report generation (background task + progress polling), retry, export (Word), content retrieval.
"""
import json
import logging
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel

from readiness import ensure_ready
from errors import actionable
import activity_events

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


def get_db():
    """Get database instance."""
    try:
        import deps
        return deps.db
    except ImportError:
        raise HTTPException(status_code=500, detail="Database not initialized")


def get_data_dir() -> str:
    """Get data directory path."""
    if os.environ.get("INKCUBE_DATA_DIR"):
        return os.environ.get("INKCUBE_DATA_DIR")
    if getattr(sys, "frozen", False):
        from platformdirs import user_data_dir
        return os.path.join(user_data_dir("InkCube", "InkCube"), "data")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


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
            raise HTTPException(
                status_code=400,
                detail="预置模板不可修改，请复制为自定义模板后编辑。",
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
            raise HTTPException(
                status_code=400,
                detail="预置模板不可修改，请复制为自定义模板后编辑。",
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
            raise HTTPException(status_code=400, detail="预置模板无需升级。")

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
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: {ext}。仅支持 Word (.docx) 和 PDF (.pdf)。",
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
        # Enrich with template info
        for r in reports:
            tmpl = db.get_template(r.get("template_id", ""))
            r["template_name"] = tmpl["name"] if tmpl else "Unknown"
            r["stage"] = tmpl.get("stage", "report") if tmpl else "report"
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
            content_path = report.get("content_path", "")
            if content_path and os.path.isdir(content_path):
                shutil.rmtree(content_path, ignore_errors=True)
                logger.info("Deleted report files: %s", content_path)
        db.delete_report(report_id)
        return {"success": True}
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
            raise HTTPException(
                status_code=429,
                detail=f"当前已有 {active_count} 个报告正在生成（上限 {max_concurrent}），请稍后再试。"
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


@router.post("/{report_id}/export")
async def export_report(report_id: str):
    """Export report as Word (docx).

    Returns the output file path.
    """
    try:
        db = get_db()
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

        content_path = report.get("content_path", "")
        if not content_path:
            raise HTTPException(status_code=400, detail="Report content not available")

        # 读取 Markdown 内容
        md_path = Path(content_path) / "content.md"
        if not md_path.exists():
            raise HTTPException(status_code=404, detail="Report content file not found")

        markdown_text = md_path.read_text(encoding="utf-8")

        from report_generator.renderer import render_markdown_to_docx
        output_path = str(Path(content_path) / f"{report['title']}.docx")
        render_markdown_to_docx(markdown_text, output_path, title=report["title"])
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


# ── Retry & Logs ──


class RetryReportRequest(BaseModel):
    """Request body for retrying a failed report."""
    mode: str = "resume"  # "resume" (from checkpoint) | "restart" (from scratch)


@router.post("/{report_id}/retry")
async def retry_report(report_id: str, background_tasks: BackgroundTasks, req: Optional[RetryReportRequest] = None):
    """Retry a failed report generation.

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

        if report["status"] != "error":
            raise HTTPException(
                status_code=400,
                detail=f"Report is not in error state (current: {report['status']})"
            )

        # 并发限制检查
        settings = db.get_settings()
        max_concurrent = settings.get("report_max_concurrent", 2)
        active_count = db.count_active_reports()
        if active_count >= max_concurrent:
            raise HTTPException(
                status_code=429,
                detail=f"当前已有 {active_count} 个报告正在生成（上限 {max_concurrent}），请稍后再试。"
            )

        mode = req.mode if req else "resume"
        report_dir = report.get("content_path", "")

        if mode == "restart":
            # 删除 checkpoint 文件
            ckpt_path = Path(report_dir) / "checkpoint.json"
            if ckpt_path.exists():
                ckpt_path.unlink()
            logger.info("Report %s restart: checkpoint deleted", report_id)

        # 重置状态
        db.update_report_status(report_id, "generating", 0)
        db.update_report_error(report_id, "")  # 清空错误信息

        # 获取模板
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


def _section_done_log(data: dict) -> tuple[str, str]:
    """section_done 事件 → (日志文案, 日志级别)。

    降级显式告知（0.4 §7.4 v3.6）：材料不足但章节以分析推理模式继续时，
    必须写 warning 级日志让用户在报告详情页显式可见，而非静默完成。
    """
    title = data.get("title", "")
    if data.get("skipped"):
        return f"章节跳过: {title}（材料不足）", "info"
    if data.get("degraded"):
        return (
            f"章节降级: {title}（材料不足，已以分析推理模式生成，"
            "内容可能缺乏数据支撑，建议补充项目材料后重新生成）",
            "warning",
        )
    return f"章节完成: {title}", "info"


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
    )

    section_count = 0
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
                section_count += 1
                total = event.data.get("total", 1)
                # 章节阶段进度封顶 90%，收尾(finalizing)推进到 95%，完成 100%，保证单调递增
                progress = int(section_count / total * 90)
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

            elif event.event_type == "progress":
                # 收尾阶段（finalizing）进度：写日志 + 推进到 95%
                db.create_report_log(
                    report_id,
                    event.data.get("message", "收尾处理中..."),
                    level="info",
                )
                db.update_report_status(report_id, "generating", 95)

            elif event.event_type == "complete":
                db.update_report_status(report_id, "done", 100)
                activity_events.complete_report(db, report_id)  # 终态事件显式声明（E2，顺序 4）
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
                # 错误已由 runner.run() 内部 yield error 事件处理，此处不再重复写入
                pass

    except Exception as e:
        logger.exception("Report generation failed for %s: %s", report_id, e)
        error_msg = actionable(e)
        db.update_report_status(report_id, "error")
        db.update_report_error(report_id, error_msg)
        db.create_report_log(report_id, f"报告生成异常: {error_msg}", level="error")
        # 终态事件显式声明（E2，顺序 4）：error_msg 直接传参——旧 DB 钩子在
        # update_report_error 写入前读列恆为 NULL，失败通知 detail 从未带出过错误信息
        activity_events.fail_report(db, report_id, error_msg)
