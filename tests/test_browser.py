from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_dashboard.browser.auth import BrowserAuth, hash_password, save_accounts, verify_password
from ai_dashboard.browser.sessions import (
    _WINDOW_SEARCH,
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

    async def available(self) -> bool:
        return True

    async def start(self, spec: ContainerSpec) -> int:
        self.starts.append(spec)
        self.running.add(spec.name)
        return 41000 + len(self.starts)

    async def wait_ready(self, name: str, port: int) -> None:
        self.ready_names.append(name)
        self.ready_ports.append(port)

    async def remove(self, name: str) -> None:
        self.removals.append(name)
        self.running.discard(name)

    async def restart(self, name: str) -> None:
        self.restarts.append(name)

    async def is_running(self, name: str) -> bool:
        return name in self.running

    async def run_command(self, name: str, args: list[str]) -> None:
        self.commands.append((name, args))

    async def cleanup_orphans(self) -> None:
        self.cleaned = True


@pytest.fixture
def browser_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(
        update={
            "browser_enabled": True,
            "browser_users_file": tmp_path / "config" / "browser_users.json",
            "browser_state_file": tmp_path / "data" / "browser_state.json",
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
    assert "meridian-browser|MeridianBrowser|Firefox" in runtime.commands[0][1]
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

    async def fake_diagnostics(name: str) -> str:
        events.append("diagnostics")
        return "state: exited\nlogs:\nFirefox failed to start"

    async def fake_wait_ready(name: str, port: int) -> None:
        events.append("wait_ready")
        raise BrowserUnavailable("The browser window did not start in time.")

    monkeypatch.setattr(runtime, "_run", fake_run)
    monkeypatch.setattr(runtime, "_diagnostics", fake_diagnostics)
    monkeypatch.setattr(runtime, "wait_ready", fake_wait_ready)

    spec = ContainerSpec(
        name="meridian-browser-test",
        image="meridian-firefox:test",
        volume="meridian-browser-home-test",
        network="meridian-browser-test",
        novnc_port=6080,
        memory="900m",
        cpus="1.5",
    )

    with caplog.at_level(logging.ERROR), pytest.raises(BrowserUnavailable, match="window"):
        asyncio.run(runtime.start(spec))

    run_args = next(call for call in run_calls if call[0] == "run")
    assert "--rm" not in run_args

    cleanup_index = max(index for index, event in enumerate(events) if event == "run:rm")
    assert events.index("diagnostics") < cleanup_index
    assert "Firefox failed to start" in caplog.text
