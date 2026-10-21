# -*- coding: utf-8 -*-
"""模板填表服务（移植自 form_fill_v2，适配改写）。

编排：按 schema 批次拓扑序 → 联合检索（KnowledgeSearchTool：项目知识库 + 关联基础库，
口径同报告 knowledge 通道）→ LLMClient 生成值集 → docx_filler.apply_field 写入
→ 填表报告。核心包与 DB 解耦：进度经 progress_cb 回调由路由层落库。
"""
import asyncio
import io
import json
import logging
import re

from docx import Document

from llm.client import LLMClient
from llm import budget
from knowledge.knowledge_search import KnowledgeSearchTool
from form_fill.docx_filler import apply_field
from form_fill.prompts import TEMPLATE_FILL_PROMPT, build_fill_user_prompt

logger = logging.getLogger(__name__)

KB_ITEMS_PER_QUERY = 4     # 每个检索词最多取条数
KB_CHARS_PER_ITEM = 2500   # 每条资料截断
QUERIES_PER_BATCH = 8      # 每批检索词上限
DIGEST_VALUE_MAX = 60      # 已完成摘要单值截断
QUERY_PARALLELISM = 4      # 检索词并发数（对齐报告 query_parallelism）

# derive 公式安全垫（2.4 §4.2）：结构余量扣除 system prompt + 字段骨架 + 已完成摘要，
# 0.7 系数覆盖 token 估算误差（CHARS_PER_TOKEN 保守值下的残余不确定性）
_DERIVE_STRUCTURAL_RESERVE_TOKENS = 8192
_DERIVE_SAFETY_RATIO = 0.7


def derive_kb_chars_budget(llm_settings: dict) -> int:
    """检索资料块总长预算（chars）：上下文窗口扣除输出上限与结构余量后，
    按 CHARS_PER_TOKEN 换算并留安全垫。

    口径纪律（D2）：tokens 判断全部经 llm/budget.py 唯一入口，本函数是
    填表管线唯一一处 chars 换算点。默认配置 (131072−32768−8192)×1.5×0.7
    ≈ 94,600 chars（旧硬截 24,000 严重低用窗口）；clamp [24k, 120k]
    防小窗口下限与超大窗口失控。
    """
    budget_tokens = (budget.context_window(llm_settings)
                     - budget.output_tokens(llm_settings)
                     - _DERIVE_STRUCTURAL_RESERVE_TOKENS)
    chars = int(budget_tokens * budget.CHARS_PER_TOKEN * _DERIVE_SAFETY_RATIO)
    return max(24_000, min(chars, 120_000))


# ============================== 批次排序 ==============================

def _order_batches(batches: list) -> list:
    """按 depends_on 拓扑排序（稳定：同层按 seq）；环时按 seq 兜底全收"""
    by_seq = {b.get('seq'): b for b in batches}
    ordered, done, pending = [], set(), list(batches)
    while pending:
        progressed = False
        for b in list(pending):
            deps = b.get('depends_on') or []
            if all(d in done or d not in by_seq for d in deps):
                ordered.append(b)
                done.add(b.get('seq'))
                pending.remove(b)
                progressed = True
        if not progressed:
            ordered.extend(sorted(pending, key=lambda x: x.get('seq', 0)))
            break
    return ordered


# ============================== 知识库联合检索 ==============================

def _merge_queries(fields: list) -> list:
    """各字段（含 label_group 子锚点）检索词去重收集，按字段轮转交错后限量——
    轮转保证截断时每个字段至少保留首条检索词，避免顺序截断系统性饿死
    排位靠后的字段；保底下限 = max(QUERIES_PER_BATCH, 字段数)——批次字段数
    超过 QUERIES_PER_BATCH（analyzer 允许至 15）时下限随之扩展，首条保底
    承诺无条件成立（merged_kb_queries 层已删除，P1）"""
    per_field: list[list] = []
    for f in fields:
        qs = []
        for q in (f.get('kb_query') or []):
            if q and q not in qs:
                qs.append(q)
        for sub in (f.get('sub_fields') or []):
            for q in (sub.get('kb_query') or []):
                if q and q not in qs:
                    qs.append(q)
        if qs:
            per_field.append(qs)
    queries: list = []
    seen: set = set()
    round_i = 0
    queries_limit = max(QUERIES_PER_BATCH, len(per_field))
    while len(queries) < queries_limit:
        added = False
        for qs in per_field:
            if round_i < len(qs) and qs[round_i] not in seen:
                seen.add(qs[round_i])
                queries.append(qs[round_i])
                added = True
                if len(queries) >= queries_limit:
                    break
        if not added:
            break
        round_i += 1
    return queries


async def _retrieve(queries: list, search_tool: KnowledgeSearchTool,
                    project_id: str, base_project_ids: list) -> str:
    """联合检索各词 → 拼接资料块（带检索词标注，来源便于追溯）；失败降级为空。
    检索范围 = [project_id] + base_project_ids（关联基础库）；chunk id 跨检索词
    先到先得去重（同一资料块只进首个命中的词，不重复占 kb_context 预算），
    词内按融合分排序限量（口径同报告 knowledge 通道）；不含互联网。"""
    search_project_ids = [project_id] + list(base_project_ids or [])
    sem = asyncio.Semaphore(QUERY_PARALLELISM)
    chunks: dict[str, tuple[str, float]] = {}   # chunk_id -> (content, score)，跨检索词共享

    async def one(q: str):
        async with sem:
            hit_ids: list[str] = []
            for pid in search_project_ids:
                try:
                    results = await search_tool.search(query=q, project_id=pid,
                                                       top_k=KB_ITEMS_PER_QUERY)
                except Exception as e:
                    logger.warning('[TemplateFill] 检索失败 query=%s project=%s: %s', q, pid, e)
                    results = []
                for r in results:
                    cid = r.get('id', '')
                    content = str(r.get('content') or '').strip()[:KB_CHARS_PER_ITEM]
                    if not (cid and content) or cid in chunks:
                        continue
                    # 来源标注：项目库常混入他企文件（关联方/客户/供应商/误入），
                    # 与 prompt 主体声明配合供模型归属甄别（格式对齐 RAG 片段）
                    filename = str((r.get('metadata') or {}).get('filename') or '').strip()
                    if filename:
                        content = f'{content}\n—— 来源：{filename}'
                    chunks[cid] = (content, r.get('_score') or 0)
                    hit_ids.append(cid)
            pairs = [chunks[cid] for cid in hit_ids]
            items = [c for c, _ in
                     sorted(pairs, key=lambda x: x[1], reverse=True)[:KB_ITEMS_PER_QUERY]]
            return f'【检索词：{q}】\n' + '\n---\n'.join(items) if items else None

    blocks = await asyncio.gather(*[one(q) for q in queries])
    return '\n\n'.join(b for b in blocks if b)


# ============================== 值归一化 ==============================

_NUM_RE = re.compile(r'-?\d+(\.\d+)?')


def _normalize_value(field: dict, value):
    """LLM 输出/人工修订 → apply_field 入参约定（严进宽出：异常值归 None 记 no_data）。
    对自身输出幂等（人工修订通道共用，§2.2）"""
    if value is None:
        return None
    if '#blank' in field['target']:   # 选项内嵌填空：只留数值（单位已印在模板）
        s = str(value).strip()
        unit = field.get('unit')
        if unit and s.endswith(unit):
            s = s[:-len(unit)].strip()
        m = _NUM_RE.search(s)
        return m.group(0) if m else (s or None)
    ftype = field['fill_type']
    if ftype in ('choice', 'checkbox'):
        payload = value if isinstance(value, dict) else {
            'selected': value if isinstance(value, list) else [value]}
        opts = [str(o) for o in (field.get('options') or [])]
        raw = [str(s).strip() for s in (payload.get('selected') or []) if str(s).strip()]
        selected = [s for s in raw if s in opts]
        other = payload.get('other')
        other = str(other).strip() if other else None
        for s in raw:   # 选项外的值落到 other
            if s not in opts and not other:
                other = s
        if field.get('multi_select') is False:
            selected = selected[:1]
        if not selected and not other:
            return None
        return {'selected': selected, 'other': other}
    if ftype == 'label_group':
        return value if isinstance(value, dict) and value else None
    if ftype == 'table_rows':
        if not isinstance(value, list):
            return None
        rows = [[('' if c is None else str(c)) for c in row]
                for row in value if isinstance(row, list)]
        return rows or None
    s = str(value).strip()
    return s or None


# ============================== 批次执行 ==============================

def _rule_value(field: dict, project_context: dict):
    for k in (field['field_key'], field.get('rule_hint')):
        if k and project_context.get(k) is not None:
            return project_context[k]
    return None


def _digest(filled: dict, fields: dict) -> str:
    """已完成字段摘要（供后续批次衔接，控制篇幅）"""
    lines = []
    for key, val in filled.items():
        f = fields.get(key) or {}
        if isinstance(val, dict):
            preview = str(val)
        elif isinstance(val, list):
            preview = f'{len(val)}行'
        else:
            preview = str(val).replace('\n', ' ')[:DIGEST_VALUE_MAX]
        lines.append(f'- {key}({f.get("label") or key}): {preview}')
    return '\n'.join(lines)


def _parse_llm_json(content: str) -> dict:
    """解析 LLM 返回的严格 JSON（容忍 ```json 围栏，与 analyzer 同源）"""
    text = content.strip()
    fence = re.search(r'```(?:json)?\s*(.+?)```', text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('LLM 输出中未找到 JSON 对象')
    return json.loads(text[start:end + 1])


async def _generate_values(user_prompt: str, llm_client: LLMClient) -> dict:
    messages = [
        {'role': 'system', 'content': TEMPLATE_FILL_PROMPT},
        {'role': 'user', 'content': user_prompt},
    ]
    result = await llm_client.chat_full(messages)
    usage = result.get('usage', {})
    raw = result.get('content') or ''
    if not raw.strip():
        raise RuntimeError(f'LLM 填表返回空响应, usage={usage}')
    logger.info('[TemplateFill] 批次生成完成, usage=%s, length=%d', usage, len(raw))
    return _parse_llm_json(raw)


def _entry(field: dict, status: str, value=None, missed=None) -> dict:
    preview = None
    if value is not None:
        preview = str(value).replace('\n', ' ')[:60]
    return {'field_key': field['field_key'], 'label': field.get('label'),
            'fill_type': field['fill_type'], 'status': status,
            'value_preview': preview, 'missed': missed or []}


# ============================== 主入口 ==============================

async def fill_template(schema: dict, docx_path: str, project_context: dict,
                        search_tool: KnowledgeSearchTool, project_id: str,
                        base_project_ids: list, llm_client: LLMClient,
                        llm_settings: dict, progress_cb=None):
    """填表主入口：批次循环（检索→生成→写入）→ (docx 字节流, 填表报告, 完整值集)

    Args:
        schema: analyze_template 落盘的模板 Schema
        docx_path: 模板 docx 路径
        project_context: 项目基础信息（rule_fill 直取，键与 field_key/rule_hint 对应）
        search_tool: KnowledgeSearchTool 实例
        project_id: 当前项目 ID（项目知识库）
        base_project_ids: 关联基础库项目 ID 列表（联合检索，不含互联网）
        llm_client: LLMClient 实例（settings 主模型，与报告一致）
        llm_settings: 应用设置 dict
        progress_cb: 批次进度回调 progress_cb(done_batches, total_batches)
            （核心包与 DB 解耦，路由层闭包落库并换算百分比）
    """
    doc = Document(docx_path)
    fields = {f['field_key']: f for f in schema['fields']}
    batches = _order_batches(schema.get('batches') or [])
    project_context = project_context or {}
    total_batches = len(batches)
    kb_budget = derive_kb_chars_budget(llm_settings)

    report = []
    filled = {}   # field_key -> value（供依赖批次衔接）
    for done, batch in enumerate(batches, 1):
        keys = [k for k in batch.get('field_keys', []) if k in fields]
        # 1) rule_fill 直填
        kb_keys = []
        for key in keys:
            field = fields[key]
            if field.get('fill_mode') != 'rule_fill':
                kb_keys.append(key)
                continue
            v = _rule_value(field, project_context)
            if v is None:
                report.append(_entry(field, 'no_data'))
                continue
            r = apply_field(doc, field, str(v))
            filled[key] = str(v)
            report.append(_entry(field, 'filled' if r['ok'] else 'failed', v, r['missed']))
        if not kb_keys:
            if progress_cb:
                progress_cb(done, total_batches)
            continue

        # 2) 知识库联合检索
        batch_fields = [fields[k] for k in kb_keys]
        queries = _merge_queries(batch_fields)
        kb_context = (await _retrieve(queries, search_tool, project_id, base_project_ids)
                      if queries else '')

        # 3) LLM 批内一次生成
        digest = _digest(filled, fields)
        user_prompt = build_fill_user_prompt(
            schema.get('template_name') or '', batch_fields, kb_context,
            project_context, digest, kb_chars_max=kb_budget)
        try:
            values = await _generate_values(user_prompt, llm_client)
        except Exception as e:
            logger.error('[TemplateFill] 批次[%s]生成失败: %s', batch.get('name'), e)
            report += [_entry(f, 'failed') for f in batch_fields]
            if progress_cb:
                progress_cb(done, total_batches)
            continue

        # 4) 归一化校验 + 写入
        for field in batch_fields:
            raw = values.get(field['field_key'])
            v = _normalize_value(field, raw)
            if v is None:
                report.append(_entry(field, 'no_data'))
                continue
            r = apply_field(doc, field, v)
            if r['ok']:
                filled[field['field_key']] = v
            report.append(_entry(field, 'filled' if r['ok'] else 'failed', v, r['missed']))

        if progress_cb:
            progress_cb(done, total_batches)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue(), report, filled
