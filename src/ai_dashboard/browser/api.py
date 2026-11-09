"""HTTP API and authenticated noVNC proxy for the private browser."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from ai_dashboard.config import Settings

from .auth import BrowserAuth
from .sessions import (
    BrowserError,
    BrowserLimitReached,
    BrowserSession,
    BrowserSessionManager,
    BrowserSessionNotFound,
    BrowserUnavailable,
    normalise_favourite_url,
    normalise_target,
)
from .store import BrowserStore, Favourite

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/browser", tags=["private-browser"])

_DISPLAY_CONNECT_TIMEOUT_SECONDS = 5.0
_DISPLAY_RETRY_DELAY_SECONDS = 0.5

BrowserAction = Literal["back", "forward", "reload", "new-tab"]


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AuthOut(BaseModel):
    user: str
    expires_at: datetime


class FavouriteIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Favourite titles cannot be empty.")
        return value

    @field_validator("url")
    @classmethod
    def clean_url(cls, value: str) -> str:
        return normalise_favourite_url(value)


class FavouritesIn(BaseModel):
    favourites: list[FavouriteIn] = Field(max_length=24)


class FavouriteOut(BaseModel):
    title: str
    url: str


class BrowserSessionOut(BaseModel):
    id: str
    created_at: datetime
    last_activity_at: datetime
    idle_timeout_minutes: int
    vnc_url: str


class BrowserStatusOut(BaseModel):
    enabled: bool
    authenticated: bool
    user: str | None
    accounts_configured: bool
    docker_available: bool
    setup_message: str | None
    max_sessions: int
    active_sessions: int
    idle_timeout_minutes: int
    session: BrowserSessionOut | None


class StartSessionIn(BaseModel):
    url: str | None = Field(default=None, max_length=2048)


class NavigateIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    new_tab: bool = False


class ActionIn(BaseModel):
    action: BrowserAction


class CloseOut(BaseModel):
    status: Literal["closed"]


class LoginThrottle:
    def __init__(self, *, limit: int = 8, window_seconds: int = 600) -> None:
        self._limit = limit
        self._window = window_seconds
        self._failures: dict[str, list[float]] = {}

    def allowed(self, key: str) -> bool:
        now = time.monotonic()
        recent = [value for value in self._failures.get(key, []) if now - value < self._window]
        self._failures[key] = recent
        return len(recent) < self._limit

    def fail(self, key: str) -> None:
        self._failures.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


_throttle = LoginThrottle()


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _auth(request: Request) -> BrowserAuth:
    return request.app.state.browser_auth  # type: ignore[no-any-return]


def _manager(request: Request) -> BrowserSessionManager:
    return request.app.state.browser_manager  # type: ignore[no-any-return]


def _store(request: Request) -> BrowserStore:
    return request.app.state.browser_store  # type: ignore[no-any-return]


def _optional_user(request: Request) -> str | None:
    token = request.cookies.get(_settings(request).browser_cookie_name)
    return _auth(request).verify_token(token)


def _require_user(request: Request) -> str:
    user = _optional_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to use the private browser.")
    return user


def _origin_allowed(origin: str, host: str | None, allowed: list[str]) -> bool:
    if origin.rstrip("/") in allowed:
        return True
    try:
        return urlsplit(origin).netloc == host
    except ValueError:
        return False


def _require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    if not _origin_allowed(origin, request.headers.get("host"), _settings(request).allowed_origins):
        raise HTTPException(status_code=403, detail="Cross-origin request rejected.")


def _vnc_url(session: BrowserSession) -> str:
    websocket_path = quote(f"api/browser/sessions/{session.id}/websockify", safe="")
    return f"/api/browser/sessions/{session.id}/vnc/meridian.html?scale=true&path={websocket_path}"


async def _fetch_display_asset(port: int, path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        return await client.get(f"http://127.0.0.1:{port}{path}")


def _session_out(session: BrowserSession, settings: Settings) -> BrowserSessionOut:
    return BrowserSessionOut(
        id=session.id,
        created_at=datetime.fromtimestamp(session.created_at, UTC),
        last_activity_at=datetime.fromtimestamp(session.last_activity, UTC),
        idle_timeout_minutes=settings.browser_idle_timeout_minutes,
        vnc_url=_vnc_url(session),
    )


def _safe_target(value: str) -> str:
    try:
        return normalise_target(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _setup_message(settings: Settings, auth: BrowserAuth, docker_available: bool) -> str | None:
    if not settings.browser_enabled:
        return "The private browser is disabled on this Meridian instance."
    if not auth.accounts_configured():
        return (
            "No browser accounts are configured. Run "
            "`uv run python -m ai_dashboard.browser.auth set-user <name>` on the Pi."
        )
    if not docker_available:
        return "Docker is unavailable, so the browser container cannot be started."
    return None


@router.get("/status", response_model=BrowserStatusOut)
async def status(request: Request) -> BrowserStatusOut:
    settings = _settings(request)
    auth = _auth(request)
    manager = _manager(request)
    user = _optional_user(request)
    docker_available = await manager.available() if settings.browser_enabled else False
    session = manager.for_user(user) if user else None
    return BrowserStatusOut(
        enabled=settings.browser_enabled,
        authenticated=user is not None,
        user=user,
        accounts_configured=auth.accounts_configured(),
        docker_available=docker_available,
        setup_message=_setup_message(settings, auth, docker_available),
        max_sessions=settings.browser_max_sessions,
        active_sessions=manager.active_count,
        idle_timeout_minutes=settings.browser_idle_timeout_minutes,
        session=_session_out(session, settings) if session else None,
    )


@router.post("/auth/login", response_model=AuthOut)
async def login(request: Request, payload: LoginIn, response: Response) -> AuthOut:
    _require_same_origin(request)
    settings = _settings(request)
    auth = _auth(request)
    if not settings.browser_enabled:
        raise HTTPException(status_code=503, detail="The private browser is disabled.")
    if not auth.accounts_configured():
        raise HTTPException(status_code=503, detail="No browser accounts are configured.")

    key = request.client.host if request.client else "unknown"
    if not _throttle.allowed(key):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")
    if not auth.authenticate(payload.username, payload.password):
        _throttle.fail(key)
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    _throttle.reset(key)

    token, expires = auth.issue_token(payload.username)
    response.set_cookie(
        key=settings.browser_cookie_name,
        value=token,
        max_age=settings.browser_session_ttl_hours * 3600,
        httponly=True,
        secure=settings.browser_cookie_secure,
        samesite="lax",
        path="/",
    )
    return AuthOut(user=payload.username, expires_at=datetime.fromtimestamp(expires, UTC))


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    _require_same_origin(request)
    user = _optional_user(request)
    if user is not None:
        manager = _manager(request)
        session = manager.for_user(user)
        if session is not None:
            await manager.remove(session.id, user)
    response.delete_cookie(_settings(request).browser_cookie_name, path="/")
    return {"status": "signed-out"}


@router.get("/favourites", response_model=list[FavouriteOut])
async def get_favourites(request: Request) -> list[FavouriteOut]:
    user = _require_user(request)
    return [
        FavouriteOut(title=favourite.title, url=favourite.url)
        for favourite in _store(request).get_favourites(user)
    ]


@router.put("/favourites", response_model=list[FavouriteOut])
async def put_favourites(request: Request, payload: FavouritesIn) -> list[FavouriteOut]:
    _require_same_origin(request)
    user = _require_user(request)
    seen: set[str] = set()
    favourites: list[Favourite] = []
    for item in payload.favourites:
        if item.url in seen:
            continue
        seen.add(item.url)
        favourites.append(Favourite(title=item.title, url=item.url))
    saved = _store(request).set_favourites(user, favourites)
    return [FavouriteOut(title=favourite.title, url=favourite.url) for favourite in saved]


@router.post("/sessions", response_model=BrowserSessionOut, status_code=201)
async def start_session(
    request: Request, payload: StartSessionIn | None = None
) -> BrowserSessionOut:
    _require_same_origin(request)
    user = _require_user(request)
    settings = _settings(request)
    manager = _manager(request)
    session = await manager.get_or_create(user)
    target = payload.url if payload else None
    if target:
        session = await manager.navigate(session.id, user, _safe_target(target), new_tab=False)
    return _session_out(session, settings)


@router.post("/sessions/{session_id}/navigate", response_model=BrowserSessionOut)
async def navigate(request: Request, session_id: str, payload: NavigateIn) -> BrowserSessionOut:
    _require_same_origin(request)
    user = _require_user(request)
    session = await _manager(request).navigate(
        session_id, user, _safe_target(payload.url), new_tab=payload.new_tab
    )
    return _session_out(session, _settings(request))


@router.post("/sessions/{session_id}/action", response_model=BrowserSessionOut)
async def action(request: Request, session_id: str, payload: ActionIn) -> BrowserSessionOut:
    _require_same_origin(request)
    user = _require_user(request)
    session = await _manager(request).action(session_id, user, payload.action)
    return _session_out(session, _settings(request))


@router.post("/sessions/{session_id}/restart", response_model=BrowserSessionOut)
async def restart_session(request: Request, session_id: str) -> BrowserSessionOut:
    _require_same_origin(request)
    user = _require_user(request)
    session = await _manager(request).restart(session_id, user)
    return _session_out(session, _settings(request))


@router.delete("/sessions/{session_id}", response_model=CloseOut)
async def close_session(request: Request, session_id: str) -> CloseOut:
    _require_same_origin(request)
    user = _require_user(request)
    await _manager(request).remove(session_id, user)
    return CloseOut(status="closed")


@router.get("/sessions/{session_id}/vnc/{asset_path:path}", include_in_schema=False)
async def vnc_asset(request: Request, session_id: str, asset_path: str) -> Response:
    user = _require_user(request)
    manager = _manager(request)
    session = manager.touch(session_id, user)
    path = f"/{quote(asset_path, safe='/')}"
    if request.url.query:
        path = f"{path}?{request.url.query}"
    try:
        upstream = await _fetch_display_asset(session.port, path)
    except httpx.HTTPError as first_error:
        logger.warning(
            "Display asset request failed for %s on port %s: %s",
            session.container,
            session.port,
            first_error,
        )
        await asyncio.sleep(_DISPLAY_RETRY_DELAY_SECONDS)
        try:
            upstream = await _fetch_display_asset(session.port, path)
        except httpx.HTTPError:
            try:
                session = await manager.recover_display(
                    session_id, user, reason=f"noVNC asset proxy failed: {path}"
                )
                upstream = await _fetch_display_asset(session.port, path)
            except BrowserError as exc:
                logger.error("Could not recover browser display for %s: %s", session.container, exc)
                raise BrowserUnavailable("The browser display is not responding.") from exc
            except httpx.HTTPError as retry_error:
                await manager.report_display_failure(
                    session_id,
                    user,
                    reason=f"noVNC asset proxy still failed after recovery: {retry_error}",
                )
                raise BrowserUnavailable("The browser display is not responding.") from retry_error
    headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() in {"content-type", "cache-control", "pragma", "expires"}
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=headers)


async def _connect_upstream(session: BrowserSession, websocket: WebSocket) -> ClientConnection:
    return await connect(
        f"ws://127.0.0.1:{session.port}/websockify",
        subprotocols=websocket.scope.get("subprotocols") or None,
        max_size=None,
        ping_interval=20,
        ping_timeout=20,
        open_timeout=_DISPLAY_CONNECT_TIMEOUT_SECONDS,
    )


@router.websocket("/sessions/{session_id}/websockify")
async def websockify(websocket: WebSocket, session_id: str) -> None:
    settings: Settings = websocket.app.state.settings
    auth: BrowserAuth = websocket.app.state.browser_auth
    manager: BrowserSessionManager = websocket.app.state.browser_manager
    origin = websocket.headers.get("origin")
    if origin and not _origin_allowed(
        origin, websocket.headers.get("host"), settings.allowed_origins
    ):
        await websocket.close(code=4403)
        return
    user = auth.verify_token(websocket.cookies.get(settings.browser_cookie_name))
    if user is None:
        await websocket.close(code=4401)
        return
    try:
        session = manager.touch(session_id, user)
    except BrowserSessionNotFound:
        await websocket.close(code=4404)
        return

    try:
        upstream = await _connect_upstream(session, websocket)
    except Exception as first_error:
        logger.warning(
            "WebSocket display connection failed for %s on port %s: %s",
            session.container,
            session.port,
            first_error,
        )
        await asyncio.sleep(_DISPLAY_RETRY_DELAY_SECONDS)
        try:
            upstream = await _connect_upstream(session, websocket)
        except Exception:
            try:
                session = await manager.recover_display(
                    session_id, user, reason="noVNC WebSocket proxy connection failed"
                )
                upstream = await _connect_upstream(session, websocket)
            except BrowserError as exc:
                logger.error("Could not recover browser display for %s: %s", session.container, exc)
                await websocket.close(code=1011)
                return
            except Exception as exc:
                await manager.report_display_failure(
                    session_id,
                    user,
                    reason=f"noVNC WebSocket proxy still failed after recovery: {exc}",
                )
                await websocket.close(code=1011)
                return

    await websocket.accept(subprotocol=upstream.subprotocol)
    logger.info(
        "noVNC proxy connected for %s on port %s (client=%s subprotocol=%s)",
        session.container,
        session.port,
        websocket.client.host if websocket.client else "unknown",
        upstream.subprotocol or "none",
    )

    client_close: str | None = None
    upstream_close: str | None = None

    async def client_to_upstream() -> None:
        nonlocal client_close, upstream_close
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    client_close = (
                        f"code={message.get('code')} reason={message.get('reason') or 'none'}"
                    )
                    break
                data = message.get("bytes")
                text = message.get("text")
                if data is not None:
                    await upstream.send(data)
                elif text is not None:
                    await upstream.send(text)
                manager.touch(session_id, user)
        except BrowserSessionNotFound:
            client_close = "session removed while display was connected"
        except WebSocketDisconnect as exc:
            client_close = f"code={exc.code} reason={exc.reason or 'none'}"
        except ConnectionClosed as exc:
            upstream_close = f"code={getattr(exc, 'code', None)} reason={exc.reason or 'none'}"
        except Exception:
            logger.exception("noVNC client-to-upstream proxy failed for %s", session.container)
            raise

    async def upstream_to_client() -> None:
        nonlocal client_close, upstream_close
        try:
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
        except (WebSocketDisconnect, ConnectionClosed) as exc:
            if isinstance(exc, WebSocketDisconnect):
                client_close = f"code={exc.code} reason={exc.reason or 'none'}"
            else:
                upstream_close = f"code={getattr(exc, 'code', None)} reason={exc.reason or 'none'}"
        except Exception:
            logger.exception("noVNC upstream-to-client proxy failed for %s", session.container)
            raise

    tasks = {
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("noVNC proxy task ended with an error for %s", session.container)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await upstream.close()
    with contextlib.suppress(RuntimeError):
        await websocket.close()
    logger.info(
        "noVNC proxy closed for %s (client=%s upstream=%s)",
        session.container,
        client_close or "still open",
        upstream_close or "still open",
    )


def register_browser_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BrowserSessionNotFound)
    async def _not_found(_request: Request, _exc: BrowserSessionNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Browser session not found."})

    @app.exception_handler(BrowserLimitReached)
    async def _limit(_request: Request, exc: BrowserLimitReached) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(BrowserUnavailable)
    async def _unavailable(_request: Request, exc: BrowserUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(BrowserError)
    async def _browser_error(_request: Request, exc: BrowserError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
