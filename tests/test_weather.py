from __future__ import annotations

import httpx
import pytest

from ai_dashboard.config import Settings
from ai_dashboard.weather import WeatherService


def payload(temperature: float = 9.4) -> dict[str, object]:
    return {
        "current": {
            "time": "2026-11-03T12:00",
            "temperature_2m": temperature,
            "apparent_temperature": temperature - 1.2,
            "weather_code": 61,
        },
        "daily": {
            "time": ["2026-11-03"],
            "temperature_2m_max": [12.1],
            "temperature_2m_min": [6.3],
            "precipitation_probability_max": [70],
        },
    }


@pytest.mark.asyncio
async def test_weather_service_returns_both_locations(settings: Settings) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["timezone"] == "Europe/London"
        assert request.url.params["forecast_days"] == "1"
        return httpx.Response(200, json=payload())

    service = WeatherService(
        settings.model_copy(update={"weather_enabled": True}),
        transport=httpx.MockTransport(handler),
    )

    readings = await service.get_weather()

    assert [reading.name for reading in readings] == ["London", "Winchester"]
    assert all(reading.available for reading in readings)
    london = readings[0]
    assert london.temperature == 9.4
    assert (london.high, london.low, london.rain_chance) == (12.1, 6.3, 70)
    assert london.condition == "Light rain"
    assert calls == 2


@pytest.mark.asyncio
async def test_weather_service_caches_results(settings: Settings) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=payload())

    service = WeatherService(
        settings.model_copy(update={"weather_enabled": True}),
        transport=httpx.MockTransport(handler),
    )

    await service.get_weather()
    await service.get_weather()

    assert calls == 2


@pytest.mark.asyncio
async def test_weather_failure_is_isolated_per_location(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        latitude = float(request.url.params["latitude"])
        if latitude < 51.5:
            return httpx.Response(503)
        return httpx.Response(200, json=payload())

    service = WeatherService(
        settings.model_copy(update={"weather_enabled": True}),
        transport=httpx.MockTransport(handler),
    )

    readings = await service.get_weather()

    assert readings[0].available is True
    assert readings[1].available is False
    assert readings[1].error == "Weather unavailable"
    assert readings[1].temperature is None


@pytest.mark.asyncio
async def test_weather_can_be_disabled(settings: Settings) -> None:
    service = WeatherService(settings.model_copy(update={"weather_enabled": False}))
    assert await service.get_weather() == ()
