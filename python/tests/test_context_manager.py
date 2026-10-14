"""Regression tests for the tiktoken encoder init in agent/context_manager.py.

2026-10-13: the encoder used to be built at module import time; on networks
where the OpenAI CDN is unreachable, tiktoken's timeout-less download of the
o200k_base data file blocked module import forever and the whole backend
missed its /health budget. The init now runs in a daemon thread — import must
return immediately, token counting must degrade to the heuristic, and the
process must be able to exit while the download thread is still hanging.
"""

import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import context_manager as cm  # noqa: E402


# ── unit: heuristic fallback when no encoder is available ──

def test_count_tokens_heuristic_cjk(monkeypatch):
    monkeypatch.setattr(cm, "_tiktoken_encoder", None)
    text = "这是一段中文文本用来测试启发式"  # pure CJK
    expected = max(1, int(len(text) * 2 / 3))
    assert cm.ContextManager.count_tokens(text) == expected


def test_count_tokens_heuristic_latin(monkeypatch):
    monkeypatch.setattr(cm, "_tiktoken_encoder", None)
    assert cm.ContextManager.count_tokens("hello") == max(1, len("hello") // 3)


def test_count_tokens_empty(monkeypatch):
    monkeypatch.setattr(cm, "_tiktoken_encoder", None)
    assert cm.ContextManager.count_tokens("") == 0


# ── unit: precise path once the encoder is ready ──

def test_count_tokens_uses_encoder_when_ready(monkeypatch):
    class StubEncoder:
        def encode(self, text):
            return [0] * (len(text) + 1)

    monkeypatch.setattr(cm, "_tiktoken_encoder", StubEncoder())
    assert cm.ContextManager.count_tokens("abcd") == 5


# ── unit: background init failure must be swallowed ──
# A fake tiktoken module is injected so the tests run whether or not the
# real package is installed.

def _fake_tiktoken(encoding_for_model):
    import types
    return types.SimpleNamespace(encoding_for_model=encoding_for_model)


def test_init_encoder_swallows_failure(monkeypatch, tmp_path):
    def _boom(_model):
        raise RuntimeError("boom")

    monkeypatch.setattr(cm, "tiktoken", _fake_tiktoken(_boom))
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(cm, "_tiktoken_encoder", None)
    cm._init_tiktoken_encoder()  # must not raise
    assert cm._tiktoken_encoder is None


def test_init_encoder_creates_cache_dir_and_sets_encoder(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))
    monkeypatch.setattr(cm, "_tiktoken_encoder", None)

    class StubEncoder:
        pass

    monkeypatch.setattr(cm, "tiktoken", _fake_tiktoken(lambda m: StubEncoder()))
    cm._init_tiktoken_encoder()
    assert cache.is_dir()
    assert isinstance(cm._tiktoken_encoder, StubEncoder)


# ── regression: a hung encoder init must not block module import ──

def test_import_not_blocked_by_hung_encoder(tmp_path):
    """Inject a fake tiktoken whose encoding_for_model sleeps 60s (mimicking
    the timeout-less CDN download) and assert that:
    - importing agent.context_manager returns fast,
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
            import agent.context_manager as cm
            dt = time.time() - t0
            assert dt < 10, f"import blocked for {dt:.1f}s by encoder init"
            assert cm._tiktoken_encoder is None, "encoder must not be ready"
            assert cm.ContextManager.count_tokens("hello") == 1  # heuristic path
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
