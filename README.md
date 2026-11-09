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
- Includes an optional private remote-browser page: Firefox runs on the Pi in an isolated container and is rendered to phones through an authenticated noVNC proxy.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm (only needed to build the frontend)
- Docker Engine on the Raspberry Pi for the optional private browser

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
| `AI_DASHBOARD_HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address |
| `AI_DASHBOARD_CORS_ORIGINS` | local Vite origins | Additional allowed browser origins |
| `AI_DASHBOARD_REFRESH_INTERVAL_MINUTES` | `30` | Scheduled refresh interval |
| `AI_DASHBOARD_RETENTION_DAYS` | `21` | Maximum article age |
| `AI_DASHBOARD_MAX_ARTICLES` | `2000` | Maximum retained articles |
| `AI_DASHBOARD_IMAGE_ENRICHMENT_ENABLED` | `true` | Fetch Open Graph fallbacks |
| `AI_DASHBOARD_IMAGE_ENRICHMENT_LIMIT` | `18` | Images fetched per refresh |
| `AI_DASHBOARD_WEATHER_ENABLED` | `true` | Enable Open-Meteo weather |
| `AI_DASHBOARD_BROWSER_ENABLED` | `true` | Enable the private remote browser |
| `AI_DASHBOARD_BROWSER_USERS_FILE` | `config/browser_users.json` | Local browser accounts |
| `AI_DASHBOARD_BROWSER_STATE_FILE` | `data/browser_state.json` | Per-user browser favourites |
| `AI_DASHBOARD_BROWSER_DIAGNOSTICS_DIR` | `data/browser-diagnostics` | Persisted browser failure diagnostics |
| `AI_DASHBOARD_BROWSER_SECRET` | generated | Cookie-signing secret |
| `AI_DASHBOARD_BROWSER_COOKIE_SECURE` | `false` | Set true when served over HTTPS |
| `AI_DASHBOARD_BROWSER_SESSION_TTL_HOURS` | `12` | Browser sign-in lifetime |
| `AI_DASHBOARD_BROWSER_IDLE_TIMEOUT_MINUTES` | `20` | Idle session shutdown |
| `AI_DASHBOARD_BROWSER_MAX_SESSIONS` | `2` | Global concurrent Firefox containers |
| `AI_DASHBOARD_BROWSER_IMAGE` | `meridian-firefox:latest` | Browser container image |
| `AI_DASHBOARD_BROWSER_MEMORY_LIMIT` | `1200m` | Memory limit per Firefox container |
| `AI_DASHBOARD_BROWSER_CPUS` | `1.5` | CPU limit per Firefox container |
| `AI_DASHBOARD_BROWSER_COOKIE_NAME` | `meridian_browser` | Authentication cookie name |
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
- `GET /api/browser/status` - browser configuration, authentication and session state
- `POST /api/browser/auth/login` / `POST /api/browser/auth/logout` - browser account session
- `GET|PUT /api/browser/favourites` - per-user saved places
- `POST /api/browser/sessions` - start or reuse the user's Firefox container
- `POST /api/browser/sessions/{id}/navigate|action|restart` - drive Firefox through xdotool
- `DELETE /api/browser/sessions/{id}` - close the container

## Story ranking

Clustering and ranking are deliberately centralized in `src/ai_dashboard/curation.py`. A story is built from articles with compatible categories, publication times and title similarity. Conflicting actions (for example raising versus cutting a rate) block a merge, and multiple feeds from the same publisher count as one independent publisher. Story scores consider coverage breadth, publisher reliability, recency and category confidence. The lead and featured set use publisher diversity to avoid repeating one outlet.

## Images and weather

Feed metadata (`media:content`, `media:thumbnail`, enclosures and image links) is preferred. If a feed provides no usable image, the article page is fetched in the background for an Open Graph/Twitter image. Only image URLs are stored. Logos, placeholders, SVG and ICO files are rejected. Some publishers do not expose article images or block automated page requests; those cards simply render without an image.

Weather comes from Open-Meteo's free forecast API, uses `Europe/London` for local dates, and is cached for 12 minutes. If the service is unavailable, the weather strip is omitted.

## Private browser

The private browser is deliberately separate from the news database. A phone opens Meridian over Tailscale, signs in with a local browser account, and receives a noVNC display from a Firefox container on the Pi. Keyboard and pointer input travel back through the same authenticated WebSocket.

### How it is isolated

- Each user gets one Firefox container and one named Docker volume for the browser profile.
- Firefox and the VNC server run as the unprivileged `browser` user inside the container.
- Containers use a private Docker network, no host filesystem mounts other than the profile volume, `no-new-privileges`, all capabilities dropped except the minimum needed to prepare the profile and drop privileges, PID/memory/CPU limits and a localhost-only published port.
- Meridian's database, source configuration and the rest of the home directory are not mounted into the browser.
- noVNC is served on a separate container port. Meridian proxies it after authentication; the host mapping is bound only to `127.0.0.1` on a random port and is never exposed on the network.
- Browser accounts use scrypt password hashes. Sessions use signed, HttpOnly cookies and are closed on sign-out.
- Idle sessions are reaped after `AI_DASHBOARD_BROWSER_IDLE_TIMEOUT_MINUTES`; at most `AI_DASHBOARD_BROWSER_MAX_SESSIONS` containers run at once.
- Firefox force-installs uBlock Origin, blocks popups, enables tracking protection, disables telemetry, password storage and app updates.
- Firefox is capped at two content processes and is configured not to restore tabs after a crash, reducing memory pressure on the Pi.
- The container asks Firefox to quit, then falls back to SIGTERM and a bounded wait before killing it, so a normal restart writes a clean profile. If the previous run ended uncleanly, only the session-restore files are moved aside into the profile volume; bookmarks, history, cookies, logins and favourites are kept. This prevents the Firefox "Sorry. We're having trouble getting your pages back." screen from trapping the browser after an OOM kill or forced restart.
- Crash reporter minidumps, cgroup memory counters, process lists and session-file sizes are written under `/home/browser/.meridian/diagnostics` inside the profile volume. Meridian also saves Docker state, limits, OOM flag and logs to `data/browser-diagnostics` on the host.
- The container has a Docker health check for Xvnc, openbox, websockify and Firefox. The startup supervisor restarts a dead or unresponsive websockify/openbox process and exits the container if Xvnc or Firefox disappears.
- Meridian retries a failed display request, then stops and restarts the container in place when the display is genuinely stale. The named profile volume is retained.
- There is no phone-side file transfer, host folder mount or clipboard permission granted to the embedded display. Downloads remain inside the user's browser volume.

### Limitations

- This is not a security boundary against a malicious website: the site still runs on the Pi and can reach the network available to the container.
- Do not expose Meridian's port to the public internet. Bind it to localhost or the Tailscale interface, or put it behind Tailscale Serve. Never add a router port-forward.
- Access to the Docker socket is effectively root-equivalent on the Pi. Only the Meridian service account should hold that permission.
- A local process on the Pi that can reach loopback can connect to a running container's random VNC port and bypass Meridian's login. Treat shell access to the Pi as trusted.
- Remote rendering is functional, not native. Video, audio, high-DPI scrolling and some keyboard shortcuts may behave differently from a local browser.
- Each Firefox container defaults to 1.2 GB and 1.5 CPUs. Two sessions plus Meridian and the operating system fit a 4 GB Pi 4, but leave little headroom. If `AI_DASHBOARD_BROWSER_MEMORY_LIMIT` is explicitly set in `.env`, that value overrides the default.
- Browser profiles persist in named Docker volumes. Delete the relevant `meridian-browser-home-*` volume to reset a profile.

### Install on the Raspberry Pi

Run these steps on the Pi. They are not run automatically by Meridian.

```bash
# 1. Install Docker Engine, then allow the Meridian service account to use it.
sudo usermod -aG docker "$USER"
# Log out and back in (or run newgrp docker) so the group change applies.

# 2. Build the ARM64 Firefox image from this repository.
docker build -t meridian-firefox:latest docker/firefox
# Rebuild after changing anything under docker/firefox; the startup scripts are copied into the image.

# 3. Install Python dependencies and create a local browser account.
uv sync
uv run python -m ai_dashboard.browser.auth set-user yourname

# 4. Rebuild the frontend and start Meridian.
cd frontend
npm ci
npm run build
cd ..
uv run ai-dashboard
```

Use `uv run python -m ai_dashboard.browser.auth list-users` to list accounts and `remove-user <name>` to delete one. Account and favourites files are ignored by Git. Removing an account does not erase its Firefox profile volume or its entry in `data/browser_state.json`; delete both to remove that user's browsing data completely.

For a persistent service, adapt `deploy/meridian.service` to your username and checkout path. The unit defaults to loopback. If you need direct Tailnet access instead of Tailscale Serve, set `AI_DASHBOARD_HOST` to the Pi's Tailscale address in `.env`.

```bash
sudo cp deploy/meridian.service /etc/systemd/system/meridian.service
sudo systemctl daemon-reload
sudo systemctl enable --now meridian
```

### Access and manual test

1. Confirm the Pi and phone are both on the same Tailnet and MagicDNS resolves the Pi.
2. Bind Meridian to the Pi's Tailscale address, or keep it on `127.0.0.1` behind Tailscale Serve, then open the browser page from the phone. With an HTTPS frontend, set `AI_DASHBOARD_BROWSER_COOKIE_SECURE=true`.
3. Sign in, open a favourite and verify the noVNC display appears.
4. Use Back, Forward, Reload, Home and New tab. Type in the address bar and press Enter.
5. Start a second session from a different account, then confirm a third is rejected at the configured limit.
6. Leave a session idle until the timeout and confirm the container is removed with `docker ps`.
7. Close the session and confirm the profile volume remains with `docker volume ls`.

The image can also be smoke-tested directly; this is for local diagnosis only and has no Meridian authentication. Keep the container after it starts so its logs remain available:

```bash
docker rm -f meridian-firefox-smoke >/dev/null 2>&1 || true
docker run --detach --name meridian-firefox-smoke --shm-size=512m \
  -p 127.0.0.1:6080:6080 meridian-firefox:latest

curl --fail --silent --show-error --output /dev/null \
  http://127.0.0.1:6080/vnc_lite.html && echo "noVNC OK"
docker logs --tail 200 --timestamps meridian-firefox-smoke
```

Open <http://127.0.0.1:6080/vnc_lite.html> to confirm Firefox is visible. A 200 from noVNC only proves that websockify is serving; it does not prove Firefox started. Meridian also waits for a visible Firefox window and logs the container state plus its last 200 log lines before cleaning up a failed session. On the Pi, check those diagnostics with `journalctl -u meridian -n 200 --no-pager`.

### Display disconnects

`The browser display is not responding` is Meridian's proxy error: the container's noVNC/websockify endpoint did not answer. It does not by itself prove Firefox crashed. A browser that still accepts navigation but cannot serve noVNC usually has a dead or wedged websockify process; a container that has disappeared or was OOM-killed also produces the same panel.

Meridian now logs the Docker state, OOM flag, health status, memory/CPU usage, process list and the last 200 container log lines when the display fails. It also retries the request and restarts the container in place if the display does not recover. The profile volume is preserved.

Inspect the exact failure on the Pi with:

```bash
journalctl -u meridian -n 300 --no-pager
docker ps -a --filter label=meridian.browser.session=1 \
  --format 'name={{.Names}} status={{.Status}} image={{.Image}}'
docker inspect --format \
  'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
  <container-name>
docker top <container-name> -eo pid,comm,args
docker stats --no-stream <container-name>
docker logs --tail 200 --timestamps <container-name>
```

If `oom=True`, Firefox or another display process exhausted the container memory limit. Lower the number of simultaneous sessions or adjust `AI_DASHBOARD_BROWSER_MEMORY_LIMIT` deliberately, then restart the session. If the process list lacks `websockify`, the supervisor should have logged a websockify restart; if it lacks `Xvnc` or Firefox, the container was intentionally stopped and Meridian will recreate it from the same profile volume.

Meridian invokes the Docker CLI directly; it does not use the Docker Python SDK. Failed commands now log their full command line, stdout, stderr and exit status. To test the X window lookup manually:

```bash
docker ps --filter label=meridian.browser.session=1 --format '{{.Names}}'
docker exec --user 1000 --env XAUTHORITY=/home/browser/.Xauthority <container-name> \
  xdotool search --onlyvisible --limit 1 'meridian-browser|MeridianBrowser|Firefox'
```

```bash
docker rm -f meridian-firefox-smoke
```

### Apply the crash-recovery fix on the Pi

```bash
cd /home/pi/ai-news-dashboard
git pull
docker build -t meridian-firefox:latest docker/firefox
sudo systemctl restart meridian
```

The existing `meridian-browser-home-*` volumes are reused. If a browser is currently stuck on the Firefox recovery page, close the session from Meridian or stop the container once; the next start quarantines the stale session files and opens a clean window without touching bookmarks or favourites.

If the problem recurs, collect these files before removing any container:

```bash
journalctl -u meridian -n 300 --no-pager
ls -lt data/browser-diagnostics/
docker ps -a --filter label=meridian.browser.session=1 \
  --format 'name={{.Names}} status={{.Status}} image={{.Image}}'
docker inspect --format \
  'exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} memory={{.HostConfig.Memory}} shm={{.HostConfig.ShmSize}} stop={{.Config.StopTimeout}}' \
  <container-name>
docker logs --tail 200 --timestamps <container-name>
docker exec <container-name> cat /sys/fs/cgroup/memory.events
docker run --rm --volumes-from <container-name> --entrypoint sh meridian-firefox:latest \
  -c 'ls -l /home/browser/.meridian/diagnostics && find /home/browser/.mozilla -maxdepth 3 \( -name "sessionstore*" -o -name sessionCheckpoints.json \) -print'
```

`oom_kill 1` or `oom=True` is evidence that the memory limit was hit; do not raise the limit without that evidence. Check the newest `data/browser-diagnostics/*.log` first because it contains the container state, limits, OOM flag, cgroup counters and logs captured before Meridian removed the container.

Do not expose that port beyond localhost. Rebuild the image after changing anything under `docker/firefox`; the startup scripts are copied into the image.

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

- Data is stored locally in SQLite with WAL enabled. The database, browser accounts, browser profile state and `.env` are ignored by Git.
- The frontend uses `frontend/.npmrc` with `legacy-peer-deps=true` because of the current React 19 testing-library peer range.
- The app performs no deployment automatically. Verify locally before installing on a Raspberry Pi.
