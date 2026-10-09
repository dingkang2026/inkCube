"""python/knowledge/docling_serializers.py

Docling-dependent serializer classes for document parsing (2.2 §4.3).

Cold-start note: these classes need docling_core base classes at
class-definition time. Keeping them in doc_parser.py dragged the whole
docling_core → transformers → torch chain (~6s, measured via
`python -X importtime`) into the backend's module-level import chain.
They live in this dedicated module so the cost is paid only on the first
document parse — doc_parser._extract_chunks imports it lazily.
"""
from datetime import date, timedelta

from docling_core.transforms.chunker.hierarchical_chunker import ChunkingDocSerializer
from docling_core.transforms.serializer.base import (
    BaseDocSerializer,
    BaseSerializerProvider,
    BaseTableSerializer,
)
from docling_core.transforms.serializer.common import (
    SerializationResult,
    create_ser_result,
)
from docling_core.types.doc import DoclingDocument, TableItem


class RowMarkdownTableSerializer(BaseTableSerializer):
    """Table serializer emitting row-level Markdown (2.2 §4.3).

    Replaces docling_core's default TripletTableSerializer whose
    "row, col = value." triplets lose column-header semantics on xlsx
    (merged title/unit rows become empty headers, label rows repeat per
    column). All formats serialize tables uniformly as Markdown rows so
    HybridChunker can split on line boundaries and repeat the header per
    chunk (repeat_table_header).

    Header detection: first row within the top MAX_HEADER_SCAN having
    >= MIN_HEADER_NON_EMPTY non-empty cells covering >= MIN_HEADER_FILL_RATIO
    of all columns. xlsx merged cells do not share objects in the grid
    (col_span is always 1), so sparse title/unit rows can only be told
    apart by sparsity (measured: title 1/16, unit row 2/16, header 15/16);
    the fill ratio is computed against num_cols so unit rows with a few
    non-None cells cannot reach it.
    Rows above the header are emitted once as preamble text.
    """

    MAX_HEADER_SCAN = 5
    MIN_HEADER_NON_EMPTY = 2
    MIN_HEADER_FILL_RATIO = 0.6   # of num_cols: a real header covers most columns
    # Excel serial dates for 2000-01-01 .. 2099-12-31 (epoch 1899-12-30)
    _SERIAL_MIN = 36526
    _SERIAL_MAX = 73050

    def serialize(
        self,
        *,
        item: TableItem,
        doc_serializer: BaseDocSerializer,
        doc: DoclingDocument,
        **kwargs,
    ) -> SerializationResult:
        parts = []
        cap_res = doc_serializer.serialize_captions(item=item, **kwargs)
        if cap_res.text:
            parts.append(cap_res.text)

        grid = item.data.grid
        if not grid or not grid[0]:
            return create_ser_result(text="\n".join(parts) if parts else "", span_source=item)

        num_rows, num_cols = len(grid), len(grid[0])

        def cell_text(r, c):
            cell = grid[r][c]
            return (cell.text or "").strip() if cell is not None else ""

        # 垂直合并的重复格：span 覆盖位置与锚点是同一对象（仅 PDF 等真合并
        # 后端存在；xlsx 后端不共享对象，此分支不会触发）
        def is_span_repeat(r, c):
            return r > 0 and grid[r][c] is not None and grid[r][c] is grid[r - 1][c]

        # 水平 span 按 id 去重后的唯一格列表 [(col, cell), ...]
        def row_unique_cells(r):
            seen = {}
            for c in range(num_cols):
                cell = grid[r][c]
                if cell is not None and id(cell) not in seen:
                    seen[id(cell)] = (c, cell)
            return list(seen.values())

        # 表头检测：非空格 >= 2 且占列数 >= 60%（标题/说明行稀疏，真表头满格）。
        # 分母必须是总列数：说明行若只有少数非 None 格（如「编制单位」+
        # 「金额单位」），按非 None 格算占比会是 100% 而被误判为表头
        header_idx = None
        for r in range(min(self.MAX_HEADER_SCAN, num_rows)):
            unique = row_unique_cells(r)
            non_empty = sum(1 for _, cell in unique if (cell.text or "").strip())
            if (
                non_empty >= self.MIN_HEADER_NON_EMPTY
                and non_empty / num_cols >= self.MIN_HEADER_FILL_RATIO
            ):
                header_idx = r
                break

        preamble_lines = []
        if header_idx is None:
            # 无可检测表头：全部行按纯行输出（退化但可读），不造表头/分隔行
            body_start = 0
        else:
            body_start = header_idx + 1
            for r in range(header_idx):
                vals = [cell_text(r, c) for c, _ in row_unique_cells(r) if cell_text(r, c)]
                if vals:
                    preamble_lines.append("，".join(dict.fromkeys(vals)))

        # 表头行 Excel 日期序列号 → YYYY-MM-DD：xlsx 日期单元格被序列化为
        # 天数整数；仅当行内 >= 3 个纯数字格（多列日期表头）才整行转换
        def to_iso_date(v):
            try:
                n = float(v)
            except (TypeError, ValueError):
                return v
            if self._SERIAL_MIN <= n <= self._SERIAL_MAX and n == int(n):
                return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
            return v

        lines = list(preamble_lines)
        if header_idx is not None and header_idx < num_rows:
            header_cells = [cell_text(header_idx, c) or " " for c in range(num_cols)]
            if sum(1 for v in header_cells if v.strip().isdigit()) >= 3:
                header_cells = [to_iso_date(v) for v in header_cells]
            lines.append("| " + " | ".join(header_cells) + " |")
            lines.append("|" + "|".join([" - " for _ in range(num_cols)]) + "|")

        for r in range(body_start, num_rows):
            cells = [cell_text(r, c) for c in range(num_cols)]
            if not any(cells):
                continue
            row_cells = []
            for c, t in enumerate(cells):
                if is_span_repeat(r, c):
                    row_cells.append(" ")
                else:
                    row_cells.append(t.replace("\n", " ").replace("|", "&#124;") or " ")
            lines.append("| " + " | ".join(row_cells) + " |")

        parts.append("\n".join(lines))
        return create_ser_result(text="\n\n".join(p for p in parts if p), span_source=item)

    def get_header_and_body_lines(self, *, table_text: str, **kwargs):
        """Split the serialized table into (header lines, body lines) for the
        chunker's table path: header = column row + separator row, so split
        chunks stay on line boundaries and repeat the header."""
        all_lines = table_text.splitlines(True)
        sep_idx = next(
            (i for i, l in enumerate(all_lines)
             if l.rstrip("\n").startswith("|") and set(l) <= set("|-: \n")),
            None,
        )
        if sep_idx is None or sep_idx == 0:
            return [], all_lines
        return all_lines[sep_idx - 1: sep_idx + 1], all_lines[sep_idx + 1:]


class RowMarkdownSerializerProvider(BaseSerializerProvider):
    """ChunkingDocSerializer with the row-markdown table serializer."""

    def get_serializer(self, doc: DoclingDocument):
        serializer = ChunkingDocSerializer(doc=doc)
        serializer.table_serializer = RowMarkdownTableSerializer()
        return serializer
