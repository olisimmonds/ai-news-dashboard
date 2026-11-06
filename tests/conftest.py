from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_dashboard.config import Settings
from ai_dashboard.db import initialise
from ai_dashboard.main import create_app
from ai_dashboard.models import ParsedArticle
from ai_dashboard.parsing import canonicalise_url, dedupe_key
from ai_dashboard.repository import Repository
from ai_dashboard.sources import SourceConfig

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "data" / "test.db",
        sources_file=tmp_path / "news_sources.toml",
        static_dir=tmp_path / "dist",
        scheduler_enabled=False,
        refresh_cooldown_seconds=0,
        request_timeout_seconds=5,
        max_concurrent_fetches=3,
        image_enrichment_enabled=False,
        browser_enabled=False,
    )


@pytest.fixture
def sources() -> list[SourceConfig]:
    return [
        SourceConfig(
            id="markets",
            name="Markets Wire",
            url="https://markets.test/",
            feed_url="https://feeds.example.com/markets.xml",
            categories=("markets-energy",),
        ),
        SourceConfig(
            id="ai",
            name="AI Wire",
            url="https://ai.test/",
            feed_url="https://feeds.example.com/ai.xml",
            categories=("ai-technology",),
        ),
        SourceConfig(
            id="general",
            name="World Wire",
            url="https://world.test/",
            feed_url="https://feeds.example.com/world.xml",
            categories=("general",),
        ),
    ]


@pytest.fixture
def repository(settings: Settings, sources: list[SourceConfig]) -> Repository:
    initialise(settings.database_path)
    repo = Repository(settings.database_path)
    repo.sync_sources(sources)
    return repo


@pytest.fixture
def make_client(
    settings: Settings, sources: list[SourceConfig]
) -> Callable[[Callable[[httpx.Request], httpx.Response]], TestClient]:
    def factory(
        handler: Callable[[httpx.Request], httpx.Response],
        weather_handler: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> TestClient:
        app = create_app(
            settings,
            sources,
            transport=httpx.MockTransport(handler),
            weather_transport=(httpx.MockTransport(weather_handler) if weather_handler else None),
        )
        return TestClient(app)

    return factory


def rss(
    items: list[dict[str, str]],
    *,
    title: str = "Test Feed",
    link: str = "https://example.com",
) -> bytes:
    entries = []
    for item in items:
        pub_date = item.get("published")
        date_element = f"<pubDate>{pub_date}</pubDate>" if pub_date else ""
        entries.append(
            "<item>"
            f"<title>{item.get('title', 'Untitled')}</title>"
            f"<link>{item.get('link', 'https://example.com/article')}</link>"
            f"<description><![CDATA[{item.get('summary', '')}]]></description>"
            f"<guid>{item.get('guid', item.get('link', ''))}</guid>"
            f"{date_element}"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f"<title>{title}</title><link>{link}</link>"
        "<description>Test feed</description>"
        f"{''.join(entries)}"
        "</channel></rss>"
    ).encode()


def rfc822(value: datetime) -> str:
    return format_datetime(value, usegmt=True)


@pytest.fixture
def article_factory() -> Callable[..., ParsedArticle]:
    def factory(
        *,
        title: str = "A test headline",
        url: str = "https://example.com/story",
        source_id: str = "general",
        source_name: str = "World Wire",
        summary: str = "A short description.",
        published_at: datetime | None = None,
        categories: tuple[str, ...] = ("general",),
        primary_category: str | None = None,
        image_url: str = "",
        kind: str = "routine",
        estimated: bool = False,
    ) -> ParsedArticle:
        published_at = published_at or NOW
        canonical = canonicalise_url(url)
        return ParsedArticle(
            dedupe_key=dedupe_key(canonical, title, source_id),
            title=title,
            url=url,
            canonical_url=canonical,
            source_id=source_id,
            source_name=source_name,
            summary=summary,
            published_at=published_at,
            fetched_at=NOW,
            categories=categories,
            primary_category=primary_category or (categories[0] if categories else "general"),
            image_url=image_url,
            kind=kind,
            published_at_estimated=estimated,
        )

    return factory


@pytest.fixture
def sample_articles(article_factory: Callable[..., ParsedArticle]) -> list[ParsedArticle]:
    return [
        article_factory(
            title="Electricity prices jump as gas supply tightens",
            url="https://markets.example.com/energy-prices",
            source_id="markets",
            source_name="Markets Wire",
            categories=("markets-energy",),
            summary="Power and gas markets reacted to lower imports.",
            published_at=NOW - timedelta(hours=1),
        ),
        article_factory(
            title="OpenAI releases a new agentic model",
            url="https://ai.example.com/model",
            source_id="ai",
            source_name="AI Wire",
            categories=("ai-technology",),
            summary="The company said the model improves tool use.",
            published_at=NOW - timedelta(hours=2),
            kind="company",
        ),
        article_factory(
            title="UK government announces new housing plan",
            url="https://world.example.com/housing",
            source_id="general",
            source_name="World Wire",
            categories=("general",),
            summary="Ministers set out proposals for planning reform.",
            published_at=NOW - timedelta(hours=3),
        ),
        article_factory(
            title="Battery storage investment reaches record high",
            url="https://world.example.com/battery",
            source_id="general",
            source_name="World Wire",
            categories=("markets-energy", "general"),
            summary="Grid-scale battery projects attracted fresh capital.",
            published_at=NOW - timedelta(hours=4),
        ),
    ]


@pytest.fixture
def seeded_repository(repository: Repository, sample_articles: list[ParsedArticle]) -> Repository:
    repository.insert_articles(sample_articles)
    return repository


@pytest.fixture
def clock() -> Iterator[datetime]:
    yield NOW
