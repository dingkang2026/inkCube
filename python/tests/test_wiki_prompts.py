"""Tests for Wiki ingest prompts (§4.3 of 2.6wiki-design.md, E2-2026-10).

Covers:
- Domain role injection: recruitment/investment system prompts carry the
  domain role; general/unknown types keep the generic role
- Ledger (监控页) append-only writing rule present in Stage 2
- Profile timeline (画像页状态变迁) per-period retention rule in Stage 2
- Backward compatibility: project_type defaults to general
"""
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wiki.prompts import (
    _LEDGER_RULE,
    _PROFILE_TIMELINE_RULE,
    _ROLE_PROFILES,
    build_ingest_analysis_prompt,
    build_ingest_generation_prompt,
)


def _build_analysis(project_type: str = "general") -> list[dict]:
    return build_ingest_analysis_prompt(
        source_text="原始资料全文",
        filename="test.pdf",
        purpose="测试目标",
        overview="",
        index_summary="",
        schema_hints="",
        base_lib_context="",
        project_type=project_type,
    )


def _build_generation(project_type: str = "general") -> list[dict]:
    return build_ingest_generation_prompt(
        source_text="原始资料全文",
        filename="test.pdf",
        analysis_report="Stage 1 分析结果",
        purpose="测试目标",
        overview="",
        existing_content="",
        schema_hints="",
        base_lib_context="",
        project_type=project_type,
    )


class TestDomainRoleInjection:
    @pytest.mark.parametrize("project_type", ["recruitment", "investment"])
    def test_analysis_system_carries_role(self, project_type):
        messages = _build_analysis(project_type)
        assert messages[0]["role"] == "system"
        assert messages[0]["content"].startswith(_ROLE_PROFILES[project_type])

    @pytest.mark.parametrize("project_type", ["recruitment", "investment"])
    def test_generation_system_carries_role(self, project_type):
        messages = _build_generation(project_type)
        assert messages[0]["role"] == "system"
        assert messages[0]["content"].startswith(_ROLE_PROFILES[project_type])

    def test_general_has_no_role(self):
        messages = _build_analysis("general")
        assert messages[0]["content"].startswith("你是知识分析 Agent")
        assert "招商引资顾问" not in messages[0]["content"]

    def test_unknown_type_falls_back_to_generic(self):
        messages = _build_analysis("custom_type")
        assert messages[0]["content"].startswith("你是知识分析 Agent")

    def test_default_project_type_is_general(self):
        """Backward compatibility: omitting project_type keeps the generic role."""
        messages = build_ingest_analysis_prompt(
            source_text="t", filename="f.pdf", purpose="p", overview="",
            index_summary="", schema_hints="", base_lib_context="",
        )
        assert messages[0]["content"].startswith("你是知识分析 Agent")


class TestLedgerRule:
    def test_generation_contains_ledger_rule(self):
        messages = _build_generation("recruitment")
        assert _LEDGER_RULE in messages[0]["content"]

    def test_analysis_does_not_contain_generation_rule(self):
        """The ledger writing rule targets generation, not analysis output."""
        messages = _build_analysis("recruitment")
        assert _LEDGER_RULE not in messages[0]["content"]

    def test_ledger_rule_mentions_append_and_dates(self):
        assert "追加" in _LEDGER_RULE
        assert "禁止改写" in _LEDGER_RULE
        assert "日期" in _LEDGER_RULE


class TestProfileTimelineRule:
    def test_generation_contains_profile_timeline_rule(self):
        messages = _build_generation("investment")
        assert _PROFILE_TIMELINE_RULE in messages[0]["content"]

    def test_analysis_does_not_contain_profile_rule(self):
        """The profile timeline rule targets generation, not analysis output."""
        messages = _build_analysis("investment")
        assert _PROFILE_TIMELINE_RULE not in messages[0]["content"]

    def test_profile_rule_mentions_period_retention_and_no_silent_replace(self):
        assert "按时期分段保留" in _PROFILE_TIMELINE_RULE
        assert "静默替换" in _PROFILE_TIMELINE_RULE
        assert "数据时点" in _PROFILE_TIMELINE_RULE

    def test_profile_rule_covers_time_varying_facts(self):
        """营收/实控人/任职等用户点名的时效性场景必须在规则覆盖范围内。"""
        assert "营收" in _PROFILE_TIMELINE_RULE
        assert "实控人" in _PROFILE_TIMELINE_RULE
        assert "任职" in _PROFILE_TIMELINE_RULE


class TestPromptStructureUnchanged:
    def test_analysis_user_carries_context_sections(self):
        messages = _build_analysis("investment")
        user = messages[1]["content"]
        assert "## 原始资料（test.pdf）" in user
        assert "## Wiki 目标" not in user  # purpose lives in system, not user

    def test_generation_user_carries_analysis_report(self):
        messages = _build_generation("investment")
        user = messages[1]["content"]
        assert "## 分析结果（Stage 1 输出）" in user
