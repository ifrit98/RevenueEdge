"""Lightweight website scraper for knowledge ingestion.

Crawls a root URL up to ``max_pages`` internal links, extracts visible
text, and returns a list of ``(url, title, text)`` tuples.

Security: blocks private/reserved IPs, metadata endpoints, and non-HTTP
schemes.  Caps queue size and per-response body size.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_IGNORED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
    ".mp4", ".mp3", ".zip", ".tar", ".gz", ".css", ".js",
}

_MAX_QUEUE_SIZE = 500
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB per page


def _is_safe_url(url: str) -> bool:
    """Reject URLs targeting private networks, metadata endpoints, or non-HTTP schemes."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname or ""
    if not hostname:
        return False

    if hostname in ("localhost", "metadata.google.internal"):
        return False

    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return False

    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
        if str(ip) == "169.254.169.254":
            return False
    return True


def _same_domain(base: str, target: str) -> bool:
    return urlparse(base).netloc == urlparse(target).netloc


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<(nav|footer|header)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&\w+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return m.group(1).strip() if m else ""


def _extract_links(html: str, base_url: str) -> list[str]:
    links: list[str] = []
    for m in re.finditer(r'<a\s[^>]*href=["\']([^"\'#]+)', html, re.I):
        href = m.group(1).strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "data:", "vbscript:")):
            continue
        full = urljoin(base_url, href)
        ext = urlparse(full).path.rsplit(".", 1)[-1] if "." in urlparse(full).path else ""
        if f".{ext}" in _IGNORED_EXTENSIONS:
            continue
        if _same_domain(base_url, full) and full not in links:
            links.append(full)
    return links


async def _safe_read_body(resp: httpx.Response) -> str:
    """Read response text, capped at _MAX_RESPONSE_BYTES."""
    raw = resp.content
    if len(raw) > _MAX_RESPONSE_BYTES:
        raw = raw[:_MAX_RESPONSE_BYTES]
    return raw.decode("utf-8", errors="replace")


async def scrape_website(
    root_url: str,
    *,
    max_pages: int = 20,
    timeout: float = 15.0,
) -> list[dict[str, str]]:
    """Crawl ``root_url`` and return a list of ``{"url", "title", "text"}``."""
    if not _is_safe_url(root_url):
        logger.warning("Blocked unsafe root URL: %s", root_url)
        return []

    visited: set[str] = set()
    queue: list[str] = [root_url.rstrip("/")]
    results: list[dict[str, str]] = []

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": "RevenueEdge-KnowledgeBot/1.0"},
        max_redirects=5,
    ) as client:
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            canonical = url.rstrip("/")
            if canonical in visited:
                continue
            visited.add(canonical)

            if not _is_safe_url(url):
                logger.debug("Skipping unsafe URL: %s", url)
                continue

            try:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
            except Exception:
                logger.debug("Skipping %s (fetch failed)", url)
                continue

            final_url = str(resp.url)
            if not _is_safe_url(final_url):
                logger.debug("Skipping redirect to unsafe URL: %s", final_url)
                continue

            ct = resp.headers.get("content-type", "")
            if "html" not in ct.lower():
                continue

            html = await _safe_read_body(resp)
            title = _extract_title(html) or url
            text = _strip_html(html)

            if len(text) > 100:
                results.append({"url": url, "title": title, "text": text})

            if len(queue) < _MAX_QUEUE_SIZE:
                for link in _extract_links(html, url):
                    if link.rstrip("/") not in visited and len(queue) < _MAX_QUEUE_SIZE:
                        queue.append(link)

    logger.info("Scraped %d pages from %s", len(results), root_url)
    return results
