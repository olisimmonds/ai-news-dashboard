"""SQLite connection management and schema migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

TABLES = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    feed_url TEXT NOT NULL UNIQUE,
    categories TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    publisher_group TEXT NOT NULL DEFAULT '',
    reliability REAL NOT NULL DEFAULT 0.8,
    etag TEXT,
    last_modified TEXT,
    last_fetch_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS articles (
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
    primary_category TEXT NOT NULL DEFAULT 'general',
    kind TEXT NOT NULL DEFAULT 'routine',
    published_at_estimated INTEGER NOT NULL DEFAULT 0,
    image_url TEXT NOT NULL DEFAULT '',
    image_checked_at TEXT,
    story_id INTEGER
);

CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    categories TEXT NOT NULL,
    primary_category TEXT NOT NULL DEFAULT 'general',
    representative_article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    article_ids TEXT NOT NULL DEFAULT '[]',
    article_count INTEGER NOT NULL,
    publisher_count INTEGER NOT NULL,
    first_published_at TEXT NOT NULL,
    last_published_at TEXT NOT NULL,
    significance REAL NOT NULL,
    recency REAL NOT NULL,
    score REAL NOT NULL,
    is_major INTEGER NOT NULL DEFAULT 0,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_runs (
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

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_kind ON articles(kind, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_story ON articles(story_id);
CREATE INDEX IF NOT EXISTS idx_stories_score ON stories(score DESC, last_published_at DESC);
CREATE INDEX IF NOT EXISTS idx_refresh_runs_started ON refresh_runs(started_at DESC);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    """Open a tuned SQLite connection. Callers own the connection."""
    connection = sqlite3.connect(database_path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def session(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


_SOURCE_COLUMNS = {
    "publisher_group": "TEXT NOT NULL DEFAULT ''",
    "reliability": "REAL NOT NULL DEFAULT 0.8",
}

_ARTICLE_COLUMNS = {
    "primary_category": "TEXT NOT NULL DEFAULT 'general'",
    "image_url": "TEXT NOT NULL DEFAULT ''",
    "image_checked_at": "TEXT",
    "story_id": "INTEGER",
}


def _ensure_columns(connection: sqlite3.Connection) -> None:
    for table, columns in (("sources", _SOURCE_COLUMNS), ("articles", _ARTICLE_COLUMNS)):
        existing = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def initialise(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with session(database_path) as connection:
        connection.executescript(TABLES)
        _ensure_columns(connection)
        connection.executescript(INDEXES)
