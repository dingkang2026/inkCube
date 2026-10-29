"""python/meeting_notes_service.py — 纪要结构化（2.13 §3 结构化路径）。

向导「生成草稿」后台主体：路由层受理（status→structuring + 202）后经
run_in_executor 调 structure_meeting_note_sync —— 取材料（raw_text + 图片
VLM 描述）→ LLM 单次调用（llm_routing 'meeting_note'，usage source=meeting_note）
→ 解析 JSON 落 draft_md/status=structured；失败回 draft + last_error（i18n 错误码）。

活动模型（0.4 统一骨架）：kind='note_structuring' 经 activities_registry
FunctionSource 注册（fetch_active）；行级 status 为权威，进程中断的 structuring
残留由启动清扫（db.reset_interrupted_meeting_notes）回 draft，活动自然消失——
与 profile_service 的内存态 _RUNNING 不同：本活动权威在 DB 行，重启可恢复。

反幻觉纪律入 prompt：仅基于输入材料；无支撑推断标「待确认」。
"""
import asyncio
import json
import logging
import threading
import time

from llm.usage import UsageContext, push_context  # 2.10 §4.3 用量环境

logger = logging.getLogger(__name__)

# 材料 prompt 注入上限（防极端大输入打爆请求，同 profile_service 口径）
_MAX_MATERIAL_CHARS = 30000

# 附图解析+描述等待上限（2.13 定稿驱动解析）：大图多图 + 解析/描述队列排队余量；
# 超时图仍在后台解析，用户稍后重试定稿即可（暂不进设置面板——单常量无调参诉求）
_IMAGES_WAIT_TIMEOUT_S = 600
_POLL_INTERVAL_S = 2.0

_PROMPT_TMPL = """你是招商/投资助理。基于以下拜访/会议材料，输出 JSON：
{{"content_md": "纪要正文(markdown，含 时间/参与人/要点/结论 段落)", "action_items": [{{"title": "行动项标题"}}, ...]}}

纪律：
- 仅基于材料内容；材料中没有依据的推断在 title 前加「待确认：」前缀
- 无行动项时 action_items 输出空数组；禁止编造
- 纪要正文使用中文 markdown

【材料】
{text}"""


def _collect_materials(db, note: dict) -> str:
    """材料拼装：raw_text + 各图片 file 的 VLM 描述（现有解析产物；
    缺失/未描述的 file 跳过——前端门控保证发起时图片已就绪，此处防御）。"""
    parts = []
    if (note.get("raw_text") or "").strip():
        parts.append(note["raw_text"])
    for fid in note.get("image_file_ids") or []:
        if not db.get_file(fid):
            continue
        for img in db.list_images(fid):
            desc = (img.get("vlm_description") or "").strip()
            if desc:
                parts.append(desc)
    return "\n\n".join(parts)


def context_for_file(db, file_id: str) -> str | None:
    """file_id 被某纪要引用时的描述上下文（标题+正文，2.13 定稿驱动）；无引用 None。

    task_queue 图片描述注入用：附图定稿后才上传解析，纪要行必已落库，VLM 拿
    纪要文本作 context（对齐文档内嵌图带前后文的机制）。LIKE 带引号精确匹配
    （image_file_ids JSON 数组元素），防 file_id 前缀串误命中。
    """
    row = db.conn.execute(
        "SELECT title, raw_text FROM meeting_notes WHERE image_file_ids LIKE ? LIMIT 1",
        (f'%"{file_id}"%',),
    ).fetchone()
    if not row:
        return None
    parts = []
    if (row["title"] or "").strip():
        parts.append(f"纪要《{row['title']}》")
    if (row["raw_text"] or "").strip():
        parts.append(row["raw_text"])
    return "\n".join(parts) or None


def _image_settled(db, fid: str):
    """单附图就绪判定：True=就绪（解析 done 且图全终态）；str=失败错误码；False=在途。"""
    f = db.get_file(fid)
    if not f:
        return True  # 已删图跳过（同 _collect_materials 口径）
    if f["status"] in ("pending", "parsing"):
        return False
    if f["status"] == "error":
        return "IMAGE_FAILED"
    # 文件解析 done：图片描述层终态判定（done/archived=终态，error=失败，其余在途）
    imgs = db.list_images(fid)
    if not imgs:
        return True  # 防御：无登记图视为就绪（材料组装自然跳过）
    if any(i.get("status") == "error" for i in imgs):
        return "IMAGE_FAILED"
    return all(i.get("status") in ("done", "archived") for i in imgs)


def _wait_images_ready(db, note: dict) -> str | None:
    """定稿驱动解析（2.13）：结构化前轮询等待附图解析+描述全部就绪。

    返回 None=就绪；"IMAGE_FAILED"=任一图失败（提前败，用户修复后重试定稿）；
    "IMAGES_TIMEOUT"=超时（图仍在后台解析，稍后重试）。无图秒过。"""
    fids = note.get("image_file_ids") or []
    if not fids:
        return None
    deadline = time.monotonic() + _IMAGES_WAIT_TIMEOUT_S
    while True:
        results = [_image_settled(db, fid) for fid in fids]
        failed = next((r for r in results if isinstance(r, str)), None)
        if failed:
            return failed
        if all(r is True for r in results):
            return None
        if time.monotonic() >= deadline:
            return "IMAGES_TIMEOUT"
        time.sleep(_POLL_INTERVAL_S)


def _parse_output(raw: str) -> dict:
    """LLM 输出 → {content_md, action_items:[{title}]}；容忍 ``` 围栏，
    schema 不合抛 ValueError（调用方统一回 draft + LLM_BAD_RESPONSE）。"""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in LLM output")
    parsed = json.loads(raw[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM output is not a JSON object")
    if not isinstance(parsed.get("content_md"), str) or not parsed["content_md"].strip():
        raise ValueError("missing content_md")
    if not isinstance(parsed.get("action_items"), list):
        raise ValueError("action_items must be a list")
    return parsed


def _structure_text(text: str, settings: dict) -> dict:
    """LLM 单次调用 + 解析（同步：executor 线程内 asyncio.run 包装 async client，
    同 profile_service L187）。路由键 'meeting_note'（settings 未配置走全局默认），
    usage source=meeting_note（2.10 usage 面板可见，spec §6 回写清单项）。"""
    from llm import LLMClient
    from llm.routing import get_task_settings

    effective = get_task_settings(settings, "meeting_note")
    messages = [{"role": "user",
                 "content": _PROMPT_TMPL.format(text=text[:_MAX_MATERIAL_CHARS])}]
    raw = asyncio.run(LLMClient(effective).chat_full(messages))["content"]
    return _parse_output(raw)


def structure_meeting_note_sync(db, note_id: str, settings: dict) -> None:
    """同步入口（测试直调；路由层经 run_in_executor 后台化，2.13 §3）。

    状态机：structuring →（等附图就绪）→ structured（draft_md 落库）/
    失败回 draft + last_error（IMAGE_FAILED/IMAGES_TIMEOUT/LLM_*）。
    取行/等待/拼材料/LLM 全程在 try 内——executor 线程抛异常无人接，任何一步
    失败统一落库回 draft（防 structuring 卡死）；材料空 NO_MATERIAL（路由层
    422 前置按「文本或图非空」判定——图描述属受理后产物，此处防御）。
    """
    try:
        note = db.get_meeting_note(note_id)
        if not note:
            return
        # 2.13 定稿驱动解析：受理时附图刚入解析队列，先等就绪再拼材料
        wait_err = _wait_images_ready(db, note)
        if wait_err:
            db.update_meeting_note(note_id,
                                   {"status": "draft", "last_error": wait_err})
            return
        text = _collect_materials(db, note)
        if not text.strip():
            db.update_meeting_note(note_id,
                                   {"status": "draft", "last_error": "NO_MATERIAL"})
            return
        # 2.10 §4.3：run_in_executor 线程 contextvar 不跨线程传播，线程体内自行 push
        with push_context(UsageContext(source="meeting_note",
                                       project_id=note["project_id"])):
            parsed = _structure_text(text, settings)
    except ValueError as e:
        logger.warning("meeting note structure failed %s: %s", note_id, e)
        db.update_meeting_note(note_id,
                               {"status": "draft", "last_error": "LLM_BAD_RESPONSE"})
    except Exception as e:
        logger.warning("meeting note structure failed %s: %s", note_id, e)
        db.update_meeting_note(note_id,
                               {"status": "draft", "last_error": "LLM_FAILED"})
    else:
        db.update_meeting_note(note_id, {
            "draft_md": json.dumps(parsed, ensure_ascii=False),
            "status": "structured", "last_error": None})


def fetch_active(db) -> list[dict]:
    """ActivitySource fetch（0.4 §2.2 契约，只读无副作用）：structuring 行快照。
    补 project_name（首页进行中行 i18n 插值；在途个位数逐条查开销可忽略）。"""
    items = []
    for r in db.list_meeting_notes_by_status("structuring"):
        row = db.get_project(r["project_id"])
        items.append({
            "entity_type": "note_structuring",
            "entity_id": r["id"],
            "project_id": r["project_id"],
            "project_name": (row.get("name") or "") if row else "",
            "entity_title": r["title"],
            "progress_text": "正在整理纪要…",
        })
    return items


# confirm 是「md 上传 + 逐建事务」的复合操作，无法单事务化——内存占坑串行化
# 同 note，防双窗口并发双写产物与事务（模式同 profile_service）；status 保持
# structured，失败留存后「重试保存」天然放行。
_CONFIRM_LOCK = threading.Lock()
_CONFIRMING: set[str] = set()


def try_acquire_confirm(note_id: str) -> bool:
    with _CONFIRM_LOCK:
        if note_id in _CONFIRMING:
            return False
        _CONFIRMING.add(note_id)
        return True


def release_confirm(note_id: str) -> None:
    with _CONFIRM_LOCK:
        _CONFIRMING.discard(note_id)
