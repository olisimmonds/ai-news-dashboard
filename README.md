# Meridian

A quiet, low-maintenance news dashboard for markets, AI and the wider world. Backend: FastAPI, SQLite and feedparser. Frontend: React, Vite and TypeScript.

Meridian is designed to run comfortably on a Raspberry Pi 4 with 4 GB RAM. It uses no paid APIs, credentials or third-party hosted services.

## What it does

- Fetches public RSS/Atom feeds on a configurable schedule.
- Groups related reports into persistent stories and ranks them by independent publisher coverage, source reliability, recency, category confidence and conflict checks.
- Surfaces one lead story, a diverse featured set, desk sections and the latest reports.
- Extracts feed images first, then article-specific Open Graph/Twitter images. Images are loaded lazily and failures are hidden rather than shown as broken placeholders.
- Shows compact current weather for London and Winchester from Open-Meteo.
- Provides global headline/description search and per-desk filtering.
- Keeps read state locally in the browser. Saved articles were intentionally removed.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm (only needed to build the frontend)

## Run locally

```powershell
uv sync
uv run ai-dashboard
```

Open <http://localhost:8000>. The scheduler starts automatically and the first refresh may take a little while.

For frontend development, run the API and Vite in separate terminals:

```powershell
uv run ai-dashboard
```

```powershell
cd frontend
npm install
npm run dev
```

Vite runs at <http://localhost:5173> and proxies `/api` to port 8000.

To serve the built frontend from FastAPI:

```powershell
cd frontend
npm run build
cd ..
uv run ai-dashboard
```

## Configuration

Settings use the `AI_DASHBOARD_` environment prefix and can also be placed in a local `.env` file. Never commit `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_DASHBOARD_DATABASE_PATH` | `data/meridian.db` | SQLite database |
| `AI_DASHBOARD_SOURCES_FILE` | `config/news_sources.toml` | Feed configuration |
| `AI_DASHBOARD_STATIC_DIR` | `frontend/dist` | Built frontend |
| `AI_DASHBOARD_HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address |
| `AI_DASHBOARD_REFRESH_INTERVAL_MINUTES` | `30` | Scheduled refresh interval |
| `AI_DASHBOARD_RETENTION_DAYS` | `21` | Maximum article age |
| `AI_DASHBOARD_MAX_ARTICLES` | `2000` | Maximum retained articles |
| `AI_DASHBOARD_IMAGE_ENRICHMENT_ENABLED` | `true` | Fetch Open Graph fallbacks |
| `AI_DASHBOARD_IMAGE_ENRICHMENT_LIMIT` | `18` | Images fetched per refresh |
| `AI_DASHBOARD_WEATHER_ENABLED` | `true` | Enable Open-Meteo weather |
| `AI_DASHBOARD_SCHEDULER_ENABLED` | `true` | Enable background refresh |

Edit `config/news_sources.toml` to add, remove or disable feeds. Sources require a stable public `feed_url` and at least one category: `markets-energy`, `ai-technology` or `general`.

## API

- `GET /api/briefing` - lead, featured stories, desk sections and latest
- `GET /api/articles` - paginated archive with `q`, `category` and `sort`
- `GET /api/articles/{id}` - article detail
- `GET /api/meta` - categories, sources, counts and refresh state
- `GET /api/weather` - London and Winchester conditions
- `POST /api/refresh` - request a cooldown-limited refresh
- `GET /api/health` - health check

## Story ranking

Clustering and ranking are deliberately centralized in `src/ai_dashboard/curation.py`. A story is built from articles with compatible categories, publication times and title similarity. Conflicting actions (for example raising versus cutting a rate) block a merge, and multiple feeds from the same publisher count as one independent publisher. Story scores consider coverage breadth, publisher reliability, recency and category confidence. The lead and featured set use publisher diversity to avoid repeating one outlet.

## Images and weather

Feed metadata (`media:content`, `media:thumbnail`, enclosures and image links) is preferred. If a feed provides no usable image, the article page is fetched in the background for an Open Graph/Twitter image. Only image URLs are stored. Logos, placeholders, SVG and ICO files are rejected. Some publishers do not expose article images or block automated page requests; those cards simply render without an image.

Weather comes from Open-Meteo's free forecast API, uses `Europe/London` for local dates, and is cached for 12 minutes. If the service is unavailable, the weather strip is omitted.

## Rail disruption

Rail disruption is not implemented. A free, reliable, non-fragile source that does not require credentials or brittle scraping has not been confirmed.

## Tests and checks

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

```powershell
cd frontend
npm test
npm run lint
npm run typecheck
npm run format
npm run build
```

## Notes

- Data is stored locally in SQLite with WAL enabled. The database and `.env` are ignored by Git.
- The frontend uses `frontend/.npmrc` with `legacy-peer-deps=true` because of the current React 19 testing-library peer range.
- The app performs no deployment automatically. Verify locally before installing on a Raspberry Pi.
