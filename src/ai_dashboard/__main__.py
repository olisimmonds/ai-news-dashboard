"""Command-line entry point."""

from __future__ import annotations

import logging

import uvicorn

from .config import Settings
from .main import create_app


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(f"Meridian listening on http://{settings.host}:{settings.port}")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
