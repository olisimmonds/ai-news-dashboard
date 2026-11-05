export function ArticleSkeleton({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`skeleton-card${compact ? " is-compact" : ""}`} aria-hidden>
      <span className="skeleton-line skeleton-kicker" />
      <span className="skeleton-line skeleton-title" />
      <span className="skeleton-line skeleton-title short" />
      {!compact ? <span className="skeleton-line skeleton-text" /> : null}
      <span className="skeleton-line skeleton-meta" />
    </div>
  );
}

export function FeedSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="feed-skeleton" role="status" aria-label="Loading the latest news">
      {Array.from({ length: count }, (_, index) => (
        <ArticleSkeleton key={index} />
      ))}
    </div>
  );
}

export function BriefingSkeleton() {
  return (
    <div className="briefing-skeleton" aria-hidden>
      <div className="briefing-skeleton-lead">
        <span className="skeleton-line skeleton-kicker" />
        <span className="skeleton-line skeleton-lead-title" />
        <span className="skeleton-line skeleton-lead-title short" />
        <span className="skeleton-line skeleton-text" />
        <span className="skeleton-line skeleton-text short" />
        <span className="skeleton-line skeleton-meta" />
      </div>
      <div className="briefing-skeleton-side">
        {Array.from({ length: 4 }, (_, index) => (
          <ArticleSkeleton key={index} compact />
        ))}
      </div>
    </div>
  );
}
