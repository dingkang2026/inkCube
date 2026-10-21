"""tests/test_vlm_client.py — VLM 分类常量不变量。

背景：seal（印章）加入分类清单并纳入自动归档（印章/签名/二维码均不适合报告配图）。
守护不变量：AUTO_ARCHIVE_CATEGORIES 必须是 CATEGORIES 的子集，否则归档分支永不触发
（或触发非法类别入库）。
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge.vlm_client import VLMClient


class TestVlmCategories:
    def test_seal_is_a_valid_category(self):
        assert "seal" in VLMClient.CATEGORIES

    def test_seal_is_auto_archived(self):
        assert "seal" in VLMClient.AUTO_ARCHIVE_CATEGORIES

    def test_invoice_and_certificate_are_content_categories(self):
        """票据/资质证书为内容类：进报告插图池，不进自动归档
        （与 qr_code/signature/seal 修饰类相对，商务场景是信任背书插图）。"""
        assert "invoice" in VLMClient.CATEGORIES
        assert "certificate" in VLMClient.CATEGORIES
        assert "invoice" not in VLMClient.AUTO_ARCHIVE_CATEGORIES
        assert "certificate" not in VLMClient.AUTO_ARCHIVE_CATEGORIES

    def test_auto_archive_categories_are_subset_of_categories(self):
        assert set(VLMClient.AUTO_ARCHIVE_CATEGORIES) <= set(VLMClient.CATEGORIES)

    def test_prompt_builds_category_list_dynamically(self):
        """prompt 类别表必须由 CATEGORIES 动态拼接（新增类别自动生效，防硬编码漂移）。"""
        source = inspect.getsource(VLMClient._call_external_api)
        assert '"/".join(self.CATEGORIES)' in source
