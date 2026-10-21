# -*- coding: utf-8 -*-
"""填表 Prompt（移植自 form_fill_v2，合并分析/填表两份，措辞对齐项目知识库口径）。

- TEMPLATE_ANALYSIS_PROMPT + build_analysis_user_prompt：模板分析阶段二（语义映射 + 批次规划）
- TEMPLATE_FILL_PROMPT + build_fill_user_prompt：填表执行批次生成

内容与 docs/2.4forms-design.md §2 保持一致，调整判定尺度时请同步更新文档。
检索范围不含互联网：仅项目知识库 + 关联基础库（settings 联合检索由调用方组装）。
"""
import json

# ============================== 模板分析（阶段二） ==============================

TEMPLATE_ANALYSIS_PROMPT = """你是模板分析专家。用户提供了一份 Word 表格模板，系统已完成结构提取，
得到一份「候选字段清单」（每个条目对应模板中一个可填写的位置）。
你的任务：对清单做语义化分析，并规划填表执行批次，输出严格 JSON。

# 输入说明
- template_name：模板名称
- candidates：候选清单，每条含：
  - index：条目序号
  - target：XML 路径（该格在模板中的定位）
  - structural_type：结构提取的初步分类（text / label_group / table_rows / doc_section / label）
  - placeholder：占位样式（underline / bracket / colon / null）
  - context：上下文线索
    - left_label：左侧相邻标签格文本
    - section_chain：从属章节链，如 ["公司简介","基本信息","公司概况"]
    - table_header：所属数据表的表头（table_rows 时）
    - instruction：写作指引原文（doc_section 时）
  - options：勾选选项列表（choice / checkbox 时）
  - preset_rows：数据表预置行数（table_rows 时）
  - anchors：锚点标签列表（label_group 时，格内各“标签：”段原文）
  - blank_label / blank_unit：选项内嵌填空的名称与单位（target 带 #blank 后缀时）

# 任务一：字段语义化（对每条 structural_type ≠ label 的候选逐条处理）

1. field_key：业务语义命名，小写 snake_case，全局唯一，按「缩写_属性」构造
   （如 legal_rep、pre_valuation）；带 blank_label 的候选按其语义命名
   （如 blank_label=融资金额 → finance_amount）。
   label_group 的子字段：为该格输出一个父字段（fill_type=label_group），
   并在其下以 sub_fields 数组列出各子项（sub_key + anchor 原文 + kb_query），
   sub_fields 必须逐条覆盖 anchors 中的全部锚点，不得遗漏或自造。
2. fill_type：确认结构分类，仅在明显错分时修正。doc_section 仅限结构提取已标注
   instruction 的候选，不得把 text 候选改判为 doc_section——预期产出长文时保持
   text 并在 length_hint 给出字数。若你判断某空值格实为装饰/签名栏，
   将其 index 写入 skipped_indexes，不产出字段。
   choice / checkbox 候选另输出 multi_select（true/false）：按选项语义判断
   是否可多项同时成立——如“同意总部迁入”与“同意子公司迁入”可同时选 → true；
   融资轮次互斥 → false。其他类型输出 null。
3. fill_mode：
   - rule_fill：仅当内容为纯规则可定且与项目资料无关（如申请时间 → 规则 hint 取 today）；
     此时在 rule_hint 中注明规则（如 "today"）。
   - 其余一律 kb_fill。禁止凭"知识库可能没有"预判跳过检索——
     知识库内容是动态的，任何字段都可能查到资料；是否留白由运行时检索结果决定。
4. kb_query：知识库检索词，1~3 条中文短语，面向项目知识库与关联基础库
   （可能包含：商业计划书、BP、融资材料、财务报表、行业报告、访谈纪要、
   项目背景资料等）。doc_section 需按 instruction 的子结构从多角度出词；
   rule_fill 留空数组。
5. length_hint：doc_section 建议字数（按 instruction 繁简给 300~1500）；其余为 null。
6. confidence：high / medium / low。语义拿不准时如实给 low
   （运行时照常检索，结果仅标记供人工复核）。

# 任务二：批次规划（决定填表执行顺序，全部字段必须恰好归属一个批次）

1. 以章节为批边界（section_chain 顶层）；单批超过 12 个字段、或含 2 个以上
   doc_section 的，按二级章节再拆；相邻不足 2 字段的小批合并；
2. 批内允许混合类型（text + table_rows + doc_section 同批）；
3. depends_on：仅当本批属"总结/结论/估值合理性"类、需引用其他批次产出时声明
   （值为其他批次 seq）；普通章节留空数组；不得成环；
4. batches 按文档顺序排列。

# 输出格式（严格 JSON，不包含任何额外说明文字）
{
  "fields": [
    {"index": 1, "field_key": "project_name", "fill_type": "text",
     "fill_mode": "kb_fill", "kb_query": ["企业全称 项目名称"],
     "length_hint": null, "confidence": "high", "multi_select": null},
    {"index": 7, "field_key": "deal_info", "fill_type": "label_group",
     "fill_mode": "kb_fill", "kb_query": [], "length_hint": null,
     "confidence": "high",
     "sub_fields": [
       {"sub_key": "fund_name", "anchor": "出资基金", "kb_query": ["出资基金 所属基金"]},
       {"sub_key": "invest_way", "anchor": "投资方式", "kb_query": ["投资方式 增资 接老股"]}
     ]}
  ],
  "batches": [
    {"seq": 1, "name": "头部信息", "field_keys": ["project_name", "deal_info"],
     "depends_on": []}
  ],
  "skipped_indexes": []
}

# 示例（节选，展示判定尺度）

输入候选：
{"index": 5, "target": "top0/r1c1", "structural_type": "text", "placeholder": null,
 "context": {"left_label": "项目负责人", "section_chain": []}}
{"index": 6, "target": "top0/r4c1", "structural_type": "doc_section",
 "context": {"section_chain": ["实控人及核心团队"],
  "instruction": "说明实控人、高管人员、核心技术人员的教育背景、工作履历、主要技术成果（如适用）、专长等"}}

输出：
index=5 → {"field_key": "project_leader", "fill_type": "text", "fill_mode": "kb_fill",
           "kb_query": ["项目负责人 团队负责人"], "length_hint": null,
           "confidence": "medium"}
（负责人信息知识库未必有，但禁止预判跳过：照常检索，confidence 如实给 medium）
index=6 → {"field_key": "core_team", "fill_type": "doc_section", "fill_mode": "kb_fill",
           "kb_query": ["实控人 背景", "高管 履历", "核心技术人员"],
           "length_hint": 600, "confidence": "high"}

# 修订模式（当输入包含“上一次解析结果”时启用）
上一次结果整体可用，用户提示通常只指出其中局部问题（如某字段类型判错、某选项组拆分不对、
某子字段遗漏、某个 field_key 语义不准）。处理规则：
1. 仅按用户提示调整涉及的字段/子字段/选项/批次，未提及的部分原样保留；
2. field_key 等命名保持稳定（下游已按其落库与回显），确需改名时才改；
3. 仍输出完整严格 JSON（fields/batches/skipped_indexes），结构与首次解析完全一致，
   不得只输出差异部分。
"""


def build_analysis_user_prompt(template_name: str, candidates: list[dict],
                               prev_mapping: dict = None, user_hint: str = None) -> str:
    """组装分析 user_prompt：模板名 + 候选清单 JSON
    （重解析时附加：上次语义映射基线 + 用户补充/修正说明）"""
    parts = [
        f"# 模板\n{template_name}\n\n",
        f"# 候选字段清单\n{json.dumps(candidates, ensure_ascii=False)}\n",
    ]
    if prev_mapping:
        parts.append(
            "\n# 上一次解析结果（LLM 产出的 fields/batches 基线；"
            "用户提示通常是其局部修正）\n"
            + json.dumps(prev_mapping, ensure_ascii=False) + "\n"
        )
    if user_hint:
        if prev_mapping:
            parts.append(f"\n# 用户补充说明（优先级最高，按此修正/补充上面的解析）\n{user_hint}\n")
        else:
            # 无基线（首次解析失败重试/基线脏数据降级）：不存在“上面的解析”，改用要求措辞
            parts.append(f"\n# 用户补充说明（本次解析需重点满足的要求）\n{user_hint}\n")
    return ''.join(parts)


# ============================== 填表批次生成 ==============================

TEMPLATE_FILL_PROMPT = """你是投研助理，任务：根据项目知识库资料，为 Word 表单当前批次的字段生成填写值。

## 输出要求
输出严格 JSON 对象：{"field_key": value, ...}，只包含本批字段清单中列出的 field_key，不要输出任何其他内容。
各类型 value 约定：
- text：字符串。字段名含单位（如"融资金额(万元)"）时只输出数值不带单位；有"建议字数"的长文按字数组织，可用 \\n 分段
- choice / checkbox：{"selected": ["选项"], "other": null}。selected 只能从 options 中选，多选语义以 multi_select 为准（false 时最多 1 项）；选项均不符时 selected 为空、值放 other
- label_group：{"sub_key": "值", ...}，sub_key 以字段清单为准；无资料的 sub_key 输出 null
- table_rows：[[列1值, 列2值, ...], ...]，按 columns 顺序每行等长；无资料输出 null
- doc_section：字符串。有"子节"清单时逐节写作并保留"一、二、…"序号，用 \\n 分隔段落；无子节时按写作指引成文

## 资料纪律
- 只依据"项目资料"与"项目基础信息"填写；资料不足的字段输出 null——宁缺毋假，严禁编造数据
- 资料归属与"项目主体"不符、或无法确认归属的（他企执照/证书、关联方与客户供应商文件、个人材料等），一律不得作为字段依据
- 金额/比例等数字：模板已印单位的只写数值；未印单位的带单位
- 文风：书面、凝练、结论先行；禁止出现"根据资料""资料显示"等检索痕迹表述"""

# 检索资料总长截断不再硬编码：调用方传 kb_chars_max（fill_service.derive_kb_chars_budget
# 按上下文窗口推导，D2 口径纪律：tokens 判断全经 llm/budget.py，此处只消费换算结果）


def _field_view(field: dict) -> str:
    """单个字段 → prompt 清单行（只给语义信息，不暴露 target 等内部细节）"""
    parts = [field['field_key'], field.get('label') or '-', field['fill_type']]
    if field.get('options'):
        parts.append('options: ' + '；'.join(field['options']))
    if isinstance(field.get('multi_select'), bool):
        parts.append(f'multi_select: {str(field["multi_select"]).lower()}')
    if field.get('unit'):
        parts.append(f'单位: {field["unit"]}')
    if field.get('length_hint'):
        parts.append(f'建议字数≈{field["length_hint"]}')
    if field.get('kb_query'):
        parts.append('检索词: ' + ' / '.join(field['kb_query']))
    if field['fill_type'] == 'label_group':
        subs = '；'.join(f"{s['sub_key']}({(s.get('anchor') or '')[:20]})"
                         for s in field.get('sub_fields') or [])
        parts.append(f'sub_keys: {subs}')
    if field['fill_type'] == 'table_rows':
        parts.append('columns: ' + ' | '.join(field.get('columns') or []))
        if field.get('preset_rows'):
            parts.append(f'预置行数: {field["preset_rows"]}')
    if field['fill_type'] == 'doc_section':
        if field.get('sections'):
            parts.append('子节: ' + ' / '.join(s[:24] for s in field['sections']))
        elif field.get('instruction'):
            parts.append(f'写作指引: {field["instruction"][:60]}')
    return '- ' + ' | '.join(parts)


def build_fill_user_prompt(template_name: str, batch_fields: list, kb_context: str,
                           project_context: dict, filled_digest: str,
                           kb_chars_max: int) -> str:
    """组装填表 user_prompt：已完成摘要 + 检索资料 + 项目主体锚定 + 基础信息 + 字段清单

    项目主体段为字段值的归属锚点：检索资料常混入他企文件（关联方/客户/供应商/
    误入文件），与主体声明组合后模型可按「来源归属」甄别，不采用他方资料（§2.4）。
    """
    lines = [f'# 模板：{template_name}', '']

    if filled_digest:
        lines += ['## 已完成字段（供参考衔接，勿重复输出）', filled_digest, '']

    if kb_context:
        lines += ['## 项目资料（知识库检索）', kb_context[:kb_chars_max], '']
    else:
        lines += ['## 项目资料', '（本次检索未获得资料）', '']

    # 项目主体锚定（归属甄别依据）：company_name/credit_code 取自项目基础信息，
    # 缺失时降级为通用甄别指令照给——检索资料的来源标注（_retrieve）配合甄别
    ctx = project_context or {}
    name = str(ctx.get('company_name') or '').strip()
    code = str(ctx.get('credit_code') or '').strip()
    lines += ['## 项目主体']
    if name:
        lines.append(f'- 名称：{name}')
    if code:
        lines.append(f'- 统一社会信用代码：{code}')
    if not name and not code:
        lines.append('- （项目基础信息未登记名称与信用代码，以下方基础信息为准）')
    lines.append('甄别要求：检索资料可能包含关联方、客户、供应商、他企文件或个人材料；'
                 '凡归属与项目主体不符、或无法确认归属的资料，不得作为字段填写依据。')
    lines.append('')

    if project_context:
        lines += ['## 项目基础信息（rule_fill 已直接填入，可引用）']
        lines += [f'- {k}: {str(v)[:80]}' for k, v in ctx.items() if v]
        lines.append('')

    lines += ['## 本批字段']
    lines += [_field_view(f) for f in batch_fields]
    lines += ['', '请输出 JSON 值集。']
    return '\n'.join(lines)
