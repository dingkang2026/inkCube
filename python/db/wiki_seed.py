"""python/db/wiki_seed.py

Preset Wiki Schema data for different project types.
Seeded on application startup via Database.seed_wiki_schemas().

Based on §2.2 of 2.6wiki-design.md.

Page semantics (v2, E2-2026-10):
- profile pages: write-once with incremental revision (画像页)
- ledger pages: append-only timeline monitoring (监控页) — the structural
  basis for post-landing / post-investment long-term accompaniment:
  monthly reports, quarterly reports, policy payments and risk alerts are
  ingested file-by-file and appended to these pages without rewriting
  history (writing rule enforced in wiki/prompts.py).
- is_ledger flag (3.1 §3.4): 1 = 监控页,四期智能层解析范围(ledger_entries
  的来源页);画像页/复合页动态部分也标注,单一语义源(wiki_pages.page_type
  由 LLM 摄入自由给值,不可靠)。
"""

# Schema ids removed in v1→v2 (page_key renames: recruitment
# policy_match→policy_ledger, investment financial_health→financial_monitor).
# seed_wiki_schemas deletes these rows explicitly so stale hints stop being
# injected into prompts; already-compiled pages with the old keys stay in
# wiki_pages and are surfaced by orphan lint (§5.1).
RETIRED_SCHEMA_IDS = [
    "wiki_schema_recruitment_policy_match",
    "wiki_schema_investment_financial_health",
]


def _recruitment_schemas() -> list[dict]:
    """招商引资项目 Wiki Schema — 覆盖前/中/后全周期（投产后监管为主）。"""
    pages = [
        {
            "page_key": "enterprise_profile",
            "is_ledger": 0,
            "title": "目标企业画像",
            "compile_hint": (
                "画像页。提取企业基本信息（注册资本、成立时间、实控人与股权结构）、"
                "主营业务、核心优势、发展阶段、技术壁垒、资质荣誉等，形成完整企业画像。"
                "增量更新时合并修订，保留已有正确信息；实控人/股权变更、资质等"
                "时效性信息按时间保留历史沿革，标注变更时点。"
            ),
            "base_lib_search_hints": [],
            "position": 1,
        },
        {
            "page_key": "policy_ledger",
            "is_ledger": 1,
            "title": "政策匹配与兑现台账",
            "compile_hint": (
                "监控页（台账式）。两部分：①政策匹配——结合企业特征列出适用政策"
                "（产业扶持、税收优惠、人才政策等）及匹配度；②兑现台账——按时间线"
                "逐条记录承诺优惠的申报与兑付情况。新事件追加到时间线末尾，禁止改写"
                "或删除历史条目，每条记录带日期与期间标注。"
            ),
            "base_lib_search_hints": ["招商政策", "产业扶持", "税收优惠", "人才政策"],
            "position": 2,
        },
        {
            "page_key": "park_analysis",
            "is_ledger": 0,
            "title": "园区配套分析",
            "compile_hint": (
                "画像页。分析园区与企业的匹配度：产业定位一致性、配套设施完善度、"
                "区位优势、交通便利性等。增量更新时合并修订。"
            ),
            "base_lib_search_hints": ["园区概况", "配套设施", "产业定位", "区位优势"],
            "position": 3,
        },
        {
            "page_key": "industry_chain",
            "is_ledger": 0,
            "title": "产业链分析",
            "compile_hint": (
                "画像页。分析企业在产业链中的位置、上下游关系、集群效应、产业链完整性，"
                "以及本地配套现状。增量更新时合并修订。"
            ),
            "base_lib_search_hints": ["产业链", "上下游", "产业集群", "产业生态"],
            "position": 4,
        },
        {
            "page_key": "competitor_comparison",
            "is_ledger": 0,
            "title": "竞品园区对比",
            "compile_hint": (
                "画像页。对比同类园区的优劣势，突出本园区差异化优势：政策、区位、"
                "配套、产业生态等维度。增量更新时合并修订。"
            ),
            "base_lib_search_hints": ["竞品园区", "园区对比", "差异化优势"],
            "position": 5,
        },
        {
            "page_key": "landing_plan",
            "is_ledger": 0,
            "title": "落地方案建议",
            "compile_hint": (
                "画像页。综合分析给出落地方案建议：选址推荐、政策支持方案、时间规划、"
                "预期效益等。增量更新时合并修订，方案调整保留新旧版本对照。"
            ),
            "base_lib_search_hints": ["落地方案", "选址建议", "实施规划"],
            "position": 6,
        },
        {
            "page_key": "milestones",
            "is_ledger": 1,
            "title": "项目里程碑台账",
            "compile_hint": (
                "监控页（台账式）。按时间线记录项目关键节点：注册、拿地、立项、环评、"
                "开工建设、设备进场、投产、达产等，每条含日期、节点状态与说明。"
                "新节点追加到时间线末尾，禁止改写或删除历史条目。"
            ),
            "base_lib_search_hints": ["项目落地", "建设进度", "审批流程"],
            "position": 7,
        },
        {
            "page_key": "operation_monitor",
            "is_ledger": 1,
            "title": "企业经营监测",
            "compile_hint": (
                "监控页（台账式）。按期间（月/季/年）记录企业经营指标：产值、营收、"
                "税收、用电量、用工人数等，标注统计口径与数据来源，必要时给出同比/"
                "环比趋势说明。新期间数据追加，禁止改写历史期间记录。"
            ),
            "base_lib_search_hints": ["企业经营", "税收数据", "经济运行"],
            "position": 8,
        },
        {
            "page_key": "risk_watchlist",
            "is_ledger": 1,
            "title": "风险预警台账",
            "compile_hint": (
                "监控页（台账式）。按时间线记录风险事件与预警信号：停产减产、欠税"
                "欠薪、环保安全处罚、股权异动、迁址注销意向等，每条含日期、风险等级、"
                "处置进展。新事件追加，禁止改写历史条目；风险解除以新条目记录并关联原条目。"
            ),
            "base_lib_search_hints": ["企业风险", "经营异常", "行政处罚"],
            "position": 9,
        },
        {
            "page_key": "service_log",
            "is_ledger": 1,
            "title": "服务协调纪要",
            "compile_hint": (
                "监控页（台账式）。按时间线记录企业诉求与政府/园区协调事项：诉求内容、"
                "责任单位、协调过程、闭环状态。新记录追加，禁止改写历史条目。"
            ),
            "base_lib_search_hints": [],
            "position": 10,
        },
    ]
    return _build_schemas("recruitment", pages)


def _investment_schemas() -> list[dict]:
    """投资分析项目 Wiki Schema — 覆盖尽调/投决/投后/退出全流程（投后监控为主）。"""
    pages = [
        {
            "page_key": "company_overview",
            "is_ledger": 0,
            "title": "公司概况",
            "compile_hint": (
                "画像页。提取公司基本信息、发展历程、股权结构、战略定位、核心业务板块等。"
                "增量更新时合并修订，保留已有正确信息；股权结构变更按时间保留"
                "历史沿革并标注时点。"
            ),
            "base_lib_search_hints": [],
            "position": 1,
        },
        {
            "page_key": "core_team",
            "is_ledger": 0,
            "title": "核心团队",
            "compile_hint": (
                "画像页。提取核心管理层背景、经验、持股情况、团队稳定性评估。"
                "人员变动以增量修订记录并标注变更时间。"
            ),
            "base_lib_search_hints": [],
            "position": 2,
        },
        {
            "page_key": "financial_monitor",
            "is_ledger": 1,
            "title": "财务监控",
            "compile_hint": (
                "监控页（台账式）。按期间记录关键财务指标（营收/毛利/净利/负债/现金流），"
                "标注统计口径，给出预算/上期/同期对比与偏差原因。新期间数据追加，"
                "禁止改写历史期间记录；异常指标以引用块预警。"
            ),
            "base_lib_search_hints": ["财务基准", "行业财务数据"],
            "position": 3,
        },
        {
            "page_key": "legal_risk",
            "is_ledger": 1,   # 画像+监控复合页,动态台账部分入解析(3.1 §3.4)
            "title": "法律风险",
            "compile_hint": (
                "画像+监控页。①静态画像：诉讼、合规、知识产权等风险现状与等级评估；"
                "②动态台账：新发生的诉讼、处罚、合规事件按时间线追加，每条带日期与"
                "进展，禁止改写历史条目。"
            ),
            "base_lib_search_hints": ["法律法规", "合规要求", "知识产权"],
            "position": 4,
        },
        {
            "page_key": "valuation_ref",
            "is_ledger": 0,
            "title": "估值参考",
            "compile_hint": (
                "画像页。结合行业基准、可比公司、历史融资给出估值参考区间和依据。"
                "新一轮融资后以增量修订更新估值锚点，保留历史轮次记录。"
            ),
            "base_lib_search_hints": ["估值方法", "行业基准", "可比公司"],
            "position": 5,
        },
        {
            "page_key": "deal_terms",
            "is_ledger": 1,
            "title": "交易档案",
            "compile_hint": (
                "监控页（台账式）。记录交易结构关键条款：投资金额与估值、股权比例、"
                "对赌/业绩承诺（VAM）、回购、优先清算、反稀释、董事席位等，每条条款"
                "标注状态（生效/触发/豁免/到期）。条款状态变化以新条目追加并注明日期，"
                "禁止改写历史记录。"
            ),
            "base_lib_search_hints": ["投资协议", "交易条款"],
            "position": 6,
        },
        {
            "page_key": "milestone_tracker",
            "is_ledger": 1,
            "title": "里程碑与对赌跟踪",
            "compile_hint": (
                "监控页（台账式）。按时间线跟踪投后里程碑与对赌条款触发情况：业绩承诺"
                "完成度、上市/并购节点、资金用途里程碑，每条含日期、达成状态与偏差说明。"
                "新进展追加，禁止改写历史条目。"
            ),
            "base_lib_search_hints": ["业绩承诺", "投资里程碑"],
            "position": 7,
        },
        {
            "page_key": "risk_summary",
            "is_ledger": 1,   # 画像+监控复合页,动态台账部分入解析(3.1 §3.4)
            "title": "风险清单",
            "compile_hint": (
                "画像+监控页。①静态清单：综合财务、法律、市场、技术、团队维度形成"
                "风险清单与应对建议；②动态台账：新风险事件与预警信号按时间线追加"
                "（含日期、等级、处置进展），禁止改写历史条目。"
            ),
            "base_lib_search_hints": ["风险评估", "投资风险", "风控措施"],
            "position": 8,
        },
        {
            "page_key": "exit_path",
            "is_ledger": 1,
            "title": "退出路径跟踪",
            "compile_hint": (
                "监控页（台账式）。按时间线跟踪退出相关进展：IPO 辅导与申报节点、"
                "并购意向与接洽、回购触发条件评估、老股转让机会，每条含日期与状态。"
                "新进展追加，禁止改写历史条目。"
            ),
            "base_lib_search_hints": ["退出策略", "并购案例", "上市要求"],
            "position": 9,
        },
    ]
    return _build_schemas("investment", pages)


def _general_schemas() -> list[dict]:
    """通用项目 Wiki Schema — 基础骨架，具体页面由 LLM 编译时推断。"""
    pages = [
        {
            "page_key": "project_overview",
            "is_ledger": 0,
            "title": "项目概况",
            "compile_hint": "提取项目基本信息、目标、范围、关键利益方、当前状态等。",
            "base_lib_search_hints": [],
            "position": 1,
        },
        {
            "page_key": "key_findings",
            "is_ledger": 0,
            "title": "关键发现",
            "compile_hint": "汇总文档中的关键数据、结论、洞察，按主题分类整理。",
            "base_lib_search_hints": [],
            "position": 2,
        },
        {
            "page_key": "risk_analysis",
            "is_ledger": 0,
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
            "is_ledger": p["is_ledger"],   # 台账页标注(3.1 §3.4):解析范围 = is_ledger=1
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
