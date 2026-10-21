"""python/tools/doc_template.py — pandoc reference-doc 样式模板解析。

职责：
  - 解析随包分发的 pandoc --reference-doc 样式模板路径
    （resources/templates/custom-reference_{portrait|landscape}.docx），
    供报告导出（report_generator.renderer）与任务 doc_generator 工具
    两条 pandoc md→docx 链路共用——全应用 Word 导出样式统一单一来源。

模板目录经 paths.get_templates_dir 解析（打包兼容同一链路）；文件意外
缺失时返回 None（调用方不带该参数以 pandoc 默认样式导出并记 WARNING——
仅兜底，正常路径模板必然随包存在）。

设计参考：docs/2.5report-design.md §3.7 渲染规则
"""
import logging
from pathlib import Path

from paths import get_templates_dir

logger = logging.getLogger(__name__)

# 合法页面方向（报告导出面板 radio，§1.3）；非法值一律回退 portrait。
# 任务 doc_generator 无方向概念，固定 portrait。
_ORIENTATIONS = ("portrait", "landscape")


def resolve_reference_doc(orientation: str = "portrait") -> str | None:
    """解析导出方向对应的 pandoc reference-doc 样式模板路径（§3.7）。

    非法方向回退 portrait；模板文件意外缺失时返回 None（调用方不带
    --reference-doc 以 pandoc 默认样式导出——仅兜底，正常路径模板
    必然随包存在）。
    """
    if orientation not in _ORIENTATIONS:
        orientation = "portrait"
    path = Path(get_templates_dir()) / f"custom-reference_{orientation}.docx"
    if not path.exists():
        logger.warning(
            "Reference-doc template missing (orientation=%s): %s — "
            "falling back to pandoc default styles",
            orientation, path,
        )
        return None
    return str(path)
