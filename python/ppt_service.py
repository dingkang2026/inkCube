"""python/ppt_service.py — PPT 交付物服务（2.3 §6 数据流 / 2.3.1 §9 长任务桥）。

InkCube 对 ppt-master 的姿态 = 包内零修改 + 外部驱动：本服务创建 origin='ppt'
任务并以「泵」后台驱动 TaskAgent（事件已由 agent 持久化，泵只消费不转发），
同时按 2.3.1 §9.1 从 workspace 产物聚合进度（stage / 页数 / 门禁状态），
门禁回执出现时自动续跑（不使用官方 --wait-only 阻塞）。

进度信号全部只读 workspace 文件；本模块不解析 SVG/PPTX 内容。
门禁两阶段判定（2.3.1 §5.1 文件契约）：
    recommendations.stage1.json 存在且无 result.json          → 待 Stage 1
    result.json(stage1) + handoff + recommendations.stage2.json 且非 final → 待 Stage 2
    result.json(stage=final)                                  → 确认完成
门禁回执续跑为双驱动：泵伴随心跳（回合进行中）+ 详情页轮询（泵退出后接棒）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import activity_events
from readiness import ensure_ready
from tools.mermaid_bridge import cleanup_temp_dir, preprocess_mermaid

logger = logging.getLogger(__name__)

# 任务 workspace 内的 ppt-master 项目目录名（固定，前端/服务共享）
PROJECT_DIR_NAME = "ppt_project"
# 进度聚合周期（秒）：任务执行期的伴随协程，非全局轮询端点（0.4 §3）
_PROGRESS_INTERVAL_S = 3.0

_STAGES = ("intake", "strategy", "authoring", "export")


def _project_dir(workspace_path: str) -> Path:
    """仅写入端用：在任务工作区内建 ppt-master 项目目录。
    读取端（sync/finalize/cancel/delete/recover）必须直读行内 workspace_path，
    禁止二次套用——行内存的已是项目目录本身，双套曾致 deck 全链路读空。"""
    return Path(workspace_path) / PROJECT_DIR_NAME


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ── 素材就位（intake 前置：后端只放原始素材，import-sources 由 agent 执行）──

def _stage_materials(db, workspace: str, source_report_id: str | None,
                     image_ids: list[str]) -> dict:
    """把报告 md（mermaid 预重绘）与图片库选中图拷入 workspace/staging/。

    返回 {"report": 相对路径|None, "images": [相对路径], "image_count": int}。
    mermaid 块经现有图表执行器桥重绘为 PNG 并替换引用（2.3 §4.1）；
    桥不可用时降级保留原始 md（LLM 按 mermaid 源码创作示意）。
    """
    staging = Path(workspace) / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    info: dict = {"report": None, "images": [], "image_count": 0}

    if source_report_id:
        row = db.conn.execute(
            "SELECT title, content_path FROM reports WHERE id = ?",
            (source_report_id,),
        ).fetchone()
        if row and row["content_path"] and os.path.isfile(row["content_path"]):
            staged = staging / "report.md"
            shutil.copyfile(row["content_path"], staged)
            try:
                # PNG 持久化到 staging/mermaid（工作区内）：staged md 的引用随
                # 工作区长期有效；缺省临时目录模式会泄漏目录且清理后引用断裂
                processed, _tmp = preprocess_mermaid(staged, image_dir=staging / "mermaid")
                if processed and processed != staged:
                    shutil.move(str(processed), staged)
                cleanup_temp_dir(_tmp)
            except Exception:
                logger.warning("mermaid preprocess failed, keep raw md", exc_info=True)
            info["report"] = "staging/report.md"

    if image_ids:
        images_dir = staging / "images"
        images_dir.mkdir(exist_ok=True)
        placeholders = ",".join("?" for _ in image_ids)
        rows = db.conn.execute(
            f"SELECT id, local_path FROM images WHERE id IN ({placeholders})",
            list(image_ids),
        ).fetchall()
        by_id = {r["id"]: r["local_path"] for r in rows}
        for iid in image_ids:
            src = by_id.get(iid)
            if not src or not os.path.isfile(src):
                continue
            dest = images_dir / f"{iid}{os.path.splitext(src)[1] or '.png'}"
            shutil.copyfile(src, dest)
            info["images"].append(f"staging/images/{dest.name}")
        info["image_count"] = len(info["images"])
    return info


# 图片来源复合选择物化参数（2.3 §4.1 E2 定稿）：池上限；钉选优先占位，
# 超限部分按「报告 vs 图片 VLM 描述」向量相关性缩池
_IMAGE_POOL_CAP = 120


def _report_text(db, source_report_id: str | None) -> str | None:
    """报告正文前段（向量排序 query 语义源；无报告/不可读返回 None）。"""
    if not source_report_id:
        return None
    row = db.conn.execute(
        "SELECT content_path FROM reports WHERE id = ?",
        (source_report_id,),
    ).fetchone()
    if not row or not row["content_path"] or not os.path.isfile(row["content_path"]):
        return None
    try:
        with open(row["content_path"], "r", encoding="utf-8") as f:
            return f.read(4000)
    except OSError:
        return None


def _rank_by_report_figure_similarity(project_id: str, pool: list[str],
                                      report_text: str) -> list[str] | None:
    """figure 向量相关性排序（报告语义 vs 图片 VLM 描述，2.2 检索链路）。

    返回按相关性降序的图片 id（不在池内的自动被调用方过滤）；向量链路不可用
    （bge-m3 未下载 / fp16 NaN / 查询异常）返回 None，调用方降级 created_at 序。
    """
    try:
        import numpy as np
        import deps
        vs = deps.worker._get_vector_store()
        model = vs._get_embedding_model()
        result = model.encode([report_text[:2000]], batch_size=1, max_length=512)
        vec = np.asarray(result["dense_vecs"][0], dtype=np.float32)
        if not np.isfinite(vec).all():
            return None
        hits = vs.search_dense(project_id, vec.tolist(), top_k=len(pool),
                               where={"content_type": "figure"})
        ranked: list[str] = []
        for h in hits:
            iid = (h.get("metadata") or {}).get("image_id")
            if iid and iid not in ranked:
                ranked.append(iid)
        return ranked
    except Exception:
        logger.warning("figure similarity ranking unavailable, fallback to recency",
                       exc_info=True)
        return None


def _resolve_image_pool(db, project_id: str, selection,
                        report_text: str | None) -> list[str]:
    """图片来源复合选择物化（2.3 §4.1 E2）：钉选全收 + 范围圈池。

    selection 形如 {categories, min_width, pinned_ids}（None = 不用图）。
    圈池 = status=done + 分类多选（COALESCE 未分类）+ 最小宽度；池上限
    _IMAGE_POOL_CAP 张（钉选优先占位，范围池贡献 = 剩余额度）；超限按报告
    语义向量排序取 top-N，向量不可用降级 created_at 序。返回最终入池 id
    （钉选在前保持用户顺序）。
    """
    if selection is None:
        return []
    pinned = list(dict.fromkeys(p for p in (selection.pinned_ids or []) if p))
    cap_left = max(0, _IMAGE_POOL_CAP - len(pinned))

    clauses = ["f.project_id = ?", "i.status = 'done'"]
    params: list = [project_id]
    cats = [c.strip() for c in (selection.categories or []) if c.strip()]
    if cats:
        clauses.append("COALESCE(i.category, 'uncategorized') IN (%s)"
                       % ",".join("?" * len(cats)))
        params += cats
    if selection.min_width:
        clauses.append("COALESCE(i.width_px, 0) >= ?")
        params.append(selection.min_width)
    rows = db.conn.execute(
        "SELECT i.id FROM images i JOIN files f ON f.id = i.file_id "
        "WHERE %s ORDER BY i.created_at DESC" % " AND ".join(clauses),
        params,
    ).fetchall()
    pinned_set = set(pinned)
    pool = [r["id"] for r in rows if r["id"] not in pinned_set]
    if len(pool) <= cap_left:
        return pinned + pool
    ranked = _rank_by_report_figure_similarity(project_id, pool, report_text) \
        if report_text else None
    if ranked is None:
        return pinned + pool[:cap_left]
    pool_set = set(pool)
    head = [i for i in ranked if i in pool_set][:cap_left]
    head_set = set(head)
    # 未被 head 选中的池内图（无向量/超出相关性名次）按 created_at 序补尾兜底
    rest = [i for i in pool if i not in head_set]
    return pinned + head + rest[:max(0, cap_left - len(head))]


class TemplateRefError(ValueError):
    """向导模板选择引用非法（格式错/不存在/同 kind 重复）——创建前拦截。"""


# 向导选择标识：kind:id（kind 与 _TEMPLATE_KIND_DIRS 对齐；id 允许中文，
# 与用户库/内置 id 同域，禁冒号/空白防歧义）
_TEMPLATE_REF_RE = re.compile(r"^(brand|style|layout|deck):([^:\s]+)$")


def _builtin_templates_root() -> Path | None:
    """随包内置模板源根（seed 同源定位；未随包返回 None）。"""
    from skills.seed import builtin_skill_source
    src = builtin_skill_source("ppt-master")
    return Path(src) / "templates" if src else None


def _resolve_template_refs(refs: str) -> list[dict]:
    """向导模板选择解析（2.3 §4.1 向导选择器）：kind:id 串 → 显式根条目。

    每项按 用户库 → 内置源 定位模板包根（<root>/templates/design_spec.md 单
    kind 工作区契约，confirm_ui explicit 通道直接可用；内置包路径与 library
    注册根重合时 server 自动归并为 library 候选，显式选择即预选高亮）。
    格式非法 / 不存在 / 同 kind 重复 → TemplateRefError（创建前拦截，不让
    agent 跑到 Stage 1 才发现路径无效）。
    """
    roots: list[dict] = []
    seen_kinds: set[str] = set()
    user_root = _user_template_library_root()
    builtin_root = _builtin_templates_root()
    for raw in (refs or "").split(","):
        token = raw.strip()
        if not token:
            continue
        m = _TEMPLATE_REF_RE.match(token)
        if not m:
            raise TemplateRefError(f"模板选择格式非法：{token}")
        kind, tid = m.group(1), m.group(2)
        if kind in seen_kinds:
            raise TemplateRefError(f"模板选择重复类型：{kind}（每类至多一个）")
        candidates = [
            (user_root / _TEMPLATE_KIND_DIRS[kind] / tid, False),
            (builtin_root / _TEMPLATE_KIND_DIRS[kind] / tid
             if builtin_root else None, True),
        ]
        for root, builtin in candidates:
            if root is not None and root.is_dir():
                seen_kinds.add(kind)
                # 路径统一正斜杠：absolute root 由 agent 手写进 template_options.json
                # （严格 JSON 禁 \p 类非法转义，Windows 反斜杠路径必须转换）
                roots.append({"kind": kind, "id": tid,
                              "root": str(root).replace("\\", "/"), "builtin": builtin})
                break
        else:
            raise TemplateRefError(f"模板不存在或已删除：{token}")
    return roots


def _build_first_message(workspace: str, staging: dict, style_ref: str | None,
                         notes: str | None,
                         template_roots: list[dict] | None = None) -> str:
    """首条工作流指令（2.3 §4.1/§6.2：Generate Default 唯一管线 + 门禁纪律）。

    template_roots = _resolve_template_refs 结果（向导显式选择的模板包根）：
    以 explicit root 契约（2.3.1 §6 显式根表述）告知 agent 在 Stage 1 Step 3
    写 template_options.json 时写入 explicit_workspace_roots；不选则不出现
    本段，由内置候选自主决定（默认行为不变）。
    """
    project = f"{workspace}/{PROJECT_DIR_NAME}".replace("\\", "/")
    skill = _skill_install_dir() or "<ppt-master 安装目录>"
    lines = [
        "[InkCube PPT 任务 — Generate PPTX]",
        "请严格按 ppt-master 的 Generate PPTX 工作流（Default 全质量管线；禁止 quick 降级）执行。",
        f"项目目录：{project}（bash/命令路径请用此正斜杠形式）。",
        "ppt-master skill 安装目录：" + skill + "，其 scripts/ 下命令用它拼绝对路径调用。",
        "",
        "第一步（intake）：",
        "1. python <skill>/scripts/project_manager.py init <上述项目目录> --format ppt169",
    ]
    if staging.get("report"):
        lines.append(f"2. python <skill>/scripts/project_manager.py import-sources <项目目录> <workspace>/{staging['report']}")
    else:
        lines.append("2. 自由主题：以本任务标题为选题，直接进入 Stage 1 方案构思（无 sources 导入）")
    if staging["images"]:
        lines.append(f"3. python <skill>/scripts/project_manager.py import-sources <项目目录> "
                     f"<workspace>/staging/images/*（共 {staging['image_count']} 张项目图片库选中图，image_usage 含 provided）")
    step = 4
    if template_roots:
        lines.append(
            f"{step}. 显式模板选择（Stage 1 Step 3 产出 template_options.json 时，"
            "把下列模板工作区绝对路径逐项写入 explicit_workspace_roots，"
            "default_mode 固定 templates；用户仍可在确认页改选内置候选或自由设计）：")
        lines += [f"   - {t['kind']}:{t['id']} → {t['root']}"
                  f"（{'内置模板' if t['builtin'] else '用户库模板'}）"
                  for t in template_roots]
        step += 1
    if style_ref:
        lines.append(f"{step}. 用户风格意向：{style_ref}（作为 Stage 1 模板倾向参考，最终以确认页为准）")
        step += 1
    if notes:
        lines.append(f"{step}. 用户备注要求：{notes}")
    lines += [
        "",
        "门禁纪律（最重要，必须遵守）：",
        "- 启动确认服务：python <skill>/scripts/confirm_ui/server.py <项目目录> --daemon --no-browser --timeout 0",
        "- 写完 recommendations.stage1.json 后【立即结束你的回合】：不要用 --wait-only，"
        "不要轮询任何文件等待用户。系统检测到用户提交后会自动唤醒你并附回执说明。",
        "- Stage 2 同理：写完 recommendations.stage2.json 后立即结束回合。",
        "- 拿到 final 回执（result.json stage=final）后：先执行 "
        "python <skill>/scripts/confirm_ui/server.py <项目目录> --shutdown 释放端口，再继续后续阶段。",
        "",
        "导出要求：三命令串行（total_md_split 仅备注启用时 → finalize_svg → svg_to_pptx，"
        "建议附 --native-charts-and-tables），完成后报告 exports/ 下 PPTX 文件名并结束。",
    ]
    return "\n".join(lines)


def _skill_install_dir() -> str | None:
    """ppt-master 安装目录（seed 布局 <data_dir>/skills/ppt-master）。"""
    from paths import get_data_dir
    p = os.path.join(get_data_dir(), "skills", "ppt-master")
    return p if os.path.isdir(p) else None


# ── 模板工坊任务（2.3 §4.2，E2）──────────────────────────

# kind → library 子目录（与 register_template.py KIND_CONFIG / routers/ppt_templates 同源）
_TEMPLATE_KIND_DIRS = {"brand": "brands", "style": "styles",
                       "layout": "layouts", "deck": "decks"}
# Create Template brief 确认门标记（create-template.md Step 3：agent echo 最终
# brief 后独行发出）；服务检测到它 = agent 停下等确认（文字门，无文件契约）
_BRIEF_CONFIRMED_MARKER = "[TEMPLATE_BRIEF_CONFIRMED]"


def _user_template_library_root() -> Path:
    """用户模板库根（E2 库外置）：<data_dir>/ppt-library/templates/<kind_dir>/<id>/
    + 四个索引 JSON；纯目录文件即状态，索引与 ppt-master 同 schema（2.3 §5.3）。"""
    from paths import get_data_dir
    return Path(get_data_dir()) / "ppt-library" / "templates"


def _atomic_write_json(path: Path, data: dict) -> None:
    """索引原子写（tmp + os.replace）：收录/rescan/删除共用，防半写状态。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _template_project_spec(ppt: dict) -> Path | None:
    """project 形态 spec 路径（create-template.md：限定名
    templates/design_spec.<kind>.<id>.md，kind/id 须与 frontmatter 一致）。"""
    tid, kind = ppt.get("template_id"), ppt.get("template_kind")
    if not tid or kind not in _TEMPLATE_KIND_DIRS:
        return None
    return Path(ppt["workspace_path"]) / "templates" / f"design_spec.{kind}.{tid}.md"


def _template_package_valid(ppt: dict) -> bool:
    """工作区模板包校验（E2 完成判定基线）：限定名 spec 存在 + 结构完整
    （Layout/Deck 须有 SVG 页面原型；Brand/Style 纯 spec 型 spec 即全部）。"""
    spec = _template_project_spec(ppt)
    if not spec or not spec.is_file():
        return False
    if ppt.get("template_kind") in ("layout", "deck"):
        return any(spec.parent.glob("*.svg"))
    return True


def _parse_spec_frontmatter(text: str) -> dict:
    """design_spec.md frontmatter 扁平解析（InkCube 自实现，不依赖 PyYAML）。

    覆盖收录所需字段的两种形态：`key: value`（strip 引号）与 `key: [a, b]`
    流式数组、`key:` + `- item` 块数组；嵌套结构（如正文表格）不进入扫描。"""
    if not text.startswith("---"):
        return {}
    fm: dict = {}
    current_key: str | None = None
    for line in text.splitlines()[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if not stripped:
            continue
        if stripped.startswith("- ") and current_key:
            val = fm.get(current_key)
            if isinstance(val, list):
                val.append(stripped[2:].strip().strip("'\""))
            continue
        if ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        key, raw = key.strip(), raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            fm[key] = [v.strip().strip("'\"") for v in raw[1:-1].split(",") if v.strip()]
        elif raw:
            fm[key] = raw.strip("'\"")
        else:
            fm[key] = []
        current_key = key
    return fm


# 收录时索引条目字段（对齐 register_template.py KIND_CONFIG 的索引 schema）
_TEMPLATE_INDEX_FIELDS = {
    "brand": ("summary", "primary_color"),
    "style": ("summary", "keywords"),
    "layout": ("summary", "canvas_format", "page_count", "page_types"),
    "deck": ("summary", "canvas_format", "page_count", "primary_color"),
}


def _collect_template(ppt: dict) -> tuple[bool, str]:
    """模板包收录（E2 库外置核心步骤）：工作区包校验通过后拷入用户库 + 写索引。

    - 包结构归一：project 形态 spec（design_spec.<kind>.<id>.md）→ 库形态
      design_spec.md（与内置/随包包同构，spec/预览端点按库形态读取）；
      templates/ 整目录 + images/ icons/ exports/（存在才拷）。
    - 索引条目：从 spec frontmatter 按 kind 提取（KIND_CONFIG 字段），缺字段
      不写入（展示层已有 '—' 兑底）。
    - 异常返回 (False, 原因) 由调用方置 failed——包已产出但收录不成时
      用户无法自助推进，禁静默降级。
    """
    tid, kind = ppt.get("template_id"), ppt.get("template_kind")
    if not _template_package_valid(ppt):
        return False, "工作区模板包不完整（缺 design_spec 或页面原型）"
    project = Path(ppt["workspace_path"])
    dest = _user_template_library_root() / _TEMPLATE_KIND_DIRS[kind] / tid
    try:
        # 拷贝（幂等：先清残留再拷，重复收录不累积脏文件）
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(project / "templates", dest / "templates")
        spec_src = project / "templates" / f"design_spec.{kind}.{tid}.md"
        os.replace(dest / "templates" / spec_src.name, dest / "templates" / "design_spec.md")
        for sub in ("images", "icons", "exports"):
            if (project / sub).is_dir():
                shutil.copytree(project / sub, dest / sub)
        # 索引条目（frontmatter 按 kind 提取；损坏索引按空重建，收录条目是权威新增）
        fm = _parse_spec_frontmatter(spec_src.read_text(encoding="utf-8"))
        entry = {k: fm.get(k) for k in _TEMPLATE_INDEX_FIELDS[kind]
                 if fm.get(k) not in (None, "")}
        index_path = dest.parent / f"{kind}s_index.json"
        index: dict = {}
        if index_path.is_file():
            try:
                loaded = json.loads(index_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    index = loaded
            except ValueError:
                index = {}
        index[tid] = entry
        _atomic_write_json(index_path, index)
        logger.info("Template collected to user library: kind=%s id=%s", kind, tid)
        return True, ""
    except Exception as e:
        logger.error("Template collect failed: kind=%s id=%s", kind, tid, exc_info=True)
        return False, f"收录失败: {e}"


def _template_collected(ppt: dict) -> bool:
    """已收录判定：用户库 spec（库形态 design_spec.md）+ 索引条目双条件。"""
    tid, kind = ppt.get("template_id"), ppt.get("template_kind")
    if not tid or kind not in _TEMPLATE_KIND_DIRS:
        return False
    ws = _user_template_library_root() / _TEMPLATE_KIND_DIRS[kind] / tid
    if not (ws / "templates" / "design_spec.md").is_file():
        return False
    try:
        with open(ws.parent / f"{kind}s_index.json", "r", encoding="utf-8") as f:
            return tid in json.load(f)
    except (OSError, ValueError):
        return False


def _stage_ref_files(workspace: str, ref_paths: list[str]) -> list[str]:
    """把用户本机参考素材拷入 workspace/staging/refs/（Create Template 输入通道 A-D）。

    返回 staging 相对路径列表；重名加序号防覆盖。单个缺失/不可读文件跳过
    （混合素材批选场景下单个失效不应阻断整批），全部缺失由调用方空列表自然降级。"""
    refs_dir = Path(workspace) / "staging" / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    used: set[str] = set()
    for raw in ref_paths:
        src = Path(raw)
        if not src.is_file():
            continue
        name = src.name
        stem, suffix = os.path.splitext(name)
        n = 1
        while name in used:
            name = f"{stem}_{n}{suffix}"
            n += 1
        used.add(name)
        try:
            shutil.copyfile(src, refs_dir / name)
            staged.append(f"staging/refs/{name}")
        except OSError:
            logger.warning("stage ref failed: %s", src, exc_info=True)
    return staged


# ── 成品增强（2.3 §4.3，E3：native_enhance_pptx 工作流）──────────────

# init --project-dir 目标目录名（区别于 deck 的 ppt_project；ppt 行
# workspace_path 直接指向它，进度/终态判定不再套 _project_dir）
ENHANCE_PROJECT_DIR_NAME = "enhance_project"


def _enhance_project_dir(workspace: str) -> Path:
    return Path(workspace) / ENHANCE_PROJECT_DIR_NAME


def _stage_enhance_source(workspace: str, src: str | Path) -> str:
    """源 PPTX 拷入 workspace/staging/sources/（init 输入；重名加序号防覆盖）。"""
    src = Path(src)
    src_dir = Path(workspace) / "staging" / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)
    name = src.name
    stem, suffix = os.path.splitext(name)
    n = 1
    while (src_dir / name).exists():
        name = f"{stem}_{n}{suffix}"
        n += 1
    shutil.copyfile(src, src_dir / name)
    return f"staging/sources/{name}"


def _build_enhance_message(workspace: str, staged: str,
                           notes: str | None) -> str:
    """增强首条工作流指令（native-enhance-pptx.md 固定入口 + 两处确认门纪律）。"""
    project = _enhance_project_dir(workspace)
    skill = _skill_install_dir() or "<ppt-master 安装目录>"
    lines = [
        "[InkCube PPT 任务 — Enhance Native PPTX]",
        "请严格按 ppt-master 的 Enhance Native PPTX 工作流"
        "（workflows/native-enhance-pptx.md）执行：对既有 PPTX 直接 OOXML 补丁，"
        "不重生成页面。",
        f"ppt-master skill 安装目录：{skill}（scripts/ 在其下，命令用绝对路径调用）。",
        f"项目目录（init 时用 --project-dir 显式指定，路径用正斜杠形式）："
        f"{str(project).replace(chr(92), '/')}。",
        "第一步执行初始化：python \"" + skill
        + "/scripts/native_enhance_pptx.py\" init \""
        + f"{workspace}/{staged}".replace(chr(92), "/")
        + f"\" --name enhance --project-dir \"{str(project).replace(chr(92), '/')}\"。",
        f"源 PPTX（相对项目工作区）：{staged}。",
    ]
    if notes:
        lines.append(f"用户补充要求：{notes}")
    lines += [
        "",
        "执行契约（最重要，必须遵守）：",
        "- 禁止 SVG 管线：pptx_template_import / finalize_svg / svg_to_pptx "
        "均不得运行；产物 = exports/ 下新 PPTX，不得覆盖源文件。",
        "- init 完成后读 analysis/enhancement_plan.json，向用户逐项提出增强计划"
        "（notes / audio / timings / transitions 模块开关与推荐默认值）后"
        "【立即结束你的回合】等待确认；确认前不得生成 notes/audio 或打补丁。",
        "- 音频配置确认（仅用户确认启用配音后）：provider/音色/语速一次性提案，"
        "提案后立即结束回合等待确认，一条消息不要拆多轮。",
        "- 两处确认通过后：写 notes/00N.md（口播稿，忠实幻灯片不加戏）→ "
        "validate --materials notes → 音频生成 → validate → apply，"
        "exports/ 产出后报告文件名并结束回合。",
        "- 收到修改意见时：按意见更新提案重新确认，不要跳过确认直接执行。",
    ]
    return "\n".join(lines)


def _enhance_notes_coverage(project: Path) -> tuple[int, int]:
    """notes 覆盖（present, total）：slide_index 每页 note_file 存在且非空。"""
    idx = _read_json(project / "analysis" / "slide_index.json")
    slides = (idx or {}).get("slides") or []
    total = len(slides)
    present = 0
    for s in slides:
        rel = s.get("note_file") if isinstance(s, dict) else None
        if rel:
            p = project / rel
            try:
                if p.is_file() and p.stat().st_size > 0:
                    present += 1
            except OSError:
                continue
    return present, total


def _enhance_gate(project: Path) -> str | None:
    """增强确认门判定（产物状态即状态机，native-enhance-pptx.md §4-§7）。

    'plan' = enhancement_plan.json 仍为 draft 且 notes 未开始（模块计划门）；
    'audio' = notes 全覆盖且 audio 启用但 provider 未配置（音频配置门）；
    None = 无门禁（进行中 / 已导出 / 状态不可判定）。
    """
    plan = _read_json(project / "analysis" / "enhancement_plan.json")
    if not plan:
        return None
    present, total = _enhance_notes_coverage(project)
    if str(plan.get("status") or "").lower() != "confirmed" and present == 0:
        return "plan"
    audio = (plan.get("modules") or {}).get("audio") or {}
    pj = _read_json(project / "project.json") or {}
    provider = ((pj.get("audio") or {}).get("provider") or "").strip()
    if total > 0 and present >= total and audio.get("enabled") and not provider:
        return "audio"
    return None


def _enhance_exports(project: Path) -> list[Path]:
    exports = project / "exports"
    return sorted(exports.glob("*.pptx"), key=os.path.getmtime) \
        if exports.is_dir() else []


def _build_template_message(workspace: str, kind: str, template_id: str,
                            template_name: str, staged: list[str],
                            notes: str | None) -> str:
    """模板创建首条工作流指令（Create Template 固定入口 + 文字确认门纪律；
    E2：输出 scope 固定 project——模板包留在任务工作区，由系统收录进用户库）。"""
    project = f"{workspace}/{PROJECT_DIR_NAME}".replace("\\", "/")
    skill = _skill_install_dir() or "<ppt-master 安装目录>"
    lines = [
        "[InkCube PPT 任务 — Create Template]",
        "请严格按 ppt-master 的 Create Template 工作流（workflows/create-template.md 固定入口）执行，"
        "按素材与用户意图分发唯一子工作流。",
        f"模板类型：{kind}（brand/style/layout/deck，已定，不要重开类型选择）。",
        f"模板 id：{template_id}（project scope 的 spec 限定名组成，已定）。",
        f"显示名：{template_name}。",
        f"ppt-master skill 安装目录：{skill}（scripts/ 在其下，命令用绝对路径调用）。",
        f"项目目录（输出 scope 固定 project，即 <target_project>；"
        f"bash/命令路径请用此正斜杠形式）：{project}。",
        "第一步先初始化项目骨架：python \"" + skill
        + "/scripts/project_manager.py\" init \"" + project + "\" --format ppt169。",
    ]
    if staged:
        lines.append("参考素材已就位于工作目录下（类型可能混合，逐个分析）：")
        lines += [f"- {workspace}/{rel}".replace("\\", "/") for rel in staged]
    else:
        lines.append("无外部参考素材：按 Type E 通道处理，brief 全部字段由用户意图给出。")
    if notes:
        lines.append(f"用户补充要求：{notes}")
    lines += [
        "",
        "执行契约（最重要，必须遵守）：",
        "- 输出 scope 固定 project（<target_project> = 上述项目目录）：spec 写 "
        f"templates/design_spec.{kind}.{template_id}.md（限定名，kind/id 与 frontmatter 一致），"
        "不做 library scope。",
        "- 完成 Step 2 fact-based brief 提案与 Step 3 确认门（echo 最终 brief，"
        "并在其后另起一行发出标记 [TEMPLATE_BRIEF_CONFIRMED]）后【立即结束你的回合】，"
        "不要执行 Step 4 及之后任何步骤。系统检测到用户确认后会自动唤醒你。",
        "- 用户确认后继续：Step 4 子工作流 → Step 5 校验（python \"" + skill
        + "/scripts/svg_quality_checker.py\" \"" + project + "/templates\" --template-mode）"
        "→（多 Master 必做）Step 6 Review PPTX。",
        "- 禁止注册：不运行 register_template.py、不写任何全局发现索引；"
        "校验通过后报告模板包位置（templates/ 下 spec 与页面原型清单）并结束回合，"
        "系统会负责收录进用户模板库。",
    ]
    return "\n".join(lines)


# ── 门禁状态判定（2.3.1 §5.1 文件契约 / §9.1 信号源）──

def _gate_state(project: Path) -> dict:
    """返回 {pending: 'stage1'|'stage2'|None, confirmed_final: bool, receipt_stage1: bool}。"""
    cui = project / "confirm_ui"
    rec1 = (cui / "recommendations.stage1.json").is_file()
    result = _read_json(cui / "result.json") if (cui / "result.json").is_file() else None
    handoff = (cui / "template_handoff.json").is_file()
    rec2 = (cui / "recommendations.stage2.json").is_file()
    final = bool(result and result.get("stage") == "final")
    receipt_stage1 = result is not None and not final
    if final:
        pending = None
    elif rec1 and result is None:
        pending = "stage1"
    elif receipt_stage1 and handoff and rec2:
        pending = "stage2"
    else:
        pending = None
    return {"pending": pending, "confirmed_final": final,
            "receipt_stage1": receipt_stage1}


def confirm_ui_port(project_dir: Path) -> int | None:
    """读 confirm_ui 锁文件取服务端口（server 未启动/锁损坏返回 None）。"""
    lock = _read_json(project_dir / ".confirm_ui.lock")
    if lock and isinstance(lock.get("port"), int):
        return lock["port"]
    return None


def list_pages(project_dir: Path) -> list[str]:
    """创作页清单（svg_output/page_*.svg 相对路径，按页号升序；预览网格数据源）。"""
    svg_dir = project_dir / "svg_output"
    if not svg_dir.is_dir():
        return []
    def _page_no(p: Path) -> int:
        try:
            return int(p.stem.split("_", 1)[1])
        except (IndexError, ValueError):
            return 10**9  # 非标准命名沉底，保持稳定顺序
    return sorted(
        (str(p.relative_to(project_dir)).replace("\\", "/")
         for p in svg_dir.glob("page_*.svg")),
        key=lambda rel: _page_no(Path(rel)),
    )


def _shutdown_confirm_ui(venv_python: str, project: Path) -> None:
    """门禁结束兜底关停（官方幂等 CLI，2.3.1 §5.2；释放 5050 段端口）。"""
    server = Path(_skill_install_dir() or "") / "scripts" / "confirm_ui" / "server.py"
    if not venv_python or not server.is_file():
        return
    try:
        subprocess.run(
            [venv_python, str(server), str(project), "--shutdown"],
            capture_output=True, timeout=15,
        )
    except Exception:
        logger.warning("confirm_ui shutdown failed: %s", project, exc_info=True)


# ── 进度聚合（§9.1 信号映射）──

def _page_count_from_result(project: Path) -> int | None:
    result = _read_json(project / "confirm_ui" / "result.json") or {}
    raw = result.get("page_count")
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and "-" in raw:
        try:
            return int(raw.split("-")[1])
        except ValueError:
            return None
    return None


def sync_progress(db, ppt: dict) -> None:
    """按 workspace 产物推进 ppts（阶段/页数/门禁）。只读文件 + 幂等更新。"""
    if ppt.get("kind") == "template":
        _sync_template_progress(db, ppt)
        return
    if ppt.get("kind") == "enhanced":
        _sync_enhance_progress(db, ppt)
        return
    # 契约：deck 行 workspace_path 即项目目录本身（prepare() 写入端已套
    # _project_dir，读取端直读，曾因二次套用进度/门禁/终态全链路读空）
    project = Path(ppt["workspace_path"])
    if not project.is_dir():
        return

    gate = _gate_state(project)
    svg_dir = project / "svg_output"
    page_done = len(list(svg_dir.glob("page_*.svg"))) if svg_dir.is_dir() else 0
    exports = sorted((project / "exports").glob("*.pptx"), key=os.path.getmtime) \
        if (project / "exports").is_dir() else []

    if exports:
        stage = "export"
    elif page_done:
        stage = "authoring"
    elif (project / "design_spec.md").is_file() or (project / "spec_lock.md").is_file():
        stage = "authoring"
    elif gate["pending"] or gate["receipt_stage1"] or gate["confirmed_final"]:
        stage = "strategy"
    else:
        stage = "intake"

    page_count = _page_count_from_result(project)
    db.update_ppt_progress(
        ppt["id"], stage=stage,
        page_count=page_count,
        page_done=page_done or None,
    )

    # 门禁进入检测：agent 回合仍在跑时不打扰（泵收尾/心跳统一判定），
    # 此处只做「pending 且尚未标记」的置位 + 通知（未读查重幂等）
    cur = db.get_ppt(ppt["id"])
    if not cur:
        return
    if gate["pending"] and cur["status"] == "running" and not _task_running(cur["task_id"]):
        db.update_ppt_status(cur["id"], "awaiting_input")
        activity_events.gate_ppt(
            db, cur["id"],
            "等待你在确认页完成 " + ("Stage 1" if gate["pending"] == "stage1" else "Stage 2") + " 确认",
        )


def _task_running(task_id: str) -> bool:
    import deps
    agent = getattr(deps, "task_agent", None)
    return bool(agent and agent.is_task_running(task_id))


def _sync_template_progress(db, ppt: dict) -> None:
    """模板任务阶段推进（E2：产物在任务工作区，收录后入用户库）：
    intake（素材分析）→ authoring（模板包产出）→ export（已收录进用户库）。
    页级进度不适用（无规划页数）；brief 门等待态由 _finalize_template 置位。"""
    # brief 文字门等待态：门阶段工作区尚无模板包，产物推导
    # 会错误回落 intake 并覆写 finalize 置位的 strategy——门禁态短路
    if ppt["status"] == "awaiting_input":
        db.update_ppt_progress(ppt["id"], stage="strategy")
        return
    spec = _template_project_spec(ppt)
    if _template_collected(ppt):
        stage = "export"
    elif spec and (spec.is_file() or any(spec.parent.glob("*.svg"))):
        stage = "authoring"
    else:
        stage = "intake"
    db.update_ppt_progress(ppt["id"], stage=stage)


def _sync_enhance_progress(db, ppt: dict) -> None:
    """增强任务阶段推进（2.3 §4.3）：产物都在 enhance_project 下。
    intake（init 前）→ strategy（plan 草案）→ authoring（notes 产出）→
    export（exports 产物）。页级进度不适用；两处确认门由 _finalize_enhanced 置位。"""
    project = Path(ppt["workspace_path"])
    if _enhance_exports(project):
        stage = "export"
    elif _enhance_notes_coverage(project)[0]:
        stage = "authoring"
    elif (project / "analysis" / "enhancement_plan.json").is_file():
        stage = "strategy"
    else:
        stage = "intake"
    db.update_ppt_progress(ppt["id"], stage=stage)


def _last_assistant_text(db, task_id: str) -> str | None:
    """任务最后一条 assistant 消息文本（文字门标记检测源；无消息返回 None）。"""
    try:
        msgs = db.get_task_messages(task_id)
    except Exception:
        return None
    for msg in reversed(msgs):
        if msg.get("role") == "assistant":
            return msg.get("content") or ""
    return None


# ── 泵（后台驱动 agent + 伴随聚合 + 自动续跑）──

class PptService:
    """模块级单例（main.py lifespan 创建）；泵任务句柄按 ppt_id 记录。"""

    def __init__(self):
        self._pumps: dict[str, asyncio.Task] = {}

    # -- 创建 --

    def prepare(self, db, project_id: str, title: str, *,
                source_report_id: str | None = None,
                image_selection=None,
                style_ref: str | None = None,
                template_refs: str | None = None,
                notes: str | None = None) -> tuple[dict, str]:
        """建 task(origin='ppt') + ppts 行 + 素材就位 + 组首条消息。

        image_selection 为复合选择参数 {categories, min_width, pinned_ids}
        （2.3 §4.1 E2，None = 不用图），先物化为最终入池 id 再拷贝。
        template_refs = 向导模板选择标识串（"kind:id,..."，None = 不选，
        由 Stage 1 内置候选自主决定）：解析为显式根写入首条消息，
        ppts.style_ref 列存选择标识（拍板口径：kind:id 串或 legacy 文本）。
        纯同步重活（mermaid 重绘为 headless Electron 同步子进程，秒级×块数），
        由调用方放线程池执行；不启动泵——launch() 须在事件循环线程调用。
        """
        from paths import get_data_dir
        task = db.create_task(
            title=f"[PPT] {title}", data_dir=get_data_dir(), origin="ppt",
        )
        workspace, task_id = task["workspace_path"], task["id"]
        ppt: dict | None = None
        try:
            template_roots = (_resolve_template_refs(template_refs)
                              if template_refs else None)
            pool_ids = _resolve_image_pool(
                db, project_id, image_selection,
                _report_text(db, source_report_id))
            staging = _stage_materials(db, workspace, source_report_id, pool_ids)
            project = _project_dir(workspace)
            project.mkdir(parents=True, exist_ok=True)
            ppt = db.create_ppt(
                project_id=project_id, task_id=task_id, title=title,
                source_report_id=source_report_id, source_images=pool_ids,
                style_ref=template_refs or style_ref,
                workspace_path=str(project),
            )
            message = _build_first_message(
                workspace, staging, style_ref, notes,
                template_roots=template_roots)
        except Exception:
            # 创建原子性：素材就位/建行/首条消息任一失败即回滚已建资源——
            # origin='ppt' 任务不进任务 Tab，孤儿 task/ppt 行泄漏后完全不可见
            # （ppt 行 running 还会被 fetch_active 推入活动中心永久轮询）
            if ppt is not None:
                try:
                    db.delete_ppt(ppt["id"])
                except Exception:
                    logger.warning("PPT prepare rollback: delete_ppt failed task=%s",
                                   task_id, exc_info=True)
            try:
                db.delete_task(task_id)
            except Exception:
                logger.warning("PPT prepare rollback: delete_task failed task=%s",
                               task_id, exc_info=True)
            raise
        return db.get_ppt(ppt["id"]), message  # type: ignore[return-value]

    def prepare_template(self, db, *, kind: str, template_id: str,
                         template_name: str, ref_paths: list[str] | None = None,
                         notes: str | None = None) -> tuple[dict, str]:
        """模板创建任务（2.3 §4.2，E2）：origin='ppt' + ppts 行(kind='template')。

        全局资产不隶属项目：project_id 存空串（活动/通知层按 kind 分流 container）。
        参考素材拷入 workspace/staging/refs/；不启动泵——launch() 须在事件循环线程调用。
        """
        from paths import get_data_dir
        task = db.create_task(
            title=f"[PPT模板] {template_name}", data_dir=get_data_dir(), origin="ppt",
        )
        workspace, task_id = task["workspace_path"], task["id"]
        ppt: dict | None = None
        try:
            staged = _stage_ref_files(workspace, ref_paths or [])
            project = _project_dir(workspace)
            project.mkdir(parents=True, exist_ok=True)
            ppt = db.create_ppt(
                project_id="", task_id=task_id, title=template_name,
                kind="template", workspace_path=str(project),
                template_kind=kind, template_id=template_id,
            )
            message = _build_template_message(
                workspace, kind, template_id, template_name, staged, notes)
        except Exception:
            # 创建原子性：同 prepare——origin='ppt' 任务不进任务 Tab，孤儿行不可见
            if ppt is not None:
                try:
                    db.delete_ppt(ppt["id"])
                except Exception:
                    logger.warning("template prepare rollback: delete_ppt failed task=%s",
                                   task_id, exc_info=True)
            try:
                db.delete_task(task_id)
            except Exception:
                logger.warning("template prepare rollback: delete_task failed task=%s",
                               task_id, exc_info=True)
            raise
        return db.get_ppt(ppt["id"]), message  # type: ignore[return-value]

    def prepare_enhance(self, db, *, project_id: str, title: str,
                        source_pptx: str,
                        notes: str | None = None) -> tuple[dict, str]:
        """成品增强任务（2.3 §4.3，E3）：origin='ppt' + ppts 行(kind='enhanced')。

        source_pptx = 源 PPTX 绝对路径（项目内交付物或本机导入文件），拷入
        workspace/staging/sources/；enhance_project 由 init --project-dir 建于
        任务 workspace 下（workspace_path 直接指向它，不套 ppt_project）。
        纯同步轻活（单文件拷贝）；不启动泵——launch() 须在事件循环线程调用。
        """
        from paths import get_data_dir
        task = db.create_task(
            title=f"[PPT增强] {title}", data_dir=get_data_dir(), origin="ppt",
        )
        workspace, task_id = task["workspace_path"], task["id"]
        ppt: dict | None = None
        try:
            staged = _stage_enhance_source(workspace, source_pptx)
            project = _enhance_project_dir(workspace)
            project.mkdir(parents=True, exist_ok=True)
            ppt = db.create_ppt(
                project_id=project_id, task_id=task_id, title=title,
                kind="enhanced", workspace_path=str(project),
                enhance_source_path=staged,
            )
            message = _build_enhance_message(workspace, staged, notes)
        except Exception:
            # 创建原子性：同 prepare/prepare_template——origin='ppt' 任务不进
            # 任务 Tab，孤儿行不可见（running 行还会被 fetch_active 永久轮询）
            if ppt is not None:
                try:
                    db.delete_ppt(ppt["id"])
                except Exception:
                    logger.warning("enhance prepare rollback: delete_ppt failed task=%s",
                                   task_id, exc_info=True)
            try:
                db.delete_task(task_id)
            except Exception:
                logger.warning("enhance prepare rollback: delete_task failed task=%s",
                               task_id, exc_info=True)
            raise
        return db.get_ppt(ppt["id"]), message  # type: ignore[return-value]

    def launch(self, db, ppt: dict, message: str) -> dict:
        """启动 agent 回合（事件循环线程；create 路由的 to_thread 段之后调用）。"""
        self._start_drive(db, ppt, message)
        return db.get_ppt(ppt["id"])  # type: ignore[return-value]

    # -- 泵驱动 --

    def _start_drive(self, db, ppt: dict, message: str) -> None:
        """启动一次 agent 回合（前置原子预留；成功后泵在后台消费事件）。"""
        import deps
        task_id = ppt["task_id"]
        if task_id in getattr(deps.task_agent, "_running_task_ids", set()):
            raise RuntimeError("TASK_RUNNING")
        if not deps.task_agent.try_acquire_task(task_id):
            raise RuntimeError("TASK_LIMIT_REACHED")
        # try_acquire 预留后 run() 内部再 add 是幂等的；泵失败路径要释放，
        # 交给 run() finally（只要 run 真正启动）。启动前异常需手动释放：
        # 顺序即不变量：可能失败的 DB 写在前（消息落库→置 running），泵创建
        # （内存操作）垫底——任一 DB 写失败时行尚未 running，不会出现
        # 「running 行无泵」僵尸（launch 失败回滚据此统一处置 failed）
        try:
            db.save_task_message(task_id, "user", message)
            db.update_ppt_status(ppt["id"], "running")
            pump = asyncio.create_task(
                self._pump(db, ppt["id"], task_id, message))
            self._pumps[ppt["id"]] = pump
        except Exception:
            deps.task_agent._running_task_ids.discard(task_id)
            raise

    async def _pump(self, db, ppt_id: str, task_id: str, _message: str) -> None:
        import deps
        loop = asyncio.get_running_loop()
        progress = loop.create_task(self._progress_loop(db, ppt_id))
        try:
            ppt = db.get_ppt(ppt_id)
            if not ppt:
                raise RuntimeError("ppt row missing at pump start")
            async for _event in deps.task_agent.run(task_id, _message):
                pass  # 事件已由 agent 落库；SSE 消费面是详情页消息轮询
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("PPT pump failed: ppt=%s error=%s", ppt_id, e, exc_info=True)
            # run() 未启动时（如启动窗口与 delete 竞态）无人释放并发槽，此处兜住
            deps.task_agent._running_task_ids.discard(task_id)
            db.update_ppt_status(ppt_id, "failed", error=str(e))
            activity_events.fail_ppt(db, ppt_id, str(e))
        finally:
            progress.cancel()
            self._pumps.pop(ppt_id, None)
            try:
                self._finalize(db, ppt_id)
            except Exception:
                logger.warning("PPT finalize failed: ppt=%s", ppt_id, exc_info=True)

    async def _progress_loop(self, db, ppt_id: str) -> None:
        while True:
            await asyncio.sleep(_PROGRESS_INTERVAL_S)
            ppt = db.get_ppt(ppt_id)
            if not ppt:
                return
            try:
                sync_progress(db, ppt)
                self.auto_resume_if_receipt(db, ppt)
            except Exception:
                logger.warning("PPT progress sync failed: ppt=%s", ppt_id, exc_info=True)

    def auto_resume_if_receipt(self, db, ppt: dict) -> None:
        """awaiting_input 状态下检测到确认回执 → 自动续跑（2.3 §3.4 不挂 wait）。

        驱动者：泵伴随心跳（回合进行中）与 get_ppt 轮询（泵退出后接棒，
        C1 修复——回执落盘晚于泵退出时由详情页 3s 轮询接手）。
        acquire 失败仅降级日志，由下一次调用重试（幂等安全）。
        """
        cur = db.get_ppt(ppt["id"])
        if not cur or cur["status"] != "awaiting_input":
            return
        project = Path(cur["workspace_path"])
        gate = _gate_state(project)
        if not (gate["receipt_stage1"] or gate["confirmed_final"]):
            return
        if gate["pending"]:
            return  # 已进入下一阶段等待（如 stage2 文件已齐）
        message = self._resume_message(cur, gate)
        try:
            self._start_drive(db, cur, message)
        except RuntimeError as e:
            logger.info("PPT auto-resume deferred: ppt=%s (%s)", cur["id"], e)

    def _resume_message(self, ppt: dict, gate: dict) -> str:
        skill = _skill_install_dir() or "<ppt-master 安装目录>"
        project = str(ppt["workspace_path"]).replace("\\", "/")
        if gate["confirmed_final"]:
            return (
                "[系统] 用户已完成 Stage 2 最终确认（confirm_ui/result.json stage=final，"
                "这是后续所有阶段的唯一输入契约）。请先执行 "
                f"python \"{skill}/scripts/confirm_ui/server.py\" \"{project}\" --shutdown 释放确认服务端口，"
                "然后按回执继续：产出 design_spec.md 与 spec_lock.md，逐页创作 SVG 至 svg_output/，"
                "最后完成导出三连并报告 exports/ 下 PPTX 文件名。"
            )
        return (
            "[系统] 用户已在确认页完成 Stage 1 确认（回执见 confirm_ui/result.json，"
            "模板选择见 template_selection.json）。请继续：按回执安装所选模板"
            "（如为模板模式）→ 运行 python "
            f"\"{skill}/scripts/confirm_ui/server.py\" \"{project}\" --complete-template-selection "
            "写 handoff → 产出 recommendations.stage2.json（完整方案 + 生产机制）后立即结束回合等待用户确认。"
        )

    def _finalize(self, db, ppt_id: str) -> None:
        """回合结束终态判定：completed / awaiting（自动续跑已接手）/ paused。"""
        import deps
        ppt = db.get_ppt(ppt_id)
        if not ppt or ppt["status"] in ("completed", "failed", "cancelled"):
            return
        if ppt.get("kind") == "template":
            self._finalize_template(db, ppt)
            return
        if ppt.get("kind") == "enhanced":
            self._finalize_enhanced(db, ppt)
            return
        project = Path(ppt["workspace_path"])
        gate = _gate_state(project)
        sync_progress(db, ppt)

        exports = sorted((project / "exports").glob("*.pptx"), key=os.path.getmtime) \
            if (project / "exports").is_dir() else []
        if exports:
            rel = str(exports[-1].relative_to(project)).replace("\\", "/")
            db.update_ppt_status(ppt_id, "completed", export_path=rel)
            activity_events.complete_ppt(db, ppt_id, rel)
            return
        cur = db.get_ppt(ppt_id)
        if not cur:
            return
        if cur["status"] == "awaiting_input":
            return  # 门禁置位已发通知；回执续跑由心跳/详情页轮询接手
        if cur["status"] == "paused":
            return  # 用户主动暂停已声明（弹出即读后重复声明会成第二条噪音）
        if gate["receipt_stage1"] or gate["confirmed_final"]:
            # 回执早于回合收尾落盘（pending 已清空，sync_progress 不置位）：
            # 补置 awaiting + 通知后立即续跑；并发满时留给轮询重试
            db.update_ppt_status(ppt_id, "awaiting_input")
            activity_events.gate_ppt(db, ppt_id, "检测到确认回执，继续执行")
            self.auto_resume_if_receipt(db, cur)
            return
        # 迭代耗尽 / agent 正常收尾但未导出：可恢复暂停态
        db.update_ppt_status(ppt_id, "paused")
        activity_events.pause_ppt(
            db, ppt_id, "本回合已结束但尚未导出，可在详情页继续")
        task = deps.db.get_task(ppt["task_id"]) if hasattr(deps, "db") else None
        if task:
            logger.info("PPT round ended without export: ppt=%s task=%s", ppt_id, task["id"])

    def _finalize_template(self, db, ppt: dict) -> None:
        """模板任务回合结束终态（E2）：工作区包就绪 → 收录（成功 completed /
        失败 failed 带原因）；brief 文字门 → awaiting；否则 paused。

        文字门无文件契约（区别于 Generate 的 confirm_ui）：检测最后一条 assistant
        消息的 [TEMPLATE_BRIEF_CONFIRMED] 标记；确认后续跑由用户 answer 驱动。"""
        ppt_id = ppt["id"]
        if _template_package_valid(ppt):
            ok, reason = _collect_template(ppt)
            if ok:
                db.update_ppt_status(ppt_id, "completed")
                activity_events.complete_ppt(db, ppt_id, ppt.get("template_id") or "")
            else:
                # 包已产出但收录不成（IO/索引写坏）：用户无法自助推进，置 failed 显式暴露
                db.update_ppt_status(ppt_id, "failed", error=reason)
                activity_events.fail_ppt(db, ppt_id, reason)
            return
        last = _last_assistant_text(db, ppt["task_id"])
        if last and _BRIEF_CONFIRMED_MARKER in last:
            db.update_ppt_progress(ppt_id, stage="strategy")
            db.update_ppt_status(ppt_id, "awaiting_input")
            activity_events.gate_ppt(db, ppt_id, "模板 brief 已就绪，等待你确认")
            return
        db.update_ppt_status(ppt_id, "paused")
        activity_events.pause_ppt(
            db, ppt_id, "本回合已结束但模板包尚未产出，可在模板分区继续")

    def _finalize_enhanced(self, db, ppt: dict) -> None:
        """增强任务回合结束终态：completed（exports 产物）/ awaiting（两处确认门）
        / paused。产物状态机判定（_enhance_gate），不依赖 agent 消息标记。"""
        ppt_id = ppt["id"]
        project = Path(ppt["workspace_path"])
        exports = _enhance_exports(project)
        if exports:
            rel = str(exports[-1].relative_to(project)).replace("\\", "/")
            db.update_ppt_status(ppt_id, "completed", export_path=rel)
            activity_events.complete_ppt(db, ppt_id, rel)
            return
        gate = _enhance_gate(project)
        if gate == "plan":
            db.update_ppt_progress(ppt_id, stage="strategy")
            db.update_ppt_status(ppt_id, "awaiting_input")
            activity_events.gate_ppt(db, ppt_id, "增强计划已就绪，等待你确认")
            return
        if gate == "audio":
            db.update_ppt_progress(ppt_id, stage="authoring")
            db.update_ppt_status(ppt_id, "awaiting_input")
            activity_events.gate_ppt(db, ppt_id, "音频配置已就绪，等待你确认")
            return
        db.update_ppt_status(ppt_id, "paused")
        activity_events.pause_ppt(
            db, ppt_id, "本回合已结束但尚未导出，可在详情页继续")

    # -- 交互操作 --

    def answer(self, db, ppt_id: str, text: str | None = None) -> dict:
        """门禁应答（手动触发续跑；自动续跑已覆盖常规路径，此处兜底/补充意见）。"""
        ppt = db.get_ppt(ppt_id)
        if not ppt:
            return {"error": "PPT 不存在", "code": "PPT_NOT_FOUND"}
        if ppt["status"] in ("completed", "failed", "cancelled"):
            return {"error": "PPT 已结束，无法继续", "code": "PPT_FINISHED"}
        if _task_running(ppt["task_id"]):
            return {"error": "任务正在执行中", "code": "TASK_RUNNING"}
        if ppt.get("kind") == "template":
            # brief 文字门应答：空文本 = 确认；非空 = 修改意见（agent 重新提案再等确认）
            if text:
                message = ("[系统] 用户对模板 brief 提出修改意见：" + text
                           + "。请按意见更新提案，重新走 Step 3 确认门（echo 最终 brief "
                             "并在其后另起一行发出 [TEMPLATE_BRIEF_CONFIRMED]）后"
                             "再次结束回合等待确认。")
            else:
                message = ("[系统] 用户已确认模板 brief。请继续执行 Step 4 及后续步骤："
                           "子工作流 → Step 5 校验（svg_quality_checker.py "
                           "<项目目录>/templates --template-mode）→ Review PPTX"
                           "（多 Master 必做）；禁止注册全局索引，校验通过后"
                           "报告模板包位置并结束回合，系统会负责收录。")
        elif ppt.get("kind") == "enhanced":
            # 产物状态机应答（native-enhance-pptx.md §5/§7 门禁契约）：
            # 空文本 = 确认推进；非空 = 修改意见（agent 更新提案再等确认）
            gate = _enhance_gate(Path(ppt["workspace_path"]))
            if gate == "plan":
                if text:
                    message = ("[系统] 用户对增强计划提出修改意见：" + text
                               + "。请按意见更新 analysis/enhancement_plan.json，"
                                 "向用户重新逐项提出计划后再次结束回合等待确认。")
                else:
                    message = ("[系统] 用户已确认增强计划（确认门①通过，把 plan status "
                               "置为 confirmed）。请继续：写 notes/00N.md 口播稿"
                               "（忠实幻灯片不加戏）→ validate --materials notes → "
                               "（audio 启用时）一次性提出 provider/音色/语速配置提案后"
                               "立即结束回合等待确认。")
            elif gate == "audio":
                if text:
                    message = ("[系统] 用户对音频配置提出修改意见：" + text
                               + "。请按意见调整提案，重新一次性提出后"
                                 "再次结束回合等待确认。")
                else:
                    message = ("[系统] 用户已确认音频配置（确认门②通过）。请把确认的 "
                               "provider/voice/rate 写入 project.json，然后继续："
                               "生成音频 → validate → apply，exports/ 产出后"
                               "报告文件名并结束回合。")
            else:
                message = "[系统] 用户请求继续执行 PPT 增强任务。"
                if text:
                    message += f"用户补充意见：{text}"
        else:
            message = "[系统] 用户请求继续执行 PPT 任务。"
            if text:
                message += f"用户补充意见：{text}"
        try:
            self._start_drive(db, ppt, message)
        except RuntimeError as e:
            code = str(e)
            if code == "TASK_RUNNING":
                return {"error": "任务正在执行中", "code": "TASK_RUNNING"}
            return {"error": "已达到最大并发任务数限制", "code": "TASK_LIMIT_REACHED"}
        return {"ok": True}

    def pause(self, db, ppt_id: str) -> dict:
        """手动暂停（复用任务暂停状态机，2.3 §3.4）。"""
        import deps
        ppt = db.get_ppt(ppt_id)
        if not ppt:
            return {"error": "PPT 不存在", "code": "PPT_NOT_FOUND"}
        task_id = ppt["task_id"]
        agent = deps.task_agent
        if not agent.is_task_running(task_id):
            return {"error": "任务未在运行中，无需暂停", "code": "TASK_NOT_RUNNING"}
        messages = agent.get_messages(task_id)
        if messages is None:
            return {"error": "任务尚未进入可暂停的执行阶段，请稍后重试",
                    "code": "TASK_NOT_PAUSABLE"}
        ok = agent.pause_task(task_id, messages, agent.get_iteration(task_id), [])
        if not ok:
            return {"error": "暂停失败", "code": "PAUSE_FAILED"}
        db.update_ppt_status(ppt_id, "paused")
        activity_events.pause_ppt(db, ppt_id)
        return {"ok": True}

    async def cancel(self, db, ppt_id: str) -> dict:
        import deps
        ppt = db.get_ppt(ppt_id)
        if not ppt:
            return {"error": "PPT 不存在", "code": "PPT_NOT_FOUND"}
        if ppt["status"] in ("completed", "failed", "cancelled"):
            return {"ok": True}
        deps.task_agent.request_cancel(ppt["task_id"])
        db.update_ppt_status(ppt_id, "cancelled")
        pump = self._pumps.pop(ppt_id, None)
        if pump and not pump.done():
            # run() 会在 cancel_event 生效后自然退出；此 cancel 仅兜底悬挂
            pump.cancel()
        # 关停命令是同步子进程（timeout 15s）——放线程池，不阻塞事件循环
        await asyncio.to_thread(
            _shutdown_confirm_ui,
            self._venv_python(db, ppt), Path(ppt["workspace_path"]))
        return {"ok": True}

    async def delete(self, db, ppt_id: str) -> dict:
        """删除（对齐 tasks delete 语义：bounded 等待 agent 退出 + 线程池删除，
        防工作区删除与 agent 在飞写盘竞态；先删任务再删 ppt 行——ppt 行
        最后删，中途失败可重试，反向则行已丢失无法追踪）。"""
        import deps
        ppt = db.get_ppt(ppt_id)
        if not ppt:
            return {"error": "PPT 不存在", "code": "PPT_NOT_FOUND"}
        task_id = ppt["task_id"]
        if deps.task_agent.is_task_running(task_id):
            deps.task_agent.request_cancel(task_id)
            for _ in range(20):  # 最多 ~10s，同 tasks delete 的 bounded 等待
                if not deps.task_agent.is_task_running(task_id):
                    break
                await asyncio.sleep(0.5)
        await asyncio.to_thread(
            _shutdown_confirm_ui,
            self._venv_python(db, ppt), Path(ppt["workspace_path"]))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, deps.db.delete_task, task_id)
        db.delete_ppt(ppt_id)
        return {"ok": True}

    @staticmethod
    def _venv_python(db, ppt: dict) -> str:
        task = db.get_task(ppt.get("task_id") or "")
        venv = (task or {}).get("venv_path") or ""
        if venv:
            py = os.path.join(venv, "Scripts", "python.exe")
            if os.name != "nt":
                py = os.path.join(venv, "bin", "python")
            if os.path.isfile(py):
                return py
        return ""

    # -- 启动恢复 --

    def recover_orphans(self, db) -> None:
        """启动扫描：泵随进程消失，active 行按产物落位（0.4 §2.4 同语义）。"""
        for row in db.list_active_ppts():
            ppt = db.get_ppt(row["id"])
            if not ppt:
                continue
            if ppt.get("kind") == "template":
                # E2：工作区包完整 → 收录（幂等）置终态；不完整 → 暂停可续
                if _template_package_valid(ppt):
                    ok, reason = _collect_template(ppt)
                    if ok:
                        db.update_ppt_status(ppt["id"], "completed")
                        activity_events.complete_ppt(
                            db, ppt["id"], ppt.get("template_id") or "")
                    else:
                        db.update_ppt_status(ppt["id"], "failed", error=reason)
                        activity_events.fail_ppt(db, ppt["id"], reason)
                else:
                    db.update_ppt_status(ppt["id"], "paused")
                    activity_events.pause_ppt(db, ppt["id"], "应用重启，可在模板分区继续")
                continue
            if ppt.get("kind") == "enhanced":
                # 增强任务产物在 enhance_project 下（workspace_path 直接指向）
                project = Path(ppt["workspace_path"])
                exports = _enhance_exports(project)
                if exports and ppt["status"] != "completed":
                    rel = str(exports[-1].relative_to(project)).replace("\\", "/")
                    db.update_ppt_status(ppt["id"], "completed", export_path=rel)
                    activity_events.complete_ppt(db, ppt["id"], rel)
                    continue
                gate = _enhance_gate(project)
                if gate in ("plan", "audio"):
                    # 门禁恢复与 _finalize_enhanced 对齐 stage（plan→strategy /
                    # audio→authoring）：详情首拉前前端按 stage 选门禁卡不误显
                    db.update_ppt_progress(
                        ppt["id"], stage="strategy" if gate == "plan" else "authoring")
                    db.update_ppt_status(ppt["id"], "awaiting_input")
                    activity_events.gate_ppt(db, ppt["id"], "应用重启，请继续完成确认")
                    continue
                if ppt["status"] == "running":
                    db.update_ppt_status(ppt["id"], "paused")
                    activity_events.pause_ppt(db, ppt["id"], "应用重启，可在详情页继续")
                continue
            project = Path(ppt["workspace_path"])
            exports = sorted((project / "exports").glob("*.pptx"), key=os.path.getmtime) \
                if (project / "exports").is_dir() else []
            if exports and ppt["status"] != "completed":
                rel = str(exports[-1].relative_to(project)).replace("\\", "/")
                db.update_ppt_status(ppt["id"], "completed", export_path=rel)
                activity_events.complete_ppt(db, ppt["id"], rel)
                continue
            gate = _gate_state(project)
            if gate["pending"] and not gate["receipt_stage1"] and not gate["confirmed_final"]:
                db.update_ppt_status(ppt["id"], "awaiting_input")
                activity_events.gate_ppt(db, ppt["id"], "应用重启，请继续完成确认")
                continue
            if ppt["status"] == "running":
                db.update_ppt_status(ppt["id"], "paused")
                activity_events.pause_ppt(db, ppt["id"], "应用重启，可在详情页继续")

    # -- 活动注册表 source（0.4 §2.2）--

    @staticmethod
    def fetch_active(db) -> list[dict]:
        rows = db.conn.execute(
            """
            SELECT x.id, x.project_id, x.title, x.status, x.stage, x.progress,
                   x.page_done, x.page_count, x.kind,
                   p.name AS project_name,
                   CASE WHEN x.kind = 'template' THEN 'templates'
                        WHEN p.kind = 'base_lib' THEN 'baselib' ELSE 'project' END AS container
            FROM ppts x
            LEFT JOIN projects p ON p.id = x.project_id
            WHERE x.status IN ('running', 'awaiting_input')
            ORDER BY x.created_at DESC
            """
        ).fetchall()
        result = []
        for r in rows:
            item = {
                "entity_type": "ppt",
                "entity_id": r["id"],
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "container": r["container"] or "project",
                "entity_title": r["title"],
                "status": r["status"],
                "stage": r["stage"],
                "progress": int((r["progress"] or 0) * 100),
            }
            if r["page_count"]:
                item["progress_text"] = f"{r['page_done'] or 0}/{r['page_count']}"
            result.append(item)
        return result


service = PptService()
