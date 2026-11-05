"""Text, URL and timestamp normalisation helpers."""

from __future__ import annotations

import hashlib
import html
import re
from calendar import timegm
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "dclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "ref_src",
    "cmpid",
    "ocid",
    "ns_mchannel",
    "ns_source",
    "at_medium",
    "at_campaign",
    "guccounter",
    "output",
    "ito",
    "smid",
    "soc_src",
    "soc_trk",
}

_WHITESPACE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    _SKIP_TAGS: ClassVar[set[str]] = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "blockquote"}:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "blockquote"}:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def clean_text(value: str | None, limit: int = 600) -> str:
    """Strip markup, decode entities and collapse whitespace."""
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = parser.text()
    except Exception:
        text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    text = _WHITESPACE.sub(" ", html.unescape(text)).strip()
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    if " " in truncated[limit // 2 :]:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip(" ,.;:") + "…"


def clean_title(value: str | None) -> str:
    return clean_text(value, limit=300)


def canonicalise_url(url: str) -> str:
    """Return a stable URL used for deduplication.

    Removes tracking parameters, fragments, default ports and a leading www.
    The original URL is always preserved separately for the reader.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return url.strip()

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(":80") and parts.scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and parts.scheme == "https":
        host = host[:-4]

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMETERS and not key.lower().startswith(("utm_", "syn-"))
    ]
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def dedupe_key(canonical_url: str, title: str, source_id: str) -> str:
    basis = canonical_url or f"{source_id}:{title.lower().strip()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def entry_datetime(
    entry: dict[str, object],
    now: datetime | None = None,
) -> tuple[datetime, bool]:
    """Extract a UTC publication time, falling back to now when needed.

    Returns ``(timestamp, estimated)``. Future timestamps (common in malformed
    feeds) are clamped to now and flagged as estimated.
    """
    now = now or datetime.now(UTC)
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                timestamp = timegm(tuple(parsed)[:6])  # type: ignore[arg-type]
                value = datetime.fromtimestamp(timestamp, tz=UTC)
            except (TypeError, ValueError, OverflowError):
                continue
            if value > now + timedelta(hours=6):
                return now, True
            return value, False
    return now, True


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
