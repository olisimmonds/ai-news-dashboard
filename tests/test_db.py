from __future__ import annotations

import sqlite3
from pathlib import Path

from ai_dashboard.db import initialise

LEGACY_SCHEMA = """
CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    feed_url TEXT NOT NULL UNIQUE,
    categories TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    etag TEXT,
    last_modified TEXT,
    last_fetch_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(id),
    source_name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    categories TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'routine',
    published_at_estimated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    sources_ok INTEGER NOT NULL DEFAULT 0,
    sources_failed INTEGER NOT NULL DEFAULT 0,
    sources_not_modified INTEGER NOT NULL DEFAULT 0,
    articles_added INTEGER NOT NULL DEFAULT 0,
    articles_updated INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);
"""


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def test_initialise_upgrades_legacy_database_without_losing_data(tmp_path: Path) -> None:
    path = tmp_path / "meridian.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_SCHEMA)
        connection.execute(
            """
            INSERT INTO sources (id, name, url, feed_url, categories)
            VALUES ('legacy', 'Legacy Wire', 'https://legacy.test/', 'https://legacy.test/feed', '["general"]')
            """
        )
        connection.execute(
            """
            INSERT INTO articles (
                dedupe_key, title, url, canonical_url, source_id, source_name,
                published_at, fetched_at, first_seen_at, last_seen_at, categories
            )
            VALUES (
                'legacy-key', 'Legacy headline', 'https://legacy.test/story',
                'https://legacy.test/story', 'legacy', 'Legacy Wire',
                '2026-11-03T10:00:00+00:00', '2026-11-03T10:05:00+00:00',
                '2026-11-03T10:05:00+00:00', '2026-11-03T10:05:00+00:00', '["general"]'
            )
            """
        )

    initialise(path)
    initialise(path)

    assert {"primary_category", "image_url", "image_checked_at", "story_id"} <= _columns(
        path, "articles"
    )
    assert {"publisher_group", "reliability"} <= _columns(path, "sources")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM stories").fetchone() == (0,)
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    assert "idx_articles_story" in indexes
