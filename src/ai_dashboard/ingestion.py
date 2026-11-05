"""Feed fetching, parsing and the refresh lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx

from .categorisation import categorise
from .config import Settings
from .images import ImageEnricher, extract_feed_image
from .models import ParsedArticle
from .parsing import canonicalise_url, clean_text, clean_title, dedupe_key, entry_datetime
from .repository import Repository
from .sources import SourceConfig

logger = logging.getLogger(__name__)

USER_AGENT = (
    "MeridianNewsDashboard/1.0 (+https://github.com/olisimmonds/ai-news-dashboard) "
    "Mozilla/5.0 (compatible; RSS reader)"
)
ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
)
MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_ENTRIES = 60


class FeedError(Exception):
    """Raised when a feed cannot be used."""


@dataclass(frozen=True, slots=True)
class FeedFetchResult:
    source_id: str
    status: str
    articles: tuple[ParsedArticle, ...] = ()
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class RefreshSummary:
    run_id: int
    trigger: str
    status: str
    sources_ok: int
    sources_failed: int
    sources_not_modified: int
    articles_added: int
    articles_updated: int


def _entry_summary(entry: Any) -> str:
    summary = entry.get("summary")
    if not summary:
        content = entry.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("value"):
                    summary = item["value"]
                    break
    return clean_text(summary, limit=600)


def _entry_link(entry: Any, base_url: str) -> str:
    link = entry.get("link")
    if not link:
        links = entry.get("links")
        if isinstance(links, list):
            for candidate in links:
                if isinstance(candidate, dict) and candidate.get("href"):
                    link = candidate["href"]
                    break
    if not link:
        return ""
    return urljoin(base_url, str(link).strip())


def _entry_tags(entry: Any) -> str:
    tags = entry.get("tags")
    if not isinstance(tags, list):
        return ""
    terms = []
    for tag in tags:
        if isinstance(tag, dict):
            term = tag.get("term") or tag.get("label")
            if term:
                terms.append(str(term))
    return " ".join(terms)


def parse_feed(
    payload: bytes,
    source: SourceConfig,
    fetched_at: datetime | None = None,
) -> tuple[ParsedArticle, ...]:
    """Parse a feed payload into normalised articles.

    Malformed entries are skipped individually; a wholly unusable feed raises
    :class:`FeedError`.
    """
    fetched_at = fetched_at or datetime.now(UTC)
    parsed = feedparser.parse(payload)

    if not parsed.entries and (parsed.bozo or not parsed.get("version")):
        detail = getattr(parsed, "bozo_exception", None) or "feed contained no entries"
        raise FeedError(str(detail)[:300])

    articles: list[ParsedArticle] = []
    seen: set[str] = set()
    for entry in parsed.entries[:MAX_ENTRIES]:
        try:
            title = clean_title(entry.get("title"))
            url = _entry_link(entry, source.url or source.feed_url)
            if not title or not url.startswith(("http://", "https://")):
                continue

            summary = _entry_summary(entry)
            tags = _entry_tags(entry)
            image_url = extract_feed_image(entry, source.url or source.feed_url)
            categorised = categorise(
                title,
                f"{summary} {tags}".strip(),
                source_categories=source.categories,
            )
            published_at, estimated = entry_datetime(entry, now=fetched_at)
            canonical_url = canonicalise_url(url)
            key = dedupe_key(canonical_url, title, source.id)
            if key in seen:
                continue
            seen.add(key)

            kind = categorised.kind
            if kind == "routine" and source.default_kind:
                kind = source.default_kind

            articles.append(
                ParsedArticle(
                    dedupe_key=key,
                    title=title,
                    url=url,
                    canonical_url=canonical_url,
                    source_id=source.id,
                    source_name=source.name,
                    summary=summary,
                    published_at=published_at,
                    fetched_at=fetched_at,
                    categories=categorised.categories,
                    primary_category=categorised.primary_category,
                    kind=kind,
                    published_at_estimated=estimated,
                    image_url=image_url,
                )
            )
        except Exception as exc:  # A single bad entry must not lose the feed.
            logger.debug("Skipping malformed entry from %s: %s", source.id, exc)
    return tuple(articles)


async def fetch_feed(
    client: httpx.AsyncClient,
    source: SourceConfig,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    timeout: float = 20.0,
) -> FeedFetchResult:
    """Fetch and parse one feed with conditional requests and a single retry."""
    headers = {"User-Agent": USER_AGENT, "Accept": ACCEPT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    last_error = "unknown feed error"
    for attempt in range(2):
        started = time.perf_counter()
        try:
            response = await client.get(
                source.feed_url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
            duration_ms = int((time.perf_counter() - started) * 1000)

            if response.status_code == 304:
                return FeedFetchResult(
                    source_id=source.id,
                    status="not_modified",
                    etag=etag,
                    last_modified=last_modified,
                    duration_ms=duration_ms,
                )
            if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                await asyncio.sleep(2.0)
                last_error = f"HTTP {response.status_code}"
                continue
            if not response.is_success:
                return FeedFetchResult(
                    source_id=source.id,
                    status="failed",
                    error=f"HTTP {response.status_code}",
                    duration_ms=duration_ms,
                )
            if len(response.content) > MAX_FEED_BYTES:
                return FeedFetchResult(
                    source_id=source.id,
                    status="failed",
                    error="feed larger than 5 MB",
                    duration_ms=duration_ms,
                )
            if not response.content.strip():
                return FeedFetchResult(
                    source_id=source.id,
                    status="failed",
                    error="empty response",
                    duration_ms=duration_ms,
                )

            articles = parse_feed(response.content, source)
            return FeedFetchResult(
                source_id=source.id,
                status="ok",
                articles=articles,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                duration_ms=duration_ms,
            )
        except (httpx.HTTPError, FeedError, ValueError) as exc:
            last_error = str(exc)[:300]
            if attempt == 0:
                await asyncio.sleep(1.5)
                continue
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            break

    return FeedFetchResult(source_id=source.id, status="failed", error=last_error)


class IngestionService:
    """Coordinates a refresh across all enabled sources.

    A single process-wide refresh lock prevents overlapping runs, whether
    triggered by the scheduler or the manual refresh endpoint.
    """

    def __init__(
        self,
        repository: Repository,
        settings: Settings,
        sources: list[SourceConfig],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.sources = [source for source in sources if source.enabled]
        self._transport = transport
        self._lock = asyncio.Lock()
        self._last_activity: datetime | None = None
        self.next_refresh_at: datetime | None = None
        self._image_enricher = ImageEnricher(repository, settings, transport=transport)
        self._image_task: asyncio.Task[dict[str, int]] | None = None

    @property
    def is_refreshing(self) -> bool:
        return self._lock.locked()

    @property
    def last_activity(self) -> datetime | None:
        return self._last_activity

    async def refresh(self, trigger: str = "scheduled") -> RefreshSummary | None:
        if self._lock.locked():
            logger.info("Refresh already running; skipping %s trigger", trigger)
            return None

        async with self._lock:
            run_id = self.repository.start_refresh_run(trigger)
            datetime.now(UTC)
            semaphore = asyncio.Semaphore(self.settings.max_concurrent_fetches)
            results: list[FeedFetchResult] = []

            async with httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                transport=self._transport,
            ) as client:

                async def process(source: SourceConfig) -> FeedFetchResult:
                    async with semaphore:
                        etag, last_modified = self.repository.source_etag(source.id)
                        try:
                            result = await fetch_feed(
                                client,
                                source,
                                etag=etag,
                                last_modified=last_modified,
                                timeout=self.settings.request_timeout_seconds,
                            )
                        except Exception as exc:
                            logger.exception("Unexpected feed error for %s", source.id)
                            result = FeedFetchResult(
                                source_id=source.id,
                                status="failed",
                                error=f"{type(exc).__name__}: {exc}"[:300],
                            )

                        try:
                            if result.status in {"ok", "not_modified"}:
                                self.repository.record_source_success(
                                    source.id,
                                    etag=result.etag,
                                    last_modified=result.last_modified,
                                )
                            else:
                                self.repository.record_source_failure(
                                    source.id, result.error or "unknown error"
                                )
                        except Exception:
                            logger.exception("Could not record health for %s", source.id)
                        return result

                results = list(await asyncio.gather(*(process(source) for source in self.sources)))

            added = 0
            updated = 0
            sources_ok = 0
            sources_failed = 0
            not_modified = 0
            errors: list[str] = []

            for result in results:
                if result.status == "failed":
                    sources_failed += 1
                    errors.append(f"{result.source_id}: {result.error}")
                    logger.warning("Feed failed: %s (%s)", result.source_id, result.error)
                    continue
                sources_ok += 1
                if result.status == "not_modified":
                    not_modified += 1
                    continue
                try:
                    new_count, updated_count = self.repository.insert_articles(result.articles)
                    added += new_count
                    updated += updated_count
                except Exception:
                    sources_failed += 1
                    sources_ok -= 1
                    errors.append(f"{result.source_id}: storage error")
                    logger.exception("Could not store articles for %s", result.source_id)

            if sources_failed and sources_ok:
                status = "partial"
            elif sources_failed and not sources_ok:
                status = "failed"
            else:
                status = "success"

            self.repository.finish_refresh_run(
                run_id,
                status=status,
                sources_ok=sources_ok,
                sources_failed=sources_failed,
                sources_not_modified=not_modified,
                articles_added=added,
                articles_updated=updated,
                error_summary="; ".join(errors[:8]) or None,
            )
            self.repository.apply_retention(
                self.settings.retention_days, self.settings.max_articles
            )
            try:
                self.repository.rebuild_stories()
            except Exception:
                logger.exception("Could not rebuild story clusters")
            self._last_activity = datetime.now(UTC)
            self.schedule_image_enrichment()

            logger.info(
                "Refresh %s: %s ok, %s failed, %s not modified, +%s articles, %s updated",
                trigger,
                sources_ok,
                sources_failed,
                not_modified,
                added,
                updated,
            )
            return RefreshSummary(
                run_id=run_id,
                trigger=trigger,
                status=status,
                sources_ok=sources_ok,
                sources_failed=sources_failed,
                sources_not_modified=not_modified,
                articles_added=added,
                articles_updated=updated,
            )

    def schedule_image_enrichment(self) -> None:
        if not self.settings.image_enrichment_enabled:
            return
        if self._image_task is not None and not self._image_task.done():
            return
        task = asyncio.create_task(self._image_enricher.enrich(), name="meridian-image-enrichment")
        self._image_task = task

    async def close(self) -> None:
        if self._image_task is None:
            return
        if not self._image_task.done():
            self._image_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._image_task
        self._image_task = None


class RefreshScheduler:
    """A tiny asyncio scheduler; no external job queue is needed."""

    def __init__(self, service: IngestionService, interval_minutes: int) -> None:
        self.service = service
        self.interval_seconds = max(interval_minutes, 5) * 60
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="meridian-refresh-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        try:
            if self.service.repository.is_empty():
                await self.service.refresh(trigger="startup")
            while True:
                self.service.next_refresh_at = datetime.now(UTC) + timedelta(
                    seconds=self.interval_seconds
                )
                await asyncio.sleep(self.interval_seconds)
                try:
                    await self.service.refresh(trigger="scheduled")
                except Exception:
                    logger.exception("Scheduled refresh failed; will retry later")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Refresh scheduler stopped unexpectedly")
