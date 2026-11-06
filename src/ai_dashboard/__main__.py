"""Command-line entry point."""

from __future__ import annotations

import logging

import uvicorn

from .config import Settings
from .main import create_app


def _browse_host(host: str) -> str:
    if host in {"0.0.0.0", "::", ""}:
        return "localhost"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(f"Open Meridian at http://{_browse_host(settings.host)}:{settings.port}")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
