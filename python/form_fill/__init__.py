"""python/form_fill/ — Word 表单智能填写。

移植自已验证的 form_fill_v2 实现（设计见 docs/2.4forms-design.md §2）：
两阶段模板分析（结构提取 + LLM 语义映射）→ TemplateSchema 落盘 → 批次化填表。

模块职责：
- docx_filler:      XML 路径定位 + 各 fill_type 写入原语（原样移植）
- prompts:          语义映射 / 批次填表 system prompt + user prompt 组装
- template_analyzer: 阶段一结构提取（规则决策树）+ 阶段二 LLM 映射 + 校验组装
- fill_service:     批次编排（rule_fill → 检索 → LLM 生成 → 写入 → 报告）
"""
