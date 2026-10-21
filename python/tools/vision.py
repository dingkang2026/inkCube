"""python/tools/vision.py — 图片理解工具（chat/任务通用）。

输入图片或含图文档，内部图片逐张交给标准/高配 LLM（多模态，
设置 > 外部模型）理解并返回自然语言描述/答案：
- 图片（png/jpg/jpeg/gif/bmp/webp）：直接多模态问答；
- PDF：PyMuPDF 提取内嵌位图（md5 去重 + 小图/格式/超大过滤）→ 逐张问答；
- DOCX/PPTX：复用 knowledge/office_media 无损提取 → 逐张问答。

设计约束：
- 不落业务库：提取图写入临时目录，execute 结束即清理（生命周期闭环）；
- 截断显式声明：文档图数超过 max_images 时在返回中给出 total/truncated
  （对齐 0.1dev-guide §16 列表翻页核查，禁止静默 limit）；
- 单张失败不中断：某张调用失败记入该张 error 项，其余照常；
- LLM 未配置：前置拦截返回结构化错误（code=LLM_NOT_CONFIGURED），
  引导文案指向设置页；全局提示面由 SystemAlerts 红条承担，本工具不重复建 UI。
"""
import asyncio
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from tools.base import BaseTool

logger = logging.getLogger(__name__)

# VLM 可消费的位图格式（与 knowledge/office_media 白名单对齐，剔除 tif——
# 部分 OpenAI 兼容端点不接受 tiff，宁可不收）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
# 支持内部提图的文档格式
_DOC_EXTS = {".pdf", ".docx", ".pptx"}
# 小图过滤阈值：宽或高低于该值视为装饰图标，跳过（返回中显式计数）
_MIN_DIM_PX = 64
# 单张图字节上限：单图超限报错、文档内嵌图跳过（端点普遍限制 ~10MB 请求体）
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
# 默认单次分析的图片数上限
_DEFAULT_MAX_IMAGES = 10
# max_images 硬上限：钳制无界传参（最坏 50×60s 超时串行仍可控）
_MAX_IMAGES_CAP = 50


class VisionTool(BaseTool):
    name = "vision"
    description = (
        "图片理解工具。用多模态大模型解析图片内容："
        "支持图片文件（png/jpg/jpeg/gif/bmp/webp），也支持 PDF/DOCX/PPTX "
        "（自动提取内部图片后逐张解析）。可传 question 针对图片提问。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "图片或文档的路径（相对工作区；data/fetched 内文件可用绝对路径）",
            },
            "question": {
                "type": "string",
                "description": "针对图片的具体问题（如\"图里表格的数据是什么\"）。省略则默认详细描述图片内容",
            },
            "max_images": {
                "type": "integer",
                "description": f"文档提图分析上限（默认 {_DEFAULT_MAX_IMAGES}）。图数超出时返回 total 与截断标记",
            },
        },
        "required": ["file_path"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## vision\n"
            "图片理解：用多模态大模型解析图片或文档内的图片。\n\n"
            "支持输入:\n"
            "- 图片文件: png / jpg / jpeg / gif / bmp / webp\n"
            "- 文档: pdf / docx / pptx（自动提取内部图片逐张解析，"
            "默认上限 10 张，可用 max_images 调整）\n\n"
            "用法:\n"
            "- file_path: 相对工作区的路径\n"
            "- question 可选: 针对图片的具体问题（如\"总结图中图表的趋势\"）；"
            "省略则返回每张图的详细描述\n"
            "- 注意: 本工具只理解图片，不提取文档文字；PDF 文字请用 file_ops 或其他方式\n\n"
            "示例:\n"
            '```\n{"file_path": "report.pdf", "question": "图中的数据趋势是什么？"}\n'
            '→ {"ok": true, "total_images": 3, "analyzed": 3, "images": [{"page": 1, "description": "..."}, ...]}\n```\n\n'
            '```\n{"file_path": "photo.png"}\n'
            '→ {"ok": true, "file": "photo.png", "description": "..."}\n```'
        )

    def __init__(self, db=None):
        self.workspace_dir = ""
        self.db = db  # Database 实例（读 settings 构造 LLMClient，standard 档）
        # 工作区外的只读根（如 data/fetched），语义对齐 file_ops
        self._readonly_roots: list[str] = []

    def set_workspace(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def add_readonly_root(self, path: str):
        if path and path not in self._readonly_roots:
            self._readonly_roots.append(path)

    def _resolve_path(self, rel_path: str) -> Path | None:
        """在 workspace 或只读根内解析路径，防目录穿越（语义对齐 file_ops）。"""
        candidates: list[tuple[Path, Path]] = []
        if self.workspace_dir:
            workspace = Path(self.workspace_dir).resolve()
            candidates.append((workspace, (workspace / rel_path).resolve()))
        for root in self._readonly_roots:
            base = Path(root).resolve()
            # 绝对路径按 pathlib 语义替换 base——正是只读根绝对路径回读所需
            candidates.append((base, (base / rel_path).resolve()))
        for base, target in candidates:
            if target != base and not str(target).startswith(str(base) + os.sep):
                continue
            return target
        return None

    def _error(self, e: Exception) -> str:
        """结构化错误返回（InkCubeError 带 code/suggestion，其余透传）。"""
        from errors import InkCubeError

        if isinstance(e, InkCubeError):
            payload = {"error": e.message, "code": e.code}
            if e.suggestion:
                payload["suggestion"] = e.suggestion
        else:
            payload = {"error": str(e)}
        return json.dumps(payload, ensure_ascii=False)

    async def execute(
        self, file_path: str, question: str = "", max_images: int = _DEFAULT_MAX_IMAGES
    ) -> str:
        if not self.workspace_dir and not self._readonly_roots:
            return json.dumps({"error": "工作目录未设置"}, ensure_ascii=False)
        if not file_path or not file_path.strip():
            return json.dumps({"error": "file_path 不能为空"}, ensure_ascii=False)

        target = self._resolve_path(file_path.strip())
        if target is None:
            return json.dumps({
                "error": f"路径超出可访问范围: {file_path}",
                "suggestion": "路径必须位于工作区内（或 data/fetched 只读根内）",
            }, ensure_ascii=False)
        if not target.exists():
            return json.dumps({"error": f"文件不存在: {file_path}"}, ensure_ascii=False)

        from errors import LLMNotConfigured
        from llm import LLMClient
        from llm.routing import resolve_model

        # 前置拦截 LLM 配置缺失：提图成本高（临时目录写盘），先验配置再提图。
        # 图片理解统一走 LLM（standard 档，D3 派生 dict → 构造 client）
        settings = dict(self.db.get_settings())
        settings["model"] = resolve_model(settings, "standard")
        if (not settings.get("api_url") or not settings.get("api_key")
                or not settings.get("model")):
            return self._error(LLMNotConfigured())
        client = LLMClient(settings)

        ext = target.suffix.lower()
        try:
            if ext in _IMAGE_EXTS:
                size = target.stat().st_size
                if size > _MAX_IMAGE_BYTES:
                    return json.dumps({
                        "error": (
                            f"图片过大: {size / (1024 * 1024):.1f}MB"
                            f"（单张上限 {_MAX_IMAGE_BYTES // (1024 * 1024)}MB）"
                        ),
                        "suggestion": "请压缩图片或转换格式后重试",
                    }, ensure_ascii=False)
                return await self._analyze_single(client, target, question)
            if ext in _DOC_EXTS:
                cap = max_images if isinstance(max_images, int) and max_images > 0 else _DEFAULT_MAX_IMAGES
                cap = min(cap, _MAX_IMAGES_CAP)
                return await self._analyze_document(client, target, question, cap)
            return json.dumps({
                "error": f"不支持的文件类型: {ext or '(无扩展名)'}",
                "supported": sorted(_IMAGE_EXTS | _DOC_EXTS),
            }, ensure_ascii=False)
        except Exception as e:
            logger.error("vision tool failed for %s: %s", target.name, e, exc_info=True)
            return self._error(e)

    async def _analyze_single(self, client, image_path: Path, question: str) -> str:
        """单图输入：直接 LLM 多模态问答，返回精简 JSON。"""
        from llm import vision

        desc = await vision.analyze_image(client, str(image_path), question or None)
        return json.dumps({
            "ok": True,
            "file": image_path.name,
            "description": desc,
        }, ensure_ascii=False)

    async def _analyze_document(
        self, client, doc_path: Path, question: str, max_images: int
    ) -> str:
        """文档输入：提图（临时目录）→ 过滤（显式计数）→ 逐张 VLM → 汇总。"""
        # 跳过统计：显式计数返回（small=装饰小图 / unsupported=VLM 不支持的
        # 格式 / oversized=单张超限），禁止静默丢弃
        stats = {"small": 0, "unsupported": 0, "oversized": 0}
        with tempfile.TemporaryDirectory(prefix="inkcube_vision_") as tmp:
            out_dir = Path(tmp)
            if doc_path.suffix.lower() == ".pdf":
                candidates = await asyncio.to_thread(
                    self._extract_pdf_images, doc_path, out_dir, stats
                )
            else:
                from knowledge.office_media import extract_office_images

                contexts = await asyncio.to_thread(
                    extract_office_images, doc_path, "vision", out_dir
                )
                # 过滤对齐 PDF 路：小图/不支持格式/超大图（office_media 白名单
                # 含 tif/tiff，需在此剔除；尺寸经 PIL 测量必有值）
                candidates = []
                for c in contexts:
                    width = c.width_px or 0
                    height = c.height_px or 0
                    img_path = Path(c.image_path)
                    if 0 < width < _MIN_DIM_PX or 0 < height < _MIN_DIM_PX:
                        stats["small"] += 1
                        continue
                    if img_path.suffix.lower() not in _IMAGE_EXTS:
                        stats["unsupported"] += 1
                        continue
                    if img_path.stat().st_size > _MAX_IMAGE_BYTES:
                        stats["oversized"] += 1
                        continue
                    candidates.append({
                        "path": img_path,
                        "page": c.page,
                        "width": width,
                        "height": height,
                    })

            total = len(candidates)
            analyzed_list = candidates[:max_images]
            truncated = total > len(analyzed_list)
            logger.info(
                "vision: %s — %d image(s) extracted, analyzing %d (max_images=%d)",
                doc_path.name, total, len(analyzed_list), max_images,
            )

            images = []
            for i, item in enumerate(analyzed_list):
                entry: dict = {"index": i}
                if item["page"] is not None:
                    entry["page"] = item["page"]
                try:
                    from llm import vision

                    desc = await vision.analyze_image(client, str(item["path"]), question or None)
                    entry["description"] = desc
                except Exception as e:
                    # 单张失败不中断整体（对齐 office_media 容错哲学）
                    logger.warning("vision: image %d of %s failed: %s", i, doc_path.name, e)
                    entry["error"] = self._error(e)
                images.append(entry)

        result = {
            "ok": True,
            "file": doc_path.name,
            "total_images": total,
            "analyzed": sum(1 for e in images if "description" in e),
            "images": images,
        }
        if truncated:
            result["truncated"] = True
            result["hint"] = (
                f"共 {total} 张图片，本次仅分析前 {len(analyzed_list)} 张；"
                f"需要更多请调大 max_images 重新调用"
            )
        for key, count in stats.items():
            if count:
                result[f"skipped_{key}"] = count
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _extract_pdf_images(pdf_path: Path, out_dir: Path, stats: dict) -> list[dict]:
        """fitz 提取 PDF 内嵌位图：页序遍历、md5 去重、小图/格式/超大过滤。

        跳过数量累加进 stats（small/unsupported/oversized），由调用方显式返回。
        """
        import fitz  # PyMuPDF

        candidates: list[dict] = []
        seen_md5: set[str] = set()
        doc = fitz.open(str(pdf_path))
        try:
            for page_no, page in enumerate(doc, start=1):
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    try:
                        info = doc.extract_image(xref)
                    except Exception as e:
                        logger.info("vision: skip unextractable xref %d in %s: %s", xref, pdf_path.name, e)
                        continue
                    blob = info.get("image") or b""
                    width = int(info.get("width") or 0)
                    height = int(info.get("height") or 0)
                    if not blob:
                        continue
                    digest = hashlib.md5(blob).hexdigest()
                    if digest in seen_md5:
                        continue
                    seen_md5.add(digest)
                    # jpx/jbig2/ccitt 等 VLM 不支持的编码：跳过并显式计数
                    ext = f".{(info.get('ext') or 'png').lower()}"
                    if ext not in _IMAGE_EXTS:
                        stats["unsupported"] += 1
                        continue
                    # 装饰小图标（页眉 logo、分隔线等）跳过，计数显式返回
                    if width < _MIN_DIM_PX or height < _MIN_DIM_PX:
                        stats["small"] += 1
                        continue
                    # 单张超限：上传必败（端点限制 ~10MB 请求体），跳过省预算
                    if len(blob) > _MAX_IMAGE_BYTES:
                        stats["oversized"] += 1
                        continue
                    img_path = out_dir / f"page_{page_no}_img_{len(candidates)}{ext}"
                    img_path.write_bytes(blob)
                    candidates.append({
                        "path": img_path,
                        "page": page_no,
                        "width": width,
                        "height": height,
                    })
        finally:
            doc.close()
        return candidates
