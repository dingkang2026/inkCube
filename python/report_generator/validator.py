"""python/report_generator/validator.py — 校验层。

职责：
  1. 单节校验（每节生成后立即执行，§3.6 规则）
  2. 全局校验（所有章节完成后执行）

设计参考：docs/2.5report-design.md §3.6
"""
import logging
import re

logger = logging.getLogger(__name__)


# ── 单节校验 ────────────────────────────────────────────────


class SectionViolation:
    """单节校验违规记录。"""

    def __init__(self, rule: int, message: str, severity: str = "error"):
        self.rule = rule
        self.message = message
        self.severity = severity  # "error" | "warning"

    def __repr__(self):
        return f"Violation(R{self.rule}: {self.message})"


def validate_section(
    output: str,
    given_ref_numbers: set[int],
    section: dict,
    report_depth: str = "full",
) -> list[SectionViolation]:
    """对单节生成结果执行校验规则（§3.6）。

    Args:
        output: LLM 生成的章节文本
        given_ref_numbers: 本节给定的引用编号集合
        section: 节定义 dict（含 section_id、title、compose_mode 等）
        report_depth: 报告深度 "full" | "brief"，决定 Rule 3 是否启用及目标字数

    Returns:
        违规列表。空列表表示全部通过。
    """
    violations: list[SectionViolation] = []

    # Rule 1: 引用编号 ⊆ 本节给定编号集
    cited = set(_extract_refs(output))
    hallucinated = cited - given_ref_numbers
    if hallucinated:
        violations.append(SectionViolation(
            rule=1,
            message=f"引用了未给定的编号: {sorted(hallucinated)}",
        ))

    # Rule 2: 出现 markdown 标题或加粗
    md_headings = re.findall(r"^#{1,6}\s+.+$", output, re.MULTILINE)
    bold_text = re.findall(r"\*\*.+?\*\*", output)
    if md_headings:
        violations.append(SectionViolation(
            rule=2,
            message=f"出现 Markdown 标题: {md_headings[:3]}",
        ))
    if bold_text:
        violations.append(SectionViolation(
            rule=2,
            message=f"出现 Markdown 加粗: {bold_text[:3]}",
        ))

    # Rule 3: 字数偏差 > ±20%（§3.6）。v2 已删除模板 word_count，目标字数按 depth 推算。
    # 仅对 full 深度的 standard 节启用：
    #   - post_body（摘要/结论）天然短小，不适用每节字数目标；
    #   - brief 深度 §3.5 指示「每节 200-500 字」与 §3.6 目标 1200 存在冲突，
    #     强校验会触发无谓重试，故 brief 不启用。
    if report_depth == "full" and section.get("compose_mode") != "post_body":
        target = 1200
        actual = len(output)
        ratio = actual / target
        if ratio < 0.8 or ratio > 1.2:
            violations.append(SectionViolation(
                rule=3,
                message=f"字数 {actual} 偏离目标 {target}（{ratio:.0%}，允许 ±20%）",
            ))

    # Rule 4: ◆ 小标题超过 15 字（v2 已删除 promote_subhead，不再检查小标题数量）
    subheads = re.findall(r"◆\s*(.+)", output)
    for sh in subheads:
        # 去掉尾部可能的标点
        title = sh.strip().rstrip("。，、；")
        if len(title) > 15:
            violations.append(SectionViolation(
                rule=4,
                message=f"小标题超过 15 字: 「{title}」（{len(title)} 字）",
            ))

    # Rule 5: 结尾套话
    ending_phrases = ["综上所述", "总而言之", "综上", "总之"]
    last_200 = output[-200:] if len(output) > 200 else output
    for phrase in ending_phrases:
        if phrase in last_200:
            violations.append(SectionViolation(
                rule=5,
                message=f"结尾出现套话: 「{phrase}」",
            ))
            break

    # Rule 7: 首行不是正文（输出了标题/前言）
    first_line = output.strip().split("\n")[0].strip()
    if first_line.startswith("#") or first_line.startswith("◆"):
        violations.append(SectionViolation(
            rule=7,
            message=f"首行输出了标题: 「{first_line[:50]}」",
        ))
    # 检测前言套话
    preamble_patterns = [
        r"^(以下是|本节将|下面将|本章将)",
        r"^(关于|针对|对于).{2,20}(的分析|的报告|的内容)",
    ]
    for pat in preamble_patterns:
        if re.match(pat, first_line):
            violations.append(SectionViolation(
                rule=7,
                message=f"首行是前言套话: 「{first_line[:50]}」",
            ))
            break

    return violations


def format_retry_feedback(violations: list[SectionViolation]) -> str:
    """将违规列表格式化为重试反馈文本（注入到 retry prompt）。"""
    lines = ["【上次生成存在以下问题，请修正后重新输出】"]
    for i, v in enumerate(violations, 1):
        prefix = "⚠️" if v.severity == "warning" else "❌"
        lines.append(f"{i}. {prefix} [规则{v.rule}] {v.message}")
    lines.append("\n请严格遵守输出契约，直接输出正文，不要输出标题或前言。")
    return "\n".join(lines)


# ── 全局校验 ────────────────────────────────────────────────


def validate_global(
    full_text: str,
    registry,
) -> list[dict]:
    """全局校验（所有章节完成后执行）。

    检查项：
      - 幻觉引用（cited - defined 非空）
      - 未被引用（defined - cited）
      - 编号连续性

    Args:
        full_text: 完整报告文本
        registry: CitationRegistry 实例

    Returns:
        问题列表。空列表表示全部通过。
    """
    issues: list[dict] = []

    # 提取全文中所有引用编号
    cited = set(_extract_refs(full_text))
    defined = set(registry.all_ref_numbers())

    # 幻觉引用
    hallucinated = cited - defined
    if hallucinated:
        issues.append({
            "type": "hallucinated_refs",
            "refs": sorted(hallucinated),
            "message": f"幻觉引用: {sorted(hallucinated)}",
        })
        logger.warning("Global validation: hallucinated refs %s", sorted(hallucinated))

    # 未被引用
    uncited = defined - cited
    if uncited:
        issues.append({
            "type": "uncited_refs",
            "refs": sorted(uncited),
            "message": f"未被引用的编号: {sorted(uncited)}",
        })
        logger.info("Global validation: uncited refs %s", sorted(uncited))

    # 编号连续性
    if defined:
        expected = set(range(1, max(defined) + 1))
        missing = expected - defined
        if missing:
            issues.append({
                "type": "gap_in_numbering",
                "refs": sorted(missing),
                "message": f"编号不连续，缺失: {sorted(missing)}",
            })

    return issues


def fix_hallucinated_refs(text: str, valid_refs: set[int]) -> str:
    """删除文本中的幻觉引用编号。

    Args:
        text: 报告文本
        valid_refs: 有效的引用编号集合

    Returns:
        清理后的文本
    """
    def replace_ref(m):
        num = int(m.group(1))
        if num in valid_refs:
            return m.group(0)
        return ""  # 删除无效引用

    return re.sub(r"\[(\d+)\]", replace_ref, text)


# ── 辅助 ────────────────────────────────────────────────────


def _extract_refs(text: str) -> list[int]:
    """从文本中提取所有 [n] 引用编号。"""
    return [int(m) for m in re.findall(r"\[(\d+)\]", text)]
