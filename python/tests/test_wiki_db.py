"""Tests for Wiki DB schema changes (Step 1.1).

Covers:
- wiki_pages new columns: page_type, refs, sources, tags
- wiki_change_log new columns: source_title, affected_page_keys
- projects new columns: wiki_purpose, wiki_overview
- wiki_source_pages table: full CRUD + hash lookup
"""
import json
import os
import sys
import tempfile

import pytest

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import Database


@pytest.fixture
def db():
    """Create a fresh in-memory database with all tables."""
    database = Database(db_path=":memory:")
    database.initialize()
    return database


@pytest.fixture
def project_id(db):
    """Create a test project and return its ID."""
    proj = db.create_project(name="Test Project", project_type="general")
    return proj["id"]


@pytest.fixture
def file_id(db, project_id):
    """Create a test file and return its ID."""
    f = db.create_file(
        project_id=project_id,
        filename="test.pdf",
        file_type="pdf",
        file_size=1024,
        file_hash="abc123",
    )
    return f["id"]


# ── wiki_pages: new fields ──


class TestWikiPagesNewFields:
    def test_create_page_with_page_type(self, db, project_id):
        page = db.create_or_update_wiki_page(
            project_id=project_id,
            page_key="test_entity",
            title="Test Entity",
            content="# Test",
            page_type="entity",
        )
        assert page is not None
        assert page["page_type"] == "entity"

    def test_create_page_with_refs_sources_tags(self, db, project_id):
        page = db.create_or_update_wiki_page(
            project_id=project_id,
            page_key="test_concept",
            title="Test Concept",
            content="# Concept",
            page_type="concept",
            refs=["enterprise_profile", "industry_chain"],
            sources=["file_abc"],
            tags=["tech", "china"],
        )
        assert page["refs"] == ["enterprise_profile", "industry_chain"]
        assert page["sources"] == ["file_abc"]
        assert page["tags"] == ["tech", "china"]

    def test_create_page_defaults(self, db, project_id):
        """New fields should default to empty lists / 'structure' type."""
        page = db.create_or_update_wiki_page(
            project_id=project_id,
            page_key="default_page",
            title="Default",
            content="content",
        )
        assert page["page_type"] == "structure"
        assert page["refs"] == []
        assert page["sources"] == []
        assert page["tags"] == []

    def test_update_page_refs(self, db, project_id):
        page = db.create_or_update_wiki_page(
            project_id=project_id,
            page_key="updatable",
            title="Updatable",
        )
        db.update_wiki_page_content(
            page_id=page["id"],
            content="updated content",
            refs=["page_a", "page_b"],
            tags=["new_tag"],
        )
        updated = db.get_wiki_page(project_id, "updatable")
        assert updated["refs"] == ["page_a", "page_b"]
        assert updated["tags"] == ["new_tag"]

    def test_list_pages_includes_json_fields(self, db, project_id):
        db.create_or_update_wiki_page(
            project_id=project_id,
            page_key="listed",
            title="Listed",
            page_type="topic",
            refs=["ref1"],
            tags=["t1", "t2"],
        )
        pages = db.list_wiki_pages(project_id)
        assert len(pages) == 1
        p = pages[0]
        assert p["page_type"] == "topic"
        assert p["refs"] == ["ref1"]
        assert p["tags"] == ["t1", "t2"]


# ── wiki_change_log: new fields ──


class TestWikiChangeLogNewFields:
    def test_add_log_with_source_title(self, db, project_id):
        log_id = db.add_wiki_change_log(
            project_id=project_id,
            action="ingest",
            source_title="商业计划书.pdf",
            affected_page_keys=["enterprise_profile", "policy_match"],
            summary="摄入完成",
        )
        assert log_id

        logs = db.list_wiki_change_log(project_id)
        assert len(logs) == 1
        assert logs[0]["source_title"] == "商业计划书.pdf"
        assert logs[0]["affected_page_keys"] == ["enterprise_profile", "policy_match"]

    def test_log_defaults(self, db, project_id):
        """New fields should default to empty string / empty list."""
        db.add_wiki_change_log(project_id=project_id, action="lint")
        logs = db.list_wiki_change_log(project_id)
        assert logs[0]["source_title"] == ""
        assert logs[0]["affected_page_keys"] == []


# ── projects: wiki_purpose / wiki_overview ──


class TestProjectsWikiFields:
    def test_purpose_default_empty(self, db, project_id):
        assert db.get_wiki_purpose(project_id) == ""

    def test_update_and_get_purpose(self, db, project_id):
        db.update_wiki_purpose(project_id, "构建招商知识库")
        assert db.get_wiki_purpose(project_id) == "构建招商知识库"

    def test_overview_default_empty(self, db, project_id):
        assert db.get_wiki_overview(project_id) == ""

    def test_update_and_get_overview(self, db, project_id):
        db.update_wiki_overview(project_id, "本项目涵盖 5 个领域")
        assert db.get_wiki_overview(project_id) == "本项目涵盖 5 个领域"


# ── wiki_source_pages ──


class TestWikiSourcePages:
    def test_create_source_page(self, db, project_id, file_id):
        sp = db.create_or_update_source_page(
            project_id=project_id,
            file_id=file_id,
            source_page_key="src_test_pdf",
            content_hash="sha256_abc",
            status="ingested",
            pages_touched=["enterprise_profile", "policy_match"],
            entities_found=["华为", "腾讯"],
            concepts_found=["对赌协议"],
        )
        assert sp is not None
        assert sp["source_page_key"] == "src_test_pdf"
        assert sp["content_hash"] == "sha256_abc"
        assert sp["status"] == "ingested"
        assert sp["pages_touched"] == ["enterprise_profile", "policy_match"]
        assert sp["entities_found"] == ["华为", "腾讯"]
        assert sp["concepts_found"] == ["对赌协议"]

    def test_get_source_page_by_hash(self, db, project_id, file_id):
        db.create_or_update_source_page(
            project_id=project_id,
            file_id=file_id,
            content_hash="sha256_unique",
            status="ingested",
        )
        found = db.get_source_page_by_hash(project_id, "sha256_unique")
        assert found is not None
        assert found["file_id"] == file_id

    def test_hash_miss_returns_none(self, db, project_id):
        assert db.get_source_page_by_hash(project_id, "nonexistent") is None

    def test_upsert_on_conflict(self, db, project_id, file_id):
        """Same project+file should update, not duplicate."""
        db.create_or_update_source_page(
            project_id=project_id,
            file_id=file_id,
            content_hash="v1",
            pages_touched=["page_a"],
        )
        db.create_or_update_source_page(
            project_id=project_id,
            file_id=file_id,
            content_hash="v2",
            pages_touched=["page_a", "page_b"],
        )
        sp = db.get_source_page(project_id, file_id)
        assert sp["content_hash"] == "v2"
        assert sp["pages_touched"] == ["page_a", "page_b"]

    def test_list_source_pages(self, db, project_id):
        f1 = db.create_file(project_id, "a.pdf", "pdf", 100, "h1")
        f2 = db.create_file(project_id, "b.pdf", "pdf", 200, "h2")
        db.create_or_update_source_page(project_id, f1["id"], content_hash="h1")
        db.create_or_update_source_page(project_id, f2["id"], content_hash="h2")
        pages = db.list_source_pages(project_id)
        assert len(pages) == 2

    def test_update_status(self, db, project_id, file_id):
        db.create_or_update_source_page(project_id, file_id, content_hash="h")
        db.update_source_page_status(project_id, file_id, "stale")
        sp = db.get_source_page(project_id, file_id)
        assert sp["status"] == "stale"

    def test_delete_source_page(self, db, project_id, file_id):
        db.create_or_update_source_page(project_id, file_id)
        assert db.delete_source_page(project_id, file_id) is True
        assert db.get_source_page(project_id, file_id) is None
        assert db.delete_source_page(project_id, file_id) is False
