"""SettingsModel 与 get_settings() defaults 同构性防线。

回归背景（同款坑已两次踩中）：
- task_max_iterations：仅 task_agent 内联默认，defaults 白名单与 SettingsModel
  均缺失 → 用户调整被静默丢弃（1.1 §9.8，已修，见 test_task_settings.py）
- wiki_ledger_parse_model：3.1 §4.2 新增时只加 defaults 未加 SettingsModel →
  设置页修改经 POST /settings 反序列化被 pydantic 丢弃，用户输入永久丢失

持久化链路：POST /settings 只落 SettingsModel.model_dump() 的键；
GET /settings 只回 defaults 白名单内的键。因此两个方向都必须同构：
- defaults 有、Model 无 → 设置页修改静默丢失（本次 bug）
- Model 有、defaults 无 → 保存成功但重启后读不回

豁免机制（D7 决策）：defaults 定义处行内注释 `# model-exempt: 理由` 即登记
豁免，本测试用 AST 解析自动收集——新增「有意不入 Model」的键只需在
database.py defaults 定义处写行内注释，无需同步本文件的人工集合。
"""
import ast
import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from db.database import Database
from schemas import SettingsModel

_EXEMPT_MARKER = "model-exempt:"


def _parse_model_exempt_keys() -> dict[str, str]:
    """AST 解析 database.py 中 get_settings() 的 defaults 字面量，
    返回 {键: 豁免理由}（行内注释含 model-exempt: 的键）。"""
    src_path = Path(inspect.getfile(Database))
    source = src_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.FunctionDef) and node.name == "get_settings"):
            continue
        for stmt in ast.walk(node):
            if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Dict)):
                continue
            if not (stmt.targets and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "defaults"):
                continue
            exempt: dict[str, str] = {}
            for k_node in stmt.value.keys:
                key = k_node.value  # ast.Constant
                parts = lines[k_node.lineno - 1].split("#", 1)
                if len(parts) == 2 and _EXEMPT_MARKER in parts[1]:
                    exempt[key] = parts[1].split(_EXEMPT_MARKER, 1)[1].strip()
            return exempt
    raise AssertionError(
        "未找到 get_settings() 的 defaults 字面量——database.py 结构变化，防线需同步"
    )


MODEL_EXEMPT_KEYS = _parse_model_exempt_keys()


@pytest.fixture()
def db(tmp_path):
    database = Database(str(tmp_path / "settings_sync_test.db"))
    database.initialize()
    return database


class TestExemptParsing:
    def test_exempt_keys_parsed_with_reasons(self):
        """解析器自检：豁免集合非空且每项带理由（防解析静默失效退化为空集）。"""
        assert MODEL_EXEMPT_KEYS, "未解析到任何 model-exempt 键——注释格式或解析器失效"
        for key, reason in MODEL_EXEMPT_KEYS.items():
            assert reason, f"豁免键 {key} 缺少理由说明"


class TestSettingsSchemaSync:
    def test_model_declares_every_default_key(self, db):
        """defaults 每个键都被 SettingsModel 声明（POST /settings 不丢键）。"""
        declared = set(SettingsModel.model_fields)
        missing = set(db.get_settings()) - declared - set(MODEL_EXEMPT_KEYS)
        assert not missing, (
            f"defaults 键未入 SettingsModel，设置页修改会被静默丢弃: {sorted(missing)}；"
            f"如属有意豁免，在 database.py defaults 该键行内注释加 '{_EXEMPT_MARKER} 理由'"
        )

    def test_default_whitelist_covers_every_model_key(self, db):
        """SettingsModel 每个键都在 defaults 白名单（GET /settings 读回不丢键）。"""
        whitelist = set(db.get_settings())
        missing = set(SettingsModel.model_fields) - whitelist
        assert not missing, (
            f"SettingsModel 键不在 defaults 白名单，保存后重启读不回: {sorted(missing)}"
        )

    def test_wiki_ledger_parse_model_round_trips(self, db):
        """wiki_ledger_parse_model 保存-读回往返（本次 bug 回归）。"""
        db.save_settings({"wiki_ledger_parse_model": "glm-4-flash"})
        assert db.get_settings()["wiki_ledger_parse_model"] == "glm-4-flash"

    def test_wiki_ledger_parse_model_survives_model_dump(self):
        """POST /settings 反序列化链路：model_dump 含该键（pydantic 不丢）。"""
        model = SettingsModel(wiki_ledger_parse_model="glm-4-flash")
        assert model.wiki_ledger_parse_model == "glm-4-flash"
        assert "wiki_ledger_parse_model" in model.model_dump()


class TestShellConfirmDefaultAuto:
    """出厂默认 smart→auto（2026-10-22）：三层默认一致 + 存量 smart 跟随迁移。

    显式 strict/auto 保留；显式 smart 无法与被动落库区分，被一并升级
    （用户裁决接受，自用为主）；改回 smart 后不再被迁移（幂等按值匹配）。
    """

    def test_new_db_defaults_to_auto(self, db):
        assert db.get_settings()["task_shell_confirm_mode"] == "auto"

    def test_model_default_matches(self):
        assert SettingsModel().task_shell_confirm_mode == "auto"

    def test_stored_smart_upgraded_to_auto(self, tmp_path):
        """设置页全量保存被动落库的 smart 存量 → 启动迁移跟随新默认。"""
        database = Database(str(tmp_path / "mig_smart.db"))
        database.initialize()
        database.save_settings({"task_shell_confirm_mode": "smart"})
        database._migrate_shell_confirm_default_auto()
        assert database.get_settings()["task_shell_confirm_mode"] == "auto"
        database.conn.close()

    def test_explicit_strict_and_auto_preserved(self, tmp_path):
        """显式 strict / auto 不被迁移触碰。"""
        database = Database(str(tmp_path / "mig_preserve.db"))
        database.initialize()
        database.save_settings({"task_shell_confirm_mode": "strict"})
        database._migrate_shell_confirm_default_auto()
        assert database.get_settings()["task_shell_confirm_mode"] == "strict"
        database.save_settings({"task_shell_confirm_mode": "auto"})
        database._migrate_shell_confirm_default_auto()
        assert database.get_settings()["task_shell_confirm_mode"] == "auto"
        database.conn.close()

    def test_migration_idempotent(self, tmp_path):
        """幂等：二次迁移无行可改不报错，终态仍 auto。"""
        database = Database(str(tmp_path / "mig_idem.db"))
        database.initialize()
        database.save_settings({"task_shell_confirm_mode": "smart"})
        database._migrate_shell_confirm_default_auto()
        database._migrate_shell_confirm_default_auto()
        assert database.get_settings()["task_shell_confirm_mode"] == "auto"
        database.conn.close()
