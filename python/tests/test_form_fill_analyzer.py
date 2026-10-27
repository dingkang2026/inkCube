# -*- coding: utf-8 -*-
"""form_fill.template_analyzer 单元测试。

覆盖：8 级决策树分类（text/choice/label_group/doc_section/占位/空格/短标签）、
数据表探测（单级/两级表头/gridSpan/vMerge 续行）、_extract_option 内嵌填空拆分、
批次校验（覆盖性/无环/体量）与默认分批、_assemble 组装（key 规范化/结构锁定/
blank label+单位/LLM 未映射）、analyze_template 端到端（FakeLLMClient，含围栏容忍）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from docx import Document
from docx.oxml.ns import qn

from form_fill.template_analyzer import (
    InvalidTemplateError,
    _TemplateExtractor,
    _assemble,
    _batches_valid,
    _default_batches,
    _extract_option,
    _split_instruction_sections,
    analyze_template,
)


# ============================== docx 构造辅助 ==============================

def _blank_doc():
    return Document()


def _set_grid_span(tc, span: int):
    tcpr = tc.get_or_add_tcPr()
    gs = tcpr.makeelement(qn("w:gridSpan"), {qn("w:val"): str(span)})
    tcpr.append(gs)


def _set_vmerge(tc, val: str = None):
    """val=None → OOXML 默认 continue；val='restart' → 重启格"""
    tcpr = tc.get_or_add_tcPr()
    attrs = {} if val is None else {qn("w:val"): val}
    tcpr.append(tcpr.makeelement(qn("w:vMerge"), attrs))


def _extract_targets(extractor):
    return [c["target"] for c in extractor.candidates]


def _cand_by_target(extractor, target):
    return next(c for c in extractor.candidates if c["target"] == target)


# ============================== 8 级决策树分类 ==============================

def test_decision_tree_basic_form():
    """两列基本信息表：左标签格不产候选；空值格 text / 勾选格 choice / 指令长文 doc_section"""
    doc = _blank_doc()
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("项目名称")
    table.cell(1, 0).paragraphs[0].add_run("所属行业")
    table.cell(1, 1).paragraphs[0].add_run("□人工智能 □高端装备")
    table.cell(2, 0).paragraphs[0].add_run("公司简介")
    table.cell(2, 1).paragraphs[0].add_run("请说明公司主营业务、核心产品与竞争优势（不少于200字）")

    ex = _TemplateExtractor().extract(doc)
    assert _extract_targets(ex) == ["top0/r0c1", "top0/r1c1", "top0/r2c1"]

    c0 = _cand_by_target(ex, "top0/r0c1")
    assert c0["structural_type"] == "text"
    assert c0["context"]["left_label"] == "项目名称"
    assert c0["placeholder"] is None

    c1 = _cand_by_target(ex, "top0/r1c1")
    assert c1["structural_type"] == "choice"
    assert c1["options"] == ["人工智能", "高端装备"]

    c2 = _cand_by_target(ex, "top0/r2c1")
    assert c2["structural_type"] == "doc_section"
    assert "说明" in c2["context"]["instruction"]


def test_decision_tree_placeholders():
    """下划线 / 【】占位 → text 候选并标记 placeholder"""
    doc = _blank_doc()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("注册资本")
    table.cell(0, 1).paragraphs[0].add_run("____万元")
    table.cell(1, 0).paragraphs[0].add_run("官网地址")
    table.cell(1, 1).paragraphs[0].add_run("【请填写官网】")

    ex = _TemplateExtractor().extract(doc)
    assert _extract_targets(ex) == ["top0/r0c1", "top0/r1c1"]
    assert _cand_by_target(ex, "top0/r0c1")["placeholder"] == "underline"
    assert _cand_by_target(ex, "top0/r1c1")["placeholder"] == "bracket"


def test_decision_tree_label_group():
    """同格多段「短标签：」→ label_group，锚点为各段冒号前主体；括注提示不产锚"""
    doc = _blank_doc()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("股东情况")
    cell = table.cell(0, 1)
    cell.paragraphs[0].add_run("法定代表人：")
    cell.add_paragraph("持股比例：")
    cell.add_paragraph("（包括但不限于以上内容）")

    ex = _TemplateExtractor().extract(doc)
    cand = _cand_by_target(ex, "top0/r0c1")
    assert cand["structural_type"] == "label_group"
    assert cand["_anchors"] == ["法定代表人", "持股比例"]


def test_decision_tree_empty_without_left_label_unmapped():
    """空格且无左标签 → unmapped；左侧无内容的长指令文 → doc_section"""
    doc = _blank_doc()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 1).paragraphs[0].add_run("说明本项目的投资亮点、估值逻辑与未来退出路径的安排")
    table.cell(1, 0).paragraphs[0].add_run("备注说明")
    table.cell(1, 1).paragraphs[0].add_run("这里是一段较长的备注内容文本")

    ex = _TemplateExtractor().extract(doc)
    # r0c0 空 + 无左标签 → unmapped（短文本）；r0c1 长文含指令 → doc_section
    assert any(u["target"] == "top0/r0c0" and "空格" in u["reason"] for u in ex.unmapped)
    assert _cand_by_target(ex, "top0/r0c1")["structural_type"] == "doc_section"
    # r1c1 超过短标签阈值（>12）但不足 doc_section 长度（<20）→ 规则 8 兜底 label 候选
    assert _cand_by_target(ex, "top0/r1c1")["structural_type"] == "label"


def test_decision_tree_short_text_context_only():
    """短文本无占位 → 仅作上下文（label），不产候选"""
    doc = _blank_doc()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("姓名")
    table.cell(0, 1).paragraphs[0].add_run("联系方式")

    ex = _TemplateExtractor().extract(doc)
    assert ex.candidates == []


def test_checkbox_with_embedded_blank():
    """「□租赁：___平方米」复合格：choice 候选 + 独立 blank text 候选（带标签与单位）"""
    doc = _blank_doc()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("投资方式")
    table.cell(0, 1).paragraphs[0].add_run("□租赁：___平方米 □购置")

    ex = _TemplateExtractor().extract(doc)
    assert _cand_by_target(ex, "top0/r0c1")["options"] == ["租赁", "购置"]
    blank = _cand_by_target(ex, "top0/r0c1#blank1")
    assert blank["structural_type"] == "text"
    assert blank["_blank_label"] == "租赁"
    assert blank["_blank_unit"] == "平方米"


def test_data_table_single_level_header():
    """首行表头 + 全空数据行 → table_rows，列按 gridSpan 展开"""
    doc = _blank_doc()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("姓名")
    table.cell(0, 1).paragraphs[0].add_run("年龄")
    # r1 全空数据行

    ex = _TemplateExtractor().extract(doc)
    cand = _cand_by_target(ex, "top0")
    assert cand["structural_type"] == "table_rows"
    assert cand["context"]["table_header"] == ["姓名", "年龄"]
    assert cand["preset_rows"] == 1
    assert cand["_header_rows"] == 1


def test_data_table_two_level_header_with_grid_span():
    """两级表头（gridSpan，span 格物理吸收相邻 tc）+ 数据行 → header_rows=2，
    二级表头对位合并。注意：table.cell(r,c) 是逻辑坐标寻址，设置 span 前必须先
    写完文本并物理移除被吸收的 tc，否则后续访问错位（实测结论）。"""
    doc = _blank_doc()
    table = doc.add_table(rows=3, cols=4)
    # r0 先写文本（此时物理与逻辑坐标一致）：tc0/tc2 为 span 起点，tc1/tc3 将被吸收
    table.cell(0, 0).paragraphs[0].add_run("基本信息")
    table.cell(0, 2).paragraphs[0].add_run("财务信息")
    tr0 = table.rows[0]._tr
    tcs = tr0.findall(qn("w:tc"))
    tr0.remove(tcs[3])
    tr0.remove(tcs[1])
    _set_grid_span(tcs[0], 2)        # 基本信息
    _set_grid_span(tcs[2], 2)        # 财务信息
    # r1: 四个二级表头
    for ci, name in enumerate(["注册资本", "实缴资本", "营业收入", "净利润"]):
        table.cell(1, ci).paragraphs[0].add_run(name)
    # r2: 全空数据行

    ex = _TemplateExtractor().extract(doc)
    cand = _cand_by_target(ex, "top0")
    assert cand["structural_type"] == "table_rows"
    assert cand["_header_rows"] == 2
    assert cand["context"]["table_header"] == [
        "基本信息/注册资本", "基本信息/实缴资本", "财务信息/营业收入", "财务信息/净利润",
    ]


def test_vmerge_continue_cell_skipped():
    """vMerge 续行格不分类、不作左标签：其右侧空格因无左标签进 unmapped。
    r0c1 用长文本避免整表被误判为数据表（首行两短格恰好是表头样式）"""
    doc = _blank_doc()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("分类标签")
    _set_vmerge(table.cell(0, 0)._tc, "restart")
    table.cell(0, 1).paragraphs[0].add_run("这里是一段较长的取值说明文本用于规避表头样式判定")
    _set_vmerge(table.cell(1, 0)._tc)   # continue（无 val 属性）
    # r1c1 留空

    ex = _TemplateExtractor().extract(doc)
    # 续行格未被分类（不产生 top0/r1c0 的候选或 unmapped 记录）
    assert all("r1c0" not in t for t in _extract_targets(ex))
    # r1c1 空格：因左标签（续行格）被跳过而判 unmapped
    assert any(u["target"] == "top0/r1c1" for u in ex.unmapped)


def test_no_table_raises_invalid():
    doc = _blank_doc()
    doc.add_paragraph("纯段落文档")
    with pytest.raises(InvalidTemplateError):
        _TemplateExtractor().extract(doc)


# ============================== _extract_option / 子节切分 ==============================

def test_extract_option_variants():
    opts, blanks = _extract_option("租赁：___平方米")
    assert opts == ["租赁"]
    assert blanks == [{"label": "租赁", "unit": "平方米"}]

    # 纯"其他：____"片段不产选项/填空："其他"独立走 other 通道
    # （normalize 白名单外值落 other，apply_field 用"其他："锚插值）
    opts, blanks = _extract_option("其他：____")
    assert opts == []
    assert blanks == []

    opts, blanks = _extract_option("人工智能")
    assert opts == ["人工智能"]
    assert blanks == []


def test_split_instruction_sections():
    text = "一、说明公司主营业务情况及市场份额 二、分析核心竞争优势与壁垒 三、总结未来发展规划"
    parts = _split_instruction_sections(text)
    assert len(parts) == 3
    assert parts[0].startswith("一、")

    # 不足 2 段 / 存在过短段（<4 字）→ 不切
    assert _split_instruction_sections("说明公司主营业务情况") == []
    assert _split_instruction_sections("一、简 二、分析核心竞争力情况介绍") == []


# ============================== 批次校验与默认分批 ==============================

def _mk_fields(keys, fill_type="text"):
    return [{"field_key": k, "label": k, "fill_type": fill_type,
             "context": {"section_chain": []}} for k in keys]


def test_batches_valid_scenarios():
    keys = {"a", "b", "c"}
    assert _batches_valid(
        [{"seq": 1, "field_keys": ["a", "b", "c"], "depends_on": []}], keys) is True
    # 覆盖不全
    assert _batches_valid(
        [{"seq": 1, "field_keys": ["a", "b"], "depends_on": []}], keys) is False
    # 重复覆盖
    assert _batches_valid(
        [{"seq": 1, "field_keys": ["a", "b", "c", "a"], "depends_on": []}], keys) is False
    # 引用不存在的 key
    assert _batches_valid(
        [{"seq": 1, "field_keys": ["a", "b", "x"], "depends_on": []}], keys) is False
    # 体量超限
    big = [{"seq": 1, "field_keys": [f"k{i}" for i in range(16)], "depends_on": []}]
    assert _batches_valid(big, {f"k{i}" for i in range(16)}) is False
    # 依赖成环
    assert _batches_valid(
        [{"seq": 1, "field_keys": ["a"], "depends_on": [2]},
         {"seq": 2, "field_keys": ["b", "c"], "depends_on": [1]}], keys) is False


def test_default_batches_boundaries():
    fields = _mk_fields([f"f{i}" for i in range(14)])
    batches = _default_batches(fields)
    assert len(batches) == 2   # 12 + 2
    assert batches[0]["field_keys"] == [f"f{i}" for i in range(12)]
    assert batches[1]["field_keys"] == ["f12", "f13"]
    assert all(b["depends_on"] == [] for b in batches)


def test_default_batches_doc_section_boundary():
    fields = _mk_fields(["t1"], fill_type="text") + \
        _mk_fields(["d1"], fill_type="doc_section") + \
        _mk_fields(["t2"], fill_type="text")
    batches = _default_batches(fields)
    assert len(batches) == 2
    assert batches[0]["field_keys"] == ["t1"]
    assert batches[1]["field_keys"] == ["d1", "t2"]


def test_default_batches_merges_trailing_single():
    fields = _mk_fields([f"f{i}" for i in range(13)])
    batches = _default_batches(fields)
    assert len(batches) == 1
    assert len(batches[0]["field_keys"]) == 13


# ============================== _assemble 组装 ==============================

def _extractor_with(cands):
    ex = _TemplateExtractor()
    ex.top_tables = 1
    ex.candidates = cands
    return ex


def test_assemble_key_normalization_and_lock():
    ex = _extractor_with([
        {"target": "top0/r0c1", "structural_type": "text", "placeholder": None,
         "context": {"left_label": "项目名称", "section_chain": [],
                     "table_header": None, "instruction": None},
         "options": None, "preset_rows": None},
        {"target": "top0/r1c1", "structural_type": "text", "placeholder": None,
         "context": {"left_label": "备注", "section_chain": [],
                     "table_header": None, "instruction": None},
         "options": None, "preset_rows": None},
    ])
    llm = {"fields": [
        # 非法 key → 规范化为 field_0
        {"index": 0, "field_key": "Project Name", "fill_type": "text",
         "fill_mode": "kb_fill", "kb_query": ["q"], "confidence": "high"},
        # 与规范化后冲突 → 追加序号
        {"index": 1, "field_key": "field_0", "fill_type": "text",
         "fill_mode": "kb_fill", "kb_query": [], "confidence": "low"},
    ], "batches": [], "skipped_indexes": []}
    schema = _assemble("测试模板", ex, llm)
    keys = [f["field_key"] for f in schema["fields"]]
    # 非法 key → field_{idx}；与已占用 key 冲突 → 回退 field_{idx}
    assert keys == ["field_0", "field_1"]
    assert schema["batches"]   # 空批次草案 → 默认分批兜底
    assert schema["unmapped_cells"] == []


def test_assemble_locked_fill_type():
    """choice 为结构锁定类型：LLM 误判 text 不生效"""
    ex = _extractor_with([
        {"target": "top0/r0c1", "structural_type": "choice", "placeholder": None,
         "context": {"left_label": "行业", "section_chain": [],
                     "table_header": None, "instruction": None},
         "options": ["人工智能"], "preset_rows": None},
    ])
    llm = {"fields": [
        {"index": 0, "field_key": "industry", "fill_type": "text",
         "fill_mode": "kb_fill", "kb_query": [], "confidence": "high",
         "multi_select": False},
    ], "batches": [], "skipped_indexes": []}
    schema = _assemble("t", ex, llm)
    field = schema["fields"][0]
    assert field["fill_type"] == "choice"
    assert field["multi_select"] is False
    assert field["options"] == ["人工智能"]


def test_assemble_blank_label_and_unit():
    ex = _extractor_with([
        {"target": "top0/r1c1#blank1", "structural_type": "text",
         "placeholder": "underline",
         "context": {"left_label": None, "section_chain": [],
                     "table_header": None, "instruction": None},
         "options": None, "preset_rows": None,
         "_blank_label": "融资金额", "_blank_unit": "万元"},
    ])
    llm = {"fields": [
        {"index": 0, "field_key": "finance_amount", "fill_type": "text",
         "fill_mode": "kb_fill", "kb_query": ["融资金额"], "confidence": "high"},
    ], "batches": [], "skipped_indexes": []}
    schema = _assemble("t", ex, llm)
    field = schema["fields"][0]
    assert field["label"] == "融资金额(万元)"
    assert field["unit"] == "万元"


def test_assemble_label_group_anchor_alignment():
    """以阶段一 anchors 为准逐锚点落 sub_fields，LLM 输出按 anchor 匹配"""
    ex = _extractor_with([
        {"target": "top0/r0c1", "structural_type": "label_group", "placeholder": None,
         "context": {"left_label": "交易信息", "section_chain": [],
                     "table_header": None, "instruction": None},
         "options": None, "preset_rows": None,
         "_anchors": ["出资基金", "投资方式"]},
    ])
    llm = {"fields": [
        {"index": 0, "field_key": "deal_info", "fill_type": "label_group",
         "fill_mode": "kb_fill", "kb_query": [], "confidence": "high",
         "sub_fields": [
             {"sub_key": "fund_name", "anchor": "出资基金",
              "kb_query": ["出资基金 名称"]},
         ]},
    ], "batches": [], "skipped_indexes": []}
    schema = _assemble("t", ex, llm)
    subs = schema["fields"][0]["sub_fields"]
    assert [s["anchor"] for s in subs] == ["出资基金", "投资方式"]
    assert subs[0]["sub_key"] == "fund_name"
    assert subs[0]["kb_query"] == ["出资基金 名称"]
    assert subs[1]["sub_key"] == "sub_2"   # LLM 未覆盖的锚点自动编号


def test_assemble_unmapped_for_llm_missed():
    """LLM 未映射的非 label 候选 → unmapped（不阻塞落盘）"""
    ex = _extractor_with([
        {"target": "top0/r0c1", "structural_type": "text", "placeholder": None,
         "context": {"left_label": "名称", "section_chain": [],
                     "table_header": None, "instruction": None},
         "options": None, "preset_rows": None},
        {"target": "top0/r1c1", "structural_type": "text", "placeholder": None,
         "context": {"left_label": "地址", "section_chain": [],
                     "table_header": None, "instruction": None},
         "options": None, "preset_rows": None},
    ])
    llm = {"fields": [
        {"index": 0, "field_key": "name", "fill_type": "text",
         "fill_mode": "kb_fill", "kb_query": [], "confidence": "high"},
    ], "batches": [], "skipped_indexes": []}
    schema = _assemble("t", ex, llm)
    assert len(schema["fields"]) == 1
    assert schema["unmapped_cells"] == [
        {"target": "top0/r1c1", "reason": "LLM未映射"}]


def test_assemble_skipped_index_to_unmapped():
    """LLM 判定装饰格（skipped_indexes）→ 不产字段，进 unmapped。
    skipped 语义：fields 中仍含该 index 条目但标记跳过"""
    ex = _extractor_with([
        {"target": "top0/r0c1", "structural_type": "text", "placeholder": None,
         "context": {"left_label": "签名栏", "section_chain": [],
                     "table_header": None, "instruction": None},
         "options": None, "preset_rows": None},
    ])
    llm = {"fields": [
        {"index": 0, "field_key": "signature", "fill_type": "text",
         "fill_mode": "kb_fill", "kb_query": [], "confidence": "high"},
    ], "batches": [], "skipped_indexes": [0]}
    schema = _assemble("t", ex, llm)
    assert schema["fields"] == []
    assert schema["unmapped_cells"] == [
        {"target": "top0/r0c1", "reason": "LLM判定为装饰格"}]


# ============================== analyze_template 端到端 ==============================

class FakeLLMClient:
    """LLMClient 桩：返回固定 content，记录调用参数"""

    def __init__(self, content: str):
        self._content = content
        self.calls = []

    async def chat_full(self, messages):
        self.calls.append({"messages": messages})
        return {"content": self._content, "finish_reason": "stop",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                          "total_tokens": 30}}


def _basic_form_doc():
    doc = _blank_doc()
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("项目名称")
    table.cell(1, 0).paragraphs[0].add_run("所属行业")
    table.cell(1, 1).paragraphs[0].add_run("□人工智能 □高端装备")
    table.cell(2, 0).paragraphs[0].add_run("公司简介")
    table.cell(2, 1).paragraphs[0].add_run("请说明公司主营业务、核心产品与竞争优势（不少于200字）")
    return doc


_LLM_OUTPUT = {
    "fields": [
        {"index": 0, "field_key": "project_name", "fill_type": "text",
         "fill_mode": "kb_fill", "kb_query": ["项目名称 企业全称"],
         "length_hint": None, "confidence": "high", "multi_select": None},
        {"index": 1, "field_key": "industry", "fill_type": "text",   # 会被结构锁定改回 choice
         "fill_mode": "kb_fill", "kb_query": ["所属行业"],
         "length_hint": None, "confidence": "high", "multi_select": False},
        {"index": 2, "field_key": "company_profile", "fill_type": "doc_section",
         "fill_mode": "kb_fill", "kb_query": ["公司 主营业务 竞争优势"],
         "length_hint": 600, "confidence": "high", "multi_select": None},
    ],
    "batches": [
        {"seq": 1, "name": "头部信息",
         "field_keys": ["project_name", "industry", "company_profile"],
         "depends_on": []},
    ],
    "skipped_indexes": [],
}


def test_analyze_template_end_to_end(tmp_path):
    doc = _basic_form_doc()
    path = str(tmp_path / "tpl.docx")
    doc.save(path)

    llm = FakeLLMClient("```json\n" + json.dumps(_LLM_OUTPUT, ensure_ascii=False) + "\n```")
    schema, mapping = asyncio.run(analyze_template(path, "测试模板", llm))

    # LLM 调用形态：system prompt + user prompt（构造期单轨注入后 settings 不再透传）
    assert len(llm.calls) == 1
    assert llm.calls[0]["messages"][0]["role"] == "system"
    assert "测试模板" in llm.calls[0]["messages"][1]["content"]

    # Schema 组装
    assert schema["schema_version"] == "2.0"
    assert schema["template_name"] == "测试模板"
    assert schema["doc_stats"] == {"top_tables": 1, "nested_tables": 0}
    fields = {f["field_key"]: f for f in schema["fields"]}
    assert fields["project_name"]["target"] == "top0/r0c1"
    assert fields["project_name"]["label"] == "项目名称"
    # 结构锁定：LLM 说 text，候选是 choice → choice
    assert fields["industry"]["fill_type"] == "choice"
    assert fields["industry"]["multi_select"] is False
    assert fields["company_profile"]["fill_type"] == "doc_section"
    assert fields["company_profile"]["length_hint"] == 600
    # 批次合法保留；llm_mapping 原样返回（重解析基线）
    assert schema["batches"][0]["field_keys"] == [
        "project_name", "industry", "company_profile"]
    assert mapping == _LLM_OUTPUT


def test_analyze_template_rerevision_passes_baseline(tmp_path):
    """重解析：user_prompt 附带上次映射基线与用户补充说明"""
    doc = _basic_form_doc()
    path = str(tmp_path / "tpl.docx")
    doc.save(path)
    llm = FakeLLMClient(json.dumps(_LLM_OUTPUT, ensure_ascii=False))
    prev = {"fields": [], "batches": [], "skipped_indexes": []}
    asyncio.run(analyze_template(path, "测试模板", llm,
                                 user_hint="行业字段选项拆分不对",
                                 prev_mapping=prev))
    user_content = llm.calls[0]["messages"][1]["content"]
    assert "上一次解析结果" in user_content
    assert "行业字段选项拆分不对" in user_content


def test_analyze_template_invalid_docx(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a docx")
    with pytest.raises(InvalidTemplateError):
        asyncio.run(analyze_template(str(bad), "t", FakeLLMClient("{}")))


def test_analyze_template_no_fillable_fields(tmp_path):
    """无表格文档 → InvalidTemplateError"""
    doc = _blank_doc()
    doc.add_paragraph("没有任何表格")
    path = str(tmp_path / "notable.docx")
    doc.save(path)
    with pytest.raises(InvalidTemplateError):
        asyncio.run(analyze_template(path, "t", FakeLLMClient("{}")))
