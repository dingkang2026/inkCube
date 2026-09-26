"""python/report_generator/registry.py — 全局引用注册器。

职责：
  - 管理报告生成过程中所有知识卡片和原始片段的引用
  - 去重 key=(source_type, source_from, content_hash)，按注册顺序分配全局唯一编号
  - 提供 Prompt 呈现格式（知识卡片格式 / 原始片段格式）
  - 支持序列化/反序列化（用于 checkpoint 持久化）

设计参考：docs/2.1project-design.md §6.7.4
"""
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    """计算内容哈希（取前 16 位）。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


class CitationRegistry:
    """全局引用注册器。

    支持两种注册方式：
      1. register_card(card) — 注册知识卡片（推荐）
      2. register(doc_id, chunk_id, ...) — 注册原始片段（向后兼容）

    每个引用条目包含：
      - card_id: 卡片 ID（知识卡片模式）
      - section_id: 所属章节
      - content: 内容文本
      - source_type: "knowledge" | "internet" | "raw"（向后兼容）
      - source_title: 来源名称（文档名/网页标题）
      - source_from: 来源详情（文件路径+页码 / URL）
      - ref_number: 全局唯一编号（从 1 开始）
    """

    def __init__(self):
        # key=(source_type, source_from, content_hash) -> entry dict
        self._entries: dict[tuple[str, str, str], dict] = {}
        # 按注册顺序维护编号
        self._ordered: list[dict] = []
        self._next_number = 1

    def register_card(self, card: dict) -> int:
        """注册一个知识卡片，返回其全局编号。

        去重 key=(source_type, source_from, content_hash)。
        如果已存在，直接返回已有编号。

        Args:
            card: 知识卡片 dict，包含 card_id, section_id, content,
                  source_type, source_title, source_from

        Returns:
            全局引用编号 [1], [2], ...
        """
        content = card.get("content", "")
        source_type = card.get("source_type", "knowledge")
        source_from = card.get("source_from", "")

        key = (source_type, source_from, _content_hash(content))
        if key in self._entries:
            return self._entries[key]["ref_number"]

        entry = {
            "card_id": card.get("card_id", ""),
            "section_id": card.get("section_id", ""),
            "content": content,
            "source_type": source_type,
            "source_title": card.get("source_title", ""),
            "source_from": source_from,
            "source_date": card.get("source_date", ""),
            "ref_number": self._next_number,
        }
        self._entries[key] = entry
        self._ordered.append(entry)
        number = self._next_number
        self._next_number += 1

        logger.debug("Registry: [%d] card=%s, source=%s:%s",
                     number, card.get("card_id", "?"), source_type, source_from[:40])
        return number

    def register(
        self,
        doc_id: str = "",
        chunk_id: str = "",
        doc_name: str = "",
        page: Optional[int] = None,
        content: str = "",
    ) -> int:
        """注册一个原始片段（向后兼容）。

        如果 (doc_id, chunk_id) 已存在，直接返回已有编号（去重）。

        Args:
            doc_id: 文档 ID
            chunk_id: 片段 ID
            doc_name: 文档名称
            page: 页码
            content: 原文片段

        Returns:
            全局引用编号 [1], [2], ...
        """
        # 向后兼容：用 (doc_id, chunk_id) 作为去重 key 的一部分
        compat_key = ("raw", f"{doc_id}:{chunk_id}", _content_hash(content))
        if compat_key in self._entries:
            return self._entries[compat_key]["ref_number"]

        page_str = f" p.{page}" if page else ""
        source_from = f"{doc_id}{page_str}"

        entry = {
            "card_id": "",
            "section_id": "",
            "content": content,
            "source_type": "raw",
            "source_title": doc_name,
            "source_from": source_from,
            "source_date": "",
            # 保留旧字段用于序列化兼容
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "doc_name": doc_name,
            "page": page,
            "ref_number": self._next_number,
        }
        self._entries[compat_key] = entry
        self._ordered.append(entry)
        number = self._next_number
        self._next_number += 1

        logger.debug("Registry: [%d] %s (doc=%s, chunk=%s)",
                     number, doc_name, doc_id[:8], chunk_id[:8])
        return number

    def get_entry(self, ref_number: int) -> Optional[dict]:
        """根据编号获取引用条目。"""
        for entry in self._ordered:
            if entry["ref_number"] == ref_number:
                return entry
        return None

    def all_entries(self) -> list[dict]:
        """返回所有引用条目（按注册顺序）。"""
        return list(self._ordered)

    def all_ref_numbers(self) -> list[int]:
        """返回所有已注册的引用编号。"""
        return [e["ref_number"] for e in self._ordered]

    def format_cards_for_prompt(self, ref_numbers: list[int], max_content_len: int = 500) -> str:
        """将指定编号的知识卡片格式化为 Prompt 中的参考资料文本。

        格式（按设计文档 §6.7.5）：
        【知识卡片】
        [n] card_id | 来源: source_type - source_title
            content
        """
        lines = ["【知识卡片】"]
        for num in ref_numbers:
            entry = self.get_entry(num)
            if not entry:
                continue

            card_id = entry.get("card_id", "")
            source_type = entry.get("source_type", "raw")
            source_title = entry.get("source_title", "未知来源")
            content = entry.get("content", "")

            # 截断过长内容
            if len(content) > max_content_len:
                content = content[:max_content_len] + "……"

            # 来源类型标签
            type_label = {"knowledge": "知识库", "internet": "互联网", "raw": "文档"}.get(source_type, source_type)

            if card_id:
                lines.append(f"[{num}] {card_id} | 来源: {type_label} - {source_title}")
            else:
                # 向后兼容：旧格式
                page = entry.get("page")
                page_str = f" p.{page}" if page else ""
                lines.append(f"[{num}] 《{source_title}》{page_str}：「{content}」")
                continue

            lines.append(f"    {content}")

        return "\n".join(lines)

    def format_for_prompt(self, ref_numbers: list[int], max_content_len: int = 500) -> str:
        """将指定编号的引用格式化为 Prompt 中的参考资料文本。

        自动检测格式：如果有 card_id 使用卡片格式，否则使用旧格式。
        """
        has_cards = any(
            (entry := self.get_entry(n)) and entry.get("card_id")
            for n in ref_numbers
        )
        if has_cards:
            return self.format_cards_for_prompt(ref_numbers, max_content_len)

        # 旧格式兼容
        lines = []
        for num in ref_numbers:
            entry = self.get_entry(num)
            if not entry:
                continue
            source_title = entry.get("source_title") or entry.get("doc_name", "未知文档")
            page = entry.get("page")
            content = entry.get("content", "")

            if len(content) > max_content_len:
                content = content[:max_content_len] + "……"

            page_str = f" p.{page}" if page else ""
            lines.append(f"[{num}] 《{source_title}》{page_str}：「{content}」")

        return "\n".join(lines)

    def format_source_list(self) -> str:
        """格式化引用来源列表（用于 post_body prompt）。

        格式：编号 + 来源信息
        """
        lines = []
        for entry in self._ordered:
            num = entry["ref_number"]
            card_id = entry.get("card_id", "")
            source_title = entry.get("source_title") or entry.get("doc_name", "未知文档")
            source_type = entry.get("source_type", "raw")

            type_label = {"knowledge": "知识库", "internet": "互联网", "raw": "文档"}.get(source_type, source_type)

            if card_id:
                lines.append(f"[{num}] {card_id} | {type_label} - {source_title}")
            else:
                lines.append(f"[{num}] 《{source_title}》")
        return "\n".join(lines)

    def mark_cited(self, ref_numbers: list[int]) -> None:
        """标记哪些引用被实际引用了（用于全局校验）。"""
        cited_set = set(ref_numbers)
        for entry in self._ordered:
            if entry["ref_number"] in cited_set:
                entry["_cited"] = True

    def get_uncited(self) -> list[dict]:
        """获取未被引用的条目。"""
        return [e for e in self._ordered if not e.get("_cited")]

    def remove_uncited(self) -> list[int]:
        """清理未被引用的条目，返回被移除的编号列表。"""
        to_remove = []
        for entry in self._ordered:
            if not entry.get("_cited"):
                to_remove.append(entry["ref_number"])

        if to_remove:
            self._ordered = [e for e in self._ordered if e.get("_cited")]
            self._rebuild_index()
            self._renumber()
            logger.info("Removed %d uncited references, renumbered to %d entries.",
                        len(to_remove), len(self._ordered))

        return to_remove

    def _rebuild_index(self) -> None:
        """重建去重索引。"""
        self._entries = {}
        for entry in self._ordered:
            source_type = entry.get("source_type", "raw")
            source_from = entry.get("source_from", "")
            content = entry.get("content", "")
            # 旧格式条目使用 doc_id:chunk_id
            if source_type == "raw" and entry.get("doc_id"):
                source_from = f"{entry.get('doc_id', '')}:{entry.get('chunk_id', '')}"
            key = (source_type, source_from, _content_hash(content))
            self._entries[key] = entry

    def _renumber(self) -> dict[int, int]:
        """重新编号（按当前顺序从 1 开始）。

        Returns:
            映射表 old_number -> new_number
        """
        mapping: dict[int, int] = {}
        for i, entry in enumerate(self._ordered, start=1):
            old = entry["ref_number"]
            mapping[old] = i
            entry["ref_number"] = i
        self._next_number = len(self._ordered) + 1
        return mapping

    def renumber_and_remap(self, text: str) -> str:
        """重新编号并更新文本中的引用编号。

        Args:
            text: 包含 [n] 引用的文本

        Returns:
            更新后的文本
        """
        mapping = self._renumber()
        import re
        def replace_ref(m):
            old_num = int(m.group(1))
            new_num = mapping.get(old_num, old_num)
            return f"[{new_num}]"
        return re.sub(r"\[(\d+)\]", replace_ref, text)

    # ── 序列化 ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        """序列化为 dict（用于 checkpoint 持久化）。"""
        return {
            "entries": self._ordered,
            "next_number": self._next_number,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CitationRegistry":
        """从 dict 反序列化（用于断点恢复）。"""
        reg = cls()
        reg._ordered = data.get("entries", [])
        reg._next_number = data.get("next_number", 1)
        reg._rebuild_index()
        return reg

    def save(self, path: str | Path) -> None:
        """保存到 JSON 文件。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "CitationRegistry":
        """从 JSON 文件加载。"""
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def __len__(self) -> int:
        return len(self._ordered)

    def __repr__(self) -> str:
        return f"CitationRegistry(entries={len(self._ordered)})"
