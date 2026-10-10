"""python/db/playbook_seed.py

Preset Playbook templates (类型 × 时期任务模板) for 3.0 时期任务模型.
Seeded on startup via Database.seed_playbook_templates() — INSERT OR REPLACE
idempotent, aligned with wiki_seed.py (3.0 §3.3 / §4.1).

字段口径(§3.3):
- due_offset_days: 进入该时期后 N 天截止(pending_activation 实例激活时换算)
- due_date_fixed: 固定日期(milestone 申报窗口类,铺入时抄入 due_date,用户可改)
- recurrence_rule: {"freq": monthly|quarterly|yearly, "due_day": 1-31|"end"|"MM-DD"}
- auto_complete: {"on": "file_parsed", "match": "k1|k2"}(资料/周期=文件名关键词)
                 {"on": "report_completed", "match": "title~k1|k2"}(报告=标题关键词)
"""


def _T(tid, ptype, phase, ttype, title, *, due_offset_days=None, due_date_fixed=None,
       recurrence_rule=None, auto_complete=None, position=0):
    return {
        "id": tid, "project_type": ptype, "phase": phase, "task_type": ttype,
        "title": title, "description": "", "due_offset_days": due_offset_days,
        "due_date_fixed": due_date_fixed,
        "recurrence_rule": recurrence_rule, "auto_complete": auto_complete,
        "position": position,
    }


def _recruitment() -> list[dict]:
    return [
        _T("playbook_recruitment_before_bp", "recruitment", "before", "material",
           "上传企业 BP / 商业计划书", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "BP|商业计划"}, position=1),
        _T("playbook_recruitment_before_license", "recruitment", "before", "material",
           "收集工商注册信息(执照/章程)", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "营业执照|工商"}, position=2),
        _T("playbook_recruitment_before_credit", "recruitment", "before", "material",
           "收集企业征信/信用报告", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "征信|信用报告"}, position=3),
        _T("playbook_recruitment_before_finance", "recruitment", "before", "material",
           "收集近三年财务报表", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "财报|财务报表|审计"}, position=4),
        _T("playbook_recruitment_before_team", "recruitment", "before", "material",
           "收集核心团队背景资料", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "团队|简历|高管"}, position=5),
        _T("playbook_recruitment_before_initial_report", "recruitment", "before", "report",
           "产出项目初评报告", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~初评|评估"}, position=6),
        _T("playbook_recruitment_before_pitch", "recruitment", "before", "report",
           "产出园区推介方案", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~推介"}, position=7),
        _T("playbook_recruitment_before_visit", "recruitment", "before", "milestone",
           "完成首次实地考察/接待", position=8),
        _T("playbook_recruitment_due_diligence", "recruitment", "during", "material",
           "收集尽调材料清单", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "尽调"}, position=1),
        _T("playbook_recruitment_apply_material", "recruitment", "during", "material",
           "收集政策申报材料",
           auto_complete={"on": "file_parsed", "match": "申报"}, position=2),
        _T("playbook_recruitment_agreement", "recruitment", "during", "material",
           "收集投资/合作协议文本",
           auto_complete={"on": "file_parsed", "match": "协议|合同"}, position=3),
        _T("playbook_recruitment_dd_report", "recruitment", "during", "report",
           "产出项目尽调/评估报告", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~尽调|评估"}, position=4),
        _T("playbook_recruitment_landing_plan", "recruitment", "during", "report",
           "产出落地方案(选址/政策/时间表)", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~落地方案"}, position=5),
        _T("playbook_recruitment_signing", "recruitment", "during", "milestone",
           "完成项目签约", position=6),
        _T("playbook_recruitment_registration", "recruitment", "during", "milestone",
           "完成公司注册落地", position=7),
        _T("playbook_recruitment_land", "recruitment", "during", "milestone",
           "完成用地/厂房交付(拿地)", position=8),
        _T("playbook_recruitment_construction", "recruitment", "during", "milestone",
           "完成开工建设", position=9),
        _T("playbook_recruitment_production", "recruitment", "during", "milestone",
           "完成设备进场与投产", position=10),
        _T("playbook_recruitment_after_monthly_report", "recruitment", "after", "recurring",
           "企业月报", recurrence_rule={"freq": "monthly", "due_day": 15},
           auto_complete={"on": "file_parsed", "match": "月报|月度"}, position=1),
        _T("playbook_recruitment_after_quarterly_report", "recruitment", "after", "recurring",
           "企业季报", recurrence_rule={"freq": "quarterly", "due_day": "end"},
           auto_complete={"on": "file_parsed", "match": "季报|季度"}, position=2),
        _T("playbook_recruitment_after_yearly_data", "recruitment", "after", "recurring",
           "年度经营数据", recurrence_rule={"freq": "yearly", "due_day": "04-30"},
           auto_complete={"on": "file_parsed", "match": "年报|年度|经营数据"}, position=3),
        _T("playbook_recruitment_after_service_report", "recruitment", "after", "report",
           "产出年度项目服务报告", recurrence_rule={"freq": "yearly", "due_day": "12-31"},
           auto_complete={"on": "report_completed", "match": "title~服务报告|年度报告"},
           position=4),
        _T("playbook_recruitment_after_policy_window", "recruitment", "after", "milestone",
           "政策申报窗口(高企认定等,日期可改)", due_date_fixed="", position=5),
        _T("playbook_recruitment_after_annual_visit", "recruitment", "after", "milestone",
           "年度企业评估/走访", position=6),
    ]


def _investment() -> list[dict]:
    return [
        _T("playbook_investment_before_bp", "investment", "before", "material",
           "上传企业 BP", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "BP|商业计划"}, position=1),
        _T("playbook_investment_before_finance", "investment", "before", "material",
           "收集近三年财务报表/审计报告", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "财报|审计"}, position=2),
        _T("playbook_investment_before_legal", "investment", "before", "material",
           "收集工商/股权/涉诉信息", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "工商|股权|涉诉"}, position=3),
        _T("playbook_investment_before_team", "investment", "before", "material",
           "收集核心团队背景", due_offset_days=7,
           auto_complete={"on": "file_parsed", "match": "团队|简历"}, position=4),
        _T("playbook_investment_before_industry", "investment", "before", "material",
           "收集行业研究报告", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "行业|研报"}, position=5),
        _T("playbook_investment_before_dd_report", "investment", "before", "report",
           "产出尽职调查报告", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~尽调"}, position=6),
        _T("playbook_investment_before_ic_report", "investment", "before", "report",
           "产出投资分析报告(投决材料)", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~投资分析|投决"}, position=7),
        _T("playbook_investment_before_ic_review", "investment", "before", "milestone",
           "完成投决会评审", position=8),
        _T("playbook_investment_deal_docs", "investment", "during", "material",
           "收集投资协议/交易文件", due_offset_days=14,
           auto_complete={"on": "file_parsed", "match": "协议|交易|SPA"}, position=1),
        _T("playbook_investment_closing_conds", "investment", "during", "material",
           "收集交割先决条件材料",
           auto_complete={"on": "file_parsed", "match": "交割"}, position=2),
        _T("playbook_investment_closing_memo", "investment", "during", "report",
           "产出交割备忘", due_offset_days=14,
           auto_complete={"on": "report_completed", "match": "title~交割|备忘"}, position=3),
        _T("playbook_investment_payment", "investment", "during", "milestone",
           "完成打款交割", position=4),
        _T("playbook_investment_registration", "investment", "during", "milestone",
           "完成工商变更登记", position=5),
        _T("playbook_investment_after_monthly_report", "investment", "after", "recurring",
           "被投企业月报", recurrence_rule={"freq": "monthly", "due_day": 15},
           auto_complete={"on": "file_parsed", "match": "月报|月度"}, position=1),
        _T("playbook_investment_after_quarterly_report", "investment", "after", "recurring",
           "被投企业季报/季度财务", recurrence_rule={"freq": "quarterly", "due_day": "end"},
           auto_complete={"on": "file_parsed", "match": "季报|季度"}, position=2),
        _T("playbook_investment_after_audit", "investment", "after", "recurring",
           "年度审计报告", recurrence_rule={"freq": "yearly", "due_day": "04-30"},
           auto_complete={"on": "file_parsed", "match": "审计|年报"}, position=3),
        _T("playbook_investment_after_pm_report", "investment", "after", "report",
           "产出投后管理报告", recurrence_rule={"freq": "yearly", "due_day": "12-31"},
           auto_complete={"on": "report_completed", "match": "title~投后"}, position=4),
        _T("playbook_investment_after_exit_review", "investment", "after", "report",
           "退出路径评估",
           auto_complete={"on": "report_completed", "match": "title~退出"}, position=5),
        _T("playbook_investment_after_vam", "investment", "after", "milestone",
           "对赌/业绩承诺考核节点(按协议日期,可改)", due_date_fixed="", position=6),
        _T("playbook_investment_after_valuation", "investment", "after", "milestone",
           "年度投后回顾/估值更新", position=7),
    ]


def _general() -> list[dict]:
    return [
        _T("playbook_general_before_materials", "general", "before", "material",
           "上传项目相关资料", due_offset_days=14, position=1),
        _T("playbook_general_before_report", "general", "before", "report",
           "产出分析报告", due_offset_days=21,
           auto_complete={"on": "report_completed", "match": "title~分析|报告"}, position=2),
    ]


def get_all_playbook_templates() -> list[dict]:
    return _recruitment() + _investment() + _general()
