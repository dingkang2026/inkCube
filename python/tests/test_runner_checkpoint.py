"""python/tests/test_runner_checkpoint.py — checkpoint 原子写与恢复单测。

修复回归（review M3）：
  - _save_checkpoint 原子写（tmp + os.replace）：无 .tmp 残留；替换失败时
    旧 checkpoint 完好。旧实现 write_text 直接覆盖，写入中途被杀留半写
    JSON → resume 解析失败静默全量重跑，已消耗 token 全废且无提示。
  - _load_checkpoint 三态：正常恢复字段一致 / 缺失返回 None / 损坏返回
    用户可见文案且不污染运行态。
"""
import os
import sys
from unittest.mock import MagicMock, patch

# Ensure python/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from report_generator.runner import ReportRunner  # noqa: E402


def _make_runner(tmp_path):
    return ReportRunner(
        template={"report_depth": "full"}, variables={}, project_id="p1",
        search_tool=MagicMock(), llm_client=MagicMock(), llm_settings={},
        output_dir=str(tmp_path),
    )


class TestCheckpointRoundtrip:
    def test_save_load_roundtrip_no_tmp_residue(self, tmp_path):
        """保存后可完整恢复；原子写不残留 .tmp 临时文件。"""
        runner = _make_runner(tmp_path)
        runner.outputs = {"s1": "内容1"}
        runner.digests = {"s1": "摘要1"}
        runner.order = ["s1"]
        runner.resolved_variables = {"focus": "技术壁垒"}
        runner.resolution_evidence = {}

        runner._save_checkpoint()

        ckpt = tmp_path / "checkpoint.json"
        assert ckpt.exists()
        # 原子写：临时文件已换名落位，无 .tmp 残留
        assert not (tmp_path / "checkpoint.json.tmp").exists()
        assert list(tmp_path.glob("*.tmp")) == []

        restored = _make_runner(tmp_path)
        assert restored._load_checkpoint() is None
        assert restored.outputs == {"s1": "内容1"}
        assert restored.digests == {"s1": "摘要1"}
        assert restored.order == ["s1"]
        assert restored.resolved_variables == {"focus": "技术壁垒"}

    def test_load_missing_returns_none(self, tmp_path):
        """无 checkpoint（首次生成）→ None，运行态不受影响。"""
        runner = _make_runner(tmp_path)
        assert runner._load_checkpoint() is None
        assert runner.outputs == {}

    def test_load_corrupted_returns_user_visible_message(self, tmp_path):
        """损坏 checkpoint → 返回用户可见文案（不再静默全量重跑）。"""
        (tmp_path / "checkpoint.json").write_text(
            '{"outputs": {"s1": "内容1"', encoding="utf-8")  # 半截 JSON
        runner = _make_runner(tmp_path)
        msg = runner._load_checkpoint()
        assert msg is not None
        assert "断点文件损坏" in msg
        # 运行态不被半写数据污染
        assert runner.outputs == {}


class TestCheckpointAtomicity:
    def test_failed_replace_preserves_old_checkpoint(self, tmp_path):
        """关键不变量：替换失败时旧 checkpoint 完好（原子写语义）。

        write_text 已写 tmp 后 os.replace 失败（磁盘满/文件锁）→ 旧断点
        不被破坏，resume 仍能从上一致恢复点续跑。
        """
        runner = _make_runner(tmp_path)
        runner.outputs = {"s1": "v1"}
        runner.order = ["s1"]
        runner._save_checkpoint()

        runner.outputs["s2"] = "v2"
        with patch("os.replace", side_effect=OSError("disk full")):
            try:
                runner._save_checkpoint()
                failed = False
            except OSError:
                failed = True
        assert failed  # 替换失败向上传播，不静默吞掉

        restored = _make_runner(tmp_path)
        assert restored._load_checkpoint() is None
        assert restored.outputs == {"s1": "v1"}  # 旧断点完好
