"""Per-user browser favourites stored outside the article database."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_FAVOURITES: tuple[tuple[str, str], ...] = (
    ("DuckDuckGo", "https://duckduckgo.com/"),
    ("Wikipedia", "https://www.wikipedia.org/"),
    ("BBC News", "https://www.bbc.co.uk/news"),
    ("Reuters", "https://www.reuters.com/"),
    ("Hacker News", "https://news.ycombinator.com/"),
)


@dataclass(frozen=True)
class Favourite:
    title: str
    url: str


class BrowserStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def get_favourites(self, username: str) -> list[Favourite]:
        with self._lock:
            data = self._read()
        stored = data.get("favourites", {}).get(username)
        if not isinstance(stored, list):
            return [Favourite(title=title, url=url) for title, url in DEFAULT_FAVOURITES]
        favourites: list[Favourite] = []
        for item in stored:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            url = item.get("url")
            if isinstance(title, str) and isinstance(url, str):
                favourites.append(Favourite(title=title, url=url))
        return favourites

    def set_favourites(self, username: str, favourites: list[Favourite]) -> list[Favourite]:
        with self._lock:
            data = self._read()
            all_favourites = data.setdefault("favourites", {})
            if not isinstance(all_favourites, dict):
                all_favourites = {}
                data["favourites"] = all_favourites
            all_favourites[username] = [
                {"title": favourite.title, "url": favourite.url} for favourite in favourites
            ]
            self._write(data)
        return favourites

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read browser state from %s: %s", self._path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self._path)
