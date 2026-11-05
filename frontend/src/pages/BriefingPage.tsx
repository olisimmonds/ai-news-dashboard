import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ArticleCard } from "../components/ArticleCard";
import { DeskStatus } from "../components/DeskStatus";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { RefreshButton } from "../components/RefreshButton";
import { SectionHeading } from "../components/SectionHeading";
import { BriefingSkeleton } from "../components/Skeleton";
import { WeatherStrip } from "../components/WeatherStrip";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useMeta } from "../hooks/useMeta";
import { useRefreshSignal } from "../hooks/useRefreshSignal";
import { getBriefing } from "../lib/api";
import { CATEGORIES } from "../lib/categories";
import { formatDay, formatStoryCount } from "../lib/format";
import type { Briefing } from "../lib/types";

export function BriefingPage() {
  useDocumentTitle("");
  const { refreshing } = useMeta();
  const refreshSignal = useRefreshSignal();
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      setLoading(true);
      const data = await getBriefing({ signal });
      if (signal?.aborted) return;
      setBriefing(data);
      setError(null);
    } catch (cause) {
      if (signal?.aborted) return;
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(cause instanceof Error ? cause.message : "Could not load the briefing.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, refreshSignal]);

  const isEmpty = !loading && !error && (!briefing || !briefing.lead);

  return (
    <div className="page briefing-page">
      <header className="page-header briefing-header">
        <div className="page-eyebrow">{formatDay()}</div>
        <div className="briefing-heading-row">
          <div>
            <h1 className="page-title">The Briefing</h1>
            <p className="page-deck">
              A considered scan of markets, energy, artificial intelligence and the world beyond.
            </p>
          </div>
          <div className="page-toolbar">
            <DeskStatus />
            <RefreshButton />
          </div>
        </div>
      </header>

      <WeatherStrip />

      {loading && !briefing ? <BriefingSkeleton /> : null}

      {error && !briefing ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      {isEmpty ? (
        <EmptyState
          eyebrow={refreshing ? "Collecting stories" : "First edition"}
          title={refreshing ? "The news desk is gathering the first stories" : "No stories yet"}
          description={
            refreshing
              ? "Meridian is fetching and sorting the latest articles. The briefing will appear here automatically."
              : "No articles have been collected yet. Trigger a refresh to fetch the latest feeds."
          }
          action={
            !refreshing ? (
              <RefreshButton />
            ) : (
              <span className="quiet-note">This can take a minute.</span>
            )
          }
        />
      ) : null}

      {briefing?.lead ? (
        <>
          <section className="briefing-grid" aria-label="Today's leading stories">
            <ArticleCard article={briefing.lead} variant="lead" />
            {briefing.featured.length > 0 ? (
              <aside className="briefing-side">
                <div className="side-heading">
                  <span className="eyebrow">Also worth your time</span>
                </div>
                <div className="side-list">
                  {briefing.featured.map((article, index) => (
                    <ArticleCard
                      key={article.id}
                      article={article}
                      variant="featured"
                      index={index}
                    />
                  ))}
                </div>
              </aside>
            ) : null}
          </section>

          <section className="desks-section" aria-labelledby="across-desks">
            <SectionHeading eyebrow="By desk" title="Across the brief" />
            <div className="desk-grid">
              {CATEGORIES.map((category) => {
                const articles = briefing.sections[category.id] ?? [];
                const count = briefing.stats.category_counts[category.id] ?? 0;
                return (
                  <div className={`desk-column desk-${category.id}`} key={category.id}>
                    <div className="desk-header">
                      <div className="desk-title-row">
                        <span className="desk-dot" aria-hidden />
                        <h3 id={`desk-${category.id}`}>{category.label}</h3>
                        <span className="desk-count">{formatStoryCount(count)}</span>
                      </div>
                      <Link to={category.path} className="text-link">
                        View all
                      </Link>
                    </div>
                    {articles.length > 0 ? (
                      <div className="desk-list">
                        {articles.map((article) => (
                          <ArticleCard
                            key={article.id}
                            article={article}
                            variant="compact"
                            hideCategory={category.id}
                          />
                        ))}
                      </div>
                    ) : (
                      <p className="desk-empty">No stories in this desk yet.</p>
                    )}
                  </div>
                );
              })}
            </div>
          </section>

          {briefing.latest.length > 0 ? (
            <section className="latest-section" aria-labelledby="latest-heading">
              <SectionHeading
                eyebrow="Chronological"
                title="Latest"
                to="/news"
                linkLabel="Browse the archive"
              />
              <div className="latest-list">
                {briefing.latest.map((article) => (
                  <ArticleCard key={article.id} article={article} variant="latest" />
                ))}
              </div>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
