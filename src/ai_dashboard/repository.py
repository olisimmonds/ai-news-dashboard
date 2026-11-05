"""SQLite-backed persistence for articles, sources, stories and refresh history."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Row

from .config import CATEGORY_ORDER
from .curation import CURATION, CurationConfig
from .db import session
from .models import (
    ArticlePage,
    ArticleRecord,
    BriefingData,
    DashboardStats,
    ParsedArticle,
    RefreshRun,
    SourceRecord,
    StoryRecord,
)
from .parsing import from_iso, to_iso
from .sources import SourceConfig, publisher_group_for
from .stories import StoryCluster, build_story_clusters, select_briefing

logger = logging.getLogger(__name__)

_KIND_PRIORITY = {"major": 5, "research": 4, "opinion": 3, "company": 2, "routine": 1}

_ARTICLE_SELECT = """
SELECT a.*, s.publisher_group AS publisher_group,
       s.reliability AS source_reliability
FROM articles a
LEFT JOIN sources s ON s.id = a.source_id
"""


def _json_categories(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ("general",)
    if not isinstance(parsed, list):
        return ("general",)
    return tuple(category for category in parsed if isinstance(category, str))


def _json_ids(value: str) -> tuple[int, ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(int(item) for item in parsed if isinstance(item, int))


def _row_value(row: Row, key: str, default: object = None) -> object:
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _article_from_row(row: Row) -> ArticleRecord:
    categories = _json_categories(str(row["categories"]))
    primary = str(_row_value(row, "primary_category", "") or "")
    if primary not in CATEGORY_ORDER:
        primary = categories[0] if categories else "general"
    story_value = _row_value(row, "story_id")
    return ArticleRecord(
        id=int(row["id"]),
        title=str(row["title"]),
        url=str(row["url"]),
        canonical_url=str(row["canonical_url"]),
        source_id=str(row["source_id"]),
        source_name=str(row["source_name"]),
        summary=str(row["summary"]),
        published_at=from_iso(str(row["published_at"])),
        fetched_at=from_iso(str(row["fetched_at"])),
        first_seen_at=from_iso(str(row["first_seen_at"])),
        categories=categories,
        kind=str(row["kind"]),
        published_at_estimated=bool(row["published_at_estimated"]),
        primary_category=primary,
        image_url=str(_row_value(row, "image_url", "") or ""),
        story_id=int(str(story_value)) if story_value is not None else None,
        publisher_group=str(_row_value(row, "publisher_group", "") or row["source_id"]),
        source_reliability=float(str(_row_value(row, "source_reliability", 0.8) or 0.8)),
    )


def _source_from_row(row: Row) -> SourceRecord:
    return SourceRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        url=str(row["url"]),
        feed_url=str(row["feed_url"]),
        categories=_json_categories(str(row["categories"])),
        enabled=bool(row["enabled"]),
        last_fetch_at=from_iso(str(row["last_fetch_at"])) if row["last_fetch_at"] else None,
        last_success_at=from_iso(str(row["last_success_at"])) if row["last_success_at"] else None,
        last_error=str(row["last_error"]) if row["last_error"] else None,
        consecutive_failures=int(row["consecutive_failures"]),
        publisher_group=str(_row_value(row, "publisher_group", "") or row["id"]),
        reliability=float(str(_row_value(row, "reliability", 0.8) or 0.8)),
    )


def _run_from_row(row: Row) -> RefreshRun:
    return RefreshRun(
        id=int(row["id"]),
        trigger=str(row["trigger"]),
        started_at=from_iso(str(row["started_at"])),
        finished_at=from_iso(str(row["finished_at"])) if row["finished_at"] else None,
        status=str(row["status"]),
        sources_ok=int(row["sources_ok"]),
        sources_failed=int(row["sources_failed"]),
        sources_not_modified=int(row["sources_not_modified"]),
        articles_added=int(row["articles_added"]),
        articles_updated=int(row["articles_updated"]),
        error_summary=str(row["error_summary"]) if row["error_summary"] else None,
    )


def _cluster_from_row(row: Row) -> StoryCluster:
    representative = _article_from_row(row)
    story_id = int(row["st_id"])
    if representative.story_id is None:
        representative = replace(representative, story_id=story_id)
    return StoryCluster(
        title=str(row["st_title"]),
        summary=str(row["st_summary"]),
        categories=_json_categories(str(row["st_categories"])),
        primary_category=str(row["st_primary_category"]),
        representative=representative,
        article_ids=_json_ids(str(row["st_article_ids"])),
        article_count=int(row["article_count"]),
        publisher_count=int(row["publisher_count"]),
        first_published_at=from_iso(str(row["first_published_at"])),
        last_published_at=from_iso(str(row["last_published_at"])),
        significance=float(row["significance"]),
        recency=float(row["recency"]),
        score=float(row["score"]),
        is_major=bool(row["is_major"]),
        story_id=story_id,
    )


def _story_record(cluster: StoryCluster) -> StoryRecord:
    return StoryRecord(
        id=cluster.story_id,
        title=cluster.title,
        summary=cluster.summary,
        categories=cluster.categories,
        primary_category=cluster.primary_category,
        representative=cluster.representative,
        article_ids=cluster.article_ids,
        article_count=cluster.article_count,
        publisher_count=cluster.publisher_count,
        first_published_at=cluster.first_published_at,
        last_published_at=cluster.last_published_at,
        significance=cluster.significance,
        recency=cluster.recency,
        score=cluster.score,
        is_major=cluster.is_major,
    )


class Repository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    # Sources ---------------------------------------------------------------

    def sync_sources(self, sources: Iterable[SourceConfig]) -> None:
        sources = list(sources)
        keep_ids = [source.id for source in sources]
        with session(self.database_path) as connection:
            for source in sources:
                connection.execute(
                    """
                    INSERT INTO sources (
                        id, name, url, feed_url, categories, enabled,
                        publisher_group, reliability
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        url = excluded.url,
                        feed_url = excluded.feed_url,
                        categories = excluded.categories,
                        enabled = excluded.enabled,
                        publisher_group = excluded.publisher_group,
                        reliability = excluded.reliability
                    """,
                    (
                        source.id,
                        source.name,
                        source.url,
                        source.feed_url,
                        json.dumps(list(source.categories)),
                        1 if source.enabled else 0,
                        source.publisher_group or publisher_group_for(source.url, source.feed_url),
                        source.reliability,
                    ),
                )
            if keep_ids:
                placeholders = ",".join("?" for _ in keep_ids)
                connection.execute(
                    f"UPDATE sources SET enabled = 0 WHERE id NOT IN ({placeholders})",
                    keep_ids,
                )

    def get_sources(self, enabled_only: bool = False) -> tuple[SourceRecord, ...]:
        query = "SELECT * FROM sources"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY name COLLATE NOCASE"
        with session(self.database_path) as connection:
            rows = connection.execute(query).fetchall()
        return tuple(_source_from_row(row) for row in rows)

    def get_source(self, source_id: str) -> SourceRecord | None:
        with session(self.database_path) as connection:
            row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _source_from_row(row) if row else None

    def source_etag(self, source_id: str) -> tuple[str | None, str | None]:
        with session(self.database_path) as connection:
            row = connection.execute(
                "SELECT etag, last_modified FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        if not row:
            return None, None
        return row["etag"], row["last_modified"]

    def record_source_success(
        self,
        source_id: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        now = to_iso(datetime.now(UTC))
        with session(self.database_path) as connection:
            connection.execute(
                """
                UPDATE sources SET
                    last_fetch_at = ?,
                    last_success_at = ?,
                    last_error = NULL,
                    consecutive_failures = 0,
                    etag = COALESCE(?, etag),
                    last_modified = COALESCE(?, last_modified)
                WHERE id = ?
                """,
                (now, now, etag, last_modified, source_id),
            )

    def record_source_failure(self, source_id: str, error: str) -> None:
        now = to_iso(datetime.now(UTC))
        with session(self.database_path) as connection:
            connection.execute(
                """
                UPDATE sources SET
                    last_fetch_at = ?,
                    last_error = ?,
                    consecutive_failures = consecutive_failures + 1
                WHERE id = ?
                """,
                (now, error[:500], source_id),
            )

    # Articles --------------------------------------------------------------

    def insert_articles(self, articles: Iterable[ParsedArticle]) -> tuple[int, int]:
        added = 0
        updated = 0
        with session(self.database_path) as connection:
            for article in articles:
                existing = connection.execute(
                    "SELECT id, title, summary, categories, primary_category, kind,"
                    " published_at, published_at_estimated, image_url"
                    " FROM articles WHERE dedupe_key = ?",
                    (article.dedupe_key,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO articles (
                            dedupe_key, title, url, canonical_url, source_id, source_name,
                            summary, published_at, fetched_at, first_seen_at, last_seen_at,
                            categories, primary_category, kind, published_at_estimated,
                            image_url
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            article.dedupe_key,
                            article.title,
                            article.url,
                            article.canonical_url,
                            article.source_id,
                            article.source_name,
                            article.summary,
                            to_iso(article.published_at),
                            to_iso(article.fetched_at),
                            to_iso(article.fetched_at),
                            to_iso(article.fetched_at),
                            json.dumps(list(article.categories)),
                            article.primary_category,
                            article.kind,
                            1 if article.published_at_estimated else 0,
                            article.image_url,
                        ),
                    )
                    added += 1
                    continue

                existing_categories = set(_json_categories(str(existing["categories"])))
                merged_categories = tuple(
                    category
                    for category in CATEGORY_ORDER
                    if category in existing_categories or category in article.categories
                )
                existing_kind = str(existing["kind"])
                kind = (
                    article.kind
                    if _KIND_PRIORITY.get(article.kind, 0) > _KIND_PRIORITY.get(existing_kind, 0)
                    else existing_kind
                )
                summary = str(existing["summary"])
                if len(article.summary) > len(summary):
                    summary = article.summary
                published_at = str(existing["published_at"])
                estimated = bool(existing["published_at_estimated"])
                if estimated and not article.published_at_estimated:
                    published_at = to_iso(article.published_at)
                    estimated = False
                primary = str(existing["primary_category"] or "")
                if not primary or primary == "general":
                    primary = article.primary_category
                image_url = str(existing["image_url"] or "") or article.image_url

                connection.execute(
                    """
                    UPDATE articles SET
                        title = ?,
                        summary = ?,
                        categories = ?,
                        primary_category = ?,
                        kind = ?,
                        published_at = ?,
                        published_at_estimated = ?,
                        image_url = ?,
                        last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        article.title or str(existing["title"]),
                        summary,
                        json.dumps(list(merged_categories)),
                        primary,
                        kind,
                        published_at,
                        1 if estimated else 0,
                        image_url,
                        to_iso(article.fetched_at),
                        int(existing["id"]),
                    ),
                )
                updated += 1
        return added, updated

    def get_article(self, article_id: int) -> ArticleRecord | None:
        with session(self.database_path) as connection:
            row = connection.execute(f"{_ARTICLE_SELECT} WHERE a.id = ?", (article_id,)).fetchone()
        return _article_from_row(row) if row else None

    def get_articles(
        self,
        *,
        category: str | None = None,
        source_id: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        ids: Iterable[int] | None = None,
        since: datetime | None = None,
        sort: str = "newest",
        limit: int = 30,
        offset: int = 0,
    ) -> ArticlePage:
        clauses: list[str] = []
        parameters: list[object] = []

        if category:
            clauses.append("instr(a.categories, ?) > 0")
            parameters.append(f'"{category}"')
        if source_id:
            clauses.append("a.source_id = ?")
            parameters.append(source_id)
        if kind:
            clauses.append("a.kind = ?")
            parameters.append(kind)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            clauses.append("(a.title LIKE ? ESCAPE '\\' OR a.summary LIKE ? ESCAPE '\\')")
            parameters.extend([pattern, pattern])
        if ids is not None:
            id_list = [int(value) for value in ids]
            if not id_list:
                return ArticlePage(items=(), total=0, limit=limit, offset=offset)
            placeholders = ",".join("?" for _ in id_list)
            clauses.append(f"a.id IN ({placeholders})")
            parameters.extend(id_list)
        if since:
            clauses.append("a.published_at >= ?")
            parameters.append(to_iso(since))

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "ASC" if sort == "oldest" else "DESC"

        with session(self.database_path) as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM articles a{where}", parameters
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"{_ARTICLE_SELECT}{where} ORDER BY a.published_at {order}, a.id {order}"
                " LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()

        return ArticlePage(
            items=tuple(_article_from_row(row) for row in rows),
            total=total,
            limit=limit,
            offset=offset,
        )

    def count_articles(self) -> int:
        with session(self.database_path) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])

    def is_empty(self) -> bool:
        return self.count_articles() == 0

    # Images ---------------------------------------------------------------

    def get_image_candidates(
        self,
        limit: int,
        *,
        retry_after_days: int = 7,
        max_age_days: int = 7,
        now: datetime | None = None,
    ) -> tuple[ArticleRecord, ...]:
        now = now or datetime.now(UTC)
        with session(self.database_path) as connection:
            rows = connection.execute(
                f"{_ARTICLE_SELECT}"
                " WHERE a.image_url = '' AND a.published_at >= ?"
                " AND (a.image_checked_at IS NULL OR a.image_checked_at < ?)"
                " ORDER BY a.published_at DESC, a.id DESC LIMIT ?",
                (
                    to_iso(now - timedelta(days=max_age_days)),
                    to_iso(now - timedelta(days=retry_after_days)),
                    limit,
                ),
            ).fetchall()
        return tuple(_article_from_row(row) for row in rows)

    def set_article_image(self, article_id: int, image_url: str) -> None:
        now = to_iso(datetime.now(UTC))
        with session(self.database_path) as connection:
            connection.execute(
                "UPDATE articles SET image_url = ?, image_checked_at = ? WHERE id = ?",
                (image_url, now, article_id),
            )

    def mark_image_checked(self, article_id: int) -> None:
        with session(self.database_path) as connection:
            connection.execute(
                "UPDATE articles SET image_checked_at = ? WHERE id = ?",
                (to_iso(datetime.now(UTC)), article_id),
            )

    # Stories --------------------------------------------------------------

    def _stories_need_rebuild(self) -> bool:
        with session(self.database_path) as connection:
            article_marker = connection.execute(
                "SELECT MAX(first_seen_at) FROM articles"
            ).fetchone()[0]
            story_marker = connection.execute("SELECT MAX(built_at) FROM stories").fetchone()[0]
        if story_marker is None:
            return True
        return bool(article_marker and str(article_marker) > str(story_marker))

    def rebuild_stories(
        self,
        now: datetime | None = None,
        config: CurationConfig = CURATION,
    ) -> int:
        now = now or datetime.now(UTC)
        recent = self.get_articles(
            since=now - timedelta(hours=config.cluster_window_hours),
            sort="newest",
            limit=1500,
        ).items
        clusters = build_story_clusters(recent, now=now, config=config)
        built_at = to_iso(now)
        with session(self.database_path) as connection:
            connection.execute("UPDATE articles SET story_id = NULL WHERE story_id IS NOT NULL")
            connection.execute("DELETE FROM stories")
            for cluster in clusters:
                cursor = connection.execute(
                    """
                    INSERT INTO stories (
                        cluster_key, title, summary, categories, primary_category,
                        representative_article_id, article_ids, article_count,
                        publisher_count, first_published_at, last_published_at,
                        significance, recency, score, is_major, built_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cluster.representative.canonical_url or cluster.representative.url,
                        cluster.title,
                        cluster.summary,
                        json.dumps(list(cluster.categories)),
                        cluster.primary_category,
                        cluster.representative.id,
                        json.dumps(list(cluster.article_ids)),
                        cluster.article_count,
                        cluster.publisher_count,
                        to_iso(cluster.first_published_at),
                        to_iso(cluster.last_published_at),
                        cluster.significance,
                        cluster.recency,
                        cluster.score,
                        1 if cluster.is_major else 0,
                        built_at,
                    ),
                )
                story_id = int(cursor.lastrowid or 0)
                if cluster.article_ids:
                    placeholders = ",".join("?" for _ in cluster.article_ids)
                    connection.execute(
                        f"UPDATE articles SET story_id = ? WHERE id IN ({placeholders})",
                        (story_id, *cluster.article_ids),
                    )
        logger.info("Built %s stories from %s recent articles", len(clusters), len(recent))
        return len(clusters)

    def get_story_clusters(
        self,
        now: datetime | None = None,
        config: CurationConfig = CURATION,
    ) -> tuple[StoryCluster, ...]:
        now = now or datetime.now(UTC)
        if self._stories_need_rebuild():
            self.rebuild_stories(now, config)
        with session(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT st.id AS st_id, st.title AS st_title, st.summary AS st_summary,
                       st.categories AS st_categories, st.primary_category AS st_primary_category,
                       st.article_ids AS st_article_ids, st.article_count, st.publisher_count,
                       st.first_published_at, st.last_published_at, st.significance,
                       st.recency, st.score, st.is_major,
                       a.*, s.publisher_group AS publisher_group,
                       s.reliability AS source_reliability
                FROM stories st
                JOIN articles a ON a.id = st.representative_article_id
                LEFT JOIN sources s ON s.id = a.source_id
                ORDER BY st.score DESC, st.last_published_at DESC
                """
            ).fetchall()
        return tuple(_cluster_from_row(row) for row in rows)

    # Refresh runs ----------------------------------------------------------

    def start_refresh_run(self, trigger: str) -> int:
        with session(self.database_path) as connection:
            cursor = connection.execute(
                "INSERT INTO refresh_runs (trigger, started_at, status) VALUES (?, ?, 'running')",
                (trigger, to_iso(datetime.now(UTC))),
            )
            return int(cursor.lastrowid or 0)

    def finish_refresh_run(
        self,
        run_id: int,
        *,
        status: str,
        sources_ok: int,
        sources_failed: int,
        sources_not_modified: int,
        articles_added: int,
        articles_updated: int,
        error_summary: str | None = None,
    ) -> None:
        with session(self.database_path) as connection:
            connection.execute(
                """
                UPDATE refresh_runs SET
                    finished_at = ?, status = ?, sources_ok = ?, sources_failed = ?,
                    sources_not_modified = ?, articles_added = ?, articles_updated = ?,
                    error_summary = ?
                WHERE id = ?
                """,
                (
                    to_iso(datetime.now(UTC)),
                    status,
                    sources_ok,
                    sources_failed,
                    sources_not_modified,
                    articles_added,
                    articles_updated,
                    error_summary[:1000] if error_summary else None,
                    run_id,
                ),
            )

    def get_last_run(self) -> RefreshRun | None:
        with session(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM refresh_runs ORDER BY started_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return _run_from_row(row) if row else None

    def get_last_successful_run(self) -> RefreshRun | None:
        with session(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM refresh_runs WHERE status IN ('success', 'partial')"
                " ORDER BY started_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return _run_from_row(row) if row else None

    # Aggregates ------------------------------------------------------------

    def get_stats(self) -> DashboardStats:
        with session(self.database_path) as connection:
            rows = connection.execute("SELECT categories FROM articles").fetchall()
            enabled_sources = int(
                connection.execute("SELECT COUNT(*) FROM sources WHERE enabled = 1").fetchone()[0]
            )
            healthy_sources = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sources WHERE enabled = 1 AND last_success_at IS NOT NULL"
                ).fetchone()[0]
            )
            total = len(rows)

        counts = {category: 0 for category in CATEGORY_ORDER}
        for row in rows:
            for category in _json_categories(str(row["categories"])):
                if category in counts:
                    counts[category] += 1

        return DashboardStats(
            total_articles=total,
            category_counts=counts,
            enabled_sources=enabled_sources,
            healthy_sources=healthy_sources,
            last_refresh=self.get_last_successful_run(),
            last_run=self.get_last_run(),
        )

    def get_briefing(
        self,
        now: datetime | None = None,
        config: CurationConfig = CURATION,
    ) -> BriefingData:
        now = now or datetime.now(UTC)
        clusters = self.get_story_clusters(now, config)
        selection = select_briefing(clusters, now=now, config=config)
        return BriefingData(
            lead=_story_record(selection.lead) if selection.lead else None,
            featured=tuple(_story_record(story) for story in selection.featured),
            sections={
                category: tuple(_story_record(story) for story in items)
                for category, items in selection.sections.items()
            },
            latest=tuple(_story_record(story) for story in selection.latest),
            stats=self.get_stats(),
            generated_at=now,
            used_story_ids=selection.used_story_ids,
        )

    # Maintenance -----------------------------------------------------------

    def apply_retention(
        self,
        retention_days: int,
        max_articles: int,
        now: datetime | None = None,
    ) -> int:
        now = now or datetime.now(UTC)
        cutoff = to_iso(now - timedelta(days=retention_days))
        with session(self.database_path) as connection:
            deleted = connection.execute(
                "DELETE FROM articles WHERE published_at < ?", (cutoff,)
            ).rowcount
            connection.execute(
                "DELETE FROM articles WHERE id NOT IN"
                " (SELECT id FROM articles ORDER BY published_at DESC, id DESC LIMIT ?)",
                (max_articles,),
            )
            connection.execute("UPDATE articles SET story_id = NULL")
            connection.execute("DELETE FROM stories")
            connection.execute(
                "DELETE FROM refresh_runs WHERE started_at < ?",
                (to_iso(now - timedelta(days=90)),),
            )
        if deleted:
            logger.info("Retention removed %s old articles", deleted)
        return int(deleted)
