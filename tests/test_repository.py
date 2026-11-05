from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from ai_dashboard.models import ParsedArticle
from ai_dashboard.repository import Repository
from ai_dashboard.sources import SourceConfig
from tests.conftest import NOW


def test_insert_and_retrieve_article(
    repository: Repository, article_factory: Callable[..., ParsedArticle]
) -> None:
    article = article_factory(title="A power market story")
    added, updated = repository.insert_articles([article])
    assert (added, updated) == (1, 0)

    page = repository.get_articles()
    assert page.total == 1
    assert page.items[0].title == "A power market story"
    assert page.items[0].categories == ("general",)
    assert repository.get_article(page.items[0].id) is not None


def test_duplicate_is_merged(
    repository: Repository, article_factory: Callable[..., ParsedArticle]
) -> None:
    first = article_factory(
        summary="short",
        categories=("general",),
        kind="routine",
        url="https://example.com/story",
    )
    second = article_factory(
        summary="a much longer and more useful description",
        categories=("markets-energy", "general"),
        kind="major",
        url="https://www.example.com/story/?utm_source=other",
    )
    repository.insert_articles([first])
    added, updated = repository.insert_articles([second])

    assert (added, updated) == (0, 1)
    page = repository.get_articles()
    assert page.total == 1
    merged = page.items[0]
    assert merged.categories == ("markets-energy", "general")
    assert merged.kind == "major"
    assert merged.summary == "a much longer and more useful description"


def test_get_articles_filters_and_sorts(
    seeded_repository: Repository,
) -> None:
    markets = seeded_repository.get_articles(category="markets-energy")
    assert markets.total == 2
    assert all("markets-energy" in article.categories for article in markets.items)

    general_source = seeded_repository.get_articles(source_id="general")
    assert general_source.total == 2

    ai = seeded_repository.get_articles(category="ai-technology")
    assert ai.total == 1

    search = seeded_repository.get_articles(query="battery")
    assert search.total == 1
    assert "Battery" in search.items[0].title

    oldest = seeded_repository.get_articles(sort="oldest")
    assert oldest.items[0].title.startswith("Battery storage")

    newest = seeded_repository.get_articles(sort="newest", limit=2)
    assert len(newest.items) == 2
    assert newest.items[0].title.startswith("Electricity prices")

    second_page = seeded_repository.get_articles(limit=2, offset=2)
    assert len(second_page.items) == 2
    assert second_page.has_more is False


def test_get_articles_by_ids(seeded_repository: Repository) -> None:
    all_articles = seeded_repository.get_articles().items
    wanted = [all_articles[0].id, all_articles[2].id]
    page = seeded_repository.get_articles(ids=wanted)
    assert page.total == 2
    assert {article.id for article in page.items} == set(wanted)
    assert seeded_repository.get_articles(ids=[]).total == 0


def test_search_escapes_wildcards(seeded_repository: Repository) -> None:
    assert seeded_repository.get_articles(query="%").total == 0
    assert seeded_repository.get_articles(query="100%").total == 0


def test_empty_database_returns_empty_page(repository: Repository) -> None:
    page = repository.get_articles()
    assert page.total == 0
    assert page.items == ()
    assert page.has_more is False
    assert repository.get_article(999) is None


def test_category_counts_and_stats(seeded_repository: Repository) -> None:
    stats = seeded_repository.get_stats()
    assert stats.total_articles == 4
    assert stats.category_counts["markets-energy"] == 2
    assert stats.category_counts["ai-technology"] == 1
    assert stats.category_counts["general"] == 2
    assert stats.enabled_sources == 3
    assert stats.healthy_sources == 0


def test_retention_removes_old_articles(
    repository: Repository, article_factory: Callable[..., ParsedArticle]
) -> None:
    old = article_factory(
        url="https://example.com/old",
        published_at=NOW - timedelta(days=100),
    )
    fresh = article_factory(
        url="https://example.com/fresh",
        published_at=NOW - timedelta(days=1),
    )
    repository.insert_articles([old, fresh])
    deleted = repository.apply_retention(45, 6000, now=NOW)
    assert deleted == 1
    assert repository.count_articles() == 1
    assert repository.get_articles().items[0].url == "https://example.com/fresh"


def test_max_article_cap(
    repository: Repository, article_factory: Callable[..., ParsedArticle]
) -> None:
    articles = [
        article_factory(
            title=f"Story {index}",
            url=f"https://example.com/{index}",
            published_at=NOW - timedelta(hours=index),
        )
        for index in range(5)
    ]
    repository.insert_articles(articles)
    repository.apply_retention(45, 3, now=NOW)
    page = repository.get_articles()
    assert page.total == 3
    assert page.items[0].title == "Story 0"


def test_briefing_selects_lead_and_sections(seeded_repository: Repository) -> None:
    briefing = seeded_repository.get_briefing(now=NOW)
    assert briefing.lead is not None
    assert briefing.lead.title.startswith("Electricity prices")
    assert len(briefing.featured) >= 1
    assert set(briefing.sections) == {"markets-energy", "ai-technology", "general"}
    assert briefing.stats.total_articles == 4
    latest_ids = {article.id for article in briefing.latest}
    assert briefing.lead.id not in latest_ids


def test_briefing_on_empty_database(repository: Repository) -> None:
    briefing = repository.get_briefing(now=NOW)
    assert briefing.lead is None
    assert briefing.featured == ()
    assert all(items == () for items in briefing.sections.values())


def test_sync_sources_disables_removed_sources(
    repository: Repository, sources: list[SourceConfig]
) -> None:
    repository.sync_sources([sources[0]])
    stored = {source.id: source for source in repository.get_sources()}
    assert stored["markets"].enabled is True
    assert stored["ai"].enabled is False
    assert stored["general"].enabled is False


def test_record_source_health(
    repository: Repository,
) -> None:
    repository.record_source_failure("markets", "connection reset")
    source = repository.get_source("markets")
    assert source is not None
    assert source.consecutive_failures == 1
    assert source.last_error == "connection reset"

    repository.record_source_success("markets", etag='"abc"')
    source = repository.get_source("markets")
    assert source is not None
    assert source.consecutive_failures == 0
    assert source.last_error is None
    assert source.last_success_at is not None
    assert repository.source_etag("markets")[0] == '"abc"'
