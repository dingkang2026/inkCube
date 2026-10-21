"""python/knowledge/office_media.py — DOCX/PPTX 原生图片无损提取器(2.2 §4.4)。

图片损伤根因:Office→PDF 导出重采样(Word/PPT 默认 ~220ppi 有损压缩)后,
Docling 再按 bbox 从页面位图裁剪,两次有损。本模块直接从 DOCX/PPTX(zip 容器)
的 media 部件取原始字节,零损伤;文字/版面仍走 PDF 路(doc_parser)。

约定(方案评审已确认):
- 同一图片字节(md5)在文件内多处引用只登记首次出现(去重);
- 提取器内单张图片异常不中断整体(跳过并记日志),容器级失败由调用方转 warning;
- PIL 无法识别的格式(WMF/EMF 元文件、损坏图)跳过——无法作为多模态输入,登记只会
  产生 error 记录,跳过数量记入日志。
"""
import hashlib
import io
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 上下文窗口:与 PDF 路一致,图前/图后各取 2 个非空文本块
_CONTEXT_BLOCKS = 2

# 多模态 LLM 可消费的位图格式白名单:WMF/EMF 元文件等矢量格式即使 PIL 在部分平台
# 能读出尺寸,也无法作为多模态输入,直接跳过(不写盘不登记)
_SUPPORTED_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff"}


def extract_office_images(file_path: Path, file_id: str, output_dir: Path):
    """按扩展名分发到对应提取器;返回 ImageContext 列表,容器级异常向上抛。"""
    from knowledge.doc_parser import ImageContext  # 延迟导入避免循环依赖

    suffix = file_path.suffix.lower()
    if suffix == ".pptx":
        raw = _iter_pptx_pictures(file_path)
    elif suffix == ".docx":
        raw = _iter_docx_pictures(file_path)
    else:
        raise ValueError(f"Unsupported office format for native image extraction: {suffix}")

    output_dir.mkdir(parents=True, exist_ok=True)
    images: list[ImageContext] = []
    seen_md5: set[str] = set()
    skipped = 0
    for blob, ext, page, caption, before_text, after_text in raw:
        digest = hashlib.md5(blob).hexdigest()
        # 按内容去重:同一 media 部件多处引用(页眉 logo 等)只登记首次
        if digest in seen_md5:
            continue
        seen_md5.add(digest)

        img_path, width_px, height_px = _save_and_measure(
            blob, ext, page, len(images), file_id, output_dir
        )
        if img_path is None:
            skipped += 1
            continue
        images.append(ImageContext(
            image_id=f"img_{file_id}_{len(images)}",
            file_id=file_id,
            page=page,
            image_path=str(img_path),
            caption=caption,
            before_text=before_text,
            after_text=after_text,
            width_px=width_px,
            height_px=height_px,
        ))
    if skipped:
        logger.info(
            "office_media: %s — skipped %d unusable image(s) (unreadable/wmf-emf)",
            file_path.name, skipped,
        )
    # 对账：清理上一轮中断遗留的孤儿（已写盘但未登记，如首提中断后 retry
    # 提取数收缩）——仅成功路径执行，失败路径保守保留待下次成功对账
    registered = {Path(i.image_path).name for i in images}
    for orphan in output_dir.glob("page_*_img_*"):
        if orphan.name not in registered:
            logger.info("office_media: removing orphan image %s", orphan.name)
            orphan.unlink(missing_ok=True)
    logger.info(
        "office_media: %s — extracted %d unique image(s) losslessly",
        file_path.name, len(images),
    )
    return images


def _save_and_measure(blob: bytes, ext: str, page: Optional[int], idx: int,
                      file_id: str, output_dir: Path):
    """写盘(保留原始格式扩展名)+ PIL 测宽高;不可登记格式返回 (None, None, None)。"""
    clean_ext = (ext or "png").lower().lstrip(".")
    if clean_ext not in _SUPPORTED_IMAGE_EXTS:
        logger.info(
            "office_media: skip non-bitmap image (%s), not consumable as multimodal input", clean_ext,
        )
        return None, None, None
    img_path = output_dir / f"page_{page or 0}_img_{idx}.{clean_ext}"
    img_path.write_bytes(blob)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(blob)) as im:
            width_px, height_px = im.size
        return img_path, width_px, height_px
    except Exception as e:
        # PIL 不识别(WMF/EMF 元文件、损坏字节):移除刚写盘的文件,跳过登记
        logger.info("office_media: skip unreadable image %s (%s)", img_path.name, e)
        img_path.unlink(missing_ok=True)
        return None, None, None


# ---------------------------------------------------------------------------
# PPTX:slide 序号即页码;同 slide 的其余 shape 文本作为图片上下文
# ---------------------------------------------------------------------------

def _iter_pptx_pictures(file_path: Path):
    """产出 (blob, ext, page, caption, before_text, after_text) 六元组。

    页码 = slide 序号(1 起,与转 PDF 后 slide↔page 一一对应一致);
    caption 无来源(PPT 图形无题注概念)置 None;
    上下文 = 同 slide 全部文本(shape 序)放 after_text——slide 内 shape
    顺序不等于视觉阅读顺序,无「图前/图后」语义,整体作为页面文字上下文。
    """
    from pptx import Presentation
    from pptx.shapes.picture import Picture

    prs = Presentation(str(file_path))
    for page, slide in enumerate(prs.slides, start=1):
        shapes = list(slide.shapes)
        slide_texts = _collect_texts(shapes)
        for shape in _iter_shapes_recursive(shapes):
            # isinstance 而非 shape_type：PlaceholderPicture（内容/图片占位符
            # 插入的图，模板化 PPT 极常见）的 shape_type 返回 PLACEHOLDER 而非
            # PICTURE，按类型判断会整类静默丢失；它继承自 Picture，isinstance
            # 同时覆盖普通图与占位符图
            if not isinstance(shape, Picture):
                continue
            try:
                image = shape.image
            except Exception as e:
                # linked picture(外链)等无内嵌字节的形态
                logger.info("office_media: pptx slide %d picture without blob: %s", page, e)
                continue
            # 同 slide 非图 shape 文本(已滤空)整体作为页面文字上下文
            yield image.blob, image.ext, page, None, "", "\n".join(slide_texts)


def _iter_shapes_recursive(shapes):
    """深度优先展开 group shape(python-pptx 的 group.shapes 可再嵌套)。"""
    from pptx.enum.shapes import MSO_SHAPE_TYPE  # 与调用方同源的延迟导入
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes_recursive(shape.shapes)


def _collect_texts(shapes) -> list[str]:
    """按 shape 序收集全部非空文本(递归 group,表格文本一并收取)。"""
    texts: list[str] = []
    for shape in _iter_shapes_recursive(shapes):
        if getattr(shape, "has_text_frame", False):
            t = shape.text_frame.text.strip()
            if t:
                texts.append(t)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    t = cell.text.strip()
                    if t:
                        texts.append(t)
    return texts


# ---------------------------------------------------------------------------
# DOCX:按 body 文档序遍历段落(含表格内),w:drawing 的 blip rId → media 部件
# ---------------------------------------------------------------------------

def _iter_docx_pictures(file_path: Path):
    """产出 (blob, ext, page, caption, before_text, after_text) 六元组。

    页码 = None(DOCX 流式文档无固定分页,前端对 null 页码显示 '?');
    上下文 = 文档序上前/后各 2 个非空段落(含表格单元格段落);
    inline 与 anchored(浮动)drawing 统一经 //w:drawing//a:blip 提取。
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(file_path))
    part = doc.part
    # 深度优先文档序:iter() 给出含表格单元格在内的全部 w:p
    paragraphs = list(doc.element.body.iter(qn("w:p")))
    texts = [_paragraph_text(p) for p in paragraphs]

    for i, p in enumerate(paragraphs):
        for blip in p.findall(".//" + qn("a:blip")):
            rId = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
            if not rId:
                continue
            try:
                image_part = part.related_parts[rId]
            except KeyError:
                logger.info("office_media: docx relationship %s not found, skipping", rId)
                continue
            before = "\n".join(t for t in texts[max(0, i - _CONTEXT_BLOCKS):i] if t)
            after = "\n".join(t for t in texts[i + 1:i + 1 + _CONTEXT_BLOCKS] if t)
            yield image_part.blob, Path(str(image_part.partname)).suffix, None, None, before, after


def _paragraph_text(p) -> str:
    """提取 w:p 的可见文本(w:t 拼接;含 tab/换行占位符的展开)。"""
    from docx.oxml.ns import qn
    return "".join(node.text or "" for node in p.iter(qn("w:t"))).strip()
