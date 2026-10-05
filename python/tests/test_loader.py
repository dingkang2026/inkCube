"""python/tests/test_loader.py — 模板迁移/治愈单测（§2.1.7 / §2.3.3）。

聚焦 migrate_template 的 v2-obsolete 字段治愈（foreach/item_as 移除）：
  - 已 stamped v2 的存量模板携带 foreach → 剥离且不升版本
  - 递归剥离 children 中的 foreach/item_as
  - 携带 foreach 的模板不被误判为 v1（_OBSOLETE_NODE_FIELDS_V2 与 V1 分离）
  - 干净 v2 模板幂等通过
  - v1（id-based）模板仍正常升级到 v2
  - 未知/更高版本 → None
  - 治愈不修改调用方原始 dict（返回副本）
"""
import os
import sys

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.loader import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    detect_schema_version,
    migrate_template,
)


def _v2_template_with_foreach():
    """构造一个已 stamped v2、但节点携带过时 foreach/item_as 的存量模板。"""
    return {
        "template_id": "legacy_dd",
        "template_name": "尽调（存量）",
        "version": "1.0",
        "project_types": ["investment"],
        "output_format": "docx",
        "schema_version": 2,
        "outline": [
            {
                "section_id": "ch4",
                "title": "{{peer}}竞争分析",
                "foreach": "peers",
                "item_as": "peer",
                "children": [
                    {"section_id": "s4_1", "title": "子节", "foreach": "x", "item_as": "i"},
                ],
            },
        ],
    }


class TestMigrateHealObsoleteFields:
    def test_strips_foreach_from_stamped_v2(self):
        """已 stamped v2 的存量模板携带 foreach → 剥离，且 schema_version 保持 2。"""
        tpl = _v2_template_with_foreach()
        out = migrate_template(tpl)
        assert out is not None
        assert out["schema_version"] == CURRENT_SCHEMA_VERSION == 2
        node = out["outline"][0]
        assert "foreach" not in node
        assert "item_as" not in node
        # 其它字段保留
        assert node["section_id"] == "ch4"
        assert node["title"] == "{{peer}}竞争分析"

    def test_strips_recursively_into_children(self):
        """children 中的 foreach/item_as 一并剥离。"""
        tpl = _v2_template_with_foreach()
        out = migrate_template(tpl)
        child = out["outline"][0]["children"][0]
        assert "foreach" not in child
        assert "item_as" not in child
        assert child["section_id"] == "s4_1"

    def test_does_not_mutate_original(self):
        """治愈返回副本，不修改调用方原始 dict。"""
        tpl = _v2_template_with_foreach()
        migrate_template(tpl)
        # 原始对象仍保留 foreach（未被就地修改）
        assert tpl["outline"][0]["foreach"] == "peers"
        assert tpl["outline"][0]["children"][0]["item_as"] == "i"

    def test_idempotent_on_clean_v2(self):
        """干净 v2 模板幂等通过（无 foreach，结构不变）。"""
        tpl = {
            "template_id": "clean",
            "template_name": "干净模板",
            "version": "1.0",
            "project_types": ["general"],
            "schema_version": 2,
            "outline": [{"section_id": "ch1", "title": "章",
                         "children": [{"section_id": "s1", "title": "节"}]}],
        }
        out = migrate_template(tpl)
        assert out["schema_version"] == 2
        assert out["outline"][0]["children"][0]["section_id"] == "s1"


class TestVersionDetectionNotFooledByForeach:
    def test_foreach_v2_not_misdetected_as_v1(self):
        """携带 foreach 的 v2 模板（显式 schema_version=2）检测为 2，而非 1。"""
        tpl = _v2_template_with_foreach()
        assert detect_schema_version(tpl) == 2

    def test_foreach_without_schema_version_detected_as_v2(self):
        """无 schema_version 但用 section_id 且携带 foreach → 判为 v2（foreach 非 v1 信号）。

        这是 _OBSOLETE_NODE_FIELDS_V2 与 _OBSOLETE_NODE_FIELDS_V1 分离的关键：
        若 foreach 被并入 v1 检测集，此模板会被误判为 v1 并走 v1→v2 迁移。
        """
        tpl = _v2_template_with_foreach()
        del tpl["schema_version"]
        assert detect_schema_version(tpl) == CURRENT_SCHEMA_VERSION
        out = migrate_template(tpl)
        # 仍按 v2 治愈：剥离 foreach，stamp schema_version=2
        assert "foreach" not in out["outline"][0]
        assert out["schema_version"] == 2


class TestMigrateVersionBoundaries:
    def test_v1_id_based_upgrades_to_v2(self):
        """v1（id-based、含过时节点字段）模板正常升级到 v2。"""
        v1 = {
            "template_id": "old",
            "template_name": "旧模板",
            "version": "1.0",
            "project_types": ["general"],
            "terminology": {"a": "b"},  # v1 top-level obsolete
            "variables": ["公司名"],     # v1 flat → v2 structured user var
            "outline": [
                {"id": "ch1", "title": "章", "word_count": 500,
                 "children": [{"id": "s1", "title": "节"}]},
            ],
        }
        assert detect_schema_version(v1) == 1
        out = migrate_template(v1)
        assert out["schema_version"] == 2
        assert "terminology" not in out
        # id → section_id
        assert out["outline"][0]["section_id"] == "ch1"
        assert out["outline"][0]["children"][0]["section_id"] == "s1"
        # v1 过时节点字段被剥离
        assert "word_count" not in out["outline"][0]
        # v1 flat variables → v2 structured
        assert out["variables"][0]["name"] == "公司名"
        assert out["variables"][0]["source"] == "user"

    def test_newer_version_returns_none(self):
        """高于当前版本 → None（拒绝降级）。"""
        future = {"schema_version": CURRENT_SCHEMA_VERSION + 1, "outline": []}
        assert migrate_template(future) is None

    def test_non_dict_returns_none(self):
        assert migrate_template(None) is None
        assert migrate_template("notadict") is None
