"""python/tests/test_prompts.py — Prompt 构建单测（§3.5 / §3.1.1）。

覆盖：
  - build_system_prompt：注入 company_name / depth_instruction（full/brief 两档）、
    role_hint / audience；不含 v1 残留（terminology/subhead）、无未格式化占位符
  - build_extract_value_prompt：scalar/list 抽值契约、deps 渲染、derive_hint/证据注入
  - build_standard_prompt / build_post_body_prompt：writing_requirements 进入写作要求
  - build_digest_prompt：纲要式定位（主题/论点覆盖，不搬运数值与引用编号）
"""
import os
import sys
from unittest.mock import MagicMock

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.prompts import (  # noqa: E402
    build_digest_prompt,
    build_extract_value_prompt,
    build_post_body_prompt,
    build_standard_prompt,
    build_system_prompt,
)


# ── build_system_prompt ─────────────────────────────────────


class TestBuildSystemPrompt:
    def test_injects_company_name(self):
        out = build_system_prompt(company_name="华为技术有限公司")
        assert "华为技术有限公司" in out
        assert "本报告为：华为技术有限公司" in out

    def test_empty_company_name_placeholder(self):
        out = build_system_prompt(company_name="")
        assert "（未指定报告主体）" in out

    def test_full_depth_instruction(self):
        out = build_system_prompt(report_depth="full")
        assert "撰写详尽的长篇研究报告" in out

    def test_brief_depth_instruction(self):
        out = build_system_prompt(report_depth="brief")
        assert "撰写精炼的短篇报告" in out
        assert "200-500" in out

    def test_unknown_depth_falls_back_to_full(self):
        out = build_system_prompt(report_depth="unknown")
        assert "撰写详尽的长篇研究报告" in out

    def test_injects_role_and_audience(self):
        out = build_system_prompt(role_hint="资深投资分析师", audience="投委会")
        assert "资深投资分析师" in out
        assert "投委会" in out

    def test_subject_anchored_default_true(self):
        """缺省 true（schema default）：老模板（无 subject_anchored 字段）行为不变。"""
        out = build_system_prompt(company_name="华为技术有限公司")
        assert "本报告为：华为技术有限公司" in out

    def test_subject_not_anchored_excludes_subject_section(self):
        """subject_anchored=False（行业/区域/企业池类模板）：不注入【报告主体】段，
        公司名不进 prompt——超导设备变量被主体锚定纠正实证的根因开关（§2.1.2/§3.5）。"""
        out = build_system_prompt(company_name="华为技术有限公司", subject_anchored=False)
        assert "【报告主体】" not in out
        assert "华为技术有限公司" not in out

    def test_subject_not_anchored_no_placeholder(self):
        """不锚定时连「（未指定报告主体）」占位也不注入——整段消失而非注空。"""
        out = build_system_prompt(company_name="", subject_anchored=False)
        assert "（未指定报告主体）" not in out

    def test_no_v1_residue_and_no_unformatted_braces(self):
        out = build_system_prompt(company_name="X", report_depth="full")
        # v1 残留字段已删除
        assert "terminology" not in out
        assert "subhead_rules" not in out
        assert "promote_subhead" not in out
        # 所有 format 占位符已填充，无残留花括号
        assert "{" not in out
        assert "}" not in out
        # v2 输出契约不再包含「目标字数 ±20%」
        assert "±20%" not in out

    def test_format_spec_pandoc_aligned(self):
        """行文形式规范（代码层统一，按 pandoc 支持矩阵实测闭环）：
        表格/mermaid/引用块/公式可用；禁内联 HTML 与 emoji；不用 markdown 脚注
        （多节生成期 LLM 无法协调全局唯一脚注编号，pandoc 遇重名定义静默取首个）。"""
        out = build_system_prompt(company_name="X")
        assert "Markdown 表格" in out
        assert "mermaid" in out
        assert "引用块" in out
        assert "LaTeX" in out
        assert "禁止内联 HTML" in out
        assert "emoji" in out
        assert "[^" not in out


# ── build_extract_value_prompt ──────────────────────────────


class TestBuildExtractValuePrompt:
    def test_returns_system_and_user_messages(self):
        msgs = build_extract_value_prompt("判断行业", {}, "证据", "scalar")
        assert isinstance(msgs, list)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_scalar_contract(self):
        msgs = build_extract_value_prompt("判断行业", {}, "证据", "scalar")
        assert "单个值" in msgs[0]["content"]
        assert "名词短语" in msgs[1]["content"]

    def test_list_contract(self):
        msgs = build_extract_value_prompt("列出竞争对手", {}, "证据", "list")
        user = msgs[1]["content"]
        assert "JSON 字符串数组" in user
        assert '["项1", "项2", "项3"]' in user

    def test_derive_hint_and_evidence_injected(self):
        msgs = build_extract_value_prompt("判断所属细分赛道", {}, "某证据段落", "scalar")
        user = msgs[1]["content"]
        assert "判断所属细分赛道" in user
        assert "某证据段落" in user

    def test_deps_rendered_excluding_none(self):
        deps = {"company_name": "华为", "stage": None, "industry": "通信"}
        msgs = build_extract_value_prompt("hint", deps, "ev", "scalar")
        user = msgs[1]["content"]
        assert "company_name: 华为" in user
        assert "industry: 通信" in user
        # None（UNRESOLVED）依赖不渲染
        assert "stage:" not in user

    def test_empty_deps_placeholder(self):
        msgs = build_extract_value_prompt("hint", {}, "ev", "scalar")
        assert "（无上游依赖）" in msgs[1]["content"]

    def test_empty_evidence_placeholder(self):
        msgs = build_extract_value_prompt("hint", {}, "", "scalar")
        assert "（无证据）" in msgs[1]["content"]


# ── build_standard_prompt: writing_requirements ─────────────


class TestBuildStandardPrompt:
    def _base_section(self):
        return {
            "section_id": "s1",
            "title": "市场规模",
            "description": "描述市场规模",
            "prompt_hint": "须给出TAM/SAM",
            "writing_requirements": ["须覆盖：规模——增速（含CAGR）", "须覆盖：区域分布"],
        }

    def test_writing_requirements_included(self):
        out = build_standard_prompt(
            section=self._base_section(), chapter_title="市场", report_title="尽调报告",
            completed_digests={}, section_order=[], ref_text="[1] 资料", ref_numbers=[1],
        )
        # 多级折叠产生的写作要求须进入 prompt（C2 核心修复）
        assert "须覆盖：规模——增速（含CAGR）" in out
        assert "须覆盖：区域分布" in out
        # prompt_hint / description 也在
        assert "须给出TAM/SAM" in out
        assert "描述市场规模" in out

    def test_global_requirements_included(self):
        out = build_standard_prompt(
            section=self._base_section(), chapter_title="市场", report_title="R",
            completed_digests={}, section_order=[], ref_text="[1] x", ref_numbers=[1],
            global_requirements=["全文使用第三人称"],
        )
        assert "全文使用第三人称" in out

    def test_degraded_mode_hint(self):
        """零材料降级注入 sparse 写作模式指令（§3.3：不是跳过而是换写法）。"""
        out = build_standard_prompt(
            section=self._base_section(), chapter_title="市场", report_title="R",
            completed_digests={}, section_order=[], ref_text="", ref_numbers=[],
            is_degraded=True,
        )
        assert "sparse 写作模式" in out
        assert "分析方法论" in out
        assert "严禁虚构具体数据" in out

    def test_completed_digests_recent_shown(self):
        out = build_standard_prompt(
            section=self._base_section(), chapter_title="市场", report_title="R",
            completed_digests={"s0": "前文摘要内容"}, section_order=["s0"],
            ref_text="[1] x", ref_numbers=[1],
        )
        assert "前文摘要内容" in out

    def test_completed_digests_full_injection(self):
        """全量注入：超过 3 节的前文摘要全部进入 prompt（无窗口截断）。"""
        digests = {f"s{i}": f"摘要{i}" for i in range(5)}
        out = build_standard_prompt(
            section=self._base_section(), chapter_title="市场", report_title="R",
            completed_digests=digests, section_order=[f"s{i}" for i in range(5)],
            ref_text="[1] x", ref_numbers=[1],
        )
        for i in range(5):
            assert f"摘要{i}" in out

    def test_writing_brief_as_main_body_description_dropped(self):
        """有任务书时：brief 为主体段、description 不注入、附加要求单独分层（§3.5）。"""
        section = {
            "section_id": "s1", "title": "竞争对比",
            "description": "给人看的检索素材说明",
            "writing_brief": "1. 开篇以表格对比关键维度；\n2. 表后逐一成段展开",
            "prompt_hint": "名单为空时按材料不足处理",
        }
        out = build_standard_prompt(
            section=section, chapter_title="业务", report_title="R",
            completed_digests={}, section_order=[], ref_text="[1] x", ref_numbers=[1],
        )
        assert "【本节写作任务】" in out
        assert "1. 开篇以表格对比关键维度" in out
        assert "【附加写作要求】" in out
        # description 职责收窄：给人看（检索素材/前端展示），不注入写作 prompt
        assert "给人看的检索素材说明" not in out
        assert "名单为空时按材料不足处理" in out

    def test_no_brief_falls_back_to_description(self):
        """无任务书时回退注入 description（向后兼容旧模板/提取器未升级场景）。"""
        section = {
            "section_id": "s1", "title": "市场规模",
            "description": "市场规模整体说明",
        }
        out = build_standard_prompt(
            section=section, chapter_title="市场", report_title="R",
            completed_digests={}, section_order=[], ref_text="[1] x", ref_numbers=[1],
        )
        assert "市场规模整体说明" in out
        assert "【附加写作要求】" not in out  # 无任务书保持原单层结构

    def test_target_words_injected(self):
        """节级篇幅护栏注入 prompt（§3.6 规则 3 的 prompt 侧对应）。"""
        section = {**self._base_section(), "target_words": 800}
        out = build_standard_prompt(
            section=section, chapter_title="市场", report_title="R",
            completed_digests={}, section_order=[], ref_text="[1] x", ref_numbers=[1],
        )
        assert "本节篇幅约 800 字" in out

    def test_target_words_skipped_for_brief_depth(self):
        """brief 深度不注入 target_words（与 depth 指令「200-500 字」冲突）。"""
        section = {**self._base_section(), "target_words": 800}
        out = build_standard_prompt(
            section=section, chapter_title="市场", report_title="R",
            completed_digests={}, section_order=[], ref_text="[1] x", ref_numbers=[1],
            report_depth="brief",
        )
        assert "本节篇幅约" not in out

    def test_no_target_words_no_hint(self):
        out = build_standard_prompt(
            section=self._base_section(), chapter_title="市场", report_title="R",
            completed_digests={}, section_order=[], ref_text="[1] x", ref_numbers=[1],
        )
        assert "本节篇幅约" not in out

    def test_insufficient_sufficiency_directive(self):
        """insufficient（材料有限）注入弱化 sparse 指令：可用维度正常写、不足维度定性。"""
        out = build_standard_prompt(
            section=self._base_section(), chapter_title="市场", report_title="R",
            completed_digests={}, section_order=[], ref_text="[1] x", ref_numbers=[1],
            sufficiency={"level": "insufficient", "reason": "知识卡片少于充足阈值"},
        )
        assert "检索材料有限" in out
        assert "知识卡片少于充足阈值" in out
        assert "严禁编造具体数据" in out

    def test_sufficient_no_sparse_directive(self):
        """材料充足不注入任何 sparse 指令。"""
        out = build_standard_prompt(
            section=self._base_section(), chapter_title="市场", report_title="R",
            completed_digests={}, section_order=[], ref_text="[1] x", ref_numbers=[1],
            sufficiency={"level": "sufficient", "reason": ""},
        )
        assert "sparse 写作模式" not in out
        assert "检索材料有限" not in out


# ── build_digest_prompt: 纲要式定位 ─────────────────────────


class TestBuildDigestPrompt:
    def test_outline_positioning_not_compression(self):
        """digest 定位为「写了哪些方面」的纲要，非内容简写（§3.5 全量注入前提）。"""
        msgs = build_digest_prompt("章节正文...")
        user = msgs[1]["content"]
        assert "写了哪些方面的内容" in user
        assert "每行一条" in user
        # 明确禁止内容简写与数值搬运
        assert "不要压缩复述正文事实" in user
        assert "不要搬运具体数值和引用编号" in user

    def test_default_word_limit_120(self):
        """默认字数 120（纲要式，原 200 字内容简写已废弃）。"""
        msgs = build_digest_prompt("章节正文...")
        assert "不超过 120 字" in msgs[1]["content"]

    def test_custom_word_limit_rendered(self):
        msgs = build_digest_prompt("章节正文...", max_words=60)
        assert "不超过 60 字" in msgs[1]["content"]

    def test_text_injected(self):
        msgs = build_digest_prompt("某节全文内容")
        assert "某节全文内容" in msgs[1]["content"]


# ── build_post_body_prompt: writing_requirements ────────────


class TestBuildPostBodyPrompt:
    def test_writing_requirements_included(self):
        registry = MagicMock()
        registry.format_source_list.return_value = "[1] 《文档》"
        section = {
            "section_id": "summary", "title": "摘要", "compose_mode": "post_body",
            "prompt_hint": "结论先行",
            "writing_requirements": ["须覆盖：核心结论"],
        }
        out = build_post_body_prompt(
            section=section, digests={"s1": "正文摘要"}, section_order=["s1"],
            key_data_text="营收: 100亿", registry=registry,
        )
        assert "须覆盖：核心结论" in out  # C4：post_body 同步注入
        assert "结论先行" in out
        assert "营收: 100亿" in out
        assert "[1] 《文档》" in out

    def test_writing_brief_as_main_body(self):
        """post_body 同样支持 writing_brief 任务书主体（§3.5；模板摘要节示范）。"""
        registry = MagicMock()
        registry.format_source_list.return_value = ""
        section = {
            "section_id": "summary", "title": "摘要", "compose_mode": "post_body",
            "description": "给人看的描述",
            "writing_brief": "1. 用 3-5 条要点呈现核心发现；\n2. 成段给出投资建议",
        }
        out = build_post_body_prompt(
            section=section, digests={"s1": "正文摘要"}, section_order=["s1"],
            key_data_text="", registry=registry,
        )
        assert "【本节写作任务】" in out
        assert "1. 用 3-5 条要点呈现核心发现" in out
        assert "【附加写作要求】" in out
        assert "给人看的描述" not in out
