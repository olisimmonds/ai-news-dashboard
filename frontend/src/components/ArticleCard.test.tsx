import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ReadingProvider } from "../context/ReadingProvider";
import { makeArticle } from "../test/fixtures";
import { ArticleCard } from "./ArticleCard";

const article = makeArticle({ id: 99, title: "A headline worth reading" });

function renderCard() {
  return render(
    <ReadingProvider>
      <ArticleCard article={article} variant="standard" />
    </ReadingProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe("ArticleCard", () => {
  it("renders the headline, source and summary", () => {
    renderCard();
    expect(screen.getByRole("link", { name: /A headline worth reading/i })).toHaveAttribute(
      "href",
      article.url,
    );
    expect(screen.getByText("Example Wire")).toBeInTheDocument();
    expect(screen.getByText(article.summary)).toBeInTheDocument();
  });

  it("has no saved action", () => {
    renderCard();
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
  });

  it("renders an image and story coverage when available", () => {
    render(
      <ReadingProvider>
        <ArticleCard
          article={makeArticle({
            id: 100,
            image_url: "https://images.example.com/story.jpg",
            publisher_count: 3,
          })}
          variant="standard"
        />
      </ReadingProvider>,
    );
    expect(document.querySelector("img.article-image")).toHaveAttribute(
      "src",
      "https://images.example.com/story.jpg",
    );
    expect(screen.getByText("3 publishers")).toBeInTheDocument();
  });

  it("marks a story as read when its link is followed", async () => {
    const user = userEvent.setup();
    renderCard();

    const link = screen.getByRole("link", { name: /A headline worth reading/i });
    link.addEventListener("click", (event: MouseEvent) => event.preventDefault());
    await user.click(link);

    expect(JSON.parse(localStorage.getItem("meridian.read.v1") ?? "[]")).toEqual([99]);
  });

  it("hides a category when asked", () => {
    render(
      <ReadingProvider>
        <ArticleCard article={article} variant="standard" hideCategory="ai-technology" />
      </ReadingProvider>,
    );
    expect(screen.queryByText("AI & Tech")).not.toBeInTheDocument();
  });
});
