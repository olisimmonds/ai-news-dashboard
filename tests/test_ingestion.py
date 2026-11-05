from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest

from ai_dashboard.ingestion import (
    FeedError,
    IngestionService,
    fetch_feed,
    parse_feed,
)
from ai_dashboard.repository import Repository
from ai_dashboard.sources import SourceConfig
from tests.conftest import NOW, rfc822, rss

SOURCE = SourceConfig(
    id="markets",
    name="Markets Wire",
    url="https://markets.example.com/",
    feed_url="https://feeds.example.com/markets.xml",
    categories=("markets-energy",),
)


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("ai_dashboard.ingestion.asyncio.sleep", no_sleep)


def transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_parse_feed_normalises_articles() -> None:
    payload = rss(
        [
            {
                "title": "  <b>Electricity prices</b> rise  ",
                "link": "https://markets.example.com/story?utm_source=test",
                "summary": "<p>Gas and <em>power</em> markets tightened.</p>",
                "published": rfc822(NOW - timedelta(hours=2)),
            }
        ]
    )
    articles = parse_feed(payload, SOURCE, fetched_at=NOW)
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "Electricity prices rise"
    assert article.summary == "Gas and power markets tightened."
    assert article.canonical_url == "https://markets.example.com/story"
    assert article.categories == ("markets-energy",)
    assert article.published_at == NOW - timedelta(hours=2)
    assert article.published_at_estimated is False


def test_parse_feed_skips_entries_without_title_or_link() -> None:
    payload = rss(
        [
            {"title": "", "link": "https://example.com/no-title"},
            {"title": "No link", "link": ""},
            {"title": "Valid story", "link": "https://example.com/valid"},
        ]
    )
    articles = parse_feed(payload, SOURCE, fetched_at=NOW)
    assert [article.title for article in articles] == ["Valid story"]


def test_parse_feed_deduplicates_within_feed() -> None:
    payload = rss(
        [
            {"title": "Same story", "link": "https://example.com/story"},
            {
                "title": "Same story duplicate",
                "link": "https://www.example.com/story/?utm_source=x",
            },
        ]
    )
    articles = parse_feed(payload, SOURCE, fetched_at=NOW)
    assert len(articles) == 1


def test_parse_feed_estimates_missing_timestamps() -> None:
    payload = rss([{"title": "Undated story", "link": "https://example.com/undated"}])
    articles = parse_feed(payload, SOURCE, fetched_at=NOW)
    assert articles[0].published_at == NOW
    assert articles[0].published_at_estimated is True


def test_parse_feed_applies_source_kind_hint() -> None:
    research_source = SourceConfig(
        id="research",
        name="Research Wire",
        url="https://research.example.com/",
        feed_url="https://research.example.com/feed.xml",
        categories=("ai-technology",),
        default_kind="research",
    )
    payload = rss([{"title": "A new paper on agents", "link": "https://example.com/paper"}])
    articles = parse_feed(payload, research_source, fetched_at=NOW)
    assert articles[0].kind == "research"


def test_malformed_feed_raises() -> None:
    with pytest.raises(FeedError):
        parse_feed(b"<html><body>Not a feed</body></html>", SOURCE, fetched_at=NOW)


@pytest.mark.asyncio
async def test_fetch_feed_success() -> None:
    payload = rss([{"title": "Story", "link": "https://example.com/story"}])

    def handler(request: httpx.Request) -> httpx.Response:
        assert "MeridianNewsDashboard" in request.headers["user-agent"]
        return httpx.Response(200, content=payload, headers={"etag": '"v1"'})

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await fetch_feed(client, SOURCE)
    assert result.status == "ok"
    assert len(result.articles) == 1
    assert result.etag == '"v1"'


@pytest.mark.asyncio
async def test_fetch_feed_sends_conditional_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await fetch_feed(client, SOURCE, etag='"v1"')
    assert result.status == "not_modified"
    assert result.articles == ()


@pytest.mark.asyncio
async def test_fetch_feed_handles_http_error() -> None:
    async with httpx.AsyncClient(
        transport=transport(lambda request: httpx.Response(404))
    ) as client:
        result = await fetch_feed(client, SOURCE)
    assert result.status == "failed"
    assert "404" in (result.error or "")


@pytest.mark.asyncio
async def test_fetch_feed_retries_server_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await fetch_feed(client, SOURCE)
    assert calls == 2
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_fetch_feed_handles_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=transport(handler)) as client:
        result = await fetch_feed(client, SOURCE)
    assert result.status == "failed"
    assert "timed out" in (result.error or "")


@pytest.mark.asyncio
async def test_fetch_feed_handles_malformed_content() -> None:
    async with httpx.AsyncClient(
        transport=transport(lambda request: httpx.Response(200, content=b"<html>nope</html>"))
    ) as client:
        result = await fetch_feed(client, SOURCE)
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_refresh_service_stores_articles_and_records_failures(
    settings: object,
    repository: Repository,
    sources: list[SourceConfig],
) -> None:
    payload = rss(
        [
            {"title": "Power prices rise", "link": "https://example.com/power"},
            {"title": "Grid update", "link": "https://example.com/grid"},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "markets" in request.url.path:
            return httpx.Response(200, content=payload)
        if "ai" in request.url.path:
            return httpx.Response(500)
        return httpx.Response(
            200, content=rss([{"title": "World", "link": "https://example.com/w"}])
        )

    service = IngestionService(repository, settings, sources, transport=transport(handler))  # type: ignore[arg-type]
    summary = await service.refresh("test")

    assert summary is not None
    assert summary.status == "partial"
    assert summary.sources_ok == 2
    assert summary.sources_failed == 1
    assert summary.articles_added == 3
    assert repository.count_articles() == 3

    ai_source = repository.get_source("ai")
    assert ai_source is not None
    assert ai_source.consecutive_failures == 1
    assert ai_source.last_error

    last_run = repository.get_last_run()
    assert last_run is not None
    assert last_run.status == "partial"


@pytest.mark.asyncio
async def test_refresh_service_uses_conditional_requests(
    settings: object,
    repository: Repository,
    sources: list[SourceConfig],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        slug = request.url.path.rsplit("/", 1)[-1].replace(".xml", "")
        payload = rss([{"title": f"{slug} story", "link": f"https://example.com/{slug}/story"}])
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=payload, headers={"etag": '"v1"'})

    service = IngestionService(
        repository,
        settings,  # type: ignore[arg-type]
        sources,
        transport=transport(handler),
    )
    first = await service.refresh("test")
    second = await service.refresh("test")

    assert first is not None and first.articles_added == 3
    assert second is not None
    assert second.sources_not_modified == 3
    assert second.articles_added == 0
    assert repository.count_articles() == 3


@pytest.mark.asyncio
async def test_refresh_skips_when_already_running(
    settings: object,
    repository: Repository,
    sources: list[SourceConfig],
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, content=rss([]))

    transport_instance = httpx.MockTransport(handler)
    service = IngestionService(repository, settings, sources, transport=transport_instance)  # type: ignore[arg-type]
    task = asyncio.create_task(service.refresh("first"))
    await started.wait()
    second = await service.refresh("second")
    release.set()
    await task
    assert second is None


@pytest.mark.asyncio
async def test_empty_feed_is_success(settings: object, repository: Repository) -> None:
    source = SourceConfig(
        id="empty",
        name="Empty",
        url="https://empty.example.com/",
        feed_url="https://empty.example.com/feed.xml",
        categories=("general",),
    )
    payload = rss([])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    service = IngestionService(
        repository,
        settings,  # type: ignore[arg-type]
        [source],
        transport=transport(handler),
    )
    summary = await service.refresh("test")
    assert summary is not None
    assert summary.status == "success"
    assert summary.articles_added == 0
    assert summary.sources_ok == 1


def test_parse_feed_ignores_unknown_entry_types() -> None:
    payload = rss([{"title": "Story", "link": "http://example.com/story"}])
    articles = parse_feed(payload, SOURCE)
    assert articles[0].url == "http://example.com/story"


def test_feed_source_validation_rejects_bad_url() -> None:
    source = SourceConfig(
        id="bad",
        name="Bad",
        url="https://bad.example.com/",
        feed_url="not-a-url",
        categories=("general",),
    )
    with pytest.raises(ValueError):
        source.validate()
