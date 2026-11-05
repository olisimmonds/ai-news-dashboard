from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest

from ai_dashboard.config import Settings
from ai_dashboard.images import ImageEnricher, extract_feed_image, extract_og_image
from ai_dashboard.ingestion import parse_feed
from ai_dashboard.models import ParsedArticle
from ai_dashboard.repository import Repository
from ai_dashboard.sources import SourceConfig
from tests.conftest import NOW

SOURCE = SourceConfig(
    id="markets",
    name="Markets Wire",
    url="https://markets.example.com/",
    feed_url="https://feeds.example.com/markets.xml",
    categories=("markets-energy",),
)


def media_rss(media_url: str, *, media_type: str = "image/jpeg") -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">'
        "<channel><title>Test Feed</title><link>https://example.com</link>"
        "<description>Test</description><item>"
        "<title>Power prices rise</title>"
        "<link>https://markets.example.com/power</link>"
        "<guid>https://markets.example.com/power</guid>"
        f'<media:content url="{media_url}" type="{media_type}" width="1200" height="800"/>'
        "</item></channel></rss>"
    ).encode()


def test_feed_media_content_is_stored() -> None:
    payload = media_rss("https://cdn.example.com/hero.jpg")
    article = parse_feed(payload, SOURCE, fetched_at=NOW)[0]
    assert article.image_url == "https://cdn.example.com/hero.jpg"


def test_feed_media_rejects_logos_and_non_images() -> None:
    payload = media_rss("https://cdn.example.com/site-logo.png")
    article = parse_feed(payload, SOURCE, fetched_at=NOW)[0]
    assert article.image_url == ""

    payload = media_rss("https://cdn.example.com/document.pdf", media_type="application/pdf")
    article = parse_feed(payload, SOURCE, fetched_at=NOW)[0]
    assert article.image_url == ""


def test_media_thumbnail_is_relative_to_source() -> None:
    payload = media_rss("/images/hero.jpg")
    article = parse_feed(payload, SOURCE, fetched_at=NOW)[0]
    assert article.image_url == "https://markets.example.com/images/hero.jpg"


def test_extract_feed_image_handles_missing_metadata() -> None:
    assert extract_feed_image({}, "https://example.com") == ""


def test_open_graph_image_extraction() -> None:
    html = """
    <html><head>
      <meta property="og:image" content="/images/hero.webp">
      <meta name="twitter:image" content="https://cdn.example.com/twitter.jpg">
    </head></html>
    """
    assert extract_og_image(html, "https://example.com/story") == (
        "https://example.com/images/hero.webp"
    )


def test_open_graph_rejects_generic_fallback_images() -> None:
    html = '<meta property="og:image" content="https://cdn.example.com/placeholder.png">'
    assert extract_og_image(html, "https://example.com/story") == ""


@pytest.mark.asyncio
async def test_image_enricher_stores_open_graph_image(
    settings: Settings,
    repository: Repository,
    article_factory: Callable[..., ParsedArticle],
) -> None:
    repository.insert_articles(
        [
            article_factory(
                title="Power prices rise",
                url="https://markets.example.com/power",
                categories=("markets-energy",),
                published_at=NOW - timedelta(hours=1),
            )
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://markets.example.com/power"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text='<meta property="og:image" content="https://cdn.example.com/power.jpg">',
        )

    enriched_settings = settings.model_copy(
        update={"image_enrichment_enabled": True, "image_enrichment_limit": 5}
    )
    enricher = ImageEnricher(
        repository,
        enriched_settings,
        transport=httpx.MockTransport(handler),
    )

    result = await enricher.enrich()

    assert result == {"found": 1, "failed": 0, "skipped": 0}
    article = repository.get_articles().items[0]
    assert article.image_url == "https://cdn.example.com/power.jpg"


@pytest.mark.asyncio
async def test_image_enricher_remembers_failures(
    settings: Settings,
    repository: Repository,
    article_factory: Callable[..., ParsedArticle],
) -> None:
    repository.insert_articles(
        [
            article_factory(
                title="Power prices rise",
                url="https://markets.example.com/power",
                categories=("markets-energy",),
                published_at=NOW - timedelta(hours=1),
            )
        ]
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    enriched_settings = settings.model_copy(
        update={"image_enrichment_enabled": True, "image_enrichment_limit": 5}
    )
    enricher = ImageEnricher(
        repository,
        enriched_settings,
        transport=httpx.MockTransport(handler),
    )

    first = await enricher.enrich()
    second = await enricher.enrich()

    assert first["failed"] == 1
    assert second == {"found": 0, "failed": 0, "skipped": 0}
    assert calls == 1
