"""python/routers/reports.py — Report system API routes.

Phase 2: Template management (CRUD, copy, extract-sample).
Phase 3: Report generation (SSE streaming), section regeneration.
Phase 4: Export (Word/PDF), content retrieval.
"""
import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
    """Get template loader instance."""
    from report_generator.loader import TemplateLoader
    return TemplateLoader(db=get_db(), data_dir=get_data_dir())


def _ensure_templates_loaded():
    """Ensure preset templates are loaded into DB cache.

    Called on first API access. Loads preset templates from resources/templates/
    if they haven't been loaded yet (checks manifest version).
    """
    db = get_db()
    loader = get_loader()

    # Check if templates are already cached
    cached = db.list_templates(is_custom=False)
    if cached:
        return  # Already loaded

    # Load preset templates
    loader.load_preset_templates()


# ── Template Models ──


class TemplateSaveRequest(BaseModel):
    """Request body for saving a custom template."""
    template_json: dict  # Full template JSON


class TemplateCopyRequest(BaseModel):
    """Request body for copying a preset template."""
    new_name: str


class ExtractSampleResponse(BaseModel):
    """Response for template extraction from sample."""
    template: dict
    heading_count: int
    outline_chapters: int


# ── Template API Routes ──


@router.get("/templates")
async def list_templates(
    project_type: Optional[str] = None,
    stage: Optional[str] = None,
):
    """Get all templates (preset + custom), optionally filtered by project_type and stage."""
    try:
        _ensure_templates_loaded()
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
                "output_format": tpl.get("output_format", "Word"),
                "role_hint": tpl.get("role_hint"),
                "audience": tpl.get("audience"),
                "report_depth": tpl.get("report_depth", "full"),
                "variables": tpl.get("variables", []),
                "chapter_count": chapter_count,
            })

        # Mark is_custom based on DB info
        db_templates = get_db().list_templates()
        custom_ids = {row["id"] for row in db_templates if row.get("is_custom")}
        for item in result:
            item["is_custom"] = item["template_id"] in custom_ids

        return {"templates": result}
    except Exception as e:
        logger.error("Failed to list templates: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get full template details including outline."""
    try:
        _ensure_templates_loaded()
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
        _ensure_templates_loaded()
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
    """Delete a report."""
    try:
        db = get_db()
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
    output_format: Optional[str] = "Word"
    supplementary_notes: Optional[str] = None  # 用户补充说明，注入 global_requirements


@router.post("/generate")
async def generate_report(req: GenerateRequest, background_tasks: BackgroundTasks):
    """Start report generation as a background task.

    Returns {report_id} immediately. Frontend polls GET /reports/{id} for progress.
    """
    try:
        db = get_db()
        _ensure_templates_loaded()
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
        output_fmt = req.output_format or "Word"
        db.conn.execute(
            "INSERT INTO reports (id, project_id, template_id, title, output_format, status, progress, content_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, req.project_id, req.template_id, req.title, output_fmt, "generating", 0, report_dir, now, now),
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
        )

        return {"report_id": report_id, "status": "generating"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start report generation: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Section Regeneration (Phase 4) ──


class RegenerateSectionRequest(BaseModel):
    """Request body for regenerating a section."""
    section_id: str
    prompt_hint: Optional[str] = None  # 补充说明


@router.post("/{report_id}/sections/{section_id}/regenerate")
async def regenerate_section(report_id: str, section_id: str, req: Optional[RegenerateSectionRequest] = None):
    """Regenerate a single section with SSE streaming.

    Returns SSE stream with section content when done.
    """
    try:
        db = get_db()
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")

        # 获取模板
        _ensure_templates_loaded()
        loader = get_loader()
        template = loader.get_template(report["template_id"])
        if not template:
            raise HTTPException(status_code=404, detail=f"Template not found: {report['template_id']}")

        # TODO: Implement single section regeneration
        # For now, return a placeholder
        raise HTTPException(status_code=501, detail="Section regeneration not yet implemented")

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to regenerate section: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Export (Phase 4) ──


class ExportRequest(BaseModel):
    """Request body for exporting a report."""
    format: str = "Word"  # "Word" | "PDF"


@router.post("/{report_id}/export")
async def export_report(report_id: str, req: Optional[ExportRequest] = None):
    """Export report as Word or PDF.

    Returns the file path and triggers download.
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
        fmt = req.format if req else "Word"
        data_dir = get_data_dir()

        if fmt == "Word":
            from report_generator.renderer import render_markdown_to_docx
            output_path = str(Path(content_path) / f"{report['title']}.docx")
            render_markdown_to_docx(markdown_text, output_path, title=report["title"])
            return {"success": True, "format": "Word", "path": output_path}

        elif fmt == "PDF":
            from report_generator.renderer import render_markdown_to_docx, render_docx_to_pdf
            docx_path = str(Path(content_path) / f"{report['title']}.docx")
            pdf_path = str(Path(content_path) / f"{report['title']}.pdf")
            render_markdown_to_docx(markdown_text, docx_path, title=report["title"])
            render_docx_to_pdf(docx_path, pdf_path)
            return {"success": True, "format": "PDF", "path": pdf_path}

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}")

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
            import shutil
            ckpt_path = Path(report_dir) / "checkpoint.json"
            if ckpt_path.exists():
                ckpt_path.unlink()
            logger.info("Report %s restart: checkpoint deleted", report_id)

        # 重置状态
        db.update_report_status(report_id, "generating", 0)
        db.update_report_error(report_id, "")  # 清空错误信息

        # 获取模板
        _ensure_templates_loaded()
        loader = get_loader()
        template = loader.get_template(report["template_id"])
        if not template:
            raise HTTPException(status_code=404, detail=f"Template not found: {report['template_id']}")

        # 启动后台任务
        data_dir = get_data_dir()
        background_tasks.add_task(
            _run_report_generation,
            report_id=report_id,
            template=template,
            variables={},  # TODO: 从 DB 恢复原始变量
            project_id=report["project_id"],
            data_dir=data_dir,
            report_dir=report_dir,
            base_project_ids=[],  # TODO: 从 DB 恢复
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
):
    """Background task: run report generation and update DB progress."""
    from report_generator.runner import ReportRunner
    from llm.client import LLMClient

    db = get_db()
    llm_settings = db.get_settings()
    search_tool = _get_search_tool(db, data_dir)
    llm_client = LLMClient(llm_settings)

    # 写入开始日志
    db.create_report_log(report_id, "开始生成报告", level="info")

    runner = ReportRunner(
        template=template,
        variables=variables,
        project_id=project_id,
        search_tool=search_tool,
        llm_client=llm_client,
        llm_settings=llm_settings,
        output_dir=report_dir,
        base_project_ids=base_project_ids,
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
                skipped = event.data.get("skipped", False)
                if not skipped:
                    section_count += 1
                total = event.data.get("total", 1)
                progress = int(section_count / total * 100)
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
                    word_count=event.data.get("word_count", 0),
                    status="done",
                )

                # 章节完成/跳过日志
                word_count = event.data.get("word_count", 0)
                if skipped:
                    log_msg = f"章节跳过: {event.data.get('title', '')}（材料不足）"
                else:
                    log_msg = f"章节完成: {event.data.get('title', '')} ({word_count} 字)"
                db.create_report_log(
                    report_id,
                    log_msg,
                    level="info",
                    section_id=event.data.get("section_id"),
                )

            elif event.event_type == "section_error":
                # 章节错误日志
                db.create_report_log(
                    report_id,
                    f"章节生成失败: {event.data.get('message', 'Unknown error')}",
                    level="error",
                    section_id=event.data.get("section_id"),
                )

            elif event.event_type == "complete":
                db.update_report_status(report_id, "done", 100)
                # 保存 token 统计
                token_usage = event.data.get("token_usage")
                if token_usage:
                    db.update_report_token_usage(report_id, token_usage)
                # 完成日志
                db.create_report_log(report_id, "报告生成完成", level="info")

            elif event.event_type == "error":
                # 错误已由 runner.run() 内部 yield error 事件处理，此处不再重复写入
                pass

    except Exception as e:
        logger.exception("Report generation failed for %s: %s", report_id, e)
        error_msg = str(e)
        db.update_report_status(report_id, "error")
        db.update_report_error(report_id, error_msg)
        db.create_report_log(report_id, f"报告生成异常: {error_msg}", level="error")
