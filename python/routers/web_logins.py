"""Web logins router — 用户登记的「网页登录态」站点管理（2.8 §9.1）。

仅维护 web_logins 表（域名白名单式的管理列表）；cookie 的实际清除由前端
先经 IPC `weblogin:remove-cookies` 完成，再调本路由删行（§9.2 链路方向）。
"""
from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from fastapi import APIRouter, Query
from pydantic import BaseModel

import deps

logger = logging.getLogger(__name__)

router = APIRouter()


class WebLoginAddRequest(BaseModel):
    domain: str
    note: str | None = None


def normalize_domain(raw: str) -> str | None:
    """域名规范化（§3.2）：支持裸域名或完整 URL，取 hostname 小写。

    返回 None 表示无法提取有效域名。
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    host = urlparse(raw).hostname
    if not host:
        return None
    host = host.lower()
    # 拒绝 IP 字面量与内网地址：登录态管理面向公网站点，且与 Electron
    # openLoginWindow 的内网拒绝保持一致（登记成功但打不开窗 = 状态不一致）。
    # inet_aton 覆盖 Chromium 接受的全部 IPv4 字面量形式：除点分十进制外，
    # BSD 简写（127.1 / 2130706433 / 0x7f000001 / 0177.0.0.1）也会被浏览器
    # 规范化为 127.0.0.1，仅拦点分形式会造成登记成功但登录窗被内网防线拒绝
    if host in ("localhost",) or host.endswith(".local"):
        return None
    if ":" in host:  # IPv6 字面量
        return None
    try:
        socket.inet_aton(host)
        return None  # 任意形式的 IPv4 字面量
    except OSError:
        pass
    return host


@router.get("/web-logins")
async def list_web_logins(
    limit: int = Query(50, ge=0, description="Page size; 0 = return all"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """列出已登记站点（limit=0 → 全量）。"""
    rows = deps.db.list_web_logins(limit=limit + 1 if limit > 0 else 0, offset=offset)
    has_more = limit > 0 and len(rows) > limit
    if has_more:
        rows = rows[:limit]
    total = deps.db.conn.execute(
        "SELECT COUNT(*) AS n FROM web_logins"
    ).fetchone()["n"]
    return {"logins": rows, "has_more": has_more, "total": total}


@router.post("/web-logins")
async def add_web_login(req: WebLoginAddRequest):
    """添加/登记站点（幂等：已存在时保留原行，含用户备注）。"""
    domain = normalize_domain(req.domain)
    if not domain:
        return {"error": "无效的域名", "code": "INVALID_DOMAIN"}
    result = deps.db.add_web_login(domain, (req.note or "").strip() or None)
    logger.info("Web login registered: %s (inserted=%s)", domain, result["inserted"])
    return {"login": result}


@router.delete("/web-logins/{domain}")
async def delete_web_login(domain: str):
    """删除登记行。cookie 清除由前端先行经 IPC 完成（§9.2）。"""
    ok = deps.db.delete_web_login(domain.lower())
    if not ok:
        return {"error": "站点不存在", "code": "SITE_NOT_FOUND"}
    logger.info("Web login removed: %s", domain)
    return {"ok": True}
