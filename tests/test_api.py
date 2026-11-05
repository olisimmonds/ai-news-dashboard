from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_dashboard.config import Settings
from ai_dashboard.main import create_app
from ai_dashboard.repository import Repository
from ai_dashboard.sources import SourceConfig
from tests.conftest import rss


def ok_handler(request: httpx.Request) -> httpx.Response:
    slug = request.url.path.rsplit("/", 1)[-1].replace(".xml", "")
    return httpx.Response(
        200,
        content=rss(
            [
                {"title": f"{slug} story one", "link": f"https://example.com/{slug}/1"},
                {"title": f"{slug} story two", "link": f"https://example.com/{slug}/2"},
            ]
        ),
    )


def wait_for_refresh(client: TestClient, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        meta = client.get("/api/meta").json()
        if not meta["is_refreshing"] and meta["stats"]["last_refresh"] is not None:
            return meta
        time.sleep(0.05)
    pytest.fail("Refresh did not complete in time")


def test_empty_database_endpoints(
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    with make_client(ok_handler) as client:
        articles = client.get("/api/articles")
        assert articles.status_code == 200
        payload = articles.json()
        assert payload["items"] == []
        assert payload["total"] == 0
        assert payload["has_more"] is False

        briefing = client.get("/api/briefing")
        assert briefing.status_code == 200
        data = briefing.json()
        assert data["lead"] is None
        assert data["featured"] == []

        meta = client.get("/api/meta")
        assert meta.status_code == 200
        meta_data = meta.json()
        assert meta_data["stats"]["total_articles"] == 0
        assert len(meta_data["categories"]) == 3
        assert len(meta_data["sources"]) == 3

        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"


def test_frontend_placeholder_when_not_built(
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    with make_client(ok_handler) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "backend is running" in response.text


def test_article_list_filters_and_search(
    seeded_repository: Repository,
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    with make_client(ok_handler) as client:
        markets = client.get("/api/articles", params={"category": "markets-energy"}).json()
        assert markets["total"] == 2
        assert all("markets-energy" in item["categories"] for item in markets["items"])

        search = client.get("/api/articles", params={"q": "battery"}).json()
        assert search["total"] == 1
        assert "Battery" in search["items"][0]["title"]

        source = client.get("/api/articles", params={"source_id": "ai"}).json()
        assert source["total"] == 1
        assert source["items"][0]["source_name"] == "AI Wire"

        oldest = client.get("/api/articles", params={"sort": "oldest"}).json()
        assert oldest["items"][0]["title"].startswith("Battery storage")

        paged = client.get("/api/articles", params={"limit": 2, "offset": 2}).json()
        assert len(paged["items"]) == 2
        assert paged["has_more"] is False


def test_article_by_id_and_not_found(
    seeded_repository: Repository,
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    article_id = seeded_repository.get_articles().items[0].id
    with make_client(ok_handler) as client:
        found = client.get(f"/api/articles/{article_id}")
        assert found.status_code == 200
        assert found.json()["id"] == article_id

        missing = client.get("/api/articles/99999")
        assert missing.status_code == 404


def test_briefing_endpoint(
    seeded_repository: Repository,
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    with make_client(ok_handler) as client:
        response = client.get("/api/briefing")
        assert response.status_code == 200
        data = response.json()
        assert data["lead"]["title"] in {
            "Electricity prices jump as gas supply tightens",
            "OpenAI releases a new agentic model",
        }
        assert data["lead"]["story_id"] > 0
        assert data["lead"]["publisher_count"] == 1
        assert data["lead"]["story_article_count"] == 1
        assert data["lead"]["image_url"] == ""
        assert len(data["sections"]) == 3
        assert data["stats"]["total_articles"] == 4


def test_invalid_requests(
    seeded_repository: Repository,
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    with make_client(ok_handler) as client:
        assert client.get("/api/articles", params={"category": "sport"}).status_code == 422
        assert client.get("/api/articles", params={"limit": 101}).status_code == 422
        assert client.get("/api/articles", params={"offset": -1}).status_code == 422
        bad_ids = client.get("/api/articles", params={"ids": "1,two"})
        assert bad_ids.status_code == 400

        assert client.get("/api/unknown-endpoint").status_code == 404


def test_database_error_returns_503(
    seeded_repository: Repository,
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    with make_client(ok_handler) as client:

        def fail(**_kwargs: object) -> None:
            raise sqlite3.OperationalError("database locked")

        client.app.state.repository.get_articles = fail  # type: ignore[attr-defined]
        response = client.get("/api/articles")
        assert response.status_code == 503


def test_manual_refresh_updates_articles(
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    with make_client(ok_handler) as client:
        response = client.post("/api/refresh")
        assert response.status_code == 202
        assert response.json()["status"] == "started"

        meta = wait_for_refresh(client)
        assert meta["stats"]["total_articles"] == 6
        assert meta["stats"]["last_refresh"]["status"] == "success"
        assert meta["stats"]["healthy_sources"] == 3

        articles = client.get("/api/articles", params={"limit": 10}).json()
        assert articles["total"] == 6


def test_manual_refresh_cooldown(
    settings: Settings,
    sources: list[SourceConfig],
) -> None:
    cooled = settings.model_copy(update={"refresh_cooldown_seconds": 120})
    app = create_app(cooled, sources, transport=httpx.MockTransport(ok_handler))
    with TestClient(app) as client:
        assert client.post("/api/refresh").status_code == 202
        wait_for_refresh(client)
        second = client.post("/api/refresh")
        assert second.status_code == 429
        assert "Try again" in second.json()["detail"]


def test_source_health_reported_in_meta(
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "ai" in request.url.path:
            return httpx.Response(403)
        return ok_handler(request)

    with make_client(handler) as client:
        client.post("/api/refresh")
        meta = wait_for_refresh(client)
        sources = {source["id"]: source for source in meta["sources"]}
        assert sources["markets"]["healthy"] is True
        assert sources["ai"]["healthy"] is False
        assert sources["ai"]["last_error"]


def test_weather_endpoint(
    make_client: Callable[[Callable[[httpx.Request], httpx.Response]], TestClient],
) -> None:
    def weather_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.open-meteo.com"
        return httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-11-03T12:00",
                    "temperature_2m": 9.4,
                    "apparent_temperature": 8.2,
                    "weather_code": 3,
                },
                "daily": {
                    "time": ["2026-11-03"],
                    "temperature_2m_max": [12.1],
                    "temperature_2m_min": [6.3],
                    "precipitation_probability_max": [30],
                },
            },
        )

    with make_client(ok_handler, weather_handler) as client:
        response = client.get("/api/weather")
        assert response.status_code == 200
        data = response.json()
        assert [location["name"] for location in data["locations"]] == [
            "London",
            "Winchester",
        ]
        assert data["locations"][0]["condition"] == "Overcast"
        assert data["locations"][0]["high"] == 12.1
