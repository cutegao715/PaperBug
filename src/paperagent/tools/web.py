"""Browser-like web tools without browser automation.

These tools are intentionally read-only: search result discovery, page
download, and deterministic HTML-to-text extraction. Stateful browser actions
such as login, clicking, form submission, and downloads belong to a later
PermissionGate-protected layer.
"""
from __future__ import annotations

import hashlib
import html
import re
import time
import threading
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
from urllib import robotparser

import requests


DUCKDUCKGO_HTML_URL = "https://duckduckgo.com/html/"
DEFAULT_USER_AGENT = "PaperAgent/0.1 (+https://local.paperagent)"
MAX_HTML_CHARS = 500_000
MAX_TEXT_CHARS = 20_000

_LAST_FETCH_BY_HOST: dict[str, float] = {}
_RATE_LOCKS: dict[str, threading.Lock] = {}
_RATE_GUARD = threading.Lock()
_ROBOTS_CACHE: dict[str, robotparser.RobotFileParser] = {}


@dataclass
class ExtractedPage:
    title: str = ""
    text: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    published_at: str | None = None


class _ReadableHTMLParser(HTMLParser):
    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self.meta: dict[str, str] = {}
        self._tag_stack: list[str] = []
        self._current_link: dict[str, str] | None = None
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): v or "" for k, v in attrs}
        self._tag_stack.append(tag)
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (attrs_dict.get("property") or attrs_dict.get("name") or "").lower()
            value = attrs_dict.get("content", "").strip()
            if key and value:
                self.meta[key] = value
        if tag == "a" and attrs_dict.get("href"):
            self._current_link = {
                "url": urljoin(self.base_url, attrs_dict["href"]),
                "text": "",
            }
        if tag in {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._current_link:
            text = _clean_text(self._current_link.get("text", ""))
            if text and self._current_link["url"].startswith(("http://", "https://")):
                self.links.append({"url": self._current_link["url"], "text": text[:200]})
            self._current_link = None
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self.text_parts.append("\n")
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self._current_link is not None:
            self._current_link["text"] += f" {text}"
        if _visible_context(self._tag_stack):
            self.text_parts.append(text)
            self.text_parts.append(" ")

    def extracted(self) -> ExtractedPage:
        title = _clean_text(" ".join(self.title_parts)) or _metadata_title(self.meta)
        text = _clean_text(" ".join(self.text_parts))
        return ExtractedPage(
            title=title,
            text=text[:MAX_TEXT_CHARS],
            links=_dedup_links(self.links),
            published_at=_metadata_date(self.meta, text),
        )


def web_search(query: str, max_results: int = 5, timeout: float = 20.0,
               provider: str = "duckduckgo") -> dict[str, Any]:
    if provider == "offline_stub":
        return _offline_search(query, max_results)
    if provider != "duckduckgo":
        return {"error": f"unsupported web search provider: {provider}"}

    resp = requests.get(
        DUCKDUCKGO_HTML_URL,
        params={"q": query},
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=timeout,
    )
    resp.raise_for_status()
    results = _parse_duckduckgo_results(resp.text, max_results)
    return {
        "query": query,
        "provider": provider,
        "results": results,
        "provenance": f"{DUCKDUCKGO_HTML_URL}?q={quote_plus(query)}",
    }


def page_fetch(url: str, timeout: float = 20.0, respect_robots: bool = True,
               min_delay_s: float = 1.0) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return {"error": f"unsupported URL scheme: {parsed.scheme or '<none>'}"}
    if respect_robots and not _allowed_by_robots(url, timeout):
        return {"error": f"blocked by robots.txt: {url}"}
    _rate_limit(parsed.netloc, min_delay_s)

    resp = requests.get(
        url,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=timeout,
        allow_redirects=True,
    )
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    html_text = resp.text[:MAX_HTML_CHARS]
    snapshot = _source_snapshot(resp.url, html_text, content_type)
    return {
        "url": resp.url,
        "status_code": resp.status_code,
        "content_type": content_type,
        "html": html_text,
        "truncated": len(resp.text) > MAX_HTML_CHARS,
        "snapshot": snapshot,
        "provenance": resp.url,
    }


def html_extract(html_text: str, url: str = "", max_links: int = 25) -> dict[str, Any]:
    parser = _ReadableHTMLParser(base_url=url)
    parser.feed(html_text)
    page = parser.extracted()
    return {
        "url": url or None,
        "title": page.title,
        "text": page.text,
        "published_at": page.published_at,
        "links": page.links[:max_links],
        "snapshot": _source_snapshot(url, html_text, "text/html") if url else None,
        "provenance": url or "inline_html",
    }


def run_search(input: dict, timeout: float = 20.0) -> dict[str, Any]:
    return web_search(
        query=input["query"],
        max_results=int(input.get("max_results", 5)),
        timeout=timeout,
        provider=input.get("provider", "duckduckgo"),
    )


def run_fetch(input: dict, timeout: float = 20.0) -> dict[str, Any]:
    return page_fetch(
        url=input["url"],
        timeout=timeout,
        respect_robots=bool(input.get("respect_robots", True)),
        min_delay_s=float(input.get("min_delay_s", 1.0)),
    )


def run_extract(input: dict, timeout: float = 20.0) -> dict[str, Any]:
    return html_extract(
        html_text=input.get("html", ""),
        url=input.get("url", ""),
        max_links=int(input.get("max_links", 25)),
    )


def _parse_duckduckgo_results(html_text: str, max_results: int) -> list[dict[str, str]]:
    parser = _ReadableHTMLParser()
    parser.feed(html_text)
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.extracted().links:
        url = _unwrap_duckduckgo_url(link["url"])
        if not url or "duckduckgo.com" in urlparse(url).netloc:
            continue
        key = _dedup_key(url)
        if key in seen:
            continue
        seen.add(key)
        results.append({"title": link["text"], "url": url})
        if len(results) >= max_results:
            break
    return results


def _unwrap_duckduckgo_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return unquote(qs["uddg"][0])
    return url


def _allowed_by_robots(url: str, timeout: float) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = _ROBOTS_CACHE.get(robots_url)
    if rp is None:
        rp = robotparser.RobotFileParser(robots_url)
        try:
            response = requests.get(robots_url, timeout=timeout,
                                    headers={"User-Agent": DEFAULT_USER_AGENT})
            if response.status_code in (401, 403):
                rp.parse(['User-agent: *', 'Disallow: /'])
            elif response.status_code == 404:
                rp.parse([])
            else:
                response.raise_for_status()
                rp.parse(response.text.splitlines())
        except Exception:
            return True
        _ROBOTS_CACHE[robots_url] = rp
    try:
        return rp.can_fetch(DEFAULT_USER_AGENT, url)
    except Exception:
        return True


def _rate_limit(host: str, min_delay_s: float) -> None:
    if min_delay_s <= 0:
        return
    with _RATE_GUARD:
        lock = _RATE_LOCKS.setdefault(host, threading.Lock())
    with lock:
        now = time.monotonic()
        last = _LAST_FETCH_BY_HOST.get(host)
        if last is not None:
            wait = min_delay_s - (now - last)
            if wait > 0:
                time.sleep(wait)
        _LAST_FETCH_BY_HOST[host] = time.monotonic()


def _source_snapshot(url: str, body: str, content_type: str) -> dict[str, Any]:
    normalized_url = _normalize_url(url) if url else ""
    return {
        "url": url,
        "normalized_url": normalized_url,
        "dedup_key": _dedup_key(url) if url else hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
        "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "content_type": content_type,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    return f"{scheme}://{host}{path}"


def _dedup_key(url: str) -> str:
    normalized = _normalize_url(url)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _dedup_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in links:
        key = _dedup_key(link["url"])
        if key in seen:
            continue
        seen.add(key)
        out.append(link)
    return out


def _visible_context(stack: list[str]) -> bool:
    if not stack:
        return False
    return stack[-1] in {
        "article", "main", "section", "p", "li", "td", "th",
        "h1", "h2", "h3", "h4", "blockquote", "span", "a", "div",
    }


def _clean_text(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _metadata_title(meta: dict[str, str]) -> str:
    return meta.get("og:title") or meta.get("twitter:title") or ""


def _metadata_date(meta: dict[str, str], text: str) -> str | None:
    for key in (
        "article:published_time",
        "date",
        "dc.date",
        "dc.date.issued",
        "citation_publication_date",
    ):
        if meta.get(key):
            return meta[key]
    match = re.search(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", text)
    return match.group(1) if match else None


def _offline_search(query: str, max_results: int) -> dict[str, Any]:
    topic = query.strip() or "research"
    results = [
        {
            "title": f"[SYNTHETIC WEB STUB {i}] {topic}",
            "url": f"https://example.test/paperagent/{quote_plus(topic)}/{i}",
            "snippet": "Synthetic search result for offline tests and demos.",
        }
        for i in range(1, min(max_results, 3) + 1)
    ]
    return {
        "query": query,
        "provider": "offline_stub",
        "results": results,
        "provenance": "offline_stub (synthetic web results for tests/demo)",
        "warning": "offline mode: web results are synthetic",
    }
