"""On-demand, per-user Firefox container orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from ai_dashboard.config import Settings

logger = logging.getLogger(__name__)

_WINDOW_SEARCH = (
    "search",
    "--onlyvisible",
    "--limit",
    "1",
    "meridian-browser|MeridianBrowser|Firefox",
)
_BROWSER_UID = "1000"
_BROWSER_XAUTHORITY = "/home/browser/.Xauthority"
_DISPLAY_RECOVERY_COOLDOWN_SECONDS = 15.0
_DISPLAY_RECOVERY_MAX_ATTEMPTS = 2
_SESSION_MONITOR_INTERVAL_SECONDS = 5.0


class BrowserError(RuntimeError):
    pass


class DockerCommandError(BrowserError):
    def __init__(
        self,
        command: tuple[str, ...],
        stdout: str,
        stderr: str,
        returncode: int,
        *,
        summary: str = "Docker command failed",
    ) -> None:
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        output = "\n".join(part for part in (stderr, stdout) if part) or "(no output)"
        super().__init__(f"{summary} ({' '.join(command)}), exit {returncode}:\n{output}")


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
    pids_limit: int


class BrowserRuntime(Protocol):
    async def available(self) -> bool: ...

    async def start(self, spec: ContainerSpec) -> int: ...

    async def wait_ready(self, name: str, port: int) -> None: ...

    async def remove(self, name: str) -> None: ...

    async def restart(self, name: str) -> None: ...

    async def is_running(self, name: str) -> bool: ...

    async def run_command(self, name: str, args: list[str]) -> None: ...

    async def diagnostics(self, name: str) -> str: ...

    async def persist_diagnostics(self, name: str, details: str, *, reason: str) -> str | None: ...

    async def cleanup_orphans(self) -> None: ...


class DockerRuntime:
    def __init__(self, settings: Settings) -> None:
        self._binary = settings.browser_docker_binary
        self._network = settings.browser_network
        self._container_prefix = settings.browser_container_prefix
        self._diagnostics_dir = settings.browser_diagnostics_dir
        self._memory_limit = settings.browser_memory_limit
        self._pids_limit = settings.browser_pids_limit
        self._available: bool | None = None
        self._checked_at = 0.0

    def _browser_exec(self, name: str) -> tuple[str, ...]:
        return (
            "exec",
            "--user",
            _BROWSER_UID,
            "--env",
            f"XAUTHORITY={_BROWSER_XAUTHORITY}",
            name,
        )

    async def _execute(self, *args: str, timeout: float = 20.0) -> tuple[str, str, int]:
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

        returncode = process.returncode
        assert returncode is not None
        return (
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
            returncode,
        )

    async def _run(self, *args: str, timeout: float = 20.0, check: bool = True) -> str:
        stdout, stderr, returncode = await self._execute(*args, timeout=timeout)
        if check and returncode != 0:
            error = DockerCommandError(args, stdout, stderr, returncode)
            logger.error("%s", error)
            raise error
        return stdout

    async def available(self) -> bool:
        now = time.monotonic()
        if self._available is not None and now - self._checked_at < 15:
            return self._available
        try:
            _stdout, _stderr, returncode = await self._execute(
                "version", "--format", "{{.Server.Version}}", timeout=5
            )
            self._available = returncode == 0
        except BrowserError:
            self._available = False
        self._checked_at = now
        return self._available

    async def _ensure_network(self) -> None:
        _stdout, _stderr, returncode = await self._execute(
            "network", "inspect", self._network, timeout=10
        )
        if returncode != 0:
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
        try:
            await self._run(
                "run",
                "--detach",
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
                str(spec.pids_limit),
                "--memory",
                spec.memory,
                "--cpus",
                spec.cpus,
                "--shm-size",
                "512m",
                "--stop-timeout",
                "30",
                "--publish",
                f"127.0.0.1::{spec.novnc_port}",
                "--volume",
                f"{spec.volume}:/home/browser",
                spec.image,
                timeout=90,
            )
            mapping = await self._run("port", spec.name, f"{spec.novnc_port}/tcp", timeout=10)
            line = mapping.splitlines()[-1]
            port = int(line.rsplit(":", 1)[-1])
            await self.wait_ready(spec.name, port)
        except (BrowserError, IndexError, ValueError) as exc:
            await self._report_start_failure(spec.name, exc)
            try:
                await self.remove(spec.name)
            except BrowserError:
                logger.exception("Could not remove failed browser container %s", spec.name)
            if isinstance(exc, BrowserUnavailable):
                raise
            raise BrowserUnavailable(str(exc)) from exc
        return port

    async def _diagnostics(self, name: str) -> str:
        try:
            raw_state, state_error, state_code = await self._execute(
                "inspect", "--format", "{{json .}}", name, timeout=5
            )
            if state_code == 0:
                inspect_data = json.loads(raw_state)
                state_data = inspect_data.get("State") or {}
                host_config = inspect_data.get("HostConfig") or {}
                config = inspect_data.get("Config") or {}
                health = state_data.get("Health")
                state = (
                    f"status={state_data.get('Status')} "
                    f"running={state_data.get('Running')} "
                    f"exit={state_data.get('ExitCode')} "
                    f"oom_killed={state_data.get('OOMKilled')} "
                    f"error={state_data.get('Error')} "
                    f"restart_count={inspect_data.get('RestartCount')} "
                    f"health={health.get('Status') if health else 'none'}"
                )
                limits = (
                    f"memory_limit={host_config.get('Memory')} "
                    f"configured_memory_limit={self._memory_limit} "
                    f"pids_limit={host_config.get('PidsLimit')} "
                    f"configured_pids_limit={self._pids_limit} "
                    f"memory_swap={host_config.get('MemorySwap')} "
                    f"shm_size={host_config.get('ShmSize')} "
                    f"stop_timeout={config.get('StopTimeout')}"
                )
            else:
                state = state_error or raw_state or f"docker inspect exited {state_code}"
                limits = "(no limits data)"
        except (BrowserUnavailable, json.JSONDecodeError, AttributeError) as exc:
            state = str(exc)
            limits = "(no limits data)"

        try:
            info_raw, info_error, info_code = await self._execute(
                "info", "--format", "{{json .}}", timeout=5
            )
            if info_code == 0 and info_raw:
                info = json.loads(info_raw)
                warnings = "; ".join(str(item) for item in (info.get("Warnings") or []))
                docker_info = (
                    f"cgroup_driver={info.get('CgroupDriver')} "
                    f"cgroup_version={info.get('CgroupVersion')} "
                    f"memory_limit_support={info.get('MemoryLimit')} "
                    f"swap_limit_support={info.get('SwapLimit')} "
                    f"pids_limit_support={info.get('PidsLimit')} "
                    f"server_version={info.get('ServerVersion')} "
                    f"warnings={warnings or 'none'}"
                )
            else:
                docker_info = info_error or info_raw or f"docker info exited {info_code}"
        except (BrowserUnavailable, json.JSONDecodeError, AttributeError) as exc:
            docker_info = str(exc)

        try:
            cgroup_script = (
                "printf 'cgroup_type:%s\\n' \"$(stat -fc %T /sys/fs/cgroup 2>/dev/null || true)\"; "
                "printf 'cgroup_controllers:%s\\n' "
                '"$(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || true)"; '
                "printf 'proc_cmdline:%s\\n' \"$(cat /proc/cmdline 2>/dev/null || true)\"; "
                "for path in "
                "/sys/fs/cgroup/memory.events "
                "/sys/fs/cgroup/memory.max "
                "/sys/fs/cgroup/memory.current "
                "/sys/fs/cgroup/memory.swap.current "
                "/sys/fs/cgroup/memory.peak "
                "/sys/fs/cgroup/pids.current "
                "/sys/fs/cgroup/pids.max "
                "/sys/fs/cgroup/pids.events "
                "/sys/fs/cgroup/memory/memory.limit_in_bytes "
                "/sys/fs/cgroup/memory/memory.usage_in_bytes "
                "/sys/fs/cgroup/pids/pids.current "
                "/sys/fs/cgroup/pids/pids.max; do "
                'if [ -e "$path" ]; then '
                "printf '%s:\\n' \"$path\"; "
                "sed 's/^/  /' \"$path\" 2>/dev/null || true; "
                "else printf '%s: (not present)\\n' \"$path\"; fi; "
                "done"
            )
            events, events_error, events_code = await self._execute(
                "exec",
                name,
                "sh",
                "-c",
                cgroup_script,
                timeout=5,
            )
            cgroup = events or events_error or f"cgroup read exited {events_code}"
        except BrowserUnavailable as exc:
            cgroup = str(exc)

        try:
            processes, processes_error, processes_code = await self._execute(
                "top", name, "-eo", "pid,ppid,nlwp,stat,comm,args", timeout=5
            )
            processes = (
                "\n".join(part for part in (processes, processes_error) if part)
                or f"docker top exited {processes_code}"
            )
        except BrowserUnavailable as exc:
            processes = str(exc)

        try:
            resources, resources_error, _resources_code = await self._execute(
                "stats",
                "--no-stream",
                "--format",
                "memory={{.MemUsage}} cpu={{.CPUPerc}} pids={{.PIDs}}",
                name,
                timeout=5,
            )
            resources = "\n".join(part for part in (resources, resources_error) if part) or (
                "(no resource data)"
            )
        except BrowserUnavailable as exc:
            resources = str(exc)

        try:
            logs, logs_error, _logs_code = await self._execute(
                "logs", "--tail", "200", "--timestamps", name, timeout=5
            )
            logs = "\n".join(part for part in (logs, logs_error) if part) or "(no logs)"
        except BrowserUnavailable as exc:
            logs = str(exc)

        return (
            f"state: {state}\n"
            f"limits: {limits}\n"
            f"docker_info: {docker_info}\n"
            f"cgroup: {cgroup}\n"
            f"resources: {resources}\n"
            f"processes:\n{processes}\n"
            f"logs:\n{logs}"
        )

    async def diagnostics(self, name: str) -> str:
        return await self._diagnostics(name)

    def _persist_diagnostics_sync(self, name: str, details: str, *, reason: str) -> str:
        directory = self._diagnostics_dir
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        path = directory / f"{safe_name}-{timestamp}.log"
        path.write_text(
            f"container: {name}\nreason: {reason}\ncaptured_at: {timestamp}\n\n{details}\n",
            encoding="utf-8",
        )
        existing = sorted(
            directory.glob("*.log"),
            key=lambda item: (item.stat().st_mtime, item.name),
            reverse=True,
        )
        for stale in existing[50:]:
            stale.unlink(missing_ok=True)
        return str(path)

    async def _export_container_artifacts(self, name: str) -> str | None:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        directory = self._diagnostics_dir / "artifacts" / f"{safe_name}-{timestamp}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            _stdout, stderr, returncode = await self._execute(
                "cp",
                f"{name}:/home/browser/.meridian/diagnostics/.",
                str(directory),
                timeout=15,
            )
        except (BrowserError, OSError) as exc:
            logger.warning("Could not export browser diagnostics artifacts for %s: %s", name, exc)
            with contextlib.suppress(OSError):
                directory.rmdir()
            return None
        if returncode != 0:
            logger.warning(
                "Could not export browser diagnostics artifacts for %s: %s",
                name,
                stderr or f"docker cp exited {returncode}",
            )
            with contextlib.suppress(OSError):
                directory.rmdir()
            return None
        return str(directory)

    async def persist_diagnostics(self, name: str, details: str, *, reason: str) -> str | None:
        try:
            artifacts = await self._export_container_artifacts(name)
            if artifacts is not None:
                details = f"{details}\nartifacts_dir: {artifacts}"
            return await asyncio.to_thread(
                self._persist_diagnostics_sync, name, details, reason=reason
            )
        except OSError:
            logger.exception("Could not persist browser diagnostics for %s", name)
            return None

    async def _report_start_failure(self, name: str, error: Exception) -> None:
        try:
            diagnostics = await self._diagnostics(name)
            path = await self.persist_diagnostics(
                name, diagnostics, reason=f"start failure: {error}"
            )
            if path is not None:
                diagnostics = f"{diagnostics}\ndiagnostics_file: {path}"
        except Exception:
            logger.exception("Could not collect diagnostics for browser container %s", name)
            diagnostics = "diagnostics unavailable"
        logger.error("Browser container %s failed during startup: %s\n%s", name, error, diagnostics)

    async def _find_window_once(self, name: str) -> tuple[str | None, str, str, int]:
        stdout, stderr, returncode = await self._execute(
            *self._browser_exec(name),
            "xdotool",
            *_WINDOW_SEARCH,
            timeout=5,
        )
        window = None
        if returncode == 0:
            window = next(
                (line.strip() for line in stdout.splitlines() if line.strip().isdigit()),
                None,
            )
        return window, stdout, stderr, returncode

    async def _find_window(self, name: str, timeout: float = 5.0) -> str:
        deadline = time.monotonic() + timeout
        command = (*self._browser_exec(name), "xdotool", *_WINDOW_SEARCH)
        stdout = ""
        stderr = ""
        returncode = 0
        while True:
            window, stdout, stderr, returncode = await self._find_window_once(name)
            if window is not None:
                return window
            if time.monotonic() >= deadline:
                error = DockerCommandError(
                    command,
                    stdout,
                    stderr,
                    returncode,
                    summary="Could not find the Firefox window",
                )
                logger.error("%s", error)
                raise error
            await asyncio.sleep(0.25)

    async def wait_ready(self, name: str, port: int, timeout: float = 45.0) -> None:
        deadline = time.monotonic() + timeout
        display_ready = False
        window_error = "The Firefox window has not been searched for yet."
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                if not display_ready:
                    try:
                        response = await client.get(f"http://127.0.0.1:{port}/vnc_lite.html")
                        display_ready = response.status_code == 200
                    except httpx.HTTPError:
                        pass

                if display_ready:
                    window, stdout, stderr, returncode = await self._find_window_once(name)
                    if window is not None:
                        return
                    window_error = str(
                        DockerCommandError(
                            (*self._browser_exec(name), "xdotool", *_WINDOW_SEARCH),
                            stdout,
                            stderr,
                            returncode,
                            summary="Firefox window search failed",
                        )
                    )

                if not await self.is_running(name):
                    raise BrowserUnavailable("The browser container exited during startup.")
                await asyncio.sleep(0.5)

        if not display_ready:
            raise BrowserUnavailable("The browser display did not start in time.")
        logger.error("Firefox window not ready in %s: %s", name, window_error)
        raise BrowserUnavailable("The browser window did not start in time.")

    async def _disconnect_endpoint(self, name: str) -> None:
        await self._run(
            "network",
            "disconnect",
            "--force",
            self._network,
            name,
            timeout=10,
            check=False,
        )

    async def remove(self, name: str) -> None:
        await self._disconnect_endpoint(name)
        await self._run("stop", "--time", "30", name, timeout=45, check=False)
        await self._run("rm", "--force", name, timeout=20, check=False)

    async def restart(self, name: str) -> None:
        await self._run("restart", "--time", "30", name, timeout=45)

    async def is_running(self, name: str) -> bool:
        try:
            value = await self._run(
                "inspect", "--format", "{{.State.Running}}", name, timeout=10, check=False
            )
            return value == "true"
        except BrowserError:
            return False

    async def run_command(self, name: str, args: list[str]) -> None:
        window = await self._find_window(name)
        await self._run(
            *self._browser_exec(name),
            "xdotool",
            "windowactivate",
            "--sync",
            window,
            *args,
            timeout=15,
        )

    async def _cleanup_stale_network_endpoints(self) -> None:
        raw = await self._run(
            "network",
            "inspect",
            "--format",
            "{{json .Containers}}",
            self._network,
            timeout=10,
            check=False,
        )
        if not raw:
            return
        try:
            endpoints = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Could not parse Docker network endpoints for %s", self._network)
            return
        if not isinstance(endpoints, dict):
            return
        for endpoint in endpoints.values():
            name = str((endpoint or {}).get("Name") or "")
            if not name.startswith(f"{self._container_prefix}-"):
                continue
            _stdout, _stderr, returncode = await self._execute("inspect", name, timeout=5)
            if returncode == 0:
                continue
            logger.info("Disconnecting stale browser network endpoint %s", name)
            await self._disconnect_endpoint(name)

    async def cleanup_orphans(self) -> None:
        output = await self._run(
            "ps", "--all", "--quiet", "--filter", "label=meridian.browser.session=1", timeout=10
        )
        names = [line.strip() for line in output.splitlines() if line.strip()]
        for name in names:
            await self.remove(name)
        await self._cleanup_stale_network_endpoints()


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
        self._last_display_recovery: dict[str, float] = {}
        self._display_recovery_attempts: dict[str, int] = {}
        self._dead_reported: set[str] = set()
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None
        self._monitor: asyncio.Task[None] | None = None

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
        self._monitor = asyncio.create_task(self._monitor_loop(), name="meridian-browser-monitor")
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
        if self._monitor is not None:
            self._monitor.cancel()
            await asyncio.gather(self._monitor, return_exceptions=True)
            self._monitor = None
        if self.enabled:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._by_user.clear()
            self._last_display_recovery.clear()
            self._display_recovery_attempts.clear()
            self._dead_reported.clear()
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
                await self._record_diagnostics(
                    existing.container, "container exited before session reuse"
                )
                self._sessions.pop(existing.id, None)
                self._by_user.pop(username, None)
                self._display_recovery_attempts.pop(existing.id, None)
                self._last_display_recovery.pop(existing.id, None)
                self._dead_reported.discard(existing.id)

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
            self._display_recovery_attempts[session_id] = 0
            return session

    async def remove(self, session_id: str, username: str) -> None:
        async with self._lock:
            session = self.get(session_id, username)
            self._sessions.pop(session_id, None)
            self._by_user.pop(username, None)
            self._last_display_recovery.pop(session_id, None)
            self._display_recovery_attempts.pop(session_id, None)
            self._dead_reported.discard(session_id)
        try:
            await self._runtime.remove(session.container)
        except BrowserError:
            logger.warning("Could not remove browser container %s", session.container)

    async def restart(self, session_id: str, username: str) -> BrowserSession:
        async with self._lock:
            session = self.get(session_id, username)
            self._display_recovery_attempts[session_id] = 0
            await self._restart_or_recreate(session, username)
            now = self._clock()
            self._last_display_recovery[session_id] = now
            session.last_activity = now
            return session

    async def recover_display(
        self, session_id: str, username: str, *, reason: str
    ) -> BrowserSession:
        async with self._lock:
            session = self.get(session_id, username)
            attempts = self._display_recovery_attempts.get(session_id, 0)
            if attempts >= _DISPLAY_RECOVERY_MAX_ATTEMPTS:
                logger.error(
                    "Browser display recovery limit reached for %s; manual restart required",
                    session.container,
                )
                raise BrowserUnavailable(
                    "The browser display could not be recovered after "
                    f"{_DISPLAY_RECOVERY_MAX_ATTEMPTS} automatic attempts. "
                    "Restart the browser session manually."
                )
            now = self._clock()
            last = self._last_display_recovery.get(session_id, 0.0)
            if now - last < _DISPLAY_RECOVERY_COOLDOWN_SECONDS:
                logger.info(
                    "Skipping duplicate display recovery for %s; retrying display",
                    session.container,
                )
                return session
            attempts += 1
            self._display_recovery_attempts[session_id] = attempts
            self._last_display_recovery[session_id] = now
            logger.info(
                "Recovering browser display for %s (attempt %s/%s)",
                session.container,
                attempts,
                _DISPLAY_RECOVERY_MAX_ATTEMPTS,
            )
            await self.report_display_failure(session_id, username, reason=reason)
            try:
                await self._restart_or_recreate(session, username)
            except BrowserError as exc:
                logger.error("Browser display recovery failed for %s: %s", session.container, exc)
                raise BrowserUnavailable(
                    "The browser display could not be recovered after "
                    f"{attempts} automatic attempt(s)."
                ) from exc
            session.last_activity = self._clock()
            logger.info("Recovered browser display for %s", session.container)
            return session

    async def report_display_failure(self, session_id: str, username: str, *, reason: str) -> None:
        session = self.get(session_id, username)
        await self._record_diagnostics(
            session.container,
            f"display failure on port {session.port}: {reason}",
        )

    async def _record_diagnostics(self, container: str, reason: str) -> str | None:
        try:
            details = await self._runtime.diagnostics(container)
        except Exception:
            logger.exception("Could not collect browser diagnostics for %s", container)
            details = "diagnostics unavailable"
        logger.error("Browser diagnostics for %s (%s):\n%s", container, reason, details)
        try:
            path = await self._runtime.persist_diagnostics(container, details, reason=reason)
        except Exception:
            logger.exception("Could not persist browser diagnostics for %s", container)
            return None
        if path is not None:
            logger.error("Saved browser diagnostics for %s to %s", container, path)
        return path

    async def _restart_or_recreate(self, session: BrowserSession, username: str) -> None:
        if await self._runtime.is_running(session.container):
            try:
                await self._runtime.restart(session.container)
                await self._runtime.wait_ready(session.container, session.port)
                return
            except BrowserError as exc:
                logger.error(
                    "Browser container %s did not recover from restart: %s",
                    session.container,
                    exc,
                )
                await self._record_diagnostics(session.container, f"restart failed: {exc}")
        await self._runtime.remove(session.container)
        logger.info("Recreating browser container %s", session.container)
        session.port = await self._runtime.start(self._spec(username))

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
            pids_limit=self._settings.browser_pids_limit,
        )

    async def navigate(
        self, session_id: str, username: str, url: str, *, new_tab: bool = False
    ) -> BrowserSession:
        session = self.get(session_id, username)
        args: list[str] = []
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
            ["key", "--clearmodifiers", key],
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

    async def _check_sessions(self) -> None:
        if not self.enabled or not await self._runtime.available():
            return
        for session in list(self._sessions.values()):
            if self._sessions.get(session.id) is not session:
                continue
            if await self._runtime.is_running(session.container):
                self._dead_reported.discard(session.id)
                continue
            if session.id in self._dead_reported:
                continue
            self._dead_reported.add(session.id)
            await self._record_diagnostics(
                session.container, "container exited while session was active"
            )

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(_SESSION_MONITOR_INTERVAL_SECONDS)
            try:
                await self._check_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Could not monitor browser sessions")

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
