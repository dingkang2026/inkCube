"""python/tests/test_pipeline_source_image.py

独立图片文件登记为图片库记录（2.2 §4.4）：
- pipeline._source_image_context：扩展名判定 + 源图构造 + 尺寸容错
- file_cleanup.cleanup_file_data：源文件保护（retry 不得删除源文件本身）
"""
import asyncio
import base64

import pytest

from knowledge.file_cleanup import cleanup_file_data
from knowledge.pipeline import ProcessingPipeline, _IMAGE_FILE_EXTS

# 1x1 PNG（最小合法图片）
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestSourceImageContext:
    def test_png_file_builds_context(self, tmp_path):
        f = tmp_path / "扫描件.PNG"  # 大写扩展名同样命中
        f.write_bytes(_PNG_1PX)
        ctx = ProcessingPipeline._source_image_context(str(f), "file_abc", "扫描件.PNG")
        assert ctx is not None
        assert ctx.image_id == "img_file_abc_0"
        assert ctx.page == 1
        assert ctx.image_path == str(f)
        assert ctx.caption == "扫描件.PNG"
        assert (ctx.width_px, ctx.height_px) == (1, 1)

    def test_non_image_ext_returns_none(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")
        assert ProcessingPipeline._source_image_context(str(f), "file_abc", "doc.pdf") is None

    def test_corrupt_image_still_registers_without_dims(self, tmp_path):
        f = tmp_path / "broken.jpg"
        f.write_bytes(b"not-an-image")
        ctx = ProcessingPipeline._source_image_context(str(f), "file_abc", "broken.jpg")
        assert ctx is not None  # 仍登记，图片库/VLM 链路可见
        assert ctx.width_px is None and ctx.height_px is None

    def test_ext_set_matches_upload_whitelist(self):
        assert _IMAGE_FILE_EXTS == {".png", ".jpg", ".jpeg"}


class TestConverterPictureExtraction:
    """converter 格式级配置（2.2 §4.4）：仅独立图片输入禁用 picture 区域裁剪。

    PDF/Office 转 PDF 的内嵌图提取不受影响（文档理解的图片保留）。
    """

    def test_image_input_disables_picture_extraction(self, tmp_path):
        from knowledge.doc_parser import _get_docling_converter

        conv = _get_docling_converter(tmp_path)
        if conv is None:
            pytest.skip("docling not available")
        from docling.datamodel.base_models import InputFormat

        fmt_opts = conv.format_to_options
        assert fmt_opts[InputFormat.PDF].pipeline_options.generate_picture_images is True
        assert fmt_opts[InputFormat.IMAGE].pipeline_options.generate_picture_images is False


class _StubRow(dict):
    """sqlite3.Row 形态的桩（支持 row["key"] 访问）。"""


class _StubDb:
    """cleanup_file_data 所需的最小 DB 桩：images 路径列表 + files.filename 查询。"""

    def __init__(self, image_paths, filename):
        self._image_paths = image_paths
        self._filename = filename

        class _Conn:
            def __init__(self, outer):
                self._outer = outer

            def execute(self, sql, params=()):
                row = _StubRow(filename=self._outer._filename) if "FROM files" in sql else None
                return _StubCursor(row)

        self.conn = _Conn(self)

    def delete_images_by_file(self, file_id):
        return list(self._image_paths)


class _StubCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _StubStore:
    def delete_by_file_id(self, *args, **kwargs):
        return []


class TestCleanupPreservesSourceImage:
    def test_retry_cleanup_keeps_source_deletes_extracted(self, tmp_path):
        file_id, project_id = "file_src1", "p1"
        file_dir = tmp_path / "files" / project_id / file_id
        file_dir.mkdir(parents=True)
        source = file_dir / "截图.png"
        source.write_bytes(_PNG_1PX)
        extracted = file_dir / "page_3_img_0.png"
        extracted.write_bytes(_PNG_1PX)

        db = _StubDb([str(source), str(extracted)], "截图.png")
        asyncio.run(cleanup_file_data(
            db, _StubStore(), _StubStore(), file_id, project_id, str(tmp_path),
            remove_file_record=False,
        ))

        assert source.exists(), "源文件受保护，retry 依赖其重跑"
        assert not extracted.exists(), "提取产物照常清理"

    def test_cleanup_without_files_row_still_deletes(self, tmp_path):
        """files 记录已删（如全删竞态）→ 无源文件概念，图片文件照常清理。"""
        file_id, project_id = "file_src2", "p1"
        orphan = tmp_path / "files" / project_id / file_id
        orphan.mkdir(parents=True)
        img = orphan / "page_1_img_0.png"
        img.write_bytes(_PNG_1PX)

        db = _StubDb([str(img)], None)
        db._filename = None

        class _NullCursor:
            def fetchone(self):
                return None

        class _NullConn:
            def execute(self, sql, params=()):
                return _NullCursor()

        db.conn = _NullConn()

        asyncio.run(cleanup_file_data(
            db, _StubStore(), _StubStore(), file_id, project_id, str(tmp_path),
            remove_file_record=False,
        ))

        assert not img.exists()
