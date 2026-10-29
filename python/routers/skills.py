"""Skills management router — install, list, delete third-party skills."""
import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

import deps
from paths import get_data_dir
from tools.proc_priority import hidden_window_kwargs

logger = logging.getLogger(__name__)

router = APIRouter()


class SkillInstallRequest(BaseModel):
    source: str  # local path or git URL
    source_type: str = "local"  # "local" or "git"


def _get_skills_dir() -> str:
    """Return the base directory for installed skills."""
    return os.path.join(get_data_dir(), "skills")


def _parse_skill_md_meta(skill_dir: Path) -> dict:
    """Parse SKILL.md frontmatter to extract name, description, etc."""
    md_path = skill_dir / "SKILL.md"
    if not md_path.exists():
        return {}
    try:
        from skills.parser import parse_skill_md
        parsed = parse_skill_md(str(md_path))
        fm = parsed.get("frontmatter", {})
        return {
            "name": fm.get("name", ""),
            "description": fm.get("description", ""),
            "compatibility": fm.get("compatibility", ""),
            "skill_type": "agent_skill",
        }
    except Exception:
        return {"name": skill_dir.name}


@router.get("/skills")
async def list_skills(
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """List all installed skills."""
    skills = deps.db.list_skills(limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(skills) > limit
    if has_more:
        skills = skills[:limit]
    # Normalize enabled / needs_confirm fields（归一化只作用于当前页行）
    for s in skills:
        s["enabled"] = bool(s.get("enabled", 1))
        s["needs_confirm"] = bool(s.get("needs_confirm", 0))
        s["permissions"] = []  # Agent Skills don't declare permissions
    total = deps.db.conn.execute(
        "SELECT COUNT(*) AS n FROM skills"
    ).fetchone()["n"]
    return {"skills": skills, "has_more": has_more, "total": total}


@router.post("/skills/install")
async def install_skill(req: SkillInstallRequest):
    """Install a skill from local path or git URL."""
    logger.info("Skill install request: source=%s, type=%s", req.source[:100], req.source_type)
    skills_dir = _get_skills_dir()
    os.makedirs(skills_dir, exist_ok=True)

    if req.source_type == "local":
        return await _install_local(req.source, skills_dir)
    elif req.source_type == "git":
        return await _install_git(req.source, skills_dir)
    else:
        return {"error": f"不支持的安装类型: {req.source_type}", "code": "INVALID_SOURCE_TYPE"}


async def _install_local(source_path: str, skills_dir: str) -> dict:
    """Install skill from a local directory path."""
    source = Path(source_path).resolve()
    if not source.is_dir():
        return {"error": f"路径不存在或不是目录: {source_path}", "code": "SOURCE_PATH_NOT_FOUND"}

    # Detect skill type: SKILL.md (Agent Skills standard)
    is_agent_skill = (source / "SKILL.md").exists()

    # If no skill manifest at root, check for subdirectory skills (collection)
    if not is_agent_skill:
        sub_skills = []
        for child in sorted(source.iterdir()):
            if child.is_dir() and not child.name.startswith((".", "_", "__")):
                if (child / "SKILL.md").exists():
                    sub_name = child.name
                    sub_meta = _parse_skill_md_meta(child)
                    sub_name = sub_meta.get("name", sub_name)
                    sub_skills.append(sub_name)
        if sub_skills:
            return {
                # 引导性复合返回：动态子列表无法静态 i18n，结构化字段供前端插值渲染（D1）
                "error": (
                    f"所选目录 `{source.name}` 是一个 Skill 集合目录，根目录没有 SKILL.md。\n\n"
                    f"发现以下子 Skill: {', '.join(f'`{s}`' for s in sub_skills)}\n\n"
                    f"请单独安装子目录，例如: `{source / sub_skills[0].replace('-', '_')}`"
                ),
                "code": "COLLECTION_DETECTED",
                "type": "collection_detected",
                "sub_skills": sub_skills,
                "dir": source.name,
                "example": str(source / sub_skills[0].replace('-', '_')),
            }
        return {"error": "未找到 SKILL.md，这不是有效的 Agent Skill", "code": "SKILL_MANIFEST_MISSING"}

    # Parse skill metadata
    meta = _parse_skill_md_meta(source)
    name = meta.get("name", source.name)
    description = meta.get("description", "")

    # Check for duplicate name
    existing = deps.db.list_skills()
    if any(s["name"] == name for s in existing):
        return {"error": f"已存在同名 Skill: {name}", "code": "SKILL_NAME_EXISTS"}

    # Copy to skills directory
    install_name = name.lower().replace(" ", "-")
    # Sanitize: remove any path traversal characters
    install_name = re.sub(r'[^a-z0-9_\-]', '', install_name)
    if not install_name:
        install_name = "unnamed-skill"
    install_path = os.path.join(skills_dir, install_name)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, shutil.copytree, str(source), install_path)

    # Agent Skills use task's venv, no separate venv needed

    # Save to DB
    try:
        skill = deps.db.create_skill(
            name=name,
            description=description,
            source_type="local",
            source_path=str(source),
            install_path=install_path,
        )
    except Exception:
        await loop.run_in_executor(None, shutil.rmtree, install_path, True)
        raise
    return {"skill": skill}


async def _install_git(git_url: str, skills_dir: str) -> dict:
    """Install skill from a git URL by cloning."""
    # Extract repo name for directory
    repo_name = git_url.rstrip("/").split("/")[-1].replace(".git", "")
    # Validate: reject path traversal characters
    if not repo_name or ".." in repo_name or "/" in repo_name or "\\" in repo_name:
        return {"error": "无效的 Git URL", "code": "INVALID_GIT_URL"}
    # Sanitize to safe characters
    repo_name = re.sub(r'[^a-zA-Z0-9_\-.]', '', repo_name)
    if not repo_name:
        return {"error": "无法从 Git URL 提取仓库名", "code": "GIT_REPO_NAME_FAILED"}
    install_path = os.path.join(skills_dir, repo_name)

    if os.path.exists(install_path):
        return {"error": f"已存在同名目录: {repo_name}，请先删除", "code": "SKILL_DIR_EXISTS"}

    loop = asyncio.get_event_loop()

    # Clone the repo
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", git_url, install_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **hidden_window_kwargs(),  # 无控制台父进程下 git clone 闪窗（2026-10-28）
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            return {"error": f"Git clone 失败: {stderr.decode(errors='replace')}", "code": "GIT_CLONE_FAILED"}
    except FileNotFoundError:
        return {"error": "未找到 git 命令，请确保已安装 Git", "code": "GIT_NOT_FOUND"}
    except asyncio.TimeoutError:
        await loop.run_in_executor(None, shutil.rmtree, install_path, True)
        return {"error": "Git clone 超时（60 秒）", "code": "GIT_CLONE_TIMEOUT"}

    # Parse metadata (Agent Skills format)
    is_agent_skill = (Path(install_path) / "SKILL.md").exists()
    if not is_agent_skill:
        await loop.run_in_executor(None, shutil.rmtree, install_path, True)
        return {"error": "Git 仓库中没有 SKILL.md，不是有效的 Agent Skill", "code": "SKILL_MANIFEST_MISSING"}

    meta = _parse_skill_md_meta(Path(install_path))
    name = meta.get("name", repo_name)
    description = meta.get("description", "")

    # Agent Skills use task's venv, no separate venv needed

    # Save to DB
    try:
        skill = deps.db.create_skill(
            name=name,
            description=description,
            source_type="git",
            source_path=git_url,
            install_path=install_path,
        )
    except Exception:
        await loop.run_in_executor(None, shutil.rmtree, install_path, True)
        raise
    return {"skill": skill}


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str):
    """Delete an installed skill."""
    logger.info("Skill delete request: skill_id=%s", skill_id)
    skills = deps.db.list_skills()
    skill = next((s for s in skills if s["id"] == skill_id), None)
    if not skill:
        return {"error": "Skill 不存在", "code": "SKILL_NOT_FOUND"}

    # 内置 skill（2.8 §9.1）：随应用种子安装，卸载无意义且下次启动会被重新种回；
    # 禁用同样禁止（2026-10-19：内置能力是应用功能基线，不允许用户关掉）
    if skill.get("source_type") == "builtin":
        return {"error": "内置 Skill 受保护，不可卸载或禁用", "code": "SKILL_BUILTIN_PROTECTED"}

    # Remove files
    install_path = skill.get("install_path", "")
    if install_path and os.path.isdir(install_path):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, shutil.rmtree, install_path, True)

    # Remove from DB
    deps.db.delete_skill(skill_id)
    return {"ok": True}


@router.post("/skills/{skill_id}/toggle")
async def toggle_skill(skill_id: str, body: dict):
    """Enable or disable a skill（内置 skill 受保护，不允许禁用）."""
    enabled = body.get("enabled", True)
    if not enabled:
        skill = next(
            (s for s in deps.db.list_skills() if s["id"] == skill_id), None)
        if skill and skill.get("source_type") == "builtin":
            return {"error": "内置 Skill 受保护，不可禁用", "code": "SKILL_BUILTIN_PROTECTED"}
    deps.db.toggle_skill(skill_id, enabled)
    return {"ok": True}


@router.post("/skills/{skill_id}/confirm")
async def confirm_skill(skill_id: str):
    """Mark a skill as confirmed (first-use acknowledgement done)."""
    ok = deps.db.confirm_skill(skill_id)
    if not ok:
        return {"error": "Skill 不存在", "code": "SKILL_NOT_FOUND"}
    return {"ok": True}


# ── Builtin tools metadata ──
# Static list of builtin tools registered in TaskAgent.
# Used by the frontend Skill Management page to show both builtin and external skills.

BUILTIN_TOOLS = [
    # icon 不在后端下发（图标属 UI 语言，前端 TOOL_ICONS 按 name 映射 lucide）
    {"name": "code_executor", "description": "执行 Python 代码"},
    {"name": "shell", "description": "Shell 命令执行"},
    {"name": "file_ops", "description": "文件操作（读写/替换/备份）"},
    {"name": "code_search", "description": "代码搜索（正则/文件过滤）"},
    {"name": "web_search", "description": "网络搜索（Tavily/智谱）"},
    {"name": "code_lint", "description": "代码静态检查"},
    {"name": "code_format", "description": "代码格式化"},
    {"name": "package_manager", "description": "pip 依赖管理"},
    {"name": "project_scaffold", "description": "项目骨架生成"},
    {"name": "knowledge_search", "description": "知识库检索"},
    {"name": "doc_generator", "description": "文档生成（PPTX/MD）"},
    {"name": "repo_map", "description": "项目结构概览"},
    {"name": "skill_runner", "description": "外部 Skill 执行器"},
    {"name": "git_ops", "description": "Git 版本控制操作"},
    {"name": "lsp_client", "description": "代码智能分析（类型检查/跳转定义/引用查找）"},
]


@router.get("/skills/builtin-tools")
async def list_builtin_tools():
    """Return the list of builtin tools (for the Skill Management page)."""
    return {"tools": BUILTIN_TOOLS}
