"""News source configuration loaded from TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import ARTICLE_KINDS, CATEGORY_ORDER

_TWO_PART_SUFFIXES = {
    "co.uk",
    "com.au",
    "co.jp",
    "co.nz",
    "com.br",
    "com.cn",
    "co.in",
    "org.uk",
    "gov.uk",
    "ac.uk",
}

# Reliability is deliberately coarse and editable here. Publisher grouping is
# inferred from the homepage domain so multiple feeds from one outlet count as
# one independent publisher.
SOURCE_RELIABILITY: dict[str, float] = {
    # Official / primary sources
    "bank-of-england": 0.98,
    "ecb": 0.98,
    "federal-reserve": 0.98,
    "ofgem": 0.98,
    "desnz": 0.98,
    "elexon": 0.98,
    "eia-today": 0.96,
    "openai": 0.95,
    "deepmind": 0.95,
    "google-ai": 0.95,
    "microsoft-research": 0.95,
    "nvidia": 0.94,
    "huggingface": 0.92,
    "arxiv-ai": 0.92,
    "bair": 0.92,
    "nature": 0.97,
    # Established newsrooms
    "bbc-business": 0.92,
    "bbc-news": 0.92,
    "bbc-uk": 0.92,
    "bbc-world": 0.92,
    "bbc-science": 0.92,
    "ft-home": 0.95,
    "ft-companies": 0.95,
    "guardian-business": 0.92,
    "guardian-uk": 0.92,
    "guardian-world": 0.92,
    "economist-finance": 0.94,
    "economist-business": 0.94,
    "cnbc-markets": 0.88,
    "cnbc-energy": 0.88,
    "marketwatch": 0.86,
    "dow-jones-world": 0.9,
    "sky-news": 0.88,
    "sky-politics": 0.88,
    "npr": 0.9,
    "al-jazeera": 0.86,
    "politico-europe": 0.88,
    "mit-tech-review": 0.9,
    "ars-technica": 0.86,
    "the-verge": 0.84,
    "techcrunch": 0.82,
    "wired": 0.86,
    "carbon-brief": 0.9,
    "utility-dive": 0.82,
}

DEFAULT_RELIABILITY = 0.8


def _site_host(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower().split("@")[-1].split(":")[0]
    except ValueError:
        return ""
    return host.removeprefix("www.")


def publisher_group_for(url: str, fallback: str) -> str:
    host = _site_host(url) or _site_host(fallback)
    if not host:
        return fallback
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    suffix = ".".join(labels[-2:])
    if suffix in _TWO_PART_SUFFIXES:
        return ".".join(labels[-3:])
    return suffix


@dataclass(frozen=True, slots=True)
class SourceConfig:
    id: str
    name: str
    url: str
    feed_url: str
    categories: tuple[str, ...]
    enabled: bool = True
    default_kind: str | None = None
    publisher_group: str = ""
    reliability: float = DEFAULT_RELIABILITY

    def validate(self) -> None:
        if not self.id or not self.name or not self.feed_url:
            raise ValueError("Source is missing id, name or feed_url")
        if not self.feed_url.startswith(("http://", "https://")):
            raise ValueError(f"Source {self.id} has an invalid feed URL")
        unknown = set(self.categories) - set(CATEGORY_ORDER)
        if unknown:
            raise ValueError(f"Source {self.id} has unknown categories: {sorted(unknown)}")
        if not self.categories:
            raise ValueError(f"Source {self.id} has no categories")
        if self.default_kind and self.default_kind not in ARTICLE_KINDS:
            raise ValueError(f"Source {self.id} has an unknown default_kind")
        if not 0 <= self.reliability <= 1:
            raise ValueError(f"Source {self.id} has an invalid reliability")


def load_sources(path: Path) -> list[SourceConfig]:
    """Load and validate sources from a TOML file."""
    if not path.exists():
        raise FileNotFoundError(f"News source configuration not found: {path}")

    with path.open("rb") as handle:
        payload = tomllib.load(handle)

    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError(f"No sources configured in {path}")

    sources: list[SourceConfig] = []
    seen_ids: set[str] = set()
    for raw in raw_sources:
        source = SourceConfig(
            id=str(raw["id"]).strip(),
            name=str(raw["name"]).strip(),
            url=str(raw.get("url", "")).strip(),
            feed_url=str(raw["feed_url"]).strip(),
            categories=tuple(str(category).strip() for category in raw.get("categories", [])),
            enabled=bool(raw.get("enabled", True)),
            default_kind=str(raw["default_kind"]) if raw.get("default_kind") else None,
            publisher_group=str(raw.get("publisher_group", "")).strip()
            or publisher_group_for(str(raw.get("url", "")), str(raw["feed_url"])),
            reliability=float(
                raw.get("reliability", SOURCE_RELIABILITY.get(str(raw["id"]), DEFAULT_RELIABILITY))
            ),
        )
        source.validate()
        if source.id in seen_ids:
            raise ValueError(f"Duplicate source id: {source.id}")
        seen_ids.add(source.id)
        sources.append(source)

    return sources
