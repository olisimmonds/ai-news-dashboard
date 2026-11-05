"""Deterministic story clustering, scoring and briefing selection."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .config import CATEGORY_ORDER
from .curation import CurationConfig
from .models import ArticleRecord

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# Generic newsroom words are removed before comparing titles. Company names,
# places, products and event nouns are deliberately retained.
_STOPWORDS = {
    "a",
    "about",
    "after",
    "again",
    "against",
    "all",
    "also",
    "amid",
    "an",
    "and",
    "announce",
    "announced",
    "announces",
    "annual",
    "are",
    "as",
    "at",
    "be",
    "been",
    "before",
    "big",
    "breaking",
    "business",
    "by",
    "can",
    "company",
    "companies",
    "could",
    "day",
    "days",
    "deal",
    "deals",
    "did",
    "do",
    "does",
    "during",
    "exclusive",
    "first",
    "for",
    "from",
    "get",
    "gets",
    "global",
    "government",
    "has",
    "have",
    "he",
    "her",
    "here",
    "his",
    "how",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "latest",
    "launch",
    "launched",
    "launches",
    "less",
    "live",
    "major",
    "market",
    "markets",
    "may",
    "minister",
    "ministers",
    "more",
    "most",
    "new",
    "news",
    "not",
    "now",
    "of",
    "official",
    "officials",
    "on",
    "one",
    "or",
    "other",
    "our",
    "out",
    "over",
    "own",
    "plan",
    "plans",
    "report",
    "reported",
    "reports",
    "reveal",
    "revealed",
    "reveals",
    "said",
    "says",
    "second",
    "set",
    "sets",
    "share",
    "shares",
    "she",
    "show",
    "shows",
    "so",
    "some",
    "story",
    "than",
    "that",
    "the",
    "their",
    "them",
    "there",
    "they",
    "third",
    "this",
    "through",
    "to",
    "today",
    "top",
    "two",
    "under",
    "update",
    "updated",
    "updates",
    "up",
    "us",
    "was",
    "we",
    "week",
    "weeks",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "world",
    "would",
    "year",
    "years",
    "you",
    "your",
}

_CONFLICT_GROUPS = (
    {"rise", "fall", "surge", "plunge", "jump", "drop", "soar", "slump"},
    {"raise", "cut", "hold", "pause", "hike", "lift", "reduce"},
    {"win", "lose", "victory", "defeat"},
    {"approve", "reject", "block", "allow"},
    {"open", "close", "start", "stop", "resume", "suspend"},
    {"expand", "shrink", "grow", "contract"},
    {"buy", "sell", "acquire", "divest"},
)

_ALIASES = {
    "boe": ("bank", "england"),
    "fed": ("federal", "reserve"),
    "ecb": ("european", "central", "bank"),
    "ai": ("artificial", "intelligence"),
    "llm": ("large", "language", "model"),
    "llms": ("large", "language", "model"),
}


@dataclass(frozen=True, slots=True)
class StoryCluster:
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
    story_id: int = 0


@dataclass(frozen=True, slots=True)
class BriefingSelection:
    lead: StoryCluster | None
    featured: tuple[StoryCluster, ...]
    sections: dict[str, tuple[StoryCluster, ...]]
    latest: tuple[StoryCluster, ...]
    used_story_ids: frozenset[int]


def _normalise_plural(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if (
        len(token) > 3
        and token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
        and token not in {"bias", "lens"}
    ):
        return token[:-1]
    return token


def title_tokens(title: str) -> frozenset[str]:
    """Return meaningful title tokens, with light plural and alias handling."""
    normalised = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    tokens: set[str] = set()
    for raw in _TOKEN_SPLIT.split(normalised.lower()):
        if not raw or raw in _STOPWORDS:
            continue
        token = _normalise_plural(raw)
        if token in _STOPWORDS or len(token) < 2:
            continue
        tokens.add(token)
        for alias in _ALIASES.get(token, ()):
            if alias not in _STOPWORDS:
                tokens.add(_normalise_plural(alias))
    return frozenset(tokens)


def _normalised_title(title: str) -> str:
    return " ".join(_TOKEN_SPLIT.split(unicodedata.normalize("NFKD", title).lower())).strip()


def same_story(
    left: ArticleRecord,
    right: ArticleRecord,
    *,
    config: CurationConfig,
) -> bool:
    """Conservatively decide whether two articles cover the same event."""
    if abs((right.published_at - left.published_at).total_seconds()) > (
        config.max_story_span_hours * 3600
    ):
        return False

    if not set(left.categories) & set(right.categories):
        return False

    if _normalised_title(left.title) == _normalised_title(right.title):
        return True

    left_tokens = title_tokens(left.title)
    right_tokens = title_tokens(right.title)
    if len(left_tokens) < config.min_title_tokens or len(right_tokens) < config.min_title_tokens:
        return False

    shared = left_tokens & right_tokens
    if len(shared) < config.min_shared_title_tokens:
        return False

    for group in _CONFLICT_GROUPS:
        left_actions = left_tokens & group
        right_actions = right_tokens & group
        if left_actions and right_actions and not (left_actions & right_actions):
            return False

    union = left_tokens | right_tokens
    jaccard = len(shared) / len(union)
    containment = len(shared) / min(len(left_tokens), len(right_tokens))

    if jaccard >= config.title_jaccard_threshold:
        return True
    return (
        len(shared) >= config.title_containment_min_shared
        and containment >= config.title_containment_threshold
        and jaccard >= 0.28
    )


def _coverage(publisher_count: int, config: CurationConfig) -> float:
    if publisher_count <= 1:
        return 0.0
    return min(
        1.0,
        math.log1p(publisher_count - 1) / math.log1p(config.coverage_full_publishers),
    )


def _score(
    *,
    publisher_count: int,
    reliability: float,
    category_consistency: float,
    kind: str,
    latest: datetime,
    now: datetime,
    config: CurationConfig,
) -> tuple[float, float, float, bool]:
    coverage = _coverage(publisher_count, config)
    relevance = 0.75 + 0.25 * max(0.0, min(1.0, category_consistency))
    kind_signal = config.kind_signals.get(kind, config.kind_signals["routine"])
    significance = (
        coverage * config.coverage_weight
        + max(0.0, min(1.0, reliability)) * config.reliability_weight
        + relevance * config.relevance_weight
        + kind_signal * config.kind_weight
    )
    hours_old = max(0.0, (now - latest).total_seconds() / 3600)
    recency = math.exp(-math.log(2) * hours_old / config.recency_half_life_hours)
    score = significance * (config.recency_floor + (1 - config.recency_floor) * recency)
    is_major = score >= config.major_threshold and hours_old <= config.major_max_age_hours
    return significance, recency, score, is_major


def _representative(articles: list[ArticleRecord]) -> ArticleRecord:
    return max(
        articles,
        key=lambda article: (article.source_reliability, article.published_at),
    )


def _category_consistency(articles: list[ArticleRecord]) -> float:
    if not articles:
        return 0.0
    primary = articles[0].primary_category
    matches = sum(1 for article in articles if article.primary_category == primary)
    return matches / len(articles)


def _make_cluster(
    articles: list[ArticleRecord],
    now: datetime,
    config: CurationConfig,
) -> StoryCluster:
    articles = sorted(articles, key=lambda article: article.published_at, reverse=True)
    representative = _representative(articles)
    categories = tuple(
        category
        for category in CATEGORY_ORDER
        if any(category in article.categories for article in articles)
    )
    primary = representative.primary_category
    if primary not in categories:
        primary = categories[0] if categories else "general"
    summary = max((article.summary for article in articles), key=len, default="")
    publishers = {article.publisher_group or article.source_id for article in articles}
    reliability = max((article.source_reliability for article in articles), default=0.8)
    latest = articles[0].published_at
    significance, recency, score, is_major = _score(
        publisher_count=len(publishers),
        reliability=reliability,
        category_consistency=_category_consistency(articles),
        kind=representative.kind,
        latest=latest,
        now=now,
        config=config,
    )
    return StoryCluster(
        title=representative.title,
        summary=summary,
        categories=categories or ("general",),
        primary_category=primary,
        representative=representative,
        article_ids=tuple(sorted(article.id for article in articles)),
        article_count=len(articles),
        publisher_count=len(publishers),
        first_published_at=min(article.published_at for article in articles),
        last_published_at=latest,
        significance=significance,
        recency=recency,
        score=score,
        is_major=is_major,
    )


def build_story_clusters(
    articles: Iterable[ArticleRecord],
    *,
    now: datetime,
    config: CurationConfig,
) -> list[StoryCluster]:
    """Greedy, deterministic clustering against each cluster's first article."""
    recent = [
        article
        for article in articles
        if (now - article.published_at).total_seconds() <= config.cluster_window_hours * 3600
        and article.published_at <= now
    ]
    recent.sort(key=lambda article: (article.published_at, article.id), reverse=True)

    groups: list[list[ArticleRecord]] = []
    anchors: list[ArticleRecord] = []
    for article in recent:
        for index, anchor in enumerate(anchors):
            span = abs((article.published_at - groups[index][0].published_at).total_seconds())
            if span > config.max_story_span_hours * 3600:
                continue
            if same_story(anchor, article, config=config):
                groups[index].append(article)
                break
        else:
            groups.append([article])
            anchors.append(article)

    clusters = [_make_cluster(group, now, config) for group in groups]
    clusters.sort(key=lambda story: (story.score, story.last_published_at), reverse=True)
    return clusters


def _diverse_pick(
    candidates: list[StoryCluster],
    *,
    used: set[int],
    count: int,
    max_per_publisher: int,
    max_per_category: int | None = None,
    initial_publishers: dict[str, int] | None = None,
) -> list[StoryCluster]:
    picked: list[StoryCluster] = []
    publishers = dict(initial_publishers or {})
    categories: dict[str, int] = {}

    for story in candidates:
        if len(picked) >= count:
            break
        if story.representative.id in used:
            continue
        publisher = story.representative.publisher_group or story.representative.source_id
        if publishers.get(publisher, 0) >= max_per_publisher:
            continue
        if (
            max_per_category is not None
            and categories.get(story.primary_category, 0) >= max_per_category
        ):
            continue
        picked.append(story)
        publishers[publisher] = publishers.get(publisher, 0) + 1
        categories[story.primary_category] = categories.get(story.primary_category, 0) + 1
        used.add(story.representative.id)
    return picked


def select_briefing(
    stories: Iterable[StoryCluster],
    *,
    now: datetime,
    config: CurationConfig,
) -> BriefingSelection:
    """Choose a lead, diverse featured stories, desk sections and latest items."""
    candidates = [
        story
        for story in stories
        if (now - story.last_published_at).total_seconds() <= config.briefing_lookback_hours * 3600
        and story.last_published_at <= now
    ]
    candidates.sort(key=lambda story: (story.score, story.last_published_at), reverse=True)

    lead: StoryCluster | None = None
    for story in candidates:
        age_hours = (now - story.last_published_at).total_seconds() / 3600
        if age_hours <= config.lead_max_age_hours and story.score >= config.lead_min_score:
            lead = story
            break
    if lead is None and candidates:
        lead = candidates[0]

    used_article_ids: set[int] = set()
    used_story_ids: set[int] = set()
    if lead is not None:
        used_article_ids.update(lead.article_ids)
        used_story_ids.add(lead.story_id or lead.representative.id)

    remaining = [story for story in candidates if story.representative.id not in used_article_ids]
    lead_publisher = (
        lead.representative.publisher_group or lead.representative.source_id
        if lead is not None
        else ""
    )
    featured = _diverse_pick(
        remaining,
        used=used_article_ids,
        count=config.featured_count,
        max_per_publisher=config.featured_max_per_publisher,
        max_per_category=config.featured_max_per_category,
        initial_publishers={lead_publisher: 1} if lead_publisher else None,
    )
    used_story_ids.update(story.story_id or story.representative.id for story in featured)
    remaining = [story for story in candidates if story.representative.id not in used_article_ids]

    sections: dict[str, tuple[StoryCluster, ...]] = {}
    for category in CATEGORY_ORDER:
        category_candidates = [story for story in remaining if story.primary_category == category]
        picks = _diverse_pick(
            category_candidates,
            used=used_article_ids,
            count=config.section_count,
            max_per_publisher=config.section_max_per_publisher,
        )
        sections[category] = tuple(picks)
        used_story_ids.update(story.story_id or story.representative.id for story in picks)
        remaining = [
            story for story in remaining if story.representative.id not in used_article_ids
        ]

    latest_candidates = [
        story for story in candidates if story.representative.id not in used_article_ids
    ]
    latest_candidates.sort(key=lambda story: story.last_published_at, reverse=True)
    latest = tuple(latest_candidates[: config.latest_count])
    used_story_ids.update(story.story_id or story.representative.id for story in latest)

    return BriefingSelection(
        lead=lead,
        featured=tuple(featured),
        sections=sections,
        latest=latest,
        used_story_ids=frozenset(used_story_ids),
    )
