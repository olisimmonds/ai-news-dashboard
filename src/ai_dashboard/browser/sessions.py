"""On-demand, per-user Firefox container orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from ai_dashboard.config import Settings

logger = logging.getLogger(__name__)

_WINDOW_SEARCH = (
    "search",
    "--onlyvisible",
    "--limit",
    "1",
    "--any",
    "--classname",
    "meridian-browser",
    "--class",
    ".*[Ff]irefox.*",
)


class BrowserError(RuntimeError):
    pass


class BrowserUnavailable(BrowserError):
    pass


class BrowserLimitReached(BrowserError):
    pass


class BrowserSessionNotFound(BrowserError):
    pass


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    volume: str
    network: str
    novnc_port: int
    memory: str
    cpus: str


class BrowserRuntime(Protocol):
    async def available(self) -> bool: ...

    async def start(self, spec: ContainerSpec) -> int: ...

    async def wait_ready(self, name: str, port: int) -> None: ...

    async def remove(self, name: str) -> None: ...

    async def restart(self, name: str) -> None: ...

    async def is_running(self, name: str) -> bool: ...

    async def run_command(self, name: str, args: list[str]) -> None: ...

    async def cleanup_orphans(self) -> None: ...


class DockerRuntime:
    def __init__(self, settings: Settings) -> None:
        self._binary = settings.browser_docker_binary
        self._network = settings.browser_network
        self._available: bool | None = None
        self._checked_at = 0.0

    async def _run(self, *args: str, timeout: float = 20.0, check: bool = True) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                self._binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise BrowserUnavailable(
                "Docker is not installed. Install Docker Engine to use the private browser."
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise BrowserUnavailable("Docker did not respond in time.") from exc

        if check and process.returncode != 0:
            message = stderr.decode(errors="replace").strip()
            raise BrowserError(message or f"Docker command failed: {' '.join(args[:2])}")
        return stdout.decode(errors="replace").strip()

    async def available(self) -> bool:
        now = time.monotonic()
        if self._available is not None and now - self._checked_at < 15:
            return self._available
        try:
            await self._run("version", "--format", "{{.Server.Version}}", timeout=5)
            self._available = True
        except BrowserError:
            self._available = False
        self._checked_at = now
        return self._available

    async def _ensure_network(self) -> None:
        try:
            await self._run("network", "inspect", self._network, timeout=10)
        except BrowserError:
            await self._run(
                "network",
                "create",
                "--opt",
                "com.docker.network.bridge.enable_icc=false",
                self._network,
                timeout=20,
            )

    async def start(self, spec: ContainerSpec) -> int:
        await self._ensure_network()
        await self._run("volume", "create", spec.volume, timeout=20)
        await self.remove(spec.name)
        await self._run(
            "run",
            "--detach",
            "--rm",
            "--init",
            "--name",
            spec.name,
            "--label",
            "meridian.browser.session=1",
            "--network",
            spec.network,
            "--security-opt",
            "no-new-privileges=true",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--pids-limit",
            "256",
            "--memory",
            spec.memory,
            "--cpus",
            spec.cpus,
            "--shm-size",
            "512m",
            "--publish",
            f"127.0.0.1::{spec.novnc_port}",
            "--volume",
            f"{spec.volume}:/home/browser",
            spec.image,
            timeout=90,
        )
        try:
            mapping = await self._run("port", spec.name, f"{spec.novnc_port}/tcp", timeout=10)
            line = mapping.splitlines()[-1]
            port = int(line.rsplit(":", 1)[-1])
        except (IndexError, ValueError) as exc:
            await self.remove(spec.name)
            raise BrowserError("Could not determine the browser container port.") from exc

        try:
            await self.wait_ready(spec.name, port)
        except BrowserUnavailable:
            await self.remove(spec.name)
            raise
        return port

    async def wait_ready(self, name: str, port: int, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/vnc_lite.html")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            else:
                raise BrowserUnavailable("The browser display did not start in time.")

        try:
            await self._run(
                "exec",
                name,
                "xdotool",
                "search",
                "--sync",
                "--onlyvisible",
                "--limit",
                "1",
                "--any",
                "--classname",
                "meridian-browser",
                "--class",
                ".*[Ff]irefox.*",
                timeout=30,
            )
        except BrowserError as exc:
            raise BrowserUnavailable("The browser window did not start in time.") from exc

    async def remove(self, name: str) -> None:
        await self._run("rm", "--force", name, timeout=20, check=False)

    async def restart(self, name: str) -> None:
        await self._run("restart", name, timeout=30)

    async def is_running(self, name: str) -> bool:
        try:
            value = await self._run(
                "inspect", "--format", "{{.State.Running}}", name, timeout=10, check=False
            )
            return value == "true"
        except BrowserError:
            return False

    async def run_command(self, name: str, args: list[str]) -> None:
        await self._run("exec", name, *args, timeout=15)

    async def cleanup_orphans(self) -> None:
        output = await self._run(
            "ps", "--all", "--quiet", "--filter", "label=meridian.browser.session=1", timeout=10
        )
        names = [line.strip() for line in output.splitlines() if line.strip()]
        for name in names:
            await self.remove(name)


@dataclass
class BrowserSession:
    id: str
    user: str
    container: str
    port: int
    created_at: float
    last_activity: float


class BrowserSessionManager:
    def __init__(
        self,
        settings: Settings,
        runtime: BrowserRuntime,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._clock = clock
        self._sessions: dict[str, BrowserSession] = {}
        self._by_user: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.browser_enabled

    @property
    def max_sessions(self) -> int:
        return self._settings.browser_max_sessions

    @property
    def idle_timeout_seconds(self) -> int:
        return self._settings.browser_idle_timeout_minutes * 60

    async def available(self) -> bool:
        return self.enabled and await self._runtime.available()

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def for_user(self, username: str) -> BrowserSession | None:
        session_id = self._by_user.get(username)
        return self._sessions.get(session_id) if session_id else None

    def get(self, session_id: str, username: str) -> BrowserSession:
        session = self._sessions.get(session_id)
        if session is None or session.user != username:
            raise BrowserSessionNotFound("Browser session not found.")
        return session

    def touch(self, session_id: str, username: str) -> BrowserSession:
        session = self.get(session_id, username)
        session.last_activity = self._clock()
        return session

    async def start(self) -> None:
        if not self.enabled:
            return
        self._reaper = asyncio.create_task(self._reap_loop(), name="meridian-browser-reaper")
        if await self._runtime.available():
            try:
                await self._runtime.cleanup_orphans()
            except BrowserError:
                logger.warning("Could not clean up old browser containers", exc_info=True)

    async def stop(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            await asyncio.gather(self._reaper, return_exceptions=True)
            self._reaper = None
        if self.enabled:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._by_user.clear()
            if await self._runtime.available():
                await asyncio.gather(
                    *(self._runtime.remove(session.container) for session in sessions),
                    return_exceptions=True,
                )

    async def get_or_create(self, username: str) -> BrowserSession:
        if not self.enabled:
            raise BrowserUnavailable("The private browser is disabled.")
        async with self._lock:
            existing = self.for_user(username)
            if existing is not None:
                if await self._runtime.is_running(existing.container):
                    existing.last_activity = self._clock()
                    return existing
                self._sessions.pop(existing.id, None)
                self._by_user.pop(username, None)

            if len(self._sessions) >= self._settings.browser_max_sessions:
                raise BrowserLimitReached(
                    f"All {self._settings.browser_max_sessions} browser slots are in use."
                )

            session_id = secrets.token_urlsafe(24)
            spec = self._spec(username)
            port = await self._runtime.start(spec)
            container = spec.name
            now = self._clock()
            session = BrowserSession(
                id=session_id,
                user=username,
                container=container,
                port=port,
                created_at=now,
                last_activity=now,
            )
            self._sessions[session_id] = session
            self._by_user[username] = session_id
            return session

    async def remove(self, session_id: str, username: str) -> None:
        async with self._lock:
            session = self.get(session_id, username)
            self._sessions.pop(session_id, None)
            self._by_user.pop(username, None)
        try:
            await self._runtime.remove(session.container)
        except BrowserError:
            logger.warning("Could not remove browser container %s", session.container)

    async def restart(self, session_id: str, username: str) -> BrowserSession:
        async with self._lock:
            session = self.get(session_id, username)
            if await self._runtime.is_running(session.container):
                await self._runtime.restart(session.container)
                await self._runtime.wait_ready(session.container, session.port)
            else:
                await self._runtime.remove(session.container)
                session.port = await self._runtime.start(self._spec(username))
            session.last_activity = self._clock()
            return session

    def _spec(self, username: str) -> ContainerSpec:
        user_key = hashlib.sha256(username.encode("utf-8")).hexdigest()[:16]
        return ContainerSpec(
            name=f"{self._settings.browser_container_prefix}-{user_key}",
            image=self._settings.browser_image,
            volume=f"{self._settings.browser_home_volume_prefix}-{user_key}",
            network=self._settings.browser_network,
            novnc_port=self._settings.browser_novnc_port,
            memory=self._settings.browser_memory_limit,
            cpus=self._settings.browser_cpus,
        )

    async def navigate(
        self, session_id: str, username: str, url: str, *, new_tab: bool = False
    ) -> BrowserSession:
        session = self.get(session_id, username)
        args = [*_WINDOW_SEARCH, "windowactivate", "--sync"]
        if new_tab:
            args.extend(["key", "--clearmodifiers", "ctrl+t"])
        args.extend(
            [
                "key",
                "--clearmodifiers",
                "ctrl+l",
                "type",
                "--clearmodifiers",
                "--delay",
                "2",
                url,
                "key",
                "Return",
            ]
        )
        await self._runtime.run_command(session.container, args)
        session.last_activity = self._clock()
        return session

    async def action(self, session_id: str, username: str, action: str) -> BrowserSession:
        session = self.get(session_id, username)
        keys = {
            "back": "alt+Left",
            "forward": "alt+Right",
            "reload": "ctrl+r",
            "new-tab": "ctrl+t",
        }
        key = keys.get(action)
        if key is None:
            raise BrowserError(f"Unknown browser action: {action}")
        await self._runtime.run_command(
            session.container,
            [
                *_WINDOW_SEARCH,
                "windowactivate",
                "--sync",
                "key",
                "--clearmodifiers",
                key,
            ],
        )
        session.last_activity = self._clock()
        return session

    async def reap(self, now: float | None = None) -> None:
        current = self._clock() if now is None else now
        expired = [
            session
            for session in list(self._sessions.values())
            if current - session.last_activity >= self.idle_timeout_seconds
        ]
        for session in expired:
            await self.remove(session.id, session.user)
            logger.info("Closed idle browser session for %s", session.user)

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(self._settings.browser_reaper_interval_seconds)
            try:
                await self.reap()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Could not reap idle browser sessions")


def normalise_target(value: str) -> str:
    from urllib.parse import quote_plus, urlsplit, urlunsplit

    target = value.strip()
    if not target:
        raise ValueError("Enter a website address or search term.")
    if len(target) > 2048:
        raise ValueError("That address is too long.")

    if target.startswith(("http://", "https://")):
        parsed = urlsplit(target)
        if not parsed.netloc:
            raise ValueError("Enter a valid website address.")
        return urlunsplit(parsed)

    if target.startswith(("javascript:", "data:", "file:", "about:")):
        raise ValueError("Only http and https addresses are allowed.")

    looks_like_host = " " not in target and ("." in target or target == "localhost")
    if looks_like_host:
        parsed = urlsplit(f"https://{target}")
        if parsed.netloc:
            return urlunsplit(parsed)

    return f"https://duckduckgo.com/?q={quote_plus(target)}"


def normalise_favourite_url(value: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    target = value.strip()
    if not target:
        raise ValueError("Enter a website address.")
    if "://" not in target:
        target = f"https://{target}"
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Favourite addresses must use http or https.")
    return urlunsplit(parsed)
