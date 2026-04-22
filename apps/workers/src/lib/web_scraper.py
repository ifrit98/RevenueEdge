"""Lightweight website scraper for knowledge ingestion.

Crawls a root URL up to ``max_pages`` internal links, extracts visible
text, and returns a list of ``(url, title, text)`` tuples.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_IGNORED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
    ".mp4", ".mp3", ".zip", ".tar", ".gz", ".css", ".js",
}


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
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url, href)
        ext = urlparse(full).path.rsplit(".", 1)[-1] if "." in urlparse(full).path else ""
        if f".{ext}" in _IGNORED_EXTENSIONS:
            continue
        if _same_domain(base_url, full) and full not in links:
            links.append(full)
    return links


async def scrape_website(
    root_url: str,
    *,
    max_pages: int = 20,
    timeout: float = 15.0,
) -> list[dict[str, str]]:
    """Crawl ``root_url`` and return a list of ``{"url", "title", "text"}``."""
    visited: set[str] = set()
    queue: list[str] = [root_url.rstrip("/")]
    results: list[dict[str, str]] = []

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "RevenueEdge-KnowledgeBot/1.0"},
    ) as client:
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            canonical = url.rstrip("/")
            if canonical in visited:
                continue
            visited.add(canonical)

            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception:
                logger.debug("Skipping %s (fetch failed)", url)
                continue

            ct = resp.headers.get("content-type", "")
            if "html" not in ct.lower():
                continue

            html = resp.text
            title = _extract_title(html) or url
            text = _strip_html(html)

            if len(text) > 100:
                results.append({"url": url, "title": title, "text": text})

            for link in _extract_links(html, url):
                if link.rstrip("/") not in visited:
                    queue.append(link)

    logger.info("Scraped %d pages from %s", len(results), root_url)
    return results
