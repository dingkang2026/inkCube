"""内置 skill 种子安装测试（2.8 §4.2 / §11）。"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from db.database import Database

_seed_spec = importlib.util.spec_from_file_location(
    "skills_seed",
    str(Path(__file__).resolve().parents[1] / "skills" / "seed.py"),
)
seed = importlib.util.module_from_spec(_seed_spec)
sys.modules.setdefault("skills_seed", seed)
_seed_spec.loader.exec_module(seed)

PROJECT_SKILL_SRC = Path(__file__).resolve().parents[2] / "resources" / "skills" / "browser-fetch"


@pytest.fixture()
def db():
    database = Database(db_path=":memory:")
    database.initialize()
    return database


def _make_fake_source(tmp_path: Path) -> Path:
    """构造最小 skill 源目录（不依赖仓库内 resources 的存在）。"""
    src = tmp_path / "src" / "browser-fetch"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: browser-fetch\ndescription: test\n---\n# t\n", encoding="utf-8")
    (src / "scripts" / "main.py").write_text("print('x')\n", encoding="utf-8")
    return src


def _skill_rows(db: Database) -> list[dict]:
    return [s for s in db.list_skills() if s["name"] == "browser-fetch"]


def test_seed_installs_skill_and_confirms(db, tmp_path, monkeypatch):
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = str(tmp_path / "data")

    seed.seed_builtin_skills(db, data_dir)

    rows = _skill_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["source_type"] == "builtin"
    assert row["install_path"] == os.path.join(data_dir, "skills", "browser-fetch")
    # needs_confirm 迁移列 DEFAULT 1 → 种子必须显式确认置 0（免首用弹窗）
    assert row["needs_confirm"] == 0
    # 目录拷贝完整
    assert (Path(data_dir) / "skills" / "browser-fetch" / "SKILL.md").is_file()
    assert (Path(data_dir) / "skills" / "browser-fetch" / "scripts" / "main.py").is_file()


def test_seed_idempotent_keeps_user_state(db, tmp_path, monkeypatch):
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = str(tmp_path / "data")

    seed.seed_builtin_skills(db, data_dir)
    seed.seed_builtin_skills(db, data_dir)  # 重复启动

    rows = _skill_rows(db)
    assert len(rows) == 1  # 不重复插入

    # 用户禁用后再次启动种子：enabled 状态保留
    db.toggle_skill(rows[0]["id"], False)
    seed.seed_builtin_skills(db, data_dir)
    assert _skill_rows(db)[0]["enabled"] == 0


def test_seed_updates_files_but_not_db_state(db, tmp_path, monkeypatch):
    """版本升级语义：文件幂等覆盖，DB 用户状态不动。"""
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = Path(tmp_path) / "data"

    seed.seed_builtin_skills(db, data_dir)
    db.toggle_skill(_skill_rows(db)[0]["id"], False)
    # 源文件变化后再次种子 → 安装目录被覆盖更新
    (src / "CHANGELOG.md").write_text("v2", encoding="utf-8")
    seed.seed_builtin_skills(db, data_dir)

    assert (data_dir / "skills" / "browser-fetch" / "CHANGELOG.md").is_file()
    assert len(_skill_rows(db)) == 1
    assert _skill_rows(db)[0]["enabled"] == 0


def test_seed_missing_source_warns_without_raising(db, tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "_locate_source", lambda name: None)
    seed.seed_builtin_skills(db, str(tmp_path / "data"))  # 不抛异常
    assert _skill_rows(db) == []


def test_seed_fingerprint_skips_recopy(db, tmp_path, monkeypatch):
    """指纹幂等（2.3 §2.2）：源未变时二次种子不重拷（大包启动成本保障）。"""
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = str(tmp_path / "data")

    seed.seed_builtin_skills(db, data_dir)

    copy_calls = []
    real_copytree = seed.shutil.copytree

    def _counting_copytree(*args, **kwargs):
        copy_calls.append(args)
        return real_copytree(*args, **kwargs)

    monkeypatch.setattr(seed.shutil, "copytree", _counting_copytree)
    seed.seed_builtin_skills(db, data_dir)  # 二次启动：L1 快检命中

    assert copy_calls == []  # 零拷贝
    fp = (Path(data_dir) / "skills" / "browser-fetch" / seed._SEED_FINGERPRINT)
    assert fp.is_file()


def test_seed_recopies_when_source_marker_changes(db, tmp_path, monkeypatch):
    """源文件变化（SKILL.md 重写）→ L1 失效 → L2 指纹不一致 → 重拷。"""
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = Path(tmp_path) / "data"

    seed.seed_builtin_skills(db, str(data_dir))
    # 源升级：改 SKILL.md（保证 mtime 变化）+ 新增文件
    md = src / "SKILL.md"
    md.write_text(md.read_text(encoding="utf-8") + "\n<!-- v2 -->\n", encoding="utf-8")
    (src / "NEW.md").write_text("v2", encoding="utf-8")
    seed.seed_builtin_skills(db, str(data_dir))

    assert (data_dir / "skills" / "browser-fetch" / "NEW.md").is_file()


def test_locate_source_prefers_env_and_falls_back(monkeypatch, tmp_path):
    """打包环境走 INKCUBE_RESOURCES_DIR；开发环境回退项目根。"""
    fake = tmp_path / "res" / "skills" / "browser-fetch"
    fake.mkdir(parents=True)
    monkeypatch.setenv("INKCUBE_RESOURCES_DIR", str(tmp_path / "res"))
    assert seed._locate_source("browser-fetch") == str(fake)

    monkeypatch.delenv("INKCUBE_RESOURCES_DIR")
    located = seed._locate_source("browser-fetch")
    assert located is not None and Path(located) == PROJECT_SKILL_SRC


# ── 后台化拆分（2026-10-24 启动提速）：deferred 同步部分 + 文件同步原子替换 ──


def test_deferred_registers_db_without_file_copy(db, tmp_path, monkeypatch):
    """deferred 入口只做 DB upsert（毫秒级），文件同步留给后台线程。"""
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = str(tmp_path / "data")

    pending = seed.seed_builtin_skills_deferred(db, data_dir)

    # DB 行已注册（TaskAgent 执行期实时 list_skills 立即可见）
    rows = _skill_rows(db)
    assert len(rows) == 1
    assert rows[0]["needs_confirm"] == 0
    # 待同步清单含该 skill（清单共 3 个内置 skill，均定位到同一 fake 源），
    # 且文件未拷
    assert ("browser-fetch", str(src)) in pending
    assert not (Path(data_dir) / "skills" / "browser-fetch" / "SKILL.md").exists()


def test_sync_skill_files_atomic_replace(db, tmp_path, monkeypatch):
    """staging 原子替换：新版就位、无 .staging/.old 残留、指纹写入。"""
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = Path(tmp_path) / "data"

    seed.seed_builtin_skills(db, str(data_dir))
    # 源升级 → 后台同步 → 原子替换
    (src / "NEW.md").write_text("v2", encoding="utf-8")
    seed.sync_skill_files("browser-fetch", str(src), str(data_dir))

    install = data_dir / "skills" / "browser-fetch"
    assert (install / "NEW.md").is_file()
    assert (install / "SKILL.md").is_file()
    assert not (data_dir / "skills" / "browser-fetch.staging").exists()
    assert not (data_dir / "skills" / "browser-fetch.old").exists()
    fp = json.loads((install / seed._SEED_FINGERPRINT).read_text(encoding="utf-8"))
    assert fp["source_marker"] == list(seed._source_marker(str(src)))


def test_sync_skill_files_keeps_old_on_copy_failure(db, tmp_path, monkeypatch):
    """拷贝失败：旧版安装目录完整保留，不抛异常（非阻塞契约）。"""
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = Path(tmp_path) / "data"

    seed.seed_builtin_skills(db, str(data_dir))
    install = data_dir / "skills" / "browser-fetch"
    before = sorted(p.name for p in install.rglob("*"))

    def _boom(*args, **kwargs):
        raise OSError("simulated copy failure")

    monkeypatch.setattr(seed.shutil, "copytree", _boom)
    (src / "NEW.md").write_text("v2", encoding="utf-8")
    seed.sync_skill_files("browser-fetch", str(src), str(data_dir))  # 不抛

    # 旧版完整、新版未混入
    assert sorted(p.name for p in install.rglob("*")) == before
    assert not (install / "NEW.md").exists()
    assert not (data_dir / "skills" / "browser-fetch.staging").exists()


def test_sync_skill_files_fingerprint_skips_copy(db, tmp_path, monkeypatch):
    """源未变时文件同步零拷贝（后台线程不重复付全树拷贝成本）。"""
    src = _make_fake_source(tmp_path)
    monkeypatch.setattr(seed, "_locate_source", lambda name: str(src))
    data_dir = str(tmp_path / "data")

    seed.seed_builtin_skills(db, data_dir)

    copy_calls = []
    real_copytree = seed.shutil.copytree

    def _counting_copytree(*args, **kwargs):
        copy_calls.append(args)
        return real_copytree(*args, **kwargs)

    monkeypatch.setattr(seed.shutil, "copytree", _counting_copytree)
    seed.sync_skill_files("browser-fetch", str(src), data_dir)

    assert copy_calls == []


def test_sync_skill_files_batch_continues_after_failure(tmp_path):
    """批量入口单项失败仅 warning，其余项继续（非阻塞契约）。"""
    good_src = _make_fake_source(tmp_path)
    data_dir = Path(tmp_path) / "data"
    pending = [
        ("broken-skill", str(tmp_path / "does-not-exist")),  # copytree 会失败
        ("browser-fetch", str(good_src)),
    ]

    seed.sync_skill_files_batch(pending, str(data_dir))  # 不抛

    assert (data_dir / "skills" / "browser-fetch" / "SKILL.md").is_file()
