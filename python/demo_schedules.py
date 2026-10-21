"""python/demo_schedules.py — E3a 验收⑥ 30+ 订阅演示集种子脚本（10 产业 + 20 企业）。

用途：手动验收规模分层——摘要行计数（订阅 N · 新产出 M · 异常 K）、
分类页签/搜索/批量启停、左栏无全量列表。幂等：先清理同名前缀（「演示」）
订阅及其 runs/报告副本，再插入。run 历史混合 done（部分未读 → 新产出
计数）/ failed（异常计数，其一为连续 3 次失败已暂停态）；不造 running 行
（呼吸点由真实运行呈现，且避免孤儿审计 G1 误报）。

用法：INKCUBE_DATA_DIR 可选注入；默认 python/data。
    python demo_schedules.py
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db.database import Database
from paths import get_data_dir
# R2-P2-2：与创建端点同口径——存库 prompt 拼接产出约定（D4 存最终 prompt），
# 否则真实触发后摘要解析为空
from scheduler import OUTPUT_CONTRACT

PREFIX = "演示"
TODAY = datetime.now().strftime("%Y-%m-%d")

INDUSTRY = [
    ("半导体设备", "daily", "08:30"),
    ("新能源汽车", "daily", "09:00"),
    ("创新药研发", "weekly", "MON,THU 09:00"),
    ("光伏与储能", "weekly", "TUE,FRI 09:00"),
    ("人形机器人", "daily", "09:30"),
    ("低空经济", "weekly", "WED 10:00"),
    ("AIGC 大模型", "daily", "08:00"),
    ("算力基础设施", "weekly", "MON 10:00"),
    ("消费电子", "monthly", "1,15 09:00"),
    ("跨境电商", "weekly", "SAT 09:00"),
]

CORPORATE = [
    ("华为", "daily", "09:00"), ("比亚迪", "daily", "09:00"),
    ("宁德时代", "weekly", "MON,THU 09:00"), ("小米", "daily", "08:30"),
    ("腾讯", "weekly", "TUE 09:00"), ("阿里巴巴", "weekly", "TUE 09:00"),
    ("字节跳动", "daily", "09:30"), ("美团", "weekly", "WED 09:00"),
    ("京东", "weekly", "WED 09:00"), ("百度", "monthly", "1 09:00"),
    ("网易", "monthly", "1 09:00"), ("蔚来", "weekly", "THU 09:00"),
    ("理想汽车", "weekly", "THU 09:00"), ("小鹏汽车", "weekly", "FRI 09:00"),
    ("大疆", "monthly", "15 09:00"), ("科大讯飞", "weekly", "FRI 09:00"),
    ("寒武纪", "monthly", "1,15 10:00"), ("中芯国际", "weekly", "MON 10:00"),
    ("隆基绿能", "monthly", "15 10:00"), ("商汤科技", "monthly", "1 10:00"),
]


def _prompt(subject: str) -> str:
    return (f"跟踪{subject}的最新动态：检索近 24~48 小时新闻与公告，"
            f"筛选重要变化（产品/经营/融资/监管/技术），按模板产出报告。"
            f"\n\n{OUTPUT_CONTRACT}")


def _report(subject: str, day: str) -> str:
    return (
        f"## 摘要\n- {subject}发布新一代产品线，订单能见度提升\n"
        f"- 行业监管新规征求意见，合规成本可控\n- 上游原材料价格回落，毛利率改善\n"
        f"## 正文\n（{day} 演示数据）围绕三条要点展开的正文略。\n"
        f"## 来源\n1. 演示新闻源 · {day} · https://example.com/news\n")


def main() -> None:
    data_dir = Path(get_data_dir())
    db = Database(str(data_dir / "inkcube.db"))
    db.initialize()
    copy_dir = data_dir / "schedules"
    copy_dir.mkdir(parents=True, exist_ok=True)

    # 幂等清理：前缀订阅（runs CASCADE）+ 其报告副本 + 档案工作副本
    for s in db.list_schedules():
        if s["name"].startswith(PREFIX):
            for r in db.list_schedule_runs(s["id"], limit=-1):
                if r["report_path"]:
                    Path(r["report_path"]).unlink(missing_ok=True)
            (copy_dir / f"archive_{s['id']}.md").unlink(missing_ok=True)
            db.delete_schedule(s["id"])

    now = datetime.now()
    far = (now + timedelta(days=30)).replace(microsecond=0).isoformat()
    near = (now + timedelta(minutes=5)).replace(microsecond=0).isoformat()
    counters = {"done": 0, "failed": 0, "unread": 0}
    # R2-P2-2：呼吸点演示仅 1 个订阅 near（首个产业，5 分钟后到期）——
    # 启动补偿会对到期订阅串行真实补跑，near 过多即采搜集轰炸
    subjects = [(f"{PREFIX}产业·{name}", "industry", it, iv, n == 0)
                for n, (name, it, iv) in enumerate(INDUSTRY)]
    subjects += [(f"{PREFIX}企业·{name}", "corporate", it, iv, False)
                 for name, it, iv in CORPORATE]

    for idx, (name, cat, it, iv, near_trigger) in enumerate(subjects):
        s = db.create_schedule(
            name=name, prompt=_prompt(name.replace(PREFIX, "")),
            mode="general", category=cat, interval_type=it,
            interval_value=iv,
            next_run_at=near if near_trigger else far)
        # 混合历史：前 8 个订阅造 done（前 3 期未读 → 新产出计数）；
        # 第 9 个造单次 failed（异常计数）；第 10 个连续 3 次失败已暂停
        if idx < 8:
            for k in range((idx % 3) + 1):
                rid = f"run_demo{idx:02d}{k}"
                day = (now - timedelta(days=k + 1)).strftime("%Y-%m-%d")
                copy = copy_dir / f"{rid}.md"
                copy.write_text(_report(name, day), encoding="utf-8")
                db.insert_schedule_run(rid, s["id"], None, status="running")
                db.update_schedule_run(
                    rid, "done", report_path=str(copy),
                    summary=[f"{name}要点一（演示）",
                             f"{name}要点二（演示）"])
                if k > 0:                        # 仅最新一期保持未读
                    db.mark_run_read(rid)
                counters["done"] += 1
        elif idx == 8:
            rid = "run_demofail1"
            db.insert_schedule_run(rid, s["id"], None, status="running")
            db.update_schedule_run(
                rid, "failed", error="report.md 未产出或为空（演示）")
            counters["failed"] += 1
        elif idx == 9:
            for k in range(3):
                rid = f"run_demofuse{k}"
                db.insert_schedule_run(rid, s["id"], None, status="running")
                db.update_schedule_run(
                    rid, "failed", error=f"网络超时（演示失败 {k + 1}/3）")
                counters["failed"] += 1
            # R3-P2-2：consecutive_failures 不在 update_schedule 白名单
            # （会被静默丢弃，熔断订阅实际为 0）——经 set_schedule_state 落库
            db.set_schedule_state(s["id"], enabled=0,
                                  consecutive_failures=3)

    total = db.conn.execute(
        "SELECT COUNT(*) c FROM task_schedules").fetchone()["c"]
    unread = db.conn.execute(
        "SELECT COUNT(*) c FROM task_schedule_runs"
        " WHERE status='done' AND read_at IS NULL").fetchone()["c"]
    print(json.dumps({
        "schedules_total": total,
        "industry": len(INDUSTRY), "corporate": len(CORPORATE),
        "done_runs": counters["done"], "failed_runs": counters["failed"],
        "unread_done_runs": unread,
        "near_trigger": sum(1 for s in subjects if s[4]),   # 如实计数
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
