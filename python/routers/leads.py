"""Leads router (0.6 §4.1/§5 V2-E2) — 线索池端点。

错误契约（D1）：非 2xx 用 JSONResponse {error, code}，经 handlePythonResponse
解析为 err.errorCode（schedules 同口径）。
"""
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import deps
from paths import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter()

_LEAD_STATUSES = ("new", "contacting", "evaluating", "hold", "converted")
_SOURCE_TYPES = ("run", "manual")

# 提取 prompt（LLM 指令，非 UI 文案；要求纯 JSON 数组便于解析）：
# 候选零自动入池（0.6 §6.3-10）——本端点只产出候选，入池由前端勾选确认。
_EXTRACT_PROMPT = """你是招商/投资经理的线索提取助手。下面是一份订阅盯梢报告全文。
请从报告中提取值得跟进的「线索」（企业/机构/项目/园区机会），每条包含：
- name: 线索名称（企业/机构/项目名，尽量具体）
- signal: 信号摘要（一句话说明为什么值得跟进）
- evidence: 原文依据（报告中最能支撑该线索的原句，截取即可）

只输出 JSON 数组，不要任何其他文字、解释或 markdown 围栏：
[{"name": "...", "signal": "...", "evidence": "..."}]

报告全文：
"""


class LeadCreateBody(BaseModel):
    name: str
    sourceType: str = "manual"
    sourceRunId: str | None = None
    sourceScheduleName: str = ""
    note: str = ""


class LeadUpdateBody(BaseModel):
    status: str | None = None
    note: str | None = None
    projectId: str | None = None


class LeadExtractBody(BaseModel):
    runId: str


def _err(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": message, "code": code})


@router.get("/leads")
async def list_leads(status: str = "",
                     limit: int = Query(0, ge=0, description="Page size; 0 = return all"),
                     offset: int = Query(0, ge=0)):
    """线索列表（LeadPool 状态分组视图）：limit=0 全量（分组需全集）；
    limit>0 分页契约 has_more/total（0.1 §16）。"""
    db = deps.db
    leads = db.list_leads(status=status or None,
                          limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(leads) > limit
    if has_more:
        leads = leads[:limit]
    total = db.count_leads(status=status or None)
    return {"leads": leads, "has_more": has_more, "total": total}


@router.post("/leads")
async def create_lead(body: LeadCreateBody):
    """新建线索（存为线索 / 提取勾选入池 / 手动录入共用）；批量入池 =
    前端循环调用本端点（0.6 §5 五通道契约，无批量端点）。"""
    name = body.name.strip()
    if not name:
        return _err(400, "线索名不能为空", "LEAD_NAME_REQUIRED")
    if body.sourceType not in _SOURCE_TYPES:
        return _err(400, "sourceType 须为 run/manual", "INVALID_SOURCE_TYPE")
    lead = deps.db.create_lead(
        name, source_type=body.sourceType, source_run_id=body.sourceRunId,
        source_schedule_name=body.sourceScheduleName, note=body.note)
    return {"lead": lead}


@router.patch("/leads/{lead_id}")
async def update_lead(lead_id: str, body: LeadUpdateBody):
    """单行 PATCH：状态推进 / 备注编辑 / 立项回填（projectId+converted，
    项目创建成功后才调用——失败不推进可重试，0.6 §4.1）。"""
    fields: dict = {}
    if body.status is not None:
        if body.status not in _LEAD_STATUSES:
            return _err(400, "status 须为 new/contacting/evaluating/hold/converted",
                        "INVALID_LEAD_STATUS")
        fields["status"] = body.status
    if body.note is not None:
        fields["note"] = body.note
    if body.projectId is not None:
        fields["project_id"] = body.projectId
    if not fields:
        return _err(400, "无可更新字段", "LEAD_UPDATE_EMPTY")
    if not deps.db.update_lead(lead_id, fields):
        return _err(404, "线索不存在", "LEAD_NOT_FOUND")
    return {"lead": deps.db.get_lead(lead_id)}


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: str):
    if not deps.db.delete_lead(lead_id):
        return _err(404, "线索不存在", "LEAD_NOT_FOUND")
    return {"ok": True}


def _parse_candidates(content: str) -> list[dict]:
    """LLM 输出 → 候选清单：剥 markdown 围栏 → JSON 数组 → 字段规整
    （name 必填非空；signal/evidence 缺失补空串）。解析不出抛 ValueError。"""
    text = (content or "").strip()
    if text.startswith("```"):
        # ```json ... ``` 围栏：取首尾围栏之间
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("no JSON array in LLM output")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, list):
        raise ValueError("LLM output is not a list")
    candidates: list[dict] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        # 同名去重保留首条（CodeReview Minor 2026-10-24）：前端勾选态与入池
        # 循环均以 name 为键，重名会串染勾选状态并创建重复线索
        if not name or name in seen:
            continue
        seen.add(name)
        candidates.append({
            "name": name,
            "signal": str(item.get("signal") or "").strip(),
            "evidence": str(item.get("evidence") or "").strip(),
        })
    return candidates


@router.post("/leads/extract")
async def extract_leads(body: LeadExtractBody):
    """LLM 候选提取（run_id → 候选清单，0.6 §3.2）：同步 invoke + 前端按钮
    loading（不进活动注册表——单次对话级耗时）；候选零自动入池（§6.3-10，
    入池由用户勾选确认走 create）。提取源零成本（报告本就是 OUTPUT_CONTRACT
    结构化文本）；running 期次无报告不可提取。"""
    db = deps.db
    run = db.get_run(body.runId)
    if not run:
        return _err(404, "期次不存在", "RUN_NOT_FOUND")
    if run["status"] == "running":
        return _err(409, "期次执行中，完成后才能提取", "RUN_RUNNING")
    if not run["report_path"]:
        return _err(404, "本期无报告，不可提取", "REPORT_NOT_READY")
    base = (Path(get_data_dir()) / "schedules").resolve()
    target = Path(run["report_path"]).resolve()
    if not str(target).startswith(str(base) + os.sep):
        return _err(403, "报告路径越界", "REPORT_PATH_OUTSIDE")
    if not target.is_file():
        return _err(404, "报告副本文件缺失", "REPORT_FILE_MISSING")
    content = target.read_text(encoding="utf-8")
    try:
        # 延迟 import（forms.py 同模式，避免模块导入期 LLM 依赖）；
        # 2.10 §4.3 用量环境：source=lead / purpose=extract / entity_id=run_id
        from llm.usage import UsageContext, push_context
        llm_settings = db.get_settings()
        # 档位路由（CodeReview Blocker 2026-10-24）：get_settings 白名单只有
        # standard/high 槽位、无 model 键，LLMClient 缺键 → 空串请求被网关拒
        # （原 refine-prompt 同教训实证）；线索提取轻量 JSON 输出走标准档
        from llm.routing import resolve_model
        llm_settings["model"] = resolve_model(llm_settings, "standard")
        from llm.client import LLMClient
        llm_client = LLMClient(llm_settings)
        with push_context(UsageContext(
                source="lead", purpose="extract", entity_id=body.runId)):
            result = await llm_client.chat_full([
                {"role": "user", "content": _EXTRACT_PROMPT + content},
            ])
    except Exception as e:
        from errors import actionable
        msg = actionable(e)
        logger.exception("Lead extract failed for run %s: %s", body.runId, e)
        return _err(500, msg, "EXTRACT_FAILED")
    try:
        candidates = _parse_candidates(result.get("content"))
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("Lead extract parse failed for run %s: %s", body.runId, e)
        return _err(500, "提取结果解析失败，请重试", "EXTRACT_PARSE_FAILED")
    logger.info("Lead extract run=%s: %d candidates", body.runId, len(candidates))
    return {"candidates": candidates}
