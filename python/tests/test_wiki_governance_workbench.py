"""发现项治理工作台回归（2.6 §5.5/§4.3，E2-2026-10）。

覆盖四条链路：
1. parser：REVIEW 块 EVIDENCE 行提取/剥离 + RESOLVES 编号解析（含逗号并列容错）
2. database.replace_wiki_page_text：确定性精确替换 + user_edited 保护 + 版本快照
3. routers.wiki.adjudicate_review：矛盾裁决端点（成功/失配保留/各类拒绝）
4. ingest 缺口闭环：open_gaps 注入 prompt + RESOLVES 按编号确定性归档
"""
import asyncio
import json
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from wiki.ingest import WikiIngestor
from wiki.parser import parse_review_blocks, parse_resolves_blocks
from wiki.prompts import build_ingest_generation_prompt
from routers.wiki import ReviewAdjudicateRequest, adjudicate_review


def _run(coro):
    """Synchronously drive a coroutine (project uses plain pytest, no pytest-asyncio)."""
    return asyncio.run(coro)


@pytest.fixture
def db():
    """Create a fresh in-memory database with all tables."""
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    proj = db.create_project(name="Test Project", project_type="general")
    return proj["id"]


# ── 1. parser：EVIDENCE / RESOLVES ──


class TestEvidenceParsing:
    def test_evidence_extracted_and_stripped(self):
        """两行 EVIDENCE 提取为 [{page_key, quote}]，并从 description 剥离。"""
        output = (
            "---REVIEW: contradiction | 成立时间矛盾---\n"
            "证书与年报对成立时间说法不一\n"
            "EVIDENCE: [[p_cert]] 「公司成立于 2008 年」\n"
            "EVIDENCE: [[p_bp]] 「BP 载明 2009 年设立」\n"
            "---END REVIEW---"
        )
        blocks = parse_review_blocks(output)

        assert len(blocks) == 1
        b = blocks[0]
        assert b["type"] == "contradiction"
        assert b["description"] == "证书与年报对成立时间说法不一"
        assert "EVIDENCE" not in b["description"]
        assert b["evidence"] == [
            {"page_key": "p_cert", "quote": "公司成立于 2008 年"},
            {"page_key": "p_bp", "quote": "BP 载明 2009 年设立"},
        ]

    def test_evidence_english_quotes(self):
        """英文双引号容错（LLM 输出格式漂移常态）。"""
        output = (
            "---REVIEW: contradiction | Founding year conflict---\n"
            "Sources disagree on founding year\n"
            'EVIDENCE: [[p_cert]] "Incorporated in 2008"\n'
            'EVIDENCE: [[p_bp]] "BP states 2009"\n'
            "---END REVIEW---"
        )
        blocks = parse_review_blocks(output)

        assert blocks[0]["evidence"] == [
            {"page_key": "p_cert", "quote": "Incorporated in 2008"},
            {"page_key": "p_bp", "quote": "BP states 2009"},
        ]

    def test_review_without_evidence(self):
        """无 EVIDENCE 行 → evidence 为空列表（存量兼容路径）。"""
        output = (
            "---REVIEW: suggestion | 建议补充高管信息---\n"
            "年报提到高管变动，建议补充\n"
            "---END REVIEW---"
        )
        blocks = parse_review_blocks(output)

        assert len(blocks) == 1
        assert blocks[0]["evidence"] == []

    def test_resolves_single_and_comma(self):
        """RESOLVES 单个编号与逗号并列（LLM 格式漂移容忍）。"""
        output = "---RESOLVES: 1---\n正文\n---RESOLVES: 2, 3---\n更多正文"
        assert parse_resolves_blocks(output) == ["1", "2", "3"]

    def test_resolves_dedup_preserves_order(self):
        """重复编号去重，保持首次出现顺序。"""
        output = "---RESOLVES: 1---\n---RESOLVES: 1, 2---"
        assert parse_resolves_blocks(output) == ["1", "2"]

    def test_evidence_only_review_kept(self):
        """LLM 漂移跳过描述行（仅 EVIDENCE 行）→ 发现项保留，不静默丢弃。

        修复回归（review #4）：原 `if title and description` 把纯 evidence 块
        丢弃且无日志，连「留待人工」的机会都没有，违背 §4.4。
        """
        output = (
            "---REVIEW: contradiction | 只有证据---\n"
            "EVIDENCE: [[p_a]] 「甲说法」\n"
            "EVIDENCE: [[p_b]] 「乙说法」\n"
            "---END REVIEW---"
        )
        blocks = parse_review_blocks(output)

        assert len(blocks) == 1
        assert blocks[0]["description"] == ""
        assert len(blocks[0]["evidence"]) == 2


# ── 2. database.replace_wiki_page_text ──


class TestReplaceWikiPageText:
    def test_replace_success(self, db, project_id):
        """替换成功：内容更新 + status=user_edited + compile_version+1 + 版本快照。"""
        page = db.create_or_update_wiki_page(
            project_id, "p_cert", "证书页",
            content="公司成立于 2008 年。注册资本未知。",
            status="compiled", compile_version=3,
        )

        result = db.replace_wiki_page_text(
            project_id, "p_cert", "成立于 2008 年", "成立于 2009 年",
            change_summary="矛盾裁决: 以 [[p_bp]] 为准",
        )

        assert result["success"] is True
        assert result["replaced_count"] == 1
        assert result["version"] == 4

        updated = db.get_wiki_page(project_id, "p_cert")
        assert "成立于 2009 年" in updated["content"]
        assert "成立于 2008 年" not in updated["content"]
        assert updated["status"] == "user_edited"

        versions = db.list_wiki_page_versions(updated["id"])
        assert len(versions) == 1
        assert versions[0]["version"] == 4
        assert "矛盾裁决" in versions[0]["change_summary"]

    def test_replace_all_occurrences(self, db, project_id):
        """同一原文出现多次 → 全量替换，replaced_count 如实上报。"""
        db.create_or_update_wiki_page(
            project_id, "p_multi", "多出页",
            content="成立于 2008 年。后文又提到成立于 2008 年。",
            status="compiled",
        )

        result = db.replace_wiki_page_text(project_id, "p_multi", "成立于 2008 年", "成立于 2009 年")

        assert result["success"] is True
        assert result["replaced_count"] == 2
        updated = db.get_wiki_page(project_id, "p_multi")
        assert updated["content"].count("成立于 2009 年") == 2
        assert "2008" not in updated["content"]

    def test_replace_text_not_found_no_write(self, db, project_id):
        """quote 失配（页面已被编辑）→ success=False 且不产生任何写操作。"""
        db.create_or_update_wiki_page(
            project_id, "p_cert", "证书页", content="已被用户大幅改写。",
            status="user_edited", compile_version=2,
        )

        result = db.replace_wiki_page_text(project_id, "p_cert", "成立于 2008 年", "成立于 2009 年")

        assert result["success"] is False
        assert result["reason"] == "text_not_found"
        unchanged = db.get_wiki_page(project_id, "p_cert")
        assert unchanged["content"] == "已被用户大幅改写。"
        assert unchanged["compile_version"] == 2
        assert db.list_wiki_page_versions(unchanged["id"]) == []

    def test_replace_page_not_found(self, db, project_id):
        """页面不存在 → success=False, reason=page_not_found。"""
        result = db.replace_wiki_page_text(project_id, "p_missing", "旧文", "新文")

        assert result["success"] is False
        assert result["reason"] == "page_not_found"

    def test_replace_retry_already_applied_no_new_snapshot(self, db, project_id):
        """重试语义（修复 review #2）：old 已不在而 new 已在（上轮已替换）→
        success=True + already_applied，不产生写操作与快照——否则三侧以上
        矛盾部分失败后重试将永久 partial。"""
        page = db.create_or_update_wiki_page(
            project_id, "p_side", "侧页", content="成立于 2009 年。", status="compiled",
        )

        result = db.replace_wiki_page_text(project_id, "p_side", "成立于 2008 年", "成立于 2009 年")

        assert result["success"] is True
        assert result["reason"] == "already_applied"
        assert db.list_wiki_page_versions(page["id"]) == []


# ── 3. routers.wiki.adjudicate_review ──


def _setup_three_side_contradiction(db, project_id: str, review_id: str = "rev3") -> str:
    """三页各执一词 + 三侧 evidence（多侧部分失败重试场景用）。"""
    db.create_or_update_wiki_page(
        project_id, "p_a", "A 页", content="年份是 2008 年。", status="compiled",
    )
    db.create_or_update_wiki_page(
        project_id, "p_b", "B 页", content="年份是 2009 年。", status="compiled",
    )
    db.create_or_update_wiki_page(
        project_id, "p_c", "C 页", content="材料称 2007 年。", status="compiled",
    )
    db.create_or_update_wiki_review(
        review_id=review_id,
        project_id=project_id,
        review_type="contradiction",
        title="年份三方矛盾",
        description="三页各执一词",
        evidence=[
            {"page_key": "p_a", "quote": "年份是 2008 年"},
            {"page_key": "p_b", "quote": "年份是 2009 年"},
            {"page_key": "p_c", "quote": "材料称 2007 年"},
        ],
    )
    return review_id


def _setup_contradiction(db, project_id: str, review_id: str = "rev_adj") -> str:
    """两页各载不同成立年份 + 带 evidence 的 contradiction，返回 review_id。"""
    db.create_or_update_wiki_page(
        project_id, "p_cert", "证书页", content="公司成立于 2008 年。", status="compiled",
    )
    db.create_or_update_wiki_page(
        project_id, "p_bp", "BP 页", content="BP 载明 2009 年设立。", status="compiled",
    )
    db.create_or_update_wiki_review(
        review_id=review_id,
        project_id=project_id,
        review_type="contradiction",
        title="成立时间矛盾",
        description="两侧对成立年份说法不一",
        evidence=[
            {"page_key": "p_cert", "quote": "公司成立于 2008 年"},
            {"page_key": "p_bp", "quote": "BP 载明 2009 年设立"},
        ],
    )
    return review_id


class TestAdjudicateEndpoint:
    def test_adjudicate_success(self, db, project_id, monkeypatch):
        """chosen=0：被否侧 p_bp 被确定性替换 + user_edited + 归档 adjudicated。"""
        review_id = _setup_contradiction(db, project_id)
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        result = _run(adjudicate_review(
            project_id, review_id, ReviewAdjudicateRequest(chosen=0),
        ))

        assert result["success"] is True
        assert len(result["details"]) == 1
        assert result["details"][0]["success"] is True

        rejected = db.get_wiki_page(project_id, "p_bp")
        assert rejected["content"] == "公司成立于 2008 年。"
        assert rejected["status"] == "user_edited"
        assert rejected["compile_version"] == 2

        chosen_page = db.get_wiki_page(project_id, "p_cert")
        assert chosen_page["status"] == "compiled"

        review = db.get_wiki_review(review_id)
        assert review["resolved"] == 1
        assert review["resolved_action"] == "adjudicated"

        versions = db.list_wiki_page_versions(rejected["id"])
        assert len(versions) == 1
        assert "以 [[p_cert]] 为准" in versions[0]["change_summary"]

    def test_adjudicate_reverse_choice(self, db, project_id, monkeypatch):
        """chosen=1：语义反转——以 p_bp 为准，替换 p_cert。"""
        review_id = _setup_contradiction(db, project_id)
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        result = _run(adjudicate_review(
            project_id, review_id, ReviewAdjudicateRequest(chosen=1),
        ))

        assert result["success"] is True
        rejected = db.get_wiki_page(project_id, "p_cert")
        assert rejected["content"] == "BP 载明 2009 年设立。"
        assert rejected["status"] == "user_edited"
        review = db.get_wiki_review(review_id)
        assert review["resolved_action"] == "adjudicated"

    def test_adjudicate_mismatch_keeps_pending(self, db, project_id, monkeypatch):
        """被否侧 quote 失配 → 业务失败（200 + success=False），发现项保留待处理。"""
        review_id = _setup_contradiction(db, project_id)
        # 用户已大幅改写被否侧页面 → quote 不复存在
        bp = db.get_wiki_page(project_id, "p_bp")
        db.update_wiki_page_content(page_id=bp["id"], content="BP 已重写，无原文。", status="user_edited")
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        result = _run(adjudicate_review(
            project_id, review_id, ReviewAdjudicateRequest(chosen=0),
        ))

        # §4.4 宁可留待人工不误归档：业务失败非系统错误
        assert result["success"] is False
        assert result["reason"] == "partial_or_failed"
        assert result["details"][0]["reason"] == "text_not_found"

        review = db.get_wiki_review(review_id)
        assert review["resolved"] == 0
        # 被否侧未被误改
        assert db.get_wiki_page(project_id, "p_bp")["content"] == "BP 已重写，无原文。"

    def test_adjudicate_legacy_no_evidence(self, db, project_id, monkeypatch):
        """存量发现项（无 evidence）→ 400 引导人工核对，不允许盲改。"""
        db.create_or_update_wiki_review(
            review_id="rev_legacy", project_id=project_id,
            review_type="contradiction", title="旧版矛盾", description="无结构化证据",
        )
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        resp = _run(adjudicate_review(
            project_id, "rev_legacy", ReviewAdjudicateRequest(chosen=0),
        ))

        assert resp.status_code == 400
        assert json.loads(resp.body)["code"] == "FINDING_NO_EVIDENCE"

    def test_adjudicate_rejects_non_contradiction(self, db, project_id, monkeypatch):
        """非矛盾类发现项不支持裁决 → 400。"""
        db.create_or_update_wiki_review(
            review_id="rev_sugg", project_id=project_id,
            review_type="suggestion", title="建议", description="desc",
        )
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        resp = _run(adjudicate_review(
            project_id, "rev_sugg", ReviewAdjudicateRequest(chosen=0),
        ))

        assert resp.status_code == 400
        assert json.loads(resp.body)["code"] == "FINDING_NOT_ADJUDICABLE"

    def test_adjudicate_rejects_already_resolved(self, db, project_id, monkeypatch):
        """已处理发现项 → 409，不允许重复裁决。"""
        review_id = _setup_contradiction(db, project_id)
        db.resolve_wiki_review(review_id, "dismiss")
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        resp = _run(adjudicate_review(
            project_id, review_id, ReviewAdjudicateRequest(chosen=0),
        ))

        assert resp.status_code == 409
        assert json.loads(resp.body)["code"] == "FINDING_ALREADY_RESOLVED"

    def test_adjudicate_chosen_out_of_range(self, db, project_id, monkeypatch):
        """chosen 索引越界 → 400。"""
        review_id = _setup_contradiction(db, project_id)
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        resp = _run(adjudicate_review(
            project_id, review_id, ReviewAdjudicateRequest(chosen=5),
        ))

        assert resp.status_code == 400
        assert json.loads(resp.body)["code"] == "CHOSEN_INDEX_OUT_OF_RANGE"

    def test_adjudicate_rejects_while_compiling(self, db, project_id, monkeypatch):
        """编译进行中裁决 → 409（修复 review #1'：与并发摄入互斥，防覆写竞态）。"""
        review_id = _setup_contradiction(db, project_id)
        db.create_wiki_compile_task(project_id, "manual")  # status=queued
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        resp = _run(adjudicate_review(
            project_id, review_id, ReviewAdjudicateRequest(chosen=0),
        ))

        assert resp.status_code == 409
        assert json.loads(resp.body)["code"] == "COMPILING_IN_PROGRESS"
        # 未产生任何写操作
        assert db.get_wiki_review(review_id)["resolved"] == 0

    def test_adjudicate_partial_then_retry_succeeds(self, db, project_id, monkeypatch):
        """三侧部分失败 → 修复失配侧 → 重试：已应用侧 already_applied 达成，全部归档。

        修复回归（review #2）：逐侧独立 commit 无整体事务，重试时已成功侧
        quote 必然 count==0，原实现将永久 partial。
        """
        review_id = _setup_three_side_contradiction(db, project_id)
        # 第一轮：p_c 的 quote 已被改写 → partial（p_b 已被替换为选中侧结论）
        c = db.get_wiki_page(project_id, "p_c")
        db.update_wiki_page_content(page_id=c["id"], content="已被改写", status="user_edited")
        monkeypatch.setattr("routers.wiki.get_db", lambda: db)

        r1 = _run(adjudicate_review(project_id, review_id, ReviewAdjudicateRequest(chosen=0)))
        assert r1["success"] is False
        assert db.get_wiki_review(review_id)["resolved"] == 0

        # 用户修复失配侧（内容补回 quote 原文）后重试
        db.update_wiki_page_content(page_id=c["id"], content="材料称 2007 年。", status="user_edited")
        r2 = _run(adjudicate_review(project_id, review_id, ReviewAdjudicateRequest(chosen=0)))

        assert r2["success"] is True
        assert "already_applied" in [d.get("reason") for d in r2["details"]]
        review = db.get_wiki_review(review_id)
        assert review["resolved"] == 1
        assert review["resolved_action"] == "adjudicated"


# ── 4. prompts：缺口清单注入 ──


class TestGapPromptInjection:
    def _build(self, open_gaps):
        return build_ingest_generation_prompt(
            source_text="公司成立于 2008 年。",
            filename="cert_scan.jpg",
            analysis_report="分析：识别出公司实体。",
            purpose="",
            overview="",
            existing_content="",
            schema_hints="",
            base_lib_context="",
            project_type="general",
            open_gaps=open_gaps,
        )

    @staticmethod
    def _joined(messages) -> str:
        return "\n".join(m.get("content", "") for m in messages)

    def test_gap_section_injected(self):
        """open_gaps 非空 → prompt 含「待补缺口」段与编号清单。"""
        messages = self._build([
            {"index": 1, "review_id": "rev_gap1", "title": "缺少注册资本", "description": "年报未提及注册资本"},
        ])
        joined = self._joined(messages)

        assert "待补缺口" in joined
        assert "缺少注册资本" in joined
        assert "rev_gap1" not in joined  # 内部 ID 不暴露给 LLM
        assert "RESOLVES" in joined

    def test_no_gaps_no_section(self):
        """无未解决缺口 → prompt 不含待补缺口段（不诱导无意义输出）。"""
        messages = self._build([])
        assert "待补缺口" not in self._joined(messages)


# ── 5. ingest：RESOLVES 闭环 ──

# Stage 1 需 ≥50 字符，否则被视为 LLM 静默失败
_STAGE1_OUTPUT = "分析结果：" + "关键实体与概念识别，交叉发现与矛盾记录。" * 3

_STAGE2_PLAIN = """---PAGE: src_cert_scan---
type: source
title: 测试证书要点

- 要点一：公司成立于 2008 年
---END PAGE---
"""

_STAGE2_RESOLVES_1 = _STAGE2_PLAIN + "\n---RESOLVES: 1---\n"

_STAGE2_RESOLVES_UNKNOWN = _STAGE2_PLAIN + "\n---RESOLVES: 99---\n"


def _add_done_file(db, project_id: str, filename: str = "cert_scan.jpg") -> str:
    """Create a file record in 'done' status (parse succeeded)."""
    f = db.create_file(project_id, filename, ".jpg", 1024, f"hash_{filename}")
    db.update_file_status(f["id"], "done")
    return f["id"]


def _add_text_chunk(db, project_id: str, file_id: str, content: str = "公司成立于 2008 年。"):
    """Insert one FTS chunk so _read_full_text finds text."""
    db.conn.execute(
        "INSERT INTO chunks_fts (chunk_id, content, project_id, filename, content_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"chunk_{file_id}_0", content, project_id, "cert_scan.jpg", "paragraph"),
    )
    db.conn.commit()


def _make_capture_ingestor(monkeypatch, db, stage2_output: str, data_dir: str):
    """Stub LLM：stage1 返回分析文本；stage2 记录 messages 并返回指定输出。"""
    captured = {}

    async def fake_chat(self, messages, settings, task_id, stage):
        if stage == "stage1":
            return _STAGE1_OUTPUT
        captured["messages"] = messages
        return stage2_output

    monkeypatch.setattr(WikiIngestor, "_chat_with_retry", fake_chat)
    return WikiIngestor(db, data_dir, None, {}), captured


class TestGapClosure:
    def test_open_gap_injected_and_resolved(self, db, project_id, monkeypatch, tmp_path):
        """预置未解决 data-gap → 注入 prompt + RESOLVES: 1 → auto_resolved 归档。"""
        db.create_or_update_wiki_review(
            review_id="rev_gap", project_id=project_id,
            review_type="data-gap", title="缺少注册资本",
            description="年报未提及注册资本，需补充材料",
        )
        ingestor, captured = _make_capture_ingestor(
            monkeypatch, db, _STAGE2_RESOLVES_1, str(tmp_path))
        file_id = _add_done_file(db, project_id)
        _add_text_chunk(db, project_id, file_id)

        result = _run(ingestor.ingest_file(project_id, file_id, "task_1"))

        # 编号清单确实注入了 Stage 2 prompt
        prompt_text = "\n".join(m.get("content", "") for m in captured["messages"])
        assert "待补缺口" in prompt_text
        assert "缺少注册资本" in prompt_text

        # RESOLVES 按编号确定性归档
        assert result["gaps_resolved"] == 1
        gap = db.get_wiki_review("rev_gap")
        assert gap["resolved"] == 1
        assert gap["resolved_action"] == "auto_resolved"

    def test_no_open_gaps_no_injection(self, db, project_id, monkeypatch, tmp_path):
        """无未解决缺口 → prompt 无待补缺口段，RESOLVES 段也不该出现在输出约定里。"""
        ingestor, captured = _make_capture_ingestor(
            monkeypatch, db, _STAGE2_PLAIN, str(tmp_path))
        file_id = _add_done_file(db, project_id)
        _add_text_chunk(db, project_id, file_id)

        result = _run(ingestor.ingest_file(project_id, file_id, "task_1"))

        prompt_text = "\n".join(m.get("content", "") for m in captured["messages"])
        assert "待补缺口" not in prompt_text
        assert result["gaps_resolved"] == 0

    def test_resolves_unknown_number_ignored(self, db, project_id, monkeypatch, tmp_path):
        """LLM 幻觉编号（99 不存在）→ 不误归档，真实缺口保持待处理。"""
        db.create_or_update_wiki_review(
            review_id="rev_gap", project_id=project_id,
            review_type="missing-page", title="缺少组织架构页",
            description="材料中未涉及组织架构",
        )
        ingestor, _ = _make_capture_ingestor(
            monkeypatch, db, _STAGE2_RESOLVES_UNKNOWN, str(tmp_path))
        file_id = _add_done_file(db, project_id)
        _add_text_chunk(db, project_id, file_id)

        result = _run(ingestor.ingest_file(project_id, file_id, "task_1"))

        assert result["gaps_resolved"] == 0
        gap = db.get_wiki_review("rev_gap")
        assert gap["resolved"] == 0


class TestReviewEvidencePreserved:
    def test_upsert_without_evidence_keeps_existing(self, db, project_id):
        """lint 重跑落库不传 evidence → COALESCE 保留摄入产出的证据（修复 review #3）。"""
        evidence = [{"page_key": "p_a", "quote": "原文引用"}]
        db.create_or_update_wiki_review(
            review_id="rev_ev", project_id=project_id,
            review_type="contradiction", title="T", description="旧描述",
            evidence=evidence,
        )

        # lint 路径同 ID 幂等更新，不传 evidence
        db.create_or_update_wiki_review(
            review_id="rev_ev", project_id=project_id,
            review_type="contradiction", title="T", description="新描述",
        )

        review = db.get_wiki_review("rev_ev")
        assert review["description"] == "新描述"
        assert review["evidence"] == evidence


class TestIngestUserEditedWindow:
    def test_ingest_skips_page_edited_during_llm_window(self, db, project_id, monkeypatch, tmp_path):
        """两次 LLM 调用窗口内页面被置 user_edited → Step 4 实时重读跳过，不回滚。

        修复回归（review #1 根因）：Step 1 快照在数分钟 LLM 窗口内会陈旧，
        矛盾裁决/手动编辑恰落窗口内时按旧快照覆盖会静默回滚用户内容。
        """
        page = db.create_or_update_wiki_page(
            project_id, "src_cert_scan", "测试证书要点",
            content="旧内容", status="compiled",
        )

        async def fake_chat(self, messages, settings, task_id, stage):
            if stage == "stage1":
                return _STAGE1_OUTPUT
            # 模拟 Stage 2 LLM 期间用户裁决/手编写入 user_edited
            db.update_wiki_page_content(
                page_id=page["id"], content="用户手动修订的内容", status="user_edited",
            )
            return _STAGE2_PLAIN

        monkeypatch.setattr(WikiIngestor, "_chat_with_retry", fake_chat)
        ingestor = WikiIngestor(db, str(tmp_path), None, {})
        file_id = _add_done_file(db, project_id)
        _add_text_chunk(db, project_id, file_id)

        result = _run(ingestor.ingest_file(project_id, file_id, "task_1"))

        assert result["skipped"] is False
        final = db.get_wiki_page(project_id, "src_cert_scan")
        assert final["status"] == "user_edited"
        assert final["content"] == "用户手动修订的内容"
