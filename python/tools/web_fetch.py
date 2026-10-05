import ipaddress
import json
import logging
import socket
from urllib.parse import urlparse

import httpx

from tools.base import BaseTool

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 3


def _is_private_host(hostname: str) -> bool:
    """Return True if the host resolves to a private/loopback/reserved IP.

    Unresolvable hostnames are also treated as private (rejected) — safest
    default for an LLM-controlled fetch tool.
    """
    try:
        addrinfos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return True
    for info in addrinfos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_unspecified):
            return True
    return False


def _validate_url(url: str) -> str | None:
    """Return an error message if the URL is not a safe public http(s) URL, else None.

    SSRF guard (H3): the URL comes from the LLM, which can be steered by
    prompt injection embedded in fetched pages. Blocking non-http(s)
    schemes and private/loopback hosts stops exfiltration of the app's own
    localhost API (e.g. GET /settings returns plaintext API keys).

    Note: DNS is resolved again by httpx at connect time, so a rebinding
    attack has a small TOCTOU window — acceptable for a local desktop tool.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "无效的 URL"
    if parsed.scheme not in ("http", "https"):
        return "仅支持 http/https 协议"
    host = parsed.hostname
    if not host:
        return "URL 缺少主机名"
    if host.lower() == "localhost" or _is_private_host(host):
        return "禁止访问内网/本机地址（SSRF 防护）"
    return None


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = (
        "抓取指定 URL 的网页内容，并将 HTML 转换为 Markdown 格式。"
        "适用于阅读文档、获取网页信息等场景。"
        "返回页面的文本内容（Markdown 格式）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要抓取的网页 URL",
            },
            "max_length": {
                "type": "integer",
                "description": "返回内容的最大字符数（默认 10000）",
            },
        },
        "required": ["url"],
    }

    @property
    def prompt_description(self) -> str:
        return (
            "## web_fetch\n"
            "抓取网页内容，HTML 自动转换为 Markdown 格式。\n\n"
            "用法:\n"
            "- 阅读在线文档、API 文档\n"
            "- 获取网页信息用于分析\n"
            "- 超时 15 秒，内容过长会自动截断\n\n"
            "示例:\n"
            '```\n{"url": "https://docs.python.org/3/tutorial/"}\n'
            "```\n\n"
            "注意: 只能抓取静态 HTML 内容，不支持 JavaScript 渲染的页面。"
        )

    async def execute(self, url: str, max_length: int = 10000) -> str:
        logger.info("web_fetch: url=%s, max_length=%d", url[:100], max_length)

        # SSRF guard: scheme + private-IP check before any request (H3)
        err = _validate_url(url)
        if err:
            logger.warning("web_fetch blocked: url=%s, reason=%s", url[:100], err)
            return json.dumps({"error": err}, ensure_ascii=False)

        try:
            # Manual redirect handling: every hop is re-validated so a public
            # URL can't redirect into localhost/private networks.
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=15.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; InkCube/1.0)",
                },
            ) as client:
                current_url = url
                for _ in range(_MAX_REDIRECTS + 1):
                    resp = await client.get(current_url)
                    if not resp.is_redirect:
                        break
                    location = resp.headers.get("location")
                    if not location:
                        break
                    current_url = str(httpx.URL(current_url).join(location))
                    err = _validate_url(current_url)
                    if err:
                        logger.warning(
                            "web_fetch redirect blocked: %s -> %s",
                            url[:100], current_url[:100],
                        )
                        return json.dumps(
                            {"error": f"重定向被拦截: {err}"}, ensure_ascii=False,
                        )
                else:
                    return json.dumps(
                        {"error": f"重定向次数过多（>{_MAX_REDIRECTS}）"}, ensure_ascii=False,
                    )
                resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            raw = resp.text

            # If it's HTML, convert to markdown
            if "html" in content_type or raw.strip().startswith(("<html", "<!DOCTYPE", "<html")):
                text = self._html_to_markdown(raw)
            else:
                text = raw

            # Truncate if too long
            if len(text) > max_length:
                text = text[:max_length] + "\n\n... [内容过长，已截断] ..."

            return json.dumps({
                "url": url,
                "content": text.strip(),
                "length": len(text),
            }, ensure_ascii=False)

        except httpx.TimeoutException:
            logger.warning("web_fetch timeout: url=%s", url[:100])
            return json.dumps({"error": "请求超时（15 秒）"}, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            logger.warning("web_fetch HTTP error: url=%s, status=%d", url[:100], e.response.status_code)
            return json.dumps({"error": f"HTTP {e.response.status_code}"}, ensure_ascii=False)
        except Exception as e:
            logger.error("web_fetch error: url=%s, error=%s", url[:100], str(e))
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @staticmethod
    def _html_to_markdown(html: str) -> str:
        """Simple HTML to Markdown converter (no external dependency)."""
        try:
            import html2text
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.body_width = 0  # No wrapping
            return h.handle(html)
        except ImportError:
            # Fallback: strip tags with basic conversion
            return _strip_html(html)


def _strip_html(html: str) -> str:
    """Minimal HTML-to-text fallback when html2text is not installed."""
    import re
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert common block elements to newlines
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li|tr|table)>", "\n", text, flags=re.IGNORECASE)
    # Convert bold/italic
    text = re.sub(r"<(b|strong)[^>]*>(.*?)</\1>", r"**\2**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(i|em)[^>]*>(.*?)</\1>", r"*\2*", text, flags=re.DOTALL | re.IGNORECASE)
    # Convert links
    text = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
