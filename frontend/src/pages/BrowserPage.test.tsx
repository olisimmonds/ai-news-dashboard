import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../lib/api";
import { browserFavourites, makeBrowserSession, makeBrowserStatus } from "../test/fixtures";
import { BrowserPage } from "./BrowserPage";

vi.mock("../lib/api", () => ({
  browserSessionAction: vi.fn(),
  closeBrowserSession: vi.fn(),
  getBrowserFavourites: vi.fn(),
  getBrowserStatus: vi.fn(),
  loginBrowser: vi.fn(),
  logoutBrowser: vi.fn(),
  navigateBrowserSession: vi.fn(),
  restartBrowserSession: vi.fn(),
  startBrowserSession: vi.fn(),
}));

const mocked = vi.mocked(api);

function renderPage() {
  return render(
    <MemoryRouter>
      <BrowserPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getBrowserFavourites.mockResolvedValue(browserFavourites);
  mocked.getBrowserStatus.mockResolvedValue(makeBrowserStatus());
  mocked.startBrowserSession.mockResolvedValue(makeBrowserSession());
  mocked.navigateBrowserSession.mockResolvedValue(makeBrowserSession());
  mocked.browserSessionAction.mockResolvedValue(makeBrowserSession());
  mocked.restartBrowserSession.mockResolvedValue(makeBrowserSession());
  mocked.closeBrowserSession.mockResolvedValue({ status: "closed" });
  mocked.loginBrowser.mockResolvedValue({ user: "alice" });
  mocked.logoutBrowser.mockResolvedValue({ status: "signed-out" });
});

describe("BrowserPage", () => {
  it("signs in to the private browser", async () => {
    const user = userEvent.setup();
    mocked.getBrowserStatus
      .mockResolvedValueOnce(makeBrowserStatus())
      .mockResolvedValueOnce(
        makeBrowserStatus({ authenticated: true, user: "alice", active_sessions: 0 }),
      );
    renderPage();

    await user.type(await screen.findByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(mocked.loginBrowser).toHaveBeenCalledWith("alice", "correct horse battery");
    });
    expect(await screen.findByText(/Signed in as/i)).toBeVisible();
  });

  it("keeps the idle browser page compact", async () => {
    mocked.getBrowserStatus.mockResolvedValue(
      makeBrowserStatus({ authenticated: true, user: "alice" }),
    );
    renderPage();

    expect(await screen.findByText("Where would you like to go?")).toBeVisible();
    expect(screen.queryByText("A quiet window")).not.toBeInTheDocument();
    expect(screen.queryByText(/Start a private Firefox session/)).not.toBeInTheDocument();
  });

  it("starts a session from a saved favourite", async () => {
    const user = userEvent.setup();
    mocked.getBrowserStatus.mockResolvedValue(
      makeBrowserStatus({ authenticated: true, user: "alice" }),
    );
    renderPage();

    await user.click(await screen.findByRole("button", { name: /DuckDuckGo/i }));

    expect(mocked.startBrowserSession).toHaveBeenCalledWith("https://duckduckgo.com/");
    expect(await screen.findByTitle("Private Firefox browser")).toBeVisible();
  });

  it("recovers when the session has already ended", async () => {
    const user = userEvent.setup();
    mocked.getBrowserStatus
      .mockResolvedValueOnce(
        makeBrowserStatus({
          authenticated: true,
          user: "alice",
          active_sessions: 1,
          session: makeBrowserSession(),
        }),
      )
      .mockResolvedValueOnce(
        makeBrowserStatus({ authenticated: true, user: "alice", active_sessions: 0 }),
      );
    mocked.closeBrowserSession.mockRejectedValueOnce(
      Object.assign(new Error("Browser session not found."), { status: 404 }),
    );
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Close browser session" }));

    expect(await screen.findByText("Where would you like to go?")).toBeVisible();
  });

  it("restarts the display after noVNC reports a failed reconnect", async () => {
    mocked.getBrowserStatus.mockResolvedValue(
      makeBrowserStatus({
        authenticated: true,
        user: "alice",
        active_sessions: 1,
        session: makeBrowserSession(),
      }),
    );
    renderPage();

    await screen.findByTitle("Private Firefox browser");
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { type: "meridian-browser-reconnect-failed" },
        origin: window.location.origin,
      }),
    );

    await waitFor(() => {
      expect(mocked.restartBrowserSession).toHaveBeenCalledWith("session-1");
    });
  });

  it("stops automatic recovery after two failed reconnect reports", async () => {
    mocked.getBrowserStatus.mockResolvedValue(
      makeBrowserStatus({
        authenticated: true,
        user: "alice",
        active_sessions: 1,
        session: makeBrowserSession(),
      }),
    );
    renderPage();

    await screen.findByTitle("Private Firefox browser");
    const notifyFailure = () =>
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "meridian-browser-reconnect-failed" },
          origin: window.location.origin,
        }),
      );

    notifyFailure();
    await waitFor(() => {
      expect(mocked.restartBrowserSession).toHaveBeenCalledTimes(1);
    });
    notifyFailure();
    await waitFor(() => {
      expect(mocked.restartBrowserSession).toHaveBeenCalledTimes(2);
    });
    notifyFailure();

    expect(
      await screen.findByText(/could not be recovered after 2 automatic attempts/i),
    ).toBeVisible();
    expect(mocked.restartBrowserSession).toHaveBeenCalledTimes(2);
  });

  it("navigates and closes an active session", async () => {
    const user = userEvent.setup();
    mocked.getBrowserStatus.mockResolvedValue(
      makeBrowserStatus({
        authenticated: true,
        user: "alice",
        active_sessions: 1,
        session: makeBrowserSession(),
      }),
    );
    renderPage();

    const address = await screen.findByPlaceholderText("Search or enter an address");
    await user.type(address, "example.com");
    await user.click(screen.getByRole("button", { name: "Open address" }));
    expect(mocked.navigateBrowserSession).toHaveBeenCalledWith("session-1", "example.com");

    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(mocked.browserSessionAction).toHaveBeenCalledWith("session-1", "back");

    await user.click(screen.getByRole("button", { name: "Close browser session" }));
    expect(mocked.closeBrowserSession).toHaveBeenCalledWith("session-1");
    expect(await screen.findByText("Where would you like to go?")).toBeVisible();
  });
});
