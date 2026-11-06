import { useCallback, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ArticleCard } from "../components/ArticleCard";
import { DeskStatus } from "../components/DeskStatus";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { FilterBar } from "../components/FilterBar";
import { RefreshButton } from "../components/RefreshButton";
import { FeedSkeleton } from "../components/Skeleton";
import { useArticleFeed } from "../hooks/useArticleFeed";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useMeta } from "../hooks/useMeta";
import { formatCount } from "../lib/format";
import type { CategoryId, SortOrder } from "../lib/types";

interface FeedPageProps {
  title: string;
  eyebrow: string;
  description: string;
  category?: CategoryId;
  documentTitle: string;
}

export function FeedPage({ title, eyebrow, description, category, documentTitle }: FeedPageProps) {
  useDocumentTitle(documentTitle);
  const { meta } = useMeta();
  const [searchParams, setSearchParams] = useSearchParams();
  const urlQuery = searchParams.get("q") ?? "";
  const [searchInput, setSearchInput] = useState(urlQuery);
  const debouncedSearch = useDebouncedValue(searchInput, 300);

  const source = searchParams.get("source") ?? "";
  const sort: SortOrder = searchParams.get("sort") === "oldest" ? "oldest" : "newest";
  const focusSearch = searchParams.get("focus") === "search";

  const updateParams = useCallback(
    (updates: Record<string, string | undefined>) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          for (const [key, value] of Object.entries(updates)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const feed = useArticleFeed({
    category,
    source: source || undefined,
    q: debouncedSearch.trim() || undefined,
    sort,
    limit: 30,
  });

  const hasFilters = Boolean(searchInput.trim() || source || sort !== "newest");

  function onSearchChange(value: string) {
    setSearchInput(value);
    updateParams({ q: value.trim() || undefined });
  }

  function clearFilters() {
    setSearchInput("");
    setSearchParams(new URLSearchParams(), { replace: true });
  }

  const noArticlesAtAll = (meta?.stats.total_articles ?? 0) === 0;

  return (
    <div className="page feed-page">
      <header
        className={`page-header${category ? ` category-header category-header-${category}` : ""}`}
      >
        <div className="page-eyebrow">{eyebrow}</div>
        <div className="feed-heading-row">
          <div>
            <h1 className="page-title">{title}</h1>
            <p className="page-deck">{description}</p>
          </div>
          <div className="page-toolbar">
            <DeskStatus />
            <RefreshButton />
          </div>
        </div>
      </header>

      <FilterBar
        searchValue={searchInput}
        onSearchChange={onSearchChange}
        source={source}
        onSourceChange={(value) => updateParams({ source: value || undefined })}
        sources={meta?.sources ?? []}
        sort={sort}
        onSortChange={(value) => updateParams({ sort: value === "newest" ? undefined : value })}
        hasFilters={hasFilters}
        onClear={clearFilters}
        total={feed.total}
        loading={feed.loading}
        autoFocus={focusSearch}
      />

      <div className="feed-area" aria-busy={feed.loading}>
        {feed.loading && feed.items.length === 0 ? <FeedSkeleton count={8} /> : null}

        {!feed.loading && feed.error && feed.items.length === 0 ? (
          <ErrorState message={feed.error} onRetry={feed.retry} />
        ) : null}

        {!feed.loading && !feed.error && feed.items.length === 0 ? (
          <EmptyState
            eyebrow={hasFilters ? "Nothing matches" : "No stories"}
            title={hasFilters ? "No stories match those filters" : "Nothing here yet"}
            description={
              hasFilters
                ? "Try a broader search, a different source, or clear the filters to see everything."
                : noArticlesAtAll
                  ? "Meridian has not collected any articles yet. Refresh the feeds to begin."
                  : "There are no articles in this view yet. Check back after the next update."
            }
            action={
              hasFilters ? (
                <button type="button" className="button-secondary" onClick={clearFilters}>
                  Clear filters
                </button>
              ) : noArticlesAtAll ? (
                <RefreshButton />
              ) : (
                <Link to="/" className="button-secondary">
                  Back to the briefing
                </Link>
              )
            }
          />
        ) : null}

        {feed.items.length > 0 ? (
          <>
            <div className={`feed-list${feed.loading ? " is-updating" : ""}`}>
              {feed.items.map((article) => (
                <ArticleCard
                  key={article.id}
                  article={article}
                  variant="standard"
                  hideCategory={category}
                />
              ))}
            </div>

            {feed.error ? (
              <div className="feed-inline-error" role="alert">
                <span>{feed.error}</span>
                <button type="button" className="text-link" onClick={feed.retry}>
                  Try again
                </button>
              </div>
            ) : null}

            <div className="feed-footer">
              <span>
                Showing {formatCount(feed.items.length)} of {formatCount(feed.total)}
              </span>
              {feed.hasMore ? (
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => void feed.loadMore()}
                  disabled={feed.loadingMore}
                >
                  {feed.loadingMore ? "Loading…" : "Load more stories"}
                </button>
              ) : (
                <span className="feed-end">End of the retained archive</span>
              )}
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
