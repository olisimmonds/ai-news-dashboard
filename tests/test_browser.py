from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai_dashboard.browser import api as browser_api
from ai_dashboard.browser.auth import BrowserAuth, hash_password, save_accounts, verify_password
from ai_dashboard.browser.sessions import (
    _WINDOW_SEARCH,
    BrowserError,
    BrowserLimitReached,
    BrowserSessionManager,
    BrowserUnavailable,
    ContainerSpec,
    DockerCommandError,
    DockerRuntime,
    normalise_target,
    parse_memory_limit,
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
        self.remove_error: BrowserError | None = None

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
        if self.remove_error is not None:
            raise self.remove_error
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


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"browser_memory_limit": "banana"}, "memory and shm limits"),
        ({"browser_memory_limit": "1.5"}, "memory and shm limits"),
        ({"browser_shm_size": "512 mb"}, "memory and shm limits"),
        ({"browser_cpus": "0"}, "CPU limit"),
        ({"browser_cpus": "fast"}, "CPU limit"),
    ],
)
def test_browser_resource_settings_reject_invalid_limits(
    browser_settings: Settings,
    overrides: dict[str, str],
    message: str,
) -> None:
    base = browser_settings.model_dump()

    with pytest.raises(ValidationError, match=message):
        Settings.model_validate({**base, **overrides})


def test_browser_resource_settings_accept_valid_limits(browser_settings: Settings) -> None:
    configured = Settings.model_validate(
        {
            **browser_settings.model_dump(),
            "browser_memory_limit": "1.5g",
            "browser_shm_size": "256m",
            "browser_cpus": "2",
        }
    )

    assert configured.browser_memory_limit == "1.5g"
    assert configured.browser_shm_size == "256m"
    assert configured.browser_cpus == "2"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1200m", 1258291200),
        ("512M", 536870912),
        ("1.5g", 1610612736),
        ("2048k", 2097152),
        ("1048576", 1048576),
        ("1b", 1),
    ],
)
def test_parse_memory_limit(value: str, expected: int) -> None:
    assert parse_memory_limit(value) == expected


@pytest.mark.parametrize("value", ["", "banana", "0m", "-1m"])
def test_parse_memory_limit_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_memory_limit(value)


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
    assert "/vnc/meridian.html" in payload["vnc_url"]
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


def test_vnc_asset_reports_display_failure_without_restart(
    browser_settings: Settings,
    sources: list[SourceConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, runtime = make_app(browser_settings, sources)
    calls = 0

    async def fake_fetch(port: int, path: str) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("websockify is not responding")

    monkeypatch.setattr(browser_api, "_fetch_display_asset", fake_fetch)

    with client:
        login(client)
        session = client.post("/api/browser/sessions", json={}).json()
        response = client.get(f"/api/browser/sessions/{session['id']}/vnc/vnc_lite.html")

    assert response.status_code == 503
    assert calls == 1
    assert runtime.restarts == []
    assert runtime.diagnostics_calls == [runtime.starts[0].name]
    assert len(runtime.starts) == 1
    assert runtime.starts[0].volume.startswith("meridian-browser-home-")
    assert runtime.persisted == [
        (
            runtime.starts[0].name,
            "display failure on port 41001: noVNC asset proxy failed for "
            "/vnc_lite.html: websockify is not responding",
        )
    ]


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


async def test_manager_restart_propagates_failure_without_recreating(
    browser_settings: Settings,
) -> None:
    runtime = FakeRuntime()
    manager = BrowserSessionManager(browser_settings, runtime)
    session = await manager.get_or_create("alice")
    runtime.fail_ready = True

    with pytest.raises(BrowserUnavailable, match="window"):
        await manager.restart(session.id, "alice")

    assert runtime.restarts == [session.container]
    assert runtime.removals == []
    assert len(runtime.starts) == 1
    assert runtime.diagnostics_calls == [session.container]
    assert runtime.persisted[0][1].startswith("restart failed: ")


async def test_manager_reports_display_failure_without_restart(
    browser_settings: Settings,
) -> None:
    runtime = FakeRuntime()
    manager = BrowserSessionManager(browser_settings, runtime)
    session = await manager.get_or_create("alice")

    await manager.report_display_failure(session.id, "alice", reason="noVNC asset request failed")

    assert runtime.restarts == []
    assert runtime.diagnostics_calls == [session.container]
    assert runtime.persisted == [
        (session.container, "display failure on port 41001: noVNC asset request failed")
    ]
    assert len(runtime.starts) == 1


async def test_manager_remove_propagates_docker_failure(browser_settings: Settings) -> None:
    runtime = FakeRuntime()
    manager = BrowserSessionManager(browser_settings, runtime)
    session = await manager.get_or_create("alice")
    runtime.remove_error = DockerCommandError(
        ("rm", "--force", session.container), "", "device or resource busy", 1
    )

    with pytest.raises(DockerCommandError, match="device or resource busy"):
        await manager.remove(session.id, "alice")

    assert manager.active_count == 0


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
        events.append(f"execute:{args[0]}")
        return "", "", 0

    async def fake_diagnostics(name: str) -> str:
        events.append("diagnostics")
        return "state: exited\nlogs:\nFirefox failed to start"

    async def fake_wait_ready(name: str, port: int) -> None:
        events.append("wait_ready")
        raise BrowserUnavailable("The browser window did not start in time.")

    async def fake_verify_limits(name: str, spec: ContainerSpec) -> None:
        events.append("verify_limits")

    monkeypatch.setattr(runtime, "_execute", fake_execute)
    monkeypatch.setattr(runtime, "_run", fake_run)
    monkeypatch.setattr(runtime, "_diagnostics", fake_diagnostics)
    monkeypatch.setattr(runtime, "_verify_limits", fake_verify_limits)
    monkeypatch.setattr(runtime, "wait_ready", fake_wait_ready)

    spec = ContainerSpec(
        name="meridian-browser-test",
        image="meridian-firefox:test",
        volume="meridian-browser-home-test",
        network="meridian-browser-test",
        novnc_port=6080,
        memory="1200m",
        cpus="1.5",
        pids_limit=768,
        shm_size="512m",
    )

    with caplog.at_level(logging.ERROR), pytest.raises(BrowserUnavailable, match="window"):
        asyncio.run(runtime.start(spec))

    run_args = next(call for call in run_calls if call[0] == "run")
    assert "--rm" not in run_args
    assert "--stop-timeout" in run_args
    assert run_args[run_args.index("--stop-timeout") + 1] == "30"
    assert run_args[run_args.index("--pids-limit") + 1] == "768"
    assert run_args[run_args.index("--shm-size") + 1] == "512m"

    cleanup_index = max(
        index for index, event in enumerate(events) if event in {"run:rm", "execute:rm"}
    )
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


def test_docker_is_running_distinguishes_missing_from_docker_failure(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)

    async def missing(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        return "", "Error response from daemon: No such container: test", 1

    monkeypatch.setattr(runtime, "_execute", missing)
    assert asyncio.run(runtime.is_running("test")) is False

    async def unavailable(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        return "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock", 1

    monkeypatch.setattr(runtime, "_execute", unavailable)
    with pytest.raises(DockerCommandError, match="Cannot connect to the Docker daemon"):
        asyncio.run(runtime.is_running("test"))


def test_docker_only_creates_network_when_it_is_missing(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)
    created: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: float = 20.0, check: bool = True) -> str:
        created.append(args)
        return ""

    async def missing(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        return "", f"network {browser_settings.browser_network} not found", 1

    monkeypatch.setattr(runtime, "_run", fake_run)
    monkeypatch.setattr(runtime, "_execute", missing)
    asyncio.run(runtime._ensure_network())
    assert created[0][:2] == ("network", "create")

    created.clear()

    async def unavailable(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        return "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock", 1

    monkeypatch.setattr(runtime, "_execute", unavailable)
    with pytest.raises(DockerCommandError, match="Could not inspect Docker network"):
        asyncio.run(runtime._ensure_network())
    assert created == []


def test_docker_verifies_enforced_cgroup_limits(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)
    spec = ContainerSpec(
        name="meridian-browser-test",
        image="meridian-firefox:test",
        volume="meridian-browser-home-test",
        network="meridian-browser-test",
        novnc_port=6080,
        memory="1200m",
        cpus="1.5",
        pids_limit=768,
        shm_size="512m",
    )

    async def enforced(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        if args[0] == "inspect":
            return json.dumps({"Memory": 1258291200, "PidsLimit": 768}), "", 0
        return "memory_max=1258291200\npids_max=768\n", "", 0

    monkeypatch.setattr(runtime, "_execute", enforced)
    asyncio.run(runtime._verify_limits(spec.name, spec))

    async def unenforced(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        if args[0] == "inspect":
            return json.dumps({"Memory": 0, "PidsLimit": 768}), "", 0
        return "memory_max=max\npids_max=768\n", "", 0

    monkeypatch.setattr(runtime, "_execute", unenforced)
    with pytest.raises(BrowserUnavailable, match="memory limit"):
        asyncio.run(runtime._verify_limits(spec.name, spec))

    async def host_pids_unenforced(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        if args[0] == "inspect":
            return json.dumps({"Memory": 1258291200, "PidsLimit": 999}), "", 0
        return "memory_max=1258291200\npids_max=768\n", "", 0

    monkeypatch.setattr(runtime, "_execute", host_pids_unenforced)
    with pytest.raises(BrowserUnavailable, match="pids limit"):
        asyncio.run(runtime._verify_limits(spec.name, spec))

    async def cgroup_pids_unenforced(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        if args[0] == "inspect":
            return json.dumps({"Memory": 1258291200, "PidsLimit": 768}), "", 0
        return "memory_max=1258291200\npids_max=max\n", "", 0

    monkeypatch.setattr(runtime, "_execute", cgroup_pids_unenforced)
    with pytest.raises(BrowserUnavailable, match="pids limit"):
        asyncio.run(runtime._verify_limits(spec.name, spec))


def test_cleanup_orphans_preserves_diagnostics_before_removal(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)
    events: list[str] = []

    async def fake_run(*args: str, timeout: float = 20.0, check: bool = True) -> str:
        if args[:2] == ("ps", "--all"):
            return "meridian-browser-orphan\n"
        return ""

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        return "", "", 0

    async def fake_diagnostics(name: str) -> str:
        events.append("diagnostics")
        return "state: exited"

    async def fake_persist(name: str, details: str, *, reason: str) -> str | None:
        events.append(f"persist:{reason}")
        return "/tmp/diagnostics.log"

    async def fake_remove(name: str) -> None:
        events.append("remove")

    monkeypatch.setattr(runtime, "_run", fake_run)
    monkeypatch.setattr(runtime, "_execute", fake_execute)
    monkeypatch.setattr(runtime, "_diagnostics", fake_diagnostics)
    monkeypatch.setattr(runtime, "persist_diagnostics", fake_persist)
    monkeypatch.setattr(runtime, "remove", fake_remove)

    asyncio.run(runtime.cleanup_orphans())

    assert events == [
        "diagnostics",
        "persist:orphan cleanup during startup",
        "remove",
    ]


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
                "HostConfig": {
                    "Memory": 1258291200,
                    "PidsLimit": 768,
                    "ShmSize": 536870912,
                },
                "Config": {"StopTimeout": 30},
                "RestartCount": 2,
            }
            return json.dumps(state), "", 0
        if args[0] == "info":
            return (
                json.dumps(
                    {
                        "CgroupDriver": "systemd",
                        "CgroupVersion": "2",
                        "MemoryLimit": True,
                        "SwapLimit": True,
                        "PidsLimit": True,
                        "ServerVersion": "28.0.0",
                        "Warnings": [],
                    }
                ),
                "",
                0,
            )
        if args[0] == "exec":
            return (
                (
                    "memory.events:\noom 1\noom_kill 1\n"
                    "memory.max:\n1258291200\n"
                    "memory.current:\n1048576000\n"
                    "pids.current:\n42\n"
                    "pids.max:\n768\n"
                    "pids.events:\nmax 3\n"
                ),
                "",
                0,
            )
        if args[0] == "top":
            return (
                "PID PPID NLWP COMMAND\n1 0 12 Xvnc\n2 0 8 websockify\n3 1 44 firefox-esr",
                "",
                0,
            )
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
    assert "configured_memory_limit=1200m" in diagnostics
    assert "pids_limit=768" in diagnostics
    assert "configured_pids_limit=768" in diagnostics
    assert "shm_size=536870912" in diagnostics
    assert "stop_timeout=30" in diagnostics
    assert "cgroup_version=2" in diagnostics
    assert "memory_limit_support=True" in diagnostics
    assert "memory.events:\noom 1" in diagnostics
    assert "memory.max:\n1258291200" in diagnostics
    assert "memory.current:\n1048576000" in diagnostics
    assert "pids.current:\n42" in diagnostics
    assert "pids.max:\n768" in diagnostics
    assert "pids.events:\nmax 3" in diagnostics
    assert "memory=1.1GiB / 1.2GiB" in diagnostics
    assert "NLWP" in diagnostics
    assert "websockify" in diagnostics
    assert "Firefox content process terminated" in diagnostics


def test_container_supervisor_fails_fast_when_display_processes_exit() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "docker" / "firefox" / "start-session.sh"
    ).read_text(encoding="utf-8")

    assert script.startswith("#!/bin/bash")
    assert "wait -n -p" in script
    assert "restarting unresponsive websockify" not in script
    assert "websockify exited with status" in script
    assert 'persist_snapshot "websockify-health-failure"' in script
    assert 'persist_snapshot "openbox-exit-${wait_status}"' in script
    assert "Xvnc health check failed" in script


def test_firefox_policy_limits_content_processes_and_memory_pressure() -> None:
    policies = json.loads(
        (Path(__file__).resolve().parents[1] / "docker" / "firefox" / "policies.json").read_text(
            encoding="utf-8"
        )
    )
    preferences = policies["policies"]["Preferences"]

    assert "dom.ipc.forkserver.enable" not in preferences
    assert preferences["dom.ipc.processCount"]["Value"] == 2
    assert preferences["dom.ipc.processCount.file"]["Value"] == 1
    assert preferences["dom.ipc.processCount.extension"]["Value"] == 1
    assert preferences["dom.ipc.processCount.privilegedabout"]["Value"] == 1
    assert preferences["dom.ipc.processCount.privilegedmozilla"]["Value"] == 1
    assert preferences["dom.ipc.processCount.webIsolated"]["Value"] == 1
    assert preferences["dom.ipc.processCount.webServiceWorker"]["Value"] == 1
    assert preferences["browser.tabs.unloadOnLowMemory"]["Value"] is True
    assert preferences["browser.tabs.warnOnClose"]["Value"] is False
    assert preferences["browser.sessionstore.resume_from_crash"]["Value"] is False


def test_container_supervisor_shuts_down_and_preserves_crash_evidence() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "docker" / "firefox" / "start-session.sh"
    ).read_text(encoding="utf-8")

    assert "trap on_signal INT TERM" in script
    assert "trap cleanup EXIT" in script
    assert "ctrl+q" in script
    assert 'stop_child "$FIREFOX_PID" "Firefox" 10' in script
    assert "unclean-shutdown" not in script
    assert "session-quarantine" not in script
    assert "profile-path" in script
    assert "profiles.ini" in script
    assert '--profile "$PROFILE_DIR"' in script
    assert "sessionstore.jsonlz4" not in script
    assert 'mv "$profile/$name"' not in script
    assert "resume_from_crash" in script
    assert "dom.ipc.forkserver.enable" not in script
    assert "dom.ipc.processCount" in script
    assert "memory.events" in script
    assert "sending SIGKILL" in script
    assert "collect_crash_artifacts" in script
    assert "process-samples" in script
    assert "nlwp" in script
    assert "pids.current" in script
    assert "pids.events" in script
    assert "thread-summary" in script
    assert "firefox-esr --version" in script
    assert "dpkg-query" in script
    assert "firefox-esr-version" in script
    assert 'exit "$SHUTDOWN_STATUS"' in script


def test_container_image_collects_crashes_and_uses_graceful_stop() -> None:
    dockerfile = (
        Path(__file__).resolve().parents[1] / "docker" / "firefox" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "MOZ_CRASHREPORTER=1" in dockerfile
    assert "MOZ_CRASHREPORTER_NO_REPORT=1" in dockerfile
    assert "MOZ_CRASHREPORTER_SHUTDOWN=1" in dockerfile
    assert "ARG FIREFOX_ESR_VERSION=" in dockerfile
    assert "FIREFOX_ESR_VERSION" in dockerfile
    assert "firefox-esr-version" in dockerfile
    assert "MERIDIAN_BROWSER_DIAGNOSTICS_DIR=/home/browser/.meridian/diagnostics" in dockerfile
    assert "/home/browser/.meridian/diagnostics" in dockerfile
    assert "COPY meridian.html /usr/share/novnc/meridian.html" in dockerfile
    assert "STOPSIGNAL SIGTERM" in dockerfile


def test_custom_novnc_page_reconnects_after_display_disconnect() -> None:
    page = (Path(__file__).resolve().parents[1] / "docker" / "firefox" / "meridian.html").read_text(
        encoding="utf-8"
    )

    assert "MAX_RECONNECT_ATTEMPTS = 2" in page
    assert "scheduleReconnect" in page
    assert "reconnectExhausted" in page
    assert 'addEventListener("disconnect"' in page
    assert "meridian-browser-reconnect-failed" in page
    assert "rfb.scaleViewport" in page


def test_docker_uses_graceful_stop_and_restart_timeouts(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)
    calls: list[tuple[str, ...]] = []

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        calls.append(args)
        return "", "", 0

    async def fake_run(*args: str, timeout: float = 20.0, check: bool = True) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(runtime, "_execute", fake_execute)
    monkeypatch.setattr(runtime, "_run", fake_run)

    asyncio.run(runtime.remove("meridian-browser-test"))
    asyncio.run(runtime.restart("meridian-browser-test"))

    assert calls == [
        (
            "network",
            "disconnect",
            "--force",
            browser_settings.browser_network,
            "meridian-browser-test",
        ),
        ("stop", "--time", "30", "meridian-browser-test"),
        ("rm", "--force", "meridian-browser-test"),
        ("restart", "--time", "30", "meridian-browser-test"),
    ]


def test_docker_cleanup_removes_stale_browser_network_endpoints(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, timeout: float = 20.0, check: bool = True) -> str:
        calls.append(args)
        if args[:2] == ("ps", "--all"):
            return "meridian-browser-live\n"
        if args[:2] == ("network", "inspect"):
            return json.dumps(
                {
                    "one": {"Name": "meridian-browser-stale"},
                    "two": {"Name": "other-service"},
                }
            )
        return ""

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        calls.append(args)
        if args[:2] == ("network", "inspect"):
            return (
                json.dumps(
                    {
                        "one": {"Name": "meridian-browser-stale"},
                        "two": {"Name": "other-service"},
                    }
                ),
                "",
                0,
            )
        if args[0] == "inspect":
            if "meridian-browser-live" in args:
                return "{}", "", 0
            return "", "No such container", 1
        return "", "", 0

    monkeypatch.setattr(runtime, "_run", fake_run)
    monkeypatch.setattr(runtime, "_execute", fake_execute)

    asyncio.run(runtime.cleanup_orphans())

    assert (
        "network",
        "disconnect",
        "--force",
        browser_settings.browser_network,
        "meridian-browser-live",
    ) in calls
    assert (
        "network",
        "disconnect",
        "--force",
        browser_settings.browser_network,
        "meridian-browser-stale",
    ) in calls
    assert (
        "network",
        "disconnect",
        "--force",
        browser_settings.browser_network,
        "other-service",
    ) not in calls


def test_manager_records_diagnostics_when_container_exits(
    browser_settings: Settings,
) -> None:
    runtime = FakeRuntime()
    manager = BrowserSessionManager(browser_settings, runtime)

    async def scenario() -> tuple[str, str]:
        session = await manager.get_or_create("alice")
        runtime.running.discard(session.container)
        await manager._check_sessions()
        await manager._check_sessions()
        return session.id, session.container

    _, container = asyncio.run(scenario())
    assert manager.for_user("alice") is not None
    assert runtime.diagnostics_calls == [container]
    assert runtime.persisted == [(container, "container exited while session was active")]


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


def test_docker_exports_crash_artifacts_before_persisting(
    browser_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = DockerRuntime(browser_settings)
    calls: list[tuple[str, ...]] = []

    async def fake_execute(*args: str, timeout: float = 20.0) -> tuple[str, str, int]:
        calls.append(args)
        return "", "", 0

    monkeypatch.setattr(runtime, "_execute", fake_execute)

    path = asyncio.run(
        runtime.persist_diagnostics(
            "meridian-browser-test",
            "state: exited 245",
            reason="firefox-exit-245",
        )
    )

    assert path is not None
    assert calls[0][0] == "cp"
    assert calls[0][1] == "meridian-browser-test:/home/browser/.meridian/diagnostics/."
    contents = Path(path).read_text(encoding="utf-8")
    assert "artifacts_dir: " in contents
    artifacts_dir = Path(contents.split("artifacts_dir: ", 1)[1].splitlines()[0])
    assert artifacts_dir.is_dir()


def test_stability_script_covers_profiles_sites_and_crash_artifacts() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "browser-stability-test.sh"
    ).read_text(encoding="utf-8")

    assert "--matrix" in script
    assert "clean|persistent" in script
    assert "blank|bbc|other|custom" in script
    assert "MOZ_CRASHREPORTER_SHUTDOWN=0" in script
    assert "minidumps" in script
    assert "HostConfig.Memory" in script
    assert "EXPECTED_MEMORY_BYTES" in script
    assert "PIDS_LIMIT" in script
    assert "EXPECTED_PIDS_LIMIT" in script
    assert "HostConfig.PidsLimit" in script
    assert "cgroup_pids_current" in script
    assert "thread_total" in script
    assert "SIGSEGV" in script
