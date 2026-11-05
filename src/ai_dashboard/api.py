"""HTTP API for the Meridian dashboard."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from .config import CATEGORY_DESCRIPTIONS, CATEGORY_LABELS, CATEGORY_ORDER, Settings
from .models import (
    ArticlePage,
    ArticleRecord,
    BriefingData,
    DashboardStats,
    RefreshRun,
    SourceRecord,
    StoryRecord,
)
from .repository import Repository
from .weather import WeatherReading, weather_generated_at

router = APIRouter(prefix="/api")

CategoryId = Literal["markets-energy", "ai-technology", "general"]
SortOrder = Literal["newest", "oldest"]


class ArticleOut(BaseModel):
    id: int
    title: str
    url: str
    source_id: str
    source_name: str
    summary: str
    published_at: datetime
    fetched_at: datetime
    categories: list[str]
    primary_category: str
    kind: str
    published_at_estimated: bool
    image_url: str
    story_id: int | None = None
    publisher_count: int | None = None
    story_article_count: int | None = None
    score: float | None = None
    is_major: bool = False


class ArticleListOut(BaseModel):
    items: list[ArticleOut]
    total: int
    limit: int
    offset: int
    has_more: bool


class CategoryOut(BaseModel):
    id: str
    label: str
    description: str
    count: int


class SourceOut(BaseModel):
    id: str
    name: str
    url: str
    feed_url: str
    categories: list[str]
    enabled: bool
    last_fetch_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    healthy: bool


class RefreshRunOut(BaseModel):
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


class StatsOut(BaseModel):
    total_articles: int
    category_counts: dict[str, int]
    enabled_sources: int
    healthy_sources: int
    last_refresh: RefreshRunOut | None
    last_run: RefreshRunOut | None


class MetaOut(BaseModel):
    app_name: str
    generated_at: datetime
    categories: list[CategoryOut]
    sources: list[SourceOut]
    stats: StatsOut
    is_refreshing: bool
    next_refresh_at: datetime | None
    refresh_interval_minutes: int
    retention_days: int


class StoryOut(ArticleOut):
    story_id: int
    publisher_count: int
    story_article_count: int
    score: float
    is_major: bool


class BriefingOut(BaseModel):
    lead: StoryOut | None
    featured: list[StoryOut]
    sections: dict[str, list[StoryOut]]
    latest: list[StoryOut]
    stats: StatsOut
    generated_at: datetime


class RefreshRequestOut(BaseModel):
    status: str
    message: str


class HealthOut(BaseModel):
    status: str
    app: str
    database: str
    articles: int
    time: datetime


class WeatherLocationOut(BaseModel):
    id: str
    name: str
    available: bool
    observed_at: str | None
    local_date: str | None
    temperature: float | None
    feels_like: float | None
    condition: str | None
    weather_code: int | None
    high: float | None
    low: float | None
    rain_chance: int | None
    error: str | None


class WeatherOut(BaseModel):
    locations: list[WeatherLocationOut]
    generated_at: datetime


def _article_out(article: ArticleRecord) -> ArticleOut:
    return ArticleOut(
        id=article.id,
        title=article.title,
        url=article.url,
        source_id=article.source_id,
        source_name=article.source_name,
        summary=article.summary,
        published_at=article.published_at,
        fetched_at=article.fetched_at,
        categories=list(article.categories),
        primary_category=article.primary_category,
        kind=article.kind,
        published_at_estimated=article.published_at_estimated,
        image_url=article.image_url,
        story_id=article.story_id,
    )


def _story_out(story: StoryRecord) -> StoryOut:
    article = _article_out(story.representative)
    return StoryOut(
        **article.model_dump(
            exclude={"story_id", "publisher_count", "story_article_count", "score", "is_major"}
        ),
        story_id=story.id,
        publisher_count=story.publisher_count,
        story_article_count=story.article_count,
        score=round(story.score, 4),
        is_major=story.is_major,
    )


def _run_out(run: RefreshRun | None) -> RefreshRunOut | None:
    if run is None:
        return None
    return RefreshRunOut(
        id=run.id,
        trigger=run.trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        sources_ok=run.sources_ok,
        sources_failed=run.sources_failed,
        sources_not_modified=run.sources_not_modified,
        articles_added=run.articles_added,
        articles_updated=run.articles_updated,
        error_summary=run.error_summary,
    )


def _stats_out(stats: DashboardStats) -> StatsOut:
    return StatsOut(
        total_articles=stats.total_articles,
        category_counts=stats.category_counts,
        enabled_sources=stats.enabled_sources,
        healthy_sources=stats.healthy_sources,
        last_refresh=_run_out(stats.last_refresh),
        last_run=_run_out(stats.last_run),
    )


def _source_out(source: SourceRecord) -> SourceOut:
    return SourceOut(
        id=source.id,
        name=source.name,
        url=source.url,
        feed_url=source.feed_url,
        categories=list(source.categories),
        enabled=source.enabled,
        last_fetch_at=source.last_fetch_at,
        last_success_at=source.last_success_at,
        last_error=source.last_error,
        consecutive_failures=source.consecutive_failures,
        healthy=source.last_success_at is not None and (source.consecutive_failures == 0),
    )


def _briefing_out(briefing: BriefingData) -> BriefingOut:
    return BriefingOut(
        lead=_story_out(briefing.lead) if briefing.lead else None,
        featured=[_story_out(story) for story in briefing.featured],
        sections={
            category: [_story_out(story) for story in items]
            for category, items in briefing.sections.items()
        },
        latest=[_story_out(story) for story in briefing.latest],
        stats=_stats_out(briefing.stats),
        generated_at=briefing.generated_at,
    )


def _weather_out(reading: WeatherReading) -> WeatherLocationOut:
    return WeatherLocationOut(
        id=reading.id,
        name=reading.name,
        available=reading.available,
        observed_at=reading.observed_at,
        local_date=reading.local_date,
        temperature=reading.temperature,
        feels_like=reading.feels_like,
        condition=reading.condition,
        weather_code=reading.weather_code,
        high=reading.high,
        low=reading.low,
        rain_chance=reading.rain_chance,
        error=reading.error,
    )


def _repository(request: Request) -> Repository:
    return request.app.state.repository  # type: ignore[no-any-return]


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    repository = _repository(request)
    return HealthOut(
        status="ok",
        app=_settings(request).app_name,
        database="ok",
        articles=repository.count_articles(),
        time=datetime.now(UTC),
    )


@router.get("/meta", response_model=MetaOut)
async def meta(request: Request) -> MetaOut:
    repository = _repository(request)
    settings = _settings(request)
    stats = repository.get_stats()
    sources = repository.get_sources()
    return MetaOut(
        app_name=settings.app_name,
        generated_at=datetime.now(UTC),
        categories=[
            CategoryOut(
                id=category,
                label=CATEGORY_LABELS[category],
                description=CATEGORY_DESCRIPTIONS[category],
                count=stats.category_counts.get(category, 0),
            )
            for category in CATEGORY_ORDER
        ],
        sources=[_source_out(source) for source in sources],
        stats=_stats_out(stats),
        is_refreshing=request.app.state.ingestion.is_refreshing,
        next_refresh_at=request.app.state.ingestion.next_refresh_at,
        refresh_interval_minutes=settings.refresh_interval_minutes,
        retention_days=settings.retention_days,
    )


@router.get("/weather", response_model=WeatherOut)
async def weather(request: Request) -> WeatherOut:
    readings = await request.app.state.weather.get_weather()
    return WeatherOut(
        locations=[_weather_out(reading) for reading in readings],
        generated_at=weather_generated_at(),
    )


@router.get("/articles", response_model=ArticleListOut)
async def list_articles(
    request: Request,
    category: Annotated[CategoryId | None, Query()] = None,
    source_id: Annotated[str | None, Query(max_length=100)] = None,
    kind: Annotated[str | None, Query(max_length=30)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    ids: Annotated[str | None, Query(max_length=2000)] = None,
    sort: Annotated[SortOrder, Query()] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ArticleListOut:
    parsed_ids: list[int] | None = None
    if ids:
        try:
            parsed_ids = [int(value) for value in ids.split(",") if value.strip()][:100]
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="ids must be comma-separated integers"
            ) from exc

    page: ArticlePage = _repository(request).get_articles(
        category=category,
        source_id=source_id,
        kind=kind,
        query=q.strip() if q else None,
        ids=parsed_ids,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return ArticleListOut(
        items=[_article_out(article) for article in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )


@router.get("/articles/{article_id}", response_model=ArticleOut)
async def get_article(request: Request, article_id: int) -> ArticleOut:
    article = _repository(request).get_article(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return _article_out(article)


@router.get("/briefing", response_model=BriefingOut)
async def briefing(request: Request) -> BriefingOut:
    return _briefing_out(_repository(request).get_briefing())


@router.post("/refresh", response_model=RefreshRequestOut, status_code=202)
async def trigger_refresh(request: Request) -> RefreshRequestOut:
    repository = _repository(request)
    ingestion = request.app.state.ingestion
    settings = _settings(request)

    if ingestion.is_refreshing:
        raise HTTPException(status_code=409, detail="A refresh is already running")

    last_run = repository.get_last_run()
    if last_run and settings.refresh_cooldown_seconds:
        age = (datetime.now(UTC) - last_run.started_at).total_seconds()
        if age < settings.refresh_cooldown_seconds:
            wait = int(settings.refresh_cooldown_seconds - age) + 1
            raise HTTPException(
                status_code=429,
                detail=f"News was refreshed moments ago. Try again in {wait} seconds.",
            )

    task = asyncio.create_task(ingestion.refresh(trigger="manual"), name="meridian-manual-refresh")
    request.app.state.background_tasks.add(task)
    task.add_done_callback(request.app.state.background_tasks.discard)
    return RefreshRequestOut(status="started", message="Refreshing news sources")


@router.get("/debug/sources")
async def debug_sources(request: Request) -> list[SourceOut]:
    """Small diagnostic view of source health, useful when a feed changes."""
    return [_source_out(source) for source in _repository(request).get_sources()]


def register_exception_handlers(app: FastAPI) -> None:
    """Register a friendly handler for database failures."""
    from fastapi.responses import JSONResponse

    @app.exception_handler(sqlite3.Error)
    async def _database_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "The article database is temporarily unavailable."},
        )
