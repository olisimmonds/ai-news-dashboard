"""Compact Open-Meteo weather for London and Winchester."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_USER_AGENT = "MeridianNewsDashboard/1.0 (+https://github.com/olisimmonds/ai-news-dashboard)"

LOCATIONS: tuple[tuple[str, str, float, float], ...] = (
    ("london", "London", 51.5072, -0.1276),
    ("winchester", "Winchester", 51.0632, -1.3080),
)

_WEATHER_CODES: dict[int, str] = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


@dataclass(frozen=True, slots=True)
class WeatherReading:
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
    error: str | None = None


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), 1)
    return None


def _int(value: object) -> int | None:
    if isinstance(value, (int, float)):
        return round(float(value))
    return None


def _parse_location(
    location_id: str,
    name: str,
    payload: dict[str, object],
) -> WeatherReading:
    current = payload.get("current")
    daily = payload.get("daily")
    current_data = current if isinstance(current, dict) else {}
    daily_data = daily if isinstance(daily, dict) else {}
    code = _int(current_data.get("weather_code"))

    def daily_value(key: str) -> object:
        values = daily_data.get(key)
        if isinstance(values, list) and values:
            return values[0]
        return None

    return WeatherReading(
        id=location_id,
        name=name,
        available=True,
        observed_at=str(current_data.get("time")) if current_data.get("time") else None,
        local_date=str(daily_value("time")) if daily_value("time") else None,
        temperature=_number(current_data.get("temperature_2m")),
        feels_like=_number(current_data.get("apparent_temperature")),
        condition=_WEATHER_CODES.get(code, "Mixed conditions") if code is not None else None,
        weather_code=code,
        high=_number(daily_value("temperature_2m_max")),
        low=_number(daily_value("temperature_2m_min")),
        rain_chance=_int(daily_value("precipitation_probability_max")),
    )


class WeatherService:
    """Small in-memory cache with graceful per-location failure handling."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._lock = asyncio.Lock()
        self._cached_at: float = 0.0
        self._cache_ttl: float = 0.0
        self._cache: tuple[WeatherReading, ...] = ()

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        location_id: str,
        name: str,
        latitude: float,
        longitude: float,
    ) -> WeatherReading:
        try:
            response = await client.get(
                OPEN_METEO_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,apparent_temperature,weather_code",
                    "daily": (
                        "temperature_2m_max,temperature_2m_min,precipitation_probability_max"
                    ),
                    "timezone": "Europe/London",
                    "forecast_days": 1,
                },
                timeout=8.0,
                headers={"User-Agent": WEATHER_USER_AGENT, "Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("unexpected weather response")
            return _parse_location(location_id, name, payload)
        except Exception as exc:
            logger.debug("Weather lookup failed for %s: %s", location_id, exc)
            return WeatherReading(
                id=location_id,
                name=name,
                available=False,
                observed_at=None,
                local_date=None,
                temperature=None,
                feels_like=None,
                condition=None,
                weather_code=None,
                high=None,
                low=None,
                rain_chance=None,
                error="Weather unavailable",
            )

    async def get_weather(self) -> tuple[WeatherReading, ...]:
        if not self.settings.weather_enabled:
            return ()
        now = time.monotonic()
        if self._cache and now < self._cached_at + self._cache_ttl:
            return self._cache

        async with self._lock:
            now = time.monotonic()
            if self._cache and now < self._cached_at + self._cache_ttl:
                return self._cache
            async with httpx.AsyncClient(transport=self._transport) as client:
                readings = tuple(
                    await asyncio.gather(
                        *(
                            self._fetch_one(client, location_id, name, latitude, longitude)
                            for location_id, name, latitude, longitude in LOCATIONS
                        )
                    )
                )
            self._cache = readings
            self._cached_at = time.monotonic()
            all_failed = all(not reading.available for reading in readings)
            self._cache_ttl = 120.0 if all_failed else self.settings.weather_cache_minutes * 60
            return readings


def weather_generated_at() -> datetime:
    return datetime.now(UTC)
