import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorState } from "../components/EmptyState";
import {
  ArrowLeft,
  ArrowRight,
  Home,
  Maximize,
  Plus,
  Refresh,
  Search,
  Trash,
} from "../components/icons";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import {
  browserSessionAction,
  closeBrowserSession,
  getBrowserFavourites,
  getBrowserStatus,
  loginBrowser,
  logoutBrowser,
  navigateBrowserSession,
  restartBrowserSession,
  startBrowserSession,
} from "../lib/api";
import type { BrowserAction, BrowserFavourite, BrowserSession, BrowserStatus } from "../lib/types";

const HOME_URL = "https://duckduckgo.com/";

type BusyState = "login" | "logout" | "start" | "navigate" | "action" | "restart" | "close" | null;

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback;
}

function isMissingSession(cause: unknown): boolean {
  return (
    typeof cause === "object" &&
    cause !== null &&
    "status" in cause &&
    (cause as { status?: unknown }).status === 404
  );
}

function favouriteHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

interface LoginPanelProps {
  busy: boolean;
  dockerAvailable: boolean;
  onLogin: (username: string, password: string) => Promise<void>;
}

function LoginPanel({ busy, dockerAvailable, onLogin }: LoginPanelProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onLogin(username, password);
  }

  return (
    <section className="browser-auth" aria-labelledby="browser-auth-title">
      <div className="browser-auth-copy">
        <span className="eyebrow">Private access</span>
        <h2 id="browser-auth-title">Sign in to the browser</h2>
        <p>
          Firefox runs on your Raspberry Pi. This page only sends the display and receives your
          clicks and typing. Each account gets its own saved browser profile.
        </p>
        <p className="browser-auth-note">
          The browser is intended to be reachable only through your private Tailscale network.
          Websites still run on the Pi, so use the same care you would on any computer.
        </p>
      </div>

      <form className="browser-auth-form" onSubmit={(event) => void submit(event)}>
        <label className="browser-field">
          <span>Username</span>
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            required
            maxLength={64}
          />
        </label>
        <label className="browser-field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
            maxLength={256}
          />
        </label>
        {!dockerAvailable ? (
          <p className="browser-warning">
            Docker is not available on the Pi yet, so the browser cannot start after signing in.
          </p>
        ) : null}
        <button type="submit" className="button-secondary" disabled={busy}>
          {busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </section>
  );
}

export function BrowserPage() {
  useDocumentTitle("Private Browser");
  const [status, setStatus] = useState<BrowserStatus | null>(null);
  const [favourites, setFavourites] = useState<BrowserFavourite[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<BusyState>(null);
  const [address, setAddress] = useState("");
  const [frameKey, setFrameKey] = useState(0);
  const frameRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setLoadError(null);
    try {
      const next = await getBrowserStatus({ signal });
      if (signal?.aborted) return;
      setStatus(next);
      if (next.authenticated) {
        try {
          const saved = await getBrowserFavourites({ signal });
          if (!signal?.aborted) setFavourites(saved);
        } catch {
          if (!signal?.aborted) setFavourites([]);
        }
      } else {
        setFavourites([]);
      }
    } catch (cause) {
      if (signal?.aborted) return;
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setLoadError(messageOf(cause, "Could not reach the browser service."));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  function updateSession(session: BrowserSession) {
    setStatus((current) => (current ? { ...current, session } : current));
  }

  function resetSession(message: string | null) {
    setStatus((current) =>
      current
        ? {
            ...current,
            session: null,
            active_sessions: Math.max(0, current.active_sessions - 1),
          }
        : current,
    );
    if (message) setActionError(message);
    void load();
  }

  async function signIn(username: string, password: string) {
    setBusy("login");
    setActionError(null);
    try {
      await loginBrowser(username, password);
      await load();
    } catch (cause) {
      setActionError(messageOf(cause, "Could not sign in."));
    } finally {
      setBusy(null);
    }
  }

  async function signOut() {
    setBusy("logout");
    setActionError(null);
    const session = status?.session;
    try {
      if (session) {
        await closeBrowserSession(session.id).catch(() => undefined);
      }
      await logoutBrowser();
      await load();
    } catch (cause) {
      setActionError(messageOf(cause, "Could not sign out."));
    } finally {
      setBusy(null);
    }
  }

  async function start(target?: string) {
    setBusy("start");
    setActionError(null);
    try {
      const session = await startBrowserSession(target?.trim() || undefined);
      setStatus((current) =>
        current
          ? {
              ...current,
              session,
              active_sessions: current.session
                ? current.active_sessions
                : current.active_sessions + 1,
            }
          : current,
      );
      setAddress("");
      setFrameKey((value) => value + 1);
    } catch (cause) {
      setActionError(messageOf(cause, "Could not start the private browser."));
    } finally {
      setBusy(null);
    }
  }

  async function navigateTo(target: string) {
    const session = status?.session;
    if (!session || !target.trim()) return;
    setBusy("navigate");
    setActionError(null);
    try {
      updateSession(await navigateBrowserSession(session.id, target.trim()));
      setAddress("");
    } catch (cause) {
      if (isMissingSession(cause)) {
        resetSession("The browser session ended. Start a new one.");
      } else {
        setActionError(messageOf(cause, "Could not open that page."));
      }
    } finally {
      setBusy(null);
    }
  }

  async function sendAction(action: BrowserAction) {
    const session = status?.session;
    if (!session) return;
    setBusy("action");
    setActionError(null);
    try {
      updateSession(await browserSessionAction(session.id, action));
    } catch (cause) {
      if (isMissingSession(cause)) {
        resetSession("The browser session ended. Start a new one.");
      } else {
        setActionError(messageOf(cause, "The browser did not respond."));
      }
    } finally {
      setBusy(null);
    }
  }

  async function restart() {
    const session = status?.session;
    if (!session) return;
    setBusy("restart");
    setActionError(null);
    try {
      updateSession(await restartBrowserSession(session.id));
      setFrameKey((value) => value + 1);
    } catch (cause) {
      if (isMissingSession(cause)) {
        resetSession("The browser session ended. Start a new one.");
      } else {
        setActionError(messageOf(cause, "Could not restart the browser."));
      }
    } finally {
      setBusy(null);
    }
  }

  async function close() {
    const session = status?.session;
    if (!session) return;
    setBusy("close");
    setActionError(null);
    try {
      await closeBrowserSession(session.id);
      setStatus((current) =>
        current
          ? {
              ...current,
              session: null,
              active_sessions: Math.max(0, current.active_sessions - 1),
            }
          : current,
      );
    } catch (cause) {
      if (isMissingSession(cause)) {
        resetSession(null);
      } else {
        setActionError(messageOf(cause, "Could not close the session."));
      }
    } finally {
      setBusy(null);
    }
  }

  function openFavourite(favourite: BrowserFavourite) {
    if (status?.session) {
      void navigateTo(favourite.url);
    } else {
      void start(favourite.url);
    }
  }

  async function enterFullscreen() {
    try {
      await frameRef.current?.requestFullscreen();
    } catch {
      setActionError("This browser does not allow full-screen mode.");
    }
  }

  function submitAddress(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status?.session) {
      void navigateTo(address);
    } else {
      void start(address);
    }
  }

  const busyNow = busy !== null;

  if (loading && !status) {
    return (
      <div className="page browser-page">
        <div className="browser-loading" aria-live="polite">
          <span className="eyebrow">Opening a secure channel</span>
          <p>Contacting the browser service on the Pi...</p>
        </div>
      </div>
    );
  }

  if (loadError && !status) {
    return (
      <div className="page browser-page">
        <ErrorState
          title="The private browser is unavailable"
          message={loadError}
          onRetry={() => void load()}
        />
      </div>
    );
  }

  if (!status) return null;

  if (!status.enabled) {
    return (
      <div className="page browser-page">
        <EmptyState
          eyebrow="Private browser"
          title="The private browser is switched off"
          description={
            status.setup_message ?? "Set AI_DASHBOARD_BROWSER_ENABLED=true on the Pi to enable it."
          }
        />
      </div>
    );
  }

  if (!status.accounts_configured) {
    return (
      <div className="page browser-page">
        <div className="browser-setup">
          <span className="eyebrow">One step remains</span>
          <h1>Set up a browser account</h1>
          <p>
            Run this command on the Pi, choose a password, then reload this page. The password is
            stored only as a scrypt hash.
          </p>
          <pre>
            <code>uv run python -m ai_dashboard.browser.auth set-user yourname</code>
          </pre>
        </div>
      </div>
    );
  }

  if (!status.authenticated) {
    return (
      <div className="page browser-page">
        <header className="page-header browser-header">
          <div className="page-eyebrow">Private browser</div>
          <h1 className="page-title">A quiet window to the wider web</h1>
          <p className="page-deck">
            A separate Firefox profile on the Pi, rendered securely to this screen.
          </p>
        </header>
        {actionError ? (
          <p className="browser-warning" role="alert">
            {actionError}
          </p>
        ) : null}
        <LoginPanel busy={busyNow} dockerAvailable={status.docker_available} onLogin={signIn} />
      </div>
    );
  }

  const session = status.session;

  return (
    <div className={`page browser-page${session ? " has-session" : ""}`}>
      <header className="page-header browser-header">
        <div className="page-eyebrow">Private browser</div>
        <div className="browser-heading-row">
          <div>
            <h1 className="page-title">{session ? "The wider web" : "A quiet window"}</h1>
            <p className="page-deck">
              {session
                ? "Firefox is running on the Pi. Actions here travel over your private network."
                : "Start a private Firefox session on the Pi, then browse from this phone."}
            </p>
          </div>
          <div className="browser-account">
            <span>
              Signed in as <strong>{status.user}</strong>
            </span>
            <Link to="/browser/favourites" className="text-link">
              Favourites
            </Link>
            <button
              type="button"
              className="text-link"
              onClick={() => void signOut()}
              disabled={busyNow}
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      {actionError ? (
        <p className="browser-warning" role="alert">
          {actionError}
        </p>
      ) : null}

      {status.setup_message && !status.docker_available ? (
        <p className="browser-warning">{status.setup_message}</p>
      ) : null}

      {session ? (
        <section className="browser-stage" aria-label="Remote Firefox session">
          <div className="browser-chrome">
            <div className="browser-nav-buttons">
              <button
                type="button"
                className="browser-tool"
                aria-label="Back"
                title="Back"
                onClick={() => void sendAction("back")}
                disabled={busyNow}
              >
                <ArrowLeft size={18} />
              </button>
              <button
                type="button"
                className="browser-tool"
                aria-label="Forward"
                title="Forward"
                onClick={() => void sendAction("forward")}
                disabled={busyNow}
              >
                <ArrowRight size={18} />
              </button>
              <button
                type="button"
                className="browser-tool"
                aria-label="Reload page"
                title="Reload page"
                onClick={() => void sendAction("reload")}
                disabled={busyNow}
              >
                <Refresh size={17} />
              </button>
              <button
                type="button"
                className="browser-tool"
                aria-label="Home"
                title="Home"
                onClick={() => void navigateTo(HOME_URL)}
                disabled={busyNow}
              >
                <Home size={17} />
              </button>
            </div>

            <form className="browser-address" onSubmit={submitAddress}>
              <span className="sr-only">Address or search</span>
              <Search size={15} className="search-icon" />
              <input
                value={address}
                onChange={(event) => setAddress(event.target.value)}
                placeholder="Search or enter an address"
                inputMode="url"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
              />
              <button
                type="submit"
                className="browser-address-go"
                aria-label="Open address"
                disabled={busyNow || !address.trim()}
              >
                <ArrowRight size={16} />
              </button>
            </form>

            <div className="browser-session-tools">
              <button
                type="button"
                className="browser-tool"
                aria-label="Open a new tab"
                title="New tab"
                onClick={() => void sendAction("new-tab")}
                disabled={busyNow}
              >
                <Plus size={18} />
              </button>
              <button
                type="button"
                className="browser-tool"
                aria-label="Restart Firefox"
                title="Restart Firefox"
                onClick={() => void restart()}
                disabled={busyNow}
              >
                <Refresh size={17} />
              </button>
              <button
                type="button"
                className="browser-tool"
                aria-label="Full screen"
                title="Full screen"
                onClick={() => void enterFullscreen()}
              >
                <Maximize size={17} />
              </button>
              <button
                type="button"
                className="browser-tool browser-tool-danger"
                aria-label="Close browser session"
                title="Close session"
                onClick={() => void close()}
                disabled={busyNow}
              >
                <Trash size={17} />
              </button>
            </div>
          </div>

          <div className="browser-frame-wrap" ref={frameRef}>
            <iframe
              key={frameKey}
              className="browser-frame"
              src={session.vnc_url}
              title="Private Firefox browser"
              allow="clipboard-read 'none'; clipboard-write 'none'; fullscreen"
            />
          </div>

          <div className="browser-footnote">
            <span>
              Session closes after {session.idle_timeout_minutes} minutes without activity.
            </span>
            <button type="button" className="text-link" onClick={() => void restart()}>
              Reconnect display
            </button>
          </div>
        </section>
      ) : (
        <section className="browser-start">
          <div className="browser-launch-panel">
            <span className="eyebrow">New session</span>
            <h2>Where would you like to go?</h2>
            <p>
              Leave this blank for a fresh tab, or enter a website or search. Only one browser
              session can run for your account at a time.
            </p>
            <form className="browser-launch-form" onSubmit={submitAddress}>
              <label className="browser-address browser-address-large">
                <span className="sr-only">Website address or search</span>
                <Search size={17} className="search-icon" />
                <input
                  value={address}
                  onChange={(event) => setAddress(event.target.value)}
                  placeholder="Search DuckDuckGo or enter an address"
                  inputMode="url"
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                />
              </label>
              <button
                type="submit"
                className="button-secondary"
                disabled={busyNow || !status.docker_available}
              >
                {busy === "start" ? "Starting Firefox..." : "Start browsing"}
              </button>
            </form>
            <p className="browser-launch-meta">
              {status.active_sessions} of {status.max_sessions} browser slots in use across the Pi.
            </p>
          </div>

          <div className="browser-favourites">
            <div className="browser-section-heading">
              <span className="eyebrow">Saved places</span>
              <Link to="/browser/favourites" className="text-link">
                Edit favourites
              </Link>
            </div>
            {favourites.length > 0 ? (
              <div className="browser-favourite-grid">
                {favourites.map((favourite) => (
                  <button
                    type="button"
                    className="browser-favourite"
                    key={favourite.url}
                    onClick={() => openFavourite(favourite)}
                    disabled={busyNow || !status.docker_available}
                  >
                    <span className="browser-favourite-title">{favourite.title}</span>
                    <span className="browser-favourite-host">{favouriteHost(favourite.url)}</span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="browser-empty">No saved places yet.</p>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
