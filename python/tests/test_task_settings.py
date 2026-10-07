"""task_max_iterations 设置可见化回归（1.1 §9.8；§12.1 原 #2 已解决）。

回归背景：该键此前仅 task_agent.settings.get() 内联默认 25，不在
get_settings() 白名单——白名单缺失时已保存值会被静默丢弃（database.py
默认表契约），SettingsModel 反序列化同样丢键，用户无法调整迭代上限。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import Database
from schemas import SettingsModel


@pytest.fixture()
def db(tmp_path):
    database = Database(str(tmp_path / "settings_test.db"))
    database.initialize()
    return database


class TestTaskMaxIterationsExposed:
    def test_get_settings_default_contains_key(self, db):
        """默认表含 task_max_iterations=25（白名单回归）。"""
        assert db.get_settings()["task_max_iterations"] == 25

    def test_saved_value_round_trips(self, db):
        """已保存值经白名单回读不丢失（save → get 往返）。"""
        db.save_settings({"task_max_iterations": 60})
        assert db.get_settings()["task_max_iterations"] == 60

    def test_settings_model_declares_field(self):
        """SettingsModel 声明该字段（POST /settings 反序列化不丢键）。"""
        assert SettingsModel().task_max_iterations == 25
        assert SettingsModel(task_max_iterations=99).task_max_iterations == 99
