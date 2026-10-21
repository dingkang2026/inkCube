"""python/activities_registry.py — 统一长时活动注册表（0.4lifecycle-design.md §6.1 / coding-habits §2.7）。

所有长时活动（排队 → 执行 → 终态）以 ActivitySource 形式注册于此；
/activities/status 聚合端点只从注册表取活跃快照（routers/activities.py），
前端 useActivity(kind) 按需消费（0.4 §2.2 单一数据源）。

契约：
- kind 与 notifications.entity_type 一致（file / wiki_compile / report / task / model_download /
  ledger_parse / form_template / form_fill / project_export / project_import / ppt / schedule）；
- fetch() 返回 list[dict]，每项至少含 entity_type（= kind），
  可选 entity_id / project_id / project_name / entity_title / count / status / progress_text / progress；
- fetch() 只读快照，禁止副作用；
- 单源异常只影响本源（隔离降级为空），不影响其他源与聚合端点。

全部 kind 均已收编（注册清单见 routers/activities.py，0.4 §6.1 / §8.2）：
新增长时活动一律实现 ActivitySource 后在此注册，禁止绕过注册表自建聚合。
"""
import logging
from typing import Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ActivitySource(Protocol):
    """长时活动源协议：kind + 活跃快照 fetcher。"""

    kind: str

    def fetch(self) -> list[dict]:
        ...


class FunctionSource:
    """轻量包装：把 (kind, fetch_fn) 适配为 ActivitySource。

    fetch_fn 应以 lambda 形式引用模块级函数（运行时名字查找），
    保证测试 monkeypatch 模块函数后注册表取到替换实现。
    """

    def __init__(self, kind: str, fetch_fn: Callable[[], list[dict]]):
        self.kind = kind
        self._fetch_fn = fetch_fn

    def fetch(self) -> list[dict]:
        return self._fetch_fn()


_providers: dict[str, ActivitySource] = {}


def register(source: ActivitySource) -> None:
    """注册活动源；同 kind 后注册覆盖（支持热替换与测试隔离）。"""
    _providers[source.kind] = source


def unregister(kind: str) -> None:
    """注销活动源（不存在时静默）。"""
    _providers.pop(kind, None)


def registered_kinds() -> list[str]:
    return sorted(_providers)


def snapshot() -> list[dict]:
    """全源活跃快照；单源异常隔离降级为空列表，不影响其余源。"""
    items: list[dict] = []
    for kind in sorted(_providers):
        try:
            items.extend(_providers[kind].fetch())
        except Exception:
            logger.exception("activity source %s failed, degraded to empty", kind)
    return items
