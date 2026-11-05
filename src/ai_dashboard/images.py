"""Image discovery from feed metadata and Open Graph tags."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .config import Settings
from .repository import Repository

logger = logging.getLogger(__name__)

IMAGE_USER_AGENT = (
    "MeridianNewsDashboard/1.0 (+https://github.com/olisimmonds/ai-news-dashboard) "
    "Mozilla/5.0 (compatible; image metadata reader)"
)
MAX_HTML_BYTES = 512 * 1024
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif")
_GENERIC_HINTS = {
    "logo",
    "logos",
    "brand",
    "branding",
    "default",
    "fallback",
    "placeholder",
    "icon",
    "icons",
    "favicon",
    "avatar",
    "sprite",
    "pixel",
    "tracking",
}


def _clean_url(value: object, base_url: str) -> str:
    if not isinstance(value, str):
        return ""
    url = urljoin(base_url, value.strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    path = parts.path.lower()
    if path.endswith((".svg", ".ico")):
        return ""
    if any(hint in path for hint in _GENERIC_HINTS):
        return ""
    return url


def _usable_feed_image(
    url: object,
    media_type: object,
    base_url: str,
    width: object = None,
    height: object = None,
) -> str:
    cleaned = _clean_url(url, base_url)
    if not cleaned:
        return ""
    for dimension in (width, height):
        try:
            if dimension is not None and int(str(dimension)) < 200:
                return ""
        except (TypeError, ValueError):
            continue
    type_value = str(media_type or "").lower()
    path = urlsplit(cleaned).path.lower()
    if type_value and not type_value.startswith("image/"):
        return ""
    if not type_value and not path.endswith(_IMAGE_EXTENSIONS):
        return ""
    return cleaned


def _iter_media(entry: Any, key: str) -> Iterable[dict[str, object]]:
    value = entry.get(key)
    if not isinstance(value, list):
        return ()
    return (item for item in value if isinstance(item, dict))


def extract_feed_image(entry: Any, base_url: str) -> str:
    """Extract the best article image from standard feed metadata."""
    for key in ("media_content", "media_thumbnail"):
        for item in _iter_media(entry, key):
            image = _usable_feed_image(
                item.get("url"),
                item.get("type"),
                base_url,
                item.get("width"),
                item.get("height"),
            )
            if image:
                return image

    for item in _iter_media(entry, "enclosures"):
        image = _usable_feed_image(
            item.get("href") or item.get("url"),
            item.get("type"),
            base_url,
            item.get("width"),
            item.get("height"),
        )
        if image:
            return image

    links = entry.get("links")
    if isinstance(links, list):
        for item in links:
            if not isinstance(item, dict):
                continue
            if str(item.get("rel", "")).lower() not in {"enclosure", "image"}:
                continue
            image = _usable_feed_image(item.get("href"), item.get("type"), base_url)
            if image:
                return image
    return ""


class _MetaImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if key}
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower().strip()
            content = values.get("content") or ""
            if key in {
                "og:image",
                "og:image:url",
                "og:image:secure_url",
                "twitter:image",
                "twitter:image:src",
            }:
                self.candidates.setdefault(key, content)
        elif tag.lower() == "link":
            if "image_src" in (values.get("rel") or "").lower():
                self.candidates.setdefault("image_src", values.get("href") or "")


def extract_og_image(html: str, page_url: str) -> str:
    """Extract an article-specific Open Graph/Twitter image from page HTML."""
    parser = _MetaImageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ""
    for key in (
        "og:image",
        "og:image:secure_url",
        "og:image:url",
        "twitter:image",
        "twitter:image:src",
        "image_src",
    ):
        image = _clean_url(parser.candidates.get(key), page_url)
        if image:
            return image
    return ""


def _is_html(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    return not content_type or "text/html" in content_type or "application/xhtml" in content_type


class ImageEnricher:
    """Fetch Open Graph images for a small, bounded set of recent articles.

    The operation runs as a background task after ingestion. It never blocks
    feed parsing or article display, and failures are remembered in SQLite so
    the same page is not retried on every refresh.
    """

    def __init__(
        self,
        repository: Repository,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self._transport = transport

    async def _resolve(self, client: httpx.AsyncClient, article_url: str) -> str:
        async with client.stream(
            "GET",
            article_url,
            headers={
                "User-Agent": IMAGE_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            },
            follow_redirects=True,
            timeout=self.settings.image_enrichment_timeout_seconds,
        ) as response:
            if not response.is_success or not _is_html(response):
                return ""
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) >= MAX_HTML_BYTES:
                    break
        return extract_og_image(bytes(buffer).decode("utf-8", errors="replace"), article_url)

    async def enrich(self, limit: int | None = None) -> dict[str, int]:
        limit = limit if limit is not None else self.settings.image_enrichment_limit
        if not self.settings.image_enrichment_enabled or limit <= 0:
            return {"found": 0, "failed": 0, "skipped": 0}
        candidates = self.repository.get_image_candidates(
            limit,
            retry_after_days=self.settings.image_enrichment_retry_days,
        )
        if not candidates:
            return {"found": 0, "failed": 0, "skipped": 0}

        found = 0
        failed = 0
        semaphore = asyncio.Semaphore(4)

        async with httpx.AsyncClient(transport=self._transport) as client:

            async def process(article_id: int, url: str) -> bool:
                nonlocal found, failed
                async with semaphore:
                    try:
                        image_url = await self._resolve(client, url)
                    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
                        logger.debug("Image lookup failed for %s: %s", url, exc)
                        self.repository.mark_image_checked(article_id)
                        failed += 1
                        return False
                    except Exception as exc:
                        logger.debug("Image lookup error for %s: %s", url, exc)
                        self.repository.mark_image_checked(article_id)
                        failed += 1
                        return False
                    if image_url:
                        self.repository.set_article_image(article_id, image_url)
                        found += 1
                        return True
                    self.repository.mark_image_checked(article_id)
                    failed += 1
                    return False

            await asyncio.gather(*(process(article.id, article.url) for article in candidates))

        logger.info(
            "Image enrichment: %s found, %s without images, %s candidates",
            found,
            failed,
            len(candidates),
        )
        return {"found": found, "failed": failed, "skipped": 0}
