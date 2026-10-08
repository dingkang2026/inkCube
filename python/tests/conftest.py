"""python/tests/conftest.py — 测试会话级日志隔离。

根因链：main.py 在模块导入时（非 lifespan）把生产 app.log 的 RotatingFileHandler
挂上 logging.root；test_security_p0.py 在测试体内 import main 后，同一 pytest
进程内（含该时刻之后）所有测试的日志都会写进生产日志文件，污染审计链路。

本 fixture 在每个测试前后各摘除一次 root 上的 RotatingFileHandler（测试中途
import main 新挂的 handler 由 teardown 兜底摘除）。控制台 handler 保留，
caplog 机制独立于 root handlers、不受影响；现有测试无任何断言依赖 app.log
文件内容，无破坏面。
"""
import logging
from logging.handlers import RotatingFileHandler

import pytest


@pytest.fixture(autouse=True)
def _isolate_production_log():
    def _strip():
        for h in [h for h in logging.root.handlers
                  if isinstance(h, RotatingFileHandler)]:
            logging.root.handlers.remove(h)

    _strip()
    yield
    _strip()
