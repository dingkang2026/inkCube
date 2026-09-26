"""python/report_generator/token_tracker.py — Token 统计追踪。

职责：
  - 每次 LLM 调用记录精确 token 消耗（来自 API 返回的 usage 字段）
  - 按 section_id + call_type 分类记录
  - 报告完成时汇总写入 DB
  - 支持序列化/反序列化（用于 checkpoint 持久化）

设计参考：docs/2.1project-design.md §6.7.8
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class TokenTracker:
    """Token 消耗追踪器。

    每次 LLM 调用通过 record() 记录精确 token 数（来自 API usage），
    报告完成时 summary() 汇总。
    """

    def __init__(self):
        self._records: list[dict] = []

    def record(
        self,
        section_id: str,
        call_type: str,
        usage: dict,
    ) -> None:
        """记录一次 LLM 调用的精确 token 消耗。

        Args:
            section_id: 所属章节 ID
            call_type: 调用类型（search_queries / card_build / generation / digest / finalizer）
            usage: API 返回的 usage 字段
                   {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
        """
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        self._records.append({
            "section_id": section_id,
            "call_type": call_type,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        })

        logger.debug(
            "TokenTracker: section=%s, type=%s, prompt=%d, completion=%d, total=%d",
            section_id, call_type, prompt_tokens, completion_tokens, total_tokens,
        )

    def summary(self) -> dict:
        """汇总所有记录的 token 消耗。

        Returns:
            {
                "total_prompt_tokens": int,
                "total_completion_tokens": int,
                "total_tokens": int,
                "by_section": {section_id: {"prompt": int, "completion": int, "total": int}},
                "by_type": {call_type: {"prompt": int, "completion": int, "total": int}},
                "call_count": int,
            }
        """
        total_prompt = 0
        total_completion = 0
        by_section: dict[str, dict] = {}
        by_type: dict[str, dict] = {}

        for r in self._records:
            pt = r["prompt_tokens"]
            ct = r["completion_tokens"]
            tt = r["total_tokens"]
            total_prompt += pt
            total_completion += ct

            # 按章节汇总
            sid = r["section_id"]
            if sid not in by_section:
                by_section[sid] = {"prompt": 0, "completion": 0, "total": 0}
            by_section[sid]["prompt"] += pt
            by_section[sid]["completion"] += ct
            by_section[sid]["total"] += tt

            # 按类型汇总
            ctype = r["call_type"]
            if ctype not in by_type:
                by_type[ctype] = {"prompt": 0, "completion": 0, "total": 0}
            by_type[ctype]["prompt"] += pt
            by_type[ctype]["completion"] += ct
            by_type[ctype]["total"] += tt

        return {
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "by_section": by_section,
            "by_type": by_type,
            "call_count": len(self._records),
        }

    # ── 序列化 ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        """序列化为 dict（用于 checkpoint 持久化）。"""
        return {"records": list(self._records)}

    @classmethod
    def from_dict(cls, data: dict) -> "TokenTracker":
        """从 dict 反序列化（用于断点恢复）。"""
        tracker = cls()
        tracker._records = data.get("records", [])
        return tracker

    def save(self, path: str | Path) -> None:
        """保存到 JSON 文件。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "TokenTracker":
        """从 JSON 文件加载。"""
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        s = self.summary()
        return f"TokenTracker(calls={s['call_count']}, total_tokens={s['total_tokens']})"
