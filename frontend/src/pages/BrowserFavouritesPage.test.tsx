import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../lib/api";
import { browserFavourites, makeBrowserStatus } from "../test/fixtures";
import { BrowserFavouritesPage } from "./BrowserFavouritesPage";

vi.mock("../lib/api", () => ({
  getBrowserFavourites: vi.fn(),
  getBrowserStatus: vi.fn(),
  saveBrowserFavourites: vi.fn(),
}));

const mocked = vi.mocked(api);

function renderPage() {
  return render(
    <MemoryRouter>
      <BrowserFavouritesPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getBrowserStatus.mockResolvedValue(
    makeBrowserStatus({ authenticated: true, user: "alice" }),
  );
  mocked.getBrowserFavourites.mockResolvedValue(browserFavourites);
  mocked.saveBrowserFavourites.mockImplementation(async (favourites) => favourites);
});

describe("BrowserFavouritesPage", () => {
  it("edits and saves saved places", async () => {
    const user = userEvent.setup();
    renderPage();

    const title = await screen.findByLabelText("Favourite 1 title");
    await user.clear(title);
    await user.type(title, "Search");
    await user.click(screen.getByRole("button", { name: "Add favourite" }));

    const newTitle = screen.getByLabelText("Favourite 3 title");
    await user.type(newTitle, "Example");
    await user.type(screen.getByLabelText("Favourite 3 address"), "example.com");
    await user.click(screen.getByRole("button", { name: "Save favourites" }));

    await waitFor(() => {
      expect(mocked.saveBrowserFavourites).toHaveBeenCalledWith([
        { title: "Search", url: "https://duckduckgo.com/" },
        { title: "Hacker News", url: "https://news.ycombinator.com/" },
        { title: "Example", url: "example.com" },
      ]);
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Favourites saved.");
  });

  it("does not show an empty editable list when favourites fail to load", async () => {
    mocked.getBrowserFavourites.mockRejectedValueOnce(new Error("Network unavailable."));
    renderPage();

    expect(await screen.findByText("Network unavailable.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Save favourites" })).not.toBeInTheDocument();
  });

  it("shows a sign-in prompt when unauthenticated", async () => {
    mocked.getBrowserStatus.mockResolvedValue(makeBrowserStatus());
    renderPage();

    expect(
      await screen.findByRole("heading", { level: 2, name: "Sign in to edit favourites" }),
    ).toBeVisible();
    expect(mocked.getBrowserFavourites).not.toHaveBeenCalled();
  });
});
