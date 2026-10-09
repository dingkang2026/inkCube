"""Regression tests for wiki structural lint contracts (review P2-2).

结构层 finding 必须携带 pages=[page_key]：经 router 落库为「PAGES:」行后，
前端一键修复据此定位源页面；此前仅语义层有 PAGES 行，死链修复无从下手。
死链 title 契约：携带 [[dead_key]]（前端 extractDeadLinkKey 据此取死链键）。
"""
import os
import sys

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database
from wiki.lint import run_structural_lint


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


class TestStructuralLintContracts:
    def test_broken_link_finding_carries_pages_and_dead_key(self, db, project_id):
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="src_report", title="Report",
            content="see [[dead_link]] and [[target_page]]", page_type="topic",
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="target_page", title="Target",
            content="target body", page_type="entity",
        )

        findings = run_structural_lint(db, project_id)
        broken = [f for f in findings if f["type"] == "broken-link"]

        # 仅 dead_link 是死链；[[target_page]] 解析成功不报
        assert len(broken) == 1
        f = broken[0]
        # title 携带 [[dead_key]]：前端 extractDeadLinkKey 据此取死链键
        assert "[[dead_link]]" in f["title"]
        # pages 携带源页面 key：前端一键修复据此定位并改写源页面
        assert f["pages"] == ["src_report"]
        assert f["affected_page_ids"] == [db.get_wiki_page(project_id, "src_report")["id"]]

    def test_all_structural_findings_carry_pages(self, db, project_id):
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="empty_one", title="Empty",
            content="", page_type="entity",
        )
        db.create_or_update_wiki_page(
            project_id=project_id, page_key="loner", title="Loner",
            content="x" * 60, page_type="entity",
        )

        findings = run_structural_lint(db, project_id)
        types = {f["type"] for f in findings}
        assert "empty-page" in types
        assert "no-outlinks" in types
        assert "orphan" in types

        # 除 orphan-review（无关联页）外，每个 finding 都能定位到具体页面
        for f in findings:
            if f["type"] == "orphan-review":
                continue
            assert f.get("pages"), f"{f['type']} missing pages: {f}"
            assert f["pages"][0] in {"empty_one", "loner"}
