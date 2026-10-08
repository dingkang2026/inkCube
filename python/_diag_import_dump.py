# -*- coding: utf-8 -*-
"""临时诊断:import main 挂死时 dump 线程栈。用后即删。"""
import faulthandler
import sys

faulthandler.dump_traceback_later(40, exit=True)
sys.path.insert(0, ".")
import main  # noqa: E402,F401

print("IMPORT OK")
