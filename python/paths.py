"""数据目录单一来源（2026-10-13 收敛）。

历史上 get_data_dir 有 5 份副本（main.py + knowledge/reports/system/wiki
四个 router，靠 dirname 层数差异对齐路径，system.py 还以 _get_data_dir
别名存在），属重复建设；routers/tasks.py、routers/skills.py、
agent/task_agent.py 另有 4 处函数内 `from main import get_data_dir`
延迟导入，是 main → router/agent → main 循环依赖的补丁。本模块无任何
项目内依赖，各处顶层 import，副本与延迟导入一并消除。
"""
import os


def get_data_dir() -> str:
    """数据目录：INKCUBE_DATA_DIR 优先（生产由 Electron spawn 注入），
    否则 <python>/data（锚定源码布局，与 cwd 无关）。"""
    if os.environ.get("INKCUBE_DATA_DIR"):
        return os.environ["INKCUBE_DATA_DIR"]
    return os.path.join(os.path.dirname(__file__), "data")
