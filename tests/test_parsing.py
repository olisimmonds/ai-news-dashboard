from datetime import UTC, datetime, timedelta

from ai_dashboard.parsing import (
    canonicalise_url,
    clean_text,
    dedupe_key,
    entry_datetime,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def test_clean_text_strips_markup_and_scripts() -> None:
    raw = "<p>Hello <strong>world</strong></p><script>steal()</script><p>Second &amp; last</p>"
    assert clean_text(raw) == "Hello world Second & last"


def test_clean_text_truncates_on_a_word_boundary() -> None:
    text = clean_text("word " * 300, limit=50)
    assert len(text) <= 51
    assert text.endswith("…")


def test_canonicalise_url_removes_tracking_and_normalises() -> None:
    raw = "https://www.Example.com/news/story/?utm_source=x&b=2&a=1#section"
    assert canonicalise_url(raw) == "https://example.com/news/story?a=1&b=2"


def test_canonicalise_url_keeps_meaningful_query() -> None:
    raw = "http://example.com/item?id=42&fbclid=abc&syn-123=1"
    assert canonicalise_url(raw) == "https://example.com/item?id=42"


def test_dedupe_key_ignores_tracking_parameters() -> None:
    first = dedupe_key(canonicalise_url("https://example.com/a?utm_source=x"), "Title", "src")
    second = dedupe_key(canonicalise_url("https://www.example.com/a/"), "Title", "src")
    assert first == second


def test_entry_datetime_uses_published_date() -> None:
    entry = {"published_parsed": (2026, 11, 2, 8, 30, 0, 0, 0, 0)}
    value, estimated = entry_datetime(entry, now=NOW)
    assert value == datetime(2026, 11, 2, 8, 30, tzinfo=UTC)
    assert estimated is False


def test_entry_datetime_falls_back_to_now() -> None:
    value, estimated = entry_datetime({}, now=NOW)
    assert value == NOW
    assert estimated is True


def test_entry_datetime_clamps_implausible_future_date() -> None:
    entry = {"published_parsed": (2027, 1, 1, 0, 0, 0, 0, 0, 0)}
    value, estimated = entry_datetime(entry, now=NOW)
    assert value == NOW
    assert estimated is True


def test_entry_datetime_handles_invalid_tuple() -> None:
    value, estimated = entry_datetime({"published_parsed": ("bad",)}, now=NOW)
    assert value == NOW
    assert estimated is True


def test_entry_datetime_accepts_updated_date() -> None:
    entry = {"updated_parsed": (2026, 11, 1, 9, 0, 0, 0, 0, 0)}
    value, estimated = entry_datetime(entry, now=NOW)
    assert value == datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert value < NOW - timedelta(hours=12)
    assert estimated is False
