"""python/tests/test_log_enhancement.py — 日志双层增强单测（2.5 §3.6/§3.8）。

背景（B 层增强）：
  - app.log：校验违规明细（规则号+级别+内容）替代原先只记数量
    （runner._generate_with_retry → format_violations_brief）
  - 报告日志（UI report_logs，用户可见）：
    · 违规明细 / LLM attempt 失败经 runner._pending_warnings 缓冲 → run()
      每章节后 flush 为 warning 事件 → 路由层 warning 分支直写落库
    · section_done 检索摘要（queries/通道命中/raw→cards/充足性/补搜轮次）
      经 reports._format_retrieval_summary 格式化落库

覆盖：
  - format_violations_brief：单 / 多混合级别 / 空列表
  - runner：三次全违规 → 3 条 pending + app.log 明细；二次合规 → 单条重试
    pending；LLM attempt 失败 → pending 含（retry）；flush 幂等清空
  - _format_retrieval_summary：多轮含补搜 / 无命中+空白 reason / 空摘要
  - section_done handler：检索摘要端到端落库（in-memory DB）

不触网不触真实 LLM：llm_client 用脚本化 fake，检索用替身 runner。
"""
import asyncio
import logging
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from report_generator.runner import ProgressEvent, ReportRunner
from report_generator.validator import SectionViolation, format_violations_brief
from routers import reports as reports_module

_ZERO = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
# 稳定触发规则 2（加粗）；brief 深度下规则 3 不启用，无其他规则干扰
_VIOLATING = "**加粗强调** 这是带加粗的正文。"
_CLEAN = "这是合规的正文内容，无标题无加粗无套话。"


class _ScriptedLLM:
    """按脚本依次返回内容或抛异常的假 LLM 客户端。"""

    def __init__(self, script):
        self.script = list(script)

    async def chat_full(self, messages):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return {"content": step, "usage": dict(_ZERO), "finish_reason": "stop"}


def _make_runner(tmp_path, llm_client) -> ReportRunner:
    return ReportRunner(
        # brief 深度：规则 3（字数）不启用，违规聚焦规则 2，测试确定性
        template={"report_depth": "brief"},
        variables={}, project_id="p1",
        search_tool=None, llm_client=llm_client, llm_settings={},
        output_dir=str(tmp_path),
    )


def _gen_one_section(runner: ReportRunner):
    """驱动单节 _generate_with_retry（brief 深度 + 空引用集）。"""

    async def drive():
        return await runner._generate_with_retry(
            section={"section_id": "s1", "title": "T"},
            user_prompt="写点内容", given_refs=set(),
        )

    return asyncio.run(drive())


# ── validator.format_violations_brief ──────────────────────


class TestFormatViolationsBrief:
    def test_single_error_violation(self):
        v = SectionViolation(rule=2, message="出现 Markdown 加粗")
        assert format_violations_brief([v]) == "[规则2/错误] 出现 Markdown 加粗"

    def test_mixed_severities_joined(self):
        vs = [
            SectionViolation(rule=1, message="引用了未给定的编号: [2]"),
            SectionViolation(rule=3, message="字数 100 偏离目标 1200",
                             severity="warning"),
        ]
        assert format_violations_brief(vs) == (
            "[规则1/错误] 引用了未给定的编号: [2]；"
            "[规则3/警告] 字数 100 偏离目标 1200"
        )

    def test_empty_violations(self):
        assert format_violations_brief([]) == ""


# ── runner._generate_with_retry → _pending_warnings ────────


class TestRunnerPendingWarnings:
    def test_all_attempts_violating_buffers_three_warnings(self, tmp_path, caplog):
        """三次生成全违规：2 条重试中 + 1 条最终接受，均带违规明细（app.log 同步）。"""
        runner = _make_runner(tmp_path, _ScriptedLLM([_VIOLATING] * 3))

        with caplog.at_level(logging.INFO, logger="report_generator.runner"):
            output, finish_reason = _gen_one_section(runner)

        assert output == _VIOLATING  # 最后一次结果接受
        assert finish_reason == "stop"

        pending = runner._flush_pending_warnings()
        assert len(pending) == 3
        assert "第 1/3 次" in pending[0] and "重试中" in pending[0]
        assert "[规则2/错误]" in pending[0]  # 明细直达用户可见文案
        assert "第 2/3 次" in pending[1] and "重试中" in pending[1]
        assert "经 3 次生成仍有 1 项校验违规" in pending[2]
        assert "按最后一次结果接受" in pending[2]
        assert "[规则2/错误]" in pending[2]
        assert runner._flush_pending_warnings() == []  # flush 幂等清空

        # app.log 双层：runner 日志含违规明细（替代原先只记数量）
        detail_logs = [r for r in caplog.records if "规则2" in r.getMessage()]
        assert len(detail_logs) >= 2  # 重试 info + 最终接受 warning

    def test_second_attempt_clean_keeps_single_retry_warning(self, tmp_path):
        """首次违规第二次合规：仅 1 条重试 pending，输出为合规文本。"""
        runner = _make_runner(tmp_path, _ScriptedLLM([_VIOLATING, _CLEAN]))
        output, _ = _gen_one_section(runner)

        assert output == _CLEAN
        pending = runner._flush_pending_warnings()
        assert len(pending) == 1
        assert "重试中" in pending[0]
        assert "[规则2/错误]" in pending[0]

    def test_llm_attempt_failure_recorded_as_pending(self, tmp_path, monkeypatch):
        """LLM 调用失败（可重试）：attempt 失败进 pending 用户可见，最终成功。"""

        async def _no_sleep(_delay=0):
            return None

        monkeypatch.setattr(asyncio, "sleep", _no_sleep)  # 跳过指数退避等待
        llm = _ScriptedLLM([
            RuntimeError("timeout occurred"),
            RuntimeError("timeout occurred"),
            _CLEAN,
        ])
        runner = _make_runner(tmp_path, llm)
        output, _ = _gen_one_section(runner)

        assert output == _CLEAN
        pending = runner._flush_pending_warnings()
        assert len(pending) == 2
        assert "第 1/3 次生成调用失败（retry）" in pending[0]
        assert "timeout occurred" in pending[0]
        assert "第 2/3 次生成调用失败（retry）" in pending[1]


# ── reports._format_retrieval_summary ──────────────────────


class TestFormatRetrievalSummary:
    def test_multi_round_with_followups(self):
        summary = {
            "query_count": 3,
            "rounds": [
                {"round_no": 1, "channel_stats": {"knowledge": 2, "wiki": 1},
                 "raw_count": 3, "new_raw_count": 3, "card_count": 2,
                 "follow_up_queries": ["f1"]},
                {"round_no": 2, "channel_stats": {"internet": 1},
                 "raw_count": 1, "new_raw_count": 0, "card_count": 1,
                 "follow_up_queries": []},
            ],
            "card_count": 3,
            "sufficiency_level": "sufficient",
            "sufficiency_reason": "材料覆盖足够",
        }
        text = reports_module._format_retrieval_summary("市场分析", summary)
        assert text.startswith(
            "章节「市场分析」检索完成：queries=3, cards=3, 充足性=sufficient"
        )
        assert "（材料覆盖足够）" in text
        assert "第1轮[knowledge=2/wiki=1] raw=3(新3) cards=2" in text
        assert "补搜:f1" in text
        assert "第2轮[internet=1] raw=1(新0) cards=1" in text

    def test_no_hit_channel_and_blank_reason(self):
        summary = {
            "query_count": 1,
            "rounds": [{"round_no": 1, "channel_stats": {}, "raw_count": 0,
                        "new_raw_count": 0}],
            "card_count": 0,
            "sufficiency_level": "insufficient",
            "sufficiency_reason": "  ",
        }
        text = reports_module._format_retrieval_summary("T", summary)
        assert "第1轮[无命中] raw=0(新0)" in text
        assert "cards=0" in text
        assert "（" not in text  # 空白 reason 不追加括号
        # 无 card_count 字段（该轮未建卡）→ 不出现 cards 段
        assert "cards=" not in text.split("第1轮")[1]

    def test_empty_summary_fields(self):
        text = reports_module._format_retrieval_summary("T", {})
        assert text == "章节「T」检索完成：queries=0, cards=0, 充足性=unknown"


# ── section_done handler：检索摘要端到端落库 ───────────────


class _SectionDoneRunner:
    """yield section_done（带检索摘要）+ complete 的最小 runner 替身。"""

    outputs = {"s1": "章节正文"}

    def __init__(self, *args, **kwargs):
        pass

    async def run(self):
        yield ProgressEvent("section_start", {"section_id": "s1", "title": "T"})
        yield ProgressEvent("section_done", {
            "section_id": "s1", "title": "T",
            "completed": 1, "total": 1,
            "retrieval_summary": {
                "query_count": 2,
                "rounds": [
                    {"round_no": 1, "channel_stats": {"knowledge": 1},
                     "raw_count": 1, "new_raw_count": 1, "card_count": 1,
                     "follow_up_queries": []},
                ],
                "card_count": 1,
                "sufficiency_level": "sufficient",
                "sufficiency_reason": "ok",
            },
        })
        yield ProgressEvent("complete", {
            "content_path": "/tmp/x.md", "total_sections": 1,
            "total_words": 100, "registry_size": 1,
        })


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    # project_type="general" 无自动链接基础库（避免 in-memory DB 外键约束）
    return db.create_project(name="LogEnhancement Test", project_type="general")["id"]


@pytest.fixture
def report_id(db, project_id, tmp_path):
    rid = "r_log_enh"
    db.conn.execute(
        "INSERT INTO reports (id, project_id, template_id, title, output_format, status, "
        "progress, content_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, project_id, "due_diligence", "日志增强测试报告", "Word", "generating",
         0, str(tmp_path / "r_log_enh"), "2026-10-08T00:00:00", "2026-10-08T00:00:00"),
    )
    db.conn.commit()
    return rid


class TestSectionDoneSummaryLogged:
    def test_retrieval_summary_written_to_report_logs(
        self, db, project_id, report_id, monkeypatch, tmp_path,
    ):
        """section_done 带 retrieval_summary → 路由层格式化后落报告日志（info 级）。"""
        monkeypatch.setattr(reports_module, "_get_search_tool", lambda db_, d: None)
        monkeypatch.setattr("report_generator.runner.ReportRunner", _SectionDoneRunner)

        import deps
        monkeypatch.setattr(deps, "db", db)

        asyncio.run(reports_module._run_report_generation(
            report_id=report_id,
            template={"outline": []},
            variables={},
            project_id=project_id,
            data_dir=str(tmp_path),
            report_dir=str(tmp_path / "r_log_enh"),
            base_project_ids=[],
            section_internet={},
            internet_enabled=False,
        ))

        logs = db.list_report_logs(report_id, limit=100)
        summary_logs = [l for l in logs if "检索完成" in l["message"]]
        assert len(summary_logs) == 1
        msg = summary_logs[0]["message"]
        assert summary_logs[0]["level"] == "info"
        assert "queries=2, cards=1, 充足性=sufficient" in msg
        assert "第1轮[knowledge=1] raw=1(新1) cards=1" in msg
        # 章节基础日志（开始/完成）与检索摘要共存，不互相覆盖
        assert any(l["message"] == "开始生成章节: T" for l in logs)
        assert any(l["message"] == "章节完成: T" for l in logs)
