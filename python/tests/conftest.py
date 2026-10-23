"""python/tests/conftest.py — 测试会话级生产环境隔离（日志 + 数据目录 + 进程环境）。

一、日志隔离
根因链：main.py 在模块导入时（非 lifespan）把生产 app.log 的 RotatingFileHandler
挂上 logging.root；test_security_p0.py 在测试体内 import main 后，同一 pytest
进程内（含该时刻之后）所有测试的日志都会写进生产日志文件，污染审计链路。

本 fixture 在每个测试前后各摘除一次 root 上的 RotatingFileHandler（测试中途
import main 新挂的 handler 由 teardown 兜底摘除）。控制台 handler 保留，
caplog 机制独立于 root handlers、不受影响；现有测试无任何断言依赖 app.log
文件内容，无破坏面。

二、数据目录隔离（2026-10-22）
根因链：test_system_data_api.py 漏 patch get_data_dir，其调用的
_clean_knowledge / reset_all_data 直接 rmtree 真实 python/data 产物目录
（tasks/schedules/skills/files/reports/form_templates），每次全量回归清
一次用户数据（DB 因 deps.db 已隔离而幸存，隐蔽性强）。测试级 patch 是
根因修复；本钩子把整个 pytest 进程的 INKCUBE_DATA_DIR 强制指向会话级
临时目录（paths.get_data_dir 的最高优先级入口），任何未来漏 patch 的
测试物理上够不到生产数据，属纵深防御。

三、环境变量隔离（2026-10-22）
根因链：main.py 模块级 os.environ.setdefault("UV_CACHE_DIR", ...) 在
test_security_p0 import main 时永久写入 pytest 进程环境，使
venv_manager.is_uv_exclusion_verified 从「未设置视为通过」翻转为
「marker 缺失 → 拦截」，跨文件污染后续所有测试（外部 skill 用例全量跑
必挂、单跑必过——字母序 security < shell）。autouse fixture 在每个
测试开始前摘除；需要该变量的用例（test_venv_lazy）自行 setenv 不受
影响。
"""
import logging
import os
import shutil
import tempfile
from logging.handlers import RotatingFileHandler

import pytest


def pytest_configure(config):
    os.environ["INKCUBE_DATA_DIR"] = tempfile.mkdtemp(
        prefix="inkcube-pytest-data-")


def pytest_unconfigure(config):
    data_dir = os.environ.pop("INKCUBE_DATA_DIR", "")
    if data_dir:
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolate_production_env():
    # main.py import 副作用的进程级泄漏，见模块 docstring「三」
    os.environ.pop("UV_CACHE_DIR", None)

    def _strip():
        for h in [h for h in logging.root.handlers
                  if isinstance(h, RotatingFileHandler)]:
            logging.root.handlers.remove(h)

    _strip()
    yield
    _strip()
