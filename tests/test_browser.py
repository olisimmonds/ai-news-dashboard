from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_dashboard.browser import api as browser_api
from ai_dashboard.browser.auth import BrowserAuth, hash_password, save_accounts, verify_password
from ai_dashboard.browser.sessions import (
    _WINDOW_SEARCH,
    BrowserError,
    BrowserLimitReached,
    BrowserSessionManager,
    BrowserUnavailable,
    ContainerSpec,
    DockerRuntime,
    normalise_target,
)
from ai_dashboard.config import Settings
from ai_dashboard.main import create_app
from ai_dashboard.sources import SourceConfig


class FakeRuntime:
    def __init__(self) -> None:
        self.running: set[str] = set()
        self.starts: list[ContainerSpec] = []
        self.removals: list[str] = []
        self.commands: list[tuple[str, list[str]]] = []
        self.restarts: list[str] = []
        self.cleaned = False
        self.ready_ports: list[int] = []
        self.ready_names: list[str] = []
        self.diagnostics_calls: list[str] = []
        self.persisted: list[tuple[str, str]] = []
        self.fail_ready = False

    async def available(self) -> bool:
        return True

    async def start(self, spec: ContainerSpec) -> int:
        self.starts.append(spec)
        self.running.add(spec.name)
        return 41000 + len(self.starts)

    async def wait_ready(self, name: str, port: int) -> None:
        self.ready_names.append(name)
        self.ready_ports.append(port)
        if self.fail_ready:
            self.fail_ready = False
            raise BrowserUnavailable("The browser window did not start in time.")

    async def remove(self, name: str) -> None:
        self.removals.append(name)
        self.running.discard(name)

    async def restart(self, name: str) -> None:
        self.restarts.append(name)

    async def is_running(self, name: str) -> bool:
        return name in self.running

    async def run_command(self, name: str, args: list[str]) -> None:
        self.commands.append((name, args))

    async def diagnostics(self, name: str) -> str:
        self.diagnostics_calls.append(name)
        return "state: running oom_killed=False\nprocesses:\n1 Xvnc\n2 websockify"

    async def persist_diagnostics(self, name: str, details: str, *, reason: str) -> str | None:
        self.persisted.append((name, reason))
        return f"/tmp/{name}.log"

    async def cleanup_orphans(self) -> None:
        self.cleaned = True


@pytest.fixture
def browser_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "browser_enabled": True,
            "browser_users_file": tmp_path / "config" / "browser_users.json",
            "browser_state_file": tmp_path / "data" / "browser_state.json",
            "browser_diagnostics_dir": tmp_path / "data" / "browser-diagnostics",
            "browser_secret": "test-browser-secret",
            "browser_session_ttl_hours": 12,
            "browser_idle_timeout_minutes": 1,
            "browser_max_sessions": 2,
        }
    )


def seed_accounts(settings: Settings, usernames: tuple[str, ...] = ("alice",)) -> None:
    save_accounts(
        settings.browser_users_file,
        {username: hash_password("correct horse battery") for username in usernames},
    )


def make_app(
    settings: Settings,
    sources: list[SourceConfig],
    *,
    accounts: bool = True,
) -> tuple[TestClient, FakeRuntime]:
    if accounts:
        seed_accounts(settings, ("alice", "bob", "carol"))
    runtime = FakeRuntime()
    return TestClient(create_app(settings, sources, browser_runtime=runtime)), runtime


def login(client: TestClient, username: str = "alice") -> None:
    response = client.post(
        "/api/browser/auth/login",
        json={"username": username, "password": "correct horse battery"},
    )
    assert response.status_code == 200


def test_browser_status_reports_anonymous_access(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, runtime = make_app(browser_settings, sources)

    with client:
        response = client.get("/api/browser/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["authenticated"] is False
    assert payload["accounts_configured"] is True
    assert payload["docker_available"] is True
    assert payload["session"] is None
    assert runtime.cleaned is True


def test_browser_login_sets_cookie_and_authenticates(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, _runtime = make_app(browser_settings, sources)

    with client:
        failure = client.post(
            "/api/browser/auth/login",
            json={"username": "alice", "password": "wrong password"},
        )
        assert failure.status_code == 401

        login(client)
        assert client.cookies.get("meridian_browser")
        status = client.get("/api/browser/status").json()

    assert status["authenticated"] is True
    assert status["user"] == "alice"


def test_browser_rejects_cross_origin_mutations(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, _runtime = make_app(browser_settings, sources)

    with client:
        login(client)
        same_host = {"Origin": "https://testserver"}
        assert client.post("/api/browser/sessions", json={}, headers=same_host).status_code == 201
        cross_site = {"Origin": "https://evil.example"}
        assert client.post("/api/browser/sessions", json={}, headers=cross_site).status_code == 403


def test_browser_requires_auth_and_can_start_a_session(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, runtime = make_app(browser_settings, sources)

    with client:
        assert client.get("/api/browser/favourites").status_code == 401
        assert client.post("/api/browser/sessions", json={}).status_code == 401

        login(client)
        response = client.post("/api/browser/sessions", json={"url": "example.com"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["vnc_url"].startswith("/api/browser/sessions/")
    assert "scale=true" in payload["vnc_url"]
    assert "path=api%2Fbrowser%2Fsessions%2F" in payload["vnc_url"]
    assert "view_only" not in payload["vnc_url"]
    assert len(runtime.starts) == 1
    assert len(runtime.commands) == 1
    assert runtime.commands[0][1][0] == "key"
    assert "https://example.com" in runtime.commands[0][1]


def test_browser_session_is_reused_and_can_be_closed(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, runtime = make_app(browser_settings, sources)

    with client:
        login(client)
        first = client.post("/api/browser/sessions", json={}).json()
        second = client.post("/api/browser/sessions", json={}).json()
        assert first["id"] == second["id"]
        assert len(runtime.starts) == 1

        closed = client.delete(f"/api/browser/sessions/{first['id']}")

    assert closed.status_code == 200
    assert runtime.removals


def test_vnc_asset_retries_transient_proxy_failure_without_restart(
    browser_settings: Settings,
    sources: list[SourceConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, runtime = make_app(browser_settings, sources)
    calls = 0

    async def fake_fetch(port: int, path: str) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("websockify temporarily unavailable")
        return httpx.Response(200, content=b"noVNC", headers={"content-type": "text/html"})

    monkeypatch.setattr(browser_api, "_DISPLAY_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(browser_api, "_fetch_display_asset", fake_fetch)

    with client:
        login(client)
        session = client.post("/api/browser/sessions", json={}).json()
        response = client.get(f"/api/browser/sessions/{session['id']}/vnc/vnc_lite.html")

    assert response.status_code == 200
    assert response.content == b"noVNC"
    assert runtime.restarts == []


def test_vnc_asset_recovers_dead_display_without_changing_profile(
    browser_settings: Settings,
    sources: list[SourceConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, runtime = make_app(browser_settings, sources)
    calls = 0

    async def fake_fetch(port: int, path: str) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("websockify is not responding")
        return httpx.Response(200, content=b"noVNC", headers={"content-type": "text/html"})

    monkeypatch.setattr(browser_api, "_DISPLAY_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(browser_api, "_fetch_display_asset", fake_fetch)

    with client:
        login(client)
        session = client.post("/api/browser/sessions", json={}).json()
        response = client.get(f"/api/browser/sessions/{session['id']}/vnc/vnc_lite.html")

    assert response.status_code == 200
    assert runtime.restarts == [runtime.starts[0].name]
    assert runtime.diagnostics_calls == [runtime.starts[0].name]
    assert len(runtime.starts) == 1
    assert runtime.starts[0].volume.startswith("meridian-browser-home-")


def test_browser_rejects_privileged_or_invalid_targets(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, _runtime = make_app(browser_settings, sources)

    with client:
        login(client)
        session = client.post("/api/browser/sessions", json={}).json()
        response = client.post(
            f"/api/browser/sessions/{session['id']}/navigate",
            json={"url": "javascript:alert(1)"},
        )

    assert response.status_code == 400


def test_browser_favourites_are_per_user_and_can_be_empty(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, _runtime = make_app(browser_settings, sources)

    with client:
        login(client)
        defaults = client.get("/api/browser/favourites")
        assert defaults.status_code == 200
        assert len(defaults.json()) >= 1

        saved = client.put(
            "/api/browser/favourites",
            json={"favourites": [{"title": "Local", "url": "example.com"}]},
        )
        assert saved.status_code == 200
        assert saved.json() == [{"title": "Local", "url": "https://example.com"}]

        empty = client.put("/api/browser/favourites", json={"favourites": []})
        assert empty.status_code == 200
        assert client.get("/api/browser/favourites").json() == []


def test_browser_logout_closes_the_session(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, runtime = make_app(browser_settings, sources)

    with client:
        login(client)
        client.post("/api/browser/sessions", json={})
        response = client.post("/api/browser/auth/logout")
        status = client.get("/api/browser/status").json()

    assert response.status_code == 200
    assert status["authenticated"] is False
    assert runtime.removals


def test_browser_missing_accounts_explains_setup(
    browser_settings: Settings, sources: list[SourceConfig]
) -> None:
    client, _runtime = make_app(browser_settings, sources, accounts=False)

    with client:
        status = client.get("/api/browser/status").json()
        response = client.post(
            "/api/browser/auth/login",
            json={"username": "alice", "password": "correct horse battery"},
        )

    assert status["accounts_configured"] is False
    assert "set-user" in status["setup_message"]
    assert response.status_code == 503


async def test_manager_enforces_slot_limit_and_reaps_idle_sessions(
    browser_settings: Settings,
) -> None:
    runtime = FakeRuntime()
    current = [1000.0]
    manager = BrowserSessionManager(browser_settings, runtime, clock=lambda: current[0])

    first = await manager.get_or_create("alice")
    second = await manager.get_or_create("bob")
    assert first.id != second.id
    with pytest.raises(BrowserLimitReached):
        await manager.get_or_create("carol")

    current[0] += manager.idle_timeout_seconds
    await manager.reap()

    assert manager.active_count == 0
    assert len(runtime.removals) == 2


async def test_manager_restarts_dead_container(browser_settings: Settings) -> None:
    runtime = FakeRuntime()
    manager = BrowserSessionManager(browser_settings, runtime)
    session = await manager.get_or_create("alice")
    runtime.running.discard(session.container)

    restarted = await manager.restart(session.id, "alice")

    assert restarted.port == 41000 + len(runtime.starts)
    assert len(runtime.starts) == 2


async def test_manager_waits_for_display_after_running_restart(
    browser_settings: Settings,
) -> None:
    runtime = FakeRuntime()
    manager = BrowserSessionManager(browser_settings, runtime)
    session = await manager.get_or_create("alice")

    restarted = await manager.restart(session.id, "alice")

    assert runtime.restarts == [session.container]
    assert runtime.ready_names == [session.container]
    assert runtime.ready_ports == [session.port]
    assert restarted.id == session.id


async def test_manager_recreates_container_when_restart_never_becomes_ready(
    browser_settings: Settings,
) -> None:
    runtime = FakeRuntime()
    manager = BrowserSessionManager(browser_settings, runtime)
    session = await manager.get_or_create("alice")
    first_port = session.port
    runtime.fail_ready = True

    restarted = await manager.restart(session.id, "alice")

    assert runtime.restarts == [session.container]
    assert runtime.removals == [session.container]
    assert len(runtime.starts) == 2
    assert restarted.port != first_port
    assert runtime.starts[0].volume == runtime.starts[1].volume


async def test_manager_recovers_display_and_keeps_profile_volume(
    browser_settings: Settings,
) -> None:
    runtime = FakeRuntime()
    manager = BrowserSessionManager(browser_settings, runtime)
    session = await manager.get_or_create("alice")

    recovered = await manager.recover_display(
        session.id, "alice", reason="noVNC asset request failed"
    )

    assert recovered.id == session.id
    assert recovered.port == session.port
    assert runtime.restarts == [session.container]
    assert runtime.diagnostics_calls == [session.container]
    assert runtime.persisted == [
        (session.container, "display failure on port 41001: noVNC asset request failed")
    ]
    assert len(runtime.starts) == 1
    assert runtime.starts[0].volume.startswith("meridian-browser-home-")

    again = await manager.recover_display(session.id, "alice", reason="display still failing")
    assert again.id == session.id
    assert runtime.restarts == [session.container]
    assert runtime.diagnostics_calls == [session.container]


def test_manager_stop_removes_sessions(browser_settings: Settings) -> None:
    async def run() -> tuple[int, int]:
        runtime = FakeRuntime()
        manager = BrowserSessionManager(browser_settings, runtime)
        await manager.start()
        await manager.get_or_create("alice")
        await manager.stop()
        return manager.active_count, len(runtime.removals)

    import asyncio

    active, removals = asyncio.run(run())
    assert active == 0
    assert removals == 1


def test_password_hashes_and_signed_tokens(browser_settings: Settings) -> None:
    encoded = hash_password("correct horse battery")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery", encoded)
    assert not verify_password("wrong password", encoded)

    save_accounts(browser_settings.browser_users_file, {"alice": encoded})
    auth = BrowserAuth(browser_settings)
    token, _expires = auth.issue_token("alice")

    assert auth.verify_token(token) == "alice"
    assert auth.verify_token(f"{token}tampered") is None


def test_normalise_target_preserves_http_and_builds_web_targets() -> None:
    assert normalise_target("https://example.com/path") == "https://example.com/path"
    assert normalise_target("example.com") == "https://example.com"
    assert normalise_target("energy prices") == ("https://duckduckgo.com/?q=energy+prices")
    with pytest.raises(ValueError):
        normalise_target("file:///etc/passwd")


def test_container_start_script_uses_valid_x_server_options() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "docker" / "firefox" / "start-session.sh"
    ).read_text(encoding="utf-8")

    assert "-depth 24" in script
    assert "-geometry 1280x800" in script
    assert "-nolisten tcp" in script
    assert "-localhost \\" in script
    assert "/vnc_lite.html" in script

    for invalid in ("-depth=", "-geometry=", "-nolisten=", "-xkb"):
        assert invalid not in script


def test_window_search_uses_one_xdotool_pattern() -> None:
    assert _WINDOW_SEARCH[-1] == "meridian-browser|MeridianBrowser|Firefox"
    assert "--class" not in _WINDOW_SEARCH
    assert "--classname" not in _WINDOW_SEARCH


def test_docker_start_reports_diagnostics_before_cleanup(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = DockerRuntime(browser_settings)
    events: list[str] = []
    run_calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: float = 20.0, check: bool = True) -> str:
        events.append(f"run:{args[0]}")
        run_calls.append(args)
        if args[0] == "port":
            return "127.0.0.1:32770"
        return ""

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        return "", "", 0

    async def fake_diagnostics(name: str) -> str:
        events.append("diagnostics")
        return "state: exited\nlogs:\nFirefox failed to start"

    async def fake_wait_ready(name: str, port: int) -> None:
        events.append("wait_ready")
        raise BrowserUnavailable("The browser window did not start in time.")

    monkeypatch.setattr(runtime, "_execute", fake_execute)
    monkeypatch.setattr(runtime, "_run", fake_run)
    monkeypatch.setattr(runtime, "_diagnostics", fake_diagnostics)
    monkeypatch.setattr(runtime, "wait_ready", fake_wait_ready)

    spec = ContainerSpec(
        name="meridian-browser-test",
        image="meridian-firefox:test",
        volume="meridian-browser-home-test",
        network="meridian-browser-test",
        novnc_port=6080,
        memory="1200m",
        cpus="1.5",
    )

    with caplog.at_level(logging.ERROR), pytest.raises(BrowserUnavailable, match="window"):
        asyncio.run(runtime.start(spec))

    run_args = next(call for call in run_calls if call[0] == "run")
    assert "--rm" not in run_args
    assert "--stop-timeout" in run_args
    assert run_args[run_args.index("--stop-timeout") + 1] == "30"

    cleanup_index = max(index for index, event in enumerate(events) if event == "run:rm")
    assert events.index("diagnostics") < cleanup_index
    assert "Firefox failed to start" in caplog.text


def test_docker_run_command_uses_browser_user_and_window_id(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)
    calls: list[tuple[str, ...]] = []

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        calls.append(args)
        if "search" in args:
            return "12345\n", "", 0
        return "", "", 0

    monkeypatch.setattr(runtime, "_execute", fake_execute)

    asyncio.run(
        runtime.run_command(
            "meridian-browser-test",
            ["key", "--clearmodifiers", "ctrl+l"],
        )
    )

    assert len(calls) == 2
    search, action = calls
    prefix = (
        "exec",
        "--user",
        "1000",
        "--env",
        "XAUTHORITY=/home/browser/.Xauthority",
        "meridian-browser-test",
        "xdotool",
    )
    assert search[:7] == prefix
    assert search[7:] == _WINDOW_SEARCH
    assert action[:7] == prefix
    assert action[7:] == (
        "windowactivate",
        "--sync",
        "12345",
        "key",
        "--clearmodifiers",
        "ctrl+l",
    )


def test_docker_command_error_preserves_stdout_and_full_command(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        return "xdotool: search failed\n", "", 1

    monkeypatch.setattr(runtime, "_execute", fake_execute)

    with pytest.raises(BrowserError) as caught:
        asyncio.run(runtime._run("exec", "--user", "1000", "container", "xdotool"))

    message = str(caught.value)
    assert "exec --user 1000 container xdotool" in message
    assert "exit 1" in message
    assert "xdotool: search failed" in message


def test_find_window_failure_preserves_xdotool_stderr(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        return "", "Authorization required, but no authorization protocol specified", 1

    monkeypatch.setattr(runtime, "_execute", fake_execute)

    with pytest.raises(BrowserError) as caught:
        asyncio.run(runtime._find_window("meridian-browser-test", timeout=0))

    message = str(caught.value)
    assert "Could not find the Firefox window" in message
    assert "exec --user 1000" in message
    assert "Authorization required" in message


def test_browser_image_uses_uid_1000() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[1] / "docker" / "firefox" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "--uid 1000" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "procps" in dockerfile


def test_docker_diagnostics_report_oom_processes_and_resources(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        if args[0] == "inspect":
            state = {
                "State": {
                    "Status": "exited",
                    "Running": False,
                    "ExitCode": 137,
                    "OOMKilled": True,
                    "Error": "",
                    "Health": {"Status": "unhealthy"},
                },
                "HostConfig": {"Memory": 1258291200, "ShmSize": 536870912},
                "Config": {"StopTimeout": 30},
                "RestartCount": 2,
            }
            return json.dumps(state), "", 0
        if args[0] == "exec":
            return "oom 1\noom_kill 1\n", "", 0
        if args[0] == "top":
            return "PID COMMAND\n1 Xvnc\n2 websockify\n3 firefox-esr", "", 0
        if args[0] == "stats":
            return "memory=1.1GiB / 1.2GiB cpu=4.00%", "", 0
        if args[0] == "logs":
            return "Firefox content process terminated", "", 0
        return "", "", 1

    monkeypatch.setattr(runtime, "_execute", fake_execute)

    diagnostics = asyncio.run(runtime.diagnostics("meridian-browser-test"))

    assert "oom_killed=True" in diagnostics
    assert "health=unhealthy" in diagnostics
    assert "restart_count=2" in diagnostics
    assert "memory_limit=1258291200" in diagnostics
    assert "shm_size=536870912" in diagnostics
    assert "stop_timeout=30" in diagnostics
    assert "cgroup_memory_events: oom 1" in diagnostics
    assert "memory=1.1GiB / 1.2GiB" in diagnostics
    assert "websockify" in diagnostics
    assert "Firefox content process terminated" in diagnostics


def test_container_supervisor_restarts_websockify_and_health_checks_display() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "docker" / "firefox" / "start-session.sh"
    ).read_text(encoding="utf-8")

    assert script.startswith("#!/bin/bash")
    assert "wait -n -p" in script
    assert "restarting unresponsive websockify" in script
    assert "websockify exited with status" in script
    assert "Xvnc health check failed" in script


def test_firefox_policy_limits_content_processes_and_memory_pressure() -> None:
    policies = json.loads(
        (Path(__file__).resolve().parents[1] / "docker" / "firefox" / "policies.json").read_text(
            encoding="utf-8"
        )
    )
    preferences = policies["policies"]["Preferences"]

    assert preferences["dom.ipc.processCount"]["Value"] == 2
    assert preferences["browser.tabs.unloadOnLowMemory"]["Value"] is True
    assert preferences["browser.tabs.warnOnClose"]["Value"] is False
    assert preferences["browser.sessionstore.resume_from_crash"]["Value"] is False


def test_container_supervisor_shuts_down_cleanly_and_quarantines_crash_state() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "docker" / "firefox" / "start-session.sh"
    ).read_text(encoding="utf-8")

    assert "trap on_signal INT TERM" in script
    assert "trap cleanup EXIT" in script
    assert "ctrl+q" in script
    assert "stop_child \"$FIREFOX_PID\" \"Firefox\" 10" in script
    assert "unclean-shutdown" in script
    assert "sessionstore.jsonlz4" in script
    assert "sessionCheckpoints.json" in script
    assert "sessionstore-backups" in script
    assert "resume_from_crash" in script
    assert "dom.ipc.processCount" in script
    assert "memory.events" in script
    assert "sending SIGKILL" in script


def test_container_image_collects_crashes_and_uses_graceful_stop() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[1] / "docker" / "firefox" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "MOZ_CRASHREPORTER=1" in dockerfile
    assert "MOZ_CRASHREPORTER_NO_REPORT=1" in dockerfile
    assert "MERIDIAN_BROWSER_DIAGNOSTICS_DIR=/home/browser/.meridian/diagnostics" in dockerfile
    assert "/home/browser/.meridian/diagnostics" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile


def test_docker_uses_graceful_stop_and_restart_timeouts(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: float = 20.0, check: bool = True) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(runtime, "_run", fake_run)

    asyncio.run(runtime.remove("meridian-browser-test"))
    asyncio.run(runtime.restart("meridian-browser-test"))

    assert calls == [
        ("stop", "--time", "30", "meridian-browser-test"),
        ("rm", "--force", "meridian-browser-test"),
        ("restart", "--time", "30", "meridian-browser-test"),
    ]


def test_docker_persists_diagnostics_with_rotation(
    browser_settings: Settings,
) -> None:
    runtime = DockerRuntime(browser_settings)
    diagnostics_dir = browser_settings.browser_diagnostics_dir
    diagnostics_dir.mkdir(parents=True)

    for index in range(55):
        (diagnostics_dir / f"old-{index:02d}.log").write_text("old", encoding="utf-8")

    path = asyncio.run(
        runtime.persist_diagnostics(
            "meridian-browser-test",
            "state: exited oom_killed=True",
            reason="test failure",
        )
    )

    assert path is not None
    contents = Path(path).read_text(encoding="utf-8")
    assert "reason: test failure" in contents
    assert "state: exited oom_killed=True" in contents
    assert len(list(diagnostics_dir.glob("*.log"))) == 50
