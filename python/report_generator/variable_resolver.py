"""python/report_generator/variable_resolver.py — 变量解析层（研究 DAG）。

职责（§3.1.1）：
  1. 隐式项目变量映射（company_name / credit_code / project_name / project_type / stage）
  2. user 变量合并（向导 Step2 填写）
  3. derived 变量解析：按 depends_on 建 DAG → 拓扑序 one-shot 求解
     - 每个 derived 变量 = 一轮检索（retrieve_for_section）+ 一次抽值（extract_value）
     - 检索证据不进报告参考文献（使用一次性 throwaway CitationRegistry）
     - 任一上游依赖未解析（UNRESOLVED）→ 本变量降级为 UNRESOLVED
  4. 成环检测（DAG 拓扑排序失败 → VariableResolutionError）

解析产物随 checkpoint 持久化，恢复时可直接复用（确定性 + 已付 token）。

设计参考：docs/2.5report-design.md §3.1.1 / §3.9
"""
import json
import logging
import re
from typing import Any, Optional

from errors import InkCubeError
from report_generator.prompts import build_extract_value_prompt
from report_generator.registry import CitationRegistry
from report_generator.retrieval import retrieve_for_section

logger = logging.getLogger(__name__)


# UNRESOLVED 哨兵：用 None 表示（JSON 可序列化，进 checkpoint）。
# instantiate 阶段 None → 空串；list 值以顿号连接注入。
UNRESOLVED = None

_EMPTY_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class VariableResolutionError(InkCubeError):
    """模板变量解析错误（循环依赖 / 必填缺失）。

    message 参数化：两种根因的 message/suggestion 不同，共用一个错误码
    （均属模板配置类错误，前端呈现路径一致）。
    """

    def __init__(
        self,
        message: str = "模板派生变量存在循环依赖",
        suggestion: str = "检查模板 variables 的 depends_on，确保不构成环",
        detail: str = "",
    ):
        super().__init__(
            "TEMPLATE_VARIABLE_ERROR", "CONFIG",
            message, suggestion, detail=detail,
        )


# ── 隐式项目变量 ────────────────────────────────────────────


def implicit_project_vars(project: Optional[dict]) -> dict:
    """从 project 记录映射隐式项目变量（§3.1.1）。

    这些变量无需在模板 variables 中声明，正文可直接以 {{name}} 引用。
    缺失字段以空串表示（确定性映射，非研究派生，不参与 UNRESOLVED 降级）。

    Args:
        project: projects 表记录 dict（db.get_project 产物），可为 None

    Returns:
        {company_name, credit_code, project_name, project_type, stage}
    """
    project = project or {}
    return {
        "company_name": project.get("company_name") or "",
        "credit_code": project.get("credit_code") or "",
        "project_name": project.get("name") or "",
        "project_type": project.get("type") or "",
        "stage": project.get("stage") or "",
    }


# ── 主入口 ──────────────────────────────────────────────────


async def resolve_variables(
    template: dict,
    project: Optional[dict],
    user_inputs: Optional[dict],
    *,
    search_tool: Any,
    llm_client: Any,
    llm_settings: dict,
    db: Any = None,
    base_project_ids: Optional[list[str]] = None,
    web_search_cfg: dict | None = None,
    internet_enabled: bool = True,
    include_project_kb: bool = True,
) -> tuple[dict, dict, dict]:
    """解析模板全部变量（隐式 + user + derived），返回可实例化的变量表。

    算法（确定性、有界）：
      1. ctx = implicit_project_vars(project) | user_inputs
      2. 取 template["variables"] 中 source=="derived" 的变量，按 depends_on 建 DAG
      3. 成环检测 → VariableResolutionError
      4. 拓扑序遍历（首版串行；无依赖分支未来可 asyncio.gather 并行）：
         - 任一依赖为 UNRESOLVED → 本变量降级为 UNRESOLVED（下游连锁降级）
         - 检索证据 → 抽值 → ctx[name]（scalar=str / list=list[str]）
         - 抽值用的知识卡片存 resolution_evidence[name]（审计/溯源）

    Args:
        template: 完整模板 dict（含 variables、report_depth）
        project: projects 表记录（提供隐式变量与 project_id），可为 None
        user_inputs: 向导收集的 user 变量 {name: value}
        search_tool: 知识检索工具实例
        llm_client: LLMClient 实例
        llm_settings: 应用设置 dict
        db: 数据库句柄（预留给检索通道）
        base_project_ids: 关联基础库项目 ID 列表
        web_search_cfg: 网络搜索配置 {"provider","key","engine"}（internet 通道，web_search_cfg_from_settings 归一产物）
        internet_enabled: 报告级互联网总开关（§1.1）；False 时强制派生变量仅走 knowledge，无视 data_sources
        include_project_kb: 项目知识库纳入开关（§1.2，2026-10-27）；False 时抽值
            检索同样排除本项目知识库（仅基础库），与章节检索语义一致

    Returns:
        (resolved_variables, resolution_evidence, usage)
          - resolved_variables: {name: scalar | list | None}，含全部声明变量与隐式变量
          - resolution_evidence: {derived_name: [card, ...]}，抽值所用证据（审计）
          - usage: 累计 API token 消耗 {"prompt_tokens","completion_tokens","total_tokens"}
    """
    user_inputs = user_inputs or {}
    total_usage = dict(_EMPTY_USAGE)

    def _add_usage(usage: Optional[dict]):
        if not usage:
            return
        total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
        total_usage["total_tokens"] += usage.get("total_tokens", 0)

    declared = _declared_variables(template)

    # ① 基础上下文：隐式项目变量 + user 输入
    ctx: dict = implicit_project_vars(project)
    for v in declared:
        name = v["name"]
        if v.get("source") == "user":
            # 声明的 user 变量：取用户输入；缺失或纯空白（向导初始值即空串，
            # 空白不是有效输入）均归一 UNRESOLVED，required 变量由下方
            # fast-fail 拦截，避免空白注入 prompt 后生成跑题内容
            raw = user_inputs.get(name, UNRESOLVED)
            if isinstance(raw, str) and not raw.strip():
                raw = UNRESOLVED
            ctx[name] = raw

    # required user 变量缺失 fast-fail：进入检索/生成前报错，而非 instantiate
    # 静默替换空串后生成跑题内容（§3.9）
    missing_required = [
        v.get("label") or v["name"]
        for v in declared
        if v.get("source") == "user" and v.get("required", True)
        and ctx.get(v["name"]) is UNRESOLVED
    ]
    if missing_required:
        raise VariableResolutionError(
            message=f"必填变量未填写: {'、'.join(missing_required)}",
            suggestion="请返回向导填写带 * 的必填变量后重新生成",
            detail=f"missing_required={missing_required}",
        )
    # user_inputs 中可能含未在模板声明的额外键，一并纳入（无害，供正文引用）
    for k, val in user_inputs.items():
        ctx.setdefault(k, val)

    resolution_evidence: dict[str, list] = {}

    # ② derived 变量 DAG（成环 → 抛异常）
    derived = [v for v in declared if v.get("source") == "derived"]
    order = _topo_sort(derived)

    report_depth = template.get("report_depth", "full")
    project_id = (project or {}).get("id", "")

    # ③ 拓扑序串行求解（确定性/可复现优先；并行留待后续）
    for v in order:
        name = v["name"]
        depends_on = v.get("depends_on", []) or []
        deps = {d: ctx.get(d, UNRESOLVED) for d in depends_on}

        # 上游降级：任一依赖未解析 → 本变量 UNRESOLVED
        missing = [d for d, val in deps.items() if val is UNRESOLVED]
        if missing:
            logger.warning(
                "resolve_variables: '%s' 依赖未解析 %s → 降级 UNRESOLVED", name, missing,
            )
            ctx[name] = UNRESOLVED
            continue

        cardinality = v.get("cardinality", "scalar")
        derive_hint = v.get("derive_hint", "")

        # 报告级总开关 gating：OFF 时强制仅用知识库，无视变量声明的 data_sources（§1.1）
        var_sources = v.get("data_sources") or ["knowledge"]
        if not internet_enabled:
            var_sources = ["knowledge"]

        # 合成 section 喂给检索层（section_id=变量名，title=label，description=依赖值，hint=derive_hint）
        synthetic = {
            "section_id": name,
            "title": v.get("label", name),
            "description": _render_deps(deps),
            "prompt_hint": derive_hint,
        }

        # 一次性 throwaway registry：证据不进报告参考文献（§3.1.1）
        throwaway = CitationRegistry()
        try:
            retrieval, r_usage = await retrieve_for_section(
                section=synthetic,
                search_tool=search_tool,
                registry=throwaway,
                project_id=project_id,
                llm_client=llm_client,
                llm_settings=llm_settings,
                digests=[],
                web_search_cfg=web_search_cfg,
                base_project_ids=base_project_ids,
                db=db,
                data_sources=var_sources,
                include_project_kb=include_project_kb,
                report_depth=report_depth,
            )
            _add_usage(r_usage)
        except Exception as e:
            logger.error("resolve_variables: '%s' 检索失败: %s → 降级 UNRESOLVED", name, e)
            ctx[name] = UNRESOLVED
            continue

        cards = retrieval.cards or []
        if not cards:
            # 无材料与 LLM 抽取失败是两种根因，审计时须可区分（否则只能靠
            # 汇总行「证据 0 组」间接推断）
            logger.warning(
                "resolve_variables: '%s' 检索 0 张卡片（各通道无相关材料），抽值无证据可用", name,
            )
        evidence_text = "\n".join(
            c.get("content", "") for c in cards if c.get("content")
        )
        # 存证据供审计/溯源（进 checkpoint）
        resolution_evidence[name] = [
            {
                "content": c.get("content", ""),
                "source_type": c.get("source_type", ""),
                "source_title": c.get("source_title", ""),
                "source_from": c.get("source_from", ""),
            }
            for c in cards
        ]

        value, e_usage = await _extract_value(
            derive_hint, deps, evidence_text, cardinality, llm_client,
        )
        _add_usage(e_usage)
        ctx[name] = value

        if isinstance(value, list):
            logger.info("resolve_variables: '%s' = [%d 项]", name, len(value))
        elif value is UNRESOLVED:
            logger.info("resolve_variables: '%s' = UNRESOLVED", name)
        else:
            logger.info("resolve_variables: '%s' = %s", name, value)

    # ④ 确保全部声明变量都在 ctx（instantiate 声明校验依赖此完整性）
    for v in declared:
        ctx.setdefault(v["name"], UNRESOLVED)

    logger.info(
        "resolve_variables: 完成，%d 个变量（derived %d），证据 %d 组，token=%d",
        len(ctx), len(derived), len(resolution_evidence), total_usage["total_tokens"],
    )
    return ctx, resolution_evidence, total_usage


# ── DAG 拓扑排序 ────────────────────────────────────────────


def _declared_variables(template: dict) -> list[dict]:
    """提取模板中结构合法且带 name 的变量声明。"""
    out = []
    for v in template.get("variables", []) or []:
        if isinstance(v, dict) and v.get("name"):
            out.append(v)
    return out


def _topo_sort(derived: list[dict]) -> list[dict]:
    """按 depends_on 对 derived 变量做拓扑排序（Kahn 算法）。

    仅统计指向其他 derived 变量的依赖边（指向隐式/user 变量的依赖天然就绪）。
    队列按名排序保证确定性输出。

    Raises:
        VariableResolutionError: 存在循环依赖
    """
    by_name: dict[str, dict] = {}
    for v in derived:
        by_name[v["name"]] = v

    indeg = {n: 0 for n in by_name}
    dependents: dict[str, list[str]] = {n: [] for n in by_name}
    for v in derived:
        for d in v.get("depends_on", []) or []:
            if d in by_name:
                indeg[v["name"]] += 1
                dependents[d].append(v["name"])

    queue = sorted(n for n, deg in indeg.items() if deg == 0)
    order: list[dict] = []
    while queue:
        n = queue.pop(0)
        order.append(by_name[n])
        ready = []
        for dep in dependents[n]:
            indeg[dep] -= 1
            if indeg[dep] == 0:
                ready.append(dep)
        if ready:
            queue.extend(ready)
            queue.sort()

    if len(order) != len(by_name):
        cycle_nodes = sorted(n for n in by_name if indeg[n] > 0)
        raise VariableResolutionError(detail=f"循环依赖变量: {', '.join(cycle_nodes)}")

    return order


# ── 抽值 ────────────────────────────────────────────────────


async def _extract_value(
    derive_hint: str,
    deps: dict,
    evidence_text: str,
    cardinality: str,
    llm_client: Any,
) -> tuple[Any, dict]:
    """调用 LLM 从证据中抽取变量值（one-shot resolver）。

    Returns:
        (value, usage)：scalar → str（空则 UNRESOLVED）；list → list[str]（空则 UNRESOLVED）
    """
    messages = build_extract_value_prompt(derive_hint, deps, evidence_text, cardinality)
    try:
        result = await llm_client.chat_full(messages)
        content = result.get("content", "")
        usage = result.get("usage", _EMPTY_USAGE)
    except Exception as e:
        logger.error("_extract_value: LLM 调用失败: %s → UNRESOLVED", e)
        return UNRESOLVED, dict(_EMPTY_USAGE)

    if cardinality == "list":
        value = _parse_list_value(content)
    else:
        value = content.strip() or UNRESOLVED

    if value is UNRESOLVED:
        logger.warning("_extract_value: 抽值为空（cardinality=%s）→ UNRESOLVED", cardinality)
    return value, usage


def _parse_list_value(content: str) -> Any:
    """解析 list 型抽值结果：JSON 数组优先，失败降级为按行分割。

    Returns:
        list[str]（非空）或 UNRESOLVED（空/无法解析）
    """
    text = (content or "").strip()
    if not text:
        return UNRESOLVED

    # ① 直接 JSON 数组
    data = _try_json_array(text)
    if data is not None:
        items = [str(x).strip() for x in data if str(x).strip()]
        return items or UNRESOLVED

    # ② markdown 代码块内的 JSON 数组
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        data = _try_json_array(m.group(1).strip())
        if data is not None:
            items = [str(x).strip() for x in data if str(x).strip()]
            return items or UNRESOLVED

    # ③ 降级：按行分割，剥离常见列表前缀
    items = []
    for line in text.splitlines():
        s = line.strip().lstrip("-*•").strip()
        s = re.sub(r"^\d+[.、)]\s*", "", s).strip()
        if s:
            items.append(s)
    return items or UNRESOLVED


def _try_json_array(text: str) -> Optional[list]:
    """尝试把文本解析为 JSON 数组，失败返回 None。"""
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _render_deps(deps: dict) -> str:
    """把已解析的上游依赖渲染为合成 section 的 description 文本。"""
    if not deps:
        return ""
    parts = []
    for k, v in deps.items():
        if v is UNRESOLVED:
            continue
        if isinstance(v, list):
            parts.append(f"{k}: {'、'.join(str(x) for x in v)}")
        else:
            parts.append(f"{k}: {v}")
    return "；".join(parts)
