from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from ai_dashboard.curation import CURATION
from ai_dashboard.models import ArticleRecord, ParsedArticle
from ai_dashboard.repository import Repository
from ai_dashboard.sources import SourceConfig
from ai_dashboard.stories import StoryCluster, build_story_clusters, select_briefing, title_tokens
from tests.conftest import NOW


def test_related_coverage_forms_one_major_story(
    repository: Repository,
    article_factory: Callable[..., ParsedArticle],
) -> None:
    articles = [
        article_factory(
            title="Bank of England raises interest rates to 4%",
            url=f"https://{source}.example.com/rates",
            source_id=source,
            source_name=source,
            categories=("markets-energy",),
            published_at=NOW - timedelta(hours=index + 1),
        )
        for index, source in enumerate(("markets", "ai", "general"))
    ]
    repository.insert_articles(articles)
    records = repository.get_articles().items

    clusters = build_story_clusters(records, now=NOW, config=CURATION)

    assert len(clusters) == 1
    story = clusters[0]
    assert story.article_count == 3
    assert story.publisher_count == 3
    assert story.is_major is True
    assert story.score > 0.6


def test_opposite_actions_are_not_merged(
    repository: Repository,
    article_factory: Callable[..., ParsedArticle],
) -> None:
    repository.insert_articles(
        [
            article_factory(
                title="Bank of England raises interest rates to 4%",
                url="https://example.com/raise",
                categories=("markets-energy",),
            ),
            article_factory(
                title="Bank of England cuts interest rates to 4%",
                url="https://example.com/cut",
                categories=("markets-energy",),
                published_at=NOW - timedelta(hours=1),
            ),
        ]
    )

    clusters = build_story_clusters(repository.get_articles().items, now=NOW, config=CURATION)

    assert len(clusters) == 2


def test_same_publisher_feeds_count_as_one_publisher(
    repository: Repository,
    sources: list[SourceConfig],
    article_factory: Callable[..., ParsedArticle],
) -> None:
    repository.sync_sources(
        [
            *sources,
            replace(
                sources[0],
                id="markets-second-feed",
                feed_url="https://feeds.example.com/markets-2.xml",
            ),
        ]
    )
    repository.insert_articles(
        [
            article_factory(
                title="Electricity prices jump after supply warning",
                url="https://markets.example.com/one",
                source_id="markets",
                categories=("markets-energy",),
            ),
            article_factory(
                title="Electricity prices jump after supply warning",
                url="https://markets.example.com/two",
                source_id="markets-second-feed",
                source_name="Markets Second",
                categories=("markets-energy",),
                published_at=NOW - timedelta(minutes=10),
            ),
        ]
    )

    clusters = build_story_clusters(repository.get_articles().items, now=NOW, config=CURATION)

    assert len(clusters) == 1
    assert clusters[0].article_count == 2
    assert clusters[0].publisher_count == 1


def test_unrelated_titles_with_generic_overlap_stay_separate(
    repository: Repository,
    article_factory: Callable[..., ParsedArticle],
) -> None:
    repository.insert_articles(
        [
            article_factory(
                title="UK government announces new housing plan",
                url="https://example.com/housing",
                categories=("general",),
            ),
            article_factory(
                title="UK government announces new energy plan",
                url="https://example.com/energy",
                categories=("general",),
                published_at=NOW - timedelta(hours=1),
            ),
        ]
    )

    clusters = build_story_clusters(repository.get_articles().items, now=NOW, config=CURATION)

    assert len(clusters) == 2


def test_title_tokens_normalise_plurals_and_aliases() -> None:
    tokens = title_tokens("BOE rate decisions and AI models")
    assert {
        "bank",
        "england",
        "rate",
        "decision",
        "artificial",
        "intelligence",
        "model",
    } <= tokens


def test_briefing_uses_persisted_story_ids(
    repository: Repository,
    article_factory: Callable[..., ParsedArticle],
) -> None:
    repository.insert_articles(
        [
            article_factory(
                title="Bank of England raises interest rates",
                url="https://example.com/rates",
                categories=("markets-energy",),
                published_at=NOW - timedelta(hours=1),
            )
        ]
    )

    briefing = repository.get_briefing(now=NOW)

    assert briefing.lead is not None
    assert briefing.lead.id > 0
    assert briefing.lead.representative.story_id == briefing.lead.id
    assert repository.get_story_clusters(now=NOW)[0].story_id == briefing.lead.id


def test_briefing_deduplicates_stories_across_sections(
    repository: Repository,
    article_factory: Callable[..., ParsedArticle],
) -> None:
    repository.insert_articles(
        [
            article_factory(
                title=f"Markets story {index} about power prices",
                url=f"https://example.com/markets-{index}",
                source_id="markets",
                categories=("markets-energy",),
                published_at=NOW - timedelta(hours=index + 1),
            )
            for index in range(8)
        ]
    )
    briefing = repository.get_briefing(now=NOW)
    chosen = [
        *([briefing.lead] if briefing.lead else []),
        *briefing.featured,
        *[story for items in briefing.sections.values() for story in items],
        *briefing.latest,
    ]
    assert len({story.id for story in chosen}) == len(chosen)
    assert briefing.used_story_ids


def test_selection_avoids_duplicate_featured_publishers(
    article_factory: Callable[..., ParsedArticle],
) -> None:
    def record(article_id: int, title: str, publisher: str) -> ArticleRecord:
        parsed = article_factory(
            title=title,
            url=f"https://example.com/{article_id}",
            categories=("general",),
        )
        return ArticleRecord(
            id=article_id,
            title=parsed.title,
            url=parsed.url,
            canonical_url=parsed.canonical_url,
            source_id=parsed.source_id,
            source_name=parsed.source_name,
            summary=parsed.summary,
            published_at=parsed.published_at,
            fetched_at=parsed.fetched_at,
            first_seen_at=parsed.fetched_at,
            categories=parsed.categories,
            kind=parsed.kind,
            published_at_estimated=False,
            primary_category=parsed.primary_category,
            publisher_group=publisher,
            source_reliability=0.8,
        )

    stories = [
        StoryCluster(
            title=f"Story {index}",
            summary="",
            categories=("general",),
            primary_category="general",
            representative=record(index, f"Story {index}", "same-publisher"),
            article_ids=(index,),
            article_count=1,
            publisher_count=1,
            first_published_at=NOW,
            last_published_at=NOW - timedelta(hours=index),
            significance=0.5,
            recency=1.0,
            score=0.5 - index * 0.01,
            is_major=False,
            story_id=index,
        )
        for index in range(1, 5)
    ]
    selection = select_briefing(stories, now=NOW, config=CURATION)

    assert selection.lead is not None
    assert selection.featured == ()
