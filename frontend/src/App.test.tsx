import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { MetaProvider } from "./context/MetaProvider";
import { ReadingProvider } from "./context/ReadingProvider";
import { ThemeProvider } from "./context/ThemeProvider";
import * as api from "./lib/api";
import { briefing, browserFavourites, makeArticle, makeBrowserStatus, meta } from "./test/fixtures";

vi.mock("./lib/api", () => ({
  browserSessionAction: vi.fn(),
  closeBrowserSession: vi.fn(),
  getArticles: vi.fn(),
  getBriefing: vi.fn(),
  getBrowserFavourites: vi.fn(),
  getBrowserStatus: vi.fn(),
  getMeta: vi.fn(),
  getWeather: vi.fn(),
  loginBrowser: vi.fn(),
  logoutBrowser: vi.fn(),
  navigateBrowserSession: vi.fn(),
  restartBrowserSession: vi.fn(),
  saveBrowserFavourites: vi.fn(),
  startBrowserSession: vi.fn(),
  triggerRefresh: vi.fn(),
}));

const mocked = vi.mocked(api);

function renderApp(entries: string[] = ["/"]) {
  return render(
    <ThemeProvider>
      <ReadingProvider>
        <MetaProvider>
          <MemoryRouter initialEntries={entries}>
            <App />
          </MemoryRouter>
        </MetaProvider>
      </ReadingProvider>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mocked.getMeta.mockResolvedValue(meta);
  mocked.getBriefing.mockResolvedValue(briefing);
  mocked.getWeather.mockResolvedValue({ locations: [], generated_at: new Date().toISOString() });
  mocked.getBrowserStatus.mockResolvedValue(makeBrowserStatus());
  mocked.getBrowserFavourites.mockResolvedValue(browserFavourites);
  mocked.getArticles.mockResolvedValue({
    items: [makeArticle({ id: 3, title: "A feed article" })],
    total: 1,
    limit: 30,
    offset: 0,
    has_more: false,
  });
});

describe("App", () => {
  it("renders the briefing lead, desks and latest stories", async () => {
    renderApp();

    expect(await screen.findByRole("heading", { level: 1, name: "The Briefing" })).toBeVisible();
    expect(
      await screen.findByRole("heading", { level: 2, name: /Test lead headline/i }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "Across the brief" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Latest" })).toBeVisible();
  });

  it("opens global search and shows live results", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByRole("button", { name: /Search all news/i }));
    expect(screen.getByRole("dialog", { name: "Search news" })).toBeVisible();

    await user.type(screen.getByRole("searchbox"), "feed");
    expect(
      await screen.findByRole("link", { name: /A feed article/i }, { timeout: 3000 }),
    ).toBeVisible();
  });

  it("opens the private browser and asks for sign-in", async () => {
    renderApp(["/browser"]);

    expect(
      await screen.findByRole("heading", { level: 2, name: "Sign in to the browser" }),
    ).toBeVisible();
    expect(screen.queryByRole("link", { name: "All News" })).not.toBeInTheDocument();
  });

  it("shows the not-found page for an unknown route", async () => {
    renderApp(["/does-not-exist"]);

    expect(
      await screen.findByRole("heading", { level: 2, name: "This page is off the map" }),
    ).toBeVisible();
  });
});
