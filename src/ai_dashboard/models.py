"""Shared domain models used by ingestion, storage and the API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ParsedArticle:
    dedupe_key: str
    title: str
    url: str
    canonical_url: str
    source_id: str
    source_name: str
    summary: str
    published_at: datetime
    fetched_at: datetime
    categories: tuple[str, ...]
    kind: str
    published_at_estimated: bool = False
    primary_category: str = "general"
    image_url: str = ""


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    id: int
    title: str
    url: str
    canonical_url: str
    source_id: str
    source_name: str
    summary: str
    published_at: datetime
    fetched_at: datetime
    first_seen_at: datetime
    categories: tuple[str, ...]
    kind: str
    published_at_estimated: bool
    primary_category: str = "general"
    image_url: str = ""
    story_id: int | None = None
    publisher_group: str = ""
    source_reliability: float = 0.8


@dataclass(frozen=True, slots=True)
class StoryRecord:
    id: int
    title: str
    summary: str
    categories: tuple[str, ...]
    primary_category: str
    representative: ArticleRecord
    article_ids: tuple[int, ...]
    article_count: int
    publisher_count: int
    first_published_at: datetime
    last_published_at: datetime
    significance: float
    recency: float
    score: float
    is_major: bool


@dataclass(frozen=True, slots=True)
class SourceRecord:
    id: str
    name: str
    url: str
    feed_url: str
    categories: tuple[str, ...]
    enabled: bool
    last_fetch_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    publisher_group: str = ""
    reliability: float = 0.8


@dataclass(frozen=True, slots=True)
class RefreshRun:
    id: int
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    sources_ok: int
    sources_failed: int
    sources_not_modified: int
    articles_added: int
    articles_updated: int
    error_summary: str | None


@dataclass(frozen=True, slots=True)
class ArticlePage:
    items: tuple[ArticleRecord, ...]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(frozen=True, slots=True)
class DashboardStats:
    total_articles: int
    category_counts: dict[str, int]
    enabled_sources: int
    healthy_sources: int
    last_refresh: RefreshRun | None
    last_run: RefreshRun | None


@dataclass(frozen=True, slots=True)
class BriefingData:
    lead: StoryRecord | None
    featured: tuple[StoryRecord, ...]
    sections: dict[str, tuple[StoryRecord, ...]]
    latest: tuple[StoryRecord, ...]
    stats: DashboardStats
    generated_at: datetime
    used_story_ids: frozenset[int] = field(default_factory=frozenset)
