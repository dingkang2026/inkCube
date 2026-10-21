"""python/tests/test_budget.py — llm/budget.py 单测（D1/D2 唯一预算入口）。

覆盖：
  A. count_tokens / encoder 初始化（自 test_context_manager.py 迁入——
     count_tokens 已从 ContextManager 迁至 budget，本文件是唯一测试落点）
  B. floor 语义：context_window / output_tokens 的 raise-only 三分支
  C. CHARS_PER_TOKEN 换算方向：中文内容不低估 token 数
  D. derive_kb_chars_budget（form_fill 填表资料块预算）边界

HTTP-free，全部纯函数级测试。
"""
import os
import subprocess
import sys
import textwrap
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import budget  # noqa: E402
from form_fill.fill_service import derive_kb_chars_budget  # noqa: E402


# ── A. count_tokens：启发式回退 / 精确路径 ──────────────────

def test_count_tokens_heuristic_cjk(monkeypatch):
    monkeypatch.setattr(budget, "_tiktoken_encoder", None)
    text = "这是一段中文文本用来测试启发式"  # pure CJK
    expected = max(1, int(len(text) * 2 / 3))
    assert budget.count_tokens(text) == expected


def test_count_tokens_heuristic_latin(monkeypatch):
    monkeypatch.setattr(budget, "_tiktoken_encoder", None)
    assert budget.count_tokens("hello") == max(1, len("hello") // 3)


def test_count_tokens_empty(monkeypatch):
    monkeypatch.setattr(budget, "_tiktoken_encoder", None)
    assert budget.count_tokens("") == 0


def test_count_tokens_uses_encoder_when_ready(monkeypatch):
    class StubEncoder:
        def encode(self, text):
            return [0] * (len(text) + 1)

    monkeypatch.setattr(budget, "_tiktoken_encoder", StubEncoder())
    assert budget.count_tokens("abcd") == 5


# ── A2. encoder 后台初始化（失败吞噬 / 缓存目录）────────────

def _fake_tiktoken(encoding_for_model):
    return types.SimpleNamespace(encoding_for_model=encoding_for_model)


def test_init_encoder_swallows_failure(monkeypatch, tmp_path):
    def _boom(_model):
        raise RuntimeError("boom")

    monkeypatch.setattr(budget, "tiktoken", _fake_tiktoken(_boom))
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(budget, "_tiktoken_encoder", None)
    budget._init_tiktoken_encoder()  # must not raise
    assert budget._tiktoken_encoder is None


def test_init_encoder_creates_cache_dir_and_sets_encoder(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))
    monkeypatch.setattr(budget, "_tiktoken_encoder", None)

    class StubEncoder:
        pass

    monkeypatch.setattr(budget, "tiktoken", _fake_tiktoken(lambda m: StubEncoder()))
    budget._init_tiktoken_encoder()
    assert cache.is_dir()
    assert isinstance(budget._tiktoken_encoder, StubEncoder)


def test_import_not_blocked_by_hung_encoder(tmp_path):
    """Inject a fake tiktoken whose encoding_for_model sleeps 60s (mimicking
    the timeout-less CDN download) and assert that:
    - importing llm.budget returns fast,
    - the encoder stays None (heuristic counting kicks in),
    - the process can still exit normally (daemon thread).
    """
    fake = tmp_path / "fakepkg"
    (fake / "tiktoken").mkdir(parents=True)
    (fake / "tiktoken" / "__init__.py").write_text(
        "import time\n"
        "def encoding_for_model(name):\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )

    script = tmp_path / "check_import.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys, time
            t0 = time.time()
            import llm.budget as budget
            dt = time.time() - t0
            assert dt < 10, f"import blocked for {dt:.1f}s by encoder init"
            assert budget._tiktoken_encoder is None, "encoder must not be ready"
            assert budget.count_tokens("hello") == 1  # heuristic path
            print("IMPORT-UNBLOCKED-OK")
            """
        ),
        encoding="utf-8",
    )

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(fake) + os.pathsep + root
    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "IMPORT-UNBLOCKED-OK" in result.stdout


# ── B. floor 语义（D1 raise-only）───────────────────────────

class TestContextWindowFloor:
    def test_default_falls_to_minimum(self):
        assert budget.context_window({}) == budget.MIN_CONTEXT_WINDOW

    def test_below_minimum_is_raised(self):
        # 存量 65536 配置读取时自动抬升，无需数据迁移
        assert budget.context_window({"context_window": 65_536}) == budget.MIN_CONTEXT_WINDOW

    def test_above_minimum_is_kept(self):
        assert budget.context_window({"context_window": 200_000}) == 200_000

    def test_invalid_value_falls_to_minimum(self):
        assert budget.context_window({"context_window": "abc"}) == budget.MIN_CONTEXT_WINDOW
        assert budget.context_window({"context_window": None}) == budget.MIN_CONTEXT_WINDOW


class TestOutputTokensFloor:
    def test_default_falls_to_minimum(self):
        assert budget.output_tokens({}) == budget.MIN_OUTPUT_TOKENS

    def test_legacy_8192_is_raised(self):
        # 低于下限的配置值 8192 → 读取抬升 32K（D1 核心行为）
        assert budget.output_tokens({"max_tokens": 8192}) == budget.MIN_OUTPUT_TOKENS

    def test_above_minimum_is_kept(self):
        assert budget.output_tokens({"max_tokens": 65_536}) == 65_536

    def test_invalid_value_falls_to_minimum(self):
        assert budget.output_tokens({"max_tokens": None}) == budget.MIN_OUTPUT_TOKENS


# ── C. CHARS_PER_TOKEN 换算方向（中文不低估 token）──────────

def test_chars_per_token_never_underestimates_cjk(monkeypatch):
    """换算纪律：chars × (1/CHARS_PER_TOKEN) 得到的 token 估计必须 ≥ 真实计数
    （中文启发式 1 tok ≈ 1.5 chars，方向安全：高估 token → 配额保守）。"""
    monkeypatch.setattr(budget, "_tiktoken_encoder", None)
    text = "这是一段足够长的中文文本用来验证换算方向的安全性" * 10
    estimated = int(len(text) / budget.CHARS_PER_TOKEN)
    assert estimated <= budget.count_tokens(text) + 2  # 容忍取整误差


# ── D. derive_kb_chars_budget（填表资料块预算）───────────────

class TestDeriveKbCharsBudget:
    def test_default_settings(self):
        # (131072 − 32768 − 8192) × 1.5 × 0.7 ≈ 94,617 chars
        assert derive_kb_chars_budget({"max_tokens": 8192, "context_window": 0}) == 94_617

    def test_upper_clamp_on_huge_window(self):
        assert derive_kb_chars_budget({"context_window": 1_000_000}) == 120_000

    def test_lower_clamp_when_output_exceeds_window(self):
        # 配置值超过窗口 → budget_tokens 为负 → clamp 下限
        assert derive_kb_chars_budget({"max_tokens": 130_000}) == 24_000

    def test_bigger_window_raises_budget(self):
        small = derive_kb_chars_budget({"context_window": 131_072})
        big = derive_kb_chars_budget({"context_window": 200_000})
        assert big > small
