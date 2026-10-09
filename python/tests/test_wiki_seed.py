"""Tests for Wiki schema seed v2 (§2.2 of 2.6wiki-design.md, E2-2026-10).

Covers:
- v2 schema inventory: recruitment 10 pages / investment 9 pages / general 3 pages
- ledger (监控页) compile hints are marked for append-only semantics
- profile pages with time-varying facts (enterprise_profile/company_overview)
  declare timeline retention in compile_hint
- seed_wiki_schemas removes retired schema rows (RETIRED_SCHEMA_IDS)
"""
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from db.wiki_seed import RETIRED_SCHEMA_IDS, get_all_wiki_schemas


@pytest.fixture
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


class TestSeedInventory:
    def test_recruitment_has_10_pages(self):
        schemas = [s for s in get_all_wiki_schemas() if s["project_type"] == "recruitment"]
        assert len(schemas) == 10
        keys = {s["page_key"] for s in schemas}
        assert {"enterprise_profile", "policy_ledger", "park_analysis", "industry_chain",
                "competitor_comparison", "landing_plan", "milestones", "operation_monitor",
                "risk_watchlist", "service_log"} == keys

    def test_investment_has_9_pages(self):
        schemas = [s for s in get_all_wiki_schemas() if s["project_type"] == "investment"]
        assert len(schemas) == 9
        keys = {s["page_key"] for s in schemas}
        assert {"company_overview", "core_team", "financial_monitor", "legal_risk",
                "valuation_ref", "deal_terms", "milestone_tracker", "risk_summary",
                "exit_path"} == keys

    def test_general_unchanged(self):
        schemas = [s for s in get_all_wiki_schemas() if s["project_type"] == "general"]
        assert len(schemas) == 3

    def test_positions_unique_per_type(self):
        for ptype in ("recruitment", "investment", "general"):
            schemas = [s for s in get_all_wiki_schemas() if s["project_type"] == ptype]
            positions = [s["position"] for s in schemas]
            assert sorted(positions) == list(range(1, len(schemas) + 1))


class TestLedgerSemantics:
    def test_recruitment_monitoring_pages_marked(self):
        """New v2 ledger pages must declare append-only semantics in compile_hint."""
        schemas = {s["page_key"]: s for s in get_all_wiki_schemas()
                   if s["project_type"] == "recruitment"}
        for key in ("policy_ledger", "milestones", "operation_monitor",
                    "risk_watchlist", "service_log"):
            assert "监控页" in schemas[key]["compile_hint"], f"{key} missing ledger marker"
            assert "追加" in schemas[key]["compile_hint"], f"{key} missing append rule"

    def test_investment_monitoring_pages_marked(self):
        schemas = {s["page_key"]: s for s in get_all_wiki_schemas()
                   if s["project_type"] == "investment"}
        for key in ("financial_monitor", "deal_terms", "milestone_tracker", "exit_path"):
            assert "监控页" in schemas[key]["compile_hint"], f"{key} missing ledger marker"
            assert "追加" in schemas[key]["compile_hint"], f"{key} missing append rule"


class TestProfileTimelineSemantics:
    def test_enterprise_profile_declares_timeline_retention(self):
        """实控人/股权/资质等时效性信息须声明按时间保留历史沿革。"""
        schemas = {s["page_key"]: s for s in get_all_wiki_schemas()
                   if s["project_type"] == "recruitment"}
        hint = schemas["enterprise_profile"]["compile_hint"]
        assert "历史沿革" in hint
        assert "变更时点" in hint

    def test_company_overview_declares_timeline_retention(self):
        schemas = {s["page_key"]: s for s in get_all_wiki_schemas()
                   if s["project_type"] == "investment"}
        hint = schemas["company_overview"]["compile_hint"]
        assert "历史沿革" in hint
        assert "时点" in hint


class TestSeedRetiredCleanup:
    def test_seed_removes_retired_schema_rows(self, db):
        """Stale v1 schema rows (renamed page_keys) are deleted on seeding."""
        # Simulate a v1 database: retired rows present, new rows absent
        db.conn.execute(
            "INSERT INTO wiki_schemas (id, project_type, page_key, title, compile_hint,"
            " base_lib_search_hints, position) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("wiki_schema_recruitment_policy_match", "recruitment", "policy_match",
             "招商政策匹配", "old hint", "[]", 2),
        )
        db.conn.commit()

        from db.wiki_seed import get_all_wiki_schemas
        db.seed_wiki_schemas(get_all_wiki_schemas())

        for schema_id in RETIRED_SCHEMA_IDS:
            row = db.conn.execute(
                "SELECT id FROM wiki_schemas WHERE id = ?", (schema_id,)
            ).fetchone()
            assert row is None, f"retired schema {schema_id} not removed"

        # New schemas are present
        keys = {r["page_key"] for r in db.conn.execute(
            "SELECT page_key FROM wiki_schemas WHERE project_type = 'recruitment'"
        ).fetchall()}
        assert "policy_ledger" in keys
        assert "policy_match" not in keys

    def test_seed_preserves_custom_schemas(self, db):
        """User-defined custom schemas (CRUD API) survive re-seeding."""
        db.create_wiki_schema(
            project_type="recruitment",
            page_key="custom_page",
            title="自定义页",
        )
        from db.wiki_seed import get_all_wiki_schemas
        db.seed_wiki_schemas(get_all_wiki_schemas())

        row = db.conn.execute(
            "SELECT id FROM wiki_schemas WHERE page_key = 'custom_page'"
        ).fetchone()
        assert row is not None, "custom schema was removed by seeding"
