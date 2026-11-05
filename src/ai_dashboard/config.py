"""Application settings and shared constants."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CATEGORY_LABELS: dict[str, str] = {
    "markets-energy": "Markets & Energy",
    "ai-technology": "AI & Technology",
    "general": "General News",
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "markets-energy": "Markets, macroeconomics, energy and the grid.",
    "ai-technology": "Models, research, chips, infrastructure and enterprise tech.",
    "general": "UK and international news, politics, science and the world.",
}

CATEGORY_ORDER = ("markets-energy", "ai-technology", "general")

ARTICLE_KINDS = ("major", "research", "opinion", "company", "routine")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_DASHBOARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Meridian"
    database_path: Path = PROJECT_ROOT / "data" / "meridian.db"
    sources_file: Path = PROJECT_ROOT / "config" / "news_sources.toml"
    static_dir: Path = PROJECT_ROOT / "frontend" / "dist"

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    refresh_interval_minutes: int = Field(default=30, ge=5, le=360)
    retention_days: int = Field(default=21, ge=1, le=365)
    max_articles: int = Field(default=2000, ge=100, le=100_000)
    refresh_cooldown_seconds: int = Field(default=60, ge=0, le=3600)

    request_timeout_seconds: float = Field(default=20.0, ge=5, le=120)
    max_concurrent_fetches: int = Field(default=6, ge=1, le=20)

    image_enrichment_enabled: bool = True
    image_enrichment_limit: int = Field(default=18, ge=0, le=100)
    image_enrichment_timeout_seconds: float = Field(default=5.0, ge=2, le=20)
    image_enrichment_retry_days: int = Field(default=7, ge=1, le=90)

    weather_enabled: bool = True
    weather_cache_minutes: int = Field(default=12, ge=5, le=120)

    scheduler_enabled: bool = True
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
