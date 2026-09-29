"""python/db/wiki_seed.py

Preset Wiki Schema data for different project types.
Seeded on application startup via Database.seed_wiki_schemas().

Based on §2.1 of 2.6wiki-design.md.
"""


def _recruitment_schemas() -> list[dict]:
    """招商引资项目 Wiki Schema."""
    pages = [
        {
            "page_key": "enterprise_profile",
            "title": "目标企业画像",
            "compile_hint": "提取企业基本信息、主营业务、核心优势、发展阶段、技术壁垒等关键信息，形成完整的企业画像。",
            "base_lib_search_hints": [],
            "position": 1,
        },
        {
            "page_key": "policy_match",
            "title": "招商政策匹配",
            "compile_hint": "结合企业特征，匹配适用的招商政策，标注匹配度和优惠力度。重点关注产业扶持、税收优惠、人才政策等。",
            "base_lib_search_hints": ["招商政策", "产业扶持", "税收优惠", "人才政策"],
            "position": 2,
        },
        {
            "page_key": "park_analysis",
            "title": "园区配套分析",
            "compile_hint": "分析园区与企业的匹配度，包括产业定位一致性、配套设施完善度、区位优势、交通便利性等。",
            "base_lib_search_hints": ["园区概况", "配套设施", "产业定位", "区位优势"],
            "position": 3,
        },
        {
            "page_key": "industry_chain",
            "title": "产业链分析",
            "compile_hint": "分析企业在产业链中的位置、上下游关系、集群效应、产业链完整性等。",
            "base_lib_search_hints": ["产业链", "上下游", "产业集群", "产业生态"],
            "position": 4,
        },
        {
            "page_key": "competitor_comparison",
            "title": "竞品园区对比",
            "compile_hint": "对比同类园区的优劣势，突出本园区差异化优势。从政策、区位、配套、产业生态等维度进行对比。",
            "base_lib_search_hints": ["竞品园区", "园区对比", "差异化优势"],
            "position": 5,
        },
        {
            "page_key": "landing_plan",
            "title": "落地方案建议",
            "compile_hint": "综合以上分析，给出落地方案建议，包括选址推荐、政策支持方案、时间规划、预期效益等。",
            "base_lib_search_hints": ["落地方案", "选址建议", "实施规划"],
            "position": 6,
        },
    ]
    return _build_schemas("recruitment", pages)


def _investment_schemas() -> list[dict]:
    """投资分析项目 Wiki Schema."""
    pages = [
        {
            "page_key": "company_overview",
            "title": "公司概况",
            "compile_hint": "提取公司基本信息、发展历程、股权结构、战略定位、核心业务板块等。",
            "base_lib_search_hints": [],
            "position": 1,
        },
        {
            "page_key": "core_team",
            "title": "核心团队",
            "compile_hint": "提取核心管理层背景、经验、持股情况、团队稳定性评估等。",
            "base_lib_search_hints": [],
            "position": 2,
        },
        {
            "page_key": "financial_health",
            "title": "财务健康度",
            "compile_hint": "提取关键财务指标（营收/利润/负债/现金流），进行趋势分析和同业对比。",
            "base_lib_search_hints": [],
            "position": 3,
        },
        {
            "page_key": "legal_risk",
            "title": "法律风险",
            "compile_hint": "提取诉讼、合规、知识产权等风险信息，评估风险等级和影响。",
            "base_lib_search_hints": ["法律法规", "合规要求", "知识产权"],
            "position": 4,
        },
        {
            "page_key": "valuation_ref",
            "title": "估值参考",
            "compile_hint": "结合行业基准、可比公司、历史融资等给出估值参考区间和依据。",
            "base_lib_search_hints": ["估值方法", "行业基准", "可比公司"],
            "position": 5,
        },
        {
            "page_key": "risk_summary",
            "title": "风险清单",
            "compile_hint": "综合所有风险维度（财务、法律、市场、技术、团队），形成风险清单和应对建议。",
            "base_lib_search_hints": ["风险评估", "投资风险", "风控措施"],
            "position": 6,
        },
    ]
    return _build_schemas("investment", pages)


def _general_schemas() -> list[dict]:
    """通用项目 Wiki Schema — 基础骨架，具体页面由 LLM 编译时推断。"""
    pages = [
        {
            "page_key": "project_overview",
            "title": "项目概况",
            "compile_hint": "提取项目基本信息、目标、范围、关键利益方、当前状态等。",
            "base_lib_search_hints": [],
            "position": 1,
        },
        {
            "page_key": "key_findings",
            "title": "关键发现",
            "compile_hint": "汇总文档中的关键数据、结论、洞察，按主题分类整理。",
            "base_lib_search_hints": [],
            "position": 2,
        },
        {
            "page_key": "risk_analysis",
            "title": "风险分析",
            "compile_hint": "识别和汇总项目相关风险，评估影响和可能性，提出应对建议。",
            "base_lib_search_hints": [],
            "position": 3,
        },
    ]
    return _build_schemas("general", pages)


def _build_schemas(project_type: str, pages: list[dict]) -> list[dict]:
    """Build full schema entries with IDs."""
    result = []
    for p in pages:
        result.append({
            "id": f"wiki_schema_{project_type}_{p['page_key']}",
            "project_type": project_type,
            "page_key": p["page_key"],
            "title": p["title"],
            "compile_hint": p["compile_hint"],
            "base_lib_search_hints": p["base_lib_search_hints"],
            "position": p["position"],
        })
    return result


def get_all_wiki_schemas() -> list[dict]:
    """Return all preset wiki schemas for seeding."""
    schemas = []
    schemas.extend(_recruitment_schemas())
    schemas.extend(_investment_schemas())
    schemas.extend(_general_schemas())
    return schemas
