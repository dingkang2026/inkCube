"""Unit tests for RowMarkdownTableSerializer (doc_parser, 2.2 §4.3).

Covers the heuristics that replace docling_core's TripletTableSerializer:
sparse title/unit rows become preamble, the dense row becomes the column
header, Excel serial dates convert to ISO dates, and rows serialize as
Markdown lines that the chunker can split on (get_header_and_body_lines).
"""

import pytest
from docling_core.types.doc import DocItemLabel, TableData, TableCell, TableItem

from knowledge.doc_parser import RowMarkdownTableSerializer


def make_cell(r, c, text, row_span=1, col_span=1):
    return TableCell(
        text=text,
        start_row_offset_idx=r,
        end_row_offset_idx=r + row_span,
        start_col_offset_idx=c,
        end_col_offset_idx=c + col_span,
        row_span=row_span,
        col_span=col_span,
    )


def make_table(cells, num_rows, num_cols):
    """Build a TableItem the same way backends do: TableData expands spans
    into the grid and shares the cell object across covered positions."""
    data = TableData(table_cells=cells, num_rows=num_rows, num_cols=num_cols)
    return TableItem(
        self_ref="#/tables/0", label=DocItemLabel.TABLE, data=data,
    )


def serialize(item):
    class _StubDocSerializer:
        def serialize_captions(self, **kwargs):
            from docling_core.transforms.serializer.common import SerializationResult
            return SerializationResult(text="")

    return RowMarkdownTableSerializer().serialize(
        item=item, doc_serializer=_StubDocSerializer(), doc=None,
    ).text


# ---------------------------------------------------------------------------
# 表头检测 + preamble
# ---------------------------------------------------------------------------

def test_sparse_title_and_unit_rows_become_preamble():
    """Mimics the real xlsx layout: sparse merged title row, sparse unit row,
    dense year header row. Title/unit rows must land in the preamble, the
    year row must be the header."""
    cells = [
        make_cell(0, 0, "合并资产负债表", col_span=4),
        make_cell(1, 0, "编制单位：X 公司"),
        make_cell(1, 1, ""),
        make_cell(1, 2, ""),
        make_cell(1, 3, "金额单位：人民币元"),
        make_cell(2, 0, "项目"),
        make_cell(2, 1, "45657"),
        make_cell(2, 2, "46022"),
        make_cell(2, 3, "46387"),
        make_cell(3, 0, "货币资金"),
        make_cell(3, 1, "80416775.5"),
        make_cell(3, 2, "68805094.35"),
        make_cell(3, 3, "310423655.4"),
    ]
    text = serialize(make_table(cells, 4, 4))
    lines = text.splitlines()

    assert lines[0] == "合并资产负债表"
    assert lines[1] == "编制单位：X 公司，金额单位：人民币元"
    assert lines[2].startswith("| 项目 | 2024-12-31 | 2025-12-31 | 2026-12-31 |")
    assert lines[3] == "| - | - | - | - |"
    assert lines[4] == "| 货币资金 | 80416775.5 | 68805094.35 | 310423655.4 |"


def test_dense_first_row_is_header_without_preamble():
    """A table whose first row is already dense has no preamble."""
    cells = [
        make_cell(0, 0, "项目"),
        make_cell(0, 1, "金额"),
        make_cell(1, 0, "货币资金"),
        make_cell(1, 1, "100.0"),
    ]
    lines = serialize(make_table(cells, 2, 2)).splitlines()
    assert lines[0] == "| 项目 | 金额 |"
    assert len(lines) == 3


def test_no_dense_row_within_scan_window_degrades_to_plain_rows():
    """All rows sparse (no detectable header) → every row emitted as plain
    output, no separator line, no preamble, no rows lost."""
    cells = [
        make_cell(0, 0, "备注一"),
        make_cell(1, 3, "备注二"),
    ]
    text = serialize(make_table(cells, 2, 4))
    lines = text.splitlines()
    assert lines[0].startswith("| 备注一")
    assert "备注二" in lines[1]
    # no fabricated header/separator
    assert not any(set(l) <= set("|-: \n") for l in lines)


# ---------------------------------------------------------------------------
# Excel 日期序列号
# ---------------------------------------------------------------------------

def test_header_serial_dates_converted():
    cells = [
        make_cell(0, 0, "项目"),
        make_cell(0, 1, "45657"),   # 2024-12-31
        make_cell(0, 2, "46022"),   # 2025-12-31
        make_cell(0, 3, "47848"),   # 2030-12-31
        make_cell(1, 0, "货币资金"),
        make_cell(1, 1, "1"),
        make_cell(1, 2, "2"),
        make_cell(1, 3, "3"),
    ]
    lines = serialize(make_table(cells, 2, 4)).splitlines()
    assert "2024-12-31" in lines[0]
    assert "2025-12-31" in lines[0]
    assert "2030-12-31" in lines[0]
    assert "45657" not in lines[0]
    # data-row numbers stay untouched
    assert lines[2] == "| 货币资金 | 1 | 2 | 3 |"


def test_header_few_numbers_not_converted():
    """One/two numeric header cells are plain values (e.g. 项目 | 金额), not
    dates — the >= 3 numeric-cells guard keeps them as-is."""
    cells = [
        make_cell(0, 0, "项目"),
        make_cell(0, 1, "45657"),
        make_cell(1, 0, "货币资金"),
        make_cell(1, 1, "80416775.5"),
    ]
    lines = serialize(make_table(cells, 2, 2)).splitlines()
    assert "45657" in lines[0]
    assert "2024" not in lines[0]


# ---------------------------------------------------------------------------
# 行输出
# ---------------------------------------------------------------------------

def test_empty_cells_blank_and_full_rows_kept():
    cells = [
        make_cell(0, 0, "项目"),
        make_cell(0, 1, "金额"),
        make_cell(0, 2, "备注"),
        make_cell(1, 0, "货币资金"),
        # (1,1)/(1,2) absent → None cells
        make_cell(2, 0, "应收账款"),
        make_cell(2, 1, "5.0"),
    ]
    lines = serialize(make_table(cells, 3, 3)).splitlines()
    assert lines[2] == "| 货币资金 |   |   |"
    assert lines[3] == "| 应收账款 | 5.0 |   |"


def test_pipe_and_newline_in_text_escaped():
    cells = [
        make_cell(0, 0, "项目"),
        make_cell(0, 1, "说明"),
        make_cell(1, 0, "a|b"),
        make_cell(1, 1, "line1\nline2"),
    ]
    lines = serialize(make_table(cells, 2, 2)).splitlines()
    assert lines[2] == "| a&#124;b | line1 line2 |"


def test_vertical_span_repeat_blank():
    """row_span=2 cell shares its object on the covered row → second row
    keeps a blank instead of repeating the label."""
    cells = [
        make_cell(0, 0, "项目"),
        make_cell(0, 1, "金额"),
        make_cell(1, 0, "流动资产", row_span=2),
        make_cell(1, 1, "10"),
        make_cell(2, 1, "20"),
    ]
    lines = serialize(make_table(cells, 3, 2)).splitlines()
    assert lines[2] == "| 流动资产 | 10 |"
    assert lines[3] == "|   | 20 |"


# ---------------------------------------------------------------------------
# 分块协作与边界
# ---------------------------------------------------------------------------

def test_get_header_and_body_lines_split():
    table_text = (
        "标题\n"
        "| 项目 | 金额 |\n"
        "| - | - |\n"
        "| 货币资金 | 1 |\n"
        "| 存货 | 2 |"
    )
    header, body = RowMarkdownTableSerializer().get_header_and_body_lines(
        table_text=table_text,
    )
    assert header == ["| 项目 | 金额 |\n", "| - | - |\n"]
    assert body == ["| 货币资金 | 1 |\n", "| 存货 | 2 |"]


def test_get_header_and_body_lines_no_separator():
    table_text = "| 只有一行 |\n"
    header, body = RowMarkdownTableSerializer().get_header_and_body_lines(
        table_text=table_text,
    )
    assert header == []
    assert body == ["| 只有一行 |\n"]


def test_empty_grid_returns_empty_text():
    item = make_table([], 0, 0)
    assert serialize(item) == ""
