"""内置 Skill 种子安装（2.8 §4.2）：启动自检，幂等。

将 resources/skills/browser-fetch 拷贝到 <data_dir>/skills/ 并写入 skills 表
（source_type='builtin'）。设计要点：
- copytree(dirs_exist_ok=True) 幂等覆盖，保证随版本升级（覆盖用户对该目录的
  手工修改属预期行为——内置 skill 目录不支持用户改造）
- DB 按 name 查重：已存在则完全不动（保留用户 enabled 状态与确认状态）；
  不存在则 create_skill 并紧接 confirm_skill_by_name（needs_confirm 是迁移列
  且 DEFAULT 1，create_skill 的 INSERT 不含该列，不显式确认则首用会弹确认框）
- 任一步失败仅 warning，不阻塞后端启动（抓取能力缺失可接受）
"""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)

SKILL_NAME = "browser-fetch"
SKILL_DESCRIPTION = (
    "用内置浏览器抓取动态网页（JavaScript 渲染后的内容），"
    "支持 CSS 选择器截取与登录态复用。"
)


def _locate_source() -> str | None:
    """定位资源目录中的 browser-fetch 源。

    打包环境：INKCUBE_RESOURCES_DIR（python-manager.js 注入）；
    开发环境：回退项目根 resources/skills/browser-fetch。
    """
    candidates = []
    res_dir = os.environ.get("INKCUBE_RESOURCES_DIR")
    if res_dir:
        candidates.append(os.path.join(res_dir, "skills", SKILL_NAME))
    # 开发回退：seed.py 位于 python/skills/,三层 dirname 即项目根
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates.append(os.path.join(project_root, "resources", "skills", SKILL_NAME))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def seed_builtin_skills(db, data_dir: str) -> None:
    """启动自检入口：拷贝 + DB upsert，全程不抛异常。"""
    try:
        src = _locate_source()
        if not src:
            logger.warning("builtin skill source not found, skip seeding: %s", SKILL_NAME)
            return

        install_path = os.path.join(data_dir, "skills", SKILL_NAME)
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copytree(src, install_path, dirs_exist_ok=True)

        existing = [s for s in db.list_skills() if s.get("name") == SKILL_NAME]
        if existing:
            logger.info("builtin skill already present, keep user state: %s", SKILL_NAME)
            return

        db.create_skill(
            name=SKILL_NAME,
            description=SKILL_DESCRIPTION,
            source_type="builtin",
            source_path=src,
            install_path=install_path,
        )
        # needs_confirm 迁移列 DEFAULT 1：内置代码可信，立即置 0 免首用确认弹窗
        db.confirm_skill_by_name(SKILL_NAME)
        logger.info("builtin skill seeded: %s -> %s", src, install_path)
    except Exception as e:
        logger.warning("builtin skill seeding failed (non-blocking): %s", e)
