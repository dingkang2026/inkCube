"""内置 Skill 种子安装（2.8 §4.2 / 2.3 §2.2）：启动自检，幂等，清单式多 skill。

将 resources/skills/<name> 拷贝到 <data_dir>/skills/<name> 并写入 skills 表
（source_type='builtin'）。设计要点：
- 清单驱动：BUILTIN_SKILLS 声明全部内置 skill，新增内置 skill 只改清单
- 指纹幂等拷贝（2.3 §2.2）：安装目录写 .inkcube-seed.json（skill 版本 +
  文件数 + 总大小）；L1 快检（版本 + SKILL.md + 源顶层条目清单未变）直接
  跳过，L2 全树指纹不一致（升级）才重拷——13,245 文件的 ppt-master
  逐次启动幂等拷贝不可接受，walk 全树也应避免
- DB 按 name 查重：已存在则完全不动（保留用户 enabled 状态与确认状态）；
  不存在则 create_skill 并紧接 confirm_skill_by_name（needs_confirm 是迁移列
  且 DEFAULT 1，create_skill 的 INSERT 不含该列，不显式确认则首用会弹确认框）
- 任一步失败仅 warning，不阻塞后端启动（单个 skill 缺失可接受）
- 文件同步后台化（2026-10-24 启动提速）：seed_builtin_skills_deferred 只做
  毫秒级定位 + DB upsert，文件同步交调用方后台线程（main.py to_thread），
  13,245 文件的 ppt-master 逐文件拷贝（实测 ~22s）不再阻塞 /health；拷贝走
  _atomic_install staging 原子替换，install_path 任意时刻是完整旧版或完整新版
"""
from __future__ import annotations

import json
import logging
import os
import shutil

logger = logging.getLogger(__name__)

BUILTIN_SKILLS: list[dict] = [
    {
        "name": "browser-fetch",
        "description": (
            "用内置浏览器抓取动态网页（JavaScript 渲染后的内容），"
            "支持 CSS 选择器截取与登录态复用。"
        ),
    },
    {
        "name": "ppt-master",
        "description": (
            "PPT 交付物生成管线：报告/主题 → 可编辑 PPTX，"
            "含逐页 SVG 创作、质量门禁、模板工坊与成品增强。"
        ),
    },
    {
        # 2026-10-19 补种：资源目录一直存在但清单漏注册，Skill 管理页不可见、
        # 任务不可用；以目录名注册（frontmatter name 带外部前缀，不采用）
        "name": "primary-market-skill",
        "description": (
            "金融一级市场与投研总入口：募投管退、尽调文书（NDA/Teaser/IM/"
            "Pitch Deck/IC Memo）、估值建模（DCF/LBO/Comps）、商业模式与"
            "护城河拆解、投后管理等。"
        ),
    },
]

# 安装侧指纹文件名（2.3 §2.2）；记录 skill 版本 + 文件数 + 总大小
_SEED_FINGERPRINT = ".inkcube-seed.json"


def _locate_source(name: str) -> str | None:
    """定位资源目录中的 skill 源。

    打包环境：INKCUBE_RESOURCES_DIR（python-manager.js 注入）；
    开发环境：回退项目根 resources/skills/<name>。
    """
    candidates = []
    res_dir = os.environ.get("INKCUBE_RESOURCES_DIR")
    if res_dir:
        candidates.append(os.path.join(res_dir, "skills", name))
    # 开发回退：seed.py 位于 python/skills/,三层 dirname 即项目根
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates.append(os.path.join(project_root, "resources", "skills", name))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def builtin_skill_source(name: str) -> str | None:
    """内置 skill 源目录（外部内置判定消费，如 PPT 模板内置标记）；未随包返回 None。"""
    return _locate_source(name)


def _skill_version(src: str) -> str:
    """从 SKILL.md frontmatter 粗提取 version（解析失败回退 unknown）。"""
    md = os.path.join(src, "SKILL.md")
    try:
        with open(md, "r", encoding="utf-8") as f:
            head = f.read(4096)
        for line in head.splitlines():
            stripped = line.strip()
            if stripped.startswith("version:"):
                return stripped.split(":", 1)[1].strip().strip("'\"") or "unknown"
            if stripped == "---":
                continue
            if stripped and not stripped.startswith(("name:", "description:", "version:")) \
                    and ":" not in stripped:
                break  # frontmatter 结束
    except OSError:
        pass
    return "unknown"


def _source_marker(src: str) -> list:
    """L1 快检标记：version + SKILL.md (mtime,size) + 源顶层条目清单。

    纯 SKILL.md 判定有盲区：仅新增/删除顶层文件（随包 CHANGELOG、
    纯脚本升级只动 scripts/）不会触碰 SKILL.md，会被误判「源未变」跳过。
    顶层 os.listdir + stat 成本可忽略（几十条目）；直接子目录记条目数
    （子目录增删文件必检出）。残留盲区：深层文件同数替换——由发布约定
    兜底（正规发布必 bump SKILL.md version，marker 含 version 参与比较），
    且 L1 一旦失效即触发 L2 全树精确对比。
    """
    st = os.stat(os.path.join(src, "SKILL.md"))
    entries: list = []
    try:
        for name in sorted(os.listdir(src)):
            full = os.path.join(src, name)
            if os.path.isdir(full):
                try:
                    entries.append([name, "d", len(os.listdir(full))])
                except OSError:
                    entries.append([name, "d", -1])
            else:
                try:
                    est = os.stat(full)
                    entries.append([name, "f", int(est.st_mtime), est.st_size])
                except OSError:
                    entries.append([name, "f", -1, -1])
    except OSError:
        pass
    return [_skill_version(src), int(st.st_mtime), st.st_size, entries]


def _tree_fingerprint(root: str) -> tuple[int, int]:
    """L2 全树指纹：(文件数, 总字节数)。"""
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            try:
                total += os.stat(os.path.join(dirpath, fn)).st_size
                files += 1
            except OSError:
                continue
    return (files, total)


def _read_fingerprint(install_path: str) -> dict | None:
    fp = os.path.join(install_path, _SEED_FINGERPRINT)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_fingerprint(install_path: str, src: str, tree: tuple[int, int]) -> None:
    payload = {
        "version": _skill_version(src),
        "files": tree[0],
        "total_size": tree[1],
        "source_marker": list(_source_marker(src)),
    }
    fp = os.path.join(install_path, _SEED_FINGERPRINT)
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError as e:
        logger.warning("seed fingerprint write failed: %s", e)


def _needs_recopy(src: str, install_path: str) -> bool:
    """指纹判定是否需要重拷。L1 快检不过（源标记变化）才走 L2 全树对比。"""
    fp = _read_fingerprint(install_path)
    if not fp:
        return True
    marker = list(_source_marker(src))
    if fp.get("source_marker") == marker:
        return False  # L1：源未变 → 零 walk 跳过
    files, total = _tree_fingerprint(install_path)
    return fp.get("files") != files or fp.get("total_size") != total


def _seed_one_meta(db, name: str, description: str, data_dir: str) -> tuple[str, str] | None:
    """种子同步部分（毫秒级，2026-10-24 启动提速拆分）：定位源 + DB upsert。

    DB 行即时可见：TaskAgent 执行期实时 list_skills（task_agent.py）、Skill
    管理页打开即见，不依赖文件同步完成。返回待后台文件同步的 (name, src)；
    源缺失或 DB 异常返回 None（仅 warning，非阻塞契约不变）。
    """
    try:
        src = _locate_source(name)
        if not src:
            logger.warning("builtin skill source not found, skip seeding: %s", name)
            return None

        install_path = os.path.join(data_dir, "skills", name)
        os.makedirs(os.path.dirname(install_path), exist_ok=True)

        existing = [s for s in db.list_skills() if s.get("name") == name]
        if existing:
            logger.info("builtin skill already present, keep user state: %s", name)
        else:
            db.create_skill(
                name=name,
                description=description,
                source_type="builtin",
                source_path=src,
                install_path=install_path,
            )
            # needs_confirm 迁移列 DEFAULT 1：内置代码可信，立即置 0 免首用确认弹窗
            db.confirm_skill_by_name(name)
            logger.info("builtin skill registered: %s", name)
        return (name, src)
    except Exception as e:
        logger.warning("builtin skill seeding failed (non-blocking): %s (%s)",
                       name, e)
        return None


def _atomic_install(src: str, install_path: str) -> None:
    """staging 原子替换拷贝（2026-10-24 启动提速）：install_path 任意时刻是

    完整旧版或完整新版，消除「逐文件覆盖中的混合状态」——文件同步已后台化
    （main.py to_thread），任务可能在拷贝中途读安装目录。

    流程：清残留（.staging/.old，进程被杀的遗留）→ copytree → 指纹写 staging
    → rename 旧→.old → rename staging→正式 → 清 .old；正式位替换失败回滚
    .old，旧版始终完整可用。
    """
    staging = install_path + ".staging"
    old = install_path + ".old"
    for leftover in (staging, old):
        if os.path.isdir(leftover):
            shutil.rmtree(leftover, ignore_errors=True)
    shutil.copytree(src, staging)
    # 拷贝后源==目标，用源树指纹免二次 walk
    _write_fingerprint(staging, src, _tree_fingerprint(src))
    renamed_old = False
    if os.path.isdir(install_path):
        os.rename(install_path, old)
        renamed_old = True
    try:
        os.rename(staging, install_path)
    except OSError:
        if renamed_old and not os.path.isdir(install_path):
            os.rename(old, install_path)
        raise
    if renamed_old:
        shutil.rmtree(old, ignore_errors=True)


def sync_skill_files(name: str, src: str, data_dir: str) -> None:
    """文件同步（线程版，2026-10-24 启动提速）：L1 指纹快检命中即零拷贝跳过；

    需更新时走 staging 原子替换。全程不抛——失败仅 warning，旧版安装目录
    保持完整可用（非阻塞契约与 seed_builtin_skills 一致）。
    """
    try:
        install_path = os.path.join(data_dir, "skills", name)
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        if not _needs_recopy(src, install_path):
            logger.info("builtin skill fingerprint matched, skip copy: %s", name)
            return
        _atomic_install(src, install_path)
        logger.info("builtin skill synced: %s -> %s", src, install_path)
    except Exception as e:
        logger.warning("builtin skill file sync failed (non-blocking): %s (%s)",
                       name, e)


def sync_skill_files_batch(pending: list[tuple[str, str]], data_dir: str) -> None:
    """批量文件同步（后台线程入口）：单项失败仅 warning 不阻塞其余。"""
    for name, src in pending:
        sync_skill_files(name, src, data_dir)


def seed_builtin_skills(db, data_dir: str) -> None:
    """同步全量入口（测试/工具直调语义不变）：逐清单项 meta + 立即文件同步。"""
    for item in BUILTIN_SKILLS:
        meta = _seed_one_meta(db, item["name"], item["description"], data_dir)
        if meta:
            sync_skill_files(meta[0], meta[1], data_dir)


def seed_builtin_skills_deferred(db, data_dir: str) -> list[tuple[str, str]]:
    """启动入口（2026-10-24 启动提速）：只做毫秒级同步部分（定位 + DB
    upsert），文件同步清单交调用方后台线程执行（main.py to_thread）——
    13,245 文件的 ppt-master 逐文件拷贝（实测 ~22s）不再阻塞 uvicorn
    lifespan 的 /health。调用方须持后台任务引用防 GC（存储维护先例）。"""
    pending: list[tuple[str, str]] = []
    for item in BUILTIN_SKILLS:
        meta = _seed_one_meta(db, item["name"], item["description"], data_dir)
        if meta:
            pending.append(meta)
    return pending
